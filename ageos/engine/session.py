from __future__ import annotations

import os
import json
from collections.abc import Callable
from dataclasses import dataclass

from ageos.engine.downloader import HfDownloader
from ageos.engine.registry import ModelRegistry, ModelSpec
from ageos.engine.selector import select_tier
from ageos.native import detect_hardware
from ageos.node.client import SchedulerClient


DEFAULT_MAX_OUTPUT_TOKENS = 512


@dataclass(frozen=True)
class ResolvedSession:
    model: ModelSpec
    model_path: str
    attached: bool = False


class EngineSession:
    def __init__(
        self,
        specialty: str,
        niceness: int = 0,
        flavor: str | None = None,
        capability: str | None = None,
        scheduler: SchedulerClient | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.specialty = specialty
        self.niceness = niceness
        self.flavor = flavor
        self.capability = capability
        self.scheduler = scheduler or SchedulerClient.local()
        self.status_callback = status_callback
        self.resolved: ResolvedSession | None = None

    def __enter__(self) -> "EngineSession":
        registry = ModelRegistry.load_default()
        hardware = detect_hardware()
        limits = self.scheduler.resource_limits()
        max_ram_gb = _limit_gb(limits.get("ram_bytes"), hardware.ram_bytes)
        max_vram_gb = _limit_gb(limits.get("vram_bytes"), hardware.vram_bytes)
        tier = select_tier(hardware)
        candidates = registry.resolve_candidates(
            self.specialty,
            tier_order=tier.order,
            flavor=self.flavor,
            capability=self.capability,
            max_ram_gb=max_ram_gb,
            max_vram_gb=max_vram_gb,
            supported_gpu_backends=hardware.gpu_backends,
        )
        if not candidates:
            raise RuntimeError(f"no model matches specialty '{self.specialty}' for available RAM/VRAM")
        model = candidates[0]
        self._status(f"Selected model {model.name} ({model.backend}, {model.placement})")
        self._status(f"Ensuring model files for {model.repo_id}")
        model_path = str(HfDownloader().ensure_model(model))
        self.resolved = ResolvedSession(model=model, model_path=model_path)
        return self

    def chat(self, messages: list[dict[str, str]], stream: bool = False, max_tokens: int | None = None) -> str:
        del stream
        if self.resolved is None:
            raise RuntimeError("engine session is not started")
        if max_tokens is None:
            max_tokens = default_max_output_tokens()
        model = self.resolved.model
        response = self.scheduler.inference_chat(
            {
                "specialty": self.specialty,
                "model_name": model.name,
                "backend": model.backend,
                "model_path": self.resolved.model_path,
                "ram_gb": model.ram_gb,
                "vram_gb": model.vram_gb,
                "niceness": self.niceness,
                "max_tokens": max_tokens,
                "gpu_layers": model.gpu_layers if model.gpu_layers is not None else -999999,
                "messages_json": json.dumps(messages),
            }
        )
        return str(response["content"])

    def embeddings(self, inputs: list[str]) -> list[list[float]]:
        del inputs
        raise RuntimeError("native embeddings are not implemented")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def _status(self, message: str) -> None:
        if self.status_callback is not None:
            self.status_callback(message)

def _limit_gb(limit_bytes: object, hardware_bytes: int) -> float:
    limit = _int_or_zero(limit_bytes)
    if limit <= 0:
        limit = hardware_bytes
    return limit / 1024**3


def _int_or_zero(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def default_max_output_tokens() -> int:
    value = os.environ.get("AGEOS_MAX_OUTPUT_TOKENS")
    if value is None:
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        parsed = int(value)
    except ValueError:
        raise RuntimeError("AGEOS_MAX_OUTPUT_TOKENS must be an integer") from None
    if parsed <= 0:
        raise RuntimeError("AGEOS_MAX_OUTPUT_TOKENS must be greater than zero")
    return parsed
