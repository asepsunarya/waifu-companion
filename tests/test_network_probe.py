import ctypes
import socket
import threading
import time
from ctypes import wintypes
from datetime import datetime
from types import SimpleNamespace

import pytest

from waifu.config import NetworkHostsConfig
from waifu.finance.values import WIB
from waifu.network_probe import (
    DNS_QUERY_BYPASS_CACHE,
    ECHO_TIMEOUT_MS,
    ICMP_ECHO_REPLY,
    INVALID_HANDLE_VALUE,
    IP_OPTION_INFORMATION,
    MIB_IPFORWARDROW,
    NetworkProbe,
    Reading,
    Route,
    default_route,
    dns_query_ms,
    icmp_echo,
    tcp_connect_ms,
)

pytestmark = pytest.mark.usefixtures("nothing_beyond_this_pc")

NOW = datetime(2026, 10, 25, 21, 3, tzinfo=WIB)
HANDLE = 0x5C
IP_REQ_TIMED_OUT = 11010
IP_DEST_HOST_UNREACHABLE = 11003
DNS_ERROR_RCODE_SERVER_FAILURE = 9002
DNS_INFO_NO_RECORDS = 9501
WAIT_S = 2.0  # an upper bound for a probe thread, never a sleep


def address_value(address: str) -> int:
    """An IPv4 address as Windows' IPAddr: network byte order in a little-endian ULONG."""
    return int.from_bytes(socket.inet_aton(address), "little")


# --- the echo ----------------------------------------------------------------------------------


class FakeIcmp:
    """IcmpCreateFile, IcmpSendEcho, and IcmpCloseHandle: a reply written into the caller's buffer
    as Windows writes it, or none."""

    def __init__(
        self,
        *,
        handle: int | None = HANDLE,
        replies: int = 1,
        status: int = 0,
        source: str | None = None,
        round_trip: int = 23,
        error: int = IP_REQ_TIMED_OUT,
    ) -> None:
        self.handle, self.replies, self.status = handle, replies, status
        self.source, self.round_trip, self.error = source, round_trip, error
        self.sent: list[dict] = []
        self.closed: list[int] = []

    def create(self) -> int | None:
        return self.handle

    def send(self, handle, destination, data, size, options, reply, reply_size, timeout_ms):
        self.sent.append(
            {
                "handle": handle,
                "destination": destination,
                "data": bytes(data)[:size],
                "options": options,
                "reply_size": reply_size,
                "timeout_ms": timeout_ms,
            }
        )
        assert reply_size == ctypes.sizeof(reply)
        if not self.replies:
            ctypes.set_last_error(self.error)
            return 0
        echo = ICMP_ECHO_REPLY.from_buffer(reply)
        echo.Address = destination if self.source is None else address_value(self.source)
        echo.Status = self.status
        echo.RoundTripTime = self.round_trip
        echo.DataSize = size
        return self.replies

    def close(self, handle) -> bool:
        self.closed.append(handle)
        return True

    def echo(self, address: str, timeout_ms: int = ECHO_TIMEOUT_MS) -> float | None:
        return icmp_echo(address, timeout_ms, create=self.create, send=self.send, close=self.close)


def test_an_echo_reply_from_the_address_asked_gives_its_round_trip():
    icmp = FakeIcmp(round_trip=23)

    assert icmp.echo("1.1.1.1", 800) == 23.0

    [sent] = icmp.sent
    assert sent["handle"] == HANDLE
    assert sent["destination"] == address_value("1.1.1.1")  # network byte order
    assert len(sent["data"]) == 32
    assert sent["options"] is None
    assert sent["reply_size"] >= ctypes.sizeof(ICMP_ECHO_REPLY) + 32 + 8 + 16
    assert sent["timeout_ms"] == 800
    assert icmp.closed == [HANDLE]


def test_a_lan_gateway_answering_within_a_millisecond_reads_zero():
    assert FakeIcmp(round_trip=0).echo("192.168.1.1") == 0.0


def test_a_reply_with_another_status_is_no_answer():
    # A router may answer for the address asked: "destination host unreachable".
    icmp = FakeIcmp(status=IP_DEST_HOST_UNREACHABLE)

    assert icmp.echo("1.1.1.1") is None
    assert icmp.closed == [HANDLE]


def test_a_reply_from_another_address_is_no_answer():
    assert FakeIcmp(source="192.168.1.1").echo("1.1.1.1") is None


def test_no_reply_is_no_answer_and_the_handle_is_closed():
    icmp = FakeIcmp(replies=0)

    assert icmp.echo("8.8.8.8") is None
    assert icmp.closed == [HANDLE]


@pytest.mark.parametrize("handle", [INVALID_HANDLE_VALUE, None])
def test_icmp_that_cannot_be_opened_is_no_answer_and_nothing_is_sent(handle):
    icmp = FakeIcmp(handle=handle)

    assert icmp.echo("1.1.1.1") is None
    assert (icmp.sent, icmp.closed) == ([], [])


def test_the_handle_is_closed_when_sending_raises():
    icmp = FakeIcmp()

    def broken_send(*args):
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        icmp_echo("1.1.1.1", create=icmp.create, send=broken_send, close=icmp.close)

    assert icmp.closed == [HANDLE]


# --- the route ---------------------------------------------------------------------------------


class FakeRoutes:
    """GetBestRoute: a row written into the caller's MIB_IPFORWARDROW, or an error."""

    def __init__(self, *, status: int = 0, next_hop: str = "192.168.1.1", kind: int = 4) -> None:
        self.status, self.next_hop, self.kind = status, next_hop, kind
        self.asked: list[tuple[int, int]] = []

    def best_route(self, destination, source, row) -> int:
        self.asked.append((destination, source))
        if self.status:
            return self.status
        written = row._obj
        written.dwForwardDest = 0
        written.dwForwardNextHop = address_value(self.next_hop)
        written.dwForwardType = self.kind
        return 0


def test_the_route_toward_the_internet_names_its_next_hop_as_the_gateway():
    routes = FakeRoutes(next_hop="192.168.1.1")

    assert default_route("1.1.1.1", best_route=routes.best_route) == Route("192.168.1.1")
    assert routes.asked == [(address_value("1.1.1.1"), 0)]  # from any source


@pytest.mark.parametrize(("next_hop", "kind"), [("192.168.1.1", 3), ("0.0.0.0", 4), ("1.1.1.1", 4)])
def test_a_direct_route_has_no_gateway(next_hop, kind):
    routes = FakeRoutes(next_hop=next_hop, kind=kind)

    assert default_route("1.1.1.1", best_route=routes.best_route) == Route(None)


def test_no_route_toward_the_internet_is_none():
    ERROR_NETWORK_UNREACHABLE = 1231

    routes = FakeRoutes(status=ERROR_NETWORK_UNREACHABLE)

    assert default_route("1.1.1.1", best_route=routes.best_route) is None


# --- DNS ---------------------------------------------------------------------------------------


class FakeDns:
    """DnsQuery_W and DnsRecordListFree: an answer list handed back through the pointer, or an
    error status; the clock moves 31 ms during the query."""

    def __init__(self, *, status: int = 0, answer: int | None = 0xABC0) -> None:
        self.status, self.answer = status, answer
        self.now = 10.0
        self.queries: list[tuple] = []
        self.freed: list[tuple[int | None, int]] = []

    def clock(self) -> float:
        return self.now

    def query(self, name, kind, options, extra, results, reserved) -> int:
        self.queries.append((name, kind, options, extra, reserved))
        if self.answer is not None:
            results._obj.value = self.answer
        self.now += 0.031
        return self.status

    def free(self, records, free_type) -> None:
        self.freed.append((records.value, free_type))

    def lookup(self, name: str = "www.google.com") -> float | None:
        return dns_query_ms(name, query=self.query, free=self.free, clock=self.clock)


def test_a_name_looked_up_past_the_cache_gives_how_long_it_took_and_its_answer_is_freed():
    dns = FakeDns()

    assert dns.lookup("www.google.com") == pytest.approx(31.0)

    assert dns.queries == [("www.google.com", 1, DNS_QUERY_BYPASS_CACHE, None, None)]
    assert dns.freed == [(0xABC0, 1)]  # DnsFreeRecordList


def test_a_failed_lookup_is_no_answer_and_an_answer_list_it_gave_is_still_freed():
    dns = FakeDns(status=DNS_ERROR_RCODE_SERVER_FAILURE)

    assert dns.lookup() is None
    assert dns.freed == [(0xABC0, 1)]


def test_a_failed_lookup_without_an_answer_frees_nothing():
    dns = FakeDns(status=DNS_ERROR_RCODE_SERVER_FAILURE, answer=None)

    assert dns.lookup() is None
    assert dns.freed == []


def test_a_name_without_an_address_record_still_shows_the_dns_server_answered():
    dns = FakeDns(status=DNS_INFO_NO_RECORDS, answer=None)

    assert dns.lookup() == pytest.approx(31.0)


# --- the Windows functions themselves ----------------------------------------------------------


class FakeFunction:
    """One Windows function: refuses to run undeclared and records the signature it ran with."""

    def __init__(self, signatures: dict, name: str, behaviour) -> None:
        self.signatures, self.name, self.behaviour = signatures, name, behaviour
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        assert self.argtypes is not None, f"{self.name} called without argtypes"
        self.signatures[self.name] = (self.argtypes, self.restype)
        return self.behaviour(*args)


def test_the_probes_load_iphlpapi_and_dnsapi_with_last_error_and_full_signatures(monkeypatch):
    icmp, routes, dns = FakeIcmp(), FakeRoutes(), FakeDns()
    signatures: dict = {}
    libraries = {
        "iphlpapi": SimpleNamespace(
            IcmpCreateFile=FakeFunction(signatures, "IcmpCreateFile", icmp.create),
            IcmpSendEcho=FakeFunction(signatures, "IcmpSendEcho", icmp.send),
            IcmpCloseHandle=FakeFunction(signatures, "IcmpCloseHandle", icmp.close),
            GetBestRoute=FakeFunction(signatures, "GetBestRoute", routes.best_route),
        ),
        "dnsapi": SimpleNamespace(
            DnsQuery_W=FakeFunction(signatures, "DnsQuery_W", dns.query),
            DnsRecordListFree=FakeFunction(signatures, "DnsRecordListFree", dns.free),
        ),
    }
    loads: list = []

    def fake_windll(name, **kwargs):
        loads.append((name, kwargs))
        return libraries[name]

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)

    assert icmp_echo("1.1.1.1") == 23.0
    assert default_route("1.1.1.1") == Route("192.168.1.1")
    assert dns_query_ms("www.google.com") is not None

    assert signatures == {
        "IcmpCreateFile": ([], wintypes.HANDLE),
        "IcmpSendEcho": (
            [
                wintypes.HANDLE,
                wintypes.ULONG,
                ctypes.c_void_p,
                wintypes.WORD,
                ctypes.POINTER(IP_OPTION_INFORMATION),
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
            ],
            wintypes.DWORD,
        ),
        "IcmpCloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "GetBestRoute": (
            [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(MIB_IPFORWARDROW)],
            wintypes.DWORD,
        ),
        "DnsQuery_W": (
            [
                wintypes.LPCWSTR,
                wintypes.WORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
            ],
            wintypes.LONG,
        ),
        "DnsRecordListFree": ([ctypes.c_void_p, ctypes.c_int], None),
    }
    assert {name for name, _ in loads} == {"iphlpapi", "dnsapi"}
    assert all(arguments == {"use_last_error": True} for _, arguments in loads)


def test_the_windows_structures_have_their_documented_64_bit_sizes():
    assert ctypes.sizeof(IP_OPTION_INFORMATION) == 16
    assert ctypes.sizeof(ICMP_ECHO_REPLY) == 40
    assert ctypes.sizeof(MIB_IPFORWARDROW) == 56


@pytest.mark.parametrize(
    ("probe", "refused"),
    [
        (lambda: icmp_echo("1.1.1.1"), "iphlpapi.IcmpCreateFile"),
        (lambda: default_route("1.1.1.1"), "iphlpapi.GetBestRoute"),
        (lambda: dns_query_ms("www.google.com"), "dnsapi.DnsQuery_W"),
    ],
)
def test_the_real_probes_never_reach_the_network_in_the_suite(probe, refused, refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("iphlpapi and dnsapi only exist on Windows")

    with pytest.raises(AssertionError):
        probe()

    assert refused_system_calls == [refused]
    refused_system_calls.clear()  # this one was on purpose


# --- a TCP connect -----------------------------------------------------------------------------


def test_a_connect_to_a_listening_port_gives_how_long_it_took():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]

        took = tcp_connect_ms("127.0.0.1", port, 2.0)

    assert took is not None and took >= 0


def test_a_connect_goes_over_ipv4_like_the_echoes_so_a_broken_ipv6_path_is_no_outage(
    monkeypatch,
):
    # Windows sorts a host's IPv6 addresses first; this one's IPv6 path leads nowhere.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        ipv4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))

        def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return [ipv4] if family == socket.AF_INET else [ipv6, ipv4]

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

        took = tcp_connect_ms("hoyo.example", port, 0.5)

    assert took is not None and took >= 0


def test_a_connect_nothing_answers_is_no_answer():
    # Port 9 (discard) is closed: refused, or timed out (Windows retries a closed loopback port).
    assert tcp_connect_ms("127.0.0.1", 9, 0.5) is None


def test_a_connect_beyond_this_pc_is_refused_in_the_suite(nothing_beyond_this_pc):
    with pytest.raises(AssertionError, match="internet"):
        tcp_connect_ms("sg-public-api.hoyolab.com", 443, 1.0)

    assert nothing_beyond_this_pc == ["sg-public-api.hoyolab.com"]
    nothing_beyond_this_pc.clear()  # this one was on purpose


# --- one check ---------------------------------------------------------------------------------

HOSTS = NetworkHostsConfig()
HOYO = HOSTS.hoyo
VIA_ROUTER = Route("192.168.1.1")


class FakeProbes:
    """Every probe a check makes, answered from tables (None: no answer), each call recorded."""

    def __init__(
        self,
        *,
        route: Route | None = VIA_ROUTER,
        echoes: dict[str, float | None] | None = None,
        connects: dict[tuple[str, int], float | None] | None = None,
        lookup: float | None = 31.0,
    ) -> None:
        self.route_result = route
        self.echoes = {"192.168.1.1": 1.0, "1.1.1.1": 24.0, "8.8.8.8": 30.0, **(echoes or {})}
        self.connects = {**{(host, 443): 45.0 for host in HOYO}, **(connects or {})}
        self.lookup = lookup
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def _record(self, call: tuple) -> None:
        with self._lock:
            self.calls.append(call)

    def route(self, toward: str) -> Route | None:
        self._record(("route", toward))
        return self.route_result

    def echo(self, address: str, timeout_ms: int) -> float | None:
        self._record(("echo", address, timeout_ms))
        return self.echoes.get(address)

    def connect(self, host: str, port: int, timeout_s: float) -> float | None:
        self._record(("connect", host, port))
        return self.connects.get((host, port))

    def resolve(self, name: str) -> float | None:
        self._record(("resolve", name))
        return self.lookup

    def probe(self, hosts: NetworkHostsConfig = HOSTS, **options) -> NetworkProbe:
        return NetworkProbe(
            hosts,
            route=self.route,
            echo=self.echo,
            connect=self.connect,
            resolve=self.resolve,
            now=lambda: NOW,
            **options,
        )

    def made(self, kind: str) -> list[tuple]:
        return sorted(call[1:] for call in self.calls if call[0] == kind)


def test_a_check_echoes_the_route_s_gateway_and_every_anchor():
    probes = FakeProbes()

    reading = probes.probe().check()

    assert reading == Reading(
        at=NOW,
        route=True,
        gateway="192.168.1.1",
        gateway_ms=1.0,
        anchor_ms=24.0,  # the quickest anchor
    )
    assert probes.made("route") == [("1.1.1.1",)]  # toward the first anchor
    assert probes.made("echo") == [
        ("1.1.1.1", ECHO_TIMEOUT_MS),
        ("192.168.1.1", ECHO_TIMEOUT_MS),
        ("8.8.8.8", ECHO_TIMEOUT_MS),
    ]
    assert probes.made("connect") == probes.made("resolve") == []


def test_a_configured_gateway_is_echoed_in_place_of_the_route_s():
    probes = FakeProbes(echoes={"192.168.0.254": 2.0})

    reading = probes.probe(NetworkHostsConfig(gateway="192.168.0.254")).check()

    assert (reading.gateway, reading.gateway_ms) == ("192.168.0.254", 2.0)
    assert ("192.168.1.1", ECHO_TIMEOUT_MS) not in probes.made("echo")


@pytest.mark.parametrize("route", [Route(None), None])
def test_without_a_gateway_on_the_route_none_is_echoed(route):
    probes = FakeProbes(route=route)

    reading = probes.probe().check()

    assert (reading.route, reading.gateway, reading.gateway_ms) == (route is not None, None, None)
    assert [address for address, _ in probes.made("echo")] == ["1.1.1.1", "8.8.8.8"]


def test_a_gateway_that_does_not_answer_has_no_round_trip():
    reading = FakeProbes(echoes={"192.168.1.1": None}).probe().check()

    assert (reading.gateway, reading.gateway_ms) == ("192.168.1.1", None)


def test_when_no_anchor_echoes_a_connect_on_443_stands_in_for_the_echo():
    # A firewall dropping ICMP must not look like an outage.
    probes = FakeProbes(
        echoes={"1.1.1.1": None, "8.8.8.8": None},
        connects={("1.1.1.1", 443): 38.0, ("8.8.8.8", 443): None},
    )

    reading = probes.probe().check()

    assert reading.anchor_ms == 38.0
    assert probes.made("connect") == [("1.1.1.1", 443), ("8.8.8.8", 443)]


def test_when_one_anchor_echoes_none_is_connected_to():
    probes = FakeProbes(echoes={"1.1.1.1": None})

    reading = probes.probe().check()

    assert reading.anchor_ms == 30.0
    assert probes.made("connect") == []


def test_no_anchor_by_echo_or_connect_is_no_round_trip():
    probes = FakeProbes(
        echoes={"1.1.1.1": None, "8.8.8.8": None},
        connects={("1.1.1.1", 443): None, ("8.8.8.8", 443): None},
    )

    assert probes.probe().check().anchor_ms is None


def test_the_name_is_looked_up_only_when_asked():
    probes = FakeProbes(lookup=31.0)

    asked = probes.probe().check(dns=True)

    assert (asked.dns_checked, asked.dns_ms) == (True, 31.0)
    assert probes.made("resolve") == [("www.google.com",)]


def test_a_failed_lookup_is_checked_without_a_time():
    reading = FakeProbes(lookup=None).probe().check(dns=True)

    assert (reading.dns_checked, reading.dns_ms) == (True, None)


def test_the_hoyo_hosts_are_connected_to_only_when_asked_each_in_its_order():
    probes = FakeProbes(connects={(HOYO[1], 443): None, (HOYO[2], 443): 60.0})

    reading = probes.probe().check(hoyo=True)

    assert reading.hoyo_ms == (45.0, None, 60.0)
    assert probes.made("connect") == sorted((host, 443) for host in HOYO)
    assert FakeProbes().probe().check().hoyo_ms is None


def test_the_probes_of_one_check_run_at_once():
    # Each echo waits for the others: only probes running together get past the barrier.
    barrier = threading.Barrier(3, timeout=WAIT_S)
    probes = FakeProbes()

    def echo(address: str, timeout_ms: int) -> float:
        barrier.wait()
        return 5.0

    probe = NetworkProbe(
        HOSTS, route=probes.route, echo=echo, connect=probes.connect, resolve=probes.resolve
    )

    assert probe.check().anchor_ms == 5.0


def test_a_probe_that_has_not_answered_within_the_budget_counts_as_failed():
    release = threading.Event()
    probes = FakeProbes()

    def slow_lookup(name: str) -> float:
        release.wait(WAIT_S)
        return 31.0

    probe = NetworkProbe(
        HOSTS,
        route=probes.route,
        echo=probes.echo,
        connect=probes.connect,
        resolve=slow_lookup,
        budget_s=0.2,
    )
    started = time.monotonic()
    try:
        reading = probe.check(dns=True)
    finally:
        release.set()

    assert time.monotonic() - started < WAIT_S
    assert (reading.dns_checked, reading.dns_ms, reading.anchor_ms) == (True, None, 24.0)


def test_a_probe_still_running_from_an_earlier_check_is_not_started_again():
    release = threading.Event()
    lookups: list[str] = []
    probes = FakeProbes()

    def stuck_lookup(name: str) -> float:
        lookups.append(name)
        release.wait(WAIT_S)
        return 31.0

    probe = NetworkProbe(
        HOSTS,
        route=probes.route,
        echo=probes.echo,
        connect=probes.connect,
        resolve=stuck_lookup,
        budget_s=0.1,
    )
    try:
        probe.check(dns=True)
        again = probe.check(dns=True)
    finally:
        release.set()

    assert lookups == ["www.google.com"]
    assert again.dns_ms is None


def test_a_check_made_while_another_runs_waits_for_the_probes_they_share():
    # The watcher, her answers, and the tool share one probe: Master asking during the watcher's
    # HoYo poll must not find the HoYo hosts silent.
    begun = threading.Semaphore(0)
    probes = FakeProbes()

    def slow_connect(host: str, port: int, timeout_s: float) -> float:
        begun.release()
        time.sleep(0.3)  # a slow server that answers
        return 45.0

    probe = NetworkProbe(
        HOSTS, route=probes.route, echo=probes.echo, connect=slow_connect, resolve=probes.resolve
    )
    readings: dict[str, Reading] = {}
    watcher = threading.Thread(target=lambda: readings.update(watcher=probe.check(hoyo=True)))
    watcher.start()
    assert all(begun.acquire(timeout=WAIT_S) for _ in HOYO)

    asked = probe.check(hoyo=True)
    watcher.join(WAIT_S)

    assert asked.hoyo_ms == readings["watcher"].hoyo_ms == (45.0,) * len(HOYO)
    assert asked.anchor_ms == 24.0


def test_a_probe_that_raises_counts_as_failed():
    probes = FakeProbes(connects={("1.1.1.1", 443): 40.0})

    def broken_echo(address: str, timeout_ms: int) -> float:
        raise RuntimeError("unexpected")

    probe = NetworkProbe(
        HOSTS,
        route=probes.route,
        echo=broken_echo,
        connect=probes.connect,
        resolve=probes.resolve,
    )

    reading = probe.check()

    assert (reading.gateway_ms, reading.anchor_ms) == (None, 40.0)


def test_a_route_lookup_that_raises_counts_as_no_route():
    probes = FakeProbes()

    def broken_route(toward: str) -> Route:
        raise OSError("unexpected")

    probe = NetworkProbe(
        HOSTS, route=broken_route, echo=probes.echo, connect=probes.connect, resolve=probes.resolve
    )

    reading = probe.check()

    assert (reading.route, reading.gateway, reading.anchor_ms) == (False, None, 24.0)
