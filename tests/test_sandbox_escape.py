from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from bubblehub.native import NativeScheduler

pytestmark = pytest.mark.skipif(platform.system() != "Linux", reason="sandbox escape tests are Linux-only")

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PYTHON_ESCAPE_CATEGORIES = [
    "env",
    "fs-direct",
    "fs-links",
    "protected-paths",
    "namespace-tools",
]
PYTHON_ISOLATED_ONLY_CATEGORIES = [
    "network-isolated",
    "proxy-bypass",
    "endpoint-repoint",
]
C_ESCAPE_CATEGORIES = [
    "env",
    "fs-direct",
    "proc-root",
    "fs-links",
    "protected-writes",
    "protected-reads",
    "protected-dirs",
    "runtime-sockets",
    "namespace",
    "kernel",
    "swap",
]
C_ISOLATED_ONLY_CATEGORIES = [
    "network-isolated",
    "endpoint-repoint",
]


def _prepare_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_canary = tmp_path / "host-canary.txt"
    host_canary.write_text("host-secret\n", encoding="utf-8")
    return workspace, host_canary


def _configure_escape_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    host_canary: Path,
    allow_network: bool,
    suffix: str,
) -> None:
    monkeypatch.setenv("BUBBLEHUB_AGENT_ID", f"agt-escape-{suffix}-{'net' if allow_network else 'isolated'}")
    monkeypatch.setenv("HOST_CANARY", str(host_canary))
    monkeypatch.setenv("EXPECT_NETWORK_BLOCKED", "0" if allow_network else "1")


def _run_in_sandbox(workspace: Path, argv: list[str], *, allow_network: bool) -> int:
    return NativeScheduler().run_sandbox(
        argv[0],
        argv,
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(workspace),
        root_dir=str(workspace),
        isolate_network=not allow_network,
    )


def _assert_host_canary_unchanged(tmp_path: Path, host_canary: Path) -> None:
    assert host_canary.read_text(encoding="utf-8") == "host-secret\n"
    assert not (tmp_path / "created-by-python-sandbox").exists()
    assert not (tmp_path / "created-by-c-sandbox").exists()


@pytest.mark.parametrize("category", PYTHON_ESCAPE_CATEGORIES)
@pytest.mark.parametrize("allow_network", [False, True])
def test_python_escape_attempts_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_network: bool,
    category: str,
) -> None:
    workspace, host_canary = _prepare_workspace(tmp_path)
    _configure_escape_env(monkeypatch, host_canary=host_canary, allow_network=allow_network, suffix=f"python-{category}")
    shutil.copy2(FIXTURES / "sandbox_escape_python.py", workspace / "sandbox_escape_python.py")

    result = _run_in_sandbox(
        workspace,
        ["/usr/bin/python3", "sandbox_escape_python.py", category],
        allow_network=allow_network,
    )

    assert result == 0
    _assert_host_canary_unchanged(tmp_path, host_canary)


@pytest.mark.parametrize("category", PYTHON_ISOLATED_ONLY_CATEGORIES)
def test_python_isolated_network_escape_attempts_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    workspace, host_canary = _prepare_workspace(tmp_path)
    _configure_escape_env(monkeypatch, host_canary=host_canary, allow_network=False, suffix=f"python-{category}")
    shutil.copy2(FIXTURES / "sandbox_escape_python.py", workspace / "sandbox_escape_python.py")

    result = _run_in_sandbox(
        workspace,
        ["/usr/bin/python3", "sandbox_escape_python.py", category],
        allow_network=False,
    )

    assert result == 0
    _assert_host_canary_unchanged(tmp_path, host_canary)


def _compile_c_probe(workspace: Path) -> Path:
    probe = workspace / "sandbox_escape_probe"
    subprocess.run(
        ["cc", str(FIXTURES / "sandbox_escape_probe.c"), "-o", str(probe)],
        cwd=ROOT,
        check=True,
    )
    return probe


@pytest.mark.parametrize("category", C_ESCAPE_CATEGORIES)
@pytest.mark.parametrize("allow_network", [False, True])
def test_c_syscall_escape_attempts_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_network: bool,
    category: str,
) -> None:
    if shutil.which("cc") is None:
        pytest.skip("C compiler is not installed")
    workspace, host_canary = _prepare_workspace(tmp_path)
    _configure_escape_env(monkeypatch, host_canary=host_canary, allow_network=allow_network, suffix=f"c-{category}")
    probe = _compile_c_probe(workspace)

    result = _run_in_sandbox(workspace, [str(probe), category], allow_network=allow_network)

    assert result == 0
    _assert_host_canary_unchanged(tmp_path, host_canary)


@pytest.mark.parametrize("category", C_ISOLATED_ONLY_CATEGORIES)
def test_c_isolated_network_escape_attempts_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    if shutil.which("cc") is None:
        pytest.skip("C compiler is not installed")
    workspace, host_canary = _prepare_workspace(tmp_path)
    _configure_escape_env(monkeypatch, host_canary=host_canary, allow_network=False, suffix=f"c-{category}")
    probe = _compile_c_probe(workspace)

    result = _run_in_sandbox(workspace, [str(probe), category], allow_network=False)

    assert result == 0
    _assert_host_canary_unchanged(tmp_path, host_canary)
