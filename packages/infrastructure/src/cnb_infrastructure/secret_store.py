"""基于 AES-GCM 信封加密的自托管密钥存储。"""

import asyncio
import base64
import binascii
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import SecretNotFoundError, SecretOperationError
from cnb_domain import ConfigScope, SecretIntegrityStatus, SecretMetadata
from cnb_infrastructure.models import AuditLog, SecretReference

_PROVIDER = "aes_gcm"


class AesGcmEnvelopeCipher:
    """使用启动主密钥加密每条独立随机 nonce 的密钥材料。"""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("配置加密主密钥解码后必须恰好为 32 字节")
        self._cipher = AESGCM(master_key)

    @classmethod
    def from_encoded_key(
        cls, encoded_key: str, *, allow_development_placeholder: bool = False
    ) -> "AesGcmEnvelopeCipher":
        """从 Base64 启动设置构造；开发占位值只允许在非生产环境使用。"""
        if allow_development_placeholder and encoded_key == "development-only-placeholder":
            return cls(hashlib.sha256(encoded_key.encode("utf-8")).digest())
        try:
            master_key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("CNB_CONFIG_MASTER_KEY 必须是有效的 Base64 编码") from error
        return cls(master_key)

    def encrypt(
        self,
        plaintext: str,
        *,
        secret_id: UUID,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        key_version: int,
    ) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(
            nonce,
            plaintext.encode("utf-8"),
            self._associated_data(secret_id, key, scope_type, scope_id, key_version),
        )
        return encrypted, nonce

    def decrypt(
        self,
        encrypted: bytes,
        nonce: bytes,
        *,
        secret_id: UUID,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        key_version: int,
    ) -> str:
        try:
            plaintext = self._cipher.decrypt(
                nonce,
                encrypted,
                self._associated_data(secret_id, key, scope_type, scope_id, key_version),
            )
        except (InvalidTag, ValueError) as error:
            raise SecretOperationError("密钥完整性校验失败，请轮换后再使用") from error
        return plaintext.decode("utf-8")

    @staticmethod
    def _associated_data(
        secret_id: UUID,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        key_version: int,
    ) -> bytes:
        return (
            f"cnb-secret-v1|{secret_id}|{key}|{scope_type.value}|{scope_id or '-'}|{key_version}"
        ).encode()


@dataclass(frozen=True, slots=True)
class _MemorySecretRecord:
    metadata: SecretMetadata
    encrypted_value: bytes
    nonce: bytes
    key_version: int


class MemorySecretStore:
    """使用同一加密信封语义的进程内密钥存储，供隔离测试使用。"""

    def __init__(self, cipher: AesGcmEnvelopeCipher | None = None) -> None:
        self._cipher = cipher or AesGcmEnvelopeCipher(bytes(range(32)))
        self._records: dict[UUID, _MemorySecretRecord] = {}
        self._lock = asyncio.Lock()

    async def list_metadata(self) -> tuple[SecretMetadata, ...]:
        async with self._lock:
            return tuple(
                item.metadata
                for item in sorted(
                    self._records.values(),
                    key=lambda record: (
                        record.metadata.key,
                        record.metadata.scope_type.value,
                        str(record.metadata.scope_id or ""),
                    ),
                )
            )

    async def set_secret(
        self,
        *,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        plaintext: str,
        actor_id: UUID | None,
    ) -> SecretMetadata:
        del actor_id
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._records.values()
                    if item.metadata.key == key
                    and item.metadata.scope_type is scope_type
                    and item.metadata.scope_id == scope_id
                ),
                None,
            )
            return self._write(existing, key, scope_type, scope_id, plaintext)

    async def rotate_secret(
        self, secret_id: UUID, *, plaintext: str, actor_id: UUID | None
    ) -> SecretMetadata:
        del actor_id
        async with self._lock:
            existing = self._require(secret_id)
            return self._write(
                existing,
                existing.metadata.key,
                existing.metadata.scope_type,
                existing.metadata.scope_id,
                plaintext,
            )

    async def test_secret(self, secret_id: UUID, *, actor_id: UUID | None) -> SecretMetadata:
        del actor_id
        async with self._lock:
            record = self._require(secret_id)
            now = datetime.now(UTC)
            try:
                self._decrypt(record)
                status = SecretIntegrityStatus.VALID
            except SecretOperationError:
                status = SecretIntegrityStatus.INVALID
            metadata = replace(
                record.metadata,
                integrity_status=status,
                updated_at=now,
                last_tested_at=now,
            )
            self._records[secret_id] = replace(record, metadata=metadata)
            return metadata

    async def clear_secret(self, secret_id: UUID, *, actor_id: UUID | None) -> None:
        del actor_id
        async with self._lock:
            self._require(secret_id)
            del self._records[secret_id]

    async def resolve_secret(
        self,
        key: str,
        *,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> str | None:
        async with self._lock:
            target = _select_effective_record(
                tuple(self._records.values()),
                key=key,
                tenant_id=tenant_id,
                agent_id=agent_id,
                channel_id=channel_id,
                user_id=user_id,
                metadata=lambda item: item.metadata,
            )
            return None if target is None else self._decrypt(target)

    def _write(
        self,
        existing: _MemorySecretRecord | None,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        plaintext: str,
    ) -> SecretMetadata:
        now = datetime.now(UTC)
        secret_id = existing.metadata.id if existing is not None else uuid4()
        key_version = existing.key_version + 1 if existing is not None else 1
        encrypted, nonce = self._cipher.encrypt(
            plaintext,
            secret_id=secret_id,
            key=key,
            scope_type=scope_type,
            scope_id=scope_id,
            key_version=key_version,
        )
        metadata = SecretMetadata(
            id=secret_id,
            key=key,
            scope_type=scope_type,
            scope_id=scope_id,
            provider=_PROVIDER,
            masked_hint=_mask_secret(plaintext),
            integrity_status=SecretIntegrityStatus.UNTESTED,
            created_at=existing.metadata.created_at if existing is not None else now,
            updated_at=now,
            last_tested_at=None,
        )
        self._records[secret_id] = _MemorySecretRecord(
            metadata=metadata,
            encrypted_value=encrypted,
            nonce=nonce,
            key_version=key_version,
        )
        return metadata

    def _decrypt(self, record: _MemorySecretRecord) -> str:
        return self._cipher.decrypt(
            record.encrypted_value,
            record.nonce,
            secret_id=record.metadata.id,
            key=record.metadata.key,
            scope_type=record.metadata.scope_type,
            scope_id=record.metadata.scope_id,
            key_version=record.key_version,
        )

    def _require(self, secret_id: UUID) -> _MemorySecretRecord:
        try:
            return self._records[secret_id]
        except KeyError as error:
            raise SecretNotFoundError(f"密钥引用不存在：{secret_id}") from error


class SqlAlchemySecretStore:
    """将加密信封和审计记录原子持久化到 PostgreSQL。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: AesGcmEnvelopeCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    async def list_metadata(self) -> tuple[SecretMetadata, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(SecretReference).order_by(
                        SecretReference.key,
                        SecretReference.scope_type,
                        SecretReference.scope_id,
                    )
                )
            ).all()
            return tuple(self._metadata(row) for row in rows)

    async def set_secret(
        self,
        *,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        plaintext: str,
        actor_id: UUID | None,
    ) -> SecretMetadata:
        async with self._session_factory() as session, session.begin():
            statement = select(SecretReference).where(
                SecretReference.key == key,
                SecretReference.scope_type == scope_type.value,
                SecretReference.scope_id.is_(None)
                if scope_id is None
                else SecretReference.scope_id == scope_id,
            )
            row = await session.scalar(statement.with_for_update())
            action = "secret.created" if row is None else "secret.updated"
            if row is None:
                row = SecretReference(
                    id=uuid4(),
                    key=key,
                    scope_type=scope_type.value,
                    scope_id=scope_id,
                    provider=_PROVIDER,
                    locator="",
                    encrypted_value=None,
                    nonce=None,
                    key_version=0,
                    masked_hint="••••",
                    integrity_status=SecretIntegrityStatus.UNTESTED.value,
                )
                row.locator = f"database://secret_references/{row.id}"
                session.add(row)
            self._encrypt_row(row, plaintext)
            self._add_audit(session, row, actor_id, action)
            await session.flush()
            return self._metadata(row)

    async def rotate_secret(
        self, secret_id: UUID, *, plaintext: str, actor_id: UUID | None
    ) -> SecretMetadata:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_row(session, secret_id)
            self._encrypt_row(row, plaintext)
            self._add_audit(session, row, actor_id, "secret.rotated")
            await session.flush()
            return self._metadata(row)

    async def test_secret(self, secret_id: UUID, *, actor_id: UUID | None) -> SecretMetadata:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_row(session, secret_id)
            now = datetime.now(UTC)
            try:
                self._decrypt_row(row)
                row.integrity_status = SecretIntegrityStatus.VALID.value
            except SecretOperationError:
                row.integrity_status = SecretIntegrityStatus.INVALID.value
            row.last_tested_at = now
            row.updated_at = now
            self._add_audit(session, row, actor_id, "secret.integrity_tested")
            await session.flush()
            return self._metadata(row)

    async def clear_secret(self, secret_id: UUID, *, actor_id: UUID | None) -> None:
        async with self._session_factory() as session, session.begin():
            row = await self._locked_row(session, secret_id)
            self._add_audit(session, row, actor_id, "secret.cleared")
            await session.delete(row)

    async def resolve_secret(
        self,
        key: str,
        *,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> str | None:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(select(SecretReference).where(SecretReference.key == key))
            ).all()
            target = _select_effective_record(
                tuple(rows),
                key=key,
                tenant_id=tenant_id,
                agent_id=agent_id,
                channel_id=channel_id,
                user_id=user_id,
                metadata=self._metadata,
            )
            return None if target is None else self._decrypt_row(target)

    def _encrypt_row(self, row: SecretReference, plaintext: str) -> None:
        row.provider = _PROVIDER
        row.key_version += 1
        encrypted, nonce = self._cipher.encrypt(
            plaintext,
            secret_id=row.id,
            key=row.key,
            scope_type=ConfigScope(row.scope_type),
            scope_id=row.scope_id,
            key_version=row.key_version,
        )
        row.encrypted_value = encrypted
        row.nonce = nonce
        row.masked_hint = _mask_secret(plaintext)
        row.integrity_status = SecretIntegrityStatus.UNTESTED.value
        row.last_tested_at = None
        row.updated_at = datetime.now(UTC)

    def _decrypt_row(self, row: SecretReference) -> str:
        if row.provider != _PROVIDER or row.encrypted_value is None or row.nonce is None:
            raise SecretOperationError("当前密钥引用无法由自托管存储读取")
        return self._cipher.decrypt(
            row.encrypted_value,
            row.nonce,
            secret_id=row.id,
            key=row.key,
            scope_type=ConfigScope(row.scope_type),
            scope_id=row.scope_id,
            key_version=row.key_version,
        )

    @staticmethod
    async def _locked_row(session: AsyncSession, secret_id: UUID) -> SecretReference:
        row = await session.scalar(
            select(SecretReference).where(SecretReference.id == secret_id).with_for_update()
        )
        if row is None:
            raise SecretNotFoundError(f"密钥引用不存在：{secret_id}")
        return row

    @staticmethod
    def _metadata(row: SecretReference) -> SecretMetadata:
        return SecretMetadata(
            id=row.id,
            key=row.key,
            scope_type=ConfigScope(row.scope_type),
            scope_id=row.scope_id,
            provider=row.provider,
            masked_hint=row.masked_hint,
            integrity_status=SecretIntegrityStatus(row.integrity_status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_tested_at=row.last_tested_at,
        )

    @staticmethod
    def _add_audit(
        session: AsyncSession,
        row: SecretReference,
        actor_id: UUID | None,
        action: str,
    ) -> None:
        session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                resource_type="secret_reference",
                resource_id=str(row.id),
                detail={
                    "key": row.key,
                    "scope_type": row.scope_type,
                    "scope_id": str(row.scope_id) if row.scope_id else None,
                    "key_version": row.key_version,
                },
            )
        )


def _mask_secret(plaintext: str) -> str:
    """只保留便于操作者区分凭证的末四位提示。"""
    return f"••••{plaintext[-4:]}" if len(plaintext) > 4 else "••••"


def _select_effective_record[T](
    records: tuple[T, ...],
    *,
    key: str,
    tenant_id: UUID,
    agent_id: UUID | None,
    channel_id: UUID | None,
    user_id: UUID | None,
    metadata: Callable[[T], SecretMetadata],
) -> T | None:
    """按照与普通配置相同的作用域优先级选择密钥。"""
    by_scope = {
        (item_metadata.scope_type, item_metadata.scope_id): item
        for item in records
        if (item_metadata := metadata(item)).key == key
    }
    selected = by_scope.get((ConfigScope.SYSTEM, None))
    for scope_type, scope_id in (
        (ConfigScope.TENANT, tenant_id),
        (ConfigScope.AGENT, agent_id),
        (ConfigScope.CHANNEL, channel_id),
        (ConfigScope.USER, user_id),
    ):
        if scope_id is not None and (candidate := by_scope.get((scope_type, scope_id))) is not None:
            selected = candidate
    return selected
