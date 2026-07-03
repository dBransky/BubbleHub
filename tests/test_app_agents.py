from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bubblehub.app import agents


def test_normalize_agent_name_strips_unsafe_characters_and_limits_length() -> None:
    assert agents.normalize_agent_name("  review\nagent!!  ") == "reviewagent"
    assert agents.normalize_agent_name("!!!") is None
    assert agents.normalize_agent_name("x" * 80) == "x" * 64
    assert agents.normalize_agent_name(None) is None


def test_agent_metadata_roundtrip_and_invalid_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))

    agents.write_agent_metadata("agt-test", name="Agent", root_dir="/repo", workdir="/repo/src", binary="/bin/agent")
    assert agents.read_agent_metadata("agt-test")["workdir"] == "/repo/src"
    assert agents.read_agent_metadata("bad id") == {}

    metadata_path = tmp_path / "state" / "agents" / "agt-bad.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text("not json", encoding="utf-8")
    assert agents.read_agent_metadata("agt-bad") == {}


def test_known_agent_records_merges_metadata_and_manifests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    agents.write_agent_metadata("agt-meta", name="Meta Agent", root_dir=None, workdir=None, binary="/bin/meta")
    (tmp_path / "state" / "sandboxes" / "agt-manifest").mkdir(parents=True)
    (tmp_path / "state" / "sandboxes" / "not an agent").mkdir()

    records = agents.known_agent_records(running_agent_ids={"agt-running"})

    by_id = {str(record["agent_id"]): record for record in records}
    assert set(by_id) == {"agt-manifest", "agt-meta"}
    assert by_id["agt-manifest"]["status"] == "stopped"
    assert by_id["agt-meta"]["display_name"] == "Meta Agent"


def test_enrich_agent_view_prefers_metadata_and_running_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    agents.write_agent_metadata("agt-test", name="Named", root_dir="/repo", workdir="/repo/src", binary="/bin/agent")

    enriched = agents.enrich_agent_view({"agent_id": "agt-test", "running": False})

    assert enriched["display_name"] == "Named"
    assert enriched["root_dir"] == "/repo"
    assert enriched["status"] == "stopped"
    assert enriched["actions"] == ["delete"]


def test_stop_agent_deregisters_and_handles_missing_process(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Mock()
    client.status_snapshot.return_value = {"agents": [{"agent_id": "agt-test", "pid": 999999}]}

    with patch("bubblehub.app.agents.os.kill", side_effect=ProcessLookupError):
        result = agents.stop_agent("agt-test", client)

    assert result == {"agent_id": "agt-test", "pid": 999999, "stopped": False}
    client.deregister_agent.assert_called_once_with("agt-test")


def test_stop_agent_rejects_invalid_or_missing_agent() -> None:
    client = Mock()
    client.status_snapshot.return_value = {"agents": []}

    with pytest.raises(ValueError, match="invalid agent id"):
        agents.stop_agent("bad id", client)
    with pytest.raises(ValueError, match="agent not found"):
        agents.stop_agent("agt-missing", client)


def test_delete_agent_removes_metadata_manifest_persistent_dir_and_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "state"
    root_dir = tmp_path / "repo"
    agent_dir = root_dir / ".bubblehub" / "agents" / "agt-test"
    (agent_dir / "home").mkdir(parents=True)
    marker = root_dir / ".bubblehub" / "current-agent"
    marker.write_text("agt-test", encoding="utf-8")
    manifest_dir = state_dir / "sandboxes" / "agt-test"
    manifest_dir.mkdir(parents=True)
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(state_dir))
    agents.write_agent_metadata("agt-test", name="Agent", root_dir=str(root_dir), workdir=None, binary="/bin/agent")
    client = Mock()
    client.status_snapshot.return_value = {"agents": []}

    result = agents.delete_agent("agt-test", client)

    assert result["deleted"] is True
    assert result["manifest_deleted"] is True
    assert not agent_dir.exists()
    assert not marker.exists()
    assert not (state_dir / "agents" / "agt-test.json").exists()


def test_delete_agent_stops_running_agent_and_ignores_missing_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    client = Mock()
    client.status_snapshot.return_value = {"agents": [{"agent_id": "agt-test", "pid": 0}]}

    with patch("bubblehub.app.agents.stop_agent", return_value={"stopped": True}) as stop:
        result = agents.delete_agent("agt-test", client)

    assert result["deleted"] is False
    stop.assert_called_once_with("agt-test", client)


def test_persistent_agent_dir_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    agent_parent = root / ".bubblehub" / "agents"
    agent_parent.mkdir(parents=True)
    (agent_parent / "agt-test").symlink_to(escaped, target_is_directory=True)

    assert agents._remove_persistent_agent(root, "agt-test") is False


def test_state_root_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert agents._state_root() == tmp_path / "xdg" / "bubblehub"

    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert agents._state_root() == tmp_path / "home" / ".local" / "state" / "bubblehub"

    monkeypatch.delenv("HOME", raising=False)
    assert str(agents._state_root()).endswith(f"bubblehub-{os.getuid()}/state")


def test_agent_from_snapshot_and_int_parsing_helpers() -> None:
    assert agents._agent_from_snapshot("agt-test", {"agents": "bad"}) is None
    assert agents._agent_from_snapshot("agt-test", {"agents": [{"agent_id": "agt-test"}]}) == {"agent_id": "agt-test"}
    assert agents._int_or_zero("42") == 42
    assert agents._int_or_zero("bad") == 0
    assert agents._int_or_zero(None) == 0


def test_read_agent_metadata_ignores_non_object_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "state" / "agents" / "agt-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["not", "object"]), encoding="utf-8")

    assert agents.read_agent_metadata("agt-test") == {}
