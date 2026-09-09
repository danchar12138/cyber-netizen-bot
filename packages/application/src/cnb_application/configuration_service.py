"""不可变版本化运行配置的应用用例。"""

from typing import Protocol
from uuid import UUID

from cnb_application.configuration_registry import ConfigurationRegistry
from cnb_domain import ConfigEntry, ConfigVersion


class ConfigurationNotFoundError(LookupError):
    """请求的配置版本不存在时抛出。"""


class ConfigurationConflictError(RuntimeError):
    """操作与配置版本生命周期状态冲突时抛出。"""


class ConfigurationRepository(Protocol):
    """不可变配置版本的持久化边界。"""

    async def list_versions(self) -> tuple[ConfigVersion, ...]: ...

    async def get_version(self, version_id: UUID) -> ConfigVersion | None: ...

    async def create_draft(
        self, *, note: str | None, values: tuple[ConfigEntry, ...], actor_id: UUID | None
    ) -> ConfigVersion: ...

    async def publish(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion: ...

    async def rollback(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion: ...


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
