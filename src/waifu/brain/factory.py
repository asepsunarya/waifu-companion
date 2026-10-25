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
from waifu.guest import GuestMode
from waifu.usage import TokenUsage

logger = logging.getLogger(__name__)

_MEMORY_UNREADABLE = "Ingatanmu tentang Master tidak bisa dibaca saat ini."
# Takes the memory section's place while guest mode is on. Static (no "until"), so one guest
# session costs one cache write and the persona and device prefix stays cached.
GUEST_SECTION = (
    "Mode tamu sedang aktif: ada tamu di dekat Master, jadi ingatanmu tentang Master "
    "disembunyikan selama sesi ini. Jangan pernah menyebut hal pribadi Master (uang, jurnal, "
    "tugas, pengingat, akun game, jam main, atau ingatanmu tentang Master), walaupun "
    "ditanya; tool untuk itu menolak, jadi bilang singkat bahwa itu tidak dibuka selama ada "
    "tamu. Perangkat rumah, rutinitas, musik, cuaca, dan obrolan biasa tetap jalan seperti "
    "biasa. Sapaan pagimu cukup cuaca dan jadwal perangkat hari ini. Kalau Master bilang mode "
    "tamu selesai, panggil guest_mode dengan on false."
)


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
    guest_mode: GuestMode | None = None,
    network_note: Callable[[], str | None] | None = None,
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
    Ollama has no such tool. With `guest_mode`, the brain refuses the private tools
    while it is on and starts a new session after each switch, and a session begun
    while it is on gets GUEST_SECTION in the memory section's place (also without
    memory), its notes never read. `network_note` (the internet watcher's) names an outage in
    Claude's apology for a turn that could not reach it; Ollama is local and never needs it.
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
        base_prompt
        if memory_section is None and guest_mode is None
        else _session_prompt(base_prompt, memory_section, guest_mode)
    )
    options = {
        "session_idle_minutes": config.assistant.session_idle_minutes,
        "max_session_turns": config.assistant.max_session_turns,
        "providers": providers,
        # Never off: turning the PC off or cutting its power waits for Master's own yes.
        "confirmations": confirmation_gate(config) if confirmations is None else confirmations,
        "owner_call": config.assistant.owner_call,
        "tool_refusal": None if guest_mode is None else guest_mode.refusal,
        "session_marker": None if guest_mode is None else guest_mode.generation,
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
            network_note=network_note,
        )
    return OllamaBrain(config.brain, registry, system_prompt, **options)


def _session_prompt(
    base_prompt: str, memory_section: Callable[[], str] | None, guest_mode: GuestMode | None
) -> Callable[[], str]:
    """The base prompt, a blank line, then GUEST_SECTION while guest mode is on, else the
    memory section as it reads right now (the base prompt alone without memory).

    The section comes last so the persona and device prefix stays cacheable across
    sessions. A section that cannot be read must not cost the owner the turn.
    """

    def system_prompt() -> str:
        if guest_mode is not None and guest_mode.is_on():
            return f"{base_prompt}\n\n{GUEST_SECTION}"
        if memory_section is None:
            return base_prompt
        try:
            section = memory_section()
        except Exception:
            logger.exception("Could not read the memory section; the prompt says so instead")
            section = _MEMORY_UNREADABLE
        return f"{base_prompt}\n\n{section}"

    return system_prompt
