from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from bubblehub.native import NativeScheduler


def test_scheduler_state_is_visible_across_processes(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["BUBBLEHUB_SCHEDULER_STATE"] = str(tmp_path / "scheduler.state")
    env["PYTHONPATH"] = str(repo_root)

    writer = """
from bubblehub.node.client import SchedulerClient
client = SchedulerClient.local()
agent_id = client.register_agent('/bin/agent', 0, 'default-instruct', pid=12345)
client.mark_model_loaded('mistral-instruct-small', 'default-instruct', 'llama', 9, 0, 23456, 51000)
print(agent_id)
"""
    reader = """
from bubblehub.node.client import SchedulerClient
snapshot = SchedulerClient.local().status_snapshot()
assert any(agent['binary'] == '/bin/agent' for agent in snapshot['agents']), snapshot
assert any(model['name'] == 'mistral-instruct-small' for model in snapshot['models']), snapshot
print('ok')
"""

    subprocess.run([sys.executable, "-c", writer], check=True, env=env, cwd=repo_root)
    result = subprocess.run(
        [sys.executable, "-c", reader],
        check=True,
        env=env,
        cwd=repo_root,
        text=True,
        capture_output=True,
    )

    assert "ok" in result.stdout


def test_run_sandbox_invokes_native_helper(monkeypatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_SANDBOX_HELPER", "/tmp/bubblehub-sandbox")

    with patch("bubblehub.native.subprocess.call", return_value=0) as call:
        result = NativeScheduler().run_sandbox(
            "/bin/echo",
            ["/bin/echo", "ok"],
            resource_niceness=3,
            memory_max=1024,
            cpu_percent=50,
            workdir="/workspace",
            root_dir="/tmp/root",
            rootfs_dir="/tmp/rootfs",
            overlay_upper_dir="/tmp/upper",
            overlay_work_dir="/tmp/work",
            isolate_network=True,
            inference_host="127.0.0.1",
            inference_port=8000,
            sandbox_inference_port=18000,
        )

    assert result == 0
    assert call.call_args.args[0] == [
        "/tmp/bubblehub-sandbox",
        "--memory",
        "1024",
        "--cpu",
        "50",
        "--niceness",
        "3",
        "--workdir",
        "/workspace",
        "--root-dir",
        "/tmp/root",
        "--rootfs-dir",
        "/tmp/rootfs",
        "--overlay-upper-dir",
        "/tmp/upper",
        "--overlay-work-dir",
        "/tmp/work",
        "--isolate-network",
        "--inference-host",
        "127.0.0.1",
        "--inference-port",
        "8000",
        "--sandbox-inference-port",
        "18000",
        "--",
        "/bin/echo",
        "ok",
    ]
