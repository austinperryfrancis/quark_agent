"""Client for the local long-lived Quark runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

from quark.gateways.models import GatewayRequest, GatewayResponse


class LocalRuntimeClient:
    def __init__(self, socket_path: str | Path) -> None:
        self.socket_path = str(socket_path)

    async def request(self, request: GatewayRequest) -> GatewayResponse:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        try:
            writer.write(request.model_dump_json().encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise ConnectionError("Quark runtime closed without a response")
            return GatewayResponse.model_validate_json(line)
        finally:
            writer.close()
            await writer.wait_closed()
