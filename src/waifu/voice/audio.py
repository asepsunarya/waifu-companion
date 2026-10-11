"""Microphone and speaker I/O over sounddevice.

sounddevice is imported lazily (or injected as `sd_module`) so tests and text
mode never load PortAudio or open a real device. The microphone runs half
duplex: `pause()` while the assistant speaks so she never hears herself.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import time
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MAX_QUEUED_S = 5.0
POLL_S = 0.1
STALL_S = 2.0


class MicrophoneError(Exception):
    """The microphone stopped delivering audio; the message is Indonesian and safe to show."""


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
    driver reset after sleep) instead of waiting forever.
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

    def __enter__(self) -> MicStream:
        info = self._sd.query_devices(self._device, "input")
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
        self._last_callback = self._clock()
        if status:
            logger.warning("Microphone status: %s", status)
        if self._paused:
            return
        block = indata[:, 0].copy()
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(block)
            self._dropped += 1  # counted here, logged by the reader: no I/O on the audio thread

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
    """Blocking speaker output at the speaker's own rate.

    WASAPI and WDM-KS outputs refuse any rate but the device's mix rate, so a
    24 kHz TTS clip is resampled first, as the microphone does on the way in.
    """

    def __init__(self, device: int | str | None = None, *, sd_module: Any = None) -> None:
        self._device = device
        self._sd = _sounddevice(sd_module)

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        info = self._sd.query_devices(self._device, "output")
        device_rate = round(info["default_samplerate"])
        self._sd.play(
            resample(audio, sample_rate, device_rate), samplerate=device_rate, device=self._device
        )
        self._sd.wait()


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
