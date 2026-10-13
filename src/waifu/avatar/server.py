"""Loopback HTTP server for the avatar viewer: the page, the model, events, and interactions.

The mascot window loads `index.html` from here, reads `/config.json` (which engine
and model to show, and the token for interactions), fetches the model (`/model.vrm`,
or the Live2D model folder under `/model/` and the owner's Cubism Core), listens to
`/events`, a Server-Sent Events stream fed by the `AvatarBus`, and posts the owner's
pats, pokes, and double-clicks to `/interact`. Every connection gets its own daemon
thread, so a long-lived event stream never holds up the page or the model, and none
of them keep Cyrene alive.

Only Cyrene's own page may use it: every request must name the server's own address
as Host (DNS rebinding), an interaction must come from the page's origin and carry
the token that only `/config.json` reveals, and every response tells the browser that
no other website may embed it or show it in a frame.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import shutil
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit

from waifu.avatar.bus import (
    LIVE2D_CORE_URL,
    LIVE2D_MODEL_PREFIX,
    MODEL_URL,
    AvatarBus,
    Event,
    Subscription,
    viewer_settings,
)
from waifu.config import AvatarConfig

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent / "web"
INDEX = "index.html"
EVENTS_URL = "/events"
CONFIG_URL = "/config.json"
INTERACT_URL = "/interact"
MODEL_CONTENT_TYPE = "model/gltf-binary"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"
# The files a Live2D model folder holds for the engine (model3, physics, cdi, moc3,
# textures); anything else in the folder, such as the author's notes, is not served.
LIVE2D_CONTENT_TYPES = {
    ".json": "application/json",
    ".png": "image/png",
    ".moc3": "application/octet-stream",
}
INTERACTION_KINDS = ("head_pat", "body_touch", "double_click", "welcome_back")
INTERACT_MAX_BYTES = 4096
# At most this many interactions per rolling second, from every client together.
INTERACT_RATE_LIMIT = 10
INTERACT_RATE_WINDOW_S = 1.0
TOKEN_BYTES = 24
SSE_RETRY_MS = 2000
FILE_CHUNK_BYTES = 64 * 1024
# A client that stops sending its request, or stops reading the stream, is dropped.
SOCKET_TIMEOUT_S = 30
NOT_FOUND_TEXT = "Tidak ditemukan."
MODEL_NOT_FOUND_TEXT = "Model VRM tidak ditemukan. Atur avatar.model di config.yaml."
CORE_NOT_FOUND_TEXT = (
    "Cubism Core tidak ditemukan. Atur avatar.live2d_core di config.yaml ke "
    "live2dcubismcore.min.js dari Cubism SDK for Web."
)
METHOD_NOT_ALLOWED_TEXT = "Hanya GET yang didukung."
POST_ONLY_TEXT = "Hanya POST yang didukung."
FOREIGN_HOST_TEXT = "Server avatar Cyrene hanya melayani alamat 127.0.0.1 atau localhost."
FOREIGN_ORIGIN_TEXT = "Interaksi hanya diterima dari halaman avatar Cyrene sendiri."
WRONG_TOKEN_TEXT = "Token interaksi tidak cocok."
BAD_INTERACTION_TEXT = "Interaksi tidak valid."
LENGTH_REQUIRED_TEXT = "Interaksi harus menyertakan Content-Length."
TOO_LARGE_TEXT = "Interaksi terlalu besar."
TOO_MANY_TEXT = "Terlalu banyak interaksi. Coba lagi sebentar."

# Sent with every response. The Host check stops DNS rebinding, but a page elsewhere can
# still embed http://127.0.0.1:<port>/... (its requests name this server as Host): the
# Core as a script, the textures as images, the page in a hidden frame that turns clicks
# into pats. These keep everything to the page's own origin and the page out of frames.
PROTECTING_HEADERS = {
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}

# A viewer closing its window mid-response surfaces as one of these.
_DISCONNECTS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
# Never part of a web path. A backslash could form a UNC path (resolving it would
# contact another host), a colon a drive or an NTFS stream, a NUL breaks the OS call.
_FORBIDDEN_PATH_CHARS = ("\\", ":", "\0")


class AvatarError(Exception):
    """The avatar cannot be shown; the message is Indonesian and safe to show."""


class _RollingLimit:
    """Admits at most `limit` events within any `window_s` seconds; thread-safe."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float]) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._admitted: deque[float] = deque()
        self._lock = threading.Lock()

    def admit(self) -> bool:
        with self._lock:
            now = self._clock()
            while self._admitted and now - self._admitted[0] >= self._window_s:
                self._admitted.popleft()
            if len(self._admitted) >= self._limit:
                return False
            self._admitted.append(now)
            return True


class AvatarServer(ThreadingHTTPServer):
    """Serves the viewer on `config.host:config.port`; bind failures raise AvatarError.

    `on_interact(kind)` receives each accepted interaction on a request thread, so it
    must return quickly; its exceptions are logged.
    """

    daemon_threads = True
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second server bind the port
    # of one that is already listening; the two would silently split the viewers.
    allow_reuse_address = sys.platform != "win32"
    # A `: ping` comment on a quiet event stream keeps it from looking dead.
    ping_interval_s: float = 15.0
    # How long the accept loop may take to notice stop().
    poll_interval_s: float = 0.2

    def __init__(
        self,
        config: AvatarConfig,
        bus: AvatarBus,
        *,
        web_root: Path = WEB_ROOT,
        on_interact: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus = bus
        self.web_root = web_root.resolve()
        self.engine = config.engine
        self.model_path = config.model
        live2d = config.engine == "live2d"
        # A model3.json loads its moc3, physics, and textures from its own folder.
        self.model_root = (
            config.model.parent.resolve() if live2d and config.model is not None else None
        )
        self.core_path = config.live2d_core if live2d else None
        self.on_interact = on_interact
        self.interaction_limit = _RollingLimit(INTERACT_RATE_LIMIT, INTERACT_RATE_WINDOW_S, clock)
        self._token = secrets.token_urlsafe(TOKEN_BYTES)
        self._viewer: Event = viewer_settings(config, bus.name)
        self._thread: threading.Thread | None = None
        try:
            super().__init__((config.host, config.port), _AvatarRequestHandler)
        except OSError as exc:
            raise AvatarError(
                f"Port {config.host}:{config.port} sudah dipakai program lain (mungkin Cyrene "
                "yang lain masih berjalan) atau diblokir Windows. Tutup program itu atau ganti "
                "avatar.port di config.yaml."
            ) from exc
        bus.set_viewer_settings(self._viewer)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

    @property
    def token(self) -> str:
        """The secret the page sends with every interaction; only /config.json reveals it."""
        return self._token

    def page_config(self) -> Event:
        """The body of /config.json: the hello settings plus the interaction token."""
        return {**self._viewer, "token": self._token}

    def own_hosts(self) -> set[str]:
        """The Host values that name this server: its own address or localhost, with the port."""
        host, port = self.server_address[:2]
        return {f"{host}:{port}", f"localhost:{port}"}

    def start(self) -> None:
        """Serves in a daemon thread."""
        self._thread = threading.Thread(
            target=self.serve_forever,
            kwargs={"poll_interval": self.poll_interval_s},
            name="avatar-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("Avatar server listening on %s", self.url)

    def stop(self) -> None:
        """Ends every event stream, stops serving, and frees the port; safe to repeat."""
        self.bus.close()
        if self._thread is not None:
            self.shutdown()
            self._thread.join()
            self._thread = None
        self.server_close()

    def __enter__(self) -> AvatarServer:
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
            logger.debug("Avatar viewer %s disconnected", client_address[0])
        else:
            logger.exception("Avatar request from %s failed", client_address[0])


class _AvatarRequestHandler(BaseHTTPRequestHandler):
    server: AvatarServer
    timeout = SOCKET_TIMEOUT_S

    def parse_request(self) -> bool:
        """Parses the request and refuses (403) any Host but this server's own address.

        A web page whose host name an attacker re-points at 127.0.0.1 (DNS rebinding)
        counts as same-origin with this server, but its requests still carry that name.
        """
        if not super().parse_request():
            return False
        host = (self.headers.get("Host") or "").strip().lower()
        if host in self.server.own_hosts():
            return True
        logger.warning(
            "Refused an avatar request for host %r from %s", host, self.client_address[0]
        )
        self._discard_small_body()
        self._send_text(HTTPStatus.FORBIDDEN, FOREIGN_HOST_TEXT)
        return False

    def _discard_small_body(self) -> None:
        """Reads a refused request's body, up to INTERACT_MAX_BYTES, before answering.

        On Windows a socket closed with unread data is reset, and the reset can throw away
        the refusal before the client has read it. A larger body is left unread.
        """
        lengths = self.headers.get_all("Content-Length") or []
        length = lengths[0].strip() if len(lengths) == 1 else ""
        if not (length.isascii() and length.isdigit()):
            return
        if len(length) <= len(str(INTERACT_MAX_BYTES)) and int(length) <= INTERACT_MAX_BYTES:
            self.rfile.read(int(length))

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == EVENTS_URL:
            self._stream_events()
        elif path == CONFIG_URL:
            self._send_json(HTTPStatus.OK, self.server.page_config())
        elif path == MODEL_URL:
            self._send_model()
        elif path.startswith(LIVE2D_MODEL_PREFIX):
            self._send_live2d_file(path.removeprefix(LIVE2D_MODEL_PREFIX))
        elif path == LIVE2D_CORE_URL:
            self._send_core()
        elif path == INTERACT_URL:
            self._refuse_method()
        else:
            self._send_static(path)

    def do_POST(self) -> None:
        if urlsplit(self.path).path == INTERACT_URL:
            self._interact()
        else:
            self._refuse_method()

    def _refuse_method(self) -> None:
        if urlsplit(self.path).path == INTERACT_URL:
            allowed, text = "POST", POST_ONLY_TEXT
        else:
            allowed, text = "GET", METHOD_NOT_ALLOWED_TEXT
        self._discard_small_body()
        self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, text, headers={"Allow": allowed})

    do_HEAD = do_PUT = do_DELETE = do_PATCH = _refuse_method
    do_OPTIONS = do_CONNECT = do_TRACE = _refuse_method

    # --- the event stream ---------------------------------------------------------------------

    def _stream_events(self) -> None:
        subscription = self.server.bus.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self._write_event_stream(subscription)
        except _DISCONNECTS:
            logger.debug("Avatar viewer %s disconnected", self.address_string())
        finally:
            self.server.bus.unsubscribe(subscription)

    def _write_event_stream(self, subscription: Subscription) -> None:
        self._write(f"retry: {SSE_RETRY_MS}\n\n")
        while True:
            event = subscription.get(timeout=self.server.ping_interval_s)
            if subscription.closed:
                return
            if event is None:
                self._write(": ping\n\n")
            else:
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                self._write(f"data: {data}\n\n")

    def _write(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()

    # --- files --------------------------------------------------------------------------------

    def _send_model(self) -> None:
        if self.server.engine != "vrm":
            self._send_text(HTTPStatus.NOT_FOUND, NOT_FOUND_TEXT)
            return
        model = self.server.model_path
        if model is None or not model.is_file():
            logger.warning("Avatar model not found: %s", model)
            self._send_text(HTTPStatus.NOT_FOUND, MODEL_NOT_FOUND_TEXT)
            return
        self._send_file(model, MODEL_CONTENT_TYPE)

    def _send_live2d_file(self, url_path: str) -> None:
        """A file of the Live2D model folder; `url_path` is relative to `/model/`."""
        root = self.server.model_root
        path = None if root is None else _file_under(root, unquote(url_path))
        content_type = None if path is None else LIVE2D_CONTENT_TYPES.get(path.suffix.lower())
        if path is None or content_type is None:
            self._send_text(HTTPStatus.NOT_FOUND, NOT_FOUND_TEXT)
            return
        self._send_file(path, content_type)

    def _send_core(self) -> None:
        core = self.server.core_path
        if core is None:
            self._send_text(HTTPStatus.NOT_FOUND, NOT_FOUND_TEXT)
            return
        if not core.is_file():
            logger.warning("Live2D Cubism Core not found: %s", core)
            self._send_text(HTTPStatus.NOT_FOUND, CORE_NOT_FOUND_TEXT)
            return
        self._send_file(core, CONTENT_TYPES[".js"])

    def _send_static(self, url_path: str) -> None:
        path = _file_under(self.server.web_root, unquote(url_path).lstrip("/") or INDEX)
        if path is None:
            self._send_text(HTTPStatus.NOT_FOUND, NOT_FOUND_TEXT)
            return
        suffix = path.suffix.lower()
        self._send_file(
            path,
            CONTENT_TYPES.get(suffix, DEFAULT_CONTENT_TYPE),
            cache_control="no-store" if suffix == ".html" else None,
        )

    def _send_file(
        self, path: Path, content_type: str, *, cache_control: str | None = None
    ) -> None:
        with path.open("rb") as file:
            size = os.fstat(file.fileno()).st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            if cache_control is not None:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            shutil.copyfileobj(file, self.wfile, FILE_CHUNK_BYTES)

    # --- interactions -------------------------------------------------------------------------

    def _interact(self) -> None:
        """Hands one pat, poke, double-click, or welcome-back of the page to `on_interact`.

        The body (at most 4 KB) is read before anything else is checked: Windows resets a
        connection closed with unread data, and the page would get no answer at all.
        """
        body = self._read_interaction_body()
        if body is None:
            return
        origins = self.headers.get_all("Origin") or []
        own_origins = {f"http://{host}" for host in self.server.own_hosts()}
        if len(origins) != 1 or origins[0] not in own_origins:
            self._refuse_interaction(HTTPStatus.FORBIDDEN, FOREIGN_ORIGIN_TEXT, "foreign origin")
            return
        if not self.server.interaction_limit.admit():
            logger.debug(
                "Refused an avatar interaction: more than %d a second", INTERACT_RATE_LIMIT
            )
            self._send_text(
                HTTPStatus.TOO_MANY_REQUESTS, TOO_MANY_TEXT, headers={"Retry-After": "1"}
            )
            return
        fields = _interaction_fields(self.headers.get("Content-Type", ""), body)
        if fields is None:
            self._refuse_interaction(HTTPStatus.BAD_REQUEST, BAD_INTERACTION_TEXT, "malformed body")
            return
        token, kind = fields
        expected = self.server.token.encode("ascii")
        if not hmac.compare_digest(token.encode("utf-8", "surrogatepass"), expected):
            self._refuse_interaction(HTTPStatus.FORBIDDEN, WRONG_TOKEN_TEXT, "wrong token")
            return
        if kind not in INTERACTION_KINDS:
            self._refuse_interaction(HTTPStatus.BAD_REQUEST, BAD_INTERACTION_TEXT, "unknown kind")
            return
        self._hand_on(kind)
        self._send_json(HTTPStatus.ACCEPTED, {"ok": True})

    def _read_interaction_body(self) -> bytes | None:
        """The body, read only when its Content-Length allows; None once refused."""
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            self._refuse_interaction(
                HTTPStatus.LENGTH_REQUIRED, LENGTH_REQUIRED_TEXT, "no Content-Length"
            )
            return None
        length = lengths[0].strip()
        if len(lengths) != 1 or not (length.isascii() and length.isdigit()):
            self._refuse_interaction(
                HTTPStatus.BAD_REQUEST, BAD_INTERACTION_TEXT, "invalid Content-Length"
            )
            return None
        digits = length.lstrip("0") or "0"
        # The digits are counted before int(), which refuses numbers over 4300 digits.
        if len(digits) > len(str(INTERACT_MAX_BYTES)) or int(digits) > INTERACT_MAX_BYTES:
            self._refuse_interaction(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, TOO_LARGE_TEXT, "body too large"
            )
            return None
        size = int(digits)
        body = self.rfile.read(size)
        if len(body) != size:
            self._refuse_interaction(HTTPStatus.BAD_REQUEST, BAD_INTERACTION_TEXT, "body cut short")
            return None
        return body

    def _hand_on(self, kind: str) -> None:
        on_interact = self.server.on_interact
        logger.debug("Avatar interaction: %s", kind)
        if on_interact is None:
            return
        try:
            on_interact(kind)
        except Exception:
            logger.exception("Handling the avatar interaction %r failed", kind)

    def _refuse_interaction(self, status: HTTPStatus, text: str, reason: str) -> None:
        logger.warning("Refused an avatar interaction from %s: %s", self.client_address[0], reason)
        self._send_text(status, text)

    # --- replies ------------------------------------------------------------------------------

    def end_headers(self) -> None:
        """Ends every response's headers, refusals and errors included, with PROTECTING_HEADERS."""
        for name, value in PROTECTING_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPES[".json"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(
        self, status: HTTPStatus, text: str, *, headers: Mapping[str, str] | None = None
    ) -> None:
        body = f"{text}\n".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)


def file_under(root: Path, relative: str) -> Path | None:
    """The file that `relative`, a path with `/` separators, names inside `root`; else None.

    `root` must be resolved. Anything but a plain relative path (empty, absolute, with a
    drive, a UNC or URL name, a backslash, a NUL) is refused before it is ever resolved,
    so a UNC name never reaches the OS: resolving `\\\\host\\share` makes Windows contact
    that host and offer it the owner's credentials.
    """
    if (
        not relative
        or relative.startswith("/")
        or any(char in relative for char in _FORBIDDEN_PATH_CHARS)
    ):
        return None
    try:
        path = (root / relative).resolve()
    except OSError:
        return None
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def _file_under(root: Path, url_path: str) -> Path | None:
    """The file a decoded URL path names inside `root`; its leading slashes are dropped."""
    return file_under(root, url_path.lstrip("/"))


def _interaction_fields(content_type: str, body: bytes) -> tuple[str, str] | None:
    """The token and kind of a JSON body `{"token": str, "kind": str}`; None otherwise."""
    media_type, _, parameters = content_type.partition(";")
    if media_type.strip().lower() != "application/json":
        return None
    if parameters.replace(" ", "").lower() not in ("", "charset=utf-8"):
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):  # also UnicodeDecodeError; deep nesting on 3.11-3.13
        return None
    if not isinstance(data, dict) or data.keys() != {"token", "kind"}:
        return None
    token, kind = data["token"], data["kind"]
    if not isinstance(token, str) or not isinstance(kind, str):
        return None
    return token, kind
