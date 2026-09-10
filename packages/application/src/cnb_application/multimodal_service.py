"""把已持久化附件安全转换为厂商无关的模型输入块。"""

import asyncio
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from dataclasses import dataclass
from hmac import compare_digest
from io import BytesIO
from typing import Protocol
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from cnb_application.object_storage import (
    ObjectInspectionError,
    ObjectNotFoundError,
    ObjectStorage,
)
from cnb_cognition import (
    ModelDocumentInput,
    ModelImageInput,
    ModelInputPart,
    ModelTextInput,
    UntrustedContentSource,
    serialize_untrusted_content,
)
from cnb_domain import Attachment, AttachmentStatus, ContentBlockKind, Message, MessagePart


class MultimodalInputError(RuntimeError):
    """附件在进入模型前无法通过权限、完整性或解析复核。"""


class ModelAttachmentRepository(Protocol):
    """组装模型输入所需的最小附件元数据查询端口。"""

    async def get_attachment_for_user(
        self, attachment_id: UUID, user_id: UUID
    ) -> Attachment | None: ...


@dataclass(frozen=True, slots=True)
class MultimodalInputLimits:
    """一次模型请求读取附件时使用的已校验运行限制。"""

    max_image_bytes: int
    max_document_bytes: int
    max_total_bytes: int
    max_document_characters: int
    max_pdf_pages: int


@dataclass(frozen=True, slots=True)
class LoadedModelInput:
    """一条消息新增的附件块与实际读取字节数。"""

    parts: tuple[ModelInputPart, ...]
    bytes_loaded: int


class MultimodalInputService:
    """读取用户有权访问的附件，并在进入 Provider 前再次验证内容。"""

    def __init__(
        self,
        *,
        repository: ModelAttachmentRepository,
        object_storage: ObjectStorage,
        user_id: UUID,
        tenant_id: UUID,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._user_id = user_id
        self._tenant_id = tenant_id

    async def load_messages(
        self,
        messages: Sequence[Message],
        *,
        limits: MultimodalInputLimits,
    ) -> dict[UUID, LoadedModelInput]:
        """从新到旧选择附件，并并发读取已选对象，优先保留本轮与最近消息。"""
        selected: list[tuple[Message, int]] = []
        remaining_bytes = limits.max_total_bytes
        for message in reversed(messages):
            reserved_bytes = 0
            for part in message.parts:
                if (
                    part.kind not in {ContentBlockKind.IMAGE, ContentBlockKind.FILE}
                    or part.size_bytes is None
                ):
                    continue
                item_limit = (
                    limits.max_image_bytes
                    if part.kind is ContentBlockKind.IMAGE
                    else limits.max_document_bytes
                )
                if part.size_bytes <= item_limit and part.size_bytes <= remaining_bytes:
                    reserved_bytes += part.size_bytes
                    remaining_bytes -= part.size_bytes
            selected.append((message, reserved_bytes))
        loaded = await asyncio.gather(
            *(
                self.load_message_attachments(
                    message,
                    limits=limits,
                    remaining_bytes=reserved_bytes,
                )
                for message, reserved_bytes in selected
            )
        )
        return {message.id: item for (message, _), item in zip(selected, loaded, strict=True)}

    async def load_message_attachments(
        self,
        message: Message,
        *,
        limits: MultimodalInputLimits,
        remaining_bytes: int,
    ) -> LoadedModelInput:
        """按消息块顺序加载附件；超出运行限制时给出透明文本降级。"""
        parts: list[ModelInputPart] = []
        bytes_loaded = 0
        for part in sorted(message.parts, key=lambda item: item.position):
            if part.kind not in {ContentBlockKind.IMAGE, ContentBlockKind.FILE}:
                continue
            if part.attachment_id is None:
                raise MultimodalInputError("消息附件引用不完整")
            attachment = await self._repository.get_attachment_for_user(
                part.attachment_id, self._user_id
            )
            self._verify_attachment_reference(message, attachment, part)
            assert attachment is not None
            item_limit = (
                limits.max_image_bytes
                if part.kind is ContentBlockKind.IMAGE
                else limits.max_document_bytes
            )
            if attachment.size_bytes > item_limit:
                parts.append(self._limit_fallback(attachment, "超过单个模型输入上限"))
                continue
            if attachment.size_bytes > remaining_bytes - bytes_loaded:
                parts.append(self._limit_fallback(attachment, "超过本次模型输入总量上限"))
                continue
            try:
                content = await self._object_storage.read_object(
                    attachment.object_key,
                    max_bytes=item_limit,
                )
            except (ObjectInspectionError, ObjectNotFoundError) as error:
                raise MultimodalInputError("附件内容暂时无法安全读取") from error
            normalized_type = content.content_type.casefold().split(";", maxsplit=1)[0]
            if (
                len(content.data) != attachment.size_bytes
                or normalized_type != attachment.content_type
                or not compare_digest(content.sha256, attachment.sha256)
            ):
                raise MultimodalInputError("附件内容完整性复核失败")
            bytes_loaded += len(content.data)
            if part.kind is ContentBlockKind.IMAGE:
                parts.append(
                    ModelImageInput(
                        content_type=attachment.content_type,
                        data=content.data,
                        file_name=attachment.original_name,
                        alt_text=part.alt_text,
                    )
                )
                continue
            extracted_text, page_count = await asyncio.to_thread(
                _extract_document_text,
                content.data,
                attachment.content_type,
                limits.max_document_characters,
                limits.max_pdf_pages,
            )
            parts.append(
                ModelDocumentInput(
                    content_type=attachment.content_type,
                    data=content.data,
                    file_name=attachment.original_name,
                    extracted_text=extracted_text,
                    page_count=page_count,
                )
            )
        return LoadedModelInput(parts=tuple(parts), bytes_loaded=bytes_loaded)

    def _verify_attachment_reference(
        self,
        message: Message,
        attachment: Attachment | None,
        part: MessagePart,
    ) -> None:
        if attachment is None:
            raise MultimodalInputError("消息附件不存在或当前用户无权访问")
        if attachment.tenant_id != self._tenant_id or message.tenant_id != self._tenant_id:
            raise MultimodalInputError("消息附件不属于当前租户")
        if attachment.status is not AttachmentStatus.ATTACHED:
            raise MultimodalInputError("消息附件尚未完成绑定")
        if (
            attachment.conversation_id != message.conversation_id
            or attachment.message_id != message.id
        ):
            raise MultimodalInputError("消息附件绑定关系复核失败")
        if (part.kind is ContentBlockKind.IMAGE) != attachment.content_type.startswith("image/"):
            raise MultimodalInputError("消息附件内容块类型复核失败")
        if (
            part.content_type != attachment.content_type
            or part.file_name != attachment.original_name
            or part.size_bytes != attachment.size_bytes
            or part.sha256 is None
            or not compare_digest(part.sha256, attachment.sha256)
        ):
            raise MultimodalInputError("消息附件元数据复核失败")

    @staticmethod
    def _limit_fallback(attachment: Attachment, reason: str) -> ModelTextInput:
        description = f"附件“{attachment.original_name}”未载入：{reason}。"
        return ModelTextInput(
            text=serialize_untrusted_content(description, UntrustedContentSource.ATTACHMENT)
        )


def _extract_document_text(
    content: bytes,
    content_type: str,
    max_characters: int,
    max_pdf_pages: int,
) -> tuple[str, int | None]:
    """从已检查的常用文档中提取正文，并保持确定性裁剪。"""
    try:
        if content_type in {"text/plain", "text/markdown", "text/csv", "application/json"}:
            text = content.decode("utf-8")
            page_count = None
        elif content_type == "application/pdf":
            reader = PdfReader(BytesIO(content), strict=False)
            page_count = len(reader.pages)
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages[:max_pdf_pages])
            if page_count > max_pdf_pages:
                text += "\n\n[…其余 PDF 页面因模型输入页数限制未提取…]"
        elif content_type == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            page_count = None
            text = _extract_docx_text(content)
        else:
            raise MultimodalInputError("附件类型不支持模型文档输入")
    except (
        BadZipFile,
        ElementTree.ParseError,
        KeyError,
        OSError,
        PyPdfError,
        ValueError,
    ) as error:
        raise MultimodalInputError("附件正文无法安全提取") from error
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return _truncate_text(normalized, max_characters), page_count


def _extract_docx_text(content: bytes) -> str:
    with ZipFile(BytesIO(content)) as archive:
        document = archive.read("word/document.xml")
    root = ElementTree.fromstring(document)
    paragraphs: list[str] = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(
            node.text or ""
            for node in paragraph.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
            )
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _truncate_text(value: str, max_characters: int) -> str:
    if len(value) <= max_characters:
        return value
    marker = "\n[…文档正文因模型输入字符上限截断…]"
    return value[: max(0, max_characters - len(marker))].rstrip() + marker
