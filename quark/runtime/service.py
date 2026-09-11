"""Unix-socket host for the single long-lived Quark runtime."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import stat

from pydantic import ValidationError

from quark.gateways.models import GatewayCommand, GatewayRequest, GatewayResponse
from quark.runtime.application import QuarkRuntime

logger = logging.getLogger(__name__)


class RuntimeService:
    def __init__(self, runtime: QuarkRuntime, socket_path: str | Path) -> None:
        self.runtime = runtime
        self.socket_path = Path(socket_path)
        self._server: asyncio.Server | None = None
        self._owns_socket = False

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.stat().st_mode):
                raise RuntimeError(
                    f"Refusing to replace non-socket path {self.socket_path}"
                )
            try:
                _, writer = await asyncio.open_unix_connection(self.socket_path)
            except (ConnectionError, OSError):
                self.socket_path.unlink()
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"Quark runtime is already using {self.socket_path}")
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=self.socket_path,
            limit=64 * 1024,
        )
        self._owns_socket = True
        logger.info("runtime service listening socket=%s", self.socket_path)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._owns_socket and self.socket_path.exists():
            self.socket_path.unlink()
        self._owns_socket = False
        logger.info("runtime service stopped socket=%s", self.socket_path)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            request = GatewayRequest.model_validate_json(line)
            response = await self._dispatch(request)
        except (ValidationError, ValueError) as exc:
            response = GatewayResponse(
                ok=False,
                kind="INVALID_REQUEST",
                message=str(exc),
            )
        except Exception as exc:
            response = GatewayResponse(
                ok=False,
                kind="RUNTIME_ERROR",
                message=str(exc) or type(exc).__name__,
            )
        writer.write(response.model_dump_json().encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _dispatch(self, request: GatewayRequest) -> GatewayResponse:
        if request.command is GatewayCommand.STATUS:
            return self.runtime.status()
        if request.command is GatewayCommand.SKILLS:
            return self.runtime.skills()
        if request.command is GatewayCommand.RUNS:
            return self.runtime.list_runs()
        if request.command is GatewayCommand.RUN:
            assert request.run_id is not None
            return self.runtime.inspect_run(request.run_id)
        if request.command is GatewayCommand.RECOVER:
            assert request.run_id is not None and request.recovery_action is not None
            return await self.runtime.recover_run(
                request.run_id,
                request.recovery_action,
                skill_call_id=request.skill_call_id,
            )
        assert request.session_key is not None and request.text is not None
        return await self.runtime.handle_message(request.session_key, request.text)
