"""OIDC JWT 验证、JWKS 信任边界与安全错误测试。"""

import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cnb_application import AuthenticationError
from cnb_domain import AdminPermission, AdminRole
from cnb_infrastructure import OidcAuthenticator, Settings

ISSUER = "https://identity.example.test/realms/cnb"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
AUDIENCE = "cyber-netizen-api"
CLIENT_ID = "cyber-netizen-web"


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
