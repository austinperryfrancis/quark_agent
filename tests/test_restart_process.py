import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from quark.gateways import GatewayCommand, GatewayRequest, LocalRuntimeClient


def _start_service(
    database: Path, socket: Path, marker: Path
) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path.cwd())
    return subprocess.Popen(
        [
            sys.executable,
            "tests/support/restart_service.py",
            str(database),
            str(socket),
            str(marker),
        ],
        cwd=Path.cwd(),
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_service(process: subprocess.Popen[str], socket: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise AssertionError(f"service exited during startup: {stderr}")
        try:
            response = asyncio.run(
                LocalRuntimeClient(socket).request(
                    GatewayRequest(command=GatewayCommand.STATUS)
                )
            )
        except (ConnectionError, OSError):
            time.sleep(0.02)
            continue
        assert response.ok
        return
    raise AssertionError("service did not start")


def _stop_service(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_pending_review_survives_real_process_restart(tmp_path) -> None:
    database = tmp_path / "quark.db"
    marker = tmp_path / "executed.txt"
    socket = Path.cwd() / f"restart-{uuid4().hex[:8]}.sock"
    first = _start_service(database, socket, marker)
    second: subprocess.Popen[str] | None = None
    try:
        _wait_for_service(first, socket)
        proposed = asyncio.run(
            LocalRuntimeClient(socket).request(
                GatewayRequest(
                    command=GatewayCommand.CHAT,
                    session_key="cli:restart",
                    text="Write the marker.",
                )
            )
        )
        assert proposed.kind == "REVIEW_CALL"
        assert not marker.exists()

        _stop_service(first)
        second = _start_service(database, socket, marker)
        _wait_for_service(second, socket)
        completed = asyncio.run(
            LocalRuntimeClient(socket).request(
                GatewayRequest(
                    command=GatewayCommand.CHAT,
                    session_key="cli:restart",
                    text="approve",
                )
            )
        )

        assert completed.kind == "COMPLETED"
        assert completed.run_id == proposed.run_id
        assert marker.read_text() == "persisted"
    finally:
        if first.poll() is None:
            _stop_service(first)
        if second is not None and second.poll() is None:
            _stop_service(second)
        if socket.exists():
            socket.unlink()
