"""Vendor-neutral channel capability declarations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    """Features a channel implementation can faithfully provide."""

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
    """Non-networking descriptor for an adapter planned for later delivery."""

    key: str
    display_name: str
    capabilities: ChannelCapabilities
    status: str = "not_configured"
