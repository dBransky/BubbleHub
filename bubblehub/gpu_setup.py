from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from bubblehub.log import configure_logging, log_debug, log_error, log_info
from bubblehub.native import HardwareInfo, detect_hardware

LLAMA_GPU_BACKENDS = ("cuda-llama", "rocm-llama", "vulkan-llama", "sycl-llama")
GPU_MODES = ("auto", "cpu", "vllm", *LLAMA_GPU_BACKENDS)


def choose_install_profile(hardware: HardwareInfo, mode: str) -> dict[str, object]:
    normalized = mode.strip().lower() or "auto"
    if normalized not in GPU_MODES:
        return _profile(hardware, "cpu", False, False, f"unsupported BUBBLEHUB_GPU mode '{mode}'")
    if normalized == "cpu":
        return _profile(hardware, "cpu", False, True, "CPU mode forced by BUBBLEHUB_GPU=cpu")
    if normalized == "vllm":
        return _profile(
            hardware,
            "vllm",
            True,
            hardware.supports_backend("vllm"),
            "vLLM forced by BUBBLEHUB_GPU=vllm",
        )
    if normalized in LLAMA_GPU_BACKENDS:
        return _profile(
            hardware,
            normalized,
            False,
            hardware.supports_backend(normalized),
            f"{normalized} forced by BUBBLEHUB_GPU={normalized}",
        )
    if hardware.supports_backend("vllm"):
        return _profile(hardware, "vllm", True, True, "vLLM selected automatically")
    for backend in LLAMA_GPU_BACKENDS:
        if hardware.supports_backend(backend):
            return _profile(hardware, backend, False, True, f"{backend} selected automatically")
    return _profile(hardware, "cpu", False, True, "No supported GPU backend detected")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure optional BubbleHub GPU dependencies.")
    parser.add_argument("--mode", default="auto", choices=GPU_MODES)
    parser.add_argument("--wheel", help="Local BubbleHub wheel to install with optional extras.")
    parser.add_argument("--profile-out", help="Path to write the install profile JSON.")
    parser.add_argument("--no-install", action="store_true", help="Only compute and write the profile.")
    parser.add_argument(
        "--log-level",
        default="error",
        choices=("error", "info", "debug"),
        help="Log verbosity: error (default), info, or debug.",
    )
    parser.add_argument("--log-file", help="Write BubbleHub logs to this file.")
    args = parser.parse_args(argv)

    configure_logging(args.log_level, args.log_file)
    hardware = detect_hardware()
    profile = choose_install_profile(hardware, args.mode)
    log_debug("gpu setup profile", json.dumps(profile, sort_keys=True))
    if args.profile_out:
        _write_profile(Path(args.profile_out), profile)

    if not profile["supported"]:
        message = str(profile["reason"])
        if args.mode == "auto":
            log_info("gpu setup continuing with cpu runtime", message)
            print(f"BubbleHub GPU setup: {message}; continuing with CPU runtime.", file=sys.stderr)
            return 0
        log_error("gpu setup failed", message)
        print(f"BubbleHub GPU setup failed: {message}", file=sys.stderr)
        return 1

    if profile["install_vllm"] and not args.no_install:
        if not args.wheel:
            log_error("gpu setup missing wheel for vllm install")
            print("BubbleHub GPU setup failed: --wheel is required to install vLLM extras.", file=sys.stderr)
            return 1
        status = _install_vllm_extra(Path(args.wheel), forced=args.mode == "vllm")
        if status != 0 and args.mode == "auto":
            log_info("gpu setup vllm install failed continuing without vllm")
            print("BubbleHub GPU setup: vLLM install failed; continuing with non-vLLM runtime.", file=sys.stderr)
            return 0
        return status

    log_info("gpu setup selected backend", f"{profile['backend']} ({profile['reason']})")
    print(f"BubbleHub GPU setup: {profile['backend']} ({profile['reason']})")
    return 0


def _profile(
    hardware: HardwareInfo,
    backend: str,
    install_vllm: bool,
    supported: bool,
    reason: str,
) -> dict[str, object]:
    data = asdict(hardware)
    return {
        "backend": backend,
        "install_vllm": install_vllm,
        "supported": supported,
        "reason": reason,
        "hardware": data,
    }


def _write_profile(path: Path, profile: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _install_vllm_extra(wheel: Path, *, forced: bool) -> int:
    target = f"{wheel}[vllm]"
    log_info("installing vllm optional dependencies", target)
    print("BubbleHub GPU setup: installing vLLM optional dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", target],
        check=False,
        text=True,
    )
    if result.returncode != 0 and forced:
        log_error("vllm optional dependency install failed")
        print("BubbleHub GPU setup failed: vLLM optional dependency install failed.", file=sys.stderr)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
