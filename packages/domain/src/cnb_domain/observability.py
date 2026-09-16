"""性能、成本、SLO 与告警使用的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AlertSeverity(StrEnum):
    """确定性阈值告警的严重级别。"""

    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class LatencyPercentiles:
    """一段时间窗口内的毫秒延迟分位数。"""

    p50_ms: int
    p95_ms: int
    p99_ms: int


@dataclass(frozen=True, slots=True)
class ApiSloMetrics:
    """管理接口的安全服务等级聚合指标。"""

    requests: int
    server_errors: int
    error_rate_percent: float
    latency: LatencyPercentiles


@dataclass(frozen=True, slots=True)
class AgentRunSloMetrics:
    """Agent Run 的终态成功率与执行延迟。"""

    terminal_runs: int
    completed_runs: int
    unsuccessful_runs: int
    success_rate_percent: float
    latency: LatencyPercentiles


@dataclass(frozen=True, slots=True)
class ModelUsageMetrics:
    """按 Provider 与模型冻结聚合的 Token、成本和可靠性指标。"""

    provider: str
    model: str
    invocations: int
    failed_invocations: int
    input_tokens: int
    output_tokens: int
    estimated_cost_microusd: int
    latency: LatencyPercentiles


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    """数据库任务真相源中的队列积压。"""

    backlog: int
    oldest_wait_seconds: int


@dataclass(frozen=True, slots=True)
class ChannelDeliveryMetrics:
    """窗口内渠道出站投递的安全聚合。"""

    attempts: int = 0
    delivered: int = 0
    degraded: int = 0
    failed: int = 0
    rate_limited: int = 0

    @property
    def failure_rate_percent(self) -> float:
        return (
            round((self.failed + self.rate_limited) * 100 / self.attempts, 4)
            if self.attempts
            else 0.0
        )


@dataclass(frozen=True, slots=True)
class NotificationDeliveryMetrics:
    """通知任务窗口计数与尚未安全重放的死信数量。"""

    total: int = 0
    pending: int = 0
    running: int = 0
    retrying: int = 0
    succeeded: int = 0
    failed: int = 0
    dead_letters: int = 0


@dataclass(frozen=True, slots=True)
class ObservabilityMetrics:
    """仓储返回的一段租户隔离原始聚合窗口。"""

    window_started_at: datetime
    window_ended_at: datetime
    api: ApiSloMetrics
    agent_runs: AgentRunSloMetrics
    models: tuple[ModelUsageMetrics, ...]
    queue: QueueMetrics
    channel_delivery: ChannelDeliveryMetrics = ChannelDeliveryMetrics()
    notification_delivery: NotificationDeliveryMetrics = NotificationDeliveryMetrics()


@dataclass(frozen=True, slots=True)
class ActiveAlert:
    """由已发布阈值确定性计算且不含业务正文的活动告警。"""

    code: str
    severity: AlertSeverity
    title: str
    summary: str
    current_value: float
    threshold_value: float
    unit: str
    source_type: str = "observability"
    source_key: str | None = None
    first_occurred_at: datetime | None = None
    last_occurred_at: datetime | None = None

    @property
    def alert_key(self) -> str:
        """返回不依赖标题和瞬时数值的稳定来源键。"""
        return self.source_key or self.code


class ObservabilityAlertLifecycleStatus(StrEnum):
    """通用可观测告警生命周期状态。"""

    ACTIVE = "active"
    RESOLVED = "resolved"


class ObservabilityAlertDispositionStatus(StrEnum):
    """通用告警处置状态。"""

    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED = "suppressed"


class ObservabilityAlertDispositionAction(StrEnum):
    """通用告警处置历史动作。"""

    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED = "suppressed"
    CLEARED = "cleared"


class ObservabilityAlertRecommendationAction(StrEnum):
    """人工确认前只读告警建议的候选动作。"""

    ACKNOWLEDGE = "acknowledge"
    SUPPRESS = "suppress"
    OBSERVE = "observe"


class ObservabilityAlertRecommendationPriority(StrEnum):
    """告警处置建议的确定性优先级。"""

    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"


class ObservabilityAlertRecommendationGuardrail(StrEnum):
    """建议执行前不可绕过的稳定护栏码。"""

    MANUAL_CONFIRMATION_REQUIRED = "manual_confirmation_required"
    AUTOMATIC_EXECUTION_FORBIDDEN = "automatic_execution_forbidden"
    CURRENT_SCOPE_ONLY = "current_scope_only"


class ObservabilityAlertRecommendationReason(StrEnum):
    """不含自由文本与隐藏推理的稳定建议原因码。"""

    CRITICAL = "critical"
    ESCALATED = "escalated"
    BASELINE_ANOMALY = "baseline_anomaly"
    LONG_RUNNING = "long_running"
    REPEATED_WARNING = "repeated_warning"
    INSUFFICIENT_SIGNAL = "insufficient_signal"


class ObservabilityAlertRecommendationFeedbackDecision(StrEnum):
    """管理员对服务端建议的最终反馈。"""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ObservabilityAlertCalibrationRule(StrEnum):
    """可以用冻结反馈单独评估的建议阈值规则。"""

    LONG_RUNNING = "long_running"
    REPEATED_WARNING = "repeated_warning"


class ObservabilityAlertCalibrationStatus(StrEnum):
    """离线阈值评测对当前配置的稳定结论。"""

    INSUFFICIENT_DATA = "insufficient_data"
    INCONCLUSIVE = "inconclusive"
    KEEP = "keep"
    TIGHTEN = "tighten"
    LIMIT_REACHED = "limit_reached"


class ObservabilityAlertReplayDecision(StrEnum):
    """通用告警通知重放复核结论。"""

    ALLOWED = "allowed"
    BLOCKED = "blocked"


class ObservabilityAlertReplayReason(StrEnum):
    """不暴露任务载荷的稳定重放复核原因码。"""

    ALLOWED_NO_SUPPRESSION = "allowed_no_suppression"
    ALLOWED_SUPPRESSION_EXPIRED = "allowed_suppression_expired"
    BLOCKED_ACTIVE_SUPPRESSION = "blocked_active_suppression"
    BLOCKED_MISSING_SOURCE = "blocked_missing_source"
    BLOCKED_INVALID_AGENT = "blocked_invalid_agent"
    BLOCKED_AGENT_MISMATCH = "blocked_agent_mismatch"


@dataclass(frozen=True, slots=True)
class ObservabilityAlertLifecycle:
    """不含业务正文的通用可观测告警事件生命周期。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    source_type: str
    source_key: str
    code: str
    status: ObservabilityAlertLifecycleStatus
    severity: AlertSeverity
    occurrences: int
    current_value: float
    threshold_value: float
    unit: str
    first_occurred_at: datetime
    last_occurred_at: datetime
    last_evaluated_at: datetime
    escalated_at: datetime | None
    resolved_at: datetime | None
    recovery_duration_seconds: int | None
    created_at: datetime
    updated_at: datetime
    escalation_level: int = 0
    last_escalated_at: datetime | None = None
    disposition_status: ObservabilityAlertDispositionStatus | None = None
    disposition_reason: str | None = None
    disposition_expires_at: datetime | None = None

    @property
    def alert_key(self) -> str:
        """返回来源类型和来源键组成的稳定告警键。"""
        return f"{self.source_type}:{self.source_key}"


@dataclass(frozen=True, slots=True)
class ObservabilityAlertDisposition:
    """按稳定来源键保存的通用告警确认或临时抑制记录。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    source_type: str
    source_key: str
    code: str
    status: ObservabilityAlertDispositionStatus
    reason: str
    actor_id: UUID
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def alert_key(self) -> str:
        """返回与生命周期一致的稳定告警键。"""
        return f"{self.source_type}:{self.source_key}"


@dataclass(frozen=True, slots=True)
class ObservabilityAlertDispositionEvent:
    """追加式通用告警处置历史事件，不保存正文或凭据。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    action: ObservabilityAlertDispositionAction
    reason: str
    actor_id: UUID
    expires_at: datetime | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ObservabilityAlertReplayReview:
    """追加式重放复核事件，只保存告警来源和操作元数据。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID | None
    source_job_id: UUID | None
    source_type: str | None
    source_key: str | None
    decision: ObservabilityAlertReplayDecision
    reason_code: ObservabilityAlertReplayReason
    actor_id: UUID
    suppression_expires_at: datetime | None
    reviewed_at: datetime


@dataclass(frozen=True, slots=True)
class ObservabilityAlertRecommendationFeedback:
    """追加式建议反馈，只保存服务端计算的安全元数据。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    recommendation_action: ObservabilityAlertRecommendationAction
    priority: ObservabilityAlertRecommendationPriority
    reason_codes: tuple[ObservabilityAlertRecommendationReason, ...]
    decision: ObservabilityAlertRecommendationFeedbackDecision
    actor_id: UUID
    feedback_at: datetime
    alternative_action: ObservabilityAlertRecommendationAction | None = None


@dataclass(frozen=True, slots=True)
class ObservabilityAlertRecommendationEvidence:
    """告警建议的固定白名单证据摘要，不含正文或隐藏推理。"""

    lifecycle_id: UUID
    source_type: str
    source_key: str
    code: str
    evaluated_at: datetime
    current_value: float
    threshold_value: float
    unit: str
    active_minutes: int
    occurrences: int
    escalation_level: int
    baseline_anomalous: bool
    baseline_median: float | None
    baseline_mad: float | None
    baseline_threshold: float | None
    baseline_samples: tuple[int, ...]
    action: ObservabilityAlertRecommendationAction
    priority: ObservabilityAlertRecommendationPriority
    confidence: float
    reason_codes: tuple[ObservabilityAlertRecommendationReason, ...]
    guardrail_codes: tuple[ObservabilityAlertRecommendationGuardrail, ...]
    evidence_fingerprint: str


@dataclass(frozen=True, slots=True)
class ObservabilityAlertRecommendationActionMetrics:
    """单一建议动作的反馈计数。"""

    action: ObservabilityAlertRecommendationAction
    total: int
    accepted: int
    rejected: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertRecommendationSourceMetrics:
    """单一告警来源的建议反馈计数。"""

    source_type: str
    total: int
    accepted: int
    rejected: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertRecommendationQualityMetrics:
    """有界窗口内的建议反馈与历史复核概览。"""

    window_started_at: datetime
    window_ended_at: datetime
    total: int
    accepted: int
    rejected: int
    acceptance_rate_percent: float
    accepted_resolved: int
    accepted_active: int
    replay_total: int
    replay_allowed: int
    replay_blocked: int
    actions: tuple[ObservabilityAlertRecommendationActionMetrics, ...]
    sources: tuple[ObservabilityAlertRecommendationSourceMetrics, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertCalibrationGroup:
    """单一规则与来源分组的 Wilson 区间事实。"""

    rule: ObservabilityAlertCalibrationRule
    source_type: str
    total: int
    accepted: int
    rejected: int
    acceptance_rate_percent: float
    confidence_lower_percent: float
    confidence_upper_percent: float


@dataclass(frozen=True, slots=True)
class ObservabilityAlertCalibrationProposal:
    """只读配置阈值评测结论，不代表已经创建或发布配置。"""

    rule: ObservabilityAlertCalibrationRule
    configuration_key: str
    current_value: int
    proposed_value: int
    status: ObservabilityAlertCalibrationStatus
    sample_size: int
    acceptance_rate_percent: float
    confidence_lower_percent: float
    confidence_upper_percent: float


@dataclass(frozen=True, slots=True)
class ObservabilityAlertCalibrationAnalysis:
    """由冻结人工反馈生成的有界离线阈值评测。"""

    window_started_at: datetime
    window_ended_at: datetime
    configuration_version: int
    minimum_samples_per_group: int
    target_acceptance_rate_percent: float
    confidence_level_percent: float
    total_feedback: int
    eligible_feedback: int
    groups: tuple[ObservabilityAlertCalibrationGroup, ...]
    proposals: tuple[ObservabilityAlertCalibrationProposal, ...]
    automatic_tuning_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ObservabilityAlertReplayActionMetrics:
    """候选阈值保留样本中的人工动作计数。"""

    action: ObservabilityAlertRecommendationAction
    total: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertCalibrationReplayProposal:
    """基于生命周期聚合事实的候选阈值场景回放结果。"""

    rule: ObservabilityAlertCalibrationRule
    configuration_key: str
    current_value: int
    candidate_value: int
    sample_size: int
    lifecycle_facts: int
    missing_lifecycle_facts: int
    current_triggered: int
    candidate_triggered: int
    avoided: int
    retained: int
    retained_accepted: int
    retained_rejected: int
    alternative_actions: tuple[ObservabilityAlertReplayActionMetrics, ...]


@dataclass(frozen=True, slots=True)
class ObservabilityAlertCalibrationReplayAnalysis:
    """不证明因果关系的有界候选阈值场景模拟。"""

    window_started_at: datetime
    window_ended_at: datetime
    configuration_version: int
    total_feedback: int
    eligible_feedback: int
    proposals: tuple[ObservabilityAlertCalibrationReplayProposal, ...]
    replay_fingerprint: str
    scenario_only: bool = True
    automatic_tuning_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ObservabilityAlertReplayReasonMetrics:
    """单一重放复核原因的窗口计数。"""

    reason_code: ObservabilityAlertReplayReason
    count: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertReplaySourceMetrics:
    """单一告警来源的重放复核窗口计数。"""

    source_type: str | None
    total: int
    allowed: int
    blocked: int


@dataclass(frozen=True, slots=True)
class ObservabilityAlertReplayMetrics:
    """通用告警通知重放复核的安全运营聚合。"""

    window_started_at: datetime
    window_ended_at: datetime
    total: int
    allowed: int
    blocked: int
    reasons: tuple[ObservabilityAlertReplayReasonMetrics, ...]
    sources: tuple[ObservabilityAlertReplaySourceMetrics, ...]

    @property
    def allowed_rate_percent(self) -> float:
        return round(self.allowed * 100 / self.total, 4) if self.total else 0.0


@dataclass(frozen=True, slots=True)
class ObservabilityDashboard:
    """管理后台使用的可观测性与成本总览。"""

    metrics: ObservabilityMetrics
    total_estimated_cost_microusd: int
    alerts: tuple[ActiveAlert, ...]
    alert_lifecycles: tuple[ObservabilityAlertLifecycle, ...] = ()
