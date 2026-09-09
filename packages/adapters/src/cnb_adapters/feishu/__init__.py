"""飞书 Channel Adapter 占位包。"""

from cnb_adapters.placeholder import PlaceholderAdapter
from cnb_domain import ChannelCapabilities


class FeishuPlaceholderAdapter(PlaceholderAdapter):
    """冻结飞书能力声明，但在正式接入前不访问开放平台。"""

    def __init__(self) -> None:
        super().__init__(
            key="feishu",
            display_name="飞书",
            capabilities=ChannelCapabilities(
                markdown=True,
                images=True,
                files=True,
                reactions=True,
                threads=True,
                message_edit=True,
                proactive_messages=True,
                max_text_chars=30_000,
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
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ),
        )


__all__ = ["FeishuPlaceholderAdapter"]
