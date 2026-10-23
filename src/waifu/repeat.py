"""Whether Master's whole utterance asks her to repeat what she just said ("ulangi").

The voice loop answers such a request by saying her last words again, without the brain, so
only a small set of whole phrases counts: left out are her names and wake words, what she
calls the owner, "master", and a few softeners ("ulangi dong"), and what is left must be one of
the phrases exactly. Anything with another word ("ulangi lagunya", "putar sekali lagi") goes to
the brain as always. Unlike a yes to her question, a question mark is fine ("hah?").
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# Runs of letters and digits in any script, as brain.confirm splits Master's answer.
_WORD = re.compile(r"[^\W_]+")
# Always left out, like her names: "master", and words that soften a request without changing it.
_SKIPPED = frozenset({"master", "dong", "deh", "ya", "coba", "tolong"})
# With these, "sekali lagi" asks her to try again ("coba sekali lagi" after a failure), which is
# a new attempt for the brain, not a repeat; they soften only the other phrases ("coba ulangi").
_TRY_AGAIN = frozenset({"coba", "tolong"})
_ONCE_MORE = ("sekali", "lagi")
_PHRASES = frozenset(
    {
        ("ulangi",),
        ("ulangi", "lagi"),
        ("ulang", "lagi"),
        ("diulang",),
        ("diulangi",),
        ("bisa", "diulang"),
        ("bisa", "diulangi"),
        ("tadi", "bilang", "apa"),
        ("tadi", "kamu", "bilang", "apa"),
        ("kamu", "tadi", "bilang", "apa"),
        ("kamu", "bilang", "apa", "tadi"),
        ("bilang", "apa", "tadi"),
        ("apa", "tadi"),
        ("sekali", "lagi"),
        ("hah",),
    }
)
# Every phrase, also after "maaf", and "maaf, apa?" (a bare "apa?" is not one).
REPEAT_PHRASES = _PHRASES | {("maaf", *phrase) for phrase in _PHRASES} | {("maaf", "apa")}


def is_repeat_request(text: str, names: Iterable[str] = ()) -> bool:
    """Whether `text` is a repeat request and nothing else, such as "Cyrene, ulangi dong."

    `names` are her name, the wake words she answers to, and what she calls the owner; their
    words are left out with "master" and the softeners ("dong", "ya", "tolong", ...), and the
    words left must be one of REPEAT_PHRASES. "Sekali lagi" with "coba" or "tolong" ("coba
    sekali lagi") asks her to try again, so it is not one.
    """
    words = _words(text)
    skipped = _SKIPPED | {word for name in names for word in _words(name)}
    left = tuple(word for word in words if word not in skipped)
    if left[-len(_ONCE_MORE) :] == _ONCE_MORE and not _TRY_AGAIN.isdisjoint(words):
        return False
    return left in REPEAT_PHRASES


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())
