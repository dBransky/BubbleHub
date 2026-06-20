from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer

from ageos.cli.run import run_agent


def command(
    ctx: typer.Context,
    memory: str = typer.Option("2G", "--memory", help="Sandbox memory limit."),
    cpu: int = typer.Option(0, "--cpu", help="Optional cgroup CPU percent cap."),
    speciality: str | None = typer.Option(None, "--speciality", "--specialty", help="Default model specialty."),
    workdir: Path | None = typer.Option(None, "--workdir", file_okay=False, dir_okay=True),
    root_dir: Path | None = typer.Option(
        None,
        "--root-dir",
        file_okay=False,
        dir_okay=True,
        help="Writable directory exposed inside the sandbox. Defaults to an empty /workspace.",
    ),
    force_new_sandbox: bool = typer.Option(
        False,
        "--force-new-sandbox",
        help="Discard any persistent sandbox under --root-dir and start with a new agent home.",
    ),
) -> None:
    """Open an interactive shell inside the AgeOS sandbox."""

    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    args = list(ctx.args) or _interactive_args(shell)
    if not ctx.args:
        typer.echo("Entering AgeOS sandbox shell. Run `exit` to return to the host shell.")
    run_agent(
        binary=shell,
        extra_args=args,
        niceness=0,
        memory=memory,
        cpu=cpu,
        speciality=speciality,
        workdir=workdir,
        root_dir=root_dir,
        force_new_sandbox=force_new_sandbox,
    )


def _interactive_args(shell: str) -> list[str]:
    name = Path(shell).name
    if name == "bash":
        return ["--noprofile", "--norc", "-i"]
    if name == "zsh":
        return ["-f", "-i"]
    return ["-i"]
