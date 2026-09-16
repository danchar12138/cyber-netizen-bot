"""拟人评测与告警运营统一质量概览的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from cnb_domain.evaluation import EvaluationReport
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
