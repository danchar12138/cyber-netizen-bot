"""厂商无关的 Channel Adapter 契约、错误和能力协商。"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from cnb_domain import (
    ChannelCapabilities,
    ChannelEventStatus,
    ChannelHealthStatus,
    ChannelPlatform,
    ContentBlockKind,
    ExternalConversationKind,
    JsonValue,
    MultimodalContentBlock,
)

SAFE_IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
SAFE_DOCUMENT_CONTENT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
SAFE_ATTACHMENT_CONTENT_TYPES = SAFE_IMAGE_CONTENT_TYPES | SAFE_DOCUMENT_CONTENT_TYPES
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ChannelAdapterError(RuntimeError):
    """可安全向管理端报告的 Adapter 失败，不包含远端响应或凭证明文。"""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class ChannelNotConfiguredError(ChannelAdapterError):
    """Adapter 尚未实现或实例缺少必须凭证。"""

    def __init__(self, message: str = "渠道尚未配置可用连接") -> None:
        super().__init__("not_configured", message)


class ChannelCapabilityError(ChannelAdapterError):
    """请求无法在不破坏语义的前提下映射到目标平台。"""

    def __init__(self, message: str) -> None:
        super().__init__("unsupported_capability", message)


class ChannelRateLimitError(ChannelAdapterError):
    """确定性本地限流拒绝。"""

    def __init__(self) -> None:
        super().__init__(
            "rate_limited",
            "渠道已达到每分钟发送上限",
            retryable=True,
            retry_after_seconds=60,
        )


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    """不包含远端响应正文的连接检查结果。"""

    status: ChannelHealthStatus
    detail: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelDeliveryCommand:
    """经过应用层授权后交给 Adapter 的标准化发送命令。"""

    channel_id: UUID
    recipient_id: str
    blocks: tuple[MultimodalContentBlock, ...]
    idempotency_key: str
    thread_id: str | None = None
    proactive: bool = False
    request_streaming: bool = False
    edit_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityNegotiation:
    """目标平台可执行的内容和全部透明降级原因。"""

    blocks: tuple[MultimodalContentBlock, ...]
    degradations: tuple[str, ...]
    buffered: bool
    thread_id: str | None
    edit_message_id: str | None


@dataclass(frozen=True, slots=True)
class AdapterDeliveryResult:
    """发送完成后的稳定结果；远端原始响应不得穿透该边界。"""

    status: ChannelEventStatus
    external_message_id: str
    degradations: tuple[str, ...]
    delivered_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelInboundEvent:
    """Adapter 归一化后的入站事件。"""

    external_event_id: str
    event_type: str
    sender_external_id: str
    conversation_external_id: str
    blocks: tuple[MultimodalContentBlock, ...]
    occurred_at: datetime
    thread_external_id: str | None = None
    message_external_id: str | None = None
    conversation_kind: ExternalConversationKind = ExternalConversationKind.DIRECT


class ChannelAdapter(Protocol):
    """所有 Web 与 IM 平台必须满足的最小端口。"""

    @property
    def platform(self) -> ChannelPlatform: ...

    @property
    def display_name(self) -> str: ...

    @property
    def capabilities(self) -> ChannelCapabilities: ...

    @property
    def implementation_status(self) -> Literal["ready", "placeholder"]: ...

    def validate_credential(self, credential: str) -> None: ...

    async def test_connection(self, *, credential: str | None) -> AdapterHealth: ...

    async def deliver(
        self,
        *,
        command: ChannelDeliveryCommand,
        negotiation: CapabilityNegotiation,
        credential: str | None,
    ) -> AdapterDeliveryResult: ...

    async def normalize_inbound(
        self,
        payload: Mapping[str, JsonValue],
    ) -> ChannelInboundEvent: ...

    async def aclose(self) -> None: ...


def negotiate_capabilities(
    command: ChannelDeliveryCommand,
    capabilities: ChannelCapabilities,
) -> CapabilityNegotiation:
    """验证安全内容并将可降级能力映射到目标平台。"""
    if command.proactive and not capabilities.proactive_messages:
        raise ChannelCapabilityError("目标渠道不支持主动消息")
    degradations: list[str] = []
    buffered = command.request_streaming and not capabilities.streaming
    if buffered:
        degradations.append("streaming_to_buffered")
    thread_id = command.thread_id
    if thread_id is not None and not capabilities.threads:
        thread_id = None
        degradations.append("thread_to_root_message")
    edit_message_id = command.edit_message_id
    if edit_message_id is not None and not capabilities.message_edit:
        edit_message_id = None
        degradations.append("edit_to_new_message")

    normalized: list[MultimodalContentBlock] = []
    for block in command.blocks:
        _validate_block(block, capabilities)
        if block.kind is ContentBlockKind.MARKDOWN and not capabilities.markdown:
            normalized.append(
                MultimodalContentBlock(
                    kind=ContentBlockKind.TEXT, text=_markdown_to_text(block.text)
                )
            )
            degradations.append("markdown_to_text")
        elif block.kind is ContentBlockKind.IMAGE and not capabilities.images:
            if capabilities.files:
                normalized.append(replace(block, kind=ContentBlockKind.FILE))
                degradations.append("image_to_file")
            elif capabilities.text:
                normalized.append(_attachment_fallback(block, label="图片"))
                degradations.append("image_to_text_reference")
            else:
                raise ChannelCapabilityError("目标渠道无法承载图片内容")
        elif block.kind is ContentBlockKind.FILE and not capabilities.files:
            if capabilities.text:
                normalized.append(_attachment_fallback(block, label="附件"))
                degradations.append("file_to_text_reference")
            else:
                raise ChannelCapabilityError("目标渠道无法承载文件内容")
        elif block.kind is ContentBlockKind.TEXT and not capabilities.text:
            raise ChannelCapabilityError("目标渠道不支持文本内容")
        else:
            normalized.append(block)

    expanded: list[MultimodalContentBlock] = []
    for block in normalized:
        if block.kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}:
            text = block.text or ""
            chunks = tuple(
                text[index : index + capabilities.max_text_chars]
                for index in range(0, len(text), capabilities.max_text_chars)
            )
            if len(chunks) > 1:
                degradations.append("long_text_split")
            expanded.extend(replace(block, text=chunk) for chunk in chunks)
        else:
            expanded.append(block)
    if not expanded:
        raise ChannelCapabilityError("消息至少需要一个可发送内容块")
    if len(expanded) > capabilities.max_blocks:
        raise ChannelCapabilityError(
            f"能力协商后内容块数量超过目标渠道上限 {capabilities.max_blocks}"
        )
    return CapabilityNegotiation(
        blocks=tuple(expanded),
        degradations=tuple(dict.fromkeys(degradations)),
        buffered=buffered,
        thread_id=thread_id,
        edit_message_id=edit_message_id,
    )


def summarize_blocks(blocks: tuple[MultimodalContentBlock, ...]) -> dict[str, JsonValue]:
    """生成可审计且不包含文本正文、文件名和对象地址的摘要。"""
    return {
        "block_count": len(blocks),
        "block_kinds": [block.kind.value for block in blocks],
        "attachment_ids": [
            str(block.attachment_id) for block in blocks if block.attachment_id is not None
        ],
        "total_attachment_bytes": sum(block.size_bytes or 0 for block in blocks),
    }


def _validate_block(
    block: MultimodalContentBlock,
    capabilities: ChannelCapabilities,
) -> None:
    if block.kind in {ContentBlockKind.TEXT, ContentBlockKind.MARKDOWN}:
        if block.text is None or not block.text.strip():
            raise ChannelCapabilityError("文本内容块不能为空")
        if len(block.text) > capabilities.max_text_chars * capabilities.max_blocks:
            raise ChannelCapabilityError("文本内容超过目标渠道可安全拆分的上限")
        return
    if (
        block.attachment_id is None
        or block.content_type is None
        or block.file_name is None
        or block.size_bytes is None
        or block.sha256 is None
    ):
        raise ChannelCapabilityError("图片和文件必须引用已校验附件元数据")
    content_type = block.content_type.strip().lower()
    if content_type not in SAFE_ATTACHMENT_CONTENT_TYPES:
        raise ChannelCapabilityError(f"不支持的安全媒体类型：{content_type}")
    if block.kind is ContentBlockKind.IMAGE and content_type not in SAFE_IMAGE_CONTENT_TYPES:
        raise ChannelCapabilityError("图片内容块必须使用安全图片媒体类型")
    if (
        capabilities.accepted_content_types
        and content_type not in capabilities.accepted_content_types
    ):
        raise ChannelCapabilityError(f"目标渠道不接受媒体类型：{content_type}")
    if block.size_bytes <= 0 or block.size_bytes > capabilities.max_attachment_bytes:
        raise ChannelCapabilityError("附件大小超过目标渠道上限")
    if not _SHA256.fullmatch(block.sha256.lower()):
        raise ChannelCapabilityError("附件必须携带有效 SHA-256")
    if (
        not block.file_name.strip()
        or "/" in block.file_name
        or "\\" in block.file_name
        or any(ord(character) < 32 for character in block.file_name)
    ):
        raise ChannelCapabilityError("附件文件名无效")


def _attachment_fallback(
    block: MultimodalContentBlock,
    *,
    label: str,
) -> MultimodalContentBlock:
    description = block.alt_text.strip() if block.alt_text and block.alt_text.strip() else "已附加"
    return MultimodalContentBlock(
        kind=ContentBlockKind.TEXT,
        text=f"[{label}：{description}]",
    )


def _markdown_to_text(value: str | None) -> str:
    text = value or ""
    text = re.sub(r"```[^\n]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", text)
    return text.strip()
