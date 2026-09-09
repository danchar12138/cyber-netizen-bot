"""认知资源版本、运行轨迹与回放评测 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from cnb_domain import (
    CognitionResourceKind,
    CognitionVersionStatus,
    InvocationStatus,
    JsonValue,
)


class CognitionResourceDraftCreate(BaseModel):
    """创建认知资源不可变草稿的命令。"""

    kind: CognitionResourceKind
    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    payload: dict[str, JsonValue]
    note: str | None = Field(default=None, max_length=1000)


class CognitionResourceResponse(BaseModel):
    """认知资源版本的完整管理视图。"""

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


class CognitionResourceListResponse(BaseModel):
    """认知资源版本列表。"""

    items: tuple[CognitionResourceResponse, ...]


class CognitionPayloadTestCommand(BaseModel):
    """不产生外部调用的认知资源载荷测试命令。"""

    kind: CognitionResourceKind
    payload: dict[str, JsonValue]


class CognitionPayloadTestResponse(BaseModel):
    """认知资源离线测试结果。"""

    valid: bool
    messages: tuple[str, ...]


class PersonaStateResponse(BaseModel):
    """短期人格状态的安全摘要。"""

    persona_version: int
    valence: float
    arousal: float
    social_energy: float
    created_at: datetime


class RunStepResponse(BaseModel):
    """可安全展示的运行阶段摘要。"""

    sequence: int
    stage: str
    summary: str
    detail: dict[str, JsonValue]
    created_at: datetime


class ActionCandidateResponse(BaseModel):
    """经过策略门的行动候选摘要。"""

    sequence: int
    action: str
    confidence: float
    reason_summary: str
    parameters: dict[str, JsonValue]
    tool_name: str | None
    risk_level: str
    selected: bool
    rejection_reason: str | None


class ModelInvocationResponse(BaseModel):
    """不包含请求正文的模型尝试摘要。"""

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


class CognitiveRunTraceResponse(BaseModel):
    """一次 Agent Run 的安全认知回放。"""

    run_id: UUID
    persona_state: PersonaStateResponse | None
    steps: tuple[RunStepResponse, ...]
    candidates: tuple[ActionCandidateResponse, ...]
    model_invocations: tuple[ModelInvocationResponse, ...]


class EvaluationCaseResponse(BaseModel):
    """拟人行为回放用例结果。"""

    case_id: str
    category: str
    input_text: str
    expected_action: str
    actual_action: str
    passed: bool
    summary: str


class EvaluationSuiteResponse(BaseModel):
    """内置拟人回放集的总体结果。"""

    passed: int
    total: int
    cases: tuple[EvaluationCaseResponse, ...]
