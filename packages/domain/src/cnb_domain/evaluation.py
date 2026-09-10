"""拟人表现回放、质量门与人工盲评的纯领域类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class EvaluationSuiteStatus(StrEnum):
    """评测集不可变版本的生命周期。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class EvaluationRunStatus(StrEnum):
    """一次自动回放的终态。"""

    COMPLETED = "completed"
    FAILED = "failed"


class BlindReviewPreference(StrEnum):
    """服务端去盲后保存的比较结论。"""

    CANDIDATE = "candidate"
    REFERENCE = "reference"
    TIE = "tie"


@dataclass(frozen=True, slots=True)
class EvaluationCaseDefinition:
    """评测集版本中的一条不可变对话样例。"""

    id: UUID
    suite_id: UUID
    case_key: str
    category: str
    input_text: str
    expected_action: str
    reference_response: str | None
    required_phrases: tuple[str, ...]
    forbidden_phrases: tuple[str, ...]
    sort_order: int


@dataclass(frozen=True, slots=True)
class EvaluationSuiteDefinition:
    """可发布、可回溯的拟人评测集版本。"""

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
    cases: tuple[EvaluationCaseDefinition, ...]
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvaluationCheck:
    """一项不依赖模型裁判的确定性质量门判定。"""

    key: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EvaluationCaseRunResult:
    """自动回放中冻结的输入、输出与确定性检查。"""

    id: UUID
    run_id: UUID
    case_key: str
    category: str
    input_text: str
    expected_action: str
    actual_action: str
    candidate_response: str | None
    reference_response: str | None
    passed: bool
    checks: tuple[EvaluationCheck, ...]
    summary: str
    latency_ms: int


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """一次固定配置与认知版本的自动回放报告。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
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
    estimated_cost_microusd: int
    error_code: str | None
    results: tuple[EvaluationCaseRunResult, ...]
    created_by: UUID
    created_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class BlindReviewScore:
    """单侧回答的四项拟人质量评分，范围均为 1 至 5。"""

    persona_consistency: int
    naturalness: int
    empathy: int
    boundary_respect: int

    @property
    def average(self) -> float:
        """返回四个维度的算术平均值。"""
        return (
            self.persona_consistency + self.naturalness + self.empathy + self.boundary_respect
        ) / 4


@dataclass(frozen=True, slots=True)
class BlindReviewAssignment:
    """稳定随机左右位置的匿名人工评审任务。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    run_id: UUID
    result_id: UUID
    reviewer_id: UUID
    case_key: str
    category: str
    input_text: str
    response_a: str
    response_b: str
    candidate_is_a: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BlindReview:
    """去盲后持久化的人工比较结果。"""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    assignment_id: UUID
    run_id: UUID
    result_id: UUID
    reviewer_id: UUID
    preference: BlindReviewPreference
    candidate_score: BlindReviewScore
    reference_score: BlindReviewScore
    note: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """自动质量门和人工盲评的安全聚合视图。"""

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
