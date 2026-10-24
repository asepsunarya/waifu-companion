"""The mic Cyrene listens through, kept open across failures and switches (SPEC, diagnosa suara
(v1.47)).

`MicSource` is the voice loop's `Microphone` over one `MicStream` at a time, picked from the
preferences in order: the mic picked on the phone, then `audio.input_device`'s list, then the
Windows default (None). When the mic in use fails (it stops calling back, sends only exact
zeros, or PortAudio raises), she goes on with the next one that opens and says once which mic she
now uses; when none opens, she hears silence at the frames' pace (so rings and reminders are
still said between frames) and tries again every RETRY_S. PortAudio lists the devices once, as it
starts, so with `restart` it is restarted before such a reopen, and whenever Windows' own list of
inputs (`present`, read from winmm) changes, so a preferred mic that came back is found and
switched back to.

Every open, close, and restart happens on the thread iterating `frames()`, between two frames,
never on the phone server's thread: closing a stream under its reader would make a PortAudio call
on a closed stream. A switch the phone asks for (`request_switch`) is therefore posted and taken
there, and the phone server's thread waits for it a moment. The pause she speaks under carries
over to a stream opened meanwhile.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol

import numpy as np

from waifu.voice.audio import LISTING, MicLevel
from waifu.voice.mic_check import InputDevice

logger = logging.getLogger(__name__)

RETRY_S = 5.0  # how often a mic is tried again while none opens
PROBE_S = 5.0  # how often Windows' list of inputs is read for a mic that came or went
# What she says (through `announce`) when the mic she listens through changed by itself.
MIC_NOW = "Sekarang aku pakai mic {name}."
MIC_BACK = "Mic {name} tersambung lagi, aku pakai itu lagi."
# The answers to a switch asked for on the phone.
SWITCHED = "Sekarang memakai {name}."
FOLLOWING_CONFIG = "Mengikuti urutan mic di config: sekarang memakai {name}."
NONE_FOLLOWING = "Mengikuti urutan mic di config, tapi belum ada mic yang bisa dibuka."
NOT_OPENED = "{device} tidak bisa dibuka ({why}). Tetap memakai {name}."
NONE_OPENED = "{device} tidak bisa dibuka ({why}), dan belum ada mic lain yang bisa dibuka."
SWITCH_PENDING = "Mic sedang diganti. Hasilnya muncul di sini sebentar lagi."
SWITCH_BUSY = "Mic masih diganti, tunggu sebentar."
DEFAULT_LABEL = "mic default Windows"

Device = int | str | None
DeviceList = tuple[list[InputDevice], str | None]  # the inputs, and the Windows default's name


class Stream(Protocol):
    """What MicSource needs of a `MicStream`."""

    name: str | None
    host_api: str | None

    @property
    def level(self) -> MicLevel | None: ...

    def __enter__(self) -> Any: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def frames(self) -> Iterator[np.ndarray]: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


@dataclass(frozen=True)
class ActiveMic:
    name: str
    host_api: str | None
    value: Device  # the preference it was opened as


@dataclass(frozen=True)
class SwitchResult:
    state: str  # "done", "failed", "pending", or "busy"
    message: str  # Indonesian, for the phone


@dataclass(frozen=True)
class MicStatus:
    mic: ActiveMic | None  # None while no mic opens
    level: MicLevel | None
    preferences: list[Device]
    picked: str | None  # the mic picked on the phone
    switching: bool  # a switch the phone asked for waits for `run`'s thread
    last_switch: SwitchResult | None


class _Switch:
    """A switch the phone asked for, waiting for the thread that reads the frames."""

    def __init__(
        self, device: str | None, on_switched: Callable[[str | None], None] | None
    ) -> None:
        self.device = device  # None: follow the config again
        self.on_switched = on_switched
        self.done = threading.Event()
        self.result: SwitchResult | None = None


class MicSource:
    def __init__(
        self,
        preferences: Sequence[int | str],
        *,
        open_stream: Callable[[Device], Stream],
        sample_rate: int,
        frame_ms: int,
        picked: str | None = None,
        announce: Callable[[str], None] | None = None,
        on_swap: Callable[[], None] | None = None,
        restart: Callable[[], None] | None = None,
        present: Callable[[], frozenset[str]] | None = None,
        listing: Callable[[], DeviceList] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`preferences` are `audio.input_device`'s, `picked` the phone's (the override file).
        `open_stream(device)` makes an unopened `MicStream`; `announce` says a line by herself;
        `on_swap` runs after every (re)open (the segmenter relearns the room); `restart`
        restarts PortAudio (None: never, with a numbered device); `present` reads the names of
        Windows' inputs (None: never); `listing` lists the inputs for the Suara page."""
        self._config = list(preferences)
        self._picked = picked
        self._open_stream = open_stream
        self._frame_s = frame_ms / 1000
        self._silence = np.zeros(round(sample_rate * frame_ms / 1000), dtype=np.float32)
        self._announce = announce
        self._on_swap = on_swap
        self._restart = restart
        self._present = present
        self._listing = listing
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()  # the stream in use, the pause, and the switch asked for
        self._stream: Stream | None = None
        self._value: Device = None
        self._paused = False
        self._closed = False
        self._request: _Switch | None = None
        self._last_switch: SwitchResult | None = None
        self._last_name: str | None = None  # the mic she listened through last
        self._last_rank: int | None = None
        self._retry_at: float | None = None  # while no mic opens
        self._known_names: frozenset[str] | None = None  # Windows' inputs at PortAudio's start
        self._next_probe = 0.0
        self._next_page_probe = 0.0
        self._relist = False  # the page saw Windows' list change: restart PortAudio
        self._devices: DeviceList | None = None

    # --- the voice loop's Microphone --------------------------------------------------------

    def __enter__(self) -> MicSource:
        """Open the first preference that opens; raises the last error when none does."""
        self._known_names = self._read_present()
        self._next_probe = self._clock() + PROBE_S
        preferences = self.preferences
        error: Exception | None = None
        for rank, value in enumerate(preferences):
            try:
                stream = self._opened(value)
            except Exception as exc:
                logger.warning("Microphone %s does not open: %s", _label(value), exc)
                error = exc
                continue
            self._use(value, stream, announce=rank > 0)
            return self
        assert error is not None  # the preferences end with the Windows default
        raise error

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._closed = True
        self._close_current()

    def frames(self) -> Iterator[np.ndarray]:
        """The frames of the mic in use, then of the next one after a failure or a switch, and
        silence while none opens; ends once the source is closed."""
        while not self._closed:
            stream = self._stream
            if stream is None:
                yield self._silence.copy()
                self._sleep(self._frame_s)
                self._maintain()
                continue
            frames = stream.frames()
            while self._stream is stream:
                try:
                    frame = next(frames)
                except StopIteration:  # its stream was closed: so was the source
                    return
                except Exception as exc:  # MicrophoneError, MicrophoneFlat, or PortAudio's own
                    logger.warning("Microphone %s failed: %s", stream.name, exc)
                    self._close_current()
                    self._reopen(failed=(self._value,))
                    break
                yield frame
                self._maintain()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            if self._stream is not None:
                self._stream.pause()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            if self._stream is not None:
                self._stream.resume()

    # --- for the Suara page ---------------------------------------------------------------

    @property
    def preferences(self) -> list[Device]:
        """The order the mics are tried in: the phone's pick, the config's, the default."""
        picked = [] if self._picked is None else [self._picked]
        order: list[Device] = []
        for value in [*picked, *self._config, None]:
            if value not in order:
                order.append(value)
        return order

    def status(self) -> MicStatus:
        """What the Suara page shows of the mic; from any thread."""
        with self._lock:
            stream, value = self._stream, self._value
        mic = None
        if stream is not None and stream.name is not None:
            mic = ActiveMic(stream.name, stream.host_api, value)
        return MicStatus(
            mic=mic,
            level=None if stream is None else stream.level,
            preferences=self.preferences,
            picked=self._picked,
            switching=self._request is not None,
            last_switch=self._last_switch,
        )

    def devices(self) -> DeviceList:
        """The inputs and the Windows default as PortAudio listed them at its last start, read
        once (under LISTING) and kept until it restarts. Asked while Windows' own list has
        changed (read at most every PROBE_S), it also has PortAudio restarted between two
        frames, so the page lists a mic plugged in since."""
        now = self._clock()
        if self._restart is not None and now >= self._next_page_probe:
            self._next_page_probe = now + PROBE_S
            names = self._read_present()
            if names is not None and names != self._known_names:
                self._relist = True
        devices = self._devices
        if devices is None:
            with LISTING:
                devices = ([], None) if self._listing is None else self._listing()
            self._devices = devices
        return devices

    def request_switch(
        self,
        device: str | None,
        *,
        wait_s: float,
        on_switched: Callable[[str | None], None] | None = None,
    ) -> SwitchResult:
        """Switch to `device` (None: follow the config again) on the thread that reads the
        frames, waiting up to `wait_s` for it; `on_switched(device)` runs there once it worked.
        A request while another waits is refused (busy); one not taken in time is pending."""
        request = _Switch(device, on_switched)
        with self._lock:
            if self._request is not None:
                return SwitchResult("busy", SWITCH_BUSY)
            self._request = request
        if not request.done.wait(wait_s):
            return SwitchResult("pending", SWITCH_PENDING)
        assert request.result is not None
        return request.result

    # --- between two frames, on the reading thread -----------------------------------------

    def _maintain(self) -> None:
        """Take a switch the phone asked for, retry while no mic opens, or notice Windows'
        list of inputs change."""
        request = self._request
        if request is not None:
            self._switch(request)
            return
        now = self._clock()
        if self._stream is None:
            if self._retry_at is not None and now >= self._retry_at:
                self._reopen()
            return
        if self._relist:
            self._relist = False
            self._reopen_after_change()
            return
        if self._restart is None or len(self.preferences) < 2 or now < self._next_probe:
            return
        self._next_probe = now + PROBE_S
        names = self._read_present()
        if names is not None and names != self._known_names:
            self._reopen_after_change()

    def _reopen_after_change(self) -> None:
        """Close the mic, restart PortAudio, and open the first preference that opens."""
        logger.info("Windows lists other microphones now; listing them again")
        self._close_current()
        self._reopen()

    def _reopen(self, *, failed: tuple[Device, ...] = (), announce: bool = True) -> None:
        """Restart PortAudio when allowed, then open the first preference that opens, those
        that just `failed` last; with none, wait and try again every RETRY_S."""
        self._restart_portaudio()
        waiting = self._retry_at is not None
        preferences = self.preferences
        order = [value for value in preferences if value not in failed]
        order += [value for value in preferences if value in failed]
        for value in order:
            try:
                stream = self._opened(value)
            except Exception as exc:
                log = logger.debug if waiting else logger.warning
                log("Microphone %s does not open: %s", _label(value), exc)
                continue
            self._use(value, stream, announce=announce)
            return
        self._retry_at = self._clock() + RETRY_S
        if not waiting:
            logger.error("No microphone opens; trying again every %g s", RETRY_S)

    def _switch(self, request: _Switch) -> None:
        """Take the phone's switch: close the mic, restart PortAudio, open the one asked for;
        when it does not open, the one in use before (or the first that opens)."""
        previous = self._value
        self._close_current()
        if request.device is None:
            self._picked = None
            self._reopen(announce=False)
            result = self._following_config_result()
        else:
            self._restart_portaudio()
            result = self._switch_to(request.device, previous)
        if result.state == "done" and request.on_switched is not None:
            try:
                request.on_switched(request.device)
            except Exception:
                logger.exception("Could not keep the microphone picked on the phone")
        self._last_switch = request.result = result
        with self._lock:
            self._request = None
        request.done.set()

    def _switch_to(self, device: str, previous: Device) -> SwitchResult:
        try:
            stream = self._opened(device)
        except Exception as exc:
            logger.warning("The microphone picked on the phone, %s, does not open: %s", device, exc)
            others = [value for value in self.preferences if value != device]
            for value in [previous, *others]:
                with contextlib.suppress(Exception):
                    self._use(value, self._opened(value), announce=False)
                    return SwitchResult(
                        "failed", NOT_OPENED.format(device=device, why=exc, name=self._last_name)
                    )
            self._retry_at = self._clock() + RETRY_S
            return SwitchResult("failed", NONE_OPENED.format(device=device, why=exc))
        self._picked = device
        self._use(device, stream, announce=False)
        logger.info("Switched the microphone to %s, picked on the phone", device)
        return SwitchResult("done", SWITCHED.format(name=stream.name))

    def _following_config_result(self) -> SwitchResult:
        stream = self._stream
        if stream is None:
            return SwitchResult("failed", NONE_FOLLOWING)
        return SwitchResult("done", FOLLOWING_CONFIG.format(name=stream.name))

    # --- streams ---------------------------------------------------------------------------

    def _opened(self, value: Device) -> Stream:
        stream = self._open_stream(value)
        stream.__enter__()
        return stream

    def _use(self, value: Device, stream: Stream, *, announce: bool) -> None:
        """Listen through `stream`, opened as `value`, paused as the source is; say so when it
        is another mic than before (MIC_BACK when it is a more preferred one)."""
        with self._lock:  # a stream opens unpaused: one opened while she speaks is paused now
            if self._paused:
                stream.pause()
            self._stream, self._value = stream, value
        self._retry_at = None
        name, rank = stream.name, self.preferences.index(value)
        previous_name, previous_rank = self._last_name, self._last_rank
        self._last_name, self._last_rank = name, rank
        if self._on_swap is not None:
            self._on_swap()
        if name == previous_name:
            return
        logger.info("Listening through %s (%s)", name, _label(value))
        if not announce or self._announce is None:
            return
        back = previous_rank is not None and rank < previous_rank
        self._announce((MIC_BACK if back else MIC_NOW).format(name=name))

    def _close_current(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.__exit__(None, None, None)
        except Exception as exc:  # a stream that failed may not stop cleanly
            logger.warning("Could not close microphone %s cleanly: %s", stream.name, exc)

    def _restart_portaudio(self) -> None:
        if self._restart is None:
            return
        try:
            self._restart()
        except Exception:
            logger.exception("Could not restart PortAudio")
        self._devices = None
        self._known_names = self._read_present()

    def _read_present(self) -> frozenset[str] | None:
        if self._present is None:
            return None
        try:
            return self._present()
        except Exception as exc:
            logger.warning("Could not read Windows' list of microphones: %s", exc)
            return None


def _label(value: Device) -> str:
    return DEFAULT_LABEL if value is None else repr(value)


class _WaveInCaps(ctypes.Structure):
    """WAVEINCAPSW (mmeapi.h): a name of at most 31 characters, as MME cuts it."""

    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


def windows_input_names(winmm: Any = None) -> frozenset[str]:
    """The names of the inputs Windows lists now (winmm's, MME's 31-character names): unlike
    PortAudio's list, it follows devices as they come and go. Read-only and cheap. winmm is
    looked up at every call, never at import, so the test suite's guard on it always applies."""
    library = winmm if winmm is not None else ctypes.WinDLL("winmm")
    count_inputs = library.waveInGetNumDevs
    count_inputs.argtypes, count_inputs.restype = [], wintypes.UINT
    read_caps = library.waveInGetDevCapsW
    read_caps.argtypes = [ctypes.c_size_t, ctypes.POINTER(_WaveInCaps), wintypes.UINT]
    read_caps.restype = wintypes.UINT  # MMRESULT: 0 when read
    names = set()
    for index in range(count_inputs()):
        caps = _WaveInCaps()
        if read_caps(index, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.add(caps.szPname)
    return frozenset(names)
