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
