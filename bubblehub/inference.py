from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

from bubblehub.log import log_debug, log_error, log_info


@dataclass(frozen=True)
class InferenceConfig:
    host: str
    port: int
    default_specialty: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def openai_base_url(self) -> str:
        return f"{self.base_url}/v1"


def load_inference_config() -> InferenceConfig:
    data = _load_models_config()
    inference = data.get("inference", {}) if isinstance(data, dict) else {}
    if not isinstance(inference, dict):
        inference = {}
    host = os.environ.get("BUBBLEHUB_INFERENCE_HOST") or str(inference.get("host") or "127.0.0.1")
    port = _optional_int(os.environ.get("BUBBLEHUB_INFERENCE_PORT")) or _optional_int(inference.get("port")) or 8000
    default_specialty = (
        os.environ.get("BUBBLEHUB_INFERENCE_SPECIALTY")
        or os.environ.get("BUBBLEHUB_SPECIALITY")
        or str(inference.get("default_specialty") or "default-instruct")
    )
    return InferenceConfig(host=host, port=port, default_specialty=default_specialty)


def ensure_inference_endpoint(config: InferenceConfig | None = None) -> str:
    """Return the shared inference base URL, starting the daemon if needed."""

    resolved = config or load_inference_config()
    explicit = os.environ.get("BUBBLEHUB_API_BASE_URL")
    base_url = explicit.rstrip("/") if explicit else resolved.base_url
    if is_healthy(base_url):
        log_debug("inference endpoint already healthy", base_url)
        return base_url
    daemon_config = _config_for_base_url(base_url, resolved)
    log_info("starting inference daemon", base_url)
    _start_inference_daemon(daemon_config)
    wait_until_healthy(base_url)
    log_info("inference endpoint ready", base_url)
    return base_url


def is_healthy(base_url: str) -> bool:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/health", timeout=1)
        return response.status_code < 500
    except requests.RequestException:
        return False


def wait_until_healthy(base_url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_healthy(base_url):
            return
        time.sleep(0.25)
    log_error("inference endpoint failed health check", base_url)
    raise RuntimeError(f"BubbleHub inference endpoint did not become healthy: {base_url}")


def apply_inference_env(env: dict[str, str], specialty: str | None = None) -> str:
    config = load_inference_config()
    base_url = ensure_inference_endpoint(config)
    env["BUBBLEHUB_API_BASE_URL"] = base_url
    env["OPENAI_BASE_URL"] = f"{base_url.rstrip('/')}/v1"
    env.setdefault("OPENAI_API_KEY", "bubblehub-local")
    env["BUBBLEHUB_SPECIALITY"] = specialty or config.default_specialty
    env["NO_PROXY"] = _append_no_proxy(env.get("NO_PROXY", ""))
    env["no_proxy"] = _append_no_proxy(env.get("no_proxy", ""))
    return base_url


def _config_for_base_url(base_url: str, fallback: InferenceConfig) -> InferenceConfig:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https", ""}:
        return fallback
    host = parsed.hostname or fallback.host
    port = parsed.port or fallback.port
    return InferenceConfig(host=host, port=port, default_specialty=fallback.default_specialty)


def _start_inference_daemon(config: InferenceConfig) -> None:
    python = _bubblehub_python()
    env = os.environ.copy()
    env["BUBBLEHUB_INFERENCE_HOST"] = config.host
    env["BUBBLEHUB_INFERENCE_PORT"] = str(config.port)
    env["BUBBLEHUB_INFERENCE_SPECIALTY"] = config.default_specialty
    env.setdefault("BUBBLEHUB_LOG_LEVEL", os.environ.get("BUBBLEHUB_LOG_LEVEL", "error"))
    if "BUBBLEHUB_LOG_FILE" in os.environ:
        env["BUBBLEHUB_LOG_FILE"] = os.environ["BUBBLEHUB_LOG_FILE"]
    log_debug("spawning inference daemon", f"python={python} host={config.host} port={config.port}")
    subprocess.Popen(
        [python, "-m", "bubblehub.cli.inference_daemon"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _load_models_config() -> dict[str, object]:
    with resources.files("bubblehub.config").joinpath("models.yaml").open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    override_path = Path.home() / ".config" / "bubblehub" / "models.yaml"
    if override_path.exists():
        with override_path.open("r", encoding="utf-8") as handle:
            override = yaml.safe_load(handle)
        if isinstance(data, dict) and isinstance(override, dict):
            merged = dict(data)
            for key, value in override.items():
                if key == "specialties" and isinstance(value, dict):
                    specialties = dict(data.get("specialties", {}))
                    specialties.update(value)
                    merged["specialties"] = specialties
                else:
                    merged[key] = value
            data = merged
    explicit_path = os.environ.get("BUBBLEHUB_MODELS_CONFIG")
    if explicit_path:
        with Path(explicit_path).expanduser().open("r", encoding="utf-8") as handle:
            override = yaml.safe_load(handle)
        if isinstance(data, dict) and isinstance(override, dict):
            merged = dict(data)
            for key, value in override.items():
                if key == "specialties" and isinstance(value, dict):
                    specialties = dict(data.get("specialties", {}))
                    specialties.update(value)
                    merged["specialties"] = specialties
                else:
                    merged[key] = value
            data = merged
    return data if isinstance(data, dict) else {}


def _append_no_proxy(value: str) -> str:
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    for entry in ["127.0.0.1", "localhost"]:
        if entry not in entries:
            entries.append(entry)
    return ",".join(entries)


def _bubblehub_python() -> str:
    bubblehub_python = Path(os.environ.get("BUBBLEHUB_PYTHON", "/opt/bubblehub/bin/python"))
    return str(bubblehub_python if bubblehub_python.exists() else Path(sys.executable))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
