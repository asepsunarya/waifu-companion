"""DeskServer: the quick buttons for the PC's own browser, on 127.0.0.1.

Every server binds port 0 and is stopped by the `serve` fixture. The web root holds small fake
page files under tmp_path, the buttons run MockDevice registries (never a real lamp, AC, plug,
or the PC), and clocks are fake. Nothing here opens a browser.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from waifu.alarm import AlarmBell
from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.base import DeviceError, DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.listen_pause import ListenPause
from waifu.phone.actions import QuickActions
from waifu.phone.desk import PC_WHERE, DeskServer
from waifu.schedule import AlarmAction, Entry, ScheduleStore

CLIENT_TIMEOUT_S = 5.0
SECRET = "the private corpus"
PAGE_FILES = {
    "tombol/index.html": b"<!doctype html><title>Tombol cepat</title>\n",
    "tombol/tombol.js": b"import '../phone/actions.js';\n",
    "tombol/desk.js": b"export const REQUEST_TIMEOUT_MS = 60000;\n",
    "tombol/tombol.css": b"body { margin: 0; }\n",
    "phone/actions.js": b"export function actionRequest(id) {}\n",
}
OTHER_FILES = {
    "phone/index.html": b"<!doctype html><title>Cyrene</title>\n",
    "phone/phone.js": b"boot();\n",
    "index.html": b"<!doctype html><title>Avatar</title>\n",
    "vendor/pixi/pixi.min.mjs": b"export const Application = class {};\n",
}
PROTECTING_HEADERS = {
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}
NOT_FOUND = "Tidak ditemukan."
UNKNOWN_BUTTON = "Tombol itu sudah tidak ada. Muat ulang halaman."
AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]
TIDUR = RoutineConfig(
    description="Mode tidur",
    steps=[
        RoutineStep(device="lampu_kamar", command="off"),
        RoutineStep(device="ac", command="temp", value=26),
    ],
)


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class SlowDevice(MockDevice):
    """A MockDevice whose commands wait for `release`; `entered` tells one has begun."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        self.entered.set()
        assert self.release.wait(CLIENT_TIMEOUT_S)
        return super().execute(command, value)


class UnreachableDevice(MockDevice):
    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        raise DeviceError("Kipas tidak menjawab.")


class BrokenSchedule:
    """A schedule whose every new entry fails with an error nobody expects."""

    def entries(self) -> list[Any]:
        return []

    def add(self, *args: Any) -> Any:
        raise RuntimeError("the schedule broke")


def quick_house(
    routines: Mapping[str, RoutineConfig] | None = None, schedule: Any = None, **extra: MockDevice
) -> tuple[dict[str, MockDevice], QuickActions]:
    """The owner's house as mocks (a lamp, the AC, the PC and its plug, and `extra`), with the
    PC page's quick buttons of its devices and of `routines` (by default the routine `tidur`)."""
    devices: dict[str, MockDevice] = {
        "lampu_kamar": MockDevice(
            "lampu_kamar",
            "Lampu kamar",
            commands=["on", "off", "toggle", "brightness"],
            kind="light",
        ),
        "ac": MockDevice("ac", "AC Samsung", commands=AC_COMMANDS, kind="ir"),
        "komputer": MockDevice(
            "komputer", "Komputer", commands=["wake", "shutdown", "restart", "sleep"], kind="pc"
        ),
        "colokan_pc": MockDevice(
            "colokan_pc", "Colokan PC", commands=["on", "off", "toggle"], kind="plug"
        ),
        **extra,
    }
    registry = DeviceRegistry(list(devices.values()), pc_power_ids=["colokan_pc"])
    routines = {"tidur": TIDUR} if routines is None else routines
    return devices, QuickActions(registry, routines, schedule=schedule, where=PC_WHERE)


def all_calls(devices: Mapping[str, MockDevice]) -> list[tuple[Any, ...]]:
    """Every command the house's devices were told, device by device."""
    return [(name, *call) for name, device in devices.items() for call in device.calls]


Serve = Callable[..., DeskServer]


@pytest.fixture
def web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    for name, content in {**PAGE_FILES, **OTHER_FILES}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    (tmp_path / "secret.txt").write_bytes(SECRET.encode())
    return root


@pytest.fixture(autouse=True)
def _fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short accept-loop poll keeps every stop() fast."""
    monkeypatch.setattr(DeskServer, "poll_interval_s", 0.02)


@pytest.fixture
def serve(web_root: Path) -> Iterator[Serve]:
    """Starts DeskServers on 127.0.0.1 port 0; every one is stopped after the test."""
    started: list[DeskServer] = []

    def start(
        actions: QuickActions | None = None,
        *,
        owner_call: str = "Master",
        clock: Callable[[], float] = time.monotonic,
    ) -> DeskServer:
        server = DeskServer(
            actions or quick_house()[1], owner_call=owner_call, web_root=web_root, clock=clock
        )
        started.append(server)
        server.start()
        return server

    yield start
    for server in started:
        server.stop()


def address(server: DeskServer) -> tuple[str, int]:
    url = urlsplit(server.url)
    assert url.hostname is not None and url.port is not None
    return url.hostname, url.port


def prefix(server: DeskServer) -> str:
    """The page's path, `/t/<token>/`: every route is under it."""
    return urlsplit(server.url).path


def token_of(server: DeskServer) -> str:
    return prefix(server).split("/")[2]


def own_origin(server: DeskServer) -> str:
    return f"http://127.0.0.1:{address(server)[1]}"


def request(
    server: DeskServer,
    path: str,
    method: str = "GET",
    *,
    host: str | None = None,
    headers: Mapping[str, str | None] | None = None,
    body: bytes | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """Sends `path` exactly as given, unnormalised; returns status, headers, and body.

    `host` replaces the Host header http.client sends (`127.0.0.1:<port>`); a header given as
    None is left out.
    """
    sent: dict[str, str] = {}
    if host is not None:
        sent["Host"] = host
    for name, value in (headers or {}).items():
        if value is None:
            sent.pop(name, None)
        else:
            sent[name] = value
    connection = http.client.HTTPConnection(*address(server), timeout=CLIENT_TIMEOUT_S)
    try:
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def post(
    server: DeskServer,
    path: str,
    payload: Mapping[str, Any] | bytes,
    *,
    host: str | None = None,
    headers: Mapping[str, str | None] | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """POSTs as the page does (its own Origin, JSON); `headers` overrides, None removes."""
    sent: dict[str, str | None] = {
        "Origin": own_origin(server),
        "Content-Type": "application/json",
        **(headers or {}),
    }
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return request(server, path, "POST", host=host, headers=sent, body=body)


def press(
    server: DeskServer, action_id: Any = "dev.lampu_kamar.on", **kwargs: Any
) -> tuple[int, http.client.HTTPMessage, bytes]:
    return post(server, f"{prefix(server)}action", {"id": action_id}, **kwargs)


def raw_request(server: DeskServer, data: bytes) -> bytes:
    """Sends `data` as it is and returns the whole reply (the server closes the connection)."""
    with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
        client.sendall(data)
        client.shutdown(socket.SHUT_WR)
        reply = b""
        while chunk := client.recv(65536):
            reply += chunk
    return reply


def error_of(body: bytes) -> str:
    payload = json.loads(body)
    assert list(payload) == ["error"]
    return payload["error"]


# --- address and lifecycle -----------------------------------------------------------------


def test_the_page_s_address_is_loopback_with_a_token_in_its_path(serve: Serve) -> None:
    server = serve()

    assert re.fullmatch(r"http://127\.0\.0\.1:[0-9]+/t/[A-Za-z0-9_-]{32,}/", server.url)
    assert address(server)[1] != 0


def test_every_server_has_a_token_of_its_own(serve: Serve) -> None:
    first, second = serve(), serve()

    assert token_of(first) != token_of(second)
    assert request(first, prefix(second))[0] == 404


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::1", "localhost", "::"])
def test_a_host_that_is_not_ipv4_loopback_is_refused(web_root: Path, host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        DeskServer(quick_house()[1], owner_call="Master", web_root=web_root, host=host)


def test_stop_frees_the_port_and_is_safe_twice_and_without_start(web_root: Path) -> None:
    def make(port: int = 0) -> DeskServer:
        return DeskServer(quick_house()[1], owner_call="Master", web_root=web_root, port=port)

    never_started = make()
    never_started.stop()
    server = make()
    server.start()
    port = address(server)[1]
    server.stop()
    server.stop()

    again = make(port)
    again.stop()


def test_the_context_manager_serves_inside_and_stops_on_exit(web_root: Path) -> None:
    server = DeskServer(quick_house()[1], owner_call="Master", web_root=web_root)
    with server:
        status, _, _ = request(server, prefix(server))
        port = address(server)[1]

    assert status == 200
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()


@pytest.mark.parametrize("request_line", [b"GET /\r\n", b"GET / HTTP/0.9\r\nHost: x\r\n\r\n"])
def test_an_http_0_9_request_gets_no_answer_and_no_error(
    serve: Serve, caplog: pytest.LogCaptureFixture, request_line: bytes
) -> None:
    server = serve()

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        reply = raw_request(server, request_line)

    assert reply == b""
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


# --- the page and its files -----------------------------------------------------------------


def test_the_page_is_served_at_its_address_never_cached(serve: Serve) -> None:
    server = serve()

    status, headers, body = request(server, prefix(server))

    assert status == 200
    assert headers.get_content_type() == "text/html"
    assert headers["Cache-Control"] == "no-store"
    assert body == PAGE_FILES["tombol/index.html"]


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("tombol/tombol.js", "text/javascript"),
        ("tombol/desk.js", "text/javascript"),
        ("tombol/tombol.css", "text/css"),
        ("phone/actions.js", "text/javascript"),
    ],
)
def test_the_page_s_files_are_served_under_its_address(
    serve: Serve, name: str, content_type: str
) -> None:
    server = serve()

    status, headers, body = request(server, f"{prefix(server)}{name}")

    assert status == 200
    assert headers.get_content_type() == content_type
    assert body == PAGE_FILES[name]


@pytest.mark.parametrize(
    "name",
    [
        "phone/phone.js",
        "phone/index.html",
        "index.html",
        "vendor/pixi/pixi.min.mjs",
        "tombol/../phone/phone.js",
        "tombol/%2e%2e/phone/phone.js",
        "tombol/..%5c..%5csecret.txt",
        "tombol/../../secret.txt",
        "../secret.txt",
        "tombol/",
        "tombol/missing.js",
        "phone/",
        "config.json",
        "events",
    ],
)
def test_nothing_else_of_the_web_root_is_served(serve: Serve, name: str) -> None:
    server = serve()

    status, _, body = request(server, f"{prefix(server)}{name}")

    assert status == 404
    assert error_of(body) == NOT_FOUND
    assert SECRET.encode() not in body


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/t/",
        "/t//",
        "/t/{wrong}/",
        "/t/{token}",
        "/t/{token}x/",
        "/t/{upper}/",
        "/T/{token}/",
        "/t/%7Btoken%7D/",
        "/{token}/",
        "/tombol/tombol.js",
        "/phone/actions.js",
        "/actions",
        "/t/{wrong}/actions",
        "/t/{wrong}/phone/actions.js",
        "/t/{wrong}/t/{token}/",
    ],
)
def test_without_the_token_nothing_is_served(serve: Serve, path: str) -> None:
    server = serve()
    token = token_of(server)
    wrong = token[:-1] + ("A" if token[-1] != "A" else "B")
    sent = path.format(token=token, wrong=wrong, upper=token.upper())

    status, _, body = request(server, sent)

    assert status == 404
    assert error_of(body) == NOT_FOUND


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS"])
def test_a_request_without_the_token_is_404_for_every_method_and_runs_nothing(
    serve: Serve, method: str
) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    body = json.dumps({"id": "dev.lampu_kamar.on"}).encode()

    for path in ("/action", "/t/not-the-token/action", f"/t/{token_of(server)}"):
        status, headers, _ = request(
            server,
            path,
            method,
            headers={"Origin": own_origin(server), "Content-Type": "application/json"},
            body=body,
        )
        assert status == 404, path
        assert "Allow" not in headers

    assert all_calls(devices) == []


# --- the Host check --------------------------------------------------------------------------

FOREIGN_HOSTS = [
    "evil.example",
    "evil.example:{port}",
    "127.0.0.1",
    "127.0.0.1:1",
    "127.0.0.2:{port}",
    "localhost",
    "[::1]:{port}",
    "127.0.0.1:{port}.evil.example",
    "",
]


@pytest.mark.parametrize("host", FOREIGN_HOSTS)
def test_a_foreign_host_is_refused_before_anything_even_with_the_token(
    serve: Serve, host: str
) -> None:
    server = serve()

    status, headers, body = request(
        server, prefix(server), host=host.format(port=address(server)[1])
    )

    assert status == 403
    assert "127.0.0.1" in error_of(body) and "localhost" in error_of(body)
    assert headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("host", FOREIGN_HOSTS)
def test_a_press_for_a_foreign_host_is_refused_and_runs_nothing(serve: Serve, host: str) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    port = address(server)[1]

    status, _, _ = press(server, host=host.format(port=port))

    assert status == 403
    assert all_calls(devices) == []


def test_localhost_names_the_page_too(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    host = f"localhost:{address(server)[1]}"

    page_status, _, _ = request(server, prefix(server), host=host)
    status, _, _ = press(server, host=host, headers={"Origin": f"http://{host}"})

    assert (page_status, status) == (200, 200)
    assert all_calls(devices) == [("lampu_kamar", "on", None)]


def test_the_host_check_ignores_case_and_spaces(serve: Serve) -> None:
    server = serve()

    status, _, _ = request(server, prefix(server), host=f" LocalHost:{address(server)[1]} ")

    assert status == 200


# --- origins, methods, and headers --------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "http://evil.example",
        "http://127.0.0.1:{other}",
        "https://127.0.0.1:{port}",
        "http://127.0.0.1",
        "http://127.0.0.1:{port}/",
        "http://localhost.evil.example:{port}",
        "",
    ],
)
def test_a_press_from_any_other_origin_is_forbidden_and_runs_nothing(
    serve: Serve, origin: str | None
) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    port = address(server)[1]
    sent = None if origin is None else origin.format(port=port, other=port + 1)

    status, _, body = press(server, headers={"Origin": sent})

    assert status == 403
    assert error_of(body) == "Permintaan hanya diterima dari halaman Cyrene sendiri."
    assert all_calls(devices) == []


def test_a_press_with_two_origins_is_forbidden(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    port = address(server)[1]
    body = b'{"id": "dev.lampu_kamar.on"}'
    head = (
        b"POST %saction HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://127.0.0.1:%d\r\n"
        b"Origin: http://evil.example\r\nContent-Type: application/json\r\n"
        b"Content-Length: %d\r\n\r\n" % (prefix(server).encode(), port, port, len(body))
    )

    reply = raw_request(server, head + body)

    assert reply.startswith(b"HTTP/1.0 403 ")
    assert all_calls(devices) == []


@pytest.mark.parametrize("method", ["OPTIONS", "PUT", "DELETE", "PATCH", "HEAD"])
def test_a_preflight_or_any_other_method_is_405_without_cors_headers(
    serve: Serve, method: str
) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, headers, body = request(
        server,
        f"{prefix(server)}action",
        method,
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert status == 405
    assert headers["Allow"] == "POST"
    assert not [name for name in headers if name.lower().startswith("access-control-")]
    assert (body == b"") == (method == "HEAD")
    assert all_calls(devices) == []


def test_get_of_the_press_route_is_405_and_a_post_elsewhere_is_405(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    get_status, get_headers, _ = request(server, f"{prefix(server)}action")
    post_page, page_headers, _ = post(server, prefix(server), {"id": "dev.lampu_kamar.on"})
    post_list, list_headers, _ = post(
        server, f"{prefix(server)}actions", {"id": "dev.lampu_kamar.on"}
    )

    assert (get_status, get_headers["Allow"]) == (405, "POST")
    assert (post_page, page_headers["Allow"]) == (405, "GET")
    assert (post_list, list_headers["Allow"]) == (405, "GET")
    assert all_calls(devices) == []


def test_every_answer_protects_the_page_and_sends_no_referrer(serve: Serve) -> None:
    server = serve()
    port = address(server)[1]

    answers = [
        request(server, prefix(server)),
        request(server, f"{prefix(server)}tombol/tombol.js"),
        request(server, f"{prefix(server)}actions"),
        request(server, "/"),
        request(server, prefix(server), host="evil.example"),
        request(server, f"{prefix(server)}action"),
        press(server, headers={"Origin": "http://evil.example"}),
        press(server, host=f"localhost:{port}"),
        press(server),
    ]

    for status, headers, _ in answers:
        for name, value in PROTECTING_HEADERS.items():
            assert headers[name] == value, (status, name)


# --- the buttons -----------------------------------------------------------------------------


def test_the_page_gets_the_buttons_never_cached(serve: Serve) -> None:
    server = serve()

    status, headers, body = request(server, f"{prefix(server)}actions")

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert headers["Cache-Control"] == "no-store"
    groups = json.loads(body)["groups"]
    assert [group["name"] for group in groups] == ["Lampu kamar", "AC Samsung", "Rutinitas"]
    assert groups[2]["actions"] == [{"id": "rt.tidur", "label": "Mode tidur"}]
    assert b"komputer" not in body and b"colokan_pc" not in body


def test_a_press_runs_its_button_and_answers_its_message(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, headers, body = press(server, "dev.lampu_kamar.brightness.30")

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "ok": True,
        "message": "Lampu kamar: perintah 'brightness' berhasil (simulasi).",
    }
    assert all_calls(devices) == [("lampu_kamar", "brightness", 30)]


def test_a_device_that_fails_is_told_in_a_200_so_the_page_shows_why(serve: Serve) -> None:
    _, actions = quick_house(kipas=UnreachableDevice("kipas", "Kipas", kind="plug"))
    server = serve(actions)

    status, _, body = press(server, "dev.kipas.on")

    assert status == 200
    assert json.loads(body) == {"ok": False, "message": "Kipas tidak menjawab."}


def test_a_routine_button_runs_the_routine(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, _, body = press(server, "rt.tidur")

    assert status == 200
    assert json.loads(body) == {"ok": True, "message": "Mode tidur: 2 dari 2 langkah berhasil."}
    assert all_calls(devices) == [("lampu_kamar", "off", None), ("ac", "temp_26", 26)]


@pytest.mark.parametrize(
    "action_id",
    [
        "dev.komputer.shutdown",
        "dev.komputer.restart",
        "dev.komputer.sleep",
        "dev.komputer.wake",
        "dev.colokan_pc.off",
        "dev.colokan_pc.toggle",
        "dev.colokan_pc.on",
    ],
)
def test_the_pc_and_its_plug_are_never_buttons(serve: Serve, action_id: str) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, _, body = press(server, action_id)

    assert status == 404
    assert error_of(body) == UNKNOWN_BUTTON
    assert all_calls(devices) == []


@pytest.mark.parametrize(
    "action_id", ["dev.kulkas.on", "dev.lampu_kamar.brightness.55", "dev.ac.temp_17", "rt.siang"]
)
def test_an_unknown_button_is_404_in_indonesian(serve: Serve, action_id: str) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, _, body = press(server, action_id)

    assert status == 404
    assert error_of(body) == UNKNOWN_BUTTON
    assert all_calls(devices) == []


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"[]",
        b"not json",
        b'{"id": 1}',
        b'{"id": null}',
        b'{"id": "dev.lampu_kamar.on", "value": 5}',
        b'{"id": "dev.lampu_kamar.on", "token": "x"}',
        b'{"id": "DEV.lampu_kamar.on"}',
        b'{"id": "dev.lampu_kamar.on; shutdown"}',
        b'{"id": "dev.lampu_kamar.brightness.1000"}',
        b'{"id": "dev.lampu_kamar.on\\n"}',
        b'{"id": "\\ud800"}',
    ],
)
def test_a_malformed_press_is_400_and_runs_nothing(serve: Serve, body: bytes) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, _, reply = post(server, f"{prefix(server)}action", body)

    assert status == 400
    assert error_of(reply)
    assert all_calls(devices) == []


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_a_press_a_form_could_send_is_415(serve: Serve, content_type: str) -> None:
    devices, actions = quick_house()
    server = serve(actions)

    status, _, _ = press(server, headers={"Content-Type": content_type})

    assert status == 415
    assert all_calls(devices) == []


def test_a_press_over_a_kilobyte_is_413(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    body = json.dumps({"id": "dev.lampu_kamar.on", "pad": "x" * 1024}).encode()

    status, _, _ = post(server, f"{prefix(server)}action", body)

    assert status == 413
    assert all_calls(devices) == []


def test_a_press_without_a_content_length_is_411(serve: Serve) -> None:
    devices, actions = quick_house()
    server = serve(actions)
    port = address(server)[1]
    head = (
        b"POST %saction HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://127.0.0.1:%d\r\n"
        b"Content-Type: application/json\r\n\r\n" % (prefix(server).encode(), port, port)
    )

    reply = raw_request(server, head)

    assert reply.startswith(b"HTTP/1.0 411 ")
    assert all_calls(devices) == []


def test_the_page_gets_ten_presses_a_minute(serve: Serve) -> None:
    devices, actions = quick_house()
    clock = FakeClock()
    server = serve(actions, clock=clock)

    statuses = [press(server)[0] for _ in range(10)]
    clock.now += 59.0
    status, headers, body = press(server)

    assert statuses == [200] * 10
    assert status == 429
    assert headers["Retry-After"] == "60"
    assert "Tunggu sebentar" in error_of(body)
    assert len(devices["lampu_kamar"].calls) == 10
    clock.now += 1.0
    assert press(server)[0] == 200  # the first of the ten is a minute old


def test_a_refused_press_counts_toward_the_ten(serve: Serve) -> None:
    server = serve(clock=FakeClock())

    statuses = [press(server, "dev.kulkas.on")[0] for _ in range(10)]

    assert statuses == [404] * 10
    assert press(server)[0] == 429


def test_a_press_from_another_site_never_uses_up_the_ten(serve: Serve) -> None:
    server = serve(clock=FakeClock())

    refused = [press(server, headers={"Origin": "http://evil.example"})[0] for _ in range(20)]

    assert refused == [403] * 20
    assert press(server)[0] == 200


def test_a_second_press_while_the_first_runs_is_409(serve: Serve) -> None:
    """Not the rate limit's 429: the page tells the owner the last press still runs."""
    slow = SlowDevice("kipas", "Kipas", kind="plug")
    devices, actions = quick_house(kipas=slow)
    server = serve(actions)
    statuses: list[int] = []
    thread = threading.Thread(target=lambda: statuses.append(press(server, "dev.kipas.on")[0]))
    thread.start()
    assert slow.entered.wait(CLIENT_TIMEOUT_S)

    status, headers, body = press(server)
    slow.release.set()
    thread.join(CLIENT_TIMEOUT_S)

    assert status == 409
    assert "Retry-After" not in headers
    assert error_of(body) == "Perintah tadi masih jalan. Tunggu sebentar."
    assert statuses == [200]
    assert all_calls(devices) == [("kipas", "on", None)]
    assert press(server)[0] == 200  # free again once it ran


def test_a_press_that_raises_is_a_500_in_indonesian_and_frees_the_page(
    serve: Serve, caplog: pytest.LogCaptureFixture
) -> None:
    later = RoutineConfig(steps=[RoutineStep(device="ac", command="off", after_minutes=60)])
    _, actions = quick_house({"nanti": later}, schedule=BrokenSchedule())
    server = serve(actions, owner_call="sayang")

    with caplog.at_level(logging.ERROR, logger="waifu.phone.desk"):
        status, _, body = press(server, "rt.nanti")

    assert status == 500
    assert error_of(body) == "Maaf, sayang, ada masalah di PC. Coba lagi nanti ya."
    assert any(record.exc_info for record in caplog.records)
    assert press(server)[0] == 200


def test_presses_are_logged_by_button_and_the_token_never(
    serve: Serve,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, actions = quick_house(kipas=UnreachableDevice("kipas", "Kipas", kind="plug"))
    server = serve(actions)
    token = token_of(server)

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        request(server, prefix(server))
        request(server, f"{prefix(server)}actions")
        request(server, f"/t/{token}x/")
        press(server)
        press(server, "dev.kipas.on")
        press(server, "dev.komputer.shutdown")
        raw_request(server, b"GET /t/" + token.encode() + b"/ HTTP/1.1 junk\r\n\r\n")

    infos = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert "PC page ran quick action dev.lampu_kamar.on: ok" in infos
    assert "PC page ran quick action dev.kipas.on: failed" in infos
    assert any("dev.komputer.shutdown" in line for line in infos)
    debugs = [record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG]
    assert any("GET /t/<token>/ " in line for line in debugs)
    logged = "\n".join(record.getMessage() for record in caplog.records) + caplog.text
    assert token not in logged
    assert token not in capsys.readouterr().err


# --- the alarm's buttons -------------------------------------------------------------------------


def ringing_actions(tmp_path: Path) -> tuple[AlarmBell, QuickActions]:
    """Quick buttons with an alarm bell under tmp_path that rings."""
    moment = datetime(2026, 10, 25, 6, 0)
    bell = AlarmBell(
        tmp_path / "alarm.json",
        ScheduleStore(tmp_path / "schedule.json", now=lambda: moment),
        now=lambda: moment,
        clock=lambda: 0.0,
    )
    bell.start(Entry("j1", moment, "none", AlarmAction(), moment))
    devices, _ = quick_house()
    registry = DeviceRegistry(list(devices.values()), pc_power_ids=["colokan_pc"])
    return bell, QuickActions(registry, {}, alarm=bell, where=PC_WHERE)


def test_the_pc_page_lists_and_presses_the_alarm_s_buttons_while_it_rings(
    serve: Serve, tmp_path: Path
) -> None:
    bell, actions = ringing_actions(tmp_path)
    server = serve(actions)

    groups = json.loads(request(server, f"{prefix(server)}actions")[2])["groups"]
    status, _, body = press(server, "al.snooze")

    assert groups[0]["name"] == "Alarm"
    assert (status, json.loads(body)) == (
        200,
        {"ok": True, "message": "Alarm ditunda sampai jam 06.05."},
    )
    assert bell.ringing() is False


def test_the_pc_page_lists_and_presses_dengar_lagi_while_her_hearing_is_paused(
    serve: Serve, tmp_path: Path
) -> None:
    pause = ListenPause(tmp_path / "listen_pause.json")
    pause.start()
    devices, _ = quick_house()
    registry = DeviceRegistry(list(devices.values()), pc_power_ids=["colokan_pc"])
    server = serve(QuickActions(registry, {}, where=PC_WHERE, listen_pause=pause))

    groups = json.loads(request(server, f"{prefix(server)}actions")[2])["groups"]
    status, _, body = press(server, "mic.listen")

    assert groups[0]["name"] == "Mikrofon"
    assert (status, json.loads(body)) == (200, {"ok": True, "message": "Mendengarkan lagi."})
    assert pause.paused() is False
