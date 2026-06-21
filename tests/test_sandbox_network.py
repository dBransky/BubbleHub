from __future__ import annotations

import platform
import shutil
from pathlib import Path

import pytest

from ageos.native import NativeScheduler


pytestmark = pytest.mark.skipif(platform.system() != "Linux", reason="sandbox network tests are Linux-only")


def _run_shell(tmp_path: Path, script: str, *, isolate_network: bool) -> int:
    return NativeScheduler().run_sandbox(
        "/bin/sh",
        ["/bin/sh", "-c", script],
        resource_niceness=0,
        memory_max=2 * 1024 * 1024 * 1024,
        cpu_percent=0,
        workdir=str(tmp_path),
        root_dir=str(tmp_path),
        isolate_network=isolate_network,
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
    )

    assert result == 0
