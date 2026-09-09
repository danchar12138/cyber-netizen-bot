"""配置管理 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cnb_domain import (
    ConfigDiffKind,
    ConfigScope,
    ConfigValueKind,
    ConfigVersionStatus,
    JsonValue,
    SecretIntegrityStatus,
)


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


class ConfigDifferenceResponse(BaseModel):
    """配置差异中的单个安全非密钥变更。"""

    key: str
    scope_type: ConfigScope
    scope_id: UUID | None
    kind: ConfigDiffKind
    before: JsonValue
    after: JsonValue


class ConfigDiffResponse(BaseModel):
    """发布前可检查的配置版本差异。"""

    base_version: int
    target_version: int
    changes: tuple[ConfigDifferenceResponse, ...]


class EffectiveConfigSourceResponse(BaseModel):
    """最终生效值的版本与作用域来源。"""

    scope_type: ConfigScope | None
    scope_id: UUID | None
    version: int


class EffectiveConfigValueResponse(BaseModel):
    """带来源的最终生效配置值。"""

    key: str
    value: JsonValue
    source: EffectiveConfigSourceResponse


class EffectiveConfigurationResponse(BaseModel):
    """指定运行上下文的最终配置快照。"""

    version: int
    values: tuple[EffectiveConfigValueResponse, ...]


class SecretWriteCommand(BaseModel):
    """写入一个作用域密钥；SecretStr 防止诊断输出意外携带明文。"""

    key: str = Field(min_length=1, max_length=255)
    scope_type: ConfigScope = ConfigScope.SYSTEM
    scope_id: UUID | None = None
    plaintext: SecretStr = Field(min_length=1, max_length=16384)


class SecretRotateCommand(BaseModel):
    """轮换现有密钥引用。"""

    plaintext: SecretStr = Field(min_length=1, max_length=16384)


class SecretMetadataResponse(BaseModel):
    """不含明文和加密信封的密钥管理响应。"""

    id: UUID
    key: str
    scope_type: ConfigScope
    scope_id: UUID | None
    provider: str
    configured: bool = True
    masked_hint: str
    integrity_status: SecretIntegrityStatus
    created_at: datetime
    updated_at: datetime
    last_tested_at: datetime | None


class SecretListResponse(BaseModel):
    """全部已配置密钥的安全元数据。"""

    secrets: tuple[SecretMetadataResponse, ...]
