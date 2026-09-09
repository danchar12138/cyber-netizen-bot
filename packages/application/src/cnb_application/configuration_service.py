"""不可变版本化运行配置的应用用例。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from cnb_application.configuration_registry import (
    ConfigurationRegistry,
    ConfigurationValidationError,
)
from cnb_domain import (
    ConfigDifference,
    ConfigDiffKind,
    ConfigEntry,
    ConfigScope,
    ConfigVersion,
    ConfigVersionStatus,
    EffectiveConfigSource,
    JsonValue,
    SecretMetadata,
)


class ConfigurationNotFoundError(LookupError):
    """请求的配置版本不存在时抛出。"""


class ConfigurationConflictError(RuntimeError):
    """操作与配置版本生命周期状态冲突时抛出。"""


class SecretNotFoundError(LookupError):
    """请求的密钥引用不存在时抛出。"""


class SecretOperationError(RuntimeError):
    """密钥材料无法安全处理时抛出。"""


class ConfigurationRepository(Protocol):
    """不可变配置版本的持久化边界。"""

    async def list_versions(self) -> tuple[ConfigVersion, ...]: ...

    async def get_version(self, version_id: UUID) -> ConfigVersion | None: ...

    async def get_version_number(self, version: int) -> ConfigVersion | None: ...

    async def get_published(self) -> ConfigVersion | None: ...

    async def create_draft(
        self, *, note: str | None, values: tuple[ConfigEntry, ...], actor_id: UUID | None
    ) -> ConfigVersion: ...

    async def publish(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion: ...

    async def rollback(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion: ...


class SecretStore(Protocol):
    """隔离密钥加密、元数据、轮换与运行时读取的安全边界。"""

    async def list_metadata(self) -> tuple[SecretMetadata, ...]: ...

    async def set_secret(
        self,
        *,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        plaintext: str,
        actor_id: UUID | None,
    ) -> SecretMetadata: ...

    async def rotate_secret(
        self, secret_id: UUID, *, plaintext: str, actor_id: UUID | None
    ) -> SecretMetadata: ...

    async def test_secret(self, secret_id: UUID, *, actor_id: UUID | None) -> SecretMetadata: ...

    async def clear_secret(self, secret_id: UUID, *, actor_id: UUID | None) -> None: ...

    async def resolve_secret(
        self,
        key: str,
        *,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> str | None: ...


@dataclass(frozen=True, slots=True)
class EffectiveConfigurationSnapshot:
    """一次运行使用的不可变最终配置及其来源版本。"""

    version: int
    values: Mapping[str, JsonValue]
    sources: Mapping[str, EffectiveConfigSource]


@dataclass(frozen=True, slots=True)
class ConfigurationDiffPreview:
    """相对于一个已发布版本的安全配置差异。"""

    base_version: int
    target_version: int
    changes: tuple[ConfigDifference, ...]


class ConfigurationService:
    """在交由仓储原子持久化前校验管理命令。"""

    def __init__(
        self, registry: ConfigurationRegistry, repository: ConfigurationRepository
    ) -> None:
        self._registry = registry
        self._repository = repository

    async def list_versions(self) -> tuple[ConfigVersion, ...]:
        return await self._repository.list_versions()

    async def get_version(self, version_id: UUID) -> ConfigVersion:
        version = await self._repository.get_version(version_id)
        if version is None:
            raise ConfigurationNotFoundError(f"配置版本不存在：{version_id}")
        return version

    async def create_draft(
        self,
        *,
        note: str | None,
        values: tuple[ConfigEntry, ...],
        actor_id: UUID | None = None,
    ) -> ConfigVersion:
        seen: set[tuple[str, str, UUID | None]] = set()
        for entry in values:
            identity = (entry.key, entry.scope_type.value, entry.scope_id)
            if identity in seen:
                raise ConfigurationConflictError(f"同一作用域存在重复配置值：{entry.key}")
            seen.add(identity)
            self._registry.validate_entry(entry)
        return await self._repository.create_draft(
            note=note.strip() if note and note.strip() else None,
            values=values,
            actor_id=actor_id,
        )

    async def publish(self, version_id: UUID, *, actor_id: UUID | None = None) -> ConfigVersion:
        target = await self.get_version(version_id)
        if target.status is not ConfigVersionStatus.DRAFT:
            raise ConfigurationConflictError("只有草稿状态的配置版本可以发布")
        self._validate_snapshot(target)
        return await self._repository.publish(version_id, actor_id=actor_id)

    async def rollback(self, version_id: UUID, *, actor_id: UUID | None = None) -> ConfigVersion:
        target = await self.get_version(version_id)
        if target.status is ConfigVersionStatus.DRAFT:
            raise ConfigurationConflictError("草稿不能作为回滚目标")
        self._validate_snapshot(target)
        return await self._repository.rollback(version_id, actor_id=actor_id)

    async def preview_diff(
        self,
        version_id: UUID,
        *,
        base_version_id: UUID | None = None,
    ) -> ConfigurationDiffPreview:
        """比较两个快照；结果只包含注册表允许进入版本的非密钥值。"""
        target = await self.get_version(version_id)
        if base_version_id is None:
            base = await self._repository.get_published()
            if base is not None and base.id == target.id:
                base = None
        else:
            base = await self.get_version(base_version_id)

        base_values = self._entry_map(base.values if base is not None else ())
        target_values = self._entry_map(target.values)
        identities = sorted(
            base_values.keys() | target_values.keys(),
            key=lambda item: (item[0], item[1].value, str(item[2] or "")),
        )
        changes: list[ConfigDifference] = []
        for identity in identities:
            before_entry = base_values.get(identity)
            after_entry = target_values.get(identity)
            if before_entry is not None and after_entry is not None:
                if before_entry.value == after_entry.value:
                    continue
                kind = ConfigDiffKind.CHANGED
            elif after_entry is not None:
                kind = ConfigDiffKind.ADDED
            else:
                kind = ConfigDiffKind.REMOVED
            key, scope_type, scope_id = identity
            changes.append(
                ConfigDifference(
                    key=key,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    kind=kind,
                    before=before_entry.value if before_entry is not None else None,
                    after=after_entry.value if after_entry is not None else None,
                )
            )
        return ConfigurationDiffPreview(
            base_version=base.version if base is not None else 0,
            target_version=target.version,
            changes=tuple(changes),
        )

    async def resolve_effective(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
        version: int | None = None,
    ) -> EffectiveConfigurationSnapshot:
        """按作用域优先级解析当前或指定不可变版本的最终配置。"""
        if version == 0:
            stored = None
        elif version is None:
            stored = await self._repository.get_published()
        else:
            stored = await self._repository.get_version_number(version)
            if stored is None:
                raise ConfigurationNotFoundError(f"配置版本不存在：v{version}")

        values = {
            definition.key: definition.default
            for definition in self._registry.all()
            if not definition.secret
        }
        sources = {
            key: EffectiveConfigSource(scope_type=None, scope_id=None, version=0) for key in values
        }
        if stored is not None:
            targets = {
                ConfigScope.SYSTEM: None,
                ConfigScope.TENANT: tenant_id,
                ConfigScope.AGENT: agent_id,
                ConfigScope.CHANNEL: channel_id,
                ConfigScope.USER: user_id,
            }
            for scope in ConfigScope:
                target_id = targets[scope]
                for entry in stored.values:
                    if entry.scope_type is scope and entry.scope_id == target_id:
                        values[entry.key] = entry.value
                        sources[entry.key] = EffectiveConfigSource(
                            scope_type=entry.scope_type,
                            scope_id=entry.scope_id,
                            version=stored.version,
                        )
        return EffectiveConfigurationSnapshot(
            version=stored.version if stored is not None else 0,
            values=MappingProxyType(values),
            sources=MappingProxyType(sources),
        )

    def _validate_snapshot(self, version: ConfigVersion) -> None:
        seen: set[tuple[str, str, UUID | None]] = set()
        for entry in version.values:
            identity = (entry.key, entry.scope_type.value, entry.scope_id)
            if identity in seen:
                raise ConfigurationConflictError(f"同一作用域存在重复配置值：{entry.key}")
            seen.add(identity)
            self._registry.validate_entry(entry)

    @staticmethod
    def _entry_map(
        values: tuple[ConfigEntry, ...],
    ) -> dict[tuple[str, ConfigScope, UUID | None], ConfigEntry]:
        return {(item.key, item.scope_type, item.scope_id): item for item in values}


class SecretManagementService:
    """校验密钥定义和作用域后委托给不可回显明文的密钥存储。"""

    def __init__(self, registry: ConfigurationRegistry, store: SecretStore) -> None:
        self._registry = registry
        self._store = store

    async def list_metadata(self) -> tuple[SecretMetadata, ...]:
        return await self._store.list_metadata()

    async def set_secret(
        self,
        *,
        key: str,
        scope_type: ConfigScope,
        scope_id: UUID | None,
        plaintext: str,
        actor_id: UUID | None = None,
    ) -> SecretMetadata:
        self._validate_target(key, scope_type, scope_id)
        self._validate_plaintext(plaintext)
        return await self._store.set_secret(
            key=key,
            scope_type=scope_type,
            scope_id=scope_id,
            plaintext=plaintext,
            actor_id=actor_id,
        )

    async def rotate_secret(
        self,
        secret_id: UUID,
        *,
        plaintext: str,
        actor_id: UUID | None = None,
    ) -> SecretMetadata:
        self._validate_plaintext(plaintext)
        return await self._store.rotate_secret(secret_id, plaintext=plaintext, actor_id=actor_id)

    async def test_secret(self, secret_id: UUID, *, actor_id: UUID | None = None) -> SecretMetadata:
        return await self._store.test_secret(secret_id, actor_id=actor_id)

    async def clear_secret(self, secret_id: UUID, *, actor_id: UUID | None = None) -> None:
        await self._store.clear_secret(secret_id, actor_id=actor_id)

    def _validate_target(self, key: str, scope_type: ConfigScope, scope_id: UUID | None) -> None:
        try:
            definition = self._registry.get(key)
        except KeyError as error:
            raise ConfigurationValidationError(f"未知配置键：{key}") from error
        if not definition.secret:
            raise ConfigurationValidationError(f"配置不是密钥类型：{key}")
        if scope_type not in definition.scopes:
            raise ConfigurationValidationError(f"配置 {key} 不支持作用域 {scope_type.value}")
        if scope_type is ConfigScope.SYSTEM and scope_id is not None:
            raise ConfigurationValidationError("系统作用域不能设置作用域 ID")
        if scope_type is not ConfigScope.SYSTEM and scope_id is None:
            raise ConfigurationValidationError(f"作用域 {scope_type.value} 必须设置作用域 ID")

    @staticmethod
    def _validate_plaintext(plaintext: str) -> None:
        if not plaintext:
            raise ConfigurationValidationError("密钥内容不能为空")
