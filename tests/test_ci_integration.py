from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import pytest

from bubblehub.native import NativeScheduler

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_NODE_VERSION = "22.19.0"
OPENCLAW_PNPM_VERSION = "11.2.2"
OPENCLAW_NPM_SPEC = "openclaw@2026.6.11"


def _integration_enabled() -> bool:
    return os.environ.get("BUBBLEHUB_RUN_INTEGRATION") == "1"


def _integration_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("BUBBLEHUB_API_BASE_URL", None)
    cache = env.get("BUBBLEHUB_CACHE", str(tmp_path / "bubblehub-cache"))
    env["BUBBLEHUB_CACHE"] = cache
    env["BUBBLEHUB_MODELS_CONFIG"] = env.get("BUBBLEHUB_MODELS_CONFIG", f"{cache}/ci-models.yaml")
    env["HOME"] = str(tmp_path / "home")
    env["BUBBLEHUB_SKIP_MODEL_SETUP"] = "1"
    env.setdefault(
        "BUBBLEHUB_INTEGRATION_WORKSPACE_DIR",
        str(Path(cache) / "integration-workspaces"),
    )
    env.setdefault("BUBBLEHUB_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env.setdefault("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    env.setdefault("BUBBLEHUB_LLAMA_CTX_SIZE", "512")
    env.setdefault("BUBBLEHUB_MAX_OUTPUT_TOKENS", "32")
    env.setdefault("BUBBLEHUB_VALIDATE_MODEL_CACHE", "1")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    Path(cache).mkdir(parents=True, exist_ok=True)
    return env


@pytest.fixture
def integration_workspace_factory(integration_env: dict[str, str]) -> Iterator[Callable[[Path, str], Path]]:
    roots: list[Path] = []

    def make_root(tmp_path: Path, name: str) -> Path:
        workspace_dir = integration_env.get("BUBBLEHUB_INTEGRATION_WORKSPACE_DIR")
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
    env = _integration_env(tmp_path_factory.mktemp("bubblehub-integration"))
    _run(["bash", str(ROOT / "scripts/ci/write-ci-model-config.sh")], env=env, timeout=30)
    _run(
        ["bubble", "prompt", "--text", "Reply with ok.", "--speciality", "default-instruct"],
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
    assert result.returncode == 0, _failure_output(result.stdout)
    return result


def _failure_output(output: str) -> str:
    parts = [output]
    for pattern in ("bubblehub-llama-native-*.log", "bubblehub-vllm-native-*.log"):
        for log_path in sorted(Path("/tmp").glob(pattern)):
            parts.append(f"\n--- {log_path} ---\n{log_path.read_text(encoding='utf-8', errors='replace')}")
    return "".join(parts)


def _require_integration_runtime() -> None:
    if not _integration_enabled():
        pytest.skip("set BUBBLEHUB_RUN_INTEGRATION=1 to run real local-inference integration tests")
    for binary in ("bubble", "llama-server"):
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} is not installed")


def _copy_openclaw_workspace(source_root: Path, workspace_root: Path) -> Path:
    if not source_root.is_dir():
        pytest.skip("OpenClaw checkout is not prepared")
    shutil.copytree(
        source_root,
        workspace_root,
        ignore=shutil.ignore_patterns(
            ".bubblehub",
            ".bubblehub-toolchain",
            ".pnpm-store",
            "node_modules",
        ),
    )
    return workspace_root


def _copy_mcp_agent_workspace(workspace_root: Path) -> Path:
    shutil.copytree(
        ROOT / "examples" / "mcp_agent",
        workspace_root,
        ignore=shutil.ignore_patterns("__pycache__", ".bubblehub"),
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
    state_root = openclaw_root / ".bubblehub"
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
        "bubble",
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
    rootfs = Path(env.get("BUBBLEHUB_ROOTFS_DIR", "/opt/bubblehub/rootfs/ubuntu-26.04"))
    if not rootfs.is_dir():
        pytest.skip("BubbleHub Ubuntu rootfs is not installed")
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
        "bubble",
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


def _proxy_env(env: dict[str, str]) -> dict[str, str]:
    proxy_env = env.copy()
    proxy_env["BUBBLEHUB_LOG_LEVEL"] = "info"
    return proxy_env


def _apply_access_policy_for_url(env: dict[str, str], agent_id: str, url: str, *, method: str, policy: str) -> None:
    parsed = urlparse(url)
    old_env = os.environ.copy()
    try:
        os.environ.update(env)
        NativeScheduler().apply_access_policy(
            agent_id,
            kind="http",
            subject=parsed.hostname or "",
            method=method,
            path=parsed.path or "/",
            policy=policy,
        )
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def _assert_proxy_denied_output(output: str, method: str, url: str) -> None:
    assert "BubbleHub proxy denied the request" in output
    assert f"http proxy denied:method={method} url={url}" in output


def _assert_proxy_log_prefix(output: str, method: str, url_prefix: str) -> None:
    assert "BubbleHub proxy denied the request" in output or "403" in output
    assert f"http proxy denied:method={method} url={url_prefix}" in output


def _run_mcp_agent(
    root_dir: Path,
    *,
    binary: str,
    env: dict[str, str],
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "bubble",
        "run",
        "--memory",
        "4G",
        "--root-dir",
        str(root_dir),
        "--binary",
        binary,
    ]
    if extra_args:
        command.extend(["--", *extra_args])
    return _run(command, env=_proxy_env(env), timeout=240)


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"host mcp ok")

    def do_POST(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"host mcp ok"}]}}')

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _mcp_http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def _openclaw_setup_command(openclaw_root: Path) -> list[str]:
    return _openclaw_shell_command(
        openclaw_root,
        [
            "setup",
            "--non-interactive",
            "--accept-risk",
            "--flow",
            "quickstart",
            "--mode",
            "local",
            "--auth-choice",
            "skip",
            "--no-install-daemon",
            "--skip-channels",
            "--skip-skills",
            "--skip-search",
            "--skip-health",
            "--skip-ui",
        ],
        allow_network=True,
    )


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
        "bubble",
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
            'TOOLCHAIN="$BUBBLEHUB_WORKSPACE/.bubblehub-toolchain"',
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
            'TOOLCHAIN="$BUBBLEHUB_WORKSPACE/.bubblehub-toolchain"',
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
            f"npm install -g {OPENCLAW_NPM_SPEC}",
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
            "bubblehub-local",
            "--custom-model-id",
            "default-instruct",
            "--custom-provider-id",
            "bubblehub-ci",
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

    assert "BubbleHub basic agent starting" in result.stdout
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
    persisted = root_dir / ".bubblehub" / "agents" / agent_id / "home" / "test.txt"
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
    assert not (root_dir / ".bubblehub" / "agents" / first_agent_id).exists()
    assert (root_dir / ".bubblehub" / "agents" / second_agent_id / "home" / "test.txt").exists()


def test_ubuntu_rootfs_overlay_environment_and_private_copyup(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    rootfs = _rootfs_path(integration_env)
    root_dir = integration_workspace_factory(tmp_path, "ubuntu-overfs")
    marker = rootfs / "etc" / "bubblehub-overfs-integration"

    first = _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script=(
            "set -eu; "
            ". /etc/os-release; "
            'test "$VERSION_ID" = "26.04"; '
            'test "$BUBBLEHUB_ROOTFS_RELEASE" = "ubuntu-26.04"; '
            'test "$BUBBLEHUB_WORKSPACE" = "$HOME/workspace"; '
            "printf private > /etc/bubblehub-overfs-integration; "
            "printf 'rootfs=%s\\n' \"$PRETTY_NAME\""
        ),
    )
    _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script='set -eu; test "$(cat /etc/bubblehub-overfs-integration)" = private',
    )
    agent_id = (root_dir / ".bubblehub" / "current-agent").read_text(encoding="utf-8").strip()
    upper_file = root_dir / ".bubblehub" / "agents" / agent_id / "overlay" / "upper" / "etc" / "bubblehub-overfs-integration"

    assert (root_dir / ".bubblehub" / "current-agent").read_text(encoding="utf-8").strip() == agent_id
    assert "Ubuntu 26.04" in first.stdout
    assert upper_file.read_text(encoding="utf-8") == "private"
    assert not marker.exists()

    _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script="set -eu; test ! -e /etc/bubblehub-overfs-integration",
        force_new_sandbox=True,
    )
    reset_agent_id = (root_dir / ".bubblehub" / "current-agent").read_text(encoding="utf-8").strip()
    assert reset_agent_id != agent_id


def test_bubblehub_cli_runs_inside_ubuntu_rootfs_sandbox(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = integration_workspace_factory(tmp_path, "nested-bubblehub-cli")

    result = _run_rootfs_shell(
        root_dir,
        env=integration_env,
        script=(
            "set -eu; "
            'help_output="$(bubble --help)"; '
            "printf '%s\\n' \"$help_output\"; "
            "printf '%s\\n' \"$help_output\" | grep -q 'BubbleHub local agent runtime'; "
            "bubble --version | grep -q '^bubble '"
        ),
    )

    assert "BubbleHub local agent runtime" in result.stdout


def test_internal_tool_web_access_is_denied_by_proxy(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = integration_workspace_factory(tmp_path, "proxy-tool-web")
    tool = root_dir / "node_modules" / ".bin" / "internal-web-tool"
    tool.parent.mkdir(parents=True)
    target_url = "http://example.com/internal-tool-web"
    tool.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"status=$(curl -sS -o \"$TMPDIR/tool-body\" -w '%{{http_code}}' --max-time 5 {target_url})\n"
        'test "$status" = 403\n'
        'cat "$TMPDIR/tool-body"\n',
        encoding="utf-8",
    )
    tool.chmod(0o755)

    result = _run(
        [
            "bubble",
            "run",
            "--memory",
            "4G",
            "--root-dir",
            str(root_dir),
            "--binary",
            "internal-web-tool",
        ],
        env=_proxy_env(integration_env),
        timeout=240,
    )

    _assert_proxy_denied_output(result.stdout, "GET", target_url)


def test_agent_http_mcp_host_access_is_denied_by_proxy(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    with _mcp_http_server() as target_url:
        root_dir = integration_workspace_factory(tmp_path, "proxy-mcp-http")
        result = _run_rootfs_shell(
            root_dir,
            env=_proxy_env(integration_env),
            script=(
                "set -eu; "
                f"status=$(curl --noproxy '' -sS -o \"$TMPDIR/mcp-body\" -w '%{{http_code}}' --max-time 5 {target_url}); "
                'test "$status" = 403; '
                'cat "$TMPDIR/mcp-body"'
            ),
        )

    _assert_proxy_denied_output(result.stdout, "GET", target_url)


MCP_AGENT_BINARIES = ["./simple_agent.py", "./mcp_agent.py"]


@pytest.mark.parametrize("agent_binary", MCP_AGENT_BINARIES)
def test_mcp_agent_stdio_tool_web_search_is_denied_by_proxy(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
    agent_binary: str,
) -> None:
    slug = agent_binary.replace("./", "").replace(".", "-")
    root_dir = _copy_mcp_agent_workspace(integration_workspace_factory(tmp_path, f"mcp-stdio-{slug}"))
    result = _run_mcp_agent(root_dir, binary=agent_binary, env=integration_env)

    assert "mcp_stdio_result: search_status=403" in result.stdout
    _assert_proxy_log_prefix(result.stdout, "GET", "http://searx.tiekoetter.com/search?")


@pytest.mark.parametrize("agent_binary", MCP_AGENT_BINARIES)
def test_mcp_agent_http_host_mcp_is_denied_by_proxy(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
    agent_binary: str,
) -> None:
    with _mcp_http_server() as target_url:
        slug = agent_binary.replace("./", "").replace(".", "-")
        root_dir = _copy_mcp_agent_workspace(integration_workspace_factory(tmp_path, f"mcp-http-{slug}"))
        result = _run_mcp_agent(
            root_dir,
            binary=agent_binary,
            env=integration_env,
            extra_args=["--http-url", target_url],
        )

    assert "mcp_http_result: status=403" in result.stdout
    _assert_proxy_denied_output(result.stdout, "POST", target_url)


def test_mcp_agent_http_host_mcp_uses_persisted_access_approval(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    with _mcp_http_server() as target_url:
        root_dir = _copy_mcp_agent_workspace(integration_workspace_factory(tmp_path, "mcp-http-approved"))
        _run_mcp_agent(
            root_dir,
            binary="./simple_agent.py",
            env=integration_env,
            extra_args=["--addition", "--a", "1", "--b", "2"],
        )
        agent_id = (root_dir / ".bubblehub" / "current-agent").read_text(encoding="utf-8").strip()
        _apply_access_policy_for_url(integration_env, agent_id, target_url, method="POST", policy="always")

        result = _run_mcp_agent(
            root_dir,
            binary="./simple_agent.py",
            env=integration_env,
            extra_args=["--http-url", target_url],
        )

    assert "Persistent sandbox found: reusing" in result.stdout
    assert "mcp_http_result: status=200" in result.stdout
    assert "host mcp ok" in result.stdout
    assert "http proxy approved:method=POST" in result.stdout


def test_mcp_agent_direct_requests_is_denied_by_proxy(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    target_url = "http://example.com/direct-requests"
    root_dir = _copy_mcp_agent_workspace(integration_workspace_factory(tmp_path, "mcp-direct-proxy"))
    result = _run_mcp_agent(
        root_dir,
        binary="./simple_agent.py",
        env=integration_env,
        extra_args=["--direct-url", target_url],
    )

    assert "direct_http_result: status=403" in result.stdout
    _assert_proxy_denied_output(result.stdout, "GET", target_url)


def test_mcp_agent_addition_stdio_local(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    root_dir = _copy_mcp_agent_workspace(integration_workspace_factory(tmp_path, "mcp-addition-local"))
    result = _run_mcp_agent(
        root_dir,
        binary="./simple_agent.py",
        env=integration_env,
        extra_args=["--addition", "--a", "123", "--b", "456"],
    )

    assert result.returncode == 0
    assert "mcp_addition_result: 579" in result.stdout
    assert "http proxy denied" not in result.stdout


def test_agent_curl_web_access_is_denied_by_proxy(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    target_url = "http://example.com/curl-web"
    root_dir = integration_workspace_factory(tmp_path, "proxy-curl-web")
    result = _run_rootfs_shell(
        root_dir,
        env=_proxy_env(integration_env),
        script=(
            "set -eu; "
            f"status=$(curl -sS -o \"$TMPDIR/curl-body\" -w '%{{http_code}}' --max-time 5 {target_url}); "
            'test "$status" = 403; '
            'cat "$TMPDIR/curl-body"'
        ),
    )

    _assert_proxy_denied_output(result.stdout, "GET", target_url)


def test_tenant_unset_proxy_env_cannot_bypass_network_in_rootfs(
    integration_env: dict[str, str],
    tmp_path: Path,
    integration_workspace_factory: Callable[[Path, str], Path],
) -> None:
    target_url = "http://example.com/tenant-rootfs-bypass"
    root_dir = integration_workspace_factory(tmp_path, "proxy-bypass-escape")
    result = _run_rootfs_shell(
        root_dir,
        env=_proxy_env(integration_env),
        script=(
            "set -eu; "
            'test -n "$HTTP_PROXY"; '
            "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy BUBBLEHUB_HTTP_PROXY_PORT NO_PROXY no_proxy; "
            f"if curl -fsS --max-time 3 {target_url} >/dev/null 2>&1; then exit 9; fi; "
            f"if curl -fsS --noproxy '*' --max-time 3 {target_url} >/dev/null 2>&1; then exit 10; fi; "
            "python3 - <<'PY'\n"
            "import os\n"
            "import urllib.request\n"
            "for key in list(os.environ):\n"
            "    if key.startswith('BUBBLEHUB_') or 'proxy' in key.lower():\n"
            "        os.environ.pop(key, None)\n"
            "try:\n"
            f"    urllib.request.urlopen('{target_url}', timeout=3).read()\n"
            "except Exception:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit(11)\n"
            "print('tenant_rootfs_proxy_bypass_blocked')\n"
            "PY"
        ),
    )

    assert "tenant_rootfs_proxy_bypass_blocked" in result.stdout
    assert "http proxy denied" not in result.stdout


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
            f"npm install -g {OPENCLAW_NPM_SPEC}",
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
    state_root = openclaw_root / ".bubblehub"
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
    state_root = openclaw_root / ".bubblehub"
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
    state_root = openclaw_root / ".bubblehub"
    _run(_openclaw_onboard_command(openclaw_root), env=integration_env, timeout=240)

    first = _run(_openclaw_command(openclaw_root, ["config", "validate", "--json"]), env=integration_env, timeout=240)
    config_path = state_root / "agents" / agent_id / "home" / ".openclaw" / "openclaw.json"
    assert config_path.exists()

    second = _run(_openclaw_command(openclaw_root, ["config", "validate", "--json"]), env=integration_env, timeout=240)

    assert "Persistent sandbox found: reusing" in first.stdout
    assert f"Persistent sandbox found: reusing {agent_id}" in second.stdout
    assert (state_root / "current-agent").read_text(encoding="utf-8").strip() == agent_id
    assert config_path.exists()


def test_openclaw_persistent_sandbox_uses_access_manifest_for_web_probe(
    integration_env: dict[str, str],
    openclaw_sandbox_state: tuple[Path, str],
) -> None:
    openclaw_root, agent_id = openclaw_sandbox_state
    _run(_openclaw_onboard_command(openclaw_root), env=integration_env, timeout=240)
    _run(_openclaw_command(openclaw_root, ["config", "validate", "--json"]), env=integration_env, timeout=240)

    with _mcp_http_server() as target_url:
        _apply_access_policy_for_url(integration_env, agent_id, target_url, method="GET", policy="always")
        result = _run(
            _openclaw_shell_script_command(
                openclaw_root,
                _openclaw_toolchain_env_script() + "\n" + f"curl --noproxy '' -sS --max-time 5 {shlex.quote(target_url)}",
            ),
            env=_proxy_env(integration_env),
            timeout=240,
        )

    assert f"Persistent sandbox found: reusing {agent_id}" in result.stdout
    assert "host mcp ok" in result.stdout
    assert "http proxy approved:method=GET" in result.stdout
