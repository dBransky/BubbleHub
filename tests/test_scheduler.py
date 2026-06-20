from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ageos.cli.main import app
from ageos.native import LibAgeosError
from ageos.node.client import SchedulerClient


def test_ram_available_admits_background(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    admission = SchedulerClient.local().admit_model_job(
        specialty="default-instruct",
        model_name="tiny",
        niceness=10,
        ram_gb=0,
        vram_gb=0,
    )
    assert admission.allowed
    assert admission.state == "available"


def test_ram_low_queues_background(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    client = SchedulerClient.local()
    client.native.configure_limits(10, 0)
    admission = client.admit_model_job(
        specialty="default-instruct",
        model_name="large",
        niceness=10,
        ram_gb=8.5,
        vram_gb=0,
    )
    assert not admission.allowed
    assert admission.state == "low"
    assert client.queue_snapshot()[0]["model_name"] == "large"


def test_idle_warm_model_stays_loaded_until_lru_eviction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    client = SchedulerClient.local()
    client.native.configure_limits(10, 0)
    client.mark_model_loaded("old", "default-instruct", "llama", 6, 0, 999999, 51000)
    client.mark_model_unloaded("old")
    idle_snapshot = client.status_snapshot()
    assert any(model["name"] == "old" and model["refcount"] == 0 for model in idle_snapshot["models"])

    admission = client.admit_model_job(
        specialty="default-instruct",
        model_name="new",
        niceness=0,
        ram_gb=6,
        vram_gb=0,
    )
    assert admission.allowed
    assert not any(model["name"] == "old" for model in client.status_snapshot()["models"])


def test_active_model_is_not_evicted_for_new_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    client = SchedulerClient.local()
    client.native.configure_limits(10, 0)
    client.mark_model_loaded("active", "default-instruct", "llama", 6, 0, 999999, 51000)

    admission = client.admit_model_job(
        specialty="default-instruct",
        model_name="new",
        niceness=0,
        ram_gb=6,
        vram_gb=0,
    )
    assert not admission.allowed
    assert "no idle model" in admission.reason
    assert any(model["name"] == "active" for model in client.status_snapshot()["models"])


def test_scheduler_limits_load_from_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    config_dir = tmp_path / ".config" / "ageos"
    config_dir.mkdir(parents=True)
    (config_dir / "models.yaml").write_text(
        "scheduler:\n  ram_limit_gb: 7\n  vram_limit_gb: 3\n",
        encoding="utf-8",
    )

    limits = SchedulerClient.local().resource_limits()
    assert limits["ram_bytes"] == 7 * 1024**3
    assert limits["vram_bytes"] == 3 * 1024**3


def test_scheduler_limits_load_from_explicit_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    config_path = tmp_path / "custom-models.yaml"
    config_path.write_text(
        "scheduler:\n  ram_limit_gb: 11\n  vram_limit_gb: 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGEOS_MODELS_CONFIG", str(config_path))

    limits = SchedulerClient.local().resource_limits()
    assert limits["ram_bytes"] == 11 * 1024**3
    assert limits["vram_bytes"] == 5 * 1024**3


def test_models_stop_evicts_all_loaded_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGEOS_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    client = SchedulerClient.local()
    client.mark_model_loaded("first", "default-instruct", "llama", 1, 0, 999999, 51000)
    client.mark_model_loaded("second", "default-instruct", "llama", 1, 0, 999998, 51001)

    result = CliRunner().invoke(app, ["models", "stop"])

    assert result.exit_code == 0, result.output
    assert "Stopped 2 loaded model(s)." in result.output
    assert SchedulerClient.local().status_snapshot()["models"] == []


def test_missing_libageos_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import ageos.native as native

    monkeypatch.setattr(native.Path, "exists", lambda self: False)
    with pytest.raises(LibAgeosError, match="libageos.so is required"):
        native._load_libageos()

