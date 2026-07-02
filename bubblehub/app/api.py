from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bubblehub import __version__
from bubblehub.app.agents import delete_agent, stop_agent
from bubblehub.app.models import models_overview, select_model_for_speciality
from bubblehub.app.telemetry import control_snapshot, pending_access
from bubblehub.log import log_debug, log_error
from bubblehub.node.client import SchedulerClient


@dataclass(frozen=True)
class ControlApiConfig:
    host: str = "127.0.0.1"
    port: int = 8010
    speciality: str = "default-instruct"


ClientFactory = Callable[[], SchedulerClient]


def run_control_api(config: ControlApiConfig) -> None:
    server = create_control_server(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return
    finally:
        server.server_close()


def create_control_server(
    config: ControlApiConfig,
    client_factory: ClientFactory | None = None,
) -> "BubbleHubControlServer":
    return BubbleHubControlServer((config.host, config.port), _handler_for(config), config, client_factory or SchedulerClient.local)


class BubbleHubControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        config: ControlApiConfig,
        client_factory: ClientFactory,
    ) -> None:
        self.config = config
        self.client_factory = client_factory
        super().__init__(server_address, handler_class)

    def client(self) -> SchedulerClient:
        return self.client_factory()


def _handler_for(config: ControlApiConfig) -> type[BaseHTTPRequestHandler]:
    class BubbleHubControlHandler(BaseHTTPRequestHandler):
        server_version = "bubblehub-control/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            log_debug("control api request", f"GET {path}")
            try:
                if path == "/":
                    self._send_static("index.html", "text/html; charset=utf-8")
                elif path == "/app.js":
                    self._send_static("app.js", "application/javascript; charset=utf-8")
                elif path == "/style.css":
                    self._send_static("style.css", "text/css; charset=utf-8")
                elif path.startswith("/icons/"):
                    self._send_icon(Path(path).name)
                elif path == "/health":
                    self._send_json(
                        {
                            "status": "ok",
                            "service": "bubblehub-control-center",
                            "version": __version__,
                            "time": time.time(),
                        }
                    )
                elif path == "/api/telemetry":
                    self._send_json(control_snapshot(_control_server(self.server).client()))
                elif path == "/api/manifest/pending":
                    self._send_json({"pending": pending_access(_control_server(self.server).client())})
                elif path.startswith("/api/agents/") and path.endswith("/manifest"):
                    self._send_json(self._agent_manifest(path))
                elif path == "/api/models":
                    speciality = parse_qs(parsed.query).get("speciality", [config.speciality])[0]
                    self._send_json(models_overview(speciality))
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, f"unknown endpoint: {path}")
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # noqa: BLE001 - local GUI should receive structured backend failures.
                log_error("control api backend failure", str(exc))
                self._send_error(HTTPStatus.BAD_GATEWAY, str(exc))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            log_debug("control api request", f"POST {path}")
            try:
                body = self._read_json()
                if path.startswith("/api/agents/") and path.endswith("/stop"):
                    self._stop_agent(path)
                elif path.startswith("/api/agents/") and path.endswith("/delete"):
                    self._delete_agent(path)
                elif path.startswith("/api/agents/") and path.endswith("/manifest/policies"):
                    self._apply_manifest_policy(path, body)
                elif path == "/api/models/select":
                    speciality = str(body.get("speciality") or body.get("specialty") or config.speciality)
                    model_name = str(body.get("model_name") or body.get("model") or "")
                    if not model_name:
                        raise ValueError("model_name is required")
                    self._send_json(select_model_for_speciality(speciality, model_name))
                elif path == "/api/models/evict":
                    self._evict_models(body)
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, f"unknown endpoint: {path}")
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # noqa: BLE001 - local GUI should receive structured backend failures.
                log_error("control api backend failure", str(exc))
                self._send_error(HTTPStatus.BAD_GATEWAY, str(exc))

        def log_message(self, format: str, *args: object) -> None:
            log_debug("control api access", format % args if args else format)

        def _agent_manifest(self, path: str) -> dict[str, object]:
            agent_id = _agent_id_from_path(path, suffix="/manifest")
            manifest = _control_server(self.server).client().native.access_manifest(agent_id)
            return {"agent_id": agent_id, "manifest": manifest}

        def _apply_manifest_policy(self, path: str, body: dict[str, Any]) -> None:
            agent_id = _agent_id_from_path(path, suffix="/manifest/policies")
            policy = str(body.get("policy", ""))
            if policy not in {"always", "never", "ask"}:
                raise ValueError("policy must be one of: always, never, ask")
            _control_server(self.server).client().native.apply_access_policy(
                agent_id,
                kind=str(body.get("kind", "")),
                subject=str(body.get("subject", "")),
                method=str(body.get("method", "")),
                path=str(body.get("path", "")),
                policy=policy,
            )
            manifest = _control_server(self.server).client().native.access_manifest(agent_id)
            self._send_json({"agent_id": agent_id, "manifest": manifest})

        def _stop_agent(self, path: str) -> None:
            agent_id = _agent_id_from_path(path, suffix="/stop")
            self._send_json(stop_agent(agent_id, _control_server(self.server).client()))

        def _delete_agent(self, path: str) -> None:
            agent_id = _agent_id_from_path(path, suffix="/delete")
            self._send_json(delete_agent(agent_id, _control_server(self.server).client()))

        def _evict_models(self, body: dict[str, Any]) -> None:
            client = _control_server(self.server).client()
            names: list[str]
            if body.get("all"):
                models = client.status_snapshot().get("models", [])
                names = [str(item["name"]) for item in models if isinstance(item, dict) and item.get("name")]
            else:
                name = str(body.get("name") or body.get("model_name") or "")
                if not name:
                    raise ValueError("name is required unless all=true")
                names = [name]
            for name in names:
                client.evict_model(name)
            self._send_json({"evicted": names})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length", "0"))
            if length <= 0:
                raise ValueError("request body is required")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("request body must be a JSON object")
            return data

        def _send_static(self, name: str, content_type: str) -> None:
            text = _static_asset(name).read_text(encoding="utf-8")
            self._send_text(text, content_type)

        def _send_icon(self, name: str) -> None:
            allowed = {"bubblehub-icon.svg", "nvidia.svg", "qwen.svg", "mistral.svg"}
            if name not in allowed:
                self._send_error(HTTPStatus.NOT_FOUND, f"unknown icon: {name}")
                return
            data = _icon_asset(name).read_bytes()
            self.send_response(HTTPStatus.OK)
            content_type = "image/svg+xml" if name.endswith(".svg") else "image/png"
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.send_header("access-control-allow-origin", "http://127.0.0.1")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_text(self, payload: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json(
                {
                    "error": {
                        "message": message,
                        "type": status.phrase.lower().replace(" ", "_"),
                        "code": status.value,
                    }
                },
                status=status,
            )

    return BubbleHubControlHandler


def _agent_id_from_path(path: str, *, suffix: str) -> str:
    prefix = "/api/agents/"
    if not path.startswith(prefix) or not path.endswith(suffix):
        raise ValueError("invalid agent manifest path")
    agent_id = unquote(path[len(prefix) : -len(suffix)]).strip("/")
    if not agent_id:
        raise ValueError("agent id is required")
    return agent_id


def _control_server(server: object) -> BubbleHubControlServer:
    if not isinstance(server, BubbleHubControlServer):
        raise RuntimeError("unexpected server type")
    return server


def _static_asset(name: str) -> Path:
    candidates = []
    configured = os.environ.get("BUBBLEHUB_APP_STATIC_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            root / "app" / "static",
            Path(sys.prefix) / "share" / "bubblehub" / "app" / "static",
            Path("/usr/share/bubblehub/app/static"),
        ]
    )
    for directory in candidates:
        path = directory / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"BubbleHub app static asset not found: {name}")


def _icon_asset(name: str) -> Path:
    candidates = []
    configured = os.environ.get("BUBBLEHUB_APP_ICON_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            root / "app" / "icons",
            root / "app" / "icons" / "models",
            Path(sys.prefix) / "share" / "bubblehub" / "app" / "icons",
            Path(sys.prefix) / "share" / "bubblehub" / "app" / "icons" / "models",
            Path("/usr/share/bubblehub/app/icons"),
            Path("/usr/share/bubblehub/app/icons/models"),
        ]
    )
    for directory in candidates:
        path = directory / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"BubbleHub app icon not found: {name}")
