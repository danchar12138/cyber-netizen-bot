"""版本化运行配置与审计的初始持久化模型。"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """共享声明式元数据。"""


class ConfigurationVersion(Base):
    """不可变的配置草稿或已发布快照。"""

    __tablename__ = "configuration_versions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'superseded')",
            name="ck_configuration_versions_status",
        ),
    )


class SecretReference(Base):
    """指向加密或外部密钥材料的不透明引用。"""

    __tablename__ = "secret_references"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_value: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    masked_hint: Mapped[str] = mapped_column(String(128), nullable=False)
    integrity_status: Mapped[str] = mapped_column(String(24), nullable=False, default="untested")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('system', 'tenant', 'agent', 'channel', 'user')",
            name="ck_secret_references_scope_type",
        ),
        CheckConstraint(
            "integrity_status IN ('untested', 'valid', 'invalid')",
            name="ck_secret_references_integrity_status",
        ),
        CheckConstraint(
            "(encrypted_value IS NULL) = (nonce IS NULL)",
            name="ck_secret_references_envelope",
        ),
        UniqueConstraint(
            "key",
            "scope_type",
            "scope_id",
            name="uq_secret_references_scope_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_secret_references_key", "key"),
    )


class ConfigurationValue(Base):
    """不可变配置版本中的单个作用域值。"""

    __tablename__ = "configuration_values"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("configuration_versions.id", ondelete="CASCADE"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict[str, Any] | list[Any] | str | int | float | bool | None] = mapped_column(
        JSONB
    )
    secret_reference_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("secret_references.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('system', 'tenant', 'agent', 'channel', 'user')",
            name="ck_configuration_values_scope_type",
        ),
        CheckConstraint(
            "(value IS NULL) <> (secret_reference_id IS NULL)",
            name="ck_configuration_values_payload",
        ),
        UniqueConstraint(
            "version_id",
            "scope_type",
            "scope_id",
            "key",
            name="uq_configuration_values_scope_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_configuration_values_key", "key"),
    )


class AuditLog(Base):
    """仅追加的管理与策略审计记录。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),
    )


class ApiRequestMetricModel(Base):
    """仅保存路由模板、状态和耗时的 API 请求指标。"""

    __tablename__ = "api_request_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    route: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status_code BETWEEN 100 AND 599", name="ck_api_metrics_status"),
        CheckConstraint("duration_ms >= 0", name="ck_api_metrics_duration"),
        Index("ix_api_metrics_tenant_occurred", "tenant_id", "occurred_at"),
    )


class Tenant(Base):
    """承载全部业务数据隔离边界的租户。"""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_tenants_status"),
    )


class Agent(Base):
    """租户内可参与会话的 Agent。"""

    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_agents_status"),
        Index("ix_agents_tenant", "tenant_id"),
        Index("uq_agents_tenant_name_ci", "tenant_id", func.lower(name), unique=True),
    )


class User(Base):
    """租户内可绑定外部身份的用户。"""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        Index("ix_users_tenant", "tenant_id"),
    )


class ExternalIdentity(Base):
    """经 OIDC 验证并绑定到本地用户的稳定外部主体。"""

    __tablename__ = "external_identities"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
        Index("ix_external_identities_tenant_user", "tenant_id", "user_id"),
    )


class RoleAssignment(Base):
    """由可信 OIDC claim 同步的租户级管理角色。"""

    __tablename__ = "role_assignments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'operator', 'viewer')", name="ck_role_assignments_role"),
        CheckConstraint("source IN ('oidc')", name="ck_role_assignments_source"),
        UniqueConstraint("tenant_id", "user_id", name="uq_role_assignments_tenant_user"),
        Index("ix_role_assignments_tenant_role", "tenant_id", "role"),
    )


class AdminSession(Base):
    """只保存令牌不可逆摘要的 OIDC 管理会话观测记录。"""

    __tablename__ = "admin_sessions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    external_identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("external_identities.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_admin_sessions_tenant_user_seen", "tenant_id", "user_id", "last_seen_at"),
        Index("ix_admin_sessions_expires", "expires_at"),
    )


class DataLifecycleRunModel(Base):
    """不保存用户正文或存储路径的数据生命周期运行证据。"""

    __tablename__ = "data_lifecycle_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    counters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('user_export', 'user_forget', 'retention_cleanup', "
            "'orphan_cleanup', 'backup_restore_drill')",
            name="ck_data_lifecycle_runs_kind",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_data_lifecycle_runs_status",
        ),
        Index("ix_data_lifecycle_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_data_lifecycle_runs_subject", "tenant_id", "subject_user_id", "started_at"),
    )


class ConversationModel(Base):
    """内部 Web Channel 使用的持久化会话。"""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    branched_from_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    branched_from_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "messages.id",
            name="fk_conversations_branch_message",
            ondelete="SET NULL",
            use_alter=True,
        )
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_conversations_status"),
        Index("ix_conversations_tenant_updated", "tenant_id", "updated_at"),
        Index("ix_conversations_member_state", "deleted_at", "pinned_at", "updated_at"),
    )


class ConversationMember(Base):
    """会话与用户之间的显式成员关系。"""

    __tablename__ = "conversation_members"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="owner")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_members_user"),
        Index("ix_conversation_members_user", "user_id"),
    )


class MessageModel(Base):
    """会话中支持幂等接收、流式更新和内容块投影的消息。"""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    sender_type: Mapped[str] = mapped_column(String(24), nullable=False)
    sender_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    client_message_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    edited_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    parts: Mapped[list["MessagePartModel"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MessagePartModel.position",
    )

    __table_args__ = (
        CheckConstraint(
            "sender_type IN ('user', 'agent', 'system')", name="ck_messages_sender_type"
        ),
        CheckConstraint(
            "status IN ('received', 'processing', 'streaming', 'completed', "
            "'suppressed', 'cancelled', 'failed')",
            name="ck_messages_status",
        ),
        Index(
            "uq_messages_client_id",
            "conversation_id",
            "sender_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index(
            "ix_messages_content_search",
            text("to_tsvector('simple', content)"),
            postgresql_using="gin",
        ),
    )


class MessagePartModel(Base):
    """消息内按位置排序的文本、Markdown、图片或文件内容块。"""

    __tablename__ = "message_parts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL")
    )
    content_type: Mapped[str | None] = mapped_column(String(160))
    file_name: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    alt_text: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_message_parts_position"),
        CheckConstraint(
            "kind IN ('text', 'markdown', 'image', 'file')",
            name="ck_message_parts_kind",
        ),
        CheckConstraint(
            "((kind IN ('text', 'markdown') AND text IS NOT NULL "
            "AND attachment_id IS NULL AND content_type IS NULL AND file_name IS NULL "
            "AND size_bytes IS NULL AND sha256 IS NULL) OR "
            "(kind IN ('image', 'file') AND text IS NULL AND content_type IS NOT NULL "
            "AND file_name IS NOT NULL AND size_bytes > 0 "
            "AND sha256 ~ '^[0-9a-f]{64}$'))",
            name="ck_message_parts_shape",
        ),
        UniqueConstraint("message_id", "position", name="uq_message_parts_position"),
        Index("ix_message_parts_tenant_message", "tenant_id", "message_id"),
        Index("ix_message_parts_attachment", "attachment_id"),
    )


class AttachmentModel(Base):
    """MinIO 对象对应的上传与校验元数据。"""

    __tablename__ = "attachments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    client_message_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    validation_error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ready', 'attached', 'rejected', 'deleted')",
            name="ck_attachments_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_attachments_size_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_attachments_sha256"),
        Index("ix_attachments_conversation_created", "conversation_id", "created_at"),
        Index("ix_attachments_expiry", "status", "expires_at"),
        Index("ix_attachments_message", "message_id"),
    )


class AgentRunModel(Base):
    """记录版本快照和模型用量的 Agent Run。"""

    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    trigger_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )
    response_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    persona_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_route_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_profile: Mapped[str] = mapped_column(String(160), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'cancelled', 'failed')",
            name="ck_agent_runs_status",
        ),
        Index("ix_agent_runs_conversation_created", "conversation_id", "created_at"),
        Index("ix_agent_runs_trigger", "trigger_message_id"),
        Index("ix_agent_runs_tenant_completed", "tenant_id", "completed_at"),
    )


class CognitionResourceVersionModel(Base):
    """人格、Prompt、模型、工具与策略的统一不可变版本存储。"""

    __tablename__ = "cognition_resource_versions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('persona', 'prompt', 'model_profile', 'model_route', 'tool', 'policy')",
            name="ck_cognition_resource_versions_kind",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'superseded')",
            name="ck_cognition_resource_versions_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "kind",
            "key",
            "version",
            name="uq_cognition_resource_versions_number",
        ),
        Index(
            "ix_cognition_resource_versions_lookup",
            "tenant_id",
            "agent_id",
            "kind",
            "key",
            "status",
        ),
        Index(
            "uq_cognition_resource_versions_published",
            "tenant_id",
            "agent_id",
            "kind",
            "key",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )


class PersonaStateSnapshotModel(Base):
    """Agent Run 结束决策阶段时的可衰减人格状态。"""

    __tablename__ = "persona_state_snapshots"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    persona_version: Mapped[int] = mapped_column(Integer, nullable=False)
    valence: Mapped[float] = mapped_column(nullable=False)
    arousal: Mapped[float] = mapped_column(nullable=False)
    social_energy: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("valence BETWEEN -1 AND 1", name="ck_persona_state_valence"),
        CheckConstraint("arousal BETWEEN 0 AND 1", name="ck_persona_state_arousal"),
        CheckConstraint("social_energy BETWEEN 0 AND 1", name="ck_persona_state_social_energy"),
        Index(
            "ix_persona_state_conversation_created",
            "tenant_id",
            "agent_id",
            "conversation_id",
            "created_at",
        ),
    )


class RunStepModel(Base):
    """Agent Run 的安全认知阶段摘要。"""

    __tablename__ = "run_steps"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("sequence > 0", name="ck_run_steps_sequence"),
        UniqueConstraint("run_id", "sequence", name="uq_run_steps_sequence"),
        Index("ix_run_steps_tenant_run", "tenant_id", "run_id"),
    )


class ActionCandidateModel(Base):
    """一次运行中经过策略门评估的行动候选。"""

    __tablename__ = "action_candidates"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    reason_summary: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(120))
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    selected: Mapped[bool] = mapped_column(nullable=False, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("sequence > 0", name="ck_action_candidates_sequence"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_action_candidates_confidence"),
        CheckConstraint(
            "action IN ('reply', 'ask', 'wait', 'no_reply', 'tool')",
            name="ck_action_candidates_action",
        ),
        CheckConstraint(
            "risk_level IN ('none', 'low', 'medium', 'high')",
            name="ck_action_candidates_risk",
        ),
        UniqueConstraint("run_id", "sequence", name="uq_action_candidates_sequence"),
        Index("ix_action_candidates_tenant_run", "tenant_id", "run_id"),
    )


class ModelInvocationModel(Base):
    """模型用途路由的单次尝试，不保存请求或响应正文。"""

    __tablename__ = "model_invocations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_microusd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("attempt > 0", name="ck_model_invocations_attempt"),
        CheckConstraint("estimated_cost_microusd >= 0", name="ck_model_invocations_estimated_cost"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'timed_out')",
            name="ck_model_invocations_status",
        ),
        UniqueConstraint("run_id", "purpose", "attempt", name="uq_model_invocations_attempt"),
        Index("ix_model_invocations_tenant_run", "tenant_id", "run_id"),
        Index("ix_model_invocations_tenant_created", "tenant_id", "created_at"),
    )


class EvaluationSuiteModel(Base):
    """当前 Agent 可发布的不可变拟人评测集版本。"""

    __tablename__ = "evaluation_suites"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    minimum_pass_rate: Mapped[float] = mapped_column(nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'superseded')",
            name="ck_evaluation_suites_status",
        ),
        CheckConstraint(
            "minimum_pass_rate BETWEEN 0 AND 100",
            name="ck_evaluation_suites_pass_rate",
        ),
        CheckConstraint(
            "max_output_tokens BETWEEN 64 AND 32768",
            name="ck_evaluation_suites_output_tokens",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "key",
            "version",
            name="uq_evaluation_suites_version",
        ),
        Index(
            "ix_evaluation_suites_lookup",
            "tenant_id",
            "agent_id",
            "key",
            "status",
        ),
        Index(
            "uq_evaluation_suites_published",
            "tenant_id",
            "agent_id",
            "key",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )


class EvaluationCaseModel(Base):
    """评测集版本中按顺序冻结的对话样例。"""

    __tablename__ = "evaluation_cases"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    suite_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_suites.id", ondelete="CASCADE"), nullable=False
    )
    case_key: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_action: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_response: Mapped[str | None] = mapped_column(Text)
    required_phrases: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    forbidden_phrases: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("sort_order > 0", name="ck_evaluation_cases_sort_order"),
        CheckConstraint(
            "expected_action IN ('reply', 'ask', 'wait', 'no_reply', 'tool')",
            name="ck_evaluation_cases_action",
        ),
        UniqueConstraint("suite_id", "case_key", name="uq_evaluation_cases_key"),
        UniqueConstraint("suite_id", "sort_order", name="uq_evaluation_cases_order"),
    )


class EvaluationRunModel(Base):
    """一次自动回放的版本、模型、成本与质量门快照。"""

    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    suite_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_suites.id", ondelete="SET NULL")
    )
    suite_key: Mapped[str] = mapped_column(String(120), nullable=False)
    suite_version: Mapped[int] = mapped_column(Integer, nullable=False)
    suite_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_rate: Mapped[float] = mapped_column(nullable=False)
    gate_passed: Mapped[bool] = mapped_column(nullable=False)
    minimum_pass_rate: Mapped[float] = mapped_column(nullable=False)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    persona_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model_route_version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('completed', 'failed')", name="ck_evaluation_runs_status"),
        CheckConstraint(
            "passed >= 0 AND total > 0 AND passed <= total",
            name="ck_evaluation_runs_counts",
        ),
        CheckConstraint(
            "pass_rate BETWEEN 0 AND 100 AND minimum_pass_rate BETWEEN 0 AND 100",
            name="ck_evaluation_runs_rates",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost_microusd >= 0",
            name="ck_evaluation_runs_usage",
        ),
        Index("ix_evaluation_runs_tenant_created", "tenant_id", "agent_id", "created_at"),
    )


class EvaluationCaseResultModel(Base):
    """一次运行中冻结的单条回答与确定性检查。"""

    __tablename__ = "evaluation_case_results"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_key: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_action: Mapped[str] = mapped_column(String(32), nullable=False)
    actual_action: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_response: Mapped[str | None] = mapped_column(Text)
    reference_response: Mapped[str | None] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_evaluation_results_latency"),
        UniqueConstraint("run_id", "case_key", name="uq_evaluation_results_case"),
        Index("ix_evaluation_results_run", "run_id"),
    )


class BlindReviewAssignmentModel(Base):
    """只在服务端保存候选回答左右位置的匿名评审任务。"""

    __tablename__ = "blind_review_assignments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_case_results.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    candidate_is_a: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("result_id", "reviewer_id", name="uq_blind_assignments_result_reviewer"),
        Index(
            "ix_blind_assignments_reviewer",
            "tenant_id",
            "agent_id",
            "reviewer_id",
        ),
    )


class BlindReviewModel(Base):
    """去盲后保存的偏好与候选/参考双侧评分。"""

    __tablename__ = "blind_reviews"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("blind_review_assignments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_case_results.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    preference: Mapped[str] = mapped_column(String(24), nullable=False)
    candidate_score: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    reference_score: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "preference IN ('candidate', 'reference', 'tie')",
            name="ck_blind_reviews_preference",
        ),
        Index("ix_blind_reviews_tenant_created", "tenant_id", "agent_id", "created_at"),
    )


class ConversationEventModel(Base):
    """供 WebSocket 重放的不可变会话事件。"""

    __tablename__ = "conversation_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_events_sequence"),
        CheckConstraint("sequence > 0", name="ck_conversation_events_sequence_positive"),
        Index("ix_conversation_events_replay", "conversation_id", "sequence"),
    )


class MessageFeedbackModel(Base):
    """用户对 Agent 回复的可更新反馈。"""

    __tablename__ = "message_feedback"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(24), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("rating IN ('positive', 'negative')", name="ck_message_feedback_rating"),
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_user"),
        Index("ix_message_feedback_conversation", "conversation_id", "created_at"),
    )


class EpisodeModel(Base):
    """连续消息形成的可追溯情景单元。"""

    __tablename__ = "episodes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_message_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'closed', 'consolidated')",
            name="ck_episodes_status",
        ),
        Index("ix_episodes_scope_started", "tenant_id", "agent_id", "user_id", "started_at"),
    )


class MemoryModel(Base):
    """长期记忆真相记录；遗忘后正文置空。"""

    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    lineage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    episode_id: Mapped[UUID | None] = mapped_column(ForeignKey("episodes.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    importance: Mapped[float] = mapped_column(nullable=False)
    emotional_weight: Mapped[float] = mapped_column(nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(24), nullable=False)
    confirmation: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_version: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('working', 'episodic', 'semantic', 'relational', "
            "'autobiographical', 'procedural')",
            name="ck_memories_kind",
        ),
        CheckConstraint(
            "visibility IN ('user', 'agent', 'tenant')",
            name="ck_memories_visibility",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive', 'restricted')",
            name="ck_memories_sensitivity",
        ),
        CheckConstraint(
            "confirmation IN ('unconfirmed', 'confirmed', 'disputed')",
            name="ck_memories_confirmation",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'forgotten')",
            name="ck_memories_status",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_memories_confidence"),
        CheckConstraint("importance BETWEEN 0 AND 1", name="ck_memories_importance"),
        CheckConstraint(
            "emotional_weight BETWEEN -1 AND 1",
            name="ck_memories_emotional_weight",
        ),
        CheckConstraint(
            "(visibility <> 'user') OR user_id IS NOT NULL",
            name="ck_memories_user_visibility",
        ),
        CheckConstraint(
            "(status = 'forgotten' AND content IS NULL) OR "
            "(status <> 'forgotten' AND content IS NOT NULL)",
            name="ck_memories_forgotten_content",
        ),
        UniqueConstraint(
            "tenant_id",
            "lineage_id",
            "version",
            name="uq_memories_lineage_version",
        ),
        Index(
            "ix_memories_scope_event",
            "tenant_id",
            "agent_id",
            "user_id",
            "status",
            "event_at",
        ),
        Index("ix_memories_episode", "episode_id"),
        Index(
            "ix_memories_content_fts",
            text("to_tsvector('simple', coalesce(content, ''))"),
            postgresql_using="gin",
        ),
        Index(
            "ix_memories_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )


class MemorySourceModel(Base):
    """记忆来源与是否逐字摘录的明确证据。"""

    __tablename__ = "memory_sources"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    is_verbatim: Mapped[bool] = mapped_column(nullable=False, default=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('message', 'episode', 'user_statement', 'admin_correction', "
            "'reflection', 'import')",
            name="ck_memory_sources_kind",
        ),
        CheckConstraint(
            "(NOT is_verbatim) OR excerpt IS NOT NULL",
            name="ck_memory_sources_verbatim_excerpt",
        ),
        Index("ix_memory_sources_memory", "tenant_id", "memory_id", "created_at"),
    )


class MemoryLinkModel(Base):
    """记忆冲突、替代、派生与普通关联。"""

    __tablename__ = "memory_links"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    target_memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('related_to', 'conflicts_with', 'supersedes', 'derived_from')",
            name="ck_memory_links_kind",
        ),
        CheckConstraint(
            "source_memory_id <> target_memory_id",
            name="ck_memory_links_distinct",
        ),
        UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "kind",
            name="uq_memory_links_direction",
        ),
        Index("ix_memory_links_source", "tenant_id", "source_memory_id"),
        Index("ix_memory_links_target", "tenant_id", "target_memory_id"),
    )


class MemoryEmbeddingModel(Base):
    """支持渐进版本切换的 pgvector 向量。"""

    __tablename__ = "memory_embeddings"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    embedding_version: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(256), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("dimensions = 256", name="ck_memory_embeddings_dimensions"),
        UniqueConstraint(
            "memory_id",
            "embedding_version",
            name="uq_memory_embeddings_version",
        ),
        Index(
            "uq_memory_embeddings_active",
            "memory_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index(
            "ix_memory_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class RelationshipModel(Base):
    """Agent 与单个用户的当前关系快照。"""

    __tablename__ = "relationships"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    affinity: Mapped[float] = mapped_column(nullable=False)
    trust: Mapped[float] = mapped_column(nullable=False)
    familiarity: Mapped[float] = mapped_column(nullable=False)
    interaction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    boundaries: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN ('stranger', 'acquaintance', 'familiar', 'trusted')",
            name="ck_relationships_stage",
        ),
        CheckConstraint("affinity BETWEEN 0 AND 1", name="ck_relationships_affinity"),
        CheckConstraint("trust BETWEEN 0 AND 1", name="ck_relationships_trust"),
        CheckConstraint(
            "familiarity BETWEEN 0 AND 1",
            name="ck_relationships_familiarity",
        ),
        CheckConstraint(
            "interaction_count >= 0",
            name="ck_relationships_interaction_count",
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "user_id",
            name="uq_relationships_scope",
        ),
    )


class RelationshipEventModel(Base):
    """关系变化的只追加安全摘要。"""

    __tablename__ = "relationship_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    relationship_id: Mapped[UUID] = mapped_column(
        ForeignKey("relationships.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    affinity_delta: Mapped[float] = mapped_column(nullable=False)
    trust_delta: Mapped[float] = mapped_column(nullable=False)
    familiarity_delta: Mapped[float] = mapped_column(nullable=False)
    evidence_memory_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "affinity_delta BETWEEN -1 AND 1",
            name="ck_relationship_events_affinity",
        ),
        CheckConstraint(
            "trust_delta BETWEEN -1 AND 1",
            name="ck_relationship_events_trust",
        ),
        CheckConstraint(
            "familiarity_delta BETWEEN -1 AND 1",
            name="ck_relationship_events_familiarity",
        ),
        Index(
            "ix_relationship_events_timeline",
            "tenant_id",
            "relationship_id",
            "created_at",
        ),
    )


class MemoryIndexJobModel(Base):
    """embedding 重建进度和失败状态。"""

    __tablename__ = "memory_index_jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    target_embedding_version: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_items: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_memory_index_jobs_status",
        ),
        CheckConstraint(
            "total_items >= 0 AND processed_items >= 0 AND processed_items <= total_items",
            name="ck_memory_index_jobs_progress",
        ),
        Index("ix_memory_index_jobs_scope", "tenant_id", "agent_id", "created_at"),
    )


class InboxEventModel(Base):
    """Adapter/API 入站事件的租户级幂等真相。"""

    __tablename__ = "inbox_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "background_jobs.id",
            name="fk_inbox_events_job",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'dead_letter', 'canceled')",
            name="ck_inbox_events_status",
        ),
        UniqueConstraint("tenant_id", "event_key", name="uq_inbox_events_tenant_key"),
        UniqueConstraint("job_id", name="uq_inbox_events_job"),
        Index("ix_inbox_events_status", "tenant_id", "status", "received_at"),
    )


class BackgroundJobModel(Base):
    """独立于 Redis 生命周期的可恢复后台任务。"""

    __tablename__ = "background_jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    queue: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_inbox_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inbox_events.id", ondelete="SET NULL")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_base_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_summary: Mapped[str | None] = mapped_column(String(500))
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    replayed_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL")
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('reflection', 'episode_consolidation', 'memory_extraction', "
            "'embedding_rebuild', 'relationship_update', 'scheduled_action')",
            name="ck_background_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'retrying', 'succeeded', 'failed', "
            "'dead_letter', 'canceled')",
            name="ck_background_jobs_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20 "
            "AND attempt_count <= max_attempts",
            name="ck_background_jobs_attempts",
        ),
        CheckConstraint(
            "lease_seconds BETWEEN 1 AND 86400 AND retry_base_seconds BETWEEN 1 AND 86400",
            name="ck_background_jobs_timing",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_background_jobs_lease",
        ),
        UniqueConstraint("tenant_id", "deduplication_key", name="uq_background_jobs_deduplication"),
        UniqueConstraint("source_inbox_id", name="uq_background_jobs_source_inbox"),
        Index("ix_background_jobs_queue_due", "status", "queue", "available_at"),
        Index("ix_background_jobs_tenant_created", "tenant_id", "created_at"),
        Index("ix_background_jobs_tenant_status_available", "tenant_id", "status", "available_at"),
        Index("ix_background_jobs_lease", "status", "lease_expires_at"),
    )


class OutboxEventModel(Base):
    """后台任务的事务投递记录。"""

    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'retrying', "
            "'dead_letter', 'canceled')",
            name="ck_outbox_events_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_outbox_events_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="ck_outbox_events_lease",
        ),
        Index("ix_outbox_events_due", "status", "available_at"),
        Index("ix_outbox_events_job", "tenant_id", "job_id", "created_at"),
    )


class JobAttemptModel(Base):
    """后台任务每次租约执行的安全审计。"""

    __tablename__ = "job_attempts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_summary: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'timed_out', 'canceled')",
            name="ck_job_attempts_status",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_job_attempts_number"),
        UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
        Index("ix_job_attempts_tenant_job", "tenant_id", "job_id", "started_at"),
    )


class ScheduledActionModel(Base):
    """等待策略门评估或等待 P6 渠道发送的主动行为。"""

    __tablename__ = "scheduled_actions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score: Mapped[float | None] = mapped_column()
    social_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('follow_up', 'proactive_message', 'reflection')",
            name="ck_scheduled_actions_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'dispatched', 'completed', 'suppressed', "
            "'canceled', 'expired', 'failed')",
            name="ck_scheduled_actions_status",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 1", name="ck_scheduled_actions_score"
        ),
        CheckConstraint("social_cost BETWEEN 0 AND 20", name="ck_scheduled_actions_social_cost"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_scheduled_actions_idempotency"),
        UniqueConstraint("job_id", name="uq_scheduled_actions_job"),
        Index("ix_scheduled_actions_due", "tenant_id", "status", "scheduled_for"),
        Index("ix_scheduled_actions_user", "tenant_id", "agent_id", "user_id", "created_at"),
    )


class SocialBudgetUsageModel(Base):
    """主动行为每日社交预算的幂等占用。"""

    __tablename__ = "social_budget_usages"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    budget_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_action_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduled_actions.id", ondelete="CASCADE"), nullable=False
    )
    cost: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("cost BETWEEN 0 AND 20", name="ck_social_budget_usages_cost"),
        UniqueConstraint("scheduled_action_id", name="uq_social_budget_usages_action"),
        Index(
            "ix_social_budget_usages_daily",
            "tenant_id",
            "agent_id",
            "user_id",
            "budget_date",
        ),
    )


class WorkerHeartbeatModel(Base):
    """Worker 最近一次进程心跳。"""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    queues: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    current_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_worker_heartbeats_seen", "last_seen_at"),)


class ChannelInstanceModel(Base):
    """归属单一 Agent 且不保存凭证明文的渠道实例。"""

    __tablename__ = "channel_instances"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    health_status: Mapped[str] = mapped_column(String(24), nullable=False)
    health_detail: Mapped[str | None] = mapped_column(String(500))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "platform IN ('web', 'feishu', 'discord', 'telegram')",
            name="ck_channel_instances_platform",
        ),
        CheckConstraint(
            "status IN ('enabled', 'disabled')",
            name="ck_channel_instances_status",
        ),
        CheckConstraint(
            "health_status IN ('healthy', 'degraded', 'not_configured', 'disabled')",
            name="ck_channel_instances_health_status",
        ),
        CheckConstraint(
            "rate_limit_per_minute BETWEEN 1 AND 10000",
            name="ck_channel_instances_rate_limit",
        ),
        Index(
            "uq_channel_instances_tenant_agent_name_ci",
            "tenant_id",
            "agent_id",
            func.lower(name),
            unique=True,
        ),
        Index(
            "ix_channel_instances_tenant_agent_platform",
            "tenant_id",
            "agent_id",
            "platform",
            "status",
        ),
    )


class ChannelDiagnosticEventModel(Base):
    """不保存消息正文和原始平台载荷的渠道事件摘要。"""

    __tablename__ = "channel_diagnostic_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_instances.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    payload_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    degradations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound', 'outbound', 'system')",
            name="ck_channel_diagnostic_events_direction",
        ),
        CheckConstraint(
            "status IN ('accepted', 'delivered', 'degraded', 'rejected', 'failed', 'rate_limited')",
            name="ck_channel_diagnostic_events_status",
        ),
        UniqueConstraint(
            "channel_id",
            "direction",
            "idempotency_key",
            name="uq_channel_diagnostic_events_idempotency",
        ),
        Index(
            "ix_channel_diagnostic_events_tenant_time",
            "tenant_id",
            "occurred_at",
        ),
        Index(
            "ix_channel_diagnostic_events_channel_time",
            "channel_id",
            "occurred_at",
        ),
    )


class ChannelRateLimitWindowModel(Base):
    """数据库原子维护的分钟级渠道发送预算。"""

    __tablename__ = "channel_rate_limit_windows"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_instances.id", ondelete="CASCADE"), primary_key=True
    )
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("used_count BETWEEN 1 AND 10000", name="ck_channel_rate_windows_used"),
        Index("ix_channel_rate_windows_time", "window_started_at"),
    )
