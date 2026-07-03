from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from bubblehub.http_api import (
    ApiConfig,
    _bubblehub_server,
    _content_to_text,
    _default_max_output_tokens,
    _handler_for,
    _max_output_tokens,
    _model_name,
    _normalize_role,
    _resolve_specialty_alias,
    _specialty_from_body,
    chat_completion_payload,
    create_http_server,
    embeddings_payload,
    messages_from_responses_input,
    normalize_chat_messages,
    normalize_embeddings_input,
    responses_payload,
)


def test_chat_completion_payload_is_openai_shaped() -> None:
    payload = chat_completion_payload("default-instruct", "hello", response_id="chatcmpl-test", created=123)
    assert payload["id"] == "chatcmpl-test"
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "hello"


def test_responses_input_accepts_string_and_message_list() -> None:
    assert messages_from_responses_input("hi") == [{"role": "user", "content": "hi"}]
    assert messages_from_responses_input([{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]) == [
        {"role": "user", "content": "hello"},
    ]


def test_chat_message_normalization_accepts_openclaw_history() -> None:
    assert normalize_chat_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": "[assistant turn failed before producing content]"},
            {
                "role": "assistant",
                "content": (
                    "Context overflow: prompt too large for the model. Try /reset (or /new) to start a fresh session, or use a larger-context model."
                ),
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
    assert _resolve_specialty_alias("openai/not-bubblehub", "default-instruct") == "default-instruct"


def test_chat_completions_stream_returns_sse() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("bubblehub.http_api.EngineSession") as session_cls:
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
        with patch("bubblehub.http_api.EngineSession") as session_cls:
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
        with patch("bubblehub.http_api.EngineSession") as session_cls:
            manager = session_cls.return_value
            session = manager.__enter__.return_value
            session.chat.side_effect = ["first", "second", "third"]

            for message in ["one", "two", "three"]:
                response = requests.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "model": "bubblehub-local",
                        "bubblehub_specialty": "default-instruct",
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
        with patch("bubblehub.http_api.EngineSession") as session_cls:
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
        with patch("bubblehub.http_api.EngineSession") as session_cls:
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
        with patch("bubblehub.http_api.EngineSession") as session_cls:
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


def test_run_http_api_closes_server_on_keyboard_interrupt() -> None:
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt

    with patch("bubblehub.http_api.create_http_server", return_value=server):
        from bubblehub.http_api import run_http_api

        run_http_api(ApiConfig())

    server.server_close.assert_called_once()


def test_http_server_preload_and_embeddings_delegate_to_engine_session() -> None:
    config = ApiConfig(niceness=7, status_callback=lambda message: None)
    server = create_http_server(ApiConfig(port=0, niceness=config.niceness, status_callback=config.status_callback))
    try:
        with patch("bubblehub.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.embeddings.return_value = [[0.1, 0.2]]

            server.preload()
            assert server.embeddings("code-review", ["one"]) == [[0.1, 0.2]]

        assert session_cls.call_args_list[0].args == ("default-instruct",)
        assert session_cls.call_args_list[0].kwargs["niceness"] == 7
        assert session_cls.call_args_list[1].args == ("code-review",)
        session.embeddings.assert_called_once_with(["one"])
    finally:
        server.server_close()


def test_responses_stream_and_embeddings_endpoints() -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch("bubblehub.http_api.EngineSession") as session_cls:
            session = session_cls.return_value.__enter__.return_value
            session.chat.return_value = "response text"
            session.embeddings.return_value = [[1.0, 2.0]]

            streamed = requests.post(
                f"http://127.0.0.1:{port}/v1/responses",
                json={"model": "default-instruct", "stream": True, "input": "hello", "max_output_tokens": 12},
                timeout=5,
            )
            embedded = requests.post(
                f"http://127.0.0.1:{port}/v1/embeddings",
                json={"model": "default-instruct", "input": [[1, 2], [3, 4]]},
                timeout=5,
            )

        assert streamed.status_code == 200
        assert "response.output_text.delta" in streamed.text
        assert embedded.status_code == 200
        assert embedded.json()["data"][0]["embedding"] == [1.0, 2.0]
        assert session.chat.call_args.kwargs["max_tokens"] == 12
        session.embeddings.assert_called_once_with(["1 2", "3 4"])
    finally:
        server.shutdown()
        server.server_close()


def test_http_api_bad_requests_unknown_routes_and_invalid_env(monkeypatch) -> None:
    server = create_http_server(ApiConfig(port=0))
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        missing_body = requests.post(f"http://127.0.0.1:{port}/v1/chat/completions", timeout=5)
        non_object = requests.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=["bad"], timeout=5)
        empty_messages = requests.post(f"http://127.0.0.1:{port}/v1/chat/completions", json={"messages": []}, timeout=5)
        bad_max = requests.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": "many"},
            timeout=5,
        )
        unknown_post = requests.post(f"http://127.0.0.1:{port}/v1/missing", json={}, timeout=5)
        health = requests.get(f"http://127.0.0.1:{port}/health", timeout=5)
        missing_get = requests.get(f"http://127.0.0.1:{port}/missing", timeout=5)

        monkeypatch.setenv("BUBBLEHUB_MAX_OUTPUT_TOKENS", "bad")
        bad_env = requests.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            timeout=5,
        )

    finally:
        server.shutdown()
        server.server_close()

    assert missing_body.status_code == 400
    assert non_object.status_code == 400
    assert empty_messages.json()["error"]["message"] == "messages must be a non-empty list"
    assert bad_max.json()["error"]["message"] == "max_tokens must be an integer"
    assert unknown_post.status_code == 404
    assert health.json() == {"status": "ok"}
    assert missing_get.status_code == 404
    assert bad_env.json()["error"]["message"] == "BUBBLEHUB_MAX_OUTPUT_TOKENS must be an integer"


def test_http_helper_edge_cases(monkeypatch) -> None:
    assert messages_from_responses_input([{"role": "user", "content": "hi"}]) == [{"role": "user", "content": "hi"}]
    try:
        messages_from_responses_input(123)
    except ValueError as exc:
        assert "responses input" in str(exc)
    else:
        raise AssertionError("expected responses input error")

    assert normalize_embeddings_input([["a", 2], ["b"]]) == ["a 2", "b"]
    try:
        normalize_embeddings_input([{"bad": True}])
    except ValueError as exc:
        assert "embeddings input" in str(exc)
    else:
        raise AssertionError("expected embeddings input error")

    assert _content_to_text({"output_text": "done"}) == "done"
    assert _content_to_text(["one", {"input_text": "two"}, {"text": ""}]) == "one\ntwo"
    assert _normalize_role("developer") == "system"
    assert _normalize_role("strange") == "user"
    assert _model_name({}, "default-instruct") == "default-instruct"
    assert _max_output_tokens({"max_tokens": 0}, "max_tokens") == 512
    monkeypatch.setenv("BUBBLEHUB_MAX_OUTPUT_TOKENS", "16")
    assert _default_max_output_tokens() == 16
    monkeypatch.setenv("BUBBLEHUB_MAX_OUTPUT_TOKENS", "0")
    try:
        _default_max_output_tokens()
    except ValueError as exc:
        assert "greater than zero" in str(exc)
    else:
        raise AssertionError("expected max output token error")


def test_http_handler_server_type_guard_and_specialty_alias() -> None:
    try:
        _bubblehub_server(object())
    except RuntimeError as exc:
        assert "BubbleHub HTTP handler" in str(exc)
    else:
        raise AssertionError("expected server guard error")

    registry = SimpleNamespace(specialties={"code": object(), "default-instruct": object()})
    with patch("bubblehub.http_api.ModelRegistry.load_default", return_value=registry):
        assert _specialty_from_body({"bubblehub_speciality": "code"}, "default-instruct") == "code"
        assert _specialty_from_body({"model": "openai/code"}, "default-instruct") == "code"
        assert _specialty_from_body({"model": "unknown"}, "default-instruct") == "default-instruct"

    handler = _handler_for(ApiConfig())
    assert handler.server_version == "bubblehub-http/0.1"
