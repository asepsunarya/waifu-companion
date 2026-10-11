"""The PC itself: Windows power commands for this machine and Wake-on-LAN for any PC.

Power commands go through an injected `subprocess.run`, sleep through an
injected PowrProf call, and the magic packet through an injected sender, so
tests never touch the real machine or network.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading
from collections.abc import Callable, Collection
from typing import Any, Protocol

from wakeonlan import send_magic_packet

from waifu.config import PcConnection
from waifu.devices.base import Device, DeviceError, DeviceResult

logger = logging.getLogger(__name__)

LOCAL_COMMANDS = ("shutdown", "restart", "cancel_shutdown", "sleep")
# Interlock for tests and dry experiments: when set (to anything but "0"), the real
# shutdown/sleep calls refuse to run even if a caller forgot to inject a fake.
POWER_LOCK_ENV = "WAIFU_DISABLE_POWER"
SLEEP_DELAY_S = 5.0

# Win32 error codes that shutdown.exe returns as its exit code.
NO_SHUTDOWN_IN_PROGRESS = 1116
SHUTDOWN_ALREADY_SCHEDULED = 1190

# CREATE_NO_WINDOW keeps a console from flashing up; 0 is the only value POSIX accepts.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The device cannot know the assistant's name, so the hint says "call my name": a bare
# "batalkan" only passes the wake gate during the short follow-up window after she speaks.
_HOW_TO_CANCEL = "panggil namaku lalu bilang 'batalkan'"


class _Timer(Protocol):
    def start(self) -> None: ...

    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], _Timer]


def _daemon_timer(interval: float, function: Callable[[], None]) -> threading.Timer:
    """A timer that never keeps the app alive, so quitting it also drops a pending sleep."""
    timer = threading.Timer(interval, function)
    timer.daemon = True
    return timer


def _power_locked() -> bool:
    """True when WAIFU_DISABLE_POWER is set to anything but "0" (the test suite sets it)."""
    return os.environ.get(POWER_LOCK_ENV, "").strip() not in ("", "0")


def _check_power_unlocked() -> None:
    if _power_locked():
        raise OSError(f"power commands are disabled by {POWER_LOCK_ENV}")


def _run_power_command(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    _check_power_unlocked()
    return subprocess.run(args, **kwargs)


def _suspend_to_sleep() -> None:
    """Put Windows to sleep, never into hibernation; raise OSError if Windows refuses.

    Calls PowrProf's SetSuspendState(bHibernate, bForce, bWakeupEventsDisabled)
    directly. Through `rundll32 powrprof.dll,SetSuspendState 0,1,0` the "0,1,0"
    never arrives: the function receives rundll32's window handle as bHibernate,
    so the PC hibernates whenever hibernation is on (Fast Startup needs it).
    """
    _check_power_unlocked()
    powrprof = ctypes.WinDLL("PowrProf", use_last_error=True)
    set_suspend_state = powrprof.SetSuspendState
    set_suspend_state.argtypes = [ctypes.c_ubyte] * 3  # three Win32 BOOLEANs
    set_suspend_state.restype = ctypes.c_ubyte
    if not set_suspend_state(0, 0, 0):
        raise OSError(f"SetSuspendState failed with Windows error {ctypes.get_last_error()}")


class PcDevice(Device):
    kind = "pc"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        conn: PcConnection,
        *,
        run: Callable[..., Any] = _run_power_command,
        send_wol: Callable[..., None] = send_magic_packet,
        suspend: Callable[[], None] = _suspend_to_sleep,
        timer_factory: TimerFactory = _daemon_timer,
    ) -> None:
        super().__init__(device_id, name, aliases)
        self._conn = conn
        self._run = run
        self._send_wol = send_wol
        self._suspend_system = suspend
        self._timer_factory = timer_factory
        self._pending_sleep: _Timer | None = None

    def commands(self) -> list[str]:
        available = ["wake"] if self._conn.mac else []
        if self._conn.local:
            available.extend(LOCAL_COMMANDS)
        return available

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        if command == "wake":
            return self._wake()
        if not self._conn.local or command not in LOCAL_COMMANDS:
            available = ", ".join(self.commands()) or "tidak ada"
            raise DeviceError(
                f"Perintah '{command}' tidak tersedia untuk {self.name}. "
                f"Perintah yang tersedia: {available}."
            )
        handlers = {
            "shutdown": lambda: self._schedule("shutdown", "/s", "dimatikan"),
            "restart": lambda: self._schedule("restart", "/r", "di-restart"),
            "cancel_shutdown": self._cancel_shutdown,
            "sleep": self._sleep,
        }
        return handlers[command]()

    def _wake(self) -> DeviceResult:
        mac = self._conn.mac
        if not mac:
            raise DeviceError(
                f"{self.name} belum punya alamat MAC, jadi tidak bisa dinyalakan lewat jaringan."
            )
        try:
            self._send_wol(mac, ip_address=self._conn.broadcast)
        except ValueError as exc:
            raise DeviceError(f"Alamat MAC {mac} untuk {self.name} tidak valid.") from exc
        except OSError as exc:
            logger.warning("Wake-on-LAN for %s failed: %s", self.id, exc)
            raise DeviceError(f"Gagal mengirim sinyal untuk menyalakan {self.name}.") from exc
        logger.info("Sent Wake-on-LAN packet for %s to %s", self.id, self._conn.broadcast)
        return self._done("wake", f"Sinyal untuk menyalakan {self.name} sudah dikirim.")

    def _schedule(self, command: str, flag: str, verb: str) -> DeviceResult:
        delay = self._conn.shutdown_delay_s
        self._run_command(["shutdown", flag, "/t", str(delay)])
        logger.info("Scheduled %s of %s in %d s", command, self.id, delay)
        if delay == 0:
            return self._done(command, f"{self.name} {verb} sekarang.")
        # shutdown.exe implies /f whenever /t is above 0: apps get no chance to ask to save.
        return self._done(
            command,
            f"{self.name} akan {verb} dalam {delay} detik. "
            "Aplikasi yang masih terbuka akan ditutup paksa, jadi simpan pekerjaanmu dulu. "
            f"Untuk membatalkan, {_HOW_TO_CANCEL}.",
        )

    def _cancel_shutdown(self) -> DeviceResult:
        cancelled = ["sleep"] if self._cancel_pending_sleep() else []
        exit_code = self._run_command(
            ["shutdown", "/a"], tolerated_exit_codes={NO_SHUTDOWN_IN_PROGRESS}
        )
        if exit_code != NO_SHUTDOWN_IN_PROGRESS:
            cancelled.append("mati atau restart")
        if not cancelled:
            return self._done(
                "cancel_shutdown",
                "Tidak ada jadwal mati, restart, atau sleep yang perlu dibatalkan.",
            )
        logger.info("Cancelled the pending %s of %s", " and ".join(cancelled), self.id)
        return self._done(
            "cancel_shutdown", f"Jadwal {' dan '.join(cancelled)} {self.name} dibatalkan."
        )

    def _sleep(self) -> DeviceResult:
        # A second request replaces the first; two timers would sleep the PC again on wake.
        self._cancel_pending_sleep()
        timer = self._timer_factory(SLEEP_DELAY_S, self._suspend)
        timer.start()
        self._pending_sleep = timer
        logger.info("Putting %s to sleep in %.0f s", self.id, SLEEP_DELAY_S)
        return self._done(
            "sleep", f"{self.name} akan masuk mode sleep dalam {SLEEP_DELAY_S:.0f} detik."
        )

    def _cancel_pending_sleep(self) -> bool:
        timer, self._pending_sleep = self._pending_sleep, None
        if timer is None:
            return False
        timer.cancel()
        return True

    def _suspend(self) -> None:
        # Runs on the timer thread, where nobody could catch the failure.
        self._pending_sleep = None
        try:
            self._suspend_system()
        except OSError as exc:
            logger.error("Could not put %s to sleep: %s", self.id, exc)

    def _run_command(self, args: list[str], *, tolerated_exit_codes: Collection[int] = ()) -> int:
        """Run a power command; return 0 or a tolerated exit code, raise DeviceError otherwise."""
        try:
            self._run(args, check=True, capture_output=True, creationflags=_CREATION_FLAGS)
        except subprocess.CalledProcessError as exc:
            if exc.returncode in tolerated_exit_codes:
                return exc.returncode
            logger.warning("%s exited with code %s: %r", args, exc.returncode, exc.stderr)
            if exc.returncode == SHUTDOWN_ALREADY_SCHEDULED:
                raise DeviceError(
                    f"{self.name} sudah dijadwalkan mati atau restart. "
                    f"Kalau mau mengubahnya, {_HOW_TO_CANCEL} dulu."
                ) from exc
            raise DeviceError(
                f"Perintah daya untuk {self.name} gagal (kode {exc.returncode})."
            ) from exc
        except OSError as exc:
            logger.warning("%s could not be started: %s", args, exc)
            raise DeviceError(f"Perintah daya untuk {self.name} tidak bisa dijalankan.") from exc
        return 0

    def _done(self, command: str, message: str) -> DeviceResult:
        self.state["last_command"] = command
        return DeviceResult(ok=True, message=message, state=dict(self.state))
