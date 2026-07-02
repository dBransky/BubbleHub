from __future__ import annotations

import json
import time

import typer

from bubblehub.log import configure_logging, log_info
from bubblehub.node.client import SchedulerClient


def main() -> None:
    """Run a foreground scheduler status loop.

    The MVP uses an in-process scheduler for CLI calls. This command gives packagers
    and operators an always-on process shape that can later switch to Unix-socket IPC.
    """

    configure_logging()
    client = SchedulerClient.local()
    log_info("bubblehub-node running")
    typer.echo("bubblehub-node running (MVP local scheduler)")
    try:
        while True:
            typer.echo(json.dumps(client.status_snapshot(), sort_keys=True))
            time.sleep(5)
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


if __name__ == "__main__":
    main()
