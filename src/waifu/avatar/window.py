"""The window that shows Cyrene's avatar page: the Electron mascot, an Edge app window, or none.

`AvatarWindow` starts that program as a child of this process and puts it in a
kill-on-close job, so the window disappears with Cyrene however she ends; `close()`
ends only the window it started.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from waifu.avatar.server import AvatarError
from waifu.config import AvatarConfig
from waifu.voice.rvc import KillOnCloseJob, PopenFactory, ProcessJob, ServerProcess

logger = logging.getLogger(__name__)

STOP_TIMEOUT_S = 5.0
# src/waifu/avatar/window.py -> repository root; the package is installed editable from src/.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ELECTRON_EXE = Path("node_modules") / "electron" / "dist" / "electron.exe"
EDGE_EXE = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
# Edge's own profile for the avatar: with it the app window is a separate Edge process,
# which Cyrene can close without touching the owner's browser.
EDGE_PROFILE = Path("data") / "avatar-edge-profile"
_EDGE_FOLDERS = ("ProgramFiles(x86)", "ProgramFiles")


class AvatarWindow:
    """Opens the avatar page per `avatar.window` and closes what it opened.

    `mascot` runs the Electron shell in `avatar.electron_dir`, `app` runs Edge in app
    mode, `none` opens nothing (the page is only served). On Windows the child goes in a
    `KillOnCloseJob`: closing Cyrene's console ends Python without running `finally`.
    """

    def __init__(
        self,
        config: AvatarConfig,
        url: str,
        *,
        popen: PopenFactory | None = None,
        edge_path: Path | None = None,
        job_factory: Callable[[], ProcessJob] | None = None,
        project_root: Path = PROJECT_ROOT,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._config = config
        self._url = url
        # Looked up at construction, not bound as a default, so tests/conftest.py's
        # guarded Popen is the one used if a test forgets to inject a fake.
        self._popen = popen or subprocess.Popen
        self._edge_path = Path(edge_path) if edge_path is not None else None
        self._job_factory = job_factory or (KillOnCloseJob if sys.platform == "win32" else None)
        self._project_root = Path(project_root)
        self._environ = os.environ if environ is None else environ
        self._process: ServerProcess | None = None
        self._job: ProcessJob | None = None

    def program(self) -> Path | None:
        """The program `open()` runs, which may be missing; None for `none` or without Edge."""
        if self._config.window == "mascot":
            return self._electron_dir() / ELECTRON_EXE
        if self._config.window == "app":
            return self._edge_path or _find_edge(self._environ)
        return None

    def open(self) -> None:
        """Open the window unless it is already up; AvatarError (Indonesian) when it cannot."""
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        command = self._command()
        if command is None:
            logger.info("avatar.window is none: the avatar page is only served at %s", self._url)
            return
        try:
            process = self._popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise AvatarError(f"Jendela avatar tidak bisa dibuka: {exc}") from exc
        self._process = process
        logger.info("Opened the avatar %s window (pid %s)", self._config.window, process.pid)
        self._job = self._tie_to_this_process(process)

    def close(self) -> None:
        """End the window this object started, if any; safe to repeat, never raises."""
        process, self._process = self._process, None
        job, self._job = self._job, None
        if process is not None:
            try:
                _end(process)
            except Exception as exc:  # cleanup must not stop Cyrene's shutdown
                logger.warning("Could not close the avatar window (pid %s): %s", process.pid, exc)
        if job is not None:
            try:
                job.close()
            except Exception as exc:
                logger.warning("Could not close the avatar window's job: %s", exc)

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


def _find_edge(environ: Mapping[str, str]) -> Path | None:
    for folder in _EDGE_FOLDERS:
        base = environ.get(folder)
        if base and (candidate := Path(base) / EDGE_EXE).is_file():
            return candidate
    return None


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
