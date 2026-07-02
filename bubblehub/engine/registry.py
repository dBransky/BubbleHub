from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelSpec:
    name: str
    flavor: str
    capability: str
    tier: str
    backend: str
    repo_id: str
    filename: str | None
    ram_gb: float
    vram_gb: float
    context_tokens: int = 4096
    placement: str = "auto"
    gpu_backends: tuple[str, ...] = ()
    gpu_layers: int | None = None

    def __post_init__(self) -> None:
        placement = self.placement
        if placement == "auto":
            placement = "gpu" if self.vram_gb > 0 else "cpu"
        object.__setattr__(self, "placement", placement)

        backends: object = self.gpu_backends
        if backends is None:
            normalized: tuple[str, ...] = ()
        elif isinstance(backends, str):
            normalized = tuple(item.strip() for item in backends.split(",") if item.strip())
        else:
            normalized = tuple(str(item) for item in backends)
        if placement == "gpu" and not normalized:
            if self.backend == "vllm":
                normalized = ("vllm",)
            elif self.backend == "llama":
                normalized = ("cuda-llama", "rocm-llama", "vulkan-llama", "sycl-llama")
        object.__setattr__(self, "gpu_backends", normalized)


@dataclass(frozen=True)
class Specialty:
    name: str
    capability: str
    flavor: str | None = None
    lora: str | None = None
    min_context_tokens: int | None = None
    model: str | None = None


class ModelRegistry:
    def __init__(self, models: list[ModelSpec], specialties: dict[str, Specialty]) -> None:
        self.models = models
        self.specialties = specialties

    @classmethod
    def load_default(cls) -> "ModelRegistry":
        with resources.files("bubblehub.config").joinpath("models.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        override = Path.home() / ".config" / "bubblehub" / "models.yaml"
        if override.exists():
            with override.open("r", encoding="utf-8") as handle:
                data = _merge_config(data, yaml.safe_load(handle))
        explicit = os.environ.get("BUBBLEHUB_MODELS_CONFIG")
        if explicit:
            with Path(explicit).expanduser().open("r", encoding="utf-8") as handle:
                data = _merge_config(data, yaml.safe_load(handle))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRegistry":
        models = [ModelSpec(**item) for item in data.get("models", [])]
        specialties = {name: Specialty(name=name, **spec) for name, spec in data.get("specialties", {}).items()}
        return cls(models=models, specialties=specialties)

    def resolve_specialty(
        self,
        name: str,
        tier_order: list[str],
        flavor: str | None = None,
        capability: str | None = None,
        max_ram_gb: float | None = None,
        max_vram_gb: float | None = None,
        supported_gpu_backends: tuple[str, ...] | list[str] | None = None,
    ) -> ModelSpec:
        candidates = self.resolve_candidates(
            name,
            tier_order=tier_order,
            flavor=flavor,
            capability=capability,
            max_ram_gb=max_ram_gb,
            max_vram_gb=max_vram_gb,
            supported_gpu_backends=supported_gpu_backends,
        )
        if not candidates:
            raise KeyError(f"no model matches specialty '{name}' for available RAM/VRAM")
        return candidates[0]

    def resolve_candidates(
        self,
        name: str,
        tier_order: list[str],
        flavor: str | None = None,
        capability: str | None = None,
        max_ram_gb: float | None = None,
        max_vram_gb: float | None = None,
        supported_gpu_backends: tuple[str, ...] | list[str] | None = None,
    ) -> list[ModelSpec]:
        specialty = self.specialties.get(name)
        if specialty is None:
            raise KeyError(f"unknown specialty '{name}'")
        if specialty.model is not None:
            return [
                self._resolve_model_name(
                    specialty.model,
                    specialty=name,
                    max_ram_gb=max_ram_gb,
                    max_vram_gb=max_vram_gb,
                    supported_gpu_backends=supported_gpu_backends,
                )
            ]
        supported = set(supported_gpu_backends) if supported_gpu_backends is not None else None
        target_capability = capability or specialty.capability
        target_flavor = flavor or specialty.flavor
        min_context_tokens = specialty.min_context_tokens
        candidates = [
            model
            for model in self.models
            if model.capability == target_capability
            and (target_flavor is None or model.flavor == target_flavor)
            and (min_context_tokens is None or model.context_tokens >= min_context_tokens)
        ]
        if max_ram_gb is not None:
            candidates = [model for model in candidates if model.ram_gb <= max_ram_gb]
        if max_vram_gb is not None:
            candidates = [model for model in candidates if model.vram_gb <= max_vram_gb]
        candidates = [model for model in candidates if _model_backend_supported(model, supported)]
        if not candidates:
            return []
        rank = {tier: idx for idx, tier in enumerate(tier_order)}
        return sorted(candidates, key=lambda item: (_placement_rank(item), rank.get(item.tier, 999), item.name))

    def _resolve_model_name(
        self,
        name: str,
        *,
        specialty: str,
        max_ram_gb: float | None = None,
        max_vram_gb: float | None = None,
        supported_gpu_backends: tuple[str, ...] | list[str] | None = None,
    ) -> ModelSpec:
        matches = [model for model in self.models if model.name == name]
        if not matches:
            raise KeyError(f"specialty '{specialty}' selects unknown model '{name}'")
        model = matches[0]
        if max_ram_gb is not None and model.ram_gb > max_ram_gb:
            raise KeyError(f"model '{name}' exceeds available RAM for specialty '{specialty}'")
        if max_vram_gb is not None and model.vram_gb > max_vram_gb:
            raise KeyError(f"model '{name}' exceeds available VRAM for specialty '{specialty}'")
        supported = set(supported_gpu_backends) if supported_gpu_backends is not None else None
        if not _model_backend_supported(model, supported):
            raise KeyError(f"model '{name}' requires an unsupported GPU backend for specialty '{specialty}'")
        return model


def _model_backend_supported(model: ModelSpec, supported: set[str] | None) -> bool:
    if model.placement != "gpu" or model.vram_gb <= 0:
        return True
    if supported is None:
        return True
    return bool(supported.intersection(model.gpu_backends))


def _placement_rank(model: ModelSpec) -> int:
    if model.placement != "gpu" or model.vram_gb <= 0:
        return 2
    if model.backend == "vllm":
        return 0
    return 1


def _merge_config(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return base
    merged = dict(base)
    if "models" in override:
        merged["models"] = override["models"]
    if "specialties" in override:
        specialties = dict(base.get("specialties", {}))
        specialties.update(override["specialties"])
        merged["specialties"] = specialties
    return merged
