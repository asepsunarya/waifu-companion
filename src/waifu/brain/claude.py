"""Claude backend: one append-only conversation per session and a manual tool loop.

Thinking blocks are bound to the exact conversation prefix (preserved thinking),
so within a session the history only grows: every assistant turn is echoed with
its full content, earlier messages are never edited, reordered, or trimmed, and
the system prompt and tools stay frozen. A session ends by starting a new, empty
history. A failed turn is rolled back by truncating to where it began, which
restores the exact earlier prefix.

The tools are fixed for the brain's lifetime. A callable system prompt is read
once when a session starts (a turn begins with an empty history) and kept for
the rest of that session, so what it returns can change between sessions only.
A rolled-back turn that saved or deleted a note ends its session instead: the
note would be missing from both that session's prompt and its history. One that
recorded or undid an expense in tepira says so after its apology, since tepira
keeps it.

A call held for Master's yes (`confirmations`) is answered like any failed tool
call, so the gate never touches the system prompt, the tools, or earlier turns.
The gate's question is added to the spoken reply only, never to the history; a
rolled-back turn drops its request instead, since Master never hears its question.

Providers may have two optional hooks: `begin_turn(user_text)` hears Master's
words at the start of every turn, and `take_images()` hands over images (the
screenshot Master asked for) that go back in that call's tool_result, after its
JSON. The history keeps that tool_result exactly as it was sent.

`turn_images` holds the photo Master sent with the turn's words (the phone
camera). The turn takes it right after the providers' `begin_turn` and sends it
in Master's own message: the image, a note saying it is from Master's phone
camera, then the words. Without a photo Master's message stays the plain string,
so those requests are exactly what they were before photos and the prompt cache
stays warm. The history keeps the message as sent; a rolled-back turn drops it
with the rest. Every request resends the session's images, screenshots and photos
alike, so a session holding 10 MiB of them ends before the next turn, like an idle
one, and one holding 20 MiB ends even while a question awaits Master's yes.

`on_usage` (the API cost monitor's ledger) hears the model and the tokens of every
response, a refused or cut-off one too, since each is billed; after a server-side
fallback, once for each model attempt, the declined one too. It only observes: the
requests stay exactly as they are, and a failure to record is logged, never the turn's.

`web_search` (from `web_search_tool`) is Claude's server-side web search, the last of the
frozen tools when given; without it the requests are exactly what they were before. With it
every request is streamed (the same request with `stream`), since a search turn may outlast
the read timeout that a stream applies between its events. It runs on Anthropic's servers:
its blocks (`server_tool_use`, `web_search_tool_result`, a
search error too) stay in the history exactly as returned, and a server-side loop that
paused (`pause_turn`, its last block a `server_tool_use`) is resumed by resending the
history as it stands, with no message added, as one tool round. A turn that would end with
a server tool call still unanswered (paused at the round limit, or cut off mid-search) is
rolled back: the API refuses such a call in every later request unless it ends the history.
The response's searches reach `on_usage` with its tokens.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import os
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import datetime
from itertools import groupby
from typing import Any

import anthropic
import httpx2
from anthropic.types.beta import (
    BetaContentBlock,
    BetaImageBlockParam,
    BetaMessage,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
    BetaToolUseBlock,
    BetaUserLocationParam,
    BetaWebSearchTool20250305Param,
    BetaWebSearchTool20260209Param,
)

from waifu.brain.attachments import TurnImages
from waifu.brain.base import Brain, BrainReply, ToolCallRecord, ToolImage
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.finance_tools import finance_changes
from waifu.brain.memory_tools import changed_memory
from waifu.brain.persona import format_user_turn
from waifu.brain.session import SessionClock
from waifu.brain.tools import (
    ToolProvider,
    build_tool_specs,
    held_for_yes,
    logged_arguments,
    run_tool,
    to_anthropic,
)
from waifu.config import AssistantConfig, BrainConfig, ClaudeConfig
from waifu.devices.registry import DeviceRegistry
from waifu.usage import TokenUsage

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# The models whose web search filters its results with code (`web_search_20260209`), from the
# claude-api reference; the others search with `web_search_20250305`.
DYNAMIC_FILTERING_MODELS = frozenset(
    {
        "claude-fable-5-1",
        "claude-fable-5",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
    }
)
WebSearchTool = BetaWebSearchTool20260209Param | BetaWebSearchTool20250305Param
# A declining model's reasoning and tool calls before the hand-over are not echoed back, nor
# its server tool calls that got no result.
_DROPPED_BEFORE_FALLBACK = frozenset({"thinking", "redacted_thinking", "tool_use"})
# The `usage.iterations` entries of the model attempts behind one response (her requests use
# neither compaction nor the advisor tool, the other kinds).
_ATTEMPT_ITERATIONS = frozenset({"message", "fallback_message"})
_SESSION_DEFAULTS = AssistantConfig()
# The voice loop is deaf while a request is open, so bound it far below the SDK's
# 10-minute default; a timeout before the answer starts becomes the "cannot connect" apology.
# With web search the requests are streamed, so the read timeout bounds each wait between the
# stream's events instead of the server's whole search loop.
_REQUEST_TIMEOUT = anthropic.Timeout(60.0, connect=5.0)
_MAX_RETRIES = 1
# Every request resends the session's images, screenshots and Master's photos alike, and the
# API refuses a request over 32 MB (taken as 32,000,000 bytes). One turn adds at most one photo
# (the phone server takes 3.5 MiB at most: 4,893,356 bytes as base64) and one screenshot (5 MiB
# as base64 at most), 9.67 MiB together. A session holding _SESSION_IMAGE_BYTES of base64 image
# data ends before the next turn, so the turn that crosses it ends under 10 + 9.67 MiB. A
# question awaiting Master's yes keeps the session one turn more, which may add as much again:
# 10 + 2 x 9.67 MiB = 30.76 MB, plus the text. That turn may ask a question of its own, and so
# on, so a session holding _SESSION_IMAGE_CAP ends even while a question awaits (its yes then
# runs nothing): its last turn stays under 20 + 9.67 MiB = 31.1 MB.
_SESSION_IMAGE_BYTES = 10 * 2**20
_SESSION_IMAGE_CAP = 20 * 2**20

# Her canned replies; `{owner}` is filled with what she calls the owner (owner_call).
_DONE = "Sudah, {owner}."
_TOOLS_FAILED = "Maaf {owner}, ada perintah yang gagal dijalankan."
_NO_ANSWER = "Maaf {owner}, aku belum bisa menjawab itu."
_CUT_OFF = "Maaf {owner}, jawabanku terpotong. Coba ulangi, ya."
_ROUND_LIMIT = "Maaf {owner}, permintaannya butuh terlalu banyak langkah, jadi aku berhenti dulu."
_REFUSED = "Maaf {owner}, aku tidak bisa membantu yang itu."
_BAD_CREDENTIALS = "Maaf {owner}, API key Claude belum diatur atau tidak valid."
_RATE_LIMITED = "Maaf {owner}, server Claude sedang sibuk. Coba lagi sebentar lagi, ya."
_NO_CONNECTION = "Maaf {owner}, aku tidak bisa terhubung ke server Claude."
_SERVER_ERROR = "Maaf {owner}, server Claude sedang bermasalah. Coba lagi nanti, ya."
_REQUEST_FAILED = "Maaf {owner}, ada masalah saat menghubungi Claude."
_TOO_SLOW = "Maaf {owner}, jawaban dari Claude terlalu lama, jadi aku berhenti dulu."
_NEEDS_WORKSPACE = (
    "Maaf {owner}, API key Claude ini belum terikat ke workspace. Isi ANTHROPIC_WORKSPACE_ID "
    "dengan ID workspace-nya, atau buat API key di dalam sebuah workspace."
)
# Organization-level API keys must name a workspace on every request; the SDK only
# reads ANTHROPIC_WORKSPACE_ID for federated logins, so the header is added here.
WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"
_WORKSPACE_HEADER = "anthropic-workspace-id"
_NOT_RUN_ROUND_LIMIT = "Tidak dijalankan: batas langkah untuk satu permintaan sudah tercapai."
_NOT_RUN_CUT_OFF = "Tidak dijalankan: jawaban terpotong sebelum perintahnya lengkap."
# Goes between a photo from the phone camera and Master's words, so she knows what it is: what
# Master is showing her, so "lihat ini" is about the photo (the screen tool refuses such a turn's
# bare look words too).
_CAMERA_NOTE = (
    "[Foto dari kamera HP {owner}, diambil saat pesan ini dikirim: inilah yang {owner} "
    "tunjukkan. Jangan simpan isinya dengan remember kecuali diminta.]"
)


class _Unkept(Exception):
    """A turn that cannot stay in the history, answered with `apology` (a canned reply)."""

    def __init__(self, apology: str) -> None:
        super().__init__(apology)
        self.apology = apology


class ClaudeBrain(Brain):
    def __init__(
        self,
        config: BrainConfig,
        registry: DeviceRegistry,
        system_prompt: str | Callable[[], str],
        *,
        client: anthropic.Anthropic | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_idle_minutes: float = _SESSION_DEFAULTS.session_idle_minutes,
        max_session_turns: int = _SESSION_DEFAULTS.max_session_turns,
        providers: Sequence[ToolProvider] = (),
        confirmations: ConfirmationGate | None = None,
        owner_call: str = "Master",
        on_usage: Callable[[str, TokenUsage], None] | None = None,
        turn_images: TurnImages | None = None,
        web_search: WebSearchTool | None = None,
    ) -> None:
        self._max_tool_rounds = config.max_tool_rounds
        self._registry = registry
        self._providers = tuple(providers)
        self._confirmations = confirmations
        self._owner_call = owner_call
        self._on_usage = on_usage
        self._turn_images = turn_images
        self._client = client
        self._session = SessionClock(session_idle_minutes, max_session_turns, clock)
        self._request = _frozen_request(config.claude, registry, self._providers, web_search)
        self._streams = web_search is not None
        self._system_prompt = system_prompt
        self._session_system = ""
        self._history: list[dict[str, Any]] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        gate = self._confirmations
        if gate is not None:
            # Every turn counts, even one rolled back later: only the very next one can say yes.
            gate.begin_turn(user_text)
        for provider in self._providers:
            # An optional hook for tools that need Master's own words (the screen tool).
            if hasattr(provider, "begin_turn"):
                provider.begin_turn(user_text)
        # The photo Master sent with these words, if any (the phone camera).
        images = self._turn_images.take() if self._turn_images is not None else []
        awaiting = gate is not None and gate.awaiting_answer()
        ending = self._session_ending(awaiting)
        if ending is not None:
            logger.info("Claude session %s; starting a new one", ending)
            self.reset()
            if gate is not None and awaiting:
                # The new session never saw the question, so Master's yes must run nothing.
                gate.forget_question()
        if not self._history:
            # A new session (or a rolled-back first turn): its system prompt is fixed from here.
            self._session_system = _read_system_prompt(self._system_prompt)
        turn_start = len(self._history)
        user_turn = format_user_turn(user_text, now or datetime.now())
        self._history.append(
            {"role": "user", "content": _user_content(user_turn, images, self._owner_call)}
        )
        tool_calls: list[ToolCallRecord] = []
        try:
            text = self._run_turn(tool_calls)
        except _Unkept as unkept:
            return self._roll_back(turn_start, unkept.apology, tool_calls)
        except httpx2.TimeoutException:
            # A streamed answer that stalled (the SDK wraps a timeout before an answer starts). The
            # server may bill what it did already, so it is not asked again.
            logger.warning("Claude's streamed answer stalled past the read timeout")
            return self._roll_back(turn_start, _TOO_SLOW, tool_calls)
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
        if gate is not None:
            text = gate.with_question(text)
        return BrainReply(text, tool_calls)

    def reset(self) -> None:
        self._history = []
        self._session.reset()

    def _session_ending(self, awaiting: bool) -> str | None:
        """Why the session should end before this turn, or None when it goes on.

        While a question awaits Master's answer (`awaiting`), a new session would hide the
        question this turn may answer, so it waits one turn; only at _SESSION_IMAGE_CAP it
        does not.
        """
        image_bytes = _image_bytes(self._history)
        if image_bytes >= _SESSION_IMAGE_CAP:
            return f"holds {image_bytes // 2**20} MB of images, too many to wait for an answer"
        if awaiting:
            return None
        if self._session.should_reset():
            return "idle too long or at its turn cap"
        if image_bytes >= _SESSION_IMAGE_BYTES:
            return f"holds {image_bytes // 2**20} MB of images"
        return None

    def _run_turn(self, tool_calls: list[ToolCallRecord]) -> str | None:
        """Call Claude until it stops asking for tools. Returns the reply, or None on a refusal.

        _Unkept when the turn would end with a server tool call left without its result.
        """
        client = self._client_or_create()
        rounds = 0
        while True:
            response = self._send(client)
            self._report_usage(response)
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
                if _unanswered_server_call(content):
                    logger.warning("Claude stopped (%s) mid server tool call", response.stop_reason)
                    raise _Unkept(_CUT_OFF)
                if calls:
                    logger.warning("Claude stopped (%s) mid tool call", response.stop_reason)
                    self._answer_without_running(calls, _NOT_RUN_CUT_OFF)
                    return _CUT_OFF.format(owner=self._owner_call)
                return _final_text(content, tool_calls, self._owner_call)
            if rounds >= self._max_tool_rounds:
                logger.warning("Claude hit the limit of %d tool rounds", self._max_tool_rounds)
                if _unanswered_server_call(content):
                    # A paused search: only resending this very response could answer it.
                    raise _Unkept(_ROUND_LIMIT)
                self._answer_without_running(calls, _NOT_RUN_ROUND_LIMIT)
                return _ROUND_LIMIT.format(owner=self._owner_call)
            rounds += 1
            if calls:
                results = [self._run_tool(call, tool_calls) for call in calls]
                self._history.append({"role": "user", "content": results})

    def _send(self, client: anthropic.Anthropic) -> BetaMessage:
        """One request with the session's history; streamed when she can search the web.

        A search turn may outlast the read timeout: a stream applies it between its events, and
        an answer that stalls after it began is never sent again (and billed twice) by the SDK.
        """
        request = {**self._request, "system": self._session_system, "messages": self._history}
        if not self._streams:
            return client.beta.messages.create(**request)
        with client.beta.messages.stream(**request) as stream:
            return stream.get_final_message()

    def _report_usage(self, response: BetaMessage) -> None:
        """Hand `on_usage` the model and the tokens of every attempt the response billed."""
        if self._on_usage is None:
            return
        try:
            for model, tokens in billed_attempts(response, self._request["model"]):
                self._on_usage(model, tokens)
        except Exception:
            logger.exception("Could not record the usage of a Claude response; the turn goes on")

    def _run_tool(
        self, call: BetaToolUseBlock, tool_calls: list[ToolCallRecord]
    ) -> BetaToolResultBlockParam:
        result, is_error = run_tool(
            self._registry,
            call.name,
            call.input,
            self._providers,
            confirmations=self._confirmations,
        )
        logger.info(
            "Tool %s %s -> %s",
            call.name,
            logged_arguments(call.name, call.input),
            "error" if is_error else "ok",
        )
        arguments = dict(call.input) if isinstance(call.input, Mapping) else {}
        tool_calls.append(ToolCallRecord(call.name, arguments, result, is_error))
        return _tool_result(call.id, result, is_error, self._take_images())

    def _take_images(self) -> list[ToolImage]:
        """What the providers have to show for the call that just ran (a screenshot)."""
        return [
            image
            for provider in self._providers
            if hasattr(provider, "take_images")
            for image in provider.take_images()
        ]

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
        """Undo the turn and apologise with `apology`, one of the canned replies above, followed
        by what the turn changed in tepira all the same (a recorded or undone expense)."""
        if self._confirmations is not None:
            self._confirmations.discard_turn()
        if changed_memory(tool_calls):
            # Neither this session's prompt nor its history would show the saved or deleted
            # note; the next session lists the notes as they are.
            logger.info("Rolled back a turn that changed a note; starting a new session")
            self.reset()
        else:
            del self._history[turn_start:]
        text = " ".join(
            filter(None, (apology.format(owner=self._owner_call), finance_changes(tool_calls)))
        )
        return BrainReply(text, tool_calls)

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


def web_search_tool(
    max_uses: int,
    *,
    model: str,
    country: str,
    timezone: str | None = None,
    city: str | None = None,
) -> WebSearchTool:
    """Claude's server-side web search for `model`: at most `max_uses` searches per request,
    with results for someone in `country` (two letters), and `timezone` (IANA) and `city`
    when known.

    A model with dynamic filtering gets `web_search_20260209`, which filters its results with
    code execution of its own (so no `code_execution` tool goes beside it: a second one would
    only confuse the model); any other model refuses that version, so it gets
    `web_search_20250305`.
    """
    location: BetaUserLocationParam = {"type": "approximate", "country": country}
    if timezone is not None:
        location["timezone"] = timezone
    if city is not None:
        location["city"] = city
    if model in DYNAMIC_FILTERING_MODELS:
        return {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": max_uses,
            "user_location": location,
        }
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
        "user_location": location,
    }


def _read_system_prompt(system_prompt: str | Callable[[], str]) -> str:
    return system_prompt() if callable(system_prompt) else system_prompt


def _frozen_request(
    claude: ClaudeConfig,
    registry: DeviceRegistry,
    providers: Sequence[ToolProvider],
    web_search: WebSearchTool | None,
) -> dict[str, Any]:
    """Every request field except `system` and `messages`, fixed for the brain's whole lifetime.

    No `thinking` (adaptive by omission; disabling it is rejected on this model),
    no sampling parameters, and no forced `tool_choice`. The web search tool, when given,
    comes after every tool of hers.
    """
    tools: list[Mapping[str, Any]] = [
        to_anthropic(spec) for spec in build_tool_specs(registry, providers)
    ]
    if web_search is not None:
        tools.append(web_search)
    request: dict[str, Any] = {
        "model": claude.model,
        "max_tokens": claude.max_tokens,
        "tools": tools,
        "output_config": {"effort": claude.effort},
        "cache_control": {"type": "ephemeral"},
    }
    if claude.fallbacks:
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"
    return request


def _echoable(content: Sequence[BetaContentBlock]) -> list[BetaContentBlock]:
    """The response blocks to append to history, applying the fallback echo rule.

    Before the last `fallback` block the declining model's thinking, tool calls, and server
    tool calls without a result (no block names their id as its `tool_use_id`) are dropped;
    paired server-tool blocks, text, and everything after the boundary are echoed.
    """
    fallback_at = max(
        (index for index, block in enumerate(content) if block.type == "fallback"), default=None
    )
    if fallback_at is None:
        return list(content)
    answered = {block.tool_use_id for block in content if hasattr(block, "tool_use_id")}
    return [
        block
        for index, block in enumerate(content)
        if index >= fallback_at or not _declined(block, answered)
    ]


def _unanswered_server_call(content: Sequence[BetaContentBlock]) -> bool:
    """Whether a server tool call in `content` has no result there (no block names its id as its
    `tool_use_id`). Kept in the history, it would make every later request invalid."""
    answered = {block.tool_use_id for block in content if hasattr(block, "tool_use_id")}
    return any(block.type == "server_tool_use" and block.id not in answered for block in content)


def _declined(block: BetaContentBlock, answered: Collection[str]) -> bool:
    """Whether a block before the last `fallback` block is left out of the echo."""
    if block.type == "server_tool_use":
        return block.id not in answered
    return block.type in _DROPPED_BEFORE_FALLBACK


def _tool_result(
    tool_use_id: str, result: str, is_error: bool, images: Sequence[ToolImage] = ()
) -> BetaToolResultBlockParam:
    """The JSON result as plain text, or as a text block followed by one block per image."""
    content: str | list[BetaTextBlockParam | BetaImageBlockParam] = result
    if images:
        content = [{"type": "text", "text": result}, *map(_image_block, images)]
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _user_content(
    user_turn: str, images: Sequence[ToolImage], owner_call: str
) -> str | list[BetaTextBlockParam | BetaImageBlockParam]:
    """Master's message: the words alone, or after the photo and a note saying where it is from.

    Without a photo it stays the plain string, so such requests are exactly as they always were.
    """
    if not images:
        return user_turn
    return [
        *map(_image_block, images),
        {"type": "text", "text": _CAMERA_NOTE.format(owner=owner_call)},
        {"type": "text", "text": user_turn},
    ]


def _image_bytes(history: Sequence[Mapping[str, Any]]) -> int:
    """The size of the base64 data of every image in the history: the photos in Master's own
    messages and the screenshots in tool results."""
    blocks = [
        block
        for message in history
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
    ]
    in_results = [
        inner
        for block in blocks
        if block["type"] == "tool_result" and isinstance(block["content"], list)
        for inner in block["content"]
    ]
    return sum(
        len(block["source"]["data"]) for block in [*blocks, *in_results] if block["type"] == "image"
    )


def _image_block(image: ToolImage) -> BetaImageBlockParam:
    data = base64.b64encode(image.data).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": image.media_type, "data": data},
    }


def _final_text(
    content: Sequence[BetaContentBlock], tool_calls: list[ToolCallRecord], owner_call: str
) -> str:
    """The reply's text, or when it has none, what the tool calls did, said to `owner_call`.

    Adjacent text blocks are one passage the API split at its citations (see `_passage`);
    passages that other blocks (a search) separate join with a space. Calls held for Master's
    yes are left out: the gate's question follows the reply, and is the whole reply when every
    call was held.
    """
    passages = (
        _passage(block.text for block in blocks)
        for is_text, blocks in groupby(content, key=lambda block: block.type == "text")
        if is_text
    )
    text = " ".join(passage for passage in passages if passage)
    if text:
        return text
    if not tool_calls:
        return _NO_ANSWER.format(owner=owner_call)
    ran = [call for call in tool_calls if not held_for_yes(call)]
    if not ran:
        return ""
    canned = _TOOLS_FAILED if any(call.is_error for call in ran) else _DONE
    return canned.format(owner=owner_call)


def _passage(pieces: Iterable[str]) -> str:
    """The pieces of one passage joined as they are, since each keeps its own spacing, with
    its ends stripped. Whitespace that both pieces carry where they meet becomes one space, or
    its line breaks when it holds any; the text inside a piece, its line breaks too, stays as
    written.
    """
    text = ""
    for piece in pieces:
        head, tail = text.rstrip(), piece.lstrip()
        if head != text and tail != piece:
            junction = text[len(head) :] + piece[: len(piece) - len(tail)]
            text = head + ("\n" * junction.count("\n") or " ") + tail
        else:
            text += piece
    return text.strip()


def billed_attempts(response: BetaMessage, requested_model: str) -> list[tuple[str, TokenUsage]]:
    """The model and the tokens of each model attempt behind `response`.

    `usage.iterations` lists every sampling iteration: each turn of a server-side tool loop
    (a search), and after a server-side fallback every attempt, a declined one too (billed like
    any other), where the top-level `usage` covers only the attempt that produced the message.
    Its `message` entries, and `fallback_message` for the fallback that answered, are summed per
    model attempt: the iterations in a row of one model are one attempt. A `message` entry may
    leave out its model: it ran on the requested one. Without such entries, the top-level
    `usage` is the whole bill, from the model that answered.

    Only the top-level `usage` counts web searches, so they go once, with the last attempt
    (the one that produced the message).
    """
    usage = response.usage
    whole = TokenUsage.from_usage(usage)
    attempts: list[tuple[str, TokenUsage]] = []
    for entry in getattr(usage, "iterations", None) or ():
        if entry.type not in _ATTEMPT_ITERATIONS:
            continue
        model, tokens = entry.model or requested_model, TokenUsage.from_usage(entry)
        if attempts and attempts[-1][0] == model:
            attempts[-1] = (model, attempts[-1][1] + tokens)
        else:
            attempts.append((model, tokens))
    if not attempts:
        return [(response.model, whole)]
    model, tokens = attempts[-1]
    attempts[-1] = (model, dataclasses.replace(tokens, web_searches=whole.web_searches))
    return attempts


def _log_cache_use(response: BetaMessage) -> None:
    cache_read = getattr(getattr(response, "usage", None), "cache_read_input_tokens", None)
    if cache_read is not None:
        logger.debug("Claude read %d input tokens from the prompt cache", cache_read)


def _log_refusal(response: BetaMessage) -> None:
    details = response.stop_details
    category = details.category if details is not None else None
    logger.warning("Claude declined the turn (category %s); rolling it back", category)
