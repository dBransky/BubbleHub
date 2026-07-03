from __future__ import annotations

from pathlib import Path


def test_install_ps1_creates_start_menu_and_desktop_shortcuts() -> None:
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert 'GetFolderPath("Desktop")' in script
    assert "BubbleHub.lnk" in script
    assert "New-BubbleHubShortcut" in script


def test_install_ps1_supports_release_smoke_overrides() -> None:
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert "BUBBLEHUB_INSTALL_SH_URL" in script
    assert "BUBBLEHUB_RELEASE_BASE_URL" in script
    assert "BUBBLEHUB_ASSET_NAME" in script


def test_install_ps1_routes_commands_to_configured_wsl_distro() -> None:
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert "BUBBLEHUB_WSL_DISTRO" in script
    assert "wsl.exe -d $WslDistro" in script
    assert "wsl.exe -d `$WslDistro" in script


def test_linux_install_script_supports_release_base_override() -> None:
    script = Path("scripts/install.sh").read_text(encoding="utf-8")

    assert "BUBBLEHUB_RELEASE_BASE_URL" in script
    assert "${RELEASE_BASE_URL%/}/${VERSION}/${ASSET_NAME}" in script


def test_package_release_supports_exe_ps1_url_override() -> None:
    script = Path("scripts/package-release.sh").read_text(encoding="utf-8")

    assert "BUBBLEHUB_INSTALL_PS1_URL" in script


def test_linux_release_smoke_exercises_visible_cli_command() -> None:
    script = Path("scripts/ci/run-linux-release-install-smoke.sh").read_text(encoding="utf-8")

    assert "validating Bubble CLI" in script
    assert "bubble specialties list" in script
    assert "default-instruct" in script


def test_ci_release_smoke_runs_only_on_main_push() -> None:
    workflow_path = Path(".github/workflows/ci.yml")
    if not workflow_path.is_file():
        return
    workflow = workflow_path.read_text(encoding="utf-8")

    assert workflow.count("github.event_name == 'push' && github.ref == 'refs/heads/main'") >= 3
    assert "github.event.pull_request.head.repo.full_name == github.repository" not in workflow


def test_release_flow_keeps_tag_based_release_smoke() -> None:
    workflow_path = Path(".github/workflows/release.yml")
    if not workflow_path.is_file():
        return
    workflow = workflow_path.read_text(encoding="utf-8")

    assert workflow.count("if: startsWith(github.ref, 'refs/tags/')") >= 3
    assert "release-smoke-linux" in workflow
    assert "release-smoke-windows" in workflow
