from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer

from ageos.cli.run import run_agent
from ageos.cli.shell import _interactive_args


def test_interactive_shell_args_force_interactive_mode() -> None:
    assert _interactive_args("/usr/bin/bash") == ["--noprofile", "--norc", "-i"]
    assert _interactive_args("/bin/zsh") == ["-f", "-i"]
    assert _interactive_args("/bin/sh") == ["-i"]


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
            )

    assert exc.value.exit_code == 0
    _binary, argv = client.native.run_sandbox.call_args.args
    assert argv[0] == "/usr/bin/bash"
    assert client.native.run_sandbox.call_args.kwargs["root_dir"] == str(tmp_path.resolve())
