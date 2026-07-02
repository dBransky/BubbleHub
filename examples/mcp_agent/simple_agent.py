#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal BubbleHub MCP example agent.")
    parser.add_argument("--query", default="bubblehub runtime", help="Search query to send to the MCP tool.")
    parser.add_argument("--http-url", help="HTTP MCP endpoint to call instead of the stdio tool.")
    parser.add_argument(
        "--direct-url",
        help="Fetch a URL directly from the agent process (internet mode without MCP).",
    )
    parser.add_argument(
        "--addition",
        action="store_true",
        help="Call the local addition stdio MCP server (no network).",
    )
    parser.add_argument("--a", type=int, default=123, help="Left operand for --addition.")
    parser.add_argument("--b", type=int, default=456, help="Right operand for --addition.")
    args = parser.parse_args()

    mode_count = sum(bool(flag) for flag in (args.http_url, args.direct_url, args.addition))
    if mode_count > 1:
        parser.error("choose only one of --http-url, --direct-url, or --addition")

    if args.direct_url:
        text = call_direct_http(args.direct_url)
        print(f"direct_http_result: {text}")
    elif args.http_url:
        text = call_http_mcp(args.http_url, args.query)
        print(f"mcp_http_result: {text}")
    elif args.addition:
        text = call_addition_tool(args.a, args.b)
        print(f"mcp_addition_result: {text}")
    else:
        text = call_stdio_tool(args.query)
        print(f"mcp_stdio_result: {text}")

    if os.environ.get("BUBBLEHUB_EXPECT_PROXY_DENY") == "1" and "403" not in text:
        print(f"expected proxy denial, got: {text}", file=sys.stderr)
        return 2
    return 0


def call_direct_http(url: str) -> str:
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        url,
        timeout=5,
        proxies=proxy_config(),
    )
    return f"status={response.status_code} body={response.text.strip()}"


def call_stdio_tool(query: str) -> str:
    tool_path = Path(__file__).with_name("web_search_tool.py")
    return call_stdio_mcp(
        tool_path,
        "web_search",
        {"query": query},
    )


def call_addition_tool(a: int, b: int) -> str:
    tool_path = Path(__file__).with_name("addition.py")
    return call_stdio_mcp(
        tool_path,
        "add",
        {"a": a, "b": b},
    )


def call_stdio_mcp(tool_path: Path, tool_name: str, arguments: dict[str, Any]) -> str:
    process = subprocess.Popen(
        [sys.executable, str(tool_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        send_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "bubblehub-simple-agent", "version": "0.1.0"},
                },
            },
        )
        read_message(process)
        send_message(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send_message(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        read_message(process)
        send_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        response = read_message(process)
        return format_tool_result(response.get("result", {}))
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def format_tool_result(result: dict[str, Any]) -> str:
    content = result.get("content", [])
    if content and isinstance(content[0], dict):
        text = content[0].get("text")
        if text is not None:
            return str(text)
        return str(content[0])
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "result" in structured:
        return str(structured["result"])
    return json.dumps(result, sort_keys=True)


def call_http_mcp(url: str, query: str) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "web_search", "arguments": {"query": query}},
    }
    session = requests.Session()
    session.trust_env = False
    response = session.post(
        url,
        json=payload,
        timeout=5,
        proxies=proxy_config(),
    )
    return f"status={response.status_code} body={response.text.strip()}"


def proxy_config() -> dict[str, str] | None:
    proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if not proxy_url:
        port = os.environ.get("BUBBLEHUB_HTTP_PROXY_PORT")
        if port:
            proxy_url = f"http://127.0.0.1:{port}"
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def send_message(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise RuntimeError("MCP tool stdin is closed")
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def read_message(process: subprocess.Popen[str]) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("MCP tool stdout is closed")
    while True:
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"MCP tool exited without a response: {stderr}")
        data = json.loads(line)
        if "method" in data and "id" not in data:
            continue
        if "error" in data:
            raise RuntimeError(json.dumps(data["error"], sort_keys=True))
        return data


if __name__ == "__main__":
    raise SystemExit(main())
