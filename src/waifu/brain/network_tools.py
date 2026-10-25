"""The tool that lets Cyrene check Master's internet: `network_status`.

"Internetnya lemot?", "ping-ku berapa?", "server HoYo down?", "tadi internet putus berapa
kali?": the tool checks right then (about 3 s: the router, the internet, a DNS lookup, and the
HoYoLAB and HoYoverse web servers), classifies the check with what the run's internet watcher
knows, and adds the outage it confirmed and today's outages from the history. Its answer holds no
address: only statuses, rounded milliseconds, and counts. With the internet down Claude is out of
reach anyway, so this is for the online case; offline, the voice loop answers by itself
(`waifu.network_ask`). It takes no argument, so it takes no strict slot, and its spec is static,
so it stays byte-identical for the brain's lifetime and keeps the prompt cache warm. Nothing in it
is private: guest mode leaves it open. `run` never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from waifu.brain.base import ToolSpec
from waifu.network import MEANINGS, Finding, NetworkCheck
from waifu.network_history import NetworkRecord, outage_totals
from waifu.play_time import whole_minutes, wib_now

logger = logging.getLogger(__name__)

NETWORK_STATUS = "network_status"
_FAILED = "Internet tidak bisa dicek saat ini."
_DESCRIPTION = (
    "Check Master's internet right now, in about 3 seconds, and read today's outages: for "
    '"internetnya lemot?", "ping-ku berapa?", "server HoYo down?", "tadi internet putus berapa '
    'kali?" and the like. It pings the router and the internet, looks a name up, and times '
    "HoYoLAB's and HoYoverse's web servers. `status`: ok, slow, dns (names cannot be looked up, "
    "so sites do not open), hoyo (the HoYoLAB and HoYoverse servers are silent or slow), offline "
    "(the internet beyond the router is cut off), or router (the PC cannot reach the router: the "
    "Wi-Fi, the cable, or the router itself); `meaning` says it in Indonesian. `internet_ms` is "
    'the round trip to the internet right now, which slowness is judged by: say it for "ping-ku '
    'berapa?". It measures delay, not download speed, so when it is ok and Master finds the '
    "internet slow, say the ping is normal and the speed or the app may be slow, never that the "
    "internet is fine. `router` is answered, no_reply (many routers ignore pings, so that alone is "
    "no problem), or none. The HoYo servers are HoYoLAB's and HoYoverse's "
    "web servers, not the game server, so never call their time the game's ping. `outage_now` is "
    "an outage your internet watcher confirmed: since when and how many minutes; "
    "`outages_today` is there only when the internet was down today while `waifu run` was on. "
    "Never say an IP address. Answer in one or two short sentences without parentheses, which "
    "your voice drops. After an error result, say its message briefly and do not call again in "
    "this turn."
)


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class OutageSource(Protocol):
    """The part of `NetworkStore` the tool reads."""

    def record(self) -> NetworkRecord: ...


class NetworkTools:
    """A `ToolProvider` for the brain: Master's internet, checked when asked."""

    def __init__(
        self,
        check: NetworkCheck,
        history: OutageSource,
        *,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`now` is aware WIB time, for today's outages and the open outage's length."""
        self._check = check
        self._history = history
        self._now = now
        self._spec = ToolSpec(
            name=NETWORK_STATUS,
            description=_DESCRIPTION,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != NETWORK_STATUS:
            return _error(f"Tool '{name}' bukan tool internet.")
        try:
            return self._network_status(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Network tool '%s' failed", name)
            return _error(_FAILED)

    def _network_status(self, arguments: object) -> tuple[str, bool]:
        if not isinstance(arguments, Mapping) or arguments:
            raise _InvalidArguments("tool ini tidak menerima argumen; kirim objek kosong {}.")
        finding = self._check.now(hoyo=True)
        data = _finding_json(finding, self._now())
        today = self._outages_today()
        if today is not None:
            data["outages_today"] = today
        return _to_json(data), False

    def _outages_today(self) -> dict[str, int] | None:
        """Today's outages up to now, None when there were none or the history cannot be read
        (logged)."""
        now = self._now()
        try:
            record = self._history.record()
        except OSError as exc:
            logger.warning("Could not read today's internet outages: %s", exc)
            return None
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        total = outage_totals(record, midnight, now)
        if not total.count:
            return None
        return {"count": total.count, "minutes": whole_minutes(total.seconds)}


def _finding_json(finding: Finding, now: datetime) -> dict[str, Any]:
    reading = finding.reading
    if reading.gateway_ms is not None:
        router = "answered"
    else:
        router = "none" if reading.gateway is None else "no_reply"
    data: dict[str, Any] = {
        "ok": True,
        "status": finding.status,
        "meaning": MEANINGS[finding.status],
        "internet_ms": _whole(finding.judged_ms),
        "router": router,
        "dns": "failed" if reading.dns_ms is None else "ok",
    }
    hoyo = finding.hoyo
    if hoyo is not None:
        data["hoyo_servers"] = {
            "answered": hoyo.answered,
            "of": hoyo.of,
            "median_ms": _whole(hoyo.median_ms),
        }
    outage = finding.outage
    if outage is not None:
        minutes = whole_minutes(int((now - outage.start).total_seconds()))
        data["outage_now"] = {"since": f"{outage.start:%H:%M}", "minutes": minutes}
    return data


def _whole(took: float | None) -> int | None:
    """Whole milliseconds, a half up."""
    return None if took is None else int(took + 0.5)


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
