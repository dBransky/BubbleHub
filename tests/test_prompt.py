from types import SimpleNamespace
from unittest.mock import patch

from bubblehub.cli import prompt


def test_prompt_uses_engine_session_even_when_api_base_is_set(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("BUBBLEHUB_API_BASE_URL", "http://127.0.0.1:8000")

    with patch("bubblehub.cli.prompt.EngineSession") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "direct"

        prompt.command(
            speciality="default-instruct",
            structure=None,
            text="hi",
            niceness=0,
            output=None,
        )

    session_cls.assert_called_once_with("default-instruct", niceness=0)
    session.chat.assert_called_once_with([{"role": "user", "content": "hi"}])
    assert capsys.readouterr().out.strip() == "direct"


def test_prompt_defaults_to_configured_speciality(capsys) -> None:
    with (
        patch("bubblehub.cli.prompt.load_inference_config", return_value=SimpleNamespace(default_specialty="default-instruct")),
        patch("bubblehub.cli.prompt.EngineSession") as session_cls,
    ):
        session = session_cls.return_value.__enter__.return_value
        session.chat.return_value = "direct"

        prompt.command(
            speciality=None,
            structure=None,
            text="hi",
            niceness=0,
            output=None,
        )

    session_cls.assert_called_once_with("default-instruct", niceness=0)
    assert capsys.readouterr().out.strip() == "direct"
