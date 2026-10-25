"""Reading what Master copied on the PC: no test reads the real clipboard.

The reader goes through user32 and kernel32; every test but the last puts a fake WinDLL in place
whose clipboard is a buffer in memory, and the last shows that the suite refuses the real one.
"""

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from waifu.clipboard import (
    BUSY,
    NOT_TEXT,
    OPEN_RETRY_S,
    OPEN_TRIES,
    PRIVATE,
    TEXT,
    ClipboardRead,
    looks_private,
    read_clipboard,
)

CF_UNICODETEXT = 13
HANDLE = 0x7FF6_1234_5678  # a 64-bit handle, as Windows gives one
JAPANESE = "お疲れ様でした！また明日ね"
MARKS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")


class FakeClipboard:
    """The clipboard as user32 and kernel32 show it, over a buffer in memory.

    `text` is on it as CF_UNICODETEXT (None: no text); `marks` are registered formats a program
    put beside it; the first `held` OpenClipboard calls fail (another program has it open);
    without `data` GetClipboardData gives nothing, without `locks` GlobalLock fails; `size` is
    what GlobalSize says (default: the whole buffer). `calls` holds the order of the calls."""

    def __init__(
        self,
        text: str | None = JAPANESE,
        *,
        marks=(),
        held: int = 0,
        data: bool = True,
        locks: bool = True,
        size: int | None = None,
    ) -> None:
        self.buffer = None if text is None else ctypes.create_unicode_buffer(text)
        self.size = size if size is not None else ctypes.sizeof(self.buffer or ctypes.c_wchar())
        self.marks = set(marks)
        self.held = held
        self.data = data
        self.locks = locks
        self.calls: list[str] = []
        self.formats: dict[int, str] = {}
        self.open = False
        self.locked = 0

    def register(self, name):
        self.calls.append(f"RegisterClipboardFormatW({name})")
        for number, known in self.formats.items():
            if known == name:
                return number
        number = 0xC000 + len(self.formats)
        self.formats[number] = name
        return number

    def available(self, number):
        self.calls.append(f"IsClipboardFormatAvailable({number})")
        if number == CF_UNICODETEXT:
            return int(self.buffer is not None)
        return int(self.formats.get(number) in self.marks)

    def open_clipboard(self, owner):
        self.calls.append("OpenClipboard")
        assert owner is None  # no window of hers owns it
        assert not self.open
        if self.held:
            self.held -= 1
            return 0
        self.open = True
        return 1

    def get_data(self, number):
        self.calls.append("GetClipboardData")
        assert self.open and number == CF_UNICODETEXT
        return HANDLE if self.data and self.buffer is not None else None

    def lock(self, handle):
        self.calls.append("GlobalLock")
        assert handle == HANDLE
        if not self.locks:
            return None
        self.locked += 1
        return ctypes.addressof(self.buffer)

    def global_size(self, handle):
        self.calls.append("GlobalSize")
        assert handle == HANDLE and self.locked
        return self.size

    def unlock(self, handle):
        self.calls.append("GlobalUnlock")
        assert handle == HANDLE and self.locked
        self.locked -= 1
        return 0  # the lock count reached zero: no error

    def close(self):
        self.calls.append("CloseClipboard")
        assert self.open
        self.open = False
        return 1


class FakeFunction:
    """One Windows function: refuses to run undeclared and records the signature it ran with."""

    def __init__(self, signatures: dict, name: str, behaviour) -> None:
        self.signatures = signatures
        self.name = name
        self.behaviour = behaviour
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        assert self.argtypes is not None, f"{self.name} called without argtypes"
        assert self.restype is not None, f"{self.name} called without restype"
        self.signatures[self.name] = (self.argtypes, self.restype)
        return self.behaviour(*args)


class FakeWindows:
    """ctypes.WinDLL over a FakeClipboard: only its functions exist, so looking anything else
    up (GlobalFree) fails; `loads` holds every library loaded and how."""

    def __init__(self, clipboard: FakeClipboard) -> None:
        self.signatures: dict = {}
        self.loads: list = []
        functions = {
            "user32": {
                "RegisterClipboardFormatW": clipboard.register,
                "IsClipboardFormatAvailable": clipboard.available,
                "OpenClipboard": clipboard.open_clipboard,
                "GetClipboardData": clipboard.get_data,
                "CloseClipboard": clipboard.close,
            },
            "kernel32": {
                "GlobalLock": clipboard.lock,
                "GlobalSize": clipboard.global_size,
                "GlobalUnlock": clipboard.unlock,
            },
        }
        self.libraries = {
            library: {name: FakeFunction(self.signatures, name, call) for name, call in calls}
            for library, calls in ((key, value.items()) for key, value in functions.items())
        }

    def __call__(self, name, **kwargs):
        self.loads.append((name, kwargs))
        return SimpleNamespace(**self.libraries[name])


@pytest.fixture
def install(monkeypatch):
    def put_in_place(clipboard: FakeClipboard) -> FakeWindows:
        windows = FakeWindows(clipboard)
        monkeypatch.setattr(ctypes, "WinDLL", windows, raising=False)
        return windows

    return put_in_place


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


# --- reading ------------------------------------------------------------------------------------


def test_it_reads_the_text_master_copied(install):
    clipboard = FakeClipboard(JAPANESE)
    install(clipboard)

    assert read_clipboard(2000) == ClipboardRead(TEXT, JAPANESE, cut=False)


def test_it_closes_the_clipboard_and_unlocks_its_memory_after_reading(install):
    clipboard = FakeClipboard("Good morning")
    install(clipboard)

    read_clipboard(2000)

    assert not clipboard.open and clipboard.locked == 0
    assert clipboard.calls[-6:] == [
        "OpenClipboard",
        "GetClipboardData",
        "GlobalLock",
        "GlobalSize",
        "GlobalUnlock",
        "CloseClipboard",
    ]


@pytest.mark.parametrize("mark", MARKS)
def test_a_copy_a_password_manager_marked_is_private_and_never_read(install, mark):
    clipboard = FakeClipboard("hunter2-but-longer", marks={mark})
    install(clipboard)

    assert read_clipboard(2000) == ClipboardRead(PRIVATE)
    assert "OpenClipboard" not in clipboard.calls
    assert "GetClipboardData" not in clipboard.calls


def test_both_marks_are_looked_for_before_the_text(install):
    clipboard = FakeClipboard("Good morning")
    install(clipboard)

    read_clipboard(2000)

    assert clipboard.calls[:5] == [
        f"RegisterClipboardFormatW({MARKS[0]})",
        "IsClipboardFormatAvailable(49152)",
        f"RegisterClipboardFormatW({MARKS[1]})",
        "IsClipboardFormatAvailable(49153)",
        f"IsClipboardFormatAvailable({CF_UNICODETEXT})",
    ]


def test_without_text_on_it_the_clipboard_is_not_opened(install):
    clipboard = FakeClipboard(None)  # an image or a file
    install(clipboard)

    assert read_clipboard(2000) == ClipboardRead(NOT_TEXT)
    assert "OpenClipboard" not in clipboard.calls


def test_a_clipboard_held_by_another_program_is_tried_again_then_busy(install):
    clipboard = FakeClipboard("Good morning", held=99)
    install(clipboard)
    sleeps = Sleeps()

    assert read_clipboard(2000, sleep=sleeps) == ClipboardRead(BUSY)

    assert clipboard.calls.count("OpenClipboard") == OPEN_TRIES == 5
    assert sleeps.waits == [OPEN_RETRY_S] * (OPEN_TRIES - 1)
    assert OPEN_RETRY_S == 0.03
    assert "GetClipboardData" not in clipboard.calls
    assert "CloseClipboard" not in clipboard.calls  # never opened, so never closed


def test_a_clipboard_held_for_a_moment_is_read_once_it_is_free(install):
    clipboard = FakeClipboard("Good morning", held=2)
    install(clipboard)
    sleeps = Sleeps()

    assert read_clipboard(2000, sleep=sleeps) == ClipboardRead(TEXT, "Good morning")
    assert sleeps.waits == [OPEN_RETRY_S] * 2


def test_no_data_behind_the_text_format_is_no_text_and_the_clipboard_is_closed(install):
    clipboard = FakeClipboard("Good morning", data=False)
    install(clipboard)

    assert read_clipboard(2000) == ClipboardRead(NOT_TEXT)
    assert not clipboard.open
    assert "GlobalLock" not in clipboard.calls


def test_memory_that_cannot_be_locked_is_an_os_error_and_the_clipboard_is_closed(install):
    clipboard = FakeClipboard("Good morning", locks=False)
    install(clipboard)

    with pytest.raises(OSError, match="GlobalLock failed"):
        read_clipboard(2000)

    assert not clipboard.open
    assert "GlobalUnlock" not in clipboard.calls  # nothing was locked


def test_a_text_longer_than_the_cap_is_cut_to_it(install):
    install(FakeClipboard("a" * 2500))

    assert read_clipboard(2000) == ClipboardRead(TEXT, "a" * 2000, cut=True)


def test_a_text_as_long_as_the_cap_is_whole(install):
    install(FakeClipboard("a" * 2000))

    assert read_clipboard(2000) == ClipboardRead(TEXT, "a" * 2000, cut=False)


def test_it_reads_no_more_than_one_unit_past_the_cap(install, monkeypatch):
    install(FakeClipboard("b" * 100_000))
    sizes: list[int] = []
    real_wstring_at = ctypes.wstring_at

    def wstring_at(pointer, size=-1):
        sizes.append(size)
        return real_wstring_at(pointer, size)

    monkeypatch.setattr(ctypes, "wstring_at", wstring_at)

    assert read_clipboard(10) == ClipboardRead(TEXT, "b" * 10, cut=True)
    assert sizes == [11]


def test_it_reads_only_within_the_memory_windows_gave(install):
    # Six UTF-16 units in the buffer, but the memory block holds three of them: that is all.
    install(FakeClipboard("abcdef", size=6))

    assert read_clipboard(2000) == ClipboardRead(TEXT, "abc", cut=False)


def test_the_text_ends_at_its_first_nul(install):
    install(FakeClipboard("Good\0morning"))

    assert read_clipboard(2000) == ClipboardRead(TEXT, "Good", cut=False)


def test_a_cut_through_a_character_outside_the_bmp_drops_its_lone_half(install):
    # "😀" is two UTF-16 units: a cap of 3 units cuts the second one in half.
    install(FakeClipboard("ab😀cd"))

    read = read_clipboard(2)

    assert read == ClipboardRead(TEXT, "ab", cut=True)


def test_a_cut_text_always_encodes_as_utf_8(install):
    install(FakeClipboard("😀" * 50))

    read = read_clipboard(6)

    assert read.cut
    assert read.text == "😀" * 3  # the seven units read hold three and a half
    read.text.encode("utf-8")


def test_a_lone_surrogate_on_the_clipboard_is_dropped(install):
    install(FakeClipboard("ab\ud800cd"))

    assert read_clipboard(2000) == ClipboardRead(TEXT, "abcd")


def test_the_reader_loads_user32_and_kernel32_with_last_error_and_documented_signatures(install):
    windows = install(FakeClipboard("Good morning"))

    read_clipboard(2000)

    assert windows.signatures == {
        "RegisterClipboardFormatW": ([wintypes.LPCWSTR], wintypes.UINT),
        "IsClipboardFormatAvailable": ([wintypes.UINT], wintypes.BOOL),
        "OpenClipboard": ([wintypes.HWND], wintypes.BOOL),
        "GetClipboardData": ([wintypes.UINT], wintypes.HANDLE),
        "GlobalLock": ([wintypes.HGLOBAL], wintypes.LPVOID),
        "GlobalSize": ([wintypes.HGLOBAL], ctypes.c_size_t),
        "GlobalUnlock": ([wintypes.HGLOBAL], wintypes.BOOL),
        "CloseClipboard": ([], wintypes.BOOL),
    }
    assert {name for name, _ in windows.loads} == {"user32", "kernel32"}
    assert all(arguments == {"use_last_error": True} for _, arguments in windows.loads)


def test_the_real_reader_never_reaches_the_owner_s_clipboard_in_the_suite(refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")

    with pytest.raises(AssertionError, match="read the owner's real clipboard"):
        read_clipboard(2000)

    assert refused_system_calls == ["user32.RegisterClipboardFormatW"]
    refused_system_calls.clear()  # this one was on purpose


# --- what looks private --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "password: hunter2kucing",  # a secret word with its value (memory.looks_secret)
        "sk-ant-api03-AbCdEfGh1234567890XyZ",  # a long run of letters and digits
        "4111 1111 1111 1111",  # a card number
        "Tr0ub4dor&3",  # a lone password-like word: lower, upper, digits, symbols
        "correcthorse9!",  # lower, digits, symbols
        "Kucing123",  # lower, upper, digits
        "  Kucing123\n",  # stripped first
        "kucing123!",  # lower, digits, and a symbol at its end: not letters alone
        "Kucing123!",
        "Hunter2!x",
        "Pass!word",  # a symbol inside letters
        "482910",  # an OTP
        "４８２９１０",  # in full-width digits
        "1234",  # a PIN
        "482 910",
        "4829-1035",
        "1234567890",  # an account number
        "0812-3456-7890",  # a phone number
        "123 456 7890 12",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n",
        "my PRIVATE KEY is here",
        # A message with its code: OTP or verification wording near 4-8 digits, either side.
        "<#> 482910 adalah kode OTP Anda. JANGAN BERIKAN kode ini kepada siapapun.",
        "Kode verifikasi: 482910",
        "【認証コード】482910 このコードを他人に教えないでください。",
        "認証コード: 482910",
        "認証コード：４８２９１０",  # full-width digits
        "確認コードは 482 910 です",
        "ワンタイムパスワード 48291035",
        "Your verification code is 482910. It expires in 10 minutes.",
        "Your HoYoverse verification code is: 482910",
        "482910 is your verification code",
        "G-482910 is your Google verification code.",
        "Login code: 482910",
        "Use 4829-1035 as your Microsoft account security code",
        "Your one-time passcode: 482910 5 minutes",
        # A password under a short label at the start of a line.
        "user: budi\npass: Kucing123",
        "pw = kucing",
    ],
)
def test_secret_looking_copies_look_private(text):
    assert looks_private(text)


@pytest.mark.parametrize(
    "text",
    [
        JAPANESE,
        "こんにちは",
        "The quick brown fox jumps over the lazy dog.",
        "Hello",
        "Good morning!",
        "otsukaresama",  # one word, lower case only
        "HELLO-WORLD",  # upper case and a symbol: two kinds
        "Version 2.0 is out today",  # several words
        "今日は 2026年10月25日 です",
        "123",  # too short for a PIN
        "123456789",  # nine digits: no PIN, too short for an account
        "1234 5678 9",  # three groups: neither
        "Kucing1",  # seven characters: too short for a password
        "https://example.com/page",  # lower case and symbols: two kinds
        # One word of letters with punctuation around it, as a game or a tweet writes it.
        "Welcome!",
        "Congratulations!",
        "Mesmerizing.",
        "Otsukaresama!",
        "Sorry...",
        "Seriously?!",
        "#HonkaiStarRail",
        '"Trailblazer"',
        "(HoYoverse)",
        # Numbers beside a code word that is no OTP's, or OTP wording without its code.
        "kode pos 40115",
        "Promo code 2024: diskon 50%",
        "Kode OTP dikirim lewat SMS, jangan berikan ke siapa pun.",
        "2024年にISO認証を取得しました",  # certification, not a code
        "Battle Pass: 2 days left",
        "Nameless Honor\nPass: 1280 Oneiric Shards",
    ],
)
def test_ordinary_copies_do_not_look_private(text):
    assert not looks_private(text)
