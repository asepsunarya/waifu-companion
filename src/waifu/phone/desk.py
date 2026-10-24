"""The quick buttons on the PC: the phone's buttons (`actions.py`) for the PC's own browser.

`waifu tombol` serves this page on 127.0.0.1, on a port the system picks, and opens it in the
default browser; it lives as long as that console. GET `actions` lists the buttons, and POST
`action` runs the one whose id it names, looked up in the buttons of that moment, behind the
phone's limits: ACTION_RATE_LIMIT presses in any ACTION_RATE_WINDOW_S, one at a time.

Only Master's own page may use it, never another website open in the same browser (CSRF) or a
name an attacker re-points at 127.0.0.1 (DNS rebinding):
- every route is under `/t/<token>/`, with a token made for each server, and anything else is a
  404. The token is in the path, not a cookie: browsers share cookies across every port of
  127.0.0.1, so any local page could plant or shadow one;
- every request must name this server as its Host;
- a press must come from the page's own Origin, as JSON: another origin's JSON request needs a
  preflight, which gets a 405 and never a CORS header;
- every response keeps other pages from embedding or framing what it serves, and the page never
  hands its address on as a Referer.
The token is never logged.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import sys
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit

from waifu.avatar.server import (
    METHOD_NOT_ALLOWED_TEXT,
    NOT_FOUND_TEXT,
    POST_ONLY_TEXT,
    WEB_ROOT,
    RollingLimit,
    ServedFile,
    web_file,
)
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
from waifu.phone.server import (
    BAD_REQUEST_TEXT,
    FAILED_TEXT,
    FOREIGN_ORIGIN_TEXT,
    LOOPBACK,
    JsonRequestHandler,
    is_ipv4_loopback,
)

logger = logging.getLogger(__name__)

# Where a refused device "never runs from", in `pc_power_refusal`'s sentence.
PC_WHERE = "tombol cepat PC"
TOKEN_BYTES = 24
TOKEN_PREFIX = "/t/"
# The routes under the token's prefix: the prefix itself is the page, `tombol/<file>` a file of
# its folder, SHARED_FILES the phone page's files it imports.
PAGE_FOLDER = "tombol"
PAGE = "index.html"
SHARED_FILES = frozenset({"phone/actions.js"})
ACTIONS_ROUTE = "actions"
ACTION_ROUTE = "action"
# What the logs show instead of the token.
TOKEN_MARK = "<token>"
# Sent with every response besides PROTECTING_HEADERS.
NO_REFERRER = {"Referrer-Policy": "no-referrer"}
FOREIGN_HOST_TEXT = "Tombol cepat di PC hanya melayani alamat 127.0.0.1 atau localhost."

# A browser closing the page mid-response surfaces as one of these.
_DISCONNECTS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class DeskServer(ThreadingHTTPServer):
    """Serves the quick buttons of `actions` on `host:port` (0: a port the system picks).

    `host` must be an IPv4 loopback address. `clock` times the presses' rate limit, and
    `owner_call` is how the refusal of a press that failed in code addresses Master.
    """

    daemon_threads = True
    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second server bind the port of
    # one that is already listening; the two would silently split the requests.
    allow_reuse_address = sys.platform != "win32"
    # How long the accept loop may take to notice stop().
    poll_interval_s: float = 0.2

    def __init__(
        self,
        actions: QuickActions,
        *,
        owner_call: str,
        web_root: Path = WEB_ROOT,
        host: str = LOOPBACK,
        port: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not is_ipv4_loopback(host):
            raise ValueError(f"The PC page's server listens only on IPv4 loopback, not {host!r}")
        self.actions = actions
        self.web_root = web_root.resolve()
        self.page_root = (self.web_root / PAGE_FOLDER).resolve()
        self.failed_text = FAILED_TEXT.replace("{owner}", owner_call)
        self.press_limit = RollingLimit(ACTION_RATE_LIMIT, ACTION_RATE_WINDOW_S, clock)
        self.token = secrets.token_urlsafe(TOKEN_BYTES)
        self._pressing = False
        self._pressing_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        super().__init__((host, port), _DeskRequestHandler)
        bound_host, bound_port = self.server_address[:2]
        own = {f"{bound_host}:{bound_port}", f"localhost:{bound_port}"}
        self.own_hosts = frozenset(own)
        self.own_origins = frozenset(f"http://{name}" for name in own)

    @property
    def url(self) -> str:
        """The page's address, with its token: only the browser `waifu tombol` opens gets it."""
        return f"{self._origin}{TOKEN_PREFIX}{self.token}/"

    @property
    def _origin(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def route(self, url_path: str) -> str | None:
        """What a request's URL path (still %-encoded) names under `/t/<token>/`, or None when
        it is not under that prefix."""
        if not url_path.startswith(TOKEN_PREFIX):
            return None
        token, slash, rest = url_path.removeprefix(TOKEN_PREFIX).partition("/")
        sent = token.encode("utf-8", "surrogatepass")
        if not slash or not hmac.compare_digest(sent, self.token.encode("ascii")):
            return None
        return rest

    def claim(self) -> bool:
        """Marks a press as running; False while an earlier one still runs."""
        with self._pressing_lock:
            if self._pressing:
                return False
            self._pressing = True
            return True

    def release(self) -> None:
        with self._pressing_lock:
            self._pressing = False

    def start(self) -> None:
        """Serves in a daemon thread."""
        self._thread = threading.Thread(
            target=self.serve_forever,
            kwargs={"poll_interval": self.poll_interval_s},
            name="desk-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("PC page server listening on %s/", self._origin)

    def stop(self) -> None:
        """Stops serving and frees the port; safe to repeat."""
        if self._thread is not None:
            self.shutdown()
            self._thread.join()
            self._thread = None
        self.server_close()

    def __enter__(self) -> DeskServer:
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
            logger.debug("PC page client %s disconnected", client_address[0])
        else:
            logger.exception("PC page request from %s failed", client_address[0])


class _DeskRequestHandler(JsonRequestHandler):
    server: DeskServer

    def parse_request(self) -> bool:
        """Parses the request and refuses (403) any Host but this server's own address.

        A web page whose host name an attacker re-points at 127.0.0.1 (DNS rebinding) counts
        as same-origin with this server, but its requests still carry that name.
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
                "Refused a PC page request for host %r from %s", host, self.client_address[0]
            )
            self._send_error(HTTPStatus.FORBIDDEN, FOREIGN_HOST_TEXT)
            return False
        return True

    def do_GET(self) -> None:
        route = self._route()
        if route is None:
            return
        if route == "":
            self._send_found(web_file(self.server.page_root, PAGE))
        elif route == ACTIONS_ROUTE:
            groups = self.server.actions.groups()
            self._send_json(HTTPStatus.OK, {"groups": [group_json(group) for group in groups]})
        elif route == ACTION_ROUTE:
            self._refuse_method(route)
        else:
            self._send_found(self._file(route))

    def do_POST(self) -> None:
        route = self._route()
        if route is None:
            return
        if route != ACTION_ROUTE:
            self._refuse_method(route)
            return
        origins = self.headers.get_all("Origin") or []
        if len(origins) != 1 or origins[0] not in self.server.own_origins:
            logger.warning(
                "Refused a quick action of the PC page from %s: foreign origin",
                self.client_address[0],
            )
            self._send_error(HTTPStatus.FORBIDDEN, FOREIGN_ORIGIN_TEXT)
            return
        self._press()

    def _refuse_other_method(self) -> None:
        route = self._route()
        if route is not None:
            self._refuse_method(route)

    do_HEAD = do_PUT = do_DELETE = do_PATCH = _refuse_other_method
    do_OPTIONS = do_CONNECT = do_TRACE = _refuse_other_method

    def _route(self) -> str | None:
        """The route under the token's prefix; None after a 404 for any other path."""
        route = self.server.route(urlsplit(self.path).path)
        if route is None:
            self._send_error(HTTPStatus.NOT_FOUND, NOT_FOUND_TEXT)
        return route

    def _refuse_method(self, route: str) -> None:
        if route == ACTION_ROUTE:
            allowed, text = "POST", POST_ONLY_TEXT
        else:
            allowed, text = "GET", METHOD_NOT_ALLOWED_TEXT
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, text, headers={"Allow": allowed})

    def _file(self, route: str) -> ServedFile | None:
        """The page's file, or the phone page's, that a route names; None for anything else."""
        relative = unquote(route)
        if relative in SHARED_FILES:
            return web_file(self.server.web_root, relative)
        folder, slash, name = relative.partition("/")
        if folder == PAGE_FOLDER and slash:
            return web_file(self.server.page_root, name)
        return None

    def _press(self) -> None:
        """`{"id"}` of a button `actions` lists: runs it and answers `{"ok", "message"}`.

        As on the phone: the rate limit counts before the body is read, a press while the last
        one still runs is a 409, not the limit's 429, and a device's failure is an answer too.
        """
        if not self.server.press_limit.admit():
            logger.info(
                "Refused a quick action of the PC page: more than %d in %d s",
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
            logger.info("Refused a quick action of the PC page: not a button id")
            self._send_error(HTTPStatus.BAD_REQUEST, BAD_REQUEST_TEXT)
            return
        actions = self.server.actions
        action = actions.find(action_id)
        if action is None:
            logger.info("Refused quick action %s of the PC page: no such button", action_id)
            self._send_error(HTTPStatus.NOT_FOUND, ACTION_UNKNOWN_TEXT)
            return
        if not self.server.claim():
            logger.info(
                "Refused quick action %s of the PC page: the last one still runs", action_id
            )
            self._send_error(HTTPStatus.CONFLICT, ACTION_BUSY_TEXT)
            return
        try:
            result = actions.run(action)
        except Exception:
            logger.exception("Quick action %s of the PC page failed", action_id)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, self.server.failed_text)
            return
        finally:
            self.server.release()
        outcome = "ok" if result.ok else "failed"
        logger.info("PC page ran quick action %s: %s", action_id, outcome)
        logger.debug("Quick action %s: %s", action_id, result.message)
        self._send_json(HTTPStatus.OK, {"ok": result.ok, "message": result.message})

    def end_headers(self) -> None:
        """Adds NO_REFERRER to every response; JsonRequestHandler adds PROTECTING_HEADERS."""
        for name, value in NO_REFERRER.items():
            self.send_header(name, value)
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        message = (format % args).replace(self.server.token, TOKEN_MARK)
        logger.debug("%s %s", self.address_string(), message)
