"""拟人评测人工决策与不可变安全报告。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class EvaluationDecisionOutcome(StrEnum):
    """人工结论，不授予发布或调参权限。"""

    ADOPT_CANDIDATE = "adopt_candidate"
    KEEP_BASELINE = "keep_baseline"
    WAIT_FOR_EVIDENCE = "wait_for_evidence"


class EvaluationDecisionReason(StrEnum):
    """受控原因代码，避免自由文本进入可下载报告。"""

    QUALITY_GAIN = "quality_gain"
    REGRESSION_RISK = "regression_risk"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class EvaluationDecisionRecord:
    """报告原始字节是持久化真相源；摘要用于完整性校验。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    created_by: UUID
    created_at: datetime
    outcome: EvaluationDecisionOutcome
    reason: EvaluationDecisionReason
    content: bytes
    sha256: str
