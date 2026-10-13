"""Media keys: no test presses a real key.

The device gets a recording fake instead of `press_keys`, and the real sender is
checked piece by piece: its INPUT structures are built and measured, its SendInput
declaration is read from a fake user32, and its send step gets a fake SendInput.
"""

import ctypes
import os
import struct
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from waifu.devices.base import DeviceError
from waifu.devices.media import (
    HARDWAREINPUT,
    INPUT,
    KEYBDINPUT,
    MOUSEINPUT,
    MediaKeys,
    _key_events,
    _load_send_input,
    _send_events,
)

# Virtual-key codes as Microsoft's "Virtual-Key Codes" table lists them.
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

SRC = Path(__file__).resolve().parent.parent / "src"
CHILD_TIMEOUT_S = 60

win64_only = pytest.mark.skipif(
    ctypes.sizeof(ctypes.c_void_p) != 8, reason="the expected layout is 64-bit Windows'"
)


class FakePress:
    """Stands in for press_keys: records each key sequence instead of pressing it."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sequences: list[list[int]] = []

    def __call__(self, keys) -> None:
        self.sequences.append(list(keys))
        if self.error is not None:
            raise self.error


class FakeSendInput:
    """Stands in for user32's SendInput: records the call, inserts `inserted` events."""

    def __init__(self, inserted: int | None = None) -> None:
        self.inserted = inserted
        self.calls: list[tuple] = []

    def __call__(self, count: int, events, size: int) -> int:
        self.calls.append((count, events, size))
        return count if self.inserted is None else self.inserted


class FakeUser32:
    """Stands in for ctypes.WinDLL: hands out a SendInput that is only ever declared."""

    def __init__(self) -> None:
        self.loaded: list[tuple[str, dict]] = []
        self.SendInput = type("DeclaredOnly", (), {})()

    def __call__(self, name: str, **kwargs: object) -> "FakeUser32":
        self.loaded.append((name, kwargs))
        return self


def make_media(press: FakePress | None = None) -> MediaKeys:
    return MediaKeys("musik", "Musik", ["lagu", "volume"], press=press or FakePress())


def test_media_keys_offer_the_player_and_volume_commands():
    device = make_media()

    assert device.kind == "media"
    assert device.commands() == [
        "play_pause",
        "next",
        "previous",
        "stop",
        "mute",
        "volume_up",
        "volume_down",
        "volume",
    ]


# Messages tell what was pressed, never what it did: Windows does not say whether a player got
# the key, and silently drops it while a program running as administrator (a game) has focus.
@pytest.mark.parametrize(
    ("command", "key", "message"),
    [
        (
            "play_pause",
            VK_MEDIA_PLAY_PAUSE,
            "Tombol putar/jeda sudah ditekan (menjeda, atau memutar lagi kalau tadi berhenti).",
        ),
        ("next", VK_MEDIA_NEXT_TRACK, "Tombol lagu berikutnya sudah ditekan."),
        ("previous", VK_MEDIA_PREV_TRACK, "Tombol lagu sebelumnya sudah ditekan."),
        ("stop", VK_MEDIA_STOP, "Tombol stop sudah ditekan."),
        (
            "mute",
            VK_VOLUME_MUTE,
            "Tombol bisu sudah ditekan (membisukan, atau menyalakan suara lagi kalau tadi "
            "dibisukan).",
        ),
    ],
)
def test_player_commands_press_their_media_key_once(command, key, message):
    press = FakePress()
    device = make_media(press)

    result = device.execute(command)

    assert press.sequences == [[key]]
    assert result.ok
    assert result.message == message
    assert result.state == {"last": command}


@pytest.mark.parametrize(
    ("command", "key", "message"),
    [
        ("volume_up", VK_VOLUME_UP, "Tombol volume naik sudah ditekan 5 kali (sekitar 10%)."),
        ("volume_down", VK_VOLUME_DOWN, "Tombol volume turun sudah ditekan 5 kali (sekitar 10%)."),
    ],
)
def test_volume_step_without_a_value_presses_five_steps_of_two_percent(command, key, message):
    press = FakePress()
    device = make_media(press)

    result = device.execute(command)

    assert press.sequences == [[key] * 5]
    assert result.message == message
    assert device.state == {"last": command}


@pytest.mark.parametrize(
    ("command", "key", "percent", "presses", "message"),
    [
        # "kecilkan volume 20 persen": the value is the percentage Master says, not key presses.
        (
            "volume_down",
            VK_VOLUME_DOWN,
            20,
            10,
            "Tombol volume turun sudah ditekan 10 kali (sekitar 20%).",
        ),
        (
            "volume_up",
            VK_VOLUME_UP,
            50,
            25,
            "Tombol volume naik sudah ditekan 25 kali (sekitar 50%).",
        ),
        (
            "volume_up",
            VK_VOLUME_UP,
            100,
            50,
            "Tombol volume naik sudah ditekan 50 kali (sekitar 100%).",
        ),
        # Rounded down to whole 2 % presses, like `volume`, but always at least one press.
        ("volume_up", VK_VOLUME_UP, 5, 2, "Tombol volume naik sudah ditekan 2 kali (sekitar 4%)."),
        (
            "volume_down",
            VK_VOLUME_DOWN,
            1,
            1,
            "Tombol volume turun sudah ditekan 1 kali (sekitar 2%).",
        ),
    ],
)
def test_volume_change_is_a_percentage_with_one_key_press_per_two_percent(
    command, key, percent, presses, message
):
    press = FakePress()
    device = make_media(press)

    result = device.execute(command, percent)

    assert press.sequences == [[key] * presses]
    assert result.message == message


def test_volume_goes_to_zero_then_up_two_percent_per_press():
    # Windows has no key for a level: 50 downs reach 0 %, then each up adds 2 %.
    press = FakePress()
    device = make_media(press)

    result = device.execute("volume", 30)

    assert press.sequences == [[VK_VOLUME_DOWN] * 50 + [VK_VOLUME_UP] * 15]
    assert result.ok
    assert result.message == "Tombol volume sudah ditekan untuk mengatur ke sekitar 30%."
    assert result.state == {"last": "volume"}


@pytest.mark.parametrize(
    ("level", "ups", "message"),
    [
        (0, 0, "Tombol volume sudah ditekan untuk mengatur ke sekitar 0%."),
        (1, 0, "Tombol volume sudah ditekan untuk mengatur ke sekitar 0%."),
        (31, 15, "Tombol volume sudah ditekan untuk mengatur ke sekitar 30%."),
        (100, 50, "Tombol volume sudah ditekan untuk mengatur ke sekitar 100%."),
    ],
)
def test_volume_level_rounds_down_to_a_whole_key_press(level, ups, message):
    press = FakePress()
    device = make_media(press)

    result = device.execute("volume", level)

    assert press.sequences == [[VK_VOLUME_DOWN] * 50 + [VK_VOLUME_UP] * ups]
    assert result.message == message


@pytest.mark.parametrize(
    ("command", "value"),
    [
        ("volume_up", 0),
        ("volume_up", 101),
        ("volume_down", 0),
        ("volume_down", 101),
        ("volume", -1),
        ("volume", 101),
        ("volume", None),
    ],
)
def test_out_of_range_or_missing_value_is_a_device_error_and_presses_nothing(command, value):
    press = FakePress()
    device = make_media(press)

    with pytest.raises(DeviceError):
        device.execute(command, value)
    assert press.sequences == []
    assert device.state == {}


def test_volume_change_range_error_names_the_range_in_indonesian():
    with pytest.raises(DeviceError, match="1 sampai 100"):
        make_media().execute("volume_up", 150)


def test_volume_level_error_names_the_range_in_indonesian():
    with pytest.raises(DeviceError, match="0 sampai 100"):
        make_media().execute("volume")


def test_unknown_command_lists_the_available_commands_and_presses_nothing():
    press = FakePress()
    device = make_media(press)

    with pytest.raises(DeviceError, match="play_pause, next, previous, stop, mute, volume_up"):
        device.execute("close_spotify")
    assert press.sequences == []


def test_state_and_describe_keep_only_the_last_command():
    device = make_media()
    device.execute("next")
    device.execute("volume", 20)

    description = device.describe()

    assert description["kind"] == "media"
    assert description["aliases"] == ["lagu", "volume"]
    assert description["state"] == {"last": "volume"}


def test_keys_windows_refuses_become_an_indonesian_device_error():
    device = make_media(FakePress(error=OSError("SendInput inserted 0 of 2 key events")))

    with pytest.raises(DeviceError, match="Windows"):
        device.execute("play_pause")
    assert device.state == {}


@win64_only
def test_input_structures_match_the_win32_layout():
    # WinUser.h: INPUT is a DWORD type and a union of MOUSEINPUT, KEYBDINPUT, HARDWAREINPUT;
    # SendInput fails unless cbSize is exactly sizeof(INPUT).
    assert ctypes.sizeof(MOUSEINPUT) == 32
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(HARDWAREINPUT) == 8
    assert ctypes.sizeof(INPUT) == 40
    assert (INPUT.type.offset, INPUT.ki.offset) == (0, 8)
    offsets = [getattr(KEYBDINPUT, name).offset for name, _ in KEYBDINPUT._fields_]
    assert [name for name, _ in KEYBDINPUT._fields_] == [
        "wVk",
        "wScan",
        "dwFlags",
        "time",
        "dwExtraInfo",
    ]
    assert offsets == [0, 2, 4, 8, 16]


def test_key_events_press_and_release_each_key_in_order():
    events = _key_events([VK_VOLUME_UP, VK_MEDIA_PLAY_PAUSE])

    assert len(events) == 4
    assert [event.type for event in events] == [INPUT_KEYBOARD] * 4
    assert [event.ki.wVk for event in events] == [
        VK_VOLUME_UP,
        VK_VOLUME_UP,
        VK_MEDIA_PLAY_PAUSE,
        VK_MEDIA_PLAY_PAUSE,
    ]
    assert [event.ki.dwFlags for event in events] == [0, KEYEVENTF_KEYUP] * 2
    assert {(event.ki.wScan, event.ki.time, event.ki.dwExtraInfo) for event in events} == {
        (0, 0, 0)
    }


@win64_only
def test_key_up_event_has_the_exact_bytes_of_a_64_bit_keyboard_input():
    # type, 4 padding bytes, wVk, wScan, dwFlags, time, 4 padding, dwExtraInfo, union tail.
    expected = struct.pack("<I4xHHII4xQ8x", INPUT_KEYBOARD, VK_MEDIA_STOP, 0, KEYEVENTF_KEYUP, 0, 0)

    assert bytes(_key_events([VK_MEDIA_STOP])[1]) == expected


def test_send_input_is_declared_in_full_through_user32_with_last_error(monkeypatch):
    user32 = FakeUser32()  # never the real user32: nothing here can press a key
    monkeypatch.setattr(ctypes, "WinDLL", user32, raising=False)

    send_input = _load_send_input()

    assert user32.loaded == [("user32", {"use_last_error": True})]
    assert send_input is user32.SendInput
    assert send_input.argtypes == [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    assert send_input.restype is wintypes.UINT
    send_input.argtypes[1].from_param(_key_events([VK_MEDIA_STOP]))  # the events array fits


def test_events_go_to_send_input_in_one_call_with_the_input_size():
    send_input = FakeSendInput()
    events = _key_events([VK_MEDIA_NEXT_TRACK, VK_VOLUME_MUTE])

    _send_events(send_input, events)

    assert send_input.calls == [(4, events, ctypes.sizeof(INPUT))]


@pytest.mark.parametrize(
    ("command", "value", "keys"),
    [
        ("volume_up", 6, [VK_VOLUME_UP] * 3),
        ("volume", 30, [VK_VOLUME_DOWN] * 50 + [VK_VOLUME_UP] * 15),
    ],
)
def test_media_keys_send_every_key_through_user32_by_default(monkeypatch, command, value, keys):
    # The real sender end to end: a default that does nothing, or one that drops keys, would
    # pass every test that hands the device a fake.
    user32 = FakeUser32()  # never the real user32: nothing here can press a key
    user32.SendInput = FakeSendInput()
    monkeypatch.setattr(ctypes, "WinDLL", user32, raising=False)

    result = MediaKeys("musik", "Musik", []).execute(command, value)

    assert result.ok
    assert user32.loaded == [("user32", {"use_last_error": True})]
    [(count, events, size)] = user32.SendInput.calls
    assert (count, size) == (2 * len(keys), ctypes.sizeof(INPUT))
    assert [event.ki.wVk for event in events] == [key for key in keys for _ in range(2)]
    assert [event.ki.dwFlags for event in events] == [0, KEYEVENTF_KEYUP] * len(keys)


def test_short_send_raises_os_error_with_the_windows_error(monkeypatch):
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    send_input = FakeSendInput(inserted=1)

    with pytest.raises(OSError, match="1 of 2 .*error 5"):
        _send_events(send_input, _key_events([VK_MEDIA_PLAY_PAUSE]))


IMPORT_PROBE = """
import ctypes
from pathlib import PurePath

real_windll = ctypes.WinDLL
user32_loads = []


def refuse_user32(name, *args, **kwargs):
    if PurePath(str(name)).stem.lower() == "user32":
        user32_loads.append(str(name))
        raise OSError("the import probe refuses user32")
    return real_windll(name, *args, **kwargs)


ctypes.WinDLL = refuse_user32
ctypes.windll = ctypes.LibraryLoader(refuse_user32)
import waifu.devices.media
print(user32_loads)
"""


def test_importing_the_module_never_loads_user32():
    # A fresh interpreter: this one imported the module before any guard was in place.
    paths = [str(SRC), os.environ.get("PYTHONPATH", "")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in paths if path)}

    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_S,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
