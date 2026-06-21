import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer

from ageos.cli.run import _resolve_sandbox_paths, run_agent


def test_run_agent_uses_native_inference_only_network() -> None:
    client = Mock()
    client.register_agent.return_value = "agt-test"
    captured_env: dict[str, str] = {}

    def run_sandbox(*_args: object, **_kwargs: object) -> int:
        captured_env.update(os.environ)
        return 0

    client.native.run_sandbox.side_effect = run_sandbox

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary="/bin/true",
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
            )

    assert exc.value.exit_code == 0
    _binary, argv = client.native.run_sandbox.call_args.args
    assert Path(argv[-1]).name == "true"
    assert client.native.run_sandbox.call_args.kwargs["isolate_network"] is True
    assert client.native.run_sandbox.call_args.kwargs["root_dir"] is None
    assert client.native.run_sandbox.call_args.kwargs["workdir"] == "/workspace"
    assert client.native.run_sandbox.call_args.kwargs["inference_host"] == "127.0.0.1"
    assert client.native.run_sandbox.call_args.kwargs["inference_port"] == 8000
    assert client.native.run_sandbox.call_args.kwargs["sandbox_inference_port"] == 8000
    assert captured_env["AGEOS_NETWORK"] == "inference-only"
    assert captured_env["AGEOS_SANDBOX_INFERENCE_PORT"] == "8000"


def test_run_agent_stages_relative_binary_without_root_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    binary = tmp_path / "basic_agent.py"
    binary.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    client = Mock()
    client.register_agent.return_value = "agt-test"
    staged_roots: list[Path] = []

    def run_sandbox(*_args: object, **kwargs: object) -> int:
        staged_root = Path(str(kwargs["root_dir"]))
        staged_roots.append(staged_root)
        assert (staged_root / "basic_agent.py").is_file()
        return 0

    client.native.run_sandbox.side_effect = run_sandbox

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary="./basic_agent.py",
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
            )

    assert exc.value.exit_code == 0
    _binary, argv = client.native.run_sandbox.call_args.args
    assert argv[-1] == "/home/agt-test/workspace/basic_agent.py"
    assert len(staged_roots) == 1
    assert staged_roots[0].name.startswith("ageos-workspace-")


def test_run_agent_rejects_binary_outside_root_dir(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    binary = outside / "agent.py"
    binary.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    root_dir = tmp_path / "workspace"
    root_dir.mkdir()
    client = Mock()
    client.register_agent.return_value = "agt-test"

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.BadParameter, match="--binary must be inside --root-dir"):
            run_agent(
                binary=str(binary),
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=root_dir,
            )


def test_run_agent_resolves_explicit_relative_binary_from_host_cwd() -> None:
    client = Mock()
    client.register_agent.return_value = "agt-test"
    client.native.run_sandbox.return_value = 0

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary="examples/basic/basic_agent.py",
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=Path("examples"),
            )

    assert exc.value.exit_code == 0
    _binary, argv = client.native.run_sandbox.call_args.args
    assert argv[-1] == "/home/agt-test/workspace/basic/basic_agent.py"
    assert client.native.run_sandbox.call_args.kwargs["root_dir"] == str((Path.cwd() / "examples").resolve())
    assert client.native.run_sandbox.call_args.kwargs["workdir"] == "/workspace"


def test_run_agent_resolves_dot_relative_binary_inside_root_dir(tmp_path: Path) -> None:
    (tmp_path / "basic_agent.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    client = Mock()
    client.register_agent.return_value = "agt-test"
    client.native.run_sandbox.return_value = 0

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary="./basic_agent.py",
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=tmp_path,
            )

    assert exc.value.exit_code == 0
    _binary, argv = client.native.run_sandbox.call_args.args
    assert argv[-1] == "/home/agt-test/workspace/basic_agent.py"


def test_run_agent_reuses_persistent_sandbox(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    binary = tmp_path / "true"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    agent_home = tmp_path / ".ageos" / "agents" / "agt-existing" / "home"
    agent_home.mkdir(parents=True)
    client = Mock()
    client.register_agent.side_effect = lambda *_args, **kwargs: kwargs["agent_id"] or "agt-new"
    client.native.run_sandbox.return_value = 0

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary=str(binary),
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=tmp_path,
            )

    assert exc.value.exit_code == 0
    assert client.register_agent.call_args.kwargs["agent_id"] == "agt-existing"
    assert client.native.run_sandbox.call_args.kwargs["root_dir"] == str(tmp_path.resolve())
    assert (tmp_path / ".ageos" / "current-agent").read_text(encoding="utf-8") == "agt-existing\n"
    assert "Persistent sandbox found: reusing agt-existing" in capsys.readouterr().out


def test_run_agent_does_not_print_persistent_message_without_existing_sandbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "true"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    client = Mock()
    client.register_agent.return_value = "agt-new"
    client.native.run_sandbox.return_value = 0

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary=str(binary),
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=tmp_path,
            )

    assert exc.value.exit_code == 0
    assert client.register_agent.call_args.kwargs["agent_id"] is None
    assert (tmp_path / ".ageos" / "current-agent").read_text(encoding="utf-8") == "agt-new\n"
    assert "Persistent sandbox found" not in capsys.readouterr().out


def test_run_agent_force_new_sandbox_removes_existing_agent_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "true"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    agent_dir = tmp_path / ".ageos" / "agents" / "agt-existing"
    agent_home = agent_dir / "home"
    agent_home.mkdir(parents=True)
    (agent_home / "test.txt").write_text("from previous run\n", encoding="utf-8")
    client = Mock()
    client.register_agent.return_value = "agt-new"
    client.native.run_sandbox.return_value = 0

    with (
        patch("ageos.cli.run.SchedulerClient.local", return_value=client),
        patch("ageos.cli.run.apply_inference_env", return_value="http://127.0.0.1:8000"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_agent(
                binary=str(binary),
                extra_args=[],
                niceness=0,
                memory="2G",
                cpu=0,
                speciality="default-instruct",
                workdir=None,
                root_dir=tmp_path,
                force_new_sandbox=True,
            )

    assert exc.value.exit_code == 0
    assert client.register_agent.call_args.kwargs["agent_id"] is None
    assert not agent_dir.exists()
    assert (tmp_path / ".ageos" / "current-agent").read_text(encoding="utf-8") == "agt-new\n"
    assert "Persistent sandbox found" not in capsys.readouterr().out


def test_sandbox_paths_default_to_empty_workspace() -> None:
    paths = _resolve_sandbox_paths(None, None)
    assert paths.host_root_dir is None
    assert paths.sandbox_workdir == "/workspace"


def test_sandbox_paths_use_root_dir_as_writable_workdir(tmp_path: Path) -> None:
    paths = _resolve_sandbox_paths(tmp_path, None)
    assert paths.host_root_dir == str(tmp_path.resolve())
    assert paths.sandbox_workdir == "/workspace"


def test_sandbox_paths_map_nested_workdir_under_workspace(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "project"
    nested.mkdir(parents=True)
    paths = _resolve_sandbox_paths(tmp_path, nested)
    assert paths.host_workdir == nested.resolve()
    assert paths.host_root_dir == str(tmp_path.resolve())
    assert paths.sandbox_workdir == "/workspace/nested/project"


def test_sandbox_paths_reject_workdir_outside_root_dir(tmp_path: Path) -> None:
    outside = tmp_path.parent
    with pytest.raises(Exception):
        _resolve_sandbox_paths(tmp_path, outside)


def test_sandbox_paths_reject_protected_root_dir() -> None:
    with pytest.raises(typer.BadParameter):
        _resolve_sandbox_paths(Path("/usr"), None)


def test_sandbox_paths_reject_ageos_source_tree() -> None:
    with pytest.raises(typer.BadParameter):
        _resolve_sandbox_paths(Path.cwd(), None)


def test_sandbox_paths_allow_examples_workspace() -> None:
    paths = _resolve_sandbox_paths(Path.cwd() / "examples", None)
    assert paths.host_root_dir == str((Path.cwd() / "examples").resolve())


def test_sandbox_paths_allow_nested_examples_workspace() -> None:
    paths = _resolve_sandbox_paths(Path.cwd() / "examples" / "openclaw", None)
    assert paths.host_root_dir == str((Path.cwd() / "examples" / "openclaw").resolve())
