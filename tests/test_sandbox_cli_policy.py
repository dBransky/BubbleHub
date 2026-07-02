from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from bubblehub.cli import app as app_cmd
from bubblehub.cli import dashboard, ps
from bubblehub.cli import main as cli_main


def test_app_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)
    with patch("bubblehub.cli.app.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            app_cmd.command()


def test_dashboard_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)
    with patch("bubblehub.cli.dashboard.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            dashboard.command()


def test_models_stop_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)
    with patch("bubblehub.cli.main.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            cli_main.models_stop()


def test_ps_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)
    with patch("bubblehub.cli.ps.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter):
            ps.command()


def test_interactive_model_chooser_is_denied_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)

    with patch("bubblehub.cli.main._choose_base_model") as choose:
        with patch("bubblehub.cli.main.is_sandboxed", return_value=True):
            with pytest.raises(typer.BadParameter):
                cli_main.models(SimpleNamespace(invoked_subcommand=None), speciality="default-instruct")

    choose.assert_not_called()


def test_models_list_is_allowed_inside_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_SANDBOX", "1")
    registry = SimpleNamespace(models=[])
    hardware = SimpleNamespace(ram_bytes=16 * 1024**3, vram_bytes=0)
    tier = SimpleNamespace(name="test", order=["tiny"])

    with (
        patch("bubblehub.cli.main.ModelRegistry.load_default", return_value=registry),
        patch("bubblehub.cli.main.detect_hardware", return_value=hardware),
        patch("bubblehub.cli.main.select_tier", return_value=tier),
        patch("bubblehub.cli.main.selected_model_name", return_value=None),
    ):
        cli_main.models_list(speciality="default-instruct")
