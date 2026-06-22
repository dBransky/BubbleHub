from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_NODE_VERSION = "22.19.0"
OPENCLAW_PNPM_VERSION = "11.2.2"


def _integration_enabled() -> bool:
    return os.environ.get("AGEOS_RUN_INTEGRATION") == "1"


def _integration_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("AGEOS_API_BASE_URL", None)
    env.setdefault("AGEOS_CACHE", str(tmp_path / "ageos-cache"))
    env.setdefault(
        "AGEOS_INTEGRATION_WORKSPACE_DIR",
        str(Path(env["AGEOS_CACHE"]) / "integration-workspaces"),
    )
    env.setdefault("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env.setdefault("AGEOS_LLAMA_CTX_SIZE", "512")
    env.setdefault("AGEOS_MAX_OUTPUT_TOKENS", "32")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    return env


@pytest.fixture
def integration_workspace_factory(integration_env: dict[str, str]) -> Iterator[Callable[[Path, str], Path]]:
    roots: list[Path] = []

    def make_root(tmp_path: Path, name: str) -> Path:
        workspace_dir = integration_env.get("AGEOS_INTEGRATION_WORKSPACE_DIR")
        if not workspace_dir:
            return tmp_path / name
        root = Path(workspace_dir) / f"{tmp_path.name}-{name}-{uuid.uuid4().hex[:10]}"
        root.parent.mkdir(parents=True, exist_ok=True)
        roots.append(root)
        return root

    try:
        yield make_root
    finally:
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def integration_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    _require_integration_runtime()
    env = _integration_env(tmp_path_factory.mktemp("ageos-integration"))
    _run(
        ["ageos", "prompt", "--text", "Reply with ok.", "--speciality", "default-instruct"],
        env=env,
        timeout=180,
    )
    return env


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    return result


def _require_integration_runtime() -> None:
    if not _integration_enabled():
        pytest.skip("set AGEOS_RUN_INTEGRATION=1 to run real local-inference integration tests")
    for binary in ("ageos", "llama-server"):
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} is not installed")


def _copy_openclaw_workspace(source_root: Path, workspace_root: Path) -> Path:
    if not source_root.is_dir():
        pytest.skip("OpenClaw checkout is not prepared")
    shutil.copytree(
        source_root,
        workspace_root,
        ignore=shutil.ignore_patterns(
            ".ageos",
            ".ageos-toolchain",
            ".pnpm-store",
            "node_modules",
        ),
    )
    return workspace_root


@pytest.fixture
def openclaw_sandbox_state(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> Iterator[tuple[Path, str]]:
    openclaw_root = _copy_openclaw_workspace(
        ROOT / "examples" / "openclaw" / "openclaw",
        integration_workspace_factory(tmp_path, "openclaw"),
    )
    state_root = openclaw_root / ".ageos"
    agents_root = state_root / "agents"
    marker = state_root / "current-agent"
    agent_id = f"agt-ci-{uuid.uuid4().hex[:10]}"
    agent_dir = agents_root / agent_id

    try:
        (agent_dir / "home").mkdir(parents=True)
        marker.write_text(f"{agent_id}\n", encoding="utf-8")
        _run(_openclaw_install_toolchain_command(openclaw_root), env=integration_env, timeout=600)
        _run(_openclaw_pnpm_install_command(openclaw_root), env=integration_env, timeout=600)
        yield openclaw_root, agent_id
    finally:
        shutil.rmtree(agent_dir, ignore_errors=True)


def _agent_id_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("agent_id="):
            return line.removeprefix("agent_id=")
    raise AssertionError(f"agent_id line not found in output:\n{output}")


def _run_basic_agent(root_dir: Path, *, env: dict[str, str], force_new_sandbox: bool = False) -> subprocess.CompletedProcess[str]:
    root_dir.mkdir(parents=True, exist_ok=True)
    agent_path = root_dir / "basic_agent.py"
    if not agent_path.exists():
        shutil.copy2(ROOT / "examples" / "basic" / "basic_agent.py", agent_path)
    command = [
        "ageos",
        "run",
        "--memory",
        "4G",
        "--root-dir",
        str(root_dir),
        "--binary",
        "./basic_agent.py",
    ]
    if force_new_sandbox:
        command.insert(2, "--force-new-sandbox")
    return _run(command, env=env, timeout=240)


def _rootfs_path(env: dict[str, str]) -> Path:
    rootfs = Path(env.get("AGEOS_ROOTFS_DIR", "/opt/ageos/rootfs/ubuntu-26.04"))
    if not rootfs.is_dir():
        pytest.skip("AgeOS Ubuntu rootfs is not installed")
    return rootfs


def _run_rootfs_shell(
    root_dir: Path,
    *,
    env: dict[str, str],
    script: str,
    force_new_sandbox: bool = False,
    allow_network: bool = False,
    shell: str = "/bin/sh",
    timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    root_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "ageos",
        "run",
        "--memory",
        "4G",
    ]
    if allow_network:
        command.append("--allow-network")
    command.extend(
        [
            "--root-dir",
            str(root_dir),
            "--binary",
            shell,
            "-c",
            script,
        ]
    )
    if force_new_sandbox:
        command.insert(2, "--force-new-sandbox")
    return _run(command, env=env, timeout=timeout)


def _openclaw_setup_command(openclaw_root: Path) -> list[str]:
    return _openclaw_shell_command(openclaw_root, ["setup"], allow_network=True)


def _openclaw_install_toolchain_command(openclaw_root: Path) -> list[str]:
    return _openclaw_shell_script_command(openclaw_root, _openclaw_toolchain_script(), allow_network=True)


def _openclaw_pnpm_install_command(openclaw_root: Path) -> list[str]:
    return _openclaw_shell_command(
        openclaw_root,
        ["install", "--frozen-lockfile", "--store-dir", ".pnpm-store"],
        allow_network=True,
        use_exec=False,
    )


def _openclaw_shell_command(openclaw_root: Path, args: list[str], *, allow_network: bool = False, use_exec: bool = True) -> list[str]:
    pnpm_args = ["pnpm"]
    if use_exec:
        pnpm_args.append("openclaw")
    pnpm_args.extend(args)
    script = _openclaw_toolchain_env_script() + "\n" + shlex.join(pnpm_args)
    return _openclaw_shell_script_command(openclaw_root, script, allow_network=allow_network)


def _openclaw_shell_script_command(openclaw_root: Path, script: str, *, allow_network: bool = False) -> list[str]:
    command = [
        "ageos",
        "run",
        "--memory",
        "4G",
    ]
    if allow_network:
        command.append("--allow-network")
    command.extend(
        [
            "--root-dir",
            str(openclaw_root),
            "--binary",
            "/bin/sh",
            "-c",
            script,
        ]
    )
    return command


def _openclaw_toolchain_env_script() -> str:
    return "\n".join(
        [
            "set -eu",
            'TOOLCHAIN="$AGEOS_WORKSPACE/.ageos-toolchain"',
            f'NODE_HOME="$TOOLCHAIN/node-v{OPENCLAW_NODE_VERSION}"',
            'NPM_CONFIG_PREFIX="$TOOLCHAIN/npm-global"',
            "export NPM_CONFIG_PREFIX",
            "export CI=true",
            "export PNPM_CONFIG_CONFIRM_MODULES_PURGE=false",
            'export PATH="$NPM_CONFIG_PREFIX/bin:$NODE_HOME/bin:$PATH"',
            "command -v node >/dev/null",
            "command -v npm >/dev/null",
            "command -v pnpm >/dev/null",
            "command -v openclaw >/dev/null",
        ]
    )


def _openclaw_toolchain_script() -> str:
    node_url = f"https://nodejs.org/dist/v{OPENCLAW_NODE_VERSION}/node-v{OPENCLAW_NODE_VERSION}-linux-$node_arch.tar.xz"
    return "\n".join(
        [
            "set -eu",
            'TOOLCHAIN="$AGEOS_WORKSPACE/.ageos-toolchain"',
            f'NODE_HOME="$TOOLCHAIN/node-v{OPENCLAW_NODE_VERSION}"',
            'NPM_CONFIG_PREFIX="$TOOLCHAIN/npm-global"',
            "export NPM_CONFIG_PREFIX",
            "export CI=true",
            "export PNPM_CONFIG_CONFIRM_MODULES_PURGE=false",
            'export PATH="$NPM_CONFIG_PREFIX/bin:$NODE_HOME/bin:$PATH"',
            'mkdir -p "$TOOLCHAIN" "$NPM_CONFIG_PREFIX/bin"',
            'arch="$(uname -m)"',
            'case "$arch" in',
            '  x86_64|amd64) node_arch="x64" ;;',
            '  aarch64|arm64) node_arch="arm64" ;;',
            '  *) echo "unsupported Node.js test architecture: $arch" >&2; exit 1 ;;',
            "esac",
            'if [ ! -x "$NODE_HOME/bin/node" ]; then',
            '  tmp_archive="$TMPDIR/node.tar.xz"',
            f'  curl -fsSL "{node_url}" -o "$tmp_archive"',
            '  rm -rf "$NODE_HOME"',
            '  mkdir -p "$NODE_HOME"',
            '  tar -xJf "$tmp_archive" -C "$NODE_HOME" --strip-components=1',
            "fi",
            "node --version",
            "npm --version",
            "npm install -g openclaw@latest",
            'corepack enable --install-directory "$NPM_CONFIG_PREFIX/bin"',
            f"corepack prepare pnpm@{OPENCLAW_PNPM_VERSION} --activate",
            "pnpm --version",
            "command -v openclaw",
        ]
    )


def _openclaw_command(openclaw_root: Path, args: list[str], *, allow_network: bool = False) -> list[str]:
    return _openclaw_shell_command(openclaw_root, args, allow_network=allow_network)


def _openclaw_onboard_command(openclaw_root: Path) -> list[str]:
    return _openclaw_command(
        openclaw_root,
        [
            "onboard",
            "--non-interactive",
            "--accept-risk",
            "--mode",
            "local",
            "--auth-choice",
            "skip",
            "--custom-base-url",
            "http://127.0.0.1:8000/v1",
            "--custom-api-key",
            "ageos-local",
            "--custom-model-id",
            "default-instruct",
            "--custom-provider-id",
            "ageos-ci",
            "--custom-compatibility",
            "openai",
            "--skip-daemon",
            "--skip-channels",
            "--skip-skills",
            "--skip-search",
            "--skip-health",
            "--skip-ui",
            "--skip-hooks",
            "--json",
        ],
    )


def test_basic_agent_gets_model_response(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    result = _run_basic_agent(integration_workspace_factory(tmp_path, "basic-agent"), env=integration_env)

    assert "AgeOS basic agent starting" in result.stdout
    assert "model_response:" in result.stdout
    marker_index = result.stdout.index("model_response:")
    response = result.stdout[marker_index:].splitlines()[1].strip()
    assert response


def test_basic_agent_home_persists_across_runs(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = integration_workspace_factory(tmp_path, "basic-persistent")
    first = _run_basic_agent(root_dir, env=integration_env)
    second = _run_basic_agent(root_dir, env=integration_env)
    agent_id = _agent_id_from_output(first.stdout)

    assert _agent_id_from_output(second.stdout) == agent_id
    assert "Persistent sandbox found: reusing" in second.stdout
    assert "existing_home_data=<missing>" in first.stdout
    assert "existing_home_data=Hello, world!" in second.stdout
    persisted = root_dir / ".ageos" / "agents" / agent_id / "home" / "test.txt"
    assert persisted.read_text(encoding="utf-8") == "Hello, world!"


def test_basic_agent_force_new_sandbox_starts_with_fresh_home(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = integration_workspace_factory(tmp_path, "basic-force-new")
    first = _run_basic_agent(root_dir, env=integration_env)
    second = _run_basic_agent(root_dir, env=integration_env, force_new_sandbox=True)
    first_agent_id = _agent_id_from_output(first.stdout)
    second_agent_id = _agent_id_from_output(second.stdout)

    assert second_agent_id != first_agent_id
    assert "Persistent sandbox found" not in second.stdout
    assert "existing_home_data=<missing>" in second.stdout
    assert not (root_dir / ".ageos" / "agents" / first_agent_id).exists()
    assert (root_dir / ".ageos" / "agents" / second_agent_id / "home" / "test.txt").exists()


def test_ubuntu_rootfs_overlay_environment_and_private_copyup(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    rootfs = _rootfs_path(integration_env)
    root_dir = integration_workspace_factory(tmp_path, "ubuntu-overfs")
    marker = rootfs / "etc" / "ageos-overfs-integration"

    first = _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script=(
            "set -eu; "
            ". /etc/os-release; "
            'test "$VERSION_ID" = "26.04"; '
            'test "$AGEOS_ROOTFS_RELEASE" = "ubuntu-26.04"; '
            'test "$AGEOS_WORKSPACE" = "$HOME/workspace"; '
            "printf private > /etc/ageos-overfs-integration; "
            "printf 'rootfs=%s\\n' \"$PRETTY_NAME\""
        ),
    )
    _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script='set -eu; test "$(cat /etc/ageos-overfs-integration)" = private',
    )
    agent_id = (root_dir / ".ageos" / "current-agent").read_text(encoding="utf-8").strip()
    upper_file = root_dir / ".ageos" / "agents" / agent_id / "overlay" / "upper" / "etc" / "ageos-overfs-integration"

    assert (root_dir / ".ageos" / "current-agent").read_text(encoding="utf-8").strip() == agent_id
    assert "Ubuntu 26.04" in first.stdout
    assert upper_file.read_text(encoding="utf-8") == "private"
    assert not marker.exists()

    _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script="set -eu; test ! -e /etc/ageos-overfs-integration",
        force_new_sandbox=True,
    )
    reset_agent_id = (root_dir / ".ageos" / "current-agent").read_text(encoding="utf-8").strip()
    assert reset_agent_id != agent_id


def test_ageos_cli_runs_inside_ubuntu_rootfs_sandbox(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = integration_workspace_factory(tmp_path, "nested-ageos-cli")

    result = _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script=(
            "set -eu; "
            "help_output=\"$(ageos --help)\"; "
            "printf '%s\\n' \"$help_output\"; "
            "printf '%s\\n' \"$help_output\" | grep -q 'AgeOS local agent runtime'; "
            "ageos --version | grep -q '^ageos '"
        ),
    )

    assert "AgeOS local agent runtime" in result.stdout


def test_sandbox_installs_openclaw_with_nvm_and_npm(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = integration_workspace_factory(tmp_path, "nvm-openclaw")
    script = "\n".join(
        [
            "set -eu",
            "curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.5/install.sh | bash",
            'export NVM_DIR="$HOME/.nvm"',
            '[ -s "$NVM_DIR/nvm.sh" ]',
            '. "$NVM_DIR/nvm.sh"',
            f"nvm install {OPENCLAW_NODE_VERSION}",
            f"nvm use {OPENCLAW_NODE_VERSION}",
            "node --version",
            "npm --version",
            "npm install -g openclaw@latest",
            "command -v openclaw",
            "openclaw --version >/dev/null",
        ]
    )

    result = _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script=script,
        allow_network=True,
        shell="/bin/bash",
        timeout=600,
    )

    assert "Downloading nvm" in result.stdout
    assert "openclaw" in result.stdout.lower()


def test_openclaw_setup_runs_with_network_allowed_sandbox(integration_env: dict[str, str], openclaw_sandbox_state: tuple[Path, str]) -> None:
    openclaw_root, agent_id = openclaw_sandbox_state
    state_root = openclaw_root / ".ageos"
    config_path = state_root / "agents" / agent_id / "home" / ".openclaw" / "openclaw.json"

    result = _run(
        _openclaw_setup_command(openclaw_root),
        env=integration_env,
        timeout=240,
    )

    assert "Setup complete" in result.stdout or config_path.exists()
    assert config_path.exists(), f"OpenClaw config was not created under {config_path.parent}"


def test_openclaw_onboard_configures_local_inference(integration_env: dict[str, str], openclaw_sandbox_state: tuple[Path, str]) -> None:
    openclaw_root, agent_id = openclaw_sandbox_state
    state_root = openclaw_root / ".ageos"
    config_path = state_root / "agents" / agent_id / "home" / ".openclaw" / "openclaw.json"

    _run(
        _openclaw_onboard_command(openclaw_root),
        env=integration_env,
        timeout=240,
    )

    assert config_path.exists(), f"OpenClaw config was not created under {config_path.parent}"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = config.get("gateway", {})
    wizard = config.get("wizard", {})
    assert gateway.get("mode") == "local"
    assert gateway.get("auth", {}).get("mode") == "token"
    assert wizard.get("lastRunCommand") == "onboard"
    assert wizard.get("lastRunMode") == "local"


def test_openclaw_reuses_configured_sandbox_without_reset(integration_env: dict[str, str], openclaw_sandbox_state: tuple[Path, str]) -> None:
    openclaw_root, agent_id = openclaw_sandbox_state
    state_root = openclaw_root / ".ageos"
    _run(_openclaw_onboard_command(openclaw_root), env=integration_env, timeout=240)

    first = _run(_openclaw_command(openclaw_root, ["config", "validate", "--json"]), env=integration_env, timeout=240)
    config_path = state_root / "agents" / agent_id / "home" / ".openclaw" / "openclaw.json"
    assert config_path.exists()

    second = _run(_openclaw_command(openclaw_root, ["config", "validate", "--json"]), env=integration_env, timeout=240)

    assert "Persistent sandbox found: reusing" in first.stdout
    assert f"Persistent sandbox found: reusing {agent_id}" in second.stdout
    assert (state_root / "current-agent").read_text(encoding="utf-8").strip() == agent_id
    assert config_path.exists()
