from __future__ import annotations

import errno
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _require_bubblehub_cli() -> None:
    if shutil.which("bubblehub") is None:
        pytest.skip("bubblehub is not installed")


def _cli_e2e_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("BUBBLEHUB_API_BASE_URL", "http://127.0.0.1:8000")
    env.setdefault("BUBBLEHUB_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env["BUBBLEHUB_STATE_DIR"] = str(tmp_path / "bubblehub-state")
    env["BUBBLEHUB_PYTHONPATH"] = str(ROOT)
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    return env


def _cli_e2e_env_with_inference(tmp_path: Path) -> dict[str, str]:
    from bubblehub.inference import ensure_inference_endpoint

    env = _cli_e2e_env(tmp_path)
    env.pop("BUBBLEHUB_API_BASE_URL", None)
    endpoint = ensure_inference_endpoint()
    env["BUBBLEHUB_API_BASE_URL"] = endpoint
    env.setdefault("BUBBLEHUB_LLAMA_CTX_SIZE", os.environ.get("BUBBLEHUB_LLAMA_CTX_SIZE", "512"))
    env.setdefault("BUBBLEHUB_MAX_OUTPUT_TOKENS", os.environ.get("BUBBLEHUB_MAX_OUTPUT_TOKENS", "32"))
    return env


def _pty_shell_root_dir(env: dict[str, str]) -> Path:
    state_dir = Path(env.get("BUBBLEHUB_STATE_DIR", ROOT / ".bubblehub-state"))
    default_root = state_dir.parent / "workspace"
    integration_workspace_dir = env.get("BUBBLEHUB_INTEGRATION_WORKSPACE_DIR")
    if not integration_workspace_dir:
        return default_root
    workspace_name = f"{state_dir.parent.parent.name}-{state_dir.parent.name}-workspace"
    return Path(integration_workspace_dir) / workspace_name


_SANDBOX_LLM_PROMPT = "what is two plus two? reply with one digit only"
_SANDBOX_LLM_EXPECTED = "4"
_PTY_FAILURE_MARKERS = (
    "Traceback (most recent call last)",
    "502 Server Error",
    "sandbox inference request failed",
    "RuntimeError:",
    "failed to create filesystem sandbox",
    "failed to mount BubbleHub overfs",
)


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"bubblehub-cli-policy-ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _host_http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/cli-policy"
    finally:
        server.shutdown()
        server.server_close()


def _run_cli_e2e(
    command: list[str],
    *,
    tmp_path: Path,
    stdin: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    _require_bubblehub_cli()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_cli_e2e_env(tmp_path),
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    return result


def _run_cli(
    command: list[str],
    *,
    tmp_path: Path,
    stdin: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    _require_bubblehub_cli()
    return subprocess.run(
        command,
        cwd=ROOT,
        env=_cli_e2e_env(tmp_path),
        input=stdin,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _run_policy_probe(root_dir: Path, tmp_path: Path, target_url: str, expected_status: int) -> subprocess.CompletedProcess[str]:
    script = (
        "set -eu; "
        "body=bubblehub-run-policy-body; "
        "status=$(curl --noproxy '' -sS -o \"$body\" "
        f"-w '%{{http_code}}' --max-time 15 {target_url}); "
        f'printf \'status=%s\\n\' "$status"; cat "$body"; test "$status" = {expected_status}'
    )
    return _run_cli(
        ["bubblehub", "run", "--root-dir", str(root_dir), "--binary", "/bin/sh", "--", "-c", script],
        tmp_path=tmp_path,
        timeout=120,
    )


def _apply_manifest_policy(tmp_path: Path, agent_id: str, policy: str) -> None:
    script = f"""
from bubblehub.native import NativeScheduler
NativeScheduler().apply_access_policy(
    {agent_id!r},
    kind="http",
    subject="api.example.com",
    method="GET",
    path="/",
    policy={policy!r},
)
"""
    result = _run_cli([sys.executable, "-c", script], tmp_path=tmp_path)
    assert result.returncode == 0, result.stdout


def _run_pty_shell_access_prompt(command: list[str], env: dict[str, str], target_url: str) -> str:
    pid, master_fd = pty.fork()
    output = ""
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(command[0], command, env)

    try:
        output = _read_pty_until(master_fd, pid, "[BubbleHub]", timeout=120)
        curl_command = "curl --noproxy '' -sS -o /tmp/bubblehub-cli-policy-body " f"-w 'status=%{{http_code}}\\n' --max-time 15 {target_url}\r"
        os.write(master_fd, curl_command.encode("utf-8"))
        output += _read_pty_until(master_fd, pid, "ask every time (approve now)", timeout=30)
        os.write(master_fd, b"\x1b[B\x1b[B\r")
        output += _read_pty_until(master_fd, pid, "status=200", timeout=30)
        repeated_curl_command = (
            "curl --noproxy '' -sS -o /tmp/bubblehub-cli-policy-body-repeat " f"-w 'status2=%{{http_code}}\\n' --max-time 15 {target_url}\r"
        )
        os.write(master_fd, repeated_curl_command.encode("utf-8"))
        output += _read_pty_until(master_fd, pid, "ask every time (approve now)", timeout=30)
        os.write(master_fd, b"\x1b[B\x1b[B\r")
        output += _read_pty_until(master_fd, pid, "status2=200", timeout=30)
        os.write(master_fd, b"exit\r")
        _wait_pty_child(pid, timeout=30)
        return output
    finally:
        os.close(master_fd)
        _kill_pty_child(pid)


def _run_pty_shell(
    commands: list[str],
    env: dict[str, str],
    contains_strs: list[str],
    *,
    response_timeout: int = 120,
) -> str:
    _require_bubblehub_cli()
    root_dir = _pty_shell_root_dir(env)
    root_dir.mkdir(parents=True, exist_ok=True)
    shell_command = ["bubblehub", "shell", "--root-dir", str(root_dir), "--force-new-sandbox"]

    pid, master_fd = pty.fork()
    output = ""
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(shell_command[0], shell_command, env)

    try:
        output = _read_pty_until(master_fd, pid, "[BubbleHub]", timeout=120)
        for typed_command, contains_str in zip(commands, contains_strs):
            line = typed_command if typed_command.endswith("\r") else f"{typed_command}\r"
            step_start = len(output)
            os.write(master_fd, line.encode("utf-8"))
            output += _read_pty_until(
                master_fd,
                pid,
                contains_str,
                timeout=response_timeout,
                forbid=_PTY_FAILURE_MARKERS,
                match_from=step_start,
                initial=output,
            )
            step_output = output[step_start:]
            assert contains_str in step_output, f"expected {contains_str} in response, but got {step_output}"
        _exit_pty_shell(master_fd, pid, timeout=30)
        return output
    finally:
        os.close(master_fd)
        _kill_pty_child(pid)


def _run_pty_shell_google_denial_prompt(command: list[str], env: dict[str, str]) -> str:
    pid, master_fd = pty.fork()
    output = ""
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(command[0], command, env)

    try:
        output = _read_pty_until(master_fd, pid, "[BubbleHub]", timeout=120)
        os.write(master_fd, b"curl google.com\r")
        output += _read_pty_until(master_fd, pid, "ask every time (approve now)", timeout=30)
        os.write(master_fd, b"\x1b[B\r")
        output += _read_pty_until(master_fd, pid, "BubbleHub proxy denied the request", timeout=30)
        os.write(master_fd, b"exit\r")
        _wait_pty_child(pid, timeout=30)
        return output
    finally:
        os.close(master_fd)
        _kill_pty_child(pid)


def _run_pty_command_access_prompt(command: list[str], env: dict[str, str], success_needle: str) -> str:
    pid, master_fd = pty.fork()
    output = ""
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(command[0], command, env)

    try:
        output = _read_pty_until(master_fd, pid, "ask every time (approve now)", timeout=120)
        os.write(master_fd, b"\r")
        output += _read_pty_until(master_fd, pid, success_needle, timeout=60)
        _wait_pty_child(pid, timeout=30)
        return output
    finally:
        os.close(master_fd)
        _kill_pty_child(pid)


def _read_pty_until(
    master_fd: int,
    pid: int,
    needle: str,
    *,
    timeout: int,
    forbid: tuple[str, ...] = (),
    initial: str = "",
    match_from: int = 0,
) -> str:
    deadline = time.monotonic() + timeout
    output = initial
    while time.monotonic() < deadline:
        if needle in output[match_from:]:
            return output[match_from:]
        exited = os.waitpid(pid, os.WNOHANG)
        if exited[0] == pid:
            raise AssertionError(f"pty child exited before {needle!r}; output:\n{output[match_from:]}")
        readable, _, _ = select.select([master_fd], [], [], 0.1)
        if not readable:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                raise AssertionError(f"pty closed before {needle!r}; output:\n{output[match_from:]}") from exc
            raise
        if not chunk:
            continue
        output += chunk.decode("utf-8", errors="replace")
        for marker in forbid:
            if marker in output[match_from:]:
                raise AssertionError(f"pty output contained failure marker {marker!r}; output:\n{output[match_from:]}")
        if needle in output[match_from:]:
            return output[match_from:]
    raise AssertionError(f"timed out waiting for {needle!r}; output:\n{output[match_from:]}")


def _exit_pty_shell(master_fd: int, pid: int, *, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        os.write(master_fd, b"exit\r")
        while time.monotonic() < deadline:
            exited = os.waitpid(pid, os.WNOHANG)
            if exited[0] == pid:
                return
            time.sleep(0.2)


def _wait_pty_child(pid: int, *, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exited = os.waitpid(pid, os.WNOHANG)
        if exited[0] == pid:
            return
        time.sleep(0.1)
    raise AssertionError("timed out waiting for pty child to exit")


def _kill_pty_child(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return


@pytest.mark.parametrize(
    "command",
    [
        ["bubblehub", "shell", "--root-dir", "examples/basic"],
        ["bubblehub", "shell", "--root-dir", "examples/basic", "--allow-network"],
    ],
)
def test_bubblehub_shell_examples_basic_cli_e2e(command: list[str], tmp_path: Path) -> None:
    result = _run_cli_e2e(command, tmp_path=tmp_path, stdin="exit\n")

    assert "Entering BubbleHub sandbox shell" in result.stdout
    assert "Using BubbleHub inference endpoint at http://127.0.0.1:8000" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_bubblehub_shell_access_prompt_hands_terminal_to_host_cli(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    root_dir = tmp_path / "workspace"
    root_dir.mkdir()

    with _host_http_server() as target_url:
        output = _run_pty_shell_access_prompt(
            ["bubblehub", "shell", "--root-dir", str(root_dir), "--force-new-sandbox"],
            _cli_e2e_env(tmp_path),
            target_url,
        )

    assert "BubbleHub sandbox paused for host access decision" in output
    assert "always" in output
    assert "never" in output
    assert "ask every time (approve now)" in output
    assert "Deny" not in output
    assert "Approve\n" not in output
    assert "suspended (tty output)" not in output
    assert "status=200" in output
    assert "status2=200" in output
    assert output.count("BubbleHub sandbox paused for host access decision") == 2


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_bubblehub_shell_force_new_prompts_for_plain_google_curl_with_default_state(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    root_dir = tmp_path / "workspace"
    old_agent_id = "agt-stalegoogle"
    (root_dir / ".bubblehub" / "agents" / old_agent_id / "home").mkdir(parents=True)
    (root_dir / ".bubblehub" / "current-agent").write_text(f"{old_agent_id}\n", encoding="utf-8")
    home = tmp_path / "home"
    old_manifest_dir = home / ".local" / "state" / "bubblehub" / "sandboxes" / old_agent_id
    old_manifest_dir.mkdir(parents=True)
    (old_manifest_dir / "access-manifest.json").write_text(
        '{"version":1,"agent_id":"agt-stalegoogle","policies":[{"kind":"http","subject":"google.com","method":"GET","path":"/","policy":"never"}],"pending":[]}\n',
        encoding="utf-8",
    )
    env = _cli_e2e_env(tmp_path)
    env.pop("BUBBLEHUB_STATE_DIR", None)
    env.pop("XDG_STATE_HOME", None)
    env["HOME"] = str(home)

    output = _run_pty_shell_google_denial_prompt(
        ["bubblehub", "shell", "--root-dir", str(root_dir), "--force-new-sandbox"],
        env,
    )

    new_agent_id = (root_dir / ".bubblehub" / "current-agent").read_text(encoding="utf-8").strip()
    assert new_agent_id != old_agent_id
    assert not old_manifest_dir.exists()
    assert "BubbleHub sandbox paused for host access decision" in output
    assert "subject=google.com method=GET path=/" in output
    assert "always" in output
    assert "never" in output
    assert "ask every time (approve now)" in output
    assert "suspended (tty output)" not in output
    assert "BubbleHub proxy denied the request" in output
    new_manifest = home / ".local" / "state" / "bubblehub" / "sandboxes" / new_agent_id / "access-manifest.json"
    assert '"subject":"google.com"' in new_manifest.read_text(encoding="utf-8")
    assert '"policy":"never"' in new_manifest.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="pty-backed run test requires POSIX")
def test_bubblehub_run_prompts_for_access_when_terminal_is_interactive(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    root_dir = tmp_path / "run-pty-workspace"
    root_dir.mkdir()
    with _host_http_server() as target_url:
        script = (
            "set -eu; "
            "status=$(curl --noproxy '' -sS -o bubblehub-run-pty-body "
            f"-w '%{{http_code}}' --max-time 15 {target_url}); "
            'printf "status=%s\\n" "$status"; test "$status" = 200'
        )
        output = _run_pty_command_access_prompt(
            ["bubblehub", "run", "--root-dir", str(root_dir), "--binary", "/bin/sh", "--", "-c", script],
            _cli_e2e_env(tmp_path),
            "status=200",
        )

    assert "BubbleHub sandbox paused for host access decision" in output
    assert "always" in output
    assert "never" in output
    assert "ask every time (approve now)" in output
    assert "BubbleHub proxy denied the request" not in output


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_bubblehub_shell_name_appears_in_prompt(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    root_dir = tmp_path / "named-shell-workspace"
    root_dir.mkdir()
    env = _cli_e2e_env(tmp_path)
    command = ["bubblehub", "shell", "--name", "researcher", "--root-dir", str(root_dir), "--force-new-sandbox"]
    pid, master_fd = pty.fork()
    output = ""
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(command[0], command, env)

    try:
        output = _read_pty_until(master_fd, pid, "[BubbleHub researcher]", timeout=120)
        os.write(master_fd, b"exit\r")
        _wait_pty_child(pid, timeout=30)
    finally:
        os.close(master_fd)
        _kill_pty_child(pid)

    assert "[BubbleHub researcher]" in output


def test_bubblehub_run_pending_access_can_be_resolved_from_dashboard_cli(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    root_dir = tmp_path / "run-dashboard-workspace"
    root_dir.mkdir()

    with _host_http_server() as target_url:
        denied = _run_policy_probe(root_dir, tmp_path, target_url, 403)
        assert denied.returncode == 0, denied.stdout
        assert "status=403" in denied.stdout
        assert "bubblehub dashboard" in denied.stdout

        dashboard = _run_cli(["bubblehub", "dashboard", "--once"], tmp_path=tmp_path, stdin="always\n", timeout=120)
        assert dashboard.returncode == 0, dashboard.stdout
        assert "Pending sandbox access requests" in dashboard.stdout
        assert "http GET 127.0.0.1/cli-policy" in dashboard.stdout

        target = f"127.0.0.1:{target_url.rsplit(':', 1)[1].split('/', 1)[0]}"
        connect_script = (
            "set -eu; "
            'exec 3<>"/dev/tcp/127.0.0.1/$BUBBLEHUB_HTTP_PROXY_PORT"; '
            f"printf 'CONNECT {target} HTTP/1.1\\r\\nHost: {target}\\r\\n\\r\\n' >&3; "
            "IFS= read -r line <&3; "
            "printf 'connect_response=%s\\n' \"$line\"; "
            "case \"$line\" in *'200 Connection Established'*) exit 0 ;; *) exit 1 ;; esac"
        )
        connect = _run_cli(
            ["bubblehub", "run", "--root-dir", str(root_dir), "--binary", "/usr/bin/bash", "--", "-lc", connect_script],
            tmp_path=tmp_path,
            timeout=120,
        )
        assert connect.returncode == 0, connect.stdout
        assert "connect_response=HTTP/1.1 200 Connection Established" in connect.stdout

        clear_dashboard = _run_cli(["bubblehub", "dashboard", "--once"], tmp_path=tmp_path, timeout=120)
        assert clear_dashboard.returncode == 0, clear_dashboard.stdout
        assert "Pending sandbox access requests" not in clear_dashboard.stdout


def test_bubblehub_manifest_cli_edits_policy_by_agent_id_and_root_dir(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    agent_id = "agt-manifest-cli"
    root_dir = tmp_path / "manifest-workspace"
    marker = root_dir / ".bubblehub" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text(f"{agent_id}\n", encoding="utf-8")
    _apply_manifest_policy(tmp_path, agent_id, "always")

    listed = _run_cli(["bubblehub", "manifest", "--root-dir", str(root_dir), "--no-edit"], tmp_path=tmp_path)
    assert listed.returncode == 0, listed.stdout
    assert "Access manifest" in listed.stdout
    assert "api.example.com" in listed.stdout
    assert "always" in listed.stdout

    edited = _run_cli(["bubblehub", "manifest", "--agent-id", agent_id], tmp_path=tmp_path, stdin="1\nnever\n")
    assert edited.returncode == 0, edited.stdout
    assert "Updated policy 1 to" in edited.stdout
    assert "never" in edited.stdout

    verified = _run_cli(["bubblehub", "manifest", "--agent-id", agent_id, "--no-edit"], tmp_path=tmp_path)
    assert verified.returncode == 0, verified.stdout
    assert "api.example.com" in verified.stdout
    assert "never" in verified.stdout
    assert "always" not in verified.stdout


def test_bubblehub_run_force_new_sandbox_discards_unremovable_overlay_workdir(tmp_path: Path) -> None:
    _require_bubblehub_cli()
    root_dir = tmp_path / "force-new-workspace"
    agent_id = "agt-staleforce"
    protected = root_dir / ".bubblehub" / "agents" / agent_id / "overlay" / "work" / "work"
    home = root_dir / ".bubblehub" / "agents" / agent_id / "home"
    protected.mkdir(parents=True)
    home.mkdir(parents=True)
    protected.chmod(0)
    (root_dir / ".bubblehub" / "current-agent").write_text(f"{agent_id}\n", encoding="utf-8")
    _apply_manifest_policy(tmp_path, agent_id, "never")
    old_manifest_dir = tmp_path / "bubblehub-state" / "sandboxes" / agent_id
    assert old_manifest_dir.exists()

    try:
        result = _run_cli(
            [
                "bubblehub",
                "run",
                "--binary",
                "/bin/true",
                "--root-dir",
                str(root_dir),
                "--force-new-sandbox",
                "--unsafe-no-sandbox",
            ],
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, result.stdout
        assert not (root_dir / ".bubblehub" / "agents" / agent_id).exists()
        assert not old_manifest_dir.exists()
        assert (root_dir / ".bubblehub" / "current-agent").read_text(encoding="utf-8") != f"{agent_id}\n"
    finally:
        tombstone = root_dir / ".bubblehub" / "agents" / f".removed-{agent_id}"
        for candidate in (protected, tombstone / "overlay" / "work" / "work"):
            if candidate.exists():
                candidate.chmod(0o700)
        shutil.rmtree(tombstone, ignore_errors=True)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["bubblehub", "--version"], "bubblehub "),
        (["bubblehub", "--help"], "BubbleHub local agent runtime"),
        (["bubblehub", "specialties", "list"], "default-instruct"),
        (["bubblehub", "ps"], "Memory pressure:"),
        (["bubblehub", "queue"], "BubbleHub Waiting Queue"),
        (["bubblehub", "poc", "--help"], "Start a local model REPL"),
        (["bubblehub", "prompt", "--help"], "Run one local prompt"),
        (["bubblehub", "run", "--help"], "Run a binary"),
        (["bubblehub", "shell", "--help"], "Open an interactive shell"),
        (["bubblehub", "dashboard", "--help"], "--once"),
        (["bubblehub", "manifest", "--help"], "Inspect and edit"),
        (["bubblehub", "serve", "--help"], "OpenAI-compatible"),
        (["bubblehub", "models", "--help"], "Inspect and choose"),
        (["bubblehub", "models", "list", "--help"], "List"),
        (["bubblehub", "specialties", "--help"], "Inspect available"),
    ],
)
def test_bubblehub_host_cli_tools_e2e(command: list[str], expected: str, tmp_path: Path) -> None:
    result = _run_cli_e2e(command, tmp_path=tmp_path)

    assert expected in result.stdout


@pytest.mark.integration
@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_sandbox_prompt_e2e(tmp_path: Path) -> None:
    commands = [f"bubblehub prompt --text '{_SANDBOX_LLM_PROMPT}'"]
    expected = [_SANDBOX_LLM_EXPECTED]
    _run_pty_shell(
        commands,
        _cli_e2e_env_with_inference(tmp_path),
        expected,
        response_timeout=300,
    )


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_sandbox_poc_e2e(tmp_path: Path) -> None:
    commands = ["bubblehub poc"]
    expected = ["bubblehub>"]
    _run_pty_shell(commands, _cli_e2e_env(tmp_path), expected)


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_sandbox_ps_blocked_inside_sandbox(tmp_path: Path) -> None:
    commands = ["bubblehub ps"]
    expected = ["only available to the real host user"]
    _run_pty_shell(commands, _cli_e2e_env(tmp_path), expected)


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_sandbox_app_blocked_inside_sandbox(tmp_path: Path) -> None:
    commands = ["bubblehub app"]
    expected = ["only available to the real host user"]
    _run_pty_shell(commands, _cli_e2e_env(tmp_path), expected)


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_sandbox_dashboard_blocked_inside_sandbox(tmp_path: Path) -> None:
    commands = ["bubblehub dashboard"]
    expected = ["only available to the real host user"]
    _run_pty_shell(commands, _cli_e2e_env(tmp_path), expected)
