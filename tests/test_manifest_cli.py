from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import typer
from rich.console import Console

from ageos.cli.manifest import _choose_policy_index, _resolve_agent_id, command


def test_manifest_resolves_agent_id_from_root_dir(tmp_path) -> None:
    root = tmp_path / "workspace"
    marker = root / ".ageos" / "current-agent"
    marker.parent.mkdir(parents=True)
    marker.write_text("agt-root-manifest\n", encoding="utf-8")

    assert _resolve_agent_id(None, root) == "agt-root-manifest"


def test_manifest_requires_one_target(tmp_path) -> None:
    with pytest.raises(typer.BadParameter):
        _resolve_agent_id(None, None)
    with pytest.raises(typer.BadParameter):
        _resolve_agent_id("agt-test", tmp_path)


def test_manifest_command_edits_selected_policy() -> None:
    native = Mock()
    native.access_manifest.side_effect = [
        {
            "agent_id": "agt-test",
            "policies": [
                {"kind": "http", "subject": "api.example.com", "method": "GET", "path": "/", "policy": "always"},
            ],
        },
        {
            "agent_id": "agt-test",
            "policies": [
                {"kind": "http", "subject": "api.example.com", "method": "GET", "path": "/", "policy": "never"},
            ],
        },
    ]
    client = Mock(native=native)

    with (
        patch("ageos.cli.manifest.SchedulerClient.local", return_value=client),
        patch("ageos.cli.manifest.Prompt.ask", side_effect=["1", "never"]),
    ):
        command(agent_id="agt-test", root_dir=None)

    native.apply_access_policy.assert_called_once_with(
        "agt-test",
        kind="http",
        subject="api.example.com",
        method="GET",
        path="/",
        policy="never",
    )


def test_choose_policy_index_can_quit() -> None:
    with patch("ageos.cli.manifest.Prompt.ask", return_value="q"):
        assert _choose_policy_index(2, Console(record=True)) is None
