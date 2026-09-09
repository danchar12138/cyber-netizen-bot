"""配置管理 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from cnb_domain import ConfigScope, ConfigValueKind, ConfigVersionStatus, JsonValue


class ConfigDefinitionResponse(BaseModel):
    """向管理客户端公开的安全配置元数据。"""

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
    """已注册配置定义的版本化快照。"""

    schema_version: str = Field(default="1")
    definitions: tuple[ConfigDefinitionResponse, ...]


class ConfigValueInput(BaseModel):
    """创建草稿时提交的单个非密钥作用域值。"""

    key: str = Field(min_length=1, max_length=255)
    scope_type: ConfigScope = ConfigScope.SYSTEM
    scope_id: UUID | None = None
    value: JsonValue


class ConfigDraftCreate(BaseModel):
    """创建不可变草稿快照的命令。"""

    note: str | None = Field(default=None, max_length=1000)
    values: tuple[ConfigValueInput, ...]


class ConfigValueResponse(BaseModel):
    """配置版本中存储的单个安全非密钥值。"""

    key: str
    scope_type: ConfigScope
    scope_id: UUID | None
    value: JsonValue


class ConfigVersionResponse(BaseModel):
    """返回给管理客户端的配置版本。"""

    id: UUID
    version: int
    status: ConfigVersionStatus
    note: str | None
    created_at: datetime
    published_at: datetime | None
    values: tuple[ConfigValueResponse, ...]


class ConfigVersionListResponse(BaseModel):
    """按最新版本优先排列的配置版本历史。"""

    versions: tuple[ConfigVersionResponse, ...]
