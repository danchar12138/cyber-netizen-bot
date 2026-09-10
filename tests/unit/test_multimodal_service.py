"""附件模型输入组装、正文提取和安全限制测试。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from uuid import NAMESPACE_DNS, UUID, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfWriter

from cnb_application import (
    MultimodalInputError,
    MultimodalInputLimits,
    MultimodalInputService,
)
from cnb_cognition import ModelDocumentInput, ModelTextInput
from cnb_domain import (
    Attachment,
    AttachmentStatus,
    ContentBlockKind,
    Message,
    MessagePart,
    MessageSenderType,
    MessageStatus,
)
from cnb_infrastructure import MemoryAttachmentRepository, MemoryObjectStorage

TENANT_ID = uuid5(NAMESPACE_DNS, "multimodal.tenant")
USER_ID = uuid5(NAMESPACE_DNS, "multimodal.user")
CONVERSATION_ID = uuid5(NAMESPACE_DNS, "multimodal.conversation")
MESSAGE_ID = uuid5(NAMESPACE_DNS, "multimodal.message")


def _limits(**overrides: int) -> MultimodalInputLimits:
    values = {
        "max_image_bytes": 1_000_000,
        "max_document_bytes": 1_000_000,
        "max_total_bytes": 2_000_000,
        "max_document_characters": 30,
        "max_pdf_pages": 1,
        **overrides,
    }
    return MultimodalInputLimits(**values)


def _attachment(
    *, attachment_id: UUID, content: bytes, content_type: str, file_name: str
) -> Attachment:
    now = datetime.now(UTC)
    return Attachment(
        id=attachment_id,
        tenant_id=TENANT_ID,
        owner_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        client_message_id=uuid5(NAMESPACE_DNS, f"draft.{attachment_id}"),
        message_id=MESSAGE_ID,
        original_name=file_name,
        content_type=content_type,
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
        object_key=f"tenants/{TENANT_ID}/attachments/{attachment_id}/content",
        status=AttachmentStatus.ATTACHED,
        validation_error=None,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        uploaded_at=now,
        attached_at=now,
        deleted_at=None,
    )


def _message(attachment: Attachment) -> Message:
    now = datetime.now(UTC)
    return Message(
        id=MESSAGE_ID,
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        sender_type=MessageSenderType.USER,
        sender_id=USER_ID,
        content="请阅读附件",
        status=MessageStatus.RECEIVED,
        client_message_id=attachment.client_message_id,
        created_at=now,
        updated_at=now,
        parts=(
            MessagePart(
                id=uuid5(NAMESPACE_DNS, "multimodal.text-part"),
                tenant_id=TENANT_ID,
                message_id=MESSAGE_ID,
                position=0,
                kind=ContentBlockKind.MARKDOWN,
                text="请阅读附件",
                attachment_id=None,
                content_type=None,
                file_name=None,
                size_bytes=None,
                sha256=None,
                alt_text=None,
                created_at=now,
                updated_at=now,
            ),
            MessagePart(
                id=uuid5(NAMESPACE_DNS, f"multimodal.part.{attachment.id}"),
                tenant_id=TENANT_ID,
                message_id=MESSAGE_ID,
                position=1,
                kind=(
                    ContentBlockKind.IMAGE
                    if attachment.content_type.startswith("image/")
                    else ContentBlockKind.FILE
                ),
                text=None,
                attachment_id=attachment.id,
                content_type=attachment.content_type,
                file_name=attachment.original_name,
                size_bytes=attachment.size_bytes,
                sha256=attachment.sha256,
                alt_text=attachment.original_name,
                created_at=now,
                updated_at=now,
            ),
        ),
    )


async def _load(
    attachment: Attachment,
    content: bytes | None,
    *,
    limits: MultimodalInputLimits | None = None,
):
    repository = MemoryAttachmentRepository()
    storage = MemoryObjectStorage()
    await repository.create_attachment(attachment)
    if content is not None:
        storage.put_for_test(
            object_key=attachment.object_key,
            content=content,
            content_type=attachment.content_type,
        )
    service = MultimodalInputService(
        repository=repository,
        object_storage=storage,
        user_id=USER_ID,
        tenant_id=TENANT_ID,
    )
    return await service.load_message_attachments(
        _message(attachment),
        limits=limits or _limits(),
        remaining_bytes=(limits or _limits()).max_total_bytes,
    )


async def test_docx_body_is_extracted_and_truncated_for_model_fallback() -> None:
    output = BytesIO()
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>第一段正文</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第二段内容比较长，需要进行字符裁剪，后续内容不会全部进入模型上下文，"
        "这里继续补充足够多的测试文字。</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", document)
    content = output.getvalue()
    attachment = _attachment(
        attachment_id=uuid5(NAMESPACE_DNS, "multimodal.docx"),
        content=content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_name="访谈记录.docx",
    )

    loaded = await _load(attachment, content)

    assert loaded.bytes_loaded == len(content)
    assert len(loaded.parts) == 1
    model_document = loaded.parts[0]
    assert isinstance(model_document, ModelDocumentInput)
    assert model_document.file_name == "访谈记录.docx"
    assert model_document.extracted_text.startswith("第一段正文\n第二段")
    assert model_document.extracted_text.endswith("[…文档正文因模型输入字符上限截断…]")


async def test_pdf_page_count_and_extraction_limit_are_preserved() -> None:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    content = output.getvalue()
    attachment = _attachment(
        attachment_id=uuid5(NAMESPACE_DNS, "multimodal.pdf"),
        content=content,
        content_type="application/pdf",
        file_name="两页资料.pdf",
    )

    loaded = await _load(attachment, content)

    model_document = loaded.parts[0]
    assert isinstance(model_document, ModelDocumentInput)
    assert model_document.page_count == 2
    assert "其余 PDF 页面" in model_document.extracted_text


async def test_oversized_image_transparently_degrades_without_reading_object() -> None:
    content = b"image bytes over the configured limit"
    attachment = _attachment(
        attachment_id=uuid5(NAMESPACE_DNS, "multimodal.large-image"),
        content=content,
        content_type="image/png",
        file_name="大图.png",
    )

    loaded = await _load(
        attachment,
        None,
        limits=_limits(max_image_bytes=4),
    )

    fallback = loaded.parts[0]
    assert isinstance(fallback, ModelTextInput)
    assert "超过单个模型输入上限" in fallback.text
    assert loaded.bytes_loaded == 0


async def test_changed_private_object_is_rejected_before_model_request() -> None:
    declared = b"trusted content"
    attachment = _attachment(
        attachment_id=uuid5(NAMESPACE_DNS, "multimodal.changed"),
        content=declared,
        content_type="text/plain",
        file_name="证据.txt",
    )

    with pytest.raises(MultimodalInputError, match="完整性复核失败"):
        await _load(attachment, b"changed content")


async def test_attachment_bound_to_another_message_is_rejected() -> None:
    content = b"private content"
    attachment = _attachment(
        attachment_id=uuid5(NAMESPACE_DNS, "multimodal.wrong-message"),
        content=content,
        content_type="text/plain",
        file_name="私有资料.txt",
    )
    attachment = replace(
        attachment,
        message_id=uuid5(NAMESPACE_DNS, "multimodal.other-message"),
    )

    with pytest.raises(MultimodalInputError, match="绑定关系复核失败"):
        await _load(attachment, content)
