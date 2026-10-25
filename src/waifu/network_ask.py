"""Whether Master's whole utterance asks how the internet or the HoYo servers are, and her answer.

"Internetnya kenapa?", "internet mati ya?", "kok lemot?", "server HoYo down?": the voice loop
answers these from a check made right then (`NetworkAnswers`), without the brain, so she answers
also while the internet, and so Claude, is out of reach. Like "ulangi" (`waifu.repeat`), only
whole phrases count: left out are her names and wake words, what she calls the owner, "master",
and a few softeners, and what is left must be one of the phrases exactly. A subject (internet,
wifi, koneksi, jaringan) and a question word ("lagi" between them allowed), after "kok", "apa",
"apakah", "kenapa", or nothing, or "kenapa" or "cek" and a subject; "kok lemot"; for the HoYo
servers the same with a HoYo word (Whisper's spellings too), "server" before it or not. Anything
with another word ("internetnya kenapa terus restart router") goes to the brain as always.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence

from waifu.network import NetworkCheck, Topic, answer_line

logger = logging.getLogger(__name__)

CHECK_FAILED = "Maaf {owner}, internetnya tidak bisa kucek sekarang."
# Runs of letters and digits in any script, as `waifu.repeat` splits Master's words.
_WORD = re.compile(r"[^\W_]+")
_SKIPPED = frozenset(
    {"master", "dong", "deh", "ya", "sih", "nih", "kah", "coba", "tolong", "banget"}
)
_SUBJECTS = (
    ("internet",),
    ("internetnya",),
    ("wifi",),
    ("wifinya",),
    ("wi", "fi"),  # "Wi-Fi"
    ("wi", "fi", "nya"),
    ("koneksi",),
    ("koneksinya",),
    ("jaringan",),
    ("jaringannya",),
)
_QUESTIONS = (
    "kenapa",
    "mati",
    "putus",
    "lemot",
    "lambat",
    "down",
    "error",
    "bermasalah",
    "gangguan",
    "aman",
    "lancar",
    "normal",
    "gimana",
)
_OPENERS = ((), ("kok",), ("apa",), ("apakah",), ("kenapa",))
_HOYO_WORDS = (
    ("hoyo",),
    ("hoyoverse",),
    ("hoyo", "verse"),
    ("hoyolab",),
    ("hoyo", "lab"),
    ("mihoyo",),
)
_HOYO_SUBJECTS = tuple((*server, *word) for server in ((), ("server",)) for word in _HOYO_WORDS)


def _questions(subjects: Sequence[tuple[str, ...]]) -> frozenset[tuple[str, ...]]:
    """Each subject with a question word, "lagi" between them or not, after an opener or not;
    and "kenapa" or "cek" with the subject."""
    asked = {
        (*opener, *subject, *between, question)
        for opener in _OPENERS
        for subject in subjects
        for between in ((), ("lagi",))
        for question in _QUESTIONS
    }
    return frozenset(
        asked | {(verb, *subject) for verb in ("kenapa", "cek") for subject in subjects}
    )


INTERNET_PHRASES = _questions(_SUBJECTS) | {("kok", "lemot"), ("kok", "lambat")}
HOYO_PHRASES = _questions(_HOYO_SUBJECTS) | {("server", *word) for word in _HOYO_WORDS}


def network_question(text: str, names: Iterable[str] = ()) -> Topic | None:
    """The topic `text` asks about and nothing else ("internet" or "hoyo"), such as "Cyrene,
    internetnya kenapa?"; None for anything else. `names` are her name, her wake words, and
    what she calls the owner."""
    skipped = _SKIPPED | {word for name in names for word in _words(name)}
    left = tuple(word for word in _words(text) if word not in skipped)
    if left in INTERNET_PHRASES:
        return "internet"
    if left in HOYO_PHRASES:
        return "hoyo"
    return None


class NetworkAnswers:
    """Her answer to Master's question, from a check made now (about 3 s at most)."""

    def __init__(self, check: NetworkCheck, *, owner_call: str) -> None:
        self._check = check
        self._owner_call = owner_call

    def answer(self, topic: Topic) -> str:
        """What she says; never raises: an unexpected failure gets an apology."""
        try:
            finding = self._check.now(hoyo=topic == "hoyo")
        except Exception:
            logger.exception("Could not check the internet for Master's question")
            return CHECK_FAILED.format(owner=self._owner_call)
        logger.info("Told Master the internet's status (%s)", finding.status)
        return answer_line(finding, topic, self._owner_call)


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())
