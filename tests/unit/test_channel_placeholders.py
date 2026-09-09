"""Channel capability tests."""

from cnb_adapters import ChannelCapabilities, PlaceholderAdapter


def test_placeholder_never_claims_configuration() -> None:
    adapter = PlaceholderAdapter(
        key="feishu",
        display_name="Feishu",
        capabilities=ChannelCapabilities(markdown=True, images=True, files=True),
    )

    assert adapter.status == "not_configured"
    assert adapter.capabilities.text is True
    assert adapter.capabilities.streaming is False
