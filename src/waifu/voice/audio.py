"""Microphone and speaker I/O over sounddevice.

sounddevice is imported lazily (or injected as `sd_module`) so tests and text
mode never load PortAudio or open a real device. The microphone runs half
duplex: `pause()` while the assistant speaks so she never hears herself.

PortAudio lists the devices once, as it starts, so a microphone that came back after it started
is found only by restarting it (`restart_portaudio`), which closes every stream: the restart
holds `PLAYBACK`, which `Player.play` holds for its whole clip, and `LISTING`, which a device
listing on another thread holds, and is made only with the microphone closed.
"""

from __future__ import annotations

import contextlib
import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MAX_QUEUED_S = 5.0
POLL_S = 0.1
STALL_S = 2.0
END_POLL_S = 0.05  # how often `Player.play` checks whether its clip has played out
LEVEL_WINDOW_S = 1.0  # the span of `MicStream.level`
# How long a mic may send exact zeros before it counts as flat: well above a Bluetooth headset's
# switch to hands-free, which sends near-silence (not zeros) for a second or two.
FLAT_S = 8.0

# Held by `Player.play` for its whole clip and by a device listing, and both by
# `restart_portaudio`, whose stop of PortAudio would close a stream or a listing under them.
PLAYBACK = threading.Lock()
LISTING = threading.Lock()


class MicrophoneError(Exception):
    """The microphone stopped delivering audio; the message is Indonesian and safe to show."""


class MicrophoneFlat(MicrophoneError):
    """The microphone has sent only exact zeros for FLAT_S: muted at the driver, or gone."""


@dataclass(frozen=True)
class MicLevel:
    """What the microphone heard over the last LEVEL_WINDOW_S, in dBFS."""

    rms_db: float  # the whole window
    peak_db: float  # the loudest sample
    # The loudest block, each about one VAD frame long: what the VAD holds against its bar.
    loudest_db: float
    at: float  # when the window ended, on the stream's clock


def _db(amplitude: float) -> float:
    return 20 * math.log10(amplitude + 1e-10)


def _sounddevice(sd_module: Any) -> Any:
    if sd_module is not None:
        return sd_module
    import sounddevice

    return sounddevice


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample to `dst_rate`, keeping the duration; returns float32."""
    samples = np.asarray(audio, dtype=np.float32)
    if src_rate == dst_rate:
        return samples
    out_len = round(len(samples) * dst_rate / src_rate)
    positions = np.arange(out_len) * (src_rate / dst_rate)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


class _StreamResampler:
    """Linear-interpolation resampler for a stream split into arbitrary blocks.

    The last input sample and the fractional read position carry over between
    blocks, so the output is the same as resampling the whole stream at once.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self._step = src_rate / dst_rate
        self._position = 0.0
        self._tail = np.zeros(0, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        buffer = np.concatenate([self._tail, block])
        last = len(buffer) - 1
        count = max(0, int((last - self._position) // self._step) + 1)
        positions = self._position + self._step * np.arange(count)
        out = np.interp(positions, np.arange(len(buffer)), buffer).astype(np.float32)
        self._position += self._step * count - last
        self._tail = buffer[-1:]
        return out


class MicStream:
    """Microphone as an iterator of fixed-size float32 frames at `sample_rate`.

    Use as a context manager; `frames()` ends once the stream is closed and
    raises `MicrophoneError` when PortAudio stops calling back (mic unplugged,
    driver reset after sleep) instead of waiting forever, and `MicrophoneFlat` once it has sent
    only exact zeros for FLAT_S while not paused, once per run of zeros: `frames()` called again
    reads on (zeros, then whatever comes once the mic is unmuted). `level` is what it heard over
    the last second, paused or not, measured on the audio thread without keeping the samples.
    """

    def __init__(
        self,
        sample_rate: int,
        frame_ms: int,
        device: int | str | None = None,
        *,
        sd_module: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._frame_len = round(sample_rate * frame_ms / 1000)
        self._device = device
        self._sd = _sounddevice(sd_module)
        self._clock = clock
        self._queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=max(1, round(MAX_QUEUED_S * 1000 / frame_ms))
        )
        self._stream: Any = None
        self._device_rate = sample_rate
        self._paused = False
        self._epoch = 0
        self._closed = True
        self._dropped = 0
        self._last_callback = 0.0
        # The level window being measured, on the audio thread only.
        self._level_samples = 0
        self._sum_squares = 0.0
        self._peak = 0.0
        self._loudest = 0.0  # the highest mean square of a block
        self._level: MicLevel | None = None
        self._zeros_since: float | None = None  # when the current run of exact zeros began
        self._flat_told: float | None = None  # when the run already reported as flat began
        self.name: str | None = None
        self.host_api: str | None = None

    @property
    def level(self) -> MicLevel | None:
        """The level of the last whole second; None before one has passed. Any thread."""
        return self._level

    def __enter__(self) -> MicStream:
        info = self._sd.query_devices(self._device, "input")
        self.name = info["name"]
        self.host_api = self._sd.query_hostapis()[info["hostapi"]]["name"]
        self._device_rate = round(info["default_samplerate"])
        self._stream = self._sd.InputStream(
            samplerate=self._device_rate,
            blocksize=round(self._device_rate * self._frame_ms / 1000),
            device=self._device,
            channels=1,
            dtype="float32",
            callback=self._on_audio,
        )
        self._closed = False
        self._last_callback = self._clock()
        self._stream.start()
        logger.info(
            "Microphone %r opened at %d Hz, resampling to %d Hz",
            info["name"],
            self._device_rate,
            self._sample_rate,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._closed = True
        self._stream.stop()
        self._stream.close()
        logger.info("Microphone closed")

    def pause(self) -> None:
        """Drop incoming audio until `resume()`."""
        self._paused = True

    def resume(self) -> None:
        """Start listening again, discarding audio queued before the pause."""
        self._drain()
        self._dropped = 0
        self._epoch += 1
        self._paused = False

    def frames(self) -> Iterator[np.ndarray]:
        epoch = self._epoch
        resampler = _StreamResampler(self._device_rate, self._sample_rate)
        pending = np.zeros(0, dtype=np.float32)
        while True:
            try:
                block = self._queue.get(block=not self._closed, timeout=POLL_S)
            except queue.Empty:
                if self._closed:
                    return
                self._check_alive()
                continue
            self._check_flat()
            self._report_dropped()
            if epoch != self._epoch:
                epoch = self._epoch
                resampler = _StreamResampler(self._device_rate, self._sample_rate)
                pending = np.zeros(0, dtype=np.float32)
            pending = np.concatenate([pending, resampler.process(block)])
            while len(pending) >= self._frame_len and not self._paused and epoch == self._epoch:
                frame, pending = pending[: self._frame_len], pending[self._frame_len :]
                yield frame

    def _on_audio(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        # Runs on PortAudio's audio thread: copy and hand off, never block.
        # Callbacks keep coming while paused, so this also proves the mic is alive.
        now = self._clock()
        self._last_callback = now
        if status:
            logger.warning("Microphone status: %s", status)
        samples = indata[:, 0]
        if samples.size:
            self._measure(samples, now)
        if self._paused:
            self._zeros_since = None
            return
        if samples.any():
            self._zeros_since = None
        elif self._zeros_since is None:
            self._zeros_since = now
        block = samples.copy()
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(block)
            self._dropped += 1  # counted here, logged by the reader: no I/O on the audio thread

    def _measure(self, samples: np.ndarray, now: float) -> None:
        """Add `samples` to the level window and publish the window once it spans LEVEL_WINDOW_S.
        Arithmetic only, on the audio thread: the samples are never kept."""
        squares = float(np.dot(samples, samples))
        self._sum_squares += squares
        self._loudest = max(self._loudest, squares / samples.size)
        self._peak = max(self._peak, float(samples.max()), -float(samples.min()))
        self._level_samples += samples.size
        if self._level_samples < self._device_rate * LEVEL_WINDOW_S:
            return
        rms = math.sqrt(self._sum_squares / self._level_samples)
        self._level = MicLevel(_db(rms), _db(self._peak), _db(math.sqrt(self._loudest)), now)
        self._level_samples, self._sum_squares, self._peak, self._loudest = 0, 0.0, 0.0, 0.0

    def _check_flat(self) -> None:
        since = self._zeros_since
        if since is None or since == self._flat_told or self._clock() - since < FLAT_S:
            return
        self._flat_told = since
        logger.error("Microphone sent only exact zeros for %.1f s", self._clock() - since)
        raise MicrophoneFlat("Mikrofon hanya mengirim sunyi total.")

    def _check_alive(self) -> None:
        silent_s = self._clock() - self._last_callback
        if silent_s < STALL_S and self._stream.active:
            return
        logger.error(
            "Microphone stopped delivering audio (%.1f s since its last callback)", silent_s
        )
        raise MicrophoneError(
            "Mikrofon terputus atau berhenti mengirim suara. "
            "Periksa sambungan mikrofon, lalu jalankan ulang."
        )

    def _report_dropped(self) -> None:
        dropped, self._dropped = self._dropped, 0
        if dropped:
            logger.warning(
                "Microphone queue was full; dropped the oldest %d audio blocks (%.1f s)",
                dropped,
                dropped * self._frame_ms / 1000,
            )

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


class Player:
    """Blocking speaker output at the speaker's own rate, which `stop()` can cut short.

    WASAPI and WDM-KS outputs refuse any rate but the device's mix rate, so a
    24 kHz TTS clip is resampled first, as the microphone does on the way in.

    Only the thread in `play` calls PortAudio. `sounddevice.wait()` closes the stream
    once its finished callback fires, and PortAudio fires that from inside the
    `Pa_StopStream` of `sounddevice.stop()`, so a stop made from another thread would
    still be stopping (then closing) the stream while the waiting thread closes it: a
    use-after-free or double free inside PortAudio. `stop()` therefore only asks:
    `play` watches the stream and the request, and calls `sounddevice.stop()` (stop and
    close) itself, whether the clip played out or was cut.

    `stop()` and the end of `play` agree under one lock, so the caller always gets one
    answer: a `stop()` that lands after the clip reached its end but before `play`
    noticed counts as a cut for both (the clip was heard to the end anyway), and a
    `stop()` while `sounddevice.play` is still opening the stream finds nothing
    playing and returns False (the clip then plays). Each `play` starts with no
    request, so a late `stop()` can never cut the next clip.
    """

    def __init__(self, device: int | str | None = None, *, sd_module: Any = None) -> None:
        self._device = device
        self._sd = _sounddevice(sd_module)
        self._lock = threading.Lock()  # `stop()` and the end of `play` agree on both under it
        self._playing = False
        self._cut = threading.Event()  # `stop()`'s request; wakes `play` at once

    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        """Play `audio` and wait; True when it played to the end, False when `stop()` cut it."""
        with PLAYBACK:  # a PortAudio restart waits for the clip
            info = self._sd.query_devices(self._device, "output")
            device_rate = round(info["default_samplerate"])
            self._sd.play(
                resample(audio, sample_rate, device_rate),
                samplerate=device_rate,
                device=self._device,
            )
            with self._lock:
                self._playing = True
                self._cut.clear()
            try:
                self._await_end_or_cut(self._sd.get_stream())
            finally:
                with self._lock:
                    self._playing = False
                    cut = self._cut.is_set()
                self._sd.stop()  # stops the stream when cut, and closes it, on this thread
        return not cut

    def stop(self) -> bool:
        """Ask `play` to cut the clip being played; safe from any thread, never waits.

        It makes no PortAudio call itself (see the class). Returns False when nothing
        is playing.
        """
        with self._lock:
            if not self._playing:
                return False
            self._cut.set()
        return True

    def _await_end_or_cut(self, stream: Any) -> None:
        """Return once the clip has played out or `stop()` asked to cut it, whichever is first."""
        while stream.active:
            if self._cut.wait(END_POLL_S):
                return


def restart_portaudio(sd_module: Any = None) -> None:
    """Stop PortAudio and start it again, so it lists the devices of now.

    Its stop closes every stream: call it only with the microphone closed. It waits for a clip
    being played (`PLAYBACK`) and a listing under way (`LISTING`). sounddevice 0.5.6 has no
    public restart: its private `_terminate` and `_initialize` keep a count of starts, which a
    start that failed leaves at 0 (then it is only started). Logging it is left to the caller,
    which restarts it every few seconds while it waits for a mic.
    """
    sd = _sounddevice(sd_module)
    with PLAYBACK, LISTING:
        while sd._initialized:
            sd._terminate()
        sd._initialize()


def list_devices(sd_module: Any = None) -> str:
    """Human-readable (Indonesian) list of audio devices with the defaults marked."""
    sd = _sounddevice(sd_module)
    default_input, default_output = sd.default.device[0], sd.default.device[1]
    host_apis = sd.query_hostapis()
    lines = ["Perangkat audio (> mikrofon default, < speaker default, * keduanya):"]
    for info in sd.query_devices():
        index = info["index"]
        mark = " ><*"[(index == default_input) + 2 * (index == default_output)]
        lines.append(
            f"{mark} {index:>3} {info['name']}, {host_apis[info['hostapi']]['name']} "
            f"({info['max_input_channels']} masuk, {info['max_output_channels']} keluar)"
        )
    return "\n".join(lines)
