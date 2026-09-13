"""多模态内容、渠道实例和安全诊断的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue


class ChannelPlatform(StrEnum):
    """稳定的平台标识；Telegram 已提供正式出站实现。"""

    WEB = "web"
    FEISHU = "feishu"
    DISCORD = "discord"
    TELEGRAM = "telegram"


class ChannelInstanceStatus(StrEnum):
    """管理员控制的渠道实例启停状态。"""

    ENABLED = "enabled"
    DISABLED = "disabled"


class ChannelHealthStatus(StrEnum):
    """最近一次无密钥连接检查结果。"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"


class ContentBlockKind(StrEnum):
    """跨模型和渠道传递的稳定内容块类型。"""

    TEXT = "text"
    MARKDOWN = "markdown"
    IMAGE = "image"
    FILE = "file"


class ChannelEventDirection(StrEnum):
    """渠道诊断事件相对于 Agent 网关的方向。"""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    SYSTEM = "system"


class ChannelEventStatus(StrEnum):
    """标准化事件或发送尝试的安全结果。"""

    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    """Adapter 能够可靠承诺的能力和静态载荷上限。"""

    text: bool = True
    markdown: bool = False
    images: bool = False
    files: bool = False
    streaming: bool = False
    reactions: bool = False
    threads: bool = False
    message_edit: bool = False
    proactive_messages: bool = False
    max_text_chars: int = 4_000
    max_blocks: int = 20
    max_attachment_bytes: int = 25 * 1024 * 1024
    accepted_content_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MultimodalContentBlock:
    """只引用已校验附件，不携带二进制正文或对象存储凭证。"""

    kind: ContentBlockKind
    text: str | None = None
    attachment_id: UUID | None = None
    content_type: str | None = None
    file_name: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    alt_text: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelInstance:
    """一个归属单一 Agent 且不直接保存凭证明文的渠道实例。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    name: str
    platform: ChannelPlatform
    status: ChannelInstanceStatus
    rate_limit_per_minute: int
    settings: dict[str, JsonValue]
    health_status: ChannelHealthStatus
    health_detail: str | None
    last_checked_at: datetime | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelDiagnosticEvent:
    """不保存消息正文、凭证或远端原始载荷的渠道诊断记录。"""

    id: UUID
    tenant_id: UUID
    channel_id: UUID
    direction: ChannelEventDirection
    event_type: str
    status: ChannelEventStatus
    external_event_id: str | None
    idempotency_key: str
    external_message_id: str | None
    payload_summary: dict[str, JsonValue]
    error_code: str | None
    degradations: tuple[str, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelOperationMetrics:
    """渠道运营时间窗聚合，不携带消息正文或平台原始载荷。"""

    channel_id: UUID
    window_started_at: datetime
    window_ended_at: datetime
    inbound_events: int
    outbound_events: int
    outbound_delivered: int
    outbound_degraded: int
    outbound_failed: int
    outbound_rate_limited: int
    last_failure_at: datetime | None

    @property
    def outbound_attempts(self) -> int:
        """出站尝试总量，供成功率和失败率计算复用。"""
        return (
            self.outbound_delivered
            + self.outbound_degraded
            + self.outbound_failed
            + self.outbound_rate_limited
        )

    @property
    def outbound_failure_rate_percent(self) -> float:
        attempts = self.outbound_attempts
        return (
            round(
                (self.outbound_failed + self.outbound_rate_limited) * 100 / attempts,
                4,
            )
            if attempts
            else 0.0
        )
