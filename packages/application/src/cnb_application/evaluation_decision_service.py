"""拟人评测决策证据的不可变、安全序列化。"""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from cnb_application.quality_service import EvaluationQualityHistoryService
from cnb_domain import (
    EvaluationDecisionOutcome,
    EvaluationDecisionReason,
    EvaluationDecisionRecord,
    EvaluationVersionSnapshot,
)


class EvaluationDecisionValidationError(ValueError):
    """选择的快照或人工结论不满足证据边界。"""


class EvaluationDecisionNotFoundError(LookupError):
    """当前租户和智能体内未找到决策。"""


class EvaluationDecisionRepository(Protocol):
    """决策记录只允许插入及在当前作用域内读取。"""

    async def save_decision(self, record: EvaluationDecisionRecord) -> EvaluationDecisionRecord: ...

    async def list_decisions(
        self, *, tenant_id: UUID, agent_id: UUID, limit: int
    ) -> tuple[EvaluationDecisionRecord, ...]: ...

    async def get_decision(
        self, *, decision_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionRecord | None: ...


class EvaluationDecisionService:
    """手工选定已存在证据并冻结报告；不执行调参或发布。"""

    def __init__(
        self,
        repository: EvaluationDecisionRepository,
        history_service: EvaluationQualityHistoryService,
        *,
        agent_id: UUID,
    ) -> None:
        self._repository = repository
        self._history_service = history_service
        self._agent_id = agent_id

    async def create_decision(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        window_minutes: int,
        candidate: EvaluationVersionSnapshot,
        baseline: EvaluationVersionSnapshot,
        outcome: EvaluationDecisionOutcome,
        reason: EvaluationDecisionReason,
    ) -> EvaluationDecisionRecord:
        created_at = datetime.now(UTC)
        history = await self._history_service.get_history(
            tenant_id=tenant_id,
            window_minutes=window_minutes,
            bucket_minutes=1_440,
            now=created_at,
        )
        versions = {item.snapshot: item for item in history.versions}
        if candidate not in versions or baseline not in versions:
            raise EvaluationDecisionValidationError("所选完整快照不在当前质量窗口内")
        try:
            comparison = self._history_service.compare_snapshots(
                versions[candidate], versions[baseline]
            )
        except ValueError as error:
            raise EvaluationDecisionValidationError(str(error)) from error
        if outcome is not EvaluationDecisionOutcome.WAIT_FOR_EVIDENCE and not (
            comparison.automatic_regression_comparable and comparison.blind_review_comparable
        ):
            raise EvaluationDecisionValidationError(
                "采纳或保持基线前，双方回归和盲评样本均须达到门槛"
            )

        decision_id = uuid4()
        report = {
            "schema_version": 1,
            "id": str(decision_id),
            "tenant_id": str(tenant_id),
            "agent_id": str(self._agent_id),
            "created_by": str(actor_id),
            "created_at": created_at.isoformat(),
            "window_started_at": history.window_started_at.isoformat(),
            "window_ended_at": history.window_ended_at.isoformat(),
            "window_minutes": history.window_minutes,
            "review_attribution": history.review_attribution.value,
            "comparison": asdict(comparison),
            "outcome": outcome.value,
            "reason": reason.value,
            "statistical_significance_assessed": False,
            "causal_conclusion_allowed": False,
            "automatic_actions_allowed": False,
        }
        # 报告仅取质量摘要的封闭字段集；不给自由文本任何序列化入口。
        content = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda value: value.isoformat() if isinstance(value, datetime) else value,
        ).encode("utf-8")
        record = EvaluationDecisionRecord(
            id=decision_id,
            tenant_id=tenant_id,
            agent_id=self._agent_id,
            created_by=actor_id,
            created_at=created_at,
            outcome=outcome,
            reason=reason,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )
        return await self._repository.save_decision(record)

    async def list_decisions(
        self, *, tenant_id: UUID, limit: int = 20
    ) -> tuple[EvaluationDecisionRecord, ...]:
        return await self._repository.list_decisions(
            tenant_id=tenant_id, agent_id=self._agent_id, limit=max(1, min(limit, 100))
        )

    async def get_decision(self, *, decision_id: UUID, tenant_id: UUID) -> EvaluationDecisionRecord:
        record = await self._repository.get_decision(
            decision_id=decision_id, tenant_id=tenant_id, agent_id=self._agent_id
        )
        if record is None:
            raise EvaluationDecisionNotFoundError(f"评测决策不存在：{decision_id}")
        return record
