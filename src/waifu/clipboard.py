"""What Master copied on the PC, for "artinya apa yang kucopy?": the clipboard's text.

`read_clipboard` reads the clipboard's `CF_UNICODETEXT` once, when the clipboard tool asks, and
nothing more: no watching, no other format, never writing it. A copy a password manager marked
(it registers "ExcludeClipboardContentFromMonitorProcessing" or "Clipboard Viewer Ignore" beside
a secret it copies, so clipboard history and monitors leave it out) is never read at all. Another
program may hold the clipboard open for a moment (an Office program, a clipboard manager, a
remote desktop), so opening it is tried a few times. The memory behind the text belongs to
Windows: it is locked and unlocked, never freed, and read only up to the cap, so a huge copy
costs nothing. The text is never logged, only its length.

`looks_private` tells a copy that looks like a secret (a password, a token, an OTP or PIN, a card
or account number, a private key), which the tool refuses whole.

user32 and kernel32 come through ctypes with Microsoft's documented signatures (winuser.h,
winbase.h): `GetClipboardData` gives a HANDLE and `GlobalLock` a pointer, so a 64-bit value is
never cut to an int. They are looked up at every call, never at import, so the test suite's guard
on the clipboard functions always applies.
"""

from __future__ import annotations

import ctypes
import logging
import re
import string
import time
import unicodedata
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from waifu.memory import looks_secret

logger = logging.getLogger(__name__)

CF_UNICODETEXT = 13
# The formats a password manager registers beside a secret it copies.
PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
OPEN_TRIES = 5
OPEN_RETRY_S = 0.03

# What a read found: the text, no text (an image, a file, nothing), a copy a password manager
# marked, or a clipboard another program held open through every try.
TEXT = "text"
NOT_TEXT = "not_text"
PRIVATE = "private"
BUSY = "busy"

# A lone word of printable ASCII, as a password is.
_LONE_WORD = re.compile(r"[\x21-\x7e]{8,128}")
_CHARACTER_KINDS = (
    re.compile(r"[a-z]"),
    re.compile(r"[A-Z]"),
    re.compile(r"[0-9]"),
    re.compile(r"[^a-zA-Z0-9]"),
)
_PASSWORD_KINDS = 3
_PIN = re.compile(r"[0-9]+(?:[ -][0-9]+)?")
_PIN_DIGITS = range(4, 9)
_NUMBER = re.compile(r"[0-9 -]+")
_NUMBER_DIGITS = 10
_KEY_MARKS = ("-----BEGIN", "PRIVATE KEY")
# What a message sending a one-time code calls it, in any case: never a bare "kode", "code", or
# "コード", which a postcode or a promo code is too.
_CODE_WORDS = re.compile(
    r"(?<![a-z])(?:otp|passcode|one[\s-]*time[\s-]*(?:pass(?:word|code)?|code|pin)"
    r"|(?:verification|verify|log[\s-]*in|sign[\s-]*in|security|auth(?:entication)?"
    r"|confirmation)[\s-]*code"
    r"|kode[\s-]+(?:verifikasi|keamanan|login|masuk|konfirmasi|autentikasi)"
    r"|認証コード|認証番号|確認コード|確認番号|ワンタイム|暗証|セキュリティコード|ログインコード)"
    r"(?:-?(?:nya|ku|mu))?(?![a-z])",
    re.IGNORECASE,
)
# Digits in one or two groups split by a space or a dash, as a code is written ("482 910").
_CODE_DIGITS = re.compile(r"(?<![0-9])[0-9]+(?:[ -][0-9]+)?(?![0-9])")
_CODE_GROUP_SPLIT = re.compile(r"[ -]")
# How far from its code a message's code word may be, on either side.
_CODE_NEAR_CHARS = 40
# A line holding a short password label and one word ("pass: Kucing123").
_PASSWORD_LINE = re.compile(
    r"^[ \t]*(?:pass|pw|pwd)[ \t]*[:=][ \t]*\S+[ \t]*$", re.IGNORECASE | re.MULTILINE
)
_SURROGATE = re.compile("[\ud800-\udfff]")

_Signature = tuple[list[Any], Any]
_FUNCTIONS: Mapping[str, tuple[str, _Signature]] = {
    "RegisterClipboardFormatW": ("user32", ([wintypes.LPCWSTR], wintypes.UINT)),
    "IsClipboardFormatAvailable": ("user32", ([wintypes.UINT], wintypes.BOOL)),
    "OpenClipboard": ("user32", ([wintypes.HWND], wintypes.BOOL)),
    "GetClipboardData": ("user32", ([wintypes.UINT], wintypes.HANDLE)),
    "CloseClipboard": ("user32", ([], wintypes.BOOL)),
    "GlobalLock": ("kernel32", ([wintypes.HGLOBAL], wintypes.LPVOID)),
    "GlobalSize": ("kernel32", ([wintypes.HGLOBAL], ctypes.c_size_t)),  # SIZE_T
    "GlobalUnlock": ("kernel32", ([wintypes.HGLOBAL], wintypes.BOOL)),
}


def _windows(name: str) -> Callable[..., Any]:
    """The Windows function `name` with its full signature, loaded only when it is called."""
    library, (argtypes, restype) = _FUNCTIONS[name]

    def call(*args: Any) -> Any:
        function = getattr(ctypes.WinDLL(library, use_last_error=True), name)
        function.argtypes = argtypes
        function.restype = restype
        return function(*args)

    return call


_register_clipboard_format = _windows("RegisterClipboardFormatW")
_is_clipboard_format_available = _windows("IsClipboardFormatAvailable")
_open_clipboard = _windows("OpenClipboard")
_get_clipboard_data = _windows("GetClipboardData")
_close_clipboard = _windows("CloseClipboard")
_global_lock = _windows("GlobalLock")
_global_size = _windows("GlobalSize")
_global_unlock = _windows("GlobalUnlock")


@dataclass(frozen=True)
class ClipboardRead:
    """What a read found (`kind`); with TEXT, the text, cut to the cap when `cut`."""

    kind: str
    text: str = ""
    cut: bool = False


def read_clipboard(max_chars: int, *, sleep: Callable[[float], None] = time.sleep) -> ClipboardRead:
    """The clipboard's text, at most `max_chars` characters, or why there is none.

    A copy a password manager marked is PRIVATE and its text is never read. Without text on the
    clipboard, NOT_TEXT. A clipboard another program holds is tried OPEN_TRIES times,
    OPEN_RETRY_S apart, then BUSY. The clipboard is closed again on this thread whenever it
    opened, and the memory behind the text unlocked. OSError when that memory cannot be locked.
    """
    if any(_marked(name) for name in PRIVATE_FORMATS):
        logger.info("The clipboard holds a copy a password manager marked as private")
        return ClipboardRead(PRIVATE)
    if not _is_clipboard_format_available(CF_UNICODETEXT):
        return ClipboardRead(NOT_TEXT)
    if not _open(sleep):
        logger.info("The clipboard is held by another program")
        return ClipboardRead(BUSY)
    try:
        handle = _get_clipboard_data(CF_UNICODETEXT)
        if not handle:
            return ClipboardRead(NOT_TEXT)
        return _read_text(handle, max_chars)
    finally:
        _close_clipboard()


def looks_private(text: str) -> bool:
    """Whether a copy looks like a secret: what `memory.looks_secret` catches (a secret word with
    its value, a long run of letters and digits, a card number), or, the whole text stripped,
    one word of 8-128 ASCII characters mixing at least three of lower case, upper case, digits,
    and symbols (a password) unless it is letters alone with punctuation around them
    ("Welcome!", "#HonkaiStarRail"), 4-8 digits with at most one space or dash among them (an
    OTP or a PIN), only digits, spaces, and dashes with 10 or more digits (an account or phone
    number), or a text holding a private key, a message with its one-time code (OTP or
    verification wording within _CODE_NEAR_CHARS characters of 4-8 digits, on either side), or a
    line "pass: <one word>" ("pw", "pwd", ":" or "="). Full-width characters count as their
    ASCII forms (NFKC)."""
    text = unicodedata.normalize("NFKC", text)
    if (
        looks_secret(text)
        or any(mark in text for mark in _KEY_MARKS)
        or _PASSWORD_LINE.search(text) is not None
        or _holds_a_code_with_its_words(text)
    ):
        return True
    whole = text.strip()
    digits = sum(character.isascii() and character.isdigit() for character in whole)
    return (
        _looks_like_password(whole)
        or (_PIN.fullmatch(whole) is not None and digits in _PIN_DIGITS)
        or (_NUMBER.fullmatch(whole) is not None and digits >= _NUMBER_DIGITS)
    )


def _looks_like_password(whole: str) -> bool:
    """Whether `whole` is one word mixing at least _PASSWORD_KINDS kinds of characters; the
    punctuation around a word of letters alone is no symbol of it."""
    if _LONE_WORD.fullmatch(whole) is None or whole.strip(string.punctuation).isalpha():
        return False
    return sum(kind.search(whole) is not None for kind in _CHARACTER_KINDS) >= _PASSWORD_KINDS


def _holds_a_code_with_its_words(text: str) -> bool:
    """Whether a one-time code's wording comes near a code of 4-8 digits in `text`."""
    words = [match.span() for match in _CODE_WORDS.finditer(text)]
    if not words:
        return False
    codes = [match.span() for match in _CODE_DIGITS.finditer(text) if _is_code(match.group())]
    return any(
        max(code_start - word_end, word_start - code_end) <= _CODE_NEAR_CHARS
        for word_start, word_end in words
        for code_start, code_end in codes
    )


def _is_code(digits: str) -> bool:
    """Whether a run of digit groups is a code: 4-8 digits in all, or a group of 4-8 alone."""
    groups = _CODE_GROUP_SPLIT.split(digits)
    return sum(map(len, groups)) in _PIN_DIGITS or any(
        len(group) in _PIN_DIGITS for group in groups
    )


def _marked(name: str) -> bool:
    """Whether the registered format `name` is on the clipboard."""
    number = _register_clipboard_format(name)
    return bool(number) and bool(_is_clipboard_format_available(number))


def _open(sleep: Callable[[float], None]) -> bool:
    """Open the clipboard for this thread, trying again while another program holds it."""
    for attempt in range(OPEN_TRIES):
        if attempt:
            sleep(OPEN_RETRY_S)
        if _open_clipboard(None):
            return True
    return False


def _read_text(handle: int, max_chars: int) -> ClipboardRead:
    """The text in the memory `handle`, read up to one UTF-16 unit past `max_chars` and within
    the memory's size, cut at its first NUL and then to `max_chars`; a surrogate left without
    its other half (by the cut, or so copied) is dropped, so the text encodes as UTF-8."""
    pointer = _global_lock(handle)
    if not pointer:
        raise OSError(f"GlobalLock failed (Windows error {ctypes.get_last_error()})")
    try:
        held = _global_size(handle) // ctypes.sizeof(ctypes.c_wchar)
        units = min(held, max_chars + 1)
        raw = ctypes.wstring_at(pointer, units) if units else ""
    finally:
        _global_unlock(handle)  # 0 once unlocked: not an error
    text, nul, _ = raw.partition("\0")
    cut = len(text) > max_chars or (not nul and held > units)
    text = _SURROGATE.sub("", text[:max_chars])
    logger.debug("Read %d characters from the clipboard", len(text))
    return ClipboardRead(TEXT, text, cut)
