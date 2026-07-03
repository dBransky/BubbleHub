from __future__ import annotations

import io
from unittest.mock import Mock

from bubblehub.app.desktop import (
    DesktopAppConfig,
    _app_deps_script,
    _choose_desktop_app_install,
    _install_tauri_app,
    _normalize_yes_no,
    _prompt_and_install_tauri_app,
    _run_app_deps_installer,
    _tauri_command,
    run_desktop_app,
)


def test_desktop_app_opens_native_window_by_default(monkeypatch) -> None:
    opened: dict[str, object] = {}

    def fake_launch(url: str) -> None:
        opened["url"] = url

    monkeypatch.setattr("bubblehub.app.desktop._launch_tauri_app", fake_launch)

    run_desktop_app(DesktopAppConfig(port=0))

    assert str(opened["url"]).startswith("http://127.0.0.1:")


def test_tauri_command_prefers_configured_binary(monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_TAURI_BIN", "/opt/bubblehub/share/bubblehub/app/bubblehub")

    assert _tauri_command() == ["/opt/bubblehub/share/bubblehub/app/bubblehub"]


def test_tauri_command_prompts_to_install_when_missing(monkeypatch, tmp_path) -> None:
    installed = tmp_path / "bubblehub"
    monkeypatch.delenv("BUBBLEHUB_TAURI_BIN", raising=False)
    monkeypatch.setattr("bubblehub.app.desktop.shutil.which", lambda name: None)
    monkeypatch.setattr("bubblehub.app.desktop.Path.is_file", lambda self: False)
    monkeypatch.setattr("bubblehub.app.desktop._prompt_and_install_tauri_app", lambda: installed)

    assert _tauri_command() == [str(installed)]


def test_prompt_and_install_respects_explicit_no(monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_INSTALL_APP", "0")
    install = Mock()
    monkeypatch.setattr("bubblehub.app.desktop._install_tauri_app", install)

    assert _prompt_and_install_tauri_app() is None
    install.assert_not_called()


def test_desktop_install_prompt_supports_arrow_navigation() -> None:
    output = io.StringIO()

    selected = _choose_desktop_app_install(
        title="BubbleHub",
        message="Install the desktop app?",
        options=("Install desktop app now", "Keep using CLI commands only"),
        input_stream=io.StringIO("\x1b[B\n"),
        output_stream=output,
    )

    assert selected == 1
    assert "Use Up/Down arrows and press Enter." in output.getvalue()
    assert "\x1b[32m> Keep using CLI commands only\x1b[0m" in output.getvalue()


def test_prompt_and_install_respects_explicit_yes(monkeypatch, tmp_path) -> None:
    installed = tmp_path / "bubblehub"
    monkeypatch.setenv("BUBBLEHUB_INSTALL_APP", "yes")
    monkeypatch.setattr("bubblehub.app.desktop._install_tauri_app", lambda: installed)

    assert _prompt_and_install_tauri_app() == installed


def test_prompt_and_install_skips_non_interactive(monkeypatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_INSTALL_APP", raising=False)
    monkeypatch.setattr("bubblehub.app.desktop._can_prompt", lambda: False)

    assert _prompt_and_install_tauri_app() is None


def test_install_tauri_app_builds_and_installs_binary(monkeypatch, tmp_path) -> None:
    manifest = tmp_path / "app" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("[package]\nname='bubblehub'\nversion='0.1.0'\nedition='2021'\n", encoding="utf-8")
    built = tmp_path / ".cache" / "bubblehub" / "app-target" / "release" / "bubblehub"
    built.parent.mkdir(parents=True)
    built.write_text("#!/bin/sh\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> None:
        commands.append(command)

    monkeypatch.setattr("bubblehub.app.desktop._app_manifest_path", lambda: manifest)
    monkeypatch.setattr("bubblehub.app.desktop._run_app_deps_installer", lambda: None)
    monkeypatch.setattr("bubblehub.app.desktop.shutil.which", lambda name: "/usr/bin/cargo" if name == "cargo" else None)
    monkeypatch.setattr("bubblehub.app.desktop.Path.home", lambda: tmp_path)
    monkeypatch.setattr("bubblehub.app.desktop.subprocess.run", fake_run)

    installed = _install_tauri_app()

    assert installed == tmp_path / ".local" / "share" / "bubblehub" / "app" / "bubblehub"
    assert installed.read_text(encoding="utf-8") == "#!/bin/sh\n"
    assert commands[0][:5] == ["/usr/bin/cargo", "build", "--release", "--manifest-path", str(manifest)]


def test_run_app_deps_installer_honors_skip(monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_SKIP_APP_DEPS", "1")
    run = Mock()
    monkeypatch.setattr("bubblehub.app.desktop.subprocess.run", run)

    _run_app_deps_installer()

    run.assert_not_called()


def test_app_deps_script_finds_source_script() -> None:
    script = _app_deps_script()

    assert script is not None
    assert script.name == "install-app-deps.sh"


def test_launch_tauri_app_sets_url_env(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], env: dict[str, str], check: bool) -> object:
        captured["command"] = command
        captured["env"] = env
        return Mock(returncode=0)

    monkeypatch.setattr("bubblehub.app.desktop._tauri_command", lambda: ["/opt/bubblehub/share/bubblehub/app/bubblehub"])
    monkeypatch.setattr("bubblehub.app.desktop.subprocess.run", fake_run)

    __import__("bubblehub.app.desktop", fromlist=["_launch_tauri_app"])._launch_tauri_app("http://127.0.0.1:9999/")

    assert captured["command"] == ["/opt/bubblehub/share/bubblehub/app/bubblehub", "http://127.0.0.1:9999/"]
    assert captured["env"]["BUBBLEHUB_APP_URL"] == "http://127.0.0.1:9999/"


def test_normalize_yes_no_values() -> None:
    assert _normalize_yes_no("yes") is True
    assert _normalize_yes_no("off") is False
    assert _normalize_yes_no("later") is None
