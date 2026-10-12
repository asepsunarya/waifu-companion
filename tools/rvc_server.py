"""Loopback HTTP server that converts speech to the RVC voice with Applio.

Runs under Applio's own Python, not Cyrene's, so it uses the standard library
only; Applio is imported lazily in `load_applio_converter`, which keeps this
module importable (and testable) without Applio. The model loads once at start
and requests are served one at a time: the GPU model is not shared between threads.

    python tools/rvc_server.py --applio-dir C:/AI/Applio --model M.pth [--index I.index]
"""

from __future__ import annotations

import argparse
import array
import io
import json
import logging
import math
import os
import sys
import tempfile
import wave
from collections.abc import Callable, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, NamedTuple, Protocol, TypeVar
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 20 * 1024 * 1024
# Long enough for a 20 MB body over loopback; a stalled client must not block the
# single-threaded server forever.
SOCKET_TIMEOUT_S = 30


class Converter(Protocol):
    def convert(
        self, wav_bytes: bytes, *, pitch: int, index_rate: float, protect: float
    ) -> bytes: ...


class RvcHttpServer(HTTPServer):
    """A single-threaded HTTPServer that will not share its port with a running server."""

    # HTTPServer sets SO_REUSEADDR, which on Windows lets a second server bind the port
    # of one that is already listening; the two would silently split the requests.
    allow_reuse_address = sys.platform != "win32"


class QueryError(ValueError):
    """The /convert query string is malformed or out of range."""


class ConvertParams(NamedTuple):
    pitch: int
    index_rate: float
    protect: float


DEFAULT_PARAMS = ConvertParams(pitch=0, index_rate=0.75, protect=0.33)
_QUERY_NAMES = frozenset(ConvertParams._fields)
_Number = TypeVar("_Number", int, float)


def parse_convert_query(query: str) -> ConvertParams:
    """Validates ?pitch=&index_rate=&protect=; a missing parameter takes its default."""
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise QueryError(f"malformed query: {exc}") from exc
    unknown = sorted(set(values) - _QUERY_NAMES)
    if unknown:
        raise QueryError(f"unknown query parameters: {', '.join(unknown)}")
    defaults = DEFAULT_PARAMS
    return ConvertParams(
        pitch=_query_number(values, "pitch", int, default=defaults.pitch, low=-24, high=24),
        index_rate=_query_number(
            values, "index_rate", float, default=defaults.index_rate, low=0.0, high=1.0
        ),
        protect=_query_number(
            values, "protect", float, default=defaults.protect, low=0.0, high=0.5
        ),
    )


def _query_number(
    values: dict[str, list[str]],
    name: str,
    parse: Callable[[str], _Number],
    *,
    default: _Number,
    low: _Number,
    high: _Number,
) -> _Number:
    raw = values.get(name)
    if raw is None:
        return default
    kind = "an integer" if parse is int else "a number"
    error = QueryError(f"{name} must be {kind} from {low:g} to {high:g}, given once")
    if len(raw) != 1:
        raise error
    try:
        value = parse(raw[0])
    except ValueError:
        raise error from None
    if not low <= value <= high:  # also false for nan
        raise error
    return value


def _is_wav(body: bytes) -> bool:
    return len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WAVE"


def make_handler(converter: Converter, model_name: str) -> type[BaseHTTPRequestHandler]:
    """Builds the request handler serving GET /health and POST /convert with `converter`."""

    class RvcRequestHandler(BaseHTTPRequestHandler):
        timeout = SOCKET_TIMEOUT_S

        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "model": model_name})
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            url = urlsplit(self.path)
            if url.path != "/convert":
                self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            body = self._read_body()
            if body is None:
                return
            try:
                params = parse_convert_query(url.query)
            except QueryError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if not _is_wav(body):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "body must be a WAV file")
                return
            try:
                converted = converter.convert(
                    body, pitch=params.pitch, index_rate=params.index_rate, protect=params.protect
                )
            except Exception:
                logger.exception("RVC conversion failed")
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "conversion failed")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(converted)))
            self.end_headers()
            self.wfile.write(converted)

        def _read_body(self) -> bytes | None:
            """Reads the whole body, or answers the error and returns None.

            The size is checked before reading, so an oversized upload is refused
            without being read. A body that is read in full also keeps the socket
            from being reset under an error reply that follows.
            """
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._send_error_json(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
                return None
            try:
                length = int(raw_length)
            except ValueError:
                length = -1
            if length < 0:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
                return None
            if length > MAX_BODY_BYTES:
                self._send_error_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"body is larger than {MAX_BODY_BYTES} bytes",
                )
                return None
            body = self.rfile.read(length)
            if len(body) != length:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "body ended early")
                return None
            return body

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"ok": False, "error": message})

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("%s %s", self.address_string(), format % args)

        def log_error(self, format: str, *args: Any) -> None:
            logger.warning("%s %s", self.address_string(), format % args)

    return RvcRequestHandler


class ApplioConverter:
    """Converts WAV bytes with Applio's VoiceConverter by way of temporary WAV files."""

    def __init__(self, voice_converter: Any, model_path: Path, index_path: Path | None) -> None:
        self._voice_converter = voice_converter
        self._model_path = model_path
        self._index_path = index_path

    def convert(self, wav_bytes: bytes, *, pitch: int, index_rate: float, protect: float) -> bytes:
        if _is_digital_silence(wav_bytes):
            # Applio's pitch embedding trips a CUDA device assert on all-zero audio, which
            # poisons this process for every later request; silence needs no conversion.
            logger.debug("Returning all-silent audio unchanged")
            return wav_bytes
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as source:
            source.write(wav_bytes)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as target:
            pass
        input_path, output_path = Path(source.name), Path(target.name)
        try:
            self._voice_converter.convert_audio(
                audio_input_path=str(input_path),
                audio_output_path=str(output_path),
                model_path=str(self._model_path),
                index_path=str(self._index_path) if self._index_path is not None else "",
                pitch=pitch,
                f0_method="rmvpe",
                index_rate=index_rate,
                protect=protect,
                embedder_model="contentvec",
                export_format="WAV",
            )
            converted = output_path.read_bytes()
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
        if not converted:
            # convert_audio prints and returns without raising for some failures.
            raise RuntimeError("Applio returned without writing any audio")
        return converted

    def warmup(self) -> None:
        """Converts 1 s of quiet tone so the model and embedder load before the first reply.

        Not silence: digital silence would crash Applio's pitch embedding on CUDA.
        """
        self.convert(
            _tone_wav(seconds=1, sample_rate=16_000),
            pitch=DEFAULT_PARAMS.pitch,
            index_rate=DEFAULT_PARAMS.index_rate,
            protect=DEFAULT_PARAMS.protect,
        )


def _tone_wav(*, seconds: int, sample_rate: int) -> bytes:
    """A 220 Hz tone with four overtones under a Hann fade, as 16-bit mono WAV."""
    frames = seconds * sample_rate
    samples = array.array("h")
    for n in range(frames):
        t = n / sample_rate
        fade = 0.5 - 0.5 * math.cos(2 * math.pi * n / (frames - 1))
        value = sum(0.2 / k * math.sin(2 * math.pi * 220 * k * t) for k in range(1, 6))
        samples.append(int(32767 * 0.5 * fade * value))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


def _is_digital_silence(wav_bytes: bytes) -> bool:
    """True when every byte of the RIFF `data` chunk is zero (PCM or float alike)."""
    position = 12  # after "RIFF" <size> "WAVE"
    while position + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[position : position + 4]
        size = int.from_bytes(wav_bytes[position + 4 : position + 8], "little")
        if chunk_id == b"data":
            return not any(wav_bytes[position + 8 : position + 8 + size])
        position += 8 + size + (size & 1)
    return False


def load_applio_converter(applio_dir: Path, model: Path, index: Path | None) -> ApplioConverter:
    """Imports Applio from `applio_dir` and returns a converter for the model.

    Applio resolves its own assets (embedders, rmvpe) relative to the working
    directory, so this moves the process into `applio_dir` for good; the model and
    index paths are made absolute first.
    """
    model_path = model.resolve()
    index_path = index.resolve() if index is not None else None
    if index_path is not None:
        _check_applio_finds_index(index_path)
    applio_root = applio_dir.resolve()
    sys.path.insert(0, str(applio_root))
    os.chdir(applio_root)
    from rvc.infer.infer import VoiceConverter

    return ApplioConverter(VoiceConverter(), model_path, index_path)


def _check_applio_finds_index(index_path: Path) -> None:
    """Raises ValueError when Applio would silently convert without this index.

    Applio's convert_audio replaces "trained" with "added" anywhere in the index path,
    for legacy RVC folders that hold an empty trained_*.index next to the filled
    added_*.index, and runs without an index when the rewritten path does not exist.
    """
    redirected = Path(str(index_path).replace("trained", "added"))
    if redirected == index_path:
        return
    if redirected.is_file():
        logger.info("Applio reads the index %s in place of %s", redirected, index_path)
        return
    raise ValueError(
        f"Applio would look for the index at {redirected} instead of {index_path} (it "
        "replaces 'trained' with 'added' anywhere in the path) and convert without it. "
        "Move or rename the index so that its path does not contain 'trained'."
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve RVC voice conversion with Applio.")
    parser.add_argument("--applio-dir", type=Path, required=True, help="Applio folder")
    parser.add_argument("--model", type=Path, required=True, help="RVC model (.pth)")
    parser.add_argument("--index", type=Path, default=None, help="feature index (.index)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9881)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    model_name = args.model.name
    logger.info("Loading Applio from %s with model %s", args.applio_dir, args.model)
    converter = load_applio_converter(args.applio_dir, args.model, args.index)
    converter.warmup()
    server = RvcHttpServer((args.host, args.port), make_handler(converter, model_name))
    host, port = server.server_address[:2]
    print(f"RVC server ready on http://{host}:{port} (model {model_name})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("RVC server stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
