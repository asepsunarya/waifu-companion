"""PhoneServer: Cyrene's page and her answers for the owner's paired phone, on 127.0.0.1.

Every server binds port 0 and is stopped by the `serve` fixture. The web root, the Live2D
model folder, the VRM, and the Cubism Core are small fake files under tmp_path, never the
real models or Live2D's Core; pairing uses a real PhoneAuth on tmp_path, and her answers
come from a fake backend (no brain, STT, or TTS). Nothing here runs Tailscale: the tailnet
name is only a Host and an Origin the requests carry. Camera photos are tiny JPEGs built by
hand from marker segments (no image library), never a real camera frame.
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import logging
import os
import socket
import struct
import threading
import time
import wave
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pytest

from waifu.config import AvatarConfig, PhoneConfig, RoutineConfig, RoutineStep
from waifu.devices.base import DeviceError, DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.phone import server as phone_server
from waifu.phone.actions import QuickActions
from waifu.phone.auth import CODE_LIFETIME, PhoneAuth
from waifu.phone.server import PhoneBackend, PhoneError, PhoneReply, PhoneServer

CLIENT_TIMEOUT_S = 5.0
TAILNET = "cyrene-pc.tail1234.ts.net"
COOKIE = "cyrene_device"
SECRET = "the private corpus"
CREDIT = "Cyrene20251105 (MomokaSono), © miHoYo"
PAGE_FILES = {
    "phone/index.html": b"<!doctype html><title>Cyrene</title>\n",
    "phone/phone.js": b"import './audio.js';\n",
    "phone/audio.js": b"export const TALK_RATE = 16000;\n",
    "phone/capture-worklet.js": b"registerProcessor('cyrene-capture', class {});\n",
    "phone/phone.css": b"body { margin: 0; }\n",
    "phone/manifest.webmanifest": b'{"name": "Cyrene"}\n',
    "phone/sw.js": b"self.addEventListener('fetch', () => {});\n",
}
AVATAR_FILES = {
    "index.html": b"<!doctype html><title>Avatar</title>\n",
    "live2d.js": b"export async function createRenderer() {}\n",
    "avatar.js": b"export async function createRenderer() { return 'vrm'; }\n",
    "logic.js": b"export const approach = () => 0;\n",
    "vendor/pixi/pixi.min.mjs": b"export const Application = class {};\n",
    "vendor/VENDOR.md": b"# Vendor\n",
}
LIVE2D_FILES = {
    "Cyrene.model3.json": b'{"Version": 3, "FileReferences": {"Moc": "Cyrene.moc3"}}\n',
    "Cyrene.moc3": b"MOC3" + bytes(range(256)) * 4,
    "Cyrene.4096/texture_00.png": b"\x89PNG\r\n\x1a\n" + bytes(64),
}
CORE_BYTES = b"/* a stand-in, not Live2D's Core */\nwindow.Live2DCubismCore = {};\n" * 200
CORE_URL = "/live2d/live2dcubismcore.min.js"
VRM_BYTES = b"glTF" + bytes(range(256)) * 8
PROTECTING_HEADERS = {
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}
REPLY_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(range(40))
REPLY = PhoneReply(
    text="Lampu kamar sudah menyala, Master.",
    emotion="happy",
    audio_wav=REPLY_WAV,
    fps=60,
    visemes=[(0.123, 0.456, 0.0, np.float32(0.25), 0.999), (0.0, 0.0, 0.0, 0.0, 0.0)],
    duration=np.float32(1.23456),  # numpy numbers, as lipsync may hand them over
)
REPLY_JSON = {
    "text": "Lampu kamar sudah menyala, Master.",
    "emotion": "happy",
    "audio": base64.b64encode(REPLY_WAV).decode("ascii"),
    "fps": 60,
    "visemes": [[0.12, 0.46, 0.0, 0.25, 1.0], [0.0, 0.0, 0.0, 0.0, 0.0]],
    "duration": 1.235,
}
HEARD = "nyalakan lampu kamar"


class FakeBackend:
    """Stands in for the Assistant: records what it was asked (the photo of each chat, None
    without one, and whether it was sent through the camera) and answers REPLY.

    `release` holds every call until it is set (it starts set); `entered` tells that a call
    has begun, so a test can send a second request while the first is still answered.
    """

    def __init__(
        self,
        *,
        reply: PhoneReply = REPLY,
        heard: str = HEARD,
        chat_error: Exception | None = None,
        transcribe_error: Exception | None = None,
    ) -> None:
        self.reply = reply
        self.heard = heard
        self.chat_error = chat_error
        self.transcribe_error = transcribe_error
        self.chats: list[str] = []
        self.photos: list[bytes | None] = []
        self.cameras: list[bool] = []
        self.recordings: list[bytes] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def chat(self, text: str, image: bytes | None = None, *, camera: bool = False) -> PhoneReply:
        self.chats.append(text)
        self.photos.append(image)
        self.cameras.append(camera)
        self.entered.set()
        assert self.release.wait(CLIENT_TIMEOUT_S)
        if self.chat_error is not None:
            raise self.chat_error
        return self.reply

    def transcribe(self, wav_bytes: bytes) -> str:
        self.recordings.append(wav_bytes)
        if self.transcribe_error is not None:
            raise self.transcribe_error
        return self.heard


class TextOnlyBackend:
    """A backend as v1.14 wrote it, whose chat takes no photo argument at all."""

    def __init__(self) -> None:
        self.chats: list[str] = []
        self.recordings: list[bytes] = []

    def chat(self, text: str) -> PhoneReply:
        self.chats.append(text)
        return REPLY

    def transcribe(self, wav_bytes: bytes) -> str:
        self.recordings.append(wav_bytes)
        return HEARD


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


Serve = Callable[..., PhoneServer]


def make_phone_config(host: str | None = TAILNET, port: int = 0) -> PhoneConfig:
    # model_copy skips validation: the config refuses port 0, the tests need it.
    return PhoneConfig(host=host).model_copy(update={"port": port})


@pytest.fixture
def web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    for name, content in {**PAGE_FILES, **AVATAR_FILES}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    (tmp_path / "secret.txt").write_bytes(SECRET.encode())
    return root


@pytest.fixture
def live2d_config(tmp_path: Path) -> AvatarConfig:
    folder = tmp_path / "live2d" / "Cyrene"
    for name, content in LIVE2D_FILES.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
    (folder / "notes.txt").write_bytes(SECRET.encode())
    core = tmp_path / "cubism" / "live2dcubismcore.min.js"
    core.parent.mkdir()
    core.write_bytes(CORE_BYTES)
    return AvatarConfig(
        engine="live2d", model=folder / "Cyrene.model3.json", live2d_core=core, credit=CREDIT
    )


@pytest.fixture
def vrm_config(tmp_path: Path) -> AvatarConfig:
    model = tmp_path / "models" / "tsukuyomi_a.vrm"
    model.parent.mkdir()
    model.write_bytes(VRM_BYTES)
    return AvatarConfig(model=model)


@pytest.fixture
def auth(tmp_path: Path) -> PhoneAuth:
    return PhoneAuth(tmp_path / "phone.json")


@pytest.fixture(autouse=True)
def _fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short accept-loop poll keeps every stop() fast."""
    monkeypatch.setattr(PhoneServer, "poll_interval_s", 0.02)


@pytest.fixture
def serve(web_root: Path, live2d_config: AvatarConfig, auth: PhoneAuth) -> Iterator[Serve]:
    """Starts PhoneServers on 127.0.0.1 port 0; every one is stopped after the test."""
    started: list[PhoneServer] = []

    def start(
        *,
        backend: PhoneBackend | None = None,
        avatar: AvatarConfig = live2d_config,
        config: PhoneConfig | None = None,
        owner_call: str = "Master",
        name: str = "Cyrene",
        clock: Callable[[], float] = time.monotonic,
        camera_max_side: int | None = None,
        actions: QuickActions | None = None,
    ) -> PhoneServer:
        server = PhoneServer(
            config or make_phone_config(),
            avatar,
            auth,
            backend or FakeBackend(),
            owner_call=owner_call,
            assistant_name=name,
            web_root=web_root,
            clock=clock,
            camera_max_side=camera_max_side,
            actions=actions,
        )
        started.append(server)
        server.start()
        return server

    yield start
    for server in started:
        server.stop()


def address(server: PhoneServer) -> tuple[str, int]:
    url = urlsplit(server.url)
    assert url.hostname is not None and url.port is not None
    return url.hostname, url.port


def own_origin(server: PhoneServer) -> str:
    return f"http://127.0.0.1:{address(server)[1]}"


def pair_device(auth: PhoneAuth, name: str = "Android") -> str:
    """Pairs a device straight through PhoneAuth and returns its token."""
    token = auth.redeem(auth.new_code(), name)
    assert isinstance(token, str)
    return token


def request(
    server: PhoneServer,
    path: str,
    method: str = "GET",
    *,
    token: str | None = None,
    host: str | None = None,
    headers: Mapping[str, str | None] | None = None,
    body: bytes | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """Sends `path` exactly as given, unnormalised; returns status, headers, and body.

    `token` sends the device cookie; `host` replaces the Host header http.client sends
    (`127.0.0.1:<port>`); a header given as None is left out.
    """
    sent: dict[str, str] = {}
    if token is not None:
        sent["Cookie"] = f"{COOKIE}={token}"
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
    server: PhoneServer,
    path: str,
    payload: Mapping[str, Any] | bytes,
    *,
    token: str | None = None,
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
    return request(server, path, "POST", token=token, host=host, headers=sent, body=body)


def chat(
    server: PhoneServer, token: str | None, text: str = HEARD, **kwargs: Any
) -> tuple[int, http.client.HTTPMessage, bytes]:
    return post(server, "/chat", {"text": text}, token=token, **kwargs)


def talk(
    server: PhoneServer,
    token: str | None,
    recording: bytes,
    *,
    headers: Mapping[str, str | None] | None = None,
    **kwargs: Any,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    sent = {"Content-Type": "audio/wav", **(headers or {})}
    return post(server, "/talk", recording, token=token, headers=sent, **kwargs)


def pair(
    server: PhoneServer, code: Any, **kwargs: Any
) -> tuple[int, http.client.HTTPMessage, bytes]:
    return post(server, "/pair", {"code": code}, **kwargs)


def raw_request(server: PhoneServer, data: bytes, *, end_of_request: bool = True) -> bytes:
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


def post_head(server: PhoneServer, path: str, token: str, extra: bytes) -> bytes:
    """The head of a POST from the page, without its Content-Length unless `extra` has one."""
    port = address(server)[1]
    return (
        b"POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://127.0.0.1:%d\r\n"
        b"Cookie: %s=%s\r\nContent-Type: application/json\r\n%s\r\n"
        % (path.encode(), port, port, COOKIE.encode(), token.encode(), extra)
    )


def recording(
    seconds: float = 1.0,
    *,
    rate: int = 16000,
    channels: int = 1,
    width: int = 2,
    frames: int | None = None,
) -> bytes:
    """A WAV of a repeating byte pattern, as the page's encoder writes one."""
    count = round(seconds * rate) if frames is None else frames
    data = (bytes(range(256)) * (count * channels * width // 256 + 1))[: count * channels * width]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(data)
    return buffer.getvalue()


def float_recording() -> bytes:
    """A mono 32-bit float WAV at 16 kHz (format tag 3), which /talk does not take."""
    data = bytes(16000 * 4)
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 16000 * 4, 4, 32)
    return (
        b"RIFF"
        + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data))
        + b"WAVEfmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def error_of(body: bytes) -> str:
    payload = json.loads(body)
    assert list(payload) == ["error"]
    return payload["error"]


# --- address and lifecycle -----------------------------------------------------------------


def test_it_listens_on_127_0_0_1_only_even_with_a_tailnet_name(serve: Serve) -> None:
    server = serve(config=make_phone_config(TAILNET))
    host, port = address(server)

    assert server.server_address[0] == "127.0.0.1"
    assert server.url == f"http://127.0.0.1:{port}/"
    assert host == "127.0.0.1"
    assert port > 0


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.10", "100.101.102.103", "::1", "::", "localhost", ""]
)
def test_a_listening_address_other_than_ipv4_loopback_is_refused_before_binding(
    web_root: Path,
    live2d_config: AvatarConfig,
    auth: PhoneAuth,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    def no_bind(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the server bound a socket")

    monkeypatch.setattr(PhoneServer, "server_bind", no_bind)

    with pytest.raises(ValueError, match="loopback"):
        PhoneServer(
            make_phone_config(),
            live2d_config,
            auth,
            FakeBackend(),
            owner_call="Master",
            assistant_name="Cyrene",
            web_root=web_root,
            host=host,
        )


def test_a_port_in_use_is_a_phone_error_with_an_indonesian_hint(
    serve: Serve, web_root: Path, live2d_config: AvatarConfig, auth: PhoneAuth
) -> None:
    port = address(serve())[1]

    with pytest.raises(PhoneError) as raised:
        PhoneServer(
            make_phone_config(port=port),
            live2d_config,
            auth,
            FakeBackend(),
            owner_call="Master",
            assistant_name="Cyrene",
            web_root=web_root,
        )

    assert f"127.0.0.1:{port}" in str(raised.value)
    assert "phone.port" in str(raised.value)
    assert "sudah dipakai" in str(raised.value)


def test_stop_frees_the_port_and_is_safe_twice_and_without_start(
    web_root: Path, live2d_config: AvatarConfig, auth: PhoneAuth
) -> None:
    def make(port: int = 0) -> PhoneServer:
        return PhoneServer(
            make_phone_config(port=port),
            live2d_config,
            auth,
            FakeBackend(),
            owner_call="Master",
            assistant_name="Cyrene",
            web_root=web_root,
        )

    never_started = make()
    never_started.stop()
    server = make()
    server.start()
    port = address(server)[1]
    server.stop()
    server.stop()

    again = make(port)
    again.stop()


def test_the_context_manager_serves_inside_and_stops_on_exit(
    web_root: Path, live2d_config: AvatarConfig, auth: PhoneAuth
) -> None:
    server = PhoneServer(
        make_phone_config(),
        live2d_config,
        auth,
        FakeBackend(),
        owner_call="Master",
        assistant_name="Cyrene",
        web_root=web_root,
    )
    with server:
        status, _, _ = request(server, "/")
        port = address(server)[1]

    assert status == 200
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()


# --- the Host check --------------------------------------------------------------------------

FOREIGN_HOSTS = [
    "evil.example",
    "evil.example:{port}",
    "127.0.0.1",
    "127.0.0.1:1",
    "127.0.0.2:{port}",
    "localhost",
    "[::1]:{port}",
    "other-pc.tail1234.ts.net",
    f"{TAILNET}:443",
    f"{TAILNET}.evil.example",
    "",
]


@pytest.mark.parametrize("path", ["/", "/phone/phone.js", "/config.json", "/live2d.js", CORE_URL])
@pytest.mark.parametrize("host", FOREIGN_HOSTS)
def test_a_request_for_another_host_name_is_refused_before_anything_is_served(
    serve: Serve, auth: PhoneAuth, path: str, host: str
) -> None:
    # A web page whose name an attacker re-points at 127.0.0.1 is same-origin with this
    # server but still sends its own name as Host.
    server = serve()
    token = pair_device(auth)

    status, headers, body = request(
        server, path, token=token, host=host.format(port=address(server)[1])
    )

    assert status == 403
    assert headers.get_content_type() == "application/json"
    assert "Tailscale" in error_of(body)
    assert CORE_BYTES[:64] not in body
    assert PAGE_FILES["phone/index.html"] not in body


@pytest.mark.parametrize("path", ["/pair", "/chat", "/talk"])
def test_a_post_for_another_host_name_is_refused_and_reaches_nobody(
    serve: Serve, auth: PhoneAuth, path: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    token = pair_device(auth)
    code = auth.new_code()
    rebound = "rebind.attacker.example"

    status, _, _ = post(
        server,
        path,
        {"code": code} if path == "/pair" else {"text": HEARD},
        token=token,
        host=rebound,
        headers={"Origin": f"http://{rebound}"},
    )

    assert status == 403
    assert backend.chats == []
    assert backend.recordings == []
    assert pair(server, code)[0] == 204  # the code was not even tried


def test_a_tailnet_name_is_refused_while_phone_host_is_unset(serve: Serve) -> None:
    server = serve(config=make_phone_config(None))

    assert request(server, "/", host=TAILNET)[0] == 403
    assert request(server, "/")[0] == 200


# Tailscale adds this to every request that `tailscale funnel` brings in from the internet, and
# drops any copy a client sends; requests from the owner's own tailnet never carry it.
FUNNEL_HEADER = "Tailscale-Funnel-Request"


@pytest.mark.parametrize("path", ["/", "/sw.js", "/icon-192.png", "/config.json", CORE_URL])
def test_a_request_from_the_internet_through_tailscale_funnel_is_refused(
    serve: Serve, auth: PhoneAuth, path: str
) -> None:
    # Funnel opens a whole host:port to the internet, with the Host the tailnet uses.
    server = serve()
    token = pair_device(auth)

    status, headers, body = request(
        server, path, token=token, host=TAILNET, headers={FUNNEL_HEADER: "?1"}
    )

    assert status == 403
    assert headers.get_content_type() == "application/json"
    assert "Funnel" in error_of(body)
    assert PAGE_FILES["phone/index.html"] not in body
    assert CORE_BYTES[:64] not in body


def test_pairing_and_chat_through_tailscale_funnel_are_refused_and_cost_no_try(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    token = pair_device(auth)
    code = auth.new_code()
    from_the_tailnet = {"Origin": f"https://{TAILNET}"}
    from_the_internet = {**from_the_tailnet, FUNNEL_HEADER: "?1"}

    guesses = [
        pair(server, wrong(code), host=TAILNET, headers=from_the_internet)[0] for _ in range(5)
    ]
    right = pair(server, code, host=TAILNET, headers=from_the_internet)[0]
    asked = chat(server, token, host=TAILNET, headers=from_the_internet)[0]

    assert guesses == [403] * 5
    assert (right, asked) == (403, 403)
    assert backend.chats == []
    assert pair(server, code, host=TAILNET, headers=from_the_tailnet)[0] == 204


@pytest.mark.parametrize(
    "host", ["127.0.0.1:{port}", "localhost:{port}", "LOCALHOST:{port}", TAILNET, TAILNET.upper()]
)
def test_its_own_address_localhost_and_the_tailnet_name_are_served(serve: Serve, host: str) -> None:
    server = serve()

    status, _, body = request(server, "/", host=host.format(port=address(server)[1]))

    assert status == 200
    assert body == PAGE_FILES["phone/index.html"]


def test_a_request_without_a_host_header_is_refused(serve: Serve) -> None:
    reply = raw_request(serve(), b"GET / HTTP/1.0\r\n\r\n")

    assert reply.startswith(b"HTTP/1.0 403 ")
    assert PAGE_FILES["phone/index.html"] not in reply


@pytest.mark.parametrize("request_line", [b"GET /\r\n", b"GET / HTTP/0.9\r\nHost: x\r\n\r\n"])
def test_an_http_0_9_request_gets_no_answer_and_no_error(
    serve: Serve, caplog: pytest.LogCaptureFixture, request_line: bytes
) -> None:
    server = serve()

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        reply = raw_request(server, request_line)

    assert reply == b""
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    ("head", "status"),
    [
        (b"POST /chat HTTP/1.1\r\nHost: rebind.attacker.example\r\n", b"403"),
        (b"POST /chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n", b"403"),
        (
            b"POST /chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n",
            b"401",
        ),
        (b"PUT / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n", b"405"),
    ],
    ids=["foreign host", "no origin", "unpaired", "other method"],
)
def test_a_refused_request_s_late_body_is_read_before_the_refusal(
    serve: Serve, head: bytes, status: bytes
) -> None:
    # The body arrives after the server has read the head. Answering at once and closing
    # the socket would leave it unread, and Windows resets a socket closed with unread
    # data, which can throw away the refusal before the client has read it.
    server = serve()
    port = str(address(server)[1]).encode()
    body = json.dumps({"text": "x" * 1500}).encode()
    head = head.replace(b"{port}", port) + b"Content-Length: %d\r\n\r\n" % len(body)

    for _ in range(3):
        with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
            client.sendall(head)
            time.sleep(0.2)
            client.sendall(body)
            reply = b""
            while chunk := client.recv(65536):
                reply += chunk
        assert reply.startswith(b"HTTP/1.0 " + status + b" ")


# --- the Origin check --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://127.0.0.1:{port}", None),
        ("http://localhost:{port}", "localhost:{port}"),
        (f"https://{TAILNET}", TAILNET),
    ],
)
def test_posts_from_the_page_s_own_origins_are_answered(
    serve: Serve, auth: PhoneAuth, origin: str, host: str | None
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    port = address(server)[1]

    status, _, _ = chat(
        server,
        pair_device(auth),
        host=None if host is None else host.format(port=port),
        headers={"Origin": origin.format(port=port)},
    )

    assert status == 200
    assert backend.chats == [HEARD]


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "http://evil.example",
        f"http://{TAILNET}",
        f"https://{TAILNET}:8443",
        "https://127.0.0.1:{port}",
        "http://127.0.0.1",
        "http://127.0.0.1:{port}/",
    ],
)
@pytest.mark.parametrize("path", ["/pair", "/chat", "/talk"])
def test_a_post_from_any_other_origin_is_forbidden(
    serve: Serve, auth: PhoneAuth, origin: str | None, path: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    token = pair_device(auth)
    code = auth.new_code()
    payload = {"code": code} if path == "/pair" else {"text": HEARD}

    status, headers, body = post(
        server,
        path,
        payload,
        token=token,
        headers={"Origin": None if origin is None else origin.format(port=address(server)[1])},
    )

    assert status == 403
    assert headers.get_content_type() == "application/json"
    assert error_of(body)
    assert backend.chats == []
    assert pair(server, code)[0] == 204  # a refused origin never costs a try


def test_two_origin_headers_are_forbidden(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    token = pair_device(auth)
    port = address(server)[1]
    body = json.dumps({"text": HEARD}).encode()

    reply = raw_request(
        server,
        b"POST /chat HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://127.0.0.1:%d\r\n"
        b"Origin: http://evil.example\r\nCookie: %s=%s\r\nContent-Type: application/json\r\n"
        b"Content-Length: %d\r\n\r\n%s"
        % (port, port, COOKIE.encode(), token.encode(), len(body), body),
    )

    assert reply.startswith(b"HTTP/1.0 403 ")
    assert backend.chats == []


# --- the page shell, served without a cookie -----------------------------------------------


@pytest.mark.parametrize("path", ["/", "/?from=home"])
def test_the_page_is_the_phone_index_and_never_cached(serve: Serve, path: str) -> None:
    status, headers, body = request(serve(), path)

    assert status == 200
    assert headers.get_content_type() == "text/html"
    assert headers["Cache-Control"] == "no-store"
    assert body == PAGE_FILES["phone/index.html"]


@pytest.mark.parametrize(
    ("path", "name", "content_type"),
    [
        ("/phone/phone.js", "phone/phone.js", "text/javascript"),
        ("/phone/audio.js", "phone/audio.js", "text/javascript"),
        ("/phone/capture-worklet.js", "phone/capture-worklet.js", "text/javascript"),
        ("/phone/phone.css", "phone/phone.css", "text/css"),
        ("/phone/index.html", "phone/index.html", "text/html"),
        ("/sw.js", "phone/sw.js", "text/javascript"),
        ("/manifest.webmanifest", "phone/manifest.webmanifest", "application/manifest+json"),
    ],
)
def test_the_page_s_own_files_need_no_cookie(
    serve: Serve, path: str, name: str, content_type: str
) -> None:
    status, headers, body = request(serve(), path)

    assert status == 200
    assert headers.get_content_type() == content_type
    assert headers["Content-Length"] == str(len(PAGE_FILES[name]))
    assert body == PAGE_FILES[name]


def png_size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    width, height = struct.unpack(">II", png[16:24])
    return width, height


@pytest.mark.parametrize(("path", "size"), [("/icon-192.png", 192), ("/icon-512.png", 512)])
def test_the_app_icons_are_pngs_of_their_size_without_a_cookie(
    serve: Serve, path: str, size: int
) -> None:
    status, headers, body = request(serve(), path)

    assert status == 200
    assert headers.get_content_type() == "image/png"
    assert headers["Content-Length"] == str(len(body))
    assert png_size(body) == (size, size)


def test_the_icon_shows_her_initial(serve: Serve) -> None:
    cyrene = request(serve(name="Cyrene"), "/icon-192.png")[2]
    again = request(serve(name="cyrene"), "/icon-192.png")[2]
    aqua = request(serve(name="Aqua"), "/icon-192.png")[2]
    no_latin_initial = request(serve(name="キュレネ"), "/icon-192.png")[2]

    assert cyrene == again
    assert aqua != cyrene
    assert png_size(no_latin_initial) == (192, 192)
    assert no_latin_initial not in (cyrene, aqua)


@pytest.mark.parametrize(
    "path",
    [
        "/phone/",
        "/phone/missing.js",
        "/phone/../live2d.js",
        "/phone/%2e%2e/live2d.js",
        "/phone/%2E%2E%2Flive2d.js",
        "/phone/..%2F..%2Fsecret.txt",
        "/phone/../../secret.txt",
        "/phone/..\\live2d.js",
        "/phone/..%5Clive2d.js",
        "/phone/phone.js%00.css",
        "/phone/phone.js::$DATA",
    ],
)
def test_the_page_folder_never_leads_outside_itself(serve: Serve, path: str) -> None:
    # /phone/ is served without a cookie: no path through it may reach the renderers,
    # the vendored libraries, or anything else.
    status, headers, body = request(serve(), path)

    assert status == 404
    assert headers.get_content_type() == "application/json"
    assert AVATAR_FILES["live2d.js"] not in body
    assert SECRET.encode() not in body


def guard_unc_lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records UNC lookups instead of making them (Windows would contact that host)."""
    real_realpath = os.path.realpath
    lookups: list[str] = []

    def guarded_realpath(target: Any, *args: Any, **kwargs: Any) -> str:
        text = os.fspath(target)
        if text.startswith(("\\\\", "//")):
            lookups.append(text)
            return text
        return real_realpath(target, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", guarded_realpath)
    return lookups


@pytest.mark.parametrize(
    ("path", "with_cookie"),
    [
        ("/phone/%5C%5Cattacker.invalid%5Cshare%5Cphone.js", False),
        ("/%5C%5Cattacker.invalid%5Cshare%5Clive2d.js", True),
        ("/model/%5C%5Cattacker.invalid%5Cshare%5Cx.moc3", True),
    ],
)
def test_a_unc_path_is_refused_before_it_is_ever_resolved(
    serve: Serve,
    auth: PhoneAuth,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    with_cookie: bool,
) -> None:
    server = serve()
    token = pair_device(auth) if with_cookie else None
    lookups = guard_unc_lookups(monkeypatch)

    status, _, _ = request(server, path, token=token)

    assert status == 404
    assert lookups == []


# --- everything else needs the device cookie ---------------------------------------------

PROTECTED_PATHS = [
    "/config.json",
    "/actions",
    "/live2d.js",
    "/avatar.js",
    "/logic.js",
    "/index.html",
    "/vendor/pixi/pixi.min.mjs",
    "/model/Cyrene.model3.json",
    "/model/Cyrene.moc3",
    "/model/Cyrene.4096/texture_00.png",
    CORE_URL,
    "/missing.js",
    "/events",
]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_without_the_device_cookie_everything_else_is_401_json(serve: Serve, path: str) -> None:
    status, headers, body = request(serve(), path)

    assert status == 401
    assert headers.get_content_type() == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert "waifu phone pair" in error_of(body)
    assert CORE_BYTES[:64] not in body
    assert LIVE2D_FILES["Cyrene.moc3"][:64] not in body


@pytest.mark.parametrize(
    "cookie",
    [
        "",
        f"{COOKIE}=",
        f"{COOKIE}=not-a-token",
        f"{COOKIE}={'A' * 43}",
        f"{COOKIE}=%2e%2e",
        "cyrene_devices=whatever",
        "other=1",
    ],
)
def test_a_missing_or_unknown_device_cookie_is_401(
    serve: Serve, auth: PhoneAuth, cookie: str
) -> None:
    pair_device(auth)
    server = serve()

    status, _, _ = request(server, "/config.json", headers={"Cookie": cookie})

    assert status == 401


def test_a_forgotten_device_is_401_at_once(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    token = pair_device(auth)
    assert request(server, "/config.json", token=token)[0] == 200

    assert auth.forget("d1")

    assert request(server, "/config.json", token=token)[0] == 401
    assert chat(server, token)[0] == 401


@pytest.mark.parametrize(
    "cookie",
    [
        "{cookie}={token}",
        "theme=dark; {cookie}={token}",
        "{cookie}={token}; theme=dark",
        'pref="a,b"; flag; {cookie}={token}',
    ],
)
def test_the_device_cookie_is_found_among_other_cookies(
    serve: Serve, auth: PhoneAuth, cookie: str
) -> None:
    server = serve()
    token = pair_device(auth)

    status, _, _ = request(
        server, "/config.json", headers={"Cookie": cookie.format(cookie=COOKIE, token=token)}
    )

    assert status == 200


@pytest.mark.parametrize(
    "cookie",
    [
        "{cookie}=planted; {cookie}={token}",
        "{cookie}={unknown}; {cookie}={token}",
        "{cookie}=not.a.token; {cookie}=; {cookie}={token}",
        "theme=dark; {cookie}=planted; other=1; {cookie}={token}",
    ],
    ids=["planted", "unknown-token", "malformed", "among-others"],
)
def test_a_cookie_of_the_same_name_ahead_of_the_device_cookie_never_locks_the_phone_out(
    serve: Serve, auth: PhoneAuth, cookie: str
) -> None:
    # Another page of the same site (another host of the tailnet, another program on
    # 127.0.0.1) can set its own cookie of that name, and browsers send the longer Path first.
    server = serve()
    token = pair_device(auth)
    header = {"Cookie": cookie.format(cookie=COOKIE, token=token, unknown="A" * 43)}

    assert request(server, "/config.json", headers=header)[0] == 200
    assert chat(server, None, headers=header)[0] == 200


def test_the_device_cookie_is_found_in_a_later_cookie_header(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    token = pair_device(auth)
    port = address(server)[1]

    reply = raw_request(
        server,
        b"GET /config.json HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nCookie: %s=planted\r\n"
        b"Cookie: %s=%s\r\n\r\n" % (port, COOKIE.encode(), COOKIE.encode(), token.encode()),
    )

    assert reply.startswith(b"HTTP/1.0 200 ")


def test_at_most_four_device_cookies_are_tried(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    token = pair_device(auth)
    planted = "; ".join(f"{COOKIE}=planted{number}" for number in range(4))

    status, _, _ = request(
        server, "/config.json", headers={"Cookie": f"{planted}; {COOKIE}={token}"}
    )

    assert status == 401


def test_config_json_describes_the_live2d_avatar_for_the_phone_without_secrets(
    serve: Serve, auth: PhoneAuth
) -> None:
    server = serve(owner_call="Sayang", name="Cyrene")

    status, headers, body = request(server, "/config.json", token=pair_device(auth))

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "engine": "live2d",
        "model": "/model/Cyrene.model3.json",
        "core": CORE_URL,
        "credit": CREDIT,
        "name": "Cyrene",
        "owner_call": "Sayang",
        "framing": "upper",
        "camera": {"enabled": False, "max_side": None},
    }


def test_config_json_offers_the_camera_with_the_longest_side_of_its_photos(
    serve: Serve, auth: PhoneAuth
) -> None:
    server = serve(camera_max_side=1280)

    status, _, body = request(server, "/config.json", token=pair_device(auth))

    assert status == 200
    assert json.loads(body)["camera"] == {"enabled": True, "max_side": 1280}


def test_config_json_describes_the_vrm_avatar(
    serve: Serve, auth: PhoneAuth, vrm_config: AvatarConfig
) -> None:
    server = serve(avatar=vrm_config)

    status, _, body = request(server, "/config.json", token=pair_device(auth))

    assert status == 200
    assert json.loads(body) == {
        "engine": "vrm",
        "model": "/model.vrm",
        "core": None,
        "credit": "つくよみちゃん（© Rei Yumesaki）",
        "name": "Cyrene",
        "owner_call": "Master",
        "framing": "upper",
        "camera": {"enabled": False, "max_side": None},
    }


@pytest.mark.parametrize(
    ("path", "content", "content_type"),
    [
        ("/live2d.js", AVATAR_FILES["live2d.js"], "text/javascript"),
        ("/logic.js", AVATAR_FILES["logic.js"], "text/javascript"),
        ("/vendor/pixi/pixi.min.mjs", AVATAR_FILES["vendor/pixi/pixi.min.mjs"], "text/javascript"),
        ("/model/Cyrene.model3.json", LIVE2D_FILES["Cyrene.model3.json"], "application/json"),
        ("/model/Cyrene.moc3", LIVE2D_FILES["Cyrene.moc3"], "application/octet-stream"),
        (
            "/model/Cyrene.4096/texture_00.png",
            LIVE2D_FILES["Cyrene.4096/texture_00.png"],
            "image/png",
        ),
        (CORE_URL, CORE_BYTES, "text/javascript"),
    ],
)
def test_a_paired_phone_gets_the_renderers_the_model_and_the_core(
    serve: Serve, auth: PhoneAuth, path: str, content: bytes, content_type: str
) -> None:
    status, headers, body = request(serve(), path, token=pair_device(auth))

    assert status == 200
    assert headers.get_content_type() == content_type
    assert headers["Content-Length"] == str(len(content))
    assert body == content


def test_a_paired_phone_gets_the_vrm_for_engine_vrm(
    serve: Serve, auth: PhoneAuth, vrm_config: AvatarConfig
) -> None:
    server = serve(avatar=vrm_config)
    token = pair_device(auth)

    status, headers, body = request(server, "/model.vrm", token=token)

    assert status == 200
    assert headers.get_content_type() == "model/gltf-binary"
    assert body == VRM_BYTES
    assert request(server, "/model/Cyrene.model3.json", token=token)[0] == 404


# Files the phone keeps in its browser cache and checks with the PC before each use: the
# model's textures alone are tens of megabytes, too much to download on every cold open.
KEPT_FILES = [
    "/model/Cyrene.4096/texture_00.png",
    "/model/Cyrene.moc3",
    "/model/Cyrene.model3.json",
    CORE_URL,
    "/vendor/pixi/pixi.min.mjs",
    "/live2d.js",
    "/phone/phone.js",
    "/sw.js",
]


def served_path(path: str, web_root: Path, avatar: AvatarConfig) -> Path:
    """The file on disk that a URL of KEPT_FILES names."""
    assert avatar.model is not None and avatar.live2d_core is not None
    if path == CORE_URL:
        return avatar.live2d_core
    if path.startswith("/model/"):
        return avatar.model.parent / path.removeprefix("/model/")
    if path == "/sw.js":
        return web_root / "phone" / "sw.js"
    return web_root / path.lstrip("/")


def last_modified_of(served: Path) -> str:
    """A file's modification time as HTTP writes it, in whole seconds."""
    return formatdate(int(served.stat().st_mtime), usegmt=True)


@pytest.mark.parametrize("path", KEPT_FILES)
def test_the_model_core_and_libraries_carry_their_date_and_are_checked_before_each_use(
    serve: Serve, auth: PhoneAuth, web_root: Path, live2d_config: AvatarConfig, path: str
) -> None:
    status, headers, _ = request(serve(), path, token=pair_device(auth))

    assert status == 200
    assert headers["Cache-Control"] == "no-cache"
    assert headers["Last-Modified"] == last_modified_of(served_path(path, web_root, live2d_config))


@pytest.mark.parametrize("path", KEPT_FILES)
def test_a_file_the_phone_already_has_is_a_304_without_a_body(
    serve: Serve, auth: PhoneAuth, web_root: Path, live2d_config: AvatarConfig, path: str
) -> None:
    server = serve()
    token = pair_device(auth)
    modified = last_modified_of(served_path(path, web_root, live2d_config))

    status, headers, body = request(
        server, path, token=token, headers={"If-Modified-Since": modified}
    )

    assert status == 304
    assert body == b""
    assert headers["Last-Modified"] == modified
    assert headers["Cache-Control"] == "no-cache"
    for name, value in PROTECTING_HEADERS.items():
        assert headers[name] == value


def test_a_changed_file_is_sent_again(
    serve: Serve, auth: PhoneAuth, web_root: Path, live2d_config: AvatarConfig
) -> None:
    server = serve()
    token = pair_device(auth)
    path = "/model/Cyrene.4096/texture_00.png"
    texture = served_path(path, web_root, live2d_config)
    kept = last_modified_of(texture)
    later = texture.stat().st_mtime + 60
    os.utime(texture, (later, later))

    status, headers, body = request(server, path, token=token, headers={"If-Modified-Since": kept})

    assert status == 200
    assert body == LIVE2D_FILES["Cyrene.4096/texture_00.png"]
    assert parsedate_to_datetime(headers["Last-Modified"]) == datetime.fromtimestamp(
        int(later), UTC
    )


@pytest.mark.parametrize(
    "since",
    ["not a date", "Sun, 06 Nov 1994 08:49:37 GMT", "Mon, 32 Foo 2026 99:99:99 GMT"],
    ids=["malformed", "older", "impossible"],
)
def test_an_older_or_unreadable_if_modified_since_gets_the_whole_file(
    serve: Serve, auth: PhoneAuth, since: str
) -> None:
    status, _, body = request(
        serve(), CORE_URL, token=pair_device(auth), headers={"If-Modified-Since": since}
    )

    assert status == 200
    assert body == CORE_BYTES


def test_a_kept_file_still_needs_the_device_cookie(
    serve: Serve, web_root: Path, live2d_config: AvatarConfig
) -> None:
    modified = last_modified_of(served_path(CORE_URL, web_root, live2d_config))

    status, _, body = request(serve(), CORE_URL, headers={"If-Modified-Since": modified})

    assert status == 401
    assert CORE_BYTES[:64] not in body


@pytest.mark.parametrize("path", ["/", "/phone/index.html"])
def test_the_page_itself_is_never_kept_or_answered_with_a_304(
    serve: Serve, web_root: Path, path: str
) -> None:
    modified = last_modified_of(web_root / "phone" / "index.html")

    status, headers, body = request(serve(), path, headers={"If-Modified-Since": modified})

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "Last-Modified" not in headers
    assert body == PAGE_FILES["phone/index.html"]


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
        "/..%2F..%2F..%2Fsecret.txt",
        "/live2d.js%00.js",
        "/live2d.js::$DATA",
        "/model/../secret.txt",
        "/model/%2e%2e/%2e%2e/%2e%2e/secret.txt",
        "/model/notes.txt",
        "/model.vrm",
    ],
)
def test_a_paired_phone_gets_nothing_outside_the_served_folders(
    serve: Serve, auth: PhoneAuth, path: str
) -> None:
    status, headers, body = request(serve(), path, token=pair_device(auth))

    assert status == 404
    assert headers.get_content_type() == "application/json"
    assert SECRET.encode() not in body


# --- pairing --------------------------------------------------------------------------------


def test_the_right_code_pairs_the_phone_with_a_strict_secure_http_only_cookie(
    serve: Serve, auth: PhoneAuth
) -> None:
    server = serve()

    status, headers, body = pair(server, auth.new_code())

    assert status == 204
    assert body == b""
    assert headers["Cache-Control"] == "no-store"
    (cookie,) = headers.get_all("Set-Cookie")
    name_value, *attributes = cookie.split("; ")
    name, _, token = name_value.partition("=")
    assert name == COOKIE
    # Secure works on http://127.0.0.1 too: browsers treat loopback as a secure context.
    assert set(attributes) == {
        "HttpOnly",
        "Secure",
        "SameSite=Strict",
        "Path=/",
        "Max-Age=31536000",
    }
    device = auth.check(token)
    assert device is not None
    assert device.name == "Android"
    assert request(server, "/config.json", token=token)[0] == 200


def test_pairing_through_the_tailnet_name_works(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()

    status, _, _ = pair(
        server, auth.new_code(), host=TAILNET, headers={"Origin": f"https://{TAILNET}"}
    )

    assert status == 204
    assert [device.name for device in auth.devices()] == ["Android"]


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_wrong_codes_are_403_until_the_code_is_burned_then_429(
    serve: Serve, auth: PhoneAuth
) -> None:
    server = serve()
    code = auth.new_code()

    statuses = [pair(server, wrong(code))[0] for _ in range(5)]
    after_burning = pair(server, code)

    assert statuses == [403, 403, 403, 403, 429]
    assert after_burning[0] == 429
    assert "waifu phone pair" in error_of(after_burning[2])
    assert auth.devices() == []
    assert pair(server, auth.new_code())[0] == 204  # a new code pairs at once


def test_a_typo_in_a_new_code_after_a_burned_one_is_403_and_the_new_code_still_pairs(
    serve: Serve, auth: PhoneAuth
) -> None:
    server = serve()
    burned = auth.new_code()
    assert [pair(server, wrong(burned))[0] for _ in range(5)] == [403, 403, 403, 403, 429]
    fresh = auth.new_code()

    status, _, body = pair(server, wrong(fresh))

    assert status == 403
    assert "salah" in error_of(body)
    assert pair(server, fresh)[0] == 204


def test_a_wrong_code_says_so_in_indonesian(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    code = auth.new_code()

    status, headers, body = pair(server, wrong(code))

    assert status == 403
    assert headers.get_content_type() == "application/json"
    assert "salah" in error_of(body)


def test_too_many_wrong_codes_are_forgotten_after_a_code_s_lifetime(
    serve: Serve, auth: PhoneAuth
) -> None:
    clock = FakeClock()
    server = serve(clock=clock)
    code = auth.new_code()
    assert [pair(server, wrong(code))[0] for _ in range(5)][-1] == 429

    clock.now += CODE_LIFETIME.total_seconds() - 1
    assert pair(server, wrong(code))[0] == 429
    clock.now += 1

    assert pair(server, wrong(code))[0] == 403


def test_a_pairing_forgets_the_wrong_codes_before_it(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    code = auth.new_code()
    assert [pair(server, wrong(code))[0] for _ in range(3)] == [403] * 3
    assert pair(server, code)[0] == 204

    code = auth.new_code()

    assert [pair(server, wrong(code))[0] for _ in range(4)] == [403] * 4


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b"{}",
        b'{"code": 123456}',
        b'{"code": null}',
        b'{"code": "12345"}',
        b'{"code": "1234567"}',
        b'{"code": "12a456"}',
        b'{"code": "\\uff11\\uff12\\uff13\\uff14\\uff15\\uff16"}',
        b'{"code": "123456", "name": "x"}',
        b"\xff\xfe",
    ],
)
def test_a_malformed_pairing_request_is_400_and_costs_no_try(
    serve: Serve, auth: PhoneAuth, body: bytes
) -> None:
    server = serve()
    code = auth.new_code()

    statuses = [post(server, "/pair", body)[0] for _ in range(5)]

    assert statuses == [400] * 5
    assert pair(server, code)[0] == 204


def test_a_pairing_request_that_is_not_json_is_415(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    code = auth.new_code()

    status, _, _ = post(server, "/pair", {"code": code}, headers={"Content-Type": "text/plain"})

    assert status == 415
    assert pair(server, code)[0] == 204


def test_an_oversized_pairing_request_is_413(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    code = auth.new_code()
    body = json.dumps({"code": code}).encode()

    status, _, _ = post(server, "/pair", body + b" " * (1025 - len(body)))

    assert status == 413
    assert pair(server, code)[0] == 204


# --- chat -------------------------------------------------------------------------------------


def test_a_chat_is_answered_with_her_text_voice_and_mouth_track(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, headers, body = chat(server, pair_device(auth), "  nyalakan lampu kamar \n")

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == REPLY_JSON
    assert backend.chats == ["nyalakan lampu kamar"]
    assert backend.recordings == []


def test_a_reply_without_audio_has_null_audio(serve: Serve, auth: PhoneAuth) -> None:
    silent = PhoneReply("Sudah, Master.", "neutral", None, 60, [], 0.0)
    server = serve(backend=FakeBackend(reply=silent))

    status, _, body = chat(server, pair_device(auth))

    assert status == 200
    assert json.loads(body) == {
        "text": "Sudah, Master.",
        "emotion": "neutral",
        "audio": None,
        "fps": 60,
        "visemes": [],
        "duration": 0.0,
    }


@pytest.mark.parametrize("text", ["a" * 2000, "😀" * 2000, "あ" * 2000])
def test_a_chat_of_2000_characters_is_accepted(serve: Serve, auth: PhoneAuth, text: str) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, _ = chat(server, pair_device(auth), text)

    assert status == 200
    assert backend.chats == [text]


def test_a_chat_over_2000_characters_is_413(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, body = chat(server, pair_device(auth), "😀" * 2001)

    assert status == 413
    assert "2000" in error_of(body)
    assert backend.chats == []


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b'"text"',
        b"{}",
        b'{"text": ""}',
        b'{"text": "  \\n\\t "}',
        b'{"text": 5}',
        b'{"text": ["a"]}',
        b'{"text": "a", "extra": 1}',
        b'{"text": "\\ud800"}',
        b"\xff\xfe",
        b'{"text": "a"' + b"]" * 2,
    ],
)
def test_a_malformed_chat_is_400(serve: Serve, auth: PhoneAuth, body: bytes) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, headers, _ = post(server, "/chat", body, token=pair_device(auth))

    assert status == 400
    assert headers.get_content_type() == "application/json"
    assert backend.chats == []


@pytest.mark.parametrize(
    "content_type", ["application/json; charset=utf-8", "application/json;charset=UTF-8"]
)
def test_json_with_a_utf8_charset_is_accepted(
    serve: Serve, auth: PhoneAuth, content_type: str
) -> None:
    status, _, _ = chat(serve(), pair_device(auth), headers={"Content-Type": content_type})

    assert status == 200


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "application/json; charset=latin-1"],
)
def test_a_chat_that_is_not_json_is_415(
    serve: Serve, auth: PhoneAuth, content_type: str | None
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, _ = chat(server, pair_device(auth), headers={"Content-Type": content_type})

    assert status == 415
    assert backend.chats == []


def test_a_chat_without_a_content_length_is_411(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    reply = raw_request(
        server, post_head(server, "/chat", pair_device(auth), b"Transfer-Encoding: chunked\r\n")
    )

    assert reply.startswith(b"HTTP/1.0 411 ")
    assert backend.chats == []


@pytest.mark.parametrize("length", [b"32769", b"9" * 5000, b"0" * 5000 + b"32769"])
def test_a_chat_over_32_kilobytes_is_refused_without_reading_it(
    serve: Serve, auth: PhoneAuth, length: bytes
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    # The body never comes: a server that tried to read it would time this request out.
    reply = raw_request(
        server,
        post_head(server, "/chat", pair_device(auth), b"Content-Length: %s\r\n" % length),
        end_of_request=False,
    )

    assert reply.startswith(b"HTTP/1.0 413 ")
    assert backend.chats == []


def test_a_chat_body_of_exactly_32_kilobytes_is_read(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    body = json.dumps({"text": HEARD}).encode()

    status, _, _ = post(server, "/chat", body + b" " * (32768 - len(body)), token=pair_device(auth))

    assert status == 200
    assert backend.chats == [HEARD]


@pytest.mark.parametrize(
    "length_headers",
    [
        b"Content-Length: abc\r\n",
        b"Content-Length: -1\r\n",
        b"Content-Length: +40\r\n",
        b"Content-Length: \xc2\xb2\r\n",
        b"Content-Length: 40\r\nContent-Length: 40\r\n",
    ],
)
def test_an_invalid_content_length_is_400(
    serve: Serve, auth: PhoneAuth, length_headers: bytes
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    reply = raw_request(server, post_head(server, "/chat", pair_device(auth), length_headers))

    assert reply.startswith(b"HTTP/1.0 400 ")
    assert backend.chats == []


def test_a_body_shorter_than_its_content_length_is_400(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    body = json.dumps({"text": HEARD}).encode()

    reply = raw_request(
        server,
        post_head(server, "/chat", pair_device(auth), b"Content-Length: %d\r\n" % (len(body) + 9))
        + body,
    )

    assert reply.startswith(b"HTTP/1.0 400 ")
    assert backend.chats == []


@pytest.mark.parametrize("owner_call", ["Master", "sayang"])
def test_a_failing_backend_is_a_500_in_indonesian_and_logged_without_secrets(
    serve: Serve, auth: PhoneAuth, caplog: pytest.LogCaptureFixture, owner_call: str
) -> None:
    backend = FakeBackend(chat_error=RuntimeError("the brain fell over"))
    server = serve(backend=backend, owner_call=owner_call)
    token = pair_device(auth)

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        status, headers, body = chat(server, token, "rahasia kecil")

    assert status == 500
    assert headers.get_content_type() == "application/json"
    message = error_of(body)
    assert f"Maaf, {owner_call}," in message
    assert "the brain fell over" not in message
    failures = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(failures) == 1
    assert failures[0].exc_info is not None
    assert "d1" in failures[0].getMessage()
    assert token not in caplog.text
    assert "rahasia kecil" not in caplog.text


def test_a_reply_that_cannot_be_sent_as_json_is_a_500(serve: Serve, auth: PhoneAuth) -> None:
    broken = PhoneReply("Halo.", "happy", None, 60, [(float("nan"),) * 5], 1.0)
    server = serve(backend=FakeBackend(reply=broken))

    status, _, body = chat(server, pair_device(auth))

    assert status == 500
    assert error_of(body)


# --- talk -------------------------------------------------------------------------------------


def test_a_recording_is_transcribed_and_answered(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    sent = recording(1.5)

    status, headers, body = talk(server, pair_device(auth), sent)

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert json.loads(body) == {"heard": HEARD, **REPLY_JSON}
    assert backend.recordings == [sent]
    assert backend.chats == [HEARD]


@pytest.mark.parametrize("heard", ["", "  \n"])
def test_nothing_heard_answers_without_asking_her(
    serve: Serve, auth: PhoneAuth, heard: str
) -> None:
    backend = FakeBackend(heard=heard)
    server = serve(backend=backend)

    status, _, body = talk(server, pair_device(auth), recording())

    assert status == 200
    assert json.loads(body) == {"heard": "", "text": None}
    assert backend.chats == []


def test_a_recording_of_sixty_seconds_is_accepted(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    sent = recording(60)

    status, _, _ = talk(server, pair_device(auth), sent)

    assert status == 200
    assert backend.recordings == [sent]


def test_a_recording_over_sixty_seconds_is_413(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, body = talk(server, pair_device(auth), recording(frames=60 * 16000 + 1))

    assert status == 413
    assert "60" in error_of(body)
    assert backend.recordings == []


def test_a_recording_over_two_megabytes_is_refused_without_reading_it(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)
    head = post_head(server, "/talk", pair_device(auth), b"Content-Length: 2097153\r\n")

    reply = raw_request(
        server, head.replace(b"application/json", b"audio/wav"), end_of_request=False
    )

    assert reply.startswith(b"HTTP/1.0 413 ")
    assert backend.recordings == []


NOT_TALK_RECORDINGS = {
    "44.1 kHz": lambda: recording(rate=44100),
    "8 kHz": lambda: recording(rate=8000),
    "stereo": lambda: recording(channels=2),
    "8-bit": lambda: recording(width=1),
    "24-bit": lambda: recording(width=3),
    "no frames": lambda: recording(frames=0),
    "data cut short": lambda: recording()[:-100],
    "32-bit float": float_recording,
    "empty": lambda: b"",
    "ogg": lambda: b"OggS" + bytes(100),
    "no chunks": lambda: b"RIFF\x04\x00\x00\x00WAVE",
    "cut-off fmt chunk": lambda: b"RIFF\xff\xff\xff\x7fWAVEfmt \x10\x00\x00\x00",
    "no data chunk": lambda: recording()[:44].replace(b"data", b"LIST") + recording()[44:],
}


@pytest.mark.parametrize("kind", NOT_TALK_RECORDINGS)
def test_anything_but_a_mono_16_bit_16_khz_wav_is_400(
    serve: Serve, auth: PhoneAuth, kind: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, headers, _ = talk(server, pair_device(auth), NOT_TALK_RECORDINGS[kind]())

    assert status == 400
    assert headers.get_content_type() == "application/json"
    assert backend.recordings == []


@pytest.mark.parametrize(
    "content_type", [None, "application/octet-stream", "audio/x-wav", "audio/wav; rate=16000"]
)
def test_a_recording_not_sent_as_audio_wav_is_415(
    serve: Serve, auth: PhoneAuth, content_type: str | None
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, _ = talk(
        server, pair_device(auth), recording(), headers={"Content-Type": content_type}
    )

    assert status == 415
    assert backend.recordings == []


def test_audio_wav_in_capitals_is_accepted(serve: Serve, auth: PhoneAuth) -> None:
    status, _, _ = talk(
        serve(), pair_device(auth), recording(), headers={"Content-Type": "Audio/WAV"}
    )

    assert status == 200


def test_a_failing_transcription_is_a_500(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend(transcribe_error=OSError("whisper is gone"))
    server = serve(backend=backend)

    status, _, body = talk(server, pair_device(auth), recording())

    assert status == 500
    assert "Maaf, Master," in error_of(body)
    assert backend.chats == []


def test_talk_needs_the_device_cookie(serve: Serve) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, _ = talk(server, None, recording())

    assert status == 401
    assert backend.recordings == []


# --- photos from the phone camera ---------------------------------------------------------------

CAMERA_SIDE = 1280
MB = 1024 * 1024
PHOTO_MAX_BYTES = 7 * MB // 2  # 3.5 MB: its base64 stays under the API's 5 MB image limit
CAMERA_OFF = "Kamera HP tidak aktif."
NOT_A_PHOTO = "Foto tidak valid."


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    """A JPEG marker segment: 0xFF, the marker, a length counting itself, and the payload."""
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def photo(
    width: int = 640, height: int = 480, *, size: int | None = None, frame: bool = True
) -> bytes:
    """A JPEG as the page's canvas encodes one, built by hand from its marker segments: SOI,
    APP0, DQT, a baseline frame header of `width` x `height`, DHT, a scan, and EOI.

    `size` pads the scan's entropy-coded data so the photo has that many bytes; `frame=False`
    leaves the frame header out.
    """
    components = b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    frame_header = jpeg_segment(0xC0, struct.pack(">BHHB", 8, height, width, 3) + components)
    head = (
        b"\xff\xd8"
        + jpeg_segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + jpeg_segment(0xDB, b"\x00" + bytes(range(1, 65)))
        + (frame_header if frame else b"")
        + jpeg_segment(0xC4, b"\x00\x01" + bytes(15) + b"\x00")
        + jpeg_segment(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    )
    scan = b"\x8a\x28\xa2\xff\x00\x3f"
    if size is not None:
        scan = (scan * (size // len(scan) + 1))[: size - len(head) - 2]
    return head + scan + b"\xff\xd9"


PHOTO = photo()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def chat_with_photo(
    server: PhoneServer, token: str, image: str, text: str = HEARD, **kwargs: Any
) -> tuple[int, http.client.HTTPMessage, bytes]:
    return post(server, "/chat", {"text": text, "image": image}, token=token, **kwargs)


def talk_as_json(
    server: PhoneServer, token: str, sent: bytes, image: str | None, **kwargs: Any
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """POST /talk as the page sends a recording with a photo: JSON, both in base64."""
    payload = {"audio": b64(sent)} | ({} if image is None else {"image": image})
    return post(server, "/talk", payload, token=token, **kwargs)


def send_photo(
    server: PhoneServer, token: str, route: str, image: str
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """A chat or a talk carrying `image`."""
    if route == "chat":
        return chat_with_photo(server, token, image)
    return talk_as_json(server, token, recording(), image)


def test_a_chat_with_a_photo_hands_her_the_jpeg_untouched(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, headers, body = chat_with_photo(server, pair_device(auth), b64(PHOTO))

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert json.loads(body) == REPLY_JSON
    assert backend.chats == [HEARD]
    assert backend.photos == [PHOTO]
    assert backend.cameras == [True]  # a photo always comes through the camera


def camera_message(route: str, sent: bytes | None = None) -> dict[str, Any]:
    """A chat or a talk sent with the viewfinder open, carrying the photo `sent` (None: one that
    could not be taken), as the page sends it."""
    message = {"text": HEARD} if route == "chat" else {"audio": b64(recording())}
    return message | ({} if sent is None else {"image": b64(sent)}) | {"camera": True}


@pytest.mark.parametrize("route", ["chat", "talk"])
def test_a_message_sent_with_the_viewfinder_open_but_without_a_photo_tells_her_so(
    serve: Serve, auth: PhoneAuth, route: str
) -> None:
    # The page could not take the photo (the camera was still starting): "coba lihat ini" is
    # still about the camera, not the PC screen.
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, body = post(server, f"/{route}", camera_message(route), token=pair_device(auth))

    assert status == 200
    assert json.loads(body)["text"] == REPLY.text
    assert backend.chats == [HEARD]
    assert backend.photos == [None]
    assert backend.cameras == [True]


@pytest.mark.parametrize("route", ["chat", "talk"])
def test_a_photo_sent_with_the_camera_mark_reaches_her_with_it(
    serve: Serve, auth: PhoneAuth, route: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, _ = post(server, f"/{route}", camera_message(route, PHOTO), token=pair_device(auth))

    assert status == 200
    assert backend.photos == [PHOTO]
    assert backend.cameras == [True]


def test_a_recording_sent_as_json_with_a_photo_is_heard_and_answered_with_it(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)
    sent = recording(1.5)

    status, headers, body = talk_as_json(server, pair_device(auth), sent, b64(PHOTO))

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert json.loads(body) == {"heard": HEARD, **REPLY_JSON}
    assert backend.recordings == [sent]
    assert backend.chats == [HEARD]
    assert backend.photos == [PHOTO]


@pytest.mark.parametrize("camera_max_side", [None, CAMERA_SIDE], ids=["camera off", "camera on"])
def test_a_recording_sent_as_json_without_a_photo_is_answered_like_a_wav_body(
    serve: Serve, auth: PhoneAuth, camera_max_side: int | None
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=camera_max_side)
    sent = recording(1.5)

    status, _, body = talk_as_json(server, pair_device(auth), sent, None)

    assert status == 200
    assert json.loads(body) == {"heard": HEARD, **REPLY_JSON}
    assert backend.recordings == [sent]
    assert backend.photos == [None]


def test_nothing_heard_with_a_photo_answers_without_asking_her(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend(heard=" ")
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, body = talk_as_json(server, pair_device(auth), recording(), b64(PHOTO))

    assert status == 200
    assert json.loads(body) == {"heard": "", "text": None}
    assert backend.chats == []


SEND_WITHOUT_A_PHOTO: dict[str, Callable[[PhoneServer, str], tuple[int, Any, bytes]]] = {
    "chat": lambda server, token: chat(server, token),
    "chat not through the camera": lambda server, token: post(
        server, "/chat", {"text": HEARD, "camera": False}, token=token
    ),
    "talk as wav": lambda server, token: talk(server, token, recording()),
    "talk as json": lambda server, token: talk_as_json(server, token, recording(), None),
}


@pytest.mark.parametrize("message", SEND_WITHOUT_A_PHOTO)
def test_a_message_without_a_photo_asks_her_with_the_text_alone(
    serve: Serve, auth: PhoneAuth, message: str
) -> None:
    # A backend that takes no photo argument at all still answers every message without one.
    backend = TextOnlyBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, _ = SEND_WITHOUT_A_PHOTO[message](server, pair_device(auth))

    assert status == 200
    assert backend.chats == [HEARD]


@pytest.mark.parametrize("route", ["chat", "talk"])
def test_a_photo_while_the_camera_is_off_is_400_and_reaches_nobody(
    serve: Serve, auth: PhoneAuth, route: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend)

    status, headers, body = send_photo(server, pair_device(auth), route, b64(PHOTO))

    assert status == 400
    assert headers.get_content_type() == "application/json"
    assert error_of(body) == CAMERA_OFF
    assert backend.chats == []
    assert backend.recordings == []


# A photo as large as the page's (1280 x 720, 200 KB): its base64 alone is over 32 KB.
REAL_PHOTO = photo(1280, 720, size=200_000)
TOO_LARGE = "Permintaan terlalu besar."


@pytest.mark.parametrize(
    ("route", "sent", "expected"),
    [
        ("chat", None, (400, CAMERA_OFF)),
        ("talk", None, (400, CAMERA_OFF)),
        ("chat", REAL_PHOTO, (413, TOO_LARGE)),  # over the 32 KB a chat has without the camera
        ("talk", REAL_PHOTO, (400, CAMERA_OFF)),
    ],
    ids=["chat marked", "talk marked", "chat with a real photo", "talk with a real photo"],
)
def test_a_message_sent_through_the_camera_while_it_is_off_is_refused_and_reaches_nobody(
    serve: Serve,
    auth: PhoneAuth,
    route: str,
    sent: bytes | None,
    expected: tuple[int, str],
) -> None:
    # A page loaded before the camera was turned off still has its viewfinder: whatever it
    # sends through it is refused, with or without a photo, before she is asked.
    backend = FakeBackend()
    server = serve(backend=backend)

    status, _, body = post(
        server, f"/{route}", camera_message(route, sent), token=pair_device(auth)
    )

    assert (status, error_of(body)) == expected
    assert backend.chats == []
    assert backend.recordings == []


def test_a_long_recording_with_a_real_photo_while_the_camera_is_off_is_refused_unread(
    serve: Serve, auth: PhoneAuth
) -> None:
    # 45 s of speech and the photo, both as base64, are over the 2 MB a talk has without it.
    backend = FakeBackend()
    server = serve(backend=backend)
    message = {"audio": b64(recording(45)), "image": b64(REAL_PHOTO), "camera": True}

    status, _, body = post(server, "/talk", message, token=pair_device(auth))

    assert (status, error_of(body)) == (413, TOO_LARGE)
    assert backend.recordings == []


def test_a_body_too_large_to_read_first_is_drained_so_the_client_can_finish_sending(
    serve: Serve, auth: PhoneAuth
) -> None:
    # On Windows a socket closed with unread data is reset: the phone, still sending its body,
    # would lose the connection (and maybe the refusal) instead of reading a clean 413.
    server = serve()
    size = 3_000_000  # over the 2 MB a talk may have with the camera off
    head = post_head(server, "/talk", pair_device(auth), b"Content-Length: %d\r\n" % size)
    with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
        client.sendall(head)
        reply = client.recv(65536)  # the refusal starts before any of the body was sent
        sent = 0
        while sent < size:
            sent += client.send(bytes(min(65536, size - sent)))
        client.shutdown(socket.SHUT_WR)
        while chunk := client.recv(65536):
            reply += chunk

    status_line, _, body = reply.partition(b"\r\n")
    assert b" 413 " in status_line
    assert error_of(body.split(b"\r\n\r\n", 1)[1]) == TOO_LARGE


def test_a_refused_body_is_drained_for_a_few_seconds_at_most(
    serve: Serve, auth: PhoneAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A client that declares a body and never sends it holds the request thread only briefly.
    monkeypatch.setattr(phone_server, "DRAIN_MAX_S", 0.3)
    server = serve()
    head = post_head(server, "/talk", pair_device(auth), b"Content-Length: 3000000\r\n")
    with socket.create_connection(address(server), timeout=CLIENT_TIMEOUT_S) as client:
        client.sendall(head)
        started = time.monotonic()
        reply = b""
        while chunk := client.recv(65536):
            reply += chunk
        waited = time.monotonic() - started

    assert b" 413 " in reply.split(b"\r\n", 1)[0]
    assert waited < 3


NOT_CAMERA_PHOTOS: dict[str, Callable[[], str]] = {
    "not base64": lambda: "bukan base64!",
    "data url": lambda: "data:image/jpeg;base64," + b64(PHOTO),
    "line breaks": lambda: base64.encodebytes(PHOTO).decode("ascii"),
    "url-safe alphabet": lambda: base64.urlsafe_b64encode(PHOTO).decode("ascii"),
    "missing padding": lambda: b64(photo(size=1000)).rstrip("="),
    "empty": lambda: "",
    "png": lambda: b64(b"\x89PNG\r\n\x1a\n" + bytes(64)),
    "soi alone": lambda: b64(b"\xff\xd8"),
    "soi and a marker byte": lambda: b64(b"\xff\xd8\xff"),
    "cut short": lambda: b64(PHOTO[:30]),
    "no frame header": lambda: b64(photo(frame=False)),
    "zero height": lambda: b64(photo(640, 0)),
    "15 wide": lambda: b64(photo(15, 480)),
    "15 high": lambda: b64(photo(640, 15)),
    "1281 wide": lambda: b64(photo(1281, 720)),
    "1281 high": lambda: b64(photo(720, 1281)),
    "over 3.5 MB": lambda: b64(photo(size=PHOTO_MAX_BYTES + 1)),
}


@pytest.mark.parametrize("route", ["chat", "talk"])
@pytest.mark.parametrize("kind", NOT_CAMERA_PHOTOS)
def test_anything_but_a_jpeg_within_the_camera_s_limits_is_400(
    serve: Serve, auth: PhoneAuth, route: str, kind: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, headers, body = send_photo(server, pair_device(auth), route, NOT_CAMERA_PHOTOS[kind]())

    assert status == 400
    assert headers.get_content_type() == "application/json"
    assert error_of(body) == NOT_A_PHOTO
    assert backend.chats == []
    assert backend.recordings == []


CAMERA_PHOTOS: dict[str, Callable[[], bytes]] = {
    "16 x 16": lambda: photo(16, 16),
    "landscape": lambda: photo(1280, 720),
    "portrait": lambda: photo(720, 1280),
    "square": lambda: photo(1280, 1280),
    "a sliver across": lambda: photo(1280, 16),
    "a sliver down": lambda: photo(16, 1280),
    "3.5 MB": lambda: photo(size=PHOTO_MAX_BYTES),
}


@pytest.mark.parametrize("kind", CAMERA_PHOTOS)
def test_a_photo_at_the_limits_is_accepted(serve: Serve, auth: PhoneAuth, kind: str) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)
    sent = CAMERA_PHOTOS[kind]()

    status, _, _ = chat_with_photo(server, pair_device(auth), b64(sent))

    assert status == 200
    assert backend.photos == [sent]


def test_the_longest_side_allowed_is_the_server_s_camera_side(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=512)
    token = pair_device(auth)

    accepted = chat_with_photo(server, token, b64(photo(512, 384)))[0]
    status, _, body = chat_with_photo(server, token, b64(photo(640, 480)))

    assert accepted == 200
    assert status == 400
    assert error_of(body) == NOT_A_PHOTO
    assert backend.photos == [photo(512, 384)]


@pytest.mark.parametrize(
    "payload",
    [
        {"image": b64(PHOTO)},
        {"text": "", "image": b64(PHOTO)},
        {"text": HEARD, "image": None},
        {"text": HEARD, "image": 5},
        {"text": HEARD, "image": [b64(PHOTO)]},
        {"text": HEARD, "image": b64(PHOTO), "audio": ""},
        {"text": HEARD, "camera": "true"},
        {"text": HEARD, "camera": 1},
        {"text": HEARD, "camera": None},
        {"text": HEARD, "image": b64(PHOTO), "camera": [True]},
    ],
    ids=[
        "no text",
        "empty text",
        "null photo",
        "number",
        "list",
        "extra field",
        "camera as text",
        "camera as a number",
        "null camera",
        "camera as a list",
    ],
)
def test_a_malformed_chat_with_a_photo_is_400(
    serve: Serve, auth: PhoneAuth, payload: dict[str, Any]
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, _ = post(server, "/chat", payload, token=pair_device(auth))

    assert status == 400
    assert backend.chats == []


MALFORMED_JSON_TALKS: dict[str, Callable[[], bytes]] = {
    "not json": lambda: b"not json",
    "a list": lambda: b"[]",
    "no recording": lambda: json.dumps({"image": b64(PHOTO)}).encode(),
    "recording not a string": lambda: json.dumps({"audio": 5}).encode(),
    "null photo": lambda: json.dumps({"audio": b64(recording()), "image": None}).encode(),
    "extra field": lambda: json.dumps({"audio": b64(recording()), "text": HEARD}).encode(),
    "camera as text": lambda: json.dumps({"audio": b64(recording()), "camera": "1"}).encode(),
    "null camera": lambda: json.dumps({"audio": b64(recording()), "camera": None}).encode(),
    "recording not base64": lambda: json.dumps({"audio": "bukan base64!"}).encode(),
    "recording with line breaks": lambda: json.dumps(
        {"audio": base64.encodebytes(recording()).decode("ascii")}
    ).encode(),
}


@pytest.mark.parametrize("kind", MALFORMED_JSON_TALKS)
def test_a_malformed_json_talk_is_400(serve: Serve, auth: PhoneAuth, kind: str) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, headers, _ = post(
        server, "/talk", MALFORMED_JSON_TALKS[kind](), token=pair_device(auth)
    )

    assert status == 400
    assert headers.get_content_type() == "application/json"
    assert backend.recordings == []
    assert backend.chats == []


@pytest.mark.parametrize("kind", NOT_TALK_RECORDINGS)
def test_a_recording_sent_as_json_is_checked_like_a_wav_body(
    serve: Serve, auth: PhoneAuth, kind: str
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, _ = talk_as_json(server, pair_device(auth), NOT_TALK_RECORDINGS[kind](), b64(PHOTO))

    assert status == 400
    assert backend.recordings == []


def test_a_recording_sent_as_json_over_sixty_seconds_is_413(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)

    status, _, body = talk_as_json(
        server, pair_device(auth), recording(frames=60 * 16000 + 1), b64(PHOTO)
    )

    assert status == 413
    assert "60" in error_of(body)
    assert backend.recordings == []


@pytest.mark.parametrize(("size", "expected"), [(2 * MB, 200), (2 * MB + 1, 413)])
def test_a_recording_sent_as_json_may_be_two_megabytes_like_a_wav_body(
    serve: Serve, auth: PhoneAuth, size: int, expected: int
) -> None:
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE)
    sixty_seconds = recording(60)
    sent = sixty_seconds + bytes(size - len(sixty_seconds))  # data after the data chunk

    status, _, _ = talk_as_json(server, pair_device(auth), sent, b64(PHOTO))

    assert status == expected
    assert backend.recordings == ([sent] if expected == 200 else [])


@pytest.mark.parametrize(
    ("path", "camera", "content_type", "limit", "at_the_limit"),
    [
        ("/chat", True, "application/json", 5 * MB, 400),
        ("/chat", True, "application/json; charset=utf-8", 5 * MB, 400),
        ("/chat", True, "text/plain", 32 * 1024, 415),
        ("/chat", False, "application/json", 32 * 1024, 400),
        ("/talk", True, "application/json", 8 * MB, 400),
        ("/talk", True, "audio/wav", 2 * MB, 400),
        ("/talk", True, "text/plain", 2 * MB, 415),
        ("/talk", False, "application/json", 2 * MB, 400),
        ("/talk", False, "audio/wav", 2 * MB, 400),
    ],
)
def test_the_body_limit_is_chosen_from_the_content_type_before_the_body_is_read(
    serve: Serve,
    auth: PhoneAuth,
    path: str,
    camera: bool,
    content_type: str,
    limit: int,
    at_the_limit: int,
) -> None:
    # With the camera on, JSON may carry a photo (and /talk's recording) as base64.
    backend = FakeBackend()
    server = serve(backend=backend, camera_max_side=CAMERA_SIDE if camera else None)
    token = pair_device(auth)
    head = post_head(server, path, token, b"Content-Length: %d\r\n" % (limit + 1))

    # The body never comes: a server that tried to read it would time this request out.
    over = raw_request(
        server, head.replace(b"application/json", content_type.encode()), end_of_request=False
    )
    status, _, _ = post(
        server, path, b" " * limit, token=token, headers={"Content-Type": content_type}
    )

    assert over.startswith(b"HTTP/1.0 413 ")
    assert status == at_the_limit
    assert backend.chats == []
    assert backend.recordings == []


def test_a_photo_is_logged_only_by_its_size_and_dimensions(
    serve: Serve,
    auth: PhoneAuth,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = serve(camera_max_side=CAMERA_SIDE)
    token = pair_device(auth)
    refused = photo(2000, 1000)

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        statuses = [
            chat_with_photo(server, token, b64(PHOTO))[0],
            talk_as_json(server, token, recording(), b64(PHOTO))[0],
            chat_with_photo(server, token, b64(refused))[0],
        ]

    assert statuses == [200, 200, 400]
    logged = "\n".join(record.getMessage() for record in caplog.records) + caplog.text
    logged += capsys.readouterr().err
    for sent in (PHOTO, refused):
        encoded = b64(sent)
        pieces = [encoded[start : start + 12] for start in range(0, len(encoded) - 12, 12)]
        assert [piece for piece in pieces if piece in logged] == []
        assert repr(sent[:6])[2:-1] not in logged
    described = [
        record
        for record in caplog.records
        if f"{len(PHOTO)} bytes" in record.getMessage() and "640x480" in record.getMessage()
    ]
    assert len(described) == 2


# --- one request at a time per device -----------------------------------------------------


def start_chat(server: PhoneServer, token: str) -> tuple[threading.Thread, list[int]]:
    statuses: list[int] = []
    thread = threading.Thread(target=lambda: statuses.append(chat(server, token)[0]))
    thread.start()
    return thread, statuses


@pytest.mark.parametrize("second", ["chat", "talk"])
def test_a_device_asking_while_she_still_answers_it_gets_429(
    serve: Serve, auth: PhoneAuth, second: str
) -> None:
    backend = FakeBackend()
    backend.release.clear()
    server = serve(backend=backend)
    token = pair_device(auth)
    thread, statuses = start_chat(server, token)
    assert backend.entered.wait(CLIENT_TIMEOUT_S)

    if second == "chat":
        status, headers, body = chat(server, token, "matikan TV")
    else:
        status, headers, body = talk(server, token, recording())
    backend.release.set()
    thread.join(CLIENT_TIMEOUT_S)

    assert status == 429
    assert headers.get_content_type() == "application/json"
    assert "Master" in error_of(body)
    assert statuses == [200]
    assert backend.chats == [HEARD]
    assert backend.recordings == []
    assert chat(server, token, "matikan TV")[0] == 200  # free again once she answered


def test_another_device_is_not_held_up(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    backend.release.clear()
    server = serve(backend=backend)
    first, second = pair_device(auth), pair_device(auth, "Tablet")
    thread, statuses = start_chat(server, first)
    assert backend.entered.wait(CLIENT_TIMEOUT_S)

    other, other_statuses = start_chat(server, second)
    time.sleep(0.2)
    backend.release.set()
    thread.join(CLIENT_TIMEOUT_S)
    other.join(CLIENT_TIMEOUT_S)

    assert statuses == [200]
    assert other_statuses == [200]
    assert backend.chats == [HEARD, HEARD]


def test_a_failed_answer_frees_the_device(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend(chat_error=RuntimeError("boom"))
    server = serve(backend=backend)
    token = pair_device(auth)
    assert chat(server, token)[0] == 500

    backend.chat_error = None

    assert chat(server, token)[0] == 200


# --- quick buttons ---------------------------------------------------------------------------


AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]
LAMP_BUTTONS = [
    {"id": "dev.lampu_kamar.on", "label": "Nyala"},
    {"id": "dev.lampu_kamar.off", "label": "Mati"},
    {"id": "dev.lampu_kamar.toggle", "label": "Nyala/mati"},
    {"id": "dev.lampu_kamar.brightness.10", "label": "10%"},
    {"id": "dev.lampu_kamar.brightness.30", "label": "30%"},
    {"id": "dev.lampu_kamar.brightness.60", "label": "60%"},
    {"id": "dev.lampu_kamar.brightness.100", "label": "100%"},
]
TIDUR = RoutineConfig(
    description="Mode tidur",
    steps=[
        RoutineStep(device="lampu_kamar", command="off"),
        RoutineStep(device="ac", command="temp", value=26),
    ],
)


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
    quick buttons of its devices and of `routines` (by default the routine `tidur`)."""
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
    return devices, QuickActions(registry, routines, schedule=schedule)


def press(
    server: PhoneServer, token: str | None, action_id: Any = "dev.lampu_kamar.on", **kwargs: Any
) -> tuple[int, http.client.HTTPMessage, bytes]:
    return post(server, "/action", {"id": action_id}, token=token, **kwargs)


def all_calls(devices: Mapping[str, MockDevice]) -> list[tuple[Any, ...]]:
    """Every command the house's devices were told, device by device."""
    return [(name, *call) for name, device in devices.items() for call in device.calls]


def test_a_paired_phone_gets_the_quick_buttons_never_cached(serve: Serve, auth: PhoneAuth) -> None:
    _, actions = quick_house()
    server = serve(actions=actions)

    status, headers, body = request(server, "/actions", token=pair_device(auth))

    assert status == 200
    assert headers.get_content_type() == "application/json"
    assert headers["Cache-Control"] == "no-store"
    groups = json.loads(body)["groups"]
    assert [group["name"] for group in groups] == ["Lampu kamar", "AC Samsung", "Rutinitas"]
    assert groups[0]["actions"] == LAMP_BUTTONS
    assert groups[2]["actions"] == [{"id": "rt.tidur", "label": "Mode tidur"}]
    assert b"komputer" not in body and b"colokan_pc" not in body


def test_without_quick_actions_there_are_no_buttons_to_press(serve: Serve, auth: PhoneAuth) -> None:
    server = serve()
    token = pair_device(auth)

    assert json.loads(request(server, "/actions", token=token)[2]) == {"groups": []}
    assert press(server, token)[0] == 404


def test_a_quick_button_runs_its_device_and_answers_its_message(
    serve: Serve, auth: PhoneAuth
) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, headers, body = press(server, pair_device(auth), "dev.lampu_kamar.brightness.30")

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "ok": True,
        "message": "Lampu kamar: perintah 'brightness' berhasil (simulasi).",
    }
    assert all_calls(devices) == [("lampu_kamar", "brightness", 30)]


def test_a_device_that_fails_is_told_in_a_200_so_the_page_shows_why(
    serve: Serve, auth: PhoneAuth
) -> None:
    _, actions = quick_house(kipas=UnreachableDevice("kipas", "Kipas", kind="plug"))
    server = serve(actions=actions)

    status, _, body = press(server, pair_device(auth), "dev.kipas.on")

    assert status == 200
    assert json.loads(body) == {"ok": False, "message": "Kipas tidak menjawab."}


def test_a_routine_button_runs_the_routine(serve: Serve, auth: PhoneAuth) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, _, body = press(server, pair_device(auth), "rt.tidur")

    assert status == 200
    assert json.loads(body) == {"ok": True, "message": "Mode tidur: 2 dari 2 langkah berhasil."}
    assert all_calls(devices) == [("lampu_kamar", "off", None), ("ac", "temp_26", 26)]


def test_a_quick_button_needs_the_device_cookie(serve: Serve, auth: PhoneAuth) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)
    pair_device(auth)

    status, _, body = press(server, None)

    assert status == 401
    assert "waifu phone pair" in error_of(body)
    assert all_calls(devices) == []


@pytest.mark.parametrize(
    "origin",
    [None, "null", "http://evil.example", f"http://{TAILNET}", "https://127.0.0.1:{port}"],
)
def test_a_quick_button_from_any_other_origin_is_forbidden_and_runs_nothing(
    serve: Serve, auth: PhoneAuth, origin: str | None
) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)
    sent = None if origin is None else origin.format(port=address(server)[1])

    status, _, body = press(server, pair_device(auth), headers={"Origin": sent})

    assert status == 403
    assert error_of(body)
    assert all_calls(devices) == []


def test_a_quick_button_from_the_tailnet_page_runs(serve: Serve, auth: PhoneAuth) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, _, _ = press(
        server, pair_device(auth), host=TAILNET, headers={"Origin": f"https://{TAILNET}"}
    )

    assert status == 200
    assert all_calls(devices) == [("lampu_kamar", "on", None)]


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
def test_the_pc_and_its_plug_are_never_quick_buttons(
    serve: Serve, auth: PhoneAuth, action_id: str
) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, _, body = press(server, pair_device(auth), action_id)

    assert status == 404
    assert error_of(body) == "Tombol itu sudah tidak ada. Muat ulang halaman."
    assert all_calls(devices) == []


@pytest.mark.parametrize(
    "action_id", ["dev.kulkas.on", "dev.lampu_kamar.brightness.55", "dev.ac.temp_17", "rt.siang"]
)
def test_an_unknown_quick_button_is_404_in_indonesian(
    serve: Serve, auth: PhoneAuth, action_id: str
) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, _, body = press(server, pair_device(auth), action_id)

    assert status == 404
    assert "Muat ulang" in error_of(body)
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
        b'{"id": "DEV.lampu_kamar.on"}',
        b'{"id": "dev.lampu_kamar.on; shutdown"}',
        b'{"id": "dev.lampu_kamar.brightness.1000"}',
        b'{"id": "dev.lampu_kamar.on\\n"}',
        b'{"id": "\\ud800"}',
    ],
)
def test_a_malformed_quick_action_is_400_and_runs_nothing(
    serve: Serve, auth: PhoneAuth, body: bytes
) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, _, reply = post(server, "/action", body, token=pair_device(auth))

    assert status == 400
    assert error_of(reply)
    assert all_calls(devices) == []


def test_a_quick_action_that_is_not_json_is_415(serve: Serve, auth: PhoneAuth) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)

    status, _, _ = press(server, pair_device(auth), headers={"Content-Type": "text/plain"})

    assert status == 415
    assert all_calls(devices) == []


def test_a_quick_action_over_a_kilobyte_is_413(serve: Serve, auth: PhoneAuth) -> None:
    devices, actions = quick_house()
    server = serve(actions=actions)
    body = json.dumps({"id": "dev.lampu_kamar.on", "pad": "x" * 1024}).encode()

    status, _, _ = post(server, "/action", body, token=pair_device(auth))

    assert status == 413
    assert all_calls(devices) == []


def test_a_quick_action_without_a_content_length_is_411(serve: Serve, auth: PhoneAuth) -> None:
    _, actions = quick_house()
    server = serve(actions=actions)

    reply = raw_request(server, post_head(server, "/action", pair_device(auth), b""))

    assert reply.startswith(b"HTTP/1.0 411 ")


def test_a_phone_gets_ten_quick_actions_a_minute(serve: Serve, auth: PhoneAuth) -> None:
    devices, actions = quick_house()
    clock = FakeClock()
    server = serve(actions=actions, clock=clock)
    token, other = pair_device(auth), pair_device(auth, "Tablet")

    statuses = [press(server, token)[0] for _ in range(10)]
    clock.now += 59.0
    status, headers, body = press(server, token)

    assert statuses == [200] * 10
    assert status == 429
    assert headers["Retry-After"] == "60"
    assert "Tunggu sebentar" in error_of(body)
    assert len(devices["lampu_kamar"].calls) == 10
    assert press(server, other)[0] == 200  # another phone has ten of its own
    clock.now += 1.0
    assert press(server, token)[0] == 200  # the first of the ten is a minute old


def test_a_refused_quick_action_counts_toward_the_ten(serve: Serve, auth: PhoneAuth) -> None:
    _, actions = quick_house()
    server = serve(actions=actions, clock=FakeClock())
    token = pair_device(auth)

    statuses = [press(server, token, "dev.kulkas.on")[0] for _ in range(10)]

    assert statuses == [404] * 10
    assert press(server, token)[0] == 429


def start_press(
    server: PhoneServer, token: str, action_id: str
) -> tuple[threading.Thread, list[int]]:
    statuses: list[int] = []
    thread = threading.Thread(target=lambda: statuses.append(press(server, token, action_id)[0]))
    thread.start()
    return thread, statuses


def test_a_phone_s_second_quick_action_while_its_first_runs_is_409(
    serve: Serve, auth: PhoneAuth
) -> None:
    """Not the rate limit's 429: the page tells the owner the last press still runs."""
    slow = SlowDevice("kipas", "Kipas", kind="plug")
    devices, actions = quick_house(kipas=slow)
    server = serve(actions=actions)
    token = pair_device(auth)
    thread, statuses = start_press(server, token, "dev.kipas.on")
    assert slow.entered.wait(CLIENT_TIMEOUT_S)

    status, headers, body = press(server, token)
    slow.release.set()
    thread.join(CLIENT_TIMEOUT_S)

    assert status == 409
    assert "Retry-After" not in headers
    assert error_of(body) == "Perintah tadi masih jalan. Tunggu sebentar."
    assert statuses == [200]
    assert all_calls(devices) == [("kipas", "on", None)]
    assert press(server, token)[0] == 200  # free again once it ran


def test_another_phone_s_quick_action_waits_for_the_one_running(
    serve: Serve, auth: PhoneAuth
) -> None:
    slow = SlowDevice("kipas", "Kipas", kind="plug")
    devices, actions = quick_house(kipas=slow)
    server = serve(actions=actions)
    first, second = pair_device(auth), pair_device(auth, "Tablet")
    thread, statuses = start_press(server, first, "dev.kipas.on")
    assert slow.entered.wait(CLIENT_TIMEOUT_S)

    other, other_statuses = start_press(server, second, "dev.lampu_kamar.on")
    other.join(0.2)
    ran_meanwhile = all_calls(devices)
    slow.release.set()
    thread.join(CLIENT_TIMEOUT_S)
    other.join(CLIENT_TIMEOUT_S)

    assert ran_meanwhile == []  # the lamp waits for the plug: one action at a time
    assert (statuses, other_statuses) == ([200], [200])
    assert all_calls(devices) == [("lampu_kamar", "on", None), ("kipas", "on", None)]


def test_a_quick_button_works_while_she_answers_a_chat(serve: Serve, auth: PhoneAuth) -> None:
    backend = FakeBackend()
    backend.release.clear()
    devices, actions = quick_house()
    server = serve(backend=backend, actions=actions)
    token = pair_device(auth)
    thread, statuses = start_chat(server, token)
    assert backend.entered.wait(CLIENT_TIMEOUT_S)

    status, _, _ = press(server, token)
    backend.release.set()
    thread.join(CLIENT_TIMEOUT_S)

    assert status == 200
    assert statuses == [200]
    assert all_calls(devices) == [("lampu_kamar", "on", None)]


def test_a_quick_action_that_raises_is_a_500_in_indonesian_and_frees_the_phone(
    serve: Serve, auth: PhoneAuth, caplog: pytest.LogCaptureFixture
) -> None:
    later = RoutineConfig(steps=[RoutineStep(device="ac", command="off", after_minutes=60)])
    _, actions = quick_house({"nanti": later}, schedule=BrokenSchedule())
    server = serve(actions=actions)
    token = pair_device(auth)

    with caplog.at_level(logging.ERROR, logger="waifu.phone.server"):
        status, _, body = press(server, token, "rt.nanti")

    assert status == 500
    assert error_of(body) == "Maaf, Master, ada masalah di PC. Coba lagi nanti ya."
    assert any(record.exc_info for record in caplog.records)
    assert press(server, token)[0] == 200


def test_quick_actions_are_logged_by_phone_and_button_never_by_token(
    serve: Serve,
    auth: PhoneAuth,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, actions = quick_house(kipas=UnreachableDevice("kipas", "Kipas", kind="plug"))
    server = serve(actions=actions)
    token = pair_device(auth)

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        press(server, token)
        press(server, token, "dev.kipas.on")
        press(server, token, "dev.komputer.shutdown")
        request(server, "/actions", token=token)

    infos = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert "Phone device d1 ran quick action dev.lampu_kamar.on: ok" in infos
    assert "Phone device d1 ran quick action dev.kipas.on: failed" in infos
    assert any("dev.komputer.shutdown" in line and "d1" in line for line in infos)
    logged = "\n".join(record.getMessage() for record in caplog.records) + caplog.text
    assert token not in logged
    assert token not in capsys.readouterr().err


# --- methods, headers, and logs ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "allowed"),
    [("/chat", "POST"), ("/pair", "POST"), ("/talk", "POST"), ("/action", "POST")],
)
def test_the_post_routes_take_only_post(serve: Serve, path: str, allowed: str) -> None:
    status, headers, _ = request(serve(), path)

    assert status == 405
    assert headers["Allow"] == allowed


@pytest.mark.parametrize("path", ["/", "/config.json", "/actions", "/live2d.js", "/missing"])
def test_a_post_elsewhere_is_405(serve: Serve, auth: PhoneAuth, path: str) -> None:
    status, headers, _ = post(serve(), path, {"text": HEARD}, token=pair_device(auth))

    assert status == 405
    assert headers["Allow"] == "GET"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_other_methods_are_405(serve: Serve, method: str) -> None:
    status, _, body = request(serve(), "/", method=method)

    assert status == 405
    if method == "HEAD":
        assert body == b""


def test_every_response_keeps_other_websites_from_embedding_or_framing_it(
    serve: Serve, auth: PhoneAuth
) -> None:
    backend = FakeBackend()
    backend.release.clear()
    server = serve(backend=backend)
    token = pair_device(auth)
    replies = [
        request(server, "/")[1],
        request(server, "/icon-192.png")[1],
        request(server, "/manifest.webmanifest")[1],
        request(server, "/config.json")[1],
        request(server, CORE_URL, token=token)[1],
        request(server, "/missing.js", token=token)[1],
        request(server, "/", host="evil.example")[1],
        request(server, "/", "DELETE")[1],
        pair(server, auth.new_code())[1],
        pair(server, "000000", headers={"Origin": "http://evil.example"})[1],
    ]
    thread, _ = start_chat(server, token)
    assert backend.entered.wait(CLIENT_TIMEOUT_S)
    replies.append(chat(server, token)[1])
    backend.release.set()
    thread.join(CLIENT_TIMEOUT_S)
    replies.append(chat(server, token)[1])

    for headers in replies:
        assert {name: headers[name] for name in PROTECTING_HEADERS} == PROTECTING_HEADERS


def test_requests_are_logged_at_debug_not_written_to_stderr(
    serve: Serve, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    server = serve()

    with caplog.at_level(logging.DEBUG, logger="waifu.phone.server"):
        request(server, "/phone/phone.js")

    assert capsys.readouterr().err == ""
    assert any(
        "/phone/phone.js" in record.getMessage() and record.levelno == logging.DEBUG
        for record in caplog.records
    )


def test_tokens_codes_and_words_are_never_logged(
    serve: Serve,
    auth: PhoneAuth,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = serve()

    with caplog.at_level(logging.DEBUG, logger="waifu"):
        code = auth.new_code()
        pair(server, wrong(code))
        status, headers, _ = pair(server, code)
        token = headers["Set-Cookie"].split(";")[0].partition("=")[2]
        request(server, "/config.json", token=token)
        request(server, "/config.json", token=token + "x")
        chat(server, token, "kata sandiku rahasia")
        talk(server, token, recording())
        chat(server, token, headers={"Origin": "http://evil.example"})

    assert status == 204
    logged = "\n".join(record.getMessage() for record in caplog.records) + caplog.text
    err = capsys.readouterr().err
    for secret in (token, code, wrong(code), "kata sandiku rahasia", HEARD):
        assert secret not in logged
        assert secret not in err
