"""What the internet watcher measures: the route, echoes, a DNS lookup, and TCP connects.

Only the standard library and ctypes, with Microsoft's documented signatures (icmpapi.h,
iphlpapi.h, windns.h), so there is no new dependency and no administrator right is needed:
- `default_route`: GetBestRoute toward an address on the internet, whose next hop is the gateway;
- `icmp_echo`: one ICMP echo through IcmpCreateFile, IcmpSendEcho, and IcmpCloseHandle;
- `dns_query_ms`: DnsQuery_W for an A record past the Windows DNS cache, which would hide a DNS
  server that stopped answering, freed with DnsRecordListFree;
- `tcp_connect_ms`: a TCP connect over IPv4, timed without its lookup; no TLS, nothing sent.

Every Windows function, `socket.getaddrinfo`, and `socket.socket` is looked up when it is called,
never at import or as a default argument, so the test suite's guards always apply; `NetworkProbe`
takes each probe injected, and tests pass fakes. Addresses go to Windows in network byte order.

`NetworkProbe.check` runs the probes of one check at once, each on a daemon thread of its own
within one budget: what has not answered by then counted as failed. A probe of a target still
running from an earlier check (a lookup the resolver holds for its own timeouts) counts as failed
without starting another, so threads never pile up. Never a thread pool: Python joins a pool's
workers at exit, so a stuck lookup would hold up the exit of `waifu run`. Addresses reach only the
DEBUG log. Nothing here raises out of a check.
"""

from __future__ import annotations

import ctypes
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any

from waifu.config import NetworkHostsConfig
from waifu.play_time import wib_now

logger = logging.getLogger(__name__)

ECHO_TIMEOUT_MS = 1000
CONNECT_TIMEOUT_S = 1.5
CHECK_BUDGET_S = 3.0
HTTPS_PORT = 443
# How long past its own timeout an echo's thread may take to hand its answer over.
_ECHO_SLACK_S = 0.25
_ECHO_DATA = b"cyrene-network-check-0123456789."  # 32 bytes, like Windows' own ping
# Room for one reply and its data, an ICMP error's 8 bytes, and the IO_STATUS_BLOCK.
_REPLY_BUFFER_BYTES = 256
IP_SUCCESS = 0
MIB_IPROUTE_TYPE_DIRECT = 3  # a destination on the link itself: no gateway
DNS_TYPE_A = 1
DNS_QUERY_BYPASS_CACHE = 0x8
DNS_FREE_RECORD_LIST = 1  # DnsFreeRecordList
DNS_INFO_NO_RECORDS = 9501  # the server answered: the name has no A record
# IcmpCreateFile's failure: the handle -1, as a HANDLE restype reads it.
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    """One reply as a 64-bit process gets it; Address, Status, and RoundTripTime, the only
    fields read, sit at the same offsets in the 32-bit layout too."""

    _fields_ = [
        ("Address", wintypes.ULONG),  # IPAddr, network byte order
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),  # milliseconds
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


class MIB_IPFORWARDROW(ctypes.Structure):
    _fields_ = [
        (name, wintypes.DWORD)
        for name in (
            "dwForwardDest",
            "dwForwardMask",
            "dwForwardPolicy",
            "dwForwardNextHop",
            "dwForwardIfIndex",
            "dwForwardType",
            "dwForwardProto",
            "dwForwardAge",
            "dwForwardNextHopAS",
            "dwForwardMetric1",
            "dwForwardMetric2",
            "dwForwardMetric3",
            "dwForwardMetric4",
            "dwForwardMetric5",
        )
    ]


_Signature = tuple[list[Any], Any]
_FUNCTIONS: Mapping[str, tuple[str, _Signature]] = {
    "IcmpCreateFile": ("iphlpapi", ([], wintypes.HANDLE)),
    "IcmpSendEcho": (
        "iphlpapi",
        (
            [
                wintypes.HANDLE,
                wintypes.ULONG,  # IPAddr
                ctypes.c_void_p,
                wintypes.WORD,
                ctypes.POINTER(IP_OPTION_INFORMATION),
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
            ],
            wintypes.DWORD,
        ),
    ),
    "IcmpCloseHandle": ("iphlpapi", ([wintypes.HANDLE], wintypes.BOOL)),
    "GetBestRoute": (
        "iphlpapi",
        ([wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(MIB_IPFORWARDROW)], wintypes.DWORD),
    ),
    "DnsQuery_W": (
        "dnsapi",
        (
            [
                wintypes.LPCWSTR,
                wintypes.WORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),  # PDNS_RECORD*
                ctypes.c_void_p,
            ],
            wintypes.LONG,  # DNS_STATUS
        ),
    ),
    "DnsRecordListFree": ("dnsapi", ([ctypes.c_void_p, ctypes.c_int], None)),
}


def _windows(name: str) -> Callable[..., Any]:
    """The Windows function `name` with its full signature, loaded only when it is called."""
    library, (argtypes, restype) = _FUNCTIONS[name]

    def call(*args: Any) -> Any:
        function = getattr(ctypes.WinDLL(library, use_last_error=True), name)
        function.argtypes = argtypes
        function.restype = restype
        return function(*args)

    return call


_icmp_create_file = _windows("IcmpCreateFile")
_icmp_send_echo = _windows("IcmpSendEcho")
_icmp_close_handle = _windows("IcmpCloseHandle")
_get_best_route = _windows("GetBestRoute")
_dns_query_w = _windows("DnsQuery_W")
_dns_record_list_free = _windows("DnsRecordListFree")


def _address_value(address: str) -> int:
    """A dotted IPv4 address as Windows' IPAddr: network byte order in a little-endian ULONG."""
    return int.from_bytes(socket.inet_aton(address), "little")


def _dotted(value: int) -> str:
    return socket.inet_ntoa(value.to_bytes(4, "little"))


@dataclass(frozen=True)
class Route:
    """Windows' route toward the internet: its gateway, None for a direct route."""

    gateway: str | None


def default_route(toward: str, *, best_route: Callable[..., int] = _get_best_route) -> Route | None:
    """The route Windows would take to `toward` from any source; None without one (no Wi-Fi or
    cable link)."""
    destination = _address_value(toward)
    row = MIB_IPFORWARDROW()
    status = best_route(destination, 0, ctypes.byref(row))
    if status:
        logger.debug("No route toward the internet (Windows error %d)", status)
        return None
    hop = row.dwForwardNextHop
    if row.dwForwardType == MIB_IPROUTE_TYPE_DIRECT or hop in (0, destination):
        return Route(None)
    return Route(_dotted(hop))


def icmp_echo(
    address: str,
    timeout_ms: int = ECHO_TIMEOUT_MS,
    *,
    create: Callable[[], Any] = _icmp_create_file,
    send: Callable[..., int] = _icmp_send_echo,
    close: Callable[[Any], Any] = _icmp_close_handle,
) -> float | None:
    """The round trip in milliseconds of one echo to `address`, or None without a reply from it
    (a timeout, unreachable, or ICMP unusable here)."""
    destination = _address_value(address)
    handle = create()
    if handle in (None, INVALID_HANDLE_VALUE):
        logger.debug("ICMP cannot be used here (Windows error %d)", ctypes.get_last_error())
        return None
    try:
        request = ctypes.create_string_buffer(_ECHO_DATA, len(_ECHO_DATA))
        reply = ctypes.create_string_buffer(_REPLY_BUFFER_BYTES)
        replies = send(
            handle,
            destination,
            request,
            len(_ECHO_DATA),
            None,
            reply,
            ctypes.sizeof(reply),
            timeout_ms,
        )
        if not replies:
            logger.debug(
                "No echo reply from %s (Windows error %d)", address, ctypes.get_last_error()
            )
            return None
        echo = ICMP_ECHO_REPLY.from_buffer(reply)
        if echo.Status != IP_SUCCESS or echo.Address != destination:
            logger.debug("Echo to %s answered with status %d", address, echo.Status)
            return None
        return float(echo.RoundTripTime)
    finally:
        close(handle)


def dns_query_ms(
    name: str,
    *,
    query: Callable[..., int] = _dns_query_w,
    free: Callable[..., None] = _dns_record_list_free,
    clock: Callable[[], float] = time.perf_counter,
) -> float | None:
    """How long the DNS server took to answer for `name`, asked past the Windows DNS cache, in
    milliseconds; None when it did not answer."""
    answer = ctypes.c_void_p()
    started = clock()
    status = query(name, DNS_TYPE_A, DNS_QUERY_BYPASS_CACHE, None, ctypes.byref(answer), None)
    took = (clock() - started) * 1000
    try:
        if status not in (0, DNS_INFO_NO_RECORDS):
            logger.debug("DNS lookup failed (DNS status %d)", status)
            return None
        return took
    finally:
        if answer.value:
            free(answer, DNS_FREE_RECORD_LIST)


def tcp_connect_ms(
    host: str,
    port: int,
    timeout_s: float = CONNECT_TIMEOUT_S,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> float | None:
    """How long a TCP connect to `host` on `port` over IPv4 took in milliseconds, without its
    lookup; None when it failed. IPv4 like the echoes: Windows sorts IPv6 addresses first, and a
    broken IPv6 path must not make a host that answers look down. Nothing is sent: the connection
    is closed at once."""
    try:
        family, kind, protocol, _, address = socket.getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )[0]
        with socket.socket(family, kind, protocol) as connection:
            connection.settimeout(timeout_s)
            started = clock()
            connection.connect(address)
            return (clock() - started) * 1000
    except OSError as exc:
        logger.debug("No TCP connect to %s:%d: %s", host, port, exc)
        return None


@dataclass(frozen=True)
class Reading:
    """What one check found; a time is in milliseconds, None for no answer."""

    at: datetime  # aware WIB, when the check started
    route: bool  # Windows had a route toward the internet
    gateway: str | None  # its address: only for `waifu internet` and the DEBUG log
    gateway_ms: float | None
    anchor_ms: float | None  # the quickest anchor's echo, else its quickest TCP connect
    dns_checked: bool = False
    dns_ms: float | None = None
    hoyo_ms: tuple[float | None, ...] | None = None  # per HoYo host; None: not checked


class _Running:
    """One probe on its daemon thread."""

    def __init__(self, thread: threading.Thread, outcome: list[float | None]) -> None:
        self._thread = thread
        self._outcome = outcome

    def result(self, deadline: float, clock: Callable[[], float]) -> float | None:
        """Its answer once it finished, waiting until `deadline` at most; None after that."""
        self._thread.join(max(0.0, deadline - clock()))
        return None if self._thread.is_alive() else self._outcome[0]


class NetworkProbe:
    def __init__(
        self,
        hosts: NetworkHostsConfig,
        *,
        route: Callable[[str], Route | None] = default_route,
        echo: Callable[[str, int], float | None] = icmp_echo,
        connect: Callable[[str, int, float], float | None] = tcp_connect_ms,
        resolve: Callable[[str], float | None] = dns_query_ms,
        now: Callable[[], datetime] = wib_now,
        clock: Callable[[], float] = time.monotonic,
        budget_s: float = CHECK_BUDGET_S,
    ) -> None:
        """`now` is aware WIB time, for the reading; `clock` monotonic seconds, for the budget."""
        self._hosts = hosts
        self._route = route
        self._echo = echo
        self._connect = connect
        self._resolve = resolve
        self._now = now
        self._clock = clock
        self._budget_s = budget_s
        self._lock = threading.Lock()
        self._running: set[str] = set()  # the targets whose probe has not finished

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading:
        """The route first, then at once the gateway's echo and each anchor's, with `dns` the
        lookup of `hosts.dns`, with `hoyo` a connect on 443 to each HoYo host; when no anchor
        echoes in time, a connect on 443 to each anchor. Everything within the budget."""
        at = self._now()
        deadline = self._clock() + self._budget_s
        hosts = self._hosts
        route = self._route_toward(hosts.anchors[0])
        gateway = self._gateway(route)
        gateway_echo = None if gateway is None else self._start_echo(gateway)
        echoes = [self._start_echo(anchor) for anchor in hosts.anchors]
        lookup = (
            self._start(f"resolve {hosts.dns}", partial(self._resolve, hosts.dns)) if dns else None
        )
        hoyo_connects = [self._start_connect(host) for host in hosts.hoyo] if hoyo else None
        echoed_by = min(deadline, self._clock() + ECHO_TIMEOUT_MS / 1000 + _ECHO_SLACK_S)
        anchor_ms = _quickest(self._result(echo, echoed_by) for echo in echoes)
        if anchor_ms is None:  # a firewall dropping ICMP must not look like an outage
            connects = [self._start_connect(anchor) for anchor in hosts.anchors]
            anchor_ms = _quickest(self._result(connect, deadline) for connect in connects)
        return Reading(
            at=at,
            route=route is not None,
            gateway=gateway,
            gateway_ms=self._result(gateway_echo, deadline),
            anchor_ms=anchor_ms,
            dns_checked=dns,
            dns_ms=self._result(lookup, deadline),
            hoyo_ms=None
            if hoyo_connects is None
            else tuple(self._result(connect, deadline) for connect in hoyo_connects),
        )

    def _route_toward(self, anchor: str) -> Route | None:
        try:
            return self._route(anchor)
        except Exception:
            logger.debug("Could not read the route toward the internet", exc_info=True)
            return None

    def _gateway(self, route: Route | None) -> str | None:
        """The configured gateway, or with `auto` the route's."""
        if self._hosts.gateway != "auto":
            return self._hosts.gateway
        return None if route is None else route.gateway

    def _start_echo(self, address: str) -> _Running | None:
        return self._start(f"echo {address}", partial(self._echo, address, ECHO_TIMEOUT_MS))

    def _start_connect(self, host: str) -> _Running | None:
        return self._start(
            f"connect {host}:{HTTPS_PORT}",
            partial(self._connect, host, HTTPS_PORT, CONNECT_TIMEOUT_S),
        )

    def _start(self, target: str, probe: Callable[[], float | None]) -> _Running | None:
        """`probe` on a daemon thread of its own; None, counted as failed, while the probe of the
        same `target` from an earlier check is still running."""
        with self._lock:
            if target in self._running:
                logger.debug("The probe %s is still running; counted as failed", target)
                return None
            self._running.add(target)
        outcome: list[float | None] = [None]

        def run() -> None:
            try:
                outcome[0] = probe()
            except Exception:
                logger.debug("The probe %s failed", target, exc_info=True)
            finally:
                with self._lock:
                    self._running.discard(target)

        thread = threading.Thread(target=run, name="network-probe", daemon=True)
        thread.start()
        return _Running(thread, outcome)

    def _result(self, running: _Running | None, deadline: float) -> float | None:
        return None if running is None else running.result(deadline, self._clock)


def _quickest(times: Iterable[float | None]) -> float | None:
    return min((took for took in times if took is not None), default=None)
