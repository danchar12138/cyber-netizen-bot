"""拟人评测与告警运营统一质量概览的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.evaluation import BlindReviewPreference, EvaluationReport
from cnb_domain.observability import ObservabilityAlertRecommendationQualityMetrics


class QualityDataCoverage(StrEnum):
    """统一质量概览中两类证据的数据覆盖状态。"""

    COMPLETE = "complete"
    EVALUATION_ONLY = "evaluation_only"
    OPERATIONS_ONLY = "operations_only"
    EMPTY = "empty"


class QualityEvaluationScope(StrEnum):
    """拟人评测聚合使用的稳定统计口径。"""

    CURRENT_AGENT_ALL_HISTORY = "current_agent_all_history"


class QualityReviewAttribution(StrEnum):
    """人工盲评在趋势中的稳定归属口径。"""

    RUN_CREATED_AT = "run_created_at"


@dataclass(frozen=True, slots=True)
class EvaluationVersionSnapshot:
    """一次评测运行使用的完整冻结版本与模型快照。"""

    suite_key: str
    suite_version: int
    configuration_version: int
    persona_version: int
    prompt_version: int
    policy_version: int
    model_route_version: int
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class EvaluationQualityRunSample:
    """不含输入和回答正文的自动回归质量样本。"""

    run_id: UUID
    created_at: datetime
    gate_passed: bool
    pass_rate: float
    snapshot: EvaluationVersionSnapshot


@dataclass(frozen=True, slots=True)
class EvaluationQualityReviewSample:
    """不含评审备注和回答正文的匿名盲评质量样本。"""

    run_id: UUID
    preference: BlindReviewPreference
    candidate_average_score: float
    reference_average_score: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluationQualitySamples:
    """当前作用域内用于只读质量分析的安全样本集。"""

    runs: tuple[EvaluationQualityRunSample, ...]
    reviews: tuple[EvaluationQualityReviewSample, ...]


@dataclass(frozen=True, slots=True)
class EvaluationQualityTrendPoint:
    """一个连续时间桶中的自动回归与关联盲评事实。"""

    bucket_started_at: datetime
    bucket_ended_at: datetime
    total_runs: int
    gate_passed_runs: int
    average_pass_rate: float | None
    completed_reviews: int
    candidate_wins: int
    reference_wins: int
    ties: int
    candidate_average_score: float | None
    reference_average_score: float | None


@dataclass(frozen=True, slots=True)
class EvaluationVersionQualitySummary:
    """同一完整冻结快照下的质量观察事实。"""

    snapshot: EvaluationVersionSnapshot
    first_run_at: datetime
    latest_run_at: datetime
    total_runs: int
    gate_passed_runs: int
    average_pass_rate: float
    completed_reviews: int
    candidate_wins: int
    reference_wins: int
    ties: int
    candidate_average_score: float | None
    reference_average_score: float | None


@dataclass(frozen=True, slots=True)
class EvaluationQualityHistory:
    """有界趋势和完整冻结版本对比的只读质量事实。"""

    generated_at: datetime
    window_started_at: datetime
    window_ended_at: datetime
    window_minutes: int
    bucket_minutes: int
    review_attribution: QualityReviewAttribution
    total_runs: int
    completed_reviews: int
    trend: tuple[EvaluationQualityTrendPoint, ...]
    versions: tuple[EvaluationVersionQualitySummary, ...]
    comparable_versions: bool
    automatic_actions_allowed: bool = False


@dataclass(frozen=True, slots=True)
class UnifiedQualityOverview:
    """组合不同统计口径的只读质量事实，不代表因果或自动动作。"""

    generated_at: datetime
    evaluation_scope: QualityEvaluationScope
    operations_window_minutes: int
    coverage: QualityDataCoverage
    evaluation: EvaluationReport
    alert_recommendations: ObservabilityAlertRecommendationQualityMetrics
    automatic_actions_allowed: bool = False
