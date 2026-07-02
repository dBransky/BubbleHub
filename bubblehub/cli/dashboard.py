from __future__ import annotations

import typer

from bubblehub.native import is_sandboxed
from bubblehub.tui.dashboard import run_dashboard


def command(
    refresh: float = typer.Option(1.0, "--refresh", min=0.2, help="Refresh interval in seconds."),
    once: bool = typer.Option(False, "--once", help="Resolve pending access and render one dashboard snapshot, then exit."),
) -> None:
    """Open the htop-style BubbleHub terminal dashboard."""

    if is_sandboxed():
        raise typer.BadParameter("bubblehub dashboard is only available to the real host user, not from inside a BubbleHub sandbox")
    run_dashboard(refresh_seconds=refresh, once=once)
