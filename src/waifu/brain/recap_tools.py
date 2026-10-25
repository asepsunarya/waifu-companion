"""The tool that tells Master what their day held: `daily_recap`.

"Hari ini aku ngapain aja?", "rekap kemarin dong": the facts come from `recap.DailyRecap`, read in
code from her local records only (the tasks, the focus log or timer, the play time, the stored
pull history, the Japanese lessons, the journal's note count, the schedule, the internet outages),
for today up to now, yesterday, or a date up to 30 days back; the model only tells them. Its one
argument may be left out, so it takes no strict slot, and its spec is static English built once,
so it stays byte-identical for the brain's lifetime and keeps the prompt cache warm. It refuses in
the turns she starts herself (the morning greeting, the weekly game report, and the nightly
journal question): a recap is told only when Master asks. Guest mode closes it (`PRIVATE_TOOLS`).
`run` never raises.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import date
from typing import Literal

from waifu.brain.base import ToolSpec
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.recap import DAYS_BACK, DailyRecap, RecapError, recap_day, recap_json

logger = logging.getLogger(__name__)

DAILY_RECAP = "daily_recap"
_KEYS = frozenset({"date"})
_DAY_WORDS = ("today", "yesterday")
# date.fromisoformat also reads 20261024 and 2026-W43-6; the tool takes only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: "Rekap hari hanya untuk pertanyaan Master, bukan untuk sapaan pagi.",
    WEEKLY_REPORT_PROMPT: (
        "Rekap hari hanya untuk pertanyaan Master, bukan untuk laporan game mingguan."
    ),
    JOURNAL_PROMPT: (
        "Rekap hari hanya untuk pertanyaan Master, bukan untuk pertanyaan jurnal malam."
    ),
}
_BAD_DATE = "'date' harus today, yesterday, atau tanggal YYYY-MM-DD, misalnya 2026-10-23."
_UNREADABLE = "Rekap hari itu tidak bisa dibuat: catatannya tidak terbaca saat ini."
_FAILED = "Rekap hari itu tidak bisa dibuat saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class DailyRecapTools:
    """A `ToolProvider` for the brain: a look back at one of Master's days."""

    def __init__(self, recap: DailyRecap) -> None:
        self._recap = recap
        self._spec = _spec()
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, gets no recap."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != DAILY_RECAP:
            return _error(f"Tool '{name}' bukan tool rekap hari.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return self._daily_recap(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except RecapError as exc:
            logger.info("No daily recap for the day asked: %s", exc)
            return _error(str(exc))
        except Exception:
            logger.exception("Daily recap tool '%s' failed", name)
            return _error(_FAILED)

    def _daily_recap(self, arguments: object) -> tuple[str, bool]:
        asked = _date(_arguments_mapping(arguments).get("date"))
        recap = self._recap.gather(recap_day(asked, self._recap.today()))
        if not recap.readable:
            return _error(_UNREADABLE)
        return _to_json(recap_json(recap)), False


def _spec() -> ToolSpec:
    """Built once, so every request sends the same bytes."""
    return ToolSpec(
        name=DAILY_RECAP,
        description=(
            "A look back at Master's day from your own records on this PC, read now in one "
            'call without the internet: "hari ini aku ngapain aja?", "rekap hari ini", '
            '"rekap kemarin dong", or what Master did on a date; daily_briefing tells what is '
            "ahead (the weather, today's schedule, the tasks due). `date`: today (the default: "
            "from midnight WIB up to now), yesterday, or YYYY-MM-DD from the date in Master's "
            f"message, from today back to {DAYS_BACK} days ago. A part is there only when its "
            "feature is on and has something that day: `tasks` (`done`, finished that day, or "
            "`done_known` false in place of them when the to-do list no longer keeps that day's "
            "finished tasks: then never say none were finished; and `still_due`, still open and "
            "due by that day), `focus` (the time Master focused with the focus timer), "
            "`play_time` (how long each game was open while `waifu run` was on, minimized or "
            "idle too; in `total` two games open together count once), "
            "`gacha` (that day's pulls per game in the pull history as stored at its last "
            "refresh, so the newest may be missing, with each 5-star, an S-rank in ZZZ), "
            "`japanese` (the words learned and reviewed), `journal` (how many notes Master "
            "wrote, never their text: journal_read reads them only when Master asks), "
            "`schedule` (the reminders and routines set for that day, `passed` or still to "
            "come: not a record that they were said), and `network` (how many times and how "
            "long the internet was down that day while `waifu run` watched it). `as_of` (today "
            "only) is the time now. No "
            "part at all means nothing was recorded that day: say so warmly. Each entry of "
            "`problems` is a part that could not be read: say briefly that it is unavailable "
            "and tell the rest. It holds no money: for that use the finance tools. For a "
            "question about one thing only (just the play time, just the tasks), use that "
            "thing's own tool instead. Use it only when Master asks, never for a greeting, the "
            "morning briefing, the weekly report, or anything you start yourself. Answer "
            "warmly in two to four short sentences, highlights first (a finished task, a "
            "5-star, the focus time), spoken naturally: never read the data out one by one, "
            "no lists and no parentheses, because your voice drops whatever is in "
            "parentheses. Tell the play time only as news: never judge whether it is too "
            "much, and never scold or lecture Master about how long they play. After an error "
            "result, say its message briefly and do not call again in this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string"}},
            "additionalProperties": False,
        },
    )


def _date(value: object) -> Literal["today", "yesterday"] | date:
    """The day asked (null counts as left out, as local models send it): today by default."""
    if value is None or value == "today":
        return "today"
    if value == "yesterday":
        return "yesterday"
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise _InvalidArguments(_BAD_DATE)
    try:
        return date.fromisoformat(value)
    except ValueError:  # a day that does not exist, such as 2026-02-30
        raise _InvalidArguments(_BAD_DATE) from None


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
