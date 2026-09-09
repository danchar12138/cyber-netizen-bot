"""模型 Provider 契约、本地实现和官方 SDK 适配测试。"""

from collections.abc import AsyncIterator
from typing import cast
from uuid import uuid4

from openai import AsyncOpenAI
from openai.types.responses import ResponseStreamEvent, ResponseTextDeltaEvent

from cnb_cognition import ModelMessage, ModelRequest, ModelRole
from cnb_domain import ConfigScope
from cnb_infrastructure import (
    ConfiguredModelProviderResolver,
    DevelopmentModelProvider,
    MemorySecretStore,
    OpenAIResponsesProvider,
)


async def test_development_provider_streams_without_external_credentials() -> None:
    provider = DevelopmentModelProvider()
    request = ModelRequest(
        messages=(ModelMessage(role=ModelRole.USER, content="今天过得怎么样？"),),
        instructions="自然回复。",
        max_output_tokens=128,
    )

    events = [event async for event in provider.stream(request)]

    assert "今天过得怎么样" in "".join(event.delta for event in events)
    assert events[-1].usage is not None
    assert provider.capabilities.streaming is True


class FakeOpenAIStream:
    """模拟官方 SDK 返回的异步事件流。"""

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> AsyncIterator[ResponseStreamEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ResponseStreamEvent]:
        yield ResponseTextDeltaEvent(
            content_index=0,
            delta="你好",
            item_id="item-1",
            logprobs=[],
            output_index=0,
            sequence_number=1,
            type="response.output_text.delta",
        )

    async def close(self) -> None:
        self.closed = True


class FakeResponses:
    """记录 Responses API 调用参数的测试资源。"""

    def __init__(self, stream: FakeOpenAIStream) -> None:
        self.stream = stream
        self.arguments: dict[str, object] = {}

    async def create(self, **arguments: object) -> FakeOpenAIStream:
        self.arguments = arguments
        return self.stream


class FakeOpenAIClient:
    """仅提供 Provider 测试所需的 Responses 资源。"""

    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


async def test_openai_provider_uses_responses_stream_without_remote_storage() -> None:
    stream = FakeOpenAIStream()
    responses = FakeResponses(stream)
    client = cast(AsyncOpenAI, FakeOpenAIClient(responses))
    provider = OpenAIResponsesProvider(
        api_key="仅供测试",
        model="gpt-5-mini",
        client=client,
    )
    request = ModelRequest(
        messages=(ModelMessage(role=ModelRole.USER, content="你好"),),
        instructions="自然回复。",
        max_output_tokens=128,
    )

    events = [event async for event in provider.stream(request)]

    assert [event.delta for event in events] == ["你好"]
    assert responses.arguments["model"] == "gpt-5-mini"
    assert responses.arguments["stream"] is True
    assert responses.arguments["store"] is False
    assert stream.closed is True


async def test_configured_provider_resolver_selects_local_and_openai_providers() -> None:
    secret_store = MemorySecretStore()
    tenant_id = uuid4()
    await secret_store.set_secret(
        key="model.openai.api_key",
        scope_type=ConfigScope.SYSTEM,
        scope_id=None,
        plaintext="仅供 Provider 解析测试的虚假凭证",
        actor_id=None,
    )
    resolver = ConfiguredModelProviderResolver(secret_store)

    local = await resolver.resolve(
        provider="development",
        model="friendly-echo-v1",
        tenant_id=tenant_id,
    )
    openai_provider = await resolver.resolve(
        provider="openai",
        model="gpt-5-mini",
        tenant_id=tenant_id,
    )

    assert local.name == "development"
    assert openai_provider.name == "openai"
    assert openai_provider.model == "gpt-5-mini"
