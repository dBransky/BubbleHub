from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bubblehub.gpu_setup import choose_install_profile, main
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


def test_gpu_setup_rejects_unknown_mode() -> None:
    profile = choose_install_profile(HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=0), "quantum")

    assert profile["backend"] == "cpu"
    assert not profile["supported"]
    assert "unsupported" in str(profile["reason"])


def test_gpu_setup_forced_backend_reports_unsupported_hardware() -> None:
    profile = choose_install_profile(HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=0), "vllm")

    assert profile["backend"] == "vllm"
    assert profile["install_vllm"]
    assert not profile["supported"]


def test_gpu_setup_main_writes_profile_without_install(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile_path = tmp_path / "nested" / "profile.json"
    hardware = HardwareInfo(
        ram_bytes=64 * 1024**3,
        vram_bytes=24 * 1024**3,
        free_vram_bytes=20 * 1024**3,
        gpu_vendor="nvidia",
        gpu_backend="vllm",
        gpu_backends=("vllm",),
    )

    with patch("bubblehub.gpu_setup.detect_hardware", return_value=hardware):
        status = main(["--mode", "vllm", "--no-install", "--profile-out", str(profile_path), "--log-level", "debug"])

    assert status == 0
    assert '"backend": "vllm"' in profile_path.read_text(encoding="utf-8")
    assert "vllm" in capsys.readouterr().out


def test_gpu_setup_auto_continues_when_vllm_install_fails(capsys: pytest.CaptureFixture[str]) -> None:
    hardware = HardwareInfo(ram_bytes=64 * 1024**3, vram_bytes=24 * 1024**3, gpu_backend="vllm", gpu_backends=("vllm",))

    with (
        patch("bubblehub.gpu_setup.detect_hardware", return_value=hardware),
        patch("bubblehub.gpu_setup._install_vllm_extra", return_value=9) as install,
    ):
        status = main(["--wheel", "/tmp/bubble.whl"])

    assert status == 0
    install.assert_called_once_with(Path("/tmp/bubble.whl"), forced=False)
    assert "continuing with non-vLLM" in capsys.readouterr().err


def test_gpu_setup_forced_vllm_requires_wheel(capsys: pytest.CaptureFixture[str]) -> None:
    hardware = HardwareInfo(ram_bytes=64 * 1024**3, vram_bytes=24 * 1024**3, gpu_backend="vllm", gpu_backends=("vllm",))

    with patch("bubblehub.gpu_setup.detect_hardware", return_value=hardware):
        status = main(["--mode", "vllm"])

    assert status == 1
    assert "--wheel is required" in capsys.readouterr().err


def test_gpu_setup_install_vllm_extra_reports_forced_failure(capsys: pytest.CaptureFixture[str]) -> None:
    completed = Mock(returncode=3)

    with patch("bubblehub.gpu_setup.subprocess.run", return_value=completed) as run:
        from bubblehub.gpu_setup import _install_vllm_extra

        status = _install_vllm_extra(Path("/tmp/bubble.whl"), forced=True)

    assert status == 3
    assert run.call_args.args[0][-1] == "/tmp/bubble.whl[vllm]"
    assert "optional dependency install failed" in capsys.readouterr().err
