import asyncio
import json

import httpx
from pydantic import BaseModel, ConfigDict
import pytest

from quark.inference import Message, MessageRole, ModelProviderError, OllamaProvider


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def make_provider(handler, *, repairs: int = 1) -> tuple[OllamaProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama.test"
    )
    return OllamaProvider("tiny-model", client=client, max_parse_repairs=repairs), client


def test_structured_generation_uses_schema_and_non_streaming_chat() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"message": {"content": '{"value": 7}'}, "done": True},
        )

    provider, client = make_provider(handler)
    result = asyncio.run(
        provider.generate_structured(
            (Message(role=MessageRole.USER, content="Return seven."),), Answer
        )
    )
    asyncio.run(client.aclose())

    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/api/chat"
    assert payload["model"] == "tiny-model"
    assert payload["stream"] is False
    assert payload["format"] == Answer.model_json_schema()
    assert payload["options"]["temperature"] == 0
    assert result == Answer(value=7)


def test_invalid_output_gets_one_bounded_repair() -> None:
    responses = iter(["not json", '{"value": 8}'])
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"message": {"content": next(responses)}, "done": True}
        )

    provider, client = make_provider(handler)
    result = asyncio.run(
        provider.generate_structured(
            (Message(role=MessageRole.USER, content="Return eight."),), Answer
        )
    )
    asyncio.run(client.aclose())

    repair_payload = json.loads(requests[1].content)
    assert result == Answer(value=8)
    assert len(requests) == 2
    assert len(repair_payload["messages"]) == 3
    assert repair_payload["messages"][-1]["content"].startswith("Return corrected JSON")


def test_invalid_output_after_repair_is_controlled_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": "still invalid"}, "done": True}
        )

    provider, client = make_provider(handler)
    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(
            provider.generate_structured(
                (Message(role=MessageRole.USER, content="Return one."),), Answer
            )
        )
    asyncio.run(client.aclose())

    assert caught.value.code == "INVALID_STRUCTURED_OUTPUT"
    assert not caught.value.recoverable


def test_http_errors_are_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    provider, client = make_provider(handler, repairs=0)
    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(
            provider.generate_structured(
                (Message(role=MessageRole.USER, content="Return one."),), Answer
            )
        )
    asyncio.run(client.aclose())

    assert caught.value.code == "MODEL_HTTP_ERROR"
    assert str(caught.value) == "model not found"
    assert not caught.value.recoverable


def test_connection_errors_are_structured_and_recoverable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    provider, client = make_provider(handler, repairs=0)
    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(
            provider.generate_structured(
                (Message(role=MessageRole.USER, content="Return one."),), Answer
            )
        )
    asyncio.run(client.aclose())

    assert caught.value.code == "MODEL_UNAVAILABLE"
    assert caught.value.recoverable
