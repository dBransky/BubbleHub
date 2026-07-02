from bubblehub.engine.registry import ModelRegistry


def test_default_registry_resolves_code_specialty() -> None:
    registry = ModelRegistry.load_default()
    model = registry.resolve_specialty("code-review", ["small", "tiny"])
    assert model.capability == "code"
    assert model.tier in {"small", "tiny"}


def test_default_instruct_skips_vllm_and_short_context_models_without_vram() -> None:
    registry = ModelRegistry.load_default()
    model = registry.resolve_specialty(
        "default-instruct",
        ["large", "medium", "small", "tiny"],
        max_ram_gb=128,
        max_vram_gb=0,
    )
    assert model.name == "mistral-instruct-small"
    assert model.backend == "llama"
    assert model.context_tokens >= 8192


def test_default_instruct_prefers_accelerated_llama_for_mid_vram() -> None:
    registry = ModelRegistry.load_default()
    model = registry.resolve_specialty(
        "default-instruct",
        ["large", "medium", "small", "tiny"],
        max_ram_gb=128,
        max_vram_gb=11,
        supported_gpu_backends=("cuda-llama",),
    )
    assert model.name == "qwen-instruct-gpu-small"
    assert model.backend == "llama"
    assert model.placement == "gpu"


def test_default_instruct_prefers_vllm_when_supported() -> None:
    registry = ModelRegistry.load_default()
    model = registry.resolve_specialty(
        "default-instruct",
        ["large", "medium", "small", "tiny"],
        max_ram_gb=128,
        max_vram_gb=24,
        supported_gpu_backends=("vllm", "cuda-llama"),
    )
    assert model.name == "qwen-instruct-large"
    assert model.backend == "vllm"


def test_default_registry_lists_specialties() -> None:
    registry = ModelRegistry.load_default()
    assert "default-instruct" in registry.specialties
    assert "doc-ocr" in registry.specialties


def test_specialty_can_pin_exact_model() -> None:
    registry = ModelRegistry.from_dict(
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
                    "context_tokens": 32768,
                },
                {
                    "name": "medium",
                    "flavor": "mistral",
                    "capability": "instruct",
                    "tier": "medium",
                    "backend": "llama",
                    "repo_id": "repo/medium",
                    "filename": "medium.gguf",
                    "ram_gb": 8,
                    "vram_gb": 0,
                    "context_tokens": 32768,
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

    model = registry.resolve_specialty(
        "default-instruct",
        ["medium", "small"],
        max_ram_gb=16,
        max_vram_gb=0,
    )

    assert model.name == "small"


def test_default_registry_uses_explicit_models_config(tmp_path, monkeypatch) -> None:
    config = tmp_path / "models.yaml"
    config.write_text(
        """
models:
  - name: ci-tiny
    flavor: llama
    capability: instruct
    tier: tiny
    backend: llama
    repo_id: repo/tiny
    filename: tiny.gguf
    ram_gb: 1
    vram_gb: 0
    context_tokens: 512
specialties:
  default-instruct:
    capability: instruct
    model: ci-tiny
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BUBBLEHUB_MODELS_CONFIG", str(config))

    registry = ModelRegistry.load_default()
    model = registry.resolve_specialty(
        "default-instruct",
        ["tiny"],
        max_ram_gb=4,
        max_vram_gb=0,
    )

    assert model.name == "ci-tiny"
