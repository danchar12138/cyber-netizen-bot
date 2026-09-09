"""Domain primitives for schema-driven runtime configuration."""

from dataclasses import dataclass
from enum import StrEnum

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]


class ConfigScope(StrEnum):
    """Supported override boundaries ordered from broad to specific."""

    SYSTEM = "system"
    TENANT = "tenant"
    AGENT = "agent"
    CHANNEL = "channel"
    USER = "user"


class ConfigValueKind(StrEnum):
    """UI-neutral value kinds understood by configuration clients."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class ConfigDefinition:
    """Definition and safe default for one runtime configuration key."""

    key: str
    section: str
    label: str
    description: str
    value_kind: ConfigValueKind
    default: JsonValue
    scopes: tuple[ConfigScope, ...]
    secret: bool = False
    hot_reload: bool = True
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key or self.key.startswith(".") or self.key.endswith("."):
            raise ValueError("configuration keys must be non-empty dotted names")
        if self.secret != (self.value_kind is ConfigValueKind.SECRET):
            raise ValueError("secret definitions must use the secret value kind")
        if not self.scopes:
            raise ValueError("at least one configuration scope is required")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
