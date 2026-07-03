from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import typer
from rich.console import Console

from bubblehub.cli import queue
from bubblehub.node import daemon
from bubblehub.node.telemetry import telemetry_snapshot


def test_queue_render_shows_empty_state() -> None:
    client = Mock()
    client.queue_snapshot.return_value = []
    console = Console(record=True)

    with patch("bubblehub.cli.queue.SchedulerClient.local", return_value=client):
        queue._render(console)

    text = console.export_text()
    assert "No waiting" in text
    assert "jobs;" in text


def test_queue_render_shows_waiting_jobs() -> None:
    client = Mock()
    client.queue_snapshot.return_value = [
        {
            "job_id": "job-1",
            "kind": "model",
            "specialty": "default",
            "model_name": "small",
            "niceness": 4,
            "wait_seconds": 12,
            "reason": "low ram",
        }
    ]
    console = Console(record=True)

    with patch("bubblehub.cli.queue.SchedulerClient.local", return_value=client):
        queue._render(console)

    text = console.export_text()
    assert "job-1" in text
    assert "low ram" in text


def test_queue_command_renders_once() -> None:
    with (
        patch("bubblehub.cli.queue.Console") as console_cls,
        patch("bubblehub.cli.queue._render") as render,
    ):
        queue.command(watch=False)

    console_cls.return_value.clear.assert_called_once()
    render.assert_called_once_with(console_cls.return_value)


def test_node_telemetry_snapshot_delegates_to_local_client() -> None:
    client = Mock()
    client.telemetry_snapshot.return_value = {"queue": []}

    with patch("bubblehub.node.telemetry.SchedulerClient.local", return_value=client):
        assert telemetry_snapshot() == {"queue": []}


def test_node_daemon_prints_status_until_interrupted() -> None:
    client = Mock()
    client.status_snapshot.return_value = {"models": []}

    with (
        patch("bubblehub.node.daemon.SchedulerClient.local", return_value=client),
        patch("bubblehub.node.daemon.configure_logging"),
        patch("bubblehub.node.daemon.time.sleep", side_effect=KeyboardInterrupt),
        pytest.raises(typer.Exit) as raised,
    ):
        daemon.main()

    assert raised.value.exit_code == 0
    client.status_snapshot.assert_called_once()
