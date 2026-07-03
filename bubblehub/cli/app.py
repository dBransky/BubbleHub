from __future__ import annotations

import typer

from bubblehub.app.desktop import DesktopAppConfig, run_desktop_app
from bubblehub.native import is_sandboxed


def command(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface for the local desktop API."),
    port: int = typer.Option(8010, "--port", min=1, max=65535, help="Port for the local desktop API."),
    speciality: str = typer.Option("default-instruct", "--speciality", "--specialty", help="Default speciality for model selection."),
    server_only: bool = typer.Option(False, "--server-only", help="Start only the local desktop API and print its URL."),
) -> None:
    """Open the BubbleHub desktop app."""

    if is_sandboxed():
        raise typer.BadParameter("bubblehub is only available to the real host user, not from inside a BubbleHub sandbox")
    try:
        run_desktop_app(
            DesktopAppConfig(
                host=host,
                port=port,
                speciality=speciality,
                server_only=server_only,
            )
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


def run_app() -> None:
    """Entry point for the bubblehub command that starts the BubbleHub desktop app."""
    typer.run(command)
