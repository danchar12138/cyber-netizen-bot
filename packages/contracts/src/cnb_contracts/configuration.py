"""Configuration management API contracts."""

from pydantic import BaseModel, ConfigDict, Field

from cnb_domain import ConfigScope, ConfigValueKind, JsonValue


class ConfigDefinitionResponse(BaseModel):
    """Safe configuration metadata exposed to administration clients."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    section: str
    label: str
    description: str
    value_kind: ConfigValueKind
    default: JsonValue
    scopes: tuple[ConfigScope, ...]
    secret: bool
    hot_reload: bool
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()


class ConfigRegistryResponse(BaseModel):
    """Versioned snapshot of registered configuration definitions."""

    schema_version: str = Field(default="1")
    definitions: tuple[ConfigDefinitionResponse, ...]
