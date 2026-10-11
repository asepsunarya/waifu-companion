"""Keeps a second Cyrene from starting while one is already running.

The guard is an OS lock on an open file handle, not a PID file: the OS drops the
lock when the holder exits, crashes, or is killed, so a dead process never leaves
a stale lock behind. The holder writes its PID into the file for humans; nothing
reads it back. The file itself is never deleted, because deleting it while
another process is opening it would race.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)

# O_BINARY keeps Windows from rewriting the PID's newline; it does not exist elsewhere.
_OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)

if sys.platform == "win32":
    import msvcrt

    _LOCKED_BYTES = 1

    # msvcrt.locking covers the bytes starting at the current file position, so lock
    # and unlock both seek back to byte 0 first. LK_NBLCK raises OSError at once
    # when another handle (in any process, this one included) holds the byte.
    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCKED_BYTES)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCKED_BYTES)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class InstanceAlreadyRunning(RuntimeError):
    """Another process (or another InstanceLock) holds the instance lock."""


class InstanceLock:
    """A non-blocking, exclusive lock on `path`, usable as a context manager."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        """Takes the lock without waiting; False when someone else holds it."""
        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # No O_TRUNC: a refused attempt must not wipe the holder's PID.
        fd = os.open(self._path, _OPEN_FLAGS)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            logger.info("Instance lock %s is already held elsewhere", self._path)
            return False
        self._fd = fd
        _write_pid(fd)
        logger.debug("Acquired instance lock %s", self._path)
        return True

    def release(self) -> None:
        """Unlocks and closes the file; harmless when not held."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)
        logger.debug("Released instance lock %s", self._path)

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise InstanceAlreadyRunning(f"another instance holds the lock {self._path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _write_pid(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
