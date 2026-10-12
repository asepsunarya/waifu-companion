"""Loopback HTTP server for the avatar viewer: the page, the VRM, and the event stream.

The mascot window loads `index.html` from here, fetches the model from
`/model.vrm`, and listens to `/events`, a Server-Sent Events stream fed by the
`AvatarBus`. Every connection gets its own daemon thread, so a long-lived event
stream never holds up the page or the model, and none of them keep Cyrene alive.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit

from waifu.avatar.bus import MODEL_URL, AvatarBus, Subscription
from waifu.config import AvatarConfig

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent / "web"
INDEX = "index.html"
EVENTS_URL = "/events"
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
SSE_RETRY_MS = 2000
FILE_CHUNK_BYTES = 64 * 1024
# A client that stops sending its request, or stops reading the stream, is dropped.
SOCKET_TIMEOUT_S = 30
NOT_FOUND_TEXT = "Tidak ditemukan."
MODEL_NOT_FOUND_TEXT = "Model VRM tidak ditemukan. Atur avatar.model di config.yaml."
METHOD_NOT_ALLOWED_TEXT = "Hanya GET yang didukung."
FOREIGN_HOST_TEXT = "Server avatar Cyrene hanya melayani alamat 127.0.0.1 atau localhost."

# A viewer closing its window mid-response surfaces as one of these.
_DISCONNECTS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
# Never part of a web path. A backslash could form a UNC path (resolving it would
# contact another host), a colon a drive or an NTFS stream, a NUL breaks the OS call.
_FORBIDDEN_PATH_CHARS = ("\\", ":", "\0")


class AvatarError(Exception):
    """The avatar cannot be shown; the message is Indonesian and safe to show."""


class AvatarServer(ThreadingHTTPServer):
    """Serves the viewer on `config.host:config.port`; bind failures raise AvatarError."""

    daemon_threads = True
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second server bind the port
    # of one that is already listening; the two would silently split the viewers.
    allow_reuse_address = sys.platform != "win32"
    # A `: ping` comment on a quiet event stream keeps it from looking dead.
    ping_interval_s: float = 15.0
    # How long the accept loop may take to notice stop().
    poll_interval_s: float = 0.2

    def __init__(self, config: AvatarConfig, bus: AvatarBus, *, web_root: Path = WEB_ROOT) -> None:
        self.bus = bus
        self.web_root = web_root.resolve()
        self.model_path = config.model
        self._thread: threading.Thread | None = None
        try:
            super().__init__((config.host, config.port), _AvatarRequestHandler)
        except OSError as exc:
            raise AvatarError(
                f"Port {config.host}:{config.port} sudah dipakai program lain (mungkin Cyrene "
                "yang lain masih berjalan) atau diblokir Windows. Tutup program itu atau ganti "
                "avatar.port di config.yaml."
            ) from exc

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"

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
        """Parses the request and refuses (421) any Host but this server's own address.

        A web page whose host name an attacker re-points at 127.0.0.1 (DNS rebinding)
        counts as same-origin with this server, but its requests still carry that name.
        """
        if not super().parse_request():
            return False
        host = (self.headers.get("Host") or "").strip().lower()
        if host in self._own_hosts():
            return True
        logger.warning(
            "Refused an avatar request for host %r from %s", host, self.client_address[0]
        )
        self._send_text(HTTPStatus.MISDIRECTED_REQUEST, FOREIGN_HOST_TEXT)
        return False

    def _own_hosts(self) -> set[str]:
        host, port = self.server.server_address[:2]
        return {f"{host}:{port}", f"localhost:{port}"}

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == EVENTS_URL:
            self._stream_events()
        elif path == MODEL_URL:
            self._send_model()
        else:
            self._send_static(path)

    def _refuse_method(self) -> None:
        self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, METHOD_NOT_ALLOWED_TEXT, allow="GET")

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _refuse_method
    do_OPTIONS = do_CONNECT = do_TRACE = _refuse_method

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

    def _send_model(self) -> None:
        model = self.server.model_path
        if model is None or not model.is_file():
            logger.warning("Avatar model not found: %s", model)
            self._send_text(HTTPStatus.NOT_FOUND, MODEL_NOT_FOUND_TEXT)
            return
        self._send_file(model, MODEL_CONTENT_TYPE)

    def _send_static(self, url_path: str) -> None:
        path = self._resolve_static(url_path)
        if path is None:
            self._send_text(HTTPStatus.NOT_FOUND, NOT_FOUND_TEXT)
            return
        suffix = path.suffix.lower()
        self._send_file(
            path,
            CONTENT_TYPES.get(suffix, DEFAULT_CONTENT_TYPE),
            cache_control="no-store" if suffix == ".html" else None,
        )

    def _resolve_static(self, url_path: str) -> Path | None:
        """The file under the web root that `url_path` names, or None."""
        relative = unquote(url_path).lstrip("/") or INDEX
        if any(char in relative for char in _FORBIDDEN_PATH_CHARS):
            return None
        web_root = self.server.web_root
        path = (web_root / relative).resolve()
        if not path.is_relative_to(web_root) or not path.is_file():
            return None
        return path

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

    def _send_text(self, status: HTTPStatus, text: str, *, allow: str | None = None) -> None:
        body = f"{text}\n".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if allow is not None:
            self.send_header("Allow", allow)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)
