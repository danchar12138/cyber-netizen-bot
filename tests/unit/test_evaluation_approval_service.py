"""评测决策审批、Ed25519 证明和职责分离测试。"""

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    EvaluationApprovalConflictError,
    EvaluationApprovalService,
    EvaluationApprovalSigningError,
    EvaluationApprovalValidationError,
    SecretManagementService,
    build_default_registry,
)
from cnb_domain import (
    ConfigScope,
    EvaluationApprovalOutcome,
    EvaluationApprovalReason,
    EvaluationDecisionApprovalRecord,
    EvaluationDecisionOutcome,
    EvaluationDecisionReason,
    EvaluationDecisionRecord,
    EvaluationReleaseEnvironment,
)
from cnb_infrastructure import (
    EVALUATION_APPROVAL_SIGNING_KEY,
    Ed25519EvaluationApprovalSigner,
    MemorySecretStore,
)


class ApprovalRepository:
    """允许测试替换持久化证明字节的最小仓储。"""

    def __init__(self, decision: EvaluationDecisionRecord) -> None:
        self.decision = decision
        self.approval: EvaluationDecisionApprovalRecord | None = None

    async def get_decision(
        self, *, decision_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionRecord | None:
        if (
            self.decision.id == decision_id
            and self.decision.tenant_id == tenant_id
            and self.decision.agent_id == agent_id
        ):
            return self.decision
        return None

    async def save_approval(
        self, record: EvaluationDecisionApprovalRecord
    ) -> EvaluationDecisionApprovalRecord:
        if self.approval is not None:
            raise EvaluationApprovalConflictError("评测决策已经存在终态审批")
        self.approval = record
        return record

    async def get_approval(
        self, *, decision_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionApprovalRecord | None:
        item = self.approval
        if (
            item is None
            or item.decision_id != decision_id
            or item.tenant_id != tenant_id
            or item.agent_id != agent_id
        ):
            return None
        return item


def _decision(
    *,
    tenant_id: UUID,
    agent_id: UUID,
    created_by: UUID,
    outcome: EvaluationDecisionOutcome = EvaluationDecisionOutcome.ADOPT_CANDIDATE,
) -> EvaluationDecisionRecord:
    content = b'{"schema_version":1}'
    return EvaluationDecisionRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        created_by=created_by,
        created_at=datetime.now(UTC),
        outcome=outcome,
        reason=EvaluationDecisionReason.QUALITY_GAIN,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


async def _service(
    decision: EvaluationDecisionRecord,
) -> tuple[EvaluationApprovalService, ApprovalRepository, SecretManagementService]:
    store = MemorySecretStore()
    secret_service = SecretManagementService(build_default_registry(), store)
    await secret_service.set_secret(
        key=EVALUATION_APPROVAL_SIGNING_KEY,
        scope_type=ConfigScope.AGENT,
        scope_id=decision.agent_id,
        plaintext=base64.b64encode(bytes(range(32))).decode("ascii"),
    )
    repository = ApprovalRepository(decision)
    return (
        EvaluationApprovalService(
            repository,
            Ed25519EvaluationApprovalSigner(store),
            agent_id=decision.agent_id,
        ),
        repository,
        secret_service,
    )


async def test_approval_signs_canonical_proof_and_survives_key_rotation() -> None:
    tenant_id, agent_id, creator_id, approver_id = uuid4(), uuid4(), uuid4(), uuid4()
    decision = _decision(
        tenant_id=tenant_id,
        agent_id=agent_id,
        created_by=creator_id,
    )
    service, repository, secret_service = await _service(decision)

    approval = await service.create_approval(
        decision_id=decision.id,
        tenant_id=tenant_id,
        actor_id=approver_id,
        outcome=EvaluationApprovalOutcome.APPROVED,
        reason=EvaluationApprovalReason.EVIDENCE_CONFIRMED,
        release_environment=EvaluationReleaseEnvironment.STAGING,
        change_reference="release-2026.09.17-54",
    )
    proof = json.loads(approval.content)
    initial_verification = await service.verify_approval(
        decision_id=decision.id,
        tenant_id=tenant_id,
    )
    secret_metadata = (await secret_service.list_metadata())[0]
    await secret_service.rotate_secret(
        secret_metadata.id,
        plaintext=base64.b64encode(bytes(reversed(range(32)))).decode("ascii"),
    )
    rotated_verification = await service.verify_approval(
        decision_id=decision.id,
        tenant_id=tenant_id,
    )

    assert repository.approval == approval
    assert approval.sha256 == hashlib.sha256(approval.content).hexdigest()
    assert proof["payload"]["decision_sha256"] == decision.sha256
    assert proof["payload"]["automatic_actions_allowed"] is False
    assert proof["payload"]["release_reference"] == {
        "change_id": "release-2026.09.17-54",
        "environment": "staging",
    }
    assert proof["signature"]["algorithm"] == "Ed25519"
    assert secret_metadata.masked_hint not in approval.content.decode("utf-8")
    assert initial_verification.valid is True
    assert rotated_verification.valid is True

    with pytest.raises(EvaluationApprovalConflictError, match="终态审批"):
        await service.create_approval(
            decision_id=decision.id,
            tenant_id=tenant_id,
            actor_id=uuid4(),
            outcome=EvaluationApprovalOutcome.APPROVED,
            reason=EvaluationApprovalReason.EVIDENCE_CONFIRMED,
            release_environment=None,
            change_reference=None,
        )


async def test_approval_rejects_self_review_and_missing_signing_key() -> None:
    tenant_id, agent_id, creator_id = uuid4(), uuid4(), uuid4()
    decision = _decision(tenant_id=tenant_id, agent_id=agent_id, created_by=creator_id)
    service, _, _ = await _service(decision)

    with pytest.raises(EvaluationApprovalValidationError, match="不能审批自己"):
        await service.create_approval(
            decision_id=decision.id,
            tenant_id=tenant_id,
            actor_id=creator_id,
            outcome=EvaluationApprovalOutcome.APPROVED,
            reason=EvaluationApprovalReason.EVIDENCE_CONFIRMED,
            release_environment=None,
            change_reference=None,
        )

    missing_key_service = EvaluationApprovalService(
        ApprovalRepository(decision),
        Ed25519EvaluationApprovalSigner(MemorySecretStore()),
        agent_id=agent_id,
    )
    with pytest.raises(EvaluationApprovalSigningError, match="尚未配置"):
        await missing_key_service.create_approval(
            decision_id=decision.id,
            tenant_id=tenant_id,
            actor_id=uuid4(),
            outcome=EvaluationApprovalOutcome.APPROVED,
            reason=EvaluationApprovalReason.EVIDENCE_CONFIRMED,
            release_environment=None,
            change_reference=None,
        )


@pytest.mark.parametrize(
    (
        "decision_outcome",
        "approval_outcome",
        "reason",
        "environment",
        "change_reference",
        "message",
    ),
    [
        (
            EvaluationDecisionOutcome.WAIT_FOR_EVIDENCE,
            EvaluationApprovalOutcome.APPROVED,
            EvaluationApprovalReason.EVIDENCE_CONFIRMED,
            None,
            None,
            "等待更多证据",
        ),
        (
            EvaluationDecisionOutcome.ADOPT_CANDIDATE,
            EvaluationApprovalOutcome.APPROVED,
            EvaluationApprovalReason.RISK_UNRESOLVED,
            None,
            None,
            "证据已确认",
        ),
        (
            EvaluationDecisionOutcome.ADOPT_CANDIDATE,
            EvaluationApprovalOutcome.REJECTED,
            EvaluationApprovalReason.RISK_UNRESOLVED,
            EvaluationReleaseEnvironment.PRODUCTION,
            "change-54",
            "不能关联发布",
        ),
        (
            EvaluationDecisionOutcome.ADOPT_CANDIDATE,
            EvaluationApprovalOutcome.APPROVED,
            EvaluationApprovalReason.EVIDENCE_CONFIRMED,
            EvaluationReleaseEnvironment.PRODUCTION,
            "https://release.example.test/54",
            "格式无效",
        ),
    ],
)
async def test_approval_rejects_invalid_governance_combinations(
    decision_outcome: EvaluationDecisionOutcome,
    approval_outcome: EvaluationApprovalOutcome,
    reason: EvaluationApprovalReason,
    environment: EvaluationReleaseEnvironment | None,
    change_reference: str | None,
    message: str,
) -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    decision = _decision(
        tenant_id=tenant_id,
        agent_id=agent_id,
        created_by=uuid4(),
        outcome=decision_outcome,
    )
    service, _, _ = await _service(decision)

    with pytest.raises(EvaluationApprovalValidationError, match=message):
        await service.create_approval(
            decision_id=decision.id,
            tenant_id=tenant_id,
            actor_id=uuid4(),
            outcome=approval_outcome,
            reason=reason,
            release_environment=environment,
            change_reference=change_reference,
        )


async def test_approval_verification_detects_persisted_content_tampering() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    decision = _decision(tenant_id=tenant_id, agent_id=agent_id, created_by=uuid4())
    service, repository, _ = await _service(decision)
    approval = await service.create_approval(
        decision_id=decision.id,
        tenant_id=tenant_id,
        actor_id=uuid4(),
        outcome=EvaluationApprovalOutcome.APPROVED,
        reason=EvaluationApprovalReason.EVIDENCE_CONFIRMED,
        release_environment=None,
        change_reference=None,
    )
    tampered = approval.content.replace(b'"approved"', b'"rejected"')
    repository.approval = replace(approval, content=tampered)

    verification = await service.verify_approval(
        decision_id=decision.id,
        tenant_id=tenant_id,
    )

    assert verification.valid is False
    assert verification.content_hash_valid is False
    assert verification.decision_hash_matches is False
    assert verification.signature_valid is False
