"""对话后异步反思的确定性候选提取；不保存或暴露隐藏思维。"""

import re
from dataclasses import dataclass
from enum import StrEnum

from cnb_domain import MemoryKind, MemorySensitivity


class MemoryWriteMode(StrEnum):
    """长期记忆候选的运营策略。"""

    HIGH_PRECISION = "high_precision"
    BALANCED = "balanced"


class MemoryWriteDecision(StrEnum):
    """不携带正文的长期记忆写入决策。"""

    WRITE = "write"
    SKIP_CREDENTIAL = "skip_credential"
    SKIP_SMALL_TALK = "skip_small_talk"
    SKIP_QUESTION = "skip_question"
    SKIP_LOW_SIGNAL = "skip_low_signal"


class RelationshipSignal(StrEnum):
    """可审计且不含隐藏推理的关系信号。"""

    NEUTRAL = "neutral"
    WARMTH = "warmth"
    SELF_DISCLOSURE = "self_disclosure"
    CORRECTION = "correction"
    BOUNDARY = "boundary"
    HOSTILITY = "hostility"


@dataclass(frozen=True, slots=True)
class RelationshipContext:
    """关系校准只需要的最小当前状态。"""

    affinity: float = 0.0
    trust: float = 0.0
    familiarity: float = 0.0
    interaction_count: int = 0
    boundaries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("亲和度", self.affinity),
            ("信任度", self.trust),
            ("熟悉度", self.familiarity),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name}必须位于 0 到 1 之间")
        if self.interaction_count < 0:
            raise ValueError("互动次数不能为负数")


@dataclass(frozen=True, slots=True)
class ReflectionPolicy:
    """可由配置中心管理的确定性反思策略。"""

    memory_write_mode: MemoryWriteMode = MemoryWriteMode.HIGH_PRECISION
    positive_relationship_step: float = 0.02
    negative_relationship_step: float = 0.04
    familiarity_step: float = 0.015

    def __post_init__(self) -> None:
        for name, value in (
            ("正向关系步长", self.positive_relationship_step),
            ("负向关系步长", self.negative_relationship_step),
            ("熟悉度步长", self.familiarity_step),
        ):
            if not 0 <= value <= 0.2:
                raise ValueError(f"{name}必须位于 0 到 0.2 之间")


@dataclass(frozen=True, slots=True)
class ReflectionPlan:
    """只包含可审计摘要、记忆强度和关系变化的反思结果。"""

    title: str
    episode_summary: str
    memory_content: str | None
    memory_kind: MemoryKind | None
    memory_sensitivity: MemorySensitivity | None
    memory_decision: MemoryWriteDecision
    memory_reason_codes: tuple[str, ...]
    importance: float
    confidence: float
    emotional_weight: float
    relationship_signals: tuple[RelationshipSignal, ...]
    affinity_delta: float
    trust_delta: float
    familiarity_delta: float
    relationship_summary: str
    boundaries: tuple[str, ...]


class DeterministicReflectionEngine:
    """提供高精度、安全且可回放的中文优先反思基线。"""

    _CREDENTIAL_MARKERS = (
        "密码",
        "验证码",
        "访问令牌",
        "密钥",
        "私钥",
        "助记词",
        "银行卡号",
        "信用卡号",
        "cvv",
        "password",
        "api key",
        "access token",
        "private key",
        "secret key",
    )
    _CREDENTIAL_PATTERNS = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    _DIRECT_MEMORY_MARKERS = (
        "请记住",
        "帮我记住",
        "你要记得",
        "别忘了",
        "remember that",
        "please remember",
    )
    _PREFERENCE_MARKERS = (
        "我喜欢",
        "我不喜欢",
        "我偏好",
        "我习惯",
        "我讨厌",
        "我更喜欢",
        "叫我",
        "称呼我",
        "i like",
        "i dislike",
        "i prefer",
    )
    _PERSONAL_FACT_MARKERS = (
        "我的生日",
        "我生日",
        "我的工作",
        "我在工作",
        "我住在",
        "我来自",
        "我的家人",
        "我养了",
        "我是个",
        "我是一个",
        "我不能吃",
        "我过敏",
        "my birthday",
        "i live in",
        "i work as",
    )
    _PLAN_MARKERS = (
        "我计划",
        "我的计划",
        "我的目标",
        "我打算",
        "我要在",
        "我准备",
        "i plan to",
        "my goal is",
    )
    _IMPORTANT_EVENT_MARKERS = (
        "我毕业",
        "我入职",
        "我离职",
        "我搬家",
        "我结婚",
        "我分手",
        "我确诊",
        "我获奖",
        "我的面试",
        "我的考试",
    )
    _BOUNDARY_MARKERS = (
        "不要主动联系",
        "别主动联系",
        "不要给我发消息",
        "别给我发消息",
        "不要提醒我",
        "别提醒我",
        "不要再问",
        "别再问",
        "我不想聊",
        "不想谈这个",
        "do not contact me",
        "don't contact me",
    )
    _WARMTH_MARKERS = (
        "谢谢",
        "感谢",
        "很开心",
        "太好了",
        "很贴心",
        "信任你",
        "喜欢和你聊",
        "thank you",
        "thanks",
    )
    _DISCLOSURE_MARKERS = (
        "我有点害怕",
        "我很害怕",
        "我很担心",
        "我有点焦虑",
        "我很难过",
        "只告诉你",
        "跟你说个秘密",
        "i am afraid",
        "i'm worried",
    )
    _CORRECTION_MARKERS = (
        "你记错",
        "不是这个",
        "我说的是",
        "你理解错",
        "别误会",
        "that's not what i said",
    )
    _HOSTILITY_MARKERS = (
        "闭嘴",
        "滚开",
        "烦死了",
        "讨厌你",
        "别烦我",
        "你真蠢",
        "shut up",
        "go away",
    )
    _SMALL_TALK = frozenset(
        {
            "你好",
            "嗨",
            "哈喽",
            "早",
            "早上好",
            "中午好",
            "晚上好",
            "晚安",
            "在吗",
            "哈哈",
            "哈哈哈",
            "嗯",
            "哦",
            "好的",
            "收到",
            "hello",
            "hi",
            "good morning",
            "good night",
        }
    )

    def reflect(
        self,
        text: str,
        *,
        relationship: RelationshipContext | None = None,
        policy: ReflectionPolicy | None = None,
    ) -> ReflectionPlan:
        normalized = " ".join(text.strip().split())
        if not normalized:
            raise ValueError("反思输入不能为空")
        folded = normalized.casefold()
        active_policy = policy or ReflectionPolicy()
        current = relationship or RelationshipContext()
        excerpt = normalized[:360]
        decision, memory_kind, reason_codes = self._memory_decision(
            normalized,
            folded=folded,
            mode=active_policy.memory_write_mode,
        )
        signals = self._relationship_signals(folded)
        affinity_delta, trust_delta, familiarity_delta = self._relationship_deltas(
            current,
            signals=signals,
            policy=active_policy,
            decision=decision,
        )
        importance = self._importance(reason_codes, normalized)
        confidence = self._confidence(reason_codes, decision)
        positive = RelationshipSignal.WARMTH in signals
        negative = bool(
            {RelationshipSignal.HOSTILITY, RelationshipSignal.CORRECTION} & set(signals)
        )
        emotional_weight = 0.25 if positive else -0.35 if negative else 0.0
        boundaries = self._boundaries(folded)
        return ReflectionPlan(
            title=self._episode_title(reason_codes, signals),
            episode_summary=("用户与 Agent 完成一次可追溯互动；反思仅保留安全分类与必要记忆。"),
            memory_content=(
                self._memory_content(excerpt, memory_kind)
                if decision is MemoryWriteDecision.WRITE and memory_kind is not None
                else None
            ),
            memory_kind=memory_kind,
            memory_sensitivity=(
                self._memory_sensitivity(reason_codes)
                if decision is MemoryWriteDecision.WRITE
                else None
            ),
            memory_decision=decision,
            memory_reason_codes=reason_codes,
            importance=importance,
            confidence=confidence,
            emotional_weight=emotional_weight,
            relationship_signals=signals,
            affinity_delta=affinity_delta,
            trust_delta=trust_delta,
            familiarity_delta=familiarity_delta,
            relationship_summary=self._relationship_summary(signals),
            boundaries=boundaries,
        )

    def _memory_decision(
        self,
        normalized: str,
        *,
        folded: str,
        mode: MemoryWriteMode,
    ) -> tuple[MemoryWriteDecision, MemoryKind | None, tuple[str, ...]]:
        if self._contains(folded, self._CREDENTIAL_MARKERS) or self._looks_like_credential(
            normalized
        ):
            return MemoryWriteDecision.SKIP_CREDENTIAL, None, ("credential_content",)

        direct = self._contains(folded, self._DIRECT_MEMORY_MARKERS)
        boundary = self._contains(folded, self._BOUNDARY_MARKERS)
        preference = self._contains(folded, self._PREFERENCE_MARKERS)
        personal_fact = self._contains(folded, self._PERSONAL_FACT_MARKERS)
        plan = self._contains(folded, self._PLAN_MARKERS)
        important_event = self._contains(folded, self._IMPORTANT_EVENT_MARKERS)
        reasons = tuple(
            code
            for matched, code in (
                (direct, "explicit_memory_request"),
                (boundary, "interaction_boundary"),
                (preference, "stable_preference"),
                (personal_fact, "personal_fact"),
                (plan, "future_plan"),
                (important_event, "important_event"),
            )
            if matched
        )
        is_question = normalized.endswith(("?", "？"))
        if is_question and not direct and not boundary:
            return MemoryWriteDecision.SKIP_QUESTION, None, ("question_only",)
        if reasons:
            kind = (
                MemoryKind.RELATIONAL
                if boundary
                else MemoryKind.SEMANTIC
                if preference or personal_fact
                else MemoryKind.EPISODIC
            )
            return MemoryWriteDecision.WRITE, kind, reasons

        if mode is MemoryWriteMode.BALANCED and self._is_self_disclosure(folded, normalized):
            return MemoryWriteDecision.WRITE, MemoryKind.EPISODIC, ("self_disclosure",)
        if folded in self._SMALL_TALK:
            return MemoryWriteDecision.SKIP_SMALL_TALK, None, ("small_talk",)
        if is_question:
            return MemoryWriteDecision.SKIP_QUESTION, None, ("question_only",)
        return MemoryWriteDecision.SKIP_LOW_SIGNAL, None, ("low_signal",)

    def _relationship_signals(self, folded: str) -> tuple[RelationshipSignal, ...]:
        signals = tuple(
            signal
            for markers, signal in (
                (self._HOSTILITY_MARKERS, RelationshipSignal.HOSTILITY),
                (self._BOUNDARY_MARKERS, RelationshipSignal.BOUNDARY),
                (self._CORRECTION_MARKERS, RelationshipSignal.CORRECTION),
                (self._DISCLOSURE_MARKERS, RelationshipSignal.SELF_DISCLOSURE),
                (self._WARMTH_MARKERS, RelationshipSignal.WARMTH),
            )
            if self._contains(folded, markers)
        )
        return signals or (RelationshipSignal.NEUTRAL,)

    @staticmethod
    def _relationship_deltas(
        current: RelationshipContext,
        *,
        signals: tuple[RelationshipSignal, ...],
        policy: ReflectionPolicy,
        decision: MemoryWriteDecision,
    ) -> tuple[float, float, float]:
        signal_set = set(signals)
        affinity_delta = 0.0
        trust_delta = 0.0
        if RelationshipSignal.HOSTILITY in signal_set:
            affinity_delta = -min(current.affinity, policy.negative_relationship_step)
            trust_delta = -min(current.trust, policy.negative_relationship_step / 2)
        else:
            if RelationshipSignal.WARMTH in signal_set:
                affinity_delta = policy.positive_relationship_step * (1 - current.affinity)
            if RelationshipSignal.SELF_DISCLOSURE in signal_set:
                trust_delta = policy.positive_relationship_step / 2 * (1 - current.trust)
            if RelationshipSignal.CORRECTION in signal_set:
                trust_delta -= min(current.trust, policy.negative_relationship_step / 2)

        familiarity_factor = 1.0
        if decision is MemoryWriteDecision.SKIP_SMALL_TALK:
            familiarity_factor = 0.25
        elif RelationshipSignal.HOSTILITY in signal_set:
            familiarity_factor = 0.2
        elif decision in {
            MemoryWriteDecision.SKIP_QUESTION,
            MemoryWriteDecision.SKIP_LOW_SIGNAL,
        }:
            familiarity_factor = 0.5
        familiarity_delta = policy.familiarity_step * familiarity_factor * (1 - current.familiarity)
        bounded = tuple(
            round(max(-0.2, min(0.2, value)), 6)
            for value in (affinity_delta, trust_delta, familiarity_delta)
        )
        return bounded[0], bounded[1], bounded[2]

    @staticmethod
    def _importance(reason_codes: tuple[str, ...], normalized: str) -> float:
        weights = {
            "explicit_memory_request": 0.42,
            "interaction_boundary": 0.38,
            "stable_preference": 0.3,
            "personal_fact": 0.32,
            "future_plan": 0.28,
            "important_event": 0.36,
            "self_disclosure": 0.28,
        }
        signal = max((weights.get(code, 0.0) for code in reason_codes), default=0.0)
        return round(min(0.98, 0.2 + signal + min(len(normalized), 240) / 240 * 0.1), 6)

    @staticmethod
    def _confidence(reason_codes: tuple[str, ...], decision: MemoryWriteDecision) -> float:
        if decision is not MemoryWriteDecision.WRITE:
            return 0.9
        if "explicit_memory_request" in reason_codes:
            return 0.95
        if "self_disclosure" in reason_codes:
            return 0.68
        return 0.84

    @staticmethod
    def _memory_content(excerpt: str, kind: MemoryKind) -> str:
        prefixes = {
            MemoryKind.RELATIONAL: "用户明确表达了互动边界或沟通偏好：",
            MemoryKind.SEMANTIC: "用户明确表达了较稳定的信息：",
            MemoryKind.EPISODIC: "用户提到了一项值得保持连续性的计划或经历：",
        }
        return f"{prefixes.get(kind, '用户明确表达：')}{excerpt}"

    @staticmethod
    def _memory_sensitivity(reason_codes: tuple[str, ...]) -> MemorySensitivity:
        if "self_disclosure" in reason_codes:
            return MemorySensitivity.SENSITIVE
        if {
            "interaction_boundary",
            "personal_fact",
            "future_plan",
            "important_event",
        } & set(reason_codes):
            return MemorySensitivity.PERSONAL
        if "stable_preference" in reason_codes:
            return MemorySensitivity.NORMAL
        return MemorySensitivity.PERSONAL

    @staticmethod
    def _episode_title(
        reason_codes: tuple[str, ...], signals: tuple[RelationshipSignal, ...]
    ) -> str:
        titles = (
            ("interaction_boundary", "用户边界表达"),
            ("stable_preference", "用户偏好交流"),
            ("personal_fact", "用户信息交流"),
            ("future_plan", "用户计划交流"),
            ("important_event", "重要经历交流"),
            ("explicit_memory_request", "用户明确记忆请求"),
        )
        for code, title in titles:
            if code in reason_codes:
                return title
        if RelationshipSignal.CORRECTION in signals:
            return "用户纠错互动"
        if RelationshipSignal.HOSTILITY in signals:
            return "关系边界互动"
        return "普通对话互动"

    @staticmethod
    def _relationship_summary(signals: tuple[RelationshipSignal, ...]) -> str:
        labels = {
            RelationshipSignal.NEUTRAL: "完成一次普通互动",
            RelationshipSignal.WARMTH: "用户表达了友好或感谢",
            RelationshipSignal.SELF_DISCLOSURE: "用户进行了谨慎的自我披露",
            RelationshipSignal.CORRECTION: "用户纠正了 Agent 的理解",
            RelationshipSignal.BOUNDARY: "用户明确了互动边界",
            RelationshipSignal.HOSTILITY: "用户表达了拒绝或敌意",
        }
        return "；".join(labels[item] for item in signals) + "；关系状态按有界规则校准。"

    def _boundaries(self, folded: str) -> tuple[str, ...]:
        boundaries: list[str] = []
        if self._contains(
            folded,
            (
                "不要主动联系",
                "别主动联系",
                "不要给我发消息",
                "别给我发消息",
                "不要提醒我",
                "别提醒我",
                "do not contact me",
                "don't contact me",
            ),
        ):
            boundaries.append("不主动联系")
        if self._contains(folded, ("不要再问", "别再问", "我不想聊", "不想谈这个")):
            boundaries.append("尊重用户暂不讨论的话题")
        return tuple(boundaries)

    @staticmethod
    def _contains(value: str, markers: tuple[str, ...]) -> bool:
        return any(marker in value for marker in markers)

    @classmethod
    def _looks_like_credential(cls, value: str) -> bool:
        return any(pattern.search(value) is not None for pattern in cls._CREDENTIAL_PATTERNS)

    @staticmethod
    def _is_self_disclosure(folded: str, normalized: str) -> bool:
        first_person = any(marker in folded for marker in ("我", "我的", "i ", "i'm ", "my "))
        return first_person and len(normalized) >= 12 and not normalized.endswith(("?", "？"))
