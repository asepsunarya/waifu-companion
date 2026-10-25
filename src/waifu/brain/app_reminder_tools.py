"""The tool for Master's game start reminders: `app_reminder`.

"Pas buka HSR nanti, ingetin aku farming relic", "tiap buka Genshin ingetin cek ekspedisi",
"pengingat gameku apa aja?", "hapus pengingat Genshin yang ekspedisi": the tool keeps, lists,
and removes the reminders said when a game starts (`app_reminders.py`); the time-based reminders
stay with `schedule`. When the game is open as Master makes one, the result says so, from the
play time file the watcher writes, so she can tell him it waits for the next start.

Every argument may be left out (they are checked here), so it takes no strict slot. The spec is
static English built once from the configured games, so it stays byte-identical for the brain's
lifetime and keeps the prompt cache warm. Guest mode closes it in the shared dispatcher (its
texts are Master's own); in the turns she starts herself it refuses. `run` never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import cast

from waifu.app_reminders import AppReminder, AppReminderError, AppReminderStore
from waifu.avatar.emotion import split_emotion
from waifu.brain.base import ToolSpec
from waifu.brain.play_time_tools import RecordSource
from waifu.briefing import BRIEFING_PROMPT
from waifu.config import GameId, PlayTimeConfig
from waifu.gacha.games import GAMES
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.play_time import playing_now, wib_now

logger = logging.getLogger(__name__)

APP_REMINDER = "app_reminder"
ACTIONS = ("add", "list", "remove")
# The arguments each action takes besides `action`; null counts as left out.
_ACTION_KEYS: dict[str, frozenset[str]] = {
    "add": frozenset({"game", "text", "every_time"}),
    "list": frozenset({"game"}),
    "remove": frozenset({"id", "game"}),
}
_KEYS = frozenset({"action"}).union(*_ACTION_KEYS.values())
_REFUSAL = "Pengingat game hanya diatur atas permintaan Master, bukan untuk {turn}."
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: _REFUSAL.format(turn="sapaan pagi"),
    WEEKLY_REPORT_PROMPT: _REFUSAL.format(turn="laporan game mingguan"),
    JOURNAL_PROMPT: _REFUSAL.format(turn="pertanyaan jurnal malam"),
}
_OPEN_NOW = (
    "{game} sedang terbuka sekarang; pengingat ini disimpan untuk saat {game} dibuka berikutnya."
)
_UNKNOWN_ID = "Tidak ada pengingat game dengan id {id}."
_OTHER_GAME = "Pengingat {id} untuk {game}, bukan {given}, jadi tidak dihapus."
_FAILED = "Pengingat game tidak bisa dibuka saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class AppReminderTools:
    """A `ToolProvider` for the brain: Master's reminders for when he next opens a game."""

    def __init__(
        self,
        store: AppReminderStore,
        play: RecordSource,
        config: PlayTimeConfig,
        *,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`play` is the play time store the watcher writes, read to tell whether the game is
        open now; `now` is aware WIB time."""
        self._store = store
        self._play = play
        self._games: list[GameId] = list(config.games)
        self._poll = timedelta(seconds=config.poll_seconds)
        self._now = now
        self._spec = _spec(self._games)
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, never touches a
        reminder."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != APP_REMINDER:
            return _error(f"Tool '{name}' bukan tool pengingat game.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return self._app_reminder(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except AppReminderError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("App reminder tool failed")
            return _error(_FAILED)

    def _app_reminder(self, arguments: object) -> tuple[str, bool]:
        given = _given(arguments)
        action = given.pop("action", "add")
        if not isinstance(action, str) or action not in ACTIONS:
            raise _InvalidArguments(f"'action' harus salah satu dari: {', '.join(ACTIONS)}.")
        for key in given:
            if key not in _ACTION_KEYS[action]:
                raise _InvalidArguments(f"'{key}' tidak dipakai untuk {action}.")
        if action == "add":
            return self._add(given)
        # A game is optional here: the reminders of one game, or the one it must remove.
        game = self._game(given) if "game" in given else None
        if action == "list":
            kept = self._store.reminders() if game is None else self._store.for_game(game)
            reminders = [_reminder_json(reminder) for reminder in kept]
            return _to_json({"ok": True, "reminders": reminders}), False
        return self._remove(_text(given, "id"), game)

    def _add(self, given: Mapping[str, object]) -> tuple[str, bool]:
        game_id = self._game(given)
        # The persona tags every text for Master, but a reminder is said as it is.
        text = split_emotion(_text(given, "text"))[1]
        every_time = given.get("every_time", False)
        if not isinstance(every_time, bool):
            raise _InvalidArguments("'every_time' harus true atau false.")
        open_now = game_id in playing_now(self._play.record(), self._now(), poll=self._poll)
        reminder = self._store.add(game_id, text, every_time=every_time)
        result = {"ok": True, **_reminder_json(reminder), "game_open_now": open_now}
        if open_now:
            result["message"] = _OPEN_NOW.format(game=GAMES[game_id].short)
        return _to_json(result), False

    def _remove(self, reminder_id: str, game: GameId | None) -> tuple[str, bool]:
        """Remove `reminder_id`; with `game`, only when it is that game's, so a mixed-up id
        never removes another game's reminder."""
        if game is not None:
            found = next((r for r in self._store.reminders() if r.id == reminder_id), None)
            if found is not None and found.game != game:
                return _error(
                    _OTHER_GAME.format(
                        id=reminder_id, game=GAMES[found.game].short, given=GAMES[game].short
                    )
                )
        removed = self._store.remove(reminder_id)
        if removed is None:
            return _error(_UNKNOWN_ID.format(id=reminder_id))
        return _to_json({"ok": True, "removed": _reminder_json(removed)}), False

    def _game(self, given: Mapping[str, object]) -> GameId:
        game = given.get("game")
        if not isinstance(game, str) or game not in self._games:
            raise _InvalidArguments(f"'game' harus salah satu dari: {', '.join(self._games)}.")
        return cast(GameId, game)


def _spec(games: Sequence[GameId]) -> ToolSpec:
    """Built once from the configured games, so every request sends the same bytes."""
    names = ", ".join(f"{GAMES[game].name} = {game}" for game in games)
    return ToolSpec(
        name=APP_REMINDER,
        description=(
            "Reminders you say aloud when Master next opens one of his games on this PC, not at "
            "a clock time: 'pas buka HSR nanti, ingetin aku farming relic', 'tiap buka Genshin "
            "ingetin cek ekspedisi', 'nanti kalau buka ZZZ ingetin claim battle pass'. Use this "
            "tool, never schedule, whenever Master ties a reminder to opening or starting a "
            f"game. Games: {names}. `action`: add (the default) with `game` and `text`, a short "
            "Indonesian phrase of what to do as Master said it, without 'ingetin aku' (such as "
            "'farming relic'), and `every_time` true only when Master says every time ('tiap', "
            "'setiap kali'); otherwise it is said at the next start only, then dropped; list "
            "for Master's game reminders, with `game` for one game's only; remove with the `id` "
            "from add or list (a `game` given with remove must be that reminder's game): when "
            "Master names one by its text, list first and pick it by its text; never guess an "
            "id. The `text` is said after 'jangan lupa', so leave that out of it too; something "
            "not to do keeps its 'jangan' ('jangan top up dulu'). You say a game's reminders "
            "when you see it start while `waifu run` is on, never for a game already open when "
            "you start or when the PC wakes from sleep. When the result has `game_open_now` "
            "true, the game is open right now: tell Master you will remind him the next time he "
            "opens it. Only when Master asks, never in a greeting or anything you start "
            "yourself. Answer in one short sentence: no lists and no parentheses, because your "
            "voice drops whatever is in parentheses. After an error result, say its message "
            "briefly and do not call again in this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "game": {"type": "string", "enum": list(games)},
                "text": {"type": "string"},
                "every_time": {"type": "boolean"},
                "id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )


def _reminder_json(reminder: AppReminder) -> dict[str, object]:
    return {
        "id": reminder.id,
        "game": reminder.game,
        "name": GAMES[reminder.game].short,
        "text": reminder.text,
        "every_time": reminder.every_time,
    }


def _given(arguments: object) -> dict[str, object]:
    """The arguments that are not null (local models send null for a left-out one)."""
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return {str(key): value for key, value in arguments.items() if value is not None}


def _text(given: Mapping[str, object], key: str) -> str:
    value = given.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments(f"'{key}' wajib diisi dengan teks.")
    return value


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
