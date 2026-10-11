import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from waifu.single_instance import InstanceAlreadyRunning, InstanceLock

SRC = Path(__file__).resolve().parent.parent / "src"
CHILD_TIMEOUT_S = 30
# A venv python.exe on Windows is a launcher that runs the real interpreter as a child
# process; that child ends a moment after the launcher is killed, so the OS lock can
# outlive proc.wait() briefly.
KILLED_HOLDER_GRACE_S = 10

PROBE_ACQUIRED = 0
PROBE_REFUSED = 3
PROBE = f"""
import sys
from pathlib import Path
from waifu.single_instance import InstanceLock
lock = InstanceLock(Path(sys.argv[1]))
acquired = lock.acquire()
lock.release()
sys.exit({PROBE_ACQUIRED} if acquired else {PROBE_REFUSED})
"""

HOLDER = f"""
import os
import sys
from pathlib import Path
from waifu.single_instance import InstanceLock
if not InstanceLock(Path(sys.argv[1])).acquire():
    sys.exit({PROBE_REFUSED})
print(os.getpid(), flush=True)
sys.stdin.read()
"""


def child_env() -> dict[str, str]:
    paths = [str(SRC), os.environ.get("PYTHONPATH", "")]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(path for path in paths if path)}


def probe_from_another_process(path: Path) -> int:
    """Tries the lock from a fresh Python process; returns PROBE_ACQUIRED or PROBE_REFUSED."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(path)],
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_S,
    )
    assert result.returncode in (PROBE_ACQUIRED, PROBE_REFUSED), result.stderr
    return result.returncode


@contextmanager
def holder_process(path: Path) -> Iterator[tuple[subprocess.Popen[str], int]]:
    """A separate Python process that holds the lock until its stdin closes or it is killed."""
    with subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)],
        env=child_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline().strip()
            assert line.isdigit(), f"the holder process did not take the lock (exit {proc.poll()})"
            yield proc, int(line)
        finally:
            proc.kill()


def is_free(path: Path) -> bool:
    """Whether a fresh lock in this process can take the file right now (released again)."""
    probe = InstanceLock(path)
    acquired = probe.acquire()
    probe.release()
    return acquired


def acquires_within(lock: InstanceLock, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not lock.acquire():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


# --- same process ------------------------------------------------------------------------------


def test_a_second_lock_on_the_same_file_cannot_acquire_while_the_first_holds_it(tmp_path):
    path = tmp_path / "run.lock"
    first, second = InstanceLock(path), InstanceLock(path)

    assert first.acquire()
    try:
        assert not second.acquire()
        assert first.held
        assert not second.held
    finally:
        first.release()


def test_releasing_the_first_lock_lets_a_second_lock_acquire(tmp_path):
    path = tmp_path / "run.lock"
    first, second = InstanceLock(path), InstanceLock(path)
    assert first.acquire()

    first.release()

    assert not first.held
    assert second.acquire()
    second.release()


def test_acquiring_a_lock_this_object_already_holds_succeeds_and_one_release_frees_it(tmp_path):
    path = tmp_path / "run.lock"
    lock = InstanceLock(path)
    assert lock.acquire()

    assert lock.acquire()
    lock.release()

    assert is_free(path)


def test_release_is_harmless_when_never_acquired_or_repeated(tmp_path):
    path = tmp_path / "run.lock"
    lock = InstanceLock(path)

    lock.release()
    assert lock.acquire()
    lock.release()
    lock.release()

    assert not lock.held
    assert is_free(path)


def test_acquire_creates_the_missing_parent_folders(tmp_path):
    path = tmp_path / "data" / "nested" / "run.lock"
    lock = InstanceLock(path)

    assert lock.acquire()
    lock.release()

    assert path.parent.is_dir()


def test_release_unlocks_but_keeps_the_lock_file_with_the_holders_pid(tmp_path):
    path = tmp_path / "run.lock"
    lock = InstanceLock(path)
    assert lock.acquire()

    lock.release()

    assert path.read_text().strip() == str(os.getpid())


# --- context manager ---------------------------------------------------------------------------


def test_context_manager_holds_the_lock_inside_the_block_and_releases_it_after(tmp_path):
    path = tmp_path / "run.lock"

    with InstanceLock(path) as lock:
        assert lock.held
        assert not is_free(path)

    assert not lock.held
    assert is_free(path)


def test_context_manager_releases_the_lock_when_the_block_raises(tmp_path):
    path = tmp_path / "run.lock"

    with pytest.raises(ValueError), InstanceLock(path) as lock:
        raise ValueError("boom")

    assert not lock.held
    assert is_free(path)


def test_context_manager_raises_instance_already_running_when_the_lock_is_held_elsewhere(
    tmp_path,
):
    path = tmp_path / "run.lock"
    holder = InstanceLock(path)
    assert holder.acquire()
    entered = False

    try:
        with pytest.raises(InstanceAlreadyRunning) as error, InstanceLock(path):
            entered = True
    finally:
        holder.release()

    assert not entered
    assert isinstance(error.value, RuntimeError)
    assert str(path) in str(error.value)


# --- separate processes ------------------------------------------------------------------------


def test_another_process_cannot_acquire_while_this_process_holds_the_lock(tmp_path):
    path = tmp_path / "run.lock"
    lock = InstanceLock(path)
    assert lock.acquire()

    try:
        assert probe_from_another_process(path) == PROBE_REFUSED
    finally:
        lock.release()

    assert probe_from_another_process(path) == PROBE_ACQUIRED


def test_the_holders_pid_survives_a_refused_acquire_from_another_process(tmp_path):
    path = tmp_path / "run.lock"
    lock = InstanceLock(path)

    with holder_process(path) as (holder, holder_pid):
        assert not lock.acquire()
        holder.communicate(timeout=CHILD_TIMEOUT_S)

    assert path.read_text().strip() == str(holder_pid)
    assert lock.acquire()
    lock.release()


def test_a_killed_holder_process_leaves_no_stale_lock(tmp_path):
    path = tmp_path / "run.lock"
    lock = InstanceLock(path)

    with holder_process(path) as (holder, _):
        assert not lock.acquire()
        holder.kill()
        holder.wait(timeout=CHILD_TIMEOUT_S)

        assert acquires_within(lock, KILLED_HOLDER_GRACE_S)
    lock.release()
