import copy
import json
import logging
from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace

import httpx
import ollama
import pytest

from waifu.brain.base import ToolCallRecord, ToolSpec
from waifu.brain.clipboard_tools import ClipboardTools
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import format_user_turn
from waifu.brain.schedule_tools import ScheduleTools
from waifu.brain.tools import build_tool_specs, to_ollama
from waifu.clipboard import TEXT, ClipboardRead
from waifu.config import BrainConfig, OllamaConfig
from waifu.devices.base import Device, DeviceResult
from waifu.devices.media import MediaKeys
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.memory import MemoryStore, memory_prompt_section
from waifu.schedule import ScheduleStore

SYSTEM_PROMPT = "Kamu adalah Cyrene."
NOW = datetime(2026, 10, 12, 7, 30)
ROUND_LIMIT_APOLOGY = (
    "Maaf, Master, permintaannya butuh terlalu banyak langkah, jadi aku berhenti dulu."
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeOllama:
    """Stands in for ollama.Client: returns queued replies (or raises queued errors)."""

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def chat(self, model="", messages=None, **kwargs):
        for message in messages:
            # The real client validates every message into ollama.Message before sending.
            ollama.Message.model_validate(message)
        self.requests.append({"model": model, "messages": copy.deepcopy(messages), **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(content: str = "", *calls: ollama.Message.ToolCall) -> ollama.ChatResponse:
    return ollama.ChatResponse(
        model="qwen3:8b",
        message=ollama.Message(role="assistant", content=content, tool_calls=list(calls) or None),
    )


def call(name: str, **arguments: object) -> ollama.Message.ToolCall:
    return ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(name=name, arguments=arguments)
    )


def house() -> DeviceRegistry:
    return DeviceRegistry(
        [
            MockDevice("lampu_kamar", "Lampu kamar", aliases=["lampu"], kind="light"),
            MockDevice("ac", "AC Samsung", commands=["on", "off", "temp_24", "temp_25"], kind="ir"),
        ]
    )


class UnreachableTv(Device):
    def __init__(self) -> None:
        super().__init__("tv", "TV")

    def commands(self) -> list[str]:
        return ["on", "off"]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        return DeviceResult(ok=False, message="TV tidak bisa dihubungi.")


def brain_with(
    client: FakeOllama,
    registry: DeviceRegistry | None = None,
    *,
    system_prompt: str | Callable[[], str] = SYSTEM_PROMPT,
    **kwargs,
) -> OllamaBrain:
    return OllamaBrain(BrainConfig(), registry or house(), system_prompt, client=client, **kwargs)


# Requests


def test_plain_reply_is_returned_as_the_spoken_text():
    brain = brain_with(FakeOllama(reply("はい, Master! Ada yang bisa kubantu?")))

    result = brain.respond("halo", now=NOW)

    assert result.text == "はい, Master! Ada yang bisa kubantu?"
    assert result.tool_calls == []


def test_request_sends_system_prompt_then_the_time_stamped_user_turn():
    client = FakeOllama(reply("Selamat pagi!"))

    brain_with(client).respond("selamat pagi", now=NOW)

    assert client.requests[0]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_user_turn("selamat pagi", NOW)},
    ]


def test_request_carries_configured_model_think_keep_alive_and_house_tools():
    client = FakeOllama(reply("Oke."))
    registry = house()
    config = BrainConfig(ollama=OllamaConfig(model="llama3.2", think=True, keep_alive="5m"))

    OllamaBrain(config, registry, SYSTEM_PROMPT, client=client).respond("halo", now=NOW)

    request = client.requests[0]
    assert request["model"] == "llama3.2"
    assert request["think"] is True
    assert request["keep_alive"] == "5m"
    assert request["tools"] == [to_ollama(spec) for spec in build_tool_specs(registry)]


def test_default_client_is_created_once_on_first_use_with_the_configured_host(monkeypatch):
    created: list[str] = []
    client = FakeOllama(reply("Satu."), reply("Dua."))

    def fake_client_class(host: str, **kwargs: object) -> FakeOllama:
        created.append(host)
        return client

    monkeypatch.setattr(ollama, "Client", fake_client_class)
    config = BrainConfig(ollama=OllamaConfig(host="http://192.168.1.5:11434"))
    brain = OllamaBrain(config, house(), SYSTEM_PROMPT)
    assert created == []

    brain.respond("satu", now=NOW)
    brain.respond("dua", now=NOW)

    assert created == ["http://192.168.1.5:11434"]


def test_default_client_bounds_each_request_so_a_hung_server_cannot_block_forever(monkeypatch):
    sent_timeouts: list[dict] = []
    real_client_class = ollama.Client

    def handler(request: httpx.Request) -> httpx.Response:
        sent_timeouts.append(request.extensions["timeout"])
        answer = {"role": "assistant", "content": "Halo, Master!"}
        return httpx.Response(200, json={"model": "qwen3:8b", "message": answer, "done": True})

    def build(host: str, **kwargs: object) -> ollama.Client:
        return real_client_class(host, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ollama, "Client", build)

    result = OllamaBrain(BrainConfig(), house(), SYSTEM_PROMPT).respond("halo", now=NOW)

    assert result.text == "Halo, Master!"
    (timeout,) = sent_timeouts
    assert None not in timeout.values()
    assert timeout["read"] <= 120


# Tool loop


def test_two_tool_calls_run_on_the_devices_and_the_final_text_is_returned():
    registry = house()
    client = FakeOllama(
        reply(
            "",
            call("control_device", device="lampu_kamar", command="on"),
            call("control_device", device="ac", command="temp", value=24),
        ),
        reply("Lampu dan AC sudah kuatur, Master!"),
    )

    result = brain_with(client, registry).respond("nyalakan lampu dan AC 24", now=NOW)

    assert registry.get("lampu_kamar").calls == [("on", None)]
    assert registry.get("ac").calls == [("temp_24", 24)]
    assert result.text == "Lampu dan AC sudah kuatur, Master!"
    assert [(r.name, r.arguments, r.is_error) for r in result.tool_calls] == [
        ("control_device", {"device": "lampu_kamar", "command": "on"}, False),
        ("control_device", {"device": "ac", "command": "temp", "value": 24}, False),
    ]


def test_tool_round_appends_the_assistant_call_then_one_tool_message_per_call():
    client = FakeOllama(
        reply(
            "Sebentar ya.",
            call("control_device", device="lampu_kamar", command="on"),
            call("list_devices"),
        ),
        reply("Sudah!"),
    )

    result = brain_with(client).respond("nyalakan lampu", now=NOW)

    lamp_result, devices_result = (record.result for record in result.tool_calls)
    assert client.requests[1]["messages"][2:] == [
        {
            "role": "assistant",
            "content": "Sebentar ya.",
            "tool_calls": [
                {
                    "function": {
                        "name": "control_device",
                        "arguments": {"device": "lampu_kamar", "command": "on"},
                    }
                },
                {"function": {"name": "list_devices", "arguments": {}}},
            ],
        },
        {"role": "tool", "content": lamp_result, "tool_name": "control_device"},
        {"role": "tool", "content": devices_result, "tool_name": "list_devices"},
    ]


def test_tool_arguments_sent_as_a_json_string_are_parsed():
    registry = house()
    string_call = SimpleNamespace(
        function=SimpleNamespace(
            name="control_device", arguments='{"device": "lampu_kamar", "command": "off"}'
        )
    )
    client = FakeOllama(
        SimpleNamespace(
            message=SimpleNamespace(role="assistant", content="", tool_calls=[string_call])
        ),
        reply("Lampunya sudah mati."),
    )

    result = brain_with(client, registry).respond("matikan lampu", now=NOW)

    assert registry.get("lampu_kamar").calls == [("off", None)]
    assert result.tool_calls[0].arguments == {"device": "lampu_kamar", "command": "off"}
    assert client.requests[1]["messages"][2]["tool_calls"] == [
        {
            "function": {
                "name": "control_device",
                "arguments": {"device": "lampu_kamar", "command": "off"},
            }
        }
    ]


def test_unparseable_string_arguments_become_a_tool_error_not_a_crash():
    broken_call = SimpleNamespace(
        function=SimpleNamespace(name="control_device", arguments="{device: lampu")
    )
    client = FakeOllama(
        SimpleNamespace(
            message=SimpleNamespace(role="assistant", content="", tool_calls=[broken_call])
        ),
        reply("Maaf, aku salah ketik."),
    )

    result = brain_with(client).respond("nyalakan lampu", now=NOW)

    assert result.tool_calls[0].is_error
    assert result.text == "Maaf, aku salah ketik."


def test_tool_rounds_stop_at_the_configured_limit_with_an_apology():
    registry = house()
    endless = [reply("", call("control_device", device="lampu_kamar", command="on"))] * 5
    client = FakeOllama(*endless)
    brain = OllamaBrain(BrainConfig(max_tool_rounds=2), registry, SYSTEM_PROMPT, client=client)

    result = brain.respond("nyalakan lampu terus", now=NOW)

    assert len(client.requests) == 3
    assert len(registry.get("lampu_kamar").calls) == 2
    assert result.text == ROUND_LIMIT_APOLOGY


def test_call_left_pending_at_the_round_limit_is_never_confirmed_as_done():
    registry = house()
    client = FakeOllama(
        reply("", call("list_devices")),
        reply("", call("list_devices")),
        reply("", call("control_device", device="lampu_kamar", command="on")),
        reply("Halo!"),
    )
    brain = OllamaBrain(BrainConfig(max_tool_rounds=2), registry, SYSTEM_PROMPT, client=client)

    result = brain.respond("nyalakan lampu", now=NOW)
    brain.respond("halo", now=NOW)

    assert registry.get("lampu_kamar").calls == []
    assert [record.name for record in result.tool_calls] == ["list_devices", "list_devices"]
    assert result.text == ROUND_LIMIT_APOLOGY
    assert client.requests[3]["messages"][-2] == {
        "role": "assistant",
        "content": ROUND_LIMIT_APOLOGY,
    }


def test_text_sent_with_a_call_past_the_round_limit_is_not_spoken():
    registry = house()
    client = FakeOllama(
        reply("", call("control_device", device="lampu_kamar", command="on")),
        reply(
            "Baik, Master, AC-nya aku matikan.", call("control_device", device="ac", command="off")
        ),
    )
    brain = OllamaBrain(BrainConfig(max_tool_rounds=1), registry, SYSTEM_PROMPT, client=client)

    result = brain.respond("matikan semua", now=NOW)

    assert registry.get("ac").calls == []
    assert result.text == ROUND_LIMIT_APOLOGY


# Final text


def test_think_blocks_are_stripped_from_the_reply():
    client = FakeOllama(reply("<think>\nMaster mau lampu.\n</think>\n\nSudah nyala, Master!"))

    assert brain_with(client).respond("lampu", now=NOW).text == "Sudah nyala, Master!"


def test_text_before_a_dangling_closing_think_tag_is_dropped():
    client = FakeOllama(reply("Master minta lampu, panggil tool.</think>Oke, Master!"))

    assert brain_with(client).respond("lampu", now=NOW).text == "Oke, Master!"


def test_unclosed_think_block_is_never_spoken():
    client = FakeOllama(reply("Siap!<think>hmm, apakah perlu"))

    assert brain_with(client).respond("lampu", now=NOW).text == "Siap!"


def test_empty_reply_after_successful_tools_is_a_short_confirmation():
    client = FakeOllama(
        reply("", call("control_device", device="lampu_kamar", command="on")),
        reply("<think>selesai</think>"),
    )

    assert brain_with(client).respond("nyalakan lampu", now=NOW).text == "Sudah, Master!"


def test_empty_reply_after_a_failed_tool_apologises_with_the_device_message():
    registry = DeviceRegistry([UnreachableTv()])
    client = FakeOllama(reply("", call("control_device", device="tv", command="on")), reply(""))

    result = brain_with(client, registry).respond("nyalakan TV", now=NOW)

    assert result.text.startswith("Maaf, Master")
    assert "TV tidak bisa dihubungi." in result.text


def test_empty_reply_without_tools_asks_the_owner_to_repeat():
    result = brain_with(FakeOllama(reply(""))).respond("hmm", now=NOW)

    assert result.text.startswith("Maaf, Master")


# History and sessions


def test_next_turn_carries_the_previous_turn_including_tool_messages():
    client = FakeOllama(
        reply("", call("control_device", device="lampu_kamar", command="on")),
        reply("<think>ok</think>Lampu nyala!"),
        reply("Sama-sama!"),
    )
    brain = brain_with(client)

    brain.respond("nyalakan lampu", now=NOW)
    brain.respond("makasih", now=NOW)

    messages = client.requests[2]["messages"]
    roles = [message["role"] for message in messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert messages[4] == {"role": "assistant", "content": "Lampu nyala!"}
    assert messages[5] == {"role": "user", "content": format_user_turn("makasih", NOW)}


def test_idle_session_starts_fresh_with_only_the_system_prompt():
    clock = FakeClock()
    client = FakeOllama(reply("Halo!"), reply("Halo lagi!"))
    brain = brain_with(client, clock=clock, session_idle_minutes=10)

    brain.respond("halo", now=NOW)
    clock.now += 11 * 60
    brain.respond("halo lagi", now=NOW)

    assert client.requests[1]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_user_turn("halo lagi", NOW)},
    ]


def test_turn_cap_starts_a_fresh_session():
    client = FakeOllama(reply("Satu."), reply("Dua."))
    brain = brain_with(client, clock=FakeClock(), max_session_turns=1)

    brain.respond("satu", now=NOW)
    brain.respond("dua", now=NOW)

    assert len(client.requests[1]["messages"]) == 2


def test_reset_forgets_the_conversation():
    client = FakeOllama(reply("Halo!"), reply("Siapa ya?"))
    brain = brain_with(client)

    brain.respond("aku Master", now=NOW)
    brain.reset()
    brain.respond("aku siapa?", now=NOW)

    assert client.requests[1]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_user_turn("aku siapa?", NOW)},
    ]


# Failures


def test_connection_refused_apologises_with_how_to_start_ollama():
    refused = ConnectionError("Failed to connect to Ollama.")
    brain = brain_with(FakeOllama(refused))

    result = brain.respond("nyalakan lampu", now=NOW)

    assert result.text.startswith("Maaf, Master")
    assert "Ollama belum jalan? Jalankan 'ollama serve' dan 'ollama pull qwen3:8b'" in result.text


def test_failed_turn_is_rolled_back_so_the_next_request_never_sees_it():
    client = FakeOllama(reply("Halo!"), ConnectionError("refused"), reply("Oke!"))
    brain = brain_with(client)

    brain.respond("halo", now=NOW)
    brain.respond("nyalakan lampu", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert client.requests[2]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": format_user_turn("halo", NOW)},
        {"role": "assistant", "content": "Halo!"},
        {"role": "user", "content": format_user_turn("coba lagi", NOW)},
    ]


def test_failure_after_a_tool_round_rolls_back_the_tool_messages_too():
    registry = house()
    client = FakeOllama(
        reply("", call("control_device", device="lampu_kamar", command="on")),
        ConnectionError("refused"),
        reply("Oke!"),
    )
    brain = brain_with(client, registry)

    result = brain.respond("nyalakan lampu", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert registry.get("lampu_kamar").calls == [("on", None)]
    assert [record.name for record in result.tool_calls] == ["control_device"]
    assert [m["role"] for m in client.requests[2]["messages"]] == ["system", "user"]


def test_request_timeout_apologises_that_ollama_is_slow_and_rolls_back_the_turn():
    client = FakeOllama(reply("Halo!"), httpx.ReadTimeout("timed out"), reply("Oke!"))
    brain = brain_with(client)

    brain.respond("halo", now=NOW)
    result = brain.respond("nyalakan lampu", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert result.text.startswith("Maaf, Master")
    assert "terlalu lama" in result.text
    roles = [message["role"] for message in client.requests[2]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_missing_model_apologises_with_how_to_pull_it():
    missing = ollama.ResponseError('{"error":"model \\"qwen3:8b\\" not found"}', 404)

    result = brain_with(FakeOllama(missing)).respond("halo", now=NOW)

    assert result.text.startswith("Maaf, Master")
    assert "ollama pull qwen3:8b" in result.text


def test_other_ollama_error_apologises_without_a_pull_hint():
    overloaded = ollama.ResponseError("server busy", 503)

    result = brain_with(FakeOllama(overloaded)).respond("halo", now=NOW)

    assert result.text.startswith("Maaf, Master")
    assert "ollama pull" not in result.text


def test_unexpected_error_becomes_an_apology_instead_of_escaping():
    result = brain_with(FakeOllama(RuntimeError("boom"))).respond("halo", now=NOW)

    assert result.text.startswith("Maaf, Master")
    assert result.tool_calls == []


LAMP_ON = call("control_device", device="lampu_kamar", command="on")


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        ([reply("")], "Maaf, Sayang, aku bingung mau jawab apa. Coba ulangi ya?"),
        ([reply("", LAMP_ON), reply("")], "Sudah, Sayang!"),
        (
            [reply("", call("control_device", device="tv", command="on")), reply("")],
            "Maaf, Sayang, ada yang gagal. TV tidak bisa dihubungi.",
        ),
        (
            [reply("", LAMP_ON)] * 6,
            "Maaf, Sayang, permintaannya butuh terlalu banyak langkah, jadi aku berhenti dulu.",
        ),
        (
            [ConnectionError("refused")],
            "Maaf, Sayang, aku tidak bisa terhubung ke Ollama. Ollama belum jalan? "
            "Jalankan 'ollama serve' dan 'ollama pull qwen3:8b'.",
        ),
        (
            [httpx.ReadTimeout("timed out")],
            "Maaf, Sayang, Ollama terlalu lama menjawab. Coba lagi sebentar ya.",
        ),
        (
            [ollama.ResponseError('{"error":"model \\"qwen3:8b\\" not found"}', 404)],
            "Maaf, Sayang, model qwen3:8b belum ada di Ollama. "
            "Jalankan 'ollama pull qwen3:8b' dulu ya.",
        ),
        (
            [ollama.ResponseError("server busy", 503)],
            "Maaf, Sayang, Ollama sedang bermasalah. Coba lagi sebentar ya.",
        ),
        ([RuntimeError("boom")], "Maaf, Sayang, ada yang salah di pikiranku. Coba ulangi ya."),
    ],
    ids=[
        "no_answer",
        "done",
        "some_failed",
        "round_limit",
        "unreachable",
        "too_slow",
        "missing_model",
        "trouble",
        "unexpected",
    ],
)
def test_every_canned_reply_calls_the_owner_what_she_calls_them(replies, expected):
    registry = DeviceRegistry([*house().all(), UnreachableTv()])
    brain = brain_with(FakeOllama(*replies), registry, owner_call="Sayang")

    assert brain.respond("halo", now=NOW).text == expected


@pytest.mark.parametrize(
    ("action", "error_part"),
    [
        ({"routine": "tidur"}, "Belum ada rutinitas; Sayang menulisnya di config.yaml."),
        ({"device": "musik", "command": "play_pause"}, "menyalakan suara saat Sayang tidur."),
    ],
    ids=["no_routines", "media_toggle"],
)
def test_an_empty_reply_reads_a_schedule_error_with_what_she_calls_the_owner(
    tmp_path, action, error_part
):
    pressed: list[list[int]] = []
    registry = DeviceRegistry([MediaKeys("musik", "Musik PC", ["musik"], press=pressed.append)])
    store = ScheduleStore(tmp_path / "schedule.json", now=lambda: NOW)
    schedule = ScheduleTools(store, registry=registry, routines={}, owner_call="Sayang")
    client = FakeOllama(reply("", call("schedule", at="2026-10-12 23:00", **action)), reply(""))
    brain = brain_with(client, registry, providers=[schedule], owner_call="Sayang")

    text = brain.respond("jadwalkan nanti malam", now=NOW).text

    assert text.startswith("Maaf, Sayang, ada yang gagal. ")
    assert error_part in text
    assert "Master" not in text
    assert store.entries() == []
    assert pressed == []


def test_tool_call_record_matches_the_brain_contract():
    client = FakeOllama(reply("", call("list_devices")), reply("Ini daftarnya."))

    record = brain_with(client).respond("ada perangkat apa?", now=NOW).tool_calls[0]

    assert isinstance(record, ToolCallRecord)
    assert record.name == "list_devices"
    assert record.arguments == {}
    assert not record.is_error
    assert '"id":"lampu_kamar"' in record.result


class GameTools:
    def specs(self) -> list[ToolSpec]:
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        return [ToolSpec("game_status", "Status of Master's games.", schema)]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        return json.dumps({"games": [{"game": "hsr", "stamina": {"current": 232}}]}), False


def test_provider_tools_are_offered_and_their_calls_answered():
    client = FakeOllama(reply("", call("game_status")), reply("TP-mu 232, Master!"))

    result = brain_with(client, providers=[GameTools()]).respond("TP-ku berapa?", now=NOW)

    names = [tool["function"]["name"] for tool in client.requests[0]["tools"]]
    assert names == ["control_device", "list_devices", "game_status"]
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_name"] == "game_status"
    assert json.loads(tool_message["content"])["games"][0]["stamina"]["current"] == 232
    assert [(c.name, c.is_error) for c in result.tool_calls] == [("game_status", False)]


# Per-session system prompt


class SessionPrompt:
    """A callable system prompt that counts its reads; `text` may change between them."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.reads = 0

    def __call__(self) -> str:
        self.reads += 1
        return self.text


def system_messages(client: FakeOllama) -> list[dict]:
    return [request["messages"][0] for request in client.requests]


def test_callable_system_prompt_is_read_once_for_every_turn_and_tool_round_of_a_session():
    prompt = SessionPrompt("Kamu Cyrene. Master alergi udang.")
    client = FakeOllama(
        reply("", call("control_device", device="lampu_kamar", command="on")),
        reply("Sudah."),
        reply("Halo!"),
    )
    brain = brain_with(client, system_prompt=prompt)

    brain.respond("nyalakan lampu", now=NOW)
    brain.respond("halo", now=NOW)

    assert prompt.reads == 1
    assert (
        system_messages(client)
        == [{"role": "system", "content": "Kamu Cyrene. Master alergi udang."}] * 3
    )


def test_callable_system_prompt_sends_the_same_requests_as_the_string():
    def lamp_on_then_done() -> FakeOllama:
        return FakeOllama(
            reply("", call("control_device", device="lampu_kamar", command="on")), reply("Sudah.")
        )

    with_string, with_callable = lamp_on_then_done(), lamp_on_then_done()

    brain_with(with_string).respond("nyalakan lampu", now=NOW)
    brain_with(with_callable, system_prompt=lambda: SYSTEM_PROMPT).respond(
        "nyalakan lampu", now=NOW
    )

    assert with_callable.requests == with_string.requests


def test_system_prompt_changed_mid_session_is_only_seen_by_the_next_session():
    prompt = SessionPrompt("Ingatan: belum ada.")
    client = FakeOllama(reply("Oke."), reply("Sama-sama."), reply("Nasi goreng?"))
    brain = brain_with(client, system_prompt=prompt)

    brain.respond("ingat ya, aku alergi udang", now=NOW)
    prompt.text = "Ingatan: Master alergi udang."
    brain.respond("makasih", now=NOW)
    brain.reset()
    brain.respond("enaknya masak apa?", now=NOW)

    assert [message["content"] for message in system_messages(client)] == [
        "Ingatan: belum ada.",
        "Ingatan: belum ada.",
        "Ingatan: Master alergi udang.",
    ]


def idle_too_long(brain: OllamaBrain, clock: FakeClock) -> None:
    clock.now += 11 * 60


def reached_the_turn_cap(brain: OllamaBrain, clock: FakeClock) -> None:
    """Nothing to do: with a cap of one turn, the first turn already ends the session."""


def reset_by_hand(brain: OllamaBrain, clock: FakeClock) -> None:
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
    client = FakeOllama(reply("Halo!"), reply("Hai!"))
    brain = brain_with(client, system_prompt=prompt, clock=clock, **limits)

    brain.respond("halo", now=NOW)
    prompt.text = "Sesi dua."
    end_session(brain, clock)
    brain.respond("hai", now=NOW)

    assert prompt.reads == 2
    assert client.requests[1]["messages"] == [
        {"role": "system", "content": "Sesi dua."},
        {"role": "user", "content": format_user_turn("hai", NOW)},
    ]


def test_building_the_brain_does_not_read_the_system_prompt():
    prompt = SessionPrompt("Ingatan lama.")

    brain_with(FakeOllama(), system_prompt=prompt)

    assert prompt.reads == 0


def test_the_first_session_reads_the_system_prompt_when_its_first_turn_begins():
    # `waifu run` builds the brain at login, maybe hours before the first conversation.
    prompt = SessionPrompt("Ingatan lama.")
    client = FakeOllama(reply("Halo!"))
    brain = brain_with(client, system_prompt=prompt)

    prompt.text = "Ingatan baru."
    brain.respond("halo", now=NOW)

    assert system_messages(client) == [{"role": "system", "content": "Ingatan baru."}]


def test_rolled_back_first_turn_reads_the_system_prompt_again_on_the_next_turn():
    prompt = SessionPrompt("Ingatan lama.")
    client = FakeOllama(ConnectionError("refused"), reply("Halo!"))
    brain = brain_with(client, system_prompt=prompt)

    brain.respond("halo", now=NOW)
    prompt.text = "Ingatan baru."
    brain.respond("halo lagi", now=NOW)

    assert prompt.reads == 2
    assert client.requests[1]["messages"] == [
        {"role": "system", "content": "Ingatan baru."},
        {"role": "user", "content": format_user_turn("halo lagi", NOW)},
    ]


def test_rolled_back_later_turn_keeps_the_session_system_prompt():
    prompt = SessionPrompt("Ingatan lama.")
    client = FakeOllama(reply("Halo!"), ConnectionError("refused"), reply("Oke."))
    brain = brain_with(client, system_prompt=prompt)

    brain.respond("halo", now=NOW)
    prompt.text = "Ingatan baru."
    brain.respond("nyalakan lampu", now=NOW)
    brain.respond("coba lagi", now=NOW)

    assert prompt.reads == 1
    assert [message["content"] for message in system_messages(client)] == ["Ingatan lama."] * 3


# A rolled-back turn that changed a note


def memory_brain(client: FakeOllama, store: MemoryStore) -> OllamaBrain:
    """A brain with her memory tools whose sessions list the notes in `store`."""
    return brain_with(
        client,
        system_prompt=lambda: memory_prompt_section(store.facts()),
        providers=[MemoryTools(store)],
    )


def test_rolled_back_turn_that_saved_a_note_ends_the_session_so_the_next_one_lists_it(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")
    client = FakeOllama(
        reply("Halo!"),
        reply("", call("remember", text="Master alergi udang.")),
        ConnectionError("refused"),
        reply("Oke."),
    )
    brain = memory_brain(client, store)

    brain.respond("halo", now=NOW)
    result = brain.respond("ingat ya, aku alergi udang", now=NOW)
    brain.respond("lupakan soal udang", now=NOW)

    assert [(record.name, record.is_error) for record in result.tool_calls] == [("remember", False)]
    [saved] = store.facts()
    assert saved.text == "Master alergi udang."
    assert client.requests[3]["messages"] == [
        {"role": "system", "content": memory_prompt_section([saved])},
        {"role": "user", "content": format_user_turn("lupakan soal udang", NOW)},
    ]


def test_rolled_back_turn_that_deleted_a_note_ends_the_session_so_the_next_one_drops_it(
    tmp_path,
):
    store = MemoryStore(tmp_path / "memory.json")
    store.add("Master alergi udang.")
    client = FakeOllama(
        reply("Halo!"),
        reply("", call("forget", id="m1")),
        httpx.ReadTimeout("timed out"),
        reply("Oke."),
    )
    brain = memory_brain(client, store)

    brain.respond("halo", now=NOW)
    brain.respond("lupakan soal udang", now=NOW)
    brain.respond("apa yang kamu ingat?", now=NOW)

    assert "Master alergi udang." in system_messages(client)[0]["content"]
    assert store.facts() == []
    assert client.requests[3]["messages"] == [
        {"role": "system", "content": memory_prompt_section([])},
        {"role": "user", "content": format_user_turn("apa yang kamu ingat?", NOW)},
    ]


# Master's yes before the PC turns off


SHUTDOWN_QUESTION = "Komputer mau kumatikan sekarang, Master? Jawab iya kalau jadi."
PLUG_QUESTION = (
    "Colokan PC mau kumatikan sekarang, Master? Listrik PC bisa langsung putus. "
    "Jawab iya kalau jadi."
)


def pc_house() -> DeviceRegistry:
    """Mock devices only: the PC, the plug that feeds it, and a desk lamp."""
    return DeviceRegistry(
        [
            MockDevice("komputer", "Komputer", commands=["shutdown", "cancel_shutdown"], kind="pc"),
            MockDevice("colokan_pc", "Colokan PC", commands=["on", "off"], kind="plug"),
            MockDevice("lampu_meja", "Lampu meja", commands=["on", "off"], kind="light"),
        ],
        pc_power_ids={"colokan_pc"},
    )


def shutdown() -> ollama.ChatResponse:
    return reply("", call("control_device", device="komputer", command="shutdown"))


def gated_brain(client: FakeOllama, registry: DeviceRegistry, **kwargs) -> OllamaBrain:
    return brain_with(client, registry, confirmations=ConfirmationGate(clock=FakeClock()), **kwargs)


def test_turning_the_pc_off_is_held_until_master_says_yes_in_the_next_turn():
    registry = pc_house()
    client = FakeOllama(
        shutdown(),
        reply("Baik, Master."),
        shutdown(),
        reply("Komputer mati dalam 30 detik, Master."),
    )
    brain = gated_brain(client, registry)

    asked = brain.respond("Cyrene, matikan komputer.", now=NOW)

    assert registry.get("komputer").calls == []
    held = client.requests[1]["messages"][-1]
    assert held["role"] == "tool"
    assert json.loads(held["content"])["needs_confirmation"] is True
    assert asked.text == f"Baik, Master. {SHUTDOWN_QUESTION}"
    assert [record.is_error for record in asked.tool_calls] == [True]

    done = brain.respond("iya", now=NOW)

    assert registry.get("komputer").calls == [("shutdown", None)]
    assert [record.is_error for record in done.tool_calls] == [False]
    # The model sees the question it was heard asking.
    assert client.requests[2]["messages"][4] == {"role": "assistant", "content": asked.text}


@pytest.mark.parametrize(
    ("device", "command", "question"),
    [("komputer", "shutdown", SHUTDOWN_QUESTION), ("colokan_pc", "off", PLUG_QUESTION)],
    ids=["pc", "its_plug"],
)
def test_a_model_that_skips_the_question_still_asks_it_and_thanks_is_no_yes(
    device, command, question
):
    registry = pc_house()
    client = FakeOllama(
        reply(
            "",
            call("control_device", device="lampu_meja", command="off"),
            call("control_device", device=device, command=command),
        ),
        reply("Lampu meja sudah mati, Master."),
        reply("", call("control_device", device=device, command=command)),
        reply("Sama-sama, Master."),
    )
    brain = gated_brain(client, registry)

    first = brain.respond("Cyrene, matikan semua di meja.", now=NOW)
    second = brain.respond("oke makasih ya", now=NOW)

    assert first.text == f"Lampu meja sudah mati, Master. {question}"
    assert registry.get(device).calls == []
    assert [record.is_error for record in second.tool_calls] == [True]


def test_a_rolled_back_turn_drops_its_request_because_master_never_heard_the_question():
    registry = pc_house()
    client = FakeOllama(shutdown(), ConnectionError("refused"), shutdown(), reply("Baik, Master."))
    brain = gated_brain(client, registry)

    apology = brain.respond("Cyrene, matikan lampu meja komputer.", now=NOW)
    retry = brain.respond("iya", now=NOW)

    assert apology.text.startswith("Maaf, Master")
    assert "Jawab iya" not in apology.text
    assert registry.get("komputer").calls == []
    [held] = retry.tool_calls
    assert json.loads(held.result)["needs_confirmation"] is True
    assert retry.text == f"Baik, Master. {SHUTDOWN_QUESTION}"


@pytest.mark.parametrize(
    ("ending", "max_tool_rounds", "spoken"),
    [
        (reply("<think>tanya Master</think>"), 5, SHUTDOWN_QUESTION),
        (shutdown(), 1, f"{ROUND_LIMIT_APOLOGY} {SHUTDOWN_QUESTION}"),
    ],
    ids=["empty_reply", "round_limit"],
)
def test_a_canned_reply_after_a_held_call_still_asks_and_a_yes_then_runs_it(
    ending, max_tool_rounds, spoken
):
    registry = pc_house()
    client = FakeOllama(
        shutdown(), ending, shutdown(), reply("Komputer mati dalam 30 detik, Master.")
    )
    brain = OllamaBrain(
        BrainConfig(max_tool_rounds=max_tool_rounds),
        registry,
        SYSTEM_PROMPT,
        client=client,
        confirmations=ConfirmationGate(clock=FakeClock()),
    )

    asked = brain.respond("Cyrene, matikan komputer.", now=NOW)
    brain.respond("iya", now=NOW)

    assert asked.text == spoken
    assert registry.get("komputer").calls == [("shutdown", None)]


def test_an_empty_reply_after_a_held_call_never_reads_the_models_instructions_aloud():
    registry = pc_house()
    client = FakeOllama(
        reply(
            "",
            call("control_device", device="lampu_meja", command="off"),
            call("control_device", device="komputer", command="shutdown"),
        ),
        reply(""),
    )

    result = gated_brain(client, registry).respond("matikan semua di meja", now=NOW)

    assert result.text == f"Sudah, Master! {SHUTDOWN_QUESTION}"


def test_a_session_at_its_turn_cap_waits_for_the_answer_to_its_question():
    registry = pc_house()
    client = FakeOllama(
        shutdown(),
        reply("Baik, Master."),
        shutdown(),
        reply("Komputer mati dalam 30 detik, Master."),
        reply("Halo!"),
    )
    brain = gated_brain(client, registry, clock=FakeClock(), max_session_turns=1)

    brain.respond("Cyrene, matikan komputer.", now=NOW)
    brain.respond("iya", now=NOW)
    brain.respond("halo", now=NOW)

    assert registry.get("komputer").calls == [("shutdown", None)]
    assert client.requests[2]["messages"][1] == {
        "role": "user",
        "content": format_user_turn("Cyrene, matikan komputer.", NOW),
    }
    assert len(client.requests[4]["messages"]) == 2


class Marker:
    """A session marker like guest mode's generation, moved on by the test."""

    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        return self.value


def test_what_master_copied_never_reaches_the_log_even_for_debugging(caplog):
    client = FakeOllama(reply("", call("read_clipboard")), reply("Artinya selamat pagi."))
    clipboard = ClipboardTools(read=lambda max_chars: ClipboardRead(TEXT, "COPIEDSENTINEL"))
    brain = brain_with(client, providers=[clipboard])

    with caplog.at_level(logging.DEBUG, logger="waifu.brain.ollama_brain"):
        result = brain.respond("artinya apa yang kucopy?", now=NOW)

    assert "COPIEDSENTINEL" in result.tool_calls[0].result  # the model still gets it
    assert "COPIEDSENTINEL" not in caplog.text
    assert "characters)" in caplog.text


def test_a_moved_session_marker_starts_a_new_session_before_the_next_turn(caplog):
    marker = Marker()
    prompt = SessionPrompt("Ingatan: Master alergi udang.")
    client = FakeOllama(reply("Halo!"), reply("Hai!"))
    brain = brain_with(client, system_prompt=prompt, session_marker=marker)

    brain.respond("halo", now=NOW)
    marker.value += 1  # guest mode turned on
    prompt.text = "Mode tamu sedang aktif."
    with caplog.at_level(logging.INFO, logger="waifu.brain.ollama_brain"):
        brain.respond("hai", now=NOW)

    assert client.requests[1]["messages"] == [
        {"role": "system", "content": "Mode tamu sedang aktif."},
        {"role": "user", "content": format_user_turn("hai", NOW)},
    ]
    assert "Guest mode was turned on or off; starting a new session" in caplog.text


def test_a_session_marker_that_stays_keeps_the_session():
    client = FakeOllama(reply("Halo!"), reply("Hai!"))
    brain = brain_with(client, session_marker=Marker())

    brain.respond("halo", now=NOW)
    brain.respond("hai", now=NOW)

    assert len(client.requests[1]["messages"]) == 4


def test_the_session_marker_is_read_before_the_session_s_prompt():
    marker = Marker()

    def prompt() -> str:
        marker.value += 1
        return SYSTEM_PROMPT

    client = FakeOllama(reply("Halo!"), reply("Hai!"))
    brain = brain_with(client, system_prompt=prompt, session_marker=marker)

    brain.respond("halo", now=NOW)
    brain.respond("hai", now=NOW)

    assert len(client.requests[1]["messages"]) == 2


def test_a_moved_session_marker_ends_the_session_even_while_a_question_awaits_its_yes():
    registry = pc_house()
    marker = Marker()
    client = FakeOllama(shutdown(), reply("Baik, Master."), shutdown(), reply(""))
    brain = gated_brain(client, registry, session_marker=marker)

    brain.respond("Cyrene, matikan komputer.", now=NOW)
    marker.value += 1
    answer = brain.respond("iya", now=NOW)

    assert client.requests[2]["messages"][1:] == [
        {"role": "user", "content": format_user_turn("iya", NOW)}
    ]
    assert registry.get("komputer").calls == []
    [held] = answer.tool_calls
    assert json.loads(held.result)["needs_confirmation"] is True


def test_the_tool_refusal_refuses_a_call_before_it_runs():
    registry = house()
    client = FakeOllama(
        reply("", call("control_device", device="lampu_kamar", command="on")), reply("Maaf.")
    )
    brain = brain_with(
        client,
        registry,
        tool_refusal=lambda name: "Mode tamu sedang aktif." if name == "control_device" else None,
    )

    result = brain.respond("nyalakan lampu", now=NOW)

    assert registry.get("lampu_kamar").calls == []
    refused = client.requests[1]["messages"][-1]
    assert json.loads(refused["content"])["message"] == "Mode tamu sedang aktif."
    assert [record.is_error for record in result.tool_calls] == [True]


def test_a_no_keeps_the_pc_on_even_when_the_model_calls_again():
    registry = pc_house()
    client = FakeOllama(shutdown(), reply("Yakin, Master?"), shutdown(), reply("Baik, tidak jadi."))
    brain = gated_brain(client, registry)

    brain.respond("Cyrene, matikan komputer.", now=NOW)
    result = brain.respond("tidak", now=NOW)

    assert registry.get("komputer").calls == []
    assert [record.is_error for record in result.tool_calls] == [True]


def test_retrying_in_the_same_turn_is_held_again_even_after_a_yes_in_that_turn():
    registry = pc_house()
    client = FakeOllama(shutdown(), shutdown(), reply("Yakin, Master?"))

    result = gated_brain(client, registry).respond("iya, matikan komputer sekarang", now=NOW)

    assert registry.get("komputer").calls == []
    assert [record.is_error for record in result.tool_calls] == [True, True]


def test_a_rolled_back_turn_still_counts_so_a_yes_after_it_comes_too_late():
    registry = pc_house()
    client = FakeOllama(
        shutdown(),
        reply("Yakin, Master?"),
        ConnectionError("refused"),
        shutdown(),
        reply("Yakin, Master?"),
    )
    brain = gated_brain(client, registry)

    brain.respond("Cyrene, matikan komputer.", now=NOW)
    brain.respond("iya", now=NOW)
    result = brain.respond("iya", now=NOW)

    assert registry.get("komputer").calls == []
    assert [record.is_error for record in result.tool_calls] == [True]


# Provider hooks


class TurnListener:
    """A provider with only the optional begin_turn hook; records what each turn heard."""

    def __init__(self, client: FakeOllama) -> None:
        self.client = client
        self.heard: list[tuple[str, int]] = []

    def specs(self) -> list[ToolSpec]:
        return []

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        raise AssertionError("it has no tools")

    def begin_turn(self, user_text: str) -> None:
        self.heard.append((user_text, len(self.client.requests)))


def test_providers_hear_each_turn_as_said_before_its_first_request_even_one_rolled_back():
    client = FakeOllama(ConnectionError("refused"), reply("Halo!"))
    listener = TurnListener(client)
    brain = brain_with(client, providers=[listener])

    brain.respond("Cyrene, lihat layarku", now=NOW)
    brain.respond("halo", now=NOW)

    assert listener.heard == [("Cyrene, lihat layarku", 0), ("halo", 1)]
