from __future__ import annotations

import os
import time
from pathlib import Path

from ageos.app.agents import enrich_agent_view, known_agent_records
from ageos.node.client import SchedulerClient


def control_snapshot(client: SchedulerClient | None = None) -> dict[str, object]:
    """Return the GUI/API telemetry view derived from the native scheduler."""

    client = client or SchedulerClient.local()
    snapshot = client.telemetry_snapshot()
    hardware = _dict(snapshot.get("hardware"))
    limits = _dict(snapshot.get("limits"))
    models = _list_of_dicts(snapshot.get("models"))
    ram_total = _limit_or_hardware(limits, hardware, "ram_bytes")
    vram_total = _limit_or_hardware(limits, hardware, "vram_bytes")
    ram_used = _actual_ram_used_bytes(ram_total)
    vram_used = _actual_vram_used_bytes(hardware, vram_total, models)

    agents = [_agent_view(item) for item in _list_of_dicts(snapshot.get("agents"))]
    agents.extend(known_agent_records({str(item.get("agent_id", "")) for item in agents}))

    return {
        "service": "ageos-control-center",
        "generated_at": time.time(),
        "hardware": hardware,
        "limits": limits,
        "memory_pressure": snapshot.get("memory_pressure", "available"),
        "memory": {
            "ram_total_bytes": ram_total,
            "ram_used_bytes": ram_used,
            "ram_used_percent": _percent(ram_used, ram_total),
            "vram_total_bytes": vram_total,
            "vram_used_bytes": vram_used,
            "vram_used_percent": _percent(vram_used, vram_total),
        },
        "agents": agents,
        "models": [_model_view(item) for item in models],
        "queue": _list_of_dicts(snapshot.get("queue")),
        "warnings": _telemetry_warnings(hardware),
    }


def pending_access(client: SchedulerClient | None = None) -> list[dict[str, object]]:
    client = client or SchedulerClient.local()
    return _list_of_dicts(client.native.access_pending())


def _dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _agent_view(item: dict[str, object]) -> dict[str, object]:
    pid = _int_or_zero(item.get("pid"))
    process = _process_metrics(pid)
    running = bool(process["available"])
    return enrich_agent_view(
        {
            **item,
            "pid": pid,
            "running": running,
            "pid_role": "ageos-run-host-process",
            "resource_metrics": process,
            "rss_bytes": process["rss_bytes"],
            "cpu_time_seconds": process["cpu_time_seconds"],
        }
    )


def _model_view(item: dict[str, object]) -> dict[str, object]:
    pid = _int_or_zero(item.get("pid"))
    rss_bytes = _rss_bytes(pid)
    ram_reserved = int(float(item.get("ram_gb", 0) or 0) * 1024**3)
    vram_reserved = int(float(item.get("vram_gb", 0) or 0) * 1024**3)
    return {
        **item,
        "pid": pid,
        "rss_bytes": rss_bytes,
        "ram_reserved_bytes": ram_reserved,
        "vram_reserved_bytes": vram_reserved,
    }


def _process_metrics(pid: int) -> dict[str, object]:
    if pid <= 0:
        return {
            "available": False,
            "rss_bytes": 0,
            "cpu_time_seconds": 0.0,
            "status": "unknown",
        }
    return {
        "available": (Path("/proc") / str(pid)).exists(),
        "rss_bytes": _rss_bytes(pid),
        "cpu_time_seconds": _cpu_time_seconds(pid),
        "status": _process_state(pid),
    }


def _limit_or_hardware(limits: dict[str, object], hardware: dict[str, object], key: str) -> int:
    hardware_value = _int_or_zero(hardware.get(key))
    limit = _int_or_zero(limits.get(key))
    if limit > 0:
        return min(limit, hardware_value) if hardware_value > 0 else limit
    return hardware_value


def _actual_ram_used_bytes(total: int) -> int:
    meminfo = _meminfo()
    mem_total = meminfo.get("MemTotal", total)
    mem_available = meminfo.get("MemAvailable", 0)
    if mem_total <= 0 or mem_available <= 0:
        return 0
    used = mem_total - mem_available
    if total > 0 and total < mem_total:
        return int(used * (total / mem_total))
    return used


def _actual_vram_used_bytes(hardware: dict[str, object], total: int, models: list[dict[str, object]]) -> int:
    free = _int_or_zero(hardware.get("free_vram_bytes"))
    if total > 0 and free > 0:
        return max(0, total - min(free, total))
    return int(sum(float(item.get("vram_gb", 0) or 0) * 1024**3 for item in models))


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    return values


def _rss_bytes(pid: int) -> int:
    if pid <= 0:
        return 0
    status = Path("/proc") / str(pid) / "status"
    try:
        text = status.read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return 0
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return 0
    return 0


def _cpu_time_seconds(pid: int) -> float:
    stat = Path("/proc") / str(pid) / "stat"
    try:
        text = stat.read_text(encoding="utf-8")
    except OSError:
        return 0.0
    parts = text.split()
    if len(parts) < 17:
        return 0.0
    try:
        ticks = int(parts[13]) + int(parts[14])
    except ValueError:
        return 0.0
    clock_ticks = os.sysconf("SC_CLK_TCK")
    return ticks / clock_ticks if clock_ticks else 0.0


def _process_state(pid: int) -> str:
    status = Path("/proc") / str(pid) / "status"
    try:
        text = status.read_text(encoding="utf-8")
    except OSError:
        return "missing"
    for line in text.splitlines():
        if line.startswith("State:"):
            return line.partition(":")[2].strip()
    return "unknown"


def _telemetry_warnings(hardware: dict[str, object]) -> list[str]:
    warnings: list[str] = []
    if _int_or_zero(hardware.get("vram_bytes")) <= 0:
        warnings.append("No dedicated VRAM telemetry is available; model GPU usage falls back to reservations.")
    warnings.append("Agent resource metrics use the host ageos run process until sandbox child telemetry is available.")
    return warnings


def _percent(used: int, total: int) -> float:
    return round((used / total) * 100, 1) if total > 0 else 0.0


def _int_or_zero(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
