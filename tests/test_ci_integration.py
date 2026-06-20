from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def _integration_enabled() -> bool:
    return os.environ.get("AGEOS_RUN_INTEGRATION") == "1"


def _integration_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("AGEOS_API_BASE_URL", None)
    env.setdefault("AGEOS_CACHE", str(tmp_path / "ageos-cache"))
    env.setdefault("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    env.setdefault("AGEOS_LLAMA_CTX_SIZE", "512")
    env.setdefault("AGEOS_MAX_OUTPUT_TOKENS", "32")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    return env


@pytest.fixture(scope="module")
def integration_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    _require_integration_runtime()
    env = _integration_env(tmp_path_factory.mktemp("ageos-integration"))
    _run(
        ["ageos", "prompt", "--text", "Reply with ok.", "--speciality", "default-instruct"],
        env=env,
        timeout=180,
    )
    return env


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    return result


def _require_integration_runtime() -> None:
    if not _integration_enabled():
        pytest.skip("set AGEOS_RUN_INTEGRATION=1 to run real local-inference integration tests")
    for binary in ("ageos", "llama-server"):
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} is not installed")


@pytest.fixture
def openclaw_sandbox_state() -> Iterator[tuple[Path, str]]:
    openclaw_root = ROOT / "examples" / "openclaw" / "openclaw"
    state_root = openclaw_root / ".ageos"
    agents_root = state_root / "agents"
    marker = state_root / "current-agent"
    marker_existed = marker.exists()
    previous_marker = marker.read_text(encoding="utf-8") if marker_existed else None
    ageos_existed = state_root.exists()
    agents_existed = agents_root.exists()
    agent_id = f"agt-ci-{uuid.uuid4().hex[:10]}"
    agent_dir = agents_root / agent_id

    try:
        (agent_dir / "home").mkdir(parents=True)
        marker.write_text(f"{agent_id}\n", encoding="utf-8")
        yield openclaw_root, agent_id
    finally:
        shutil.rmtree(agent_dir, ignore_errors=True)
        if previous_marker is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(previous_marker, encoding="utf-8")
        if not agents_existed:
            try:
                agents_root.rmdir()
            except OSError:
                pass
        if not ageos_existed:
            try:
                state_root.rmdir()
            except OSError:
                pass


def _agent_id_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("agent_id="):
            return line.removeprefix("agent_id=")
    raise AssertionError(f"agent_id line not found in output:\n{output}")


def _run_basic_agent(
    root_dir: Path, *, env: dict[str, str], force_new_sandbox: bool = False
) -> subprocess.CompletedProcess[str]:
    root_dir.mkdir(parents=True, exist_ok=True)
    agent_path = root_dir / "basic_agent.py"
    if not agent_path.exists():
        shutil.copy2(ROOT / "examples" / "basic" / "basic_agent.py", agent_path)
    command = [
        "ageos",
        "run",
        "--memory",
        "4G",
        "--root-dir",
        str(root_dir),
        "--binary",
        "./basic_agent.py",
    ]
    if force_new_sandbox:
        command.insert(2, "--force-new-sandbox")
    return _run(command, env=env, timeout=240)


def _openclaw_onboard_command(openclaw_root: Path) -> list[str]:
    return [
        "ageos",
        "run",
        "--memory",
        "4G",
        "--root-dir",
        str(openclaw_root),
        "--binary",
        "openclaw",
        "onboard",
        "--non-interactive",
        "--accept-risk",
        "--mode",
        "local",
        "--auth-choice",
        "skip",
        "--custom-base-url",
        "http://127.0.0.1:8000/v1",
        "--custom-api-key",
        "ageos-local",
        "--custom-model-id",
        "default-instruct",
        "--custom-provider-id",
        "ageos-ci",
        "--custom-compatibility",
        "openai",
        "--skip-daemon",
        "--skip-channels",
        "--skip-skills",
        "--skip-search",
        "--skip-health",
        "--skip-ui",
        "--skip-hooks",
        "--json",
    ]


def test_basic_agent_gets_model_response(integration_env: dict[str, str], tmp_path: Path) -> None:
    result = _run_basic_agent(tmp_path / "basic-agent", env=integration_env)

    assert "AgeOS basic agent starting" in result.stdout
    assert "model_response:" in result.stdout
    marker_index = result.stdout.index("model_response:")
    response = result.stdout[marker_index:].splitlines()[1].strip()
    assert response


def test_basic_agent_home_persists_across_runs(integration_env: dict[str, str], tmp_path: Path) -> None:
    root_dir = tmp_path / "basic-persistent"
    first = _run_basic_agent(root_dir, env=integration_env)
    second = _run_basic_agent(root_dir, env=integration_env)
    agent_id = _agent_id_from_output(first.stdout)

    assert _agent_id_from_output(second.stdout) == agent_id
    assert "Persistent sandbox found: reusing" in second.stdout
    assert "existing_home_data=<missing>" in first.stdout
    assert "existing_home_data=Hello, world!" in second.stdout
    persisted = root_dir / ".ageos" / "agents" / agent_id / "home" / "test.txt"
    assert persisted.read_text(encoding="utf-8") == "Hello, world!"


def test_basic_agent_force_new_sandbox_starts_with_fresh_home(
    integration_env: dict[str, str], tmp_path: Path
) -> None:
    root_dir = tmp_path / "basic-force-new"
    first = _run_basic_agent(root_dir, env=integration_env)
    second = _run_basic_agent(root_dir, env=integration_env, force_new_sandbox=True)
    first_agent_id = _agent_id_from_output(first.stdout)
    second_agent_id = _agent_id_from_output(second.stdout)

    assert second_agent_id != first_agent_id
    assert "Persistent sandbox found" not in second.stdout
    assert "existing_home_data=<missing>" in second.stdout
    assert not (root_dir / ".ageos" / "agents" / first_agent_id).exists()
    assert (root_dir / ".ageos" / "agents" / second_agent_id / "home" / "test.txt").exists()


def test_openclaw_onboard_configures_local_inference(
    integration_env: dict[str, str], openclaw_sandbox_state: tuple[Path, str]
) -> None:
    openclaw_root, agent_id = openclaw_sandbox_state
    openclaw_binary = openclaw_root / "node_modules" / ".bin" / "openclaw"
    if not openclaw_binary.exists():
        pytest.skip("OpenClaw example dependencies are not installed")
    if shutil.which("node", path=integration_env.get("PATH")) is None:
        pytest.skip("node is not installed")

    state_root = openclaw_root / ".ageos"
    config_path = state_root / "agents" / agent_id / "home" / ".openclaw" / "openclaw.json"

    _run(
        _openclaw_onboard_command(openclaw_root),
        env=integration_env,
        timeout=240,
    )

    assert config_path.exists(), f"OpenClaw config was not created under {config_path.parent}"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = config.get("gateway", {})
    wizard = config.get("wizard", {})
    assert gateway.get("mode") == "local"
    assert gateway.get("auth", {}).get("mode") == "token"
    assert wizard.get("lastRunCommand") == "onboard"
    assert wizard.get("lastRunMode") == "local"


def test_openclaw_reuses_configured_sandbox_without_reset(
    integration_env: dict[str, str], openclaw_sandbox_state: tuple[Path, str]
) -> None:
    openclaw_root, agent_id = openclaw_sandbox_state
    openclaw_binary = openclaw_root / "node_modules" / ".bin" / "openclaw"
    if not openclaw_binary.exists():
        pytest.skip("OpenClaw example dependencies are not installed")
    if shutil.which("node", path=integration_env.get("PATH")) is None:
        pytest.skip("node is not installed")

    state_root = openclaw_root / ".ageos"
    _run(_openclaw_onboard_command(openclaw_root), env=integration_env, timeout=240)

    first = _run(
        [
            "ageos",
            "run",
            "--memory",
            "4G",
            "--root-dir",
            str(openclaw_root),
            "--binary",
            "openclaw",
            "config",
            "validate",
            "--json",
        ],
        env=integration_env,
        timeout=240,
    )
    config_path = state_root / "agents" / agent_id / "home" / ".openclaw" / "openclaw.json"
    assert config_path.exists()

    second = _run(
        [
            "ageos",
            "run",
            "--memory",
            "4G",
            "--root-dir",
            str(openclaw_root),
            "--binary",
            "openclaw",
            "config",
            "validate",
            "--json",
        ],
        env=integration_env,
        timeout=240,
    )

    assert "Persistent sandbox found: reusing" in first.stdout
    assert f"Persistent sandbox found: reusing {agent_id}" in second.stdout
    assert (state_root / "current-agent").read_text(encoding="utf-8").strip() == agent_id
    assert config_path.exists()
