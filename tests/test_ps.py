from __future__ import annotations

from unittest.mock import Mock, patch

from bubblehub.app.agents import write_agent_metadata
from bubblehub.cli.ps import command


def test_ps_lists_agent_display_name(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BUBBLEHUB_STATE_DIR", str(tmp_path / "state"))
    write_agent_metadata(
        "agt-test",
        name="researcher",
        root_dir=str(tmp_path / "workspace"),
        workdir=str(tmp_path / "workspace"),
        binary="/bin/agent",
    )
    client = Mock()
    client.status_snapshot.return_value = {
        "memory_pressure": "available",
        "hardware": {},
        "limits": {},
        "agents": [{"agent_id": "agt-test", "pid": 123, "binary": "/bin/agent", "status": "running", "niceness": 0, "specialty": "code"}],
        "models": [],
    }

    with (
        patch("bubblehub.cli.ps.is_sandboxed", return_value=False),
        patch("bubblehub.cli.ps.SchedulerClient.local", return_value=client),
    ):
        command()

    assert "researcher" in capsys.readouterr().out


def test_ps_kill_stops_agent(capsys) -> None:
    client = Mock()

    with (
        patch("bubblehub.cli.ps.is_sandboxed", return_value=False),
        patch("bubblehub.cli.ps.SchedulerClient.local", return_value=client),
        patch("bubblehub.cli.ps.stop_agent", return_value={"agent_id": "agt-test", "pid": 123, "stopped": True}) as stop,
    ):
        command(kill="agt-test")

    stop.assert_called_once_with("agt-test", client)
    assert "Stopped agt-test" in capsys.readouterr().out
