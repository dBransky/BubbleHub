from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_install_ps1_creates_start_menu_and_desktop_shortcuts() -> None:
    script = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    assert 'GetFolderPath("Desktop")' in script
    assert "BubbleHub.lnk" in script
    assert "New-BubbleHubShortcut" in script
    assert "IconLocation" in script


def test_install_ps1_supports_release_smoke_overrides() -> None:
    script = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    assert "BUBBLEHUB_INSTALL_SH_URL" in script
    assert "BUBBLEHUB_RELEASE_BASE_URL" in script
    assert "BUBBLEHUB_DEB_URL" in script
    assert "BUBBLEHUB_WINDOWS_APP_URL" in script
    assert "BUBBLEHUB_WINDOWS_APP_LOCAL_PATH" in script


def test_install_ps1_routes_commands_to_configured_wsl_distro() -> None:
    script = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    assert "BUBBLEHUB_WSL_DISTRO" in script
    assert "wsl.exe -d $WslDistro" in script
    assert "wsl.exe -d `$WslDistro" in script


def test_linux_install_script_supports_release_base_override() -> None:
    script = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")

    assert "BUBBLEHUB_RELEASE_BASE_URL" in script
    assert "${RELEASE_BASE_URL%/}/${VERSION}/${ASSET_NAME}" in script


def test_create_rootfs_precreates_runtime_bind_targets() -> None:
    script = (ROOT / "scripts/create-rootfs.sh").read_text(encoding="utf-8")
    overfs = (ROOT / "libbubble/overfs.c").read_text(encoding="utf-8")
    match = re.search(r"const char \*files\[\] = \{(?P<body>.*?)\};", overfs, re.DOTALL)
    assert match is not None
    native_bind_targets = re.findall(r'"(/[^"]+)"', match.group("body"))

    for path in [
        "usr/bin/bubble",
        "usr/bin/bubblehub",
        "usr/bin/bubblehub-node",
        "usr/bin/bubblehub-sandbox",
        "usr/bin/llama-server",
        "usr/lib/libbubble.so",
        "usr/lib/libbubblehub.so",
        "usr/lib/x86_64-linux-gnu/libbubble.so",
        "usr/lib/x86_64-linux-gnu/libbubblehub.so",
        *(target.lstrip("/") for target in native_bind_targets),
    ]:
        assert path in script


def test_windows_install_script_installs_release_deb_in_wsl() -> None:
    script = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    assert "Resolve-DebUrl" in script
    assert "BubbleHub-$PackageVersion-x64.deb" in script
    assert "BubbleHub-$PackageVersion-control-center-x64.exe" in script
    assert "apt-get install -y /tmp/$DebName" in script
    assert "BUBBLEHUB_INSTALLER_SILENT" in script
    assert "wsl --install -d Ubuntu" in script
    assert "Install-WindowsControlCenter" in script
    assert "Stop-WindowsControlCenter -AppPath $AppPath -InstallRoot $InstallRoot -Port $AppPort" in script
    assert "Stop-WindowsProcessByCommandLine" in script
    assert "Stop-WslControlApi" in script
    assert "Could not stop existing BubbleHub WSL Control API" in script
    assert '"[a]pp --host 127.0.0.1 --port $port"' in script
    assert "ControlApiKillCommandTemplate" in script
    assert "ExpectedVersion" in script
    assert "`$Health.version -eq `$ExpectedVersion" in script
    assert "bubblehub-control-center-server.pid" in script
    assert "/tmp/bubblehub-control-center-" in script
    assert "echo $$" in script
    assert "ps -eo pid=,args=" in script
    assert "Get-CimInstance Win32_Process" in script
    assert '$TempAppPath = "$AppPath.download"' in script
    assert "Move-Item -Force $TempAppPath $AppPath" in script
    assert "bubble app --host 127.0.0.1" in script
    assert "bubblehub --host 127.0.0.1" not in script


def test_package_release_bundles_branded_x64_windows_installer() -> None:
    script = (ROOT / "scripts/package-release.sh").read_text(encoding="utf-8")

    assert "Target amd64-unicode" in script
    assert "File /oname=install.ps1" in script
    assert "BUBBLEHUB_BUNDLED_INSTALL_PS1" in script
    assert "write-windows-icon.py" in script
    assert "rsvg-convert" in script
    assert "NonInteractive" in script
    assert "WindowStyle Hidden" in script
    assert "BUBBLEHUB_INSTALLER_LOG" in script
    assert "Start-Transcript" in script


def test_windows_icon_is_rendered_from_logo_svg() -> None:
    script = (ROOT / "scripts/ci/write-windows-icon.py").read_text(encoding="utf-8")

    assert 'assets" / "bubblehub-logo.svg' in script
    assert "rsvg-convert" in script


def test_release_artifact_validation_rejects_non_x64_exe() -> None:
    script = (ROOT / "scripts/ci/validate-release-artifacts.sh").read_text(encoding="utf-8")

    assert "AMD64 PE32+" in script
    assert "BUBBLEHUB_BUNDLED_INSTALL_PS1" in script
    assert "BubbleHub-*-control-center-x64.exe" in script
    assert "Intel 80386" not in script


def test_windows_release_smoke_exercises_ps1_and_exe_contracts() -> None:
    script = (ROOT / "scripts/ci/run-windows-release-install-smoke.ps1").read_text(encoding="utf-8")

    assert "Assert-RunnerPrerequisites" in script
    assert "irm '$InstallScriptUrl' | iex" in script
    assert "BUBBLEHUB_INSTALLER_SILENT" in script
    assert "BubbleHub/BubbleHub.lnk" in script
    assert "bubble app --help" in script
    assert "[a]pp --host 127.0.0.1 --port $port" in script
    assert "Assert-ReleaseSmokeAssets" in script
    assert "apt-get purge -y bubblehub" in script
    assert "dpkg --purge --force-all bubblehub" in script
    assert "/usr/bin/bubble" in script
    assert "Stop-WindowsProcessByPath" in script
    assert "Stop-WindowsProcessByCommandLine" in script
    assert '"[a]pp --host 127.0.0.1 --port $port"' in script
    assert 'ss -H -ltnp "sport = :$port"' in script
    assert "ps -eo pid=,args=" in script
    assert "/tmp/bubblehub-control-center-$port.pid" in script
    assert "/proc/net/tcp /proc/net/tcp6" in script
    assert "printf '%04X'" not in script
    assert "Write-DesktopDebugSnapshot" in script
    assert "ss -H -ltnp 'sport = :8010'" in script
    assert "bubblehub-control-center-server.pid" in script
    assert "wsl.exe -d $Distro -u root bash -lc $KillCommand" in script
    assert "Timed out waiting for BubbleHub desktop launch health response for $VersionTag. Last health result:" in script
    assert "BUBBLEHUB_DEB_URL" in script
    assert "Start-WslArtifactServer" in script
    assert '"--exec", "python3", "-m", "http.server", "$Port", "--bind", "127.0.0.1"' in script
    assert '$WslBaseUrl = "http://127.0.0.1:$Port"' in script
    assert "BUBBLEHUB_WINDOWS_APP_URL" in script
    assert "BUBBLEHUB_INSTALLER_LOG" in script
    assert '"http://127.0.0.1:$Port"' in script


def test_linux_release_smoke_exercises_visible_cli_command() -> None:
    script = (ROOT / "scripts/ci/run-linux-release-install-smoke.sh").read_text(encoding="utf-8")

    assert "validating Bubble CLI" in script
    assert "bubble specialties list" in script
    assert "default-instruct" in script
