import asyncio
import json

import httpx
import pytest

from quark.gateways import GatewayResponse, TelegramGateway, TelegramGatewayError


class StubRuntime:
    def __init__(self, response: GatewayResponse | None = None) -> None:
        self.response = response or GatewayResponse(
            ok=True, kind="COMPLETED", message="Done."
        )
        self.messages: list[tuple[str, str]] = []

    async def handle_message(self, session_key: str, text: str) -> GatewayResponse:
        self.messages.append((session_key, text))
        return self.response


def make_gateway(handler, runtime: StubRuntime | None = None) -> tuple[TelegramGateway, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramGateway(
        runtime or StubRuntime(),
        "secret-token",
        client=client,
        poll_timeout_seconds=1,
    )
    return gateway, client


def test_text_update_uses_shared_runtime_session_and_sends_reply() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getUpdates"):
            if "offset" in json.loads(request.content):
                return httpx.Response(200, json={"ok": True, "result": []})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 41,
                            "message": {
                                "chat": {"id": 123},
                                "from": {"is_bot": False},
                                "text": "organize my note",
                                "message_thread_id": 9,
                            },
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {}})

    runtime = StubRuntime(
        GatewayResponse(
            ok=True,
            kind="REVIEW_CALL",
            message="Approve tags?",
            data={"arguments": {"tags": ["research"]}},
        )
    )
    gateway, client = make_gateway(handler, runtime)

    processed = asyncio.run(gateway.poll_once())
    asyncio.run(gateway.poll_once())
    asyncio.run(client.aclose())

    assert processed == 1
    assert runtime.messages == [("telegram:123", "organize my note")]
    sent = json.loads(requests[1].content)
    assert sent["chat_id"] == 123
    assert sent["message_thread_id"] == 9
    assert "research" in sent["text"]
    next_poll = json.loads(requests[2].content)
    assert next_poll["offset"] == 42
    assert next_poll["allowed_updates"] == ["message"]


def test_non_text_and_bot_updates_are_confirmed_without_runtime_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 2}}},
                    {
                        "update_id": 2,
                        "message": {
                            "chat": {"id": 2},
                            "from": {"is_bot": True},
                            "text": "ignore",
                        },
                    },
                ],
            },
        )

    runtime = StubRuntime()
    gateway, client = make_gateway(handler, runtime)

    assert asyncio.run(gateway.poll_once()) == 0
    asyncio.run(client.aclose())
    assert runtime.messages == []
    assert gateway._offset == 3


def test_failed_send_does_not_advance_update_offset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 7,
                            "message": {"chat": {"id": 3}, "text": "hello"},
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"ok": False, "description": "blocked"})

    gateway, client = make_gateway(handler)

    with pytest.raises(TelegramGatewayError) as caught:
        asyncio.run(gateway.poll_once())
    asyncio.run(client.aclose())

    assert caught.value.code == "TELEGRAM_SEND_FAILED"
    assert gateway._offset is None


def test_long_responses_are_split_to_telegram_limit() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    gateway, client = make_gateway(handler)
    response = GatewayResponse(ok=True, kind="LONG", message="x" * 5000)

    asyncio.run(gateway.send_response(12, response))
    asyncio.run(client.aclose())

    assert [len(item["text"]) for item in sent] == [4096, 904]


def test_bot_api_errors_are_structured_without_exposing_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "unauthorized"})

    gateway, client = make_gateway(handler)

    with pytest.raises(TelegramGatewayError) as caught:
        asyncio.run(gateway.poll_once())
    asyncio.run(client.aclose())

    assert caught.value.code == "TELEGRAM_INVALID_RESPONSE"
    assert not caught.value.recoverable
    assert "secret-token" not in str(caught.value)
