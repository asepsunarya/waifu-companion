import copy
import time
from datetime import datetime

import ollama
import pytest

from waifu.brain.base import Brain, BrainReply
from waifu.brain.factory import build_brain
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import build_system_prompt
from waifu.config import AppConfig, AssistantConfig, BrainConfig, ConfigError, OllamaConfig
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry

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
    ):
        self.config, self.registry, self.system_prompt = config, registry, system_prompt
        self.session_idle_minutes, self.max_session_turns = session_idle_minutes, max_session_turns

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
    assert brain.system_prompt == build_system_prompt(PERSONA, "Cyrene", registry)


def test_claude_brain_uses_the_session_limits_from_the_assistant_config(monkeypatch):
    monkeypatch.setattr("waifu.brain.claude.ClaudeBrain", FakeClaudeBrain)
    config = AppConfig(
        assistant=AssistantConfig(session_idle_minutes=3, max_session_turns=7),
        brain=BrainConfig(backend="claude"),
    )

    brain = build_brain(config, house(), persona_text=PERSONA)

    assert (brain.session_idle_minutes, brain.max_session_turns) == (3, 7)
