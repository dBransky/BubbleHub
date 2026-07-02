from __future__ import annotations

import ctypes
import json
import os
import select
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class HardwareInfo:
    ram_bytes: int
    vram_bytes: int
    free_vram_bytes: int = 0
    gpu_vendor: str = "none"
    gpu_name: str = ""
    gpu_backend: str = "cpu"
    gpu_backends: tuple[str, ...] = ()
    gpu_compute_capability: str = ""
    gpu_device: str = ""

    def supports_backend(self, backend: str) -> bool:
        return backend in self.gpu_backends


@dataclass(frozen=True)
class Admission:
    allowed: bool
    state: str
    reason: str = ""


class LibBubbleHubError(RuntimeError):
    pass


class SandboxConfig(ctypes.Structure):
    _fields_ = [
        ("binary", ctypes.c_char_p),
        ("argv", ctypes.POINTER(ctypes.c_char_p)),
        ("resource_niceness", ctypes.c_int),
        ("memory_max", ctypes.c_uint64),
        ("cpu_percent", ctypes.c_uint32),
        ("workdir", ctypes.c_char_p),
        ("root_dir", ctypes.c_char_p),
        ("rootfs_dir", ctypes.c_char_p),
        ("overlay_upper_dir", ctypes.c_char_p),
        ("overlay_work_dir", ctypes.c_char_p),
        ("agent_id", ctypes.c_char_p),
        ("isolate_network", ctypes.c_int),
        ("inference_host", ctypes.c_char_p),
        ("inference_port", ctypes.c_uint32),
        ("sandbox_inference_port", ctypes.c_uint32),
        ("sandbox_http_proxy_port", ctypes.c_uint32),
        ("access_broker_fd", ctypes.c_int),
    ]


class AccessRequest(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_char * 32),
        ("subject", ctypes.c_char * 256),
        ("method", ctypes.c_char * 64),
        ("path", ctypes.c_char * 512),
    ]


BUBBLEHUB_AGENT_UID_BASE = 60000
BUBBLEHUB_AGENT_UID_END = 64000


def sync_log_config(level: str, log_file: str | None = None) -> None:
    """Push the current Python log settings into libbubblehub."""

    try:
        lib = _load_libbubblehub()
        lib.bubblehub_log_set_level.argtypes = [ctypes.c_char_p]
        lib.bubblehub_log_set_level.restype = None
        lib.bubblehub_log_set_level(_bytes(level))
        lib.bubblehub_log_set_file.argtypes = [ctypes.c_char_p]
        lib.bubblehub_log_set_file.restype = None
        lib.bubblehub_log_set_file(_bytes(log_file))
    except (LibBubbleHubError, AttributeError):
        return


def sync_log_level(level: str) -> None:
    """Push the current Python log level into libbubblehub."""

    sync_log_config(level, os.environ.get("BUBBLEHUB_LOG_FILE"))


def _load_libbubblehub() -> ctypes.CDLL:
    candidates = [
        Path(__file__).resolve().parent / "libbubblehub.so",
        Path(__file__).resolve().parent.parent / "libbubblehub" / "build" / "libbubblehub.so",
        Path("/usr/lib/libbubblehub.so"),
        Path("/usr/lib/x86_64-linux-gnu/libbubblehub.so"),
        Path("/usr/local/lib/libbubblehub.so"),
        Path("/usr/local/lib/x86_64-linux-gnu/libbubblehub.so"),
    ]
    errors: list[str] = []
    for path in candidates:
        if path.exists():
            try:
                return ctypes.CDLL(str(path))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    detail = "; ".join(errors) if errors else "no candidate library path exists"
    raise LibBubbleHubError(
        f"libbubblehub.so is required but could not be loaded. Run ./scripts/build.sh or install the BubbleHub native package. Details: {detail}"
    )


def _sandbox_helper() -> str:
    configured = os.environ.get("BUBBLEHUB_SANDBOX_HELPER")
    if configured:
        return configured
    found = shutil.which("bubblehub-sandbox")
    if found:
        return found
    default = Path("/usr/local/bin/bubblehub-sandbox")
    if default.is_file() and os.access(default, os.X_OK):
        return str(default)
    raise LibBubbleHubError("bubblehub-sandbox helper is required for sandbox execution. Run ./scripts/build.sh.")


def _bytes(value: str | None) -> bytes | None:
    if value is None:
        return None
    return value.encode("utf-8")


AccessBroker = Callable[[dict[str, object]], str]


def _run_with_access_broker(command: list[str], access_broker: AccessBroker) -> int:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        separator = command.index("--")
        command = [*command[:separator], "--access-broker-fd", str(child.fileno()), *command[separator:]]
        process = subprocess.Popen(command, pass_fds=(child.fileno(),))
        child.close()
        child_buffer = ""
        while True:
            if process.poll() is not None:
                return int(process.returncode)
            readable, _, _ = select.select([parent], [], [], 0.1)
            if not readable:
                continue
            chunk = parent.recv(4096)
            if not chunk:
                continue
            child_buffer += chunk.decode("utf-8")
            while "\n" in child_buffer:
                raw, child_buffer = child_buffer.split("\n", 1)
                if not raw:
                    continue
                try:
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise ValueError("broker request was not an object")
                    response = access_broker(request)
                except Exception:
                    response = "never"
                if response not in {"always", "never", "ask"}:
                    response = "never"
                wire_response = {"always": "approve", "never": "deny", "ask": "ask"}[response]
                parent.sendall(f"{wire_response}\n".encode("utf-8"))
    finally:
        parent.close()
        child.close()


def detect_hardware() -> HardwareInfo:
    """Return host RAM/VRAM from the required native BubbleHub library."""

    lib = _load_libbubblehub()
    try:
        lib.bubblehub_hw_total_ram_bytes.restype = ctypes.c_uint64
        lib.bubblehub_hw_vram_bytes.restype = ctypes.c_uint64
        free_vram_bytes = _native_free_vram_bytes(lib)
        vram_bytes = int(lib.bubblehub_hw_vram_bytes())
        gpu_profile = _detect_gpu_profile(vram_bytes, free_vram_bytes)
        return HardwareInfo(
            ram_bytes=int(lib.bubblehub_hw_total_ram_bytes()),
            vram_bytes=int(gpu_profile["vram_bytes"]),
            free_vram_bytes=int(gpu_profile["free_vram_bytes"]),
            gpu_vendor=str(gpu_profile["gpu_vendor"]),
            gpu_name=str(gpu_profile["gpu_name"]),
            gpu_backend=str(gpu_profile["gpu_backend"]),
            gpu_backends=tuple(str(item) for item in gpu_profile["gpu_backends"]),
            gpu_compute_capability=str(gpu_profile["gpu_compute_capability"]),
            gpu_device=str(gpu_profile["gpu_device"]),
        )
    except AttributeError as exc:
        raise LibBubbleHubError("libbubblehub.so is missing required hardware detection symbols") from exc


def _native_free_vram_bytes(lib: ctypes.CDLL) -> int:
    try:
        lib.bubblehub_hw_free_vram_bytes.restype = ctypes.c_uint64
        return int(lib.bubblehub_hw_free_vram_bytes())
    except AttributeError:
        return 0


def _detect_gpu_profile(vram_bytes: int, free_vram_bytes: int) -> dict[str, object]:
    override = _gpu_profile_from_env()
    if override is not None:
        return override
    nvidia = _detect_nvidia_gpu()
    if nvidia is not None:
        return nvidia
    amd = _detect_amd_gpu(vram_bytes, free_vram_bytes)
    if amd is not None:
        return amd
    intel = _detect_intel_gpu(vram_bytes, free_vram_bytes)
    if intel is not None:
        return intel
    vulkan = _detect_vulkan_gpu(vram_bytes, free_vram_bytes)
    if vulkan is not None:
        return vulkan
    return _gpu_profile(
        vram_bytes=vram_bytes,
        free_vram_bytes=free_vram_bytes,
    )


def _gpu_profile_from_env() -> dict[str, object] | None:
    backends = _split_env_list(os.environ.get("BUBBLEHUB_GPU_BACKENDS"))
    vendor = os.environ.get("BUBBLEHUB_GPU_VENDOR")
    backend = os.environ.get("BUBBLEHUB_GPU_BACKEND")
    name = os.environ.get("BUBBLEHUB_GPU_NAME", "")
    compute = os.environ.get("BUBBLEHUB_GPU_COMPUTE_CAPABILITY", "")
    device = os.environ.get("BUBBLEHUB_GPU_DEVICE", "")
    vram_bytes = _env_int("BUBBLEHUB_GPU_VRAM_BYTES")
    free_vram_bytes = _env_int("BUBBLEHUB_GPU_FREE_VRAM_BYTES")
    if vendor is None and backend is None and not backends and vram_bytes is None and free_vram_bytes is None:
        return None
    if backend and backend != "cpu" and backend not in backends:
        backends.insert(0, backend)
    if backend is None:
        backend = backends[0] if backends else "cpu"
    return _gpu_profile(
        vram_bytes=vram_bytes or 0,
        free_vram_bytes=free_vram_bytes or vram_bytes or 0,
        vendor=vendor or ("generic" if backends else "none"),
        name=name,
        backend=backend,
        backends=tuple(backends),
        compute_capability=compute,
        device=device,
    )


def _detect_nvidia_gpu() -> dict[str, object] | None:
    if shutil.which("nvidia-smi") is None:
        return None
    output = _run_text(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    if not output:
        return None
    best: dict[str, object] | None = None
    for index, line in enumerate(output.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        total_mib = _parse_float(parts[1])
        free_mib = _parse_float(parts[2])
        compute = parts[3]
        backends = ["cuda-llama"]
        if _supports_vllm_compute(compute):
            backends.insert(0, "vllm")
        candidate = _gpu_profile(
            vram_bytes=int(total_mib * 1024**2),
            free_vram_bytes=int(free_mib * 1024**2),
            vendor="nvidia",
            name=parts[0],
            backend=backends[0],
            backends=tuple(backends),
            compute_capability=compute,
            device=str(index),
        )
        if best is None or int(candidate["free_vram_bytes"]) > int(best["free_vram_bytes"]):
            best = candidate
    return best


def _detect_amd_gpu(vram_bytes: int, free_vram_bytes: int) -> dict[str, object] | None:
    if shutil.which("rocm-smi") is None and shutil.which("rocminfo") is None:
        return None
    name = ""
    output = _run_text(["rocm-smi", "--showproductname"]) if shutil.which("rocm-smi") else ""
    for line in output.splitlines():
        if "Card series" in line or "Card model" in line:
            name = line.split(":", 1)[-1].strip()
            break
    return _gpu_profile(
        vram_bytes=vram_bytes,
        free_vram_bytes=free_vram_bytes or vram_bytes,
        vendor="amd",
        name=name,
        backend="rocm-llama",
        backends=("rocm-llama",),
    )


def _detect_intel_gpu(vram_bytes: int, free_vram_bytes: int) -> dict[str, object] | None:
    if shutil.which("sycl-ls") is None:
        return None
    output = _run_text(["sycl-ls"])
    if "gpu" not in output.lower():
        return None
    return _gpu_profile(
        vram_bytes=vram_bytes,
        free_vram_bytes=free_vram_bytes or vram_bytes,
        vendor="intel",
        name="SYCL GPU",
        backend="sycl-llama",
        backends=("sycl-llama",),
    )


def _detect_vulkan_gpu(vram_bytes: int, free_vram_bytes: int) -> dict[str, object] | None:
    if shutil.which("vulkaninfo") is None:
        return None
    output = _run_text(["vulkaninfo", "--summary"])
    if "deviceName" not in output:
        return None
    name = ""
    for line in output.splitlines():
        if "deviceName" in line:
            name = line.split("=", 1)[-1].strip()
            break
    return _gpu_profile(
        vram_bytes=vram_bytes,
        free_vram_bytes=free_vram_bytes or vram_bytes,
        vendor="vulkan",
        name=name,
        backend="vulkan-llama",
        backends=("vulkan-llama",),
    )


def _gpu_profile(
    *,
    vram_bytes: int,
    free_vram_bytes: int,
    vendor: str = "none",
    name: str = "",
    backend: str = "cpu",
    backends: tuple[str, ...] = (),
    compute_capability: str = "",
    device: str = "",
) -> dict[str, object]:
    return {
        "vram_bytes": max(0, int(vram_bytes)),
        "free_vram_bytes": max(0, int(free_vram_bytes)),
        "gpu_vendor": vendor,
        "gpu_name": name,
        "gpu_backend": backend,
        "gpu_backends": backends,
        "gpu_compute_capability": compute_capability,
        "gpu_device": device,
    }


def _supports_vllm_compute(value: str) -> bool:
    try:
        major = int(value.split(".", 1)[0])
    except (ValueError, IndexError):
        return False
    return major >= 8


def _run_text(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=False, text=True, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _parse_float(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def is_sandboxed() -> bool:
    """Return true when kernel namespace state shows this process is inside BubbleHub sandbox."""

    if _has_sandbox_agent_uid():
        return True
    if _has_sandbox_user_namespace():
        return True
    return os.environ.get("BUBBLEHUB_SANDBOX") == "1"


def _has_sandbox_agent_uid() -> bool:
    return BUBBLEHUB_AGENT_UID_BASE <= os.geteuid() < BUBBLEHUB_AGENT_UID_END


def _has_sandbox_user_namespace() -> bool:
    try:
        text = Path("/proc/self/uid_map").read_text(encoding="utf-8")
    except PermissionError:
        return os.geteuid() == 0
    except OSError:
        return False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            inside_uid, outside_uid, count = (int(part) for part in parts)
        except ValueError:
            continue
        if outside_uid != inside_uid and count == 1:
            return True
    return False


class NativeScheduler:
    def __init__(self, lib: ctypes.CDLL | None = None) -> None:
        self.lib = lib if lib is not None else _load_libbubblehub()
        self._configure()

    def admit_model_job(
        self,
        specialty: str,
        model_name: str,
        niceness: int,
        ram_gb: float,
        vram_gb: float,
    ) -> Admission:
        allowed = ctypes.c_int()
        state = ctypes.create_string_buffer(64)
        reason = ctypes.create_string_buffer(256)
        result = self.lib.bubblehub_scheduler_admit_model_job(
            _bytes(specialty),
            _bytes(model_name),
            int(niceness),
            float(ram_gb),
            float(vram_gb),
            ctypes.byref(allowed),
            state,
            ctypes.sizeof(state),
            reason,
            ctypes.sizeof(reason),
        )
        if int(result) != 0:
            from bubblehub.log import log_error

            log_error("native scheduler admission failed")
            raise LibBubbleHubError("native scheduler admission failed")
        return Admission(
            allowed=bool(allowed.value),
            state=state.value.decode("utf-8"),
            reason=reason.value.decode("utf-8"),
        )

    def configure_limits(self, ram_limit_gb: float | None, vram_limit_gb: float | None) -> None:
        result = self.lib.bubblehub_scheduler_configure_limits(
            float(ram_limit_gb or 0),
            float(vram_limit_gb or 0),
        )
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to configure resource limits")

    def register_agent(
        self,
        agent_id: str,
        pid: int,
        binary: str,
        niceness: int,
        specialty: str | None,
    ) -> None:
        result = self.lib.bubblehub_scheduler_register_agent(  # type: ignore[union-attr]
            _bytes(agent_id),
            int(pid),
            _bytes(binary),
            int(niceness),
            _bytes(specialty),
        )
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to register agent")

    def deregister_agent(self, agent_id: str) -> None:
        result = self.lib.bubblehub_scheduler_deregister_agent(_bytes(agent_id))  # type: ignore[union-attr]
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to deregister agent")

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
        result = self.lib.bubblehub_scheduler_mark_model_loaded(  # type: ignore[union-attr]
            _bytes(name),
            _bytes(specialty),
            _bytes(backend),
            float(ram_gb),
            float(vram_gb),
            int(pid),
            int(port),
        )
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to mark model loaded")

    def mark_model_unloaded(self, name: str) -> None:
        result = self.lib.bubblehub_scheduler_mark_model_unloaded(_bytes(name))  # type: ignore[union-attr]
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to mark model unloaded")

    def evict_model(self, name: str) -> None:
        result = self.lib.bubblehub_scheduler_evict_model(_bytes(name))  # type: ignore[union-attr]
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to evict model")

    def add_queue_item(
        self,
        job_id: str,
        kind: str,
        specialty: str,
        model_name: str,
        niceness: int,
        reason: str,
    ) -> None:
        result = self.lib.bubblehub_scheduler_add_queue_item(  # type: ignore[union-attr]
            _bytes(job_id),
            _bytes(kind),
            _bytes(specialty),
            _bytes(model_name),
            int(niceness),
            _bytes(reason),
        )
        if int(result) != 0:
            raise LibBubbleHubError("native scheduler failed to add queue item")

    def snapshot(self) -> dict[str, object]:
        pointer = self.lib.bubblehub_scheduler_snapshot_json()  # type: ignore[union-attr]
        if not pointer:
            raise LibBubbleHubError("native scheduler failed to build snapshot")
        try:
            raw = ctypes.string_at(pointer).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LibBubbleHubError("native scheduler returned a non-object snapshot")
            return data
        finally:
            self.lib.bubblehub_scheduler_free_string(pointer)  # type: ignore[union-attr]

    def inference_chat(self, request: dict[str, object]) -> dict[str, object]:
        pointer = self.lib.bubblehub_inference_chat_json(json.dumps(request).encode("utf-8"))  # type: ignore[union-attr]
        if not pointer:
            raise LibBubbleHubError("native inference failed to build response")
        try:
            raw = ctypes.string_at(pointer).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LibBubbleHubError("native inference returned a non-object response")
            if "error" in data:
                raise LibBubbleHubError(str(data["error"]))
            return data
        finally:
            self.lib.bubblehub_scheduler_free_string(pointer)  # type: ignore[union-attr]

    def access_pending(self) -> list[dict[str, object]]:
        pointer = self.lib.bubblehub_access_pending_json()  # type: ignore[union-attr]
        if not pointer:
            raise LibBubbleHubError("native access policy failed to list pending requests")
        try:
            raw = ctypes.string_at(pointer).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                raise LibBubbleHubError("native access policy returned a non-list pending response")
            return [item for item in data if isinstance(item, dict)]
        finally:
            self.lib.bubblehub_access_free_string(pointer)  # type: ignore[union-attr]

    def access_manifest(self, agent_id: str) -> dict[str, object]:
        pointer = self.lib.bubblehub_access_manifest_json(_bytes(agent_id))  # type: ignore[union-attr]
        if not pointer:
            raise LibBubbleHubError("native access policy failed to read manifest")
        try:
            raw = ctypes.string_at(pointer).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LibBubbleHubError("native access policy returned a non-object manifest")
            return data
        finally:
            self.lib.bubblehub_access_free_string(pointer)  # type: ignore[union-attr]

    def apply_access_policy(
        self,
        agent_id: str,
        *,
        kind: str,
        subject: str,
        method: str,
        path: str,
        policy: str,
    ) -> None:
        request = AccessRequest(
            kind=(kind or "").encode("utf-8"),
            subject=(subject or "").encode("utf-8"),
            method=(method or "").encode("utf-8"),
            path=(path or "").encode("utf-8"),
        )
        result = self.lib.bubblehub_access_apply_policy(  # type: ignore[union-attr]
            _bytes(agent_id),
            ctypes.byref(request),
            _bytes(policy),
        )
        if int(result) != 0:
            raise LibBubbleHubError("native access policy failed to apply policy")

    def run_sandbox(
        self,
        binary: str,
        argv: list[str],
        *,
        resource_niceness: int,
        memory_max: int,
        cpu_percent: int,
        workdir: str,
        isolate_network: bool,
        root_dir: str | None = None,
        rootfs_dir: str | None = None,
        overlay_upper_dir: str | None = None,
        overlay_work_dir: str | None = None,
        inference_host: str | None = None,
        inference_port: int = 0,
        sandbox_inference_port: int = 0,
        sandbox_http_proxy_port: int = 0,
        disable_http_proxy: bool = False,
        access_broker: AccessBroker | None = None,
    ) -> int:
        command = [
            _sandbox_helper(),
            "--memory",
            str(int(memory_max)),
            "--cpu",
            str(int(cpu_percent)),
            "--niceness",
            str(int(resource_niceness)),
            "--workdir",
            workdir,
        ]
        if root_dir is not None:
            command.extend(["--root-dir", root_dir])
        if rootfs_dir is not None:
            command.extend(["--rootfs-dir", rootfs_dir])
        if overlay_upper_dir is not None:
            command.extend(["--overlay-upper-dir", overlay_upper_dir])
        if overlay_work_dir is not None:
            command.extend(["--overlay-work-dir", overlay_work_dir])
        if isolate_network:
            command.append("--isolate-network")
        if inference_host:
            command.extend(["--inference-host", inference_host])
        if inference_port:
            command.extend(["--inference-port", str(int(inference_port))])
        if sandbox_inference_port:
            command.extend(["--sandbox-inference-port", str(int(sandbox_inference_port))])
        if sandbox_http_proxy_port > 0:
            command.extend(["--sandbox-http-proxy-port", str(int(sandbox_http_proxy_port))])
        if disable_http_proxy:
            command.append("--no-http-proxy")
        command.append("--")
        command.extend(argv if argv else [binary])
        if access_broker is not None:
            return _run_with_access_broker(command, access_broker)
        return subprocess.call(command)

    def run_sandbox_in_process(
        self,
        binary: str,
        argv: list[str],
        *,
        resource_niceness: int,
        memory_max: int,
        cpu_percent: int,
        workdir: str,
        isolate_network: bool,
        root_dir: str | None = None,
        rootfs_dir: str | None = None,
        overlay_upper_dir: str | None = None,
        overlay_work_dir: str | None = None,
        inference_host: str | None = None,
        inference_port: int = 0,
        sandbox_inference_port: int = 0,
        sandbox_http_proxy_port: int = 0,
    ) -> int:
        encoded_args = [_bytes(arg) for arg in argv]
        argv_array = (ctypes.c_char_p * (len(encoded_args) + 1))()
        for index, value in enumerate(encoded_args):
            argv_array[index] = value
        argv_array[len(encoded_args)] = None
        config = SandboxConfig(
            binary=_bytes(binary),
            argv=argv_array,
            resource_niceness=int(resource_niceness),
            memory_max=int(memory_max),
            cpu_percent=int(cpu_percent),
            workdir=_bytes(workdir),
            root_dir=_bytes(root_dir),
            rootfs_dir=_bytes(rootfs_dir),
            overlay_upper_dir=_bytes(overlay_upper_dir),
            overlay_work_dir=_bytes(overlay_work_dir),
            agent_id=_bytes(os.environ.get("BUBBLEHUB_AGENT_ID")),
            isolate_network=1 if isolate_network else 0,
            inference_host=_bytes(inference_host),
            inference_port=int(inference_port),
            sandbox_inference_port=int(sandbox_inference_port),
            sandbox_http_proxy_port=int(sandbox_http_proxy_port),
            access_broker_fd=-1,
        )
        from bubblehub.log import log_debug

        log_debug(
            "calling native sandbox",
            f"binary={binary} workdir={workdir} isolate_network={isolate_network}",
        )
        return int(self.lib.bubblehub_sandbox_run(ctypes.byref(config)))

    def _configure(self) -> None:
        assert self.lib is not None
        try:
            self.lib.bubblehub_scheduler_admit_model_job.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_char_p,
                ctypes.c_size_t,
                ctypes.c_char_p,
                ctypes.c_size_t,
            ]
            self.lib.bubblehub_scheduler_admit_model_job.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_configure_limits.argtypes = [
                ctypes.c_double,
                ctypes.c_double,
            ]
            self.lib.bubblehub_scheduler_configure_limits.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_register_agent.argtypes = [
                ctypes.c_char_p,
                ctypes.c_int64,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
            ]
            self.lib.bubblehub_scheduler_register_agent.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_deregister_agent.argtypes = [ctypes.c_char_p]
            self.lib.bubblehub_scheduler_deregister_agent.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_mark_model_loaded.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_int64,
                ctypes.c_int,
            ]
            self.lib.bubblehub_scheduler_mark_model_loaded.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_mark_model_unloaded.argtypes = [ctypes.c_char_p]
            self.lib.bubblehub_scheduler_mark_model_unloaded.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_evict_model.argtypes = [ctypes.c_char_p]
            self.lib.bubblehub_scheduler_evict_model.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_add_queue_item.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
            ]
            self.lib.bubblehub_scheduler_add_queue_item.restype = ctypes.c_int
            self.lib.bubblehub_scheduler_snapshot_json.argtypes = []
            self.lib.bubblehub_scheduler_snapshot_json.restype = ctypes.c_void_p
            self.lib.bubblehub_scheduler_free_string.argtypes = [ctypes.c_void_p]
            self.lib.bubblehub_scheduler_free_string.restype = None
            self.lib.bubblehub_inference_chat_json.argtypes = [ctypes.c_char_p]
            self.lib.bubblehub_inference_chat_json.restype = ctypes.c_void_p
            self.lib.bubblehub_access_pending_json.argtypes = []
            self.lib.bubblehub_access_pending_json.restype = ctypes.c_void_p
            self.lib.bubblehub_access_manifest_json.argtypes = [ctypes.c_char_p]
            self.lib.bubblehub_access_manifest_json.restype = ctypes.c_void_p
            self.lib.bubblehub_access_free_string.argtypes = [ctypes.c_void_p]
            self.lib.bubblehub_access_free_string.restype = None
            self.lib.bubblehub_access_apply_policy.argtypes = [
                ctypes.c_char_p,
                ctypes.POINTER(AccessRequest),
                ctypes.c_char_p,
            ]
            self.lib.bubblehub_access_apply_policy.restype = ctypes.c_int
            self.lib.bubblehub_sandbox_run.argtypes = [ctypes.POINTER(SandboxConfig)]
            self.lib.bubblehub_sandbox_run.restype = ctypes.c_int
        except AttributeError as exc:
            raise LibBubbleHubError("libbubblehub.so is missing required scheduler or sandbox symbols") from exc
