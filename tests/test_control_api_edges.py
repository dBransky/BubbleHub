from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import Iterator
from unittest.mock import Mock, patch

import requests

from bubblehub.app.api import ControlApiConfig, create_control_server


@contextmanager
def _running_server(client: Mock | None = None) -> Iterator[tuple[str, Mock]]:
    client = client or _make_client()
    server = create_control_server(ControlApiConfig(port=0), client_factory=lambda: client)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", client
    finally:
        server.shutdown()
        server.server_close()


def test_control_api_serves_configured_static_assets_and_icons(tmp_path: Path, monkeypatch) -> None:
    static_dir = tmp_path / "static"
    icon_dir = tmp_path / "icons"
    static_dir.mkdir()
    icon_dir.mkdir()
    (static_dir / "index.html").write_text("<main>home</main>", encoding="utf-8")
    (static_dir / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (static_dir / "style.css").write_text("body{}", encoding="utf-8")
    (icon_dir / "qwen.svg").write_text("<svg></svg>", encoding="utf-8")
    monkeypatch.setenv("BUBBLEHUB_APP_STATIC_DIR", str(static_dir))
    monkeypatch.setenv("BUBBLEHUB_APP_ICON_DIR", str(icon_dir))

    with _running_server() as (base, _client):
        index = requests.get(f"{base}/", timeout=5)
        script = requests.get(f"{base}/app.js", timeout=5)
        style = requests.get(f"{base}/style.css", timeout=5)
        icon = requests.get(f"{base}/icons/qwen.svg", timeout=5)
        missing_icon = requests.get(f"{base}/icons/unknown.svg", timeout=5)

    assert index.text == "<main>home</main>"
    assert script.headers["content-type"].startswith("application/javascript")
    assert style.headers["content-type"].startswith("text/css")
    assert icon.headers["content-type"] == "image/svg+xml"
    assert missing_icon.status_code == 404


def test_control_api_pending_and_evict_all_endpoints() -> None:
    client = _make_client()
    client.native.access_pending.return_value = [{"agent_id": "agt-test", "kind": "http"}]
    client.status_snapshot.return_value = {"models": [{"name": "small"}, {"name": "medium"}, {"not_name": True}]}

    with _running_server(client) as (base, _client):
        pending = requests.get(f"{base}/api/manifest/pending", timeout=5)
        evicted = requests.post(f"{base}/api/models/evict", json={"all": True}, timeout=5)

    assert pending.json()["pending"] == [{"agent_id": "agt-test", "kind": "http"}]
    assert evicted.json()["evicted"] == ["small", "medium"]
    assert [call.args[0] for call in client.evict_model.call_args_list] == ["small", "medium"]


def test_control_api_reports_bad_requests_and_unknown_routes() -> None:
    with _running_server() as (base, _client):
        missing_body = requests.post(f"{base}/api/models/select", timeout=5)
        non_object = requests.post(f"{base}/api/models/select", json=["small"], timeout=5)
        missing_model = requests.post(f"{base}/api/models/select", json={}, timeout=5)
        invalid_policy = requests.post(
            f"{base}/api/agents/agt-test/manifest/policies",
            json={"policy": "sometimes"},
            timeout=5,
        )
        unknown_get = requests.get(f"{base}/missing", timeout=5)
        unknown_post = requests.post(f"{base}/missing", json={}, timeout=5)

    assert missing_body.status_code == 400
    assert non_object.status_code == 400
    assert missing_model.json()["error"]["message"] == "model_name is required"
    assert invalid_policy.json()["error"]["message"] == "policy must be one of: always, never, ask"
    assert unknown_get.status_code == 404
    assert unknown_post.status_code == 404


def test_control_api_backend_errors_are_structured() -> None:
    with _running_server() as (base, _client), patch("bubblehub.app.api.models_overview", side_effect=RuntimeError("registry down")):
        response = requests.get(f"{base}/api/models", timeout=5)

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "registry down"


def test_control_api_agent_path_validation_errors() -> None:
    with _running_server() as (base, _client):
        response = requests.get(f"{base}/api/agents//manifest", timeout=5)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "agent id is required"


def _make_client() -> Mock:
    native = Mock()
    native.access_pending.return_value = []
    native.access_manifest.return_value = {"policies": []}
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
    return client
