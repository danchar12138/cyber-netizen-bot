"""评测决策审批、不可变证明签署与独立验证。"""

import base64
import binascii
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

from cnb_domain import (
    EvaluationApprovalOutcome,
    EvaluationApprovalReason,
    EvaluationApprovalVerification,
    EvaluationDecisionApprovalRecord,
    EvaluationDecisionOutcome,
    EvaluationDecisionRecord,
    EvaluationDecisionSignature,
    EvaluationReleaseEnvironment,
)

_CHANGE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")


class EvaluationApprovalValidationError(ValueError):
    """审批命令违反职责分离或发布引用边界。"""


class EvaluationApprovalConflictError(RuntimeError):
    """同一不可变决策已经存在终态审批。"""


class EvaluationApprovalNotFoundError(LookupError):
    """当前作用域内找不到决策或审批证明。"""


class EvaluationApprovalSigningError(RuntimeError):
    """签名密钥未配置或无法安全使用。"""


class EvaluationApprovalRepository(Protocol):
    """决策只读、审批只追加的持久化边界。"""

    async def get_decision(
        self, *, decision_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionRecord | None: ...

    async def save_approval(
        self, record: EvaluationDecisionApprovalRecord
    ) -> EvaluationDecisionApprovalRecord: ...

    async def get_approval(
        self, *, decision_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionApprovalRecord | None: ...


class EvaluationApprovalSigner(Protocol):
    """由基础设施实现的 Ed25519 私钥读取和公开材料验证端口。"""

    async def sign(
        self, payload: bytes, *, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionSignature: ...

    def verify(self, payload: bytes, signature: EvaluationDecisionSignature) -> bool: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class EvaluationApprovalService:
    """冻结审批事实并签名；不调用配置、发布或其他副作用服务。"""

    def __init__(
        self,
        repository: EvaluationApprovalRepository,
        signer: EvaluationApprovalSigner,
        *,
        agent_id: UUID,
    ) -> None:
        self._repository = repository
        self._signer = signer
        self._agent_id = agent_id

    async def create_approval(
        self,
        *,
        decision_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        outcome: EvaluationApprovalOutcome,
        reason: EvaluationApprovalReason,
        release_environment: EvaluationReleaseEnvironment | None,
        change_reference: str | None,
    ) -> EvaluationDecisionApprovalRecord:
        decision = await self._get_decision(decision_id=decision_id, tenant_id=tenant_id)
        if decision.created_by == actor_id:
            raise EvaluationApprovalValidationError("决策创建人不能审批自己的评测决策")
        existing = await self._repository.get_approval(
            decision_id=decision_id,
            tenant_id=tenant_id,
            agent_id=self._agent_id,
        )
        if existing is not None:
            raise EvaluationApprovalConflictError("评测决策已经存在终态审批")
        if (
            outcome is EvaluationApprovalOutcome.APPROVED
            and decision.outcome is EvaluationDecisionOutcome.WAIT_FOR_EVIDENCE
        ):
            raise EvaluationApprovalValidationError("等待更多证据的决策不能批准")
        if outcome is EvaluationApprovalOutcome.APPROVED:
            if reason is not EvaluationApprovalReason.EVIDENCE_CONFIRMED:
                raise EvaluationApprovalValidationError("批准时必须使用证据已确认原因")
        elif reason is EvaluationApprovalReason.EVIDENCE_CONFIRMED:
            raise EvaluationApprovalValidationError("驳回时不能使用证据已确认原因")

        normalized_reference = self._validate_release_reference(
            outcome=outcome,
            release_environment=release_environment,
            change_reference=change_reference,
        )
        approval_id = uuid4()
        approved_at = datetime.now(UTC)
        payload = {
            "schema_version": 1,
            "id": str(approval_id),
            "decision_id": str(decision.id),
            "decision_sha256": decision.sha256,
            "tenant_id": str(tenant_id),
            "agent_id": str(self._agent_id),
            "approved_by": str(actor_id),
            "approved_at": approved_at.isoformat(),
            "outcome": outcome.value,
            "reason": reason.value,
            "release_reference": (
                {
                    "environment": release_environment.value,
                    "change_id": normalized_reference,
                }
                if release_environment is not None and normalized_reference is not None
                else None
            ),
            "automatic_actions_allowed": False,
        }
        payload_bytes = _canonical_json(payload)
        signature = await self._signer.sign(
            payload_bytes,
            tenant_id=tenant_id,
            agent_id=self._agent_id,
        )
        if signature.algorithm != "Ed25519":
            raise EvaluationApprovalSigningError("审批证明只允许使用 Ed25519 签名")
        proof = {
            "schema_version": 1,
            "payload": payload,
            "signature": {
                "algorithm": signature.algorithm,
                "key_id": signature.key_id,
                "public_key": base64.b64encode(signature.public_key).decode("ascii"),
                "value": base64.b64encode(signature.value).decode("ascii"),
            },
        }
        content = _canonical_json(proof)
        record = EvaluationDecisionApprovalRecord(
            id=approval_id,
            decision_id=decision.id,
            tenant_id=tenant_id,
            agent_id=self._agent_id,
            approved_by=actor_id,
            approved_at=approved_at,
            outcome=outcome,
            reason=reason,
            release_environment=release_environment,
            change_reference=normalized_reference,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )
        return await self._repository.save_approval(record)

    async def get_approval(
        self, *, decision_id: UUID, tenant_id: UUID
    ) -> EvaluationDecisionApprovalRecord:
        record = await self._repository.get_approval(
            decision_id=decision_id,
            tenant_id=tenant_id,
            agent_id=self._agent_id,
        )
        if record is None:
            raise EvaluationApprovalNotFoundError(f"评测决策尚无审批：{decision_id}")
        return record

    async def verify_approval(
        self, *, decision_id: UUID, tenant_id: UUID
    ) -> EvaluationApprovalVerification:
        decision = await self._get_decision(decision_id=decision_id, tenant_id=tenant_id)
        record = await self.get_approval(decision_id=decision_id, tenant_id=tenant_id)
        content_hash_valid = hashlib.sha256(record.content).hexdigest() == record.sha256
        canonical_content_valid = False
        decision_hash_matches = False
        signature_valid = False
        try:
            proof_value = cast(object, json.loads(record.content))
            if not isinstance(proof_value, dict):
                raise ValueError("证明根节点必须是对象")
            proof = cast(dict[str, object], proof_value)
            canonical_content_valid = _canonical_json(proof) == record.content
            payload_value = proof["payload"]
            signature_value = proof["signature"]
            if not isinstance(payload_value, dict) or not isinstance(signature_value, dict):
                raise TypeError("证明载荷和签名必须是对象")
            payload = cast(dict[str, object], payload_value)
            signature_data = cast(dict[str, object], signature_value)
            expected_release_reference = (
                {
                    "environment": record.release_environment.value,
                    "change_id": record.change_reference,
                }
                if record.release_environment is not None and record.change_reference is not None
                else None
            )
            decision_hash_matches = (
                proof.get("schema_version") == 1
                and payload.get("schema_version") == 1
                and payload.get("id") == str(record.id)
                and payload.get("decision_id") == str(decision.id)
                and payload.get("decision_sha256") == decision.sha256
                and payload.get("tenant_id") == str(tenant_id)
                and payload.get("agent_id") == str(self._agent_id)
                and payload.get("approved_by") == str(record.approved_by)
                and payload.get("approved_at") == record.approved_at.isoformat()
                and payload.get("outcome") == record.outcome.value
                and payload.get("reason") == record.reason.value
                and payload.get("release_reference") == expected_release_reference
                and payload.get("automatic_actions_allowed") is False
            )
            signature = EvaluationDecisionSignature(
                algorithm=str(signature_data["algorithm"]),
                key_id=str(signature_data["key_id"]),
                public_key=base64.b64decode(str(signature_data["public_key"]), validate=True),
                value=base64.b64decode(str(signature_data["value"]), validate=True),
            )
            signature_valid = self._signer.verify(_canonical_json(payload), signature)
        except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError):
            pass
        valid = all(
            (
                content_hash_valid,
                canonical_content_valid,
                decision_hash_matches,
                signature_valid,
            )
        )
        return EvaluationApprovalVerification(
            valid=valid,
            content_hash_valid=content_hash_valid,
            canonical_content_valid=canonical_content_valid,
            decision_hash_matches=decision_hash_matches,
            signature_valid=signature_valid,
            verified_at=datetime.now(UTC),
        )

    async def _get_decision(
        self, *, decision_id: UUID, tenant_id: UUID
    ) -> EvaluationDecisionRecord:
        decision = await self._repository.get_decision(
            decision_id=decision_id,
            tenant_id=tenant_id,
            agent_id=self._agent_id,
        )
        if decision is None:
            raise EvaluationApprovalNotFoundError(f"评测决策不存在：{decision_id}")
        return decision

    @staticmethod
    def _validate_release_reference(
        *,
        outcome: EvaluationApprovalOutcome,
        release_environment: EvaluationReleaseEnvironment | None,
        change_reference: str | None,
    ) -> str | None:
        normalized = change_reference.strip() if change_reference else None
        if (release_environment is None) != (normalized is None):
            raise EvaluationApprovalValidationError("发布环境和变更编号必须同时填写")
        if outcome is EvaluationApprovalOutcome.REJECTED and release_environment is not None:
            raise EvaluationApprovalValidationError("驳回审批不能关联发布变更")
        if normalized is not None and (
            not _CHANGE_REFERENCE_PATTERN.fullmatch(normalized) or "://" in normalized
        ):
            raise EvaluationApprovalValidationError("发布变更编号格式无效")
        return normalized
