"""配置版本应用服务测试。"""

from uuid import uuid4

import pytest

from cnb_application import (
    ConfigurationConflictError,
    ConfigurationService,
    ConfigurationValidationError,
    build_default_registry,
)
from cnb_domain import ConfigEntry, ConfigScope
from cnb_infrastructure import MemoryConfigurationRepository


@pytest.fixture
def service() -> ConfigurationService:
    return ConfigurationService(build_default_registry(), MemoryConfigurationRepository())


async def test_duplicate_values_are_rejected(service: ConfigurationService) -> None:
    value = ConfigEntry(
        key="memory.recall.limit",
        scope_type=ConfigScope.SYSTEM,
        value=12,
    )

    with pytest.raises(ConfigurationConflictError, match="重复"):
        await service.create_draft(note=None, values=(value, value))


async def test_unknown_keys_are_rejected(service: ConfigurationService) -> None:
    with pytest.raises(ConfigurationValidationError, match="未知"):
        await service.create_draft(
            note=None,
            values=(
                ConfigEntry(
                    key="unknown.value",
                    scope_type=ConfigScope.SYSTEM,
                    value=True,
                ),
            ),
        )


async def test_only_drafts_can_be_published(service: ConfigurationService) -> None:
    draft = await service.create_draft(note=None, values=())
    published = await service.publish(draft.id)

    with pytest.raises(ConfigurationConflictError, match="草稿"):
        await service.publish(published.id)


async def test_rollback_creates_a_new_version_without_rewriting_history(
    service: ConfigurationService,
) -> None:
    first_draft = await service.create_draft(
        note="初始版本",
        values=(
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=12,
            ),
        ),
    )
    first_published = await service.publish(first_draft.id)
    second_draft = await service.create_draft(
        note="调整召回数量",
        values=(
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=20,
            ),
        ),
    )
    await service.publish(second_draft.id)

    rolled_back = await service.rollback(first_published.id)
    history = await service.list_versions()

    assert rolled_back.version == 3
    assert rolled_back.values == first_published.values
    assert rolled_back.note == "回滚到配置 v1"
    assert [item.version for item in history] == [3, 2, 1]
    assert history[2].id == first_published.id


async def test_draft_cannot_be_used_as_rollback_target(service: ConfigurationService) -> None:
    draft = await service.create_draft(note=None, values=())

    with pytest.raises(ConfigurationConflictError, match="草稿不能"):
        await service.rollback(draft.id)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value="十二",
            ),
            "integer 类型",
        ),
        (
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=0,
            ),
            "不能小于",
        ),
        (
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=101,
            ),
            "不能大于",
        ),
        (
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.USER,
                scope_id=uuid4(),
                value=12,
            ),
            "不支持作用域",
        ),
        (
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.AGENT,
                value=12,
            ),
            "必须设置作用域 ID",
        ),
    ],
)
async def test_value_and_scope_constraints_are_enforced(
    service: ConfigurationService,
    entry: ConfigEntry,
    message: str,
) -> None:
    with pytest.raises(ConfigurationValidationError, match=message):
        await service.create_draft(note=None, values=(entry,))
