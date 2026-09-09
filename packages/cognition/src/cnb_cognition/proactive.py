"""不依赖队列和渠道实现的主动行为评分与边界策略。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProactivePolicy:
    """一次主动行为评估使用的已发布配置快照。"""

    enabled: bool
    minimum_score: float
    daily_budget: int
    quiet_hours_start: int
    quiet_hours_end: int
    require_recent_user_days: int


@dataclass(frozen=True, slots=True)
class ProactiveContext:
    """不包含消息正文的关系、时间和预算信号。"""

    local_hour: int
    days_since_user_activity: float
    importance: float
    confidence: float
    affinity: float
    trust: float
    familiarity: float
    used_budget: int
    social_cost: int
    has_boundary_block: bool


@dataclass(frozen=True, slots=True)
class ProactiveDecision:
    """供确定性执行层审计的评分结果。"""

    approved: bool
    score: float
    reasons: tuple[str, ...]


class ProactivePolicyEvaluator:
    """先执行硬边界，再用关系与事件信号计算可解释评分。"""

    def evaluate(self, policy: ProactivePolicy, context: ProactiveContext) -> ProactiveDecision:
        score = self._score(context)
        reasons: list[str] = []
        if not policy.enabled:
            reasons.append("proactive_disabled")
        if context.has_boundary_block:
            reasons.append("relationship_boundary_blocked")
        if self._in_quiet_hours(
            context.local_hour,
            start=policy.quiet_hours_start,
            end=policy.quiet_hours_end,
        ):
            reasons.append("quiet_hours")
        if context.days_since_user_activity > policy.require_recent_user_days:
            reasons.append("user_inactive_too_long")
        if context.used_budget + context.social_cost > policy.daily_budget:
            reasons.append("daily_social_budget_exhausted")
        if score < policy.minimum_score:
            reasons.append("score_below_threshold")
        if not reasons:
            reasons.append("policy_approved")
        return ProactiveDecision(
            approved=reasons == ["policy_approved"],
            score=score,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _score(context: ProactiveContext) -> float:
        recency = max(0.0, 1.0 - context.days_since_user_activity / 30.0)
        raw = (
            context.importance * 0.25
            + context.confidence * 0.15
            + context.affinity * 0.15
            + context.trust * 0.15
            + context.familiarity * 0.15
            + recency * 0.15
        )
        return round(max(0.0, min(1.0, raw)), 6)

    @staticmethod
    def _in_quiet_hours(hour: int, *, start: int, end: int) -> bool:
        if not 0 <= hour <= 23:
            raise ValueError("本地小时必须位于 0 到 23 之间")
        if not 0 <= start <= 23 or not 0 <= end <= 23:
            raise ValueError("安静时段小时必须位于 0 到 23 之间")
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end
