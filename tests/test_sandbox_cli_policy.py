from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from ageos.cli import app as app_cmd
from ageos.cli import dashboard, ps
from ageos.cli import main as cli_main


def test_app_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGEOS_SANDBOX", raising=False)
    with patch("ageos.cli.app.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            app_cmd.command()


def test_dashboard_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGEOS_SANDBOX", raising=False)
    with patch("ageos.cli.dashboard.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            dashboard.command()


def test_models_stop_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGEOS_SANDBOX", raising=False)
    with patch("ageos.cli.main.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            cli_main.models_stop()


def test_ps_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGEOS_SANDBOX", raising=False)
    with patch("ageos.cli.ps.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            ps.command()


def test_interactive_model_chooser_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGEOS_SANDBOX", raising=False)

    with patch("ageos.cli.main._choose_base_model") as choose:
        with patch("ageos.cli.main.is_sandboxed", return_value=True):
            with pytest.raises(typer.BadParameter):
                cli_main.models(SimpleNamespace(invoked_subcommand=None), speciality="default-instruct")

    choose.assert_not_called()


def test_models_list_is_allowed_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    registry = SimpleNamespace(models=[])
    hardware = SimpleNamespace(ram_bytes=16 * 1024**3, vram_bytes=0)
    tier = SimpleNamespace(name="test", order=["tiny"])

    with (
        patch("ageos.cli.main.ModelRegistry.load_default", return_value=registry),
        patch("ageos.cli.main.detect_hardware", return_value=hardware),
        patch("ageos.cli.main.select_tier", return_value=tier),
        patch("ageos.cli.main.selected_model_name", return_value=None),
    ):
        cli_main.models_list(speciality="default-instruct")
