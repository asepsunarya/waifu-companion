"""Push notifications to the owner's phone through ntfy (https://ntfy.sh).

The owner subscribes in the ntfy app to a topic only they know; a message is one
JSON POST to the server root. Publishing as JSON keeps UTF-8 titles and Japanese
text intact, which plain HTTP headers would not. The topic is as secret as a
password on the public server, so it never reaches a log line.
"""

from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Protocol

from waifu.config import NotifyConfig

logger = logging.getLogger(__name__)

HttpPost = Callable[[str, bytes, float], int]


class Notifier(Protocol):
    def send(self, title: str, message: str, *, tags: Sequence[str] = ()) -> bool: ...


def post_json(url: str, body: bytes, timeout_s: float) -> int:
    """POST a UTF-8 JSON body; returns the HTTP status (urllib raises for 4xx/5xx)."""
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.status


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

    def send(self, title: str, message: str, *, tags: Sequence[str] = ()) -> bool:
        """Publish one notification; False (and a warning) when it did not go out. Never raises."""
        body = {"topic": self._topic, "title": title, "message": message, "tags": list(tags)}
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
