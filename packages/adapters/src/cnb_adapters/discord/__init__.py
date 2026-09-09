"""Discord Channel Adapter 占位包。"""

from cnb_adapters.placeholder import PlaceholderAdapter
from cnb_domain import ChannelCapabilities


class DiscordPlaceholderAdapter(PlaceholderAdapter):
    """冻结 Discord 能力声明，但在正式接入前不访问外部 API。"""

    def __init__(self) -> None:
        super().__init__(
            key="discord",
            display_name="Discord",
            capabilities=ChannelCapabilities(
                markdown=True,
                images=True,
                files=True,
                reactions=True,
                threads=True,
                message_edit=True,
                proactive_messages=True,
                max_text_chars=2_000,
                max_attachment_bytes=25 * 1024 * 1024,
                accepted_content_types=(
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                    "image/gif",
                    "text/plain",
                    "text/markdown",
                    "text/csv",
                    "application/json",
                    "application/pdf",
                ),
            ),
        )


__all__ = ["DiscordPlaceholderAdapter"]
