"""Local LLM backend: one chat session with an Ollama model and the house tools.

The history is plain chat messages after the system message. A failed turn is
truncated away so the next request never sees half a turn, and a session that
is idle too long or at its turn cap restarts with only the system prompt. A
callable system prompt is read when a session's first turn begins (not when the
brain is built, maybe hours before) and kept for the rest of that session, so
the prompt never changes mid-session. A rolled-back turn that saved or deleted
a note ends its session: the note would be missing from both that session's
prompt and its history.

A call held for Master's yes (`confirmations`) ends the reply with the gate's
question, kept in the history like the rest of what she said; a rolled-back turn
drops its request instead, since Master never hears its question.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx
import ollama

from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.memory_tools import changed_memory
from waifu.brain.persona import format_user_turn
from waifu.brain.session import SessionClock
from waifu.brain.tools import ToolProvider, build_tool_specs, held_for_yes, run_tool, to_ollama
from waifu.config import AssistantConfig, BrainConfig
from waifu.devices.registry import DeviceRegistry

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_ASSISTANT_DEFAULTS = AssistantConfig()
# ollama.Client waits forever by default; the voice loop is deaf while a request is open.
# The read limit still leaves room for a cold model load from disk.
_REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

_DONE = "Sudah, Master!"
_SOME_FAILED = "Maaf, Master, ada yang gagal."
_NO_ANSWER = "Maaf, Master, aku bingung mau jawab apa. Coba ulangi ya?"
_ROUND_LIMIT = "Maaf, Master, permintaannya butuh terlalu banyak langkah, jadi aku berhenti dulu."
_OLLAMA_TROUBLE = "Maaf, Master, Ollama sedang bermasalah. Coba lagi sebentar ya."
_OLLAMA_TOO_SLOW = "Maaf, Master, Ollama terlalu lama menjawab. Coba lagi sebentar ya."
_UNEXPECTED = "Maaf, Master, ada yang salah di pikiranku. Coba ulangi ya."


class OllamaBrain(Brain):
    def __init__(
        self,
        config: BrainConfig,
        registry: DeviceRegistry,
        system_prompt: str | Callable[[], str],
        *,
        client: ollama.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_idle_minutes: float = _ASSISTANT_DEFAULTS.session_idle_minutes,
        max_session_turns: int = _ASSISTANT_DEFAULTS.max_session_turns,
        providers: Sequence[ToolProvider] = (),
        confirmations: ConfirmationGate | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._providers = tuple(providers)
        self._confirmations = confirmations
        self._system_prompt = system_prompt
        self._session_system = ""
        self._client = client
        self._session = SessionClock(session_idle_minutes, max_session_turns, clock)
        self._tools = [to_ollama(spec) for spec in build_tool_specs(registry, self._providers)]
        self._history: list[dict[str, Any]] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        gate = self._confirmations
        if gate is not None:
            # Every turn counts, even one rolled back later: only the very next one can say yes.
            gate.begin_turn(user_text)
        for provider in self._providers:
            # An optional hook for tools that need Master's own words. The screen tool, which
            # also hands over images, is never given to this brain: the local model cannot see.
            if hasattr(provider, "begin_turn"):
                provider.begin_turn(user_text)
        # A new session would hide the question this turn may answer, so it waits one turn.
        if self._session.should_reset() and not (gate is not None and gate.awaiting_answer()):
            logger.info("Session idle or at its turn cap; starting a new one")
            self.reset()
        if not self._history:
            # A new session (or a rolled-back first turn): its system prompt is fixed from here.
            source = self._system_prompt
            self._session_system = source() if callable(source) else source
        turn_start = len(self._history)
        records: list[ToolCallRecord] = []
        user_turn = format_user_turn(user_text, now or datetime.now())
        self._history.append({"role": "user", "content": user_turn})
        try:
            text = self._run_turn(records)
        except Exception as exc:
            if gate is not None:
                gate.discard_turn()
            if changed_memory(records):
                logger.info("Rolled back a turn that changed a note; starting a new session")
                self.reset()
            else:
                del self._history[turn_start:]
            return BrainReply(text=self._apology(exc), tool_calls=records)
        self._session.record_turn()
        return BrainReply(text=text, tool_calls=records)

    def reset(self) -> None:
        self._history = []
        self._session.reset()

    def _run_turn(self, records: list[ToolCallRecord]) -> str:
        text = self._chat_until_answered(records)
        if self._confirmations is not None:
            text = self._confirmations.with_question(text)
        self._history.append({"role": "assistant", "content": text})
        return text

    def _chat_until_answered(self, records: list[ToolCallRecord]) -> str:
        """Chat until the model stops calling tools; apologise if the round limit comes first."""
        rounds = 0
        while True:
            message = self._chat()
            tool_calls = list(message.tool_calls or [])
            if not tool_calls:
                return _spoken_text(message.content) or _fallback_text(records)
            if rounds >= self._config.max_tool_rounds:
                logger.warning(
                    "Model still calling tools after %d rounds; not running %s",
                    rounds,
                    [call.function.name for call in tool_calls],
                )
                return _ROUND_LIMIT
            rounds += 1
            self._run_tool_calls(message.content, tool_calls, records)

    def _chat(self) -> Any:
        if self._client is None:
            self._client = ollama.Client(host=self._config.ollama.host, timeout=_REQUEST_TIMEOUT)
        response = self._client.chat(
            model=self._config.ollama.model,
            messages=[{"role": "system", "content": self._session_system}, *self._history],
            tools=self._tools,
            think=self._config.ollama.think,
            keep_alive=self._config.ollama.keep_alive,
        )
        return response.message

    def _run_tool_calls(
        self, content: str | None, tool_calls: list[Any], records: list[ToolCallRecord]
    ) -> None:
        """Append the assistant's call message, then one tool message per call, in order."""
        calls = [
            (call.function.name, _parse_arguments(call.function.arguments)) for call in tool_calls
        ]
        self._history.append(
            {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {"function": {"name": name, "arguments": _as_dict(arguments)}}
                    for name, arguments in calls
                ],
            }
        )
        for name, arguments in calls:
            result, is_error = run_tool(
                self._registry, name, arguments, self._providers, confirmations=self._confirmations
            )
            logger.debug("Tool %s(%s) -> %s", name, arguments, result)
            records.append(ToolCallRecord(name, _as_dict(arguments), result, is_error))
            self._history.append({"role": "tool", "content": result, "tool_name": name})

    def _apology(self, exc: Exception) -> str:
        model = self._config.ollama.model
        if isinstance(exc, ConnectionError):
            logger.error("Cannot reach Ollama at %s: %s", self._config.ollama.host, exc)
            return (
                "Maaf, Master, aku tidak bisa terhubung ke Ollama. "
                f"Ollama belum jalan? Jalankan 'ollama serve' dan 'ollama pull {model}'."
            )
        if isinstance(exc, httpx.TimeoutException):
            logger.warning("Ollama at %s did not answer in time: %r", self._config.ollama.host, exc)
            return _OLLAMA_TOO_SLOW
        if isinstance(exc, ollama.ResponseError):
            if exc.status_code == 404 or "not found" in str(exc.error).lower():
                logger.error("Ollama model '%s' is not available: %s", model, exc)
                return (
                    f"Maaf, Master, model {model} belum ada di Ollama. "
                    f"Jalankan 'ollama pull {model}' dulu ya."
                )
            logger.error("Ollama rejected the request: %s", exc)
            return _OLLAMA_TROUBLE
        logger.error("Unexpected error during an Ollama turn", exc_info=exc)
        return _UNEXPECTED


def _parse_arguments(raw: object) -> object:
    """Tool arguments as a mapping when possible; some models send a JSON string instead."""
    if not isinstance(raw, str):
        return raw
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _as_dict(arguments: object) -> dict[str, Any]:
    return dict(arguments) if isinstance(arguments, Mapping) else {}


def _spoken_text(content: str | None) -> str:
    """Drop reasoning: <think> blocks, text before a stray </think>, an unclosed <think>."""
    text = _THINK_BLOCK.sub("", content or "")
    text = text.rpartition("</think>")[2]
    text = text.partition("<think>")[0]
    return text.strip()


def _fallback_text(records: list[ToolCallRecord]) -> str:
    """What to say when the model's final reply is empty, based on what the tools did.

    Calls held for Master's yes are left out, and their message (meant for the model) is
    never spoken: the gate's question follows, and is the whole reply when every call was held.
    """
    if not records:
        return _NO_ANSWER
    ran = [record for record in records if not held_for_yes(record)]
    if not ran:
        return ""
    failures = [json.loads(record.result).get("message", "") for record in ran if record.is_error]
    if not failures:
        return _DONE
    return " ".join([_SOME_FAILED, *filter(None, failures)])
