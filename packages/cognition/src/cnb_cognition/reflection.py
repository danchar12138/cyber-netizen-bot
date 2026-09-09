"""对话后异步反思的确定性候选提取；不保存或暴露隐藏思维。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReflectionPlan:
    """只包含可审计摘要、记忆强度和关系变化的反思结果。"""

    title: str
    episode_summary: str
    memory_content: str
    importance: float
    confidence: float
    emotional_weight: float
    affinity_delta: float
    trust_delta: float
    familiarity_delta: float


class DeterministicReflectionEngine:
    """在模型反思可用前提供稳定、安全且可回放的基线。"""

    _IMPORTANCE_MARKERS = (
        "记住",
        "以后",
        "一直",
        "喜欢",
        "不喜欢",
        "生日",
        "工作",
        "家人",
        "目标",
        "计划",
        "remind",
        "remember",
        "prefer",
    )

    def reflect(self, text: str) -> ReflectionPlan:
        normalized = " ".join(text.strip().split())
        if not normalized:
            raise ValueError("反思输入不能为空")
        excerpt = normalized[:360]
        marker_bonus = (
            0.2 if any(item in normalized.casefold() for item in self._IMPORTANCE_MARKERS) else 0
        )
        importance = round(min(0.95, 0.3 + min(len(normalized), 400) / 400 * 0.3 + marker_bonus), 6)
        positive = any(item in normalized for item in ("谢谢", "开心", "喜欢", "太好了", "感谢"))
        negative = any(item in normalized for item in ("难过", "生气", "讨厌", "焦虑", "不舒服"))
        emotional_weight = 0.25 if positive else -0.25 if negative else 0.0
        return ReflectionPlan(
            title=f"围绕“{excerpt[:36]}”的交流",
            episode_summary="用户与 Agent 完成了一次可追溯的对话互动。",
            memory_content=f"用户在一次对话中提到：{excerpt}",
            importance=importance,
            confidence=0.65,
            emotional_weight=emotional_weight,
            affinity_delta=0.01 if positive else 0.0,
            trust_delta=0.0,
            familiarity_delta=0.02,
        )
