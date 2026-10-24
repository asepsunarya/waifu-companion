"""The global hotkeys' Win32 boundary: virtual-key codes, the thread that registers, waits, and
unregisters, and the status file `waifu check` reads.

The thread runs against a fake user32 whose message queue is a `queue.Queue`: `press` posts a
WM_HOTKEY, `post_quit` a WM_QUIT. tests/conftest.py refuses the real functions."""

from __future__ import annotations

import ctypes
import json
import logging
import queue
import threading
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from waifu import hotkeys
from waifu.hotkeys import (
    ERROR_HOTKEY_ALREADY_REGISTERED,
    KEYS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    PM_NOREMOVE,
    WM_HOTKEY,
    WM_QUIT,
    Hotkey,
    HotkeyStatus,
    HotkeyThread,
    Message,
    Registration,
    Win32User32,
    read_status,
    virtual_key,
    write_status,
)

DEFAULT_HOTKEYS = (
    Hotkey("talk", "C"),
    Hotkey("stop", "X"),
    Hotkey("repeat", "R"),
    Hotkey("quiet", "Q"),
)
_QUIT = object()
WAIT_S = 5.0


class FakeUser32:
    """user32 as the hotkey thread sees it, recording each call with the thread that made it."""

    def __init__(self, errors: dict[int, int] | None = None) -> None:
        self.errors = errors or {}  # virtual key -> Windows error of RegisterHotKey
        self.messages: queue.Queue[object] = queue.Queue()
        self.calls: list[tuple[str, tuple[int, ...], int]] = []
        self.queue_thread: int | None = None  # the native id of the thread that made its queue
        self.ready_to_queue = threading.Event()
        self.ready_to_queue.set()

    def _record(self, name: str, *args: int) -> None:
        self.calls.append((name, args, threading.get_ident()))

    def create_queue(self) -> None:
        self.ready_to_queue.wait(WAIT_S)
        self._record("create_queue")
        self.queue_thread = threading.get_native_id()

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> int:
        self._record("register_hotkey", hotkey_id, modifiers, vk)
        return self.errors.get(vk, 0)

    def unregister_hotkey(self, hotkey_id: int) -> bool:
        self._record("unregister_hotkey", hotkey_id)
        return True

    def get_message(self) -> Message | None:
        self._record("get_message")
        item = self.messages.get(timeout=WAIT_S)
        if item is _QUIT:
            return None
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, Message)
        return item

    def post_quit(self, thread_id: int) -> bool:
        self._record("post_quit", thread_id)
        if thread_id != self.queue_thread:
            return False  # Windows: no such thread's queue
        self.messages.put(_QUIT)
        return True

    def press(self, hotkey_id: int) -> None:
        self.messages.put(Message(WM_HOTKEY, hotkey_id))

    def called(self, name: str) -> list[tuple[int, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def threads(self) -> set[int]:
        return {thread for name, _, thread in self.calls if name != "post_quit"}


class Presses:
    """The `on_press` callback: records each action and lets a test wait for them."""

    def __init__(self, error: Exception | None = None) -> None:
        self.actions: list[str] = []
        self.error = error
        self._seen = threading.Condition()

    def __call__(self, action: str) -> None:
        with self._seen:
            self.actions.append(action)
            self._seen.notify_all()
        if self.error is not None:
            raise self.error

    def wait_for(self, count: int) -> list[str]:
        with self._seen:
            assert self._seen.wait_for(lambda: len(self.actions) >= count, WAIT_S)
            return list(self.actions)


# --- the keys ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "code"),
    [("A", 0x41), ("C", 0x43), ("Z", 0x5A), ("0", 0x30), ("9", 0x39), ("F1", 0x70), ("F12", 0x7B)],
)
def test_a_key_has_its_windows_virtual_key_code(key, code):
    assert virtual_key(key) == code


def test_every_key_has_its_own_virtual_key_code():
    codes = [virtual_key(key) for key in KEYS]

    assert len(KEYS) == 26 + 10 + 12
    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize("key", ["c", "F13", "F0", "AB", "", "+", "Esc"])
def test_a_key_that_is_not_a_letter_a_digit_or_f1_to_f12_is_refused(key):
    with pytest.raises(ValueError, match="key"):
        virtual_key(key)


def test_a_hotkey_names_its_combo():
    assert Hotkey("quiet", "Q").combo == "Ctrl+Alt+Q"
    assert Hotkey("talk", "F9").combo == "Ctrl+Alt+F9"


# --- the thread ----------------------------------------------------------------------------------


def run_thread(user32: FakeUser32, on_press=None, hotkey_list=DEFAULT_HOTKEYS) -> HotkeyThread:
    thread = HotkeyThread(hotkey_list, on_press or Presses(), user32=user32)
    thread.start()
    thread.registrations()  # waits until it is ready
    return thread


def test_each_hotkey_is_registered_with_ctrl_alt_and_no_repeat():
    user32 = FakeUser32()

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32) as thread:
        registrations = thread.registrations()

    modifiers = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
    assert user32.called("register_hotkey") == [
        (1, modifiers, 0x43),
        (2, modifiers, 0x58),
        (3, modifiers, 0x52),
        (4, modifiers, 0x51),
    ]
    assert registrations == tuple(Registration(hotkey, 0) for hotkey in DEFAULT_HOTKEYS)


def test_the_queue_is_made_before_any_hotkey_is_registered():
    user32 = FakeUser32()

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32):
        pass

    assert user32.calls[0][0] == "create_queue"


def test_a_pressed_hotkey_calls_on_press_with_its_action(caplog):
    caplog.set_level(logging.INFO, logger="waifu.hotkeys")
    user32 = FakeUser32()
    presses = Presses()

    with HotkeyThread(DEFAULT_HOTKEYS, presses, user32=user32) as thread:
        thread.registrations()
        user32.press(4)
        user32.press(2)
        assert presses.wait_for(2) == ["quiet", "stop"]

    assert "Hotkey Ctrl+Alt+Q (quiet)" in caplog.text


def test_messages_other_than_a_known_hotkey_are_ignored():
    user32 = FakeUser32()
    presses = Presses()

    with HotkeyThread(DEFAULT_HOTKEYS, presses, user32=user32) as thread:
        thread.registrations()
        user32.messages.put(Message(0x0113, 1))  # WM_TIMER
        user32.press(99)  # an id it never registered
        user32.press(1)
        assert presses.wait_for(1) == ["talk"]

    assert presses.actions == ["talk"]


def test_a_combo_another_program_holds_is_logged_once_and_the_others_still_work(caplog):
    caplog.set_level(logging.WARNING, logger="waifu.hotkeys")
    user32 = FakeUser32(errors={0x51: ERROR_HOTKEY_ALREADY_REGISTERED})
    presses = Presses()

    with HotkeyThread(DEFAULT_HOTKEYS, presses, user32=user32) as thread:
        registrations = thread.registrations()
        user32.press(1)
        assert presses.wait_for(1) == ["talk"]

    assert registrations[3] == Registration(Hotkey("quiet", "Q"), ERROR_HOTKEY_ALREADY_REGISTERED)
    assert [registration.registered for registration in registrations] == [
        True,
        True,
        True,
        False,
    ]
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1 and "Ctrl+Alt+Q is taken by another program" in warnings[0]
    assert user32.called("unregister_hotkey") == [(1,), (2,), (3,)]


def test_a_combo_refused_for_another_reason_names_the_windows_error(caplog):
    caplog.set_level(logging.WARNING, logger="waifu.hotkeys")
    user32 = FakeUser32(errors={0x43: 87})

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32) as thread:
        thread.registrations()

    assert "Could not register Ctrl+Alt+C (Windows error 87)" in caplog.text


def test_registering_waiting_and_unregistering_happen_on_one_thread_of_its_own():
    user32 = FakeUser32()

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32) as thread:
        thread.registrations()

    assert user32.called("unregister_hotkey") == [(1,), (2,), (3,), (4,)]
    (worker,) = user32.threads()
    assert worker != threading.get_ident()


def test_stop_posts_wm_quit_to_the_thread_that_made_the_queue_and_waits_for_it():
    user32 = FakeUser32()
    thread = run_thread(user32)

    thread.stop()

    assert user32.called("post_quit") == [(user32.queue_thread,)]
    assert not any(t.name == hotkeys.THREAD_NAME for t in threading.enumerate())


def test_the_thread_is_a_named_daemon():
    user32 = FakeUser32()
    thread = run_thread(user32)
    try:
        (worker,) = [t for t in threading.enumerate() if t.name == hotkeys.THREAD_NAME]
        assert worker.daemon
    finally:
        thread.stop()


def test_an_exception_from_on_press_is_logged_and_the_hotkeys_go_on(caplog):
    user32 = FakeUser32()
    presses = Presses(error=RuntimeError("broken"))

    with HotkeyThread(DEFAULT_HOTKEYS, presses, user32=user32) as thread:
        thread.registrations()
        user32.press(1)
        user32.press(3)
        assert presses.wait_for(2) == ["talk", "repeat"]

    assert "Ctrl+Alt+C (talk) failed" in caplog.text


def test_a_failing_message_queue_ends_the_loop_with_an_error_and_unregisters(caplog):
    user32 = FakeUser32()
    thread = run_thread(user32)

    user32.messages.put(OSError("GetMessageW failed (Windows error 1400)"))
    thread.stop()

    assert "Waiting for the global hotkeys failed" in caplog.text
    assert "Windows error 1400" in caplog.text
    assert user32.called("unregister_hotkey") == [(1,), (2,), (3,), (4,)]


class Lost:
    """The `on_lost` callback: records the combos still registered when it was called."""

    def __init__(self, user32: FakeUser32) -> None:
        self.user32 = user32
        self.unregistered_by_then: list[list[tuple[int, ...]]] = []
        self.called = threading.Event()

    def __call__(self) -> None:
        self.unregistered_by_then.append(self.user32.called("unregister_hotkey"))
        self.called.set()


def test_a_failing_message_queue_calls_on_lost_once_the_combos_are_unregistered():
    user32 = FakeUser32()
    lost = Lost(user32)
    thread = HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32, on_lost=lost)
    thread.start()
    thread.registrations()

    user32.messages.put(OSError("GetMessageW failed (Windows error 1400)"))
    assert lost.called.wait(WAIT_S)
    thread.stop()

    assert lost.unregistered_by_then == [[(1,), (2,), (3,), (4,)]]


def test_on_lost_is_never_called_when_the_thread_is_stopped():
    user32 = FakeUser32()
    lost = Lost(user32)

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32, on_lost=lost) as thread:
        thread.registrations()

    assert user32.called("unregister_hotkey") == [(1,), (2,), (3,), (4,)]
    assert not lost.called.is_set()


def test_on_lost_is_never_called_when_no_combo_was_registered():
    # The registrations already say it: every combo refused, or a queue that cannot be made.
    user32 = FakeUser32(errors={0x43: 1409, 0x58: 1409, 0x52: 1409, 0x51: 1409})
    lost = Lost(user32)

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32, on_lost=lost) as thread:
        thread.registrations()

    assert not lost.called.is_set()


def test_with_every_combo_refused_the_thread_ends_without_waiting():
    user32 = FakeUser32(errors={0x43: 1409, 0x58: 1409, 0x52: 1409, 0x51: 1409})

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32) as thread:
        assert not any(r.registered for r in thread.registrations())

    assert user32.called("get_message") == []
    assert user32.called("unregister_hotkey") == []


def test_a_queue_that_cannot_be_made_is_logged_and_registers_nothing(caplog):
    user32 = FakeUser32()

    def refuse() -> None:
        raise OSError("no queue")

    user32.create_queue = refuse

    with HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32) as thread:
        assert thread.registrations() == ()

    assert "The global hotkeys could not start" in caplog.text
    assert user32.called("register_hotkey") == []


def test_stop_before_the_thread_is_ready_gives_up_instead_of_hanging(monkeypatch, caplog):
    monkeypatch.setattr(hotkeys, "READY_TIMEOUT_S", 0.05)
    user32 = FakeUser32()
    user32.ready_to_queue.clear()  # the thread is stuck before its queue exists
    thread = HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32)
    thread.start()

    thread.stop()

    assert user32.called("post_quit") == []
    assert "never got ready" in caplog.text
    user32.ready_to_queue.set()  # let the left-behind thread finish
    user32.messages.put(_QUIT)


def test_stopping_a_thread_that_never_started_does_nothing():
    user32 = FakeUser32()

    HotkeyThread(DEFAULT_HOTKEYS, Presses(), user32=user32).stop()

    assert user32.calls == []


# --- the real user32 ------------------------------------------------------------------------------


class FakeFunction:
    """One Windows function: refuses to run undeclared and records the signature it ran with."""

    def __init__(self, signatures: dict, name: str, behaviour) -> None:
        self.signatures = signatures
        self.name = name
        self.behaviour = behaviour
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        assert self.argtypes is not None, f"{self.name} called without argtypes"
        assert self.restype is not None, f"{self.name} called without restype"
        self.signatures[self.name] = (self.argtypes, self.restype)
        return self.behaviour(*args)


@pytest.fixture
def fake_user32(monkeypatch):
    """A fake user32 behind ctypes.WinDLL; its calls are recorded."""
    signatures: dict = {}
    calls: list[tuple] = []
    loads: list = []

    def peek(msg, hwnd, low, high, remove):
        calls.append(("PeekMessageW", hwnd, low, high, remove))
        return 0

    def register(hwnd, hotkey_id, modifiers, vk):
        calls.append(("RegisterHotKey", hwnd, hotkey_id, modifiers, vk))
        if vk == 0x51:
            ctypes.set_last_error(ERROR_HOTKEY_ALREADY_REGISTERED)
            return 0
        return 1

    def unregister(hwnd, hotkey_id):
        calls.append(("UnregisterHotKey", hwnd, hotkey_id))
        return 1

    def get(msg, hwnd, low, high):
        calls.append(("GetMessageW", hwnd, low, high))
        msg._obj.message = WM_HOTKEY
        msg._obj.wParam = 3
        return 1

    def post(thread_id, message, wparam, lparam):
        calls.append(("PostThreadMessageW", thread_id, message, wparam, lparam))
        return 1

    library = SimpleNamespace(
        PeekMessageW=FakeFunction(signatures, "PeekMessageW", peek),
        RegisterHotKey=FakeFunction(signatures, "RegisterHotKey", register),
        UnregisterHotKey=FakeFunction(signatures, "UnregisterHotKey", unregister),
        GetMessageW=FakeFunction(signatures, "GetMessageW", get),
        PostThreadMessageW=FakeFunction(signatures, "PostThreadMessageW", post),
    )

    def fake_windll(name, **kwargs):
        loads.append((name, kwargs))
        assert name == "user32"
        return library

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    return SimpleNamespace(signatures=signatures, calls=calls, loads=loads)


needs_last_error = pytest.mark.skipif(
    not hasattr(ctypes, "set_last_error"), reason="ctypes keeps Windows' last error only on Windows"
)


@needs_last_error
def test_the_real_user32_is_called_with_the_documented_signatures(fake_user32):
    user32 = Win32User32()

    user32.create_queue()
    assert user32.register_hotkey(1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x43) == 0
    assert user32.get_message() == Message(WM_HOTKEY, 3)
    assert user32.unregister_hotkey(1) is True
    assert user32.post_quit(4242) is True

    msg = ctypes.POINTER(wintypes.MSG)
    assert fake_user32.signatures == {
        "PeekMessageW": (
            [msg, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT],
            wintypes.BOOL,
        ),
        "RegisterHotKey": (
            [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT],
            wintypes.BOOL,
        ),
        "UnregisterHotKey": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        "GetMessageW": ([msg, wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        "PostThreadMessageW": (
            [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
            wintypes.BOOL,
        ),
    }
    assert fake_user32.calls == [
        ("PeekMessageW", None, 0, 0, PM_NOREMOVE),
        ("RegisterHotKey", None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x43),
        ("GetMessageW", None, 0, 0),
        ("UnregisterHotKey", None, 1),
        ("PostThreadMessageW", 4242, WM_QUIT, 0, 0),
    ]
    assert all(arguments == {"use_last_error": True} for _, arguments in fake_user32.loads)


@needs_last_error
def test_a_refused_registration_gives_windows_last_error(fake_user32):
    assert Win32User32().register_hotkey(4, MOD_CONTROL | MOD_ALT, 0x51) == 1409


@needs_last_error
def test_wm_quit_ends_the_messages_and_a_failure_raises(fake_user32, monkeypatch):
    user32 = Win32User32()
    results = iter([0, -1])

    def get(msg, hwnd, low, high):
        result = next(results)
        if result == -1:
            ctypes.set_last_error(1400)
        return result

    library = ctypes.WinDLL("user32")
    library.GetMessageW.behaviour = get

    assert user32.get_message() is None
    with pytest.raises(OSError, match="Windows error 1400"):
        user32.get_message()


def test_user32_is_never_loaded_before_a_call(fake_user32):
    Win32User32()

    assert fake_user32.loads == []


# --- the status file -----------------------------------------------------------------------------


def test_the_status_is_written_and_read_back(tmp_path):
    path = tmp_path / "data" / "hotkeys.json"
    registrations = (
        Registration(Hotkey("talk", "C"), 0),
        Registration(Hotkey("quiet", "F9"), ERROR_HOTKEY_ALREADY_REGISTERED),
    )

    write_status(path, 4242, registrations)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "pid": 4242,
        "hotkeys": [
            {"action": "talk", "key": "C", "error": 0},
            {"action": "quiet", "key": "F9", "error": 1409},
        ],
    }
    assert read_status(path) == HotkeyStatus(4242, registrations)


def test_a_missing_status_reads_as_none(tmp_path):
    assert read_status(tmp_path / "hotkeys.json") is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"version": 2, "pid": 1, "hotkeys": []}',
        '{"version": 1, "pid": "1", "hotkeys": []}',
        '{"version": 1, "pid": 1, "hotkeys": [{"action": "dance", "key": "C", "error": 0}]}',
        '{"version": 1, "pid": 1, "hotkeys": [{"action": "talk", "key": "c", "error": 0}]}',
        '{"version": 1, "pid": 1, "hotkeys": [{"action": "talk", "key": "C", "error": -1}]}',
        '{"version": 1, "pid": 1, "hotkeys": [{"action": "talk", "key": "C"}]}',
    ],
)
def test_a_status_that_is_not_one_reads_as_none(tmp_path, text):
    path = tmp_path / "hotkeys.json"
    path.write_text(text, encoding="utf-8")

    assert read_status(path) is None
