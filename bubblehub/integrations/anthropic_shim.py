from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bubblehub.engine.session import EngineSession


@dataclass
class BubbleHubAnthropicMessage:
    content: list[dict[str, str]]


class _Messages:
    def __init__(self, specialty: str, niceness: int) -> None:
        self.specialty = specialty
        self.niceness = niceness

    def create(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        **kwargs: Any,
    ) -> BubbleHubAnthropicMessage:
        del model, kwargs
        converted: list[dict[str, str]] = []
        if system:
            converted.append({"role": "system", "content": system})
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, list):
                text = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
            else:
                text = str(content)
            converted.append({"role": str(message.get("role", "user")), "content": text})
        with EngineSession(self.specialty, niceness=self.niceness) as session:
            answer = session.chat(converted)
        return BubbleHubAnthropicMessage(content=[{"type": "text", "text": answer}])


class BubbleHubAnthropic:
    def __init__(self, speciality: str | None = None, specialty: str | None = None, niceness: int = 0) -> None:
        self.specialty = specialty or speciality or "default-instruct"
        self.niceness = niceness
        self.messages = _Messages(self.specialty, self.niceness)
