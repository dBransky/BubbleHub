from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer

from ageos.cli.run import _choose_access_policy, _make_interactive_access_broker, _select_persistent_sandbox, run_agent
from ageos.cli.shell import _interactive_args, command


class _Input:
    def __init__(self, value: str) -> None:
        self._value = value
        self._offset = 0

    def read(self, size: int = -1) -> str:
        if size < 0:
            size = len(self._value) - self._offset
        chunk = self._value[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def isatty(self) -> bool:
        return False


class _Output:
    def __init__(self) -> None:
        self.value = ""

    def write(self, value: str) -> int:
        self.value += value
        return len(value)

    def flush(self) -> None:
        return None


def test_interactive_shell_args_force_interactive_mode() -> None:
    assert _interactive_args("/usr/bin/bash") == ["--noprofile", "--norc", "-i"]
    assert _interactive_args("/bin/zsh") == ["-f", "-i"]
    assert _interactive_args("/bin/sh") == ["-i"]


def test_shell_command_enables_interactive_access_broker() -> None:
    ctx = Mock()
    ctx.args = []

    with patch("ageos.cli.shell.run_agent") as run:
        command(ctx)

    assert run.call_args.kwargs["interactive_access"] is True


def test_shell_command_passes_agent_name() -> None:
    ctx = Mock()
    ctx.args = []

    with patch("ageos.cli.shell.run_agent") as run:
        command(ctx, name="reviewer")

    assert run.call_args.kwargs["name"] == "reviewer"


def test_shell_with_root_dir_allows_system_shell_binary(tmp_path: Path) -> None:
    client = Mock()
    client.register_agent.return_value = "agt-test"
    client.native.run_sandbox.return_value = 0

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary="/usr/bin/bash",
                extra_args=["-i"],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=tmp_path,
                interactive_access=True,
            )

    assert exc.value.exit_code == 0
    _binary, argv = client.native.run_sandbox.call_args.args
    assert argv[0] == "/usr/bin/bash"
    assert client.native.run_sandbox.call_args.kwargs["root_dir"] == str(tmp_path.resolve())
    assert client.native.run_sandbox.call_args.kwargs["access_broker"] is not None


def test_force_new_sandbox_removes_old_access_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "workspace"
    agent_id = "agt-oldmanifest"
    home = root / ".ageos" / "agents" / agent_id / "home"
    home.mkdir(parents=True)
    (root / ".ageos" / "current-agent").write_text(f"{agent_id}\n", encoding="utf-8")
    state = tmp_path / "state"
    manifest_dir = state / "sandboxes" / agent_id
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "access-manifest.json").write_text('{"policies":[]}\n', encoding="utf-8")
    monkeypatch.setenv("AGEOS_STATE_DIR", str(state))

    selected = _select_persistent_sandbox(str(root), force_new=True)

    assert selected.agent_id is None
    assert not (root / ".ageos" / "agents" / agent_id).exists()
    assert not manifest_dir.exists()


def test_shell_access_prompt_supports_arrow_navigation_and_enter() -> None:
    output = _Output()
    choice = _choose_access_policy(
        {"subject": "google.com", "method": "GET", "path": "/"},
        input_stream=_Input("\x1b[B\x1b[B\n"),
        output_stream=output,
    )

    assert choice == "ask"
    assert "always" in output.value
    assert "never" in output.value
    assert "ask every time (approve now)" in output.value
    assert "Deny" not in output.value
    assert "Approve\n" not in output.value
    assert "\x1b[32m> ask every time (approve now)\x1b[0m" in output.value
    assert "\x1b[32m  ask every time (approve now)" not in output.value


def test_interactive_access_broker_applies_selected_policy() -> None:
    native = Mock()
    broker = _make_interactive_access_broker(native, "agt-test")

    with patch(
        "ageos.cli.run._choose_access_policy",
        return_value="always",
    ):
        response = broker({"kind": "http", "subject": "google.com", "method": "GET", "path": "/"})

    assert response == "always"
    native.apply_access_policy.assert_called_once_with(
        "agt-test",
        kind="http",
        subject="google.com",
        method="GET",
        path="/",
        policy="always",
    )
