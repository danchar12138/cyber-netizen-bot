"""版本化拟人回放、自动质量门与匿名盲评 API 契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from cnb_contracts.observability import ObservabilityAlertRecommendationQualityMetricsResponse
from cnb_domain import (
    BlindReviewPreference,
    EvaluationApprovalOutcome,
    EvaluationApprovalReason,
    EvaluationComparisonStatus,
    EvaluationDecisionOutcome,
    EvaluationDecisionReason,
    EvaluationReleaseEnvironment,
    EvaluationRunStatus,
    EvaluationSuiteStatus,
    QualityDataCoverage,
    QualityEvaluationScope,
    QualityReviewAttribution,
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


class EvaluationVersionSnapshotResponse(BaseModel):
    """定义可比较评测上下文的完整冻结版本与模型快照。"""

    suite_key: str
    suite_version: int
    configuration_version: int
    persona_version: int
    prompt_version: int
    policy_version: int
    model_route_version: int
    provider: str
    model: str


class EvaluationQualityTrendPointResponse(BaseModel):
    """一个连续时间桶中的拟人评测质量事实。"""

    bucket_started_at: datetime
    bucket_ended_at: datetime
    total_runs: int = Field(ge=0)
    gate_passed_runs: int = Field(ge=0)
    average_pass_rate: float | None = Field(default=None, ge=0, le=100)
    completed_reviews: int = Field(ge=0)
    candidate_wins: int = Field(ge=0)
    reference_wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    candidate_average_score: float | None = Field(default=None, ge=1, le=5)
    reference_average_score: float | None = Field(default=None, ge=1, le=5)


class EvaluationVersionQualitySummaryResponse(BaseModel):
    """同一完整冻结快照下的拟人评测质量事实。"""

    snapshot: EvaluationVersionSnapshotResponse
    first_run_at: datetime
    latest_run_at: datetime
    total_runs: int = Field(ge=1)
    gate_passed_runs: int = Field(ge=0)
    average_pass_rate: float = Field(ge=0, le=100)
    completed_reviews: int = Field(ge=0)
    candidate_wins: int = Field(ge=0)
    reference_wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    candidate_average_score: float | None = Field(default=None, ge=1, le=5)
    reference_average_score: float | None = Field(default=None, ge=1, le=5)


class EvaluationQualityComparisonKeyResponse(BaseModel):
    """限定同源基线比较的评测集版本与模型。"""

    suite_key: str
    suite_version: int
    provider: str
    model: str


class EvaluationQualityBaselineComparisonResponse(BaseModel):
    """最新冻结快照相对紧邻同源基线的有门槛观察差异。"""

    key: EvaluationQualityComparisonKeyResponse
    candidate: EvaluationVersionQualitySummaryResponse
    baseline: EvaluationVersionQualitySummaryResponse
    minimum_runs_per_snapshot: int = Field(ge=1)
    minimum_reviews_per_snapshot: int = Field(ge=1)
    automatic_regression_comparable: bool
    blind_review_comparable: bool
    pass_rate_delta_percentage_points: float | None = Field(default=None, ge=-100, le=100)
    candidate_average_score_delta: float | None = Field(default=None, ge=-4, le=4)
    reference_average_score_delta: float | None = Field(default=None, ge=-4, le=4)
    statistical_significance_assessed: Literal[False]
    causal_conclusion_allowed: Literal[False]


class EvaluationQualityHistoryResponse(BaseModel):
    """当前 Agent 的有界趋势与冻结版本质量对比。"""

    generated_at: datetime
    window_started_at: datetime
    window_ended_at: datetime
    window_minutes: int = Field(ge=1_440, le=129_600)
    bucket_minutes: int = Field(ge=60, le=129_600)
    review_attribution: QualityReviewAttribution
    total_runs: int = Field(ge=0)
    completed_reviews: int = Field(ge=0)
    trend: tuple[EvaluationQualityTrendPointResponse, ...]
    versions: tuple[EvaluationVersionQualitySummaryResponse, ...]
    baseline_comparisons: tuple[EvaluationQualityBaselineComparisonResponse, ...]
    comparable_versions: bool
    automatic_actions_allowed: Literal[False]


class EvaluationDecisionCreate(BaseModel):
    """明确选取同源冻结快照并记录受控人工结论。"""

    window_minutes: Literal[10_080, 43_200, 129_600] = 43_200
    candidate: EvaluationVersionSnapshotResponse
    baseline: EvaluationVersionSnapshotResponse
    outcome: EvaluationDecisionOutcome
    reason: EvaluationDecisionReason


class EvaluationDecisionSummaryResponse(BaseModel):
    """不携带报告字节的不可变决策索引。"""

    id: UUID
    created_by: UUID
    created_at: datetime
    outcome: EvaluationDecisionOutcome
    reason: EvaluationDecisionReason
    sha256: str


class EvaluationDecisionListResponse(BaseModel):
    items: tuple[EvaluationDecisionSummaryResponse, ...]


class EvaluationDecisionReportResponse(BaseModel):
    """严格白名单的安全冻结报告，不包含原始评测或评审正文。"""

    schema_version: Literal[1]
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    created_by: UUID
    created_at: datetime
    window_started_at: datetime
    window_ended_at: datetime
    window_minutes: int
    review_attribution: QualityReviewAttribution
    comparison: EvaluationQualityBaselineComparisonResponse
    outcome: EvaluationDecisionOutcome
    reason: EvaluationDecisionReason
    statistical_significance_assessed: Literal[False]
    causal_conclusion_allowed: Literal[False]
    automatic_actions_allowed: Literal[False]


class EvaluationDecisionResponse(EvaluationDecisionSummaryResponse):
    report: EvaluationDecisionReportResponse


class EvaluationApprovalCreate(BaseModel):
    """为一个不可变评测决策写入唯一终态审批。"""

    outcome: EvaluationApprovalOutcome
    reason: EvaluationApprovalReason
    release_environment: EvaluationReleaseEnvironment | None = None
    change_reference: str | None = Field(default=None, min_length=1, max_length=120)


class EvaluationApprovalReleaseReferenceResponse(BaseModel):
    """只读发布变更引用，不授予发布或配置写入能力。"""

    environment: EvaluationReleaseEnvironment
    change_id: str


class EvaluationDecisionSignatureResponse(BaseModel):
    """审批证明公开的 Ed25519 验签材料。"""

    algorithm: Literal["Ed25519"]
    key_id: str
    public_key: str
    value: str


class EvaluationApprovalProofPayloadResponse(BaseModel):
    """签名覆盖的审批白名单载荷。"""

    schema_version: Literal[1]
    id: UUID
    decision_id: UUID
    decision_sha256: str
    tenant_id: UUID
    agent_id: UUID
    approved_by: UUID
    approved_at: datetime
    outcome: EvaluationApprovalOutcome
    reason: EvaluationApprovalReason
    release_reference: EvaluationApprovalReleaseReferenceResponse | None
    automatic_actions_allowed: Literal[False]


class EvaluationApprovalProofResponse(BaseModel):
    """可下载并独立验证的完整签名证明。"""

    schema_version: Literal[1]
    payload: EvaluationApprovalProofPayloadResponse
    signature: EvaluationDecisionSignatureResponse


class EvaluationApprovalResponse(BaseModel):
    """审批索引字段及其不可变签名证明。"""

    id: UUID
    decision_id: UUID
    approved_by: UUID
    approved_at: datetime
    outcome: EvaluationApprovalOutcome
    reason: EvaluationApprovalReason
    release_environment: EvaluationReleaseEnvironment | None
    change_reference: str | None
    sha256: str
    proof: EvaluationApprovalProofResponse


class EvaluationApprovalVerificationResponse(BaseModel):
    """服务端对持久化审批证明执行的独立完整性检查。"""

    valid: bool
    content_hash_valid: bool
    canonical_content_valid: bool
    decision_hash_matches: bool
    signature_valid: bool
    verified_at: datetime


class UnifiedQualityOverviewResponse(BaseModel):
    """拟人全历史与告警有界窗口的统一只读质量概览。"""

    generated_at: datetime
    evaluation_scope: QualityEvaluationScope
    operations_window_minutes: int = Field(ge=5, le=525_600)
    coverage: QualityDataCoverage
    evaluation: EvaluationReportResponse
    alert_recommendations: ObservabilityAlertRecommendationQualityMetricsResponse
    automatic_actions_allowed: Literal[False]
