"""Global hotkeys: Ctrl+Alt plus one key, heard whichever window has the focus, a game too.

Windows' `RegisterHotKey` hands over only the combos registered, never another key Master types,
needs no admin rights, and keeps working while a game has the focus (a game that swallows keys
itself, as some anti-cheat does, is beyond reach). Windows posts `WM_HOTKEY` to the message queue
of the thread that registered the combo, and only that thread may unregister it, so one thread
of its own (`HotkeyThread`) makes its queue, registers, waits in `GetMessageW`, and unregisters
as it ends. `stop()` posts `WM_QUIT` to it, which needs the queue to exist: the thread says when
it is ready.

The thread calls `on_press(action)` for every combo pressed; that callback must never block, or
the next combo (Ctrl+Alt+X while she speaks) waits behind it.

user32 comes through ctypes with Microsoft's documented signatures (winuser.h), looked up at
every call, never at import, so the test suite's guard on these functions always applies.

`waifu run` records which combos it registered in a small status file (`write_status`) for
`waifu check`, which makes no Win32 call itself.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

# What a hotkey does: talk now, cut her off, repeat her last words, stop (or start) listening.
ACTIONS = ("talk", "stop", "repeat", "quiet")
# One key with Ctrl+Alt: a letter, a digit, or F1-F12.
KEYS = (
    *(chr(code) for code in range(ord("A"), ord("Z") + 1)),
    *(str(digit) for digit in range(10)),
    *(f"F{number}" for number in range(1, 13)),
)

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
PM_NOREMOVE = 0x0000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000  # a held combo fires once
MODIFIERS = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
VK_F1 = 0x70

THREAD_NAME = "global-hotkeys"
READY_TIMEOUT_S = 2.0  # how long `stop` waits for the thread's queue
STOP_TIMEOUT_S = 2.0
STATUS_VERSION = 1


def virtual_key(key: str) -> int:
    """The Windows virtual-key code of `key` (one of KEYS): a letter's or digit's own code
    (uppercase), F1-F12 from VK_F1. ValueError for any other key."""
    if key not in KEYS:
        raise ValueError(f"unknown key {key!r}: use A-Z, 0-9, or F1-F12")
    if len(key) == 1:
        return ord(key)
    return VK_F1 + int(key[1:]) - 1


@dataclass(frozen=True)
class Hotkey:
    """One combo: Ctrl+Alt+`key` does `action`."""

    action: str
    key: str

    @property
    def combo(self) -> str:
        return f"Ctrl+Alt+{self.key}"


@dataclass(frozen=True)
class Registration:
    """How registering a hotkey went: Windows' error code, 0 when it is registered."""

    hotkey: Hotkey
    error: int

    @property
    def registered(self) -> bool:
        return self.error == 0


@dataclass(frozen=True)
class Message:
    """A message `GetMessageW` took from the thread's queue (its id and wParam)."""

    message: int
    wparam: int


class User32(Protocol):
    """The user32 calls of the hotkey thread; all but `post_quit` are made on that thread."""

    def create_queue(self) -> None:
        """Make the calling thread's message queue."""
        ...

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> int:
        """Register a combo for the calling thread: 0, or Windows' error code."""
        ...

    def unregister_hotkey(self, hotkey_id: int) -> bool: ...

    def get_message(self) -> Message | None:
        """The next message, waiting for it; None for WM_QUIT. OSError when Windows fails."""
        ...

    def post_quit(self, thread_id: int) -> bool:
        """Post WM_QUIT to the thread `thread_id`; False when Windows refuses."""
        ...


_Signature = tuple[list[Any], Any]
_MSG_POINTER = ctypes.POINTER(wintypes.MSG)
_FUNCTIONS: Mapping[str, _Signature] = {
    "RegisterHotKey": ([wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
    "UnregisterHotKey": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
    "GetMessageW": ([_MSG_POINTER, wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
    "PeekMessageW": (
        [_MSG_POINTER, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT],
        wintypes.BOOL,
    ),
    "PostThreadMessageW": (
        [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        wintypes.BOOL,
    ),
}


def _user32(name: str) -> Callable[..., Any]:
    """The user32 function `name` with its full signature, loaded only when it is called."""
    argtypes, restype = _FUNCTIONS[name]

    def call(*args: Any) -> Any:
        function = getattr(ctypes.WinDLL("user32", use_last_error=True), name)
        function.argtypes = argtypes
        function.restype = restype
        return function(*args)

    return call


class Win32User32:
    """The real user32 (hWnd NULL throughout: the messages go to the calling thread)."""

    def __init__(self) -> None:
        self._peek = _user32("PeekMessageW")
        self._register = _user32("RegisterHotKey")
        self._unregister = _user32("UnregisterHotKey")
        self._get = _user32("GetMessageW")
        self._post = _user32("PostThreadMessageW")

    def create_queue(self) -> None:
        # Any message call makes the thread's queue; PM_NOREMOVE leaves the queue as it is.
        self._peek(ctypes.byref(wintypes.MSG()), None, 0, 0, PM_NOREMOVE)

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> int:
        if self._register(None, hotkey_id, modifiers, vk):
            return 0
        return ctypes.get_last_error()

    def unregister_hotkey(self, hotkey_id: int) -> bool:
        return bool(self._unregister(None, hotkey_id))

    def get_message(self) -> Message | None:
        msg = wintypes.MSG()
        result = self._get(ctypes.byref(msg), None, 0, 0)
        if result == -1:
            raise OSError(f"GetMessageW failed (Windows error {ctypes.get_last_error()})")
        return None if result == 0 else Message(msg.message, msg.wParam)

    def post_quit(self, thread_id: int) -> bool:
        return bool(self._post(thread_id, WM_QUIT, 0, 0))


class HotkeyThread:
    """Registers `hotkeys` on a thread of its own and calls `on_press(action)` as each is pressed,
    from `start()` (or entering the block) until `stop()`."""

    def __init__(
        self,
        hotkeys: Sequence[Hotkey],
        on_press: Callable[[str], None],
        *,
        user32: User32 | None = None,
    ) -> None:
        self._hotkeys = tuple(hotkeys)
        self._on_press = on_press
        self._user32: User32 = Win32User32() if user32 is None else user32
        self._ready = threading.Event()
        self._registrations: tuple[Registration, ...] = ()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the thread; starting a running one does nothing."""
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """End the thread: WM_QUIT once it is ready, then a bounded wait. A thread that never
        got ready, or does not end in time, is left behind (it is a daemon)."""
        thread, self._thread = self._thread, None
        if thread is None:
            return
        if not self._ready.wait(READY_TIMEOUT_S):
            logger.warning("The global hotkeys' thread never got ready; leaving it behind")
            return
        native_id = thread.native_id
        if thread.is_alive() and native_id is not None and not self._user32.post_quit(native_id):
            logger.warning("Could not ask the global hotkeys' thread to stop")
        thread.join(STOP_TIMEOUT_S)
        if thread.is_alive():
            logger.warning("The global hotkeys' thread is still running; leaving it behind")

    def registrations(self) -> tuple[Registration, ...]:
        """How each hotkey's registration went, once the thread is ready (waiting up to
        READY_TIMEOUT_S); empty when it could not start."""
        self._ready.wait(READY_TIMEOUT_S)
        return self._registrations

    def __enter__(self) -> HotkeyThread:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _run(self) -> None:
        registered: dict[int, Hotkey] = {}
        try:
            self._user32.create_queue()
            self._registrations = tuple(self._register(registered))
            self._ready.set()
            if registered:
                self._wait(registered)
        except Exception:
            logger.exception("The global hotkeys could not start")
        finally:
            self._ready.set()  # `stop` never waits for a thread that failed
            for hotkey_id in registered:
                self._user32.unregister_hotkey(hotkey_id)

    def _register(self, registered: dict[int, Hotkey]) -> list[Registration]:
        """Register every hotkey, adding each one Windows took to `registered` by its id."""
        registrations = []
        for hotkey_id, hotkey in enumerate(self._hotkeys, start=1):
            error = self._user32.register_hotkey(hotkey_id, MODIFIERS, virtual_key(hotkey.key))
            registrations.append(Registration(hotkey, error))
            if error == 0:
                registered[hotkey_id] = hotkey
            elif error == ERROR_HOTKEY_ALREADY_REGISTERED:
                logger.warning(
                    "%s is taken by another program; %s has no hotkey", hotkey.combo, hotkey.action
                )
            else:
                logger.warning("Could not register %s (Windows error %d)", hotkey.combo, error)
        if registered:
            combos = ", ".join(f"{hotkey.combo} {hotkey.action}" for hotkey in registered.values())
            logger.info("Global hotkeys: %s", combos)
        return registrations

    def _wait(self, registered: Mapping[int, Hotkey]) -> None:
        """Hand each registered combo pressed to `on_press` until WM_QUIT or a failure."""
        while True:
            try:
                message = self._user32.get_message()
            except OSError as exc:
                logger.error("Waiting for the global hotkeys failed; they stop: %s", exc)
                return
            if message is None:
                return
            hotkey = registered.get(message.wparam) if message.message == WM_HOTKEY else None
            if hotkey is not None:
                self._press(hotkey)

    def _press(self, hotkey: Hotkey) -> None:
        logger.info("Hotkey %s (%s)", hotkey.combo, hotkey.action)
        try:
            self._on_press(hotkey.action)
        except Exception:
            logger.exception("The hotkey %s (%s) failed", hotkey.combo, hotkey.action)


@dataclass(frozen=True)
class HotkeyStatus:
    """What a `waifu run` recorded: its process id, and how each hotkey's registration went."""

    pid: int
    registrations: tuple[Registration, ...]


def write_status(path: Path, pid: int, registrations: Sequence[Registration]) -> None:
    """Record `registrations` of the process `pid` at `path`, atomically. OSError on failure."""
    write_json_atomic(
        path,
        {
            "version": STATUS_VERSION,
            "pid": pid,
            "hotkeys": [
                {"action": item.hotkey.action, "key": item.hotkey.key, "error": item.error}
                for item in registrations
            ],
        },
    )


def read_status(path: Path) -> HotkeyStatus | None:
    """The status recorded at `path`; None when there is none or it is not one. Never raises."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    with contextlib.suppress(ValueError):
        return _parse_status(raw)
    return None


def _parse_status(raw: object) -> HotkeyStatus:
    if not isinstance(raw, dict) or raw.keys() != {"version", "pid", "hotkeys"}:
        raise ValueError("not a status")
    version, pid, items = raw["version"], raw["pid"], raw["hotkeys"]
    if type(version) is not int or version != STATUS_VERSION or type(pid) is not int:
        raise ValueError("not a status")
    if not isinstance(items, list):
        raise ValueError("not a status")
    return HotkeyStatus(pid, tuple(_parse_registration(item) for item in items))


def _parse_registration(item: object) -> Registration:
    if not isinstance(item, dict) or item.keys() != {"action", "key", "error"}:
        raise ValueError("not a registration")
    action, key, error = item["action"], item["key"], item["error"]
    if action not in ACTIONS or key not in KEYS or type(error) is not int or error < 0:
        raise ValueError("not a registration")
    return Registration(Hotkey(action, key), error)
