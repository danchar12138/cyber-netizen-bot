"""模型 Provider 的本地实现与 OpenAI 官方 SDK 适配器。"""

import asyncio
from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseIncompleteEvent,
    ResponseTextDeltaEvent,
)

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
        try:
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
            await stream.close()


def assert_model_provider(_: ModelProvider) -> None:
    """供静态类型检查验证 Provider 实现符合端口。"""


assert_model_provider(DevelopmentModelProvider())
