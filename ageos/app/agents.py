from __future__ import annotations

import json
import os
import re
import shutil
import signal
from pathlib import Path
from typing import Any

from ageos.node.client import SchedulerClient

_AGENT_ID_RE = re.compile(r"^agt-[A-Za-z0-9_-]+$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_. -]+")
_MAX_AGENT_NAME = 64


def normalize_agent_name(name: str | None) -> str | None:
    if name is None:
        return None
    normalized = _SAFE_NAME_RE.sub("", name.strip())
    normalized = " ".join(normalized.split())
    return normalized[:_MAX_AGENT_NAME] or None


def write_agent_metadata(
    agent_id: str,
    *,
    name: str | None,
    root_dir: str | None,
    workdir: str | None,
    binary: str,
) -> None:
    _validate_agent_id(agent_id)
    path = _agent_metadata_path(agent_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "agent_id": agent_id,
        "name": normalize_agent_name(name),
        "root_dir": root_dir,
        "workdir": workdir,
        "binary": binary,
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def read_agent_metadata(agent_id: str) -> dict[str, object]:
    if not _AGENT_ID_RE.fullmatch(agent_id):
        return {}
    try:
        data = json.loads(_agent_metadata_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def known_agent_records(running_agent_ids: set[str] | None = None) -> list[dict[str, object]]:
    running_agent_ids = running_agent_ids or set()
    records: dict[str, dict[str, object]] = {}
    for path in _agent_metadata_dir().glob("*.json"):
        agent_id = path.stem
        if not _AGENT_ID_RE.fullmatch(agent_id) or agent_id in running_agent_ids:
            continue
        metadata = read_agent_metadata(agent_id)
        if metadata:
            records[agent_id] = _stopped_agent_record(agent_id, metadata)
    manifest_paths = _manifest_root().iterdir() if _manifest_root().is_dir() else []
    for path in manifest_paths:
        agent_id = path.name
        if not _AGENT_ID_RE.fullmatch(agent_id) or agent_id in running_agent_ids or agent_id in records:
            continue
        records[agent_id] = _stopped_agent_record(agent_id, {"agent_id": agent_id})
    return sorted(records.values(), key=lambda item: str(item.get("display_name") or item.get("agent_id", "")))


def enrich_agent_view(agent: dict[str, object]) -> dict[str, object]:
    agent_id = str(agent.get("agent_id", ""))
    metadata = read_agent_metadata(agent_id)
    name = normalize_agent_name(str(metadata.get("name", ""))) if metadata.get("name") else None
    enriched = {**agent}
    if name is not None:
        enriched["name"] = name
    if metadata.get("root_dir"):
        enriched["root_dir"] = str(metadata["root_dir"])
    if metadata.get("workdir"):
        enriched["workdir"] = str(metadata["workdir"])
    enriched["display_name"] = name or agent_id
    running = bool(enriched.get("running", True))
    enriched["running"] = running
    enriched["status"] = "running" if running else "stopped"
    enriched["status_color"] = "green" if running else "red"
    enriched["actions"] = ["stop", "delete"] if running else ["delete"]
    return enriched


def stop_agent(agent_id: str, client: SchedulerClient | None = None) -> dict[str, object]:
    _validate_agent_id(agent_id)
    client = client or SchedulerClient.local()
    agent = _agent_from_snapshot(agent_id, client.status_snapshot())
    if agent is None:
        raise ValueError(f"agent not found: {agent_id}")
    pid = _int_or_zero(agent.get("pid"))
    stopped = False
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except ProcessLookupError:
            stopped = False
    client.deregister_agent(agent_id)
    return {"agent_id": agent_id, "pid": pid, "stopped": stopped}


def delete_agent(agent_id: str, client: SchedulerClient | None = None) -> dict[str, object]:
    _validate_agent_id(agent_id)
    client = client or SchedulerClient.local()
    snapshot = client.status_snapshot()
    if _agent_from_snapshot(agent_id, snapshot) is not None:
        stop_agent(agent_id, client)
    metadata = read_agent_metadata(agent_id)
    root_dir = str(metadata.get("root_dir") or "")
    removed = _remove_persistent_agent(Path(root_dir), agent_id) if root_dir else False
    manifest_removed = _remove_access_manifest(agent_id)
    if root_dir:
        _remove_current_agent_marker(Path(root_dir), agent_id)
    try:
        _agent_metadata_path(agent_id).unlink()
    except FileNotFoundError:
        pass
    return {"agent_id": agent_id, "root_dir": root_dir, "deleted": removed, "manifest_deleted": manifest_removed}


def _agent_metadata_path(agent_id: str) -> Path:
    return _agent_metadata_dir() / f"{agent_id}.json"


def _agent_metadata_dir() -> Path:
    return _state_root() / "agents"


def _manifest_root() -> Path:
    return _state_root() / "sandboxes"


def _state_root() -> Path:
    if os.environ.get("AGEOS_STATE_DIR"):
        return Path(os.environ["AGEOS_STATE_DIR"]).expanduser()
    if os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]).expanduser() / "ageos"
    if os.environ.get("HOME"):
        return Path(os.environ["HOME"]).expanduser() / ".local" / "state" / "ageos"
    return Path(f"/tmp/ageos-{os.getuid()}") / "state"


def _agent_from_snapshot(agent_id: str, snapshot: dict[str, object]) -> dict[str, object] | None:
    agents = snapshot.get("agents", [])
    if not isinstance(agents, list):
        return None
    for item in agents:
        if isinstance(item, dict) and item.get("agent_id") == agent_id:
            return item
    return None


def _remove_persistent_agent(root: Path, agent_id: str) -> bool:
    agent_dir = root / ".ageos" / "agents" / agent_id
    if not _is_persistent_agent_dir(agent_dir):
        return False
    agents_dir = agent_dir.parent.resolve()
    resolved_agent = agent_dir.resolve()
    if resolved_agent.parent != agents_dir:
        raise ValueError("persistent sandbox path escaped .ageos/agents")
    shutil.rmtree(resolved_agent)
    return True


def _remove_current_agent_marker(root: Path, agent_id: str) -> None:
    marker = root / ".ageos" / "current-agent"
    try:
        current = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if current == agent_id:
        marker.unlink(missing_ok=True)


def _remove_access_manifest(agent_id: str) -> bool:
    manifest_dir = _state_root() / "sandboxes" / agent_id
    if manifest_dir.exists() and not manifest_dir.is_symlink():
        shutil.rmtree(manifest_dir)
        return True
    return False


def _stopped_agent_record(agent_id: str, metadata: dict[str, object]) -> dict[str, object]:
    enriched = enrich_agent_view(
        {
            "agent_id": agent_id,
            "pid": 0,
            "binary": str(metadata.get("binary") or ""),
            "niceness": "",
            "specialty": str(metadata.get("specialty") or ""),
            "running": False,
            "root_dir": str(metadata.get("root_dir") or ""),
            "workdir": str(metadata.get("workdir") or ""),
            "rss_bytes": 0,
            "cpu_time_seconds": 0.0,
            "resource_metrics": {
                "available": False,
                "rss_bytes": 0,
                "cpu_time_seconds": 0.0,
                "status": "stopped",
            },
        }
    )
    enriched["has_manifest"] = (_manifest_root() / agent_id).is_dir()
    root_dir = str(enriched.get("root_dir") or "")
    enriched["has_persistent_sandbox"] = bool(root_dir and _is_persistent_agent_dir(Path(root_dir) / ".ageos" / "agents" / agent_id))
    return enriched


def _is_persistent_agent_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and not path.is_symlink()
        and _AGENT_ID_RE.fullmatch(path.name)
        and (path / "home").is_dir()
        and not (path / "home").is_symlink()
    )


def _validate_agent_id(agent_id: str) -> None:
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise ValueError(f"invalid agent id: {agent_id}")


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
