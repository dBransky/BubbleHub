from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import TextIO

from bubblehub.app.api import ControlApiConfig, create_control_server
from bubblehub.cli.interactive import choose_option


@dataclass(frozen=True)
class DesktopAppConfig:
    host: str = "127.0.0.1"
    port: int = 8010
    speciality: str = "default-instruct"
    server_only: bool = False


def run_desktop_app(config: DesktopAppConfig) -> None:
    """Start the Control Center API and show it in a native desktop window."""

    api_config = ControlApiConfig(host=config.host, port=config.port, speciality=config.speciality)
    server = create_control_server(api_config)
    url = f"http://{config.host}:{server.server_address[1]}/"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if config.server_only:
            _serve_until_interrupt(server, url)
            return
        _launch_tauri_app(url)
    finally:
        server.shutdown()
        server.server_close()


def _launch_tauri_app(url: str) -> None:
    command = _tauri_command()
    env = os.environ.copy()
    env["BUBBLEHUB_APP_URL"] = url
    result = subprocess.run([*command, url], env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"BubbleHub Control Center exited with status {result.returncode}")


def _tauri_command() -> list[str]:
    configured = os.environ.get("BUBBLEHUB_TAURI_BIN")
    if configured:
        return [configured]
    installed = shutil.which("bubblehub-control-center")
    if installed:
        return [installed]
    root = Path(__file__).resolve().parents[2]
    release_binary = root / "app" / "target" / "release" / "bubblehub-control-center"
    if release_binary.is_file() and os.access(release_binary, os.X_OK):
        return [str(release_binary)]
    debug_binary = root / "app" / "target" / "debug" / "bubblehub-control-center"
    if debug_binary.is_file() and os.access(debug_binary, os.X_OK):
        return [str(debug_binary)]
    installed_now = _prompt_and_install_tauri_app()
    if installed_now:
        return [str(installed_now)]
    raise RuntimeError(
        "BubbleHub Control Center is not installed. Run `BUBBLEHUB_INSTALL_APP=1 bubblehub app` from an interactive terminal, "
        "or set BUBBLEHUB_TAURI_BIN to the bubblehub-control-center executable."
    )


def _prompt_and_install_tauri_app() -> Path | None:
    explicit = _normalize_yes_no(os.environ.get("BUBBLEHUB_INSTALL_APP", ""))
    if explicit is None and not _can_prompt():
        return None
    if explicit is False:
        return None
    if explicit is None and not _ask_install_desktop_app():
        return None
    return _install_tauri_app()


def _ask_install_desktop_app() -> bool:
    selected = _choose_desktop_app_install(
        title="BubbleHub Control Center",
        message=(
            "The desktop app is not installed yet.\n" "Do you want to install it now?\n" "You can keep using built-in CLI commands if you prefer."
        ),
        options=("Install desktop app now", "Keep using CLI commands only"),
    )
    return selected == 0


def _choose_desktop_app_install(
    *,
    title: str,
    message: str,
    options: tuple[str, ...],
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stderr,
) -> int:
    return choose_option(
        title=title,
        message=message,
        options=options,
        input_stream=input_stream,
        output_stream=output_stream,
    )


def _install_tauri_app() -> Path:
    manifest = _app_manifest_path()
    cargo = shutil.which("cargo")
    if cargo is None:
        _run_app_deps_installer()
        cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("cargo is required to install the BubbleHub desktop app.")
    target_dir = Path.home() / ".cache" / "bubblehub" / "app-target"
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    _run_app_deps_installer()
    subprocess.run(
        [
            cargo,
            "build",
            "--release",
            "--manifest-path",
            str(manifest),
            "--target-dir",
            str(target_dir),
        ],
        check=True,
    )
    built = target_dir / "release" / "bubblehub-control-center"
    installed = bin_dir / "bubblehub-control-center"
    shutil.copy2(built, installed)
    installed.chmod(0o755)
    print(f"Installed BubbleHub Control Center: {installed}", file=sys.stderr)
    return installed


def _run_app_deps_installer() -> None:
    if os.environ.get("BUBBLEHUB_SKIP_APP_DEPS") == "1":
        return
    script = _app_deps_script()
    if script is not None:
        subprocess.run(["bash", str(script)], check=True)


def _app_manifest_path() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "app" / "Cargo.toml",
        Path(sys.prefix) / "share" / "bubblehub" / "app" / "Cargo.toml",
        Path("/usr/share/bubblehub/app/Cargo.toml"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("BubbleHub desktop app source was not found in this installation.")


def _app_deps_script() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "install-app-deps.sh",
        Path(sys.prefix) / "share" / "bubblehub" / "scripts" / "install-app-deps.sh",
        Path("/usr/share/bubblehub/scripts/install-app-deps.sh"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _normalize_yes_no(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _can_prompt() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _serve_until_interrupt(server: object, url: str) -> None:
    print(f"BubbleHub Control Center: {url}")
    stop = Event()
    try:
        while True:
            stop.wait(3600)
    except KeyboardInterrupt:
        return
