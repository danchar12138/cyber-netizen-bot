"""模型 Provider 的本地实现与 OpenAI 官方 SDK 适配器。"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from openai import AsyncOpenAI
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseIncompleteEvent,
    ResponseTextDeltaEvent,
)

from cnb_application import ModelProviderConfigurationError, SecretStore
from cnb_cognition import (
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelStreamEvent,
    ModelUsage,
)


class DevelopmentModelProvider:
    """无需外部凭证即可验证完整流式链路的本地 Provider。"""

    @property
    def name(self) -> str:
        return "development"

    @property
    def model(self) -> str:
        return "friendly-echo-v1"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            streaming=True,
            structured_output=False,
            tool_calling=False,
            image_input=False,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        last_message = request.messages[-1].content if request.messages else ""
        response = f"收到啦。你刚才说：“{last_message}” 我们可以从这里继续聊。"
        for start in range(0, len(response), 4):
            await asyncio.sleep(0)
            yield ModelStreamEvent(delta=response[start : start + 4])
        yield ModelStreamEvent(
            usage=ModelUsage(
                input_tokens=sum(len(item.content) for item in request.messages),
                output_tokens=len(response),
            )
        )


class OpenAIResponsesProvider:
    """基于 OpenAI 官方 Python SDK Responses API 的流式适配器。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._owns_client = client is None
        self._client = client or AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            streaming=True,
            structured_output=True,
            tool_calling=True,
            image_input=True,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        stream = None
        try:
            stream = await self._client.responses.create(
                model=self._model,
                instructions=request.instructions,
                input=[
                    {"role": message.role.value, "content": message.content}
                    for message in request.messages
                ],
                max_output_tokens=request.max_output_tokens,
                store=False,
                stream=True,
            )
            async for event in stream:
                if isinstance(event, ResponseTextDeltaEvent):
                    yield ModelStreamEvent(delta=event.delta)
                elif isinstance(event, ResponseCompletedEvent) and event.response.usage:
                    yield ModelStreamEvent(
                        usage=ModelUsage(
                            input_tokens=event.response.usage.input_tokens,
                            output_tokens=event.response.usage.output_tokens,
                        )
                    )
                elif isinstance(event, ResponseFailedEvent):
                    raise RuntimeError("OpenAI 模型响应失败")
                elif isinstance(event, ResponseIncompleteEvent):
                    raise RuntimeError("OpenAI 模型响应未完整完成")
        finally:
            if stream is not None:
                await stream.close()
            if self._owns_client:
                await self._client.close()


class ConfiguredModelProviderResolver:
    """根据最终配置和作用域密钥动态选择模型 Provider。"""

    def __init__(self, secret_store: SecretStore) -> None:
        self._secret_store = secret_store
        self._development_provider = DevelopmentModelProvider()

    async def resolve(
        self,
        *,
        provider: str,
        model: str,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> ModelProvider:
        if provider == "development":
            return self._development_provider
        if provider != "openai":
            raise ModelProviderConfigurationError(f"不支持的模型 Provider：{provider}")
        api_key = await self._secret_store.resolve_secret(
            "model.openai.api_key",
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            user_id=user_id,
        )
        if api_key is None:
            raise ModelProviderConfigurationError(
                "当前作用域尚未配置 OpenAI API 密钥，请先在配置中心写入凭证"
            )
        return OpenAIResponsesProvider(api_key=api_key, model=model)


def assert_model_provider(_: ModelProvider) -> None:
    """供静态类型检查验证 Provider 实现符合端口。"""


assert_model_provider(DevelopmentModelProvider())
