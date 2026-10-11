import ctypes
import logging
import subprocess
import threading

import pytest

from waifu.config import PcConnection
from waifu.devices.base import DeviceError
from waifu.devices.pc import PcDevice

MAC = "AA:BB:CC:DD:EE:FF"
SHUTDOWN_MESSAGE = (
    "Komputer akan dimatikan dalam 30 detik. "
    "Aplikasi yang masih terbuka akan ditutup paksa, jadi simpan pekerjaanmu dulu. "
    "Untuk membatalkan, panggil namaku lalu bilang 'batalkan'."
)


@pytest.fixture(autouse=True)
def refuse_real_powrprof(monkeypatch):
    """Fails any test that would reach the real SetSuspendState and sleep this PC."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to load the real PowrProf.dll")

    monkeypatch.setattr(ctypes, "WinDLL", refuse, raising=False)


class FakeRun:
    """Stands in for subprocess.run, including its check=True behaviour."""

    def __init__(self, returncode: int = 0, error: Exception | None = None) -> None:
        self.returncode = returncode
        self.error = error
        self.calls: list[tuple[list[str], dict]] = []

    @property
    def commands(self) -> list[list[str]]:
        return [args for args, _ in self.calls]

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        if self.returncode and kwargs.get("check"):
            raise subprocess.CalledProcessError(self.returncode, args, b"", b"error text")
        return subprocess.CompletedProcess(args, self.returncode, b"", b"")


class FakeWol:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *macs: str, **kwargs: object) -> None:
        self.calls.append((macs, kwargs))
        if self.error is not None:
            raise self.error


class FakeSuspend:
    """Stands in for the PowrProf call that puts Windows to sleep."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class FakeWinDll:
    """Stands in for ctypes.WinDLL: records how SetSuspendState is declared and called."""

    def __init__(self, result: int = 1) -> None:
        self.loaded: list[tuple[str, dict]] = []
        self.calls: list[tuple] = []

        def set_suspend_state(*args):
            self.calls.append(args)
            return result

        self.SetSuspendState = set_suspend_state

    def __call__(self, name: str, **kwargs: object) -> "FakeWinDll":
        self.loaded.append((name, kwargs))
        return self


class FakeTimer:
    def __init__(self, interval: float, function) -> None:
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class FakeTimerFactory:
    def __init__(self) -> None:
        self.timers: list[FakeTimer] = []

    def __call__(self, interval: float, function) -> FakeTimer:
        timer = FakeTimer(interval, function)
        self.timers.append(timer)
        return timer


def make_pc(
    conn: PcConnection, *, run=None, send_wol=None, suspend=None, timer_factory=None
) -> PcDevice:
    """Every vendor boundary is faked, so no test can power off or suspend the real PC."""
    return PcDevice(
        "komputer",
        "Komputer",
        ["pc", "laptop"],
        conn,
        run=run or FakeRun(),
        send_wol=send_wol or FakeWol(),
        suspend=suspend or FakeSuspend(),
        timer_factory=timer_factory or FakeTimerFactory(),
    )


def test_shutdown_schedules_a_delayed_windows_shutdown():
    calls = []
    pc = make_pc(PcConnection(shutdown_delay_s=30), run=lambda cmd, **kw: calls.append(cmd))

    assert pc.execute("shutdown").ok
    assert calls == [["shutdown", "/s", "/t", "30"]]


def test_shutdown_message_tells_the_delay_the_forced_close_and_how_to_cancel():
    # shutdown.exe implies /f for any /t above 0, so unsaved work is lost unless she warns.
    # A bare "batalkan" only passes the wake gate in the short follow-up window, not for 30 s.
    pc = make_pc(PcConnection(shutdown_delay_s=30))

    result = pc.execute("shutdown")

    assert result.message == SHUTDOWN_MESSAGE


def test_restart_schedules_a_delayed_windows_restart_with_the_configured_delay():
    run = FakeRun()
    pc = make_pc(PcConnection(shutdown_delay_s=90), run=run)

    result = pc.execute("restart")

    assert result.ok
    assert run.commands == [["shutdown", "/r", "/t", "90"]]
    assert "90 detik" in result.message
    assert "ditutup paksa" in result.message
    assert "panggil namaku lalu bilang 'batalkan'" in result.message


def test_zero_delay_shutdown_says_it_happens_now_without_offering_to_cancel():
    run = FakeRun()
    pc = make_pc(PcConnection(shutdown_delay_s=0), run=run)

    result = pc.execute("shutdown")

    assert run.commands == [["shutdown", "/s", "/t", "0"]]
    assert result.message == "Komputer dimatikan sekarang."


def test_power_commands_are_checked_and_run_without_a_console_window():
    run = FakeRun()
    pc = make_pc(PcConnection(), run=run)

    pc.execute("shutdown")

    _, kwargs = run.calls[0]
    assert kwargs["check"] is True
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_cancel_shutdown_aborts_the_pending_shutdown():
    run = FakeRun()
    pc = make_pc(PcConnection(), run=run)

    result = pc.execute("cancel_shutdown")

    assert result.ok
    assert run.commands == [["shutdown", "/a"]]
    assert "dibatalkan" in result.message


def test_cancel_shutdown_with_nothing_scheduled_is_ok_with_a_friendly_message():
    pc = make_pc(PcConnection(), run=FakeRun(returncode=1116))

    result = pc.execute("cancel_shutdown")

    assert result.ok
    assert result.message == "Tidak ada jadwal mati, restart, atau sleep yang perlu dibatalkan."


def test_failed_power_command_raises_device_error_with_the_exit_code():
    pc = make_pc(PcConnection(), run=FakeRun(returncode=5))

    with pytest.raises(DeviceError, match="kode 5"):
        pc.execute("restart")


def test_shutdown_while_one_is_already_scheduled_explains_how_to_cancel():
    pc = make_pc(PcConnection(), run=FakeRun(returncode=1190))

    with pytest.raises(DeviceError, match="sudah dijadwalkan.*panggil namaku.*'batalkan'"):
        pc.execute("shutdown")


def test_missing_shutdown_executable_raises_device_error():
    pc = make_pc(PcConnection(), run=FakeRun(error=FileNotFoundError("shutdown")))

    with pytest.raises(DeviceError, match="Komputer"):
        pc.execute("shutdown")


def test_sleep_is_deferred_on_a_timer_so_she_can_finish_speaking():
    run = FakeRun()
    timers = FakeTimerFactory()
    pc = make_pc(PcConnection(), run=run, timer_factory=timers)

    result = pc.execute("sleep")

    assert result.ok
    assert "5 detik" in result.message
    assert [(t.interval, t.started) for t in timers.timers] == [(5.0, True)]
    assert run.calls == []


def test_sleep_timer_suspends_the_pc_when_it_fires():
    run = FakeRun()
    suspend = FakeSuspend()
    timers = FakeTimerFactory()
    pc = make_pc(PcConnection(), run=run, suspend=suspend, timer_factory=timers)
    pc.execute("sleep")

    timers.timers[0].function()

    assert suspend.calls == 1
    assert run.calls == []


def test_sleep_failure_inside_the_timer_is_logged_instead_of_raised(caplog):
    timers = FakeTimerFactory()
    suspend = FakeSuspend(error=OSError("SetSuspendState failed"))
    pc = make_pc(PcConnection(), suspend=suspend, timer_factory=timers)
    pc.execute("sleep")

    with caplog.at_level(logging.ERROR, logger="waifu.devices.pc"):
        timers.timers[0].function()

    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_sleep_asks_windows_to_sleep_and_never_to_hibernate(monkeypatch):
    # rundll32 cannot pass "0,1,0": SetSuspendState got its window handle as bHibernate.
    monkeypatch.setenv("WAIFU_DISABLE_POWER", "0")  # safe: WinDLL itself is faked below
    powrprof = FakeWinDll()
    monkeypatch.setattr(ctypes, "WinDLL", powrprof, raising=False)
    timers = FakeTimerFactory()
    pc = PcDevice(
        "komputer",
        "Komputer",
        [],
        PcConnection(),
        run=FakeRun(),
        send_wol=FakeWol(),
        timer_factory=timers,
    )
    pc.execute("sleep")

    timers.timers[0].function()

    assert powrprof.loaded == [("PowrProf", {"use_last_error": True})]
    assert powrprof.SetSuspendState.argtypes == [ctypes.c_ubyte] * 3
    assert powrprof.calls == [(0, 0, 0)]


def test_sleep_refused_by_windows_is_logged_with_the_windows_error(monkeypatch, caplog):
    monkeypatch.setenv("WAIFU_DISABLE_POWER", "0")  # safe: WinDLL itself is faked below
    monkeypatch.setattr(ctypes, "WinDLL", FakeWinDll(result=0), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1314, raising=False)
    timers = FakeTimerFactory()
    pc = PcDevice(
        "komputer",
        "Komputer",
        [],
        PcConnection(),
        run=FakeRun(),
        send_wol=FakeWol(),
        timer_factory=timers,
    )
    pc.execute("sleep")

    with caplog.at_level(logging.ERROR, logger="waifu.devices.pc"):
        timers.timers[0].function()

    assert any("1314" in record.getMessage() for record in caplog.records)


def test_cancel_shutdown_also_cancels_a_pending_sleep():
    timers = FakeTimerFactory()
    pc = make_pc(PcConnection(), run=FakeRun(returncode=1116), timer_factory=timers)
    pc.execute("sleep")

    result = pc.execute("cancel_shutdown")

    assert timers.timers[0].cancelled
    assert result.ok
    assert result.message == "Jadwal sleep Komputer dibatalkan."


def test_cancel_after_the_sleep_timer_fired_has_no_sleep_left_to_cancel():
    timers = FakeTimerFactory()
    pc = make_pc(PcConnection(), run=FakeRun(returncode=1116), timer_factory=timers)
    pc.execute("sleep")
    timers.timers[0].function()

    result = pc.execute("cancel_shutdown")

    assert result.message == "Tidak ada jadwal mati, restart, atau sleep yang perlu dibatalkan."


def test_second_sleep_replaces_the_pending_one_so_the_pc_never_sleeps_twice():
    timers = FakeTimerFactory()
    pc = make_pc(PcConnection(), timer_factory=timers)

    pc.execute("sleep")
    pc.execute("sleep")

    assert [(t.started, t.cancelled) for t in timers.timers] == [(True, True), (True, False)]


def test_default_sleep_timer_never_keeps_the_app_alive_and_can_be_cancelled():
    suspend = FakeSuspend()
    pc = PcDevice("komputer", "Komputer", [], PcConnection(), run=FakeRun(), suspend=suspend)
    before = set(threading.enumerate())

    pc.execute("sleep")
    (timer,) = [thread for thread in threading.enumerate() if thread not in before]
    pc.execute("cancel_shutdown")
    timer.join(timeout=2)

    assert timer.daemon
    assert not timer.is_alive()
    assert suspend.calls == 0


def test_wake_sends_a_magic_packet_to_the_mac_on_the_configured_broadcast():
    wol = FakeWol()
    pc = make_pc(PcConnection(mac=MAC, broadcast="192.168.1.255", local=False), send_wol=wol)

    result = pc.execute("wake")

    assert result.ok
    assert wol.calls == [((MAC,), {"ip_address": "192.168.1.255"})]


def test_wake_without_a_mac_is_a_device_error():
    wol = FakeWol()
    pc = make_pc(PcConnection(mac=None), send_wol=wol)

    with pytest.raises(DeviceError, match="MAC"):
        pc.execute("wake")
    assert wol.calls == []


def test_wake_with_a_malformed_mac_is_a_device_error():
    pc = make_pc(
        PcConnection(mac="not-a-mac"),
        send_wol=FakeWol(error=ValueError("Incorrect MAC address format")),
    )

    with pytest.raises(DeviceError, match="not-a-mac"):
        pc.execute("wake")


def test_wake_network_failure_is_a_device_error():
    pc = make_pc(PcConnection(mac=MAC), send_wol=FakeWol(error=OSError("network unreachable")))

    with pytest.raises(DeviceError, match="Komputer"):
        pc.execute("wake")


def test_local_pc_with_mac_offers_wake_and_every_power_command():
    pc = make_pc(PcConnection(mac=MAC, local=True))

    assert pc.commands() == ["wake", "shutdown", "restart", "cancel_shutdown", "sleep"]


def test_local_pc_without_mac_does_not_offer_wake():
    pc = make_pc(PcConnection(mac=None, local=True))

    assert pc.commands() == ["shutdown", "restart", "cancel_shutdown", "sleep"]


def test_remote_pc_exposes_only_wake():
    pc = make_pc(PcConnection(mac=MAC, local=False))

    assert pc.commands() == ["wake"]


@pytest.mark.parametrize("command", ["shutdown", "restart", "cancel_shutdown", "sleep"])
def test_remote_pc_never_runs_local_power_commands(command):
    run = FakeRun()
    timers = FakeTimerFactory()
    pc = make_pc(PcConnection(mac=MAC, local=False), run=run, timer_factory=timers)

    with pytest.raises(DeviceError, match="wake"):
        pc.execute(command)
    assert run.calls == []
    assert timers.timers == []


def test_unknown_command_lists_the_available_commands():
    pc = make_pc(PcConnection())

    with pytest.raises(DeviceError, match="shutdown, restart, cancel_shutdown, sleep"):
        pc.execute("hibernate")


def test_describe_reports_a_pc_with_its_aliases_and_last_command():
    pc = make_pc(PcConnection(mac=MAC, local=False))
    pc.execute("wake")

    description = pc.describe()

    assert description["kind"] == "pc"
    assert description["aliases"] == ["pc", "laptop"]
    assert description["commands"] == ["wake"]
    assert description["state"] == {"last_command": "wake"}


class RecordingPopen:
    """Replaces subprocess.Popen to prove a locked PC never spawns a power command."""

    def __init__(self) -> None:
        self.spawned: list[object] = []

    def __call__(self, args, *pargs, **kwargs):
        self.spawned.append(args)
        raise AssertionError(f"spawned {args!r} although power commands are locked")


@pytest.mark.parametrize("command", ["shutdown", "restart", "cancel_shutdown"])
def test_power_lock_refuses_real_power_commands_without_spawning(monkeypatch, command):
    monkeypatch.setenv("WAIFU_DISABLE_POWER", "1")
    popen = RecordingPopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    pc = PcDevice("komputer", "Komputer", [], PcConnection(), timer_factory=FakeTimerFactory())

    with pytest.raises(DeviceError):
        pc.execute(command)
    assert popen.spawned == []


def test_power_lock_refuses_real_sleep_without_loading_powrprof(monkeypatch):
    monkeypatch.setenv("WAIFU_DISABLE_POWER", "1")
    timers = FakeTimerFactory()
    pc = PcDevice("komputer", "Komputer", [], PcConnection(), timer_factory=timers)

    assert pc.execute("sleep").ok
    timers.timers[0].function()  # the autouse fixture fails the test if PowrProf is loaded
    assert pc.state["last_command"] == "sleep"


def test_power_lock_value_zero_allows_power_commands(monkeypatch):
    monkeypatch.setenv("WAIFU_DISABLE_POWER", "0")
    popen = RecordingPopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    pc = PcDevice("komputer", "Komputer", [], PcConnection(), timer_factory=FakeTimerFactory())

    with pytest.raises(AssertionError, match="spawned"):
        pc.execute("cancel_shutdown")
    assert popen.spawned == [["shutdown", "/a"]]
