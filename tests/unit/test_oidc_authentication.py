"""OIDC JWT 验证、JWKS 信任边界与安全错误测试。"""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace, TracebackType
from typing import Self, cast
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import ClauseElement

from cnb_application import AuthenticationError, permissions_for_role
from cnb_domain import AdminPermission, AdminPrincipal, AdminRole
from cnb_infrastructure import OidcAuthenticator, Settings


def test_bootstrap_settings_ignore_empty_optional_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CNB_OIDC_TENANT_ID", "")
    monkeypatch.setenv("CNB_OIDC_AGENT_ID", "")
    monkeypatch.setenv("CNB_OTEL_EXPORTER_OTLP_ENDPOINT", "")

    settings = Settings(
        environment="development",
        _env_file=None,  # pyright: ignore[reportCallIssue] -- pydantic-settings 动态参数。
    )

    assert settings.oidc_tenant_id is None
    assert settings.oidc_agent_id is None
    assert settings.otel_exporter_otlp_endpoint is None


ISSUER = "https://identity.example.test/realms/cnb"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
AUDIENCE = "cyber-netizen-api"
CLIENT_ID = "cyber-netizen-web"


class OidcPersistenceRecordingSession:
    """记录 OIDC 持久化语句，并模拟仍有效的手工只读角色。"""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._scalar_results: list[object | None] = [None, "viewer", None]
        self.scalar_statements: list[object] = []

    async def execute(self, statement: object) -> None:
        del statement

    async def scalar(self, statement: object) -> object | None:
        self.scalar_statements.append(statement)
        return self._scalar_results.pop(0)

    async def flush(self) -> None:
        pass

    async def get(self, entity: type[object], identifier: object) -> object:
        del identifier
        if entity.__name__ == "Tenant":
            return SimpleNamespace(status="active")
        return SimpleNamespace(status="active", tenant_id=self._tenant_id)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class OidcPersistenceRecordingFactory:
    """提供兼容 async_sessionmaker.begin 的测试事务上下文。"""

    def __init__(self, session: OidcPersistenceRecordingSession) -> None:
        self._session = session

    def begin(self) -> OidcPersistenceRecordingSession:
        return self._session


def test_otel_bootstrap_rejects_missing_or_credential_bearing_endpoint() -> None:
    with pytest.raises(ValidationError, match="OTLP endpoint"):
        Settings(environment="test", otel_enabled=True)
    with pytest.raises(ValidationError, match="不能包含凭证"):
        Settings(
            environment="test",
            otel_enabled=True,
            otel_exporter_otlp_endpoint="https://user:secret@collector.example.test/v1/traces",
        )
    with pytest.raises(ValidationError, match="采样率"):
        Settings(environment="test", otel_trace_sample_ratio=1.1)


def _settings() -> Settings:
    return Settings(
        environment="test",
        authentication_mode="oidc",
        oidc_issuer_url=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_audience=AUDIENCE,
        oidc_tenant_id=uuid4(),
        oidc_agent_id=uuid4(),
    )


def _token_and_transport(
    *,
    role: str = "admin",
    audience: str = AUDIENCE,
    jwks_uri: str = JWKS_URI,
) -> tuple[str, httpx.MockTransport]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def encode_uint(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    public_jwk: dict[str, object] = {
        "kty": "RSA",
        "n": encode_uint(public_numbers.n),
        "e": encode_uint(public_numbers.e),
        "kid": "test-key",
        "use": "sig",
        "alg": "RS256",
    }
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "oidc-user-1",
            "aud": audience,
            "azp": CLIENT_ID,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "name": "正式管理员",
            "cnb_role": role,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "at+jwt"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": jwks_uri})
        if str(request.url) == JWKS_URI:
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(404)

    return token, httpx.MockTransport(handler)


async def test_oidc_validates_jwt_and_maps_local_permissions() -> None:
    token, transport = _token_and_transport(role="operator")
    authenticator = OidcAuthenticator(_settings(), transport=transport)

    principal = await authenticator.authenticate(token)

    assert principal.role is AdminRole.OPERATOR
    assert principal.display_name == "正式管理员"
    assert principal.authentication_mode == "oidc"
    assert AdminPermission.CHANNEL_SEND in principal.permissions
    assert AdminPermission.SECRET_MANAGE not in principal.permissions


async def test_oidc_refreshes_trusted_role_without_replacing_manual_effective_role() -> None:
    settings = _settings()
    assert settings.oidc_tenant_id is not None
    assert settings.oidc_agent_id is not None
    recording_session = OidcPersistenceRecordingSession(settings.oidc_tenant_id)
    authenticator = OidcAuthenticator(
        settings,
        cast(
            async_sessionmaker[AsyncSession],
            OidcPersistenceRecordingFactory(recording_session),
        ),
    )
    now = datetime.now(UTC)
    principal = AdminPrincipal(
        tenant_id=settings.oidc_tenant_id,
        user_id=uuid4(),
        display_name="可信管理员",
        role=AdminRole.ADMIN,
        permissions=permissions_for_role(AdminRole.ADMIN),
        authentication_mode="oidc",
    )

    effective_role = await authenticator._persist_identity(  # pyright: ignore[reportPrivateUsage]
        principal=principal,
        agent_id=settings.oidc_agent_id,
        issuer=ISSUER,
        subject="manual-override-user",
        token_hash="0" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    role_upsert = cast(ClauseElement, recording_session.scalar_statements[1])
    role_sql = str(role_upsert.compile(dialect=postgresql.dialect()))
    assert effective_role is AdminRole.VIEWER
    assert "trusted_role" in role_sql
    assert "CASE WHEN" in role_sql
    assert "role_assignments.source" in role_sql
    assert "RETURNING role_assignments.role" in role_sql


@pytest.mark.parametrize(
    ("role", "audience", "message"),
    (
        ("owner", AUDIENCE, "有效管理角色"),
        ("admin", "wrong-audience", "无效或已过期"),
    ),
)
async def test_oidc_rejects_untrusted_role_and_audience(
    role: str,
    audience: str,
    message: str,
) -> None:
    token, transport = _token_and_transport(role=role, audience=audience)
    authenticator = OidcAuthenticator(_settings(), transport=transport)

    with pytest.raises(AuthenticationError, match=message):
        await authenticator.authenticate(token)


async def test_oidc_rejects_cross_host_jwks_uri_before_fetching_key() -> None:
    token, transport = _token_and_transport(jwks_uri="https://evil.example.test/jwks")
    authenticator = OidcAuthenticator(_settings(), transport=transport)

    with pytest.raises(AuthenticationError, match="JWKS 地址不受信任"):
        await authenticator.authenticate(token)


async def test_oidc_rejects_cross_port_jwks_uri_as_different_origin() -> None:
    token, transport = _token_and_transport(
        jwks_uri="https://identity.example.test:8443/realms/cnb/jwks"
    )
    authenticator = OidcAuthenticator(_settings(), transport=transport)

    with pytest.raises(AuthenticationError, match="JWKS 地址不受信任"):
        await authenticator.authenticate(token)


def test_production_settings_require_oidc_https_bootstrap() -> None:
    with pytest.raises(ValueError, match="必须启用 OIDC"):
        Settings(environment="production")
    with pytest.raises(ValueError, match="HTTPS URL"):
        Settings(
            environment="production",
            authentication_mode="oidc",
            oidc_issuer_url="http://identity.example.test",
            oidc_client_id=CLIENT_ID,
            oidc_tenant_id=uuid4(),
            oidc_agent_id=uuid4(),
        )
