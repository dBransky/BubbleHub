from bubblehub.engine.structured import build_structured_messages, parse_json_output


def test_parse_json_output_handles_fenced_json() -> None:
    assert parse_json_output('```json\n{"ok": true}\n```') == {"ok": True}


def test_build_structured_messages_mentions_json_only() -> None:
    messages = build_structured_messages({"answer": "x"}, "hello")
    assert messages[0]["role"] == "system"
    assert "Return only valid JSON" in messages[0]["content"]
    assert messages[1]["content"] == "hello"
