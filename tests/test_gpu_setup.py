from bubblehub.gpu_setup import choose_install_profile
from bubblehub.native import HardwareInfo


def test_gpu_setup_selects_cpu_without_gpu_backend() -> None:
    profile = choose_install_profile(HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=0), "auto")
    assert profile["backend"] == "cpu"
    assert profile["supported"]


def test_gpu_setup_selects_accelerated_llama_backend() -> None:
    hardware = HardwareInfo(
        ram_bytes=32 * 1024**3,
        vram_bytes=11 * 1024**3,
        free_vram_bytes=9 * 1024**3,
        gpu_vendor="nvidia",
        gpu_backend="cuda-llama",
        gpu_backends=("cuda-llama",),
    )
    profile = choose_install_profile(hardware, "auto")
    assert profile["backend"] == "cuda-llama"
    assert profile["supported"]
    assert not profile["install_vllm"]


def test_gpu_setup_selects_vllm_when_supported() -> None:
    hardware = HardwareInfo(
        ram_bytes=64 * 1024**3,
        vram_bytes=24 * 1024**3,
        free_vram_bytes=22 * 1024**3,
        gpu_vendor="nvidia",
        gpu_backend="vllm",
        gpu_backends=("vllm", "cuda-llama"),
        gpu_compute_capability="8.9",
    )
    profile = choose_install_profile(hardware, "auto")
    assert profile["backend"] == "vllm"
    assert profile["supported"]
    assert profile["install_vllm"]
