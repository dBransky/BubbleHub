from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ageos.engine.registry import ModelRegistry, ModelSpec
from ageos.engine.selector import select_tier
from ageos.native import HardwareInfo, detect_hardware


def models_overview(
    speciality: str = "default-instruct",
    *,
    registry: ModelRegistry | None = None,
    hardware: HardwareInfo | None = None,
) -> dict[str, object]:
    registry = registry or ModelRegistry.load_default()
    hardware = hardware or detect_hardware()
    tier = select_tier(hardware)
    selected = selected_model_name(registry, speciality, tier.order, hardware)
    return {
        "speciality": speciality,
        "specialty": speciality,
        "tier": {
            "name": tier.name,
            "order": tier.order,
        },
        "hardware": {
            "ram_bytes": hardware.ram_bytes,
            "vram_bytes": hardware.vram_bytes,
            "free_vram_bytes": hardware.free_vram_bytes,
            "gpu_vendor": hardware.gpu_vendor,
            "gpu_name": hardware.gpu_name,
            "gpu_backend": hardware.gpu_backend,
            "gpu_backends": list(hardware.gpu_backends),
            "gpu_compute_capability": hardware.gpu_compute_capability,
            "gpu_device": hardware.gpu_device,
        },
        "selected_model": selected,
        "models": [_model_payload(model, selected) for model in registry.models],
        "specialties": {
            name: {
                "name": specialty.name,
                "capability": specialty.capability,
                "flavor": specialty.flavor,
                "lora": specialty.lora,
                "min_context_tokens": specialty.min_context_tokens,
                "model": specialty.model,
            }
            for name, specialty in sorted(registry.specialties.items())
        },
        "config_path": str(user_models_config_path()),
    }


def select_model_for_speciality(speciality: str, model_name: str) -> dict[str, object]:
    registry = ModelRegistry.load_default()
    matches = [model for model in registry.models if model.name == model_name]
    if not matches:
        raise ValueError(f"unknown model: {model_name}")
    specialty = registry.specialties.get(speciality)
    if specialty is None:
        raise ValueError(f"unknown specialty: {speciality}")
    model = matches[0]
    if model.capability != specialty.capability:
        raise ValueError(f"model {model_name} does not match specialty capability {specialty.capability}")
    write_speciality_model_override(speciality, model.name, model.capability)
    return {
        "speciality": speciality,
        "specialty": speciality,
        "selected_model": model.name,
        "config_path": str(user_models_config_path()),
    }


def selected_model_name(
    registry: ModelRegistry,
    speciality: str,
    tier_order: list[str],
    hardware: HardwareInfo,
) -> str | None:
    try:
        model = registry.resolve_specialty(
            speciality,
            tier_order,
            max_ram_gb=hardware.ram_bytes / 1024**3,
            max_vram_gb=hardware.vram_bytes / 1024**3,
            supported_gpu_backends=hardware.gpu_backends,
        )
    except KeyError:
        return None
    return model.name


def write_speciality_model_override(speciality: str, model_name: str, capability: str) -> None:
    path = user_models_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = read_user_models_config(path)
    specialties = dict(data.get("specialties", {}))
    current = dict(specialties.get(speciality, {}))
    current["capability"] = capability
    current["model"] = model_name
    current.pop("flavor", None)
    current.pop("min_context_tokens", None)
    specialties[speciality] = current
    data["specialties"] = specialties
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def read_user_models_config(path: Path | None = None) -> dict[str, object]:
    path = path or user_models_config_path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def user_models_config_path() -> Path:
    return Path.home() / ".config" / "ageos" / "models.yaml"


def _model_payload(model: ModelSpec, selected: str | None) -> dict[str, Any]:
    return {
        "name": model.name,
        "flavor": model.flavor,
        "capability": model.capability,
        "tier": model.tier,
        "backend": model.backend,
        "repo_id": model.repo_id,
        "filename": model.filename,
        "ram_gb": model.ram_gb,
        "vram_gb": model.vram_gb,
        "context_tokens": model.context_tokens,
        "placement": model.placement,
        "gpu_backends": list(model.gpu_backends),
        "gpu_layers": model.gpu_layers,
        "selected": model.name == selected,
    }
