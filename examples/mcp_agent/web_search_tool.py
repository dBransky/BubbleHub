#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests

DEFAULT_SEARCH_URL = "http://searx.tiekoetter.com/search"


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
                "serverInfo": {"name": "ageos-web-search", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        )
    if method == "tools/list":
        return result(
            request_id,
            {
                "tools": [
                    {
                        "name": "web_search",
                        "description": "Search the web using the open-source SearXNG metasearch API.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ]
            },
        )
    if method == "tools/call":
        params = request.get("params", {})
        if params.get("name") != "web_search":
            return error(request_id, -32601, "unknown tool")
        arguments = params.get("arguments", {})
        query = str(arguments.get("query", "")).strip()
        return result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": search_web(query),
                    }
                ]
            },
        )
    return error(request_id, -32601, "unknown method")


def search_web(query: str) -> str:
    if not query:
        return "search_error=query is required"
    search_url = os.environ.get("AGEOS_MCP_SEARCH_URL", DEFAULT_SEARCH_URL)
    try:
        response = requests.get(
            search_url,
            params={"q": query, "format": "json"},
            timeout=5,
        )
    except requests.RequestException as exc:
        return f"search_error={exc.__class__.__name__}: {exc}"
    text = response.text.strip().replace("\n", " ")[:200]
    return f"search_status={response.status_code} url={response.url} body={text}"


def result(request_id: object, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
