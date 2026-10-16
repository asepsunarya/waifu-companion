"""The speech voices installed in Windows, through Windows PowerShell 5.1 and its WinRT speech API.

These are Cyrene's offline fallback voice. `windows_voices.ps1`, shipped next to this module,
lists the voices or speaks one text: her words reach it on stdin and the voice name in the
environment, never on a command line, and the WAV comes back on stdout, so no temp file is
needed and nothing new is installed. Each call starts one PowerShell process (~0.4 s).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

SCRIPT = Path(__file__).with_name("windows_voices.ps1")
# A call took ~0.4 s on the owner's PC; the first one after boot is slower.
TIMEOUT_S = 20.0
VOICE_VARIABLE = "WAIFU_TTS_VOICE"
NOT_INSTALLED_EXIT = 3
# CREATE_NO_WINDOW keeps a console from flashing up; 0 is the only value POSIX accepts.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ERROR_TEXT_LIMIT = 300
_LANGUAGE_NAMES = {"id": "Indonesia", "ja": "Jepang"}
# What Settings → Speech → Add voices calls each language.
_SETTINGS_LANGUAGES = {"id": "Indonesian", "ja": "Japanese"}
_ADD_VOICES = "Settings → Time & language → Speech → Add voices"

Run = Callable[..., subprocess.CompletedProcess[bytes]]


class WindowsVoiceError(Exception):
    """Windows could not list its voices or speak; the message is Indonesian and safe to show."""


@dataclass(frozen=True)
class WindowsVoice:
    name: str  # the display name the script takes back, e.g. "Microsoft Andika"
    language: str  # a BCP-47 tag, e.g. "id-ID"
    gender: str  # "Female" or "Male", as Windows says
    api: str  # "onecore" (WinRT, the voices Settings adds) or "sapi" (desktop voices)

    def speaks(self, language: str) -> bool:
        """Whether this voice speaks `language` ("id", "ja"), going by its tag's first part."""
        return self.language.split("-")[0].lower() == language.lower()


def list_windows_voices(*, run: Run = subprocess.run) -> list[WindowsVoice]:
    """Every voice Windows can speak with, in the order Windows lists them."""
    result = _run_script(["-List"], run=run)
    try:
        lines = result.stdout.decode("utf-8").splitlines()
        return [_voice(json.loads(line)) for line in lines if line.strip()]
    except (ValueError, KeyError, TypeError) as exc:
        raise WindowsVoiceError(f"Daftar suara Windows tidak terbaca: {exc}") from exc


def speak_wav(voice_name: str, text: str, *, run: Run = subprocess.run) -> bytes:
    """`text` spoken by the installed voice `voice_name`, as the bytes of a WAV file."""
    return _run_script([], run=run, voice_name=voice_name, text=text).stdout


def choose_voice(
    voices: Iterable[WindowsVoice], language: str, wanted: str = ""
) -> WindowsVoice | None:
    """The voice named `wanted` whatever its language; else `language`'s first female voice,
    else its first voice; None when there is none.

    A voice of another language is never picked by itself: an English voice mangles Indonesian
    and Japanese. Naming one is the owner's stopgap until the right voice is added.
    """
    if wanted:
        return next((voice for voice in voices if _is_named(voice, wanted)), None)
    speaking = [voice for voice in voices if voice.speaks(language)]
    female = [voice for voice in speaking if voice.gender.lower() == "female"]
    return next(iter(female or speaking), None)


def language_name(language: str) -> str:
    """`Indonesia` for "id", as in "bahasa Indonesia"; an unknown language keeps its code."""
    return _LANGUAGE_NAMES.get(language, language)


def missing_voice_hint(language: str) -> str:
    """Where the owner adds a Windows voice for `language`, free."""
    where = _ADD_VOICES
    if language in _SETTINGS_LANGUAGES:
        where += f" ({_SETTINGS_LANGUAGES[language]})"
    return f"belum ada suara Windows bahasa {language_name(language)}; tambahkan gratis di {where}"


def _run_script(
    arguments: list[str], *, run: Run, voice_name: str | None = None, text: str = ""
) -> subprocess.CompletedProcess[bytes]:
    command = [
        str(_powershell()),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        *arguments,
    ]
    environment = None if voice_name is None else {**os.environ, VOICE_VARIABLE: voice_name}
    try:
        result = run(
            command,
            input=text.encode("utf-8"),
            env=environment,
            capture_output=True,
            timeout=TIMEOUT_S,
            creationflags=_CREATION_FLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        raise WindowsVoiceError(f"PowerShell tidak selesai dalam {TIMEOUT_S:g} detik.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsVoiceError(f"PowerShell tidak bisa dijalankan: {exc}") from exc
    if result.returncode == NOT_INSTALLED_EXIT and voice_name is not None:
        raise WindowsVoiceError(f"Suara Windows {voice_name} tidak terpasang.")
    if result.returncode != 0:
        raise WindowsVoiceError(
            f"PowerShell gagal (kode {result.returncode}): {_first_line(result.stderr)}"
        )
    return result


def _powershell() -> Path:
    """Windows PowerShell 5.1 in the Windows folder, never a powershell.exe found on the PATH."""
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return Path(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _voice(fields: object) -> WindowsVoice:
    if not isinstance(fields, dict):
        raise TypeError(f"bukan data suara: {fields!r}")
    return WindowsVoice(*(str(fields[key]) for key in ("name", "language", "gender", "api")))


def _is_named(voice: WindowsVoice, wanted: str) -> bool:
    name, wanted = voice.name.casefold(), wanted.casefold()
    return name in (wanted, f"microsoft {wanted}")


def _first_line(stderr: bytes) -> str:
    lines = stderr.decode("utf-8", errors="replace").strip().splitlines()
    return lines[0][:_ERROR_TEXT_LIMIT] if lines else "tanpa pesan"
