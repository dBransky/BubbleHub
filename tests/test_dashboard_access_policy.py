from __future__ import annotations

from unittest.mock import Mock, patch

from rich.console import Console

from bubblehub.tui.dashboard import _agents_table, _manifest_scope_for_pending, _pending_access_label, _resolve_pending_access


def test_pending_access_label_includes_agent_method_and_target() -> None:
    assert (
        _pending_access_label(
            {
                "agent_id": "agt-test",
                "kind": "http",
                "subject": "api.example.com",
                "method": "POST",
                "path": "/rpc",
            }
        )
        == "agt-test: http POST api.example.com/rpc"
    )


def test_resolve_pending_access_submits_choice_to_native_policy() -> None:
    native = Mock()
    native.access_pending.return_value = [
        {
            "agent_id": "agt-test",
            "kind": "http",
            "subject": "api.example.com",
            "method": "POST",
            "path": "/rpc",
        }
    ]
    client = Mock(native=native)

    with (
        patch("bubblehub.tui.dashboard.SchedulerClient.local", return_value=client),
        patch("bubblehub.tui.dashboard.Prompt.ask", return_value="always") as ask,
    ):
        _resolve_pending_access(Console(record=True))

    assert ask.call_args.kwargs["choices"] == ["always", "never", "ask"]
    native.apply_access_policy.assert_called_once_with(
        "agt-test",
        kind="http",
        subject="api.example.com",
        method="*",
        path="*",
        policy="always",
    )


def test_resolve_pending_access_ask_submits_manifest_policy() -> None:
    native = Mock()
    native.access_pending.return_value = [
        {
            "agent_id": "agt-test",
            "kind": "http",
            "subject": "api.example.com",
            "method": "GET",
            "path": "/",
        }
    ]
    client = Mock(native=native)

    with (
        patch("bubblehub.tui.dashboard.SchedulerClient.local", return_value=client),
        patch("bubblehub.tui.dashboard.Prompt.ask", return_value="ask"),
    ):
        _resolve_pending_access(Console(record=True))

    native.apply_access_policy.assert_called_once_with(
        "agt-test",
        kind="http",
        subject="api.example.com",
        method="*",
        path="*",
        policy="ask",
    )


def test_manifest_scope_for_non_http_pending_keeps_exact_request() -> None:
    assert _manifest_scope_for_pending({"kind": "mcp", "method": "tool", "path": "search"}) == ("tool", "search")


def test_agents_table_renders_running_and_stopped_leds() -> None:
    table = _agents_table(
        [
            {
                "agent_id": "agt-run",
                "display_name": "runner",
                "binary": "/bin/bash",
                "status": "running",
                "running": True,
                "pid": 123,
            },
            {
                "agent_id": "agt-stop",
                "display_name": "stopped reviewer",
                "binary": "/bin/bash",
                "status": "stopped",
                "running": False,
                "pid": 0,
            },
        ]
    )

    assert table.columns[0]._cells == ["[green]●[/green]", "[red]●[/red]"]
    assert table.columns[1]._cells == ["runner", "stopped reviewer"]
    assert table.columns[4]._cells == ["running", "stopped"]
