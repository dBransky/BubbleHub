from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import typer

from bubblehub.engine.session import EngineSession
from bubblehub.engine.structured import (
    build_repair_messages,
    build_structured_messages,
    load_example_schema,
    parse_json_output,
)
from bubblehub.inference import load_inference_config
from bubblehub.log import log_debug, log_info


def command(
    speciality: str | None = typer.Option(None, "--speciality", help="Specialty to route to."),
    structure: Path | None = typer.Option(
        None,
        "--structure",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Optional JSON example file for structured output.",
    ),
    text: str = typer.Option(..., "--text", help="Prompt text."),
    niceness: int = typer.Option(0, "--niceness", min=-20, max=19, help="BubbleHub GPU/memory priority."),
    output: Path | None = typer.Option(None, "--output", help="Optional output file."),
) -> None:
    """Run one local prompt, optionally using structured JSON output."""

    resolved_speciality = speciality or load_inference_config().default_specialty
    log_info(
        "running prompt",
        f"speciality={resolved_speciality} niceness={niceness} structured={structure is not None} output={output}",
    )
    log_debug("prompt text", text)
    with EngineSession(resolved_speciality, niceness=niceness) as session:
        payload = _run_prompt(
            resolved_speciality,
            structure,
            text,
            lambda messages: session.chat(messages),
        )
    if output:
        output.write_text(payload + "\n", encoding="utf-8")
        log_info("wrote prompt output", str(output))
    else:
        log_debug("prompt result", f"chars={len(payload)}")
        typer.echo(payload)


def _run_prompt(
    speciality: str,
    structure: Path | None,
    text: str,
    chat: Callable[[list[dict[str, str]]], str],
) -> str:
    if structure is None:
        log_debug("prompt mode", "plain")
        return chat([{"role": "user", "content": text}])

    log_debug("prompt mode", "structured", f"schema={structure}")
    example = load_example_schema(structure)
    raw = chat(build_structured_messages(example, text))
    try:
        parsed = parse_json_output(raw)
    except Exception as exc:
        log_debug("structured output parse failed retrying", str(exc))
        repaired = chat(build_repair_messages(example, text, raw))
        parsed = parse_json_output(repaired)
    log_debug("structured output parsed", f"keys={sorted(parsed.keys())}")
    return json.dumps(parsed, indent=2, sort_keys=True)
