"""AvatarServer: the viewer page, the VRM, and the SSE event stream on 127.0.0.1.

Every server binds port 0 (an ephemeral port) and is stopped by the `serve`
fixture; the web root and the .vrm are small files under tmp_path, never the
real model.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import struct
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import numpy as np
import pytest

from waifu.avatar.bus import AvatarBus
from waifu.avatar.server import AvatarError, AvatarServer
from waifu.config import AvatarConfig

CLIENT_TIMEOUT_S = 5.0
MODEL_BYTES = bytes(range(256)) * 1200  # 300 KiB: more than one read chunk
SECRET = "the private corpus"
HELLO = {
    "type": "hello",
    "model": "/model.vrm",
    "name": "Cyrene",
    "framing": "full",
    "credit": "つくよみちゃん（© Rei Yumesaki）",
}
IDLE = {"type": "state", "state": "idle"}

Serve = Callable[..., AvatarServer]


def make_config(model: Path | None, port: int = 0) -> AvatarConfig:
    # model_copy skips validation: the config refuses ports below 1024, the tests need 0.
    return AvatarConfig(model=model).model_copy(update={"port": port})


def address(server: AvatarServer) -> tuple[str, int]:
    url = urlsplit(server.url)
    assert url.hostname is not None and url.port is not None
    return url.hostname, url.port


WEB_FILES = {
    "index.html": b"<!doctype html><title>Cyrene</title>\n",
    "avatar.js": b"export const ready = true;\n",
    "vendor/three/three.module.mjs": b"export {};\n",
    "vendor/LICENSE": b"MIT\n",
    "style.css": b"body { margin: 0; }\n",
    "data.json": b"{}\n",
    "icon.png": b"\x89PNG\r\n\x1a\n",
    "icon.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>\n",
    "VENDOR.md": b"# Vendor\n",
}


@pytest.fixture
def web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    for name, content in WEB_FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    (tmp_path / "secret.txt").write_bytes(SECRET.encode())
    return root


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    path = tmp_path / "models" / "tsukuyomi_a.vrm"
    path.parent.mkdir()
    path.write_bytes(MODEL_BYTES)
    return path


@pytest.fixture(autouse=True)
def _fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short accept-loop poll keeps every stop() fast."""
    monkeypatch.setattr(AvatarServer, "poll_interval_s", 0.02)


@pytest.fixture
def serve(web_root: Path, model_file: Path) -> Iterator[Serve]:
    """Starts AvatarServers on 127.0.0.1 port 0; every one is stopped after the test."""
    started: list[AvatarServer] = []

    def start(
        *,
        bus: AvatarBus | None = None,
        model: Path | None = model_file,
        ping_interval_s: float | None = None,
    ) -> AvatarServer:
        server = AvatarServer(make_config(model), bus or AvatarBus(), web_root=web_root)
        started.append(server)
        if ping_interval_s is not None:
            server.ping_interval_s = ping_interval_s
        server.start()
        return server

    yield start
    for server in started:
        server.stop()


def request(
    server: AvatarServer, path: str, method: str = "GET", *, host: str | None = None
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """Sends `path` exactly as given, unnormalised; returns status, headers, and body.

    `host` replaces the Host header http.client sends (`127.0.0.1:<port>`).
    """
    connection = http.client.HTTPConnection(*address(server), timeout=CLIENT_TIMEOUT_S)
    try:
        connection.request(method, path, headers={} if host is None else {"Host": host})
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


class EventStream:
    """An open GET /events, read one SSE block (the lines before a blank line) at a time."""

    def __init__(self, server: AvatarServer) -> None:
        self.connection = http.client.HTTPConnection(*address(server), timeout=CLIENT_TIMEOUT_S)
        self.connection.request("GET", "/events")
        self.response = self.connection.getresponse()

    def next_block(self) -> list[str]:
        lines: list[str] = []
        while True:
            raw = self.response.readline()
            if not raw:
                raise EOFError("the event stream ended")
            line = raw.decode("utf-8").removesuffix("\n")
            if line:
                lines.append(line)
            elif lines:
                return lines

    def next_event(self) -> dict[str, Any]:
        (line,) = self.next_block()
        assert line.startswith("data: ")
        return json.loads(line.removeprefix("data: "))

    def skip_greeting(self) -> None:
        """Reads the retry line, hello, and the last state: the server has subscribed by now."""
        self.next_block()
        self.next_event()
        self.next_event()

    def close(self) -> None:
        # The stream is a will-close response: http.client handed the socket to it.
        self.response.close()
        self.connection.close()


@pytest.fixture
def open_stream() -> Iterator[Callable[[AvatarServer], EventStream]]:
    streams: list[EventStream] = []

    def open_(server: AvatarServer) -> EventStream:
        stream = EventStream(server)
        streams.append(stream)
        return stream

    yield open_
    for stream in streams:
        stream.close()


# --- address and lifecycle -------------------------------------------------------------------


def test_url_names_the_bound_loopback_address(serve: Serve) -> None:
    server = serve()
    host, port = address(server)

    assert server.url == f"http://127.0.0.1:{port}/"
    assert host == "127.0.0.1"
    assert port > 0


def test_a_second_server_on_the_same_port_is_an_avatar_error(
    serve: Serve, web_root: Path, model_file: Path
) -> None:
    port = address(serve())[1]

    with pytest.raises(AvatarError) as error:
        AvatarServer(make_config(model_file, port), AvatarBus(), web_root=web_root)

    assert f"127.0.0.1:{port}" in str(error.value)
    assert "sudah dipakai" in str(error.value)


def test_stop_frees_the_port(serve: Serve, web_root: Path, model_file: Path) -> None:
    server = serve()
    port = address(server)[1]
    assert request(server, "/")[0] == 200

    server.stop()

    with AvatarServer(make_config(model_file, port), AvatarBus(), web_root=web_root) as again:
        assert request(again, "/")[0] == 200


def test_stop_is_safe_twice_and_without_start(web_root: Path, model_file: Path) -> None:
    server = AvatarServer(make_config(model_file), AvatarBus(), web_root=web_root)

    server.stop()
    server.stop()


def test_the_context_manager_serves_inside_and_stops_on_exit(
    web_root: Path, model_file: Path
) -> None:
    bus = AvatarBus()
    with AvatarServer(make_config(model_file), bus, web_root=web_root) as server:
        assert request(server, "/")[0] == 200

    assert bus.subscribe().closed


# --- the page and static files ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/?mode=mascot", "/index.html"])
def test_the_page_is_index_html_and_never_cached(serve: Serve, path: str) -> None:
    status, headers, body = request(serve(), path)

    assert status == 200
    assert headers.get_content_type() == "text/html"
    assert headers["Cache-Control"] == "no-store"
    assert body == WEB_FILES["index.html"]


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("avatar.js", "text/javascript"),
        ("vendor/three/three.module.mjs", "text/javascript"),
        ("style.css", "text/css"),
        ("data.json", "application/json"),
        ("icon.png", "image/png"),
        ("icon.svg", "image/svg+xml"),
        ("VENDOR.md", "text/markdown"),
        ("vendor/LICENSE", "application/octet-stream"),
    ],
)
def test_static_files_are_served_with_their_content_type(
    serve: Serve, name: str, content_type: str
) -> None:
    status, headers, body = request(serve(), f"/{name}")

    assert status == 200
    assert headers.get_content_type() == content_type
    assert headers["Content-Length"] == str(len(WEB_FILES[name]))
    assert "no-store" not in headers.get("Cache-Control", "")
    assert body == WEB_FILES[name]


@pytest.mark.parametrize(
    "path",
    [
        "/missing.js",
        "/vendor",
        "/vendor/",
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/%2E%2E%2Fsecret.txt",
        "/vendor/../../secret.txt",
        "/vendor/%2e%2e/%2e%2e/secret.txt",
        "/..\\secret.txt",
        "/..%5Csecret.txt",
        "/vendor\\..\\..\\secret.txt",
        "/..%2F..%2F..%2Fsecret.txt",
        "/index.html%00.js",
        "/index.html::$DATA",
    ],
)
def test_paths_outside_the_web_root_or_not_files_are_plain_404s(serve: Serve, path: str) -> None:
    status, headers, body = request(serve(), path)

    assert status == 404
    assert headers.get_content_type() == "text/plain"
    assert SECRET.encode() not in body


def test_an_absolute_path_is_a_404(serve: Serve, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    server = serve()

    for path in ("/" + secret.as_posix(), "/" + quote(str(secret)), "/" + quote(secret.as_posix())):
        status, _, body = request(server, path)
        assert status == 404, path
        assert SECRET.encode() not in body


@pytest.mark.parametrize(
    "path",
    ["/%5C%5Cattacker.invalid%5Cshare%5Cavatar.js", "/\\\\attacker.invalid\\share\\avatar.js"],
)
def test_a_unc_path_is_refused_before_it_is_ever_resolved(
    serve: Serve, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # Resolving \\host\share makes Windows contact that host (offering it the owner's
    # credentials). The guard records the lookup instead of making it.
    real_realpath = os.path.realpath
    unc_lookups: list[str] = []

    def guarded_realpath(target: Any, *args: Any, **kwargs: Any) -> str:
        text = os.fspath(target)
        if text.startswith(("\\\\", "//")):
            unc_lookups.append(text)
            return text
        return real_realpath(target, *args, **kwargs)

    server = serve()
    monkeypatch.setattr(os.path, "realpath", guarded_realpath)

    status, _, _ = request(server, path)

    assert status == 404
    assert unc_lookups == []


# --- the model -------------------------------------------------------------------------------


def test_the_model_is_streamed_with_its_length_and_type(serve: Serve) -> None:
    status, headers, body = request(serve(), "/model.vrm")

    assert status == 200
    assert headers.get_content_type() == "model/gltf-binary"
    assert headers["Content-Length"] == str(len(MODEL_BYTES))
    assert body == MODEL_BYTES


def test_a_missing_model_file_is_a_404(serve: Serve, tmp_path: Path) -> None:
    status, headers, _ = request(serve(model=tmp_path / "gone.vrm"), "/model.vrm")

    assert status == 404
    assert headers.get_content_type() == "text/plain"


def test_no_configured_model_is_a_404(serve: Serve) -> None:
    status, _, _ = request(serve(model=None), "/model.vrm")

    assert status == 404


# --- methods and logging ---------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_methods_other_than_get_are_405(serve: Serve, method: str) -> None:
    status, headers, _ = request(serve(), "/", method=method)

    assert status == 405
    assert headers["Allow"] == "GET"


def test_requests_are_logged_at_debug_not_written_to_stderr(
    serve: Serve, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    server = serve()

    with caplog.at_level(logging.DEBUG, logger="waifu.avatar.server"):
        request(server, "/")
        request(server, "/nope")
        request(server, "/", method="POST")

    assert capsys.readouterr().err == ""
    access = [r for r in caplog.records if '"GET / HTTP/1.1" 200' in r.getMessage()]
    assert access and all(r.levelno == logging.DEBUG for r in access)


# --- the event stream ------------------------------------------------------------------------


def test_the_stream_sends_retry_hello_last_state_then_published_events_in_order(
    serve: Serve, open_stream: Callable[[AvatarServer], EventStream]
) -> None:
    bus = AvatarBus()
    bus.state("thinking")
    stream = open_stream(serve(bus=bus))

    assert stream.response.status == 200
    assert stream.response.headers.get_content_type() == "text/event-stream"
    assert stream.response.headers["Cache-Control"] == "no-cache"
    assert stream.next_block() == ["retry: 2000"]
    assert stream.next_event() == HELLO
    assert stream.next_event() == {"type": "state", "state": "thinking"}

    bus.emotion("happy")
    bus.publish({"type": "speak", "emotion": "happy", "fps": 60, "duration": 0.03,
                 "visemes": [[0.5, 0, 0, 0, 0], [0.1, 0, 0, 0, 0]]})  # fmt: skip
    bus.state("listening", followup_s=8)

    assert stream.next_event() == {"type": "emotion", "emotion": "happy", "hold_s": 4}
    assert stream.next_event()["visemes"] == [[0.5, 0, 0, 0, 0], [0.1, 0, 0, 0, 0]]
    assert stream.next_event() == {"type": "state", "state": "listening", "followup_s": 8}


def test_every_viewer_gets_every_event(
    serve: Serve, open_stream: Callable[[AvatarServer], EventStream]
) -> None:
    bus = AvatarBus()
    server = serve(bus=bus)
    streams = [open_stream(server), open_stream(server)]
    for stream in streams:
        stream.skip_greeting()

    bus.emotion("sad")

    for stream in streams:
        assert stream.next_event() == {"type": "emotion", "emotion": "sad", "hold_s": 4}


def test_a_quiet_stream_sends_ping_comments(
    serve: Serve, open_stream: Callable[[AvatarServer], EventStream]
) -> None:
    stream = open_stream(serve(ping_interval_s=0.05))
    stream.skip_greeting()

    assert stream.next_block() == [": ping"]
    assert stream.next_block() == [": ping"]


def test_the_ping_interval_is_fifteen_seconds() -> None:
    assert AvatarServer.ping_interval_s == 15


def test_stop_ends_an_open_stream(
    serve: Serve, open_stream: Callable[[AvatarServer], EventStream]
) -> None:
    server = serve()
    stream = open_stream(server)
    stream.skip_greeting()

    started = time.monotonic()
    server.stop()

    with pytest.raises(EOFError):
        stream.next_block()
    assert time.monotonic() - started < 2.0


def test_a_viewer_that_disconnects_ends_its_stream_quietly(
    serve: Serve,
    open_stream: Callable[[AvatarServer], EventStream],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = AvatarBus()
    server = serve(bus=bus)
    stream = open_stream(server)
    stream.skip_greeting()

    with caplog.at_level(logging.DEBUG, logger="waifu.avatar.server"):
        stream.close()
        deadline = time.monotonic() + CLIENT_TIMEOUT_S
        while not any("disconnected" in r.getMessage() for r in caplog.records):
            assert time.monotonic() < deadline, "the stream did not notice the disconnect"
            bus.emotion("happy")  # writing to the closed connection is what reveals it
            time.sleep(0.02)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert capsys.readouterr().err == ""
    fresh = open_stream(server)
    assert fresh.next_block() == ["retry: 2000"]


def test_a_connection_reset_mid_request_is_logged_quietly(
    serve: Serve, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    server = serve()
    # l_onoff=1, l_linger=0: close() resets the connection instead of ending it.
    linger = struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0)

    with caplog.at_level(logging.DEBUG, logger="waifu.avatar.server"):
        with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
            client.sendall(b"GET /model.vrm HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        deadline = time.monotonic() + CLIENT_TIMEOUT_S
        while not any("disconnected" in r.getMessage() for r in caplog.records):
            assert time.monotonic() < deadline, "the reset was never handled"
            time.sleep(0.02)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert capsys.readouterr().err == ""
    assert request(server, "/")[0] == 200


def test_a_viewer_that_connects_mid_reply_gets_the_speech_with_how_much_has_played(
    serve: Serve, open_stream: Callable[[AvatarServer], EventStream]
) -> None:
    clock_s = [100.0]
    track = [(0.5, 0.0, 0.0, 0.0, 0.0)]
    bus = AvatarBus(viseme_track=lambda audio, rate, fps: track, clock=lambda: clock_s[0])
    server = serve(bus=bus)
    bus.state("speaking")
    bus.speak(np.zeros(32_000, dtype=np.float32), 16_000, "happy")  # two seconds
    clock_s[0] += 0.5

    stream = open_stream(server)

    stream.skip_greeting()
    assert stream.next_event() == {
        "type": "speak",
        "emotion": "happy",
        "fps": 60,
        "duration": 2.0,
        "visemes": [[0.5, 0.0, 0.0, 0.0, 0.0]],
        "elapsed": 0.5,
    }


# --- the Host header (DNS rebinding) ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/avatar.js", "/model.vrm", "/events"])
@pytest.mark.parametrize("host", ["rebind.attacker.example:{port}", "evil.example", "127.0.0.1"])
def test_a_request_for_another_host_name_is_refused_before_anything_is_served(
    serve: Serve, path: str, host: str
) -> None:
    # A page whose name an attacker re-points at 127.0.0.1 is same-origin with this server
    # but still sends its own name as Host; refusing it keeps the VRM and the events private.
    bus = AvatarBus()
    server = serve(bus=bus)

    status, headers, body = request(server, path, host=host.format(port=address(server)[1]))

    assert status == 421
    assert headers.get_content_type() == "text/plain"
    assert MODEL_BYTES[:64] not in body
    assert b"data:" not in body
    assert not bus.wait_for_viewer(timeout=0)


@pytest.mark.parametrize("method", ["POST", "OPTIONS"])
def test_other_methods_for_another_host_name_are_refused_too(serve: Serve, method: str) -> None:
    status, _, _ = request(serve(), "/", method=method, host="rebind.attacker.example")

    assert status == 421


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}"])
def test_the_server_s_own_address_and_localhost_are_served(serve: Serve, host: str) -> None:
    server = serve()

    status, _, body = request(server, "/model.vrm", host=host.format(port=address(server)[1]))

    assert status == 200
    assert body == MODEL_BYTES


def test_a_request_without_a_host_header_is_refused(serve: Serve) -> None:
    server = serve()

    with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
        client.sendall(b"GET /model.vrm HTTP/1.0\r\n\r\n")
        reply = b""
        while chunk := client.recv(65536):
            reply += chunk

    assert reply.startswith(b"HTTP/1.0 421 ")
    assert MODEL_BYTES[:64] not in reply
