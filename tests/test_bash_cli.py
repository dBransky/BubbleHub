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


def _require_ageos_cli() -> None:
    if shutil.which("ageos") is None:
        pytest.skip("ageos is not installed")


def _cli_e2e_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("AGEOS_API_BASE_URL", "http://127.0.0.1:8000")
    env.setdefault("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env["AGEOS_STATE_DIR"] = str(tmp_path / "ageos-state")
    env["AGEOS_PYTHONPATH"] = str(ROOT)
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    return env


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"ageos-cli-policy-ok\n"
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
    _require_ageos_cli()
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
    _require_ageos_cli()
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
        "body=ageos-run-policy-body; "
        "status=$(curl --noproxy '' -sS -o \"$body\" "
        f"-w '%{{http_code}}' --max-time 15 {target_url}); "
        f'printf \'status=%s\\n\' "$status"; cat "$body"; test "$status" = {expected_status}'
    )
    return _run_cli(
        ["ageos", "run", "--root-dir", str(root_dir), "--binary", "/bin/sh", "--", "-c", script],
        tmp_path=tmp_path,
        timeout=120,
    )


def _apply_manifest_policy(tmp_path: Path, agent_id: str, policy: str) -> None:
    script = f"""
from ageos.native import NativeScheduler
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
        output = _read_pty_until(master_fd, pid, "[AgeOS]", timeout=120)
        curl_command = "curl --noproxy '' -sS -o /tmp/ageos-cli-policy-body " f"-w 'status=%{{http_code}}\\n' --max-time 15 {target_url}\r"
        os.write(master_fd, curl_command.encode("utf-8"))
        output += _read_pty_until(master_fd, pid, "ask every time (approve now)", timeout=30)
        os.write(master_fd, b"\x1b[B\x1b[B\r")
        output += _read_pty_until(master_fd, pid, "status=200", timeout=30)
        repeated_curl_command = (
            "curl --noproxy '' -sS -o /tmp/ageos-cli-policy-body-repeat " f"-w 'status2=%{{http_code}}\\n' --max-time 15 {target_url}\r"
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


def _run_pty_shell_google_denial_prompt(command: list[str], env: dict[str, str]) -> str:
    pid, master_fd = pty.fork()
    output = ""
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(command[0], command, env)

    try:
        output = _read_pty_until(master_fd, pid, "[AgeOS]", timeout=120)
        os.write(master_fd, b"curl google.com\r")
        output += _read_pty_until(master_fd, pid, "ask every time (approve now)", timeout=30)
        os.write(master_fd, b"\x1b[B\r")
        output += _read_pty_until(master_fd, pid, "AgeOS proxy denied the request", timeout=30)
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


def _read_pty_until(master_fd: int, pid: int, needle: str, *, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        exited = os.waitpid(pid, os.WNOHANG)
        if exited[0] == pid:
            raise AssertionError(f"pty child exited before {needle!r}; output:\n{output}")
        readable, _, _ = select.select([master_fd], [], [], 0.1)
        if not readable:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                raise AssertionError(f"pty closed before {needle!r}; output:\n{output}") from exc
            raise
        if not chunk:
            continue
        output += chunk.decode("utf-8", errors="replace")
        if needle in output:
            return output
    raise AssertionError(f"timed out waiting for {needle!r}; output:\n{output}")


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
        ["ageos", "shell", "--root-dir", "examples/basic"],
        ["ageos", "shell", "--root-dir", "examples/basic", "--allow-network"],
    ],
)
def test_ageos_shell_examples_basic_cli_e2e(command: list[str], tmp_path: Path) -> None:
    result = _run_cli_e2e(command, tmp_path=tmp_path, stdin="exit\n")

    assert "Entering AgeOS sandbox shell" in result.stdout
    assert "Using AgeOS inference endpoint at http://127.0.0.1:8000" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_ageos_shell_access_prompt_hands_terminal_to_host_cli(tmp_path: Path) -> None:
    _require_ageos_cli()
    root_dir = tmp_path / "workspace"
    root_dir.mkdir()

    with _host_http_server() as target_url:
        output = _run_pty_shell_access_prompt(
            ["ageos", "shell", "--root-dir", str(root_dir), "--force-new-sandbox"],
            _cli_e2e_env(tmp_path),
            target_url,
        )

    assert "AgeOS sandbox paused for host access decision" in output
    assert "always" in output
    assert "never" in output
    assert "ask every time (approve now)" in output
    assert "Deny" not in output
    assert "Approve\n" not in output
    assert "suspended (tty output)" not in output
    assert "status=200" in output
    assert "status2=200" in output
    assert output.count("AgeOS sandbox paused for host access decision") == 2


@pytest.mark.skipif(os.name != "posix", reason="pty-backed shell test requires POSIX")
def test_ageos_shell_force_new_prompts_for_plain_google_curl_with_default_state(tmp_path: Path) -> None:
    _require_ageos_cli()
    root_dir = tmp_path / "workspace"
    old_agent_id = "agt-stalegoogle"
    (root_dir / ".ageos" / "agents" / old_agent_id / "home").mkdir(parents=True)
    (root_dir / ".ageos" / "current-agent").write_text(f"{old_agent_id}\n", encoding="utf-8")
    home = tmp_path / "home"
    old_manifest_dir = home / ".local" / "state" / "ageos" / "sandboxes" / old_agent_id
    old_manifest_dir.mkdir(parents=True)
    (old_manifest_dir / "access-manifest.json").write_text(
        '{"version":1,"agent_id":"agt-stalegoogle","policies":[{"kind":"http","subject":"google.com","method":"GET","path":"/","policy":"never"}],"pending":[]}\n',
        encoding="utf-8",
    )
    env = _cli_e2e_env(tmp_path)
    env.pop("AGEOS_STATE_DIR", None)
    env.pop("XDG_STATE_HOME", None)
    env["HOME"] = str(home)

    output = _run_pty_shell_google_denial_prompt(
        ["ageos", "shell", "--root-dir", str(root_dir), "--force-new-sandbox"],
        env,
    )

    new_agent_id = (root_dir / ".ageos" / "current-agent").read_text(encoding="utf-8").strip()
    assert new_agent_id != old_agent_id
    assert not old_manifest_dir.exists()
    assert "AgeOS sandbox paused for host access decision" in output
    assert "subject=google.com method=GET path=/" in output
    assert "always" in output
    assert "never" in output
    assert "ask every time (approve now)" in output
    assert "suspended (tty output)" not in output
    assert "AgeOS proxy denied the request" in output
    new_manifest = home / ".local" / "state" / "ageos" / "sandboxes" / new_agent_id / "access-manifest.json"
    assert '"subject":"google.com"' in new_manifest.read_text(encoding="utf-8")
    assert '"policy":"never"' in new_manifest.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="pty-backed run test requires POSIX")
def test_ageos_run_prompts_for_access_when_terminal_is_interactive(tmp_path: Path) -> None:
    _require_ageos_cli()
    root_dir = tmp_path / "run-pty-workspace"
    root_dir.mkdir()
    with _host_http_server() as target_url:
        script = (
            "set -eu; "
            "status=$(curl --noproxy '' -sS -o ageos-run-pty-body "
            f"-w '%{{http_code}}' --max-time 15 {target_url}); "
            'printf "status=%s\\n" "$status"; test "$status" = 200'
        )
        output = _run_pty_command_access_prompt(
            ["ageos", "run", "--root-dir", str(root_dir), "--binary", "/bin/sh", "--", "-c", script],
            _cli_e2e_env(tmp_path),
            "status=200",
        )

    assert "AgeOS sandbox paused for host access decision" in output
    assert "always" in output
    assert "never" in output
    assert "ask every time (approve now)" in output
    assert "AgeOS proxy denied the request" not in output


def test_ageos_run_pending_access_can_be_resolved_from_dashboard_cli(tmp_path: Path) -> None:
    _require_ageos_cli()
    root_dir = tmp_path / "run-dashboard-workspace"
    root_dir.mkdir()

    with _host_http_server() as target_url:
        denied = _run_policy_probe(root_dir, tmp_path, target_url, 403)
        assert denied.returncode == 0, denied.stdout
        assert "status=403" in denied.stdout
        assert "ageos dashboard" in denied.stdout

        dashboard = _run_cli(["ageos", "dashboard", "--once"], tmp_path=tmp_path, stdin="always\n", timeout=120)
        assert dashboard.returncode == 0, dashboard.stdout
        assert "Pending sandbox access requests" in dashboard.stdout
        assert "http GET 127.0.0.1/cli-policy" in dashboard.stdout

        target = f"127.0.0.1:{target_url.rsplit(':', 1)[1].split('/', 1)[0]}"
        connect_script = (
            "set -eu; "
            'exec 3<>"/dev/tcp/127.0.0.1/$AGEOS_HTTP_PROXY_PORT"; '
            f"printf 'CONNECT {target} HTTP/1.1\\r\\nHost: {target}\\r\\n\\r\\n' >&3; "
            "IFS= read -r line <&3; "
            "printf 'connect_response=%s\\n' \"$line\"; "
            "case \"$line\" in *'200 Connection Established'*) exit 0 ;; *) exit 1 ;; esac"
        )
        connect = _run_cli(
            ["ageos", "run", "--root-dir", str(root_dir), "--binary", "/usr/bin/bash", "--", "-lc", connect_script],
            tmp_path=tmp_path,
            timeout=120,
        )
        assert connect.returncode == 0, connect.stdout
        assert "connect_response=HTTP/1.1 200 Connection Established" in connect.stdout

        clear_dashboard = _run_cli(["ageos", "dashboard", "--once"], tmp_path=tmp_path, timeout=120)
        assert clear_dashboard.returncode == 0, clear_dashboard.stdout
        assert "Pending sandbox access requests" not in clear_dashboard.stdout


def test_ageos_manifest_cli_edits_policy_by_agent_id_and_root_dir(tmp_path: Path) -> None:
    _require_ageos_cli()
    agent_id = "agt-manifest-cli"
    root_dir = tmp_path / "manifest-workspace"
    marker = root_dir / ".ageos" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text(f"{agent_id}\n", encoding="utf-8")
    _apply_manifest_policy(tmp_path, agent_id, "always")

    listed = _run_cli(["ageos", "manifest", "--root-dir", str(root_dir), "--no-edit"], tmp_path=tmp_path)
    assert listed.returncode == 0, listed.stdout
    assert "Access manifest" in listed.stdout
    assert "api.example.com" in listed.stdout
    assert "always" in listed.stdout

    edited = _run_cli(["ageos", "manifest", "--agent-id", agent_id], tmp_path=tmp_path, stdin="1\nnever\n")
    assert edited.returncode == 0, edited.stdout
    assert "Updated policy 1 to" in edited.stdout
    assert "never" in edited.stdout

    verified = _run_cli(["ageos", "manifest", "--agent-id", agent_id, "--no-edit"], tmp_path=tmp_path)
    assert verified.returncode == 0, verified.stdout
    assert "api.example.com" in verified.stdout
    assert "never" in verified.stdout
    assert "always" not in verified.stdout


def test_ageos_run_force_new_sandbox_discards_unremovable_overlay_workdir(tmp_path: Path) -> None:
    _require_ageos_cli()
    root_dir = tmp_path / "force-new-workspace"
    agent_id = "agt-staleforce"
    protected = root_dir / ".ageos" / "agents" / agent_id / "overlay" / "work" / "work"
    home = root_dir / ".ageos" / "agents" / agent_id / "home"
    protected.mkdir(parents=True)
    home.mkdir(parents=True)
    protected.chmod(0)
    (root_dir / ".ageos" / "current-agent").write_text(f"{agent_id}\n", encoding="utf-8")
    _apply_manifest_policy(tmp_path, agent_id, "never")
    old_manifest_dir = tmp_path / "ageos-state" / "sandboxes" / agent_id
    assert old_manifest_dir.exists()

    try:
        result = _run_cli(
            [
                "ageos",
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
        assert not (root_dir / ".ageos" / "agents" / agent_id).exists()
        assert not old_manifest_dir.exists()
        assert (root_dir / ".ageos" / "current-agent").read_text(encoding="utf-8") != f"{agent_id}\n"
    finally:
        tombstone = root_dir / ".ageos" / "agents" / f".removed-{agent_id}"
        for candidate in (protected, tombstone / "overlay" / "work" / "work"):
            if candidate.exists():
                candidate.chmod(0o700)
        shutil.rmtree(tombstone, ignore_errors=True)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["ageos", "--version"], "ageos "),
        (["ageos", "--help"], "AgeOS local agent runtime"),
        (["ageos", "specialties", "list"], "default-instruct"),
        (["ageos", "ps"], "Memory pressure:"),
        (["ageos", "queue"], "AgeOS Waiting Queue"),
        (["ageos", "poc", "--help"], "Start a local model REPL"),
        (["ageos", "prompt", "--help"], "Run one local prompt"),
        (["ageos", "run", "--help"], "Run a binary"),
        (["ageos", "shell", "--help"], "Open an interactive shell"),
        (["ageos", "dashboard", "--help"], "--once"),
        (["ageos", "manifest", "--help"], "Inspect and edit"),
        (["ageos", "serve", "--help"], "OpenAI-compatible"),
        (["ageos", "models", "--help"], "Inspect and choose"),
        (["ageos", "models", "list", "--help"], "List"),
        (["ageos", "specialties", "--help"], "Inspect available"),
    ],
)
def test_ageos_host_cli_tools_e2e(command: list[str], expected: str, tmp_path: Path) -> None:
    result = _run_cli_e2e(command, tmp_path=tmp_path)

    assert expected in result.stdout
