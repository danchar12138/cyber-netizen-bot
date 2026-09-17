"""拟人评测决策的职责分离审批与签名证明。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class EvaluationApprovalOutcome(StrEnum):
    """审批终态；只记录治理结论，不授予执行能力。"""

    APPROVED = "approved"
    REJECTED = "rejected"


class EvaluationApprovalReason(StrEnum):
    """审批使用的受控原因代码。"""

    EVIDENCE_CONFIRMED = "evidence_confirmed"
    RISK_UNRESOLVED = "risk_unresolved"
    GOVERNANCE_BLOCKED = "governance_blocked"
    RELEASE_NOT_READY = "release_not_ready"


class EvaluationReleaseEnvironment(StrEnum):
    """只读发布变更引用所处环境。"""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class EvaluationDecisionSignature:
    """不含私钥、可随历史证明长期验证的 Ed25519 签名材料。"""

    algorithm: str
    key_id: str
    public_key: bytes
    value: bytes


@dataclass(frozen=True, slots=True)
class EvaluationDecisionApprovalRecord:
    """审批证明原始字节是不可变真相源。"""

    id: UUID
    decision_id: UUID
    tenant_id: UUID
    agent_id: UUID
    approved_by: UUID
    approved_at: datetime
    outcome: EvaluationApprovalOutcome
    reason: EvaluationApprovalReason
    release_environment: EvaluationReleaseEnvironment | None
    change_reference: str | None
    content: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class EvaluationApprovalVerification:
    """对持久化证明各层完整性的独立校验结果。"""

    valid: bool
    content_hash_valid: bool
    canonical_content_valid: bool
    decision_hash_matches: bool
    signature_valid: bool
    verified_at: datetime
