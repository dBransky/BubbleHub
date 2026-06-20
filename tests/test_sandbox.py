import platform
import shutil
import subprocess
import os
from pathlib import Path

import pytest

from ageos.native import NativeScheduler
from ageos.node.client import SchedulerClient


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_ageos_sandbox_binary_exists_when_installed() -> None:
    if shutil.which("ageos-sandbox") is None:
        pytest.skip("ageos-sandbox not installed in test environment")
    result = subprocess.run(
        ["ageos-sandbox", "--", "/bin/true"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_binding_runs_command(tmp_path: Path) -> None:
    result = NativeScheduler().run_sandbox(
        "/bin/true",
        ["/bin/true"],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        isolate_network=False,
    )
    assert result == 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_uses_agent_home_and_non_root_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_AGENT_ID", "agt-test-home")
    monkeypatch.setenv("EXPECTED_HOME", "/home/agt-test-home")

    result = NativeScheduler().run_sandbox(
        "/bin/sh",
        [
            "/bin/sh",
            "-c",
            (
                'test "$(id -u)" != "0" && '
                'test "$(whoami)" = "agt-test-home" && '
                'test "$(id -gn)" = "agt-test-home" && '
                'test "$HOME" = "$EXPECTED_HOME" && '
                'test "$USER" = "agt-test-home" && '
                'test "$AGEOS_WORKSPACE" = "$HOME/workspace" && '
                'test "$PWD" = "$HOME/workspace" && '
                'test "$(pwd)" = "$HOME/workspace" && '
                'cd .. && test "$PWD" = "$HOME" && cd workspace && '
                'test -w "$HOME" && '
                'test -w "$TMPDIR" && '
                'test -w "$HOME/workspace" && '
                'mkdir "$HOME/.openclaw" && '
                'touch "$HOME/.openclaw/openclaw.json"'
            ),
        ],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert result == 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_preserves_agent_home_between_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_AGENT_ID", "agt-persist-home")
    scheduler = NativeScheduler()

    first = scheduler.run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", 'printf "first" > "$HOME/persist.txt"'],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )
    assert first == 0

    second = scheduler.run_sandbox(
        "/bin/sh",
        [
            "/bin/sh",
            "-c",
            'test "$(cat "$HOME/persist.txt")" = "first" && printf "+second" >> "$HOME/persist.txt"',
        ],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert second == 0
    persisted = tmp_path / ".ageos" / "agents" / "agt-persist-home" / "home" / "persist.txt"
    assert persisted.read_text(encoding="utf-8") == "first+second"


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_uses_shared_scheduler_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = Path("/opt/ageos/bin/python")
    if not python.exists():
        pytest.skip("installed AgeOS Python runtime not available")
    state_path = tmp_path / "scheduler.state"
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(state_path))

    result = NativeScheduler().run_sandbox(
        str(python),
        [
            str(python),
            "-I",
            "-c",
            (
                "from ageos.node.client import SchedulerClient; "
                "SchedulerClient.local().mark_model_loaded("
                "'sandbox-model', 'default-instruct', 'llama', 1, 0, 12345, 51000)"
            ),
        ],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert result == 0
    snapshot = SchedulerClient.local().status_snapshot()
    assert any(model["name"] == "sandbox-model" for model in snapshot["models"])


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_denies_ageos_ps_when_env_is_unset(tmp_path: Path) -> None:
    launcher = Path("/usr/local/bin/ageos")
    if not launcher.exists():
        pytest.skip("installed AgeOS launcher not available")

    result = NativeScheduler().run_sandbox(
        "/usr/bin/env",
        ["/usr/bin/env", "-u", "AGEOS_SANDBOX", str(launcher), "ps"],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert result != 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_blocks_installed_ageos_writes(tmp_path: Path) -> None:
    if not Path("/opt/ageos").exists() or not Path("/usr/local/bin/ageos").exists():
        pytest.skip("installed AgeOS runtime not available")
    result = NativeScheduler().run_sandbox(
        "/bin/sh",
        [
            "/bin/sh",
            "-c",
            "touch /opt/ageos/.ageos-denied 2>/dev/null && exit 10; "
            "printf x >> /usr/local/bin/ageos 2>/dev/null && exit 11; "
            "exit 0",
        ],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )
    assert result == 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_allows_pnpm_managed_node_readonly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pnpm_home = Path.home() / ".local" / "share" / "pnpm"
    node = pnpm_home / "bin" / "node"
    if not node.exists():
        pytest.skip("pnpm-managed node not available")
    monkeypatch.setenv("PNPM_HOME", str(pnpm_home))
    monkeypatch.setenv("PATH", f"{pnpm_home / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")

    result = NativeScheduler().run_sandbox(
        "/usr/bin/env",
        ["/usr/bin/env", "node", "-e", "require('node:crypto').randomBytes(1); console.log(process.version)"],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert result == 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_rejects_protected_writable_root() -> None:
    result = NativeScheduler().run_sandbox(
        "/bin/true",
        ["/bin/true"],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir="/",
        root_dir="/",
        isolate_network=False,
    )
    assert result != 0


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_strips_pythonpath_for_ageos_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = Path("/usr/local/bin/ageos")
    if not launcher.exists():
        pytest.skip("installed AgeOS launcher not available")
    malicious = tmp_path / "ageos"
    malicious.mkdir()
    (malicious / "__init__.py").write_text("raise RuntimeError('shadowed ageos import')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    result = NativeScheduler().run_sandbox(
        str(launcher),
        [str(launcher), "--version"],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert result == 0
