"""What a check of the internet means, what the run knows about it, and how she says it.

`classify` turns a `Reading` (`network_probe.py`) into one status, the first that holds:
`router` (no anchor answered, and Windows had no route or a gateway that answered earlier in this
run is silent now: many routers ignore pings, so one that never answered is never blamed),
`offline` (no anchor answered), `dns` (the lookup was made and failed), `slow` (the median of the
recent round trips given with this one is above `slow_ms`), `hoyo` (a HoYo host checked is
silent, or those that answered are slow by their median), else `ok`. `router` and `offline` are
`DOWN`.

`NetworkState` is one per `waifu run`, shared by the watcher, her answers, the tool, the brain,
and the speech router: the gateways that answered, the last round trips, whether the last check
failed, whether a HoYo game runs (from the play time watcher's polls), and the outage the watcher
confirmed. `NetworkCheck` makes a check, classifies it with what the state knows, and notes it:
the watcher's poll is slow by the median with the last round trips, a check when asked by its own.

Her words name no address: `answer_line` for Master's question, `back_line` once the internet is
back, and `outage_note` for Claude's connection apology. They never use parentheses, which her
voice drops.
"""

from __future__ import annotations

import statistics
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from waifu.config import at_sentence_start
from waifu.network_probe import Reading

Status = Literal["ok", "slow", "dns", "hoyo", "offline", "router"]
Topic = Literal["internet", "hoyo"]
DOWN: frozenset[Status] = frozenset({"router", "offline"})
MEANINGS: dict[Status, str] = {
    "ok": "lancar",
    "slow": "lambat",
    "dns": "DNS bermasalah",
    "hoyo": "server HoYo bermasalah",
    "offline": "internet putus",
    "router": "router tidak terjangkau",
}
_HOYO = "Server HoYoLAB dan HoYoverse"
# Only the round trip is measured, never the speed: a normal ping is no answer to "kok lemot?".
_SPEED_UNMEASURED = (
    "Kalau masih terasa lemot, mungkin kecepatannya yang turun, atau dari aplikasinya."
)


class Probe(Protocol):
    """The part of `NetworkProbe` a check uses."""

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading: ...


def judged_ms(reading: Reading, recent_ms: Sequence[float]) -> float | None:
    """The median of the recent round trips with this one's; None when no anchor answered."""
    if reading.anchor_ms is None:
        return None
    return statistics.median([*recent_ms, reading.anchor_ms])


@dataclass(frozen=True)
class HoyoFinding:
    """How the HoYo hosts answered one check."""

    answered: int
    of: int
    median_ms: float | None  # of those that answered
    slow: bool  # that median is above `slow_ms`

    @property
    def trouble(self) -> bool:
        return self.answered < self.of or self.slow


def hoyo_finding(hoyo_ms: Sequence[float | None], slow_ms: int) -> HoyoFinding:
    answered = [took for took in hoyo_ms if took is not None]
    median = statistics.median(answered) if answered else None
    return HoyoFinding(len(answered), len(hoyo_ms), median, median is not None and median > slow_ms)


def classify(
    reading: Reading, *, recent_ms: Sequence[float], slow_ms: int, gateway_seen: bool
) -> Status:
    """The first status that holds (see the module); `gateway_seen`: the reading's gateway
    answered earlier in this run."""
    median = judged_ms(reading, recent_ms)
    if median is None:
        if not reading.route or (gateway_seen and reading.gateway_ms is None):
            return "router"
        return "offline"
    if reading.dns_checked and reading.dns_ms is None:
        return "dns"
    if median > slow_ms:
        return "slow"
    if reading.hoyo_ms is not None and hoyo_finding(reading.hoyo_ms, slow_ms).trouble:
        return "hoyo"
    return "ok"


@dataclass(frozen=True)
class CurrentOutage:
    """An outage the watcher confirmed and has not seen end."""

    start: datetime  # aware WIB, its first failed poll
    kind: Status  # router or offline


@dataclass(frozen=True)
class Finding:
    """A check as she answers from it."""

    reading: Reading
    status: Status
    # The round trip she judged slowness by (a poll's median with the recent ones, an asked
    # check's own); None without the internet.
    judged_ms: float | None
    outage: CurrentOutage | None  # the watcher's, when one is open
    hoyo: HoyoFinding | None = None  # None: the HoYo hosts were not checked


class NetworkState:
    def __init__(self, slow_window: int) -> None:
        """`slow_window` counts this check: the round trips of the last `slow_window - 1`
        checks are kept."""
        self._lock = threading.Lock()
        self._recent: deque[float] = deque(maxlen=slow_window - 1)
        self._gateways: set[str] = set()
        self._down = False
        self._followers: list[Callable[[bool], None]] = []
        self._hoyo_playing = False
        self._outage: CurrentOutage | None = None

    def observe(self, playing: bool, resumed: bool) -> None:
        """A play time poll's word: every tracked game is a HoYo game."""
        self._hoyo_playing = playing

    def hoyo_playing(self) -> bool:
        return self._hoyo_playing

    def follow(self, follower: Callable[[bool], None]) -> None:
        """Have `follower` hear whether the internet is down at each check that changes it (the
        speech router, whose online voices wait while it is down)."""
        with self._lock:
            self._followers.append(follower)

    def note(self, reading: Reading, status: Status) -> None:
        """Keep what a check found: a gateway that answered, its round trip, and whether it
        failed, told to the followers when that changed."""
        down = status in DOWN
        with self._lock:
            if reading.gateway is not None and reading.gateway_ms is not None:
                self._gateways.add(reading.gateway)
            if reading.anchor_ms is not None:
                self._recent.append(reading.anchor_ms)
            changed = down != self._down
            self._down = down
            followers = list(self._followers) if changed else []
        for follower in followers:
            follower(down)

    def gateway_answered(self, address: str) -> bool:
        with self._lock:
            return address in self._gateways

    def recent_ms(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._recent)

    def down(self) -> bool:
        """Whether the last check found the internet out of reach."""
        return self._down

    def open_outage(self, start: datetime, kind: Status) -> None:
        self._outage = CurrentOutage(start, kind)

    def close_outage(self) -> None:
        self._outage = None

    def outage(self) -> CurrentOutage | None:
        return self._outage

    def outage_note(self) -> str | None:
        """What Claude's connection apology adds while an outage is open."""
        outage = self._outage
        if outage is None:
            return None
        since = f"Internetnya lagi putus sejak jam {outage.start:%H.%M}"
        if outage.kind == "router":
            return f"{since}: PC tidak tersambung ke router."
        return f"{since}."


class NetworkCheck:
    """A check classified with what the run knows, and noted in it."""

    def __init__(self, probe: Probe, state: NetworkState, *, slow_ms: int) -> None:
        self._probe = probe
        self._state = state
        self._slow_ms = slow_ms

    def now(self, *, hoyo: bool) -> Finding:
        """A check when asked, with the lookup, slow by its own round trip: "kok lemot?" and
        "ping-ku berapa?" ask about now, which the last minutes' polls must not outvote."""
        return self._made(self._probe.check(dns=True, hoyo=hoyo), recent=())

    def poll(self, *, hoyo: bool) -> Finding:
        """The watcher's check, without the lookup, slow by the median of the recent round trips
        with its own, so one slow poll is no slow internet."""
        return self._made(self._probe.check(dns=False, hoyo=hoyo), self._state.recent_ms())

    def _made(self, reading: Reading, recent: Sequence[float]) -> Finding:
        state = self._state
        seen = reading.gateway is not None and state.gateway_answered(reading.gateway)
        status = classify(reading, recent_ms=recent, slow_ms=self._slow_ms, gateway_seen=seen)
        state.note(reading, status)
        hoyo = None if reading.hoyo_ms is None else hoyo_finding(reading.hoyo_ms, self._slow_ms)
        return Finding(reading, status, judged_ms(reading, recent), state.outage(), hoyo)


def answer_line(finding: Finding, topic: Topic, owner_call: str) -> str:
    """Her answer to Master's question about the internet or the HoYo servers."""
    owner = at_sentence_start(owner_call)
    if finding.status in DOWN:
        return _down_line(finding, owner)
    if finding.status == "dns":
        return (
            f"{owner}, internetnya tersambung, tapi DNS-nya bermasalah, jadi alamat situs tidak "
            "bisa dicari."
        )
    took = _ms(finding.judged_ms or 0.0)
    if finding.status == "slow":
        said = [f"{owner}, internetnya lagi lambat, ping-nya sekitar {took} milidetik."]
    else:
        said = [f"{owner}, ping internetnya normal, sekitar {took} milidetik."]
        if finding.status == "ok" and topic == "internet":
            said.append(_SPEED_UNMEASURED)
    if finding.hoyo is not None:
        said.append(_hoyo_sentence(finding.hoyo))
    if topic == "hoyo":
        said.append("Itu server HoYoLAB dan HoYoverse, bukan server game-nya.")
    return " ".join(said)


def back_line(owner_call: str, start: datetime, minutes: int) -> str:
    """Her line once the internet is back after an outage of `minutes` since `start`."""
    return (
        f"{at_sentence_start(owner_call)}, internetnya sudah nyambung lagi, tadi putus sekitar "
        f"{minutes} menit, sejak jam {start:%H.%M}."
    )


def _down_line(finding: Finding, owner: str) -> str:
    if finding.status == "router":
        said = f"{owner}, router tidak terjangkau: coba cek Wi-Fi, kabel, atau routernya."
    elif finding.reading.gateway_ms is not None:
        said = (
            f"{owner}, internetnya putus: routernya tersambung, tapi internet di luar tidak bisa "
            "dihubungi."
        )
    else:
        said = f"{owner}, internetnya putus: internet di luar tidak bisa dihubungi."
    if finding.outage is not None:
        said += f" Putusnya sejak jam {finding.outage.start:%H.%M}."
    return said


def _hoyo_sentence(hoyo: HoyoFinding) -> str:
    if hoyo.median_ms is None:
        return f"{_HOYO} tidak menjawab."
    silent = hoyo.of - hoyo.answered
    if silent:
        return f"{_HOYO} lagi bermasalah: {silent} dari {hoyo.of} tidak menjawab."
    pace = "lagi lambat" if hoyo.slow else "menjawab"
    return f"{_HOYO} {pace}, sekitar {_ms(hoyo.median_ms)} milidetik."


def _ms(took: float) -> int:
    """Whole milliseconds, a half up."""
    return int(took + 0.5)
