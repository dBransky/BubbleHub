from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

import pytest
import requests

from bubblehub.engine.session import EngineSession
from bubblehub.http_api import ApiConfig, create_http_server
from bubblehub.integrations.openai_shim import BubbleHubOpenAI
from bubblehub.node.client import SchedulerClient


def test_native_inference_reuses_warm_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    starts = tmp_path / "starts.log"
    _write_fake_llama_server(tmp_path / "llama-server", starts)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("BUBBLEHUB_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))

    client = SchedulerClient.local()
    request = {
        "specialty": "default-instruct",
        "model_name": "native-cache-test",
        "backend": "llama",
        "model_path": str(tmp_path / "model.gguf"),
        "ram_gb": 0.1,
        "vram_gb": 0,
        "niceness": 0,
        "max_tokens": 8,
        "gpu_layers": -999999,
        "messages_json": json.dumps([{"role": "user", "content": "hi"}]),
    }

    try:
        first = client.inference_chat(request)
        second = client.inference_chat(request)

        assert first["content"] == "fake-native"
        assert second["content"] == "fake-native"
        assert first["pid"] == second["pid"]
        assert first["port"] == second["port"]
        assert starts.read_text(encoding="utf-8").count("start ") == 1
        snapshot = client.status_snapshot()
        model = next(item for item in snapshot["models"] if item["name"] == "native-cache-test")
        assert model["refcount"] == 0
        assert model["pid"] == first["pid"]
        assert model["port"] == first["port"]
    finally:
        client.evict_model("native-cache-test")
        if "first" in locals():
            _wait_for_process_exit(int(first["pid"]))


def test_native_inference_starts_vllm_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    starts = tmp_path / "vllm-starts.log"
    _write_fake_vllm_python(tmp_path / "fake-python", starts)
    monkeypatch.setenv("BUBBLEHUB_PYTHON", str(tmp_path / "fake-python"))
    monkeypatch.setenv("BUBBLEHUB_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))

    client = SchedulerClient.local()
    request = {
        "specialty": "default-instruct",
        "model_name": "native-vllm-test",
        "backend": "vllm",
        "model_path": str(tmp_path / "model-dir"),
        "ram_gb": 0.1,
        "vram_gb": 0.1,
        "niceness": 0,
        "max_tokens": 8,
        "gpu_layers": -999999,
        "messages_json": json.dumps([{"role": "user", "content": "hi"}]),
    }

    try:
        response = client.inference_chat(request)

        assert response["content"] == "fake-vllm"
        assert starts.read_text(encoding="utf-8").count("start native-vllm-test") == 1
    finally:
        client.evict_model("native-vllm-test")
        if "response" in locals():
            _wait_for_process_exit(int(response["pid"]))


def test_entrypoints_share_native_model_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    starts = tmp_path / "starts.log"
    _write_fake_llama_server(tmp_path / "llama-server", starts)
    _write_models_config(tmp_path / "models.yaml")
    _write_cached_model(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("BUBBLEHUB_SCHEDULER_STATE", str(tmp_path / "scheduler.state"))
    monkeypatch.setenv("BUBBLEHUB_MODELS_CONFIG", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("BUBBLEHUB_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("BUBBLEHUB_MAX_OUTPUT_TOKENS", "8")
    monkeypatch.delenv("BUBBLEHUB_NETWORK", raising=False)
    monkeypatch.delenv("BUBBLEHUB_API_BASE_URL", raising=False)

    client = SchedulerClient.local()
    server = create_http_server(ApiConfig(port=0))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pid = 0
    try:
        with EngineSession("default-instruct") as session:
            assert session.chat([{"role": "user", "content": "prompt"}]) == "fake-native"

        assert (
            BubbleHubOpenAI(speciality="default-instruct")
            .chat.completions.create(model="bubblehub-local", messages=[{"role": "user", "content": "shim"}])
            .choices[0]
            .message.content
            == "fake-native"
        )

        response = requests.post(
            f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions",
            json={
                "model": "bubblehub-local",
                "bubblehub_specialty": "default-instruct",
                "messages": [{"role": "user", "content": "http"}],
            },
            timeout=5,
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "fake-native"

        sandbox_result = _run_sandbox_style_native_forward(tmp_path, server.server_address[1])
        assert sandbox_result == "fake-native"

        snapshot = client.status_snapshot()
        model = next(item for item in snapshot["models"] if item["name"] == "native-cache-test")
        pid = int(model["pid"])
        assert model["refcount"] == 0
        assert starts.read_text(encoding="utf-8").count("start ") == 1
    finally:
        server.shutdown()
        server.server_close()
        client.evict_model("native-cache-test")
        if pid:
            _wait_for_process_exit(pid)


def _write_fake_llama_server(path: Path, starts: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--model")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--ctx-size")
parser.add_argument("--parallel")
parser.add_argument("--n-gpu-layers")
args = parser.parse_args()

with open({str(starts)!r}, "a", encoding="utf-8") as handle:
    handle.write(f"start {{args.port}}\\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = {{"choices": [{{"message": {{"content": "fake-native"}}}}]}}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


HTTPServer((args.host, args.port), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_fake_vllm_python(path: Path, starts: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env python3
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

if sys.argv[1:3] != ["-m", "vllm.entrypoints.openai.api_server"]:
    raise SystemExit(2)

parser = argparse.ArgumentParser()
parser.add_argument("--model")
parser.add_argument("--served-model-name", required=True)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args(sys.argv[3:])

with open({str(starts)!r}, "a", encoding="utf-8") as handle:
    handle.write(f"start {{args.served_model_name}} {{args.port}}\\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = {{"choices": [{{"message": {{"content": "fake-vllm"}}}}]}}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


HTTPServer((args.host, args.port), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_models_config(path: Path) -> None:
    path.write_text(
        """
models:
  - name: native-cache-test
    flavor: fake
    capability: instruct
    tier: small
    backend: llama
    repo_id: local/native-cache-test
    filename: model.gguf
    ram_gb: 0.1
    vram_gb: 0
    context_tokens: 512
specialties:
  default-instruct:
    capability: instruct
    model: native-cache-test
""",
        encoding="utf-8",
    )


def _write_cached_model(tmp_path: Path) -> None:
    model = tmp_path / "cache" / "models" / "native-cache-test" / "model.gguf"
    model.parent.mkdir(parents=True)
    model.write_text("fake", encoding="utf-8")


def _run_sandbox_style_native_forward(tmp_path: Path, port: int) -> str:
    request = {
        "specialty": "default-instruct",
        "model_name": "native-cache-test",
        "backend": "llama",
        "model_path": str(tmp_path / "cache" / "models" / "native-cache-test" / "model.gguf"),
        "ram_gb": 0.1,
        "vram_gb": 0,
        "niceness": 0,
        "max_tokens": 8,
        "gpu_layers": -999999,
        "messages_json": json.dumps([{"role": "user", "content": "sandbox"}]),
    }
    script = f"""
import json
from bubblehub.node.client import SchedulerClient
response = SchedulerClient.local().inference_chat({request!r})
print(json.dumps(response))
"""
    env = os.environ.copy()
    env["BUBBLEHUB_NETWORK"] = "inference-only"
    env["BUBBLEHUB_API_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return str(json.loads(result.stdout)["content"])


def _wait_for_process_exit(pid: int) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            finished, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if finished == pid:
            return
        time.sleep(0.05)
