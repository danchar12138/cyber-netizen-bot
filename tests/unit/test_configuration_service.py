"""配置版本应用服务测试。"""

from uuid import uuid4

import pytest

from cnb_application import (
    ConfigurationConflictError,
    ConfigurationService,
    ConfigurationValidationError,
    build_default_registry,
)
from cnb_domain import ConfigDiffKind, ConfigEntry, ConfigScope, ConfigVersionStatus
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


async def test_configuration_package_import_creates_a_validated_draft(
    service: ConfigurationService,
) -> None:
    imported = await service.import_draft(
        package_format="cnb-runtime-configuration",
        schema_version="1",
        source_version=7,
        source_note="生产基线",
        values=(
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=16,
            ),
        ),
    )

    assert imported.status is ConfigVersionStatus.DRAFT
    assert imported.note == "从配置包 v7 导入：生产基线"
    assert imported.values[0].value == 16


@pytest.mark.parametrize(
    ("package_format", "schema_version", "message"),
    [
        ("unknown-format", "1", "不支持的配置包格式"),
        ("cnb-runtime-configuration", "99", "不支持的配置包 Schema 版本"),
    ],
)
async def test_configuration_package_import_rejects_incompatible_documents(
    service: ConfigurationService,
    package_format: str,
    schema_version: str,
    message: str,
) -> None:
    with pytest.raises(ConfigurationValidationError, match=message):
        await service.import_draft(
            package_format=package_format,
            schema_version=schema_version,
            source_version=1,
            source_note=None,
            values=(),
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            (
                ConfigEntry("memory.recall.limit", ConfigScope.SYSTEM, 20),
                ConfigEntry("memory.recall.candidate_pool", ConfigScope.SYSTEM, 10),
            ),
            "候选池不能小于",
        ),
        (
            tuple(
                ConfigEntry(key, ConfigScope.SYSTEM, 0.0)
                for key in (
                    "memory.recall.full_text_weight",
                    "memory.recall.semantic_weight",
                    "memory.recall.recency_weight",
                    "memory.recall.importance_weight",
                    "memory.recall.relationship_weight",
                )
            ),
            "权重不能全部为 0",
        ),
    ],
)
async def test_memory_recall_settings_are_cross_validated(
    service: ConfigurationService,
    values: tuple[ConfigEntry, ...],
    message: str,
) -> None:
    with pytest.raises(ConfigurationValidationError, match=message):
        await service.create_draft(note=None, values=values)


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


async def test_effective_configuration_respects_scope_precedence_and_version() -> None:
    repository = MemoryConfigurationRepository()
    configuration = ConfigurationService(build_default_registry(), repository)
    tenant_id = uuid4()
    agent_id = uuid4()
    first = await configuration.create_draft(
        note="作用域覆盖",
        values=(
            ConfigEntry(
                key="model.chat.max_output_tokens",
                scope_type=ConfigScope.SYSTEM,
                value=512,
            ),
            ConfigEntry(
                key="model.chat.max_output_tokens",
                scope_type=ConfigScope.TENANT,
                scope_id=tenant_id,
                value=768,
            ),
            ConfigEntry(
                key="model.chat.max_output_tokens",
                scope_type=ConfigScope.AGENT,
                scope_id=agent_id,
                value=900,
            ),
        ),
    )
    published = await configuration.publish(first.id)

    effective = await configuration.resolve_effective(tenant_id=tenant_id, agent_id=agent_id)
    other_agent = await configuration.resolve_effective(tenant_id=tenant_id, agent_id=uuid4())
    builtin = await configuration.resolve_effective(tenant_id=tenant_id, version=0)

    assert effective.version == published.version
    assert effective.values["model.chat.max_output_tokens"] == 900
    assert effective.sources["model.chat.max_output_tokens"].scope_type is ConfigScope.AGENT
    assert effective.sources["model.chat.max_output_tokens"].scope_id == agent_id
    assert effective.sources["model.chat.max_output_tokens"].version == published.version
    assert other_agent.values["model.chat.max_output_tokens"] == 768
    assert other_agent.sources["model.chat.max_output_tokens"].scope_type is ConfigScope.TENANT
    assert builtin.version == 0
    assert builtin.values["model.chat.max_output_tokens"] == 1024
    assert builtin.sources["model.chat.max_output_tokens"].scope_type is None
    assert builtin.sources["model.chat.max_output_tokens"].version == 0


async def test_derived_draft_merges_overrides_into_the_complete_published_snapshot() -> None:
    repository = MemoryConfigurationRepository()
    configuration = ConfigurationService(build_default_registry(), repository)
    agent_id = uuid4()
    original = await configuration.create_draft(
        note="完整发布基线",
        values=(
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=12,
            ),
            ConfigEntry(
                key="alerts.recommendation.long_running_minutes",
                scope_type=ConfigScope.AGENT,
                scope_id=agent_id,
                value=120,
            ),
        ),
    )
    published = await configuration.publish(original.id)

    derived = await configuration.create_derived_draft(
        expected_base_version=published.version,
        note=" 告警建议离线校准 ",
        overrides=(
            ConfigEntry(
                key="alerts.recommendation.long_running_minutes",
                scope_type=ConfigScope.AGENT,
                scope_id=agent_id,
                value=150,
            ),
        ),
    )

    assert derived.status is ConfigVersionStatus.DRAFT
    assert derived.note == "告警建议离线校准"
    assert {(item.key, item.scope_type, item.scope_id, item.value) for item in derived.values} == {
        ("memory.recall.limit", ConfigScope.SYSTEM, None, 12),
        (
            "alerts.recommendation.long_running_minutes",
            ConfigScope.AGENT,
            agent_id,
            150,
        ),
    }
    effective = await configuration.resolve_effective(tenant_id=uuid4(), agent_id=agent_id)
    assert effective.version == published.version
    assert effective.values["alerts.recommendation.long_running_minutes"] == 120


async def test_derived_draft_rejects_a_stale_published_version() -> None:
    repository = MemoryConfigurationRepository()
    configuration = ConfigurationService(build_default_registry(), repository)
    first = await configuration.create_draft(note="基线", values=())
    published = await configuration.publish(first.id)

    with pytest.raises(ConfigurationConflictError, match="生效配置已从 v0 变更为 v1"):
        await configuration.create_derived_draft(
            expected_base_version=0,
            note="过期校准",
            overrides=(
                ConfigEntry(
                    key="alerts.recommendation.long_running_minutes",
                    scope_type=ConfigScope.SYSTEM,
                    value=150,
                ),
            ),
        )

    assert (await repository.get_published()) == published
    assert len(await repository.list_versions()) == 1


async def test_diff_preview_reports_added_changed_and_removed_values() -> None:
    repository = MemoryConfigurationRepository()
    configuration = ConfigurationService(build_default_registry(), repository)
    base_draft = await configuration.create_draft(
        note="差异基线",
        values=(
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=12,
            ),
            ConfigEntry(
                key="proactive.enabled",
                scope_type=ConfigScope.SYSTEM,
                value=False,
            ),
        ),
    )
    base = await configuration.publish(base_draft.id)
    target = await configuration.create_draft(
        note="差异目标",
        values=(
            ConfigEntry(
                key="memory.recall.limit",
                scope_type=ConfigScope.SYSTEM,
                value=20,
            ),
            ConfigEntry(
                key="cognition.reflection.enabled",
                scope_type=ConfigScope.SYSTEM,
                value=False,
            ),
        ),
    )

    preview = await configuration.preview_diff(target.id, base_version_id=base.id)

    assert preview.base_version == 1
    assert preview.target_version == 2
    assert {(item.key, item.kind) for item in preview.changes} == {
        ("cognition.reflection.enabled", ConfigDiffKind.ADDED),
        ("memory.recall.limit", ConfigDiffKind.CHANGED),
        ("proactive.enabled", ConfigDiffKind.REMOVED),
    }


async def test_publish_revalidates_stored_draft_against_current_registry() -> None:
    repository = MemoryConfigurationRepository()
    draft = await repository.create_draft(
        note="绕过旧注册表保存的草稿",
        values=(
            ConfigEntry(
                key="removed.setting",
                scope_type=ConfigScope.SYSTEM,
                value=True,
            ),
        ),
        actor_id=None,
    )
    configuration = ConfigurationService(build_default_registry(), repository)

    with pytest.raises(ConfigurationValidationError, match="未知配置键"):
        await configuration.publish(draft.id)

    unchanged = await repository.get_version(draft.id)
    assert unchanged is not None
    assert unchanged.status is ConfigVersionStatus.DRAFT
