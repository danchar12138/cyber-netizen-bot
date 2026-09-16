"""不可变版本化运行配置的应用用例。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from cnb_application.alert_policy import AlertEscalationPolicy, AlertPolicyValidationError
from cnb_application.configuration_registry import (
    ConfigurationRegistry,
    ConfigurationValidationError,
)
from cnb_domain import (
    CONFIGURATION_PACKAGE_FORMAT,
    CONFIGURATION_PACKAGE_SCHEMA_VERSION,
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
        self._validate_memory_recall_entries(values)
        self._validate_alert_notification_entries(values)
        return await self._repository.create_draft(
            note=note.strip() if note and note.strip() else None,
            values=values,
            actor_id=actor_id,
        )

    async def create_derived_draft(
        self,
        *,
        expected_base_version: int,
        note: str,
        overrides: tuple[ConfigEntry, ...],
        actor_id: UUID | None = None,
    ) -> ConfigVersion:
        """在当前完整快照上合并受控覆盖并创建草稿。"""
        published = await self._repository.get_published()
        current_version = published.version if published is not None else 0
        if current_version != expected_base_version:
            raise ConfigurationConflictError(
                f"生效配置已从 v{expected_base_version} 变更为 v{current_version}，请重新分析"
            )
        merged = self._entry_map(published.values if published is not None else ())
        for entry in overrides:
            merged[(entry.key, entry.scope_type, entry.scope_id)] = entry
        values = tuple(
            sorted(
                merged.values(),
                key=lambda item: (item.key, item.scope_type.value, str(item.scope_id or "")),
            )
        )
        return await self.create_draft(
            note=note,
            values=values,
            actor_id=actor_id,
        )

    async def import_draft(
        self,
        *,
        package_format: str,
        schema_version: str,
        source_version: int,
        source_note: str | None,
        values: tuple[ConfigEntry, ...],
        actor_id: UUID | None = None,
    ) -> ConfigVersion:
        """校验可移植配置包，并以新草稿导入，绝不直接覆盖生效版本。"""
        if package_format != CONFIGURATION_PACKAGE_FORMAT:
            raise ConfigurationValidationError(f"不支持的配置包格式：{package_format}")
        if schema_version != CONFIGURATION_PACKAGE_SCHEMA_VERSION:
            raise ConfigurationValidationError(f"不支持的配置包 Schema 版本：{schema_version}")
        normalized_source_note = (
            source_note.strip() if source_note and source_note.strip() else None
        )
        note = f"从配置包 v{source_version} 导入"
        if normalized_source_note is not None:
            note = f"{note}：{normalized_source_note}"
        return await self.create_draft(
            note=note[:1000],
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
        self._validate_memory_recall_entries(version.values)
        self._validate_alert_notification_entries(version.values)

    def _validate_memory_recall_entries(self, entries: tuple[ConfigEntry, ...]) -> None:
        """阻止发布不可执行的记忆候选池和全零混合权重。"""
        grouped: dict[tuple[ConfigScope, UUID | None], dict[str, JsonValue]] = {}
        for entry in entries:
            grouped.setdefault((entry.scope_type, entry.scope_id), {})[entry.key] = entry.value
        defaults = {item.key: item.default for item in self._registry.all()}
        weight_keys = (
            "memory.recall.full_text_weight",
            "memory.recall.semantic_weight",
            "memory.recall.recency_weight",
            "memory.recall.importance_weight",
            "memory.recall.relationship_weight",
        )
        for values in grouped.values():
            limit = values.get("memory.recall.limit", defaults["memory.recall.limit"])
            candidate_pool = values.get(
                "memory.recall.candidate_pool",
                defaults["memory.recall.candidate_pool"],
            )
            if (
                isinstance(limit, int)
                and isinstance(candidate_pool, int)
                and candidate_pool < limit
            ):
                raise ConfigurationValidationError("记忆候选池不能小于召回数量")
            weights = [values.get(key, defaults[key]) for key in weight_keys]
            if all(isinstance(value, (int, float)) for value in weights) and not any(weights):
                raise ConfigurationValidationError("记忆混合召回权重不能全部为 0")

    def _validate_alert_notification_entries(self, entries: tuple[ConfigEntry, ...]) -> None:
        """在配置发布前校验升级阈值、时区、工作日和路由组合。"""
        grouped: dict[tuple[ConfigScope, UUID | None], dict[str, JsonValue]] = {}
        for entry in entries:
            if entry.key.startswith("alerts.notification."):
                grouped.setdefault((entry.scope_type, entry.scope_id), {})[entry.key] = entry.value
        defaults = {item.key: item.default for item in self._registry.all()}
        for overrides in grouped.values():
            values = {**defaults, **overrides}
            try:
                AlertEscalationPolicy.from_values(values)
            except AlertPolicyValidationError as error:
                raise ConfigurationValidationError(str(error)) from error

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
