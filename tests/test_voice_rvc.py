import http.client
import io
import logging
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from waifu.config import RvcConfig
from waifu.single_instance import InstanceLock
from waifu.voice import rvc
from waifu.voice.rvc import RvcClient, RvcError, RvcServerProcess, RvcUnavailableError

WAV_ERROR_TOLERANCE = 1 / 16384  # one PCM_16 step, with rounding headroom


def encode(audio: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV")
    return buffer.getvalue()


def decode(data: bytes) -> tuple[np.ndarray, int]:
    audio, sample_rate = sf.read(io.BytesIO(data), dtype="float32")
    return audio, sample_rate


def tone(seconds: float, sample_rate: int, frequency: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (0.3 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


class FakeHttp:
    """Stands in for the HTTP layer: records each request and answers from a script.

    Replies are used in order and the last one repeats; an exception reply is raised.
    """

    def __init__(self, *replies: tuple[int, bytes] | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, bytes | None, dict[str, str], float]] = []

    def __call__(
        self, url: str, data: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        self.requests.append((url, data, dict(headers), timeout))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_client(
    config: RvcConfig | None = None, *, post: FakeHttp | None = None, get: FakeHttp | None = None
) -> RvcClient:
    return RvcClient(
        config or RvcConfig(),
        http_post=post or FakeHttp(AssertionError("unexpected POST")),
        http_get=get or FakeHttp(AssertionError("unexpected GET")),
    )


# --- RvcClient.convert ------------------------------------------------------


def test_convert_sends_a_mono_wav_and_returns_the_decoded_reply():
    converted = tone(0.5, 40000, frequency=660.0)
    post = FakeHttp((200, encode(converted, 40000)))
    original = tone(0.25, 24000)

    audio, sample_rate = make_client(post=post).convert(original, 24000)

    assert sample_rate == 40000
    assert audio.dtype == np.float32
    np.testing.assert_allclose(audio, converted, atol=WAV_ERROR_TOLERANCE)
    (_url, body, headers, _timeout) = post.requests[0]
    sent, sent_rate = decode(body)
    assert sent_rate == 24000
    assert sent.ndim == 1
    np.testing.assert_allclose(sent, original, atol=WAV_ERROR_TOLERANCE)
    assert headers["Content-Type"] == "audio/wav"


def test_convert_posts_the_configured_parameters_to_the_configured_server():
    config = RvcConfig(host="127.0.0.1", port=9999, pitch=-3, index_rate=0.5, protect=0.2)
    post = FakeHttp((200, encode(tone(0.1, 16000), 16000)))

    make_client(config, post=post).convert(tone(0.1, 16000), 16000)

    url, _body, _headers, timeout = post.requests[0]
    parts = urllib.parse.urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("http", "127.0.0.1:9999", "/convert")
    assert urllib.parse.parse_qs(parts.query) == {
        "pitch": ["-3"],
        "index_rate": ["0.5"],
        "protect": ["0.2"],
    }
    assert timeout == pytest.approx(5.05)


@pytest.mark.parametrize(
    ("seconds", "timeout"), [(0.0, 5.0), (10.0, 10.0), (50.0, 30.0), (100.0, 30.0)]
)
def test_convert_waits_for_a_clip_in_proportion_to_its_length_up_to_30_s(seconds, timeout):
    # A stuck server costs the shortest wait that still fits a real conversion, since
    # the mic stays paused until she has spoken.
    post = FakeHttp((200, encode(tone(0.1, 1000), 1000)))

    make_client(post=post).convert(tone(seconds, 1000), 1000)

    assert post.requests[0][3] == pytest.approx(timeout)


def test_convert_downmixes_a_stereo_reply_to_mono():
    left, right = tone(0.1, 40000, 440.0), tone(0.1, 40000, 880.0)
    post = FakeHttp((200, encode(np.column_stack([left, right]), 40000)))

    audio, _ = make_client(post=post).convert(tone(0.1, 16000), 16000)

    assert audio.ndim == 1
    assert audio.dtype == np.float32
    np.testing.assert_allclose(audio, (left + right) / 2, atol=WAV_ERROR_TOLERANCE)


def test_convert_error_status_raises_with_the_servers_error_text():
    post = FakeHttp((500, b'{"error": "converter exploded"}'))

    with pytest.raises(RvcError, match=r"500.*converter exploded") as excinfo:
        make_client(post=post).convert(tone(0.1, 16000), 16000)

    assert not isinstance(excinfo.value, RvcUnavailableError)  # the server did answer


def test_convert_error_status_without_json_raises_with_the_body_text():
    post = FakeHttp((413, b"Payload Too Large"))

    with pytest.raises(RvcError, match=r"413.*Payload Too Large"):
        make_client(post=post).convert(tone(0.1, 16000), 16000)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(ConnectionRefusedError(10061, "refused")),
        TimeoutError("timed out"),
        ConnectionResetError(10054, "reset"),
        # Not OSErrors: a server killed mid-reply, and a program that does not speak HTTP.
        http.client.IncompleteRead(b""),
        http.client.BadStatusLine("garbage"),
    ],
)
def test_convert_unreachable_server_or_timeout_raises_rvc_error(failure):
    post = FakeHttp(failure)

    with pytest.raises(RvcUnavailableError, match="127.0.0.1:9881") as excinfo:
        make_client(post=post).convert(tone(0.1, 16000), 16000)

    assert isinstance(excinfo.value, RvcError)
    assert excinfo.value.__cause__ is failure


def test_convert_reply_that_is_not_audio_raises_rvc_error():
    post = FakeHttp((200, b"<html>not a wav</html>"))

    with pytest.raises(RvcError):
        make_client(post=post).convert(tone(0.1, 16000), 16000)


# --- RvcClient.served_model --------------------------------------------------


def test_served_model_is_the_model_file_the_server_reports():
    get = FakeHttp((200, b'{"ok": true, "model": "tsukuyomi_100e_4500s.pth"}'))

    assert make_client(RvcConfig(port=9999), get=get).served_model() == ("tsukuyomi_100e_4500s.pth")

    url, data, _headers, timeout = get.requests[0]
    assert url == "http://127.0.0.1:9999/health"
    assert data is None
    assert timeout == 2


@pytest.mark.parametrize(
    "reply",
    [
        (503, b'{"error": "loading"}'),
        urllib.error.URLError(ConnectionRefusedError(10061, "refused")),
        TimeoutError("timed out"),
        ConnectionResetError(10054, "reset"),
        http.client.IncompleteRead(b""),
        http.client.BadStatusLine("garbage"),
    ],
)
def test_served_model_is_none_while_no_server_answers(reply):
    assert make_client(get=FakeHttp(reply)).served_model() is None


@pytest.mark.parametrize(
    "body",
    [
        b"<html>another program</html>",
        b"\xff\xfe not utf-8",
        b"[1, 2]",
        b'{"ok": false, "model": "tsukuyomi.pth"}',
        b'{"ok": true}',
        b'{"ok": true, "model": 5}',
    ],
)
def test_served_model_is_none_when_what_answers_is_not_the_rvc_server(body):
    assert make_client(get=FakeHttp((200, body))).served_model() is None


# --- default HTTP transport against an in-process loopback server ------------


class _FakeRvcHandler(BaseHTTPRequestHandler):
    """A stand-in RVC server: /health answers ok, /convert echoes or fails on demand."""

    def do_GET(self) -> None:  # noqa: N802 - http.server dispatches on this name
        self._reply(200, b'{"ok": true, "model": "fake"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802 - http.server dispatches on this name
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.received.append((self.path, self.headers["Content-Type"], body))
        if self.server.fail_with is not None:
            status, error = self.server.fail_with
            self._reply(status, error, "application/json")
        else:
            self._reply(200, body, "audio/wav")

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _FakeRvcServer(HTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FakeRvcHandler)
        self.received: list[tuple[str, str, bytes]] = []
        self.fail_with: tuple[int, bytes] | None = None


@pytest.fixture
def loopback_server() -> Iterator[_FakeRvcServer]:
    server = _FakeRvcServer()
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_transport_round_trips_audio_through_a_real_http_server(loopback_server):
    client = RvcClient(RvcConfig(port=loopback_server.server_port, pitch=2))
    original = tone(0.2, 22050)

    assert client.served_model() == "fake"
    audio, sample_rate = client.convert(original, 22050)

    assert sample_rate == 22050
    np.testing.assert_allclose(audio, original, atol=WAV_ERROR_TOLERANCE)
    path, content_type, _body = loopback_server.received[0]
    assert path.startswith("/convert?pitch=2&")
    assert content_type == "audio/wav"


def test_default_transport_turns_an_http_error_status_into_rvc_error(loopback_server):
    loopback_server.fail_with = (400, b'{"error": "bad pitch"}')
    client = RvcClient(RvcConfig(port=loopback_server.server_port))

    with pytest.raises(RvcError, match=r"400.*bad pitch"):
        client.convert(tone(0.1, 16000), 16000)


def test_default_transport_ignores_a_configured_proxy_for_the_local_server(
    loopback_server, monkeypatch
):
    # A system or environment proxy must not capture traffic meant for the local server.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    client = RvcClient(RvcConfig(port=loopback_server.server_port))

    assert client.served_model() == "fake"


def test_default_transport_finds_no_server_at_a_closed_port():
    with HTTPServer(("127.0.0.1", 0), _FakeRvcHandler) as probe:
        closed_port = probe.server_port

    assert RvcClient(RvcConfig(port=closed_port)).served_model() is None


# --- RvcServerProcess ---------------------------------------------------------
# Never a real process: every test injects FakePopen (tests/conftest.py also refuses
# spawning rvc_server.py), and the server's health comes from a scripted FakeHttp.

DOWN = urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
UP = (200, b'{"ok": true, "model": "tsukuyomi.pth"}')  # the model in Install.config()
OLD_MODEL_UP = (200, b'{"ok": true, "model": "tsukuyomi_50e_2250s.pth"}')


class FakeProcess:
    """The parts of subprocess.Popen the manager uses, recording what it is asked to do."""

    def __init__(self, *, exit_code: int | None = None, ignores_terminate: bool = False) -> None:
        self.pid = 4242
        self.returncode = exit_code
        self.ignores_terminate = ignores_terminate
        self.calls: list[Any] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")
        if not self.ignores_terminate:
            self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("rvc_server", timeout)
        return self.returncode

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = -9


class FakePopen:
    """Records each spawn; the child "writes" one line to the log handle it was given."""

    def __init__(self, process: FakeProcess | None = None, *, error: OSError | None = None):
        self.process = process or FakeProcess()
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        kwargs["stdout"].write(b"server output\n")
        return self.process

    @property
    def log_handle(self) -> Any:
        return self.calls[-1][1]["stdout"]


class FakeJob:
    """Stands in for KillOnCloseJob: records the processes put in it and whether it closed."""

    def __init__(self, error: OSError | None) -> None:
        self.error = error
        self.processes: list[Any] = []
        self.closed = False

    def add(self, process: Any) -> None:
        if self.error is not None:
            raise self.error
        self.processes.append(process)

    def close(self) -> None:
        self.closed = True


class FakeJobs:
    """The job factory: every job it made, and an OSError each one raises on add()."""

    def __init__(self, *, error: OSError | None = None) -> None:
        self.error = error
        self.made: list[FakeJob] = []

    def __call__(self) -> FakeJob:
        self.made.append(FakeJob(self.error))
        return self.made[-1]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class Install:
    applio_dir: Path
    python: Path
    model: Path
    index: Path
    script: Path
    log_path: Path
    lock_path: Path

    def config(self, **overrides: Any) -> RvcConfig:
        settings: dict[str, Any] = {
            "applio_dir": self.applio_dir,
            "python": self.python,
            "model": self.model,
            "index": self.index,
        }
        return RvcConfig(**(settings | overrides))


@pytest.fixture
def install(tmp_path: Path) -> Install:
    applio_dir = tmp_path / "Applio"
    python = applio_dir / "env" / "Scripts" / "python.exe"
    model = tmp_path / "voices" / "tsukuyomi.pth"
    index = tmp_path / "voices" / "tsukuyomi.index"
    script = tmp_path / "repo" / "tools" / "rvc_server.py"
    for path in (python, model, index, script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return Install(
        applio_dir,
        python,
        model,
        index,
        script,
        tmp_path / "logs" / "rvc_server.log",
        tmp_path / "data" / "rvc_server.lock",
    )


def make_manager(
    install: Install,
    *,
    popen: FakePopen,
    health: FakeHttp,
    clock: FakeClock | None = None,
    config: RvcConfig | None = None,
    jobs: FakeJobs | None = None,
) -> RvcServerProcess:
    config = config or install.config()
    clock = clock or FakeClock()
    return RvcServerProcess(
        config,
        popen=popen,
        client=make_client(config, get=health),
        clock=clock,
        sleep=clock.sleep,
        log_path=install.log_path,
        lock_path=install.lock_path,
        server_script=install.script,
        job_factory=jobs or FakeJobs(),
    )


def lock_is_free(path: Path) -> bool:
    probe = InstanceLock(path)
    try:
        return probe.acquire()
    finally:
        probe.release()


def test_ensure_running_reuses_a_healthy_server_and_never_stops_it(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(UP))

    manager.ensure_running()
    manager.stop()

    assert popen.calls == []
    assert not install.log_path.exists()


def test_ensure_running_spawns_the_server_script_with_applio_python(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))

    manager.ensure_running()

    args, kwargs = popen.calls[0]
    assert args == [
        str(install.python),
        str(install.script),
        "--applio-dir",
        str(install.applio_dir),
        "--model",
        str(install.model),
        "--index",
        str(install.index),
        "--host",
        "127.0.0.1",
        "--port",
        "9881",
    ]
    assert Path(kwargs["cwd"]) == install.applio_dir
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_ensure_running_gives_the_server_unbuffered_utf8_output_and_this_environment(
    install, monkeypatch
):
    # Redirected to the log, Python would encode a non-ASCII model name in the ANSI code
    # page (and crash printing it), and would lose buffered Applio output on terminate.
    monkeypatch.setenv("WAIFU_TEST_MARKER", "inherited")
    popen = FakePopen()

    make_manager(install, popen=popen, health=FakeHttp(DOWN, UP)).ensure_running()

    env = popen.calls[0][1]["env"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["WAIFU_TEST_MARKER"] == "inherited"


def test_ensure_running_appends_server_output_to_the_log_file(install):
    install.log_path.parent.mkdir()
    install.log_path.write_bytes(b"earlier run\n")
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))

    manager.ensure_running()
    manager.stop()

    assert popen.log_handle.closed
    assert install.log_path.read_bytes() == b"earlier run\nserver output\n"


def test_ensure_running_creates_the_log_folder(install):
    popen = FakePopen()

    make_manager(install, popen=popen, health=FakeHttp(DOWN, UP)).ensure_running()

    assert Path(popen.log_handle.name) == install.log_path
    assert install.log_path.parent.is_dir()


def test_ensure_running_leaves_out_index_when_none_is_configured(install):
    popen = FakePopen()
    config = install.config(index=None)

    make_manager(install, popen=popen, health=FakeHttp(DOWN, UP), config=config).ensure_running()

    assert "--index" not in popen.calls[0][0]


def test_ensure_running_passes_absolute_paths_because_the_server_runs_in_applio_dir(
    install, monkeypatch
):
    monkeypatch.chdir(install.model.parent.parent)
    popen = FakePopen()
    config = install.config(model=Path("voices/tsukuyomi.pth"), index=None)

    make_manager(install, popen=popen, health=FakeHttp(DOWN, UP), config=config).ensure_running()

    args = popen.calls[0][0]
    assert args[args.index("--model") + 1] == str(install.model)


@pytest.mark.parametrize("missing", ["python", "applio_dir", "model", "index", "script"])
def test_ensure_running_refuses_to_spawn_when_a_path_is_missing(install, missing):
    path = getattr(install, missing)
    if path.is_dir():
        path.rename(path.with_name("moved"))
    else:
        path.unlink()
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN))

    with pytest.raises(RvcError, match=re.escape(str(path))) as excinfo:
        manager.ensure_running()

    assert "README" in str(excinfo.value)
    assert popen.calls == []


def test_ensure_running_refuses_to_spawn_without_a_model(install):
    popen = FakePopen()
    config = install.config(model=None)
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN), config=config)

    with pytest.raises(RvcError, match=r"tts\.rvc\.model"):
        manager.ensure_running()

    assert popen.calls == []


def test_ensure_running_refuses_a_running_server_that_serves_another_model(install):
    # An earlier server (autostart's, or one left by a closed window) keeps the model it
    # loaded: comparing checkpoints through it would make every one sound the same.
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(OLD_MODEL_UP))

    with pytest.raises(RvcError) as excinfo:
        manager.ensure_running()

    message = str(excinfo.value)
    for part in ("tsukuyomi_50e_2250s.pth", "tsukuyomi.pth", "127.0.0.1:9881", "Hentikan"):
        assert part in message
    assert popen.calls == []


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.168.1.5", "::1", "rvc host"])
def test_ensure_running_refuses_a_host_other_than_ipv4_loopback(install, host):
    # The server has no password, so it must listen on this PC only; "localhost" would
    # also cost ~2 s per request on Windows (::1 is tried first and refused).
    popen = FakePopen()
    manager = make_manager(
        install,
        popen=popen,
        health=FakeHttp(AssertionError("no request at a refused host")),
        # load_config already rejects such a host; model_copy skips validation so this
        # second line of defence at the process boundary stays tested.
        config=install.config().model_copy(update={"host": host}),
    )

    with pytest.raises(RvcError, match=r"tts\.rvc\.host") as excinfo:
        manager.ensure_running()

    assert repr(host) in str(excinfo.value)
    assert popen.calls == []


def test_ensure_running_accepts_any_ipv4_loopback_host(install):
    popen = FakePopen()
    manager = make_manager(
        install, popen=popen, health=FakeHttp(DOWN, UP), config=install.config(host="127.0.0.2")
    )

    manager.ensure_running()

    args = popen.calls[0][0]
    assert args[args.index("--host") + 1] == "127.0.0.2"


def test_starting_a_server_holds_the_start_lock_until_it_answers(install):
    locked_at_spawn: list[bool] = []

    class LockCheckingPopen(FakePopen):
        def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
            locked_at_spawn.append(not lock_is_free(install.lock_path))
            return super().__call__(args, **kwargs)

    manager = make_manager(install, popen=LockCheckingPopen(), health=FakeHttp(DOWN, UP))

    manager.ensure_running()

    assert locked_at_spawn == [True]
    assert lock_is_free(install.lock_path)


def test_a_server_being_started_by_another_command_is_awaited_not_started_twice(install):
    # Two copies of the model would load onto the GPU, and the one that loses the port
    # race would have its command claim (and later stop) the other command's server.
    clock = FakeClock()
    popen = FakePopen()
    jobs = FakeJobs()
    manager = make_manager(
        install, popen=popen, health=FakeHttp(DOWN, DOWN, UP), clock=clock, jobs=jobs
    )

    with InstanceLock(install.lock_path):
        manager.ensure_running()
    manager.stop()

    assert popen.calls == []
    assert jobs.made == []
    assert clock.sleeps == [0.5, 0.5]


def test_waiting_for_another_commands_server_gives_up_after_the_startup_timeout(install):
    clock = FakeClock()
    popen = FakePopen()
    config = install.config(startup_timeout_s=2.0)
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN), clock=clock, config=config)

    with InstanceLock(install.lock_path), pytest.raises(RvcError, match="2 detik"):
        manager.ensure_running()

    assert popen.calls == []


def test_a_server_another_command_started_with_another_model_is_refused(install):
    manager = make_manager(install, popen=FakePopen(), health=FakeHttp(DOWN, OLD_MODEL_UP))

    with InstanceLock(install.lock_path), pytest.raises(RvcError, match="Hentikan"):
        manager.ensure_running()


def test_a_server_that_answers_after_our_child_died_is_reused_not_owned(install, caplog):
    # Our child lost the port to another server: what answers is not ours to stop.
    jobs = FakeJobs()
    popen = FakePopen(FakeProcess(exit_code=1))
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP), jobs=jobs)

    with caplog.at_level(logging.INFO, logger="waifu.voice.rvc"):
        manager.ensure_running()
    manager.stop()

    assert "Reusing the RVC server" in caplog.text
    assert popen.process.calls == []
    assert popen.log_handle.closed
    assert jobs.made[0].closed


def test_a_server_that_answers_after_our_child_died_must_serve_the_configured_model(install):
    popen = FakePopen(FakeProcess(exit_code=1))
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, OLD_MODEL_UP))

    with pytest.raises(RvcError, match="Hentikan"):
        manager.ensure_running()


def test_ensure_running_polls_health_every_half_second_until_the_server_is_up(install):
    clock = FakeClock()
    popen = FakePopen()
    health = FakeHttp(DOWN, DOWN, DOWN, DOWN, UP)  # the first check is the reuse probe
    manager = make_manager(install, popen=popen, health=health, clock=clock)

    manager.ensure_running()

    assert clock.sleeps == [0.5, 0.5, 0.5]
    assert len(popen.calls) == 1
    assert popen.process.calls == []


def test_ensure_running_fails_naming_the_log_when_the_server_exits_during_startup(install):
    popen = FakePopen(FakeProcess(exit_code=1))
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN))

    with pytest.raises(RvcError, match="berhenti") as excinfo:
        manager.ensure_running()

    assert str(install.log_path) in str(excinfo.value)
    assert popen.process.calls == []
    assert popen.log_handle.closed


def test_ensure_running_terminates_a_server_that_misses_the_startup_timeout(install):
    clock = FakeClock()
    popen = FakePopen()
    config = install.config(startup_timeout_s=2.0)
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN), clock=clock, config=config)

    with pytest.raises(RvcError, match="2 detik") as excinfo:
        manager.ensure_running()

    assert str(install.log_path) in str(excinfo.value)
    assert clock.sleeps == [0.5, 0.5, 0.5, 0.5]
    assert popen.process.calls == ["terminate", ("wait", 5)]
    assert popen.log_handle.closed


def test_ctrl_c_during_startup_stops_the_half_started_server(install):
    def interrupted_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    config = install.config()
    popen = FakePopen()
    manager = RvcServerProcess(
        config,
        popen=popen,
        client=make_client(config, get=FakeHttp(DOWN)),
        clock=FakeClock(),
        sleep=interrupted_sleep,
        log_path=install.log_path,
        lock_path=install.lock_path,
        server_script=install.script,
        job_factory=FakeJobs(),
    )

    with pytest.raises(KeyboardInterrupt):
        manager.ensure_running()

    assert popen.process.calls == ["terminate", ("wait", 5)]
    assert popen.log_handle.closed


def test_ensure_running_reports_a_server_that_cannot_be_started(install):
    popen = FakePopen(error=FileNotFoundError(2, "not found"))
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN))

    with pytest.raises(RvcError):
        manager.ensure_running()

    assert popen.log_handle.closed


def test_ensure_running_twice_keeps_ownership_of_the_server_it_started(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))

    manager.ensure_running()
    manager.ensure_running()
    manager.stop()

    assert len(popen.calls) == 1
    assert popen.process.calls == ["terminate", ("wait", 5)]


def test_ensure_running_ties_the_server_it_starts_to_this_process(install):
    # Closing Cyrene's window ends her without running `finally`; the job is what then
    # takes the server (and its model in VRAM) down with her.
    jobs = FakeJobs()
    popen = FakePopen()

    make_manager(install, popen=popen, health=FakeHttp(DOWN, UP), jobs=jobs).ensure_running()

    [job] = jobs.made
    assert job.processes == [popen.process]
    assert not job.closed


def test_stop_closes_the_job_of_the_server_it_stopped(install):
    jobs = FakeJobs()
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP), jobs=jobs)
    manager.ensure_running()

    manager.stop()

    assert popen.process.calls == ["terminate", ("wait", 5)]
    assert jobs.made[0].closed


def test_a_reused_server_is_never_put_in_a_job(install):
    jobs = FakeJobs()

    make_manager(install, popen=FakePopen(), health=FakeHttp(UP), jobs=jobs).ensure_running()

    assert jobs.made == []


def test_a_server_that_cannot_join_a_job_still_starts_with_a_warning(install, caplog):
    jobs = FakeJobs(error=PermissionError(5, "Access is denied"))
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP), jobs=jobs)

    with caplog.at_level(logging.WARNING, logger="waifu.voice.rvc"):
        manager.ensure_running()

    assert "Access is denied" in caplog.text
    assert jobs.made[0].closed
    manager.stop()
    assert popen.process.calls == ["terminate", ("wait", 5)]


def test_a_server_that_exits_during_startup_leaves_no_open_job(install):
    jobs = FakeJobs()
    manager = make_manager(
        install, popen=FakePopen(FakeProcess(exit_code=1)), health=FakeHttp(DOWN), jobs=jobs
    )

    with pytest.raises(RvcError):
        manager.ensure_running()

    assert jobs.made[0].closed


def test_stop_terminates_the_server_it_started_and_closes_the_log(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))
    manager.ensure_running()

    manager.stop()

    assert popen.process.calls == ["terminate", ("wait", 5)]
    assert popen.log_handle.closed


def test_stop_kills_a_server_that_ignores_terminate(install):
    popen = FakePopen(FakeProcess(ignores_terminate=True))
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))
    manager.ensure_running()

    manager.stop()

    assert popen.process.calls == ["terminate", ("wait", 5), "kill"]


def test_stop_leaves_a_server_that_already_exited_alone(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))
    manager.ensure_running()
    popen.process.returncode = 0

    manager.stop()

    assert popen.process.calls == []
    assert popen.log_handle.closed


def test_stop_is_idempotent(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))
    manager.ensure_running()

    manager.stop()
    manager.stop()

    assert popen.process.calls == ["terminate", ("wait", 5)]


def test_stop_without_ensure_running_does_nothing(install):
    make_manager(install, popen=FakePopen(), health=FakeHttp(DOWN)).stop()


def test_context_manager_starts_and_stops_the_server(install):
    popen = FakePopen()
    manager = make_manager(install, popen=popen, health=FakeHttp(DOWN, UP))

    with manager as running:
        assert running is manager
        assert len(popen.calls) == 1
        assert popen.process.calls == []

    assert popen.process.calls == ["terminate", ("wait", 5)]


def test_default_popen_is_looked_up_when_spawning_so_the_suite_guard_applies(install):
    config = install.config()
    manager = RvcServerProcess(
        config,
        client=make_client(config, get=FakeHttp(DOWN)),
        log_path=install.log_path,
        lock_path=install.lock_path,
        server_script=install.script,
        job_factory=FakeJobs(),
    )

    with pytest.raises(AssertionError, match="system-change command"):
        manager.ensure_running()


def test_default_server_script_is_the_repository_tools_script():
    repository = Path(__file__).resolve().parents[1]

    assert repository / "tools" / "rvc_server.py" == rvc.SERVER_SCRIPT


# --- KillOnCloseJob, against the real Windows API ------------------------------------
# Only a throwaway `python -c "sleep"` child is ever put in a job here.

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")


@windows_only
def test_closing_the_job_ends_the_processes_in_it():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = rvc.KillOnCloseJob()
        job.add(child)
        assert child.poll() is None

        job.close()

        assert child.wait(timeout=10) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@windows_only
def test_the_job_refuses_a_process_without_an_os_handle():
    # A fake process only has a made-up pid: the job must never look that pid up, or a
    # test could tie (and on close kill) some real process on the owner's PC.
    job = rvc.KillOnCloseJob()
    try:
        with pytest.raises(TypeError):
            job.add(FakeProcess())
    finally:
        job.close()


@windows_only
def test_closing_the_job_twice_is_harmless():
    job = rvc.KillOnCloseJob()

    job.close()
    job.close()
