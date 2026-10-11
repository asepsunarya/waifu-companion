"""Anime voice: every spoken reply is converted by an RVC model served by Applio on loopback.

Applio lives in its own Python 3.12 environment, so the model runs in a separate,
long-lived server process (`tools/rvc_server.py`) that loads it once. `RvcClient`
talks to that server over HTTP; `RvcServerProcess` starts it when nothing answers
on the port yet, and stops only a server it started itself.
"""

from __future__ import annotations

import contextlib
import ctypes
import http.client
import io
import ipaddress
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import IO, Protocol

import numpy as np
import soundfile as sf

from waifu.config import RvcConfig
from waifu.single_instance import InstanceLock

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 2.0
# A clip waits 5 s plus half its length for its conversion, and never more than 30 s.
CONVERT_TIMEOUT_S = 30.0
CONVERT_TIMEOUT_BASE_S = 5.0
CONVERT_TIMEOUT_PER_AUDIO_S = 0.5
POLL_INTERVAL_S = 0.5
STOP_TIMEOUT_S = 5.0
LOG_FILE = Path("logs") / "rvc_server.log"
START_LOCK = Path("data") / "rvc_server.lock"
# src/waifu/voice/rvc.py -> repository root; the package is installed editable from src/.
SERVER_SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "rvc_server.py"
_ERROR_TEXT_LIMIT = 300
_README_HINT = "Lihat bagian suara anime di README."

# What a failed request to the server raises: connection refused/reset, timeouts
# (TimeoutError is an OSError), URLError, and malformed HTTP replies.
_TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


class RvcError(Exception):
    """Voice conversion is unavailable; the message is Indonesian and safe to show."""


class RvcUnavailableError(RvcError):
    """The server could not be reached, or did not answer in time."""


class HttpGet(Protocol):
    def __call__(
        self, url: str, data: None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        """Send a GET; return (status, body) for any HTTP status, raise on transport errors."""
        ...


class HttpPost(Protocol):
    def __call__(
        self, url: str, data: bytes, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        """Send a POST; return (status, body) for any HTTP status, raise on transport errors."""
        ...


class RvcClient:
    """HTTP client for the local RVC server: `served_model()` and `convert()`."""

    def __init__(
        self,
        config: RvcConfig,
        *,
        http_post: HttpPost | None = None,
        http_get: HttpGet | None = None,
    ) -> None:
        self._config = config
        self.base_url = f"http://{config.host}:{config.port}"
        self._http_post = http_post or _send
        self._http_get = http_get or _send

    def served_model(self) -> str | None:
        """The model file the RVC server on the port serves; None while none answers.

        A reply other than the server's {"ok": true, "model": ...} is None too: it comes
        from some other program holding the port.
        """
        try:
            status, body = self._http_get(f"{self.base_url}/health", None, {}, HEALTH_TIMEOUT_S)
        except _TRANSPORT_ERRORS as exc:
            logger.debug("RVC server at %s is not answering: %s", self.base_url, exc)
            return None
        if status != 200:
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return None
        model = payload.get("model")
        return model if isinstance(model, str) else None

    def convert(self, audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
        """Return `audio` in the model's voice as mono float32 at the server's sample rate."""
        config = self._config
        query = urllib.parse.urlencode(
            {"pitch": config.pitch, "index_rate": config.index_rate, "protect": config.protect}
        )
        url = f"{self.base_url}/convert?{query}"
        body = _encode_wav(audio, sample_rate)
        timeout = min(
            CONVERT_TIMEOUT_S,
            CONVERT_TIMEOUT_BASE_S + CONVERT_TIMEOUT_PER_AUDIO_S * len(audio) / sample_rate,
        )
        try:
            status, reply = self._http_post(url, body, {"Content-Type": "audio/wav"}, timeout)
        except _TRANSPORT_ERRORS as exc:
            raise RvcUnavailableError(
                f"Server suara RVC di {self.base_url} tidak bisa dihubungi: {exc}"
            ) from exc
        if status != 200:
            raise RvcError(
                f"Server suara RVC gagal mengubah suara (HTTP {status}): {_error_text(reply)}"
            )
        return _decode_wav(reply)


class ServerProcess(Protocol):
    """The part of `subprocess.Popen` that `RvcServerProcess` uses."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


PopenFactory = Callable[..., ServerProcess]


class ProcessJob(Protocol):
    """Keeps processes from outliving this one; `KillOnCloseJob` is the real one."""

    def add(self, process: ServerProcess) -> None: ...

    def close(self) -> None: ...


class RvcServerProcess:
    """Makes sure an RVC server with the configured model answers, starting one if needed.

    A server that already answers (left over from an earlier run, or started by
    hand or by another command) is reused and never stopped here, provided it serves
    the configured model; a server this object started is terminated by `stop()` or
    on leaving the `with` block. On Windows it is also put in a `KillOnCloseJob`, so
    it dies with this process however that ends: closing Cyrene's console window
    ends Python without running `finally` blocks.

    Cyrene's commands take turns starting the server (a lock file): one that finds
    another command starting it waits for that server instead of loading a second
    copy of the model onto the GPU.
    """

    def __init__(
        self,
        config: RvcConfig,
        *,
        popen: PopenFactory | None = None,
        client: RvcClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log_path: Path = LOG_FILE,
        lock_path: Path = START_LOCK,
        server_script: Path | None = None,
        job_factory: Callable[[], ProcessJob] | None = None,
    ) -> None:
        self._config = config
        # Looked up at construction, not bound as a default, so tests/conftest.py's
        # guarded Popen is the one used if a test forgets to inject a fake.
        self._popen = popen or subprocess.Popen
        self._client = client or RvcClient(config)
        self._clock = clock
        self._sleep = sleep
        self._log_path = Path(log_path)
        self._lock_path = Path(lock_path)
        self._server_script = Path(server_script) if server_script else SERVER_SCRIPT
        # Elsewhere a closed terminal hangs up the whole process group, server included.
        self._job_factory = job_factory or (KillOnCloseJob if sys.platform == "win32" else None)
        self._process: ServerProcess | None = None
        self._log: IO[bytes] | None = None
        self._job: ProcessJob | None = None

    def ensure_running(self) -> None:
        """Return once a server with the configured model answers; RvcError when none can."""
        require_loopback_host(self._config.host)
        lock = InstanceLock(self._lock_path)
        if not lock.acquire():
            logger.info("Another command is starting the RVC server; waiting for it")
            self._accept(self._wait_for_server(None))
            return
        with lock:
            served = self._client.served_model()
            if served is None:
                self._start()
            else:
                self._accept(served)

    def stop(self) -> None:
        """Terminate the server this object started, if any; safe to call repeatedly."""
        process, self._process = self._process, None
        log, self._log = self._log, None
        job, self._job = self._job, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                logger.warning("RVC server (pid %s) ignored terminate; killing it", process.pid)
                process.kill()
            logger.info("Stopped the RVC server (pid %s)", process.pid)
        if job is not None:
            job.close()
        if log is not None:
            log.close()

    def __enter__(self) -> RvcServerProcess:
        self.ensure_running()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _start(self) -> None:
        self.stop()
        command = self._command()
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.ExitStack() as on_failure:
            log = on_failure.enter_context(self._log_path.open("ab"))
            try:
                process = self._popen(
                    command,
                    cwd=self._config.applio_dir.absolute(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=_server_environment(),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                raise RvcError(
                    f"Server suara RVC tidak bisa dijalankan: {exc}. {_README_HINT}"
                ) from exc
            on_failure.pop_all()
        self._process, self._log = process, log
        logger.info(
            "Started the RVC server (pid %s) at %s; output goes to %s",
            process.pid,
            self._client.base_url,
            self._log_path,
        )
        try:
            self._job = self._tie_to_this_process(process)
            served = self._wait_for_server(process)
        except BaseException:  # RvcError, or Ctrl+C while the model loads: never orphan it
            self.stop()
            raise
        self._accept(served)

    def _accept(self, served: str) -> None:
        """Take the answering server: ours while our child runs, else a reused one."""
        process = self._process
        if process is not None and process.poll() is None:
            return
        # A child that exited (it lost the port to another server, say) is not what
        # answers: forget it, so that server is never claimed or stopped as ours.
        self.stop()
        require_served_model(self._config, served, self._client.base_url)
        logger.info(
            "Reusing the RVC server already answering at %s (model %s)",
            self._client.base_url,
            served,
        )

    def _command(self) -> list[str]:
        config = self._config
        if config.model is None:
            raise RvcError(f"tts.rvc.model (file .pth) belum diisi di config.yaml. {_README_HINT}")
        _require(
            config.applio_dir.is_dir(), "Folder Applio", config.applio_dir, "tts.rvc.applio_dir"
        )
        _require(
            config.python_path.is_file(),
            "Python Applio",
            config.python_path,
            "tts.rvc.python atau tts.rvc.applio_dir",
        )
        _require(config.model.is_file(), "Model RVC (.pth)", config.model, "tts.rvc.model")
        if config.index is not None:
            _require(config.index.is_file(), "File index RVC", config.index, "tts.rvc.index")
        _require(
            self._server_script.is_file(),
            "Skrip server RVC",
            self._server_script,
            "folder tools/ di repo ini",
        )
        # The server runs in applio_dir, so relative paths from config.yaml are anchored here.
        command = [
            str(config.python_path.absolute()),
            str(self._server_script.absolute()),
            "--applio-dir",
            str(config.applio_dir.absolute()),
            "--model",
            str(config.model.absolute()),
        ]
        if config.index is not None:
            command += ["--index", str(config.index.absolute())]
        return command + ["--host", config.host, "--port", str(config.port)]

    def _tie_to_this_process(self, process: ServerProcess) -> ProcessJob | None:
        """A job holding the server, so the OS ends it when this process ends."""
        if self._job_factory is None:
            return None
        job: ProcessJob | None = None
        try:
            job = self._job_factory()
            job.add(process)
        except OSError as exc:
            if job is not None:
                job.close()
            logger.warning(
                "Could not tie the RVC server (pid %s) to this process, so closing this "
                "window may leave it running: %s",
                process.pid,
                exc,
            )
            return None
        return job

    def _wait_for_server(self, process: ServerProcess | None) -> str:
        """Poll until a server answers and return its model; RvcError if `process` exits."""
        timeout = self._config.startup_timeout_s
        started = self._clock()
        while (served := self._client.served_model()) is None:
            exit_code = process.poll() if process is not None else None
            if exit_code is not None:
                raise RvcError(
                    f"Server suara RVC berhenti saat mulai (kode {exit_code}). "
                    f"Lihat {self._log_path}."
                )
            if self._clock() - started >= timeout:
                raise RvcError(
                    f"Server suara RVC belum siap setelah {timeout:g} detik. "
                    f"Lihat {self._log_path}."
                )
            self._sleep(POLL_INTERVAL_S)
        logger.info("RVC server ready after %.1f s", self._clock() - started)
        return served


def require_loopback_host(host: str) -> None:
    """RvcError unless `host` (tts.rvc.host) is an IPv4 loopback address like 127.0.0.1.

    The server has no password, so it must not listen beyond this PC; and the server
    listens on IPv4 only, so "localhost" would cost ~2 s per request on Windows,
    which tries ::1 first.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None or address.version != 4 or not address.is_loopback:
        raise RvcError(
            f"tts.rvc.host harus alamat IPv4 loopback seperti 127.0.0.1, bukan {host!r}: "
            "server suara RVC tanpa kata sandi hanya boleh dijangkau dari PC ini."
        )


def require_served_model(config: RvcConfig, served: str, base_url: str) -> None:
    """RvcError unless the server at `base_url` serves tts.rvc.model.

    A running server keeps the model it loaded at start, so after `tts.rvc.model`
    changes, an older server would go on speaking with the old checkpoint.
    """
    wanted = config.model.name if config.model is not None else None
    if served != wanted:
        raise RvcError(
            f"Server suara RVC lama dengan model {served} masih berjalan di {base_url}, "
            f"bukan model di tts.rvc.model ({wanted}). Hentikan dulu server itu. {_README_HINT}"
        )


# --- Windows job object ---------------------------------------------------------------------
# Structures and constants from winnt.h; the extended limits are 144 bytes on 64-bit.

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # JOBOBJECTINFOCLASS value
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class _JobBasicLimits(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    )


class _IoCounters(ctypes.Structure):
    _fields_ = tuple(
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    )


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _JobBasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class KillOnCloseJob:
    """A Windows job object: the OS kills every process in it when its handle closes.

    Only this process holds the handle, so the processes in the job die with this one
    however it ends, or earlier on `close()`. Their children join the job too, unless
    they break away from it.
    """

    def __init__(self) -> None:
        self._kernel32 = _kernel32()
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle: int | None = handle
        limits = _JobExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = self._kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def add(self, process: ServerProcess) -> None:
        """Put a `subprocess.Popen` child in the job; OSError when Windows refuses."""
        # Popen's own process handle, never OpenProcess(pid): a test's fake process has
        # none, so it can never tie (and later kill) a real process that has its pid.
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise TypeError(f"{process!r} has no process handle; only a Popen child can join")
        if not self._kernel32.AssignProcessToJobObject(self._handle, int(process_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        """Close the job, killing whatever is still in it; safe to call repeatedly."""
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)


def _kernel32() -> ctypes.CDLL:
    # A private instance, so these signatures never clash with other users of windll.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, success = ctypes.c_void_p, ctypes.c_int
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    kernel32.CreateJobObjectW.restype = handle
    kernel32.SetInformationJobObject.argtypes = (
        handle,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    kernel32.SetInformationJobObject.restype = success
    kernel32.AssignProcessToJobObject.argtypes = (handle, handle)
    kernel32.AssignProcessToJobObject.restype = success
    kernel32.CloseHandle.argtypes = (handle,)
    kernel32.CloseHandle.restype = success
    return kernel32


# --- helpers ---------------------------------------------------------------------------------


def _server_environment() -> dict[str, str]:
    """This environment, with the server's output made fit for its log file.

    Redirected to a file, Python would encode output in the ANSI code page, where a
    model named in Japanese cannot be printed (the server would crash at "ready"), and
    would buffer what Applio prints, which `terminate()` then throws away.
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def _require(present: bool, what: str, path: Path, setting: str) -> None:
    if not present:
        raise RvcError(f"{what} tidak ditemukan: {path}. Periksa {setting}. {_README_HINT}")


def _encode_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    try:
        sf.write(
            buffer,
            np.asarray(audio, dtype=np.float32),
            sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
    except sf.SoundFileError as exc:
        raise RvcError(f"Audio tidak bisa dikirim ke server suara RVC: {exc}") from exc
    return buffer.getvalue()


def _decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    try:
        audio, sample_rate = sf.read(io.BytesIO(data), dtype="float32")
    except sf.SoundFileError as exc:
        raise RvcError(f"Audio dari server suara RVC tidak bisa dibaca: {exc}") from exc
    if audio.ndim == 2:
        audio = audio.mean(axis=1, dtype=np.float32)
    return audio, int(sample_rate)


def _error_text(body: bytes) -> str:
    """The server's JSON {"error": ...} message, else the start of the raw body."""
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return str(payload["error"])
    return body.decode("utf-8", errors="replace").strip()[:_ERROR_TEXT_LIMIT]


def _direct_opener() -> urllib.request.OpenerDirector:
    # The server is local: a system or environment proxy must never carry its traffic.
    # Built per request, which is cheap, so no opener outlives the settings it saw.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _send(
    url: str, data: bytes | None, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    """GET when `data` is None, else POST; error statuses are returned, not raised."""
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with _direct_opener().open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read()
