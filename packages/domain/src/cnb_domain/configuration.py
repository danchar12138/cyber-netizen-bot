"""Schema 驱动运行配置的领域原语。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]


class ConfigScope(StrEnum):
    """支持的配置覆盖作用域，按从宽泛到具体排列。"""

    SYSTEM = "system"
    TENANT = "tenant"
    AGENT = "agent"
    CHANNEL = "channel"
    USER = "user"


class ConfigValueKind(StrEnum):
    """配置客户端理解且不依赖具体界面的值类型。"""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"
    SECRET = "secret"


class ConfigVersionStatus(StrEnum):
    """不可变配置快照的生命周期状态。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class ConfigDefinition:
    """单个运行配置键的定义和安全默认值。"""

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
            raise ValueError("配置键必须是非空的点分名称")
        if self.secret != (self.value_kind is ConfigValueKind.SECRET):
            raise ValueError("密钥定义必须使用 secret 值类型")
        if self.secret and self.default is not None:
            raise ValueError("密钥配置不能包含明文默认值")
        if not self.scopes:
            raise ValueError("至少需要一个配置作用域")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("最小值不能大于最大值")


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    """配置快照中存储的单个非密钥作用域值。"""

    key: str
    scope_type: ConfigScope
    value: JsonValue
    scope_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    """草稿或已发布运行配置的不可变视图。"""

    id: UUID
    version: int
    status: ConfigVersionStatus
    note: str | None
    created_at: datetime
    published_at: datetime | None
    values: tuple[ConfigEntry, ...]
