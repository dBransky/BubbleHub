from __future__ import annotations

import os
from threading import Thread
from unittest.mock import Mock, patch

import requests

from bubblehub.app.api import ControlApiConfig, create_control_server


def test_control_api_serves_health_and_telemetry() -> None:
    server, client = _server()
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        health = requests.get(f"http://127.0.0.1:{port}/health", timeout=5)
        telemetry = requests.get(f"http://127.0.0.1:{port}/api/telemetry", timeout=5)
    finally:
        server.shutdown()
        server.server_close()

    assert health.status_code == 200
    assert health.json()["service"] == "bubblehub"
    assert telemetry.status_code == 200
    assert telemetry.json()["agents"][0]["agent_id"] == "agt-test"
    client.telemetry_snapshot.assert_called()


def test_control_api_reads_and_updates_agent_manifest() -> None:
    server, client = _server()
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        manifest = requests.get(f"http://127.0.0.1:{port}/api/agents/agt-test/manifest", timeout=5)
        updated = requests.post(
            f"http://127.0.0.1:{port}/api/agents/agt-test/manifest/policies",
            json={
                "kind": "http",
                "subject": "example.com",
                "method": "*",
                "path": "*",
                "policy": "always",
            },
            timeout=5,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert manifest.status_code == 200
    assert manifest.json()["manifest"]["policies"][0]["subject"] == "example.com"
    assert updated.status_code == 200
    client.native.apply_access_policy.assert_called_once_with(
        "agt-test",
        kind="http",
        subject="example.com",
        method="*",
        path="*",
        policy="always",
    )


def test_control_api_model_actions() -> None:
    server, client = _server()
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("bubblehub.app.api.select_model_for_speciality") as select_model:
            select_model.return_value = {"selected_model": "small"}
            selected = requests.post(
                f"http://127.0.0.1:{port}/api/models/select",
                json={"speciality": "default-instruct", "model_name": "small"},
                timeout=5,
            )
        evicted = requests.post(f"http://127.0.0.1:{port}/api/models/evict", json={"name": "small"}, timeout=5)
    finally:
        server.shutdown()
        server.server_close()

    assert selected.status_code == 200
    assert selected.json()["selected_model"] == "small"
    assert evicted.status_code == 200
    client.evict_model.assert_called_once_with("small")


def test_control_api_agent_stop_and_delete_actions() -> None:
    server, _client = _server()
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with (
            patch("bubblehub.app.api.stop_agent", return_value={"agent_id": "agt-test", "stopped": True}) as stop,
            patch("bubblehub.app.api.delete_agent", return_value={"agent_id": "agt-test", "deleted": True}) as delete,
        ):
            stopped = requests.post(f"http://127.0.0.1:{port}/api/agents/agt-test/stop", json={}, timeout=5)
            deleted = requests.post(f"http://127.0.0.1:{port}/api/agents/agt-test/delete", json={}, timeout=5)
    finally:
        server.shutdown()
        server.server_close()

    assert stopped.status_code == 200
    assert stopped.json()["stopped"] is True
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    stop.assert_called_once()
    delete.assert_called_once()


def _server() -> tuple[object, Mock]:
    native = Mock()
    native.access_pending.return_value = []
    native.access_manifest.return_value = {
        "policies": [
            {
                "kind": "http",
                "subject": "example.com",
                "method": "*",
                "path": "*",
                "policy": "ask",
            }
        ]
    }
    client = Mock(native=native)
    client.telemetry_snapshot.return_value = {
        "hardware": {"ram_bytes": 16 * 1024**3, "vram_bytes": 0},
        "limits": {},
        "memory_pressure": "available",
        "agents": [{"agent_id": "agt-test", "pid": os.getpid(), "binary": "/bin/agent"}],
        "models": [{"name": "small", "pid": os.getpid(), "ram_gb": 4, "vram_gb": 0}],
        "queue": [],
    }
    client.status_snapshot.return_value = {"models": [{"name": "small"}]}
    server = create_control_server(ControlApiConfig(port=0), client_factory=lambda: client)
    return server, client
