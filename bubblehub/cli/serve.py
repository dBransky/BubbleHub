from __future__ import annotations

import typer

from bubblehub.http_api import ApiConfig, run_http_api
from bubblehub.inference import load_inference_config
from bubblehub.log import log_info


def command(
    host: str | None = typer.Option(None, "--host", help="HTTP bind host."),
    port: int | None = typer.Option(None, "--port", min=1, max=65535, help="HTTP bind port."),
    speciality: str | None = typer.Option(
        None,
        "--speciality",
        "--specialty",
        help="Default BubbleHub specialty when the request model is not a registered specialty.",
    ),
    niceness: int = typer.Option(0, "--niceness", min=-20, max=19, help="BubbleHub GPU/memory priority."),
) -> None:
    """Serve the OpenAI-compatible BubbleHub HTTP API shim."""

    defaults = load_inference_config()
    resolved_host = host or defaults.host
    resolved_port = port or defaults.port
    resolved_specialty = speciality or defaults.default_specialty
    log_info("starting http api", f"http://{resolved_host}:{resolved_port}")
    typer.echo(f"Serving BubbleHub HTTP API on http://{resolved_host}:{resolved_port}")
    run_http_api(
        ApiConfig(
            host=resolved_host,
            port=resolved_port,
            default_specialty=resolved_specialty,
            niceness=niceness,
        )
    )
