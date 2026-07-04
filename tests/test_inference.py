from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from bubblehub.cli.run import _apply_sandbox_inference_env, _sandbox_inference_endpoint
from bubblehub.inference import (
    _BUBBLEHUB_MODULE_BOOTSTRAP,
    InferenceConfig,
    _append_no_proxy,
    _bubblehub_python,
    _config_for_base_url,
    _load_models_config,
    _optional_int,
    apply_inference_env,
    ensure_inference_endpoint,
    is_healthy,
    load_inference_config,
    wait_until_healthy,
)


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
    monkeypatch.setenv("BUBBLEHUB_MODELS_CONFIG", str(config_path))

    config = load_inference_config()

    assert config.host == "127.0.0.2"
    assert config.port == 8123
    assert config.default_specialty == "ci-instruct"


def test_apply_inference_env_sets_openai_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_API_BASE_URL", "http://127.0.0.1:8000")
    env: dict[str, str] = {}
    with patch("bubblehub.inference.is_healthy", return_value=True):
        base_url = apply_inference_env(env, "code-review")
    assert base_url == "http://127.0.0.1:8000"
    assert env["BUBBLEHUB_API_BASE_URL"] == "http://127.0.0.1:8000"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert env["OPENAI_API_KEY"] == "bubblehub-local"
    assert env["BUBBLEHUB_SPECIALITY"] == "code-review"


def test_ensure_inference_endpoint_checks_explicit_api_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_API_BASE_URL", "http://127.0.0.1:8123")
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("bubblehub.inference.is_healthy", return_value=True) as healthy:
        with patch("bubblehub.inference._start_inference_daemon") as start:
            assert ensure_inference_endpoint(config) == "http://127.0.0.1:8123"
    healthy.assert_called_once_with("http://127.0.0.1:8123")
    start.assert_not_called()


def test_ensure_inference_endpoint_starts_daemon_for_unhealthy_explicit_api_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUBBLEHUB_API_BASE_URL", "http://127.0.0.1:8123")
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("bubblehub.inference.is_healthy", side_effect=[False, True]) as healthy:
        with patch("bubblehub.inference._start_inference_daemon") as start:
            with patch("bubblehub.inference.wait_until_healthy") as wait:
                assert ensure_inference_endpoint(config) == "http://127.0.0.1:8123"
    healthy.assert_called()
    start.assert_called_once_with(InferenceConfig(host="127.0.0.1", port=8123, default_specialty="default-instruct"))
    wait.assert_called_once_with("http://127.0.0.1:8123")


def test_ensure_inference_endpoint_reuses_healthy_server() -> None:
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("bubblehub.inference.is_healthy", return_value=True) as healthy:
        with patch("bubblehub.inference._start_inference_daemon") as start:
            assert ensure_inference_endpoint(config) == "http://127.0.0.1:8000"
    healthy.assert_called_once_with("http://127.0.0.1:8000")
    start.assert_not_called()


def test_ensure_inference_endpoint_starts_daemon_when_unhealthy() -> None:
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("bubblehub.inference.is_healthy", side_effect=[False, True]) as healthy:
        with patch("bubblehub.inference._start_inference_daemon") as start:
            with patch("bubblehub.inference.wait_until_healthy") as wait:
                assert ensure_inference_endpoint(config) == "http://127.0.0.1:8000"
    healthy.assert_called()
    start.assert_called_once_with(config)
    wait.assert_called_once_with("http://127.0.0.1:8000")


def test_inference_daemon_discards_stdio_to_avoid_pipe_deadlock() -> None:
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")
    with patch("bubblehub.inference.subprocess.Popen") as popen:
        from bubblehub.inference import _start_inference_daemon

        _start_inference_daemon(config)

    args = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert args[1:3] == ["-I", "-c"]
    assert "bubblehub.cli.inference_daemon" in args[3]
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    package_root = Path(__file__).resolve().parents[1]
    assert str(package_root) in kwargs["env"]["BUBBLEHUB_PYTHONPATH"].split(os.pathsep)


def test_inference_daemon_preserves_existing_bubblehub_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUBBLEHUB_PYTHONPATH", "/tmp/custom-bubblehub")
    config = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")

    with patch("bubblehub.inference.subprocess.Popen") as popen:
        from bubblehub.inference import _start_inference_daemon

        _start_inference_daemon(config)

    pythonpath = popen.call_args.kwargs["env"]["BUBBLEHUB_PYTHONPATH"].split(os.pathsep)
    assert pythonpath[0] == str(Path(__file__).resolve().parents[1])
    assert "/tmp/custom-bubblehub" in pythonpath


def test_inference_daemon_bootstrap_runs_under_isolated_python(tmp_path: Path) -> None:
    package = tmp_path / "bubblehub" / "cli"
    package.mkdir(parents=True)
    marker = tmp_path / "daemon-main-called"
    (tmp_path / "bubblehub" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "inference_daemon.py").write_text(
        "from pathlib import Path\n"
        "def main():\n"
        f"    Path({str(marker)!r}).write_text('ok', encoding='utf-8')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["BUBBLEHUB_PYTHONPATH"] = str(tmp_path)

    subprocess.run([sys.executable, "-I", "-c", _BUBBLEHUB_MODULE_BOOTSTRAP], env=env, check=True)

    assert marker.read_text(encoding="utf-8") == "ok"


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
    assert env["BUBBLEHUB_API_BASE_URL"] == "http://127.0.0.1:8000"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert env["BUBBLEHUB_SANDBOX_INFERENCE_HOST"] == "127.0.0.1"
    assert env["BUBBLEHUB_SANDBOX_INFERENCE_PORT"] == "8000"
    assert env["BUBBLEHUB_NETWORK"] == "inference-only"


def test_load_inference_config_env_overrides_and_bad_yaml_shape(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "models.yaml"
    config_path.write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv("BUBBLEHUB_MODELS_CONFIG", str(config_path))
    monkeypatch.setenv("BUBBLEHUB_INFERENCE_HOST", "0.0.0.0")
    monkeypatch.setenv("BUBBLEHUB_INFERENCE_PORT", "9000")
    monkeypatch.setenv("BUBBLEHUB_INFERENCE_SPECIALTY", "env-specialty")

    config = load_inference_config()

    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.default_specialty == "env-specialty"
    assert "models" in _load_models_config()


def test_load_inference_config_handles_non_dict_inference_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_INFERENCE_HOST", raising=False)
    monkeypatch.delenv("BUBBLEHUB_INFERENCE_PORT", raising=False)
    monkeypatch.delenv("BUBBLEHUB_INFERENCE_SPECIALTY", raising=False)
    monkeypatch.delenv("BUBBLEHUB_SPECIALITY", raising=False)

    with patch("bubblehub.inference._load_models_config", return_value={"inference": []}):
        config = load_inference_config()

    assert config == InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default-instruct")


def test_load_models_config_merges_home_and_explicit_overrides(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home_override = home / ".config" / "bubblehub" / "models.yaml"
    home_override.parent.mkdir(parents=True)
    home_override.write_text(
        """
inference:
  port: 8100
specialties:
  home-specialty:
    capability: instruct
""",
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        """
specialties:
  explicit-specialty:
    capability: code
extra: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BUBBLEHUB_MODELS_CONFIG", str(explicit))

    data = _load_models_config()

    assert data["inference"]["port"] == 8100
    assert "home-specialty" in data["specialties"]
    assert "explicit-specialty" in data["specialties"]
    assert data["extra"] is True


def test_inference_health_wait_and_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    ok_response = Mock(status_code=204)
    bad_response = Mock(status_code=503)
    with patch("bubblehub.inference.requests.get", return_value=ok_response):
        assert is_healthy("http://127.0.0.1:8000")
    with patch("bubblehub.inference.requests.get", return_value=bad_response):
        assert not is_healthy("http://127.0.0.1:8000")
    with patch("bubblehub.inference.requests.get", side_effect=requests.RequestException):
        assert not is_healthy("http://127.0.0.1:8000")

    with patch("bubblehub.inference.is_healthy", side_effect=[False, True]) as healthy, patch("bubblehub.inference.time.sleep") as sleep:
        wait_until_healthy("http://127.0.0.1:8000", timeout_seconds=1)
    assert healthy.call_count == 2
    sleep.assert_called_once_with(0.25)

    with (
        patch("bubblehub.inference.is_healthy", return_value=False),
        patch("bubblehub.inference.time.sleep"),
        patch("bubblehub.inference.time.time", side_effect=[0, 2]),
        pytest.raises(RuntimeError, match="did not become healthy"),
    ):
        wait_until_healthy("http://127.0.0.1:8000", timeout_seconds=1)


def test_inference_config_url_parsing_and_env_helpers(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = InferenceConfig(host="127.0.0.1", port=8000, default_specialty="default")
    assert _config_for_base_url("ftp://example.com:9000", fallback) == fallback
    assert _config_for_base_url("http://example.com", fallback) == InferenceConfig("example.com", 8000, "default")
    assert _config_for_base_url("http://example.com:9001", fallback) == InferenceConfig("example.com", 9001, "default")

    assert _append_no_proxy("example.com,127.0.0.1") == "example.com,127.0.0.1,localhost"
    assert _optional_int("7") == 7
    assert _optional_int("0") is None
    assert _optional_int("bad") is None
    assert _optional_int(None) is None

    custom_python = tmp_path / "python"
    custom_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("BUBBLEHUB_PYTHON", str(custom_python))
    assert _bubblehub_python() == str(custom_python)


def test_start_inference_daemon_forwards_log_file(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from bubblehub.inference import _start_inference_daemon

    monkeypatch.setenv("BUBBLEHUB_LOG_FILE", "/tmp/bubblehub.log")
    monkeypatch.setenv("BUBBLEHUB_LOG_LEVEL", "debug")
    config = InferenceConfig(host="127.0.0.2", port=8123, default_specialty="code")
    with patch("bubblehub.inference.subprocess.Popen") as popen:
        _start_inference_daemon(config)

    env = popen.call_args.kwargs["env"]
    assert env["BUBBLEHUB_INFERENCE_HOST"] == "127.0.0.2"
    assert env["BUBBLEHUB_INFERENCE_PORT"] == "8123"
    assert env["BUBBLEHUB_INFERENCE_SPECIALTY"] == "code"
    assert env["BUBBLEHUB_LOG_FILE"] == "/tmp/bubblehub.log"
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["stdout"] is subprocess.DEVNULL
