"""候选行动、工具契约与确定性策略门。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol

from cnb_domain import JsonValue


class CognitiveAction(StrEnum):
    """认知运行时可以提出、但不能越权直接执行的行动。"""

    REPLY = "reply"
    ASK = "ask"
    WAIT = "wait"
    NO_REPLY = "no_reply"
    TOOL = "tool"


class ToolRiskLevel(StrEnum):
    """工具外部副作用的静态风险级别。"""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ToolSpecification:
    """工具暴露给运行时的类型化、无密钥元数据。"""

    name: str
    description: str
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    risk_level: ToolRiskLevel
    has_external_side_effect: bool


@dataclass(frozen=True, slots=True)
class ToolResult:
    """经过策略批准后工具执行的标准化结果。"""

    ok: bool
    output: Mapping[str, JsonValue] = field(default_factory=lambda: {})
    error_code: str | None = None


class Tool(Protocol):
    """工具端口；实现不得在参数校验和策略批准前产生副作用。"""

    @property
    def specification(self) -> ToolSpecification: ...

    async def execute(self, arguments: Mapping[str, JsonValue]) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    """Deliberation 产生的结构化行动候选。"""

    action: CognitiveAction
    confidence: float
    reason_summary: str
    parameters: dict[str, str] = field(default_factory=lambda: {})
    tool_name: str | None = None
    risk_level: ToolRiskLevel = ToolRiskLevel.NONE
    has_external_side_effect: bool = False
    required_permission: str | None = None
    network_host: str | None = None
    estimated_cost_units: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("候选置信度必须位于 0 到 1 之间")
        if self.action is CognitiveAction.TOOL and not self.tool_name:
            raise ValueError("工具候选必须声明工具名称")
        if self.estimated_cost_units < 0:
            raise ValueError("工具候选成本不能小于 0")


@dataclass(frozen=True, slots=True)
class PolicyRuleSet:
    """一次运行固定使用的策略版本。"""

    version: int = 0
    allowed_tools: frozenset[str] = frozenset()
    maximum_tool_risk: ToolRiskLevel = ToolRiskLevel.LOW
    allow_external_side_effects: bool = False
    allow_no_reply: bool = True
    network_allowlist: frozenset[str] = frozenset()
    maximum_tool_calls_per_run: int = 0
    maximum_cost_units_per_run: int = 0
    approval_required_at_or_above: ToolRiskLevel = ToolRiskLevel.MEDIUM


@dataclass(frozen=True, slots=True)
class ToolPolicyContext:
    """策略评估时由可信应用层提供的权限、审批和预算事实。"""

    permissions: frozenset[str] = frozenset()
    approved_tools: frozenset[str] = frozenset()
    tool_calls_used: int = 0
    cost_units_used: int = 0


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """策略门的选择结果，理由仅包含可安全展示的摘要。"""

    selected: ActionCandidate
    rejected_reasons: tuple[str, ...]
    policy_version: int


class DeterministicPolicyGate:
    """以确定性代码约束权限、风险与外部副作用。"""

    _RISK_ORDER: ClassVar[Mapping[ToolRiskLevel, int]] = {
        ToolRiskLevel.NONE: 0,
        ToolRiskLevel.LOW: 1,
        ToolRiskLevel.MEDIUM: 2,
        ToolRiskLevel.HIGH: 3,
    }

    def evaluate(
        self,
        candidates: tuple[ActionCandidate, ...],
        rules: PolicyRuleSet,
        context: ToolPolicyContext | None = None,
    ) -> PolicyEvaluation:
        if not candidates:
            raise ValueError("策略门至少需要一个行动候选")

        policy_context = context or ToolPolicyContext()
        rejected: list[str] = []
        for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
            denial = self._denial_reason(candidate, rules, policy_context)
            if denial is None:
                return PolicyEvaluation(candidate, tuple(rejected), rules.version)
            rejected.append(denial)

        fallback = ActionCandidate(
            action=CognitiveAction.WAIT,
            confidence=1,
            reason_summary="没有候选行动通过当前策略，保持等待。",
        )
        return PolicyEvaluation(fallback, tuple(rejected), rules.version)

    def _denial_reason(
        self,
        candidate: ActionCandidate,
        rules: PolicyRuleSet,
        context: ToolPolicyContext,
    ) -> str | None:
        if candidate.action is CognitiveAction.NO_REPLY and not rules.allow_no_reply:
            return "当前策略不允许主动选择不回复。"
        if candidate.action is not CognitiveAction.TOOL:
            return None
        if candidate.tool_name not in rules.allowed_tools:
            return f"工具 {candidate.tool_name} 未在当前策略允许列表中。"
        if self._RISK_ORDER[candidate.risk_level] > self._RISK_ORDER[rules.maximum_tool_risk]:
            return f"工具 {candidate.tool_name} 的风险级别超过当前策略上限。"
        if candidate.has_external_side_effect and not rules.allow_external_side_effects:
            return f"工具 {candidate.tool_name} 具有未经允许的外部副作用。"
        if (
            candidate.required_permission is not None
            and candidate.required_permission not in context.permissions
        ):
            return f"工具 {candidate.tool_name} 缺少所需权限。"
        if candidate.network_host and candidate.network_host not in rules.network_allowlist:
            return f"工具 {candidate.tool_name} 的网络目标不在允许列表。"
        if context.tool_calls_used >= rules.maximum_tool_calls_per_run:
            return f"工具 {candidate.tool_name} 已超过本次运行频率预算。"
        if (
            context.cost_units_used + candidate.estimated_cost_units
            > rules.maximum_cost_units_per_run
        ):
            return f"工具 {candidate.tool_name} 已超过本次运行成本预算。"
        if (
            self._RISK_ORDER[candidate.risk_level]
            >= self._RISK_ORDER[rules.approval_required_at_or_above]
            and candidate.tool_name not in context.approved_tools
        ):
            return f"工具 {candidate.tool_name} 尚未获得所需审批。"
        return None
