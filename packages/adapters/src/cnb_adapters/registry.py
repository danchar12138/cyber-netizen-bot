"""Channel Adapter 注册表与首期默认实现集合。"""

from collections.abc import Iterable

from cnb_adapters.channel import ChannelAdapter
from cnb_domain import ChannelPlatform


class ChannelAdapterRegistry:
    """按稳定平台键选择 Adapter，并拒绝重复注册。"""

    def __init__(self, adapters: Iterable[ChannelAdapter]) -> None:
        self._adapters: dict[ChannelPlatform, ChannelAdapter] = {}
        for adapter in adapters:
            if adapter.platform in self._adapters:
                raise ValueError(f"重复注册渠道 Adapter：{adapter.platform.value}")
            self._adapters[adapter.platform] = adapter

    def get(self, platform: ChannelPlatform) -> ChannelAdapter:
        try:
            return self._adapters[platform]
        except KeyError as error:
            raise LookupError(f"渠道 Adapter 不存在：{platform.value}") from error

    def all(self) -> tuple[ChannelAdapter, ...]:
        return tuple(
            self._adapters[platform] for platform in ChannelPlatform if platform in self._adapters
        )


def build_default_channel_registry() -> ChannelAdapterRegistry:
    """构建 Web 正式实现和三个明确不访问网络的 IM 占位实现。"""
    from cnb_adapters.discord import DiscordPlaceholderAdapter
    from cnb_adapters.feishu import FeishuPlaceholderAdapter
    from cnb_adapters.telegram import TelegramPlaceholderAdapter
    from cnb_adapters.web import WebChannelAdapter

    return ChannelAdapterRegistry(
        (
            WebChannelAdapter(),
            FeishuPlaceholderAdapter(),
            DiscordPlaceholderAdapter(),
            TelegramPlaceholderAdapter(),
        )
    )
