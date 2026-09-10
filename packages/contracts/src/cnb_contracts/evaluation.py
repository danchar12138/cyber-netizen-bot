"""版本化拟人回放、自动质量门与匿名盲评 API 契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from cnb_domain import (
    BlindReviewPreference,
    EvaluationComparisonStatus,
    EvaluationRunStatus,
    EvaluationSuiteStatus,
)


class EvaluationCaseCreate(BaseModel):
    """创建评测集版本时提交的对话样例。"""

    case_key: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    input_text: str = Field(min_length=1, max_length=8000)
    expected_action: Literal["reply", "ask", "wait", "no_reply", "tool"]
    reference_response: str | None = Field(default=None, max_length=20000)
    required_phrases: tuple[str, ...] = Field(default=(), max_length=50)
    forbidden_phrases: tuple[str, ...] = Field(default=(), max_length=50)


class EvaluationSuiteCreate(BaseModel):
    """创建不可变评测集草稿。"""

    key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    minimum_pass_rate: float = Field(default=100, ge=0, le=100)
    max_output_tokens: int = Field(default=512, ge=64, le=32768)
    cases: tuple[EvaluationCaseCreate, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def case_keys_are_unique(self) -> "EvaluationSuiteCreate":
        """在进入应用层前拒绝重复用例键。"""
        keys = [item.case_key for item in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("同一评测集中的用例键不能重复")
        return self


class EvaluationCaseDefinitionResponse(BaseModel):
    """评测集版本的一条用例。"""

    id: UUID
    case_key: str
    category: str
    input_text: str
    expected_action: str
    reference_response: str | None
    required_phrases: tuple[str, ...]
    forbidden_phrases: tuple[str, ...]
    sort_order: int


class EvaluationSuiteDefinitionResponse(BaseModel):
    """拟人评测集不可变版本。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    key: str
    name: str
    version: int
    status: EvaluationSuiteStatus
    description: str | None
    minimum_pass_rate: float
    max_output_tokens: int
    cases: tuple[EvaluationCaseDefinitionResponse, ...]
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


class EvaluationSuiteListResponse(BaseModel):
    """当前 Agent 的评测集版本列表。"""

    items: tuple[EvaluationSuiteDefinitionResponse, ...]


class EvaluationRunCreate(BaseModel):
    """运行指定已发布评测集；留空则运行内置安全基线。"""

    suite_id: UUID | None = None


class EvaluationCheckResponse(BaseModel):
    """自动回放的一项确定性检查。"""

    key: str
    passed: bool
    detail: str


class EvaluationCaseRunResponse(BaseModel):
    """自动回放中冻结的单条结果。"""

    id: UUID
    case_key: str
    category: str
    input_text: str
    expected_action: str
    actual_action: str
    candidate_response: str | None
    reference_response: str | None
    passed: bool
    checks: tuple[EvaluationCheckResponse, ...]
    summary: str
    latency_ms: int


class EvaluationRunResponse(BaseModel):
    """带版本快照、成本和质量门结论的完整回放。"""

    id: UUID
    suite_id: UUID | None
    suite_key: str
    suite_version: int
    suite_name: str
    status: EvaluationRunStatus
    passed: int
    total: int
    pass_rate: float
    gate_passed: bool
    minimum_pass_rate: float
    configuration_version: int
    persona_version: int
    prompt_version: int
    policy_version: int
    model_route_version: int
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_microusd: int = Field(ge=0)
    error_code: str | None
    results: tuple[EvaluationCaseRunResponse, ...]
    created_by: UUID
    created_at: datetime
    completed_at: datetime


class EvaluationRunSummaryResponse(BaseModel):
    """回放历史列表中的轻量摘要。"""

    id: UUID
    suite_name: str
    suite_version: int
    status: EvaluationRunStatus
    passed: int
    total: int
    pass_rate: float
    gate_passed: bool
    provider: str
    model: str
    created_at: datetime


class EvaluationRunListResponse(BaseModel):
    """最近自动回放历史。"""

    items: tuple[EvaluationRunSummaryResponse, ...]


class EvaluationModelTargetResponse(BaseModel):
    """当前已发布模型路由中的一个可比较档案。"""

    profile_key: str
    profile_version: int
    provider: str
    model: str


class EvaluationModelTargetListResponse(BaseModel):
    """当前 Agent 允许参加同源对比的模型档案。"""

    items: tuple[EvaluationModelTargetResponse, ...]


class EvaluationComparisonCreate(BaseModel):
    """使用指定发布模型档案运行一次同源对比。"""

    suite_id: UUID | None = None
    profile_keys: tuple[str, ...] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def profile_keys_are_unique(self) -> "EvaluationComparisonCreate":
        """在进入应用层前拒绝空白或重复档案键。"""
        normalized = [key.strip() for key in self.profile_keys]
        if any(not key for key in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("模型档案键不能为空且不能重复")
        return self


class EvaluationComparisonEntryResponse(BaseModel):
    """对比实验中的一个候选及完整回放。"""

    position: int
    profile_key: str
    profile_version: int
    run: EvaluationRunResponse


class EvaluationComparisonResponse(BaseModel):
    """带共享快照和逐候选结果的多模型对比详情。"""

    id: UUID
    suite_id: UUID | None
    suite_key: str
    suite_version: int
    suite_name: str
    status: EvaluationComparisonStatus
    configuration_version: int
    persona_version: int
    prompt_version: int
    policy_version: int
    model_route_version: int
    entries: tuple[EvaluationComparisonEntryResponse, ...]
    created_by: UUID
    created_at: datetime
    completed_at: datetime


class EvaluationComparisonEntrySummaryResponse(BaseModel):
    """对比历史中的候选轻量指标。"""

    position: int
    profile_key: str
    profile_version: int
    run: EvaluationRunSummaryResponse


class EvaluationComparisonSummaryResponse(BaseModel):
    """不携带回答正文的多模型对比历史摘要。"""

    id: UUID
    suite_name: str
    suite_version: int
    status: EvaluationComparisonStatus
    configuration_version: int
    persona_version: int
    prompt_version: int
    policy_version: int
    model_route_version: int
    entries: tuple[EvaluationComparisonEntrySummaryResponse, ...]
    created_at: datetime
    completed_at: datetime


class EvaluationComparisonListResponse(BaseModel):
    """当前 Agent 的最近多模型对比历史。"""

    items: tuple[EvaluationComparisonSummaryResponse, ...]


class BlindReviewAssignmentCreate(BaseModel):
    """领取一个尚未由当前评审处理的匿名任务。"""

    run_id: UUID | None = None


class BlindReviewAssignmentResponse(BaseModel):
    """不暴露左右来源、版本、模型或质量门结论的盲评任务。"""

    id: UUID
    case_key: str
    category: str
    input_text: str
    response_a: str
    response_b: str
    created_at: datetime


class BlindReviewScoreInput(BaseModel):
    """一侧回答的拟人质量评分。"""

    persona_consistency: int = Field(ge=1, le=5)
    naturalness: int = Field(ge=1, le=5)
    empathy: int = Field(ge=1, le=5)
    boundary_respect: int = Field(ge=1, le=5)


class BlindReviewSubmit(BaseModel):
    """提交左右回答的独立评分和总体偏好。"""

    preference: Literal["a", "b", "tie"]
    response_a_score: BlindReviewScoreInput
    response_b_score: BlindReviewScoreInput
    note: str | None = Field(default=None, max_length=2000)


class BlindReviewResponse(BaseModel):
    """提交后允许返回去盲的持久化结论。"""

    id: UUID
    assignment_id: UUID
    preference: BlindReviewPreference
    candidate_score: BlindReviewScoreInput
    reference_score: BlindReviewScoreInput
    note: str | None
    created_at: datetime


class EvaluationReportResponse(BaseModel):
    """拟人自动回归与人工盲评聚合报告。"""

    total_runs: int
    gate_passed_runs: int
    latest_pass_rate: float | None
    pending_reviews: int
    completed_reviews: int
    candidate_wins: int
    reference_wins: int
    ties: int
    candidate_average_score: float | None
    reference_average_score: float | None
