"""多模态内容、Channel Adapter 与渠道控制平面 API 契约。"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, model_validator

from cnb_domain import (
    AlertSeverity,
    BackgroundJobStatus,
    ChannelAlertLifecycleStatus,
    ChannelEventDirection,
    ChannelEventStatus,
    ChannelHealthStatus,
    ChannelInstanceStatus,
    ChannelPlatform,
    ContentBlockKind,
    JsonValue,
)


class ChannelCapabilitiesResponse(BaseModel):
    """Adapter 能力和载荷边界。"""

    text: bool
    markdown: bool
    images: bool
    files: bool
    streaming: bool
    reactions: bool
    threads: bool
    message_edit: bool
    proactive_messages: bool
    max_text_chars: int = Field(gt=0)
    max_blocks: int = Field(gt=0)
    max_attachment_bytes: int = Field(gt=0)
    accepted_content_types: tuple[str, ...]


class MultimodalContentBlockInput(BaseModel):
    """文本正文或已校验附件引用，绝不接受内联二进制和对象地址。"""

    kind: ContentBlockKind
    text: str | None = Field(default=None, max_length=600_000)
    attachment_id: UUID | None = None
    content_type: str | None = Field(default=None, max_length=160)
    file_name: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    alt_text: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        """在应用层能力协商前先拒绝明显不完整的内容块。"""
        if self.kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}:
            if self.text is None or not self.text.strip():
                raise ValueError("文本内容块不能为空")
        elif any(
            value is None
            for value in (
                self.attachment_id,
                self.content_type,
                self.file_name,
                self.size_bytes,
                self.sha256,
            )
        ):
            raise ValueError("图片和文件内容块必须包含完整附件元数据")
        return self


class MultimodalContentBlockResponse(BaseModel):
    """能力协商后的安全内容块。"""

    kind: ContentBlockKind
    text: str | None
    attachment_id: UUID | None
    content_type: str | None
    file_name: str | None
    size_bytes: int | None
    sha256: str | None
    alt_text: str | None


class ChannelCatalogResponse(BaseModel):
    """一个可开发或可配置的 Adapter 描述。"""

    platform: ChannelPlatform
    display_name: str
    implementation_status: Literal["ready", "placeholder"]
    credential_required: bool
    capabilities: ChannelCapabilitiesResponse


class ChannelCatalogListResponse(BaseModel):
    """当前进程注册的全部 Adapter。"""

    items: tuple[ChannelCatalogResponse, ...]


class ChannelInstanceCreate(BaseModel):
    """创建渠道实例；凭证只在本次请求中进入加密存储。"""

    name: str = Field(min_length=1, max_length=120)
    platform: ChannelPlatform
    status: ChannelInstanceStatus = ChannelInstanceStatus.DISABLED
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    credential: SecretStr | None = Field(default=None, min_length=1, max_length=16_384)


class ChannelInstanceUpdate(BaseModel):
    """部分更新渠道公开设置和启停状态。"""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: ChannelInstanceStatus | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10_000)
    settings: dict[str, JsonValue] | None = None
    confirmed: bool


class ChannelCredentialCommand(BaseModel):
    """只写不回显的渠道凭证命令。"""

    credential: SecretStr = Field(min_length=1, max_length=16_384)


class ChannelConfirmedCommand(BaseModel):
    """不可逆管理动作的明确确认。"""

    confirmed: bool


class TelegramWebhookStatusResponse(BaseModel):
    """Telegram Webhook 的安全运营状态摘要，不返回 URL、Secret 或远端错误正文。"""

    channel_id: UUID
    status: ChannelHealthStatus
    configured: bool
    pending_update_count: int = Field(ge=0)
    last_error_at: datetime | None
    last_error_present: bool
    allowed_updates: tuple[str, ...]
    checked_at: datetime


class TelegramWebhookRegisterCommand(BaseModel):
    """注册 Telegram Webhook；URL 仅允许 HTTPS，动作必须显式确认。"""

    webhook_url: str = Field(min_length=1, max_length=2048)
    drop_pending_updates: bool = False
    confirmed: bool = False


class TelegramWebhookClearCommand(BaseModel):
    """清理 Telegram Webhook；是否丢弃积压更新由调用方明确选择。"""

    drop_pending_updates: bool = False
    confirmed: bool = False


class ChannelInstanceResponse(BaseModel):
    """不包含凭证明文、定位符或远端原始响应的渠道视图。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    name: str
    platform: ChannelPlatform
    display_name: str
    implementation_status: Literal["ready", "placeholder"]
    status: ChannelInstanceStatus
    rate_limit_per_minute: int
    settings: dict[str, JsonValue]
    credential_configured: bool
    inbound_webhook_configured: bool
    capabilities: ChannelCapabilitiesResponse
    health_status: ChannelHealthStatus
    health_detail: str | None
    last_checked_at: datetime | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ChannelInstanceListResponse(BaseModel):
    """当前租户所选 Agent 的全部渠道实例。"""

    items: tuple[ChannelInstanceResponse, ...]


class ChannelSimulationCommand(BaseModel):
    """无副作用的平台能力协商模拟。"""

    platform: ChannelPlatform
    blocks: tuple[MultimodalContentBlockInput, ...] = Field(min_length=1, max_length=100)
    request_streaming: bool = False
    thread_id: str | None = Field(default=None, max_length=255)
    edit_message_id: str | None = Field(default=None, max_length=255)
    proactive: bool = False


class ChannelSimulationResponse(BaseModel):
    """模拟后的目标内容与全部降级说明。"""

    platform: ChannelPlatform
    blocks: tuple[MultimodalContentBlockResponse, ...]
    degradations: tuple[str, ...]
    buffered: bool
    thread_preserved: bool
    edit_preserved: bool


class ChannelDeliveryRequest(BaseModel):
    """管理端验证 Adapter 闭环使用的幂等发送命令。"""

    recipient_id: str = Field(min_length=1, max_length=255)
    blocks: tuple[MultimodalContentBlockInput, ...] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_streaming: bool = False
    thread_id: str | None = Field(default=None, max_length=255)
    edit_message_id: str | None = Field(default=None, max_length=255)
    proactive: bool = False


class ChannelDeliveryResponse(BaseModel):
    """不透出平台原始响应的发送结果。"""

    status: ChannelEventStatus
    external_message_id: str
    degradations: tuple[str, ...]
    delivered_at: datetime
    idempotent_replay: bool


class ChannelInboundSimulationCommand(BaseModel):
    """平台模拟器提供给 Adapter 的入站载荷。"""

    payload: dict[str, JsonValue]


class ChannelInboundResponse(BaseModel):
    """归一化的入站事件，仅用于契约与诊断验证。"""

    external_event_id: str
    event_type: str
    sender_external_id: str
    conversation_external_id: str
    blocks: tuple[MultimodalContentBlockResponse, ...]
    occurred_at: datetime
    thread_external_id: str | None


class ChannelDiagnosticEventResponse(BaseModel):
    """不包含消息正文、文件名、凭证和远端响应的诊断事件。"""

    id: UUID
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


class ChannelDiagnosticEventListResponse(BaseModel):
    """最近的渠道诊断事件。"""

    items: tuple[ChannelDiagnosticEventResponse, ...]


class ChannelOperationMetricsResponse(BaseModel):
    """渠道运营时间窗聚合，不包含消息正文、凭证或平台原始响应。"""

    channel_id: UUID
    window_started_at: datetime
    window_ended_at: datetime
    inbound_events: int = Field(ge=0)
    outbound_events: int = Field(ge=0)
    outbound_delivered: int = Field(ge=0)
    outbound_degraded: int = Field(ge=0)
    outbound_failed: int = Field(ge=0)
    outbound_rate_limited: int = Field(ge=0)
    outbound_attempts: int = Field(ge=0)
    outbound_failure_rate_percent: float = Field(ge=0, le=100)
    last_failure_at: datetime | None


class ChannelOperationMetricsListResponse(BaseModel):
    """当前 Agent 渠道运营指标及其统一查询时间窗。"""

    window_started_at: datetime
    window_ended_at: datetime
    items: tuple[ChannelOperationMetricsResponse, ...]


class ChannelErrorMetricResponse(BaseModel):
    """按安全错误码聚合的渠道告警指标。"""

    channel_id: UUID
    error_code: str = Field(min_length=1, max_length=160)
    occurrences: int = Field(ge=1)
    first_occurred_at: datetime
    last_occurred_at: datetime


class ChannelErrorMetricListResponse(BaseModel):
    """当前 Agent 渠道错误指标及其统一查询时间窗。"""

    window_started_at: datetime
    window_ended_at: datetime
    items: tuple[ChannelErrorMetricResponse, ...]


class ChannelHealthSnapshotResponse(BaseModel):
    """渠道健康趋势安全快照，不含 URL、凭证或远端错误正文。"""

    id: UUID
    channel_id: UUID
    platform: ChannelPlatform
    status: ChannelHealthStatus
    configured: bool
    pending_update_count: int = Field(ge=0)
    remote_error_present: bool
    sampled_at: datetime


class ChannelHealthTrendResponse(BaseModel):
    """当前 Agent 渠道健康快照及其统一查询时间窗。"""

    window_started_at: datetime
    window_ended_at: datetime
    items: tuple[ChannelHealthSnapshotResponse, ...]


class ChannelAlertResponse(BaseModel):
    """渠道告警策略结果，仅包含聚合数值和安全摘要。"""

    channel_id: UUID
    code: str = Field(min_length=1, max_length=120)
    error_code: str | None = Field(default=None, max_length=160)
    severity: AlertSeverity
    title: str
    summary: str
    occurrences: int = Field(ge=1)
    current_value: float = Field(ge=0)
    threshold_value: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=24)
    first_occurred_at: datetime
    last_occurred_at: datetime
    cooldown_until: datetime
    alert_key: str = Field(min_length=1, max_length=400)
    disposition_status: Literal["acknowledged", "suppressed"] | None = None
    disposition_reason: str | None = Field(default=None, max_length=500)
    disposition_expires_at: datetime | None = None


class ChannelAlertDispositionCommand(BaseModel):
    """告警确认或抑制命令，必须由调用方显式确认。"""

    alert_key: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    confirmed: bool = False


class ChannelAlertDispositionResponse(BaseModel):
    """告警处置结果，不包含消息正文或敏感配置。"""

    alert_key: str
    channel_id: UUID
    status: Literal["acknowledged", "suppressed", "cleared"]
    reason: str
    expires_at: datetime | None
    updated_at: datetime


class ChannelAlertDispositionClearCommand(BaseModel):
    """解除告警处置命令。"""

    alert_key: str = Field(min_length=1, max_length=400)
    confirmed: bool = False


class ChannelAlertListResponse(BaseModel):
    """渠道活动告警及其统一聚合窗口。"""

    window_started_at: datetime
    window_ended_at: datetime
    items: tuple[ChannelAlertResponse, ...]


class ChannelAlertLifecycleResponse(BaseModel):
    """渠道告警持久化生命周期，不包含业务正文或通知目标。"""

    id: UUID
    channel_id: UUID
    alert_key: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=120)
    error_code: str | None = Field(default=None, max_length=160)
    status: ChannelAlertLifecycleStatus
    severity: AlertSeverity
    occurrences: int = Field(ge=1)
    current_value: float = Field(ge=0)
    threshold_value: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=24)
    first_occurred_at: datetime
    last_occurred_at: datetime
    last_evaluated_at: datetime
    escalated_at: datetime | None
    escalation_level: int = Field(ge=0, le=3)
    last_escalated_at: datetime | None
    resolved_at: datetime | None
    recovery_duration_seconds: int | None = Field(default=None, ge=0)


class ChannelAlertLifecycleListResponse(BaseModel):
    """当前 Agent 的可筛选告警生命周期。"""

    items: tuple[ChannelAlertLifecycleResponse, ...]


class ChannelAlertLifecycleTrendPointResponse(BaseModel):
    """一个固定时间桶内的生命周期变化。"""

    bucket_started_at: datetime
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)


class ChannelAlertLifecycleMetricsResponse(BaseModel):
    """当前 Agent 的生命周期聚合与趋势。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)
    mean_recovery_seconds: float = Field(ge=0)
    p95_recovery_seconds: int = Field(ge=0)
    trend: tuple[ChannelAlertLifecycleTrendPointResponse, ...]


class AlertPolicySimulationCommand(BaseModel):
    """无副作用的当前 Agent 告警升级策略模拟输入。"""

    severity: AlertSeverity
    duration_minutes: int = Field(ge=0, le=525_600)
    current_level: int = Field(ge=0, le=3)
    evaluated_at: datetime


class AlertPolicySimulationStepResponse(BaseModel):
    """一个升级等级的安全模拟明细。"""

    level: int = Field(ge=1, le=3)
    threshold_minutes: int = Field(ge=1)
    adapter: str = Field(min_length=1, max_length=64)
    eligible: bool
    reached: bool
    completed: bool


class AlertPolicySimulationResponse(BaseModel):
    """不包含通知目标或 Secret 的策略模拟结果。"""

    enabled: bool
    severity: AlertSeverity
    duration_minutes: int = Field(ge=0)
    current_level: int = Field(ge=0, le=3)
    maximum_level: int = Field(ge=0, le=3)
    matched_level: int = Field(ge=0, le=3)
    target_level: int | None = Field(default=None, ge=1, le=3)
    adapter: str | None = Field(default=None, max_length=64)
    on_call: bool
    evaluated_at: datetime
    local_time: datetime
    timezone: str = Field(min_length=1, max_length=255)
    reason_code: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=255)
    steps: tuple[AlertPolicySimulationStepResponse, ...]


class ChannelAlertNotificationCommand(BaseModel):
    """告警摘要 Webhook 投递命令，必须显式确认。"""

    window_minutes: int = Field(default=60, ge=5, le=1_440)
    adapter: str | None = Field(default=None, min_length=1, max_length=64)
    confirmed: bool = False


class ChannelAlertNotificationJobResponse(BaseModel):
    """已加入可靠通知队列的任务摘要。"""

    job_id: UUID
    status: BackgroundJobStatus
    queue: str
    deduplication_key: str
    available_at: datetime
    created_at: datetime


class ChannelAlertNotificationResponse(BaseModel):
    """告警通知投递的安全结果，不包含地址、密钥或远端正文。"""

    delivered: bool
    alert_count: int = Field(ge=0)
    attempts: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=64)
    elapsed_ms: int = Field(ge=0)
    status_code: int | None = Field(default=None, ge=100, le=599)


class NotificationDeliveryTimelineItemResponse(BaseModel):
    """单条通知任务的安全状态投影，不公开任务载荷。"""

    job_id: UUID
    status: BackgroundJobStatus
    adapter: str = Field(min_length=1, max_length=64)
    event: Literal["active", "escalation", "recovery", "unknown"]
    alert_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=20)
    consecutive_failures: int = Field(ge=0)
    last_error_code: str | None = Field(default=None, max_length=160)
    delivered: bool | None
    status_code: int | None = Field(default=None, ge=100, le=599)
    elapsed_ms: int | None = Field(default=None, ge=0)
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class NotificationDeliveryTimelineResponse(BaseModel):
    """当前 Agent 的通知投递汇总和可筛选时间线。"""

    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    retrying: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_letters: int = Field(ge=0)
    current_consecutive_failures: int = Field(ge=0)
    last_succeeded_at: datetime | None
    items: tuple[NotificationDeliveryTimelineItemResponse, ...]


class ModelCapabilityResponse(BaseModel):
    """用于渠道规划的模型输入输出能力矩阵行。"""

    provider: str
    model_family: str
    text_input: bool
    image_input: bool
    document_input: bool
    streaming: bool
    structured_output: bool
    tool_calling: bool


class ModelCapabilityMatrixResponse(BaseModel):
    """内置 Provider 的能力矩阵。"""

    items: tuple[ModelCapabilityResponse, ...]
