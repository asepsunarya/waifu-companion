"""The words Whisper should expect: faster-whisper's `hotwords` (SPEC, kamus cyrene (v1.49)).

The list is built from the config in order of importance, her names first and the roster names
of the games last, so the cap drops the roster first: faster-whisper keeps the first tokens when
it cuts, and every token of the list is one less for the transcript. Roster names come only from
the rosters the brain read in this run (`GameDataTools`' `on_roster`); none is fetched for it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence

from waifu.config import AppConfig
from waifu.gacha.games import GAMES

logger = logging.getLogger(__name__)

HOTWORDS_MAX_TERMS = 40
# Roughly 100 to 150 Whisper tokens: the window's 448 tokens hold the hotwords, the prompt, and
# the transcript.
HOTWORDS_MAX_CHARS = 400
ROSTER_NAMES_PER_GAME = 10
SEPARATOR = ", "


def build_hotwords(config: AppConfig, rosters: Mapping[str, Sequence[str]] | None = None) -> str:
    """Her name, wake words, device names and aliases, routines, app keys, the games while
    gacha is on, then the roster names of `rosters` (per game, in `gacha.games` order): blanks
    and repeats (casefolded) left out, within HOTWORDS_MAX_TERMS terms and HOTWORDS_MAX_CHARS
    characters, ending before the first term that would pass them."""
    return SEPARATOR.join(_chosen(config, rosters or {}))


def _chosen(config: AppConfig, rosters: Mapping[str, Sequence[str]]) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    length = 0
    for raw in _terms(config, rosters):
        term = " ".join(raw.split())
        if not term or term.casefold() in seen:
            continue
        added = len(term) + (len(SEPARATOR) if chosen else 0)
        if len(chosen) == HOTWORDS_MAX_TERMS or length + added > HOTWORDS_MAX_CHARS:
            break
        chosen.append(term)
        seen.add(term.casefold())
        length += added
    return chosen


def _terms(config: AppConfig, rosters: Mapping[str, Sequence[str]]) -> Iterator[str]:
    yield config.assistant.name
    yield from config.assistant.wake_words
    yield from (device.name for device in config.devices)
    yield from (alias for device in config.devices for alias in device.aliases)
    yield from _spoken(config.routines)
    yield from _spoken(app for device in config.devices for app in device.apps or {})
    if not config.gacha.enabled:
        return
    for game in config.gacha.games:
        yield GAMES[game].short
        yield GAMES[game].name
    for game in config.gacha.games:
        yield from list(rosters.get(game, ()))[:ROSTER_NAMES_PER_GAME]


def _spoken(slugs: Iterable[str]) -> Iterator[str]:
    """A slug as it is said: "mau_tidur" is "mau tidur"."""
    return (slug.replace("_", " ") for slug in slugs)


class Vocabulary:
    """The hotwords of this run: the config's, built at start, and the rosters read since.

    The voice and the phone threads read `text`; the brain's turns call `set_roster`.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._rosters: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._text = build_hotwords(config)

    def text(self) -> str:
        with self._lock:
            return self._text

    def set_roster(self, game: str, names: Sequence[str]) -> None:
        """Take `game`'s roster names (most used first) in place of its earlier ones."""
        with self._lock:
            self._rosters[game] = list(names)
            chosen = _chosen(self._config, self._rosters)
            text = SEPARATOR.join(chosen)
            changed = text != self._text
            self._text = text
        if changed:
            # Never the names: only how many there are.
            logger.info("Whisper hotwords now hold %d terms", len(chosen))
