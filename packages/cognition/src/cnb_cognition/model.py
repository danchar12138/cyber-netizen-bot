"""与模型厂商无关的流式模型契约。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ModelRole(StrEnum):
    """标准化模型消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ModelTextInput:
    """进入模型消息的文本块；不可信来源应在应用层先完成安全封装。"""

    text: str


@dataclass(frozen=True, slots=True)
class ModelImageInput:
    """经过权限、摘要和大小复核的图片字节。"""

    content_type: str
    data: bytes
    file_name: str
    alt_text: str | None = None


@dataclass(frozen=True, slots=True)
class ModelDocumentInput:
    """经过安全检查的文档原文，以及供能力降级和预算估算使用的正文。"""

    content_type: str
    data: bytes
    file_name: str
    extracted_text: str
    page_count: int | None = None


type ModelInputPart = ModelTextInput | ModelImageInput | ModelDocumentInput


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """发送给模型的一条标准化、有序多模态消息。"""

    role: ModelRole
    content: tuple[ModelInputPart, ...]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """一次可流式响应的模型请求。"""

    messages: tuple[ModelMessage, ...]
    instructions: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """模型调用的标准化 Token 用量。"""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelStreamEvent:
    """模型流中的文本增量或最终用量。"""

    delta: str = ""
    usage: ModelUsage | None = None


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Provider 对外声明的稳定能力。"""

    streaming: bool
    structured_output: bool
    tool_calling: bool
    image_input: bool
    document_input: bool = False


class ModelProvider(Protocol):
    """所有模型 Provider 必须实现的流式端口。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> ModelCapabilities: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """流式生成标准化文本增量与最终用量。"""
        ...
