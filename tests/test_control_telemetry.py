from __future__ import annotations

import os
from unittest.mock import Mock

from bubblehub.app.agents import write_agent_metadata
from bubblehub.app.telemetry import control_snapshot, pending_access


def test_control_snapshot_enriches_scheduler_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    write_agent_metadata("agt-test", name="named agent", root_dir=None, workdir=None, binary="/bin/agent")
    client = Mock()
    client.telemetry_snapshot.return_value = {
        "hardware": {
            "ram_bytes": 16 * 1024**3,
            "vram_bytes": 8 * 1024**3,
            "free_vram_bytes": 6 * 1024**3,
        },
        "limits": {"ram_bytes": 8 * 1024**3, "vram_bytes": 8 * 1024**3},
        "memory_pressure": "available",
        "agents": [{"agent_id": "agt-test", "pid": os.getpid(), "binary": "/bin/agent"}],
        "models": [{"name": "model-a", "pid": os.getpid(), "ram_gb": 4, "vram_gb": 2}],
        "queue": [{"job_id": "job-1"}],
    }

    snapshot = control_snapshot(client)

    assert snapshot["memory_pressure"] == "available"
    assert snapshot["memory"]["ram_total_bytes"] == 8 * 1024**3
    assert snapshot["memory"]["vram_used_bytes"] == 2 * 1024**3
    assert snapshot["agents"][0]["agent_id"] == "agt-test"
    assert snapshot["agents"][0]["display_name"] == "named agent"
    assert snapshot["agents"][0]["rss_bytes"] > 0
    assert snapshot["agents"][0]["pid_role"] == "bubblehub-run-host-process"
    assert snapshot["models"][0]["ram_reserved_bytes"] == 4 * 1024**3
    assert snapshot["queue"] == [{"job_id": "job-1"}]


def test_control_snapshot_includes_stopped_persistent_agents(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "workspace"
    (root / ".bubblehub" / "agents" / "agt-stopped" / "home").mkdir(parents=True)
    (tmp_path / "state" / "sandboxes" / "agt-stopped").mkdir(parents=True)
    write_agent_metadata(
        "agt-stopped",
        name="stopped reviewer",
        root_dir=str(root),
        workdir=str(root),
        binary="/bin/bash",
    )
    client = Mock()
    client.telemetry_snapshot.return_value = {
        "hardware": {"ram_bytes": 16 * 1024**3, "vram_bytes": 0},
        "limits": {},
        "memory_pressure": "available",
        "agents": [],
        "models": [],
        "queue": [],
    }

    snapshot = control_snapshot(client)

    agent = snapshot["agents"][0]
    assert agent["agent_id"] == "agt-stopped"
    assert agent["display_name"] == "stopped reviewer"
    assert agent["running"] is False
    assert agent["status"] == "stopped"
    assert agent["status_color"] == "red"
    assert agent["actions"] == ["delete"]
    assert agent["has_manifest"] is True
    assert agent["has_persistent_sandbox"] is True


def test_control_snapshot_keeps_running_agent_over_stopped_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    write_agent_metadata("agt-test", name="running reviewer", root_dir=None, workdir=None, binary="/bin/agent")
    (tmp_path / "state" / "sandboxes" / "agt-test").mkdir(parents=True)
    client = Mock()
    client.telemetry_snapshot.return_value = {
        "hardware": {"ram_bytes": 16 * 1024**3, "vram_bytes": 0},
        "limits": {},
        "memory_pressure": "available",
        "agents": [{"agent_id": "agt-test", "pid": os.getpid(), "binary": "/bin/agent"}],
        "models": [],
        "queue": [],
    }

    snapshot = control_snapshot(client)

    assert len(snapshot["agents"]) == 1
    assert snapshot["agents"][0]["running"] is True
    assert snapshot["agents"][0]["status_color"] == "green"
    assert snapshot["agents"][0]["actions"] == ["stop", "delete"]


def test_pending_access_returns_native_pending_items() -> None:
    native = Mock()
    native.access_pending.return_value = [{"agent_id": "agt-test"}]
    client = Mock(native=native)

    assert pending_access(client) == [{"agent_id": "agt-test"}]
