"""The phone server: Cyrene's page, her avatar, and her answers for the owner's paired phone.

It listens on 127.0.0.1 only. `tailscale serve` shares it over HTTPS with the owner's
tailnet, where the phone opens it as https://<phone.host>/ (the PC's *.ts.net name), and a
phone pairs once with the six-digit code `waifu phone pair` shows (see `auth.py`).

Without the device cookie that pairing sets, only the page shell is served: the page, its
own files under /phone/, the manifest, the service worker, and the icons. Her settings
(/config.json), the renderers and vendored libraries, the Live2D model, the Cubism Core, and
her answers all need it (401 otherwise, and the page shows its pairing form). Every request
must name this server as Host (DNS rebinding) and come from the tailnet, never from the
internet through Tailscale Funnel; every POST must come from the page's own origin, and every
response keeps other websites from embedding or framing what it serves. The files the phone
keeps (the model, the Core, the libraries, the page's own scripts) carry their date, so a
phone that already has one gets a 304 instead of the file again.

The server never touches the brain, the STT, or the TTS: a `PhoneBackend` (the Assistant in
`waifu run`) answers. A device asks one thing at a time; asking again while she is still
answering it is refused with 429.

While the camera is on (`camera_max_side`), a chat or a talk sent with the page's viewfinder
open says so (`"camera": true` in a JSON body) and carries one JPEG photo the page took as it
was sent, in base64, unless the page could not take one. The photo reaches the backend
untouched once it is a JPEG of a size the page makes (see `jpeg.py`); photos are private, so
only a photo's byte size and dimensions are ever logged. While the camera is off, anything
sent through it is refused.

The quick buttons (`actions`, see `actions.py`) run the owner's devices and routines without the
brain: GET /actions lists them, and POST /action runs the one whose id it names, looked up in
the buttons of that moment, so the phone never names a device or a command. A device runs at
most ACTION_RATE_LIMIT of them in any ACTION_RATE_WINDOW_S, one at a time (apart from its chat).

The Suara page (`voice`, a `VoiceBackend`: the `VoicePanel`) shows what her mic hears and what
she did with each utterance: GET /voice. POST /voice/set switches the mic or changes the voice
settings, counted with the quick buttons and one at a time with them. Both are refused while
guest mode is on (`guest_mode`), since the records hold what was said.
"""

from __future__ import annotations

import base64
import functools
import io
import ipaddress
import json
import logging
import math
import re
import sys
import threading
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

import numpy as np

from waifu.avatar.bus import viewer_settings
from waifu.avatar.server import (
    CONTENT_TYPES,
    INDEX,
    METHOD_NOT_ALLOWED_TEXT,
    NOT_FOUND_TEXT,
    POST_ONLY_TEXT,
    PROTECTING_HEADERS,
    WEB_ROOT,
    AvatarFiles,
    RollingLimit,
    ServedFile,
    send_file,
    web_file,
)
from waifu.card import Card
from waifu.config import AvatarConfig, PhoneConfig
from waifu.phone.actions import (
    ACTION_BUSY_TEXT,
    ACTION_ID,
    ACTION_LIMIT_TEXT,
    ACTION_MAX_BYTES,
    ACTION_RATE_LIMIT,
    ACTION_RATE_WINDOW_S,
    ACTION_UNKNOWN_TEXT,
    QuickActions,
    group_json,
)
from waifu.phone.auth import CODE_LIFETIME, Device, PhoneAuth, Refusal
from waifu.phone.jpeg import jpeg_size
from waifu.screen import encode_png

logger = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
CONFIG_URL = "/config.json"
ACTIONS_URL = "/actions"
PAIR_URL = "/pair"
CHAT_URL = "/chat"
TALK_URL = "/talk"
ACTION_URL = "/action"
VOICE_URL = "/voice"
VOICE_SET_URL = "/voice/set"
POST_ROUTES = (PAIR_URL, CHAT_URL, TALK_URL, ACTION_URL, VOICE_SET_URL)

# The page shell, served without a cookie: these URLs, and /phone/<file>, name files of the
# page's own folder under the web root.
PAGE_FOLDER = "phone"
PAGE_PREFIX = "/phone/"
SHELL_FILES = {"/": INDEX, "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js"}
PAGE_CONTENT_TYPES = {**CONTENT_TYPES, ".webmanifest": "application/manifest+json"}
ICON_SIZES = {"/icon-192.png": 192, "/icon-512.png": 512}

COOKIE_NAME = "cyrene_device"
COOKIE_MAX_AGE_S = 365 * 24 * 60 * 60
# Device cookies of a request looked up at most: another page of the same site may add its
# own of that name ahead of the real one, but a phone has one.
MAX_DEVICE_COOKIES = 4
DEVICE_NAME = "Android"
PAIR_CODE = re.compile(r"[0-9]{6}")
# A url-safe token as PhoneAuth makes them; any other cookie value is never looked up.
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,128}")
# Tailscale marks every request that `tailscale funnel` brings in from the internet with this
# header, and drops any copy a client sends.
FUNNEL_HEADER = "Tailscale-Funnel-Request"

MAX_CHAT_CHARS = 2000
# JSON may escape every character of a 2000-character text, as \uXXXX or a surrogate pair.
CHAT_MAX_BYTES = 32 * 1024
PAIR_MAX_BYTES = 1024
TALK_MAX_BYTES = 2 * 1024 * 1024
TALK_RATE = 16000
TALK_SAMPLE_BYTES = 2
TALK_MAX_S = 60
# While the camera is on, a JSON chat or talk may carry a photo of up to PHOTO_MAX_BYTES as
# base64 (4/3 of its size): 5 MB for a chat, 8 MB for a talk with its recording in base64 too.
CAMERA_CHAT_MAX_BYTES = 5 * 1024 * 1024
CAMERA_TALK_MAX_BYTES = 8 * 1024 * 1024
# 3.5 MB: the photo's base64 then stays under the API's 5 MB image limit.
PHOTO_MAX_BYTES = 7 * 1024 * 1024 // 2
PHOTO_MIN_SIDE = 16
JPEG_START = b"\xff\xd8\xff"  # SOI and the marker after it
# What a JSON chat or talk may hold besides its text or recording: the photo in base64, and
# whether it was sent with the viewfinder open (always so with a photo).
MESSAGE_EXTRAS: Mapping[str, type] = {"image": str, "camera": bool}
JSON_TYPES = ("application/json", "application/json;charset=utf-8")
WAV_TYPE = "audio/wav"
# A refused request's body is read first when it is at most this long: on Windows a socket
# closed with unread data is reset, and the reset can throw away the refusal before the
# client has read it. A larger body is left unread.
REFUSAL_READ_BYTES = 4096
# What POST /voice/set takes: an action and its fields, each number a finite int or float.
VOICE_SET_MAX_BYTES = 1024
VOICE_TUNINGS = ("speaker_threshold", "vad_threshold_db", "vad_margin_db")
VOICE_DEVICE_MAX_CHARS = 512  # far above any "name, host API"
# A larger one is drained after the refusal instead, so the client can finish sending and read
# the refusal whole: at most this much of it, for at most this long.
DRAIN_MAX_BYTES = 16 * 2**20
DRAIN_MAX_S = 3.0
DRAIN_CHUNK_BYTES = 65536
# A client that stops sending its request, or stops reading the answer, is dropped.
SOCKET_TIMEOUT_S = 30

FOREIGN_HOST_TEXT = (
    "Server HP Cyrene hanya melayani alamat Tailscale PC ini, 127.0.0.1, atau localhost."
)
FUNNEL_TEXT = (
    "Server HP Cyrene hanya melayani jaringan Tailscale-mu, bukan internet lewat Tailscale "
    "Funnel. Matikan Funnel di PC."
)
FOREIGN_ORIGIN_TEXT = "Permintaan hanya diterima dari halaman Cyrene sendiri."
UNPAIRED_TEXT = "HP ini belum dipasangkan. Jalankan waifu phone pair di PC, lalu masukkan kodenya."
WRONG_CODE_TEXT = "Kodenya salah atau sudah kedaluwarsa."
CODE_BURNED_TEXT = "Terlalu banyak percobaan. Buat kode baru di PC dengan waifu phone pair."
LENGTH_REQUIRED_TEXT = "Permintaan harus menyertakan Content-Length."
BAD_REQUEST_TEXT = "Permintaan tidak valid."
TOO_LARGE_TEXT = "Permintaan terlalu besar."
WRONG_TYPE_TEXT = "Jenis isi permintaan tidak didukung."
TEXT_TOO_LONG_TEXT = f"Pesannya terlalu panjang. Maksimal {MAX_CHAT_CHARS} karakter."
BAD_AUDIO_TEXT = "Rekamannya harus WAV mono 16-bit 16 kHz."
AUDIO_TOO_LONG_TEXT = f"Rekamannya terlalu panjang. Maksimal {TALK_MAX_S} detik."
CAMERA_OFF_TEXT = "Kamera HP tidak aktif."
BAD_PHOTO_TEXT = "Foto tidak valid."
VOICE_MISSING_TEXT = "Diagnosa suara tidak tersedia."
VOICE_GUEST_TEXT = "Diagnosa suara disembunyikan selama mode tamu."
VOICE_LIMIT_TEXT = "Setelan suara terlalu sering diubah. Tunggu sebentar, lalu coba lagi."
VOICE_BUSY_TEXT = "Perubahan tadi masih jalan. Tunggu sebentar."
# Said in her voice: `{owner}` is assistant.owner_call, used as written mid-sentence.
BUSY_TEXT = "Sebentar ya, {owner}, aku masih menjawab yang tadi."
FAILED_TEXT = "Maaf, {owner}, ada masalah di PC. Coba lagi nanti ya."

# The icon: her initial in white on a pink disc, on the page's background colour.
ICON_BACKGROUND = np.array((0xE9, 0xE4, 0xF7), dtype=float)
ICON_DISC = np.array((0xF0, 0x8C, 0xB8), dtype=float)
ICON_LETTER = (0xFF, 0xFF, 0xFF)
ICON_DISC_RADIUS = 0.46  # of the icon's width
ICON_CELLS = 12  # the icon's width in letter cells; a letter is 5 x 7 cells
# A 5 x 7 capital per letter, one number per row, its bit 16 the leftmost cell.
GLYPHS = {
    "A": (14, 17, 17, 31, 17, 17, 17),
    "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14),
    "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 15),
    "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 2, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 17, 25, 21, 19, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
    "Z": (31, 1, 2, 4, 8, 16, 31),
}
GLYPH_WIDTH = 5

# A phone closing the page mid-response surfaces as one of these.
_DISCONNECTS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class PhoneError(Exception):
    """The phone server cannot start; the message is Indonesian and safe to show."""


@dataclass(frozen=True)
class PhoneReply:
    """Her answer for the phone: what she says, with her voice and its mouth track, and the
    text card she shows with it, if any."""

    text: str  # without the emotion tag
    emotion: str  # one of avatar.emotion.EMOTIONS
    audio_wav: bytes | None  # her voice as a 16-bit WAV; None when it could not be made
    fps: int  # frames per second of `visemes`
    visemes: Sequence[Sequence[float]]  # per frame (aa, ih, ou, ee, oh), each 0..1
    duration: float  # seconds of audio
    card: Card | None = None  # shown with `text`, never voiced


class PhoneBackend(Protocol):
    """What answers the phone: in `waifu run`, the Assistant.

    Both are called on the server's request threads and may take seconds; one device has
    at most one call running, but two devices may call at once.
    """

    def chat(self, text: str, image: bytes | None = None, *, camera: bool = False) -> PhoneReply:
        """Her reply to `text`, not spoken on the PC; `image` is the JPEG photo the message
        carries, and `camera` tells that it was sent with the viewfinder open (always so with
        a photo, and also when the page could not take one). For a message not sent through
        the camera the server calls `chat(text)`."""
        ...

    def transcribe(self, wav_bytes: bytes) -> str:
        """The words in a mono 16-bit 16 kHz WAV; empty when nothing was understood."""
        ...


@dataclass(frozen=True)
class VoiceAnswer:
    """How a change on the Suara page went."""

    ok: bool
    message: str  # Indonesian, for the page
    pending: bool = False  # the mic switch waits for her to finish thinking or speaking


class VoiceBackend(Protocol):
    """What answers the phone's Suara page: in `waifu run`, the `VoicePanel`. Called on the
    server's request threads."""

    def status(self) -> Mapping[str, Any]:
        """The mic, its level, the gate, the choices, and the records, as JSON."""
        ...

    def switch_mic(self, device: str | None) -> VoiceAnswer:
        """Listen through `device` ("name, host API"), or follow the config again (None)."""
        ...

    def tune(
        self,
        *,
        speaker_threshold: float | None,
        vad_threshold_db: float | None,
        vad_margin_db: float | None,
    ) -> VoiceAnswer:
        """Use the settings given (None: unchanged); ValueError (Indonesian) when one is out
        of its bounds."""
        ...

    def reset(self) -> VoiceAnswer:
        """Put the config's settings back."""
        ...


class _BurnedCode:
    """When this server last burned a pairing code; a pairing forgets it.

    The page is told to make a new code (429) instead of to check the one it typed (403) by
    the refusal that burns the waiting code, and by a refusal for want of a code within
    CODE_LIFETIME after it. Any other refusal, a typo in a new code included, is a 403.
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._window_s = CODE_LIFETIME.total_seconds()
        self._burned_at: float | None = None
        self._lock = threading.Lock()

    def calls_for_a_new_code(self, refusal: Refusal) -> bool:
        """Notes `refusal`; whether the page must be told to make a new code."""
        with self._lock:
            now = self._clock()
            if refusal is Refusal.BURNED:
                self._burned_at = now
                return True
            burned_at = self._burned_at
            return (
                refusal is Refusal.NO_CODE
                and burned_at is not None
                and now - burned_at < self._window_s
            )

    def forget(self) -> None:
        with self._lock:
            self._burned_at = None


class PhoneServer(ThreadingHTTPServer):
    """Serves the phone page on `host:config.port`; a port in use raises PhoneError.

    `host` must be an IPv4 loopback address: Tailscale, not this server, faces the network.
    `clock` times how long a burned pairing code is remembered, and the quick actions' rate
    limit. `camera_max_side` turns the phone camera on: the longest side, in pixels, of the
    photos the page sends (None: off). `actions` are the quick buttons (None: none). `voice`
    answers the Suara page (None: none); `guest_mode` says when it is hidden.
    """

    daemon_threads = True
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second server bind the port of
    # one that is already listening; the two would silently split the requests.
    allow_reuse_address = sys.platform != "win32"
    # How long the accept loop may take to notice stop().
    poll_interval_s: float = 0.2

    def __init__(
        self,
        config: PhoneConfig,
        avatar: AvatarConfig,
        auth: PhoneAuth,
        backend: PhoneBackend,
        *,
        owner_call: str,
        assistant_name: str,
        web_root: Path = WEB_ROOT,
        host: str = LOOPBACK,
        clock: Callable[[], float] = time.monotonic,
        camera_max_side: int | None = None,
        actions: QuickActions | None = None,
        voice: VoiceBackend | None = None,
        guest_mode: Callable[[], bool] | None = None,
    ) -> None:
        if not is_ipv4_loopback(host):
            raise ValueError(f"The phone server listens only on IPv4 loopback, not {host!r}")
        self.auth = auth
        self.backend = backend
        self.camera_max_side = camera_max_side
        self.actions = actions
        self.voice = voice
        self.guest_mode = guest_mode or (lambda: False)
        self.files = AvatarFiles(avatar, web_root)
        self.page_root = (self.files.web_root / PAGE_FOLDER).resolve()
        settings = viewer_settings(avatar, assistant_name)
        self.page_config: dict[str, Any] = {
            "engine": settings["engine"],
            "model": settings["model"],
            "core": settings["core"],
            "credit": avatar.credit,
            "name": assistant_name,
            "owner_call": owner_call,
            "framing": "upper",
            "camera": {"enabled": camera_max_side is not None, "max_side": camera_max_side},
        }
        self.initial = assistant_name.strip()[:1].upper()
        self.busy_text = BUSY_TEXT.replace("{owner}", owner_call)
        self.failed_text = FAILED_TEXT.replace("{owner}", owner_call)
        self.burned_code = _BurnedCode(clock)
        self._clock = clock
        self._answering: set[str] = set()
        self._answering_lock = threading.Lock()
        self._acting: set[str] = set()
        self._action_limits: dict[str, RollingLimit] = {}
        self._acting_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        try:
            super().__init__((host, config.port), _PhoneRequestHandler)
        except OSError as exc:
            raise PhoneError(
                f"Port {host}:{config.port} sudah dipakai program lain (mungkin Cyrene yang "
                "lain masih berjalan) atau diblokir Windows. Tutup program itu atau ganti "
                "phone.port di config.yaml."
            ) from exc
        bound_host, port = self.server_address[:2]
        own = {f"{bound_host}:{port}", f"localhost:{port}"}
        self.own_hosts = frozenset(own | ({config.host} if config.host else set()))
        self.own_origins = frozenset(
            {f"http://{name}" for name in own}
            | ({f"https://{config.host}"} if config.host else set())
        )

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    def claim(self, device_id: str) -> bool:
        """Marks `device_id` as being answered; False while an earlier request of it still is."""
        with self._answering_lock:
            if device_id in self._answering:
                return False
            self._answering.add(device_id)
            return True

    def release(self, device_id: str) -> None:
        with self._answering_lock:
            self._answering.discard(device_id)

    def admit_action(self, device_id: str) -> bool:
        """Counts a quick action of `device_id`; False past ACTION_RATE_LIMIT in the window.

        Counted by the paired device: every request comes from 127.0.0.1 (tailscale serve).
        """
        with self._acting_lock:
            limit = self._action_limits.get(device_id)
            if limit is None:
                limit = RollingLimit(ACTION_RATE_LIMIT, ACTION_RATE_WINDOW_S, self._clock)
                self._action_limits[device_id] = limit
        return limit.admit()

    def claim_action(self, device_id: str) -> bool:
        """Marks `device_id` as running a quick action; False while one of it still runs.

        Apart from `claim`, so a button works while she answers the device's chat.
        """
        with self._acting_lock:
            if device_id in self._acting:
                return False
            self._acting.add(device_id)
            return True

    def release_action(self, device_id: str) -> None:
        with self._acting_lock:
            self._acting.discard(device_id)

    def start(self) -> None:
        """Serves in a daemon thread."""
        self._thread = threading.Thread(
            target=self.serve_forever,
            kwargs={"poll_interval": self.poll_interval_s},
            name="phone-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("Phone server listening on %s", self.url)

    def stop(self) -> None:
        """Stops serving and frees the port; safe to repeat."""
        if self._thread is not None:
            self.shutdown()
            self._thread.join()
            self._thread = None
        self.server_close()

    def __enter__(self) -> PhoneServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exception(), _DISCONNECTS):
            logger.debug("Phone client %s disconnected", client_address[0])
        else:
            logger.exception("Phone request from %s failed", client_address[0])


class JsonRequestHandler(BaseHTTPRequestHandler):
    """What a loopback server of strict JSON requests shares: a body read only within a limit,
    strict JSON fields, refusals as `{"error": text}` that read (or drain) a refused body first,
    JSON and file replies, and PROTECTING_HEADERS on every response. The phone server's handler
    and the PC page's (`desk.py`) extend it."""

    timeout = SOCKET_TIMEOUT_S
    # Whether this request's body has been read (a refusal then reads nothing more).
    _body_read = False
    # How much of a refused body, too large to read before the refusal, to drain after it.
    _drain_bytes = 0

    def finish(self) -> None:
        """Sends what is left of the answer, then drains a refused body left unread: on Windows
        a socket closed with unread data is reset, which the client, still sending, would get
        instead of the refusal."""
        try:
            super().finish()
        finally:
            if self._drain_bytes:
                self._drain(self._drain_bytes)

    def _drain(self, limit: int) -> None:
        """Reads and drops up to `limit` bytes of the request, for at most DRAIN_MAX_S."""
        deadline = time.monotonic() + DRAIN_MAX_S
        left = limit
        try:
            while left > 0 and (wait := deadline - time.monotonic()) > 0:
                self.connection.settimeout(wait)
                chunk = self.connection.recv(min(left, DRAIN_CHUNK_BYTES))
                if not chunk:
                    return
                left -= len(chunk)
        except OSError:  # a timeout, or the client went away
            return

    # --- request bodies -----------------------------------------------------------------------

    def _read_body(self, limit: int) -> bytes | None:
        """The body, read only when its Content-Length allows `limit`; None once refused."""
        length = _body_length(self.headers, limit)
        if length is HTTPStatus.LENGTH_REQUIRED:
            self._send_error(length, LENGTH_REQUIRED_TEXT)
            return None
        if length is HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
            self._send_error(length, TOO_LARGE_TEXT)
            return None
        if isinstance(length, HTTPStatus):
            self._send_error(length, BAD_REQUEST_TEXT)
            return None
        body = self.rfile.read(length)
        self._body_read = True
        if len(body) != length:
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
            return None
        return body

    def _json_fields(
        self, body: bytes, key: str, optional: Mapping[str, type] | None = None
    ) -> dict[str, Any] | None:
        """The fields of a JSON body `{key: "..."}`, which may also hold the `optional` fields,
        each of its type; None after refusing anything else."""
        if _content_type(self.headers) not in JSON_TYPES:
            self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, WRONG_TYPE_TEXT)
            return None
        fields = _json_object(body, key, optional or {})
        if fields is None:
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
        return fields

    def _discard_small_body(self) -> None:
        """Reads a refused request's unread body when it is at most REFUSAL_READ_BYTES, and
        marks a larger one to be drained once the refusal has been sent."""
        if self._body_read:
            return
        length = _body_length(self.headers, REFUSAL_READ_BYTES)
        if not isinstance(length, HTTPStatus):  # an IntEnum: test it before int
            self.rfile.read(length)
            self._body_read = True
        elif length is HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
            declared = _body_length(self.headers, DRAIN_MAX_BYTES)
            self._drain_bytes = DRAIN_MAX_BYTES if isinstance(declared, HTTPStatus) else declared

    # --- replies ------------------------------------------------------------------------------

    def end_headers(self) -> None:
        """Ends every response's headers, refusals and errors included, with PROTECTING_HEADERS."""
        for name, value in PROTECTING_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def _send_found(self, found: ServedFile | str | None) -> None:
        """Sends a file the browser may keep (the page itself is never kept), or a 404 with the
        reason `AvatarFiles.find` gave, if any."""
        if isinstance(found, ServedFile):
            send_file(self, found, revalidate=True)
        else:
            self._send_error(HTTPStatus.NOT_FOUND, found or NOT_FOUND_TEXT)

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        self._send_body(status, _json_bytes(payload))

    def _send_error(
        self, status: HTTPStatus, text: str, *, headers: Mapping[str, str] | None = None
    ) -> None:
        """Refuses the request with `{"error": text}`, reading a small unread body first."""
        self._discard_small_body()
        self._send_body(status, _json_bytes({"error": text}), headers=headers)

    def _send_body(
        self, status: HTTPStatus, body: bytes, *, headers: Mapping[str, str] | None = None
    ) -> None:
        """Sends a JSON body, never cached, and never with a HEAD answer."""
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPES[".json"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)


class _PhoneRequestHandler(JsonRequestHandler):
    server: PhoneServer

    def parse_request(self) -> bool:
        """Parses the request and refuses (403) any Host but this server's own names, and any
        request from the internet through Tailscale Funnel.

        A web page whose host name an attacker re-points at 127.0.0.1 (DNS rebinding)
        counts as same-origin with this server, but its requests still carry that name.
        Funnel opens the whole host:port to the internet with the tailnet's own Host.
        """
        if not super().parse_request():
            return False
        if self.request_version == "HTTP/0.9":
            # Without headers there is no Host, and HTTP/0.9 has no status line to refuse with.
            logger.debug("Ignored an HTTP/0.9 request from %s", self.client_address[0])
            return False
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in self.server.own_hosts:
            logger.warning(
                "Refused a phone request for host %r from %s", host, self.client_address[0]
            )
            self._send_error(HTTPStatus.FORBIDDEN, FOREIGN_HOST_TEXT)
            return False
        if FUNNEL_HEADER in self.headers:
            logger.warning(
                "Refused a phone request from the internet through Tailscale Funnel; turn "
                "Funnel off for this PC"
            )
            self._send_error(HTTPStatus.FORBIDDEN, FUNNEL_TEXT)
            return False
        return True

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in POST_ROUTES:
            self._refuse_method()
        elif path in ICON_SIZES:
            self._send_bytes(icon_png(ICON_SIZES[path], self.server.initial), "image/png")
        elif path in SHELL_FILES:
            self._send_found(web_file(self.server.page_root, SHELL_FILES[path], PAGE_CONTENT_TYPES))
        elif path.startswith(PAGE_PREFIX):
            relative = unquote(path.removeprefix(PAGE_PREFIX)).lstrip("/")
            self._send_found(web_file(self.server.page_root, relative, PAGE_CONTENT_TYPES))
        elif self._device() is None:
            self._send_error(HTTPStatus.UNAUTHORIZED, UNPAIRED_TEXT)
        elif path == CONFIG_URL:
            self._send_json(HTTPStatus.OK, self.server.page_config)
        elif path == ACTIONS_URL:
            actions = self.server.actions
            groups = [] if actions is None else actions.groups()
            self._send_json(HTTPStatus.OK, {"groups": [group_json(group) for group in groups]})
        elif path == VOICE_URL:
            self._voice_status()
        else:
            self._send_found(self.server.files.find(path))

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in POST_ROUTES:
            self._refuse_method()
            return
        origins = self.headers.get_all("Origin") or []
        if len(origins) != 1 or origins[0] not in self.server.own_origins:
            logger.warning(
                "Refused a phone %s from %s: foreign origin", path, self.client_address[0]
            )
            self._send_error(HTTPStatus.FORBIDDEN, FOREIGN_ORIGIN_TEXT)
            return
        if path == PAIR_URL:
            self._pair()
            return
        device = self._device()
        if device is None:
            self._send_error(HTTPStatus.UNAUTHORIZED, UNPAIRED_TEXT)
        elif path == CHAT_URL:
            self._chat(device)
        elif path == ACTION_URL:
            self._action(device)
        elif path == VOICE_SET_URL:
            self._voice_set(device)
        else:
            self._talk(device)

    def _refuse_method(self) -> None:
        if urlsplit(self.path).path in POST_ROUTES:
            allowed, text = "POST", POST_ONLY_TEXT
        else:
            allowed, text = "GET", METHOD_NOT_ALLOWED_TEXT
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, text, headers={"Allow": allowed})

    do_HEAD = do_PUT = do_DELETE = do_PATCH = _refuse_method
    do_OPTIONS = do_CONNECT = do_TRACE = _refuse_method

    def _device(self) -> Device | None:
        """The paired device of the first device cookie of this request PhoneAuth knows, or None.

        Each of the first MAX_DEVICE_COOKIES cookies of that name shaped like a token is
        tried: a cookie another page of the site set ahead of the real one never hides it.
        """
        tokens = [
            token for token in _cookies(self.headers, COOKIE_NAME) if TOKEN_SHAPE.fullmatch(token)
        ]
        for token in tokens[:MAX_DEVICE_COOKIES]:
            device = self.server.auth.check(token)
            if device is not None:
                return device
        return None

    # --- pairing ------------------------------------------------------------------------------

    def _pair(self) -> None:
        body = self._read_body(PAIR_MAX_BYTES)
        if body is None:
            return
        fields = self._json_fields(body, "code")
        if fields is None:
            return
        code = fields["code"]
        if not PAIR_CODE.fullmatch(code):
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
            return
        token = self.server.auth.redeem(code, DEVICE_NAME)
        if isinstance(token, Refusal):
            if self.server.burned_code.calls_for_a_new_code(token):
                self._send_error(HTTPStatus.TOO_MANY_REQUESTS, CODE_BURNED_TEXT)
            else:
                self._send_error(HTTPStatus.FORBIDDEN, WRONG_CODE_TEXT)
            return
        self.server.burned_code.forget()
        self.send_response(HTTPStatus.NO_CONTENT)
        # Secure also holds on http://127.0.0.1: browsers treat loopback as a secure context.
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={token}; Max-Age={COOKIE_MAX_AGE_S}; Path=/; Secure; HttpOnly; "
            "SameSite=Strict",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    # --- her answers --------------------------------------------------------------------------

    def _chat(self, device: Device) -> None:
        """`{"text"}`, or with the viewfinder open `{"text", "camera"}` and the photo `"image"`."""
        body = self._read_body(self._body_limit(CHAT_MAX_BYTES, CAMERA_CHAT_MAX_BYTES))
        if body is None:
            return
        fields = self._json_fields(body, "text", MESSAGE_EXTRAS)
        if fields is None:
            return
        text = fields["text"].strip()
        if not text:
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
        elif len(text) > MAX_CHAT_CHARS:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, TEXT_TOO_LONG_TEXT)
        else:
            self._answer_message(
                device,
                "chat",
                fields,
                lambda backend, photo, camera: _reply_fields(_ask(backend, text, photo, camera)),
            )

    def _talk(self, device: Device) -> None:
        """A recording as an `audio/wav` body, or as JSON `{"audio"}`, which may also hold the
        viewfinder's `"camera"` and `"image"` as a chat does."""
        body = self._read_body(self._body_limit(TALK_MAX_BYTES, CAMERA_TALK_MAX_BYTES))
        if body is None:
            return
        content_type = _content_type(self.headers)
        if content_type == WAV_TYPE:
            sent, fields = body, {}
        elif content_type in JSON_TYPES:
            from_json = self._json_recording(body)
            if from_json is None:
                return
            sent, fields = from_json
        else:
            self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, WRONG_TYPE_TEXT)
            return
        problem = _recording_problem(sent)
        if problem is HTTPStatus.BAD_REQUEST:
            self._send_error(problem, BAD_AUDIO_TEXT)
        elif problem is HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
            self._send_error(problem, AUDIO_TOO_LONG_TEXT)
        else:
            self._answer_message(
                device,
                "talk",
                fields,
                lambda backend, photo, camera: _talk_fields(backend, sent, photo, camera),
            )

    def _json_recording(self, body: bytes) -> tuple[bytes, dict[str, Any]] | None:
        """The WAV and the fields of a JSON /talk body; None after refusing it.

        The recording must be strict base64 of at most TALK_MAX_BYTES, as an audio/wav body is.
        """
        fields = self._json_fields(body, "audio", MESSAGE_EXTRAS)
        if fields is None:
            return None
        try:
            sent = base64.b64decode(fields["audio"], validate=True)
        except ValueError:  # binascii.Error, and a str that is not ASCII
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_AUDIO_TEXT)
            return None
        if len(sent) > TALK_MAX_BYTES:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, TOO_LARGE_TEXT)
            return None
        return sent, fields

    def _answer_message(
        self,
        device: Device,
        route: str,
        fields: Mapping[str, Any],
        ask: Callable[[PhoneBackend, bytes | None, bool], Mapping[str, Any]],
    ) -> None:
        """Answers a message with what came through the viewfinder: whether it was open
        (`"camera"`, or a photo), and the photo in base64 (`"image"`), if any.

        Anything sent through the camera is refused (400) while it is off, and a photo when it
        is not a JPEG of a size the page makes (`_camera_photo`).
        """
        image = fields.get("image")
        camera = fields.get("camera", False) or image is not None
        photo = None
        if camera:
            max_side = self.server.camera_max_side
            if max_side is None:
                logger.info(
                    "Refused a phone %s of device %s sent through the camera: camera off",
                    route,
                    device.id,
                )
                self._send_error(HTTPStatus.BAD_REQUEST, CAMERA_OFF_TEXT)
                return
            if image is not None:
                photo = self._photo(image, max_side, device, route)
                if photo is None:
                    return
        self._answer(device, route, lambda backend: ask(backend, photo, camera))

    def _photo(self, image: str, max_side: int, device: Device, route: str) -> bytes | None:
        """The JPEG of a message's base64 `image`, or None after refusing it."""
        checked = _camera_photo(image, max_side)
        if checked is None:
            logger.info(
                "Refused a photo with a phone %s of device %s: not a JPEG of %d to %d pixels "
                "a side within %d bytes",
                route,
                device.id,
                PHOTO_MIN_SIDE,
                max_side,
                PHOTO_MAX_BYTES,
            )
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_PHOTO_TEXT)
            return None
        photo, (width, height) = checked
        logger.info(
            "A phone %s of device %s carries a photo of %d bytes, %dx%d",
            route,
            device.id,
            len(photo),
            width,
            height,
        )
        return photo

    def _answer(
        self,
        device: Device,
        route: str,
        ask: Callable[[PhoneBackend], Mapping[str, Any]],
    ) -> None:
        """Asks the backend for `device`, one request at a time, and sends its JSON answer."""
        if not self.server.claim(device.id):
            logger.info("Refused a phone %s of device %s: still answering it", route, device.id)
            self._send_error(HTTPStatus.TOO_MANY_REQUESTS, self.server.busy_text)
            return
        try:
            body = _json_bytes(ask(self.server.backend))
        except Exception:
            logger.exception("Answering the phone %s of device %s failed", route, device.id)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, self.server.failed_text)
            return
        finally:
            self.server.release(device.id)
        logger.debug("Answered the phone %s of device %s", route, device.id)
        self._send_body(HTTPStatus.OK, body)

    # --- quick buttons ------------------------------------------------------------------------

    def _action(self, device: Device) -> None:
        """`{"id"}` of a button GET /actions lists: runs it and answers `{"ok", "message"}`.

        The rate limit counts before the body is read, so a flood of anything costs the same.
        A press while the device's last one still runs is a 409, not the limit's 429, so the
        page can tell the two apart. A device's failure is an answer too (`ok: false`): the
        page shows its message.
        """
        if not self.server.admit_action(device.id):
            logger.info(
                "Refused a quick action of phone device %s: more than %d in %d s",
                device.id,
                ACTION_RATE_LIMIT,
                ACTION_RATE_WINDOW_S,
            )
            self._send_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                ACTION_LIMIT_TEXT,
                headers={"Retry-After": str(ACTION_RATE_WINDOW_S)},
            )
            return
        body = self._read_body(ACTION_MAX_BYTES)
        if body is None:
            return
        fields = self._json_fields(body, "id")
        if fields is None:
            return
        action_id = fields["id"]
        if not ACTION_ID.fullmatch(action_id):
            logger.info("Refused a quick action of phone device %s: not a button id", device.id)
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
            return
        actions = self.server.actions
        action = None if actions is None else actions.find(action_id)
        if actions is None or action is None:
            logger.info(
                "Refused quick action %s of phone device %s: no such button", action_id, device.id
            )
            self._send_error(HTTPStatus.NOT_FOUND, ACTION_UNKNOWN_TEXT)
            return
        if not self.server.claim_action(device.id):
            logger.info(
                "Refused quick action %s of phone device %s: its last one still runs",
                action_id,
                device.id,
            )
            self._send_error(HTTPStatus.CONFLICT, ACTION_BUSY_TEXT)
            return
        try:
            result = actions.run(action)
        except Exception:
            logger.exception("Quick action %s of phone device %s failed", action_id, device.id)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, self.server.failed_text)
            return
        finally:
            self.server.release_action(device.id)
        outcome = "ok" if result.ok else "failed"
        logger.info("Phone device %s ran quick action %s: %s", device.id, action_id, outcome)
        logger.debug("Quick action %s: %s", action_id, result.message)
        self._send_json(HTTPStatus.OK, {"ok": result.ok, "message": result.message})

    # --- the Suara page -----------------------------------------------------------------------

    def _voice_available(self) -> VoiceBackend | None:
        """The panel, or None after refusing: hidden in guest mode (the records hold what was
        said), missing without one."""
        if self.server.guest_mode():
            self._send_error(HTTPStatus.FORBIDDEN, VOICE_GUEST_TEXT)
            return None
        if self.server.voice is None:
            self._send_error(HTTPStatus.NOT_FOUND, VOICE_MISSING_TEXT)
        return self.server.voice

    def _voice_status(self) -> None:
        voice = self._voice_available()
        if voice is None:
            return
        try:
            status = voice.status()
        except Exception:
            logger.exception("The voice status for the phone failed")
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, self.server.failed_text)
            return
        self._send_json(HTTPStatus.OK, status)

    def _voice_set(self, device: Device) -> None:
        """`{"action": "mic", "device"}`, `{"action": "tune", ...}`, or `{"action": "reset"}`:
        answers `{"ok", "message", "pending"}`. Counted with the quick buttons (before the body
        is read) and one at a time with them; a setting out of its bounds is a 400 with the
        panel's reason."""
        voice = self._voice_available()
        if voice is None:
            return
        if not self.server.admit_action(device.id):
            logger.info("Refused a voice change of phone device %s: too many", device.id)
            self._send_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                VOICE_LIMIT_TEXT,
                headers={"Retry-After": str(ACTION_RATE_WINDOW_S)},
            )
            return
        body = self._read_body(VOICE_SET_MAX_BYTES)
        if body is None:
            return
        if _content_type(self.headers) not in JSON_TYPES:
            self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, WRONG_TYPE_TEXT)
            return
        change = _voice_change(body)
        if change is None:
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
            return
        if not self.server.claim_action(device.id):
            self._send_error(HTTPStatus.CONFLICT, VOICE_BUSY_TEXT)
            return
        action, fields = change
        try:
            answer = _change_voice(voice, action, fields)
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception:
            logger.exception("The voice change %s of phone device %s failed", action, device.id)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, self.server.failed_text)
            return
        finally:
            self.server.release_action(device.id)
        logger.info("Phone device %s changed the voice (%s): ok %s", device.id, action, answer.ok)
        self._send_json(
            HTTPStatus.OK, {"ok": answer.ok, "message": answer.message, "pending": answer.pending}
        )

    # --- request bodies -----------------------------------------------------------------------

    def _body_limit(self, plain: int, with_photo: int) -> int:
        """`with_photo` for a JSON body while the camera is on (it may carry a photo), else
        `plain`; chosen from the Content-Type before the body is read."""
        if self.server.camera_max_side is not None and _content_type(self.headers) in JSON_TYPES:
            return with_photo
        return plain

    # --- replies ------------------------------------------------------------------------------

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@functools.cache
def icon_png(size: int, initial: str) -> bytes:
    """The app icon, `size` pixels square: `initial` (A-Z) on a pink disc.

    Any other initial leaves the disc plain.
    """
    rows, columns = np.mgrid[0:size, 0:size] + 0.5
    distance = np.hypot(rows - size / 2, columns - size / 2)
    # How much of each pixel the disc covers: a one-pixel ramp keeps its edge smooth.
    cover = np.clip(ICON_DISC_RADIUS * size - distance + 0.5, 0.0, 1.0)[..., None]
    image = (1.0 - cover) * ICON_BACKGROUND + cover * ICON_DISC
    glyph = GLYPHS.get(initial)
    if glyph is not None:
        cell = size // ICON_CELLS
        bits = np.array(
            [
                [(row >> (GLYPH_WIDTH - 1 - column)) & 1 for column in range(GLYPH_WIDTH)]
                for row in glyph
            ],
            dtype=bool,
        )
        letter = bits.repeat(cell, axis=0).repeat(cell, axis=1)
        top = (size - letter.shape[0]) // 2
        left = (size - letter.shape[1]) // 2
        image[top : top + letter.shape[0], left : left + letter.shape[1]][letter] = ICON_LETTER
    return encode_png(np.round(image).astype(np.uint8))


def is_ipv4_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and address.is_loopback


def _cookies(headers: Message, name: str) -> list[str]:
    """The values of every cookie `name` in the request's Cookie headers, in their order.

    Read by hand: http.cookies.SimpleCookie drops every cookie of a header when one of them
    is not to its liking, and browsers do not keep cookies apart by port, so any other
    program's page on 127.0.0.1 may add one.
    """
    values = []
    for header in headers.get_all("Cookie") or []:
        for pair in header.split(";"):
            key, separator, value = pair.strip().partition("=")
            if separator and key == name:
                values.append(value.strip())
    return values


def _content_type(headers: Message) -> str:
    """The request's Content-Type, lowercased and without whitespace (`a/b;charset=utf-8`)."""
    return "".join((headers.get("Content-Type") or "").split()).lower()


def _body_length(headers: Message, limit: int) -> int | HTTPStatus:
    """The body length a request declares when it is at most `limit`.

    Otherwise the status that refuses it: LENGTH_REQUIRED without a Content-Length,
    BAD_REQUEST for several or one that is not plain decimal digits, and
    REQUEST_ENTITY_TOO_LARGE above `limit`.
    """
    lengths = headers.get_all("Content-Length") or []
    if not lengths:
        return HTTPStatus.LENGTH_REQUIRED
    length = lengths[0].strip()
    if len(lengths) != 1 or not (length.isascii() and length.isdigit()):
        return HTTPStatus.BAD_REQUEST
    digits = length.lstrip("0") or "0"
    # The digits are counted before int(), which refuses numbers over 4300 digits.
    if len(digits) > len(str(limit)) or int(digits) > limit:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    return int(digits)


def _json_object(body: bytes, key: str, optional: Mapping[str, type]) -> dict[str, Any] | None:
    """The fields of a JSON body `{key: str}` that may also hold the `optional` fields, each
    of exactly its type (a bool is no number, null is nothing), every string valid UTF-8
    text; None otherwise."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):  # also UnicodeDecodeError; deep nesting on 3.11-3.13
        return None
    types = {**optional, key: str}
    if not isinstance(data, dict) or key not in data or not data.keys() <= types.keys():
        return None
    if not all(type(value) is types[name] for name, value in data.items()):
        return None
    try:
        for value in data.values():
            if isinstance(value, str):
                value.encode("utf-8")  # a lone surrogate from a \ud800 escape is not text
    except UnicodeEncodeError:
        return None
    return data


def _voice_change(body: bytes) -> tuple[str, dict[str, Any]] | None:
    """The action and fields of a POST /voice/set body, or None for anything else: `mic` with
    exactly a `device` (a mic's "name, host API", or null), `tune` with one or more of
    VOICE_TUNINGS (each an int or a float, finite, never a bool), `reset` alone."""
    try:
        data = json.loads(body.decode("utf-8"), parse_constant=_refuse_json_constant)
    except (ValueError, RecursionError):  # also UnicodeDecodeError, NaN and Infinity
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    fields = {name: value for name, value in data.items() if name != "action"}
    if action == "mic" and fields.keys() == {"device"}:
        device = fields["device"]
        return (action, fields) if device is None or _mic_name(device) else None
    if action == "tune" and fields and fields.keys() <= set(VOICE_TUNINGS):
        return (action, fields) if all(map(_finite_number, fields.values())) else None
    if action == "reset" and not fields:
        return action, fields
    return None


def _refuse_json_constant(name: str) -> object:
    raise ValueError(f"{name} is not a number")


def _mic_name(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > VOICE_DEVICE_MAX_CHARS:
        return False
    try:
        value.encode("utf-8")  # a lone surrogate from a \ud800 escape is not text
    except UnicodeEncodeError:
        return False
    return True


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):  # a bool is no number
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int beyond any float
        return False


def _change_voice(voice: VoiceBackend, action: str, fields: Mapping[str, Any]) -> VoiceAnswer:
    if action == "mic":
        return voice.switch_mic(fields["device"])
    if action == "tune":
        return voice.tune(**{name: fields.get(name) for name in VOICE_TUNINGS})
    return voice.reset()


def _camera_photo(image: str, max_side: int) -> tuple[bytes, tuple[int, int]] | None:
    """The JPEG of the base64 `image` and its (width, height), or None when it is not a photo
    the camera page makes.

    That is strict base64 of at most PHOTO_MAX_BYTES that starts like a JPEG and whose frame
    header gives a longer side of at most `max_side` and a shorter one of at least
    PHOTO_MIN_SIDE pixels.
    """
    try:
        photo = base64.b64decode(image, validate=True)
    except ValueError:  # binascii.Error, and a str that is not ASCII
        return None
    if len(photo) > PHOTO_MAX_BYTES or not photo.startswith(JPEG_START):
        return None
    try:
        width, height = jpeg_size(photo)
    except ValueError:
        return None
    if min(width, height) < PHOTO_MIN_SIDE or max(width, height) > max_side:
        return None
    return photo, (width, height)


def _recording_problem(body: bytes) -> HTTPStatus | None:
    """Why POST /talk refuses `body`, or None for a mono 16-bit PCM WAV at 16 kHz.

    BAD_REQUEST for anything else, an empty recording, or one whose data is shorter than it
    declares; REQUEST_ENTITY_TOO_LARGE for one over TALK_MAX_S.
    """
    try:
        with wave.open(io.BytesIO(body), "rb") as recording:
            shape = (
                recording.getnchannels(),
                recording.getsampwidth(),
                recording.getframerate(),
            )
            frames = recording.getnframes()
            data = recording.readframes(frames)
    # wave raises EOFError for a cut-off header and RuntimeError for a chunk that claims
    # to reach past the RIFF chunk holding it.
    except (wave.Error, EOFError, RuntimeError):
        return HTTPStatus.BAD_REQUEST
    if shape != (1, TALK_SAMPLE_BYTES, TALK_RATE) or frames == 0:
        return HTTPStatus.BAD_REQUEST
    if len(data) != frames * TALK_SAMPLE_BYTES:
        return HTTPStatus.BAD_REQUEST
    if frames > TALK_MAX_S * TALK_RATE:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    return None


def _talk_fields(
    backend: PhoneBackend, wav_bytes: bytes, photo: bytes | None, camera: bool
) -> dict[str, Any]:
    """The /talk answer: what she heard and her reply, or no reply when nothing was heard."""
    heard = backend.transcribe(wav_bytes).strip()
    if not heard:
        return {"heard": "", "text": None}
    return {"heard": heard, **_reply_fields(_ask(backend, heard, photo, camera))}


def _ask(backend: PhoneBackend, text: str, photo: bytes | None, camera: bool) -> PhoneReply:
    """Her reply to `text`, with the `photo` (if any) of a message sent through the camera; a
    message not sent through it is the text-only call of v1.14."""
    if not camera:
        return backend.chat(text)
    return backend.chat(text, image=photo, camera=True)


def _reply_fields(reply: PhoneReply) -> dict[str, Any]:
    """The /chat answer; the mouth track rounded like the avatar's speak events."""
    audio = None if reply.audio_wav is None else base64.b64encode(reply.audio_wav).decode("ascii")
    return {
        "text": reply.text,
        "emotion": reply.emotion,
        "audio": audio,
        "fps": reply.fps,
        "visemes": [[round(float(weight), 2) for weight in frame] for frame in reply.visemes],
        "duration": round(float(reply.duration), 3),
    }


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    """`payload` as compact UTF-8 JSON; a NaN or infinity is a ValueError, never sent."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
