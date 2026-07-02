from __future__ import annotations

import typer
from rich.console import Console

from bubblehub.engine.session import EngineSession
from bubblehub.inference import load_inference_config
from bubblehub.log import log_debug, log_info

console = Console()


def command(
    speciality: str | None = typer.Option(None, "--speciality", help="Specialty to test."),
    niceness: int = typer.Option(0, "--niceness", min=-20, max=19, help="BubbleHub GPU/memory priority."),
    flavor: str | None = typer.Option(None, "--flavor", help="Force model flavor."),
    capability: str | None = typer.Option(None, "--capability", help="Force capability."),
) -> None:
    """Start a local model REPL for free-text testing."""

    resolved_speciality = speciality or load_inference_config().default_specialty
    log_info(
        "starting poc repl",
        f"speciality={resolved_speciality} niceness={niceness} flavor={flavor} capability={capability}",
    )
    console.print(f"[bold]BubbleHub POC[/bold] speciality={resolved_speciality} niceness={niceness}")
    with EngineSession(
        resolved_speciality,
        niceness=niceness,
        flavor=flavor,
        capability=capability,
        status_callback=lambda message: console.print(f"[dim]{message}[/dim]"),
    ) as session:
        log_debug("poc repl ready", resolved_speciality)
        while True:
            try:
                text = input("bubblehub> ").strip()
            except (EOFError, KeyboardInterrupt):
                log_debug("poc repl exiting", "interrupted")
                console.print()
                break
            if text in {":q", ":quit", "exit", "quit"}:
                log_debug("poc repl exiting", "quit")
                break
            if not text:
                continue
            log_debug("poc prompt", text)
            answer = session.chat([{"role": "user", "content": text}])
            log_debug("poc response", f"chars={len(answer)}")
            console.print(answer)
