"""带来源、优先级和裁剪报告的上下文组装器。"""

from dataclasses import dataclass, replace
from enum import StrEnum


class ContextFragmentKind(StrEnum):
    """可进入模型上下文的来源类别。"""

    SYSTEM = "system"
    PERSONA = "persona"
    WORKING_MEMORY = "working_memory"
    LONG_TERM_MEMORY = "long_term_memory"
    RECENT_MESSAGE = "recent_message"
    USER_CONTEXT = "user_context"


class ContextRole(StrEnum):
    """上下文片段在对话模型中的安全角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


def estimate_tokens(content: str) -> int:
    """提供不依赖厂商分词器、偏保守且可回放的 Token 估算。"""
    if not content:
        return 0
    ascii_count = sum(character.isascii() for character in content)
    non_ascii_count = len(content) - ascii_count
    return max(1, (ascii_count + 3) // 4 + non_ascii_count)


@dataclass(frozen=True, slots=True)
class ContextFragment:
    """一个不可变、可追溯并可独立裁剪的上下文片段。"""

    fragment_id: str
    kind: ContextFragmentKind
    role: ContextRole
    content: str
    priority: int
    required: bool = False
    ordinal: int = 0
    source_id: str | None = None
    token_estimate: int | None = None

    def __post_init__(self) -> None:
        if not self.fragment_id.strip() or not self.content.strip():
            raise ValueError("上下文片段 ID 与内容不能为空")
        if not 0 <= self.priority <= 100:
            raise ValueError("上下文优先级必须位于 0 到 100 之间")
        if self.token_estimate is not None and self.token_estimate < 1:
            raise ValueError("上下文 Token 估算必须大于 0")

    @property
    def tokens(self) -> int:
        return self.token_estimate or estimate_tokens(self.content)


@dataclass(frozen=True, slots=True)
class ContextAssembly:
    """一次确定性上下文选择的结果与可解释裁剪信息。"""

    fragments: tuple[ContextFragment, ...]
    omitted_fragment_ids: tuple[str, ...]
    truncated_fragment_ids: tuple[str, ...]
    estimated_tokens: int
    token_budget: int

    @property
    def clipped(self) -> bool:
        return bool(self.omitted_fragment_ids or self.truncated_fragment_ids)


class ContextAssembler:
    """优先保留必需项和高价值片段，并恢复原始对话顺序。"""

    def assemble(
        self,
        fragments: tuple[ContextFragment, ...],
        *,
        token_budget: int,
    ) -> ContextAssembly:
        if token_budget < 1:
            raise ValueError("上下文 Token 预算必须大于 0")

        ranked = sorted(
            fragments,
            key=lambda item: (item.required, item.priority, item.ordinal),
            reverse=True,
        )
        selected: list[ContextFragment] = []
        omitted: list[str] = []
        truncated: list[str] = []
        consumed = 0
        for fragment in ranked:
            if consumed + fragment.tokens <= token_budget:
                selected.append(fragment)
                consumed += fragment.tokens
                continue
            if fragment.required and consumed < token_budget:
                content = self._truncate_to_budget(fragment.content, token_budget - consumed)
                if content:
                    clipped = replace(
                        fragment,
                        content=content,
                        token_estimate=estimate_tokens(content),
                    )
                    selected.append(clipped)
                    consumed += clipped.tokens
                    truncated.append(fragment.fragment_id)
                    continue
            omitted.append(fragment.fragment_id)

        selected.sort(key=lambda item: item.ordinal)
        omitted_ids = frozenset(omitted)
        omitted_order = tuple(
            item.fragment_id for item in fragments if item.fragment_id in omitted_ids
        )
        return ContextAssembly(
            fragments=tuple(selected),
            omitted_fragment_ids=omitted_order,
            truncated_fragment_ids=tuple(truncated),
            estimated_tokens=consumed,
            token_budget=token_budget,
        )

    @staticmethod
    def _truncate_to_budget(content: str, token_budget: int) -> str:
        """保留内容首尾并用显式标记说明预算裁剪。"""
        marker = "\n[…内容因上下文预算截断…]\n"
        if token_budget < estimate_tokens(marker) + 2:
            return ""
        low, high = 1, len(content)
        best = ""
        while low <= high:
            kept = (low + high) // 2
            head = max(1, kept * 3 // 5)
            tail = max(1, kept - head)
            candidate = f"{content[:head]}{marker}{content[-tail:]}"
            if estimate_tokens(candidate) <= token_budget:
                best = candidate
                low = kept + 1
            else:
                high = kept - 1
        return best
