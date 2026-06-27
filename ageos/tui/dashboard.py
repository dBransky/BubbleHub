from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from ageos.app.telemetry import control_snapshot
from ageos.node.client import SchedulerClient


def run_dashboard(refresh_seconds: float = 1.0, *, once: bool = False) -> None:
    console = Console()
    _resolve_pending_access(console)
    if once:
        console.print(_render())
        return
    with Live(_render(), console=console, refresh_per_second=max(1, int(1 / refresh_seconds))) as live:
        try:
            while True:
                live.update(_render())
                time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            return


def _resolve_pending_access(console: Console) -> None:
    native = SchedulerClient.local().native
    pending = native.access_pending()
    if not pending:
        return
    console.print("[bold]Pending sandbox access requests[/bold]")
    for item in pending:
        agent_id = str(item.get("agent_id", ""))
        if not agent_id:
            continue
        console.print(_pending_access_label(item))
        choice = Prompt.ask(
            "Policy",
            choices=["always", "never", "ask"],
            default="ask",
            console=console,
        )
        method, path = _manifest_scope_for_pending(item)
        native.apply_access_policy(
            agent_id,
            kind=str(item.get("kind", "")),
            subject=str(item.get("subject", "")),
            method=method,
            path=path,
            policy=choice,
        )


def _pending_access_label(item: dict[str, Any]) -> str:
    agent_id = str(item.get("agent_id", ""))
    kind = str(item.get("kind", ""))
    subject = str(item.get("subject", ""))
    method = str(item.get("method", ""))
    path = str(item.get("path", ""))
    target = subject if not path else f"{subject}{path}"
    return f"{agent_id}: {kind} {method or '*'} {target}"


def _manifest_scope_for_pending(item: dict[str, Any]) -> tuple[str, str]:
    if str(item.get("kind", "")) == "http":
        return "*", "*"
    return str(item.get("method", "")), str(item.get("path", ""))


def _render() -> Group:
    snapshot = control_snapshot()
    memory = snapshot["memory"] if isinstance(snapshot["memory"], dict) else {}
    models = snapshot["models"]  # type: ignore[index]
    ram_total = _int_or_zero(memory.get("ram_total_bytes"))
    ram_used = _int_or_zero(memory.get("ram_used_bytes"))
    vram_total = _int_or_zero(memory.get("vram_total_bytes"))
    vram_used = _int_or_zero(memory.get("vram_used_bytes"))

    return Group(
        Panel(_bars(ram_total, ram_used, "RAM", snapshot["memory_pressure"]), title="AgeOS Memory"),
        Panel(_bars(vram_total, vram_used, "VRAM", "n/a" if vram_total == 0 else "tracked"), title="AgeOS GPU"),
        _agents_table(snapshot["agents"]),  # type: ignore[index]
        _models_table(models),
        _queue_table(snapshot["queue"]),  # type: ignore[index]
    )


def _limit_or_hardware(limits: object, hardware: object, key: str) -> int:
    hardware_value = 0
    if isinstance(hardware, dict):
        hardware_value = _int_or_zero(hardware.get(key))
    if isinstance(limits, dict):
        limit = _int_or_zero(limits.get(key))
        if limit > 0:
            return min(limit, hardware_value) if hardware_value > 0 else limit
    return hardware_value


def _int_or_zero(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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


def _actual_vram_used_bytes(hardware: object, total: int, models: list[dict[str, object]]) -> int:
    free = _int_or_zero(hardware.get("free_vram_bytes")) if isinstance(hardware, dict) else 0
    if total > 0 and free > 0:
        return max(0, total - min(free, total))
    return int(sum(float(item.get("vram_gb", 0)) * 1024**3 for item in models))


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


def _bars(total: int, used: int, label: str, state: object) -> Progress:
    progress = Progress(
        TextColumn(f"{label}"),
        BarColumn(bar_width=50),
        TextColumn("{task.percentage:>3.0f}%"),
        TextColumn(f"{_format_bytes(used)} / {_format_bytes(total)}"),
        TextColumn(f"state={state}"),
    )
    total_safe = max(total, 1)
    progress.add_task(label, total=total_safe, completed=min(used, total_safe))
    return progress


def _format_bytes(value: int) -> str:
    if value <= 0:
        return "0GiB"
    gib = value / 1024**3
    if gib >= 10:
        return f"{gib:.0f}GiB"
    return f"{gib:.1f}GiB"


def _agents_table(agents: list[dict[str, object]]) -> Table:
    table = Table(title="Agents")
    for column in ["state", "name", "agent_id", "binary", "status", "niceness", "specialty", "pid", "rss", "cpu_time"]:
        table.add_column(column)
    for item in agents:
        running = bool(item.get("running"))
        state = "[green]●[/green]" if running else "[red]●[/red]"
        table.add_row(
            state,
            str(item.get("display_name", item.get("name", ""))),
            str(item.get("agent_id", "")),
            str(item.get("binary", "")),
            str(item.get("status", "")),
            str(item.get("niceness", "")),
            str(item.get("specialty", "")),
            str(item.get("pid", "")),
            _format_bytes(_int_or_zero(item.get("rss_bytes"))),
            f"{float(item.get('cpu_time_seconds', 0)):g}s",
        )
    return table


def _models_table(models: list[dict[str, object]]) -> Table:
    table = Table(title="Loaded Model Reservations")
    columns = ["name", "backend", "specialty", "ram_reserved", "vram_reserved", "rss", "pid", "port", "refcount"]
    for column in columns:
        table.add_column(column)
    for item in models:
        pid = _int_or_zero(item.get("pid"))
        table.add_row(
            str(item.get("name", "")),
            str(item.get("backend", "")),
            str(item.get("specialty", "")),
            f"{float(item.get('ram_gb', 0)):g}G",
            f"{float(item.get('vram_gb', 0)):g}G",
            _format_bytes(_int_or_zero(item.get("rss_bytes")) or _rss_bytes(pid)),
            str(item.get("pid", "")),
            str(item.get("port", "")),
            str(item.get("refcount", "")),
        )
    return table


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


def _queue_table(queue: list[dict[str, object]]) -> Table:
    table = Table(title="Waiting Queue")
    for column in ["job_id", "model_name", "niceness", "wait_seconds", "reason"]:
        table.add_column(column)
    if not queue:
        table.add_row("", "", "", "", "No waiting jobs; admitted work appears under models/agents.")
        return table
    for item in queue:
        table.add_row(*(str(item.get(column, "")) for column in ["job_id", "model_name", "niceness", "wait_seconds", "reason"]))
    return table
