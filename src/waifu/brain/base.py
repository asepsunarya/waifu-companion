"""Contract between the conversation loop and an LLM backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """Backend-neutral tool definition; each backend converts it to its own wire format."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: str
    is_error: bool


@dataclass(frozen=True)
class BrainReply:
    text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class Brain(ABC):
    """Holds one conversation session and answers a user turn, calling tools as needed."""

    @abstractmethod
    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        """Answer one user turn. Never raises for API or tool failures: returns a spoken apology."""

    @abstractmethod
    def reset(self) -> None:
        """Forget the current conversation and start a fresh session."""
