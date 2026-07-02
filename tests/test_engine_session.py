from __future__ import annotations

from pathlib import Path

import pytest

from bubblehub.engine.registry import ModelSpec
from bubblehub.engine.session import EngineSession
from bubblehub.native import HardwareInfo

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


def test_engine_session_forwards_chat_to_sandbox_endpoint(monkeypatch) -> None:
    import bubblehub.engine.session as session_module

    calls: list[dict[str, object]] = []

    def post(url: str, *, json: dict[str, object], timeout: float) -> FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse({"choices": [{"message": {"content": "sandbox"}}]})

    monkeypatch.setenv("BUBBLEHUB_SANDBOX", "1")
    monkeypatch.setenv("BUBBLEHUB_SANDBOX_INFERENCE_HOST", "127.0.0.1")
    monkeypatch.setenv("BUBBLEHUB_SANDBOX_INFERENCE_PORT", "8123")
    monkeypatch.setattr(session_module.requests, "post", post)
    monkeypatch.setattr(
        session_module,
        "_local_scheduler_client",
        lambda: pytest.fail("sandbox sessions must not initialize the native scheduler"),
    )

    with EngineSession("default-instruct") as session:
        assert session.chat([{"role": "user", "content": "hi"}], max_tokens=8) == "sandbox"

    assert calls == [
        {
            "url": "http://127.0.0.1:8123/v1/chat/completions",
            "json": {
                "model": "default-instruct",
                "bubblehub_specialty": "default-instruct",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 8,
                "stream": False,
            },
            "timeout": session_module.SANDBOX_INFERENCE_TIMEOUT_SECONDS,
        }
    ]


def test_engine_session_requires_sandbox_inference_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_SANDBOX", "1")
    monkeypatch.delenv("BUBBLEHUB_SANDBOX_INFERENCE_HOST", raising=False)
    monkeypatch.delenv("BUBBLEHUB_SANDBOX_INFERENCE_PORT", raising=False)

    with pytest.raises(RuntimeError, match="BUBBLEHUB_SANDBOX_INFERENCE_HOST"):
        with EngineSession("default-instruct"):
            pass


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


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class FakeDownloader:
    def ensure_model(self, model: ModelSpec) -> Path:
        return Path(f"/models/{model.name}")


def _patch_session_dependencies(
    monkeypatch,
    candidates: list[ModelSpec],
) -> None:
    import bubblehub.engine.session as session_module

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
