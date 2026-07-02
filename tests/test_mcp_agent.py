from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIMPLE_AGENT_PATH = ROOT / "examples" / "mcp_agent" / "simple_agent.py"


def _load_simple_agent():
    spec = importlib.util.spec_from_file_location("simple_agent", SIMPLE_AGENT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {SIMPLE_AGENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


simple_agent = _load_simple_agent()


def test_format_tool_result_prefers_text_content() -> None:
    result = {"content": [{"type": "text", "text": "hello"}]}
    assert simple_agent.format_tool_result(result) == "hello"


def test_format_tool_result_reads_structured_content() -> None:
    result = {"structuredContent": {"result": 579}}
    assert simple_agent.format_tool_result(result) == "579"


def test_format_tool_result_falls_back_to_json() -> None:
    result = {"unexpected": True}
    assert simple_agent.format_tool_result(result) == json.dumps(result, sort_keys=True)


def test_proxy_config_uses_http_proxy_env(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.delenv("BUBBLEHUB_HTTP_PROXY_PORT", raising=False)
    assert simple_agent.proxy_config() == {
        "http": "http://127.0.0.1:9999",
        "https": "http://127.0.0.1:9999",
    }


def test_proxy_config_builds_from_bubblehub_port(monkeypatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setenv("BUBBLEHUB_HTTP_PROXY_PORT", "18080")
    assert simple_agent.proxy_config() == {
        "http": "http://127.0.0.1:18080",
        "https": "http://127.0.0.1:18080",
    }


def test_proxy_config_returns_none_without_proxy(monkeypatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("BUBBLEHUB_HTTP_PROXY_PORT", raising=False)
    assert simple_agent.proxy_config() is None


def test_simple_agent_rejects_conflicting_modes() -> None:
    result = subprocess.run(
        [sys.executable, str(SIMPLE_AGENT_PATH), "--http-url", "http://127.0.0.1/mcp", "--addition"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "choose only one" in result.stderr
