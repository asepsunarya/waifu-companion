"""Avatar events fanned out to every viewer connected to the event stream.

Cyrene publishes from her voice loop; each SSE connection holds a `Subscription`
with a bounded queue of its own. Publishing never blocks: when a viewer falls
behind, its oldest events are dropped, so a stalled window cannot hold up a reply.
A new subscriber first gets `hello` and the last `state`, so a viewer that
(re)connects mid-conversation shows the right pose at once, then the last
`hearing` (once it has changed: whether she hears Master, off while the listen
pause of Ctrl+Alt+Q holds), then the reply she is still speaking, if any, with
how far into it she is (`elapsed`), so its mouth joins in on time instead of
staying shut.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from waifu.avatar.emotion import EMOTIONS
from waifu.config import AvatarConfig

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

EVENT_QUEUE_SIZE = 64
MODEL_URL = "/model.vrm"
LIVE2D_MODEL_PREFIX = "/model/"
LIVE2D_CORE_URL = "/live2d/live2dcubismcore.min.js"
STATES = ("idle", "listening", "thinking", "speaking")
SPEAK_FPS = 60
DEFAULT_HOLD_S = 4

Event = dict[str, Any]
VisemeTrack = Callable[["np.ndarray", int, int], Sequence[Sequence[float]]]


def _lipsync_track(audio: np.ndarray, sample_rate: int, fps: int) -> Sequence[Sequence[float]]:
    from waifu.avatar.lipsync import viseme_track  # numpy-heavy, needed only when she speaks

    return viseme_track(audio, sample_rate, fps)


def viewer_settings(config: AvatarConfig, name: str) -> Event:
    """What a viewer needs to show the avatar: the fields of `hello` and of /config.json.

    One function builds both, so the event stream and the page's config never disagree.
    The page's access token is added by the server to /config.json only.
    """
    model: str | None = MODEL_URL
    core: str | None = None
    if config.engine == "live2d":
        # The model3.json names its moc3, textures, and physics relative to its own folder.
        model = None if config.model is None else LIVE2D_MODEL_PREFIX + quote(config.model.name)
        core = LIVE2D_CORE_URL
    interactions = config.interactions
    return {
        "engine": config.engine,
        "model": model,
        "core": core,
        "name": name,
        "framing": config.framing,
        "credit": config.credit,
        "interactions": {
            "enabled": interactions.enabled,
            "cursor_follow": interactions.cursor_follow,
            "head_pat": interactions.head_pat,
            "body_touch": interactions.body_touch,
            "double_click_listen": interactions.double_click_listen,
            "idle": interactions.idle,
            "welcome_back_after_min": interactions.welcome_back_after_min,
        },
    }


class Subscription:
    """One viewer's queue of events; the oldest is dropped when it is full."""

    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=EVENT_QUEUE_SIZE)
        self._ready = threading.Condition()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def get(self, timeout: float | None = None) -> Event | None:
        """The next event; None when none arrives within `timeout` s or once closed."""
        with self._ready:
            self._ready.wait_for(lambda: self._events or self._closed, timeout)
            return self._events.popleft() if self._events else None

    def _put(self, event: Event) -> None:
        with self._ready:
            if len(self._events) == self._events.maxlen:
                logger.debug("Avatar viewer is behind; dropping its oldest event")
            self._events.append(event)
            self._ready.notify_all()

    def _close(self) -> None:
        with self._ready:
            self._closed = True
            self._events.clear()
            self._ready.notify_all()


class AvatarBus:
    """Thread-safe fan-out of avatar events (see the SPEC event protocol)."""

    def __init__(
        self,
        name: str = "Cyrene",
        framing: str = "full",
        *,
        viseme_track: VisemeTrack = _lipsync_track,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        # A VRM viewer until the server sets the configured avatar's settings.
        self._hello: Event = {
            "type": "hello",
            **viewer_settings(AvatarConfig(framing=framing), name),
        }
        self._viseme_track = viseme_track
        self._clock = clock
        self._lock = threading.Lock()
        self._viewer_joined = threading.Condition(self._lock)
        self._subscriptions: list[Subscription] = []
        self._last_state: Event = {"type": "state", "state": "idle"}
        self._last_hearing: Event | None = None  # none until it first changed
        # The last speak event and when it was published, while it may still be playing.
        self._last_speak: tuple[Event, float] | None = None
        self._closed = False

    @property
    def name(self) -> str:
        return self._name

    def set_viewer_settings(self, settings: Mapping[str, Any]) -> None:
        """Makes `settings` (from `viewer_settings`) the hello of every later subscriber."""
        with self._lock:
            self._hello = {"type": "hello", **settings}

    def subscribe(self) -> Subscription:
        """A new subscription that starts with hello, the last state and hearing, and the speech
        in progress.

        After `close()` the subscription comes back already closed.
        """
        subscription = Subscription()
        with self._lock:
            if self._closed:
                subscription._close()
                return subscription
            subscription._put(dict(self._hello))
            subscription._put(dict(self._last_state))
            if self._last_hearing is not None:
                subscription._put(dict(self._last_hearing))
            if (speech := self._speech_in_progress()) is not None:
                subscription._put(speech)
            self._subscriptions.append(subscription)
            self._viewer_joined.notify_all()
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)
        subscription._close()

    def wait_for_viewer(self, timeout: float) -> bool:
        """Whether a viewer is connected, waiting up to `timeout` s for one; False once closed."""
        with self._viewer_joined:
            self._viewer_joined.wait_for(lambda: self._subscriptions or self._closed, timeout)
            return bool(self._subscriptions)

    def publish(self, event: Event) -> None:
        """Queues a copy of `event` for every subscriber; never blocks on a slow one."""
        event = dict(event)
        with self._lock:
            if event.get("type") == "state":
                self._last_state = event
                if event.get("state") != "speaking":
                    self._last_speak = None
            elif event.get("type") == "speak":
                self._last_speak = (event, self._clock())
            elif event.get("type") == "hearing":
                self._last_hearing = event
            for subscription in self._subscriptions:
                subscription._put(event)

    def state(self, name: str, followup_s: float | None = None) -> None:
        """Publishes a state; `followup_s` (the follow-up window) only with listening."""
        if name not in STATES:
            raise ValueError(f"unknown avatar state {name!r}; expected one of {STATES}")
        event: Event = {"type": "state", "state": name}
        if followup_s is not None:
            if name != "listening":
                raise ValueError(f"followup_s is only valid with 'listening', not {name!r}")
            event["followup_s"] = followup_s
        self.publish(event)

    def hearing(self, on: bool) -> None:
        """Publishes whether she hears Master: not while the listen pause (Ctrl+Alt+Q) holds."""
        self.publish({"type": "hearing", "on": on})

    def emotion(self, name: str, hold_s: float = DEFAULT_HOLD_S) -> None:
        _check_emotion(name)
        self.publish({"type": "emotion", "emotion": name, "hold_s": hold_s})

    def speak(self, audio: np.ndarray, sample_rate: int, emotion: str) -> None:
        """Publishes the mouth track for `audio`; call right before playback starts."""
        _check_emotion(emotion)
        track = self._viseme_track(audio, sample_rate, SPEAK_FPS)
        self.publish(
            {
                "type": "speak",
                "emotion": emotion,
                "fps": SPEAK_FPS,
                "duration": round(len(audio) / sample_rate, 3),
                "visemes": [[round(float(weight), 2) for weight in frame] for frame in track],
            }
        )

    def close(self) -> None:
        """Closes every subscription, waking their readers; later subscriptions start closed."""
        with self._lock:
            self._closed = True
            subscriptions, self._subscriptions = self._subscriptions, []
            self._viewer_joined.notify_all()
        for subscription in subscriptions:
            subscription._close()

    def _speech_in_progress(self) -> Event | None:
        """The last speak event with `elapsed` (s since it began) while it plays; call locked."""
        if self._last_speak is None:
            return None
        event, published_at = self._last_speak
        elapsed = self._clock() - published_at
        if elapsed >= event.get("duration", 0):
            self._last_speak = None
            return None
        return {**event, "elapsed": round(elapsed, 3)}


def _check_emotion(name: str) -> None:
    if name not in EMOTIONS:
        raise ValueError(f"unknown emotion {name!r}; expected one of {EMOTIONS}")
