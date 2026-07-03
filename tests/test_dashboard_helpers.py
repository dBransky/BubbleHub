from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

from bubblehub.tui import dashboard


def test_dashboard_render_builds_all_panels_and_tables() -> None:
    snapshot = {
        "memory": {
            "ram_total_bytes": 16 * 1024**3,
            "ram_used_bytes": 4 * 1024**3,
            "vram_total_bytes": 8 * 1024**3,
            "vram_used_bytes": 2 * 1024**3,
        },
        "memory_pressure": "available",
        "agents": [{"agent_id": "agt-test", "display_name": "agent", "running": True, "rss_bytes": 1024**3}],
        "models": [{"name": "small", "backend": "llama", "pid": 0, "ram_gb": 4, "vram_gb": 0, "refcount": 1}],
        "queue": [{"job_id": "job-1", "model_name": "small", "niceness": 1, "wait_seconds": 2, "reason": "waiting"}],
    }

    with patch("bubblehub.tui.dashboard.control_snapshot", return_value=snapshot):
        rendered = dashboard._render()

    console = Console(record=True, width=120)
    console.print(rendered)
    text = console.export_text()
    assert "BubbleHub Memory" in text
    assert "Loaded Model Reservations" in text
    assert "job-1" in text


def test_dashboard_limits_and_actual_usage_helpers() -> None:
    assert dashboard._limit_or_hardware({"ram_bytes": 8}, {"ram_bytes": 16}, "ram_bytes") == 8
    assert dashboard._limit_or_hardware({"ram_bytes": 32}, {"ram_bytes": 16}, "ram_bytes") == 16
    assert dashboard._limit_or_hardware({}, {"ram_bytes": "bad"}, "ram_bytes") == 0

    with patch("bubblehub.tui.dashboard._meminfo", return_value={"MemTotal": 100, "MemAvailable": 25}):
        assert dashboard._actual_ram_used_bytes(50) == 37
        assert dashboard._actual_ram_used_bytes(200) == 75

    assert dashboard._actual_vram_used_bytes({"free_vram_bytes": 3}, 10, []) == 7
    assert dashboard._actual_vram_used_bytes({}, 0, [{"vram_gb": 1.5}, {}]) == int(1.5 * 1024**3)
    assert dashboard._format_bytes(0) == "0GiB"
    assert dashboard._format_bytes(11 * 1024**3) == "11GiB"


def test_dashboard_meminfo_and_rss_parsers() -> None:
    meminfo = "MemTotal:       100 kB\nNoValue:\nMemAvailable:   bad kB\nCached:         25 kB\n"
    status = "Name:\ttest\nVmRSS:\t  42 kB\n"

    def read_text(self: Path, encoding: str = "utf-8") -> str:
        if str(self) == "/proc/meminfo":
            return meminfo
        return status

    with patch("pathlib.Path.read_text", read_text):
        assert dashboard._meminfo() == {"MemTotal": 100 * 1024, "Cached": 25 * 1024}
        assert dashboard._rss_bytes(123) == 42 * 1024

    with patch("pathlib.Path.read_text", side_effect=OSError):
        assert dashboard._meminfo() == {}
        assert dashboard._rss_bytes(123) == 0
    assert dashboard._rss_bytes(0) == 0


def test_dashboard_models_and_queue_tables_cover_empty_and_fallback_rows() -> None:
    with patch("bubblehub.tui.dashboard._rss_bytes", return_value=4096):
        models = dashboard._models_table([{"name": "small", "pid": 123, "ram_gb": 1, "vram_gb": 0}])
    queue = dashboard._queue_table([])

    assert models.columns[5]._cells == ["0.0GiB"]
    assert queue.columns[-1]._cells == ["No waiting jobs; admitted work appears under models/agents."]


def test_resolve_pending_access_ignores_empty_agent_ids() -> None:
    native = Mock()
    native.access_pending.return_value = [{"kind": "http", "subject": "example.com"}]
    client = Mock(native=native)

    with patch("bubblehub.tui.dashboard.SchedulerClient.local", return_value=client):
        dashboard._resolve_pending_access(Console(record=True))

    native.apply_access_policy.assert_not_called()
