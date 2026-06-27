from __future__ import annotations

from pathlib import Path


def test_install_ps1_creates_start_menu_and_desktop_shortcuts() -> None:
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")

    assert 'GetFolderPath("Desktop")' in script
    assert "AgeOS Control Center.lnk" in script
    assert "New-AgeOSControlCenterShortcut" in script
