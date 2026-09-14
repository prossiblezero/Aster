import asyncio
import subprocess
import sys

from aster.cli import Terminal, tui_main
from aster.config import Settings


def test_tui_entrypoint_forwards_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr("aster.cli.main", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["aster-tui", "--cwd", "/tmp/repo", "--read-only"])
    assert tui_main() == 0
    assert seen == [["--tui", "--cwd", "/tmp/repo", "--read-only"]]


def test_terminal_null_input_is_unavailable(monkeypatch):
    with open("/dev/null") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        assert asyncio.run(Terminal(Settings()).question("Task?")) is None


def test_installed_cli_smoke(tmp_path):
    from pathlib import Path

    for command in ("aster", "aster-tui"):
        completed = subprocess.run(
            [str(Path(sys.prefix) / "bin" / command), "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert "--cwd" in completed.stdout
