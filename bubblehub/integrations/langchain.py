from __future__ import annotations

from typing import Any

from bubblehub.engine.session import EngineSession

try:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
except ImportError:  # pragma: no cover - optional dependency.
    BaseChatModel = object  # type: ignore[assignment,misc]
    BaseMessage = object  # type: ignore[assignment,misc]
    HumanMessage = object  # type: ignore[assignment,misc]
    SystemMessage = object  # type: ignore[assignment,misc]
    AIMessage = object  # type: ignore[assignment,misc]
    ChatGeneration = object  # type: ignore[assignment,misc]
    ChatResult = object  # type: ignore[assignment,misc]


class BubbleHubChatModel(BaseChatModel):  # type: ignore[misc]
    specialty: str = "default-instruct"
    niceness: int = 0

    def __init__(self, speciality: str | None = None, specialty: str | None = None, niceness: int = 0, **kwargs: Any) -> None:
        if BaseChatModel is object:
            raise ImportError("install bubblehub[langchain] to use BubbleHubChatModel")
        super().__init__(**kwargs)
        self.specialty = specialty or speciality or "default-instruct"
        self.niceness = niceness

    @property
    def _llm_type(self) -> str:
        return "bubblehub"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        del stop, kwargs
        converted = [_convert_message(message) for message in messages]
        with EngineSession(self.specialty, niceness=self.niceness) as session:
            answer = session.chat(converted)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])


def _convert_message(message: BaseMessage) -> dict[str, str]:
    if isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, HumanMessage):
        role = "user"
    else:
        role = "assistant"
    return {"role": role, "content": str(message.content)}
