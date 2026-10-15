"""The watcher inside `waifu run` that tells Master about new redeem codes.

Soon after it starts, and then every `poll_hours`, it reads the codes of Master's games from
the community source and keeps them, so her answers about codes stay current; with
`notices` off it stops there. Each game's codes not told yet become one notice: she names
what they give and asks whether to redeem them, or, with `auto_redeem` and a cookie, redeems
them on HoYoLAB first (each outcome recorded as soon as it is known) and tells what happened.
A notice is said between utterances and pushed to the phone with every code and its rewards
in text; then its codes are marked announced in the store, so a restart never tells them
again. A failing read, or a redemption call that raises, is logged and the next poll tries
again. Codes HoYoLAB could not redeem then (cooldown, cookie, error) are told once, with the
reason, and wait for Master to ask. A failing announcement, push, or store write after
redeeming is logged; the notice still goes. Once `stop()` is called, a poll in flight
redeems, says, and pushes nothing more: redeeming stops before the next code, the codes
HoYoLAB already answered keep their outcomes but are never told (her recent outcomes show
them), and the codes left untried wait for the next start. Nothing is raised.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime

from waifu.avatar.emotion import NEUTRAL
from waifu.config import GameId, RedeemCodesConfig, at_sentence_start
from waifu.gacha.code_store import CodeStore
from waifu.gacha.code_tools import RedeemSource
from waifu.gacha.codes import FINAL_OUTCOMES, REDEEM_OUTCOMES, CodesError, RedeemCode, RedeemResult
from waifu.gacha.games import GAMES
from waifu.gacha.hoyolab import NOT_REDEEMED, REDEEM_CODES_AT_MOST, GachaError
from waifu.notify import Notifier

logger = logging.getLogger(__name__)

# Soon after start, once "waifu run" has said she is ready.
FIRST_POLL_DELAY_S = 60.0
CODE_TAG = "gift"  # ntfy shows it as a wrapped present emoji
NAMES_IN_NOTICE = 3  # the rest are counted, so a notice stays short to say
NO_REWARDS = "satu kode tanpa keterangan hadiah"
STOP_TIMEOUT_S = 5.0
# What redeeming did, as she says it after "N kode".
OUTCOME_TEXT = {
    "redeemed": "berhasil ditukarkan",
    "claimed": "sudah pernah ditukarkan",
    "expired": "sudah kedaluwarsa",
    "invalid": "tidak valid",
    "region": "tidak berlaku di server akun ini",
    "level": "butuh level akun yang lebih tinggi",
    "cooldown": "belum bisa ditukarkan",
    "cookie": "belum bisa ditukarkan",
    "error": "belum bisa ditukarkan",
}

# The outcomes whose message says what kept that code (a cookie's is the same for every code).
_WITH_REASON = frozenset({"cooldown", "error"})

ReadCodes = Callable[[Sequence[GameId]], dict[GameId, list[tuple[str, str]]] | CodesError]
# Waits up to the given seconds for the event; True when it was set.
Wait = Callable[[threading.Event, float], bool]


def _local_now() -> datetime:
    return datetime.now().astimezone()


class CodeWatcher:
    def __init__(
        self,
        store: CodeStore,
        read_codes: ReadCodes,
        hoyolab: RedeemSource,
        config: RedeemCodesConfig,
        games: Sequence[GameId],
        *,
        announce: Callable[[str, str], None] | None,
        notifier: Notifier | None,
        owner_call: str,
        title: str = "Cyrene",
        now: Callable[[], datetime] = _local_now,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`read_codes` reads `games` from the source; `now` gives an aware local time."""
        self._store = store
        self._read_codes = read_codes
        self._hoyolab = hoyolab
        self._config = config
        self._games = list(games)
        self._announce = announce
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every notice opens with it
        self._title = title
        self._now = now
        self._wait = wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[str]:
        """Read and keep the codes, then, with the notices on, tell each game's codes not told
        yet; returns the notices given."""
        try:
            found = self._read_codes(self._games)
            if isinstance(found, CodesError):
                logger.warning("Could not read the redeem codes; trying again at the next poll")
                return []
            now = self._now()
            for game, codes in found.items():
                self._store.add(game, codes, now)
        except Exception:
            logger.exception("Could not look for new redeem codes; trying again at the next poll")
            return []
        if not self._config.notices:
            return []
        notices = []
        for game in self._games:
            if self._stop.is_set():
                break
            notice = self._tell(game)
            if notice is not None:
                notices.append(notice)
        return notices

    def start(self) -> None:
        """Poll soon, then every `poll_hours`, in a daemon thread; nothing runs while
        `redeem_codes.enabled` is off, and with `redeem_codes.notices` off it only keeps the
        codes."""
        if self._thread is not None:
            return
        if not self._config.enabled:
            logger.info("Redeem codes are off; not watching for new codes")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="code-watcher", daemon=True)
        self._thread.start()
        if self._config.notices:
            logger.info("Watching for new redeem codes every %d hours", self._config.poll_hours)
        else:
            logger.info(
                "Reading the redeem codes every %d hours without telling (notices off)",
                self._config.poll_hours,
            )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        delay_s = FIRST_POLL_DELAY_S
        while not self._wait(self._stop, delay_s):
            self.poll_once()
            delay_s = self._config.poll_hours * 3600

    def _tell(self, game: GameId) -> str | None:
        """Tell the codes of `game` not told yet, redeeming them first with `auto_redeem`;
        the notice, or None when there was nothing to tell, it failed (logged), or the
        watcher is stopping."""
        short = GAMES[game].short
        try:
            fresh = self._store.unannounced(game)
            if not fresh:
                return None
            redeemed = self._auto_redeemed(game, fresh) if self._config.auto_redeem else None
            if redeemed is None:
                text, emotion = _ask_notice(self._owner, game, fresh), "happy"
                lines, told = [_code_line(code) for code in fresh], fresh
            else:
                text, emotion = _redeemed_notice(self._owner, game, redeemed)
                lines = [_result_line(*pair) for pair in redeemed]
                told = [code for code, _ in redeemed]
        except Exception:
            logger.exception(
                "Could not tell or redeem new %s codes; trying again at the next poll", short
            )
            return None
        if self._stop.is_set():
            # Stopping, perhaps while redeeming: nothing more is said or pushed. The codes
            # HoYoLAB answered keep their outcomes (her recent outcomes show them) and are
            # never told; the codes left untried wait for the next start.
            return None
        self._deliver(text, emotion, lines)
        try:
            self._store.mark_announced(told)
        except Exception:
            logger.exception("Could not mark the new %s codes told; they may be told again", short)
        return text

    def _auto_redeemed(
        self, game: GameId, codes: Sequence[RedeemCode]
    ) -> list[tuple[RedeemCode, RedeemResult]] | None:
        """Each of the first REDEEM_CODES_AT_MOST `codes` with what redeeming it did, each
        recorded as soon as it is known; None without a cookie, so that she asks instead.
        Once the watcher is stopping, no further code is tried: those are left out."""
        trying = {code.code: code for code in codes[:REDEEM_CODES_AT_MOST]}
        try:
            results = self._hoyolab.redeem_codes(
                game, list(trying), on_result=self._record, should_stop=self._stop.is_set
            )
        except GachaError as error:
            if error.kind != "no_cookie":
                raise
            logger.info("auto_redeem is on but there is no HoYoLAB cookie; asking Master instead")
            return None
        logger.info("Redeemed %d new %s codes by auto_redeem", len(results), GAMES[game].short)
        return [(trying[result.code], result) for result in results]

    def _record(self, result: RedeemResult) -> None:
        """Keep what redeeming one code did, so that a run cut short keeps it; a failing write
        is logged: HoYoLAB has done it, so the notice still tells it."""
        try:
            self._store.record([result])
        except Exception:
            logger.exception(
                "Could not record what redeeming %s code %s did",
                GAMES[result.game].short,
                result.code,
            )

    def _deliver(self, text: str, emotion: str, lines: list[str]) -> None:
        """Say the notice and push it with `lines` below it; a failure of either is logged,
        never raised."""
        if self._announce is not None:
            try:
                self._announce(text, emotion)
            except Exception:
                logger.exception("Could not say a redeem code notice")
        if self._notifier is not None:
            try:
                self._notifier.send(self._title, "\n".join([text, *lines]), tags=[CODE_TAG])
            except Exception:
                logger.exception("Could not push a redeem code notice")


def _ask_notice(owner: str, game: GameId, codes: Sequence[RedeemCode]) -> str:
    """`Master, ada 2 kode HSR baru: 60 stellar jade, dan 30 stellar jade. Mau kutukarkan?`"""
    return (
        f"{owner}, ada {len(codes)} kode {GAMES[game].short} baru: {_rewards(codes)}. "
        "Mau kutukarkan?"
    )


def _redeemed_notice(
    owner: str, game: GameId, redeemed: Sequence[tuple[RedeemCode, RedeemResult]]
) -> tuple[str, str]:
    """`Master, aku sudah menukarkan 2 kode HSR baru: ... Hadiahnya ada di surat dalam game.`,
    then the codes that did not work counted by outcome, with what kept them when that is one
    reason for all of them, and the face to say it with: happy for rewards, sad when codes are
    left to redeem again, else neutral."""
    short = GAMES[game].short
    rewarded = [code for code, result in redeemed if result.outcome == "redeemed"]
    others = [result for _, result in redeemed if result.outcome != "redeemed"]
    sentences = []
    if rewarded:
        sentences.append(
            f"{owner}, aku sudah menukarkan {len(rewarded)} kode {short} baru: "
            f"{_rewards(rewarded)}. Hadiahnya ada di surat dalam game."
        )
    if others:
        counts = _outcome_counts(others)
        if rewarded:
            sentences.append(f"Selain itu, {counts}.")
        else:
            sentences.append(f"{owner}, ada {len(others)} kode {short} baru, tapi {counts}.")
    # The same for every code it stopped, and it says what to do about the cookie.
    cookie = next((result.message for result in others if result.outcome == "cookie"), None)
    if cookie is not None:
        sentences.append(cookie)
    reasons = {_reason(result) for result in others if result.outcome in _WITH_REASON}
    if len(reasons) == 1:
        sentences.append(reasons.pop())
    retry = any(result.outcome not in FINAL_OUTCOMES for result in others)
    if retry:
        sentences.append("Minta aku mencoba lagi nanti.")
    emotion = "happy" if rewarded else "sad" if retry else NEUTRAL
    return " ".join(sentences), emotion


def _rewards(codes: Sequence[RedeemCode]) -> str:
    """What the first codes give, the rest counted: `60 stellar jade, dan 2 kode lainnya`."""
    names = [code.rewards or NO_REWARDS for code in codes[:NAMES_IN_NOTICE]]
    more = len(codes) - NAMES_IN_NOTICE
    if more > 0:
        names.append(f"{more} kode lainnya")
    return _joined(names)


def _outcome_counts(results: Sequence[RedeemResult]) -> str:
    """`1 kode sudah pernah ditukarkan, dan 2 kode sudah kedaluwarsa`, in the order of
    REDEEM_OUTCOMES; outcomes she says alike are counted together."""
    counts: dict[str, int] = {}
    for outcome in REDEEM_OUTCOMES:
        found = sum(1 for result in results if result.outcome == outcome)
        if found:
            text = OUTCOME_TEXT[outcome]
            counts[text] = counts.get(text, 0) + found
    return _joined([f"{count} kode {text}" for text, count in counts.items()])


def _joined(parts: Sequence[str]) -> str:
    """`A`, `A, dan B`, or `A, B, dan C`: the rewards texts hold commas and "and" of their
    own, so the last one is set apart with a comma too."""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])}, dan {parts[-1]}"


def _code_line(code: RedeemCode) -> str:
    """`5S6ZHRWTDNJB: 60 stellar jade`, for the push."""
    return f"{code.code}: {code.rewards}" if code.rewards else code.code


def _result_line(code: RedeemCode, result: RedeemResult) -> str:
    """`5S6ZHRWTDNJB (60 stellar jade): berhasil ditukarkan`, for the push; a code left to
    redeem again for a reason of its own says it: `...: belum bisa ditukarkan. HoYoLAB ...`."""
    given = f"{code.code} ({code.rewards})" if code.rewards else code.code
    line = f"{given}: {OUTCOME_TEXT[result.outcome]}"
    return f"{line}. {_reason(result)}" if result.outcome in _WITH_REASON else line


def _reason(result: RedeemResult) -> str:
    """What kept a cooldown or error result's code from being redeemed, without the code."""
    return result.message.removeprefix(NOT_REDEEMED.format(code=result.code))
