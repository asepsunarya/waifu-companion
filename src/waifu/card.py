"""Text cards (v1.55): the long part of a reply, shown instead of read aloud.

The system prompt (`brain.persona`) asks her to say a short gist first and then, only when it is
needed, a line "[[kartu: <judul pendek>]]" followed by the card's body: plain lines, "- "
bullets, links as plain URLs. `split_card` splits a reply into what she says and the card, so
only the text before the marker is ever spoken (her voice, "ulangi", the phone's voice note). It
is tolerant, since a local model writes the marker loosely, and degrades safely: the marker's
syntax and the card's body are never spoken, and a marker without a body is only dropped.
`is_card_request` is Master's whole "tampilkan lagi kartunya", answered without the brain like
"ulangi" (`waifu.repeat`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from waifu.avatar.emotion import split_emotion

MAX_TITLE_CHARS = 60
MAX_BODY_CHARS = 4000
MAX_BODY_LINES = 60
# One ntfy message holds at most 4096 bytes of text; a longer one arrives as an attachment.
MESSAGE_MAX_BYTES = 3800
ELLIPSIS = "…"

_OPEN = re.escape("[［")
_CLOSE = re.escape("]］")
# "[[kartu: Judul]]" in any case, with spaces anywhere, full-width brackets or colon, and no
# title; two opening brackets close with two, or with one or none at the end of the line.
_DOUBLE = (
    rf"[{_OPEN}]{{2}}[ \t]*kartu\b[ \t]*[:：]?(?P<double>[^\n]*?)"
    rf"(?:[{_CLOSE}]{{2}}|[{_CLOSE}]?[ \t]*(?=\n|$))"
)
# One opening bracket is a marker only with the colon ("[kartu: Judul]", closed with one bracket
# or none at the end of the line), or as a whole line ("[kartu Judul]"): "[kartu kredit]" in a
# sentence, "[kartu nama] = meishi", or "[Kartu Prakerja](url)" is what she wrote.
_COLON = rf"[{_OPEN}][ \t]*kartu[ \t]*[:：](?P<colon>[^\n]*?)(?:[{_CLOSE}]|[ \t]*(?=\n|$))"
_LINE = rf"^[ \t]*[{_OPEN}][ \t]*kartu\b[ \t]*(?P<line>[^\n{_CLOSE}]*)[{_CLOSE}][ \t\r]*$"
_MARKER = re.compile(f"{_DOUBLE}|{_COLON}|{_LINE}", re.IGNORECASE | re.MULTILINE)
# A marker inside the body: a whole line in one bracket stays there as written.
_LATER_MARKER = re.compile(f"{_DOUBLE}|{_COLON}", re.IGNORECASE)

# Runs of letters and digits in any script, as `waifu.repeat` splits Master's words.
_WORD = re.compile(r"[^\W_]+")
# Always left out of a re-show request, like her names: "master", and softeners.
_SKIPPED = frozenset({"master", "dong", "deh", "ya", "coba", "tolong", "sih", "nih"})
_CARD_WORDS = frozenset({"kartu", "kartunya"})
_SHOW_VERBS = frozenset(
    {
        "tampilkan",
        "tampilin",
        "tunjukkan",
        "tunjukin",
        "munculkan",
        "munculin",
        "buka",
        "lihat",
        "liat",
    }
)
# Words that point at the card already shown; "kartu" alone needs one ("buka kartu" is an idiom).
# "Mana" is not one: "kartu yang mana?" asks which card (a credit card, say), "mana kartunya?" asks
# for hers.
_POINTING = frozenset({"lagi", "tadi", "itu"})
_REQUEST_WORDS = _SHOW_VERBS | _POINTING | {"yang", "mana"}
_MAX_REQUEST_WORDS = 4


@dataclass(frozen=True)
class Card:
    """A card she shows: a short title (empty when she gave none) and its body of plain lines."""

    title: str
    body: str

    def allows_link(self, url: str) -> bool:
        """Whether `url` is written in the body as it is: the only links the card may open."""
        return bool(url) and url in self.body


def split_card(text: str) -> tuple[str, Card | None]:
    """What she says of `text` and its card, if any.

    The first marker splits the reply, also in the middle of a line: the speech is the text
    before it, stripped, and the body what follows it. Without a marker `text` comes back as it
    is; a body left empty once cleaned (`_body`) gives no card, and its title is never spoken.
    """
    marker = _MARKER.search(text)
    if marker is None:
        return text, None
    speech = text[: marker.start()].strip()
    body = _body(text[marker.end() :].lstrip(" \t"))
    return speech, Card(_title(marker), body) if body else None


def before_card(text: str, addition: str) -> str:
    """`text` with `addition` (a question she must ask) at the end of its spoken part: before a
    card marker, so it is still said, or after the text as ever without one."""
    marker = _MARKER.search(text)
    if marker is None:
        return f"{text} {addition}" if text else addition
    spoken = text[: marker.start()].strip()
    spoken = f"{spoken} {addition}" if spoken else addition
    return f"{spoken}\n{text[marker.start() :]}"


def card_message(speech: str, card: Card | None) -> str:
    """`speech`, then the card's title and body after a blank line, as one plain message cut to
    MESSAGE_MAX_BYTES of UTF-8 (a push to the phone)."""
    if card is None:
        return speech
    shown = f"{card.title}\n{card.body}" if card.title else card.body
    message = f"{speech}\n\n{shown}"
    encoded = message.encode("utf-8")
    if len(encoded) <= MESSAGE_MAX_BYTES:
        return message
    room = MESSAGE_MAX_BYTES - len(ELLIPSIS.encode("utf-8"))
    return encoded[:room].decode("utf-8", "ignore").rstrip() + ELLIPSIS


def is_card_request(text: str, names: Iterable[str] = ()) -> bool:
    """Whether `text` asks her to show the last card again and nothing else, such as "Cyrene,
    tampilkan lagi kartunya" or "kartu tadi?".

    `names` (her names, wake words, and what she calls the owner) are left out with "master"
    and the softeners; what is left is one card word and one to four words that show or point
    at it, and "kartu" (unlike "kartunya") needs one that points ("lagi", "tadi", "itu").
    """
    skipped = _SKIPPED | {word for name in names for word in _words(name)}
    left = [word for word in _words(text) if word not in skipped]
    cards = [word for word in left if word in _CARD_WORDS]
    others = [word for word in left if word not in _CARD_WORDS]
    if len(cards) != 1 or not 1 <= len(others) <= _MAX_REQUEST_WORDS:
        return False
    if not _REQUEST_WORDS.issuperset(others):
        return False
    return cards[0] == "kartunya" or not _POINTING.isdisjoint(others)


def _title(marker: re.Match[str]) -> str:
    """The marker's title without emotion tags or runs of spaces, cut to MAX_TITLE_CHARS."""
    raw = next(title for title in marker.groupdict().values() if title is not None)
    return _cut(" ".join(split_emotion(raw)[1].split()), MAX_TITLE_CHARS)


def _body(raw: str) -> str:
    """The card's lines: emotion tags removed and indentation kept, a later marker's syntax
    (`_LATER_MARKER`) replaced by its title, one blank line at most between lines and none at
    either end, cut to MAX_BODY_LINES and MAX_BODY_CHARS."""
    kept: list[str] = []
    for raw_line in raw.splitlines():
        line = _without_tags(_LATER_MARKER.sub(_title, raw_line))
        if line or (kept and kept[-1]):
            kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    if len(kept) > MAX_BODY_LINES:
        kept = [*kept[: MAX_BODY_LINES - 1], ELLIPSIS]
    return _cut("\n".join(kept), MAX_BODY_CHARS)


def _without_tags(line: str) -> str:
    """`line` without its emotion tags and trailing spaces, its indentation kept."""
    words = split_emotion(line)[1]
    if not words:
        return ""
    stripped = line.rstrip()
    return stripped[: len(stripped) - len(stripped.lstrip())] + words


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + ELLIPSIS


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())
