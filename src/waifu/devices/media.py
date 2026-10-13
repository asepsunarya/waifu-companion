"""Windows media and volume keys, for whatever player is open (Spotify, a browser, a game).

Keys go through an injected sender; the real one synthesizes key presses with
user32's SendInput, loaded only when a key is pressed, so tests never press one.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable, Sequence
from ctypes import wintypes

from waifu.devices.base import Device, DeviceError, DeviceResult

logger = logging.getLogger(__name__)

# Virtual-key codes from WinUser.h.
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

# Each volume key moves the Windows volume by 2 %, so 50 presses cover the whole range.
# Every volume value is a percentage, as Master says it ("kecilkan volume 20 persen").
VOLUME_STEP_PERCENT = 2
MAX_VOLUME = 100
FULL_RANGE_PRESSES = MAX_VOLUME // VOLUME_STEP_PERCENT
DEFAULT_VOLUME_CHANGE = 10
MIN_VOLUME_CHANGE = 1

# Player commands press one key; the player decides what it means (play_pause and mute toggle).
# Messages tell what was pressed, never what happened: Windows does not say whether a player got
# the key, and drops it silently while a program running as administrator (a game) has focus.
PLAYER_KEYS = {
    "play_pause": (
        VK_MEDIA_PLAY_PAUSE,
        "Tombol putar/jeda sudah ditekan (menjeda, atau memutar lagi kalau tadi berhenti).",
    ),
    "next": (VK_MEDIA_NEXT_TRACK, "Tombol lagu berikutnya sudah ditekan."),
    "previous": (VK_MEDIA_PREV_TRACK, "Tombol lagu sebelumnya sudah ditekan."),
    "stop": (VK_MEDIA_STOP, "Tombol stop sudah ditekan."),
    "mute": (
        VK_VOLUME_MUTE,
        "Tombol bisu sudah ditekan (membisukan, atau menyalakan suara lagi kalau tadi dibisukan).",
    ),
}
VOLUME_STEP_KEYS = {
    "volume_up": (VK_VOLUME_UP, "naik"),
    "volume_down": (VK_VOLUME_DOWN, "turun"),
}
COMMANDS = (*PLAYER_KEYS, *VOLUME_STEP_KEYS, "volume")

# Unsigned and pointer-sized, like WinDef.h's ULONG_PTR.
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    # Only keyboard input is sent, but the union must be as large as WinUser.h's:
    # SendInput refuses every event when cbSize is not sizeof(INPUT).
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [("type", wintypes.DWORD), ("_input", _InputUnion)]


def press_keys(keys: Sequence[int]) -> None:
    """Press and release each virtual key in order, as if on a keyboard.

    Raises OSError when Windows inserts fewer events than given (input blocked).
    """
    _send_events(_load_send_input(), _key_events(keys))


def _key_events(keys: Sequence[int]) -> ctypes.Array[INPUT]:
    """One key-down and one key-up keyboard INPUT per virtual key, in order."""
    events = [
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=key, dwFlags=flags))
        for key in keys
        for flags in (0, KEYEVENTF_KEYUP)
    ]
    return (INPUT * len(events))(*events)


def _load_send_input() -> Callable[..., int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send_input = user32.SendInput
    send_input.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    send_input.restype = wintypes.UINT
    return send_input


def _send_events(send_input: Callable[..., int], events: ctypes.Array[INPUT]) -> None:
    inserted = send_input(len(events), events, ctypes.sizeof(INPUT))
    if inserted != len(events):
        raise OSError(
            f"SendInput inserted {inserted} of {len(events)} key events "
            f"(Windows error {ctypes.get_last_error()})"
        )


class MediaKeys(Device):
    kind = "media"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        *,
        press: Callable[[Sequence[int]], None] = press_keys,
    ) -> None:
        super().__init__(device_id, name, aliases)
        self._press_keys = press

    def commands(self) -> list[str]:
        return list(COMMANDS)

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        if command in PLAYER_KEYS:
            key, message = PLAYER_KEYS[command]
            return self._press(command, [key], message)
        if command in VOLUME_STEP_KEYS:
            return self._step_volume(command, value)
        if command == "volume":
            return self._set_volume(value)
        raise DeviceError(
            f"Perintah '{command}' tidak tersedia untuk {self.name}. "
            f"Perintah yang tersedia: {', '.join(COMMANDS)}."
        )

    def _step_volume(self, command: str, value: int | None) -> DeviceResult:
        percent = DEFAULT_VOLUME_CHANGE if value is None else value
        if not MIN_VOLUME_CHANGE <= percent <= MAX_VOLUME:
            raise DeviceError(
                f"Perubahan volume harus angka {MIN_VOLUME_CHANGE} sampai {MAX_VOLUME} (persen)."
            )
        # Rounded down to whole key presses, like a level, but a change presses at least once.
        presses = max(1, percent // VOLUME_STEP_PERCENT)
        key, direction = VOLUME_STEP_KEYS[command]
        return self._press(
            command,
            [key] * presses,
            f"Tombol volume {direction} sudah ditekan {presses} kali "
            f"(sekitar {presses * VOLUME_STEP_PERCENT}%).",
        )

    def _set_volume(self, value: int | None) -> DeviceResult:
        if value is None or not 0 <= value <= MAX_VOLUME:
            raise DeviceError(f"Level volume harus angka 0 sampai {MAX_VOLUME}.")
        # Windows has no key for a level: go all the way down, then up to it.
        ups = value // VOLUME_STEP_PERCENT
        keys = [VK_VOLUME_DOWN] * FULL_RANGE_PRESSES + [VK_VOLUME_UP] * ups
        return self._press(
            "volume",
            keys,
            f"Tombol volume sudah ditekan untuk mengatur ke sekitar {ups * VOLUME_STEP_PERCENT}%.",
        )

    def _press(self, command: str, keys: list[int], message: str) -> DeviceResult:
        try:
            self._press_keys(keys)
        except OSError as exc:
            logger.warning("Media keys for '%s' on %s failed: %s", command, self.id, exc)
            raise DeviceError(
                "Windows menolak tombol medianya, jadi perintahnya tidak sampai."
            ) from exc
        logger.info("Pressed %d media key(s) for '%s' on %s", len(keys), command, self.id)
        self.state["last"] = command
        return DeviceResult(ok=True, message=message, state=dict(self.state))
