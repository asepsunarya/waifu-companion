"""Whether Master's whole utterance asks what she heard ("tadi kamu denger apa?"), and her answer.

The voice loop answers such a request from its diagnosis log (`waifu.voice.diagnosis`), without
the brain: the last utterance she heard and why she did or did not act on it. Like "ulangi"
(`waifu.repeat`), only whole phrases count: left out are her names and wake words, what she
calls the owner, "master", and a few softeners, and what is left must be one of HEARD_PHRASES
exactly. None of them is a repeat phrase: "tadi bilang apa?" asks for her words, "tadi kamu
denger apa?" for Master's.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from waifu.voice.diagnosis import ACCEPTED, EMPTY, NO_WAKE, UtteranceRecord

# Runs of letters and digits in any script, as `waifu.repeat` splits Master's words.
_WORD = re.compile(r"[^\W_]+")
_SKIPPED = frozenset({"master", "dong", "deh", "ya", "coba", "tolong"})
_PHRASES = frozenset(
    phrase
    for verb in ("denger", "dengar")
    for phrase in (
        ("tadi", "kamu", verb, "apa"),
        ("kamu", "tadi", verb, "apa"),
        ("kamu", verb, "apa", "tadi"),
        ("tadi", verb, "apa"),
        (verb, "apa", "tadi"),
    )
)
# Every phrase, also after "maaf".
HEARD_PHRASES = _PHRASES | {("maaf", *phrase) for phrase in _PHRASES}

NOTHING_HEARD = "Belum ada yang kudengar, {owner}."
HEARD_ANSWERED = 'Tadi aku dengar "{transcript}", dan sudah kujawab.'
HEARD_WITHOUT_NAME = (
    'Tadi aku dengar "{transcript}", tapi kamu tidak memanggil namaku, jadi tidak kujawab.'
)
HEARD_NO_WORDS = "Tadi ada suara {seconds} detik, tapi tidak ada kata yang bisa kutangkap."
HEARD_ANOTHER_VOICE = (
    "Tadi ada suara, tapi kemiripannya dengan suaramu cuma {score}, padahal butuh {threshold}, "
    "jadi tidak kujawab."
)
HEARD_WITHOUT_VERIFIER = (
    "Tadi ada suara, tapi pengenal suaramu sedang tidak bisa dipakai, jadi tidak kujawab."
)


def is_heard_request(text: str, names: Iterable[str] = ()) -> bool:
    """Whether `text` asks what she heard and nothing else, such as "Cyrene, tadi kamu denger
    apa?". `names` are her name, her wake words, and what she calls the owner."""
    skipped = _SKIPPED | {word for name in names for word in _words(name)}
    return tuple(word for word in _words(text) if word not in skipped) in HEARD_PHRASES


def heard_answer(record: UtteranceRecord | None, owner_call: str) -> str:
    """What she says about `record` (None: nothing heard), its numbers with a decimal comma."""
    if record is None:
        return NOTHING_HEARD.format(owner=owner_call)
    if record.outcome == ACCEPTED:
        return HEARD_ANSWERED.format(transcript=record.transcript)
    if record.outcome == NO_WAKE:
        return HEARD_WITHOUT_NAME.format(transcript=record.transcript)
    if record.outcome == EMPTY:
        return HEARD_NO_WORDS.format(seconds=_decimal(record.length_s, 1))
    if record.score is None or record.threshold is None:
        return HEARD_WITHOUT_VERIFIER
    return HEARD_ANOTHER_VOICE.format(
        score=_decimal(record.score, 2), threshold=_decimal(record.threshold, 2)
    )


def _decimal(value: float, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())
