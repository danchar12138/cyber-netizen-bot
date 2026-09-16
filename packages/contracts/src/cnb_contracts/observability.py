"""性能、成本、服务等级与活动告警接口契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    AlertSeverity,
    ObservabilityAlertCalibrationRule,
    ObservabilityAlertCalibrationStatus,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycleStatus,
    ObservabilityAlertRecommendationAction,
    ObservabilityAlertRecommendationFeedbackDecision,
    ObservabilityAlertRecommendationGuardrail,
    ObservabilityAlertRecommendationPriority,
    ObservabilityAlertRecommendationReason,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayReason,
)


class LatencyPercentilesResponse(BaseModel):
    """毫秒延迟分位数。"""

    p50_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    p99_ms: int = Field(ge=0)


class ApiSloResponse(BaseModel):
    """应用接口请求量、服务端错误率与延迟。"""

    requests: int = Field(ge=0)
    server_errors: int = Field(ge=0)
    error_rate_percent: float = Field(ge=0, le=100)
    latency: LatencyPercentilesResponse


class AgentRunSloResponse(BaseModel):
    """Agent Run 成功率与延迟。"""

    terminal_runs: int = Field(ge=0)
    completed_runs: int = Field(ge=0)
    unsuccessful_runs: int = Field(ge=0)
    success_rate_percent: float = Field(ge=0, le=100)
    latency: LatencyPercentilesResponse


class ModelUsageResponse(BaseModel):
    """按模型聚合且已冻结价格的用量。"""

    provider: str
    model: str
    invocations: int = Field(ge=0)
    failed_invocations: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_microusd: int = Field(ge=0)
    latency: LatencyPercentilesResponse


class QueueMetricsResponse(BaseModel):
    """数据库任务队列当前积压。"""

    backlog: int = Field(ge=0)
    oldest_wait_seconds: int = Field(ge=0)


class ChannelDeliveryMetricsResponse(BaseModel):
    """渠道出站投递的安全聚合。"""

    attempts: int = Field(ge=0)
    delivered: int = Field(ge=0)
    degraded: int = Field(ge=0)
    failed: int = Field(ge=0)
    rate_limited: int = Field(ge=0)
    failure_rate_percent: float = Field(ge=0, le=100)


class NotificationDeliveryMetricsResponse(BaseModel):
    """告警通知任务窗口计数与尚未安全重放的死信数量。"""

    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    retrying: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    dead_letters: int = Field(ge=0)


class ActiveAlertResponse(BaseModel):
    """不含正文或凭证的确定性阈值告警。"""

    code: str
    severity: AlertSeverity
    title: str
    summary: str
    current_value: float
    threshold_value: float
    unit: str
    source_type: str = "observability"
    source_key: str | None = None


class ObservabilityAlertLifecycleResponse(BaseModel):
    """通用可观测告警生命周期，不含通知目标或 Secret。"""

    id: UUID
    agent_id: UUID
    source_type: str
    source_key: str
    code: str
    status: ObservabilityAlertLifecycleStatus
    severity: AlertSeverity
    occurrences: int = Field(ge=1)
    current_value: float
    threshold_value: float
    unit: str
    first_occurred_at: datetime
    last_occurred_at: datetime
    last_evaluated_at: datetime
    escalated_at: datetime | None
    escalation_level: int = Field(ge=0, le=3)
    last_escalated_at: datetime | None
    resolved_at: datetime | None
    recovery_duration_seconds: int | None = Field(default=None, ge=0)
    disposition_status: ObservabilityAlertDispositionStatus | None = None
    disposition_reason: str | None = Field(default=None, max_length=500)
    disposition_expires_at: datetime | None = None


class ObservabilityAlertLifecyclePageResponse(BaseModel):
    """通用告警生命周期键集分页。"""

    items: tuple[ObservabilityAlertLifecycleResponse, ...]
    next_cursor: str | None = None


class ObservabilityAlertLifecycleTrendPointResponse(BaseModel):
    """一个固定时间桶内的通用告警生命周期变化。"""

    bucket_started_at: datetime
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)


class ObservabilityAlertSourceLifecycleMetricsResponse(BaseModel):
    """单一通用告警来源的安全聚合。"""

    source_type: str = Field(min_length=1, max_length=80)
    active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)
    mean_recovery_seconds: float = Field(ge=0)
    p95_recovery_seconds: int = Field(ge=0)


class ObservabilityAlertLifecycleMetricsResponse(BaseModel):
    """当前 Agent 的通用告警生命周期聚合与趋势。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)
    mean_recovery_seconds: float = Field(ge=0)
    p95_recovery_seconds: int = Field(ge=0)
    sources: tuple[ObservabilityAlertSourceLifecycleMetricsResponse, ...]
    trend: tuple[ObservabilityAlertLifecycleTrendPointResponse, ...]


class ObservabilityAlertBaselineSignalResponse(BaseModel):
    """告警开启或升级量相对稳健历史基线的偏离。"""

    metric: Literal["opened", "escalated"]
    source_type: str | None = Field(default=None, max_length=80)
    current_value: int = Field(ge=0)
    baseline_median: float = Field(ge=0)
    baseline_mad: float = Field(ge=0)
    threshold_value: float = Field(ge=0)
    anomalous: bool
    samples: tuple[int, ...] = Field(min_length=3, max_length=30)


class ObservabilityAlertBaselineResponse(BaseModel):
    """当前 Agent 的等长窗口告警异常基线。"""

    window_started_at: datetime
    window_ended_at: datetime
    window_minutes: int = Field(ge=60, le=10_080)
    periods: int = Field(ge=3, le=30)
    sensitivity: float = Field(ge=1, le=10)
    minimum_current_count: int = Field(ge=1, le=10_000)
    signals: tuple[ObservabilityAlertBaselineSignalResponse, ...]


class ObservabilityAlertHandoffSourceResponse(BaseModel):
    """值班窗口内单一告警来源的安全聚合。"""

    source_type: str = Field(min_length=1, max_length=80)
    active: int = Field(ge=0)
    critical_active: int = Field(ge=0)
    unacknowledged_active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)


class ObservabilityAlertHandoffItemResponse(BaseModel):
    """不含自由文本备注的值班交接优先关注项。"""

    lifecycle_id: UUID
    source_type: str = Field(min_length=1, max_length=80)
    source_key: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=100)
    severity: AlertSeverity
    escalation_level: int = Field(ge=0, le=3)
    active_minutes: int = Field(ge=0)
    disposition_status: ObservabilityAlertDispositionStatus | None = None
    disposition_expires_at: datetime | None = None
    reason_codes: tuple[
        Literal["critical", "unacknowledged", "escalated", "suppression_expiring"], ...
    ]


class ObservabilityAlertHandoffResponse(BaseModel):
    """当前 Agent 的告警值班交接摘要。"""

    window_started_at: datetime
    window_ended_at: datetime
    active: int = Field(ge=0)
    critical_active: int = Field(ge=0)
    unacknowledged_active: int = Field(ge=0)
    acknowledged_active: int = Field(ge=0)
    suppressed_active: int = Field(ge=0)
    opened: int = Field(ge=0)
    resolved: int = Field(ge=0)
    escalated: int = Field(ge=0)
    blocked_replays: int = Field(ge=0)
    sources: tuple[ObservabilityAlertHandoffSourceResponse, ...]
    priority_items: tuple[ObservabilityAlertHandoffItemResponse, ...] = Field(max_length=10)


class ObservabilityAlertOperationsSummaryResponse(BaseModel):
    """告警异常基线与值班交接的统一安全读模型。"""

    generated_at: datetime
    baseline: ObservabilityAlertBaselineResponse
    handoff: ObservabilityAlertHandoffResponse


class ObservabilityAlertRecommendationResponse(BaseModel):
    """只读告警处置建议及不可绕过的人工确认护栏。"""

    lifecycle_id: UUID
    source_type: str = Field(min_length=1, max_length=80)
    source_key: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=100)
    severity: AlertSeverity
    action: ObservabilityAlertRecommendationAction
    priority: ObservabilityAlertRecommendationPriority
    confidence: float = Field(ge=0, le=1)
    reason_codes: tuple[ObservabilityAlertRecommendationReason, ...] = Field(
        min_length=1,
        max_length=6,
    )
    guardrail_codes: tuple[ObservabilityAlertRecommendationGuardrail, ...] = Field(
        min_length=3,
        max_length=3,
    )
    active_minutes: int = Field(ge=0)
    occurrences: int = Field(ge=1)
    escalation_level: int = Field(ge=0, le=3)
    baseline_anomalous: bool
    suggested_suppression_minutes: int | None = Field(default=None, ge=5, le=10_080)
    requires_confirmation: bool
    automation_allowed: bool


class ObservabilityAlertRecommendationFeedbackCommand(BaseModel):
    """仅提交人工结论，建议快照由服务端重算。"""

    decision: ObservabilityAlertRecommendationFeedbackDecision
    confirmed: bool = False
    alternative_action: ObservabilityAlertRecommendationAction | None = None


class ObservabilityAlertRecommendationFeedbackResponse(BaseModel):
    """不含自由文本的建议反馈记录。"""

    id: UUID
    lifecycle_id: UUID
    source_type: str = Field(min_length=1, max_length=80)
    source_key: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=120)
    recommendation_action: ObservabilityAlertRecommendationAction
    priority: ObservabilityAlertRecommendationPriority
    reason_codes: tuple[ObservabilityAlertRecommendationReason, ...] = Field(
        min_length=1, max_length=6
    )
    decision: ObservabilityAlertRecommendationFeedbackDecision
    actor_id: UUID
    feedback_at: datetime
    alternative_action: ObservabilityAlertRecommendationAction | None = None


class ObservabilityAlertRecommendationActionMetricsResponse(BaseModel):
    """按建议动作聚合的反馈计数。"""

    action: ObservabilityAlertRecommendationAction
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)


class ObservabilityAlertRecommendationSourceMetricsResponse(BaseModel):
    """按告警来源聚合的反馈计数。"""

    source_type: str = Field(min_length=1, max_length=80)
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)


class ObservabilityAlertRecommendationQualityMetricsResponse(BaseModel):
    """建议反馈与同窗口复核事实的安全聚合。"""

    window_started_at: datetime
    window_ended_at: datetime
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    acceptance_rate_percent: float = Field(ge=0, le=100)
    accepted_resolved: int = Field(ge=0)
    accepted_active: int = Field(ge=0)
    replay_total: int = Field(ge=0)
    replay_allowed: int = Field(ge=0)
    replay_blocked: int = Field(ge=0)
    actions: tuple[ObservabilityAlertRecommendationActionMetricsResponse, ...]
    sources: tuple[ObservabilityAlertRecommendationSourceMetricsResponse, ...]


class ObservabilityAlertCalibrationGroupResponse(BaseModel):
    """单一阈值规则与来源分组的 95% Wilson 区间。"""

    rule: ObservabilityAlertCalibrationRule
    source_type: str = Field(min_length=1, max_length=80)
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    acceptance_rate_percent: float = Field(ge=0, le=100)
    confidence_lower_percent: float = Field(ge=0, le=100)
    confidence_upper_percent: float = Field(ge=0, le=100)


class ObservabilityAlertCalibrationProposalResponse(BaseModel):
    """单一运行配置阈值的离线评测结论。"""

    rule: ObservabilityAlertCalibrationRule
    configuration_key: str = Field(min_length=1, max_length=255)
    current_value: int = Field(ge=0)
    proposed_value: int = Field(ge=0)
    status: ObservabilityAlertCalibrationStatus
    sample_size: int = Field(ge=0)
    acceptance_rate_percent: float = Field(ge=0, le=100)
    confidence_lower_percent: float = Field(ge=0, le=100)
    confidence_upper_percent: float = Field(ge=0, le=100)


class ObservabilityAlertCalibrationAnalysisResponse(BaseModel):
    """不含正文且不执行调参的离线阈值分析。"""

    window_started_at: datetime
    window_ended_at: datetime
    configuration_version: int = Field(ge=0)
    minimum_samples_per_group: int = Field(ge=1)
    target_acceptance_rate_percent: float = Field(ge=0, le=100)
    confidence_level_percent: float = Field(ge=0, le=100)
    total_feedback: int = Field(ge=0)
    eligible_feedback: int = Field(ge=0)
    groups: tuple[ObservabilityAlertCalibrationGroupResponse, ...]
    proposals: tuple[ObservabilityAlertCalibrationProposalResponse, ...]
    automatic_tuning_allowed: bool


class ObservabilityAlertCalibrationDraftCommand(BaseModel):
    """基于指定分析版本创建配置草稿的显式确认命令。"""

    configuration_version: int = Field(ge=0)
    confirmed: bool = False


class ObservabilityAlertReplayActionMetricsResponse(BaseModel):
    """候选阈值保留样本中的人工动作计数。"""

    action: ObservabilityAlertRecommendationAction
    total: int = Field(ge=0)


class ObservabilityAlertCalibrationReplayProposalResponse(BaseModel):
    """基于生命周期聚合事实的候选阈值场景回放结果。"""

    rule: ObservabilityAlertCalibrationRule
    configuration_key: str = Field(min_length=1, max_length=255)
    current_value: int = Field(ge=0)
    candidate_value: int = Field(ge=0)
    sample_size: int = Field(ge=0)
    lifecycle_facts: int = Field(ge=0)
    missing_lifecycle_facts: int = Field(ge=0)
    current_triggered: int = Field(ge=0)
    candidate_triggered: int = Field(ge=0)
    avoided: int = Field(ge=0)
    retained: int = Field(ge=0)
    retained_accepted: int = Field(ge=0)
    retained_rejected: int = Field(ge=0)
    alternative_actions: tuple[ObservabilityAlertReplayActionMetricsResponse, ...]


class ObservabilityAlertCalibrationReplayAnalysisResponse(BaseModel):
    """不证明因果关系的有界候选阈值场景模拟。"""

    window_started_at: datetime
    window_ended_at: datetime
    configuration_version: int = Field(ge=0)
    total_feedback: int = Field(ge=0)
    eligible_feedback: int = Field(ge=0)
    proposals: tuple[ObservabilityAlertCalibrationReplayProposalResponse, ...]
    scenario_only: bool
    automatic_tuning_allowed: bool


class ObservabilityAlertDispositionCommand(BaseModel):
    """通用告警确认命令，调用方必须显式确认。"""

    reason: str = Field(min_length=1, max_length=500)
    confirmed: bool = False


class ObservabilityAlertSuppressionCommand(ObservabilityAlertDispositionCommand):
    """带明确到期时间的通用告警临时抑制命令。"""

    expires_at: datetime


class ObservabilityAlertDispositionClearCommand(BaseModel):
    """解除当前通用告警处置的显式确认命令。"""

    confirmed: bool = False


class ObservabilityAlertDispositionResponse(BaseModel):
    """通用告警处置结果，不包含通知目标、Secret 或业务正文。"""

    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    status: Literal["acknowledged", "suppressed", "cleared"]
    reason: str
    expires_at: datetime | None
    updated_at: datetime


class ObservabilityAlertBatchDispositionCommand(BaseModel):
    """最多 100 项的原子批量处置命令。"""

    lifecycle_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    action: Literal["acknowledge", "suppress", "clear"]
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    confirmed: bool = False


class ObservabilityAlertBatchDispositionResponse(BaseModel):
    """批量处置的逐项结果。"""

    items: tuple[ObservabilityAlertDispositionResponse, ...]


class ObservabilityAlertDispositionEventResponse(BaseModel):
    """不含正文、通知目标或 Secret 的处置历史事件。"""

    id: UUID
    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    action: ObservabilityAlertDispositionAction
    reason: str = Field(min_length=1, max_length=500)
    actor_id: UUID
    expires_at: datetime | None
    occurred_at: datetime


class ObservabilityAlertReplayReviewResponse(BaseModel):
    """不含任务载荷、通知目标或 Secret 的重放复核事件。"""

    id: UUID
    agent_id: UUID | None
    source_job_id: UUID | None
    source_type: str | None = Field(default=None, max_length=80)
    source_key: str | None = Field(default=None, max_length=255)
    decision: ObservabilityAlertReplayDecision
    reason_code: ObservabilityAlertReplayReason
    actor_id: UUID
    suppression_expires_at: datetime | None
    reviewed_at: datetime


class ObservabilityAlertReplayReviewPageResponse(BaseModel):
    """重放复核事件键集分页。"""

    items: tuple[ObservabilityAlertReplayReviewResponse, ...]
    next_cursor: str | None = None


class ObservabilityAlertReplayReasonMetricsResponse(BaseModel):
    """单一重放复核原因的窗口计数。"""

    reason_code: ObservabilityAlertReplayReason
    count: int = Field(ge=0)


class ObservabilityAlertReplaySourceMetricsResponse(BaseModel):
    """单一告警来源的重放复核窗口计数。"""

    source_type: str | None = Field(default=None, max_length=80)
    total: int = Field(ge=0)
    allowed: int = Field(ge=0)
    blocked: int = Field(ge=0)


class ObservabilityAlertReplayMetricsResponse(BaseModel):
    """通用告警通知重放复核的窗口聚合。"""

    window_started_at: datetime
    window_ended_at: datetime
    total: int = Field(ge=0)
    allowed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    allowed_rate_percent: float = Field(ge=0, le=100)
    reasons: tuple[ObservabilityAlertReplayReasonMetricsResponse, ...]
    sources: tuple[ObservabilityAlertReplaySourceMetricsResponse, ...]


class ObservabilityDashboardResponse(BaseModel):
    """管理后台可观测性完整聚合视图。"""

    window_started_at: datetime
    window_ended_at: datetime
    api: ApiSloResponse
    agent_runs: AgentRunSloResponse
    models: tuple[ModelUsageResponse, ...]
    queue: QueueMetricsResponse
    channel_delivery: ChannelDeliveryMetricsResponse
    notification_delivery: NotificationDeliveryMetricsResponse
    total_estimated_cost_microusd: int = Field(ge=0)
    alerts: tuple[ActiveAlertResponse, ...]
    alert_lifecycles: tuple[ObservabilityAlertLifecycleResponse, ...] = ()
