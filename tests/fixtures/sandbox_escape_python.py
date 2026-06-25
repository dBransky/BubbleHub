from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

workspace = Path(os.environ["AGEOS_WORKSPACE"])
home = Path(os.environ["HOME"])
tmpdir = Path(os.environ["TMPDIR"])
host_canary = Path(os.environ["HOST_CANARY"])
expect_network_blocked = os.environ["EXPECT_NETWORK_BLOCKED"] == "1"
category = sys.argv[1] if len(sys.argv) > 1 else "all"

failures: list[str] = []


def fail_if_succeeds(name: str, action) -> None:
    try:
        action()
    except Exception:
        return
    failures.append(name)


def fail_if_command_succeeds(name: str, command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode == 0:
        failures.append(name)


# Sanity: normal in-sandbox writes must still work before the adversarial phase.
(workspace / "inside-python-workspace.txt").write_text("workspace-ok", encoding="utf-8")
(home / "inside-python-home.txt").write_text("home-ok", encoding="utf-8")
(tmpdir / "inside-python-tmp.txt").write_text("tmp-ok", encoding="utf-8")

for key in list(os.environ):
    if key.startswith("AGEOS_") or key in {
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    }:
        os.environ.pop(key, None)

if any(key.startswith("AGEOS_") for key in os.environ):
    failures.append("AGEOS env vars remained available during Python escape attempts")


def run_fs_direct() -> None:
    fail_if_succeeds("read host canary outside workspace", lambda: host_canary.read_text(encoding="utf-8"))
    fail_if_succeeds("overwrite host canary outside workspace", lambda: host_canary.write_text("escaped", encoding="utf-8"))
    fail_if_succeeds("append host canary outside workspace", lambda: host_canary.open("a", encoding="utf-8").write("escaped"))
    fail_if_succeeds("unlink host canary outside workspace", lambda: host_canary.unlink())
    fail_if_succeeds(
        "create sibling outside workspace",
        lambda: (host_canary.parent / "created-by-python-sandbox").write_text("escaped", encoding="utf-8"),
    )


def run_fs_links() -> None:
    workspace_symlink = workspace / "canary-link"
    fail_if_succeeds(
        "write host canary through workspace symlink",
        lambda: (
            workspace_symlink.unlink(missing_ok=True),
            workspace_symlink.symlink_to(host_canary),
            workspace_symlink.write_text("escaped", encoding="utf-8"),
        ),
    )
    fail_if_succeeds("hardlink host canary into workspace", lambda: os.link(host_canary, workspace / "canary-hardlink"))
    rename_source = workspace / "rename-source-python"
    rename_source.write_text("rename-source", encoding="utf-8")
    fail_if_succeeds("rename workspace file over host canary", lambda: os.rename(rename_source, host_canary))


def run_protected_paths() -> None:
    fail_if_succeeds("write /etc/passwd", lambda: Path("/etc/passwd").open("a", encoding="utf-8").write("escaped"))
    fail_if_succeeds("write /usr/local/bin/ageos", lambda: Path("/usr/local/bin/ageos").open("a", encoding="utf-8").write("escaped"))
    fail_if_succeeds("write /opt/ageos", lambda: Path("/opt/ageos/.ageos-escape").write_text("escaped", encoding="utf-8"))
    fail_if_succeeds("write host /tmp", lambda: Path("/tmp/ageos-sandbox-escape-python").write_text("escaped", encoding="utf-8"))
    fail_if_succeeds("write proc sysctl", lambda: Path("/proc/sys/kernel/hostname").open("w", encoding="utf-8").write("escaped\n"))


def run_namespace_tools() -> None:
    fail_if_command_succeeds("nsenter host mount namespace", ["sh", "-c", "command -v nsenter >/dev/null 2>&1 && nsenter -t 1 -m true"])


def run_network_isolated() -> None:
    if not expect_network_blocked:
        return

    def connect_public_network() -> None:
        with socket.create_connection(("1.1.1.1", 80), timeout=1):
            pass

    fail_if_succeeds("public network connect while network is isolated", connect_public_network)


def run_proxy_bypass() -> None:
    if not expect_network_blocked:
        return

    target = "http://example.com/ageos-proxy-bypass"

    def curl_must_not_fetch(url_args: list[str], name: str) -> None:
        if shutil.which("curl") is None:
            return
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "3", *url_args, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            failures.append(name)

    curl_must_not_fetch([], "curl direct web fetch after unsetting proxy env")
    curl_must_not_fetch(["--noproxy", "*"], "curl --noproxy web fetch after unsetting proxy env")
    curl_must_not_fetch(["--proxy", ""], "curl empty --proxy web fetch after unsetting proxy env")

    def urllib_fetch() -> None:
        import urllib.request

        urllib.request.urlopen(target, timeout=3).read()

    fail_if_succeeds("urllib direct web fetch after unsetting proxy env", urllib_fetch)

    def requests_fetch() -> None:
        import requests

        session = requests.Session()
        session.trust_env = False
        response = session.get(target, timeout=3)
        if response.status_code < 400:
            raise RuntimeError(f"unexpected HTTP {response.status_code}")

    try:
        import requests  # noqa: F401
    except ImportError:
        pass
    else:
        fail_if_succeeds("requests direct web fetch after unsetting proxy env", requests_fetch)

    def raw_http_get() -> None:
        with socket.create_connection(("93.184.216.34", 80), timeout=2) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
            if sock.recv(16):
                raise RuntimeError("received bytes from public HTTP endpoint")

    fail_if_succeeds("raw HTTP GET after unsetting proxy env", raw_http_get)


ROGUE_HOST = "203.0.113.1"
WEB_TARGET = "http://example.com/ageos-endpoint-repoint"
COMMON_LEGIT_HOSTS = (
    "10.0.0.1",
    "192.168.0.1",
    "192.168.1.1",
    "172.17.0.1",
    "172.18.0.1",
    "1.1.1.1",
)


def legit_candidate_ips() -> list[str]:
    candidates: list[str] = []

    def add_host(host: str) -> None:
        if host and host not in candidates:
            candidates.append(host)

    route_path = Path("/proc/net/route")
    if route_path.exists():
        try:
            route_lines = route_path.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            route_lines = []
        for line in route_lines:
            fields = line.split()
            if len(fields) >= 3 and fields[1] == "00000000":
                gateway_hex = fields[2]
                if gateway_hex != "00000000" and len(gateway_hex) == 8:
                    octets = bytes.fromhex(gateway_hex)
                    add_host(".".join(str(byte) for byte in reversed(octets)))

    for host in COMMON_LEGIT_HOSTS:
        add_host(host)
    add_host(ROGUE_HOST)
    return candidates


def run_endpoint_repoint() -> None:
    if not expect_network_blocked:
        return

    def curl_must_not_fetch(proxy_host: str, name: str) -> None:
        if shutil.which("curl") is None:
            return
        os.environ["HTTP_PROXY"] = f"http://{proxy_host}:8080"
        os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"]
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"]
        os.environ["https_proxy"] = os.environ["HTTPS_PROXY"]
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "3", WEB_TARGET],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            failures.append(name)

    def inference_must_not_connect(host: str, name: str) -> None:
        os.environ["OPENAI_BASE_URL"] = f"http://{host}:8000/v1"
        os.environ["AGEOS_SANDBOX_INFERENCE_HOST"] = host
        os.environ["AGEOS_SANDBOX_INFERENCE_PORT"] = "8000"
        os.environ["AGEOS_API_BASE_URL"] = f"http://{host}:8000"

        def urllib_inference_fetch() -> None:
            import urllib.request

            urllib.request.urlopen(f"http://{host}:8000/v1/models", timeout=3).read()

        fail_if_succeeds(f"{name} urllib", urllib_inference_fetch)

        def inference_socket_connect() -> None:
            with socket.create_connection((host, 8000), timeout=2):
                pass

        fail_if_succeeds(f"{name} socket", inference_socket_connect)

    for host in legit_candidate_ips():
        curl_must_not_fetch(host, f"curl via repointed proxy host {host}")
        inference_must_not_connect(host, f"inference repoint to {host}")

    if shutil.which("curl") is not None:
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9999"
        os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"]
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "3", WEB_TARGET],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            failures.append("curl via wrong loopback proxy port")


categories = {
    "env": lambda: None,
    "fs-direct": run_fs_direct,
    "fs-links": run_fs_links,
    "protected-paths": run_protected_paths,
    "namespace-tools": run_namespace_tools,
    "network-isolated": run_network_isolated,
    "proxy-bypass": run_proxy_bypass,
    "endpoint-repoint": run_endpoint_repoint,
}

if category == "all":
    for run_category in categories.values():
        run_category()
elif category in categories:
    categories[category]()
else:
    print(f"unknown Python escape category: {category}", file=sys.stderr)
    raise SystemExit(2)

if failures:
    print(f"Python sandbox escape attempts unexpectedly succeeded ({category}):", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    raise SystemExit(1)
