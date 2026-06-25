#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        response = handle_request(request)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return result(
            request_id,
            {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "ageos-addition", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        )
    if method == "tools/list":
        return result(
            request_id,
            {
                "tools": [
                    {
                        "name": "add",
                        "description": "Add two integers without network access.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                            "required": ["a", "b"],
                        },
                    }
                ]
            },
        )
    if method == "tools/call":
        params = request.get("params", {})
        if params.get("name") != "add":
            return error(request_id, -32601, "unknown tool")
        arguments = params.get("arguments", {})
        try:
            value = int(arguments.get("a", 0)) + int(arguments.get("b", 0))
        except (TypeError, ValueError):
            return error(request_id, -32602, "a and b must be integers")
        return result(
            request_id,
            {
                "content": [{"type": "text", "text": str(value)}],
                "structuredContent": {"result": value},
            },
        )
    return error(request_id, -32601, "unknown method")


def result(request_id: object, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
