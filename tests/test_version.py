"""Reading the code's version and the running record: no test runs git or signals a process.

Every project read here is a small folder under tmp_path with its own CHANGELOG.md,
pyproject.toml, and src/waifu; git is a fake `run`. Only the sanity tests at the end read this
repository's own CHANGELOG.md and pyproject.toml, never its .git.
"""

import ctypes
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from waifu import version
from waifu.version import (
    REPO_ROOT,
    CodeVersion,
    Release,
    RunningVersion,
    dependencies_changed,
    dependency_hash,
    newer_releases,
    parse_releases,
    process_alive,
    read_code_version,
    read_running,
    write_running,
)

ROOT = Path(__file__).resolve().parent.parent
CREATE_NO_WINDOW = 0x08000000  # process creation flags (winbase.h)
COMMIT = "ec4aadf" + "0" * 33
CHANGELOG = """# Yang baru di Cyrene

Ringkasan per versi, yang terbaru di atas.

## v1.25: peringatan hujan

- Sebelum hujan dia memperingatkan.

## v1.24: keuangan dari tepira

- Pertanyaan soal uang.

## v1.22.1: suara kembali

- Perbaikan.
"""
PYPROJECT = """[project]
name = "waifu-companion"
dependencies = [
    "httpx>=0.27",  # imported directly
    "anthropic>=1.13,<2",
]

[project.optional-dependencies]
voice = ["faster-whisper>=1.2"]
"""
CORE_HASH = hashlib.sha256(b"anthropic>=1.13,<2\nhttpx>=0.27").hexdigest()
V125 = Release("v1.25", "peringatan hujan")
V124 = Release("v1.24", "keuangan dari tepira")
V1221 = Release("v1.22.1", "suara kembali")


def releases(*numbers: str) -> tuple[Release, ...]:
    return tuple(Release(number, f"judul {number}") for number in numbers)


# --- the CHANGELOG -----------------------------------------------------------------------------


def test_the_releases_are_the_changelog_headings_newest_first():
    assert parse_releases(CHANGELOG) == (V125, V124, V1221)


def test_windows_line_endings_and_a_byte_order_mark_read_the_same():
    assert parse_releases(CHANGELOG.replace("\n", "\r\n")) == (V125, V124, V1221)
    assert parse_releases("﻿## v1.5: lima\r\n## v1.4: empat\r\n") == (
        Release("v1.5", "lima"),
        Release("v1.4", "empat"),
    )


@pytest.mark.parametrize(
    "line",
    ["### v1.2: bagian", "## Catatan", "## v1: tanpa titik", "  ## v1.3: menjorok", "x ## v1.4: y"],
)
def test_only_a_version_heading_is_a_release(line):
    assert parse_releases(f"{line}\n## v1.5: lima\n") == (Release("v1.5", "lima"),)


def test_a_changelog_without_a_release_is_refused():
    with pytest.raises(ValueError, match="no version heading"):
        parse_releases("# Yang baru di Cyrene\n\nBelum ada.\n")


def test_a_release_number_counts_every_part():
    assert V1221.number == (1, 22, 1)
    assert Release("v1.9", "x").number < Release("v1.10", "y").number < V1221.number


# --- the dependencies --------------------------------------------------------------------------


def test_the_dependency_hash_is_the_sorted_core_dependencies_one_per_line():
    assert dependency_hash(PYPROJECT) == CORE_HASH


def test_order_spacing_and_comments_never_change_the_dependency_hash():
    reordered = PYPROJECT.replace(
        '    "httpx>=0.27",  # imported directly\n    "anthropic>=1.13,<2",\n',
        '  " anthropic>=1.13,<2 ",\n  "httpx>=0.27",  # another comment\n',
    )

    assert dependency_hash(reordered) == CORE_HASH


def test_a_changed_dependency_changes_the_hash():
    assert dependency_hash(PYPROJECT.replace("httpx>=0.27", "httpx>=0.28")) != CORE_HASH


def test_the_optional_extras_never_change_the_hash():
    # pip install -e . installs the core only; the voice extra needs the CUDA index.
    changed = PYPROJECT.replace("faster-whisper>=1.2", "faster-whisper>=1.3")

    assert dependency_hash(changed) == CORE_HASH


@pytest.mark.parametrize(
    "text",
    [
        "[project\nname = 1\n",
        '[project]\nname = "x"\n',
        "[tool.ruff]\nline-length = 100\n",
        "[project]\ndependencies = [1, 2]\n",
        '[project]\ndependencies = "httpx"\n',
    ],
    ids=["not_toml", "no_dependencies", "no_project", "not_strings", "not_a_list"],
)
def test_a_pyproject_without_a_dependency_list_is_refused(text):
    with pytest.raises(ValueError):
        dependency_hash(text)


# --- newer releases ----------------------------------------------------------------------------


def code(*numbers: str, dependencies: str | None = CORE_HASH) -> CodeVersion:
    return CodeVersion(releases(*numbers), dependencies)


def test_the_newer_releases_are_those_above_the_running_one_newest_first():
    running = code("v1.25", "v1.24")
    disk = code("v1.27", "v1.26.1", "v1.26", "v1.25", "v1.24")

    assert newer_releases(running, disk) == releases("v1.27", "v1.26.1", "v1.26")


def test_the_same_newest_release_is_nothing_newer():
    assert newer_releases(code("v1.25"), code("v1.25", "v1.24")) == ()


def test_a_changelog_that_went_back_is_nothing_newer():
    assert newer_releases(code("v1.26", "v1.25"), code("v1.25", "v1.24")) == ()


def test_a_running_release_missing_from_the_disk_s_changelog_still_counts_by_its_number():
    # v1.11 never had a heading: everything above it on disk is newer.
    assert newer_releases(code("v1.11"), code("v1.13", "v1.12.1", "v1.12")) == releases(
        "v1.13", "v1.12.1", "v1.12"
    )


@pytest.mark.parametrize(
    ("running", "disk"), [(code(), code("v1.26")), (code("v1.25"), code())], ids=["running", "disk"]
)
def test_nothing_is_newer_when_either_side_has_no_release(running, disk):
    assert newer_releases(running, disk) == ()


# --- changed dependencies ----------------------------------------------------------------------

OTHER_HASH = "b" * 64


def test_the_dependencies_changed_when_both_hashes_are_known_and_differ():
    assert dependencies_changed(code("v1.25"), code("v1.25", dependencies=OTHER_HASH)) is True


def test_the_same_dependency_hash_is_no_change():
    assert dependencies_changed(code("v1.25"), code("v1.26", "v1.25")) is False


@pytest.mark.parametrize(
    ("running", "disk"),
    [(code("v1.25", dependencies=None), code("v1.25")), (code("v1.25"), code(dependencies=None))],
    ids=["running", "disk"],
)
def test_an_unknown_dependency_hash_is_never_a_change(running, disk):
    assert dependencies_changed(running, disk) is False


# --- reading a project folder ------------------------------------------------------------------


class FakeRun:
    """Stands in for subprocess.run for git: answers with `stdout` and `returncode`, or raises
    `error`, and remembers each call."""

    def __init__(
        self, stdout: str = COMMIT + "\n", returncode: int = 0, error: Exception | None = None
    ) -> None:
        self.stdout, self.returncode, self.error = stdout, returncode, error
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        stderr = "" if self.returncode == 0 else "fatal: not a git repository"
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr=stderr)


SOURCE_TIME = datetime(2026, 10, 17, 9, 12, 30)


def project(root: Path) -> Path:
    """A project folder: CHANGELOG.md, pyproject.toml, and two source files, the newer one at
    SOURCE_TIME; Python's bytecode cache is newer still."""
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    source = root / "src" / "waifu"
    (source / "avatar" / "web").mkdir(parents=True)
    (source / "__pycache__").mkdir()
    files = {
        source / "cli.py": SOURCE_TIME.timestamp() - 3600,
        source / "avatar" / "web" / "index.html": SOURCE_TIME.timestamp(),
        source / "__pycache__" / "cli.cpython-314.pyc": SOURCE_TIME.timestamp() + 600,
    }
    for path, mtime in files.items():
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))
    return root


def test_the_code_version_is_its_releases_dependencies_and_commit(tmp_path):
    run = FakeRun()

    found = read_code_version(project(tmp_path), run=run)

    assert found == CodeVersion((V125, V124, V1221), CORE_HASH, COMMIT)
    assert found.release == V125


def test_git_is_only_asked_for_the_commit_hidden_and_with_a_timeout(tmp_path):
    run = FakeRun()

    read_code_version(project(tmp_path), run=run)

    ((args, kwargs),) = run.calls
    # rev-parse never writes .git, so it never races a commit made in the same folder.
    assert args == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]
    assert kwargs["timeout"] == 5
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_a_sha256_repository_s_commit_is_read_too(tmp_path):
    commit = "a" * 64

    assert read_code_version(project(tmp_path), run=FakeRun(commit + "\n")).commit == commit


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        (FakeRun(error=FileNotFoundError("git")), "git"),
        (FakeRun(returncode=128), "not a git repository"),
        (FakeRun(error=subprocess.TimeoutExpired(["git"], 5)), "timed out"),
        (FakeRun("HEAD\n"), "HEAD"),
    ],
    ids=["no_git", "not_a_repository", "timeout", "odd_output"],
)
def test_without_a_commit_the_newest_source_file_s_time_stands_in(tmp_path, run, reason):
    found = read_code_version(project(tmp_path), run=run)

    assert (found.commit, found.source_time) == (None, SOURCE_TIME)  # the bytecode cache left out
    assert (found.releases, found.dependencies) == ((V125, V124, V1221), CORE_HASH)
    [problem] = found.problems
    assert reason in problem


def test_without_run_neither_git_nor_the_source_files_are_read(tmp_path):
    found = read_code_version(project(tmp_path), run=None)

    assert found == CodeVersion((V125, V124, V1221), CORE_HASH)


@pytest.mark.parametrize(
    ("write", "named"),
    [
        (lambda root: (root / "CHANGELOG.md").unlink(), "CHANGELOG.md"),
        (lambda root: (root / "CHANGELOG.md").write_text("# Kosong\n"), "CHANGELOG.md"),
        (lambda root: (root / "CHANGELOG.md").write_bytes(b"## v1.2: \xff\n"), "CHANGELOG.md"),
    ],
    ids=["missing", "no_heading", "not_utf8"],
)
def test_a_changelog_that_cannot_be_read_leaves_no_releases_and_says_why(tmp_path, write, named):
    root = project(tmp_path)
    write(root)

    found = read_code_version(root, run=FakeRun())

    assert (found.releases, found.release) == ((), None)
    assert (found.dependencies, found.commit) == (CORE_HASH, COMMIT)
    [problem] = found.problems
    assert named in problem


@pytest.mark.parametrize(
    "write",
    [
        lambda root: (root / "pyproject.toml").unlink(),
        lambda root: (root / "pyproject.toml").write_text("[project\n"),
    ],
    ids=["missing", "not_toml"],
)
def test_a_pyproject_that_cannot_be_read_leaves_no_dependency_hash_and_says_why(tmp_path, write):
    root = project(tmp_path)
    write(root)

    found = read_code_version(root, run=FakeRun())

    assert found.dependencies is None
    assert found.releases == (V125, V124, V1221)
    [problem] = found.problems
    assert "pyproject.toml" in problem


def test_an_empty_folder_is_read_without_raising(tmp_path):
    found = read_code_version(tmp_path, run=FakeRun(error=FileNotFoundError("git")))

    assert (found.releases, found.dependencies, found.commit, found.source_time) == (
        (),
        None,
        None,
        None,
    )
    assert len(found.problems) == 4  # the changelog, pyproject, git, and the source files


# --- the running record ------------------------------------------------------------------------

STARTED = datetime(2026, 10, 13, 8, 15, 4)


def running(**changes) -> RunningVersion:
    code = CodeVersion((V125, V124), CORE_HASH, COMMIT, None, ("a problem",))
    return RunningVersion(changes.pop("code", code), changes.pop("started", STARTED), 4321)


def test_the_running_record_keeps_the_newest_release_hash_commit_start_and_process(tmp_path):
    path = tmp_path / "data" / "running.json"

    write_running(path, running())

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "release": "v1.25",
        "title": "peringatan hujan",
        "dependencies": CORE_HASH,
        "commit": COMMIT,
        "source_time": None,
        "started": "2026-10-13T08:15:04",
        "pid": 4321,
    }
    assert read_running(path) == RunningVersion(
        CodeVersion((V125,), CORE_HASH, COMMIT), STARTED, 4321
    )


@pytest.mark.parametrize(
    "code",
    [
        CodeVersion((V125,), CORE_HASH, None, SOURCE_TIME),
        CodeVersion((), None),
    ],
    ids=["source_time", "nothing_known"],
)
def test_a_running_record_round_trips_whatever_was_known(tmp_path, code):
    path = tmp_path / "running.json"

    write_running(path, running(code=code))

    assert read_running(path) == running(code=code)


def test_writing_replaces_the_last_record_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "data" / "running.json"
    write_running(path, running())

    write_running(path, running(started=datetime(2026, 10, 17, 7, 0)))

    assert read_running(path).started == datetime(2026, 10, 17, 7, 0)
    assert [child.name for child in path.parent.iterdir()] == ["running.json"]


def test_a_failed_write_keeps_the_last_record(tmp_path, monkeypatch):
    path = tmp_path / "running.json"
    write_running(path, running())

    def refuse(source, target):
        raise PermissionError("another process has it open")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(PermissionError):
        write_running(path, running(started=datetime(2026, 10, 17, 7, 0)))

    assert read_running(path).started == STARTED
    assert [child.name for child in tmp_path.iterdir()] == ["running.json"]


def test_reading_a_missing_record_is_none_and_creates_nothing(tmp_path):
    path = tmp_path / "data" / "running.json"

    assert read_running(path) is None
    assert not path.parent.exists()


def test_a_record_saved_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "running.json"
    write_running(path, running())
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    assert read_running(path).pid == 4321


def good_record() -> dict:
    return {
        "version": 1,
        "release": "v1.25",
        "title": "peringatan hujan",
        "dependencies": CORE_HASH,
        "commit": None,
        "source_time": None,
        "started": "2026-10-13T08:15:04",
        "pid": 4321,
    }


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        json.dumps({**good_record(), "version": 2}),
        json.dumps({key: value for key, value in good_record().items() if key != "pid"}),
        json.dumps({**good_record(), "pid": True}),
        json.dumps({**good_record(), "pid": 0}),
        json.dumps({**good_record(), "pid": "4321"}),
        json.dumps({**good_record(), "started": "kemarin"}),
        json.dumps({**good_record(), "source_time": 12}),
        json.dumps({**good_record(), "release": "v1.25", "title": None}),
        json.dumps({**good_record(), "release": "1.25"}),
        json.dumps({**good_record(), "commit": 7}),
    ],
    ids=[
        "not_json",
        "a_list",
        "a_later_format",
        "no_pid",
        "boolean_pid",
        "zero_pid",
        "text_pid",
        "odd_start",
        "odd_source_time",
        "release_without_title",
        "odd_release",
        "odd_commit",
    ],
)
def test_a_file_that_is_no_running_record_is_ignored_and_left_as_it_is(tmp_path, caplog, content):
    path = tmp_path / "running.json"
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="waifu.version"):
        assert read_running(path) is None

    assert path.read_text(encoding="utf-8") == content  # the next start replaces it
    assert [child.name for child in tmp_path.iterdir()] == ["running.json"]
    assert [record.levelno for record in caplog.records] == [logging.INFO]


def test_an_unreadable_record_is_ignored(tmp_path):
    path = tmp_path / "running.json"
    path.write_bytes(b'{"version": 1, "title": "\xff"}')

    assert read_running(path) is None


# --- whether a process runs --------------------------------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="asks Windows")


@windows_only
def test_this_process_is_alive():
    assert process_alive(os.getpid()) is True


@windows_only
def test_a_process_that_ended_is_not_alive():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()  # its handle stays open here, so the process is still there, but ended

    assert process_alive(child.pid) is False


class FakeKernel32:
    """kernel32 for process_alive: OpenProcess fails with `error`, or opens a process whose exit
    code is `exit_code`; remembers the handles closed."""

    def __init__(self, *, error: int | None = None, exit_code: int = 259) -> None:
        self.error, self.exit_code = error, exit_code
        self.opened: list[tuple[int, bool, int]] = []
        self.closed: list[int] = []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - kernel32's name
        self.opened.append((access, inherit, pid))
        if self.error is not None:
            ctypes.set_last_error(self.error)
            return None
        return 1234

    def GetExitCodeProcess(self, handle, code):  # noqa: N802 - kernel32's name
        code._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):  # noqa: N802 - kernel32's name
        self.closed.append(handle)
        return 1


@windows_only
@pytest.mark.parametrize(
    ("kernel32", "alive"),
    [
        (FakeKernel32(exit_code=259), True),
        (FakeKernel32(exit_code=0), False),
        (FakeKernel32(error=87), False),  # no such process
        (FakeKernel32(error=5), True),  # access denied: it exists
    ],
    ids=["still_active", "exited", "gone", "access_denied"],
)
def test_a_process_is_alive_while_its_exit_code_is_still_active(kernel32, alive):
    assert process_alive(4321, kernel32=lambda: kernel32) is alive

    # Only a query handle, closed again: nothing that could end the process.
    assert kernel32.opened == [(0x1000, False, 4321)]
    assert kernel32.closed == ([] if kernel32.error is not None else [1234])


@windows_only
def test_a_kernel32_that_cannot_load_leaves_it_to_the_lock():
    def broken():
        raise OSError("kernel32 could not be loaded")

    assert process_alive(4321, kernel32=broken) is True


def test_elsewhere_the_lock_alone_decides(monkeypatch):
    monkeypatch.setattr(version.sys, "platform", "linux")

    assert process_alive(4321, kernel32=lambda: pytest.fail("kernel32 on Linux")) is True


# --- this repository ---------------------------------------------------------------------------


def test_the_project_folder_is_this_repository_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert REPO_ROOT == ROOT


def test_this_repository_s_changelog_and_pyproject_can_be_read():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8-sig")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert parse_releases(changelog)[0].number >= (1, 25)
    assert len(dependency_hash(pyproject)) == 64
