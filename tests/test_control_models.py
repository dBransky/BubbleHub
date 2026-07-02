from __future__ import annotations

import yaml

from bubblehub.app.models import (
    models_overview,
    needs_base_model_setup,
    prompt_base_model_setup,
    resolve_setup_candidates,
    run_install_base_model_setup,
    select_model_for_speciality,
    selected_model_name,
    user_chose_base_model,
)
from bubblehub.engine.registry import ModelRegistry
from bubblehub.engine.selector import select_tier
from bubblehub.native import HardwareInfo


def _hardware(*, ram_gib: float = 16, vram_gib: float = 0) -> HardwareInfo:
    return HardwareInfo(ram_bytes=int(ram_gib * 1024**3), vram_bytes=int(vram_gib * 1024**3))


def _recommended_model(registry: ModelRegistry, hardware: HardwareInfo, speciality: str = "default-instruct") -> str:
    tier = select_tier(hardware)
    selected = selected_model_name(registry, speciality, tier.order, hardware)
    assert selected is not None
    return selected


def test_models_overview_marks_selected_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = _registry()
    hardware = HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=0)

    overview = models_overview(registry=registry, hardware=hardware)

    assert overview["selected_model"] == "medium"
    assert overview["needs_setup"] is True
    assert [model["name"] for model in overview["setup_candidates"]] == ["medium", "small"]
    selected = [model for model in overview["models"] if model["selected"]]
    assert [model["name"] for model in selected] == ["medium"]


def test_models_overview_skips_setup_when_user_override_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("bubblehub.app.models.ModelRegistry.load_default", lambda: _registry())
    select_model_for_speciality("default-instruct", "medium")

    overview = models_overview(hardware=HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=0))

    assert overview["needs_setup"] is False
    assert overview["setup_candidates"] == []
    assert overview["selected_model"] == "medium"


def test_select_model_for_speciality_writes_user_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("bubblehub.app.models.ModelRegistry.load_default", lambda: _registry())

    result = select_model_for_speciality("default-instruct", "medium")

    assert result["selected_model"] == "medium"
    config = yaml.safe_load((tmp_path / ".config" / "bubblehub" / "models.yaml").read_text(encoding="utf-8"))
    assert config["specialties"]["default-instruct"] == {
        "capability": "instruct",
        "model": "medium",
    }


def test_select_model_rejects_capability_mismatch(monkeypatch) -> None:
    monkeypatch.setattr("bubblehub.app.models.ModelRegistry.load_default", lambda: _registry())

    try:
        select_model_for_speciality("default-instruct", "code")
    except ValueError as exc:
        assert "does not match specialty capability" in str(exc)
    else:
        raise AssertionError("expected capability mismatch")


def test_user_chose_base_model_tracks_explicit_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("bubblehub.app.models.ModelRegistry.load_default", lambda: _registry())

    assert user_chose_base_model("default-instruct") is False
    assert needs_base_model_setup("default-instruct") is True

    select_model_for_speciality("default-instruct", "medium")

    assert user_chose_base_model("default-instruct") is True
    assert needs_base_model_setup("default-instruct") is False


def test_run_install_base_model_setup_respects_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BUBBLEHUB_BASE_MODEL", "medium")
    monkeypatch.setattr("bubblehub.app.models.ModelRegistry.load_default", lambda: _registry())

    assert run_install_base_model_setup("default-instruct") is True
    assert user_chose_base_model("default-instruct") is True


def test_run_install_base_model_setup_prompts_on_tty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = _registry()
    hardware = _hardware(ram_gib=16)
    monkeypatch.setattr("bubblehub.app.models.ModelRegistry.load_default", lambda: registry)
    output = __import__("io").StringIO()
    recommended = _recommended_model(registry, hardware)

    selected = prompt_base_model_setup(
        "default-instruct",
        registry=registry,
        hardware=hardware,
        input_stream=__import__("io").StringIO("\n"),
        output_stream=output,
    )

    assert selected == recommended
    assert user_chose_base_model("default-instruct") is True
    assert f"\x1b[32m> {recommended}" in output.getvalue()


def test_prompt_base_model_setup_recommends_smaller_model_on_small_machine(tmp_path) -> None:
    registry = _registry()
    hardware = _hardware(ram_gib=8)

    selected = prompt_base_model_setup(
        "default-instruct",
        registry=registry,
        hardware=hardware,
        input_stream=__import__("io").StringIO("\n"),
        output_stream=__import__("io").StringIO(),
    )

    assert selected == "small"
    assert _recommended_model(registry, hardware) == "small"


def test_prompt_base_model_setup_can_select_non_default_candidate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = _registry()
    hardware = _hardware(ram_gib=16)
    candidates = resolve_setup_candidates("default-instruct", registry=registry, hardware=hardware)
    assert [model.name for model in candidates] == ["medium", "small"]

    selected = prompt_base_model_setup(
        "default-instruct",
        registry=registry,
        hardware=hardware,
        input_stream=__import__("io").StringIO("\x1b[B\n"),
        output_stream=__import__("io").StringIO(),
    )

    assert selected == "small"
    assert user_chose_base_model("default-instruct") is True


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
                }
            },
        }
    )
