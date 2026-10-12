import logging
import subprocess
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


class FakeProcess:
    """The parts of subprocess.Popen the window uses, recording what it is asked to do."""

    def __init__(
        self,
        *,
        exit_code: int | None = None,
        ignores_terminate: bool = False,
        terminate_error: OSError | None = None,
    ) -> None:
        self.pid = 5151
        self.returncode = exit_code
        self.ignores_terminate = ignores_terminate
        self.terminate_error = terminate_error
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


class FakePopen:
    """Records each spawn and hands out the next scripted process."""

    def __init__(self, *processes: FakeProcess, error: OSError | None = None) -> None:
        self.processes = list(processes) or [FakeProcess()]
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        return self.processes[len(self.calls) - 1]

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


def test_the_window_output_is_discarded_and_no_console_flags_are_passed(project):
    popen = FakePopen()

    make_window(project, popen=popen).open()

    [(_, kwargs)] = popen.calls
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
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


# --- defaults ------------------------------------------------------------------------------


def test_the_default_project_root_is_this_repository():
    assert (window.PROJECT_ROOT / "avatar-app" / "package.json").is_file()
    assert (window.PROJECT_ROOT / "src" / "waifu" / "avatar" / "window.py").is_file()


def test_without_a_fake_popen_the_suite_guard_refuses_to_open_electron(project):
    # The default Popen is looked up when the window is made, so tests/conftest.py's
    # guard, not the real Popen, catches a test that forgets its fake.
    avatar_window = AvatarWindow(
        AvatarConfig(), URL, job_factory=FakeJobs(), project_root=project, environ={}
    )

    with pytest.raises(AssertionError, match="system-change command"):
        avatar_window.open()
