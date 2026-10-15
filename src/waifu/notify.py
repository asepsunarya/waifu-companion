"""Push notifications to the owner's phone through ntfy (https://ntfy.sh).

The owner subscribes in the ntfy app to a topic only they know; a message is one
JSON POST to the server root. Publishing as JSON keeps UTF-8 titles and Japanese
text intact, which plain HTTP headers would not. The topic is as secret as a
password on the public server, so it never reaches a log line.

A message may carry a link that opens when the owner taps it: ntfy's Click action,
which JSON publishing sets with the `click` field. Only an https link whose host a
browser reads as Python does goes out (`is_https_link`).
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Protocol
from urllib.parse import urlsplit

from waifu.config import NotifyConfig

logger = logging.getLogger(__name__)

HttpPost = Callable[[str, bytes, float], int]

_PLAIN_HOST = re.compile(r"[A-Za-z0-9.-]+")


class Notifier(Protocol):
    def send(
        self, title: str, message: str, *, tags: Sequence[str] = (), click: str | None = None
    ) -> bool: ...


def post_json(url: str, body: bytes, timeout_s: float) -> int:
    """POST a UTF-8 JSON body; returns the HTTP status (urllib raises for 4xx/5xx)."""
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.status


def is_https_link(url: str) -> bool:
    """Whether `url` is an https link whose host Python and every browser read alike: the only
    links a notification may open. The host holds only ASCII letters, digits, dots, and
    hyphens, so no user name or port hides the real host (https://anthropic.com@evil.example/
    opens evil.example), and the link holds no backslash, space, or invisible character: a
    browser reads the backslash in https://evil.example\\.anthropic.com/ as a slash and opens
    evil.example, where Python sees a host under anthropic.com."""
    if "\\" in url or " " in url or not url.isprintable():
        return False
    try:
        parts = urlsplit(url)
    except ValueError:  # such as an unclosed IPv6 bracket
        return False
    return parts.scheme == "https" and _PLAIN_HOST.fullmatch(parts.netloc) is not None


class NtfyNotifier:
    def __init__(
        self, config: NotifyConfig, *, http_post: HttpPost = post_json, timeout_s: float = 10.0
    ) -> None:
        if config.topic is None:
            raise ValueError("ntfy needs notify.topic")
        self._url = f"{config.server}/"
        self._topic = config.topic.get_secret_value()
        self._post = http_post
        self._timeout_s = timeout_s

    def send(
        self, title: str, message: str, *, tags: Sequence[str] = (), click: str | None = None
    ) -> bool:
        """Publish one notification, opening `click` when tapped; False (and a warning) when it
        did not go out. A `click` that is not an https link is left out. Never raises."""
        body = {"topic": self._topic, "title": title, "message": message, "tags": list(tags)}
        if click is not None:
            if is_https_link(click):
                body["click"] = click
            else:
                logger.warning("Left out a notification's link: it is not an https link")
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            status = self._post(self._url, payload, self._timeout_s)
        except urllib.error.HTTPError as exc:
            logger.warning("ntfy refused the notification (HTTP %s)", exc.code)
            return False
        except (OSError, http.client.HTTPException) as exc:  # URLError and timeouts are OSErrors
            # The topic travels in the body only, so neither the URL nor the error holds it.
            logger.warning("Could not reach ntfy at %s: %s", self._url, exc)
            return False
        if not 200 <= status < 300:
            logger.warning("ntfy answered HTTP %s; the notification may not have arrived", status)
            return False
        return True


def build_notifier(config: NotifyConfig) -> Notifier | None:
    return NtfyNotifier(config) if config.enabled else None
