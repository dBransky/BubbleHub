from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from ageos.engine.downloader import HfDownloader
from ageos.engine.registry import ModelRegistry, ModelSpec
from ageos.engine.selector import select_tier
from ageos.log import log_debug, log_info


DEFAULT_MAX_OUTPUT_TOKENS = 512
SANDBOX_INFERENCE_TIMEOUT_SECONDS = 120.0


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
        scheduler: object | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.specialty = specialty
        self.niceness = niceness
        self.flavor = flavor
        self.capability = capability
        self.scheduler = scheduler
        self.status_callback = status_callback
        self.resolved: ResolvedSession | None = None
        self._sandbox_base_url: str | None = None

    def __enter__(self) -> "EngineSession":
        if _is_sandboxed():
            self._sandbox_base_url = _sandbox_inference_base_url()
            return self

        if self.scheduler is None:
            self.scheduler = _local_scheduler_client()
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
        log_info("selected model", f"{model.name} backend={model.backend} placement={model.placement}")
        log_debug("ensuring model files", model.repo_id)
        model_path = str(HfDownloader().ensure_model(model))
        log_debug("resolved model path", model_path)
        self.resolved = ResolvedSession(model=model, model_path=model_path)
        return self

    def chat(self, messages: list[dict[str, str]], stream: bool = False, max_tokens: int | None = None) -> str:
        del stream
        if self._sandbox_base_url is not None:
            return _sandbox_chat(
                self._sandbox_base_url,
                self.specialty,
                messages,
                max_tokens=max_tokens or default_max_output_tokens(),
            )
        if self.resolved is None:
            raise RuntimeError("engine session is not started")
        if max_tokens is None:
            max_tokens = default_max_output_tokens()
        model = self.resolved.model
        if self.scheduler is None:
            raise RuntimeError("engine session scheduler is not started")
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
        if self._sandbox_base_url is not None:
            return _sandbox_embeddings(self._sandbox_base_url, self.specialty, inputs)
        raise RuntimeError("native embeddings are not implemented")

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def _status(self, message: str) -> None:
        if self.status_callback is not None:
            self.status_callback(message)


def detect_hardware() -> object:
    from ageos.native import detect_hardware as native_detect_hardware

    return native_detect_hardware()


def _local_scheduler_client() -> object:
    from ageos.node.client import SchedulerClient

    return SchedulerClient.local()


def _is_sandboxed() -> bool:
    return os.environ.get("AGEOS_SANDBOX") == "1"


def _sandbox_inference_base_url() -> str:
    host = os.environ.get("AGEOS_SANDBOX_INFERENCE_HOST")
    port = os.environ.get("AGEOS_SANDBOX_INFERENCE_PORT")
    if not host or not port:
        raise RuntimeError(
            "AGEOS_SANDBOX_INFERENCE_HOST and AGEOS_SANDBOX_INFERENCE_PORT must be set inside the sandbox"
        )
    return f"http://{host}:{_parse_port(port)}"


def _sandbox_chat(
    base_url: str,
    specialty: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
) -> str:
    payload: dict[str, Any] = {
        "model": specialty,
        "ageos_specialty": specialty,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = _post_sandbox_json(f"{base_url}/v1/chat/completions", payload)
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("sandbox inference returned an invalid chat completion response") from exc


def _sandbox_embeddings(base_url: str, specialty: str, inputs: list[str]) -> list[list[float]]:
    payload: dict[str, Any] = {
        "model": specialty,
        "ageos_specialty": specialty,
        "input": inputs,
    }
    data = _post_sandbox_json(f"{base_url}/v1/embeddings", payload)
    try:
        return [list(item["embedding"]) for item in data["data"]]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("sandbox inference returned an invalid embeddings response") from exc


def _post_sandbox_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.post(url, json=payload, timeout=SANDBOX_INFERENCE_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"sandbox inference request failed: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("sandbox inference returned a non-object JSON response")
    return data


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise RuntimeError("AGEOS_SANDBOX_INFERENCE_PORT must be an integer") from None
    if port <= 0 or port > 65535:
        raise RuntimeError("AGEOS_SANDBOX_INFERENCE_PORT must be between 1 and 65535")
    return port

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
