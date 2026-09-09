"""认知资源版本、运行轨迹与评测结果的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from cnb_domain.configuration import JsonValue


class CognitionResourceKind(StrEnum):
    """管理后台可版本化治理的认知资源类别。"""

    PERSONA = "persona"
    PROMPT = "prompt"
    MODEL_PROFILE = "model_profile"
    MODEL_ROUTE = "model_route"
    TOOL = "tool"
    POLICY = "policy"


class CognitionVersionStatus(StrEnum):
    """认知资源不可变版本的生命周期。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class InvocationStatus(StrEnum):
    """单次模型尝试的执行结果。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class CognitionResourceVersion:
    """一种认知配置资源的不可变版本。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    kind: CognitionResourceKind
    key: str
    name: str
    version: int
    status: CognitionVersionStatus
    payload: dict[str, JsonValue]
    note: str | None
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersonaStateSnapshot:
    """每次运行落盘的短期人格状态，支持后续运行恢复。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    conversation_id: UUID
    run_id: UUID
    persona_version: int
    valence: float
    arousal: float
    social_energy: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunStepRecord:
    """不包含隐藏推理的认知阶段回放记录。"""

    id: UUID
    tenant_id: UUID
    run_id: UUID
    sequence: int
    stage: str
    summary: str
    detail: dict[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ActionCandidateRecord:
    """策略门处理过的结构化行动候选。"""

    id: UUID
    tenant_id: UUID
    run_id: UUID
    sequence: int
    action: str
    confidence: float
    reason_summary: str
    parameters: dict[str, JsonValue]
    tool_name: str | None
    risk_level: str
    selected: bool
    rejection_reason: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ModelInvocationRecord:
    """不含请求正文与密钥的模型调用可观测记录。"""

    id: UUID
    tenant_id: UUID
    run_id: UUID
    purpose: str
    provider: str
    model: str
    attempt: int
    status: InvocationStatus
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class CognitiveRunTrace:
    """后台展示的一次认知运行安全回放视图。"""

    run_id: UUID
    persona_state: PersonaStateSnapshot | None
    steps: tuple[RunStepRecord, ...]
    candidates: tuple[ActionCandidateRecord, ...]
    model_invocations: tuple[ModelInvocationRecord, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """一条确定性拟人回放用例的判定结果。"""

    case_id: str
    category: str
    input_text: str
    expected_action: str
    actual_action: str
    passed: bool
    summary: str
