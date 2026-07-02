from __future__ import annotations

import time

import typer
from rich.console import Console
from rich.table import Table

from bubblehub.node.client import SchedulerClient


def command(
    watch: bool = typer.Option(False, "--watch", help="Refresh every two seconds."),
) -> None:
    """Show waiting BubbleHub model/inference work."""

    console = Console()
    while True:
        console.clear()
        _render(console)
        if not watch:
            return
        time.sleep(2)


def _render(console: Console) -> None:
    table = Table(title="BubbleHub Waiting Queue")
    for column in ["job_id", "kind", "specialty", "model_name", "niceness", "wait_seconds", "reason"]:
        table.add_column(column)
    queue = SchedulerClient.local().queue_snapshot()
    if not queue:
        table.add_row("", "", "", "", "", "", "No waiting jobs; admitted work appears in bubblehub ps/dashboard models.")
        console.print(table)
        return
    for item in queue:
        table.add_row(*(str(item.get(column, "")) for column in ["job_id", "kind", "specialty", "model_name", "niceness", "wait_seconds", "reason"]))
    console.print(table)
