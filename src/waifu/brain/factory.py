"""Assemble the LLM backend named in config.yaml with the companion's system prompt."""

from __future__ import annotations

from collections.abc import Sequence

from waifu.brain.base import Brain
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import build_system_prompt, load_persona
from waifu.brain.tools import ToolProvider
from waifu.config import AppConfig
from waifu.devices.registry import DeviceRegistry


def build_brain(
    config: AppConfig,
    registry: DeviceRegistry,
    *,
    persona_text: str | None = None,
    providers: Sequence[ToolProvider] = (),
) -> Brain:
    """The configured backend; the persona comes from `assistant.persona_file` unless given."""
    persona = load_persona(config.assistant.persona_file) if persona_text is None else persona_text
    system_prompt = build_system_prompt(persona, config.assistant.name, registry)
    options = {
        "session_idle_minutes": config.assistant.session_idle_minutes,
        "max_session_turns": config.assistant.max_session_turns,
        "providers": providers,
    }
    if config.brain.backend == "claude":
        # Imported here so the Ollama path never loads the Anthropic SDK.
        from waifu.brain.claude import ClaudeBrain

        return ClaudeBrain(config.brain, registry, system_prompt, **options)
    return OllamaBrain(config.brain, registry, system_prompt, **options)
