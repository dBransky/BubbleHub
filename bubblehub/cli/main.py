from __future__ import annotations

import os
import sys

import typer
from rich.console import Console
from rich.table import Table

from bubblehub import __version__
from bubblehub.app.models import (
    DEFAULT_SETUP_SPECIALITY,
    prompt_base_model_setup,
    run_install_base_model_setup,
    selected_model_name,
)
from bubblehub.cli import app as app_cmd
from bubblehub.cli import dashboard as dashboard_cmd
from bubblehub.cli import manifest as manifest_cmd
from bubblehub.cli import poc as poc_cmd
from bubblehub.cli import prompt as prompt_cmd
from bubblehub.cli import ps as ps_cmd
from bubblehub.cli import queue as queue_cmd
from bubblehub.cli import run as run_cmd
from bubblehub.cli import serve as serve_cmd
from bubblehub.cli import shell as shell_cmd
from bubblehub.engine.registry import ModelRegistry
from bubblehub.engine.selector import select_tier
from bubblehub.log import configure_logging, extract_global_log_options, log_debug, log_error, log_info
from bubblehub.native import detect_hardware, is_sandboxed
from bubblehub.node.client import SchedulerClient

HELP_CONTEXT = {"help_option_names": ["-h", "--help"]}
RUN_CONTEXT = {
    **HELP_CONTEXT,
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}

app = typer.Typer(
    name="bubblehub",
    help=(
        "BubbleHub local agent runtime, model scheduler, and sandbox CLI.\n\n"
        "Global options (may appear before or after any command):\n"
        "  --log-level [error|info|debug]\n"
        "  --log-file PATH"
    ),
    context_settings=HELP_CONTEXT,
    no_args_is_help=True,
)

models_app = typer.Typer(
    help="Inspect and choose local model registry entries.",
    context_settings=HELP_CONTEXT,
    invoke_without_command=True,
)
specialties_app = typer.Typer(
    help="Inspect available BubbleHub specialties.",
    context_settings=HELP_CONTEXT,
)

app.command("poc")(poc_cmd.command)
app.command("prompt")(prompt_cmd.command)
app.command("run", context_settings=RUN_CONTEXT)(run_cmd.command)
app.command("shell", context_settings=RUN_CONTEXT)(shell_cmd.command)
app.command("manifest")(manifest_cmd.command)
app.command("serve")(serve_cmd.command)
app.command("ps")(ps_cmd.command)
app.command("queue")(queue_cmd.command)
app.command("dashboard")(dashboard_cmd.command)
app.command("app")(app_cmd.command)
app.add_typer(models_app, name="models")
app.add_typer(specialties_app, name="specialties")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bubblehub {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show BubbleHub version.",
    ),
) -> None:
    """BubbleHub MVP command surface."""

    ctx.obj = {"log_level": os.environ.get("BUBBLEHUB_LOG_LEVEL", "error")}
    log_debug("bubblehub cli initialized", f"version={__version__} log_level={ctx.obj['log_level']}")


@models_app.callback(invoke_without_command=True)
def models(
    ctx: typer.Context,
    speciality: str = typer.Option(
        "default-instruct",
        "--speciality",
        "--specialty",
        help="Speciality to configure when choosing a base model.",
    ),
) -> None:
    """Choose the base model for a speciality."""

    if ctx.invoked_subcommand is not None:
        return
    _deny_in_sandbox("bubblehub models")
    _choose_base_model(speciality)


@models_app.command("setup")
def models_setup(
    speciality: str = typer.Option(
        DEFAULT_SETUP_SPECIALITY,
        "--speciality",
        "--specialty",
        help="Speciality to configure during install or first app launch.",
    ),
) -> None:
    """Choose the default base model when one has not been configured yet."""

    _deny_in_sandbox("bubblehub models setup")
    if run_install_base_model_setup(speciality):
        return
    typer.echo("Base model setup skipped. Choose one later with: bubblehub models")


@models_app.command("list")
def models_list(
    speciality: str = typer.Option(
        "default-instruct",
        "--speciality",
        "--specialty",
        help="Speciality whose currently selected model is highlighted.",
    ),
) -> None:
    """List registered models and show the tier this machine will use."""

    registry = ModelRegistry.load_default()
    hardware = detect_hardware()
    tier = select_tier(hardware)
    selected = selected_model_name(registry, speciality, tier.order, hardware)
    console = Console()
    console.print(f"Machine tier: [bold]{tier.name}[/bold] (RAM={hardware.ram_bytes // (1024**3)}GiB, VRAM={hardware.vram_bytes // (1024**3)}GiB)")
    if selected is not None:
        console.print(f"{speciality} -> [bold green]{selected}[/bold green]")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Use")
    table.add_column("Model")
    table.add_column("Flavor")
    table.add_column("Capability")
    table.add_column("Backend")
    table.add_column("Tier")
    table.add_column("RAM")
    table.add_column("VRAM")
    table.add_column("Ctx")
    table.add_column("Repo")
    for model in registry.models:
        marker = "*" if model.name == selected else ""
        table.add_row(
            marker,
            model.name,
            model.flavor,
            model.capability,
            model.backend,
            model.tier,
            f"{model.ram_gb:g}G",
            f"{model.vram_gb:g}G",
            str(model.context_tokens),
            model.repo_id,
        )
    console.print(table)


@models_app.command("stop")
def models_stop() -> None:
    """Stop all currently loaded warm model backends."""

    _deny_in_sandbox("bubblehub models stop")
    client = SchedulerClient.local()
    snapshot = client.status_snapshot()
    models = snapshot.get("models", [])
    loaded = [model for model in models if isinstance(model, dict) and model.get("name")]
    if not loaded:
        log_info("no loaded models to stop")
        typer.echo("No loaded models to stop.")
        return
    for model in loaded:
        client.evict_model(str(model["name"]))
    log_info("stopped loaded models", f"count={len(loaded)}")
    typer.echo(f"Stopped {len(loaded)} loaded model(s).")


@specialties_app.command("list")
def specialties_list() -> None:
    """List specialties available to --speciality."""

    registry = ModelRegistry.load_default()
    for name, specialty in sorted(registry.specialties.items()):
        typer.echo(
            f"{name:20} capability={specialty.capability:9} "
            f"flavor={specialty.flavor or 'auto':8} "
            f"model={specialty.model or 'auto':24} "
            f"lora={specialty.lora or '-'}"
        )


def _choose_base_model(speciality: str) -> None:
    try:
        prompt_base_model_setup(speciality, output_stream=sys.stderr)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _deny_in_sandbox(command: str) -> None:
    if is_sandboxed():
        log_error("command denied inside sandbox", command)
        raise typer.BadParameter(f"{command} is only available to the real host user, not from inside a BubbleHub sandbox")


def run_cli() -> None:
    """Entry point that accepts global log flags before or after the subcommand."""

    try:
        cleaned, log_level, log_file = extract_global_log_options(sys.argv[1:])
    except ValueError as exc:
        print(f"ERROR main.py run_cli invalid log option:{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    configure_logging(log_level, log_file)
    sys.argv = [sys.argv[0], *cleaned]
    app()
