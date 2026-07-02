from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, TextIO

import yaml

from bubblehub.cli.interactive import choose_option
from bubblehub.engine.registry import ModelRegistry, ModelSpec
from bubblehub.engine.selector import select_tier
from bubblehub.native import HardwareInfo, detect_hardware

DEFAULT_SETUP_SPECIALITY = "default-instruct"


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
    needs_setup = needs_base_model_setup(speciality, path=user_models_config_path())
    candidates = resolve_setup_candidates(speciality, registry=registry, hardware=hardware) if needs_setup else []
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
        "needs_setup": needs_setup,
        "setup_candidates": [_model_payload(model, selected) for model in candidates],
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


def needs_base_model_setup(speciality: str = DEFAULT_SETUP_SPECIALITY, *, path: Path | None = None) -> bool:
    return not user_chose_base_model(speciality, path=path)


def user_chose_base_model(speciality: str = DEFAULT_SETUP_SPECIALITY, *, path: Path | None = None) -> bool:
    config = read_user_models_config(path)
    specialties = config.get("specialties", {})
    if not isinstance(specialties, dict):
        return False
    specialty_config = specialties.get(speciality, {})
    if not isinstance(specialty_config, dict):
        return False
    model = specialty_config.get("model")
    return isinstance(model, str) and bool(model.strip())


def resolve_setup_candidates(
    speciality: str = DEFAULT_SETUP_SPECIALITY,
    *,
    registry: ModelRegistry | None = None,
    hardware: HardwareInfo | None = None,
) -> list[ModelSpec]:
    registry = registry or ModelRegistry.load_default()
    hardware = hardware or detect_hardware()
    specialty = registry.specialties.get(speciality)
    if specialty is None:
        raise ValueError(f"unknown specialty: {speciality}")
    tier = select_tier(hardware)
    return registry.resolve_candidates(
        speciality,
        tier_order=tier.order,
        capability=specialty.capability,
        max_ram_gb=hardware.ram_bytes / 1024**3,
        max_vram_gb=hardware.vram_bytes / 1024**3,
        supported_gpu_backends=hardware.gpu_backends,
    )


def run_install_base_model_setup(
    speciality: str = DEFAULT_SETUP_SPECIALITY,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> bool:
    """Configure the default base model during install or first app launch."""

    if not needs_base_model_setup(speciality):
        return True

    explicit = os.environ.get("BUBBLEHUB_BASE_MODEL", "").strip()
    if explicit:
        select_model_for_speciality(speciality, explicit)
        return True

    if os.environ.get("BUBBLEHUB_SKIP_MODEL_SETUP", "").strip() == "1":
        return False

    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    if not input_stream.isatty() or not output_stream.isatty():
        return False

    return (
        prompt_base_model_setup(
            speciality,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        is not None
    )


def prompt_base_model_setup(
    speciality: str = DEFAULT_SETUP_SPECIALITY,
    *,
    registry: ModelRegistry | None = None,
    hardware: HardwareInfo | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> str | None:
    registry = registry or ModelRegistry.load_default()
    hardware = hardware or detect_hardware()
    tier = select_tier(hardware)
    recommended = selected_model_name(registry, speciality, tier.order, hardware)
    candidates = resolve_setup_candidates(speciality, registry=registry, hardware=hardware)
    if not candidates:
        raise ValueError("no models fit the current machine for base model setup")

    default_index = next(
        (index for index, model in enumerate(candidates) if model.name == recommended),
        0,
    )
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stderr
    options = tuple(_model_option_label(model, recommended=model.name) for model in candidates)
    choice_index = choose_option(
        title="BubbleHub base model",
        message=(f"Choose the default model for {speciality}.\n" "BubbleHub will use this as your base instruct model on this machine."),
        options=options,
        default_index=default_index,
        input_stream=input_stream,
        output_stream=output_stream,
    )
    model = candidates[choice_index]
    write_speciality_model_override(speciality, model.name, model.capability)
    output_stream.write(f"Saved {speciality} -> {model.name} in {user_models_config_path()}\n")
    output_stream.flush()
    return model.name


def _model_option_label(model: ModelSpec, *, recommended: str | None) -> str:
    suffix = " (recommended)" if model.name == recommended else ""
    return (
        f"{model.name} ({model.flavor}, {model.backend}, {model.tier}, "
        f"RAM {model.ram_gb:g}G, VRAM {model.vram_gb:g}G, ctx {model.context_tokens})"
        f"{suffix}"
    )


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
    return Path.home() / ".config" / "bubblehub" / "models.yaml"


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
