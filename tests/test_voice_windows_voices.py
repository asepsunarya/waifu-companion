import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from waifu.voice import windows_voices
from waifu.voice.windows_voices import (
    SCRIPT,
    TIMEOUT_S,
    WindowsVoice,
    WindowsVoiceError,
    choose_voice,
    language_name,
    list_windows_voices,
    missing_voice_hint,
    speak_wav,
)

ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ANDIKA = WindowsVoice("Microsoft Andika", "id-ID", "Male", "onecore")
ZIRA = WindowsVoice("Microsoft Zira", "en-US", "Female", "onecore")
DAVID = WindowsVoice("Microsoft David", "en-US", "Male", "onecore")
ICHIRO = WindowsVoice("Microsoft Ichiro", "ja-JP", "Male", "onecore")
HARUKA = WindowsVoice("Microsoft Haruka", "ja-JP", "Female", "onecore")
SAYAKA = WindowsVoice("Microsoft Sayaka", "ja-JP", "Female", "onecore")

# What the script's -List printed on the owner's PC, with only the English voices.
OWNER_LIST = (
    b'{"name":"Microsoft David","language":"en-US","gender":"Male","api":"onecore"}\n'
    b'{"name":"Microsoft Zira","language":"en-US","gender":"Female","api":"onecore"}\n'
    b'{"name":"Microsoft Mark","language":"en-US","gender":"Male","api":"onecore"}\n'
)


@dataclass
class Call:
    args: list[str]
    kwargs: dict[str, Any]


@dataclass
class FakeRun:
    """Stands in for subprocess.run of powershell.exe: records each call and answers with
    `stdout`, `stderr`, and `returncode`, or raises `error`."""

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = 0
    error: Exception | None = None
    calls: list[Call] = field(default_factory=list)

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(Call(list(args), kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def powershell_command(*extra: str) -> list[str]:
    powershell = Path(os.environ["SYSTEMROOT"], "System32", "WindowsPowerShell", "v1.0")
    return [
        str(powershell / "powershell.exe"),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        *extra,
    ]


# --- the shipped script ----------------------------------------------------------------------


def test_the_script_ships_next_to_the_module_as_ascii():
    # Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page.
    assert Path(windows_voices.__file__).with_name("windows_voices.ps1") == SCRIPT
    assert SCRIPT.read_bytes().isascii()


def test_the_script_is_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["waifu"]

    assert any(PurePosixPath("voice/windows_voices.ps1").match(pattern) for pattern in patterns)


# --- list_windows_voices ---------------------------------------------------------------------


def test_listing_runs_the_script_with_windows_powershell_and_no_window():
    run = FakeRun(stdout=OWNER_LIST)

    list_windows_voices(run=run)

    [call] = run.calls
    assert call.args == powershell_command("-List")
    assert call.kwargs["capture_output"] is True
    assert call.kwargs["timeout"] == TIMEOUT_S
    assert call.kwargs["creationflags"] == NO_WINDOW


def test_powershell_is_the_one_in_the_windows_folder_not_on_the_path(monkeypatch):
    # A powershell.exe earlier on the PATH (or in the working folder) is never the one run.
    monkeypatch.setenv("SYSTEMROOT", r"D:\Win")
    run = FakeRun(stdout=b"\n")

    list_windows_voices(run=run)

    assert PureWindowsPath(run.calls[0].args[0]) == PureWindowsPath(
        r"D:\Win\System32\WindowsPowerShell\v1.0\powershell.exe"
    )


def test_listing_reads_one_voice_per_line():
    voices = list_windows_voices(run=FakeRun(stdout=OWNER_LIST.replace(b"\n", b"\r\n")))

    assert voices == [
        DAVID,
        ZIRA,
        WindowsVoice("Microsoft Mark", "en-US", "Male", "onecore"),
    ]


def test_a_listing_without_voices_is_empty():
    assert list_windows_voices(run=FakeRun(stdout=b"\n")) == []


@pytest.mark.parametrize(
    "stdout",
    [b"Preparing modules for first use.\n", b'{"name":"Microsoft Zira"}\n', b'["x"]\n'],
    ids=["not-json", "missing-fields", "not-an-object"],
)
def test_an_unreadable_listing_is_an_error(stdout):
    with pytest.raises(WindowsVoiceError, match="Daftar suara Windows tidak terbaca"):
        list_windows_voices(run=FakeRun(stdout=stdout))


def test_a_listing_that_fails_says_why():
    run = FakeRun(returncode=1, stderr=b"Cannot add type. Add-Type is blocked.\r\n")

    with pytest.raises(WindowsVoiceError, match="Add-Type is blocked"):
        list_windows_voices(run=run)


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "The system cannot find the file specified"),
        subprocess.TimeoutExpired(["powershell.exe"], TIMEOUT_S),
    ],
    ids=["no-powershell", "timeout"],
)
def test_a_listing_powershell_could_not_finish_is_an_error(error):
    with pytest.raises(WindowsVoiceError, match="PowerShell"):
        list_windows_voices(run=FakeRun(error=error))


# --- speak_wav -------------------------------------------------------------------------------

# Her words may hold PowerShell syntax: it must reach the voice as text, never as code.
TRICKY_TEXT = "Halo $env:USERNAME `whoami` $(Remove-Item x) <speak> おかえり"


def test_speaking_sends_her_text_on_stdin_and_the_voice_in_the_environment():
    run = FakeRun(stdout=b"RIFF....WAVE")

    speak_wav("Microsoft Andika", TRICKY_TEXT, run=run)

    [call] = run.calls
    assert call.args == powershell_command()
    assert call.kwargs["input"] == TRICKY_TEXT.encode("utf-8")
    assert call.kwargs["env"] == {**os.environ, "WAIFU_TTS_VOICE": "Microsoft Andika"}
    assert call.kwargs["capture_output"] is True
    assert call.kwargs["timeout"] == TIMEOUT_S
    assert call.kwargs["creationflags"] == NO_WINDOW
    assert not any("whoami" in arg or "おかえり" in arg for arg in call.args)


def test_speaking_returns_the_wav_powershell_wrote():
    assert speak_wav("Microsoft Zira", "Halo", run=FakeRun(stdout=b"RIFF....WAVE")) == (
        b"RIFF....WAVE"
    )


def test_speaking_with_a_voice_that_is_not_installed_says_so():
    run = FakeRun(returncode=3, stderr=b"voice not installed: Microsoft Andika\r\n")

    with pytest.raises(WindowsVoiceError, match="Microsoft Andika tidak terpasang"):
        speak_wav("Microsoft Andika", "Halo", run=run)


def test_speaking_that_fails_gives_the_first_error_line_cut_short():
    run = FakeRun(returncode=1, stderr=b"x" * 1000 + b"\r\nsecond line\r\n")

    with pytest.raises(WindowsVoiceError) as caught:
        speak_wav("Microsoft Zira", "Halo", run=run)

    assert "x" * 300 in str(caught.value)
    assert "x" * 301 not in str(caught.value)
    assert "second line" not in str(caught.value)


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "The system cannot find the file specified"),
        subprocess.TimeoutExpired(["powershell.exe"], TIMEOUT_S),
    ],
    ids=["no-powershell", "timeout"],
)
def test_speaking_powershell_could_not_finish_is_an_error(error):
    with pytest.raises(WindowsVoiceError, match="PowerShell"):
        speak_wav("Microsoft Zira", "Halo", run=FakeRun(error=error))


# --- choosing a voice ------------------------------------------------------------------------


@pytest.mark.parametrize(("voice", "language"), [(ANDIKA, "id"), (HARUKA, "ja"), (ZIRA, "en")])
def test_a_voice_speaks_the_language_of_its_tag(voice, language):
    assert voice.speaks(language)


def test_a_voice_does_not_speak_another_language():
    assert not ZIRA.speaks("id")


def test_the_installed_voice_of_the_language_is_chosen():
    assert choose_voice([ZIRA, DAVID, ANDIKA], "id") == ANDIKA


def test_a_female_voice_of_the_language_is_preferred():
    assert choose_voice([ICHIRO, HARUKA, SAYAKA], "ja") == HARUKA


def test_a_voice_of_another_language_is_never_chosen_by_itself():
    # An English voice mangles Indonesian and Japanese.
    assert choose_voice([ZIRA, DAVID], "id") is None


@pytest.mark.parametrize("wanted", ["Microsoft Zira", "microsoft zira", "Zira"])
def test_a_named_voice_is_chosen_whatever_its_language(wanted):
    assert choose_voice([ANDIKA, ZIRA], "id", wanted) == ZIRA


def test_a_named_voice_that_is_not_installed_is_none():
    assert choose_voice([ANDIKA, ZIRA], "id", "Microsoft Gadis") is None


# --- what to tell the owner ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "name"), [("id", "Indonesia"), ("ja", "Jepang"), ("ko", "ko")]
)
def test_languages_are_named_in_indonesian(language, name):
    assert language_name(language) == name


def test_a_missing_voice_says_where_to_add_it():
    assert missing_voice_hint("id") == (
        "belum ada suara Windows bahasa Indonesia; tambahkan gratis di Settings → "
        "Time & language → Speech → Add voices (Indonesian)"
    )
    assert missing_voice_hint("ja").endswith("Add voices (Japanese)")
    assert missing_voice_hint("ko").endswith("Add voices")
