"""使用 Agent 作用域密钥签署并验证评测审批证明。"""

import base64
import binascii
import hashlib
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from cnb_application import EvaluationApprovalSigningError, SecretOperationError, SecretStore
from cnb_domain import EvaluationDecisionSignature

EVALUATION_APPROVAL_SIGNING_KEY = "evaluation.approval.ed25519_private_key"


class Ed25519EvaluationApprovalSigner:
    """从 SecretStore 解析私钥，但只把公钥材料写入不可变证明。"""

    def __init__(self, secret_store: SecretStore) -> None:
        self._secret_store = secret_store

    async def sign(
        self, payload: bytes, *, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationDecisionSignature:
        try:
            encoded_key = await self._secret_store.resolve_secret(
                EVALUATION_APPROVAL_SIGNING_KEY,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
        except SecretOperationError as error:
            raise EvaluationApprovalSigningError("评测审批签名密钥无法安全读取") from error
        if encoded_key is None:
            raise EvaluationApprovalSigningError("当前 Agent 尚未配置评测审批签名密钥")
        try:
            private_bytes = base64.b64decode(encoded_key, validate=True)
            if len(private_bytes) != 32:
                raise ValueError("invalid Ed25519 seed length")
            private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
        except (binascii.Error, ValueError) as error:
            raise EvaluationApprovalSigningError(
                "评测审批签名密钥必须是 32 字节 Ed25519 seed 的 Base64 编码"
            ) from error

        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return EvaluationDecisionSignature(
            algorithm="Ed25519",
            key_id=hashlib.sha256(public_key).hexdigest(),
            public_key=public_key,
            value=private_key.sign(payload),
        )

    def verify(self, payload: bytes, signature: EvaluationDecisionSignature) -> bool:
        if signature.algorithm != "Ed25519" or len(signature.public_key) != 32:
            return False
        if hashlib.sha256(signature.public_key).hexdigest() != signature.key_id:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(signature.public_key).verify(
                signature.value,
                payload,
            )
        except (InvalidSignature, ValueError):
            return False
        return True
