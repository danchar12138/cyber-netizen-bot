"""用于生产环境和隔离测试的配置仓储。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import ConfigurationConflictError, ConfigurationNotFoundError
from cnb_domain import ConfigEntry, ConfigScope, ConfigVersion, ConfigVersionStatus, JsonValue
from cnb_infrastructure.models import (
    AuditLog,
    ConfigurationValue,
    ConfigurationVersion,
)

_CONFIGURATION_LOCK_ID = 4_342_642_025


class MemoryConfigurationRepository:
    """供测试和离线界面开发使用的确定性内存适配器。"""

    def __init__(self) -> None:
        self._versions: dict[UUID, ConfigVersion] = {}
        self._next_version = 1
        self._lock = asyncio.Lock()

    async def list_versions(self) -> tuple[ConfigVersion, ...]:
        async with self._lock:
            return tuple(
                sorted(self._versions.values(), key=lambda item: item.version, reverse=True)
            )

    async def get_version(self, version_id: UUID) -> ConfigVersion | None:
        async with self._lock:
            return self._versions.get(version_id)

    async def get_version_number(self, version: int) -> ConfigVersion | None:
        async with self._lock:
            return next((item for item in self._versions.values() if item.version == version), None)

    async def get_published(self) -> ConfigVersion | None:
        async with self._lock:
            return next(
                (
                    item
                    for item in self._versions.values()
                    if item.status is ConfigVersionStatus.PUBLISHED
                ),
                None,
            )

    async def create_draft(
        self, *, note: str | None, values: tuple[ConfigEntry, ...], actor_id: UUID | None
    ) -> ConfigVersion:
        del actor_id
        async with self._lock:
            snapshot = ConfigVersion(
                id=uuid4(),
                version=self._next_version,
                status=ConfigVersionStatus.DRAFT,
                note=note,
                created_at=datetime.now(UTC),
                published_at=None,
                values=values,
            )
            self._next_version += 1
            self._versions[snapshot.id] = snapshot
            return snapshot

    async def publish(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion:
        del actor_id
        async with self._lock:
            target = self._require(version_id)
            if target.status is not ConfigVersionStatus.DRAFT:
                raise ConfigurationConflictError("只有草稿状态的配置版本可以发布")
            for existing_id, existing in tuple(self._versions.items()):
                if existing.status is ConfigVersionStatus.PUBLISHED:
                    self._versions[existing_id] = replace(
                        existing, status=ConfigVersionStatus.SUPERSEDED
                    )
            published = replace(
                target,
                status=ConfigVersionStatus.PUBLISHED,
                published_at=datetime.now(UTC),
            )
            self._versions[target.id] = published
            return published

    async def rollback(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion:
        del actor_id
        async with self._lock:
            target = self._require(version_id)
            if target.status is ConfigVersionStatus.DRAFT:
                raise ConfigurationConflictError("草稿不能作为回滚目标")
            for existing_id, existing in tuple(self._versions.items()):
                if existing.status is ConfigVersionStatus.PUBLISHED:
                    self._versions[existing_id] = replace(
                        existing, status=ConfigVersionStatus.SUPERSEDED
                    )
            rolled_back = ConfigVersion(
                id=uuid4(),
                version=self._next_version,
                status=ConfigVersionStatus.PUBLISHED,
                note=f"回滚到配置 v{target.version}",
                created_at=datetime.now(UTC),
                published_at=datetime.now(UTC),
                values=target.values,
            )
            self._next_version += 1
            self._versions[rolled_back.id] = rolled_back
            return rolled_back

    def _require(self, version_id: UUID) -> ConfigVersion:
        try:
            return self._versions[version_id]
        except KeyError as error:
            raise ConfigurationNotFoundError(f"配置版本不存在：{version_id}") from error


class SqlAlchemyConfigurationRepository:
    """提供原子发布和回滚语义的 PostgreSQL 适配器。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_versions(self) -> tuple[ConfigVersion, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ConfigurationVersion).order_by(ConfigurationVersion.version.desc())
                )
            ).all()
            return tuple([await self._snapshot(session, row) for row in rows])

    async def get_version(self, version_id: UUID) -> ConfigVersion | None:
        async with self._session_factory() as session:
            row = await session.get(ConfigurationVersion, version_id)
            return None if row is None else await self._snapshot(session, row)

    async def get_version_number(self, version: int) -> ConfigVersion | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ConfigurationVersion).where(ConfigurationVersion.version == version)
            )
            return None if row is None else await self._snapshot(session, row)

    async def get_published(self) -> ConfigVersion | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ConfigurationVersion).where(
                    ConfigurationVersion.status == ConfigVersionStatus.PUBLISHED.value
                )
            )
            return None if row is None else await self._snapshot(session, row)

    async def create_draft(
        self, *, note: str | None, values: tuple[ConfigEntry, ...], actor_id: UUID | None
    ) -> ConfigVersion:
        async with self._session_factory() as session, session.begin():
            await self._lock_version_sequence(session)
            row = ConfigurationVersion(
                id=uuid4(),
                version=await self._next_version(session),
                status=ConfigVersionStatus.DRAFT.value,
                note=note,
                created_by=actor_id,
            )
            session.add(row)
            session.add_all([self._value_model(row.id, entry) for entry in values])
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action="configuration.draft_created",
                    resource_type="configuration_version",
                    resource_id=str(row.id),
                    detail={"version": row.version, "value_count": len(values)},
                )
            )
            await session.flush()
            return await self._snapshot(session, row)

    async def publish(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion:
        async with self._session_factory() as session, session.begin():
            await self._lock_version_sequence(session)
            row = await self._locked_version(session, version_id)
            if row.status != ConfigVersionStatus.DRAFT.value:
                raise ConfigurationConflictError("只有草稿状态的配置版本可以发布")
            await session.execute(
                update(ConfigurationVersion)
                .where(ConfigurationVersion.status == ConfigVersionStatus.PUBLISHED.value)
                .values(status=ConfigVersionStatus.SUPERSEDED.value)
            )
            row.status = ConfigVersionStatus.PUBLISHED.value
            row.published_at = datetime.now(UTC)
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action="configuration.published",
                    resource_type="configuration_version",
                    resource_id=str(row.id),
                    detail={"version": row.version},
                )
            )
            await session.flush()
            return await self._snapshot(session, row)

    async def rollback(self, version_id: UUID, *, actor_id: UUID | None) -> ConfigVersion:
        async with self._session_factory() as session, session.begin():
            await self._lock_version_sequence(session)
            target = await self._locked_version(session, version_id)
            if target.status == ConfigVersionStatus.DRAFT.value:
                raise ConfigurationConflictError("草稿不能作为回滚目标")
            target_snapshot = await self._snapshot(session, target)
            await session.execute(
                update(ConfigurationVersion)
                .where(ConfigurationVersion.status == ConfigVersionStatus.PUBLISHED.value)
                .values(status=ConfigVersionStatus.SUPERSEDED.value)
            )
            row = ConfigurationVersion(
                id=uuid4(),
                version=await self._next_version(session),
                status=ConfigVersionStatus.PUBLISHED.value,
                note=f"回滚到配置 v{target.version}",
                created_by=actor_id,
                published_at=datetime.now(UTC),
            )
            session.add(row)
            session.add_all([self._value_model(row.id, entry) for entry in target_snapshot.values])
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action="configuration.rolled_back",
                    resource_type="configuration_version",
                    resource_id=str(row.id),
                    detail={"version": row.version, "source_version": target.version},
                )
            )
            await session.flush()
            return await self._snapshot(session, row)

    @staticmethod
    async def _lock_version_sequence(session: AsyncSession) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _CONFIGURATION_LOCK_ID},
        )

    @staticmethod
    async def _next_version(session: AsyncSession) -> int:
        current = await session.scalar(select(func.max(ConfigurationVersion.version)))
        return (current or 0) + 1

    @staticmethod
    async def _locked_version(session: AsyncSession, version_id: UUID) -> ConfigurationVersion:
        statement: Select[tuple[ConfigurationVersion]] = (
            select(ConfigurationVersion)
            .where(ConfigurationVersion.id == version_id)
            .with_for_update()
        )
        row = await session.scalar(statement)
        if row is None:
            raise ConfigurationNotFoundError(f"配置版本不存在：{version_id}")
        return row

    @staticmethod
    def _value_model(version_id: UUID, entry: ConfigEntry) -> ConfigurationValue:
        return ConfigurationValue(
            id=uuid4(),
            version_id=version_id,
            scope_type=entry.scope_type.value,
            scope_id=entry.scope_id,
            key=entry.key,
            value=entry.value,
            secret_reference_id=None,
        )

    @staticmethod
    async def _snapshot(session: AsyncSession, row: ConfigurationVersion) -> ConfigVersion:
        value_rows = (
            await session.scalars(
                select(ConfigurationValue)
                .where(ConfigurationValue.version_id == row.id)
                .order_by(
                    ConfigurationValue.key,
                    ConfigurationValue.scope_type,
                    ConfigurationValue.scope_id,
                )
            )
        ).all()
        return ConfigVersion(
            id=row.id,
            version=row.version,
            status=ConfigVersionStatus(row.status),
            note=row.note,
            created_at=row.created_at,
            published_at=row.published_at,
            values=tuple(
                ConfigEntry(
                    key=value_row.key,
                    scope_type=ConfigScope(value_row.scope_type),
                    scope_id=value_row.scope_id,
                    value=cast(JsonValue, value_row.value),
                )
                for value_row in value_rows
            ),
        )
