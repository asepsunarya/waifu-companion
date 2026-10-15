"""The window that shows Cyrene's avatar page: the Electron mascot, an Edge app window, or none.

`AvatarWindow` starts that program as a child of this process and puts it in a
kill-on-close job, so the window disappears with Cyrene however she ends; `close()`
ends only the window it started. The mascot is also watched: its output goes to the
log, and a mascot that crashed is opened again.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Protocol

from waifu.avatar.server import AvatarError
from waifu.config import AvatarConfig
from waifu.voice.rvc import KillOnCloseJob, ProcessJob, ServerProcess

logger = logging.getLogger(__name__)

STOP_TIMEOUT_S = 5.0
# A mascot that crashed is opened again after REOPEN_DELAY_S, at most REOPEN_LIMIT times
# in any REOPEN_WINDOW_S; after that it stays closed until Cyrene restarts.
REOPEN_DELAY_S = 3.0
REOPEN_LIMIT = 3
REOPEN_WINDOW_S = 600.0
# How the mascot's own diagnostics start (avatar-app); its other output is Chromium's.
MASCOT_LOG_PREFIX = "Cyrene mascot:"
# A diagnostic is a warning when `gone`, `lost`, `unresponsive`, `crashed`, `unavailable`,
# or `skipped`, in any case, ends one of its words (no letter follows): `render-process-gone`,
# `webglcontextlost`, `webgl-unavailable`, and `page-reload skipped` (she stays gone) are
# warnings, `responsive` and `webglcontextrestored` are not.
_TROUBLE = re.compile(
    r"(?:gone|lost|unresponsive|crashed|unavailable|skipped)(?![a-z])", re.IGNORECASE
)
# src/waifu/avatar/window.py -> repository root; the package is installed editable from src/.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ELECTRON_EXE = Path("node_modules") / "electron" / "dist" / "electron.exe"
EDGE_EXE = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
# Edge's own profile for the avatar: with it the app window is a separate Edge process,
# which Cyrene can close without touching the owner's browser.
EDGE_PROFILE = Path("data") / "avatar-edge-profile"
_EDGE_FOLDERS = ("ProgramFiles(x86)", "ProgramFiles")
# The mascot's stdout and stderr share one pipe, read as UTF-8 lines; a byte that is not
# UTF-8 becomes U+FFFD instead of stopping the reader. Edge's output is not needed.
_MASCOT_OUTPUT: dict[str, Any] = {
    "stdout": subprocess.PIPE,
    "stderr": subprocess.STDOUT,
    "encoding": "utf-8",
    "errors": "replace",
    "bufsize": 1,
}
_DISCARDED_OUTPUT: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
_OUTPUT_THREAD = "avatar-mascot-output"
_SUPERVISOR_THREAD = "avatar-mascot-supervisor"


class WindowProcess(ServerProcess, Protocol):
    """A started window: a `ServerProcess` with the pipe the mascot writes its output to."""

    stdout: IO[str] | None


WindowPopen = Callable[..., WindowProcess]


class _Thread(Protocol):
    def start(self) -> None: ...


ThreadFactory = Callable[[Callable[[], None], str], _Thread]


def _daemon_thread(target: Callable[[], None], name: str) -> threading.Thread:
    """A thread that never keeps Cyrene from exiting; the job ends the mascot then."""
    return threading.Thread(target=target, name=name, daemon=True)


class AvatarWindow:
    """Opens the avatar page per `avatar.window` and closes what it opened.

    `mascot` runs the Electron shell in `avatar.electron_dir`, `app` runs Edge in app
    mode, `none` opens nothing (the page is only served). On Windows the child goes in a
    `KillOnCloseJob`: closing Cyrene's console ends Python without running `finally`.

    Two daemon threads watch the mascot. One logs each line of its output: a
    `Cyrene mascot:` diagnostic at INFO, or at WARNING when it reports something gone,
    lost, unresponsive, crashed, unavailable, or skipped (see `_TROUBLE`), and Chromium's
    own lines at DEBUG.
    The other waits for it to end: a mascot that exits with a non-zero code while
    `close()` is not ending it is opened again after REOPEN_DELAY_S, at most REOPEN_LIMIT
    times in REOPEN_WINDOW_S; exit code 0 means the owner closed her, so she stays
    closed. Edge's app window is left alone.
    """

    def __init__(
        self,
        config: AvatarConfig,
        url: str,
        *,
        popen: WindowPopen | None = None,
        edge_path: Path | None = None,
        job_factory: Callable[[], ProcessJob] | None = None,
        project_root: Path = PROJECT_ROOT,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        thread_factory: ThreadFactory = _daemon_thread,
    ) -> None:
        self._config = config
        self._url = url
        # Looked up at construction, not bound as a default, so tests/conftest.py's
        # guarded Popen is the one used if a test forgets to inject a fake.
        self._popen: WindowPopen = popen or subprocess.Popen
        self._edge_path = Path(edge_path) if edge_path is not None else None
        self._job_factory = job_factory or (KillOnCloseJob if sys.platform == "win32" else None)
        self._project_root = Path(project_root)
        self._environ = os.environ if environ is None else environ
        self._clock = clock
        self._sleep = sleep
        self._thread_factory = thread_factory
        # Keeps the supervisor's reopen and `close()` from interleaving.
        self._lock = threading.Lock()
        self._process: WindowProcess | None = None
        self._job: ProcessJob | None = None
        self._supervision: _Supervision | None = None

    def program(self) -> Path | None:
        """The program `open()` runs, which may be missing; None for `none` or without Edge."""
        if self._config.window == "mascot":
            return self._electron_dir() / ELECTRON_EXE
        if self._config.window == "app":
            return self._edge_path or _find_edge(self._environ)
        return None

    def open(self) -> None:
        """Open the window unless it is already up; AvatarError (Indonesian) when it cannot."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
        self.close()
        command = self._command()
        if command is None:
            logger.info("avatar.window is none: the avatar page is only served at %s", self._url)
            return
        with self._lock:
            process = self._launch(command)
            if self._config.window == "mascot":
                self._supervision = _Supervision()
                self._watch(process, command, self._supervision)

    def close(self) -> None:
        """End the window this object started, if any; safe to repeat, never raises.

        The mascot's supervision stops first, so the window ended here is never reopened.
        """
        with self._lock:
            if self._supervision is not None:
                self._supervision.closing = True
                self._supervision = None
            process, self._process = self._process, None
            job, self._job = self._job, None
        if process is not None:
            try:
                _end(process)
            except Exception as exc:  # cleanup must not stop Cyrene's shutdown
                logger.warning("Could not close the avatar window (pid %s): %s", process.pid, exc)
        if job is not None:
            _close_job(job)

    def __enter__(self) -> AvatarWindow:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _electron_dir(self) -> Path:
        return self._project_root / self._config.electron_dir

    def _command(self) -> list[str] | None:
        config = self._config
        if config.window == "mascot":
            app_dir = self._electron_dir()
            electron = app_dir / ELECTRON_EXE
            if not electron.is_file():
                raise AvatarError(
                    f"Program maskot (Electron) tidak ditemukan: {electron}. Pasang dulu: "
                    f"buka folder {app_dir} lalu jalankan `npm install` dan "
                    "`node node_modules/electron/install.js`."
                )
            return [
                str(electron),
                str(app_dir),
                f"--url={self._url}?mode=mascot",
                f"--width={config.width}",
                f"--height={config.height}",
                f"--corner={config.corner}",
                f"--always-on-top={int(config.always_on_top)}",
            ]
        if config.window == "app":
            edge = self.program()
            if edge is None or not edge.is_file():
                where = edge or " atau ".join(f"%{folder}%\\{EDGE_EXE}" for folder in _EDGE_FOLDERS)
                raise AvatarError(
                    f"Microsoft Edge tidak ditemukan ({where}). Pasang dengan "
                    "`winget install --id Microsoft.Edge`, atau pakai `avatar.window: mascot` "
                    "di config.yaml."
                )
            return [
                str(edge),
                f"--app={self._url}?mode=app",
                f"--window-size={config.width},{config.height}",
                f"--user-data-dir={self._project_root / EDGE_PROFILE}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
        return None

    def _launch(self, command: list[str]) -> WindowProcess:
        """Start the window as this object's own, in its job; the caller holds the lock."""
        output = _MASCOT_OUTPUT if self._config.window == "mascot" else _DISCARDED_OUTPUT
        try:
            process = self._popen(command, stdin=subprocess.DEVNULL, **output)
        except OSError as exc:
            raise AvatarError(f"Jendela avatar tidak bisa dibuka: {exc}") from exc
        self._process = process
        logger.info("Opened the avatar %s window (pid %s)", self._config.window, process.pid)
        self._job = self._tie_to_this_process(process)
        return process

    def _watch(self, process: WindowProcess, command: list[str], supervision: _Supervision) -> None:
        """Start the mascot's output reader and its supervisor."""
        assert process.stdout is not None  # the mascot is started with stdout=PIPE
        self._thread_factory(partial(_relay_output, process.stdout), _OUTPUT_THREAD).start()
        supervise = partial(self._supervise, supervision, command, process)
        self._thread_factory(supervise, _SUPERVISOR_THREAD).start()

    def _supervise(
        self, supervision: _Supervision, command: list[str], process: WindowProcess
    ) -> None:
        """Wait for the mascot to end, and open it again if it crashed (see the class)."""
        exit_code = process.wait()
        with self._lock:
            if supervision.closing:
                return
            if exit_code == 0:
                logger.info(
                    "The avatar mascot (pid %s) was closed; Cyrene goes on without it",
                    process.pid,
                )
                return
            if not supervision.may_reopen(self._clock()):
                logger.warning(
                    "The avatar mascot (pid %s) exited with code %s after being reopened %d "
                    "times in %d minutes, so it stays closed. Restart Cyrene to bring it back.",
                    process.pid,
                    exit_code,
                    REOPEN_LIMIT,
                    REOPEN_WINDOW_S // 60,
                )
                return
        logger.warning(
            "The avatar mascot (pid %s) exited with code %s; reopening it in %.0f s",
            process.pid,
            exit_code,
            REOPEN_DELAY_S,
        )
        self._sleep(REOPEN_DELAY_S)
        with self._lock:
            if supervision.closing:
                return
            job, self._job = self._job, None
            if job is not None:
                _close_job(job)  # also ends whatever the crashed mascot left running
            try:
                reopened = self._launch(command)
            except AvatarError as exc:
                logger.warning("Could not reopen the avatar mascot: %s", exc)
                return
            self._watch(reopened, command, supervision)

    def _tie_to_this_process(self, process: ServerProcess) -> ProcessJob | None:
        """A job holding the window, so the OS ends it when this process ends."""
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
                "Could not tie the avatar window (pid %s) to this process, so it may stay "
                "open after Cyrene ends: %s",
                process.pid,
                exc,
            )
            return None
        return job


class _Supervision:
    """One `open()` of the mascot: reopened after crashes until `close()` sets `closing`."""

    def __init__(self) -> None:
        self.closing = False
        self._reopened_at: list[float] = []

    def may_reopen(self, now: float) -> bool:
        """Whether one more reopen at `now` keeps within the limit; it is counted if so."""
        self._reopened_at = [at for at in self._reopened_at if now - at < REOPEN_WINDOW_S]
        if len(self._reopened_at) >= REOPEN_LIMIT:
            return False
        self._reopened_at.append(now)
        return True


def _relay_output(stream: IO[str]) -> None:
    """Log the mascot's output line by line until its pipe closes, then close it.

    It reads to the very end: Electron writes to a pipe synchronously on Windows, so a
    pipe nobody empties would stall the mascot once it is full.
    """
    with stream:
        for line in stream:
            text = line.rstrip()
            if text:
                logger.log(_level_of(text), "%s", text)


def _level_of(line: str) -> int:
    if not line.startswith(MASCOT_LOG_PREFIX):
        return logging.DEBUG
    return logging.WARNING if _TROUBLE.search(line, len(MASCOT_LOG_PREFIX)) else logging.INFO


def _find_edge(environ: Mapping[str, str]) -> Path | None:
    for folder in _EDGE_FOLDERS:
        base = environ.get(folder)
        if base and (candidate := Path(base) / EDGE_EXE).is_file():
            return candidate
    return None


def _close_job(job: ProcessJob) -> None:
    try:
        job.close()
    except Exception as exc:  # cleanup must not stop Cyrene's shutdown
        logger.warning("Could not close the avatar window's job: %s", exc)


def _end(process: ServerProcess) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.warning("Avatar window (pid %s) ignored terminate; killing it", process.pid)
        process.kill()
    logger.info("Closed the avatar window (pid %s)", process.pid)
