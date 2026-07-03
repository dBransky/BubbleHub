from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import typer

from bubblehub.cli import app, dashboard, serve


def test_serve_command_uses_config_defaults_and_overrides(capsys) -> None:
    defaults = SimpleNamespace(host="127.0.0.2", port=8123, default_specialty="default")

    with (
        patch("bubblehub.cli.serve.load_inference_config", return_value=defaults),
        patch("bubblehub.cli.serve.run_http_api") as run_api,
    ):
        serve.command(host=None, port=None, speciality=None, niceness=4)
        serve.command(host="0.0.0.0", port=9000, speciality="code", niceness=-1)

    first, second = [call.args[0] for call in run_api.call_args_list]
    assert (first.host, first.port, first.default_specialty, first.niceness) == ("127.0.0.2", 8123, "default", 4)
    assert (second.host, second.port, second.default_specialty, second.niceness) == ("0.0.0.0", 9000, "code", -1)
    assert "Serving BubbleHub HTTP API" in capsys.readouterr().out


def test_app_command_rejects_sandbox_and_wraps_runtime_errors() -> None:
    with patch("bubblehub.cli.app.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter, match="real host user"):
            app.command()

    with (
        patch("bubblehub.cli.app.is_sandboxed", return_value=False),
        patch("bubblehub.cli.app.run_desktop_app", side_effect=RuntimeError("missing app")),
        pytest.raises(typer.BadParameter, match="missing app"),
    ):
        app.command()


def test_app_command_and_entrypoint_call_desktop_runner() -> None:
    with (
        patch("bubblehub.cli.app.is_sandboxed", return_value=False),
        patch("bubblehub.cli.app.run_desktop_app") as run_desktop,
    ):
        app.command(host="0.0.0.0", port=9001, speciality="code", server_only=True)

    config = run_desktop.call_args.args[0]
    assert (config.host, config.port, config.speciality, config.server_only) == ("0.0.0.0", 9001, "code", True)

    with patch("bubblehub.cli.app.typer.run") as run:
        app.run_app()
    run.assert_called_once_with(app.command)


def test_dashboard_command_rejects_sandbox_and_runs_dashboard() -> None:
    with patch("bubblehub.cli.dashboard.is_sandboxed", return_value=True):
        with pytest.raises(typer.BadParameter, match="real host user"):
            dashboard.command()

    with (
        patch("bubblehub.cli.dashboard.is_sandboxed", return_value=False),
        patch("bubblehub.cli.dashboard.run_dashboard") as run_dashboard,
    ):
        dashboard.command(refresh=0.5, once=True)

    run_dashboard.assert_called_once_with(refresh_seconds=0.5, once=True)


def test_dashboard_run_once_and_keyboard_interrupt() -> None:
    with (
        patch("bubblehub.tui.dashboard._resolve_pending_access") as resolve,
        patch("bubblehub.tui.dashboard._render", return_value="rendered") as render,
        patch("bubblehub.tui.dashboard.Console") as console_cls,
    ):
        from bubblehub.tui.dashboard import run_dashboard

        run_dashboard(once=True)

    resolve.assert_called_once_with(console_cls.return_value)
    console_cls.return_value.print.assert_called_once_with("rendered")
    render.assert_called_once()

    live = Mock()
    live.__enter__ = Mock(return_value=live)
    live.__exit__ = Mock(return_value=None)
    with (
        patch("bubblehub.tui.dashboard._resolve_pending_access"),
        patch("bubblehub.tui.dashboard._render", return_value="rendered"),
        patch("bubblehub.tui.dashboard.Live", return_value=live),
        patch("bubblehub.tui.dashboard.time.sleep", side_effect=KeyboardInterrupt),
    ):
        from bubblehub.tui.dashboard import run_dashboard

        run_dashboard(refresh_seconds=1.0, once=False)

    live.update.assert_called_once_with("rendered")
