from __future__ import annotations

import re
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from bubblehub.node.client import SchedulerClient

_AGENT_ID_RE = re.compile(r"^agt-[A-Za-z0-9_-]+$")
_POLICY_CHOICES = ["always", "never", "ask"]


def command(
    agent_id: str | None = typer.Option(None, "--agent-id", "-a", help="Agent id whose access manifest should be inspected."),
    root_dir: Path | None = typer.Option(None, "--root-dir", "-r", file_okay=False, dir_okay=True, help="Sandbox root directory."),
    edit: bool = typer.Option(True, "--edit/--no-edit", help="Interactively choose one policy and update it."),
) -> None:
    """Inspect and edit a sandbox access manifest."""

    resolved_agent_id = _resolve_agent_id(agent_id, root_dir)
    native = SchedulerClient.local().native
    manifest = native.access_manifest(resolved_agent_id)
    policies = _policies(manifest)
    console = Console()
    console.print(f"[bold]Access manifest[/bold] agent_id={resolved_agent_id}")
    console.print(_policies_table(policies))
    if not policies:
        console.print("No manifest policies.")
        return
    if not edit:
        return

    selected = _choose_policy_index(len(policies), console)
    if selected is None:
        return
    policy = Prompt.ask("Policy", choices=_POLICY_CHOICES, default=str(policies[selected].get("policy", "ask")), console=console)
    item = policies[selected]
    native.apply_access_policy(
        resolved_agent_id,
        kind=str(item.get("kind", "")),
        subject=str(item.get("subject", "")),
        method=str(item.get("method", "")),
        path=str(item.get("path", "")),
        policy=policy,
    )
    console.print(f"Updated policy {selected + 1} to [bold]{policy}[/bold].")
    updated = native.access_manifest(resolved_agent_id)
    console.print(_policies_table(_policies(updated)))


def _resolve_agent_id(agent_id: str | None, root_dir: Path | None) -> str:
    if bool(agent_id) == bool(root_dir):
        raise typer.BadParameter("provide exactly one of --agent-id or --root-dir")
    if agent_id is not None:
        if not _AGENT_ID_RE.fullmatch(agent_id):
            raise typer.BadParameter("invalid agent id")
        return agent_id
    assert root_dir is not None
    marker = root_dir.expanduser().resolve() / ".bubblehub" / "current-agent"
    if not marker.is_file():
        raise typer.BadParameter(f"persistent sandbox marker not found: {marker}")
    resolved = marker.read_text(encoding="utf-8").strip()
    if not _AGENT_ID_RE.fullmatch(resolved):
        raise typer.BadParameter("persistent sandbox marker contains an invalid agent id")
    return resolved


def _policies(manifest: dict[str, object]) -> list[dict[str, object]]:
    policies = manifest.get("policies", [])
    return [item for item in policies if isinstance(item, dict)]


def _policies_table(policies: list[dict[str, object]]) -> Table:
    table = Table(title="Manifest Policies")
    for column in ("#", "policy", "kind", "subject", "method", "path"):
        table.add_column(column)
    for index, item in enumerate(policies, start=1):
        table.add_row(
            str(index),
            str(item.get("policy", "")),
            str(item.get("kind", "")),
            str(item.get("subject", "")),
            str(item.get("method", "")) or "*",
            str(item.get("path", "")) or "*",
        )
    return table


def _choose_policy_index(count: int, console: Console) -> int | None:
    choices = [str(index) for index in range(1, count + 1)] + ["q"]
    choice = Prompt.ask("Policy to edit", choices=choices, default="q", console=console)
    if choice == "q":
        return None
    return int(choice) - 1
