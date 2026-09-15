"""配置注册表领域层与应用层测试。"""

import pytest

from cnb_application import ConfigurationRegistry, build_default_registry
from cnb_domain import ConfigDefinition, ConfigScope, ConfigValueKind


def test_default_registry_is_sorted_and_has_unique_keys() -> None:
    registry = build_default_registry()
    definitions = registry.all()

    assert len(definitions) >= 5
    assert [item.key for item in definitions] == [
        item.key for item in sorted(definitions, key=lambda item: (item.section, item.key))
    ]
    assert len({item.key for item in definitions}) == len(definitions)


def test_data_lifecycle_settings_are_runtime_managed_and_bounded() -> None:
    definitions = {item.key: item for item in build_default_registry().all()}

    expected = {
        "data.retention.deleted_agent_days",
        "data.retention.deleted_conversation_days",
        "data.retention.deleted_attachment_days",
        "data.retention.observability_disposition_event_days",
        "data.retention.observability_replay_review_days",
        "data.retention.orphan_grace_hours",
        "data.retention.batch_size",
        "data.export.max_records",
        "data.export.max_bytes",
        "data.backup.expected_interval_hours",
    }
    assert expected <= definitions.keys()
    for key in expected:
        definition = definitions[key]
        assert definition.section == "data_lifecycle"
        assert definition.value_kind is ConfigValueKind.INTEGER
        assert definition.minimum is not None
        assert definition.maximum is not None
        assert isinstance(definition.default, int)
        assert definition.minimum <= definition.default <= definition.maximum


def test_reflection_policy_is_runtime_managed_with_safe_defaults() -> None:
    definitions = {item.key: item for item in build_default_registry().all()}
    mode = definitions["cognition.reflection.memory_write_mode"]
    assert mode.label == "反思记忆写入策略"
    assert mode.default == "high_precision"
    assert mode.options == ("high_precision", "balanced")

    for key in (
        "cognition.reflection.relationship_positive_step",
        "cognition.reflection.relationship_negative_step",
        "cognition.reflection.familiarity_step",
    ):
        definition = definitions[key]
        assert definition.section == "cognition"
        assert definition.value_kind is ConfigValueKind.NUMBER
        assert definition.minimum == 0
        assert definition.maximum == 0.2


def test_long_conversation_compression_is_runtime_managed_and_bounded() -> None:
    definitions = {item.key: item for item in build_default_registry().all()}
    enabled = definitions["cognition.context.summary_enabled"]
    assert enabled.value_kind is ConfigValueKind.BOOLEAN
    assert enabled.default is True
    assert ConfigScope.AGENT in enabled.scopes

    expected_defaults = {
        "cognition.context.source_message_limit": 240,
        "cognition.context.recent_message_limit": 24,
        "cognition.context.summary_chunk_size": 8,
        "cognition.context.summary_max_levels": 4,
        "cognition.context.summary_token_budget": 4096,
    }
    for key, default in expected_defaults.items():
        definition = definitions[key]
        assert definition.section == "cognition"
        assert definition.value_kind is ConfigValueKind.INTEGER
        assert definition.default == default
        assert definition.minimum is not None
        assert definition.maximum is not None
        assert definition.minimum <= default <= definition.maximum


def test_model_comparison_candidate_limit_is_runtime_managed() -> None:
    definition = {item.key: item for item in build_default_registry().all()}[
        "evaluation.comparison.max_candidates"
    ]

    assert definition.section == "evaluation"
    assert definition.label == "单次模型对比候选上限"
    assert definition.value_kind is ConfigValueKind.INTEGER
    assert definition.default == 4
    assert definition.minimum == 2
    assert definition.maximum == 8
    assert ConfigScope.AGENT in definition.scopes


def test_alert_recovery_notification_is_runtime_managed_and_enabled_by_default() -> None:
    definition = {item.key: item for item in build_default_registry().all()}[
        "alerts.notification.recovery_enabled"
    ]

    assert definition.section == "observability"
    assert definition.label == "启用告警恢复通知"
    assert definition.value_kind is ConfigValueKind.BOOLEAN
    assert definition.default is True
    assert ConfigScope.AGENT in definition.scopes


def test_registry_rejects_duplicate_keys() -> None:
    definition = ConfigDefinition(
        key="test.enabled",
        section="test",
        label="测试开关",
        description="用于测试的配置定义",
        value_kind=ConfigValueKind.BOOLEAN,
        default=True,
        scopes=(ConfigScope.SYSTEM,),
    )

    with pytest.raises(ValueError, match="重复配置键"):
        ConfigurationRegistry((definition, definition))


def test_secret_flag_and_kind_must_stay_aligned() -> None:
    with pytest.raises(ValueError, match="密钥定义"):
        ConfigDefinition(
            key="provider.token",
            section="provider",
            label="访问令牌",
            description="模型服务商访问令牌",
            value_kind=ConfigValueKind.STRING,
            default=None,
            scopes=(ConfigScope.SYSTEM,),
            secret=True,
        )


@pytest.mark.parametrize("key", ["", ".broken", "broken."])
def test_configuration_key_must_be_a_non_empty_dotted_name(key: str) -> None:
    with pytest.raises(ValueError, match="配置键"):
        ConfigDefinition(
            key=key,
            section="test",
            label="测试开关",
            description="用于测试的配置定义",
            value_kind=ConfigValueKind.BOOLEAN,
            default=True,
            scopes=(ConfigScope.SYSTEM,),
        )


def test_configuration_definition_requires_a_scope() -> None:
    with pytest.raises(ValueError, match="至少需要一个"):
        ConfigDefinition(
            key="test.enabled",
            section="test",
            label="测试开关",
            description="用于测试的配置定义",
            value_kind=ConfigValueKind.BOOLEAN,
            default=True,
            scopes=(),
        )


def test_configuration_range_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="最小值"):
        ConfigDefinition(
            key="test.limit",
            section="test",
            label="测试上限",
            description="用于测试的数值上限",
            value_kind=ConfigValueKind.INTEGER,
            default=5,
            scopes=(ConfigScope.SYSTEM,),
            minimum=10,
            maximum=1,
        )


def test_secret_definition_rejects_plaintext_default() -> None:
    with pytest.raises(ValueError, match="明文默认值"):
        ConfigDefinition(
            key="provider.token",
            section="provider",
            label="访问令牌",
            description="模型服务商访问令牌",
            value_kind=ConfigValueKind.SECRET,
            default="不能出现在接口中的密钥",
            scopes=(ConfigScope.SYSTEM,),
            secret=True,
        )
