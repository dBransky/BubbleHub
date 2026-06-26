from __future__ import annotations

from unittest.mock import patch

import pytest

from ageos.cli.run import _apply_sandbox_inference_env, _sandbox_inference_endpoint
from ageos.inference import InferenceConfig, apply_inference_env, ensure_inference_endpoint, load_inference_config


def test_load_inference_config_from_bundled_yaml() -> None:
    config = load_inference_config()
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.default_specialty == "default-instruct"
    assert config.openai_base_url == "http://127.0.0.1:8000/v1"


def test_load_inference_config_from_explicit_models_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
inference:
  host: 127.0.0.2
  port: 8123
  default_specialty: ci-instruct
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGEOS_MODELS_CONFIG", str(config_path))

    config = load_inference_config()

    assert config.host == "127.0.0.2"
    assert config.port == 8123
    assert config.default_specialty == "ci-instruct"


def test_apply_inference_env_sets_openai_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_API_BASE_URL", "http://127.0.0.1:8000")
    env: dict[str, str] = {}
    with patch("ageos.inference.is_healthy", return_value=True):
        base_url = apply_inference_env(env, "code-review")
    assert base_url == "http://127.0.0.1:8000"
    assert env["AGEOS_API_BASE_URL"] == "http://127.0.0.1:8000"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert env["OPENAI_API_KEY"] == "ageos-local"
    assert env["AGEOS_SPECIALITY"] == "code-review"


def test_ensure_inference_endpoint_checks_explicit_api_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGEOS_API_BASE_URL", "http://127.0.0.1:8123")
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("ageos.inference.is_healthy", return_value=True) as healthy:
        with patch("ageos.inference._start_inference_daemon") as start:
            assert ensure_inference_endpoint(config) == "http://127.0.0.1:8123"
    healthy.assert_called_once_with("http://127.0.0.1:8123")
    start.assert_not_called()


def test_ensure_inference_endpoint_starts_daemon_for_unhealthy_explicit_api_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGEOS_API_BASE_URL", "http://127.0.0.1:8123")
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("ageos.inference.is_healthy", side_effect=[False, True]) as healthy:
        with patch("ageos.inference._start_inference_daemon") as start:
            with patch("ageos.inference.wait_until_healthy") as wait:
                assert ensure_inference_endpoint(config) == "http://127.0.0.1:8123"
    healthy.assert_called()
    start.assert_called_once_with(InferenceConfig(host="127.0.0.1", port=8123, default_specialty="default-instruct"))
    wait.assert_called_once_with("http://127.0.0.1:8123")


def test_ensure_inference_endpoint_reuses_healthy_server() -> None:
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("ageos.inference.is_healthy", return_value=True) as healthy:
        with patch("ageos.inference._start_inference_daemon") as start:
            assert ensure_inference_endpoint(config) == "http://127.0.0.1:8000"
    healthy.assert_called_once_with("http://127.0.0.1:8000")
    start.assert_not_called()


def test_ensure_inference_endpoint_starts_daemon_when_unhealthy() -> None:
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("ageos.inference.is_healthy", side_effect=[False, True]) as healthy:
        with patch("ageos.inference._start_inference_daemon") as start:
            with patch("ageos.inference.wait_until_healthy") as wait:
                assert ensure_inference_endpoint(config) == "http://127.0.0.1:8000"
    healthy.assert_called()
    start.assert_called_once_with(config)
    wait.assert_called_once_with("http://127.0.0.1:8000")


def test_inference_daemon_discards_stdio_to_avoid_pipe_deadlock() -> None:
    import subprocess

    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("ageos.inference.subprocess.Popen") as popen:
        from ageos.inference import _start_inference_daemon

        _start_inference_daemon(config)

    kwargs = popen.call_args.kwargs
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_sandbox_endpoint_uses_loopback_with_same_port() -> None:
    endpoint = _sandbox_inference_endpoint("http://10.0.0.10:8123")
    assert endpoint.host == "10.0.0.10"
    assert endpoint.host_port == 8123
    assert endpoint.sandbox_base_url == "http://127.0.0.1:8123"
    assert endpoint.sandbox_port == 8123


def test_apply_sandbox_inference_env_rewrites_endpoint() -> None:
    endpoint = _sandbox_inference_endpoint("http://127.0.0.1:8000")
    env: dict[str, str] = {}
    _apply_sandbox_inference_env(env, endpoint)
    assert env["AGEOS_API_BASE_URL"] == "http://127.0.0.1:8000"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert env["AGEOS_SANDBOX_INFERENCE_HOST"] == "127.0.0.1"
    assert env["AGEOS_SANDBOX_INFERENCE_PORT"] == "8000"
    assert env["AGEOS_NETWORK"] == "inference-only"
