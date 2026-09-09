"""不可变版本化运行配置的应用用例。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from cnb_application.configuration_registry import ConfigurationRegistry
from cnb_domain import ConfigEntry, ConfigScope, ConfigVersion, JsonValue


class ConfigurationNotFoundError(LookupError):
    """请求的配置版本不存在时抛出。"""


class ConfigurationConflictError(RuntimeError):
    """操作与配置版本生命周期状态冲突时抛出。"""


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


@dataclass(frozen=True, slots=True)
class EffectiveConfigurationSnapshot:
    """一次运行使用的不可变最终配置及其来源版本。"""

    version: int
    values: Mapping[str, JsonValue]


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
        return await self._repository.publish(version_id, actor_id=actor_id)

    async def rollback(self, version_id: UUID, *, actor_id: UUID | None = None) -> ConfigVersion:
        return await self._repository.rollback(version_id, actor_id=actor_id)

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
        return EffectiveConfigurationSnapshot(
            version=stored.version if stored is not None else 0,
            values=MappingProxyType(values),
        )
