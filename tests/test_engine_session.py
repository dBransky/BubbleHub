from __future__ import annotations

from pathlib import Path

from ageos.engine.registry import ModelSpec
from ageos.engine.session import EngineSession
from ageos.native import HardwareInfo


GPU_MODEL = ModelSpec(
    name="gpu-model",
    flavor="qwen",
    capability="instruct",
    tier="small",
    backend="llama",
    repo_id="repo/gpu",
    filename="gpu.gguf",
    ram_gb=8,
    vram_gb=6,
    context_tokens=32768,
    placement="gpu",
)
CPU_MODEL = ModelSpec(
    name="cpu-model",
    flavor="mistral",
    capability="instruct",
    tier="small",
    backend="llama",
    repo_id="repo/cpu",
    filename="cpu.gguf",
    ram_gb=8,
    vram_gb=0,
    context_tokens=32768,
)
VLLM_MODEL = ModelSpec(
    name="vllm-model",
    flavor="qwen",
    capability="instruct",
    tier="large",
    backend="vllm",
    repo_id="repo/vllm",
    filename=None,
    ram_gb=16,
    vram_gb=12,
    context_tokens=32768,
    placement="gpu",
)


def test_engine_session_calls_native_inference(monkeypatch) -> None:
    scheduler = FakeScheduler()
    _patch_session_dependencies(monkeypatch, [GPU_MODEL, CPU_MODEL])

    with EngineSession("default-instruct", scheduler=scheduler) as session:
        assert session.resolved is not None
        assert session.resolved.model.name == "gpu-model"
        assert session.chat([{"role": "user", "content": "hi"}], max_tokens=42) == "native"

    assert scheduler.requests == [
        {
            "specialty": "default-instruct",
            "model_name": "gpu-model",
            "backend": "llama",
            "model_path": "/models/gpu-model",
            "ram_gb": 8,
            "vram_gb": 6,
            "niceness": 0,
            "max_tokens": 42,
            "gpu_layers": -999999,
            "messages_json": '[{"role": "user", "content": "hi"}]',
        }
    ]


def test_engine_session_does_not_mark_python_model_lifecycle(monkeypatch) -> None:
    scheduler = FakeScheduler()
    _patch_session_dependencies(monkeypatch, [CPU_MODEL])

    with EngineSession("default-instruct", scheduler=scheduler) as session:
        session.chat([{"role": "user", "content": "hi"}])

    assert scheduler.loaded == []
    assert scheduler.unloaded == []
    assert scheduler.evicted == []


class FakeRegistry:
    def __init__(self, candidates: list[ModelSpec]) -> None:
        self.candidates = candidates

    def resolve_candidates(self, *args: object, **kwargs: object) -> list[ModelSpec]:
        return self.candidates


class FakeScheduler:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
        self.evicted: list[str] = []
        self.requests: list[dict[str, object]] = []

    def resource_limits(self) -> dict[str, int]:
        return {"ram_bytes": 64 * 1024**3, "vram_bytes": 24 * 1024**3}

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
        self.loaded.append(name)

    def mark_model_unloaded(self, name: str) -> None:
        self.unloaded.append(name)

    def evict_model(self, name: str) -> None:
        self.evicted.append(name)

    def inference_chat(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        return {"content": "native"}


class FakeDownloader:
    def ensure_model(self, model: ModelSpec) -> Path:
        return Path(f"/models/{model.name}")


def _patch_session_dependencies(
    monkeypatch,
    candidates: list[ModelSpec],
) -> None:
    import ageos.engine.session as session_module

    monkeypatch.setattr(session_module.ModelRegistry, "load_default", lambda: FakeRegistry(candidates))
    monkeypatch.setattr(
        session_module,
        "detect_hardware",
        lambda: HardwareInfo(
            ram_bytes=64 * 1024**3,
            vram_bytes=24 * 1024**3,
            free_vram_bytes=22 * 1024**3,
            gpu_vendor="nvidia",
            gpu_backend="vllm",
            gpu_backends=("vllm", "cuda-llama"),
        ),
    )
    monkeypatch.setattr(session_module, "HfDownloader", FakeDownloader)
