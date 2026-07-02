from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bubblehub.engine.session import EngineSession
from bubblehub.inference import load_inference_config


@dataclass
class _Message:
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class BubbleHubChatCompletion:
    choices: list[_Choice]


class _Completions:
    def __init__(self, specialty: str, niceness: int) -> None:
        self.specialty = specialty
        self.niceness = niceness

    def create(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> BubbleHubChatCompletion:
        del model
        max_tokens = _max_tokens(kwargs)
        with EngineSession(self.specialty, niceness=self.niceness) as session:
            content = session.chat(messages, max_tokens=max_tokens)
        return BubbleHubChatCompletion(choices=[_Choice(message=_Message(content=content))])


class _Chat:
    def __init__(self, specialty: str, niceness: int) -> None:
        self.completions = _Completions(specialty, niceness)


class BubbleHubOpenAI:
    """Tiny OpenAI-style client surface for chat.completions.create."""

    def __init__(self, speciality: str | None = None, specialty: str | None = None, niceness: int = 0) -> None:
        self.specialty = specialty or speciality or load_inference_config().default_specialty
        self.niceness = niceness
        self.chat = _Chat(self.specialty, self.niceness)


def _max_tokens(kwargs: dict[str, Any]) -> int | None:
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None
