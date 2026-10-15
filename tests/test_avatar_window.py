import inspect
import io
import logging
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from waifu.avatar import window
from waifu.avatar.server import AvatarError
from waifu.avatar.window import AvatarWindow
from waifu.config import AvatarConfig

# Never a real window: every test injects FakePopen (tests/conftest.py also refuses
# spawning electron.exe and msedge.exe), and the "programs" are empty files under tmp_path.

URL = "http://127.0.0.1:9882/"
ELECTRON_EXE = Path("node_modules") / "electron" / "dist" / "electron.exe"
EDGE_EXE = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
OUTPUT = "avatar-mascot-output"
SUPERVISOR = "avatar-mascot-supervisor"
# How long a test waits for a real thread before it fails instead of hanging.
THREAD_TIMEOUT_S = 5.0


class FakeProcess:
    """The parts of subprocess.Popen the window uses, recording what it is asked to do.

    `exit_code` set means the process has already ended with it, so `wait()` returns
    it at once: a non-zero one is a crashed mascot, 0 one the owner closed.
    """

    def __init__(
        self,
        *,
        exit_code: int | None = None,
        ignores_terminate: bool = False,
        terminate_error: OSError | None = None,
        output: str = "",
    ) -> None:
        self.pid = 5151
        self.returncode = exit_code
        self.ignores_terminate = ignores_terminate
        self.terminate_error = terminate_error
        self.stdout = io.StringIO(output)
        self.calls: list[Any] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.terminate_error is not None:
            raise self.terminate_error
        if not self.ignores_terminate:
            self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("electron", timeout or 0)
        return self.returncode

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = -9


class BlockingProcess(FakeProcess):
    """A running mascot whose `wait()` blocks, like Popen's, until the process ends."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()
        self._ended = threading.Event()

    def terminate(self) -> None:
        self.calls.append("terminate")
        self.end(1)

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append(("wait", timeout))
        if timeout is None:
            self.waiting.set()
        if not self._ended.wait(THREAD_TIMEOUT_S if timeout is None else timeout):
            raise subprocess.TimeoutExpired("electron", timeout or THREAD_TIMEOUT_S)
        assert self.returncode is not None
        return self.returncode

    def end(self, exit_code: int) -> None:
        self.returncode = exit_code
        self._ended.set()


class FakePopen:
    """Records each spawn and hands out the next scripted process, or raises a scripted error."""

    def __init__(self, *processes: FakeProcess | OSError, error: OSError | None = None) -> None:
        self.processes = list(processes) or [FakeProcess()]
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        outcome = self.processes[len(self.calls) - 1]
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    @property
    def process(self) -> FakeProcess:
        return self.processes[0]

    @property
    def argv(self) -> list[str]:
        [(args, _)] = self.calls
        return args


class FakeJob:
    """Stands in for KillOnCloseJob: records the processes put in it and whether it closed."""

    def __init__(self, error: OSError | None, close_error: OSError | None) -> None:
        self.error = error
        self.close_error = close_error
        self.processes: list[Any] = []
        self.closed = False

    def add(self, process: Any) -> None:
        if self.error is not None:
            raise self.error
        self.processes.append(process)

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeJobs:
    """The job factory: every job it made, and the OSErrors they raise."""

    def __init__(
        self,
        *,
        error: OSError | None = None,
        close_error: OSError | None = None,
        factory_error: OSError | None = None,
    ) -> None:
        self.error = error
        self.close_error = close_error
        self.factory_error = factory_error
        self.made: list[FakeJob] = []

    def __call__(self) -> FakeJob:
        if self.factory_error is not None:
            raise self.factory_error
        self.made.append(FakeJob(self.error, self.close_error))
        return self.made[-1]


class ManualThread:
    """A thread that only records it was started; the test runs its target by hand."""

    def __init__(self, target: Callable[[], None], name: str) -> None:
        self.target = target
        self.name = name
        self.started = False
        self.ran = False

    def start(self) -> None:
        self.started = True


class ManualThreads:
    """The thread factory: keeps every thread, so a test decides when (and whether) each runs."""

    def __init__(self) -> None:
        self.made: list[ManualThread] = []

    def __call__(self, target: Callable[[], None], name: str) -> ManualThread:
        self.made.append(ManualThread(target, name))
        return self.made[-1]

    def started(self, name: str) -> int:
        return sum(1 for thread in self.made if thread.name == name and thread.started)

    def run(self, name: str) -> None:
        """Run the oldest started thread called `name` that has not run yet, on this thread."""
        thread = next(t for t in self.made if t.name == name and t.started and not t.ran)
        thread.ran = True
        thread.target()


class RealThreads:
    """The thread factory for race tests: real daemon threads, kept so the test can join them."""

    def __init__(self) -> None:
        self.made: list[threading.Thread] = []

    def __call__(self, target: Callable[[], None], name: str) -> threading.Thread:
        self.made.append(threading.Thread(target=target, name=name, daemon=True))
        return self.made[-1]

    def join_all(self) -> None:
        for thread in list(self.made):
            thread.join(THREAD_TIMEOUT_S)
            assert not thread.is_alive(), f"{thread.name} is still running"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSleep:
    """Records each pause; `during` runs inside it, as if it happened while the thread slept."""

    def __init__(self, during: Callable[[], None] | None = None) -> None:
        self.pauses: list[float] = []
        self.during = during

    def __call__(self, seconds: float) -> None:
        self.pauses.append(seconds)
        if self.during is not None:
            self.during()


def make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root whose avatar-app has Electron installed."""
    root = tmp_path / "repo"
    make_file(root / "avatar-app" / ELECTRON_EXE)
    return root


def make_window(
    project: Path,
    *,
    popen: FakePopen | None = None,
    jobs: FakeJobs | None = None,
    edge_path: Path | None = None,
    environ: dict[str, str] | None = None,
    threads: ManualThreads | RealThreads | None = None,
    clock: FakeClock | None = None,
    sleep: FakeSleep | None = None,
    **settings: Any,
) -> AvatarWindow:
    return AvatarWindow(
        AvatarConfig(**settings),
        URL,
        popen=popen or FakePopen(),
        edge_path=edge_path,
        job_factory=jobs or FakeJobs(),
        project_root=project,
        environ=environ if environ is not None else {},
        clock=clock or FakeClock(),
        sleep=sleep or FakeSleep(),
        thread_factory=threads or ManualThreads(),
    )


# --- mascot (Electron) --------------------------------------------------------------------


def test_the_mascot_runs_electron_on_the_app_folder_with_the_page_options(project):
    popen = FakePopen()

    make_window(project, popen=popen, window="mascot").open()

    app_dir = project / "avatar-app"
    assert popen.argv == [
        str(app_dir / ELECTRON_EXE),
        str(app_dir),
        "--url=http://127.0.0.1:9882/?mode=mascot",
        "--width=360",
        "--height=640",
        "--corner=bottom-right",
        "--always-on-top=1",
    ]


def test_the_mascot_passes_the_configured_size_corner_and_always_on_top(project):
    popen = FakePopen()

    make_window(
        project, popen=popen, width=420, height=720, corner="top-left", always_on_top=False
    ).open()

    assert popen.argv[2:] == [
        "--url=http://127.0.0.1:9882/?mode=mascot",
        "--width=420",
        "--height=720",
        "--corner=top-left",
        "--always-on-top=0",
    ]


def test_a_relative_electron_dir_is_found_under_the_project_root(project):
    make_file(project / "tools" / "mascot" / ELECTRON_EXE)
    popen = FakePopen()

    make_window(project, popen=popen, electron_dir=Path("tools/mascot")).open()

    assert popen.argv[:2] == [
        str(project / "tools" / "mascot" / ELECTRON_EXE),
        str(project / "tools" / "mascot"),
    ]


def test_an_absolute_electron_dir_is_used_as_it_is(project, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "avatar-app"
    make_file(elsewhere / ELECTRON_EXE)
    popen = FakePopen()

    make_window(project, popen=popen, electron_dir=elsewhere).open()

    assert popen.argv[:2] == [str(elsewhere / ELECTRON_EXE), str(elsewhere)]


def test_a_missing_electron_tells_the_owner_how_to_install_it(tmp_path):
    popen = FakePopen()
    jobs = FakeJobs()
    avatar_window = make_window(tmp_path / "repo", popen=popen, jobs=jobs)

    with pytest.raises(AvatarError) as raised:
        avatar_window.open()

    message = str(raised.value)
    assert str(tmp_path / "repo" / "avatar-app") in message
    assert "npm install" in message
    assert "node node_modules/electron/install.js" in message
    assert popen.calls == []
    assert jobs.made == []


def test_the_mascot_output_is_piped_as_utf8_text_and_no_console_flags_are_passed(project):
    popen = FakePopen()

    make_window(project, popen=popen).open()

    [(_, kwargs)] = popen.calls
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }


# --- app (Edge) ----------------------------------------------------------------------------


def test_the_app_window_runs_edge_with_its_own_profile(project, tmp_path):
    edge = make_file(tmp_path / "Edge" / "msedge.exe")
    popen = FakePopen()

    make_window(project, popen=popen, edge_path=edge, window="app", width=400, height=700).open()

    assert popen.argv == [
        str(edge),
        "--app=http://127.0.0.1:9882/?mode=app",
        "--window-size=400,700",
        f"--user-data-dir={project / 'data' / 'avatar-edge-profile'}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def test_the_app_window_output_is_discarded(project, tmp_path):
    edge = make_file(tmp_path / "Edge" / "msedge.exe")
    popen = FakePopen()

    make_window(project, popen=popen, edge_path=edge, window="app").open()

    [(_, kwargs)] = popen.calls
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


def test_the_app_window_is_neither_read_nor_reopened(project, tmp_path):
    # Edge's app window is the owner's to close: no output reader, no supervision.
    edge = make_file(tmp_path / "Edge" / "msedge.exe")
    threads = ManualThreads()

    make_window(project, edge_path=edge, threads=threads, window="app").open()

    assert threads.made == []


def test_edge_is_looked_up_in_program_files_x86_first(project, tmp_path):
    x86 = make_file(tmp_path / "Program Files (x86)" / EDGE_EXE)
    make_file(tmp_path / "Program Files" / EDGE_EXE)
    environ = {
        "ProgramFiles(x86)": str(tmp_path / "Program Files (x86)"),
        "ProgramFiles": str(tmp_path / "Program Files"),
    }
    popen = FakePopen()

    make_window(project, popen=popen, environ=environ, window="app").open()

    assert popen.argv[0] == str(x86)


def test_edge_is_looked_up_in_program_files_when_not_in_x86(project, tmp_path):
    native = make_file(tmp_path / "Program Files" / EDGE_EXE)
    environ = {
        "ProgramFiles(x86)": str(tmp_path / "Program Files (x86)"),
        "ProgramFiles": str(tmp_path / "Program Files"),
    }
    popen = FakePopen()

    make_window(project, popen=popen, environ=environ, window="app").open()

    assert popen.argv[0] == str(native)


@pytest.mark.parametrize("environ_key", ["ProgramFiles(x86)", None])
def test_a_missing_edge_tells_the_owner_how_to_install_it(project, tmp_path, environ_key):
    environ = {environ_key: str(tmp_path / "empty")} if environ_key else {}
    popen = FakePopen()
    avatar_window = make_window(project, popen=popen, environ=environ, window="app")

    with pytest.raises(AvatarError) as raised:
        avatar_window.open()

    assert "winget install --id Microsoft.Edge" in str(raised.value)
    assert "avatar.window: mascot" in str(raised.value)
    assert popen.calls == []


def test_a_configured_edge_path_that_does_not_exist_is_refused(project, tmp_path):
    missing = tmp_path / "nowhere" / "msedge.exe"
    popen = FakePopen()
    avatar_window = make_window(project, popen=popen, edge_path=missing, window="app")

    with pytest.raises(AvatarError, match="winget install --id Microsoft.Edge") as raised:
        avatar_window.open()

    assert str(missing) in str(raised.value)
    assert popen.calls == []


# --- none ----------------------------------------------------------------------------------


def test_window_none_starts_nothing(tmp_path):
    popen = FakePopen()
    jobs = FakeJobs()

    with make_window(tmp_path / "no-electron-here", popen=popen, jobs=jobs, window="none"):
        pass

    assert popen.calls == []
    assert jobs.made == []


# --- program -------------------------------------------------------------------------------


def test_program_names_what_open_would_run_without_starting_it(project, tmp_path):
    popen = FakePopen()
    edge = make_file(tmp_path / "Program Files" / EDGE_EXE)
    environ = {"ProgramFiles": str(tmp_path / "Program Files")}

    mascot = make_window(project, popen=popen).program()
    app = make_window(project, popen=popen, environ=environ, window="app").program()
    no_edge = make_window(project, popen=popen, window="app").program()
    none = make_window(project, popen=popen, window="none").program()

    assert (mascot, app, no_edge, none) == (project / "avatar-app" / ELECTRON_EXE, edge, None, None)
    assert popen.calls == []


# --- the kill-on-close job -----------------------------------------------------------------


def test_the_window_is_tied_to_this_process_by_a_kill_on_close_job(project):
    # Closing Cyrene's console ends her without running `finally`; the job then ends the window.
    popen = FakePopen()
    jobs = FakeJobs()

    make_window(project, popen=popen, jobs=jobs).open()

    [job] = jobs.made
    assert job.processes == [popen.process]
    assert not job.closed


def test_a_window_that_cannot_join_a_job_still_opens_with_a_warning(project, caplog):
    popen = FakePopen()
    jobs = FakeJobs(error=PermissionError(5, "Access is denied"))
    avatar_window = make_window(project, popen=popen, jobs=jobs)

    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        avatar_window.open()

    assert "Access is denied" in caplog.text
    assert jobs.made[0].closed
    avatar_window.close()
    assert popen.process.calls == ["terminate", ("wait", 5.0)]


def test_a_job_that_cannot_be_made_is_only_logged(project, caplog):
    popen = FakePopen()
    jobs = FakeJobs(factory_error=OSError(87, "The parameter is incorrect"))
    avatar_window = make_window(project, popen=popen, jobs=jobs)

    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        avatar_window.open()

    assert "The parameter is incorrect" in caplog.text
    assert len(popen.calls) == 1


def test_a_window_that_cannot_start_raises_an_avatar_error_and_makes_no_job(project):
    popen = FakePopen(error=FileNotFoundError(2, "The system cannot find the file specified"))
    jobs = FakeJobs()
    avatar_window = make_window(project, popen=popen, jobs=jobs)

    with pytest.raises(AvatarError, match="cannot find the file"):
        avatar_window.open()

    assert jobs.made == []


# --- close ---------------------------------------------------------------------------------


def test_close_terminates_the_window_and_closes_its_job(project):
    popen = FakePopen()
    jobs = FakeJobs()
    avatar_window = make_window(project, popen=popen, jobs=jobs)
    avatar_window.open()

    avatar_window.close()

    assert popen.process.calls == ["terminate", ("wait", 5.0)]
    assert jobs.made[0].closed


def test_close_kills_a_window_that_ignores_terminate(project, caplog):
    popen = FakePopen(FakeProcess(ignores_terminate=True))
    avatar_window = make_window(project, popen=popen)
    avatar_window.open()

    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        avatar_window.close()

    assert popen.process.calls == ["terminate", ("wait", 5.0), "kill"]
    assert "5151" in caplog.text


def test_close_leaves_a_window_the_owner_already_closed_alone(project):
    popen = FakePopen()
    jobs = FakeJobs()
    avatar_window = make_window(project, popen=popen, jobs=jobs)
    avatar_window.open()
    popen.process.returncode = 0  # the owner clicked the mascot's ×

    avatar_window.close()

    assert popen.process.calls == []
    assert jobs.made[0].closed


def test_close_twice_ends_the_window_once(project):
    popen = FakePopen()
    avatar_window = make_window(project, popen=popen)
    avatar_window.open()

    avatar_window.close()
    avatar_window.close()

    assert popen.process.calls == ["terminate", ("wait", 5.0)]


def test_close_before_open_does_nothing(project):
    popen = FakePopen()

    make_window(project, popen=popen).close()

    assert popen.process.calls == []


def test_close_touches_only_the_window_it_started(project):
    first, second = FakePopen(), FakePopen()
    mine = make_window(project, popen=first)
    other = make_window(project, popen=second)
    mine.open()
    other.open()

    mine.close()

    assert first.process.calls == ["terminate", ("wait", 5.0)]
    assert second.process.calls == []


def test_close_logs_errors_instead_of_raising_and_still_closes_the_job(project, caplog):
    popen = FakePopen(FakeProcess(terminate_error=PermissionError(5, "Access is denied")))
    jobs = FakeJobs(close_error=OSError(6, "The handle is invalid"))
    avatar_window = make_window(project, popen=popen, jobs=jobs)
    avatar_window.open()

    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        avatar_window.close()

    assert jobs.made[0].closed
    assert "Access is denied" in caplog.text
    assert "The handle is invalid" in caplog.text
    avatar_window.close()


def test_the_context_manager_opens_and_closes_the_window(project):
    popen = FakePopen()

    with make_window(project, popen=popen) as avatar_window:
        assert isinstance(avatar_window, AvatarWindow)
        assert popen.process.calls == []

    assert len(popen.calls) == 1
    assert popen.process.calls == ["terminate", ("wait", 5.0)]


# --- open again --------------------------------------------------------------------------


def test_open_while_the_window_is_up_starts_no_second_one(project):
    popen = FakePopen()
    avatar_window = make_window(project, popen=popen)

    avatar_window.open()
    avatar_window.open()

    assert len(popen.calls) == 1
    assert popen.process.calls == []


def test_open_after_the_owner_closed_the_window_starts_a_new_one(project):
    first, second = FakeProcess(), FakeProcess()
    popen = FakePopen(first, second)
    jobs = FakeJobs()
    avatar_window = make_window(project, popen=popen, jobs=jobs)
    avatar_window.open()
    first.returncode = 0

    avatar_window.open()

    assert len(popen.calls) == 2
    assert jobs.made[0].closed
    assert jobs.made[1].processes == [second]
    avatar_window.close()
    assert second.calls == ["terminate", ("wait", 5.0)]


# --- the mascot's output -----------------------------------------------------------------


def logged_output(project: Path, caplog: pytest.LogCaptureFixture, output: str) -> list:
    """What the window logs, as (level, message), while it reads the mascot's `output`."""
    threads = ManualThreads()
    make_window(project, popen=FakePopen(FakeProcess(output=output)), threads=threads).open()
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="waifu.avatar.window"):
        threads.run(OUTPUT)
    return [(record.levelno, record.getMessage()) for record in caplog.records]


def test_the_mascot_output_is_read_by_a_thread_of_its_own(project):
    threads = ManualThreads()

    make_window(project, threads=threads).open()

    assert threads.started(OUTPUT) == 1


def test_mascot_diagnostics_are_logged_at_info_and_chromium_noise_at_debug(project, caplog):
    output = (
        "Cyrene mascot: page-hidden\n"
        "[11064:1015/201500.123:ERROR:gpu_init.cc(523)] Passthrough off\n"
        "\n"
        "Cyrene mascot: display-metrics-changed scale=1.25\r\n"
    )

    assert logged_output(project, caplog, output) == [
        (logging.INFO, "Cyrene mascot: page-hidden"),
        (logging.DEBUG, "[11064:1015/201500.123:ERROR:gpu_init.cc(523)] Passthrough off"),
        (logging.INFO, "Cyrene mascot: display-metrics-changed scale=1.25"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "Cyrene mascot: render-process-gone reason=crashed exit-code=-1073741819",
        "Cyrene mascot: gpu-process-gone reason=oom exit-code=1",
        "Cyrene mascot: webgl-context-lost",
        "Cyrene mascot: webglcontextlost",
        "Cyrene mascot: unresponsive",
        "Cyrene mascot: GPU process CRASHED",
        "Cyrene mascot: webgl-unavailable",
        "Cyrene mascot: page-reload skipped: too many in the last 10 minutes",
    ],
)
def test_a_diagnostic_that_says_gone_lost_unresponsive_crashed_unavailable_or_skipped_is_a_warning(
    project, caplog, line
):
    # The rule: one of those six ends a word (followed by no letter), in any case. A
    # skipped reload and a page without WebGL leave her gone until something is done.
    assert logged_output(project, caplog, line + "\n") == [(logging.WARNING, line)]


@pytest.mark.parametrize(
    "line",
    [
        "Cyrene mascot: responsive",
        "Cyrene mascot: webgl-context-restored",
        "Cyrene mascot: page-reload in 1 s",
        "Cyrene mascot: re-pinned after the displays settled",
        "Cyrene mascot: page-hidden",
        "Cyrene mascot: display-removed id=2528732444",
        "Cyrene mascot: placed-back bottom-right of the primary display at 1544,384",
        "Cyrene mascot: lostpointercapture",
    ],
)
def test_other_diagnostics_stay_at_info(project, caplog, line):
    assert logged_output(project, caplog, line + "\n") == [(logging.INFO, line)]


def test_chromium_noise_saying_lost_stays_at_debug(project, caplog):
    line = "[2440:1015/201501.456:ERROR:shared_context_state.cc] GPU context lost"

    assert logged_output(project, caplog, line + "\n") == [(logging.DEBUG, line)]


def test_the_output_is_read_to_its_end_and_the_pipe_closed(project, caplog):
    # Reading must never stop early: Electron writes to its pipe synchronously on
    # Windows, so a full pipe that nobody empties would stall the mascot.
    threads = ManualThreads()
    process = FakeProcess(output="Cyrene mascot: page-reload\n" * 500)
    make_window(project, popen=FakePopen(process), threads=threads).open()

    with caplog.at_level(logging.INFO, logger="waifu.avatar.window"):
        threads.run(OUTPUT)

    assert caplog.text.count("Cyrene mascot: page-reload") == 500
    assert process.stdout.closed


# --- supervision -------------------------------------------------------------------------


def test_a_crashed_mascot_is_reopened_after_3_seconds(project, caplog):
    first, second = FakeProcess(exit_code=3), FakeProcess()
    popen = FakePopen(first, second)
    jobs = FakeJobs()
    threads = ManualThreads()
    sleep = FakeSleep()
    avatar_window = make_window(project, popen=popen, jobs=jobs, threads=threads, sleep=sleep)
    avatar_window.open()

    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        threads.run(SUPERVISOR)

    assert sleep.pauses == [3.0]
    assert [args for args, _ in popen.calls] == [popen.calls[0][0]] * 2
    assert "exited with code 3" in caplog.text
    first_job, second_job = jobs.made
    assert first_job.closed  # whatever the crashed mascot left running ends with it
    assert second_job.processes == [second]
    avatar_window.close()
    assert second.calls == ["terminate", ("wait", 5.0)]


def test_the_reopened_mascot_is_read_and_supervised_like_the_first(project):
    popen = FakePopen(FakeProcess(exit_code=3), FakeProcess(exit_code=3), FakeProcess())
    threads = ManualThreads()
    make_window(project, popen=popen, threads=threads).open()

    threads.run(SUPERVISOR)
    threads.run(SUPERVISOR)

    assert len(popen.calls) == 3
    assert threads.started(OUTPUT) == 3
    assert threads.started(SUPERVISOR) == 3


def test_a_mascot_the_owner_closed_with_exit_code_0_stays_closed(project, caplog):
    popen = FakePopen(FakeProcess(exit_code=0))
    jobs = FakeJobs()
    threads = ManualThreads()
    sleep = FakeSleep()
    make_window(project, popen=popen, jobs=jobs, threads=threads, sleep=sleep).open()

    with caplog.at_level(logging.INFO, logger="waifu.avatar.window"):
        threads.run(SUPERVISOR)

    assert len(popen.calls) == 1
    assert sleep.pauses == []
    assert "was closed" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_the_mascot_is_reopened_at_most_3_times_in_10_minutes(project, caplog):
    popen = FakePopen(*(FakeProcess(exit_code=3) for _ in range(4)))
    threads = ManualThreads()
    clock = FakeClock()
    make_window(project, popen=popen, threads=threads, clock=clock).open()

    for minute in (0, 1, 2):
        clock.now = minute * 60.0
        threads.run(SUPERVISOR)
    clock.now = 599.0
    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        threads.run(SUPERVISOR)

    assert len(popen.calls) == 4
    [restart] = [record for record in caplog.records if "Restart Cyrene" in record.getMessage()]
    assert restart.levelno == logging.WARNING
    assert threads.started(SUPERVISOR) == 4  # the fourth crash ended supervision


def test_reopens_older_than_10_minutes_no_longer_count(project):
    popen = FakePopen(*(FakeProcess(exit_code=3) for _ in range(4)), FakeProcess())
    threads = ManualThreads()
    clock = FakeClock()
    make_window(project, popen=popen, threads=threads, clock=clock).open()

    for now in (0.0, 60.0, 120.0, 600.0):
        clock.now = now
        threads.run(SUPERVISOR)

    assert len(popen.calls) == 5


def test_close_stops_supervision_so_the_mascot_it_ended_is_not_reopened(project):
    popen = FakePopen(FakeProcess(), FakeProcess())
    threads = ManualThreads()
    sleep = FakeSleep()
    avatar_window = make_window(project, popen=popen, threads=threads, sleep=sleep)
    avatar_window.open()

    avatar_window.close()  # terminate leaves exit code 1, like a crash
    threads.run(SUPERVISOR)

    assert len(popen.calls) == 1
    assert sleep.pauses == []


def test_close_during_the_reopen_delay_cancels_the_reopen(project):
    popen = FakePopen(FakeProcess(exit_code=3), FakeProcess())
    jobs = FakeJobs()
    threads = ManualThreads()
    avatar_window: AvatarWindow | None = None

    def close_now() -> None:
        assert avatar_window is not None
        avatar_window.close()

    avatar_window = make_window(
        project, popen=popen, jobs=jobs, threads=threads, sleep=FakeSleep(during=close_now)
    )
    avatar_window.open()

    threads.run(SUPERVISOR)

    assert len(popen.calls) == 1
    assert [job.closed for job in jobs.made] == [True]


def test_open_again_after_close_starts_a_new_supervision(project):
    # A supervisor of the window that was closed must not reopen it next to the new one.
    popen = FakePopen(FakeProcess(), FakeProcess(exit_code=3), FakeProcess())
    threads = ManualThreads()
    avatar_window = make_window(project, popen=popen, threads=threads)
    avatar_window.open()
    avatar_window.close()
    avatar_window.open()

    threads.run(SUPERVISOR)  # the closed window's: nothing to do
    threads.run(SUPERVISOR)  # the new window's: it crashed, reopen

    assert len(popen.calls) == 3


def test_a_mascot_that_cannot_be_reopened_is_only_logged(project, caplog):
    missing = FileNotFoundError(2, "The system cannot find the file specified")
    popen = FakePopen(FakeProcess(exit_code=3), missing)
    threads = ManualThreads()
    make_window(project, popen=popen, threads=threads).open()

    with caplog.at_level(logging.WARNING, logger="waifu.avatar.window"):
        threads.run(SUPERVISOR)

    assert "cannot find the file" in caplog.text
    assert threads.started(SUPERVISOR) == 1


def test_close_while_the_supervisor_waits_on_the_mascot_never_reopens_it(project):
    mascot = BlockingProcess()
    popen = FakePopen(mascot, FakeProcess())
    threads = RealThreads()
    avatar_window = make_window(project, popen=popen, threads=threads)
    avatar_window.open()
    assert mascot.waiting.wait(THREAD_TIMEOUT_S)

    avatar_window.close()  # its terminate releases the supervisor's wait with exit code 1
    threads.join_all()

    assert len(popen.calls) == 1
    assert "terminate" in mascot.calls


def test_close_during_a_reopen_ends_the_reopened_mascot(project):
    reopened = BlockingProcess()
    entered, release = threading.Event(), threading.Event()

    class SlowSecondPopen(FakePopen):
        def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
            if self.calls:  # the reopen, on the supervisor's thread
                entered.set()
                assert release.wait(THREAD_TIMEOUT_S)
            return super().__call__(args, **kwargs)

    popen = SlowSecondPopen(FakeProcess(exit_code=3), reopened)
    jobs = FakeJobs()
    threads = RealThreads()
    avatar_window = make_window(project, popen=popen, jobs=jobs, threads=threads)
    avatar_window.open()
    assert entered.wait(THREAD_TIMEOUT_S)

    closer = threading.Thread(target=avatar_window.close, daemon=True)
    closer.start()
    release.set()
    closer.join(THREAD_TIMEOUT_S)
    threads.join_all()

    assert not closer.is_alive()
    assert len(popen.calls) == 2
    assert "terminate" in reopened.calls
    assert jobs.made[-1].closed


# --- defaults ------------------------------------------------------------------------------


def test_the_default_project_root_is_this_repository():
    assert (window.PROJECT_ROOT / "avatar-app" / "package.json").is_file()
    assert (window.PROJECT_ROOT / "src" / "waifu" / "avatar" / "window.py").is_file()


def test_the_mascot_threads_never_keep_cyrene_from_exiting():
    # The reader may block on a pipe a Chromium child still holds, the supervisor in
    # wait(): as daemon threads they never stop `waifu run` from ending. Only the
    # factory is called; no thread is started and no window opened.
    default = inspect.signature(AvatarWindow).parameters["thread_factory"].default

    thread = window._daemon_thread(lambda: None, OUTPUT)

    assert default is window._daemon_thread
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    assert thread.name == OUTPUT
    assert not thread.is_alive()


def test_without_a_fake_popen_the_suite_guard_refuses_to_open_electron(project):
    # The default Popen is looked up when the window is made, so tests/conftest.py's
    # guard, not the real Popen, catches a test that forgets its fake.
    avatar_window = AvatarWindow(
        AvatarConfig(), URL, job_factory=FakeJobs(), project_root=project, environ={}
    )

    with pytest.raises(AssertionError, match="system-change command"):
        avatar_window.open()
