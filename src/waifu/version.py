"""What code a `waifu run` runs, and what code is in the project folder now.

The owner keeps one `waifu run` open for days while newer versions land in the project folder.
A run reads its version when it starts (`read_code_version`) and records it in
`data/running.json` (`write_running`), so `waifu check` and `waifu version` can compare it with
the code on disk, and the version watcher can tell Master once a newer version is there.

A version is CHANGELOG.md's headings (`## v1.25: peringatan hujan`, newest first) and a hash of
pyproject.toml's core dependencies, the packages `pip install -e .` installs; the git commit, or
without git the newest source file's time, only details it. Reading never raises: a part that
cannot be read is left empty, with its reason in `problems`. Git is only asked `rev-parse HEAD`,
which never writes `.git`, so it never races a commit made in the same folder.

Whether a recorded run still runs comes from the run lock and `process_alive`, never from
removing the record on exit: closing the console window ends Python without running `finally`.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

# The project folder: the install is editable, so this is the code `waifu run` imported.
REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_COMMAND = r".venv\Scripts\python -m pip install -e ."
GIT_TIMEOUT_S = 5
FILE_VERSION = 1
CHANGELOG = "CHANGELOG.md"
PYPROJECT = "pyproject.toml"
SOURCE = Path("src") / "waifu"
_BYTECODE_CACHE = "__pycache__"  # Python rewrites it at every start: never a change of code
# CREATE_NO_WINDOW keeps a console from flashing up; 0 is the only value POSIX accepts.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HEADING = re.compile(r"## (v\d+(?:\.\d+)+): (.+)")
_VERSION = re.compile(r"v\d+(?:\.\d+)+")
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # SHA-1, or SHA-256 repositories
_MAX_PID = 2**32 - 1  # a DWORD
# processthreadsapi.h, winerror.h, minwinbase.h
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_STILL_ACTIVE = 259

# subprocess.run's signature, for git.
Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Release:
    """One CHANGELOG version: `v1.25` and its title, `peringatan hujan`."""

    version: str
    title: str

    @property
    def number(self) -> tuple[int, ...]:
        """The version's numbers, for ordering: `v1.22.1` is (1, 22, 1)."""
        return tuple(int(part) for part in self.version[1:].split("."))


@dataclass(frozen=True)
class CodeVersion:
    """The code in a project folder as read at one moment."""

    releases: tuple[Release, ...]  # newest first; empty when CHANGELOG.md could not be read
    dependencies: str | None  # the core dependencies' hash; None when pyproject.toml could not
    commit: str | None = None  # git's HEAD
    source_time: datetime | None = None  # without a commit: the newest source file's time
    problems: tuple[str, ...] = ()  # why a part could not be read, for the log

    @property
    def release(self) -> Release | None:
        """The newest release, or None."""
        return self.releases[0] if self.releases else None


@dataclass(frozen=True)
class RunningVersion:
    """What a `waifu run` started with, when, and as which process."""

    code: CodeVersion
    started: datetime
    pid: int


def parse_releases(text: str) -> tuple[Release, ...]:
    """Every `## vX.Y[.Z]: title` line of a CHANGELOG, in file order; ValueError when none."""
    releases = tuple(
        Release(match.group(1), match.group(2).strip())
        for line in text.removeprefix("﻿").splitlines()
        if (match := _HEADING.fullmatch(line.rstrip()))
    )
    if not releases:
        raise ValueError("no version heading (## v1.2: title)")
    return releases


def dependency_hash(text: str) -> str:
    """The SHA-256 of pyproject's `[project].dependencies`, stripped, sorted, one per line: the
    packages `pip install -e .` installs. ValueError when there is no such list of strings."""
    try:
        dependencies = tomllib.loads(text)["project"]["dependencies"]
    except (ValueError, KeyError, TypeError) as exc:  # TOMLDecodeError is a ValueError
        raise ValueError(f"no [project] dependencies ({exc})") from exc
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        raise ValueError("[project] dependencies is not a list of strings")
    lines = "\n".join(sorted(dependency.strip() for dependency in dependencies))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def newer_releases(running: CodeVersion, disk: CodeVersion) -> tuple[Release, ...]:
    """The releases on disk above the running newest release, newest first; none when either has
    no release. Numbers decide, so a CHANGELOG that went back is never newer."""
    if running.release is None:
        return ()
    newest = running.release.number
    return tuple(release for release in disk.releases if release.number > newest)


def dependencies_changed(running: CodeVersion, disk: CodeVersion) -> bool:
    """Whether the core dependencies on disk differ from the running ones; never when either
    hash is unknown."""
    return (
        running.dependencies is not None
        and disk.dependencies is not None
        and running.dependencies != disk.dependencies
    )


def read_code_version(root: Path = REPO_ROOT, *, run: Run | None = subprocess.run) -> CodeVersion:
    """The code in `root` now: its releases and dependency hash, and with `run` its commit, or
    the newest source file's time when git cannot tell. `run=None` reads neither. Never raises."""
    problems: list[str] = []
    try:
        releases = parse_releases((root / CHANGELOG).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:  # also bad UTF-8
        releases = ()
        problems.append(f"Could not read the releases in {CHANGELOG}: {exc}")
    try:
        dependencies: str | None = dependency_hash(
            (root / PYPROJECT).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError) as exc:
        dependencies = None
        problems.append(f"Could not read the dependencies in {PYPROJECT}: {exc}")
    commit = source_time = None
    if run is not None:
        try:
            commit = _git_commit(root, run)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            problems.append(f"Could not read the git commit ({exc}); using the source files' time")
            try:
                source_time = _newest_source_time(root / SOURCE)
            except (OSError, ValueError) as error:
                problems.append(f"Could not read the source files' time: {error}")
    return CodeVersion(releases, dependencies, commit, source_time, tuple(problems))


def _git_commit(root: Path, run: Run) -> str:
    """git's HEAD in `root`; OSError without git, SubprocessError on a timeout, ValueError when
    git refuses (no repository) or answers something else."""
    result = run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=GIT_TIMEOUT_S,
        creationflags=_CREATION_FLAGS,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"git exited with {result.returncode}")
    commit = result.stdout.strip()
    if not _COMMIT.fullmatch(commit):
        raise ValueError(f"git answered {commit!r}, not a commit")
    return commit


def _newest_source_time(source: Path) -> datetime:
    """The newest modification time of the files under `source`, the bytecode cache left out."""
    times = [
        path.stat().st_mtime
        for path in source.rglob("*")
        if path.is_file() and _BYTECODE_CACHE not in path.relative_to(source).parts
    ]
    if not times:
        raise ValueError(f"no source files under {source}")
    return datetime.fromtimestamp(max(times))


def write_running(path: Path, running: RunningVersion) -> None:
    """Record `running` at `path` atomically, with only its newest release; raises OSError."""
    code = running.code
    write_json_atomic(
        path,
        {
            "version": FILE_VERSION,
            "release": code.release.version if code.release else None,
            "title": code.release.title if code.release else None,
            "dependencies": code.dependencies,
            "commit": code.commit,
            "source_time": code.source_time.isoformat() if code.source_time else None,
            "started": running.started.isoformat(timespec="seconds"),
            "pid": running.pid,
        },
    )


def read_running(path: Path) -> RunningVersion | None:
    """The run recorded at `path`; None when there is none, or the file cannot be read or is not
    such a record (left as it is: the next start replaces it). Never creates the file."""
    try:
        # utf-8-sig: Notepad may save the file with a byte order mark.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:  # also bad JSON and bad UTF-8
        logger.info("Ignored the running record %s: %s", path, exc)
        return None
    try:
        return _parse_running(raw)
    except ValueError as exc:
        logger.info("Ignored %s, which is not a running record: %s", path, exc)
        return None


def _parse_running(raw: object) -> RunningVersion:
    """The record in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or _whole(raw.get("version")) != FILE_VERSION:
        raise ValueError(f"not a version {FILE_VERSION} record")
    release, title = raw.get("release"), raw.get("title")
    if release is None and title is None:
        releases: tuple[Release, ...] = ()
    elif isinstance(release, str) and _VERSION.fullmatch(release) and isinstance(title, str):
        releases = (Release(release, title),)
    else:
        raise ValueError(f"odd release {release!r} {title!r}")
    source_time = raw.get("source_time")
    pid = _whole(raw.get("pid"))
    if pid is None or not 0 < pid <= _MAX_PID:
        raise ValueError(f"odd process id {raw.get('pid')!r}")
    return RunningVersion(
        CodeVersion(
            releases,
            _optional_text(raw, "dependencies"),
            _optional_text(raw, "commit"),
            None if source_time is None else _moment(source_time),
        ),
        _moment(raw.get("started")),
        pid,
    )


def _whole(value: object) -> int | None:
    """`value` when it is an int (a JSON true is not), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"odd {key} {value!r}")
    return value


def _moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"odd time {value!r}")
    return datetime.fromisoformat(value)


def _kernel32() -> Any:
    """A private kernel32 with the signatures `process_alive` calls (processthreadsapi.h), so
    they never clash with other users; ctypes.WinDLL is looked up here, at every call, so the
    test suite's guards on it always apply."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def process_alive(pid: int, *, kernel32: Callable[[], Any] = _kernel32) -> bool:
    """Whether the process `pid` runs now. On Windows only a query handle is opened and closed
    again, never a signal (os.kill there ends the process); access denied means it exists.
    Elsewhere, or when kernel32 cannot load, True: the run lock decides alone."""
    if sys.platform != "win32":
        return True
    try:
        api = kernel32()
    except OSError as exc:
        logger.debug("Could not load kernel32 (%s); leaving it to the run lock", exc)
        return True
    handle = api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        read = api.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(read) and code.value == _STILL_ACTIVE
    finally:
        api.CloseHandle(handle)
