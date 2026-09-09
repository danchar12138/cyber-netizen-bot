"""Configuration registry domain/application tests."""

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
        label="Test",
        description="Test definition",
        value_kind=ConfigValueKind.BOOLEAN,
        default=True,
        scopes=(ConfigScope.SYSTEM,),
    )

    with pytest.raises(ValueError, match="duplicate configuration key"):
        ConfigurationRegistry((definition, definition))


def test_secret_flag_and_kind_must_stay_aligned() -> None:
    with pytest.raises(ValueError, match="secret definitions"):
        ConfigDefinition(
            key="provider.token",
            section="provider",
            label="Token",
            description="Provider token",
            value_kind=ConfigValueKind.STRING,
            default=None,
            scopes=(ConfigScope.SYSTEM,),
            secret=True,
        )


@pytest.mark.parametrize("key", ["", ".broken", "broken."])
def test_configuration_key_must_be_a_non_empty_dotted_name(key: str) -> None:
    with pytest.raises(ValueError, match="configuration keys"):
        ConfigDefinition(
            key=key,
            section="test",
            label="Test",
            description="Test definition",
            value_kind=ConfigValueKind.BOOLEAN,
            default=True,
            scopes=(ConfigScope.SYSTEM,),
        )


def test_configuration_definition_requires_a_scope() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ConfigDefinition(
            key="test.enabled",
            section="test",
            label="Test",
            description="Test definition",
            value_kind=ConfigValueKind.BOOLEAN,
            default=True,
            scopes=(),
        )


def test_configuration_range_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="minimum"):
        ConfigDefinition(
            key="test.limit",
            section="test",
            label="Limit",
            description="Test limit",
            value_kind=ConfigValueKind.INTEGER,
            default=5,
            scopes=(ConfigScope.SYSTEM,),
            minimum=10,
            maximum=1,
        )
