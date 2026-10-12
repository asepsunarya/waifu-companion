"""Emotion tags that open a reply, like "[senang] おかえりなさい, Master!".

The persona asks the model to start every reply with one Indonesian tag in
square brackets; the avatar shows that emotion. Tags are never spoken or
printed, so every emotion tag in the reply is removed, wherever it is, also in
the full-width brackets of Japanese text (［senang］, 【senang】). Other
bracketed text ("[1]", "[catatan]") is the model's own and stays.
"""

from __future__ import annotations

import re

NEUTRAL = "neutral"
EMOTIONS = (NEUTRAL, "happy", "sad", "angry", "surprised", "relaxed")

# The Indonesian tag the persona asks for; the English name is a tag for itself too.
_INDONESIAN_TAGS = {
    "neutral": "netral",
    "happy": "senang",
    "sad": "sedih",
    "angry": "marah",
    "surprised": "kaget",
    "relaxed": "santai",
}
_CLOSING_PUNCTUATION = tuple(".,!?;:)…。、！？」』）")
# In a Japanese reply the model may write the brackets full width: ［senang］ or 【senang】.
_OPEN = "[［【"
_CLOSE = "]］】"


def _tag_pattern(*, named: bool) -> str:
    """`[tag]` with optional inner spaces; when `named`, the group name is the emotion."""
    names = "|".join(
        f"(?P<{emotion}>{tag}|{emotion})" if named else f"{tag}|{emotion}"
        for emotion, tag in _INDONESIAN_TAGS.items()
    )
    return rf"[{re.escape(_OPEN)}]\s*(?:{names})\s*[{re.escape(_CLOSE)}]"


_LEADING_TAG = re.compile(rf"\s*{_tag_pattern(named=True)}", re.IGNORECASE)
_TAG = _tag_pattern(named=False)
# A run of tags with the whitespace around it, so removing it leaves one gap, not several.
_TAG_RUN = re.compile(rf"\s*{_TAG}(?:\s*{_TAG})*\s*", re.IGNORECASE)


def split_emotion(text: str) -> tuple[str, str]:
    """The emotion of the reply's leading tag (else neutral) and the reply without any tag.

    Removing a tag leaves one space where it separated words and none before the
    punctuation that followed it; the returned text is stripped.
    """
    leading = _LEADING_TAG.match(text)
    emotion = (leading.lastgroup if leading else None) or NEUTRAL
    return emotion, _TAG_RUN.sub(_gap, text).strip()


def _gap(run: re.Match[str]) -> str:
    """What replaces a removed run of tags."""
    removed = run.group()
    separated_words = removed[0].isspace() or removed[-1].isspace()
    before_punctuation = run.string.startswith(_CLOSING_PUNCTUATION, run.end())
    return " " if separated_words and not before_punctuation else ""
