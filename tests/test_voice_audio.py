import logging
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from waifu.voice.audio import MicrophoneError, MicStream, Player, list_devices, resample

WAIT_S = 2.0  # an upper bound for events between threads, never a sleep


class FakeInputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = self.stopped = self.closed = self.active = False

    def start(self):
        self.started = self.active = True

    def stop(self):
        self.stopped = True
        self.active = False

    def close(self):
        self.closed = True

    def push(self, block: np.ndarray) -> None:
        """Deliver one block the way PortAudio's audio thread does: (frames, channels)."""
        indata = np.asarray(block, dtype=np.float32).reshape(-1, 1)
        self.kwargs["callback"](indata, len(indata), None, 0)


class FakeOutputStream:
    """The stream `sounddevice.play` opens for one clip; its PortAudio calls are logged."""

    def __init__(self, sd: "FakeSounddevice", *, held: bool) -> None:
        self._sd = sd
        self.over = threading.Event()  # the clip ended or was stopped: the finished callback
        self.closes = 0
        if not held:
            self.over.set()

    @property
    def active(self) -> bool:
        self._sd.portaudio("Pa_IsStreamActive")
        return self.closes == 0 and not self.over.is_set()

    def stop(self) -> None:
        # PortAudio calls the finished callback, which wakes `wait()`, from inside Pa_StopStream.
        self._sd.portaudio("Pa_StopStream")
        self.over.set()

    def close(self) -> None:
        self._sd.portaudio("Pa_CloseStream")
        self.closes += 1


class FakeSounddevice:
    """sounddevice without PortAudio: nothing is ever played or recorded.

    `play`, `get_stream`, `wait` and `stop` mirror sounddevice 0.5.6: `play` stops and
    closes the previous clip's stream and starts a new one, `wait` waits for the clip's
    end and then closes the stream, `stop` stops and closes it. A clip ends at once, or
    with `hold_clips` only when stopped or `end_clip()` is called. Every PortAudio call
    is logged with the thread that made it.
    """

    def __init__(self, default_samplerate: float = 16000.0, *, hold_clips: bool = False):
        self.default_samplerate = default_samplerate
        self.hold_clips = hold_clips
        self.stream: FakeOutputStream | None = None
        self.watching = threading.Event()  # set once the player waits for or watches a clip
        self.portaudio_calls: list[tuple[str, str]] = []  # (call, thread name)
        self.streams: list[FakeInputStream] = []
        self.queries: list[tuple] = []
        self.calls: list[tuple] = []
        self.default = SimpleNamespace(device=[1, 0])
        self.devices = [
            {"name": "Speakers (Realtek)", "index": 0, "hostapi": 0,
             "max_input_channels": 0, "max_output_channels": 2},
            {"name": "USB Mic", "index": 1, "hostapi": 0,
             "max_input_channels": 1, "max_output_channels": 0},
            {"name": "Headset", "index": 2, "hostapi": 1,
             "max_input_channels": 1, "max_output_channels": 2},
        ]  # fmt: skip

    def query_devices(self, device=None, kind=None):
        self.queries.append((device, kind))
        if device is None and kind is None:
            return self.devices
        return {"name": "USB Mic", "default_samplerate": self.default_samplerate}

    def query_hostapis(self):
        return ({"name": "MME"}, {"name": "Windows WASAPI"})

    def InputStream(self, **kwargs):  # noqa: N802 - mirrors sounddevice's class name
        stream = FakeInputStream(**kwargs)
        self.streams.append(stream)
        return stream

    def play(self, data, samplerate=None, **kwargs):
        self.calls.append(("play", data, samplerate, kwargs))
        self._stop_and_close()
        self.portaudio("Pa_OpenStream")
        self.stream = FakeOutputStream(self, held=self.hold_clips)

    def get_stream(self):
        self.watching.set()
        return self.stream

    def wait(self):
        self.calls.append(("wait",))
        self.watching.set()
        try:
            self.stream.over.wait(WAIT_S)
        finally:
            self.stream.close()

    def stop(self):
        self.calls.append(("stop",))
        self._stop_and_close()

    def end_clip(self) -> None:
        """The held clip plays out by itself."""
        self.stream.over.set()

    def portaudio(self, call: str) -> None:
        self.portaudio_calls.append((call, threading.current_thread().name))

    def _stop_and_close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


def sine(freq: float, rate: int, seconds: float) -> np.ndarray:
    t = np.arange(round(rate * seconds)) / rate
    return 0.5 * np.sin(2 * np.pi * freq * t)


def test_resample_preserves_duration_and_returns_float32():
    audio = sine(440, 48000, 1.5)

    result = resample(audio, 48000, 16000)

    assert result.dtype == np.float32
    assert len(result) == 24000


def test_resample_rounds_the_output_length():
    assert len(resample(np.zeros(1000), 44100, 16000)) == round(1000 * 16000 / 44100)


def test_resample_is_identity_when_rates_match():
    audio = sine(440, 16000, 0.1).astype(np.float32)

    np.testing.assert_array_equal(resample(audio, 16000, 16000), audio)


def test_resample_of_empty_audio_is_empty_float32():
    result = resample(np.zeros(0), 48000, 16000)

    assert result.dtype == np.float32
    assert len(result) == 0


def test_resample_keeps_the_waveform():
    result = resample(sine(440, 44100, 0.5), 44100, 16000)

    np.testing.assert_allclose(result, sine(440, 16000, 0.5), atol=1e-2)


def test_mic_opens_a_mono_float32_stream_at_the_device_default_rate():
    sd = FakeSounddevice(default_samplerate=48000.0)

    with MicStream(16000, 30, device=2, sd_module=sd):
        stream = sd.streams[0]
        assert stream.started
        assert stream.kwargs["samplerate"] == 48000
        assert stream.kwargs["channels"] == 1
        assert stream.kwargs["dtype"] == "float32"
        assert stream.kwargs["device"] == 2
    assert (2, "input") in sd.queries


def test_mic_stops_and_closes_the_stream_on_exit():
    sd = FakeSounddevice()

    with MicStream(16000, 30, sd_module=sd):
        pass

    assert sd.streams[0].stopped
    assert sd.streams[0].closed


def test_frames_end_once_the_mic_is_closed():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd) as mic:
        pass

    assert list(mic.frames()) == []


def test_mic_resamples_irregular_blocks_into_continuous_fixed_size_frames():
    sd = FakeSounddevice(default_samplerate=44100.0)
    source = sine(440, 44100, 1.0)
    sizes = [480, 333, 1024, 7, 1500, 911]

    with MicStream(16000, 30, sd_module=sd) as mic:
        start, i = 0, 0
        while start < len(source):
            sd.streams[0].push(source[start : start + sizes[i % len(sizes)]])
            start += sizes[i % len(sizes)]
            i += 1
    frames = list(mic.frames())

    assert len(frames) == 16000 // 480
    assert all(len(f) == 480 and f.dtype == np.float32 for f in frames)
    joined = np.concatenate(frames)
    np.testing.assert_allclose(joined, sine(440, 16000, 1.0)[: len(joined)], atol=1e-2)


def test_mic_copies_audio_out_of_the_reused_callback_buffer():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd) as mic:
        indata = np.full((480, 1), 0.25, dtype=np.float32)
        sd.streams[0].kwargs["callback"](indata, 480, None, 0)
        indata[:] = 0.0
    (frame,) = list(mic.frames())

    assert np.all(frame == 0.25)


def test_paused_mic_drops_incoming_audio():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd) as mic:
        frames = mic.frames()
        sd.streams[0].push(np.full(480, 0.1))
        assert np.all(next(frames) == np.float32(0.1))

        mic.pause()
        sd.streams[0].push(np.full(480, 0.5))
        mic.resume()
        sd.streams[0].push(np.full(480, 0.9))

    remaining = list(frames)
    assert len(remaining) == 1
    assert np.all(remaining[0] == np.float32(0.9))


def test_resume_discards_audio_queued_before_the_pause():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd) as mic:
        sd.streams[0].push(np.full(480, 0.1))
        mic.pause()
        mic.resume()
        sd.streams[0].push(np.full(480, 0.9))

    frames = list(mic.frames())
    assert len(frames) == 1
    assert np.all(frames[0] == np.float32(0.9))


def test_resume_discards_frames_left_over_from_before_the_pause():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd) as mic:
        frames = mic.frames()
        sd.streams[0].push(np.full(960, 0.1))
        assert np.all(next(frames) == np.float32(0.1))

        mic.pause()
        mic.resume()
        sd.streams[0].push(np.full(480, 0.9))

    remaining = list(frames)
    assert len(remaining) == 1
    assert np.all(remaining[0] == np.float32(0.9))


def test_audio_queued_before_a_pause_is_not_yielded_while_paused():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd) as mic:
        sd.streams[0].push(np.full(480, 0.1))
        mic.pause()

    assert list(mic.frames()) == []


def test_paused_mic_does_not_buffer_audio_while_the_assistant_speaks(caplog):
    sd = FakeSounddevice()
    with caplog.at_level(logging.WARNING), MicStream(16000, 30, sd_module=sd) as mic:
        mic.pause()
        for _ in range(1000):
            sd.streams[0].push(np.full(480, 0.5))

    assert not any("dropped" in r.getMessage() for r in caplog.records)


def test_full_queue_drops_the_oldest_audio_and_warns(caplog):
    sd = FakeSounddevice()
    with caplog.at_level(logging.WARNING), MicStream(16000, 30, sd_module=sd) as mic:
        for value in range(1000):
            sd.streams[0].push(np.full(480, value / 1000))

    levels = [round(float(f[0]) * 1000) for f in mic.frames()]
    assert 0 < len(levels) < 1000
    assert levels == list(range(1000 - len(levels), 1000))
    assert any("dropped" in r.getMessage() for r in caplog.records)


def test_mic_that_stops_calling_back_raises_instead_of_going_silently_deaf():
    now = [0.0]
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd, clock=lambda: now[0]) as mic:
        frames = mic.frames()
        sd.streams[0].push(np.full(480, 0.1))
        next(frames)

        now[0] = (
            3.0  # USB mic unplugged: PortAudio stops calling back, the stream still looks active
        )
        with pytest.raises(MicrophoneError, match="Mikrofon"):
            next(frames)


def test_mic_whose_stream_was_aborted_raises_at_once():
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd, clock=lambda: 0.0) as mic:
        sd.streams[0].active = False

        with pytest.raises(MicrophoneError):
            next(mic.frames())


def test_a_long_reply_with_the_mic_paused_is_not_mistaken_for_a_dead_mic():
    now = [0.0]
    sd = FakeSounddevice()
    with MicStream(16000, 30, sd_module=sd, clock=lambda: now[0]) as mic:
        frames = mic.frames()
        mic.pause()
        for _ in range(300):  # a 9 s reply: PortAudio keeps calling back while paused
            now[0] += 0.03
            sd.streams[0].push(np.full(480, 0.1))
        mic.resume()
        owner_speaks = threading.Timer(0.3, sd.streams[0].push, [np.full(480, 0.9)])
        owner_speaks.start()  # arrives after the reader has already waited with nothing queued

        frame = next(frames)
    owner_speaks.join()

    assert np.all(frame == np.float32(0.9))


def dropped_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "dropped" in r.getMessage()]


def test_queue_overflow_is_reported_once_by_the_reader_not_per_block_by_the_audio_thread(caplog):
    sd = FakeSounddevice()
    with caplog.at_level(logging.WARNING), MicStream(16000, 30, sd_module=sd) as mic:
        frames = mic.frames()
        for _ in range(1000):
            sd.streams[0].push(np.full(480, 0.5))
        assert dropped_warnings(caplog) == []

        next(frames)
        next(frames)

    queue_blocks = round(5000 / 30)
    (warning,) = dropped_warnings(caplog)
    assert str(1000 - queue_blocks) in warning


def test_overflow_before_she_speaks_is_not_reported_because_resume_discards_it(caplog):
    sd = FakeSounddevice()
    with caplog.at_level(logging.WARNING), MicStream(16000, 30, sd_module=sd) as mic:
        for _ in range(1000):
            sd.streams[0].push(np.full(480, 0.5))
        mic.pause()
        mic.resume()
        sd.streams[0].push(np.full(480, 0.9))

    assert len(list(mic.frames())) == 1
    assert dropped_warnings(caplog) == []


def test_player_plays_float32_audio_on_its_device_and_closes_the_stream_after_it():
    sd = FakeSounddevice(default_samplerate=24000.0)
    audio = sine(440, 24000, 0.2)

    Player(device=3, sd_module=sd).play(audio, 24000)

    assert [call[0] for call in sd.calls] == ["play", "stop"]  # sounddevice.stop() also closes
    assert sd.stream.closes == 1
    _, data, samplerate, kwargs = sd.calls[0]
    assert data.dtype == np.float32
    assert samplerate == 24000
    assert kwargs["device"] == 3


def test_player_resamples_to_the_speaker_rate_because_wasapi_refuses_any_other():
    sd = FakeSounddevice(default_samplerate=48000.0)
    audio = sine(440, 24000, 0.2)

    Player(device=14, sd_module=sd).play(audio, 24000)

    _, data, samplerate, _ = sd.calls[0]
    assert (14, "output") in sd.queries
    assert samplerate == 48000
    assert len(data) == 2 * len(audio)
    assert data.dtype == np.float32


def test_player_reports_a_clip_played_to_the_end():
    sd = FakeSounddevice(default_samplerate=24000.0)

    assert Player(sd_module=sd).play(sine(440, 24000, 0.2), 24000) is True


def play_in_thread(player: Player, results: list[bool]) -> threading.Thread:
    """Play one second of audio on a new thread, appending what `play` returns to `results`."""
    clip = sine(440, 24000, 1.0)
    thread = threading.Thread(
        target=lambda: results.append(player.play(clip, 24000)), name="speaking"
    )
    thread.start()
    return thread


def test_play_waits_until_the_clip_has_played_out_by_itself():
    sd = FakeSounddevice(default_samplerate=24000.0, hold_clips=True)
    player = Player(sd_module=sd)
    results: list[bool] = []
    playing = play_in_thread(player, results)
    assert sd.watching.wait(WAIT_S)

    still_playing = playing.is_alive()
    sd.end_clip()
    playing.join(WAIT_S)

    assert still_playing
    assert not playing.is_alive()
    assert results == [True]
    assert sd.stream.closes == 1


def test_only_the_playing_thread_calls_portaudio_when_another_thread_cuts_the_clip():
    # sounddevice.wait() closes the stream once the clip ends, and PortAudio ends it from inside
    # the Pa_StopStream of a sounddevice.stop(): a stop from the avatar server's thread would
    # stop and close the same stream while the playing thread closes it (a double free).
    sd = FakeSounddevice(default_samplerate=24000.0, hold_clips=True)
    player = Player(sd_module=sd)
    results: list[bool] = []
    playing = play_in_thread(player, results)
    assert sd.watching.wait(WAIT_S)

    stopped = player.stop()
    playing.join(WAIT_S)

    assert (stopped, results) == (True, [False])
    assert {thread for _, thread in sd.portaudio_calls} == {"speaking"}
    assert sd.stream.closes == 1


def test_player_stop_from_another_thread_cuts_the_clip_at_once():
    sd = FakeSounddevice(default_samplerate=24000.0, hold_clips=True)
    player = Player(sd_module=sd)
    results: list[bool] = []
    playing = play_in_thread(player, results)
    assert sd.watching.wait(WAIT_S)

    stopped = player.stop()
    playing.join(WAIT_S)

    assert stopped is True
    assert not playing.is_alive()
    assert results == [False]
    assert [call[0] for call in sd.calls] == ["play", "stop"]


def test_player_stop_when_nothing_is_playing_returns_false_and_stops_nothing():
    sd = FakeSounddevice(default_samplerate=24000.0)
    player = Player(sd_module=sd)

    before = player.stop()
    player.play(sine(440, 24000, 0.2), 24000)
    after = player.stop()

    assert before is False
    assert after is False
    assert [call[0] for call in sd.calls] == ["play", "stop"]  # only the clip's own close


def test_a_clip_after_a_cut_one_plays_to_the_end():
    sd = FakeSounddevice(default_samplerate=24000.0, hold_clips=True)
    player = Player(sd_module=sd)
    results: list[bool] = []
    playing = play_in_thread(player, results)
    assert sd.watching.wait(WAIT_S)
    player.stop()
    playing.join(WAIT_S)

    sd.hold_clips = False
    finished = player.play(sine(440, 24000, 0.2), 24000)

    assert results == [False]
    assert finished is True


def test_a_stop_racing_the_end_of_the_clip_counts_as_a_cut_on_both_sides():
    # The clip has just played out, but `play` has not noticed yet: whichever way it is
    # reported, `stop()` and `play` must give the caller the same answer.
    sd = FakeSounddevice(default_samplerate=24000.0)
    player = Player(sd_module=sd)
    stops: list[bool] = []
    played_stream = sd.get_stream

    def ended_unnoticed():
        stops.append(player.stop())
        return played_stream()

    sd.get_stream = ended_unnoticed

    finished = player.play(sine(440, 24000, 0.2), 24000)

    assert stops == [True]
    assert finished is False


def test_a_stop_while_the_speaker_stream_opens_finds_nothing_playing():
    sd = FakeSounddevice(default_samplerate=24000.0)
    player = Player(sd_module=sd)
    stops: list[bool] = []
    start_clip = sd.play

    def opening(*args, **kwargs) -> None:
        stops.append(player.stop())
        start_clip(*args, **kwargs)

    sd.play = opening

    finished = player.play(sine(440, 24000, 0.2), 24000)

    assert stops == [False]
    assert finished is True


@pytest.mark.parametrize(
    ("name", "mark"),
    [("USB Mic", ">"), ("Speakers (Realtek)", "<"), ("Headset", " ")],
)
def test_list_devices_marks_the_default_input_and_output(name, mark):
    text = list_devices(sd_module=FakeSounddevice())

    line = next(line for line in text.splitlines() if name in line)
    assert line.startswith(mark)


def test_list_devices_shows_host_api_and_channels_in_indonesian():
    text = list_devices(sd_module=FakeSounddevice())

    assert text.splitlines()[0].startswith("Perangkat audio")
    assert "Headset, Windows WASAPI (1 masuk, 2 keluar)" in text
