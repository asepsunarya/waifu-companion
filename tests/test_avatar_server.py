"""AvatarServer: the viewer page, the models, the event stream, and interactions on 127.0.0.1.

Every server binds port 0 (an ephemeral port) and is stopped by the `serve`
fixture; the web root, the .vrm, the Live2D model folder, and the Cubism Core
are small fake files under tmp_path, never the real models or Live2D's Core.
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
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import numpy as np
import pytest

from waifu.avatar.bus import AvatarBus
from waifu.avatar.server import AvatarError, AvatarServer, file_under
from waifu.card import Card
from waifu.config import AvatarConfig, AvatarInteractionsConfig

CLIENT_TIMEOUT_S = 5.0
MODEL_BYTES = bytes(range(256)) * 1200  # 300 KiB: more than one read chunk
SECRET = "the private corpus"
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
LIVE2D_FILES = {
    "Cyrene.model3.json": b'{"Version": 3, "FileReferences": {"Moc": "Cyrene.moc3"}}\n',
    "Cyrene.moc3": b"MOC3" + bytes(range(256)) * 4,
    "Cyrene.physics3.json": b'{"Version": 3}\n',
    "Cyrene.4096/texture_00.png": b"\x89PNG\r\n\x1a\n" + bytes(64),
}
CORE_BYTES = b"/* a stand-in, not Live2D's Core */\nwindow.Live2DCubismCore = {};\n" * 2000
CORE_URL = "/live2d/live2dcubismcore.min.js"
KINDS = ("head_pat", "body_touch", "double_click", "welcome_back")

Serve = Callable[..., AvatarServer]


def make_config(model: Path | None, port: int = 0) -> AvatarConfig:
    # model_copy skips validation: the config refuses ports below 1024, the tests need 0.
    return AvatarConfig(model=model).model_copy(update={"port": port})


def make_live2d_config(model: Path | None, core: Path | None, **settings: Any) -> AvatarConfig:
    config = AvatarConfig(engine="live2d", model=model, live2d_core=core, **settings)
    return config.model_copy(update={"port": 0})


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


@pytest.fixture
def live2d_model(tmp_path: Path, web_root: Path) -> Path:
    """A fake Live2D model folder (with a file the engine never needs) and a look-alike sibling."""
    folder = tmp_path / "live2d" / "Cyrene"
    for name, content in LIVE2D_FILES.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
    (folder / "notes.txt").write_bytes(SECRET.encode())
    (tmp_path / "live2d" / "Cyrene-other").mkdir()
    (tmp_path / "live2d" / "Cyrene-other" / "secret.json").write_bytes(SECRET.encode())
    return folder / "Cyrene.model3.json"


@pytest.fixture
def core_file(tmp_path: Path) -> Path:
    path = tmp_path / "cubism" / "live2dcubismcore.min.js"
    path.parent.mkdir()
    path.write_bytes(CORE_BYTES)
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
        config: AvatarConfig | None = None,
        ping_interval_s: float | None = None,
        on_interact: Callable[[str], None] | None = None,
        open_link: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> AvatarServer:
        server = AvatarServer(
            config or make_config(model),
            bus or AvatarBus(),
            web_root=web_root,
            on_interact=on_interact,
            open_link=open_link or Opened(),
            clock=clock,
        )
        started.append(server)
        if ping_interval_s is not None:
            server.ping_interval_s = ping_interval_s
        server.start()
        return server

    yield start
    for server in started:
        server.stop()


@pytest.fixture
def serve_live2d(serve: Serve, live2d_model: Path, core_file: Path) -> Serve:
    """Starts a server for engine live2d with the fake model folder and Core."""

    def start(**kwargs: Any) -> AvatarServer:
        return serve(config=make_live2d_config(live2d_model, core_file), **kwargs)

    return start


def request(
    server: AvatarServer,
    path: str,
    method: str = "GET",
    *,
    host: str | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """Sends `path` exactly as given, unnormalised; returns status, headers, and body.

    `host` replaces the Host header http.client sends (`127.0.0.1:<port>`).
    """
    sent = dict(headers or {})
    if host is not None:
        sent["Host"] = host
    connection = http.client.HTTPConnection(*address(server), timeout=CLIENT_TIMEOUT_S)
    try:
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def raw_request(server: AvatarServer, data: bytes, *, end_of_request: bool = True) -> bytes:
    """Sends `data` as it is and returns the whole reply (the server closes the connection).

    `end_of_request=False` keeps the sending side open, so a server that waited for more
    of the request would time this out.
    """
    with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
        client.sendall(data)
        if end_of_request:
            client.shutdown(socket.SHUT_WR)
        reply = b""
        while chunk := client.recv(65536):
            reply += chunk
    return reply


def own_origin(server: AvatarServer) -> str:
    return f"http://127.0.0.1:{address(server)[1]}"


def interact(
    server: AvatarServer,
    payload: Mapping[str, Any] | bytes,
    *,
    headers: Mapping[str, str | None] | None = None,
    host: str | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """POSTs /interact as the page does (own Origin, JSON); `headers` overrides, None removes."""
    sent: dict[str, str] = {"Origin": own_origin(server), "Content-Type": "application/json"}
    for name, value in (headers or {}).items():
        if value is None:
            sent.pop(name, None)
        else:
            sent[name] = value
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return request(server, "/interact", "POST", host=host, headers=sent, body=body)


def valid(server: AvatarServer, kind: str = "head_pat") -> dict[str, str]:
    return {"token": server.token, "kind": kind}


class Interactions:
    """Stands in for InteractionHandler.handle: records each kind, or fails like a broken one."""

    def __init__(self, error: Exception | None = None) -> None:
        self.kinds: list[str] = []
        self.error = error

    def __call__(self, kind: str) -> None:
        self.kinds.append(kind)
        if self.error is not None:
            raise self.error


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


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


@pytest.mark.parametrize(
    "reference",
    [
        "//attacker.invalid/share/x.moc3",
        "\\\\attacker.invalid\\share\\x.moc3",
        "/x.moc3",
        "C:/Windows/win.ini",
        "D:x.moc3",
        "https://attacker.invalid/x.moc3",
        "sub\\x.moc3",
        "x.moc3\0.png",
        "",
    ],
)
def test_file_under_refuses_anything_but_a_relative_path_before_resolving_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    (tmp_path / "x.moc3").write_bytes(b"MOC3")

    def no_lookup(target: Any, *args: Any, **kwargs: Any) -> str:
        raise AssertionError(f"{os.fspath(target)!r} was resolved")

    monkeypatch.setattr(os.path, "realpath", no_lookup)

    assert file_under(tmp_path, reference) is None


def test_file_under_finds_a_file_by_its_relative_path_and_nothing_outside(tmp_path: Path) -> None:
    root = tmp_path / "model"
    (root / "Cyrene.4096").mkdir(parents=True)
    (root / "Cyrene.4096" / "texture_00.png").write_bytes(b"\x89PNG")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG")
    root = root.resolve()

    assert file_under(root, "Cyrene.4096/texture_00.png") == root / "Cyrene.4096" / "texture_00.png"
    assert file_under(root, "Cyrene.4096/../Cyrene.4096/texture_00.png") is not None
    assert file_under(root, "../outside.png") is None
    assert file_under(root, "Cyrene.4096") is None
    assert file_under(root, "missing.png") is None


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


FOREIGN_HOSTS = [
    "rebind.attacker.example:{port}",
    "evil.example",
    "127.0.0.1",
    "localhost",
    "localhost.:{port}",
    "127.0.0.1:{port}.evil.example",
    "127.0.0.1:{port}0",
    "[::1]:{port}",
    "",
]


@pytest.mark.parametrize("path", ["/", "/avatar.js", "/model.vrm", "/events", "/config.json"])
@pytest.mark.parametrize("host", FOREIGN_HOSTS)
def test_a_request_for_another_host_name_is_refused_before_anything_is_served(
    serve: Serve, path: str, host: str
) -> None:
    # A page whose name an attacker re-points at 127.0.0.1 is same-origin with this server
    # but still sends its own name as Host; refusing it keeps the model, the events, and
    # the interaction token private.
    bus = AvatarBus()
    server = serve(bus=bus)

    status, headers, body = request(server, path, host=host.format(port=address(server)[1]))

    assert status == 403
    assert headers.get_content_type() == "text/plain"
    assert MODEL_BYTES[:64] not in body
    assert b"data:" not in body
    assert server.token.encode() not in body
    assert not bus.wait_for_viewer(timeout=0)


@pytest.mark.parametrize(
    "path", ["/model/Cyrene.model3.json", "/model/Cyrene.moc3", CORE_URL, "/config.json"]
)
def test_live2d_files_for_another_host_name_are_refused(serve_live2d: Serve, path: str) -> None:
    server = serve_live2d()

    status, _, body = request(server, path, host=f"evil.example:{address(server)[1]}")

    assert status == 403
    assert LIVE2D_FILES["Cyrene.moc3"][:64] not in body
    assert CORE_BYTES[:64] not in body
    assert server.token.encode() not in body


@pytest.mark.parametrize("method", ["POST", "OPTIONS"])
def test_other_methods_for_another_host_name_are_refused_too(serve: Serve, method: str) -> None:
    status, _, _ = request(serve(), "/", method=method, host="rebind.attacker.example")

    assert status == 403


def test_an_interaction_for_another_host_name_is_refused_even_with_the_token(
    serve: Serve,
) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    rebound = f"rebind.attacker.example:{address(server)[1]}"

    status, _, _ = interact(
        server, valid(server), host=rebound, headers={"Origin": f"http://{rebound}"}
    )

    assert status == 403
    assert interactions.kinds == []


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}"])
def test_the_server_s_own_address_and_localhost_are_served(serve: Serve, host: str) -> None:
    server = serve()

    status, _, body = request(server, "/model.vrm", host=host.format(port=address(server)[1]))

    assert status == 200
    assert body == MODEL_BYTES


PROTECTING_HEADERS = {
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/avatar.js",
        "/config.json",
        "/model/Cyrene.model3.json",
        "/model/Cyrene.4096/texture_00.png",
        CORE_URL,
        "/missing.js",
    ],
)
def test_no_other_website_can_embed_or_frame_what_the_server_serves(
    serve_live2d: Serve, path: str
) -> None:
    # The Host check stops DNS rebinding, but a page elsewhere can still embed
    # http://127.0.0.1:<port>/... (its requests name this server as Host): the Core as a
    # script, the textures as images, the page in a hidden frame that turns clicks into pats.
    _, headers, _ = request(serve_live2d(), path)

    assert {name: headers[name] for name in PROTECTING_HEADERS} == PROTECTING_HEADERS


def test_refusals_and_the_event_stream_carry_the_protecting_headers_too(serve: Serve) -> None:
    server = serve()
    replies = [
        request(server, "/", host="attacker.invalid")[1],
        request(server, "/", "DELETE")[1],
        interact(server, valid(server), headers={"Origin": "https://attacker.invalid"})[1],
    ]
    stream = EventStream(server)
    try:
        replies.append(stream.response.headers)
    finally:
        stream.close()

    for headers in replies:
        assert {name: headers[name] for name in PROTECTING_HEADERS} == PROTECTING_HEADERS


def test_a_request_without_a_host_header_is_refused(serve: Serve) -> None:
    reply = raw_request(serve(), b"GET /model.vrm HTTP/1.0\r\n\r\n")

    assert reply.startswith(b"HTTP/1.0 403 ")
    assert MODEL_BYTES[:64] not in reply


def test_an_http_0_9_request_without_headers_is_refused(serve: Serve) -> None:
    reply = raw_request(serve(), b"GET /model.vrm\r\n")

    assert MODEL_BYTES[:64] not in reply


# --- /config.json and hello -------------------------------------------------------------------


def test_config_json_describes_the_vrm_viewer_with_the_token(serve: Serve) -> None:
    server = serve(bus=AvatarBus(name="Cyrene"))

    status, headers, body = request(server, "/config.json")

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Length"] == str(len(body))
    assert json.loads(body) == {
        **{key: value for key, value in HELLO.items() if key != "type"},
        "token": server.token,
    }


def test_config_json_describes_the_live2d_viewer_from_the_config(
    serve: Serve, live2d_model: Path, core_file: Path
) -> None:
    config = make_live2d_config(
        live2d_model,
        core_file,
        framing="upper",
        credit="Cyrene © miHoYo · model: MomokaSono",
        interactions=AvatarInteractionsConfig(body_touch=False, welcome_back_after_min=0),
    )
    server = serve(config=config, bus=AvatarBus(name="Cyrene"))

    status, _, body = request(server, "/config.json")

    assert status == 200
    assert json.loads(body) == {
        "engine": "live2d",
        "model": "/model/Cyrene.model3.json",
        "core": CORE_URL,
        "name": "Cyrene",
        "framing": "upper",
        "credit": "Cyrene © miHoYo · model: MomokaSono",
        "interactions": {
            **DEFAULT_INTERACTIONS,
            "body_touch": False,
            "welcome_back_after_min": 0,
        },
        "token": server.token,
    }


def test_hello_carries_the_same_settings_as_config_json_except_the_token(
    serve_live2d: Serve, open_stream: Callable[[AvatarServer], EventStream]
) -> None:
    server = serve_live2d(bus=AvatarBus(name="Cyrene", framing="upper"))
    config = json.loads(request(server, "/config.json")[2])

    stream = open_stream(server)
    stream.next_block()
    hello = stream.next_event()

    assert hello == {"type": "hello", **{k: v for k, v in config.items() if k != "token"}}
    assert hello["engine"] == "live2d"
    assert server.token not in json.dumps(hello)


def test_every_server_makes_its_own_unguessable_read_only_token(serve: Serve) -> None:
    first, second = serve(), serve()

    assert first.token != second.token
    assert len(first.token) == 32  # 24 random bytes, URL-safe base64
    assert set(first.token) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    with pytest.raises(AttributeError):
        first.token = "chosen"  # type: ignore[misc]


# --- the Live2D model folder and the Cubism Core ----------------------------------------------


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("Cyrene.model3.json", "application/json"),
        ("Cyrene.physics3.json", "application/json"),
        ("Cyrene.moc3", "application/octet-stream"),
        ("Cyrene.4096/texture_00.png", "image/png"),
    ],
)
def test_live2d_model_files_are_served_from_the_model_folder(
    serve_live2d: Serve, name: str, content_type: str
) -> None:
    status, headers, body = request(serve_live2d(), f"/model/{name}")

    assert status == 200
    assert headers.get_content_type() == content_type
    assert headers["Content-Length"] == str(len(LIVE2D_FILES[name]))
    assert body == LIVE2D_FILES[name]


def test_the_model_url_from_config_json_fetches_the_model3_file(
    serve: Serve, tmp_path: Path, core_file: Path
) -> None:
    model = tmp_path / "キュレネ" / "Cyrene 2025#1.model3.json"
    model.parent.mkdir()
    model.write_bytes(LIVE2D_FILES["Cyrene.model3.json"])
    server = serve(config=make_live2d_config(model, core_file))
    model_url = json.loads(request(server, "/config.json")[2])["model"]

    status, _, body = request(server, model_url)

    assert status == 200
    assert body == LIVE2D_FILES["Cyrene.model3.json"]


@pytest.mark.parametrize(
    "path",
    [
        "/model/",
        "/model",
        "/model/missing.png",
        "/model/Cyrene.4096",
        "/model/Cyrene.4096/",
        "/model/notes.txt",
        "/model/../../secret.txt",
        "/model/%2e%2e/%2e%2e/secret.txt",
        "/model/.%2e/.%2e/secret.txt",
        "/model/%2E%2E%2F%2E%2E%2Fsecret.txt",
        "/model/%252e%252e/%252e%252e/secret.txt",
        "/model/Cyrene.4096/../../../secret.txt",
        "/model/../Cyrene-other/secret.json",
        "/model/..%2FCyrene-other%2Fsecret.json",
        "/model/..\\..\\secret.txt",
        "/model/..%5C..%5Csecret.txt",
        "/model/Cyrene.4096%5C..%5C..%5C..%5Csecret.txt",
        "/model/C:%5CWindows%5Cwin.ini",
        "/model/C:/Windows/win.ini",
        "/model/c%3A/Windows/win.ini",
        "/model/Cyrene.model3.json%00.png",
        "/model/Cyrene.model3.json::$DATA",
        "/model/Cyrene.model3.json%3A%3A%24DATA",
        "/model/CON",
        "/model/NUL.png",
        "/model//../../secret.txt",
    ],
)
def test_model_paths_outside_the_folder_or_not_model_files_are_404s(
    serve_live2d: Serve, path: str
) -> None:
    status, headers, body = request(serve_live2d(), path)

    assert status == 404
    assert headers.get_content_type() == "text/plain"
    assert SECRET.encode() not in body


def test_an_absolute_path_under_model_is_a_404(serve_live2d: Serve, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    server = serve_live2d()

    for path in (secret.as_posix(), quote(str(secret)), quote(secret.as_posix())):
        status, _, body = request(server, "/model/" + path.lstrip("/"))
        assert status == 404, path
        assert SECRET.encode() not in body


@pytest.mark.parametrize(
    "path",
    [
        "/model/%5C%5Cattacker.invalid%5Cshare%5Cx.png",
        "/model/\\\\attacker.invalid\\share\\x.png",
        "/model///attacker.invalid/share/x.png",
    ],
)
def test_a_unc_model_path_is_refused_before_it_is_ever_resolved(
    serve_live2d: Serve, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    real_realpath = os.path.realpath
    unc_lookups: list[str] = []

    def guarded_realpath(target: Any, *args: Any, **kwargs: Any) -> str:
        text = os.fspath(target)
        if text.startswith(("\\\\", "//")):
            unc_lookups.append(text)
            return text
        return real_realpath(target, *args, **kwargs)

    server = serve_live2d()
    monkeypatch.setattr(os.path, "realpath", guarded_realpath)

    status, _, _ = request(server, path)

    assert status == 404
    assert unc_lookups == []


@pytest.mark.parametrize("name", ["tsukuyomi_a.vrm", "Cyrene.model3.json", "texture_00.png"])
def test_the_model_folder_is_never_served_for_engine_vrm(
    serve: Serve, model_file: Path, name: str
) -> None:
    for neighbour in ("Cyrene.model3.json", "texture_00.png"):
        (model_file.parent / neighbour).write_bytes(SECRET.encode())

    status, _, body = request(serve(), f"/model/{name}")

    assert status == 404
    assert MODEL_BYTES[:64] not in body
    assert SECRET.encode() not in body


def test_the_vrm_route_is_a_404_for_engine_live2d(serve_live2d: Serve) -> None:
    status, _, body = request(serve_live2d(), "/model.vrm")

    assert status == 404
    assert LIVE2D_FILES["Cyrene.model3.json"] not in body


def test_the_core_is_streamed_unmodified_with_its_length(serve_live2d: Serve) -> None:
    status, headers, body = request(serve_live2d(), CORE_URL)

    assert status == 200
    assert headers.get_content_type() == "text/javascript"
    assert headers["Content-Length"] == str(len(CORE_BYTES))
    assert body == CORE_BYTES


@pytest.mark.parametrize(
    "path",
    [
        "/live2d/",
        "/live2d/live2dcubismcore.js",
        "/live2d/LIVE2DCUBISMCORE.MIN.JS",
        "/live2d//live2dcubismcore.min.js",
        "/live2d/../live2d/live2dcubismcore.min.js",
        "/live2d/%6Cive2dcubismcore.min.js",
        "/live2d/live2dcubismcore.min.js/",
    ],
)
def test_only_the_exact_core_path_serves_the_core(serve_live2d: Serve, path: str) -> None:
    status, _, body = request(serve_live2d(), path)

    assert status == 404
    assert CORE_BYTES[:64] not in body


def test_the_core_is_never_served_for_engine_vrm(
    serve: Serve, model_file: Path, core_file: Path
) -> None:
    config = AvatarConfig(model=model_file, live2d_core=core_file).model_copy(update={"port": 0})

    status, _, body = request(serve(config=config), CORE_URL)

    assert status == 404
    assert CORE_BYTES[:64] not in body


def test_a_missing_core_or_model_folder_is_a_404(serve: Serve, tmp_path: Path) -> None:
    gone = make_live2d_config(tmp_path / "gone" / "Cyrene.model3.json", tmp_path / "gone.js")
    server = serve(config=gone)

    assert request(server, CORE_URL)[0] == 404
    assert request(server, "/model/Cyrene.model3.json")[0] == 404


def test_live2d_without_a_model_or_core_serves_neither(serve: Serve) -> None:
    server = serve(config=make_live2d_config(None, None))

    assert request(server, CORE_URL)[0] == 404
    assert request(server, "/model/Cyrene.model3.json")[0] == 404
    assert json.loads(request(server, "/config.json")[2])["model"] is None


# --- POST /interact ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_valid_interaction_is_accepted_and_handed_on_once(serve: Serve, kind: str) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    status, headers, body = interact(server, valid(server, kind))

    assert status == 202
    assert headers.get_content_type() == "application/json"
    assert json.loads(body) == {"ok": True}
    assert interactions.kinds == [kind]


@pytest.mark.parametrize(
    "content_type",
    ["application/json; charset=utf-8", "application/json;charset=UTF-8", "Application/JSON"],
)
def test_json_with_a_utf8_charset_is_accepted(serve: Serve, content_type: str) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    status, _, _ = interact(server, valid(server), headers={"Content-Type": content_type})

    assert status == 202
    assert interactions.kinds == ["head_pat"]


def test_the_localhost_origin_is_accepted(serve: Serve) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    port = address(server)[1]

    status, _, _ = interact(
        server,
        valid(server),
        host=f"localhost:{port}",
        headers={"Origin": f"http://localhost:{port}"},
    )

    assert status == 202
    assert interactions.kinds == ["head_pat"]


def test_an_interaction_without_a_handler_is_still_accepted(serve: Serve) -> None:
    server = serve()

    status, _, body = interact(server, valid(server))

    assert status == 202
    assert json.loads(body) == {"ok": True}


def test_a_failing_handler_is_logged_with_its_traceback_and_still_accepted(
    serve: Serve, caplog: pytest.LogCaptureFixture
) -> None:
    interactions = Interactions(error=RuntimeError("speaker unplugged"))
    server = serve(on_interact=interactions)

    with caplog.at_level(logging.ERROR, logger="waifu.avatar.server"):
        status, _, _ = interact(server, valid(server, "body_touch"))

    assert status == 202
    assert interactions.kinds == ["body_touch"]
    failures = [r for r in caplog.records if r.exc_info is not None]
    assert len(failures) == 1
    assert "body_touch" in failures[0].getMessage()


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "http://evil.example",
        "null",
        "http://127.0.0.1",
        "http://127.0.0.1:1",
        "https://127.0.0.1:{port}",
        "http://127.0.0.1:{port}/",
        "http://127.0.0.1:{port}.evil.example",
        "http://LOCALHOST:{port}",
        "http://[::1]:{port}",
        "",
    ],
)
def test_a_cross_origin_interaction_is_forbidden(serve: Serve, origin: str | None) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    sent = None if origin is None else origin.format(port=address(server)[1])

    status, _, _ = interact(server, valid(server), headers={"Origin": sent})

    assert status == 403
    assert interactions.kinds == []


def test_two_origin_headers_are_forbidden(serve: Serve) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    body = json.dumps(valid(server)).encode()
    port = address(server)[1]

    reply = raw_request(
        server,
        b"POST /interact HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
        b"Origin: http://127.0.0.1:%d\r\nOrigin: http://evil.example\r\n"
        b"Content-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
        % (port, port, len(body), body),
    )

    assert reply.startswith(b"HTTP/1.0 403 ")
    assert interactions.kinds == []


@pytest.mark.parametrize(
    "token",
    ["", "wrong", "{token}x", "{token_prefix}", "{token_upper}", " {token}", "キュレネ", "\ud800"],
)
def test_a_wrong_token_is_forbidden(serve: Serve, token: str) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    sent = token.format(
        token=server.token, token_prefix=server.token[:-1], token_upper=server.token.upper()
    )

    status, _, _ = interact(server, {"token": sent, "kind": "head_pat"})

    assert status == 403
    assert interactions.kinds == []


@pytest.mark.parametrize(
    "kind", ["", "pat", "HEAD_PAT", "head_pat ", "head", "body", "listen", "\ud800"]
)
def test_an_unknown_kind_is_a_bad_request(serve: Serve, kind: str) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    status, _, _ = interact(server, {"token": server.token, "kind": kind})

    assert status == 400
    assert interactions.kinds == []


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"head_pat",
        b"[]",
        b'"head_pat"',
        b"null",
        b'{"token": "{token}"}',
        b'{"kind": "head_pat"}',
        b'{"token": "{token}", "kind": "head_pat", "extra": 1}',
        b'{"token": "{token}", "kind": ["head_pat"]}',
        b'{"token": "{token}", "kind": null}',
        b'{"token": 1, "kind": "head_pat"}',
        b'{"token": "{token}", "kind": "head_pat"',
        b'{"token": "{token}", "kind": "head\xff_pat"}',
        b"[" * 4000,
        b"[" * 2000 + b"]" * 2000,
        b'{"a":' * 500 + b"1" + b"}" * 500,
    ],
)
def test_a_malformed_body_is_a_bad_request(serve: Serve, body: bytes) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    status, _, _ = interact(server, body.replace(b"{token}", server.token.encode()))

    assert status == 400
    assert interactions.kinds == []


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "text/plain",
        "text/plain; charset=utf-8",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "application/json; charset=latin-1",
        "application/jsonx",
        "application/json-patch+json",
    ],
)
def test_a_body_that_is_not_declared_json_is_a_bad_request(
    serve: Serve, content_type: str | None
) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    status, _, _ = interact(server, valid(server), headers={"Content-Type": content_type})

    assert status == 400
    assert interactions.kinds == []


def post_head(server: AvatarServer, length_headers: bytes) -> bytes:
    """The head of a page-like POST /interact with the given length headers."""
    port = address(server)[1]
    return (
        b"POST /interact HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://127.0.0.1:%d\r\n"
        b"Content-Type: application/json\r\n%s\r\n" % (port, port, length_headers)
    )


def test_an_interaction_without_a_content_length_is_refused(serve: Serve) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    reply = raw_request(server, post_head(server, b"Transfer-Encoding: chunked\r\n"))

    assert reply.startswith(b"HTTP/1.0 411 ")
    assert interactions.kinds == []


def test_an_oversized_interaction_is_refused_without_reading_its_body(serve: Serve) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    # The body never comes: a server that tried to read it would time this request out.
    reply = raw_request(
        server, post_head(server, b"Content-Length: 4097\r\n"), end_of_request=False
    )

    assert reply.startswith(b"HTTP/1.0 413 ")
    assert interactions.kinds == []


@pytest.mark.parametrize("length", [b"4097", b"9" * 5000, b"0" * 5000 + b"4097"])
def test_a_content_length_over_four_kilobytes_is_too_large(serve: Serve, length: bytes) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    reply = raw_request(
        server, post_head(server, b"Content-Length: %s\r\n" % length), end_of_request=False
    )

    assert reply.startswith(b"HTTP/1.0 413 ")
    assert interactions.kinds == []


def test_a_body_of_exactly_four_kilobytes_is_accepted(serve: Serve) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    body = json.dumps(valid(server)).encode()
    body += b" " * (4096 - len(body))

    status, _, _ = interact(server, body)

    assert status == 202
    assert interactions.kinds == ["head_pat"]


@pytest.mark.parametrize(
    "length_headers",
    [
        b"Content-Length: abc\r\n",
        b"Content-Length: -1\r\n",
        b"Content-Length: +40\r\n",
        b"Content-Length: 4_0\r\n",
        b"Content-Length: \xc2\xb2\r\n",
        b"Content-Length: 40\r\nContent-Length: 40\r\n",
    ],
)
def test_an_invalid_content_length_is_a_bad_request(serve: Serve, length_headers: bytes) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    reply = raw_request(server, post_head(server, length_headers))

    assert reply.startswith(b"HTTP/1.0 400 ")
    assert interactions.kinds == []


def test_a_body_shorter_than_its_content_length_is_a_bad_request(serve: Serve) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)
    body = json.dumps(valid(server)).encode()

    reply = raw_request(
        server, post_head(server, b"Content-Length: %d\r\n" % (len(body) + 10)) + body
    )

    assert reply.startswith(b"HTTP/1.0 400 ")
    assert interactions.kinds == []


def test_more_than_ten_interactions_in_a_second_are_too_many(serve: Serve) -> None:
    clock = FakeClock()
    interactions = Interactions()
    server = serve(on_interact=interactions, clock=clock)
    port = address(server)[1]
    localhost = {"Origin": f"http://localhost:{port}"}

    accepted = [interact(server, valid(server))[0] for _ in range(5)]
    accepted += [
        interact(server, valid(server), host=f"localhost:{port}", headers=localhost)[0]
        for _ in range(5)
    ]
    clock.now += 0.999
    status, headers, _ = interact(server, valid(server))

    assert accepted == [202] * 10
    assert status == 429
    assert headers["Retry-After"] == "1"
    assert len(interactions.kinds) == 10


def test_the_interaction_limit_is_a_rolling_second(serve: Serve) -> None:
    clock = FakeClock()
    interactions = Interactions()
    server = serve(on_interact=interactions, clock=clock)
    for step in range(10):
        clock.now = 100.0 + step * 0.1
        assert interact(server, valid(server))[0] == 202

    clock.now = 100.95
    assert interact(server, valid(server))[0] == 429
    clock.now = 101.0  # the first one is a second old
    assert interact(server, valid(server))[0] == 202
    assert interact(server, valid(server))[0] == 429
    clock.now = 102.5
    assert [interact(server, valid(server))[0] for _ in range(11)] == [202] * 10 + [429]
    assert len(interactions.kinds) == 21


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "OPTIONS", "HEAD"])
def test_interact_takes_only_post(serve: Serve, method: str) -> None:
    interactions = Interactions()
    server = serve(on_interact=interactions)

    status, headers, _ = request(server, "/interact", method=method)

    assert status == 405
    assert headers["Allow"] == "POST"
    assert interactions.kinds == []


def test_the_token_is_never_logged(
    serve: Serve, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    server = serve(on_interact=Interactions(error=RuntimeError("boom")))

    with caplog.at_level(logging.DEBUG):
        request(server, "/config.json")
        interact(server, valid(server))
        interact(server, {"token": server.token + "x", "kind": "head_pat"})
        interact(server, {"token": server.token, "kind": "kick"})
        interact(server, valid(server), headers={"Origin": "http://evil.example"})

    logged = "\n".join(r.getMessage() for r in caplog.records) + caplog.text
    assert server.token not in logged
    assert server.token not in capsys.readouterr().err


def test_a_refused_host_s_body_is_read_so_the_refusal_always_arrives(serve: Serve) -> None:
    # Closing a socket with unread data resets it on Windows, which can throw away the 403
    # the client has not read yet; repeated to catch that race.
    interactions = Interactions()
    server = serve(on_interact=interactions)
    rebound = f"rebind.attacker.example:{address(server)[1]}"

    for _ in range(200):
        status, _, _ = interact(
            server, valid(server), host=rebound, headers={"Origin": f"http://{rebound}"}
        )
        assert status == 403
    assert interactions.kinds == []


def test_a_refused_method_s_body_is_read_so_the_refusal_always_arrives(serve: Serve) -> None:
    server = serve()
    body = json.dumps({"kind": "head_pat"}).encode()

    for _ in range(200):
        status, _, _ = request(
            server, "/config.json", "POST", headers={"Content-Type": "application/json"}, body=body
        )
        assert status == 405


# --- POST /open-link: a link on the card, opened on Master's click (v1.55) ---------------------

LINK = "https://example.com/docs?a=1"
HTTP_LINK = "http://example.com/plain"
LINK_CARD = Card("Tautan", f"- Docs: {LINK}.\n- Lama: {HTTP_LINK}")


class Opened:
    """Stands in for the default browser's opener: records each link, or fails like Windows."""

    def __init__(self, error: Exception | None = None) -> None:
        self.urls: list[str] = []
        self.error = error

    def __call__(self, url: str) -> None:
        self.urls.append(url)
        if self.error is not None:
            raise self.error


def open_link(
    server: AvatarServer,
    payload: Mapping[str, Any] | bytes,
    *,
    headers: Mapping[str, str | None] | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """POSTs /open-link as the page does (own Origin, JSON); `headers` overrides, None removes."""
    sent: dict[str, str] = {"Origin": own_origin(server), "Content-Type": "application/json"}
    for name, value in (headers or {}).items():
        if value is None:
            sent.pop(name, None)
        else:
            sent[name] = value
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return request(server, "/open-link", "POST", headers=sent, body=body)


def showing(card: Card | None = LINK_CARD) -> AvatarBus:
    bus = AvatarBus()
    if card is not None:
        bus.card(card, 120)
    return bus


@pytest.mark.parametrize("url", [LINK, HTTP_LINK])
def test_a_link_on_the_card_shown_is_opened_once(serve: Serve, url: str) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)

    status, headers, body = open_link(server, {"token": server.token, "url": url})

    assert status == 202
    assert headers.get_content_type() == "application/json"
    assert json.loads(body) == {"ok": True}
    assert opened.urls == [url]


def test_a_link_from_a_card_already_hidden_by_its_time_still_opens(serve: Serve) -> None:
    clock = FakeClock()
    bus = AvatarBus(clock=clock)
    bus.card(LINK_CARD, 120)
    clock.now += 600
    opened = Opened()
    server = serve(bus=bus, open_link=opened)

    assert open_link(server, {"token": server.token, "url": LINK})[0] == 202
    assert opened.urls == [LINK]


@pytest.mark.parametrize(
    "card",
    [None, Card("Lain", "- https://example.org/other")],
    ids=["no_card", "another_card"],
)
def test_a_link_not_on_the_card_shown_is_refused_and_never_opened(
    serve: Serve, card: Card | None
) -> None:
    opened = Opened()
    server = serve(bus=showing(card), open_link=opened)

    status, _, body = open_link(server, {"token": server.token, "url": LINK})

    assert status == 404
    assert body.decode("utf-8").strip() == "Tautan itu tidak ada di kartu."
    assert opened.urls == []


def test_a_hidden_card_s_link_is_refused(serve: Serve) -> None:
    bus = showing()
    bus.card(None)
    opened = Opened()
    server = serve(bus=bus, open_link=opened)

    assert open_link(server, {"token": server.token, "url": LINK})[0] == 404
    assert opened.urls == []


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///C:/Windows/System32/calc.exe",
        "ftp://example.com/file",
        "https://user@evil.example/",
        "https://example.com:8080/",
        "https://evil.example\\.example.com/",
        "https://example.com/a b",
        "https://example.com/\u202e",
        "https:///nohost",
    ],
)
def test_only_a_plain_http_or_https_link_is_opened_even_when_the_card_holds_it(
    serve: Serve, url: str
) -> None:
    opened = Opened()
    server = serve(bus=showing(Card("Aneh", f"- {url}")), open_link=opened)

    status, _, _ = open_link(server, {"token": server.token, "url": url})

    assert status == 404
    assert opened.urls == []


def test_a_link_from_another_origin_is_refused(serve: Serve) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)

    status, _, _ = open_link(
        server, {"token": server.token, "url": LINK}, headers={"Origin": "http://evil.example"}
    )

    assert status == 403
    assert opened.urls == []


def test_a_link_with_the_wrong_token_is_refused(serve: Serve) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)

    status, _, _ = open_link(server, {"token": server.token + "x", "url": LINK})

    assert status == 403
    assert opened.urls == []


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[]",
        {"url": LINK},
        {"token": "t"},
        {"token": "t", "url": LINK, "kind": "head_pat"},
        {"token": "t", "url": 5},
        {"token": "t", "kind": "head_pat"},
    ],
)
def test_a_malformed_link_request_is_a_bad_request(serve: Serve, payload: Any) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)
    if isinstance(payload, dict) and "token" in payload:
        payload = {**payload, "token": server.token}

    assert open_link(server, payload)[0] == 400
    assert opened.urls == []


def test_a_link_request_without_json_is_a_bad_request(serve: Serve) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)

    status, _, _ = open_link(
        server, {"token": server.token, "url": LINK}, headers={"Content-Type": "text/plain"}
    )

    assert status == 400
    assert opened.urls == []


def test_a_link_windows_cannot_open_is_said_without_the_url_in_the_log(
    serve: Serve, caplog: pytest.LogCaptureFixture
) -> None:
    server = serve(bus=showing(), open_link=Opened(error=OSError(2, "not found", LINK)))

    with caplog.at_level(logging.DEBUG):
        status, _, body = open_link(server, {"token": server.token, "url": LINK})

    assert status == 500
    assert body.decode("utf-8").strip() == "Tautan tidak bisa dibuka."
    assert "example.com" not in caplog.text


def test_a_link_is_never_logged(serve: Serve, caplog: pytest.LogCaptureFixture) -> None:
    server = serve(bus=showing(), open_link=Opened())

    with caplog.at_level(logging.DEBUG):
        open_link(server, {"token": server.token, "url": LINK})
        open_link(server, {"token": server.token, "url": "https://example.com/elsewhere"})
        open_link(server, {"token": server.token + "x", "url": LINK})

    assert "example.com" not in caplog.text
    assert "Opened a link from the card" in caplog.text


def test_links_count_towards_the_interaction_limit(serve: Serve) -> None:
    clock = FakeClock()
    interactions = Interactions()
    opened = Opened()
    server = serve(bus=showing(), on_interact=interactions, open_link=opened, clock=clock)

    accepted = [interact(server, valid(server))[0] for _ in range(9)]
    accepted.append(open_link(server, {"token": server.token, "url": LINK})[0])
    status, headers, _ = open_link(server, {"token": server.token, "url": LINK})

    assert accepted == [202] * 10
    assert status == 429
    assert headers["Retry-After"] == "1"
    assert opened.urls == [LINK]


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "OPTIONS", "HEAD"])
def test_open_link_takes_only_post(serve: Serve, method: str) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)

    status, headers, _ = request(server, "/open-link", method=method)

    assert status == 405
    assert headers["Allow"] == "POST"
    assert opened.urls == []


def test_a_link_for_another_host_name_is_refused_even_with_the_token(serve: Serve) -> None:
    opened = Opened()
    server = serve(bus=showing(), open_link=opened)
    rebound = f"rebind.attacker.example:{address(server)[1]}"

    status, _, _ = request(
        server,
        "/open-link",
        "POST",
        host=rebound,
        headers={"Origin": f"http://{rebound}", "Content-Type": "application/json"},
        body=json.dumps({"token": server.token, "url": LINK}).encode(),
    )

    assert status == 403
    assert opened.urls == []
