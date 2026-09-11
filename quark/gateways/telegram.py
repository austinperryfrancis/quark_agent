"""Telegram long-polling gateway for the shared Quark runtime."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quark.gateways.models import GatewayResponse

if TYPE_CHECKING:
    from quark.runtime.application import QuarkRuntime


class TelegramGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, recoverable: bool) -> None:
        self.code = code
        self.recoverable = recoverable
        super().__init__(message)


class _TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    is_bot: bool = False


class _TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int


class _TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    chat: _TelegramChat
    text: str | None = None
    message_thread_id: int | None = None
    from_user: _TelegramUser | None = Field(default=None, alias="from")


class _TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    update_id: int
    message: _TelegramMessage | None = None


class _UpdatesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ok: bool
    result: list[_TelegramUpdate] = Field(default_factory=list)
    description: str | None = None


class _MethodResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ok: bool
    description: str | None = None


class TelegramGateway:
    """Translate Telegram text updates into calls on the one Quark runtime."""

    def __init__(
        self,
        runtime: QuarkRuntime,
        token: str,
        *,
        api_url: str = "https://api.telegram.org",
        poll_timeout_seconds: int = 30,
        retry_delay_seconds: float = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("Telegram token cannot be empty")
        if poll_timeout_seconds < 1:
            raise ValueError("poll_timeout_seconds must be positive")
        self.runtime = runtime
        self._token = token
        self._api_url = api_url.rstrip("/")
        self.poll_timeout_seconds = poll_timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self._offset: int | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(poll_timeout_seconds + 10.0)
        )

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            try:
                await self.poll_once()
            except TelegramGatewayError as exc:
                if not exc.recoverable:
                    raise
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.retry_delay_seconds
                    )
                except TimeoutError:
                    pass

    async def poll_once(self) -> int:
        payload: dict[str, Any] = {
            "timeout": self.poll_timeout_seconds,
            "allowed_updates": ["message"],
        }
        if self._offset is not None:
            payload["offset"] = self._offset
        response = await self._post("getUpdates", payload)
        envelope = self._validate_response(_UpdatesResponse, response)
        if not envelope.ok:
            raise TelegramGatewayError(
                "TELEGRAM_API_ERROR",
                envelope.description or "Telegram getUpdates failed.",
                recoverable=False,
            )

        processed = 0
        for update in envelope.result:
            message = update.message
            if (
                message is not None
                and message.text is not None
                and not (message.from_user and message.from_user.is_bot)
            ):
                reply = await self.runtime.handle_message(
                    f"telegram:{message.chat.id}", message.text
                )
                await self.send_response(
                    message.chat.id,
                    reply,
                    message_thread_id=message.message_thread_id,
                )
                processed += 1
            self._offset = update.update_id + 1
        return processed

    async def send_response(
        self,
        chat_id: int,
        response: GatewayResponse,
        *,
        message_thread_id: int | None = None,
    ) -> None:
        text = self._render(response)
        for chunk in self._chunks(text, 4096):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            envelope = self._validate_response(
                _MethodResponse, await self._post("sendMessage", payload)
            )
            if not envelope.ok:
                raise TelegramGatewayError(
                    "TELEGRAM_SEND_FAILED",
                    envelope.description or "Telegram sendMessage failed.",
                    recoverable=True,
                )

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._api_url}/bot{self._token}/{method}"
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TelegramGatewayError(
                "TELEGRAM_TIMEOUT", "Telegram request timed out.", recoverable=True
            ) from exc
        except httpx.RequestError as exc:
            raise TelegramGatewayError(
                "TELEGRAM_UNAVAILABLE",
                "Could not connect to Telegram.",
                recoverable=True,
            ) from exc
        except (httpx.HTTPStatusError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            raise TelegramGatewayError(
                "TELEGRAM_INVALID_RESPONSE",
                "Telegram returned an invalid response.",
                recoverable=status is None or status >= 500,
            ) from exc
        if not isinstance(data, dict):
            raise TelegramGatewayError(
                "TELEGRAM_INVALID_RESPONSE",
                "Telegram returned an invalid response.",
                recoverable=True,
            )
        return data

    @staticmethod
    def _validate_response(
        response_type: type[_UpdatesResponse] | type[_MethodResponse],
        data: dict[str, Any],
    ) -> _UpdatesResponse | _MethodResponse:
        try:
            return response_type.model_validate(data)
        except ValidationError as exc:
            raise TelegramGatewayError(
                "TELEGRAM_INVALID_RESPONSE",
                "Telegram returned an invalid response.",
                recoverable=True,
            ) from exc

    @staticmethod
    def _render(response: GatewayResponse) -> str:
        if not response.data:
            return response.message
        return f"{response.message}\n\n{json.dumps(response.data, indent=2, sort_keys=True)}"

    @staticmethod
    def _chunks(text: str, size: int) -> tuple[str, ...]:
        return tuple(text[index : index + size] for index in range(0, len(text), size))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
