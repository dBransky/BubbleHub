from ageos.http_api import (
    ApiConfig,
    chat_completion_payload,
    create_http_server,
    embeddings_payload,
    messages_from_responses_input,
    normalize_chat_messages,
    normalize_embeddings_input,
    responses_payload,
    _resolve_specialty_alias,
)
from threading import Thread
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import requests


def test_chat_completion_payload_is_openai_shaped() -> None:
    payload = chat_completion_payload("default-instruct", "hello", response_id="chatcmpl-test", created=123)
    assert payload["id"] == "chatcmpl-test"
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "hello"


def test_responses_input_accepts_string_and_message_list() -> None:
    assert messages_from_responses_input("hi") == [{"role": "user", "content": "hi"}]
    assert messages_from_responses_input(
        [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]
    ) == [{"role": "user", "content": "hello"}]


def test_chat_message_normalization_accepts_openclaw_history() -> None:
    assert normalize_chat_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": "[assistant turn failed before producing content]"},
            {
                "role": "assistant",
                "content": "Context overflow: prompt too large for the model. Try /reset (or /new) to start a fresh session, or use a larger-context model.",
            },
            {"role": "developer", "content": "follow instructions"},
            {"role": "tool", "tool_call_id": "read-1", "content": "file contents"},
        ]
    ) == [
        {"role": "system", "content": "follow instructions"},
        {"role": "user", "content": "hi\n\nTool result (read-1):\nfile contents"},
    ]


def test_chat_message_normalization_repairs_llama_role_ordering() -> None:
    assert normalize_chat_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "[assistant turn failed before producing content]"},
            {"role": "user", "content": "Wake up, my friend!"},
            {"role": "developer", "content": "be concise"},
        ]
    ) == [
        {"role": "system", "content": "be concise"},
        {"role": "user", "content": "hi\n\nWake up, my friend!"},
    ]


def test_responses_payload_exposes_output_text() -> None:
    payload = responses_payload("default-instruct", "done", response_id="resp-test", created=123)
    assert payload["id"] == "resp-test"
    assert payload["object"] == "response"
    assert payload["output_text"] == "done"


def test_embeddings_helpers_accept_openai_inputs() -> None:
    assert normalize_embeddings_input("one") == ["one"]
    assert normalize_embeddings_input(["one", "two"]) == ["one", "two"]
    assert normalize_embeddings_input([1, 2, 3]) == ["1 2 3"]

    payload = embeddings_payload("default-instruct", [[0.1, 0.2]])
    assert payload["object"] == "list"
    assert payload["data"][0]["embedding"] == [0.1, 0.2]
    assert payload["usage"]["total_tokens"] == 0


def test_openai_provider_prefix_resolves_known_specialty() -> None:
    assert _resolve_specialty_alias("openai/default-instruct", "default-instruct") == "default-instruct"
    assert _resolve_specialty_alias("openai/not-ageos", "default-instruct") == "default-instruct"


def test_chat_completions_stream_returns_sse() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("ageos.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.chat.return_value = "hello"
            response = requests.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={
                    "model": "openai/default-instruct",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=5,
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream"
        assert "chat.completion.chunk" in response.text
        assert "data: [DONE]" in response.text
    finally:
        server.shutdown()
        server.server_close()


def test_chat_completions_call_native_session_per_request() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("ageos.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.chat.side_effect = ["one", "two"]
            for _ in range(2):
                response = requests.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "model": "default-instruct",
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    timeout=5,
                )
                assert response.status_code == 200
        assert session_cls.call_count == 2
        assert session.chat.call_count == 2
    finally:
        server.shutdown()
        server.server_close()


def test_chat_completions_leave_cache_ownership_to_native_session() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("ageos.http_api.EngineSession") as session_cls:
            manager = session_cls.return_value
            session = manager.__enter__.return_value
            session.chat.side_effect = ["first", "second", "third"]

            for message in ["one", "two", "three"]:
                response = requests.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "model": "ageos-local",
                        "ageos_specialty": "default-instruct",
                        "messages": [{"role": "user", "content": message}],
                    },
                    timeout=5,
                )
                assert response.status_code == 200

        assert session_cls.call_count == 3
        session_cls.assert_called_with("default-instruct", niceness=0, status_callback=None)
        assert manager.__enter__.call_count == 3
        assert session.chat.call_count == 3
    finally:
        server.shutdown()
        server.server_close()


def test_chat_completions_returns_gateway_error_for_native_session_failure() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("ageos.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.chat.side_effect = requests.ConnectionError("dead backend")
            response = requests.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={
                    "model": "default-instruct",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=5,
            )

        assert response.status_code == 502
        assert session_cls.call_count == 1
    finally:
        server.shutdown()
        server.server_close()


def test_chat_completions_defaults_max_tokens() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("ageos.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.chat.return_value = "hello"
            response = requests.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={"model": "default-instruct", "messages": [{"role": "user", "content": "hi"}]},
                timeout=5,
            )
        assert response.status_code == 200
        assert session.chat.call_args.kwargs["max_tokens"] == 512
    finally:
        server.shutdown()
        server.server_close()


def test_chat_completions_preserves_client_max_tokens() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("ageos.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.chat.return_value = "hello"
            response = requests.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                json={
                    "model": "default-instruct",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 32,
                },
                timeout=5,
            )
        assert response.status_code == 200
        assert session.chat.call_args.kwargs["max_tokens"] == 32
    finally:
        server.shutdown()
        server.server_close()
