import argparse
import asyncio

import quark.main as cli
from quark.gateways import GatewayResponse


def test_chat_banner_checks_runtime_and_warns_when_no_skills(
    monkeypatch, capsys
) -> None:
    async def request(socket, request):
        return GatewayResponse(
            ok=True,
            kind="STATUS",
            message="Quark runtime is running.",
            data={"installed_skills": 0, "waiting_runs": 0},
        )

    def end_input(prompt):
        raise EOFError

    monkeypatch.setattr(cli, "_request", request)
    monkeypatch.setattr("builtins.input", end_input)

    cli._chat(argparse.Namespace(socket="unused.sock"))

    output = capsys.readouterr().out
    assert "Q U A R K" in output
    assert "Runtime online | 0 Skills | 0 waiting Runs" in output
    assert "No Skills are enabled" in output


def test_default_socket_is_stable_across_working_directories(tmp_path) -> None:
    expected = cli.DEFAULT_RUNTIME_DIRECTORY / "quark.sock"
    original = cli.Path.cwd()
    try:
        cli.os.chdir(tmp_path)
        parser = cli.build_parser()
        chat = parser.parse_args(["chat"])
        serve = parser.parse_args(["serve"])
    finally:
        cli.os.chdir(original)

    assert chat.socket == expected
    assert serve.socket is None


def test_activity_spinner_animates_and_clears_terminal_line(monkeypatch) -> None:
    class Terminal:
        def __init__(self) -> None:
            self.output = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.output += value
            return len(value)

        def flush(self) -> None:
            pass

    async def delayed_request(socket, request):
        await asyncio.sleep(0.14)
        return GatewayResponse(ok=True, kind="COMPLETED", message="Done.")

    terminal = Terminal()
    monkeypatch.setattr(cli.sys, "stdout", terminal)
    monkeypatch.setattr(cli, "_request", delayed_request)

    response = asyncio.run(
        cli._request_with_activity(
            "unused.sock",
            cli.GatewayRequest(
                command=cli.GatewayCommand.CHAT,
                session_key="cli:test",
                text="hello",
            ),
        )
    )

    assert response.message == "Done."
    assert "Quark is working" in terminal.output
    assert terminal.output.endswith("\r\033[2K")
