import base64
import copy
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import ollama
import pytest

from waifu.brain.attachments import TurnImages
from waifu.brain.base import Brain, BrainReply, ToolImage, ToolSpec
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.factory import GUEST_SECTION, build_brain, confirmation_gate
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import build_system_prompt
from waifu.config import (
    AppConfig,
    AssistantConfig,
    BrainConfig,
    BriefingConfig,
    ClaudeConfig,
    ConfigError,
    OllamaConfig,
    WebSearchConfig,
)
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.guest import GUEST_REFUSAL, GuestMode
from waifu.usage import TokenUsage

NOW = datetime(2026, 10, 12, 7, 30)
PERSONA = "Kamu adalah {name}, penjaga rumah yang ceria."


class FakeOllama:
    """Stands in for ollama.Client and answers every chat with the same short reply."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.requests: list[dict] = []

    def chat(self, model="", messages=None, **kwargs) -> ollama.ChatResponse:
        self.requests.append({"model": model, "messages": copy.deepcopy(messages), **kwargs})
        return ollama.ChatResponse(message=ollama.Message(role="assistant", content="Halo!"))


@pytest.fixture
def ollama_clients(monkeypatch) -> list[FakeOllama]:
    created: list[FakeOllama] = []

    def fake_client_class(host: str, **kwargs: object) -> FakeOllama:
        created.append(FakeOllama(host))
        return created[-1]

    monkeypatch.setattr(ollama, "Client", fake_client_class)
    return created


class FakeClaudeBrain(Brain):
    """Takes ClaudeBrain's place with the same constructor signature."""

    def __init__(
        self,
        config,
        registry,
        system_prompt,
        *,
        client=None,
        clock=time.monotonic,
        session_idle_minutes=10.0,
        max_session_turns=40,
        providers=(),
        confirmations=None,
        owner_call="Master",
        on_usage=None,
        turn_images=None,
        web_search=None,
        tool_refusal=None,
        session_marker=None,
    ):
        self.config, self.registry, self.system_prompt = config, registry, system_prompt
        self.session_idle_minutes, self.max_session_turns = session_idle_minutes, max_session_turns
        self.providers = providers
        self.confirmations = confirmations
        self.owner_call = owner_call
        self.on_usage = on_usage
        self.turn_images = turn_images
        self.web_search = web_search
        self.tool_refusal = tool_refusal
        self.session_marker = session_marker

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        return BrainReply(text="")

    def reset(self) -> None:
        pass


def house() -> DeviceRegistry:
    return DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar", aliases=["lampu"])])


def test_ollama_backend_builds_an_ollama_brain_on_the_configured_host(ollama_clients):
    config = AppConfig(
        brain=BrainConfig(backend="ollama", ollama=OllamaConfig(host="http://10.0.0.2:11434"))
    )

    brain = build_brain(config, house(), persona_text=PERSONA)
    brain.respond("halo", now=NOW)

    assert isinstance(brain, OllamaBrain)
    assert [client.host for client in ollama_clients] == ["http://10.0.0.2:11434"]


def test_system_prompt_is_the_persona_with_the_assistant_name_and_the_devices(ollama_clients):
    registry = house()
    config = AppConfig(assistant=AssistantConfig(name="Hikari"))

    build_brain(config, registry, persona_text=PERSONA).respond("halo", now=NOW)

    system_message = ollama_clients[0].requests[0]["messages"][0]
    assert system_message == {
        "role": "system",
        "content": build_system_prompt(PERSONA, "Hikari", registry),
    }
    assert system_message["content"].startswith("Kamu adalah Hikari, penjaga rumah yang ceria.")


def test_persona_is_read_from_the_configured_file_when_not_given(tmp_path, ollama_clients):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("Kamu adalah {name}, おかえりなさい!", encoding="utf-8")
    config = AppConfig(assistant=AssistantConfig(name="Cyrene", persona_file=persona_file))

    build_brain(config, house()).respond("halo", now=NOW)

    system_prompt = ollama_clients[0].requests[0]["messages"][0]["content"]
    assert system_prompt.startswith("Kamu adalah Cyrene, おかえりなさい!")


def test_missing_persona_file_is_a_config_error(tmp_path):
    config = AppConfig(assistant=AssistantConfig(persona_file=tmp_path / "tidak_ada.md"))

    with pytest.raises(ConfigError, match="tidak_ada.md"):
        build_brain(config, house())


def test_ollama_brain_uses_the_session_turn_cap_from_the_assistant_config(ollama_clients):
    config = AppConfig(assistant=AssistantConfig(max_session_turns=1))
    brain = build_brain(config, house(), persona_text=PERSONA)

    brain.respond("satu", now=NOW)
    brain.respond("dua", now=NOW)

    second_request = ollama_clients[0].requests[1]["messages"]
    assert [message["role"] for message in second_request] == ["system", "user"]


def test_claude_backend_builds_a_claude_brain_with_the_brain_config_and_prompt(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    registry = house()
    config = AppConfig(
        assistant=AssistantConfig(name="Cyrene"), brain=BrainConfig(backend="claude")
    )

    brain = build_brain(config, registry, persona_text=PERSONA)

    assert isinstance(brain, FakeClaudeBrain)
    assert brain.config is config.brain
    assert brain.registry is registry
    # Web search is on by default, so the prompt ends with its note on web data.
    assert brain.system_prompt == build_system_prompt(PERSONA, "Cyrene", registry, web_search=True)


def test_claude_brain_uses_the_session_limits_from_the_assistant_config(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    config = AppConfig(
        assistant=AssistantConfig(session_idle_minutes=3, max_session_turns=7),
        brain=BrainConfig(backend="claude"),
    )

    brain = build_brain(config, house(), persona_text=PERSONA)

    assert (brain.session_idle_minutes, brain.max_session_turns) == (3, 7)


class GameTools:
    def specs(self) -> list[ToolSpec]:
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        return [ToolSpec("game_status", "Status of Master's games.", schema)]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        return "{}", False


def test_the_game_tools_reach_the_claude_brain(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    games = GameTools()

    brain = build_brain(
        AppConfig(brain=BrainConfig(backend="claude")),
        house(),
        persona_text=PERSONA,
        providers=[games],
    )

    assert list(brain.providers) == [games]


def test_the_game_tools_reach_the_ollama_brain(ollama_clients):
    build_brain(AppConfig(), house(), persona_text=PERSONA, providers=[GameTools()]).respond(
        "halo", now=NOW
    )

    tools = [tool["function"]["name"] for tool in ollama_clients[0].requests[0]["tools"]]
    assert tools == ["control_device", "list_devices", "game_status"]


# What each Claude response used (the API cost monitor)


class UsageRecorder:
    """Stands in for UsageLedger.record."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, TokenUsage]] = []

    def __call__(self, model: str, tokens: TokenUsage) -> None:
        self.calls.append((model, tokens))


def test_the_usage_hook_reaches_the_claude_brain(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    recorder = UsageRecorder()

    observed = build_brain(claude_config(), house(), persona_text=PERSONA, on_usage=recorder)
    unobserved = build_brain(claude_config(), house(), persona_text=PERSONA)

    assert observed.on_usage is recorder
    assert unobserved.on_usage is None


def test_the_ollama_brain_ignores_the_usage_hook_because_ollama_is_free(ollama_clients):
    recorder = UsageRecorder()

    brain = build_brain(AppConfig(), house(), persona_text=PERSONA, on_usage=recorder)
    reply = brain.respond("halo", now=NOW)

    assert isinstance(brain, OllamaBrain)
    assert reply.text == "Halo!"
    assert recorder.calls == []


# A photo from the phone camera, sent with Master's message

PHOTO = b"\xff\xd8\xff\xc0 a photo from the phone camera"


class RecordingAnthropic:
    """Stands in for anthropic.Anthropic inside the real Claude brain: records the messages and
    the tools of each request, and whether it was streamed (as it is with web search), and
    answers it with a short text reply, never reaching the network."""

    api_key = "test-key"
    auth_token = None
    credentials = None

    def __init__(self) -> None:
        self.sent: list[list[dict]] = []
        self.tools: list[list[dict]] = []
        self.streamed: list[bool] = []
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=self._create, stream=self._stream)
        )

    def _create(self, **request: object) -> SimpleNamespace:
        self.sent.append(copy.deepcopy(request["messages"]))
        self.tools.append(copy.deepcopy(request["tools"]))
        self.streamed.append(False)
        answer = SimpleNamespace(type="text", text="Itu kucing, Master!")
        return SimpleNamespace(content=[answer], stop_reason="end_turn", stop_details=None)

    @contextmanager
    def _stream(self, **request: object) -> Iterator[SimpleNamespace]:
        reply = self._create(**request)
        self.streamed[-1] = True
        yield SimpleNamespace(get_final_message=lambda: reply)


def test_a_photo_in_the_turn_images_reaches_claude_inside_masters_message(monkeypatch):
    clients: list[RecordingAnthropic] = []

    def build_client(**options: object) -> RecordingAnthropic:
        clients.append(RecordingAnthropic())
        return clients[-1]

    monkeypatch.setattr("waifu.brain.claude.anthropic.Anthropic", build_client)
    images = TurnImages()
    brain = build_brain(claude_config(), house(), persona_text=PERSONA, turn_images=images)

    images.put(ToolImage("image/jpeg", PHOTO))
    reply = brain.respond("ini apa?", now=NOW)

    assert reply.text == "Itu kucing, Master!"
    [[message]] = clients[0].sent
    assert message["content"][0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(PHOTO).decode("ascii"),
        },
    }
    assert images.take() == []


def test_the_ollama_brain_never_gets_the_turn_images_because_its_model_cannot_see(
    ollama_clients,
):
    images = TurnImages()
    brain = build_brain(AppConfig(), house(), persona_text=PERSONA, turn_images=images)

    images.put(ToolImage("image/jpeg", PHOTO))
    reply = brain.respond("ini apa?", now=NOW)

    assert isinstance(brain, OllamaBrain)
    assert reply.text == "Halo!"
    assert images.take() == [ToolImage("image/jpeg", PHOTO)]  # never taken


# Memory section

NO_NOTES = "Ingatanmu tentang Master: belum ada catatan."
ONE_NOTE = (
    "Ingatanmu tentang Master (catatan dari obrolan sebelumnya, tersimpan di PC ini):\n"
    "- m1 (12-10-2026): Master alergi udang."
)
UNREADABLE = "Ingatanmu tentang Master tidak bisa dibaca saat ini."


class MemorySection:
    """Stands in for `memory_prompt_section(store.facts())`, counting its reads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.reads = 0

    def __call__(self) -> str:
        self.reads += 1
        return self.text


def claude_config() -> AppConfig:
    return AppConfig(assistant=AssistantConfig(name="Cyrene"), brain=BrainConfig(backend="claude"))


def test_memory_section_follows_the_unchanged_base_prompt_after_a_blank_line(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    registry = house()

    brain = build_brain(
        claude_config(), registry, persona_text=PERSONA, memory_section=MemorySection(ONE_NOTE)
    )

    base = build_system_prompt(PERSONA, "Cyrene", registry, web_search=True)
    assert brain.system_prompt() == f"{base}\n\n{ONE_NOTE}"


def test_memory_section_is_read_each_time_the_brain_reads_the_system_prompt(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    section = MemorySection(NO_NOTES)

    brain = build_brain(claude_config(), house(), persona_text=PERSONA, memory_section=section)
    reads_when_built = section.reads
    first = brain.system_prompt()
    section.text = ONE_NOTE
    second = brain.system_prompt()

    assert reads_when_built == 0
    assert section.reads == 2
    assert first.endswith(f"\n\n{NO_NOTES}")
    assert second.endswith(f"\n\n{ONE_NOTE}")


def test_memory_reaches_the_ollama_brain_and_a_new_session_sees_new_notes(ollama_clients):
    registry = house()
    section = MemorySection(NO_NOTES)
    config = AppConfig(assistant=AssistantConfig(name="Cyrene", max_session_turns=1))
    brain = build_brain(config, registry, persona_text=PERSONA, memory_section=section)

    brain.respond("ingat ya, aku alergi udang", now=NOW)
    section.text = ONE_NOTE
    brain.respond("enaknya masak apa?", now=NOW)

    base = build_system_prompt(PERSONA, "Cyrene", registry)
    assert [request["messages"][0] for request in ollama_clients[0].requests] == [
        {"role": "system", "content": f"{base}\n\n{NO_NOTES}"},
        {"role": "system", "content": f"{base}\n\n{ONE_NOTE}"},
    ]


def test_the_ollama_brain_reads_the_notes_when_the_first_conversation_begins(ollama_clients):
    # `waifu run` builds the brain at login, maybe hours before Master first talks to her.
    section = MemorySection(NO_NOTES)

    brain = build_brain(AppConfig(), house(), persona_text=PERSONA, memory_section=section)
    reads_when_built = section.reads
    section.text = ONE_NOTE
    brain.respond("halo", now=NOW)

    assert reads_when_built == 0
    system_message = ollama_clients[0].requests[0]["messages"][0]
    assert system_message["content"].endswith(f"\n\n{ONE_NOTE}")


def test_unreadable_memory_is_logged_and_the_prompt_says_so_so_the_turn_still_works(
    ollama_clients, caplog
):
    registry = house()

    def broken_memory() -> str:
        raise OSError("memory.json is locked")

    with caplog.at_level(logging.ERROR, logger="waifu.brain.factory"):
        brain = build_brain(
            AppConfig(), registry, persona_text=PERSONA, memory_section=broken_memory
        )
        reply = brain.respond("halo", now=NOW)

    assert reply.text == "Halo!"
    base = build_system_prompt(PERSONA, "Cyrene", registry)
    system_message = ollama_clients[0].requests[0]["messages"][0]
    assert system_message == {"role": "system", "content": f"{base}\n\n{UNREADABLE}"}
    [record] = [record for record in caplog.records if record.name == "waifu.brain.factory"]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], OSError)


# Guest mode


def guest_mode() -> GuestMode:
    return GuestMode(now=lambda: datetime(2026, 10, 24, 20, 0))


def test_guest_mode_hands_the_brain_its_refusal_and_its_generation(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    guest = guest_mode()

    brain = build_brain(claude_config(), house(), persona_text=PERSONA, guest_mode=guest)

    assert brain.tool_refusal == guest.refusal
    assert brain.session_marker == guest.generation


def test_without_guest_mode_the_brain_gets_neither(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)

    brain = build_brain(claude_config(), house(), persona_text=PERSONA)

    assert (brain.tool_refusal, brain.session_marker) == (None, None)


def test_while_guest_mode_is_on_the_guest_section_takes_the_memory_s_place(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    registry = house()
    section = MemorySection(ONE_NOTE)
    guest = guest_mode()
    brain = build_brain(
        claude_config(), registry, persona_text=PERSONA, memory_section=section, guest_mode=guest
    )
    base = build_system_prompt(PERSONA, "Cyrene", registry, web_search=True)

    assert brain.system_prompt() == f"{base}\n\n{ONE_NOTE}"
    guest.turn_on()
    assert brain.system_prompt() == f"{base}\n\n{GUEST_SECTION}"
    assert section.reads == 1  # the notes are never read while guests are around
    guest.turn_off()
    assert brain.system_prompt() == f"{base}\n\n{ONE_NOTE}"


def test_without_memory_the_guest_section_still_follows_the_base_prompt(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    registry = house()
    guest = guest_mode()
    brain = build_brain(claude_config(), registry, persona_text=PERSONA, guest_mode=guest)
    base = build_system_prompt(PERSONA, "Cyrene", registry, web_search=True)

    assert brain.system_prompt() == base
    guest.turn_on()
    assert brain.system_prompt() == f"{base}\n\n{GUEST_SECTION}"


def test_the_guest_section_names_the_tool_that_ends_it_and_holds_no_live_state():
    assert "guest_mode" in GUEST_SECTION
    assert "on false" in GUEST_SECTION
    assert not any(character.isdigit() for character in GUEST_SECTION)  # no "until" time
    assert "[" not in GUEST_SECTION  # her only square brackets are emotion tags


def test_turning_guest_mode_on_gives_the_next_turn_a_new_session_without_the_notes(
    ollama_clients,
):
    registry = house()
    guest = guest_mode()
    brain = build_brain(
        AppConfig(),
        registry,
        persona_text=PERSONA,
        memory_section=MemorySection(ONE_NOTE),
        guest_mode=guest,
    )

    brain.respond("halo", now=NOW)
    guest.turn_on()
    brain.respond("ada tamu nih", now=NOW)
    guest.turn_off()
    brain.respond("tamunya sudah pulang", now=NOW)

    base = build_system_prompt(PERSONA, "Cyrene", registry)
    assert [
        [message["role"] for message in request["messages"]]
        for request in ollama_clients[0].requests
    ] == [["system", "user"]] * 3
    assert [request["messages"][0]["content"] for request in ollama_clients[0].requests] == [
        f"{base}\n\n{ONE_NOTE}",
        f"{base}\n\n{GUEST_SECTION}",
        f"{base}\n\n{ONE_NOTE}",
    ]


def test_the_ollama_brain_refuses_a_private_tool_while_guest_mode_is_on(monkeypatch):
    status = ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(name="game_status", arguments={})
    )
    client = ScriptedOllama(
        ollama.Message(role="assistant", content="", tool_calls=[status]),
        ollama.Message(role="assistant", content="Lagi ada tamu, Master."),
    )
    monkeypatch.setattr(ollama, "Client", lambda host, **kwargs: client)
    guest = guest_mode()
    guest.turn_on()

    brain = build_brain(
        AppConfig(), house(), persona_text=PERSONA, providers=[GameTools()], guest_mode=guest
    )
    reply = brain.respond("pity HSR-ku berapa?", now=NOW)

    [refused] = reply.tool_calls
    assert (refused.is_error, json.loads(refused.result)["message"]) == (True, GUEST_REFUSAL)


# Master's yes before the PC turns off


class ScriptedOllama:
    """Stands in for ollama.Client and answers each chat with the next queued message."""

    def __init__(self, *messages: ollama.Message) -> None:
        self.messages = list(messages)

    def chat(self, model="", messages=None, **kwargs) -> ollama.ChatResponse:
        return ollama.ChatResponse(message=self.messages.pop(0))


def test_every_claude_brain_gets_its_own_confirmation_gate(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)

    first = build_brain(claude_config(), house(), persona_text=PERSONA)
    second = build_brain(claude_config(), house(), persona_text=PERSONA)

    assert isinstance(first.confirmations, ConfirmationGate)
    assert isinstance(second.confirmations, ConfirmationGate)
    assert first.confirmations is not second.confirmations


def held_then_answered(monkeypatch, config: AppConfig, answer: str) -> DeviceRegistry:
    """A built brain whose model asks to shut the mock PC down in both turns."""
    shutdown = ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(
            name="control_device", arguments={"device": "komputer", "command": "shutdown"}
        )
    )
    client = ScriptedOllama(
        ollama.Message(role="assistant", content="", tool_calls=[shutdown]),
        ollama.Message(role="assistant", content="Baik, Master."),
        ollama.Message(role="assistant", content="", tool_calls=[shutdown]),
        ollama.Message(role="assistant", content="Komputer mati dalam 30 detik, Master."),
    )
    monkeypatch.setattr(ollama, "Client", lambda host, **kwargs: client)
    # A mock PC only: no test reaches a real power command.
    registry = DeviceRegistry(
        [MockDevice("komputer", "Komputer", commands=["shutdown"], kind="pc")]
    )
    brain = build_brain(config, registry, persona_text=PERSONA)

    brain.respond("Cyrene, matikan komputer.", now=NOW)
    assert registry.get("komputer").calls == []
    brain.respond(answer, now=NOW)
    return registry


def test_the_ollama_brain_holds_turning_the_pc_off_until_masters_next_words_say_yes(monkeypatch):
    registry = held_then_answered(monkeypatch, AppConfig(), "iya")

    assert registry.get("komputer").calls == [("shutdown", None)]


def test_the_gate_hears_master_through_the_assistants_name_and_wake_words(monkeypatch):
    config = AppConfig(assistant=AssistantConfig(name="Hikari", wake_words=["hei hikari"]))

    registry = held_then_answered(monkeypatch, config, "Hei Hikari, iya. Hikari, boleh.")

    assert registry.get("komputer").calls == [("shutdown", None)]


CALLED_SAYANG = AppConfig(assistant=AssistantConfig(owner_call="Sayang"))


def test_iya_sayang_is_a_yes_from_an_owner_she_calls_sayang(monkeypatch):
    registry = held_then_answered(monkeypatch, CALLED_SAYANG, "Iya, Sayang.")

    assert registry.get("komputer").calls == [("shutdown", None)]


def test_the_ollama_brain_asks_and_apologises_with_what_she_calls_the_owner(monkeypatch):
    shutdown = ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(
            name="control_device", arguments={"device": "komputer", "command": "shutdown"}
        )
    )
    client = ScriptedOllama(
        ollama.Message(role="assistant", content="", tool_calls=[shutdown]),
        ollama.Message(role="assistant", content=""),
    )
    monkeypatch.setattr(ollama, "Client", lambda host, **kwargs: client)
    registry = DeviceRegistry(
        [MockDevice("komputer", "Komputer", commands=["shutdown"], kind="pc")]
    )
    brain = build_brain(CALLED_SAYANG, registry, persona_text=PERSONA)

    asked = brain.respond("Cyrene, matikan komputer.", now=NOW)
    apology = brain.respond("halo", now=NOW)  # the scripted model has nothing more to say

    assert asked.text == "Komputer mau kumatikan sekarang, Sayang? Jawab iya kalau jadi."
    assert apology.text.startswith("Maaf, Sayang, ")


def test_the_claude_brain_and_its_gate_call_the_owner_what_she_calls_them(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    config = AppConfig(
        assistant=AssistantConfig(owner_call="Sayang"), brain=BrainConfig(backend="claude")
    )
    registry = DeviceRegistry(
        [MockDevice("komputer", "Komputer", commands=["shutdown"], kind="pc")]
    )

    brain = build_brain(config, registry, persona_text=PERSONA)
    brain.confirmations.begin_turn("matikan komputer")
    brain.confirmations.check(registry, "komputer", "shutdown", None)

    assert brain.owner_call == "Sayang"
    assert brain.confirmations.with_question("") == (
        "Komputer mau kumatikan sekarang, Sayang? Jawab iya kalau jadi."
    )


def test_confirmation_gate_hears_master_through_her_names_and_what_she_calls_the_owner():
    config = AppConfig(
        assistant=AssistantConfig(name="Hikari", wake_words=["hei hikari"], owner_call="Sayang")
    )
    gate = confirmation_gate(config)
    gate.begin_turn("catat jajan kopi")
    assert gate.check_action("catat", label="Catat", question="Catat, Sayang?") is not None

    gate.begin_turn("Hei Hikari, iya. Hikari, boleh, Sayang.")

    assert gate.check_action("catat", label="Catat", question="Catat, Sayang?") is None


def test_confirmation_gate_makes_a_new_gate_each_time():
    assert confirmation_gate(AppConfig()) is not confirmation_gate(AppConfig())


def test_the_claude_brain_takes_the_gate_it_is_given(monkeypatch):
    # The CLI hands one gate to the brain and to the finance tools: one question per turn.
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    gate = confirmation_gate(claude_config())

    brain = build_brain(claude_config(), house(), persona_text=PERSONA, confirmations=gate)

    assert brain.confirmations is gate


def test_the_ollama_brain_holds_its_calls_with_the_gate_it_is_given(monkeypatch):
    shutdown = ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(
            name="control_device", arguments={"device": "komputer", "command": "shutdown"}
        )
    )
    client = ScriptedOllama(
        ollama.Message(role="assistant", content="", tool_calls=[shutdown]),
        ollama.Message(role="assistant", content="Baik, Master."),
    )
    monkeypatch.setattr(ollama, "Client", lambda host, **kwargs: client)
    registry = DeviceRegistry(
        [MockDevice("komputer", "Komputer", commands=["shutdown"], kind="pc")]
    )
    gate = confirmation_gate(AppConfig())

    brain = build_brain(AppConfig(), registry, persona_text=PERSONA, confirmations=gate)
    brain.respond("Cyrene, matikan komputer.", now=NOW)

    question = "Komputer mau kumatikan sekarang, Master? Jawab iya kalau jadi."
    assert registry.get("komputer").calls == []
    assert gate.with_question("") == question


# Web search (Claude's server tool)


def searching_config(
    search: WebSearchConfig, briefing_city: str | None = None, *, model: str = "claude-opus-5-5"
) -> AppConfig:
    return AppConfig(
        assistant=AssistantConfig(name="Cyrene"),
        brain=BrainConfig(backend="claude", claude=ClaudeConfig(model=model)),
        web_search=search,
        briefing=BriefingConfig(city=briefing_city),
    )


@pytest.mark.parametrize(
    ("search_city", "briefing_city", "location"),
    [
        ("Bandung", "Jakarta", {"city": "Bandung"}),
        (None, "Jakarta", {"city": "Jakarta"}),
        (None, None, {}),
    ],
    ids=["its_own_city", "the_briefing_city", "no_city"],
)
def test_the_claude_brain_gets_the_web_search_tool_near_the_owner(
    search_city, briefing_city, location, monkeypatch
):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    search = WebSearchConfig(max_uses=5, country="ID", timezone="Asia/Makassar", city=search_city)

    brain = build_brain(searching_config(search, briefing_city), house(), persona_text=PERSONA)

    assert brain.web_search == {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 5,
        "user_location": {
            "type": "approximate",
            "country": "ID",
            "timezone": "Asia/Makassar",
            **location,
        },
    }


@pytest.mark.parametrize(
    ("model", "version"),
    [
        ("claude-opus-5-5", "web_search_20260209"),
        ("claude-sonnet-4-6", "web_search_20260209"),
        ("claude-haiku-5-5", "web_search_20250305"),
        ("claude-opus-4-5", "web_search_20250305"),
    ],
)
def test_a_model_without_dynamic_filtering_gets_the_earlier_web_search(model, version, monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)

    brain = build_brain(
        searching_config(WebSearchConfig(), model=model), house(), persona_text=PERSONA
    )

    assert brain.web_search["type"] == version
    assert brain.web_search["max_uses"] == 3


def test_with_web_search_off_the_claude_brain_gets_neither_the_tool_nor_the_web_note(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    registry = house()

    brain = build_brain(
        searching_config(WebSearchConfig(enabled=False)), registry, persona_text=PERSONA
    )

    assert brain.web_search is None
    assert brain.system_prompt == build_system_prompt(PERSONA, "Cyrene", registry)


def test_the_real_claude_brain_sends_the_web_search_tool_after_her_own_tools(monkeypatch):
    clients: list[RecordingAnthropic] = []

    def build_client(**options: object) -> RecordingAnthropic:
        clients.append(RecordingAnthropic())
        return clients[-1]

    monkeypatch.setattr("waifu.brain.claude.anthropic.Anthropic", build_client)
    config = searching_config(WebSearchConfig(), briefing_city="Bandung")

    build_brain(config, house(), persona_text=PERSONA).respond("harga RTX 5070?", now=NOW)

    [tools] = clients[0].tools
    assert [tool["name"] for tool in tools] == ["control_device", "list_devices", "web_search"]
    assert tools[-1]["user_location"]["city"] == "Bandung"
    assert clients[0].streamed == [True]


def test_the_ollama_brain_never_searches_the_web_even_with_web_search_on(ollama_clients):
    registry = house()

    build_brain(AppConfig(), registry, persona_text=PERSONA).respond("halo", now=NOW)

    request = ollama_clients[0].requests[0]
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "control_device",
        "list_devices",
    ]
    assert request["messages"][0]["content"] == build_system_prompt(PERSONA, "Cyrene", registry)
