from __future__ import annotations

import os
import uuid
from importlib import resources
from pathlib import Path

import yaml

from bubblehub.log import log_debug, log_info
from bubblehub.native import Admission, NativeScheduler


class SchedulerClient:
    """Thin Python facade over the required native BubbleHub scheduler."""

    @classmethod
    def local(cls) -> "SchedulerClient":
        return cls()

    def __init__(self, native: NativeScheduler | None = None) -> None:
        self.native = native or NativeScheduler()
        self.native.configure_limits(*_configured_limits())

    def admit_model_job(
        self,
        specialty: str,
        model_name: str,
        niceness: int,
        ram_gb: float,
        vram_gb: float,
    ) -> Admission:
        return self.native.admit_model_job(specialty, model_name, niceness, ram_gb, vram_gb)

    def mark_model_loaded(
        self,
        name: str,
        specialty: str,
        backend: str,
        ram_gb: float,
        vram_gb: float,
        pid: int,
        port: int,
    ) -> None:
        self.native.mark_model_loaded(name, specialty, backend, ram_gb, vram_gb, pid, port)

    def mark_model_unloaded(self, name: str) -> None:
        self.native.mark_model_unloaded(name)

    def register_agent(
        self,
        binary: str,
        niceness: int,
        specialty: str | None = None,
        pid: int | None = None,
        agent_id: str | None = None,
    ) -> str:
        agent_id = agent_id or f"agt-{uuid.uuid4().hex[:10]}"
        log_debug("registering agent", f"agent_id={agent_id} binary={binary} specialty={specialty}")
        self.native.register_agent(agent_id, pid or os.getpid(), binary, niceness, specialty)
        log_info("registered agent", agent_id)
        return agent_id

    def deregister_agent(self, agent_id: str) -> None:
        log_debug("deregistering agent", agent_id)
        self.native.deregister_agent(agent_id)

    def queue_snapshot(self) -> list[dict[str, object]]:
        queue = self.native.snapshot().get("queue", [])
        return queue if isinstance(queue, list) else []

    def status_snapshot(self) -> dict[str, object]:
        snapshot = self.native.snapshot()
        return {
            "hardware": snapshot.get("hardware", {}),
            "limits": snapshot.get("limits", {}),
            "memory_pressure": snapshot.get("memory_pressure", "available"),
            "agents": snapshot.get("agents", []),
            "models": snapshot.get("models", []),
            "queue": snapshot.get("queue", []),
        }

    def telemetry_snapshot(self) -> dict[str, object]:
        return self.status_snapshot()

    def resource_limits(self) -> dict[str, object]:
        limits = self.native.snapshot().get("limits", {})
        return limits if isinstance(limits, dict) else {}

    def evict_model(self, name: str) -> None:
        self.native.evict_model(name)

    def inference_chat(self, request: dict[str, object]) -> dict[str, object]:
        return self.native.inference_chat(request)


def _configured_limits() -> tuple[float | None, float | None]:
    with resources.files("bubblehub.config").joinpath("models.yaml").open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    override_path = Path.home() / ".config" / "bubblehub" / "models.yaml"
    if override_path.exists():
        with override_path.open("r", encoding="utf-8") as handle:
            override = yaml.safe_load(handle)
        if isinstance(data, dict) and isinstance(override, dict):
            data = {**data, **override}
    explicit_path = os.environ.get("BUBBLEHUB_MODELS_CONFIG")
    if explicit_path:
        with Path(explicit_path).expanduser().open("r", encoding="utf-8") as handle:
            override = yaml.safe_load(handle)
        if isinstance(data, dict) and isinstance(override, dict):
            data = {**data, **override}
    if not isinstance(data, dict):
        return None, None
    scheduler = data.get("scheduler", {})
    if not isinstance(scheduler, dict):
        return None, None
    return _optional_float(scheduler.get("ram_limit_gb")), _optional_float(scheduler.get("vram_limit_gb"))


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
