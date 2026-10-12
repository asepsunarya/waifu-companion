"""tools/rvc_server.py, exercised in-process: a fake converter behind a loopback test server.

The script runs under Applio's Python, so it is loaded by path, never spawned
(tests/conftest.py also refuses spawning it). Applio itself is replaced by a tiny
fake `rvc.infer.infer` package written to a temporary folder; no model is loaded.
"""

from __future__ import annotations

import array
import http.client
import importlib
import importlib.util
import io
import json
import logging
import socket
import sys
import threading
import urllib.error
import urllib.request
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "rvc_server.py"
CLIENT_TIMEOUT_S = 5
MODEL_NAME = "tsukuyomi.pth"
CONVERTED = b"RIFF\x00\x00\x00\x00WAVEconverted"


def _load_server_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rvc_server", SERVER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rvc_server = _load_server_module()
# Captured before any test monkeypatches the module attribute with a non-binding fake.
REAL_SERVER_CLASS = rvc_server.RvcHttpServer


def make_wav(frames: int = 160, sample_rate: int = 16_000, *, silent: bool = False) -> bytes:
    """16-bit mono WAV: a quiet square wave, or digital silence when `silent`."""
    samples = array.array(
        "h", [0 if silent else (800 if i % 40 < 20 else -800) for i in range(frames)]
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


class FakeConverter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def convert(self, wav_bytes: bytes, *, pitch: int, index_rate: float, protect: float) -> bytes:
        self.calls.append(
            {"wav": wav_bytes, "pitch": pitch, "index_rate": index_rate, "protect": protect}
        )
        if self.error is not None:
            raise self.error
        return CONVERTED


@pytest.fixture
def serve_handler() -> Iterator[Callable[[type], str]]:
    """Serves a handler class on 127.0.0.1, an ephemeral port, in a background thread."""
    running: list[tuple[Any, threading.Thread]] = []

    def start(handler: type) -> str:
        server = REAL_SERVER_CLASS(("127.0.0.1", 0), handler)
        # A short poll interval keeps shutdown() in teardown fast.
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        running.append((server, thread))
        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    yield start
    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(timeout=CLIENT_TIMEOUT_S)


@pytest.fixture
def serve(serve_handler: Callable[[type], str]) -> Callable[[FakeConverter], str]:
    """Serves make_handler(converter, MODEL_NAME); returns the base URL."""
    return lambda converter: serve_handler(rvc_server.make_handler(converter, MODEL_NAME))


# Proxies off: a system proxy must never see (or break) a loopback request.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_request(url: str, *, data: bytes | None = None) -> tuple[int, str, bytes]:
    """Returns (status, content type, body); HTTP error statuses are returned, not raised."""
    headers = {"Content-Type": "audio/wav"} if data is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with _opener.open(request, timeout=CLIENT_TIMEOUT_S) as response:
            return response.status, response.headers.get_content_type(), response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.headers.get_content_type(), error.read()


def send_headers_only(base_url: str, headers: dict[str, str]) -> http.client.HTTPResponse:
    """POSTs /convert with the given headers and no body at all."""
    host, port = base_url.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=CLIENT_TIMEOUT_S)
    connection.putrequest("POST", "/convert")
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.endheaders()
    response = connection.getresponse()
    response.read()
    connection.close()
    return response


def assert_json_error(content_type: str, body: bytes) -> None:
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["ok"] is False
    assert isinstance(payload["error"], str) and payload["error"]


# --- module import -------------------------------------------------------------------------


def test_the_script_imports_without_applio() -> None:
    assert "rvc" not in sys.modules
    assert "rvc.infer.infer" not in sys.modules


# --- GET -----------------------------------------------------------------------------------


def test_health_reports_ok_and_the_model_name(serve: Callable[[FakeConverter], str]) -> None:
    base_url = serve(FakeConverter())

    status, content_type, body = http_request(f"{base_url}/health")

    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body) == {"ok": True, "model": MODEL_NAME}


def test_unknown_path_is_a_404_json_error(serve: Callable[[FakeConverter], str]) -> None:
    base_url = serve(FakeConverter())

    status, content_type, body = http_request(f"{base_url}/nope")

    assert status == 404
    assert_json_error(content_type, body)


def test_requests_do_not_write_access_lines_to_stderr(
    serve: Callable[[FakeConverter], str], capsys: pytest.CaptureFixture[str]
) -> None:
    base_url = serve(FakeConverter())

    http_request(f"{base_url}/health")
    http_request(f"{base_url}/nope")

    assert capsys.readouterr().err == ""


# --- POST /convert -------------------------------------------------------------------------


def test_convert_returns_the_converted_wav(serve: Callable[[FakeConverter], str]) -> None:
    converter = FakeConverter()
    base_url = serve(converter)
    wav = make_wav()

    status, content_type, body = http_request(
        f"{base_url}/convert?pitch=4&index_rate=0.5&protect=0.2", data=wav
    )

    assert (status, content_type, body) == (200, "audio/wav", CONVERTED)
    assert converter.calls == [{"wav": wav, "pitch": 4, "index_rate": 0.5, "protect": 0.2}]


def test_convert_sets_content_length(serve: Callable[[FakeConverter], str]) -> None:
    base_url = serve(FakeConverter())
    request = urllib.request.Request(f"{base_url}/convert", data=make_wav())

    with _opener.open(request, timeout=CLIENT_TIMEOUT_S) as response:
        assert response.headers["Content-Length"] == str(len(CONVERTED))


def test_convert_uses_default_parameters_without_a_query(
    serve: Callable[[FakeConverter], str],
) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    status, _, _ = http_request(f"{base_url}/convert", data=make_wav())

    assert status == 200
    assert [(c["pitch"], c["index_rate"], c["protect"]) for c in converter.calls] == [
        (0, 0.75, 0.33)
    ]


def test_convert_accepts_the_range_limits(serve: Callable[[FakeConverter], str]) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    low, _, _ = http_request(
        f"{base_url}/convert?pitch=-24&index_rate=0&protect=0", data=make_wav()
    )
    high, _, _ = http_request(
        f"{base_url}/convert?pitch=24&index_rate=1&protect=0.5", data=make_wav()
    )

    assert (low, high) == (200, 200)
    assert [(c["pitch"], c["index_rate"], c["protect"]) for c in converter.calls] == [
        (-24, 0.0, 0.0),
        (24, 1.0, 0.5),
    ]


@pytest.mark.parametrize(
    "query",
    [
        "pitch=25",
        "pitch=-25",
        "pitch=4.5",
        "pitch=abc",
        "pitch=",
        "index_rate=1.5",
        "index_rate=-0.1",
        "index_rate=nan",
        "index_rate=x",
        "protect=0.6",
        "protect=inf",
        "pitch=1&pitch=2",
        "volume=3",
        "pitch",
    ],
)
def test_convert_rejects_an_invalid_query(
    serve: Callable[[FakeConverter], str], query: str
) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    status, content_type, body = http_request(f"{base_url}/convert?{query}", data=make_wav())

    assert status == 400
    assert_json_error(content_type, body)
    assert converter.calls == []


@pytest.mark.parametrize(
    "body",
    [b"", b"RIFF", b"not a wav file at all", b"RIFF\x00\x00\x00\x00AVI LIST", b"ID3" + bytes(64)],
)
def test_convert_rejects_a_body_that_is_not_wav(
    serve: Callable[[FakeConverter], str], body: bytes
) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    status, content_type, response = http_request(f"{base_url}/convert", data=body)

    assert status == 400
    assert_json_error(content_type, response)
    assert converter.calls == []


def test_convert_without_content_length_is_411(serve: Callable[[FakeConverter], str]) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    response = send_headers_only(base_url, {})

    assert response.status == 411
    assert response.getheader("Content-Type") == "application/json"
    assert converter.calls == []


@pytest.mark.parametrize("length", ["abc", "-1"])
def test_convert_with_a_malformed_content_length_is_400(
    serve: Callable[[FakeConverter], str], length: str
) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    response = send_headers_only(base_url, {"Content-Length": length})

    assert response.status == 400
    assert converter.calls == []


def test_an_oversized_body_is_refused_before_it_is_read(
    serve: Callable[[FakeConverter], str],
) -> None:
    # Only the headers are sent: a server that tried to read the body would block until
    # the client times out instead of answering 413.
    converter = FakeConverter()
    base_url = serve(converter)

    response = send_headers_only(base_url, {"Content-Length": str(20 * 1024 * 1024 + 1)})

    assert response.status == 413
    assert response.getheader("Content-Type") == "application/json"
    assert converter.calls == []


def raw_exchange(base_url: str, request: bytes) -> bytes:
    """Sends `request` bytes, closes the sending side, and returns everything answered."""
    host, port = base_url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=CLIENT_TIMEOUT_S) as sock:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


def test_a_body_shorter_than_its_content_length_is_400(
    serve: Callable[[FakeConverter], str],
) -> None:
    # The WAV header is intact, so only the length check stands between Applio and the
    # truncated upload.
    converter = FakeConverter()
    base_url = serve(converter)
    head = (
        b"POST /convert HTTP/1.1\r\nHost: localhost\r\nContent-Type: audio/wav\r\n"
        b"Content-Length: 100\r\n\r\n"
    )

    reply = raw_exchange(base_url, head + make_wav()[:50])

    status_line, _, rest = reply.partition(b"\r\n")
    assert status_line.split()[1] == b"400"
    assert json.loads(rest.partition(b"\r\n\r\n")[2]) == {
        "ok": False,
        "error": "body ended early",
    }
    assert converter.calls == []


def test_a_stalled_client_does_not_block_the_server(
    serve: Callable[[FakeConverter], str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # One request at a time: without a socket timeout, a client that stops halfway
    # through its headers would keep every later request (Cyrene's) waiting forever.
    monkeypatch.setattr(rvc_server, "SOCKET_TIMEOUT_S", 0.2)
    base_url = serve(FakeConverter())
    host, port = base_url.removeprefix("http://").split(":")

    with (
        caplog.at_level(logging.WARNING, logger="rvc_server"),
        socket.create_connection((host, int(port)), timeout=CLIENT_TIMEOUT_S) as stalled,
    ):
        stalled.sendall(b"GET /health HTTP/1.1\r\nHo")
        status, _, _ = http_request(f"{base_url}/health")

    assert status == 200
    assert any("timed out" in record.getMessage() for record in caplog.records)


def test_a_converter_failure_is_a_logged_500(
    serve: Callable[[FakeConverter], str], caplog: pytest.LogCaptureFixture
) -> None:
    base_url = serve(FakeConverter(error=RuntimeError("CUDA out of memory")))

    with caplog.at_level(logging.ERROR, logger="rvc_server"):
        status, content_type, body = http_request(f"{base_url}/convert", data=make_wav())

    assert status == 500
    assert_json_error(content_type, body)
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert "CUDA out of memory" in str(errors[0].exc_info[1])


def test_post_to_an_unknown_path_is_404(serve: Callable[[FakeConverter], str]) -> None:
    converter = FakeConverter()
    base_url = serve(converter)

    status, content_type, body = http_request(f"{base_url}/health", data=make_wav())

    assert status == 404
    assert_json_error(content_type, body)
    assert converter.calls == []


def test_a_large_upload_to_an_unknown_path_still_gets_its_404(
    serve: Callable[[FakeConverter], str],
) -> None:
    # Replying before reading the body made Windows reset the socket under the reply:
    # 126 of 300 such requests ended in ConnectionAbortedError instead of a 404.
    base_url = serve(FakeConverter())
    upload = b"RIFF" + bytes(400_000)

    statuses = [http_request(f"{base_url}/health", data=upload)[0] for _ in range(20)]

    assert statuses == [404] * 20


def test_a_second_server_cannot_share_the_port_of_a_running_one() -> None:
    # Two stock HTTPServers both set SO_REUSEADDR, which on Windows lets them bind the
    # same port and silently split the requests between them.
    handler = rvc_server.make_handler(FakeConverter(), MODEL_NAME)
    with REAL_SERVER_CLASS(("127.0.0.1", 0), handler) as first:
        port = first.server_address[1]

        with pytest.raises(OSError):
            REAL_SERVER_CLASS(("127.0.0.1", port), handler)


# --- the Applio converter, against a fake Applio package -----------------------------------

FAKE_APPLIO_INFER = """
import os
import wave

calls = []
cwd_at_import = os.getcwd()
mode = "ok"


class VoiceConverter:
    def convert_audio(self, **kwargs):
        with wave.open(kwargs["audio_input_path"], "rb") as wav:
            input_audio = (wav.getnframes(), wav.getframerate(), wav.readframes(wav.getnframes()))
        calls.append({**kwargs, "input_audio": input_audio})
        if mode == "fail":
            raise RuntimeError("fake Applio failure")
        if mode == "ok":
            with open(kwargs["audio_output_path"], "wb") as output:
                output.write(b"RIFF\\x00\\x00\\x00\\x00WAVEfake")
"""


@pytest.fixture
def fake_applio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A folder that looks like Applio to `import rvc.infer.infer`; cwd and sys.path restored."""
    applio_dir = tmp_path / "Applio"
    package = applio_dir / "rvc" / "infer"
    package.mkdir(parents=True)
    (applio_dir / "rvc" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "infer.py").write_text(FAKE_APPLIO_INFER, encoding="utf-8")
    importlib.invalidate_caches()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield applio_dir
    for name in [name for name in sys.modules if name == "rvc" or name.startswith("rvc.")]:
        del sys.modules[name]


def fake_infer() -> ModuleType:
    return sys.modules["rvc.infer.infer"]


def test_load_applio_converter_imports_applio_from_its_folder(fake_applio: Path) -> None:
    rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)

    assert Path.cwd() == fake_applio.resolve()
    assert Path(fake_infer().cwd_at_import) == fake_applio.resolve()
    assert Path(fake_infer().__file__).parent.parent.parent == fake_applio.resolve()


def test_applio_converter_calls_convert_audio_with_rmvpe_and_contentvec(
    fake_applio: Path, tmp_path: Path
) -> None:
    converter = rvc_server.load_applio_converter(
        fake_applio, Path("voice.pth"), Path("voice.index")
    )
    wav = make_wav(frames=320)

    result = converter.convert(wav, pitch=4, index_rate=0.5, protect=0.2)

    assert result == b"RIFF\x00\x00\x00\x00WAVEfake"
    [call] = fake_infer().calls
    # Relative paths are resolved against the folder the server started in, not Applio's.
    assert call["model_path"] == str(tmp_path / "work" / "voice.pth")
    assert call["index_path"] == str(tmp_path / "work" / "voice.index")
    assert call["pitch"] == 4
    assert call["index_rate"] == 0.5
    assert call["protect"] == 0.2
    assert call["f0_method"] == "rmvpe"
    assert call["embedder_model"] == "contentvec"
    assert call["export_format"] == "WAV"
    assert call["input_audio"][:2] == (320, 16_000)
    assert call["audio_input_path"].endswith(".wav")
    assert call["audio_output_path"].endswith(".wav")


def test_load_applio_converter_refuses_an_index_applio_would_look_for_elsewhere(
    fake_applio: Path, tmp_path: Path
) -> None:
    # Applio replaces "trained" with "added" anywhere in the index path, then converts
    # without any index, silently, when that other file does not exist.
    index = tmp_path / "rvc" / "trained" / "tsukuyomi.index"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"index")
    assert "trained" not in str(tmp_path)

    with pytest.raises(ValueError) as exc_info:
        rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), index)

    message = str(exc_info.value)
    assert str(index) in message
    assert str(tmp_path / "rvc" / "added" / "tsukuyomi.index") in message
    assert "rvc.infer.infer" not in sys.modules  # it fails before loading Applio


def test_load_applio_converter_keeps_a_legacy_index_that_applio_redirects(
    fake_applio: Path, tmp_path: Path
) -> None:
    # Old RVC wrote an empty trained_*.index next to the filled added_*.index; Applio's
    # rewrite exists for exactly that pair.
    folder = tmp_path / "logs" / "tsukuyomi"
    folder.mkdir(parents=True)
    trained = folder / "trained_IVF256_Flat_nprobe_1_tsukuyomi_v2.index"
    for name in (trained.name, trained.name.replace("trained", "added")):
        (folder / name).write_bytes(b"index")

    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), trained)
    converter.convert(make_wav(), pitch=0, index_rate=0.75, protect=0.33)

    assert fake_infer().calls[0]["index_path"] == str(trained)


def test_applio_converter_passes_an_empty_index_path_without_an_index(fake_applio: Path) -> None:
    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)

    converter.convert(make_wav(), pitch=0, index_rate=0.75, protect=0.33)

    assert fake_infer().calls[0]["index_path"] == ""


def test_applio_converter_removes_its_temp_files(fake_applio: Path) -> None:
    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)

    converter.convert(make_wav(), pitch=0, index_rate=0.75, protect=0.33)

    [call] = fake_infer().calls
    assert not Path(call["audio_input_path"]).exists()
    assert not Path(call["audio_output_path"]).exists()


def test_applio_converter_removes_its_temp_files_when_applio_fails(fake_applio: Path) -> None:
    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)
    fake_infer().mode = "fail"

    with pytest.raises(RuntimeError, match="fake Applio failure"):
        converter.convert(make_wav(), pitch=0, index_rate=0.75, protect=0.33)

    [call] = fake_infer().calls
    assert not Path(call["audio_input_path"]).exists()
    assert not Path(call["audio_output_path"]).exists()


def test_applio_converter_fails_when_applio_writes_no_audio(fake_applio: Path) -> None:
    # Applio's convert_audio prints and returns without raising for some failures.
    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)
    fake_infer().mode = "silent"

    with pytest.raises(RuntimeError):
        converter.convert(make_wav(), pitch=0, index_rate=0.75, protect=0.33)


def test_warmup_converts_one_second_of_audible_tone_not_silence(fake_applio: Path) -> None:
    # Measured on the owner's RTX 5060 Ti: Applio's pitch embedding hits a CUDA
    # "index out of bounds" device assert on digital silence, which killed the server
    # at start; a quiet harmonic tone converts fine.
    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)

    converter.warmup()

    [call] = fake_infer().calls
    frames, sample_rate, audio = call["input_audio"]
    assert (frames, sample_rate) == (16_000, 16_000)
    assert any(audio)


def test_all_silent_audio_is_returned_unchanged_without_calling_applio(
    fake_applio: Path,
) -> None:
    # The same CUDA assert would poison the server process for every later request.
    converter = rvc_server.load_applio_converter(fake_applio, Path("voice.pth"), None)
    silence = make_wav(silent=True)

    assert converter.convert(silence, pitch=4, index_rate=0.75, protect=0.33) == silence
    assert fake_infer().calls == []


# --- main ----------------------------------------------------------------------------------


class FakeLoadedConverter(FakeConverter):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def warmup(self) -> None:
        self.events.append("warmup")


class FakeServer:
    """Stands in for RvcHttpServer: never binds, and serve_forever ends like Ctrl+C."""

    instances: list[FakeServer] = []

    def __init__(self, server_address: tuple[str, int], handler: type) -> None:
        self.server_address = server_address
        self.handler = handler
        self.events: list[str] = []
        FakeServer.instances.append(self)

    def serve_forever(self) -> None:
        self.events.append("serve_forever")
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.events.append("server_close")


@pytest.fixture
def fake_main(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    recorded: dict[str, Any] = {"events": [], "load_args": None}

    def load(applio_dir: Path, model: Path, index: Path | None) -> FakeLoadedConverter:
        recorded["load_args"] = (applio_dir, model, index)
        recorded["events"].append("load")
        return FakeLoadedConverter(recorded["events"])

    FakeServer.instances = []
    monkeypatch.setattr(rvc_server, "load_applio_converter", load)
    monkeypatch.setattr(rvc_server, "RvcHttpServer", FakeServer)
    return recorded


def test_main_loads_warms_up_then_serves_on_the_given_address(
    fake_main: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = rvc_server.main(
        [
            "--applio-dir",
            "C:/Applio",
            "--model",
            "voices/tsukuyomi.pth",
            "--index",
            "voices/tsukuyomi.index",
            "--host",
            "127.0.0.2",
            "--port",
            "9999",
        ]
    )

    assert exit_code == 0
    assert fake_main["load_args"] == (
        Path("C:/Applio"),
        Path("voices/tsukuyomi.pth"),
        Path("voices/tsukuyomi.index"),
    )
    assert fake_main["events"] == ["load", "warmup"]
    [server] = FakeServer.instances
    assert server.server_address == ("127.0.0.2", 9999)
    assert server.events == ["serve_forever", "server_close"]
    assert capsys.readouterr().out == (
        "RVC server ready on http://127.0.0.2:9999 (model tsukuyomi.pth)\n"
    )


def test_main_defaults_to_loopback_port_9881_without_an_index(
    fake_main: dict[str, Any],
) -> None:
    rvc_server.main(["--applio-dir", "C:/Applio", "--model", "tsukuyomi.pth"])

    assert fake_main["load_args"] == (Path("C:/Applio"), Path("tsukuyomi.pth"), None)
    [server] = FakeServer.instances
    assert server.server_address == ("127.0.0.1", 9881)


def test_main_serves_health_with_the_model_file_name(
    fake_main: dict[str, Any], serve_handler: Callable[[type], str]
) -> None:
    rvc_server.main(["--applio-dir", "C:/Applio", "--model", "voices/tsukuyomi.pth"])

    [server] = FakeServer.instances
    base_url = serve_handler(server.handler)
    status, _, body = http_request(f"{base_url}/health")
    assert (status, json.loads(body)) == (200, {"ok": True, "model": "tsukuyomi.pth"})


@pytest.mark.parametrize(
    "argv",
    [
        ["--applio-dir", "C:/Applio"],
        ["--model", "tsukuyomi.pth"],
        ["--applio-dir", "C:/Applio", "--model", "m.pth", "--port", "abc"],
    ],
)
def test_main_rejects_incomplete_arguments(fake_main: dict[str, Any], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        rvc_server.main(argv)

    assert exc_info.value.code == 2
    assert fake_main["events"] == []
    assert FakeServer.instances == []
