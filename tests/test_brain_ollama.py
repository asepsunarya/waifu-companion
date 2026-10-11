import copy
from datetime import datetime
from types import SimpleNamespace

import httpx
import ollama

from waifu.brain.base import ToolCallRecord
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import format_user_turn
from waifu.brain.tools import build_tool_specs, to_ollama
from waifu.config import BrainConfig, OllamaConfig
from waifu.devices.base import Device, DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry

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


def brain_with(client: FakeOllama, registry: DeviceRegistry | None = None, **kwargs) -> OllamaBrain:
    return OllamaBrain(BrainConfig(), registry or house(), SYSTEM_PROMPT, client=client, **kwargs)


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


def test_tool_call_record_matches_the_brain_contract():
    client = FakeOllama(reply("", call("list_devices")), reply("Ini daftarnya."))

    record = brain_with(client).respond("ada perangkat apa?", now=NOW).tool_calls[0]

    assert isinstance(record, ToolCallRecord)
    assert record.name == "list_devices"
    assert record.arguments == {}
    assert not record.is_error
    assert '"id":"lampu_kamar"' in record.result
