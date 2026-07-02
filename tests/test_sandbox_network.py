from __future__ import annotations

import platform
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import pytest

from bubblehub.native import NativeScheduler

HTTP_PROXY_PORT = 18080


pytestmark = pytest.mark.skipif(platform.system() != "Linux", reason="sandbox network tests are Linux-only")


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"manifest impersonation should not reach host")

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _host_http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}/manifest-impersonation"
    finally:
        server.shutdown()
        server.server_close()


def _run_shell(
    tmp_path: Path,
    script: str,
    *,
    isolate_network: bool,
    disable_http_proxy: bool = False,
) -> int:
    return NativeScheduler().run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", script],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=isolate_network,
        disable_http_proxy=disable_http_proxy,
    )


def _run_shell_with_proxy(tmp_path: Path, script: str) -> int:
    return NativeScheduler().run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", script],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=True,
        sandbox_http_proxy_port=HTTP_PROXY_PORT,
    )


def test_network_allowed_grants_cap_net_raw_to_shell(tmp_path: Path) -> None:
    script = (
        "python3 - <<'PY'\n"
        "import ctypes\n"
        "class CapHeader(ctypes.Structure):\n"
        "    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]\n"
        "class CapData(ctypes.Structure):\n"
        "    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]\n"
        "header = CapHeader(version=0x20080522, pid=0)\n"
        "data = (CapData * 2)()\n"
        "if ctypes.CDLL(None).syscall(125, ctypes.byref(header), ctypes.byref(data)) != 0:\n"
        "    raise SystemExit(11)\n"
        "mask = 1 << 13\n"
        "if not (data[0].permitted & mask and data[0].effective & mask):\n"
        "    raise SystemExit(12)\n"
        "print('cap_net_raw_ok')\n"
        "PY"
    )
    result = _run_shell(tmp_path, script, isolate_network=False)

    assert result == 0


def test_network_allowed_can_resolve_public_dns(tmp_path: Path) -> None:
    if shutil.which("getent") is None:
        pytest.skip("getent is not installed")
    result = _run_shell(
        tmp_path,
        "getent ahosts example.com >/dev/null",
        isolate_network=False,
    )

    assert result == 0


def test_network_isolated_blocks_public_connectivity(tmp_path: Path) -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    result = _run_shell(
        tmp_path,
        "if curl -s --max-time 2 http://203.0.113.1 >/dev/null 2>&1; then exit 9; else exit 0; fi",
        isolate_network=True,
        disable_http_proxy=True,
    )

    assert result == 0


def test_http_proxy_denies_curl_request(tmp_path: Path) -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    result = _run_shell_with_proxy(
        tmp_path,
        (
            "status=$(curl -sS -o \"$TMPDIR/proxy-body\" -w '%{http_code}' "
            "--max-time 5 http://example.com/bubblehub-proxy-test); "
            'test "$status" = 403; '
            "grep -q 'BubbleHub proxy denied the request' \"$TMPDIR/proxy-body\""
        ),
    )

    assert result == 0


def test_sandbox_cannot_impersonate_another_agent_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    real_agent_id = "agt-real-manifest"
    victim_agent_id = "agt-victim-manifest"
    monkeypatch.delenv("BUBBLEHUB_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))

    with _host_http_server() as target_url:
        parsed = urlparse(target_url)
        NativeScheduler().apply_access_policy(
            victim_agent_id,
            kind="http",
            subject=parsed.hostname or "",
            method="GET",
            path=parsed.path or "/",
            policy="always",
        )
        monkeypatch.setenv("BUBBLEHUB_AGENT_ID", real_agent_id)
        result = _run_shell_with_proxy(
            tmp_path,
            (
                "set -eu; "
                f"export BUBBLEHUB_AGENT_ID={victim_agent_id}; "
                "status=$(curl --noproxy '' -sS -o \"$TMPDIR/proxy-body\" -w '%{http_code}' "
                f"--max-time 5 {target_url}); "
                'test "$status" = 403; '
                "grep -q 'BubbleHub proxy denied the request' \"$TMPDIR/proxy-body\""
            ),
        )

    assert result == 0


def test_tenant_unset_proxy_env_cannot_reach_public_web(tmp_path: Path) -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    target = "http://example.com/tenant-proxy-bypass"
    script = (
        "set -eu; "
        'test -n "$HTTP_PROXY"; '
        "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy BUBBLEHUB_HTTP_PROXY_PORT NO_PROXY no_proxy; "
        f"if curl -fsS --max-time 3 {target} >/dev/null 2>&1; then exit 9; fi; "
        f"if curl -fsS --noproxy '*' --max-time 3 {target} >/dev/null 2>&1; then exit 10; fi; "
        f"if curl -fsS --proxy '' --max-time 3 {target} >/dev/null 2>&1; then exit 11; fi; "
        "echo tenant_proxy_bypass_blocked"
    )
    result = _run_shell(tmp_path, script, isolate_network=True)

    assert result == 0


def test_tenant_python_unset_proxy_env_cannot_reach_public_web(tmp_path: Path) -> None:
    script = (
        "python3 - <<'PY'\n"
        "import os\n"
        "import socket\n"
        "import sys\n"
        "import urllib.request\n"
        "for key in list(os.environ):\n"
        "    if key.startswith('BUBBLEHUB_') or key in {\n"
        "        'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY', 'no_proxy',\n"
        "    }:\n"
        "        os.environ.pop(key, None)\n"
        "target = 'http://example.com/tenant-python-bypass'\n"
        "try:\n"
        "    urllib.request.urlopen(target, timeout=3).read()\n"
        "except Exception:\n"
        "    pass\n"
        "else:\n"
        "    sys.exit(12)\n"
        "try:\n"
        "    with socket.create_connection(('93.184.216.34', 80), timeout=2) as sock:\n"
        "        sock.sendall(b'GET / HTTP/1.1\\r\\nHost: example.com\\r\\nConnection: close\\r\\n\\r\\n')\n"
        "        if sock.recv(16):\n"
        "            sys.exit(13)\n"
        "except OSError:\n"
        "    pass\n"
        "print('tenant_python_proxy_bypass_blocked')\n"
        "PY"
    )
    result = _run_shell(tmp_path, script, isolate_network=True)

    assert result == 0


def _run_shell_with_inference(tmp_path: Path, script: str) -> int:
    return NativeScheduler().run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", script],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=True,
        inference_host="127.0.0.1",
        inference_port=8000,
        sandbox_inference_port=8000,
    )


def test_tenant_repoint_proxy_and_inference_hosts_cannot_escape(tmp_path: Path) -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    web_target = "http://example.com/tenant-endpoint-repoint"
    script = (
        "set -eu; "
        "python3 - <<'PY'\n"
        "import os\n"
        "import socket\n"
        "import subprocess\n"
        "import sys\n"
        "import urllib.request\n"
        "from pathlib import Path\n"
        "\n"
        "web_target = '" + web_target + "'\n"
        "common_hosts = [\n"
        "    '10.0.0.1',\n"
        "    '192.168.0.1',\n"
        "    '192.168.1.1',\n"
        "    '172.17.0.1',\n"
        "    '172.18.0.1',\n"
        "    '1.1.1.1',\n"
        "    '203.0.113.1',\n"
        "]\n"
        "hosts = []\n"
        "for host in common_hosts:\n"
        "    if host not in hosts:\n"
        "        hosts.append(host)\n"
        "route_path = Path('/proc/net/route')\n"
        "if route_path.exists():\n"
        "    try:\n"
        "        route_lines = route_path.read_text(encoding='utf-8').splitlines()[1:]\n"
        "    except OSError:\n"
        "        route_lines = []\n"
        "    for line in route_lines:\n"
        "        fields = line.split()\n"
        "        if len(fields) >= 3 and fields[1] == '00000000':\n"
        "            gateway_hex = fields[2]\n"
        "            if gateway_hex != '00000000' and len(gateway_hex) == 8:\n"
        "                octets = bytes.fromhex(gateway_hex)\n"
        "                gateway = '.'.join(str(byte) for byte in reversed(octets))\n"
        "                if gateway not in hosts:\n"
        "                    hosts.insert(0, gateway)\n"
        "\n"
        "for host in hosts:\n"
        "    env = {\n"
        "        'HTTP_PROXY': f'http://{host}:8080',\n"
        "        'HTTPS_PROXY': f'http://{host}:8080',\n"
        "    }\n"
        "    result = subprocess.run(\n"
        "        ['curl', '-fsS', '--max-time', '3', web_target],\n"
        "        env={**os.environ, **env},\n"
        "        stdout=subprocess.DEVNULL,\n"
        "        stderr=subprocess.DEVNULL,\n"
        "        check=False,\n"
        "    )\n"
        "    if result.returncode == 0:\n"
        "        sys.exit(9)\n"
        "    try:\n"
        "        urllib.request.urlopen(f'http://{host}:8000/v1/models', timeout=3).read()\n"
        "    except Exception:\n"
        "        pass\n"
        "    else:\n"
        "        sys.exit(10)\n"
        "    try:\n"
        "        with socket.create_connection((host, 8000), timeout=2):\n"
        "            pass\n"
        "    except OSError:\n"
        "        pass\n"
        "    else:\n"
        "        sys.exit(11)\n"
        "print('tenant_endpoint_repoint_blocked')\n"
        "PY"
    )
    result = _run_shell_with_inference(tmp_path, script)

    assert result == 0


def test_isolated_sandbox_overwrites_preset_proxy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.1:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://203.0.113.1:8080")
    monkeypatch.setenv("http_proxy", "http://203.0.113.1:8080")
    monkeypatch.setenv("https_proxy", "http://203.0.113.1:8080")
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setenv("no_proxy", "example.com")
    result = _run_shell(
        tmp_path,
        (
            'test "$HTTP_PROXY" = "http://127.0.0.1:18080"; '
            'test "$HTTPS_PROXY" = "http://127.0.0.1:18080"; '
            'test "$http_proxy" = "http://127.0.0.1:18080"; '
            'test "$https_proxy" = "http://127.0.0.1:18080"; '
            'test "$BUBBLEHUB_HTTP_PROXY_PORT" = "18080"; '
            'echo ",$NO_PROXY," | grep -q ",127.0.0.1,"; '
            'echo ",$NO_PROXY," | grep -q ",localhost,"; '
            'echo ",$no_proxy," | grep -q ",127.0.0.1,"; '
            'echo ",$no_proxy," | grep -q ",localhost,"; '
            "echo preset_proxy_env_overwritten"
        ),
        isolate_network=True,
    )

    assert result == 0


def test_isolated_sandbox_starts_http_proxy_by_default(tmp_path: Path) -> None:
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    result = _run_shell(
        tmp_path,
        (
            'test "$HTTP_PROXY" = "http://127.0.0.1:18080"; '
            "status=$(curl -sS -o \"$TMPDIR/proxy-body\" -w '%{http_code}' "
            "--max-time 5 http://example.com/bubblehub-default-proxy); "
            'test "$status" = 403'
        ),
        isolate_network=True,
    )

    assert result == 0
