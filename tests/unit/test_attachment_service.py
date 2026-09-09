"""附件预签名、完整性校验、消息绑定与清理测试。"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_DNS, uuid5

import pytest

from cnb_application import (
    AttachmentService,
    AttachmentValidationError,
    ConfigurationService,
    build_default_registry,
)
from cnb_domain import AttachmentStatus, DevelopmentIdentity
from cnb_infrastructure import (
    MemoryAttachmentRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
    MemoryObjectStorage,
)


def _identity() -> DevelopmentIdentity:
    return DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "attachment.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "attachment.user"),
        agent_id=uuid5(NAMESPACE_DNS, "attachment.agent"),
        user_name="附件测试用户",
        agent_name="附件测试 Agent",
    )


def _services() -> tuple[
    AttachmentService,
    MemoryAttachmentRepository,
    MemoryObjectStorage,
    MemoryConversationRepository,
]:
    repository = MemoryAttachmentRepository()
    storage = MemoryObjectStorage()
    conversations = MemoryConversationRepository()
    service = AttachmentService(
        repository=repository,
        object_storage=storage,
        conversation_repository=conversations,
        configuration_service=ConfigurationService(
            build_default_registry(), MemoryConfigurationRepository()
        ),
        identity=_identity(),
    )
    return service, repository, storage, conversations


async def test_attachment_upload_is_verified_and_bound_to_message() -> None:
    service, _, storage, conversations = _services()
    identity = _identity()
    await conversations.ensure_development_identity(identity)
    conversation = await conversations.create_conversation(identity=identity, title="附件闭环")
    content = b"hello attachment"
    digest = sha256(content).hexdigest()
    client_message_id = uuid5(NAMESPACE_DNS, "attachment.message")

    reservation = await service.reserve(
        conversation_id=conversation.id,
        client_message_id=client_message_id,
        original_name="../说明.txt",
        content_type="text/plain",
        size_bytes=len(content),
        sha256=digest,
    )
    storage.put_for_test(
        object_key=reservation.attachment.object_key,
        content=content,
        content_type="text/plain",
    )
    ready = await service.complete(reservation.attachment.id)
    pending = await conversations.begin_agent_run(
        identity=identity,
        conversation_id=conversation.id,
        client_message_id=client_message_id,
        content="请看附件",
        configuration_version=0,
        persona_version=1,
        prompt_version=1,
        policy_version=0,
        model_route_version=0,
        model_profile="development/friendly-echo-v1",
    )
    attached = await service.attach_to_message(
        attachment_ids=(ready.id,), message=pending.trigger_message
    )
    preview_item, preview_url = await service.preview(ready.id)

    assert reservation.attachment.original_name == "说明.txt"
    assert reservation.upload.method == "PUT"
    assert reservation.upload.headers["x-amz-meta-sha256"] == digest
    assert ready.status is AttachmentStatus.READY
    assert attached[0].status is AttachmentStatus.ATTACHED
    assert attached[0].message_id == pending.trigger_message.id
    assert preview_item.id == ready.id
    assert preview_url.startswith("memory://download/")


async def test_attachment_rejects_mismatched_object_and_removes_it() -> None:
    service, _, storage, conversations = _services()
    identity = _identity()
    await conversations.ensure_development_identity(identity)
    conversation = await conversations.create_conversation(identity=identity, title="摘要校验")
    content = b"expected"
    reservation = await service.reserve(
        conversation_id=conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "attachment.mismatch"),
        original_name="evidence.json",
        content_type="application/json",
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
    )
    storage.put_for_test(
        object_key=reservation.attachment.object_key,
        content=b"tampered",
        content_type="application/json",
    )

    with pytest.raises(AttachmentValidationError, match="SHA-256"):
        await service.complete(reservation.attachment.id)
    with pytest.raises(LookupError):
        await storage.stat_object(reservation.attachment.object_key)


async def test_attachment_policy_rejects_unlisted_type_and_cleanup_is_idempotent() -> None:
    service, repository, _, conversations = _services()
    identity = _identity()
    await conversations.ensure_development_identity(identity)
    conversation = await conversations.create_conversation(identity=identity, title="清理测试")

    with pytest.raises(AttachmentValidationError, match="不支持附件类型"):
        await service.reserve(
            conversation_id=conversation.id,
            client_message_id=uuid5(NAMESPACE_DNS, "attachment.executable"),
            original_name="危险程序.exe",
            content_type="application/x-msdownload",
            size_bytes=10,
            sha256="0" * 64,
        )

    reservation = await service.reserve(
        conversation_id=conversation.id,
        client_message_id=uuid5(NAMESPACE_DNS, "attachment.cleanup"),
        original_name="临时.txt",
        content_type="text/plain",
        size_bytes=10,
        sha256="0" * 64,
    )
    repository.expire_for_test(
        reservation.attachment.id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert await service.cleanup_expired() == 1
    assert await service.cleanup_expired() == 0
