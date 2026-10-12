"""AvatarBus: thread-safe fan-out of avatar events to every SSE subscriber."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from waifu.avatar.bus import EVENT_QUEUE_SIZE, AvatarBus, Subscription, viewer_settings
from waifu.config import AvatarConfig, AvatarInteractionsConfig

WAIT_S = 5.0
CREDIT = "つくよみちゃん（© Rei Yumesaki）"  # the default avatar.credit
DEFAULT_INTERACTIONS = {
    "enabled": True,
    "cursor_follow": True,
    "head_pat": True,
    "body_touch": True,
    "double_click_listen": True,
    "idle": True,
    "welcome_back_after_min": 10,
}
HELLO = {
    "type": "hello",
    "engine": "vrm",
    "model": "/model.vrm",
    "core": None,
    "name": "Cyrene",
    "framing": "full",
    "credit": "つくよみちゃん（© Rei Yumesaki）",
    "interactions": DEFAULT_INTERACTIONS,
}
IDLE = {"type": "state", "state": "idle"}
LIVE2D_MODEL = Path("C:/AI/avatars/cyrene-live2d/Cyrene.model3.json")
LIVE2D_CORE = Path("C:/AI/avatars/live2d/live2dcubismcore.min.js")


class FakeTrack:
    """Stands in for lipsync.viseme_track; returns fixed, unrounded weights."""

    def __init__(self, frames: list[tuple[float, float, float, float, float]]) -> None:
        self.frames = frames
        self.calls: list[tuple[Any, int, int]] = []

    def __call__(
        self, audio: np.ndarray, sample_rate: int, fps: int = 60
    ) -> list[tuple[float, float, float, float, float]]:
        self.calls.append((audio, sample_rate, fps))
        return self.frames


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def drain(subscription: Subscription) -> list[dict[str, Any]]:
    """Every event already queued, without waiting for more."""
    events = []
    while (event := subscription.get(timeout=0)) is not None:
        events.append(event)
    return events


# --- subscribing -----------------------------------------------------------------------------


def test_a_new_subscriber_first_gets_hello_then_the_idle_state() -> None:
    bus = AvatarBus()

    assert drain(bus.subscribe()) == [HELLO, IDLE]


def test_hello_carries_the_name_framing_and_the_model_credit() -> None:
    bus = AvatarBus(name="Tsukuyomi", framing="upper")

    hello = bus.subscribe().get(timeout=0)

    assert hello == {
        "type": "hello",
        "engine": "vrm",
        "model": "/model.vrm",
        "core": None,
        "name": "Tsukuyomi",
        "framing": "upper",
        "credit": CREDIT,
        "interactions": DEFAULT_INTERACTIONS,
    }
    assert bus.name == "Tsukuyomi"


# --- what a viewer needs to show the avatar (hello and /config.json) -------------------------


def test_viewer_settings_for_vrm_name_the_vrm_route_and_no_core() -> None:
    config = AvatarConfig(model=Path("C:/AI/avatars/cyrene.vrm"), framing="upper")

    assert viewer_settings(config, "Cyrene") == {
        "engine": "vrm",
        "model": "/model.vrm",
        "core": None,
        "name": "Cyrene",
        "framing": "upper",
        "credit": CREDIT,
        "interactions": DEFAULT_INTERACTIONS,
    }


def test_viewer_settings_for_live2d_name_the_model_file_the_core_and_the_switches() -> None:
    config = AvatarConfig(
        engine="live2d",
        model=LIVE2D_MODEL,
        live2d_core=LIVE2D_CORE,
        credit="Cyrene © miHoYo, model by MomokaSono",
        interactions=AvatarInteractionsConfig(
            head_pat=False, idle=False, welcome_back_after_min=0, reaction_cooldown_s=9
        ),
    )

    assert viewer_settings(config, "Cyrene") == {
        "engine": "live2d",
        "model": "/model/Cyrene.model3.json",
        "core": "/live2d/live2dcubismcore.min.js",
        "name": "Cyrene",
        "framing": "full",
        "credit": "Cyrene © miHoYo, model by MomokaSono",
        "interactions": {
            **DEFAULT_INTERACTIONS,
            "head_pat": False,
            "idle": False,
            "welcome_back_after_min": 0,
        },
    }


def test_the_live2d_model_url_names_only_the_file_percent_encoded() -> None:
    config = AvatarConfig(engine="live2d", model=Path("C:/AI/キュレネ/キュレネ 2025#1.model3.json"))

    assert viewer_settings(config, "Cyrene")["model"] == (
        "/model/%E3%82%AD%E3%83%A5%E3%83%AC%E3%83%8D%202025%231.model3.json"
    )


def test_viewer_settings_hold_no_token_lines_or_cooldown() -> None:
    settings = viewer_settings(AvatarConfig(), "Cyrene")

    assert "token" not in settings
    assert set(settings["interactions"]) == set(DEFAULT_INTERACTIONS)


def test_viewer_settings_replace_hello_for_later_subscribers() -> None:
    bus = AvatarBus(name="Cyrene")
    settings = viewer_settings(
        AvatarConfig(engine="live2d", model=LIVE2D_MODEL, live2d_core=LIVE2D_CORE), "Cyrene"
    )

    bus.set_viewer_settings(settings)
    settings["engine"] = "vrm"

    hello = bus.subscribe().get(timeout=0)
    assert hello is not None
    assert hello["type"] == "hello"
    assert hello["engine"] == "live2d"
    assert hello["model"] == "/model/Cyrene.model3.json"
    assert hello["core"] == "/live2d/live2dcubismcore.min.js"


def test_a_late_subscriber_gets_the_last_state_but_no_older_events() -> None:
    bus = AvatarBus()
    bus.state("thinking")
    bus.emotion("happy")
    bus.state("listening", followup_s=8)

    assert drain(bus.subscribe()) == [
        HELLO,
        {"type": "state", "state": "listening", "followup_s": 8},
    ]


def test_a_state_published_directly_also_becomes_the_last_state() -> None:
    bus = AvatarBus()
    bus.publish({"type": "state", "state": "speaking"})

    assert drain(bus.subscribe())[1] == {"type": "state", "state": "speaking"}


# --- publishing ------------------------------------------------------------------------------


def test_published_events_reach_every_subscriber_in_order() -> None:
    bus = AvatarBus()
    first, second = bus.subscribe(), bus.subscribe()
    drain(first), drain(second)

    bus.publish({"type": "emotion", "emotion": "sad", "hold_s": 4})
    bus.state("thinking")

    expected = [
        {"type": "emotion", "emotion": "sad", "hold_s": 4},
        {"type": "state", "state": "thinking"},
    ]
    assert drain(first) == expected
    assert drain(second) == expected


def test_publish_copies_the_event() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)
    event = {"type": "emotion", "emotion": "sad", "hold_s": 4}

    bus.publish(event)
    event["emotion"] = "angry"

    assert drain(subscription) == [{"type": "emotion", "emotion": "sad", "hold_s": 4}]


def test_a_full_queue_drops_the_oldest_events_without_blocking_the_publisher() -> None:
    bus = AvatarBus()
    slow = bus.subscribe()  # never read while publishing: hello and idle are queued
    published = [{"type": "emotion", "emotion": "happy", "hold_s": i} for i in range(100)]

    started = time.monotonic()
    for event in published:
        bus.publish(event)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert drain(slow) == published[-EVENT_QUEUE_SIZE:]
    assert EVENT_QUEUE_SIZE == 64


def test_a_slow_subscriber_does_not_hold_back_a_fast_one() -> None:
    bus = AvatarBus()
    bus.subscribe()  # never read
    fast = bus.subscribe()
    drain(fast)

    for hold_s in range(EVENT_QUEUE_SIZE + 10):
        bus.emotion("happy", hold_s=hold_s)
        assert fast.get(timeout=WAIT_S) == {"type": "emotion", "emotion": "happy", "hold_s": hold_s}


# --- waiting, unsubscribing, closing ---------------------------------------------------------


def test_get_returns_none_when_nothing_arrives_in_time() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)

    started = time.monotonic()
    assert subscription.get(timeout=0.05) is None
    assert time.monotonic() - started >= 0.04
    assert not subscription.closed


def test_get_wakes_up_for_an_event_published_from_another_thread() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)
    timer = threading.Timer(0.05, bus.emotion, args=("surprised",))
    timer.start()

    started = time.monotonic()
    event = subscription.get(timeout=WAIT_S)

    timer.join()
    assert event == {"type": "emotion", "emotion": "surprised", "hold_s": 4}
    assert time.monotonic() - started < 1.0


def test_close_wakes_every_waiting_subscriber() -> None:
    bus = AvatarBus()
    subscriptions = [bus.subscribe() for _ in range(3)]
    for subscription in subscriptions:
        drain(subscription)
    results: list[dict[str, Any] | None] = []
    waiters = [
        threading.Thread(target=lambda s=s: results.append(s.get(timeout=WAIT_S)))
        for s in subscriptions
    ]
    for waiter in waiters:
        waiter.start()

    started = time.monotonic()
    bus.close()
    for waiter in waiters:
        waiter.join(timeout=WAIT_S)

    assert time.monotonic() - started < 1.0
    assert results == [None, None, None]
    assert all(subscription.closed for subscription in subscriptions)


def test_a_closed_subscription_returns_none_at_once_even_with_events_queued() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()  # hello and idle still queued

    bus.close()

    assert subscription.get(timeout=WAIT_S) is None


def test_subscribing_to_a_closed_bus_gives_a_closed_subscription() -> None:
    bus = AvatarBus()
    bus.close()

    subscription = bus.subscribe()

    assert subscription.closed
    assert subscription.get(timeout=WAIT_S) is None


def test_an_unsubscribed_subscription_is_closed_and_gets_nothing_more() -> None:
    bus = AvatarBus()
    leaving, staying = bus.subscribe(), bus.subscribe()
    drain(staying)

    bus.unsubscribe(leaving)
    bus.state("thinking")

    assert leaving.closed
    assert leaving.get(timeout=0) is None
    assert drain(staying) == [{"type": "state", "state": "thinking"}]


def test_unsubscribing_twice_is_harmless() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()

    bus.unsubscribe(subscription)
    bus.unsubscribe(subscription)

    assert subscription.closed


# --- state, emotion, speak helpers -----------------------------------------------------------


@pytest.mark.parametrize("name", ["idle", "listening", "thinking", "speaking"])
def test_state_publishes_each_known_state(name: str) -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)

    bus.state(name)

    assert drain(subscription) == [{"type": "state", "state": name}]


def test_listening_carries_the_followup_window() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)

    bus.state("listening", followup_s=8.0)

    assert drain(subscription) == [{"type": "state", "state": "listening", "followup_s": 8.0}]


def test_state_rejects_an_unknown_state() -> None:
    bus = AvatarBus()

    with pytest.raises(ValueError, match="sleeping"):
        bus.state("sleeping")


def test_state_rejects_a_followup_window_outside_listening() -> None:
    bus = AvatarBus()

    with pytest.raises(ValueError, match="followup_s"):
        bus.state("thinking", followup_s=8)


def test_emotion_holds_for_four_seconds_by_default() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)

    bus.emotion("relaxed")
    bus.emotion("angry", hold_s=1.5)

    assert drain(subscription) == [
        {"type": "emotion", "emotion": "relaxed", "hold_s": 4},
        {"type": "emotion", "emotion": "angry", "hold_s": 1.5},
    ]


def test_emotion_rejects_a_name_outside_the_six_emotions() -> None:
    bus = AvatarBus()

    with pytest.raises(ValueError, match="joy"):
        bus.emotion("joy")


def test_speak_publishes_the_rounded_viseme_track_with_fps_duration_and_emotion() -> None:
    track = FakeTrack([(0.123, 0.0, 0.987654, 0.5, 0.004), (np.float32(0.3333), 0, 0, 0, 1.0)])
    bus = AvatarBus(viseme_track=track)
    subscription = bus.subscribe()
    drain(subscription)
    audio = np.zeros(24_000 * 3 + 12_000, dtype=np.float32)

    bus.speak(audio, 24_000, "happy")

    assert drain(subscription) == [
        {
            "type": "speak",
            "emotion": "happy",
            "fps": 60,
            "duration": 3.5,
            "visemes": [[0.12, 0.0, 0.99, 0.5, 0.0], [0.33, 0.0, 0.0, 0.0, 1.0]],
        }
    ]
    assert len(track.calls) == 1
    called_audio, sample_rate, fps = track.calls[0]
    assert called_audio is audio
    assert (sample_rate, fps) == (24_000, 60)


def test_speak_weights_are_plain_floats_that_serialize_to_json() -> None:
    track = FakeTrack([(np.float32(0.25), np.float64(0.5), 0, 0, 0)])
    bus = AvatarBus(viseme_track=track)
    subscription = bus.subscribe()
    drain(subscription)

    bus.speak(np.zeros(100, dtype=np.float32), 16_000, "neutral")

    (event,) = drain(subscription)
    assert json.loads(json.dumps(event))["visemes"] == [[0.25, 0.5, 0.0, 0.0, 0.0]]


def test_speak_measures_the_duration_of_multichannel_audio_in_frames() -> None:
    bus = AvatarBus(viseme_track=FakeTrack([]))
    subscription = bus.subscribe()
    drain(subscription)

    bus.speak(np.zeros((48_000, 2), dtype=np.float32), 48_000, "sad")

    assert drain(subscription)[0]["duration"] == 1.0


def test_speak_rejects_an_unknown_emotion_before_building_the_track() -> None:
    track = FakeTrack([])
    bus = AvatarBus(viseme_track=track)

    with pytest.raises(ValueError, match="joy"):
        bus.speak(np.zeros(100, dtype=np.float32), 16_000, "joy")
    assert track.calls == []


def test_speak_uses_the_lipsync_track_by_default() -> None:
    bus = AvatarBus()
    subscription = bus.subscribe()
    drain(subscription)
    sample_rate = 16_000
    t = np.arange(sample_rate // 2) / sample_rate
    audio = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    bus.speak(audio, sample_rate, "happy")

    (event,) = drain(subscription)
    assert event["duration"] == 0.5
    assert len(event["visemes"]) == 30
    assert all(len(frame) == 5 for frame in event["visemes"])
    assert any(sum(frame) > 0 for frame in event["visemes"])


# --- a viewer that arrives mid-reply, and waiting for the first viewer -----------------------

TWO_SECONDS = np.zeros(32_000, dtype=np.float32)  # at 16 kHz
TRACK = [(0.5, 0.0, 0.0, 0.0, 0.0), (0.25, 0.0, 0.0, 0.0, 0.0)]
SPEAK = {
    "type": "speak",
    "emotion": "happy",
    "fps": 60,
    "duration": 2.0,
    "visemes": [[0.5, 0.0, 0.0, 0.0, 0.0], [0.25, 0.0, 0.0, 0.0, 0.0]],
}


def test_a_viewer_that_connects_mid_speech_gets_the_track_with_how_much_has_played() -> None:
    clock = FakeClock()
    bus = AvatarBus(viseme_track=FakeTrack(TRACK), clock=clock)
    bus.state("speaking")
    bus.speak(TWO_SECONDS, 16_000, "happy")

    clock.now += 0.7504

    assert drain(bus.subscribe()) == [
        HELLO,
        {"type": "state", "state": "speaking"},
        {**SPEAK, "elapsed": 0.75},
    ]


def test_a_viewer_that_connects_after_the_speech_ended_gets_no_track() -> None:
    clock = FakeClock()
    bus = AvatarBus(viseme_track=FakeTrack(TRACK), clock=clock)
    bus.speak(TWO_SECONDS, 16_000, "happy")

    clock.now += 2.0

    assert drain(bus.subscribe()) == [HELLO, IDLE]


def test_a_state_other_than_speaking_ends_the_replay_of_the_track() -> None:
    # Playback failed at once: she listens again although the clip would still be playing.
    clock = FakeClock()
    bus = AvatarBus(viseme_track=FakeTrack(TRACK), clock=clock)
    bus.state("speaking")
    bus.speak(TWO_SECONDS, 16_000, "happy")
    bus.state("listening", followup_s=8)

    clock.now += 0.5

    assert drain(bus.subscribe()) == [
        HELLO,
        {"type": "state", "state": "listening", "followup_s": 8},
    ]


def test_a_viewer_connected_during_the_speech_gets_it_once_without_elapsed() -> None:
    clock = FakeClock()
    bus = AvatarBus(viseme_track=FakeTrack(TRACK), clock=clock)
    viewer = bus.subscribe()
    drain(viewer)

    bus.speak(TWO_SECONDS, 16_000, "happy")
    clock.now += 0.5

    assert drain(viewer) == [SPEAK]


def test_wait_for_viewer_is_true_at_once_while_a_viewer_is_connected() -> None:
    bus = AvatarBus()
    bus.subscribe()

    started = time.monotonic()
    assert bus.wait_for_viewer(timeout=WAIT_S)
    assert time.monotonic() - started < 1.0


def test_wait_for_viewer_gives_up_after_the_timeout_without_one() -> None:
    bus = AvatarBus()

    started = time.monotonic()
    assert not bus.wait_for_viewer(timeout=0.05)
    assert time.monotonic() - started >= 0.04


def test_wait_for_viewer_wakes_when_the_first_viewer_subscribes_from_another_thread() -> None:
    bus = AvatarBus()
    timer = threading.Timer(0.05, bus.subscribe)
    timer.start()

    started = time.monotonic()
    connected = bus.wait_for_viewer(timeout=WAIT_S)

    timer.join()
    assert connected
    assert time.monotonic() - started < 1.0


def test_wait_for_viewer_counts_only_viewers_still_connected() -> None:
    bus = AvatarBus()
    bus.unsubscribe(bus.subscribe())

    assert not bus.wait_for_viewer(timeout=0)


def test_wait_for_viewer_returns_false_at_once_when_the_bus_closes() -> None:
    bus = AvatarBus()
    timer = threading.Timer(0.05, bus.close)
    timer.start()

    started = time.monotonic()
    connected = bus.wait_for_viewer(timeout=WAIT_S)

    timer.join()
    assert not connected
    assert time.monotonic() - started < 1.0
