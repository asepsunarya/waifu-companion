"""Decides whether something the owner said is addressed to the assistant.

Speech counts when it contains a wake word anywhere ("Cyrene, nyalakan lampu",
"matikan TV, Cyrene") or starts inside the follow-up window that opens after
each spoken reply. Whisper spells the name inconsistently, so Latin wake words
match word by word with a difflib tolerance; Japanese wake words match as
substrings because Japanese is written without spaces.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_RATIO = 0.8
# Everyday Indonesian words within the fuzzy tolerance of a common wake-word spelling
# ("sirene" ~ "syrene"/"sairen", "sirine" ~ "sairin", "saring" ~ "sairin"), so talk about
# a siren or a filter does not wake her. They wake only when listed verbatim.
EVERYDAY_LOOKALIKES = frozenset({"sirene", "sirine", "saring"})

_WORD = re.compile(r"[^\W_]+")
_JAPANESE = re.compile("[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_LEADING_SEPARATORS = re.compile(r"^[\s,.!?;:、。！？…~-]+")
_TRAILING_SEPARATORS = re.compile(r"[\s,.!?;:、。！？…~-]+$")


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    command: str
    reason: str


@dataclass(frozen=True)
class _WakeWord:
    words: tuple[str, ...]
    japanese: bool

    @property
    def length(self) -> int:
        return sum(len(word) for word in self.words)


class WakeGate:
    """Wake-word matching plus the follow-up window, on an injectable monotonic clock."""

    def __init__(
        self,
        wake_words: list[str],
        followup_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        parsed = [_parse_wake_word(word) for word in wake_words]
        # Longest first, so "hai cyrene" removes the greeting together with the name.
        self._wake_words = sorted(
            (wake for wake in parsed if wake.words), key=lambda wake: wake.length, reverse=True
        )
        self._followup_seconds = followup_seconds
        self._clock = clock
        self._followup_until: float | None = None

    def check(self, text: str, *, heard_at: float | None = None) -> GateResult:
        """Accept `text` if it is addressed to the assistant; `command` is what to act on.

        `heard_at` is when the owner started speaking (same clock as the gate), so a
        follow-up begun inside the window still counts after slow transcription.
        """
        text = text.strip()
        if not _WORD.search(text):
            return GateResult(accepted=False, command="", reason="empty")
        if self._in_followup(heard_at):
            return GateResult(accepted=True, command=text, reason="followup")
        span = self._find_wake_word(text)
        if span is None:
            return GateResult(accepted=False, command="", reason="no_wake_word")
        return GateResult(accepted=True, command=_remove_span(text, *span), reason="wake_word")

    def open_followup(self) -> None:
        self._followup_until = self._clock() + self._followup_seconds

    def close(self) -> None:
        self._followup_until = None

    def _in_followup(self, heard_at: float | None) -> bool:
        if self._followup_until is None:
            return False
        moment = self._clock() if heard_at is None else heard_at
        return moment < self._followup_until

    def _find_wake_word(self, text: str) -> tuple[int, int] | None:
        """Span in `text` of the longest wake word it contains (that word's first occurrence)."""
        normalised, origin = _normalise_with_origin(text)
        tokens = list(_WORD.finditer(normalised))
        for wake in self._wake_words:
            span = (
                _match_japanese(wake, normalised) if wake.japanese else _match_words(wake, tokens)
            )
            if span is not None:
                start, end = span
                return origin[start], origin[end - 1] + 1
        return None


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _normalise_with_origin(text: str) -> tuple[str, list[int]]:
    """Normalised text plus, for each of its characters, the index it came from in `text`."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for normalised in _normalise(char):
            chars.append(normalised)
            origin.append(index)
    return "".join(chars), origin


def _parse_wake_word(word: str) -> _WakeWord:
    normalised = _normalise(word)
    return _WakeWord(
        words=tuple(_WORD.findall(normalised)), japanese=bool(_JAPANESE.search(normalised))
    )


def _match_japanese(wake: _WakeWord, normalised: str) -> tuple[int, int] | None:
    needle = "".join(wake.words)
    start = normalised.find(needle)
    return None if start < 0 else (start, start + len(needle))


def _match_words(wake: _WakeWord, tokens: list[re.Match[str]]) -> tuple[int, int] | None:
    size = len(wake.words)
    for first in range(len(tokens) - size + 1):
        window = tokens[first : first + size]
        if all(
            _sounds_like(token.group(), word)
            for token, word in zip(window, wake.words, strict=True)
        ):
            return window[0].start(), window[-1].end()
    return None


def _sounds_like(token: str, wake_word: str) -> bool:
    if token == wake_word:
        return True
    if token in EVERYDAY_LOOKALIKES:
        return False
    return SequenceMatcher(None, token, wake_word).ratio() >= FUZZY_RATIO


def _remove_span(text: str, start: int, end: int) -> str:
    before = _TRAILING_SEPARATORS.sub("", text[:start])
    after = _LEADING_SEPARATORS.sub("", text[end:])
    return " ".join(part for part in (before, after) if part)
