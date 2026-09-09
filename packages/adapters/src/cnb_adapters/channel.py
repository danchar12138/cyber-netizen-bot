"""与平台厂商无关的渠道能力声明。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    """渠道实现能够可靠提供的功能。"""

    text: bool = True
    markdown: bool = False
    images: bool = False
    files: bool = False
    streaming: bool = False
    reactions: bool = False
    threads: bool = False
    message_edit: bool = False
    proactive_messages: bool = False


@dataclass(frozen=True, slots=True)
class PlaceholderAdapter:
    """为后续交付的 Adapter 提供不访问网络的占位描述。"""

    key: str
    display_name: str
    capabilities: ChannelCapabilities
    status: str = "not_configured"
