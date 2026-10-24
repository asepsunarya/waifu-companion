"""MicSource: the mic Cyrene listens through, kept open across failures and switches.

No test opens a real stream, restarts the real PortAudio, or lists the real microphones: the
streams, the restart, Windows' list of inputs, and the clock and sleep are all fakes.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
from collections.abc import Iterator

import numpy as np
import pytest

from waifu.voice import audio
from waifu.voice.audio import MicLevel, MicrophoneError, MicrophoneFlat
from waifu.voice.mic_check import InputDevice
from waifu.voice.mic_source import (
    MIC_BACK,
    MIC_NOW,
    PROBE_S,
    RETRY_S,
    MicSource,
    windows_input_names,
)

RATE = 16000
FRAME_MS = 30
FRAME_LEN = RATE * FRAME_MS // 1000
WAIT_S = 2.0  # an upper bound for events between threads, never a sleep
HEADSET = "Headset (Polaris-BT), MME"
USB = "Microphone (USB), MME"
NAMES = {
    HEADSET: "Headset (Polaris-BT)",
    USB: "Microphone (USB)",
    None: "Microphone (Onboard)",
    2: "Headset (Polaris-BT)",  # a numbered device
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeStream:
    """A MicStream that yields frames filled with its own `mark` and, after `fail_after` frames,
    raises `failure`; it tells whether it is open, closed, and paused."""

    def __init__(self, mics: FakeMics, device: object, mark: float) -> None:
        self._mics = mics
        self.device = device
        self.mark = mark
        self.name: str | None = None
        self.host_api: str | None = None
        self.level: MicLevel | None = None
        self.paused = False
        self.closed = False

    def __enter__(self) -> FakeStream:
        problem = self._mics.broken.get(self.device)
        if problem is not None:
            raise problem
        self.name, self.host_api = NAMES[self.device], "MME"
        self._mics.events.append(f"open {self.name}")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True
        self._mics.events.append(f"close {self.name}")

    def frames(self) -> Iterator[np.ndarray]:
        sent = 0
        while not self.closed:
            failure = self._mics.failures.get(self.device)
            if failure is not None and sent >= self._mics.fail_after:
                del self._mics.failures[self.device]
                raise failure
            sent += 1
            yield np.full(FRAME_LEN, self.mark, dtype=np.float32)

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


class FakeMics:
    """The mics a fake PortAudio opens: `broken[device]` refuses to open, `failures[device]`
    fails once after `fail_after` frames; each opened stream marks its frames with a number."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.broken: dict[object, Exception] = {}
        self.failures: dict[object, Exception] = {}
        self.fail_after = 2
        self.streams: list[FakeStream] = []
        self.names = frozenset({"Headset (Polaris-BT)", "Microphone (Onboard)"})
        self.probes = 0
        self.listed_locked: list[bool] = []  # whether LISTING was held at each listing
        self.restart_error: Exception | None = None

    def open(self, device: object) -> FakeStream:
        stream = FakeStream(self, device, mark=len(self.streams) + 1)
        self.streams.append(stream)
        return stream

    def restart(self) -> None:
        self.events.append("restart")
        if self.restart_error is not None:
            raise self.restart_error

    def present(self) -> frozenset[str]:
        self.probes += 1
        return self.names

    def listing(self) -> tuple[list[InputDevice], str | None]:
        self.events.append("list")
        self.listed_locked.append(audio.LISTING.locked())
        devices = [
            InputDevice(1, "Headset (Polaris-BT)", "MME", 44100.0),
            InputDevice(2, "Microphone (Onboard)", "MME", 44100.0),
        ]
        return devices, "Microphone (Onboard)"


class Rig:
    def __init__(self, preferences=(), *, restart: bool = True, present: bool = True, **kwargs):
        self.mics = FakeMics()
        self.clock = FakeClock()
        self.said: list[str] = []
        self.swaps = 0
        self.source = MicSource(
            list(preferences),
            open_stream=self.mics.open,
            sample_rate=RATE,
            frame_ms=FRAME_MS,
            announce=self.said.append,
            on_swap=self.swapped,
            restart=self.mics.restart if restart else None,
            present=self.mics.present if present else None,
            listing=self.mics.listing,
            clock=self.clock,
            sleep=self.clock.sleep,
            **kwargs,
        )

    def swapped(self) -> None:
        self.swaps += 1

    @property
    def events(self) -> list[str]:
        return self.mics.events


def marks(frames: Iterator[np.ndarray], count: int) -> list[float]:
    """The marks of the next `count` frames: which stream each came from (0.0 is silence)."""
    return [float(next(frames)[0]) for _ in range(count)]


# --- opening -----------------------------------------------------------------------------------


def test_the_first_preference_that_opens_is_used_without_a_word():
    rig = Rig([HEADSET, USB])

    with rig.source as source:
        assert marks(source.frames(), 2) == [1.0, 1.0]

    assert rig.events == ["open Headset (Polaris-BT)", "close Headset (Polaris-BT)"]
    assert rig.said == []


def test_a_preferred_mic_that_does_not_open_gives_way_to_the_next_and_she_says_which():
    rig = Rig([HEADSET, USB])
    rig.mics.broken[HEADSET] = ValueError("No input device matching 'Headset'")

    with rig.source:
        pass

    assert rig.events[0] == "open Microphone (USB)"
    assert rig.said == [MIC_NOW.format(name="Microphone (USB)")]


def test_the_windows_default_comes_after_every_preference():
    rig = Rig([HEADSET])
    rig.mics.broken[HEADSET] = ValueError("gone")

    with rig.source:
        pass

    assert rig.mics.streams[-1].device is None
    assert rig.said == ["Sekarang aku pakai mic Microphone (Onboard)."]


def test_without_preferences_the_windows_default_is_used_without_a_word():
    rig = Rig()

    with rig.source:
        pass

    assert [stream.device for stream in rig.mics.streams] == [None]
    assert rig.said == []


def test_when_no_mic_opens_at_the_start_the_last_error_is_raised():
    rig = Rig([HEADSET])
    rig.mics.broken[HEADSET] = ValueError("headset gone")
    rig.mics.broken[None] = ValueError("no default input")

    with pytest.raises(ValueError, match="no default input"), rig.source:
        pass


# --- failing over ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [MicrophoneError("Mikrofon terputus"), MicrophoneFlat("sunyi total"), OSError("PortAudio")],
    ids=["stopped", "flat", "portaudio"],
)
def test_a_failing_mic_gives_way_to_the_next_with_one_line(failure, caplog):
    rig = Rig([HEADSET, USB])
    rig.mics.failures[HEADSET] = failure

    with caplog.at_level(logging.WARNING, logger="waifu.voice.mic_source"), rig.source as source:
        frames = source.frames()
        assert marks(frames, 4) == [1.0, 1.0, 2.0, 2.0]

    assert rig.events[:4] == [
        "open Headset (Polaris-BT)",
        "close Headset (Polaris-BT)",
        "restart",
        "open Microphone (USB)",
    ]
    assert rig.said == [MIC_NOW.format(name="Microphone (USB)")]
    assert rig.swaps == 2  # the first open, and the switch: the segmenter relearns the room
    assert "Headset (Polaris-BT)" in caplog.text


def test_the_failed_mic_is_tried_again_last_and_reopened_without_a_word():
    rig = Rig([HEADSET])
    rig.mics.failures[HEADSET] = MicrophoneFlat("sunyi total")
    rig.mics.broken[None] = ValueError("no default input")

    with rig.source as source:
        assert marks(source.frames(), 4) == [1.0, 1.0, 3.0, 3.0]

    assert [stream.device for stream in rig.mics.streams] == [HEADSET, None, HEADSET]
    assert rig.said == []


def test_with_no_mic_left_she_hears_silence_at_the_frames_pace_and_retries_every_five_seconds(
    caplog,
):
    rig = Rig([HEADSET])
    rig.mics.failures[HEADSET] = MicrophoneError("Mikrofon terputus")

    with caplog.at_level(logging.ERROR, logger="waifu.voice.mic_source"), rig.source as source:
        frames = source.frames()
        marks(frames, 2)
        rig.mics.broken[HEADSET] = ValueError("gone")
        rig.mics.broken[None] = ValueError("no default input")
        assert marks(frames, 3) == [0.0, 0.0, 0.0]
        assert rig.clock.slept == [FRAME_MS / 1000] * 2
        tries = len(rig.mics.streams)

        rig.clock.now += RETRY_S
        del rig.mics.broken[None]
        assert marks(frames, 3) == [float(len(rig.mics.streams))] * 3

    assert len(rig.mics.streams) == tries + 2  # the headset, then the default, once each
    assert rig.said == [MIC_NOW.format(name="Microphone (Onboard)")]
    assert caplog.text.count("No microphone opens") == 1


def test_a_failover_while_she_speaks_opens_the_next_mic_paused():
    rig = Rig([HEADSET, USB])
    rig.mics.failures[HEADSET] = MicrophoneError("Mikrofon terputus")

    with rig.source as source:
        frames = source.frames()
        marks(frames, 1)
        source.pause()
        marks(frames, 2)
        assert rig.mics.streams[-1].paused
        source.resume()

    assert not rig.mics.streams[-1].paused


def test_without_a_restart_a_numbered_device_still_fails_over():
    rig = Rig([2, USB], restart=False)
    rig.mics.failures[2] = MicrophoneError("Mikrofon terputus")

    with rig.source as source:
        assert marks(source.frames(), 4) == [1.0, 1.0, 2.0, 2.0]

    assert "restart" not in rig.events


# --- coming back -------------------------------------------------------------------------------


@contextlib.contextmanager
def on_the_usb_mic(rig: Rig) -> Iterator[MicSource]:
    """The source opened while the headset was gone, so on the USB mic; the headset is back."""
    rig.mics.broken[HEADSET] = ValueError("gone")
    with rig.source as source:
        del rig.mics.broken[HEADSET]
        yield source


def test_a_preferred_mic_that_comes_back_is_used_again_with_one_line():
    rig = Rig([HEADSET, USB])
    with on_the_usb_mic(rig) as source:
        frames = source.frames()
        assert marks(frames, 2) == [2.0, 2.0]

        rig.mics.names = rig.mics.names | {"Headset (Polaris-BT) 2"}  # Windows lists more
        rig.clock.now += PROBE_S
        assert marks(frames, 2) == [3.0, 3.0]

    assert rig.events[1:4] == [
        "close Microphone (USB)",
        "restart",
        "open Headset (Polaris-BT)",
    ]
    assert rig.said == [
        MIC_NOW.format(name="Microphone (USB)"),
        MIC_BACK.format(name="Headset (Polaris-BT)"),
    ]


def test_windows_list_is_read_at_most_every_five_seconds_and_nothing_changes_without_news():
    rig = Rig([HEADSET, USB])
    with on_the_usb_mic(rig) as source:
        frames = source.frames()
        probes = rig.mics.probes

        marks(frames, 20)
        rig.clock.now += PROBE_S
        marks(frames, 20)

    assert rig.mics.probes == probes + 1
    assert "restart" not in rig.events


def test_with_one_preference_windows_list_is_never_read_between_frames():
    rig = Rig()
    with rig.source as source:
        probes = rig.mics.probes
        rig.clock.now += PROBE_S
        marks(source.frames(), 3)

    assert rig.mics.probes == probes


def test_without_a_restart_she_never_switches_back_by_herself():
    rig = Rig([HEADSET, USB], restart=False)
    with on_the_usb_mic(rig) as source:
        frames = source.frames()
        rig.mics.names = frozenset({"something new"})
        rig.clock.now += PROBE_S

        assert marks(frames, 3) == [2.0, 2.0, 2.0]
    assert "restart" not in rig.events


# --- switching from the phone -------------------------------------------------------------------


def switch_from_the_phone(rig: Rig, device, frames, **kwargs):
    """Ask for `device` from another thread, as the phone server does, and let `run`'s thread
    take it at the next frame; returns the answer."""
    answers = []
    asking = threading.Thread(
        target=lambda: answers.append(rig.source.request_switch(device, wait_s=WAIT_S, **kwargs))
    )
    asking.start()
    while asking.is_alive():
        next(frames)
        asking.join(0.01)
    return answers[0]


def test_the_phone_switches_the_mic_on_run_s_thread_and_keeps_it_first():
    rig = Rig([HEADSET, USB])
    switched = []
    with rig.source as source:
        frames = source.frames()
        marks(frames, 1)

        answer = switch_from_the_phone(rig, USB, frames, on_switched=switched.append)

        assert answer.state == "done"
        assert "Microphone (USB)" in answer.message
        assert marks(frames, 1) == [2.0]
        assert source.preferences == [USB, HEADSET, None]
        assert source.status().picked == USB

    assert rig.events[:4] == [
        "open Headset (Polaris-BT)",
        "close Headset (Polaris-BT)",
        "restart",
        "open Microphone (USB)",
    ]
    assert switched == [USB]
    assert rig.said == []  # he chose it himself


def test_a_mic_that_does_not_open_is_never_switched_to():
    rig = Rig([HEADSET, USB])
    rig.mics.broken[USB] = ValueError("Device unavailable")
    switched = []
    with rig.source as source:
        frames = source.frames()
        marks(frames, 1)

        answer = switch_from_the_phone(rig, USB, frames, on_switched=switched.append)

        assert answer.state == "failed"
        assert "Device unavailable" in answer.message
        assert "Headset (Polaris-BT)" in answer.message
        assert source.status().mic.name == "Headset (Polaris-BT)"
        assert source.preferences == [HEADSET, USB, None]
    assert switched == []


def test_a_switch_not_taken_in_time_is_pending_and_its_outcome_shows_later():
    rig = Rig([HEADSET, USB])
    with rig.source as source:
        frames = source.frames()
        marks(frames, 1)

        answer = source.request_switch(USB, wait_s=0)
        assert answer.state == "pending"
        assert source.status().switching
        busy = source.request_switch(HEADSET, wait_s=0)
        marks(frames, 1)

        assert busy.state == "busy"
        assert not source.status().switching
        assert source.status().last_switch.state == "done"
        assert source.status().mic.name == "Microphone (USB)"


def test_following_the_config_again_drops_the_phone_s_pick():
    rig = Rig([HEADSET, USB], picked=USB)
    switched = []
    with rig.source as source:
        frames = source.frames()
        assert source.status().mic.name == "Microphone (USB)"

        answer = switch_from_the_phone(rig, None, frames, on_switched=switched.append)

        assert answer.state == "done"
        assert source.preferences == [HEADSET, USB, None]
        assert source.status().mic.name == "Headset (Polaris-BT)"
    assert switched == [None]


# --- what the Suara page shows ----------------------------------------------------------------


def test_the_status_names_the_mic_its_host_api_and_its_level():
    rig = Rig([HEADSET])
    with rig.source as source:
        rig.mics.streams[0].level = MicLevel(-51.0, -40.0, -44.0, 3.0)

        status = source.status()

    assert (status.mic.name, status.mic.host_api, status.mic.value) == (
        "Headset (Polaris-BT)",
        "MME",
        HEADSET,
    )
    assert status.level == MicLevel(-51.0, -40.0, -44.0, 3.0)
    assert status.preferences == [HEADSET, None]


def test_the_devices_are_listed_once_until_portaudio_restarts():
    rig = Rig([HEADSET, USB])
    rig.mics.failures[HEADSET] = MicrophoneError("Mikrofon terputus")
    with rig.source as source:
        frames = source.frames()
        devices, default = source.devices()
        source.devices()
        assert rig.events.count("list") == 1
        assert default == "Microphone (Onboard)"
        assert [device.name for device in devices] == [
            "Headset (Polaris-BT)",
            "Microphone (Onboard)",
        ]

        marks(frames, 3)  # the headset fails: PortAudio restarts
        source.devices()

    assert rig.events.count("list") == 2


def test_the_devices_are_listed_under_the_listing_lock():
    rig = Rig()

    with rig.source as source:
        source.devices()

    assert rig.mics.listed_locked == [True]  # a PortAudio restart waits for the listing


def test_a_new_mic_windows_lists_while_the_page_looks_makes_her_list_them_again():
    rig = Rig()  # one preference: never probed between frames
    with rig.source as source:
        frames = source.frames()
        source.devices()
        rig.mics.names = rig.mics.names | {"Microphone (USB)"}
        rig.clock.now += PROBE_S

        source.devices()  # the page asks again: Windows lists a new mic
        marks(frames, 2)
        source.devices()

    assert rig.events.count("restart") == 1
    assert rig.events.count("list") == 2


# --- Windows' list of inputs --------------------------------------------------------------------


class FakeWinmm:
    """winmm's input listing over `names`; a device whose caps cannot be read is skipped."""

    def __init__(self, names: list[str | None]) -> None:
        self.names = names
        # Plain functions, as ctypes' are, so a signature can be set on them.
        self.waveInGetNumDevs = lambda: len(self.names)  # noqa: N815 - winmm's name
        self.waveInGetDevCapsW = lambda *args: self._caps(*args)  # noqa: N815 - winmm's name

    def _caps(self, index, pointer, size) -> int:
        assert size == ctypes.sizeof(pointer._obj)
        name = self.names[index]
        if name is None:
            return 2  # MMSYSERR_BADDEVICEID: it went meanwhile
        pointer._obj.szPname = name
        return 0


def test_windows_inputs_are_listed_by_name():
    names = windows_input_names(
        FakeWinmm(["Headset (Polaris-BT)", None, "Microphone (High Definition Aud"])
    )

    assert names == frozenset({"Headset (Polaris-BT)", "Microphone (High Definition Aud"})


def test_a_portaudio_that_does_not_restart_still_has_every_mic_tried(caplog):
    rig = Rig([HEADSET, USB])
    rig.mics.failures[HEADSET] = MicrophoneError("Mikrofon terputus")
    rig.mics.restart_error = OSError("Error initializing PortAudio")

    with caplog.at_level(logging.ERROR, logger="waifu.voice.mic_source"), rig.source as source:
        assert marks(source.frames(), 4) == [1.0, 1.0, 2.0, 2.0]

    assert "Could not restart PortAudio" in caplog.text
