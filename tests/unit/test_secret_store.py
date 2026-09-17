"""密钥信封加密、作用域选择和管理服务测试。"""

import base64
from uuid import uuid4

import pytest

from cnb_application import (
    ConfigurationValidationError,
    SecretManagementService,
    SecretNotFoundError,
    build_default_registry,
)
from cnb_domain import ConfigScope, SecretIntegrityStatus
from cnb_infrastructure import AesGcmEnvelopeCipher, MemorySecretStore


def test_cipher_requires_a_base64_encoded_32_byte_master_key() -> None:
    encoded = base64.b64encode(bytes(range(32))).decode()
    cipher = AesGcmEnvelopeCipher.from_encoded_key(encoded)
    secret_id = uuid4()

    encrypted, nonce = cipher.encrypt(
        "仅供加密测试的虚假凭证",
        secret_id=secret_id,
        key="model.openai.api_key",
        scope_type=ConfigScope.SYSTEM,
        scope_id=None,
        key_version=1,
    )

    assert "虚假凭证".encode() not in encrypted
    assert (
        cipher.decrypt(
            encrypted,
            nonce,
            secret_id=secret_id,
            key="model.openai.api_key",
            scope_type=ConfigScope.SYSTEM,
            scope_id=None,
            key_version=1,
        )
        == "仅供加密测试的虚假凭证"
    )

    with pytest.raises(ValueError, match="Base64"):
        AesGcmEnvelopeCipher.from_encoded_key("不是-base64")


async def test_secret_management_never_returns_plaintext_and_supports_lifecycle() -> None:
    store = MemorySecretStore()
    service = SecretManagementService(build_default_registry(), store)
    metadata = await service.set_secret(
        key="model.openai.api_key",
        scope_type=ConfigScope.SYSTEM,
        scope_id=None,
        plaintext="仅供生命周期测试-1234",
    )

    assert metadata.masked_hint == "••••1234"
    assert "生命周期测试" not in repr(metadata)
    tested = await service.test_secret(metadata.id)
    assert tested.integrity_status is SecretIntegrityStatus.VALID

    rotated = await service.rotate_secret(metadata.id, plaintext="仅供轮换测试-5678")
    assert rotated.id == metadata.id
    assert rotated.masked_hint == "••••5678"
    assert rotated.integrity_status is SecretIntegrityStatus.UNTESTED

    await service.clear_secret(metadata.id)
    with pytest.raises(SecretNotFoundError):
        await service.test_secret(metadata.id)


async def test_secret_resolution_follows_scope_precedence() -> None:
    store = MemorySecretStore()
    service = SecretManagementService(build_default_registry(), store)
    tenant_id = uuid4()
    agent_id = uuid4()
    await service.set_secret(
        key="model.openai.api_key",
        scope_type=ConfigScope.SYSTEM,
        scope_id=None,
        plaintext="system-key",
    )
    await service.set_secret(
        key="model.openai.api_key",
        scope_type=ConfigScope.TENANT,
        scope_id=tenant_id,
        plaintext="tenant-key",
    )
    await service.set_secret(
        key="model.openai.api_key",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext="agent-key",
    )

    assert (
        await store.resolve_secret("model.openai.api_key", tenant_id=tenant_id, agent_id=agent_id)
        == "agent-key"
    )
    assert (
        await store.resolve_secret("model.openai.api_key", tenant_id=tenant_id, agent_id=uuid4())
        == "tenant-key"
    )
    assert await store.resolve_secret("model.openai.api_key", tenant_id=uuid4()) == "system-key"


async def test_secret_service_rejects_non_secret_and_invalid_scope() -> None:
    service = SecretManagementService(build_default_registry(), MemorySecretStore())

    with pytest.raises(ConfigurationValidationError, match="不是密钥"):
        await service.set_secret(
            key="model.openai.model",
            scope_type=ConfigScope.SYSTEM,
            scope_id=None,
            plaintext="不应保存",
        )
    with pytest.raises(ConfigurationValidationError, match="必须设置作用域 ID"):
        await service.set_secret(
            key="model.openai.api_key",
            scope_type=ConfigScope.AGENT,
            scope_id=None,
            plaintext="不应保存",
        )


async def test_evaluation_approval_signing_key_requires_base64_ed25519_seed() -> None:
    service = SecretManagementService(build_default_registry(), MemorySecretStore())
    agent_id = uuid4()

    with pytest.raises(ConfigurationValidationError, match="Base64"):
        await service.set_secret(
            key="evaluation.approval.ed25519_private_key",
            scope_type=ConfigScope.AGENT,
            scope_id=agent_id,
            plaintext="not-base64",
        )
    with pytest.raises(ConfigurationValidationError, match="32 字节"):
        await service.set_secret(
            key="evaluation.approval.ed25519_private_key",
            scope_type=ConfigScope.AGENT,
            scope_id=agent_id,
            plaintext=base64.b64encode(b"too-short").decode("ascii"),
        )

    metadata = await service.set_secret(
        key="evaluation.approval.ed25519_private_key",
        scope_type=ConfigScope.AGENT,
        scope_id=agent_id,
        plaintext=base64.b64encode(bytes(range(32))).decode("ascii"),
    )

    assert metadata.scope_type is ConfigScope.AGENT
    assert metadata.scope_id == agent_id
    assert "AAECAw" not in repr(metadata)
