"""Assemble the LLM backend named in config.yaml with the companion's system prompt."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from waifu.brain.attachments import TurnImages
from waifu.brain.base import Brain
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.persona import build_system_prompt, load_persona
from waifu.brain.tools import ToolProvider
from waifu.config import AppConfig
from waifu.devices.registry import DeviceRegistry
from waifu.usage import TokenUsage

logger = logging.getLogger(__name__)

_MEMORY_UNREADABLE = "Ingatanmu tentang Master tidak bisa dibaca saat ini."


def confirmation_gate(config: AppConfig) -> ConfirmationGate:
    """A new gate for Master's own yes: it hears Master through her name and wake words ("iya,
    Cyrene", in the follow-up window and in chat) and what she calls the owner ("iya, Sayang").
    """
    return ConfirmationGate(
        names=[config.assistant.name, *config.assistant.wake_words],
        owner_call=config.assistant.owner_call,
    )


def build_brain(
    config: AppConfig,
    registry: DeviceRegistry,
    *,
    persona_text: str | None = None,
    providers: Sequence[ToolProvider] = (),
    memory_section: Callable[[], str] | None = None,
    on_usage: Callable[[str, TokenUsage], None] | None = None,
    turn_images: TurnImages | None = None,
    confirmations: ConfirmationGate | None = None,
) -> Brain:
    """The configured backend; the persona comes from `assistant.persona_file` unless given.

    With `memory_section`, the brain reads the system prompt at each session start,
    and the section follows the unchanged base prompt. The brain holds its calls with
    `confirmations`, the gate the finance tools share, else with a gate of its own from
    `confirmation_gate`. `on_usage` hears what each Claude response used; Ollama is
    free, so its brain never calls it. `turn_images` brings Claude the photo a phone
    message carries; Ollama's model cannot see images, so its brain never gets it.
    With `web_search.enabled`, Claude gets its server-side web search (near
    `web_search.city`, else `briefing.city`) and the prompt its note on web data;
    Ollama has no such tool.
    """
    persona = load_persona(config.assistant.persona_file) if persona_text is None else persona_text
    uses_claude = config.brain.backend == "claude"
    searching = uses_claude and config.web_search.enabled
    base_prompt = build_system_prompt(
        persona,
        config.assistant.name,
        registry,
        owner_call=config.assistant.owner_call,
        web_search=searching,
    )
    system_prompt: str | Callable[[], str] = (
        base_prompt if memory_section is None else _with_memory(base_prompt, memory_section)
    )
    options = {
        "session_idle_minutes": config.assistant.session_idle_minutes,
        "max_session_turns": config.assistant.max_session_turns,
        "providers": providers,
        # Never off: turning the PC off or cutting its power waits for Master's own yes.
        "confirmations": confirmation_gate(config) if confirmations is None else confirmations,
        "owner_call": config.assistant.owner_call,
    }
    if uses_claude:
        # Imported here so the Ollama path never loads the Anthropic SDK.
        from waifu.brain.claude import ClaudeBrain, web_search_tool

        search = config.web_search
        web_search = (
            web_search_tool(
                search.max_uses,
                model=config.brain.claude.model,
                country=search.country,
                timezone=search.timezone,
                city=search.city or config.briefing.city,
            )
            if searching
            else None
        )
        return ClaudeBrain(
            config.brain,
            registry,
            system_prompt,
            **options,
            on_usage=on_usage,
            turn_images=turn_images,
            web_search=web_search,
        )
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
