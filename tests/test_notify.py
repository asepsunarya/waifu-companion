import http.client
import http.server
import json
import logging
import threading
import urllib.error

import pytest

from waifu.config import NotifyConfig
from waifu.notify import NtfyNotifier, build_notifier, post_json

TOPIC = "cyrene-AbCdEf123456_-x"


def ntfy_config(**overrides) -> NotifyConfig:
    return NotifyConfig(enabled=True, topic=TOPIC, **overrides)


class RecordingPost:
    def __init__(self, status: int = 200, error: BaseException | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, body: bytes, timeout_s: float) -> int:
        self.calls.append((url, json.loads(body.decode("utf-8")), timeout_s))
        if self.error is not None:
            raise self.error
        return self.status


def test_send_publishes_json_to_the_server_root():
    post = RecordingPost()
    notifier = NtfyNotifier(ntfy_config(), http_post=post, timeout_s=7.0)

    assert notifier.send("Cyrene", "Master, resin penuh! おかえり", tags=["video_game"])

    url, body, timeout_s = post.calls[0]
    assert url == "https://ntfy.sh/"
    assert body == {
        "topic": TOPIC,
        "title": "Cyrene",
        "message": "Master, resin penuh! おかえり",
        "tags": ["video_game"],
    }
    assert timeout_s == 7.0


def test_send_with_a_click_link_opens_it_when_the_notification_is_tapped():
    post = RecordingPost()
    link = "https://store.epicgames.com/en-US/p/bad-cheese-4256d9"

    assert NtfyNotifier(ntfy_config(), http_post=post).send(
        "Cyrene", "Master, ada game gratis baru.", tags=["video_game"], click=link
    )

    body = post.calls[0][1]
    assert body["click"] == link  # ntfy's Click action, in its JSON form
    assert body["message"] == "Master, ada game gratis baru."


@pytest.mark.parametrize(
    "link",
    [
        "http://store.epicgames.com/en-US/p/bad-cheese",
        "javascript:alert(1)",
        "file:///C:/Windows/System32/calc.exe",
        "https://",
        "store.epicgames.com/en-US/p/bad-cheese",
    ],
)
def test_a_click_link_that_is_not_https_is_left_out_and_the_message_still_goes(link, caplog):
    post = RecordingPost()

    with caplog.at_level(logging.WARNING, logger="waifu.notify"):
        assert NtfyNotifier(ntfy_config(), http_post=post).send("Cyrene", "halo", click=link)

    assert "click" not in post.calls[0][1]
    assert "not an https link" in caplog.text
    assert TOPIC not in caplog.text


@pytest.mark.parametrize(
    "link",
    [
        "https://www.anthropic.com@evil.example/claim",
        "https://www.anthropic.com:8443/claim",
        # Python reads the host as ...anthropic.com; a browser reads the backslash as a slash.
        "https://claude-credits.example\\.anthropic.com/claim",
        "https://claude-credits.example∕.anthropic.com/claim",
        "https://www.anthropic.com/claim‮moc.elpmaxe",
        "https://www.anthropic.com/claim\x00",
        "https://www.anthropic.com/claim free",
    ],
    ids=["user name", "port", "backslash", "non-ASCII host", "bidi override", "control", "space"],
)
def test_a_click_link_whose_host_a_browser_could_read_differently_is_left_out(link):
    post = RecordingPost()

    assert NtfyNotifier(ntfy_config(), http_post=post).send("Cyrene", "halo", click=link)

    assert "click" not in post.calls[0][1]


def test_send_without_a_click_link_publishes_no_click():
    post = RecordingPost()

    NtfyNotifier(ntfy_config(), http_post=post).send("Cyrene", "halo")

    assert "click" not in post.calls[0][1]


def test_send_uses_a_self_hosted_server():
    post = RecordingPost()
    NtfyNotifier(ntfy_config(server="https://ntfy.example.com/"), http_post=post).send("a", "b")
    assert post.calls[0][0] == "https://ntfy.example.com/"


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://ntfy.sh/", 429, "Too Many Requests", {}, None),
        urllib.error.URLError("getaddrinfo failed"),
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
        http.client.BadStatusLine("garbage"),
    ],
)
def test_a_failed_push_returns_false_and_never_logs_the_topic(error, caplog):
    notifier = NtfyNotifier(ntfy_config(), http_post=RecordingPost(error=error))

    with caplog.at_level(logging.WARNING, logger="waifu.notify"):
        assert notifier.send("Cyrene", "halo") is False

    assert caplog.records, "a failed push must leave a warning"
    assert TOPIC not in caplog.text


def test_a_non_success_status_returns_false():
    assert NtfyNotifier(ntfy_config(), http_post=RecordingPost(status=302)).send("a", "b") is False


def test_build_notifier_is_none_when_disabled():
    assert build_notifier(NotifyConfig()) is None
    assert isinstance(build_notifier(ntfy_config()), NtfyNotifier)


class _Recorder(http.server.BaseHTTPRequestHandler):
    received: list[tuple[str, str, bytes]] = []

    def do_POST(self):  # noqa: N802 - http.server naming
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.received.append((self.path, self.headers["Content-Type"], body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_post_json_sends_utf8_json_over_http():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        status = post_json(url, json.dumps({"message": "がんばって"}).encode("utf-8"), 5.0)
    finally:
        thread.join(5)
        server.server_close()

    assert status == 200
    path, content_type, body = _Recorder.received[-1]
    assert (path, content_type) == ("/", "application/json")
    assert json.loads(body.decode("utf-8")) == {"message": "がんばって"}
