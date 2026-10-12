import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from waifu.brain import claude as claude_module
from waifu.brain.base import ToolSpec
from waifu.brain.claude import ClaudeBrain
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.persona import format_user_turn
from waifu.config import BrainConfig, ClaudeConfig
from waifu.devices.base import Device, DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.memory import MemoryStore, memory_prompt_section

NOW = datetime(2026, 10, 12, 7, 30)
SYSTEM_PROMPT = "Kamu Cyrene."
FORBIDDEN_REQUEST_KEYS = {"temperature", "top_p", "top_k", "thinking", "tool_choice"}


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def thinking(signature: str) -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking="", signature=signature)


def redacted_thinking(data: str) -> SimpleNamespace:
    return SimpleNamespace(type="redacted_thinking", data=data)


def tool_use(block_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=arguments)


def fallback() -> SimpleNamespace:
    return SimpleNamespace(
        type="fallback",
        from_=SimpleNamespace(model="claude-opus-5-5"),
        to=SimpleNamespace(model="claude-opus-5"),
    )


def message(content: list, stop_reason: str, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(content=content, stop_reason=stop_reason, stop_details=None, **extra)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: records each beta.messages.create call."""

    api_key = "test-key"
    auth_token = None
    credentials = None

    def __init__(self, *outcomes: object) -> None:
        self.requests: list[dict] = []
        self._outcomes = list(outcomes)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: object) -> object:
        # Snapshot the list so later appends by the brain do not rewrite what was sent.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ExplodingDevice(Device):
    def __init__(self) -> None:
        super().__init__("tv", "TV")

    def commands(self) -> list[str]:
        return ["on"]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        raise RuntimeError("firmware crashed")


def house() -> DeviceRegistry:
    return DeviceRegistry(
        [
            MockDevice("lampu_kamar", "Lampu kamar", commands=["on", "off"], kind="light"),
            MockDevice("ac", "AC", commands=["on", "off", "temp_24"], kind="ir"),
        ]
    )


def make_brain(
    client: FakeAnthropic,
    registry: DeviceRegistry | None = None,
    *,
    fallbacks: bool = True,
    max_tool_rounds: int = 5,
    system_prompt: str | Callable[[], str] = SYSTEM_PROMPT,
    **kwargs: object,
) -> ClaudeBrain:
    config = BrainConfig(
        backend="claude",
        max_tool_rounds=max_tool_rounds,
        claude=ClaudeConfig(
            model="claude-opus-5-5", effort="low", max_tokens=16000, fallbacks=fallbacks
        ),
    )
    return ClaudeBrain(config, registry or house(), system_prompt, client=client, **kwargs)


def request_error(
    error_type: type[anthropic.APIStatusError], status: int
) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return error_type("error", response=httpx2.Response(status, request=request), body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )


def tool_results(user_message: dict) -> list[dict]:
    return [block for block in user_message["content"] if block["type"] == "tool_result"]


def answered_tool_uses_are_complete(history: list[dict]) -> bool:
    asked = {
        block.id
        for entry in history
        if entry["role"] == "assistant"
        for block in entry["content"]
        if block.type == "tool_use"
    }
    answered = {
        block["tool_use_id"]
        for entry in history
        if entry["role"] == "user" and isinstance(entry["content"], list)
        for block in entry["content"]
    }
    return asked == answered


def test_two_tool_calls_run_on_devices_and_return_in_one_user_message():
    registry = house()
    first = message(
        [
            thinking("sig-1"),
            tool_use("call_1", "control_device", {"device": "lampu_kamar", "command": "on"}),
            tool_use("call_2", "control_device", {"device": "ac", "command": "temp", "value": 24}),
        ],
        "tool_use",
    )
    client = FakeAnthropic(first, message([text("Sudah kunyalakan, Master!")], "end_turn"))

    reply = make_brain(client, registry).respond("nyalakan lampu dan AC 24", now=NOW)

    assert registry.get("lampu_kamar").calls == [("on", None)]
    assert registry.get("ac").calls == [("temp_24", 24)]
    results_message = client.requests[1]["messages"][-1]
    assert results_message["role"] == "user"
    results = tool_results(results_message)
    assert [r["tool_use_id"] for r in results] == ["call_1", "call_2"]
    assert [r["is_error"] for r in results] == [False, False]
    assert all(json.loads(r["content"])["ok"] for r in results)
    assert reply.text == "Sudah kunyalakan, Master!"
    assert [(c.name, c.arguments["device"], c.is_error) for c in reply.tool_calls] == [
        ("control_device", "lampu_kamar", False),
        ("control_device", "ac", False),
    ]


def test_assistant_turn_is_echoed_with_its_full_content_including_thinking():
    first_content = [
        thinking("sig-1"),
        tool_use("call_1", "control_device", {"device": "lampu_kamar", "command": "on"}),
    ]
    client = FakeAnthropic(
        message(first_content, "tool_use"), message([text("Sudah.")], "end_turn")
    )

    make_brain(client).respond("nyalakan lampu", now=NOW)

    echoed = client.requests[1]["messages"][-2]
    assert echoed["role"] == "assistant"
    assert echoed["content"] == first_content
    assert all(
        sent is original for sent, original in zip(echoed["content"], first_content, strict=True)
    )


def test_user_turn_carries_the_current_time():
    client = FakeAnthropic(message([text("Pagi, Master!")], "end_turn"))

    make_brain(client).respond("selamat pagi", now=NOW)

    assert client.requests[0]["messages"] == [
        {"role": "user", "content": format_user_turn("selamat pagi", NOW)}
    ]


def test_request_carries_model_effort_strict_tools_cache_control_and_fallbacks():
    client = FakeAnthropic(message([text("Halo!")], "end_turn"))

    make_brain(client).respond("halo", now=NOW)

    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["max_tokens"] == 16000
    assert request["system"] == SYSTEM_PROMPT
    assert request["output_config"] == {"effort": "low"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["fallbacks"] == "default"
    assert sorted(tool["name"] for tool in request["tools"]) == ["control_device", "list_devices"]
    assert all(tool["strict"] is True for tool in request["tools"])
    assert FORBIDDEN_REQUEST_KEYS.isdisjoint(request)


def test_request_without_fallbacks_sends_no_beta_or_fallbacks():
    client = FakeAnthropic(message([text("Halo!")], "end_turn"))

    make_brain(client, fallbacks=False).respond("halo", now=NOW)

    assert "betas" not in client.requests[0]
    assert "fallbacks" not in client.requests[0]


def test_second_turn_resends_the_first_turn_unchanged_and_appends_to_it():
    first_content = [thinking("sig-1"), text("Halo, Master!")]
    client = FakeAnthropic(
        message(first_content, "end_turn"), message([text("Jam setengah delapan.")], "end_turn")
    )
    brain = make_brain(client)

    brain.respond("halo", now=NOW)
    brain.respond("jam berapa?", now=NOW)

    first_user = client.requests[0]["messages"][0]
    second = client.requests[1]["messages"]
    assert second[0] is first_user
    assert second[1] == {"role": "assistant", "content": first_content}
    assert all(
        sent is original for sent, original in zip(second[1]["content"], first_content, strict=True)
    )
    assert second[2] == {"role": "user", "content": format_user_turn("jam berapa?", NOW)}
    assert len(second) == 3


def test_reset_starts_the_next_turn_with_an_empty_history():
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"), message([text("Hai!")], "end_turn")
    )
    brain = make_brain(client)

    brain.respond("halo", now=NOW)
    brain.reset()
    brain.respond("hai", now=NOW)

    assert client.requests[1]["messages"] == [
        {"role": "user", "content": format_user_turn("hai", NOW)}
    ]


def test_idle_timeout_starts_a_new_session():
    clock = FakeClock()
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"), message([text("Hai!")], "end_turn")
    )
    brain = make_brain(client, clock=clock, session_idle_minutes=10)

    brain.respond("halo", now=NOW)
    clock.now += 11 * 60
    brain.respond("hai", now=NOW)

    assert client.requests[1]["messages"] == [
        {"role": "user", "content": format_user_turn("hai", NOW)}
    ]


def test_turn_cap_starts_a_new_session():
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"), message([text("Hai!")], "end_turn")
    )
    brain = make_brain(client, clock=FakeClock(), max_session_turns=1)

    brain.respond("halo", now=NOW)
    brain.respond("hai", now=NOW)

    assert len(client.requests[1]["messages"]) == 1


def test_connection_error_apologises_and_rolls_back_the_turn():
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"),
        connection_error(),
        message([text("Sudah tersambung lagi.")], "end_turn"),
    )
    brain = make_brain(client)

    brain.respond("halo", now=NOW)
    reply = brain.respond("nyalakan lampu", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert "tidak bisa terhubung" in reply.text
    third = client.requests[2]["messages"]
    assert [entry["role"] for entry in third] == ["user", "assistant", "user"]
    assert third[2]["content"] == format_user_turn("coba lagi", NOW)


def test_error_after_a_tool_round_rolls_back_the_whole_turn_but_reports_the_tool_call():
    registry = house()
    client = FakeAnthropic(
        message(
            [tool_use("call_1", "control_device", {"device": "lampu_kamar", "command": "on"})],
            "tool_use",
        ),
        connection_error(),
        message([text("Halo!")], "end_turn"),
    )
    brain = make_brain(client, registry)

    reply = brain.respond("nyalakan lampu", now=NOW)
    brain.respond("halo", now=NOW)

    assert registry.get("lampu_kamar").calls == [("on", None)]
    assert [call.name for call in reply.tool_calls] == ["control_device"]
    assert client.requests[2]["messages"] == [
        {"role": "user", "content": format_user_turn("halo", NOW)}
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (request_error(anthropic.AuthenticationError, 401), "API key"),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), "terhubung"),
        (request_error(anthropic.RateLimitError, 429), "sibuk"),
        (request_error(anthropic.InternalServerError, 500), "bermasalah"),
        (request_error(anthropic.BadRequestError, 400), "Maaf Master"),
        (ValueError("unexpected"), "Maaf Master"),
    ],
)
def test_api_errors_return_an_apology_and_leave_history_empty(error, expected):
    client = FakeAnthropic(error, message([text("Halo!")], "end_turn"))
    brain = make_brain(client)

    reply = brain.respond("halo", now=NOW)
    brain.respond("halo lagi", now=NOW)

    assert expected in reply.text
    assert reply.tool_calls == []
    assert client.requests[1]["messages"] == [
        {"role": "user", "content": format_user_turn("halo lagi", NOW)}
    ]


@pytest.mark.parametrize(
    "stop_details",
    [None, SimpleNamespace(type="refusal", category="cyber", explanation="Not allowed.")],
)
def test_refusal_rolls_back_the_turn_and_apologises(stop_details):
    refused = message([text("Aku")], "refusal")
    refused.stop_details = stop_details
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"), refused, message([text("Oke.")], "end_turn")
    )
    brain = make_brain(client)

    brain.respond("halo", now=NOW)
    reply = brain.respond("sesuatu yang ditolak", now=NOW)
    brain.respond("lupakan", now=NOW)

    assert reply.text == "Maaf Master, aku tidak bisa membantu yang itu."
    third = client.requests[2]["messages"]
    assert [entry["role"] for entry in third] == ["user", "assistant", "user"]
    assert third[2]["content"] == format_user_turn("lupakan", NOW)


def test_fallback_echo_drops_thinking_and_tool_use_before_the_last_fallback():
    partial = text("Sebentar")
    after_thinking = thinking("sig-after")
    final = text("Sudah, Master.")
    content = [
        thinking("sig-before"),
        redacted_thinking("opaque"),
        tool_use("call_dropped", "control_device", {"device": "ac", "command": "on"}),
        partial,
        fallback(),
        after_thinking,
        final,
    ]
    client = FakeAnthropic(message(content, "end_turn"), message([text("Oke.")], "end_turn"))
    brain = make_brain(client)

    brain.respond("halo", now=NOW)
    brain.respond("lagi", now=NOW)

    echoed = client.requests[1]["messages"][1]["content"]
    assert [block.type for block in echoed] == ["text", "fallback", "thinking", "text"]
    assert echoed[0] is partial
    assert echoed[2] is after_thinking
    assert echoed[3] is final


def test_fallback_echo_only_runs_tool_calls_made_after_the_fallback():
    registry = house()
    content = [
        tool_use("call_dropped", "control_device", {"device": "ac", "command": "on"}),
        fallback(),
        tool_use("call_kept", "control_device", {"device": "lampu_kamar", "command": "on"}),
    ]
    client = FakeAnthropic(message(content, "tool_use"), message([text("Sudah.")], "end_turn"))

    make_brain(client, registry).respond("nyalakan lampu", now=NOW)

    assert registry.get("ac").calls == []
    assert registry.get("lampu_kamar").calls == [("on", None)]
    history = client.requests[1]["messages"]
    assert [block.type for block in history[1]["content"]] == ["fallback", "tool_use"]
    assert [r["tool_use_id"] for r in tool_results(history[2])] == ["call_kept"]


def test_device_that_raises_becomes_an_error_tool_result_and_a_reply():
    registry = DeviceRegistry([ExplodingDevice()])
    client = FakeAnthropic(
        message(
            [tool_use("call_1", "control_device", {"device": "tv", "command": "on"})], "tool_use"
        ),
        message([text("Maaf Master, TV-nya error.")], "end_turn"),
    )

    reply = make_brain(client, registry).respond("nyalakan TV", now=NOW)

    result = tool_results(client.requests[1]["messages"][-1])[0]
    assert result["is_error"] is True
    assert json.loads(result["content"])["ok"] is False
    assert reply.text == "Maaf Master, TV-nya error."
    assert reply.tool_calls[0].is_error is True


def test_pause_turn_sends_the_paused_content_back_to_continue():
    paused = [thinking("sig-1"), text("Sebentar")]
    client = FakeAnthropic(message(paused, "pause_turn"), message([text("Selesai.")], "end_turn"))

    reply = make_brain(client).respond("halo", now=NOW)

    assert client.requests[1]["messages"][-1] == {"role": "assistant", "content": paused}
    assert reply.text == "Selesai."


def test_round_limit_answers_pending_calls_without_running_them_and_apologises():
    registry = house()
    lamp_on = {"device": "lampu_kamar", "command": "on"}
    client = FakeAnthropic(
        message([tool_use("call_1", "control_device", lamp_on)], "tool_use"),
        message([tool_use("call_2", "control_device", lamp_on)], "tool_use"),
        message([text("Halo!")], "end_turn"),
    )
    brain = make_brain(client, registry, max_tool_rounds=1)

    reply = brain.respond("nyalakan lampu terus-menerus", now=NOW)
    brain.respond("halo", now=NOW)

    assert len(registry.get("lampu_kamar").calls) == 1
    assert reply.text.startswith("Maaf Master")
    history = client.requests[2]["messages"]
    assert answered_tool_uses_are_complete(history)
    pending = tool_results(history[-2])
    assert [(r["tool_use_id"], r["is_error"]) for r in pending] == [("call_2", True)]


def test_truncated_response_with_a_tool_call_leaves_no_unanswered_tool_use():
    registry = house()
    client = FakeAnthropic(
        message(
            [tool_use("call_1", "control_device", {"device": "lampu_kamar", "command": "on"})],
            "max_tokens",
        ),
        message([text("Halo!")], "end_turn"),
    )
    brain = make_brain(client, registry)

    reply = brain.respond("nyalakan lampu", now=NOW)
    brain.respond("halo", now=NOW)

    assert registry.get("lampu_kamar").calls == []
    assert reply.text.startswith("Maaf Master")
    assert answered_tool_uses_are_complete(client.requests[1]["messages"])


def test_empty_final_text_after_successful_tools_confirms():
    client = FakeAnthropic(
        message(
            [tool_use("call_1", "control_device", {"device": "lampu_kamar", "command": "on"})],
            "tool_use",
        ),
        message([thinking("sig-2")], "end_turn"),
    )

    reply = make_brain(client).respond("nyalakan lampu", now=NOW)

    assert reply.text == "Sudah, Master."


def test_empty_final_text_after_a_failed_tool_apologises():
    client = FakeAnthropic(
        message(
            [tool_use("call_1", "control_device", {"device": "kulkas", "command": "on"})],
            "tool_use",
        ),
        message([], "end_turn"),
    )

    reply = make_brain(client).respond("nyalakan kulkas", now=NOW)

    assert reply.text.startswith("Maaf Master")
    assert reply.tool_calls[0].is_error is True


def test_client_is_created_lazily_and_reused(monkeypatch):
    created: list[FakeAnthropic] = []

    def fake_anthropic(**kwargs: object) -> FakeAnthropic:
        client = FakeAnthropic(
            message([text("Halo!")], "end_turn"), message([text("Hai!")], "end_turn")
        )
        created.append(client)
        return client

    monkeypatch.setattr(claude_module.anthropic, "Anthropic", fake_anthropic)
    config = BrainConfig(backend="claude")
    brain = ClaudeBrain(config, house(), SYSTEM_PROMPT)

    assert created == []
    brain.respond("halo", now=NOW)
    brain.respond("hai", now=NOW)
    assert len(created) == 1
    assert len(created[0].requests) == 2


def test_default_client_bounds_each_request_and_retries_at_most_once(monkeypatch):
    sent_timeouts: list[dict] = []
    created: list[anthropic.Anthropic] = []
    real_anthropic = anthropic.Anthropic

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent_timeouts.append(request.extensions["timeout"])
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "Halo, Master!"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    def build(**kwargs: object) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(handler)
        client = real_anthropic(
            api_key="test-key", http_client=httpx2.Client(transport=transport), **kwargs
        )
        created.append(client)
        return client

    monkeypatch.setattr(claude_module.anthropic, "Anthropic", build)

    reply = ClaudeBrain(BrainConfig(backend="claude"), house(), SYSTEM_PROMPT).respond(
        "halo", now=NOW
    )

    assert reply.text == "Halo, Master!"
    (timeout,) = sent_timeouts
    assert None not in timeout.values()
    assert timeout["connect"] <= 5
    assert timeout["read"] <= 60
    assert created[0].max_retries <= 1


def clear_claude_credentials(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """No key in the environment and no Anthropic profile on disk, as on a fresh PC."""
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)
    for name in ("APPDATA", "HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))


def test_missing_api_key_apologises_about_the_key_without_a_traceback(
    monkeypatch, tmp_path, caplog
):
    clear_claude_credentials(monkeypatch, tmp_path)
    brain = ClaudeBrain(BrainConfig(backend="claude"), house(), SYSTEM_PROMPT)

    with caplog.at_level(logging.DEBUG):
        reply = brain.respond("halo", now=NOW)

    assert "API key" in reply.text
    assert reply.tool_calls == []
    assert [record.getMessage() for record in caplog.records if record.exc_info] == []


def test_key_found_after_a_missing_key_turn_is_used_and_the_failed_turn_is_gone(
    monkeypatch, tmp_path
):
    clear_claude_credentials(monkeypatch, tmp_path)
    brain = ClaudeBrain(BrainConfig(backend="claude"), house(), SYSTEM_PROMPT)
    brain.respond("halo", now=NOW)
    with_key = FakeAnthropic(message([text("Halo!")], "end_turn"))
    monkeypatch.setattr(claude_module.anthropic, "Anthropic", lambda **kwargs: with_key)

    reply = brain.respond("halo lagi", now=NOW)

    assert reply.text == "Halo!"
    assert with_key.requests[0]["messages"] == [
        {"role": "user", "content": format_user_turn("halo lagi", NOW)}
    ]


def test_cache_read_tokens_are_logged_at_debug(caplog):
    usage = SimpleNamespace(cache_read_input_tokens=1234)
    client = FakeAnthropic(message([text("Halo!")], "end_turn", usage=usage))

    with caplog.at_level(logging.DEBUG, logger=claude_module.__name__):
        make_brain(client).respond("halo", now=NOW)

    assert "1234" in caplog.text


def test_real_sdk_sends_the_request_and_the_fallback_echo_on_the_wire():
    sent: list[tuple[httpx2.Headers, dict]] = []
    usage = {"input_tokens": 10, "output_tokens": 5}
    replies = [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig-declined"},
                {
                    "type": "fallback",
                    "from": {"model": "claude-opus-5-5"},
                    "to": {"model": "claude-opus-5"},
                    "trigger": {"type": "refusal", "category": None},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "control_device",
                    "input": {"device": "lampu_kamar", "command": "on"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": usage,
        },
        {
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": "Sudah, Master!"}],
            "stop_reason": "end_turn",
            "usage": usage,
        },
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.headers, json.loads(request.content)))
        return httpx2.Response(200, json=replies[len(sent) - 1])

    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )

    reply = make_brain(client).respond("nyalakan lampu", now=NOW)

    headers, first_body = sent[0]
    assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert sorted(first_body) == [
        "cache_control",
        "fallbacks",
        "max_tokens",
        "messages",
        "model",
        "output_config",
        "system",
        "tools",
    ]
    _, second_body = sent[1]
    echoed = second_body["messages"][1]["content"]
    assert [block["type"] for block in echoed] == ["fallback", "tool_use"]
    assert echoed[0]["from"] == {"model": "claude-opus-5-5"}
    assert second_body["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"
    assert reply.text == "Sudah, Master!"


def _real_client_capturing_headers(monkeypatch, sent_headers: list[httpx2.Headers]) -> None:
    real_anthropic = anthropic.Anthropic

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent_headers.append(request.headers)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "Halo, Master!"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    def build(**kwargs: object) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(handler)
        return real_anthropic(
            api_key="test-key", http_client=httpx2.Client(transport=transport), **kwargs
        )

    monkeypatch.setattr(claude_module.anthropic, "Anthropic", build)


def test_workspace_id_from_the_environment_is_sent_with_every_request(monkeypatch):
    # Organization-level API keys are rejected unless the request names a workspace.
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", " wrkspc_test123 ")
    sent_headers: list[httpx2.Headers] = []
    _real_client_capturing_headers(monkeypatch, sent_headers)

    reply = ClaudeBrain(BrainConfig(backend="claude"), house(), SYSTEM_PROMPT).respond(
        "halo", now=NOW
    )

    assert reply.text == "Halo, Master!"
    assert sent_headers[0]["anthropic-workspace-id"] == "wrkspc_test123"


def test_no_workspace_header_is_sent_without_the_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    sent_headers: list[httpx2.Headers] = []
    _real_client_capturing_headers(monkeypatch, sent_headers)

    ClaudeBrain(BrainConfig(backend="claude"), house(), SYSTEM_PROMPT).respond("halo", now=NOW)

    assert "anthropic-workspace-id" not in sent_headers[0]


def test_key_without_a_workspace_gets_an_apology_that_names_the_fix():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.BadRequestError(
        "This API key is not scoped to a workspace, so this request must include the "
        "anthropic-workspace-id header with the ID of the workspace to use.",
        response=httpx2.Response(400, request=request),
        body=None,
    )
    brain = make_brain(FakeAnthropic(error))

    reply = brain.respond("halo", now=NOW)

    assert "ANTHROPIC_WORKSPACE_ID" in reply.text
    assert "workspace" in reply.text


class GameTools:
    def specs(self) -> list[ToolSpec]:
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        return [ToolSpec("game_status", "Status of Master's games.", schema)]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        return json.dumps({"games": [{"game": "hsr", "stamina": {"current": 232}}]}), False


def test_provider_tools_are_offered_and_their_calls_answered():
    first = message([tool_use("call_1", "game_status", {})], "tool_use")
    client = FakeAnthropic(first, message([text("Trailblaze Power-mu 232, Master!")], "end_turn"))

    reply = make_brain(client, providers=[GameTools()]).respond("TP-ku berapa?", now=NOW)

    assert [tool["name"] for tool in client.requests[0]["tools"]] == [
        "control_device",
        "list_devices",
        "game_status",
    ]
    [result] = tool_results(client.requests[1]["messages"][-1])
    assert json.loads(result["content"])["games"][0]["stamina"]["current"] == 232
    assert [(call.name, call.is_error) for call in reply.tool_calls] == [("game_status", False)]


# Per-session system prompt


class SessionPrompt:
    """A callable system prompt that counts its reads; `text` may change between them."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.reads = 0

    def __call__(self) -> str:
        self.reads += 1
        return self.text


def lamp_on_then_done() -> list[SimpleNamespace]:
    lamp_on = {"device": "lampu_kamar", "command": "on"}
    return [
        message([tool_use("call_1", "control_device", lamp_on)], "tool_use"),
        message([text("Sudah.")], "end_turn"),
    ]


def test_callable_system_prompt_is_read_once_for_every_turn_and_tool_round_of_a_session():
    prompt = SessionPrompt("Kamu Cyrene. Master alergi udang.")
    client = FakeAnthropic(
        *lamp_on_then_done(),
        message([text("Halo!")], "end_turn"),
        message([text("Hai!")], "end_turn"),
    )
    brain = make_brain(client, system_prompt=prompt)

    brain.respond("nyalakan lampu", now=NOW)
    brain.respond("halo", now=NOW)
    brain.respond("hai", now=NOW)

    assert prompt.reads == 1
    assert [request["system"] for request in client.requests] == [
        "Kamu Cyrene. Master alergi udang."
    ] * 4


def test_callable_system_prompt_sends_the_same_requests_as_the_string():
    with_string = FakeAnthropic(*lamp_on_then_done())
    with_callable = FakeAnthropic(*lamp_on_then_done())

    make_brain(with_string).respond("nyalakan lampu", now=NOW)
    make_brain(with_callable, system_prompt=lambda: SYSTEM_PROMPT).respond(
        "nyalakan lampu", now=NOW
    )

    assert with_callable.requests == with_string.requests


def test_system_prompt_changed_mid_session_is_only_seen_by_the_next_session():
    prompt = SessionPrompt("Ingatan: belum ada.")
    client = FakeAnthropic(*(message([text("Oke.")], "end_turn") for _ in range(3)))
    brain = make_brain(client, system_prompt=prompt)

    brain.respond("ingat ya, aku alergi udang", now=NOW)
    prompt.text = "Ingatan: Master alergi udang."
    brain.respond("makasih", now=NOW)
    brain.reset()
    brain.respond("enaknya masak apa?", now=NOW)

    assert [request["system"] for request in client.requests] == [
        "Ingatan: belum ada.",
        "Ingatan: belum ada.",
        "Ingatan: Master alergi udang.",
    ]


def idle_too_long(brain: ClaudeBrain, clock: FakeClock) -> None:
    clock.now += 11 * 60


def reached_the_turn_cap(brain: ClaudeBrain, clock: FakeClock) -> None:
    """Nothing to do: with a cap of one turn, the first turn already ends the session."""


def reset_by_hand(brain: ClaudeBrain, clock: FakeClock) -> None:
    brain.reset()


@pytest.mark.parametrize(
    ("end_session", "limits"),
    [
        (idle_too_long, {"session_idle_minutes": 10}),
        (reached_the_turn_cap, {"max_session_turns": 1}),
        (reset_by_hand, {}),
    ],
    ids=["idle", "turn_cap", "reset"],
)
def test_every_new_session_reads_the_system_prompt_again(end_session, limits):
    prompt = SessionPrompt("Sesi satu.")
    clock = FakeClock()
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"), message([text("Hai!")], "end_turn")
    )
    brain = make_brain(client, system_prompt=prompt, clock=clock, **limits)

    brain.respond("halo", now=NOW)
    prompt.text = "Sesi dua."
    end_session(brain, clock)
    brain.respond("hai", now=NOW)

    assert prompt.reads == 2
    assert [request["system"] for request in client.requests] == ["Sesi satu.", "Sesi dua."]
    assert client.requests[1]["messages"] == [
        {"role": "user", "content": format_user_turn("hai", NOW)}
    ]


def test_rolled_back_first_turn_reads_the_system_prompt_again_on_the_next_turn():
    prompt = SessionPrompt("Ingatan lama.")
    client = FakeAnthropic(connection_error(), message([text("Halo!")], "end_turn"))
    brain = make_brain(client, system_prompt=prompt)

    brain.respond("halo", now=NOW)
    prompt.text = "Ingatan baru."
    brain.respond("halo lagi", now=NOW)

    assert prompt.reads == 2
    assert [request["system"] for request in client.requests] == [
        "Ingatan lama.",
        "Ingatan baru.",
    ]


def test_rolled_back_later_turn_keeps_the_session_system_prompt():
    prompt = SessionPrompt("Ingatan lama.")
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"),
        connection_error(),
        message([text("Oke.")], "end_turn"),
    )
    brain = make_brain(client, system_prompt=prompt)

    brain.respond("halo", now=NOW)
    prompt.text = "Ingatan baru."
    brain.respond("nyalakan lampu", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert prompt.reads == 1
    assert [request["system"] for request in client.requests] == ["Ingatan lama."] * 3


def test_tools_and_every_other_request_field_stay_fixed_across_sessions():
    prompt = SessionPrompt("Sesi satu.")
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"), message([text("Hai!")], "end_turn")
    )
    brain = make_brain(client, system_prompt=prompt, providers=[GameTools()])

    brain.respond("halo", now=NOW)
    prompt.text = "Sesi dua."
    brain.reset()
    brain.respond("hai", now=NOW)

    first, second = (
        {key: value for key, value in request.items() if key not in {"system", "messages"}}
        for request in client.requests
    )
    assert second == first
    assert [tool["name"] for tool in second["tools"]] == [
        "control_device",
        "list_devices",
        "game_status",
    ]


# A rolled-back turn that changed a note


def memory_brain(client: FakeAnthropic, store: MemoryStore) -> ClaudeBrain:
    """A brain with her memory tools whose sessions list the notes in `store`."""
    return make_brain(
        client,
        system_prompt=lambda: memory_prompt_section(store.facts()),
        providers=[MemoryTools(store)],
    )


def test_rolled_back_turn_that_saved_a_note_ends_the_session_so_the_next_one_lists_it(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"),
        message([tool_use("call_1", "remember", {"text": "Master alergi udang."})], "tool_use"),
        connection_error(),
        message([text("Oke.")], "end_turn"),
    )
    brain = memory_brain(client, store)

    brain.respond("halo", now=NOW)
    reply = brain.respond("ingat ya, aku alergi udang", now=NOW)
    brain.respond("lupakan soal udang", now=NOW)

    assert "tidak bisa terhubung" in reply.text
    assert [(call.name, call.is_error) for call in reply.tool_calls] == [("remember", False)]
    [saved] = store.facts()
    assert saved.text == "Master alergi udang."
    assert client.requests[3]["system"] == memory_prompt_section([saved])
    assert client.requests[3]["messages"] == [
        {"role": "user", "content": format_user_turn("lupakan soal udang", NOW)}
    ]


def test_rolled_back_turn_that_deleted_a_note_ends_the_session_so_the_next_one_drops_it(
    tmp_path,
):
    store = MemoryStore(tmp_path / "memory.json")
    store.add("Master alergi udang.")
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"),
        message([tool_use("call_1", "forget", {"id": "m1"})], "tool_use"),
        request_error(anthropic.RateLimitError, 429),
        message([text("Oke.")], "end_turn"),
    )
    brain = memory_brain(client, store)

    brain.respond("halo", now=NOW)
    brain.respond("lupakan soal udang", now=NOW)
    brain.respond("apa yang kamu ingat?", now=NOW)

    assert "Master alergi udang." in client.requests[0]["system"]
    assert store.facts() == []
    assert client.requests[3]["system"] == memory_prompt_section([])
    assert client.requests[3]["messages"] == [
        {"role": "user", "content": format_user_turn("apa yang kamu ingat?", NOW)}
    ]


def test_rolled_back_turn_whose_note_was_refused_keeps_the_session(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    client = FakeAnthropic(
        message([text("Halo!")], "end_turn"),
        message([tool_use("call_1", "remember", {"text": "PIN ATM Master 4321"})], "tool_use"),
        connection_error(),
        message([text("Oke.")], "end_turn"),
    )
    brain = memory_brain(client, store)

    brain.respond("halo", now=NOW)
    reply = brain.respond("catat PIN ATM-ku 4321", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert [(call.name, call.is_error) for call in reply.tool_calls] == [("remember", True)]
    third = client.requests[3]["messages"]
    assert [entry["role"] for entry in third] == ["user", "assistant", "user"]
    assert third[2]["content"] == format_user_turn("coba lagi", NOW)
