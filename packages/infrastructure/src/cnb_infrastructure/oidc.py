"""基于 OIDC Discovery/JWKS 的异步访问令牌验证与本地身份映射。"""

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import AuthenticationError, permissions_for_role
from cnb_domain import AdminPrincipal, AdminRole
from cnb_infrastructure.models import (
    AdminSession,
    Agent,
    ExternalIdentity,
    RoleAssignment,
    Tenant,
    User,
)
from cnb_infrastructure.settings import Settings

_MAX_DISCOVERY_BYTES = 1024 * 1024
_UNKNOWN_KEY_REFRESH_INTERVAL_SECONDS = 10.0


class OidcAuthenticator:
    """只接受非对称签名 JWT，并将可信主体幂等映射到本地租户。"""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.authentication_mode != "oidc":
            raise ValueError("OidcAuthenticator 只能在 OIDC 模式下创建")
        self._settings = settings
        self._session_factory = session_factory
        self._transport = transport
        self._metadata: dict[str, object] | None = None
        self._jwks: tuple[dict[str, object], ...] = ()
        self._cache_expires_at = 0.0
        self._last_forced_refresh_at = 0.0
        self._refresh_lock = asyncio.Lock()

    async def authenticate(self, access_token: str) -> AdminPrincipal:
        """验证签名、issuer、audience、时效和角色后返回不可变主体。"""
        token = access_token.strip()
        if not token or len(token) > 32_768:
            raise AuthenticationError("访问令牌无效")
        try:
            header = cast(dict[str, object], jwt.get_unverified_header(token))
            algorithm = header.get("alg")
            key_id = header.get("kid")
            token_type = header.get("typ")
            if algorithm not in self._settings.oidc_allowed_algorithms:
                raise AuthenticationError("访问令牌签名算法不受信任")
            if not isinstance(key_id, str) or not key_id:
                raise AuthenticationError("访问令牌缺少签名密钥标识")
            if token_type is not None and token_type not in {"JWT", "at+jwt"}:
                raise AuthenticationError("访问令牌类型无效")
            jwk = await self._signing_key(key_id, str(algorithm))
            audience = self._settings.oidc_audience or self._settings.oidc_client_id
            claims = cast(
                dict[str, object],
                jwt.decode(
                    token,
                    key=jwk.key,
                    algorithms=[str(algorithm)],
                    audience=audience,
                    issuer=self._settings.oidc_issuer_url,
                    leeway=30,
                    options={"require": ["exp", "iat", "iss", "sub", "aud"]},
                ),
            )
        except AuthenticationError:
            raise
        except (InvalidTokenError, ValueError, TypeError) as error:
            raise AuthenticationError("访问令牌无效或已过期") from error

        subject = self._required_claim(claims, "sub", maximum=255)
        role_value = self._required_claim(
            claims,
            self._settings.oidc_role_claim,
            maximum=24,
        )
        try:
            role = AdminRole(role_value)
        except ValueError as error:
            raise AuthenticationError("访问令牌未分配有效管理角色") from error
        authorized_party = claims.get("azp", claims.get("client_id"))
        if authorized_party is not None and authorized_party != self._settings.oidc_client_id:
            raise AuthenticationError("访问令牌客户端不匹配")
        display_name_value = claims.get(self._settings.oidc_display_name_claim)
        display_name = (
            display_name_value.strip()[:120]
            if isinstance(display_name_value, str) and display_name_value.strip()
            else "OIDC 用户"
        )
        issuer = self._required_claim(claims, "iss", maximum=500)
        issued_at = self._timestamp_claim(claims, "iat")
        expires_at = self._timestamp_claim(claims, "exp")
        tenant_id = self._settings.oidc_tenant_id
        agent_id = self._settings.oidc_agent_id
        if tenant_id is None or agent_id is None:
            raise AuthenticationError("OIDC 本地身份映射尚未配置")
        user_id = uuid5(NAMESPACE_URL, f"cnb-oidc-user|{issuer}|{subject}")
        principal = AdminPrincipal(
            tenant_id=tenant_id,
            user_id=user_id,
            display_name=display_name,
            role=role,
            permissions=permissions_for_role(role),
            authentication_mode="oidc",
        )
        if self._session_factory is not None:
            effective_role = await self._persist_identity(
                principal=principal,
                agent_id=agent_id,
                issuer=issuer,
                subject=subject,
                token_hash=sha256(token.encode()).hexdigest(),
                issued_at=issued_at,
                expires_at=expires_at,
            )
            principal = replace(
                principal,
                role=effective_role,
                permissions=permissions_for_role(effective_role),
            )
        return principal

    async def _signing_key(self, key_id: str, algorithm: str) -> PyJWK:
        await self._refresh_metadata(force=False)
        match = next((item for item in self._jwks if item.get("kid") == key_id), None)
        if match is None and (
            self._last_forced_refresh_at == 0.0
            or monotonic() - self._last_forced_refresh_at >= _UNKNOWN_KEY_REFRESH_INTERVAL_SECONDS
        ):
            await self._refresh_metadata(force=True)
            match = next((item for item in self._jwks if item.get("kid") == key_id), None)
        if match is None:
            raise AuthenticationError("访问令牌签名密钥未知")
        try:
            return PyJWK.from_dict(cast(dict[str, Any], match), algorithm=algorithm)
        except (InvalidTokenError, ValueError, TypeError) as error:
            raise AuthenticationError("OIDC 签名密钥无效") from error

    async def _refresh_metadata(self, *, force: bool) -> None:
        if not force and self._jwks and monotonic() < self._cache_expires_at:
            return
        async with self._refresh_lock:
            if not force and self._jwks and monotonic() < self._cache_expires_at:
                return
            issuer = (self._settings.oidc_issuer_url or "").rstrip("/")
            discovery_url = f"{issuer}/.well-known/openid-configuration"
            metadata = await self._get_json(discovery_url)
            if metadata.get("issuer") != self._settings.oidc_issuer_url:
                raise AuthenticationError("OIDC Discovery issuer 不匹配")
            jwks_uri = metadata.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not self._trusted_jwks_uri(jwks_uri):
                raise AuthenticationError("OIDC JWKS 地址不受信任")
            jwks_document = await self._get_json(jwks_uri)
            raw_keys = jwks_document.get("keys")
            if not isinstance(raw_keys, list):
                raise AuthenticationError("OIDC JWKS 文档缺少密钥列表")
            keys: list[dict[str, object]] = []
            for raw_key in cast(list[object], raw_keys):
                if not isinstance(raw_key, dict):
                    continue
                item = cast(dict[str, object], raw_key)
                if (
                    isinstance(item.get("kid"), str)
                    and item.get("kty") in {"RSA", "EC"}
                    and item.get("use", "sig") == "sig"
                ):
                    keys.append(item)
            if not keys:
                raise AuthenticationError("OIDC JWKS 没有可用签名密钥")
            self._metadata = metadata
            self._jwks = tuple(keys)
            self._cache_expires_at = monotonic() + self._settings.oidc_jwks_cache_seconds
            if force:
                self._last_forced_refresh_at = monotonic()

    async def _get_json(self, url: str) -> dict[str, object]:
        try:
            async with (
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(5.0),
                    follow_redirects=False,
                ) as client,
                client.stream(
                    "GET",
                    url,
                    headers={"Accept": "application/json"},
                ) as response,
            ):
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_DISCOVERY_BYTES:
                        raise AuthenticationError("OIDC 元数据超过安全大小限制")
        except httpx.HTTPError as error:
            raise AuthenticationError("OIDC 元数据暂时不可用") from error
        try:
            payload: object = httpx.Response(200, content=bytes(content)).json()
        except (UnicodeDecodeError, ValueError) as error:
            raise AuthenticationError("OIDC 元数据不是有效 JSON") from error
        if not isinstance(payload, dict):
            raise AuthenticationError("OIDC 元数据结构无效")
        mapping = cast(dict[object, object], payload)
        if not all(isinstance(key, str) for key in mapping):
            raise AuthenticationError("OIDC 元数据结构无效")
        return cast(dict[str, object], mapping)

    def _trusted_jwks_uri(self, value: str) -> bool:
        issuer = urlsplit(self._settings.oidc_issuer_url or "")
        target = urlsplit(value)
        return (
            target.scheme == "https"
            and target.hostname == issuer.hostname
            and (target.port or 443) == (issuer.port or 443)
            and target.username is None
            and target.password is None
            and not target.fragment
        )

    async def _persist_identity(
        self,
        *,
        principal: AdminPrincipal,
        agent_id: UUID,
        issuer: str,
        subject: str,
        token_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> AdminRole:
        """JIT 建立固定租户内的用户、角色和不含令牌明文的会话记录。"""
        if self._session_factory is None:
            return principal.role
        now = datetime.now(UTC)
        external_identity_id = uuid5(NAMESPACE_URL, f"cnb-external-identity|{issuer}|{subject}")
        assignment_id = uuid5(
            NAMESPACE_URL,
            f"cnb-role-assignment|{principal.tenant_id}|{principal.user_id}",
        )
        session_id = uuid5(NAMESPACE_URL, f"cnb-admin-session|{token_hash}")
        async with self._session_factory.begin() as session:
            await session.execute(
                pg_insert(Tenant)
                .values(
                    id=principal.tenant_id,
                    name=self._settings.oidc_tenant_name[:120],
                    status="active",
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[Tenant.id])
            )
            await session.execute(
                pg_insert(Agent)
                .values(
                    id=agent_id,
                    tenant_id=principal.tenant_id,
                    name=self._settings.oidc_agent_name[:120],
                    status="active",
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[Agent.id])
            )
            await session.execute(
                pg_insert(User)
                .values(
                    id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    display_name=principal.display_name,
                    status="active",
                    created_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[User.id],
                    set_={"display_name": principal.display_name},
                )
            )
            await session.flush()
            await self._assert_active_identity(
                session,
                tenant_id=principal.tenant_id,
                agent_id=agent_id,
                user_id=principal.user_id,
            )
            existing_identity = await session.scalar(
                select(ExternalIdentity).where(
                    ExternalIdentity.issuer == issuer,
                    ExternalIdentity.subject == subject,
                )
            )
            if existing_identity is not None and (
                existing_identity.tenant_id != principal.tenant_id
                or existing_identity.user_id != principal.user_id
            ):
                raise AuthenticationError("外部身份绑定发生冲突")
            await session.execute(
                pg_insert(ExternalIdentity)
                .values(
                    id=external_identity_id,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    issuer=issuer,
                    subject=subject,
                    created_at=now,
                    last_authenticated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_external_identities_issuer_subject",
                    set_={"last_authenticated_at": now},
                )
            )
            effective_role = await session.scalar(
                pg_insert(RoleAssignment)
                .values(
                    id=assignment_id,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    role=principal.role.value,
                    source="oidc",
                    trusted_role=principal.role.value,
                    overridden_by=None,
                    override_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_role_assignments_tenant_user",
                    set_={
                        "role": case(
                            (RoleAssignment.source == "oidc", principal.role.value),
                            else_=RoleAssignment.role,
                        ),
                        "trusted_role": principal.role.value,
                        "updated_at": now,
                    },
                )
                .returning(RoleAssignment.role)
            )
            existing_session = await session.scalar(
                select(AdminSession).where(AdminSession.token_hash == token_hash)
            )
            if existing_session is not None and existing_session.revoked_at is not None:
                raise AuthenticationError("管理会话已被撤销")
            await session.execute(
                pg_insert(AdminSession)
                .values(
                    id=session_id,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    external_identity_id=external_identity_id,
                    token_hash=token_hash,
                    issued_at=issued_at,
                    expires_at=expires_at,
                    last_seen_at=now,
                    revoked_at=None,
                )
                .on_conflict_do_update(
                    index_elements=[AdminSession.token_hash],
                    set_={"last_seen_at": now},
                )
            )
        if effective_role is None:
            raise AuthenticationError("本地角色同步失败")
        return AdminRole(effective_role)

    @staticmethod
    async def _assert_active_identity(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
    ) -> None:
        tenant = await session.get(Tenant, tenant_id)
        agent = await session.get(Agent, agent_id)
        user = await session.get(User, user_id)
        if (
            tenant is None
            or agent is None
            or user is None
            or tenant.status != "active"
            or agent.status != "active"
            or user.status != "active"
            or agent.tenant_id != tenant_id
            or user.tenant_id != tenant_id
        ):
            raise AuthenticationError("本地租户、用户或智能体已停用")

    @staticmethod
    def _required_claim(
        claims: Mapping[str, object],
        key: str,
        *,
        maximum: int,
    ) -> str:
        value = claims.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise AuthenticationError(f"访问令牌缺少有效 claim：{key}")
        return value.strip()

    @staticmethod
    def _timestamp_claim(claims: Mapping[str, object], key: str) -> datetime:
        value = claims.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise AuthenticationError(f"访问令牌缺少有效 claim：{key}")
        return datetime.fromtimestamp(value, tz=UTC)


__all__ = ["OidcAuthenticator"]
