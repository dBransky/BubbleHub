import platform
import shutil
import subprocess
import os
from pathlib import Path

import pytest

from ageos.native import NativeScheduler
from ageos.node.client import SchedulerClient


def _installed_rootfs() -> Path:
    rootfs = Path(os.environ.get("AGEOS_ROOTFS_DIR", "/opt/ageos/rootfs/ubuntu-26.04"))
    if not rootfs.is_dir() or not (rootfs / ".ageos-rootfs.json").is_file():
        pytest.skip("AgeOS Ubuntu rootfs is not installed")
    return rootfs


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
                'test -f "$HOME/.bashrc" && '
                'test -f "$HOME/.profile" && '
                'printf "\\n# test append\\n" >> "$HOME/.bashrc" && '
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
def test_native_sandbox_uses_ubuntu_rootfs_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rootfs = _installed_rootfs()
    if os.geteuid() != 0:
        pytest.skip("rootfs overlay sandbox test requires privileged mount support")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    upper = workspace / ".ageos" / "agents" / "agt-rootfs" / "overlay" / "upper"
    work = workspace / ".ageos" / "agents" / "agt-rootfs" / "overlay" / "work"
    monkeypatch.setenv("AGEOS_AGENT_ID", "agt-rootfs")
    scheduler = NativeScheduler()

    first = scheduler.run_sandbox(
        "/bin/sh",
        [
            "/bin/sh",
            "-c",
            (
                'grep -q \'VERSION_ID="26.04"\' /etc/os-release && '
                'test "$AGEOS_ROOTFS_RELEASE" = "ubuntu-26.04" && '
                'test "$PWD" = "$HOME/workspace" && '
                'printf "private" > /etc/ageos-overfs-test'
            ),
        ],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(workspace),
        root_dir=str(workspace),
        rootfs_dir=str(rootfs),
        overlay_upper_dir=str(upper),
        overlay_work_dir=str(work),
        isolate_network=False,
    )
    if first == 126:
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("overlayfs sandbox mount is not permitted in this environment")
    assert first == 0

    second = scheduler.run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", 'test "$(cat /etc/ageos-overfs-test)" = "private"'],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(workspace),
        root_dir=str(workspace),
        rootfs_dir=str(rootfs),
        overlay_upper_dir=str(upper),
        overlay_work_dir=str(work),
        isolate_network=False,
    )

    assert second == 0
    assert not (rootfs / "etc" / "ageos-overfs-test").exists()
    assert (upper / "etc" / "ageos-overfs-test").read_text(encoding="utf-8") == "private"


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
def test_native_sandbox_runs_workspace_managed_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_AGENT_ID", "agt-workspace-tool")
    tool = tmp_path / "node_modules" / ".bin" / "workspace-tool"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\nprintf workspace-tool-ok > \"$HOME/workspace-tool.out\"\n", encoding="utf-8")
    tool.chmod(0o755)

    result = NativeScheduler().run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", "./node_modules/.bin/workspace-tool"],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=False,
    )

    assert result == 0
    output = tmp_path / ".ageos" / "agents" / "agt-workspace-tool" / "home" / "workspace-tool.out"
    assert output.read_text(encoding="utf-8") == "workspace-tool-ok"


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
    host_log = Path("/tmp/ageos-host-log-leak-test.log")
    host_log.unlink(missing_ok=True)
    host_log.write_text("host-marker\n", encoding="utf-8")
    monkeypatch.setenv("AGEOS_LOG_FILE", str(host_log))

    try:
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
        assert host_log.read_text(encoding="utf-8") == "host-marker\n"
    finally:
        host_log.unlink(missing_ok=True)


@pytest.mark.skipif(platform.system() != "Linux", reason="sandbox is Linux-only")
def test_native_sandbox_allows_agent_local_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = Path("/opt/ageos/bin/python")
    if not python.exists():
        python = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip("AgeOS Python runtime not available")
    host_log = Path("/tmp/ageos-host-only-ageos.log")
    host_log.unlink(missing_ok=True)
    monkeypatch.setenv("AGEOS_LOG_FILE", str(host_log))

    command = (
        "import os, sys\n"
        "sys.argv = ['ageos', 'poc', '--log-file', os.path.join(os.environ['AGEOS_WORKSPACE'], 'ageos.log'), "
        "'--log-level', 'debug', '-h']\n"
        "from ageos.cli.main import run_cli\n"
        "run_cli()\n"
    )

    try:
        result = NativeScheduler().run_sandbox(
            str(python),
            [str(python), "-I", "-c", command],
            resource_niceness=0,
            memory_max=2 * 1024 * 1024 * 1024,
            cpu_percent=0,
            workdir=str(tmp_path),
            root_dir=str(tmp_path),
            isolate_network=False,
        )

        assert result == 0
        log_path = tmp_path / "ageos.log"
        assert log_path.is_file()
        assert "ageos cli initialized" in log_path.read_text(encoding="utf-8")
        assert not host_log.exists()
    finally:
        host_log.unlink(missing_ok=True)
