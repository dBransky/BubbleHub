from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ageos.app.agents import enrich_agent_view, stop_agent
from ageos.native import is_sandboxed
from ageos.node.client import SchedulerClient


def command(
    kill: str | None = typer.Option(None, "--kill", metavar="AGENT_ID", help="Stop a running agent by id."),
) -> None:
    """Show running agents and loaded models."""

    if is_sandboxed():
        raise typer.BadParameter("ageos ps is only available to the real host user, not from inside an AgeOS sandbox")
    client = SchedulerClient.local()
    kill_agent_id = kill if isinstance(kill, str) else None
    if kill_agent_id:
        result = stop_agent(kill_agent_id, client)
        typer.echo(f"Stopped {result['agent_id']} (pid {result['pid']}).")
        return

    snapshot = client.status_snapshot()
    console = Console()
    console.print(f"Memory pressure: [bold]{snapshot['memory_pressure']}[/bold]")
    hardware = snapshot.get("hardware", {})
    limits = snapshot.get("limits", {})
    if isinstance(hardware, dict) and isinstance(limits, dict):
        console.print(
            f"RAM limit: [bold]{_gib(limits.get('ram_bytes'))}GiB[/bold] "
            f"(hardware {_gib(hardware.get('ram_bytes'))}GiB), "
            f"VRAM limit: [bold]{_gib(limits.get('vram_bytes'))}GiB[/bold] "
            f"(hardware {_gib(hardware.get('vram_bytes'))}GiB)"
        )

    agents = Table(title="Agents")
    for column in ["name", "agent_id", "pid", "binary", "status", "niceness", "specialty"]:
        agents.add_column(column)
    for agent in snapshot["agents"]:  # type: ignore[index]
        if isinstance(agent, dict):
            enriched = enrich_agent_view(agent)
            agents.add_row(
                *(str(enriched.get(column, "")) for column in ["display_name", "agent_id", "pid", "binary", "status", "niceness", "specialty"])
            )
    console.print(agents)

    models = Table(title="Loaded Models")
    for column in ["name", "specialty", "backend", "ram_gb", "vram_gb", "pid", "port", "refcount"]:
        models.add_column(column)
    for model in snapshot["models"]:  # type: ignore[index]
        models.add_row(*(str(model.get(column, "")) for column in ["name", "specialty", "backend", "ram_gb", "vram_gb", "pid", "port", "refcount"]))
    console.print(models)


def _gib(value: object) -> int:
    try:
        return int(int(value) / 1024**3)
    except (TypeError, ValueError):
        return 0
