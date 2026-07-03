from __future__ import annotations

import ctypes
import json
from unittest.mock import Mock, patch

import pytest

import bubblehub.native as native
from bubblehub.native import LibBubbleError, NativeScheduler


class _CFunc:
    def __init__(self, impl):
        self.impl = impl

    def __call__(self, *args):
        return self.impl(*args)


class _FakeLib:
    def __init__(self) -> None:
        self.buffers: list[ctypes.Array[ctypes.c_char]] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.next_result = 0
        self.snapshot_payload: object = {"models": []}
        self.inference_payload: object = {"message": {"content": "ok"}}
        self.pending_payload: object = [{"agent_id": "agt-test"}]
        self.manifest_payload: object = {"policies": []}

        self.bubblehub_scheduler_admit_model_job = _CFunc(self._admit)
        self.bubblehub_scheduler_configure_limits = _CFunc(self._record_ok("configure_limits"))
        self.bubblehub_scheduler_register_agent = _CFunc(self._record_ok("register_agent"))
        self.bubblehub_scheduler_deregister_agent = _CFunc(self._record_ok("deregister_agent"))
        self.bubblehub_scheduler_mark_model_loaded = _CFunc(self._record_ok("mark_model_loaded"))
        self.bubblehub_scheduler_mark_model_unloaded = _CFunc(self._record_ok("mark_model_unloaded"))
        self.bubblehub_scheduler_evict_model = _CFunc(self._record_ok("evict_model"))
        self.bubblehub_scheduler_add_queue_item = _CFunc(self._record_ok("add_queue_item"))
        self.bubblehub_scheduler_snapshot_json = _CFunc(lambda: self._json_pointer(self.snapshot_payload))
        self.bubblehub_scheduler_free_string = _CFunc(lambda pointer: self.calls.append(("free_scheduler", (pointer,))))
        self.bubblehub_inference_chat_json = _CFunc(lambda raw: self._json_pointer(self.inference_payload))
        self.bubblehub_access_pending_json = _CFunc(lambda: self._json_pointer(self.pending_payload))
        self.bubblehub_access_manifest_json = _CFunc(lambda agent_id: self._json_pointer(self.manifest_payload))
        self.bubblehub_access_free_string = _CFunc(lambda pointer: self.calls.append(("free_access", (pointer,))))
        self.bubblehub_access_apply_policy = _CFunc(self._record_ok("apply_access_policy"))
        self.bubblehub_sandbox_run = _CFunc(self._sandbox_run)

    def _record_ok(self, name: str):
        def impl(*args):
            self.calls.append((name, args))
            return self.next_result

        return impl

    def _admit(self, specialty, model_name, niceness, ram_gb, vram_gb, allowed, state, state_size, reason, reason_size):
        self.calls.append(("admit", (specialty, model_name, niceness, ram_gb, vram_gb)))
        if self.next_result != 0:
            return self.next_result
        allowed._obj.value = 1
        state.value = b"admitted"
        reason.value = b"fits"
        return 0

    def _sandbox_run(self, config_pointer):
        config = ctypes.cast(config_pointer, ctypes.POINTER(native.SandboxConfig)).contents
        self.calls.append(("sandbox_run", (config.binary, config.memory_max, config.isolate_network)))
        return 7

    def _json_pointer(self, payload: object):
        if payload is None:
            return None
        raw = json.dumps(payload).encode("utf-8")
        buffer = ctypes.create_string_buffer(raw)
        self.buffers.append(buffer)
        return ctypes.cast(buffer, ctypes.c_void_p).value


def test_detect_hardware_uses_native_ram_and_env_gpu_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    lib = Mock()
    lib.bubblehub_hw_total_ram_bytes.return_value = 32 * 1024**3
    lib.bubblehub_hw_vram_bytes.return_value = 12 * 1024**3
    lib.bubblehub_hw_free_vram_bytes.return_value = 8 * 1024**3
    monkeypatch.setenv("BUBBLEHUB_GPU_BACKEND", "vllm")
    monkeypatch.setenv("BUBBLEHUB_GPU_BACKENDS", "cuda-llama")
    monkeypatch.setenv("BUBBLEHUB_GPU_VENDOR", "nvidia")
    monkeypatch.setenv("BUBBLEHUB_GPU_NAME", "RTX")
    monkeypatch.setenv("BUBBLEHUB_GPU_COMPUTE_CAPABILITY", "8.9")
    monkeypatch.setattr(native, "_load_libbubble", lambda: lib)

    hardware = native.detect_hardware()

    assert hardware.ram_bytes == 32 * 1024**3
    assert hardware.gpu_backend == "vllm"
    assert hardware.gpu_backends == ("vllm", "cuda-llama")
    assert hardware.gpu_compute_capability == "8.9"


def test_gpu_detection_helpers_parse_tool_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        native,
        "_run_text",
        lambda command: {
            "nvidia-smi": "slow, 8192, 1024, 7.5\nfast, 24576, 20480, 8.9\n",
            "rocm-smi": "Card series: Radeon Test\n",
            "sycl-ls": "[opencl:gpu:0] Intel GPU\n",
            "vulkaninfo": "deviceName = Vulkan Card\n",
        }.get(command[0], ""),
    )

    nvidia = native._detect_nvidia_gpu()
    assert nvidia is not None
    assert nvidia["gpu_backend"] == "vllm"
    assert nvidia["gpu_device"] == "1"
    assert native._detect_amd_gpu(10, 0)["gpu_name"] == "Radeon Test"  # type: ignore[index]
    assert native._detect_intel_gpu(10, 5)["gpu_backend"] == "sycl-llama"  # type: ignore[index]
    assert native._detect_vulkan_gpu(10, 5)["gpu_name"] == "Vulkan Card"  # type: ignore[index]


def test_native_scheduler_success_paths_and_json_freeing(monkeypatch: pytest.MonkeyPatch) -> None:
    lib = _FakeLib()
    scheduler = NativeScheduler(lib)
    monkeypatch.setenv("BUBBLEHUB_AGENT_ID", "agt-native")

    admission = scheduler.admit_model_job("default", "small", 1, 2.5, 0)
    scheduler.configure_limits(8, None)
    scheduler.register_agent("agt-test", 123, "/bin/agent", 0, None)
    scheduler.deregister_agent("agt-test")
    scheduler.mark_model_loaded("small", "default", "llama", 2, 0, 123, 51000)
    scheduler.mark_model_unloaded("small")
    scheduler.evict_model("small")
    scheduler.add_queue_item("job-1", "model", "default", "small", 5, "waiting")
    scheduler.apply_access_policy("agt-test", kind="http", subject="example.com", method="GET", path="/", policy="always")
    sandbox_status = scheduler.run_sandbox_in_process(
        "/bin/echo",
        ["/bin/echo", "ok"],
        resource_niceness=2,
        memory_max=4096,
        cpu_percent=80,
        workdir="/work",
        isolate_network=True,
        root_dir="/root",
        rootfs_dir="/rootfs",
        overlay_upper_dir="/upper",
        overlay_work_dir="/workdir",
        inference_host="127.0.0.1",
        inference_port=8000,
        sandbox_inference_port=18000,
        sandbox_http_proxy_port=18080,
    )

    assert admission.state == "admitted"
    assert scheduler.snapshot() == {"models": []}
    assert scheduler.inference_chat({"messages": []}) == {"message": {"content": "ok"}}
    assert scheduler.access_pending() == [{"agent_id": "agt-test"}]
    assert scheduler.access_manifest("agt-test") == {"policies": []}
    assert sandbox_status == 7
    assert ("free_scheduler",) == tuple(name for name, _ in lib.calls if name == "free_scheduler")[:1]
    assert any(name == "free_access" for name, _ in lib.calls)


@pytest.mark.parametrize(
    ("method_name", "args", "message"),
    [
        ("configure_limits", (1, 1), "configure resource limits"),
        ("register_agent", ("agt-test", 1, "/bin/agent", 0, None), "register agent"),
        ("deregister_agent", ("agt-test",), "deregister agent"),
        ("mark_model_loaded", ("small", "default", "llama", 1, 0, 1, 1), "mark model loaded"),
        ("mark_model_unloaded", ("small",), "mark model unloaded"),
        ("evict_model", ("small",), "evict model"),
        ("add_queue_item", ("job", "model", "default", "small", 0, "waiting"), "add queue item"),
    ],
)
def test_native_scheduler_raises_on_nonzero_native_results(method_name: str, args: tuple[object, ...], message: str) -> None:
    lib = _FakeLib()
    lib.next_result = 1
    scheduler = NativeScheduler(lib)

    with pytest.raises(LibBubbleError, match=message):
        getattr(scheduler, method_name)(*args)


def test_native_scheduler_rejects_bad_json_payloads() -> None:
    lib = _FakeLib()
    scheduler = NativeScheduler(lib)

    lib.snapshot_payload = []
    with pytest.raises(LibBubbleError, match="non-object snapshot"):
        scheduler.snapshot()

    lib.inference_payload = {"error": "boom"}
    with pytest.raises(LibBubbleError, match="boom"):
        scheduler.inference_chat({})

    lib.pending_payload = {"not": "a list"}
    with pytest.raises(LibBubbleError, match="non-list pending"):
        scheduler.access_pending()

    lib.manifest_payload = []
    with pytest.raises(LibBubbleError, match="non-object manifest"):
        scheduler.access_manifest("agt-test")


def test_run_sandbox_builds_helper_command_with_proxy_options(monkeypatch: pytest.MonkeyPatch) -> None:
    lib = _FakeLib()
    scheduler = NativeScheduler(lib)
    monkeypatch.setenv("BUBBLEHUB_SANDBOX_HELPER", "/tmp/helper")

    with patch("bubblehub.native._run_with_access_broker", return_value=0) as broker:
        result = scheduler.run_sandbox(
            "/bin/echo",
            [],
            resource_niceness=3,
            memory_max=1024,
            cpu_percent=50,
            workdir="/workspace",
            isolate_network=False,
            sandbox_http_proxy_port=18080,
            disable_http_proxy=True,
            access_broker=lambda request: "always",
        )

    command = broker.call_args.args[0]
    assert result == 0
    assert command[:9] == ["/tmp/helper", "--memory", "1024", "--cpu", "50", "--niceness", "3", "--workdir", "/workspace"]
    assert "--sandbox-http-proxy-port" in command
    assert "--no-http-proxy" in command
    assert command[-1] == "/bin/echo"
