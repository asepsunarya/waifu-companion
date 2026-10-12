"""The two tools that change Cyrene's long-term memory: `remember` and `forget`.

The notes reach the model through the system prompt of each new session
(`memory_prompt_section`); these tools only change the file. The specs carry no notes,
so they stay byte-identical for the brain's lifetime and keep the prompt cache warm. A
note the store refuses (a secret, too long, a full memory, an unknown id) comes back as
an error result with the store's Indonesian message, which never repeats the note.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence

from waifu import memory
from waifu.brain.base import ToolCallRecord, ToolSpec

logger = logging.getLogger(__name__)

REMEMBER = "remember"
FORGET = "forget"
_REMEMBER_KEYS = frozenset({"text", "replaces"})
_FORGET_KEYS = frozenset({"id"})


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class MemoryTools:
    """A `ToolProvider` for the brain: notes about Master, kept between sessions."""

    def __init__(self, store: memory.MemoryStore) -> None:
        self._store = store

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=REMEMBER,
                description=(
                    "Save one lasting fact about Master in your long-term memory, or update a "
                    "saved note. Your notes are listed with their ids (m1, m2, ...) in the "
                    "system prompt of each new session; a note saved now is listed from the "
                    "next session on. Save anything Master asks you to remember, and on your "
                    "own the lasting things Master mentions in passing: likes and dislikes, "
                    "allergies and health, plans with their date (a full date such as "
                    "16-10-2026, not just a weekday), people and pets, routines, favourite "
                    "games and characters. Write the note in Indonesian, in the third person "
                    "('Master alergi udang.'), one fact per call, at most "
                    f"{self._store.max_chars} characters. Do not save small talk, device "
                    "commands, anything already in your notes, or secrets: passwords, PINs, "
                    "OTPs, tokens, cookies, API keys, card numbers (they are refused). When a "
                    "saved fact changed, pass `replaces` with the old note's id instead of "
                    "adding a second note that contradicts it."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "replaces": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name=FORGET,
                description=(
                    "Delete one note from your long-term memory by its id (m1, m2, ...: from "
                    "the list in the system prompt, or the id `remember` gave). Use it when "
                    "Master asks you to forget something. When a fact only changed, use "
                    "`remember` with `replaces` instead."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                    "additionalProperties": False,
                },
            ),
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == REMEMBER:
                return self._remember(arguments)
            if name == FORGET:
                return self._forget(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except memory.MemoryError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("Memory tool '%s' failed", name)
            return _error("Ingatan tidak bisa diubah saat ini.")
        return _error(f"Tool '{name}' bukan tool ingatan.")

    def _remember(self, arguments: object) -> tuple[str, bool]:
        mapping = _arguments_mapping(arguments, allowed=_REMEMBER_KEYS)
        text = _text(mapping, "text")
        if mapping.get("replaces") is None:
            fact, message = self._store.add(text), "Tersimpan."
        else:
            fact, message = self._store.replace(_text(mapping, "replaces"), text), "Diperbarui."
        return _to_json({"ok": True, "id": fact.id, "message": message}), False

    def _forget(self, arguments: object) -> tuple[str, bool]:
        fact_id = _text(_arguments_mapping(arguments, allowed=_FORGET_KEYS), "id")
        fact = self._store.forget(fact_id)
        return _to_json({"ok": True, "message": f"Dihapus: {fact.text}"}), False


def changed_memory(tool_calls: Sequence[ToolCallRecord]) -> bool:
    """True when one of the calls saved, replaced, or deleted a note.

    The file keeps the change even when the brain rolls that turn back, and then neither the
    session's system prompt nor its history shows it; a brain ends such a session, so the
    next one lists the notes as they are.
    """
    return any(call.name in (REMEMBER, FORGET) and not call.is_error for call in tool_calls)


def _arguments_mapping(arguments: object, allowed: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in allowed]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _text(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments(f"'{key}' wajib diisi dengan teks.")
    return value


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
