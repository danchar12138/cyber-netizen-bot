"""无厂商依赖的 embedding 编码与长期记忆混合重排。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b
from math import exp, log, sqrt
from typing import Protocol

from cnb_domain import MemoryConfirmation, MemoryRecall, RawMemoryCandidate


class EmbeddingEncoder(Protocol):
    """生成可版本化向量的纯应用端口。"""

    @property
    def version(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def encode(self, text: str) -> tuple[float, ...]: ...


class DeterministicHashEmbedding:
    """无需外部密钥的稳定字符 n-gram 基线编码器。"""

    def __init__(self, *, dimensions: int = 256, version: str = "local-hash-v1") -> None:
        if dimensions < 16:
            raise ValueError("embedding 维度不能小于 16")
        if not version.strip():
            raise ValueError("embedding 版本不能为空")
        self._dimensions = dimensions
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def encode(self, text: str) -> tuple[float, ...]:
        normalized = "".join(text.casefold().split())
        if not normalized:
            return tuple(0.0 for _ in range(self._dimensions))
        tokens = [
            *normalized,
            *(normalized[index : index + 2] for index in range(len(normalized) - 1)),
        ]
        vector = [0.0] * self._dimensions
        for token in tokens:
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "big")
            index = number % self._dimensions
            vector[index] += -1.0 if number & 1 else 1.0
        norm = sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


@dataclass(frozen=True, slots=True)
class HybridRecallWeights:
    """一次配置版本固定使用的混合召回权重。"""

    full_text: float = 0.25
    semantic: float = 0.35
    recency: float = 0.15
    importance: float = 0.15
    relationship: float = 0.10

    def __post_init__(self) -> None:
        values = (
            self.full_text,
            self.semantic,
            self.recency,
            self.importance,
            self.relationship,
        )
        if any(value < 0 for value in values) or sum(values) <= 0:
            raise ValueError("记忆召回权重必须非负且至少一项大于 0")


class HybridMemoryRanker:
    """组合全文、语义、时间、重要性和关系连续性的确定性重排器。"""

    version = "hybrid-v1"

    def rank(
        self,
        candidates: tuple[RawMemoryCandidate, ...],
        *,
        now: datetime | None = None,
        limit: int,
        recency_half_life_days: float,
        weights: HybridRecallWeights,
    ) -> tuple[MemoryRecall, ...]:
        if limit < 1:
            raise ValueError("记忆召回数量必须大于 0")
        if recency_half_life_days <= 0:
            raise ValueError("记忆时间半衰期必须大于 0")
        evaluated_at = now or datetime.now(UTC)
        total_weight = (
            weights.full_text
            + weights.semantic
            + weights.recency
            + weights.importance
            + weights.relationship
        )
        results: list[MemoryRecall] = []
        for candidate in candidates:
            age_days = max(
                0.0,
                (evaluated_at - candidate.memory.event_at).total_seconds() / 86400,
            )
            recency = exp(-log(2) * age_days / recency_half_life_days)
            full_text = self._unit(candidate.full_text_score)
            semantic = self._unit(candidate.semantic_score)
            relationship = self._unit(candidate.relationship_score)
            weighted = (
                full_text * weights.full_text
                + semantic * weights.semantic
                + recency * weights.recency
                + candidate.memory.importance * weights.importance
                + relationship * weights.relationship
            ) / total_weight
            confidence_factor = 0.45 + candidate.memory.confidence * 0.55
            confirmation_bonus = (
                0.05 if candidate.memory.confirmation is MemoryConfirmation.CONFIRMED else 0.0
            )
            score = min(1.0, weighted * confidence_factor + confirmation_bonus)
            results.append(
                MemoryRecall(
                    memory=candidate.memory,
                    score=score,
                    components={
                        "full_text": round(full_text, 6),
                        "semantic": round(semantic, 6),
                        "recency": round(recency, 6),
                        "importance": round(candidate.memory.importance, 6),
                        "relationship": round(relationship, 6),
                        "confidence": round(candidate.memory.confidence, 6),
                        "ranker_version": self.version,
                    },
                )
            )
        results.sort(
            key=lambda item: (item.score, item.memory.event_at, str(item.memory.id)),
            reverse=True,
        )
        return tuple(results[:limit])

    @staticmethod
    def _unit(value: float) -> float:
        return max(0.0, min(1.0, value))
