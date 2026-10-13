"""Assemble the LLM backend named in config.yaml with the companion's system prompt."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from waifu.brain.base import Brain
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import build_system_prompt, load_persona
from waifu.brain.tools import ToolProvider
from waifu.config import AppConfig
from waifu.devices.registry import DeviceRegistry

logger = logging.getLogger(__name__)

_MEMORY_UNREADABLE = "Ingatanmu tentang Master tidak bisa dibaca saat ini."


def build_brain(
    config: AppConfig,
    registry: DeviceRegistry,
    *,
    persona_text: str | None = None,
    providers: Sequence[ToolProvider] = (),
    memory_section: Callable[[], str] | None = None,
) -> Brain:
    """The configured backend; the persona comes from `assistant.persona_file` unless given.

    With `memory_section`, the brain reads the system prompt at each session start,
    and the section follows the unchanged base prompt. Every brain gets its own
    confirmation gate.
    """
    persona = load_persona(config.assistant.persona_file) if persona_text is None else persona_text
    base_prompt = build_system_prompt(
        persona, config.assistant.name, registry, owner_call=config.assistant.owner_call
    )
    system_prompt: str | Callable[[], str] = (
        base_prompt if memory_section is None else _with_memory(base_prompt, memory_section)
    )
    options = {
        "session_idle_minutes": config.assistant.session_idle_minutes,
        "max_session_turns": config.assistant.max_session_turns,
        "providers": providers,
        # Never off: turning the PC off or cutting its power waits for Master's own yes, which
        # still carries her name ("iya, Cyrene") in the follow-up window and in chat, or what
        # she calls the owner ("iya, Sayang").
        "confirmations": ConfirmationGate(
            names=[config.assistant.name, *config.assistant.wake_words],
            owner_call=config.assistant.owner_call,
        ),
        "owner_call": config.assistant.owner_call,
    }
    if config.brain.backend == "claude":
        # Imported here so the Ollama path never loads the Anthropic SDK.
        from waifu.brain.claude import ClaudeBrain

        return ClaudeBrain(config.brain, registry, system_prompt, **options)
    return OllamaBrain(config.brain, registry, system_prompt, **options)


def _with_memory(base_prompt: str, memory_section: Callable[[], str]) -> Callable[[], str]:
    """The base prompt, a blank line, then the memory section as it reads right now.

    The section comes last so the persona and device prefix stays cacheable across
    sessions. A section that cannot be read must not cost the owner the turn.
    """

    def system_prompt() -> str:
        try:
            section = memory_section()
        except Exception:
            logger.exception("Could not read the memory section; the prompt says so instead")
            section = _MEMORY_UNREADABLE
        return f"{base_prompt}\n\n{section}"

    return system_prompt
