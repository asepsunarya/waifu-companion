"""Claude backend: one append-only conversation per session and a manual tool loop.

Thinking blocks are bound to the exact conversation prefix (preserved thinking),
so within a session the history only grows: every assistant turn is echoed with
its full content, earlier messages are never edited, reordered, or trimmed, and
the system prompt and tools stay frozen. A session ends by starting a new, empty
history. A failed turn is rolled back by truncating to where it began, which
restores the exact earlier prefix.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import anthropic
from anthropic.types.beta import (
    BetaContentBlock,
    BetaMessage,
    BetaToolResultBlockParam,
    BetaToolUseBlock,
)

from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.brain.persona import format_user_turn
from waifu.brain.session import SessionClock
from waifu.brain.tools import build_tool_specs, run_tool, to_anthropic
from waifu.config import AssistantConfig, BrainConfig, ClaudeConfig
from waifu.devices.registry import DeviceRegistry

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A declining model's reasoning and tool calls before the hand-over are not echoed back.
_DROPPED_BEFORE_FALLBACK = frozenset({"thinking", "redacted_thinking", "tool_use"})
_SESSION_DEFAULTS = AssistantConfig()
# The voice loop is deaf while a request is open, so bound it far below the SDK's
# 10-minute default; a timeout becomes the "cannot connect" apology.
_REQUEST_TIMEOUT = anthropic.Timeout(60.0, connect=5.0)
_MAX_RETRIES = 1

_DONE = "Sudah, Master."
_TOOLS_FAILED = "Maaf Master, ada perintah yang gagal dijalankan."
_NO_ANSWER = "Maaf Master, aku belum bisa menjawab itu."
_CUT_OFF = "Maaf Master, jawabanku terpotong. Coba ulangi, ya."
_ROUND_LIMIT = "Maaf Master, permintaannya butuh terlalu banyak langkah, jadi aku berhenti dulu."
_REFUSED = "Maaf Master, aku tidak bisa membantu yang itu."
_BAD_CREDENTIALS = "Maaf Master, API key Claude belum diatur atau tidak valid."
_RATE_LIMITED = "Maaf Master, server Claude sedang sibuk. Coba lagi sebentar lagi, ya."
_NO_CONNECTION = "Maaf Master, aku tidak bisa terhubung ke server Claude."
_SERVER_ERROR = "Maaf Master, server Claude sedang bermasalah. Coba lagi nanti, ya."
_REQUEST_FAILED = "Maaf Master, ada masalah saat menghubungi Claude."
_NEEDS_WORKSPACE = (
    "Maaf Master, API key Claude ini belum terikat ke workspace. Isi ANTHROPIC_WORKSPACE_ID "
    "dengan ID workspace-nya, atau buat API key di dalam sebuah workspace."
)
# Organization-level API keys must name a workspace on every request; the SDK only
# reads ANTHROPIC_WORKSPACE_ID for federated logins, so the header is added here.
WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"
_WORKSPACE_HEADER = "anthropic-workspace-id"
_NOT_RUN_ROUND_LIMIT = "Tidak dijalankan: batas langkah untuk satu permintaan sudah tercapai."
_NOT_RUN_CUT_OFF = "Tidak dijalankan: jawaban terpotong sebelum perintahnya lengkap."


class ClaudeBrain(Brain):
    def __init__(
        self,
        config: BrainConfig,
        registry: DeviceRegistry,
        system_prompt: str,
        *,
        client: anthropic.Anthropic | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_idle_minutes: float = _SESSION_DEFAULTS.session_idle_minutes,
        max_session_turns: int = _SESSION_DEFAULTS.max_session_turns,
    ) -> None:
        self._max_tool_rounds = config.max_tool_rounds
        self._registry = registry
        self._client = client
        self._session = SessionClock(session_idle_minutes, max_session_turns, clock)
        self._request = _frozen_request(config.claude, system_prompt, registry)
        self._history: list[dict[str, Any]] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        if self._session.should_reset():
            logger.info("Claude session idle too long or at its turn cap; starting a new one")
            self.reset()
        turn_start = len(self._history)
        user_turn = format_user_turn(user_text, now or datetime.now())
        self._history.append({"role": "user", "content": user_turn})
        tool_calls: list[ToolCallRecord] = []
        try:
            text = self._run_turn(tool_calls)
        except (anthropic.AuthenticationError, anthropic.CredentialsError) as exc:
            logger.error("Claude credentials missing or rejected (%s)", type(exc).__name__)
            return self._roll_back(turn_start, _BAD_CREDENTIALS, tool_calls)
        except anthropic.RateLimitError as exc:
            logger.warning("Claude rate limit reached (request id %s)", exc.request_id)
            return self._roll_back(turn_start, _RATE_LIMITED, tool_calls)
        except anthropic.APIConnectionError as exc:
            logger.warning("Could not reach Claude: %s", exc.message)
            return self._roll_back(turn_start, _NO_CONNECTION, tool_calls)
        except anthropic.APIStatusError as exc:
            logger.error(
                "Claude returned HTTP %s (request id %s): %s",
                exc.status_code,
                exc.request_id,
                exc.message,
            )
            if exc.status_code >= 500:
                apology = _SERVER_ERROR
            elif _WORKSPACE_HEADER in str(exc.message):
                apology = _NEEDS_WORKSPACE
            else:
                apology = _REQUEST_FAILED
            return self._roll_back(turn_start, apology, tool_calls)
        except Exception:
            logger.exception("Unexpected error during a Claude turn")
            return self._roll_back(turn_start, _REQUEST_FAILED, tool_calls)
        if text is None:
            return self._roll_back(turn_start, _REFUSED, tool_calls)
        self._session.record_turn()
        return BrainReply(text, tool_calls)

    def reset(self) -> None:
        self._history = []
        self._session.reset()

    def _run_turn(self, tool_calls: list[ToolCallRecord]) -> str | None:
        """Call Claude until it stops asking for tools. Returns the reply, or None on a refusal."""
        client = self._client_or_create()
        rounds = 0
        while True:
            response = client.beta.messages.create(**self._request, messages=self._history)
            _log_cache_use(response)
            if response.stop_reason == "refusal":
                _log_refusal(response)
                return None
            content = _echoable(response.content)
            self._history.append({"role": "assistant", "content": content})
            calls = [block for block in content if block.type == "tool_use"]
            wants_more = response.stop_reason == "pause_turn" or (
                response.stop_reason == "tool_use" and bool(calls)
            )
            if not wants_more:
                if calls:
                    logger.warning("Claude stopped (%s) mid tool call", response.stop_reason)
                    self._answer_without_running(calls, _NOT_RUN_CUT_OFF)
                    return _CUT_OFF
                return _final_text(content, tool_calls)
            if rounds >= self._max_tool_rounds:
                logger.warning("Claude hit the limit of %d tool rounds", self._max_tool_rounds)
                self._answer_without_running(calls, _NOT_RUN_ROUND_LIMIT)
                return _ROUND_LIMIT
            rounds += 1
            if calls:
                results = [self._run_tool(call, tool_calls) for call in calls]
                self._history.append({"role": "user", "content": results})

    def _run_tool(
        self, call: BetaToolUseBlock, tool_calls: list[ToolCallRecord]
    ) -> BetaToolResultBlockParam:
        result, is_error = run_tool(self._registry, call.name, call.input)
        logger.info("Tool %s %s -> %s", call.name, call.input, "error" if is_error else "ok")
        arguments = dict(call.input) if isinstance(call.input, Mapping) else {}
        tool_calls.append(ToolCallRecord(call.name, arguments, result, is_error))
        return _tool_result(call.id, result, is_error)

    def _answer_without_running(self, calls: list[BetaToolUseBlock], reason: str) -> None:
        """Close unrun tool calls with error results so no tool_use is left unanswered."""
        if not calls:
            return
        payload = json.dumps({"ok": False, "message": reason}, ensure_ascii=False)
        results = [_tool_result(call.id, payload, True) for call in calls]
        self._history.append({"role": "user", "content": results})

    def _roll_back(
        self, turn_start: int, apology: str, tool_calls: list[ToolCallRecord]
    ) -> BrainReply:
        del self._history[turn_start:]
        return BrainReply(apology, tool_calls)

    def _client_or_create(self) -> anthropic.Anthropic:
        """The injected or cached client; a new one is kept only once it has credentials."""
        if self._client is None:
            workspace_id = os.environ.get(WORKSPACE_ENV, "").strip()
            headers = {_WORKSPACE_HEADER: workspace_id} if workspace_id else None
            client = anthropic.Anthropic(
                timeout=_REQUEST_TIMEOUT, max_retries=_MAX_RETRIES, default_headers=headers
            )
            if client.api_key is None and client.auth_token is None and client.credentials is None:
                # The SDK would only fail later with a bare TypeError while building the request.
                raise anthropic.CredentialsError("no Claude API key, auth token, or profile found")
            self._client = client
        return self._client


def _frozen_request(
    claude: ClaudeConfig, system_prompt: str, registry: DeviceRegistry
) -> dict[str, Any]:
    """Every request field except `messages`, fixed for the brain's whole lifetime.

    No `thinking` (adaptive by omission; disabling it is rejected on this model),
    no sampling parameters, and no forced `tool_choice`.
    """
    request: dict[str, Any] = {
        "model": claude.model,
        "max_tokens": claude.max_tokens,
        "system": system_prompt,
        "tools": [to_anthropic(spec) for spec in build_tool_specs(registry)],
        "output_config": {"effort": claude.effort},
        "cache_control": {"type": "ephemeral"},
    }
    if claude.fallbacks:
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"
    return request


def _echoable(content: Sequence[BetaContentBlock]) -> list[BetaContentBlock]:
    """The response blocks to append to history, applying the fallback echo rule."""
    fallback_at = max(
        (index for index, block in enumerate(content) if block.type == "fallback"), default=None
    )
    if fallback_at is None:
        return list(content)
    return [
        block
        for index, block in enumerate(content)
        if index >= fallback_at or block.type not in _DROPPED_BEFORE_FALLBACK
    ]


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> BetaToolResultBlockParam:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _final_text(content: Sequence[BetaContentBlock], tool_calls: list[ToolCallRecord]) -> str:
    parts = [block.text.strip() for block in content if block.type == "text"]
    text = " ".join(part for part in parts if part)
    if text:
        return text
    if any(call.is_error for call in tool_calls):
        return _TOOLS_FAILED
    return _DONE if tool_calls else _NO_ANSWER


def _log_cache_use(response: BetaMessage) -> None:
    cache_read = getattr(getattr(response, "usage", None), "cache_read_input_tokens", None)
    if cache_read is not None:
        logger.debug("Claude read %d input tokens from the prompt cache", cache_read)


def _log_refusal(response: BetaMessage) -> None:
    details = response.stop_details
    category = details.category if details is not None else None
    logger.warning("Claude declined the turn (category %s); rolling it back", category)
