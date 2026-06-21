from types import SimpleNamespace
from unittest.mock import patch

from ageos.integrations.openai_shim import AgeosOpenAI


def test_openai_shim_uses_native_session_even_when_api_base_is_set(monkeypatch) -> None:
    monkeypatch.setenv("AGEOS_API_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")

    with patch("ageos.integrations.openai_shim.EngineSession") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.chat.side_effect = ["first", "second"]
        client = AgeosOpenAI(speciality="default-instruct")
        first = client.chat.completions.create(model="ageos-local", messages=[{"role": "user", "content": "one"}])
        second = client.chat.completions.create(model="ageos-local", messages=[{"role": "user", "content": "two"}])

    assert session_cls.call_count == 2
    assert first.choices[0].message.content == "first"
    assert second.choices[0].message.content == "second"


def test_openai_shim_passes_max_tokens_to_native_session(monkeypatch) -> None:
    monkeypatch.setenv("AGEOS_API_BASE_URL", "http://127.0.0.1:8000")

    with patch("ageos.integrations.openai_shim.EngineSession") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "native"
        response = AgeosOpenAI(speciality="default-instruct", niceness=3).chat.completions.create(
            model="ageos-local",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=32,
        )

    session_cls.assert_called_once_with("default-instruct", niceness=3)
    session.chat.assert_called_once_with([{"role": "user", "content": "hi"}], max_tokens=32)
    assert response.choices[0].message.content == "native"


def test_openai_shim_forwards_to_sandbox_endpoint(monkeypatch) -> None:
    import ageos.engine.session as session_module

    calls: list[dict[str, object]] = []

    def post(url: str, *, json: dict[str, object], timeout: float) -> FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse({"choices": [{"message": {"content": "sandbox"}}]})

    monkeypatch.setenv("AGEOS_SANDBOX", "1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_HOST", "127.0.0.1")
    monkeypatch.setenv("AGEOS_SANDBOX_INFERENCE_PORT", "8123")
    monkeypatch.setattr(session_module.requests, "post", post)
    monkeypatch.setattr(
        session_module,
        "_local_scheduler_client",
        lambda: (_ for _ in ()).throw(AssertionError("sandbox sessions must not initialize the native scheduler")),
    )

    response = AgeosOpenAI(speciality="default-instruct").chat.completions.create(
        model="ageos-local",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=16,
    )

    assert response.choices[0].message.content == "sandbox"
    assert calls == [
        {
            "url": "http://127.0.0.1:8123/v1/chat/completions",
            "json": {
                "model": "default-instruct",
                "ageos_specialty": "default-instruct",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
                "stream": False,
            },
            "timeout": session_module.SANDBOX_INFERENCE_TIMEOUT_SECONDS,
        }
    ]


def test_openai_shim_ignores_openai_base_url(monkeypatch) -> None:
    monkeypatch.delenv("AGEOS_API_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")

    with patch("ageos.integrations.openai_shim.EngineSession") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "native"
        response = AgeosOpenAI(speciality="default-instruct").chat.completions.create(
            model="ageos-local",
            messages=[{"role": "user", "content": "hi"}],
        )

    session_cls.assert_called_once_with("default-instruct", niceness=0)
    assert response.choices[0].message.content == "native"


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


def test_openai_shim_defaults_to_configured_speciality(monkeypatch) -> None:
    monkeypatch.delenv("AGEOS_API_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with (
        patch(
            "ageos.integrations.openai_shim.load_inference_config",
            return_value=SimpleNamespace(default_specialty="default-instruct"),
        ),
        patch("ageos.integrations.openai_shim.EngineSession") as session_cls,
    ):
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "direct"

        AgeosOpenAI().chat.completions.create(
            model="ageos-local",
            messages=[{"role": "user", "content": "hi"}],
        )

    session_cls.assert_called_once_with("default-instruct", niceness=0)
