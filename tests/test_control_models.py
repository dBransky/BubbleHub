from __future__ import annotations

import yaml

from ageos.app.models import models_overview, select_model_for_speciality
from ageos.engine.registry import ModelRegistry
from ageos.native import HardwareInfo


def test_models_overview_marks_selected_model() -> None:
    registry = _registry()
    hardware = HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=0)

    overview = models_overview(registry=registry, hardware=hardware)

    assert overview["selected_model"] == "small"
    selected = [model for model in overview["models"] if model["selected"]]
    assert [model["name"] for model in selected] == ["small"]


def test_select_model_for_speciality_writes_user_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("ageos.app.models.ModelRegistry.load_default", lambda: _registry())

    result = select_model_for_speciality("default-instruct", "medium")

    assert result["selected_model"] == "medium"
    config = yaml.safe_load((tmp_path / ".config" / "ageos" / "models.yaml").read_text(encoding="utf-8"))
    assert config["specialties"]["default-instruct"] == {
        "capability": "instruct",
        "model": "medium",
    }


def test_select_model_rejects_capability_mismatch(monkeypatch) -> None:
    monkeypatch.setattr("ageos.app.models.ModelRegistry.load_default", lambda: _registry())

    try:
        select_model_for_speciality("default-instruct", "code")
    except ValueError as exc:
        assert "does not match specialty capability" in str(exc)
    else:
        raise AssertionError("expected capability mismatch")


def _registry() -> ModelRegistry:
    return ModelRegistry.from_dict(
        {
            "models": [
                {
                    "name": "small",
                    "flavor": "qwen",
                    "capability": "instruct",
                    "tier": "small",
                    "backend": "llama",
                    "repo_id": "repo/small",
                    "filename": "small.gguf",
                    "ram_gb": 4,
                    "vram_gb": 0,
                    "context_tokens": 8192,
                },
                {
                    "name": "medium",
                    "flavor": "qwen",
                    "capability": "instruct",
                    "tier": "medium",
                    "backend": "llama",
                    "repo_id": "repo/medium",
                    "filename": "medium.gguf",
                    "ram_gb": 8,
                    "vram_gb": 0,
                    "context_tokens": 8192,
                },
                {
                    "name": "code",
                    "flavor": "qwen",
                    "capability": "code",
                    "tier": "small",
                    "backend": "llama",
                    "repo_id": "repo/code",
                    "filename": "code.gguf",
                    "ram_gb": 4,
                    "vram_gb": 0,
                    "context_tokens": 8192,
                },
            ],
            "specialties": {
                "default-instruct": {
                    "capability": "instruct",
                    "model": "small",
                }
            },
        }
    )
