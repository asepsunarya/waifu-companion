"""What each global hotkey does: talk and stop through the avatar's interaction handler, repeat
and the listen pause on a worker of their own, never blocking the hotkey thread.

The handler is the real one over a fake voice; the pause is a real file under tmp_path."""

from __future__ import annotations

import logging
import random
import threading
from datetime import datetime

import pytest

from waifu import hotkey_actions
from waifu.avatar.interactions import InteractionHandler
from waifu.config import AvatarInteractionsConfig
from waifu.hotkey_actions import QUEUE_SIZE, HotkeyActions
from waifu.listen_pause import ListenPause

NOW = datetime(2026, 10, 24, 14, 30)
WAIT_S = 5.0  # an upper bound for events between threads, never a sleep
LISTEN_LINES = AvatarInteractionsConfig().lines["listen"]


class Voice:
    """Stands in for the Assistant: records what it was asked, with the thread that asked."""

    def __init__(self) -> None:
        self.speaking = False
        self.busy = False
        self.events: list[str] = []
        self.said: list[tuple[str, bool]] = []
        self.cuts: list[threading.Thread] = []
        self.repeat_gate = threading.Event()  # set: a repeat ends at once
        self.repeat_gate.set()
        self.repeat_error: Exception | None = None
        self._changed = threading.Condition()

    def _note(self, event: str) -> None:
        with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def interrupt(self) -> bool:
        self.cuts.append(threading.current_thread())
        was_speaking, self.speaking = self.speaking, False
        return was_speaking

    def listen(self) -> None:
        self._note("listen")

    def is_busy(self) -> bool:
        return self.busy

    def reaction(self, text: str, emotion: str) -> None:
        self._note(f"line {text}")

    def repeat(self) -> None:
        self._note("repeat")
        self.repeat_gate.wait(WAIT_S)
        if self.repeat_error is not None:
            raise self.repeat_error

    def speak(self, text: str, emotion: str = "neutral", *, followup: bool = True) -> None:
        self.said.append((text, followup))
        self._note(f"speak {text}")

    def wait_for(self, count: int) -> list[str]:
        with self._changed:
            assert self._changed.wait_for(lambda: len(self.events) >= count, WAIT_S)
            return list(self.events)


class Rig:
    def __init__(self, tmp_path, *, with_pause: bool = True) -> None:
        self.voice = Voice()
        self.reactions = InteractionHandler(
            AvatarInteractionsConfig(enabled=False),  # the hotkeys work all the same
            speak=self.voice.reaction,
            listen=self.voice.listen,
            is_busy=self.voice.is_busy,
            interrupt=self.voice.interrupt,
            rng=random.Random(7),
        )
        self.pause = (
            ListenPause(tmp_path / "data" / "listen_pause.json", hours=2, now=lambda: NOW)
            if with_pause
            else None
        )
        self.actions = HotkeyActions(
            reactions=self.reactions,
            interrupt=self.voice.interrupt,
            repeat=self.voice.repeat,
            speak=self.voice.speak,
            pause=self.pause,
            owner_call="Master",
        )

    def __enter__(self) -> Rig:
        self.reactions.start()
        self.actions.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.voice.repeat_gate.set()
        self.actions.stop()
        self.reactions.stop()


def test_talk_listens_and_says_a_listen_line_like_a_double_click(tmp_path):
    with Rig(tmp_path) as rig:
        rig.actions.press("talk")
        events = rig.voice.wait_for(2)

    assert events[0] == "listen"
    assert events[1].removeprefix("line ") in [
        line.replace("{owner}", "Master") for line in LISTEN_LINES
    ]


def test_talk_while_she_speaks_cuts_her_at_once_and_she_only_listens(tmp_path):
    with Rig(tmp_path) as rig:
        rig.voice.speaking = rig.voice.busy = True

        rig.actions.press("talk")
        cut_by_the_caller = list(rig.voice.cuts)
        events = rig.voice.wait_for(1)

    assert cut_by_the_caller == [threading.current_thread()]
    assert events == ["listen"]


def test_stop_cuts_her_at_once_on_the_calling_thread_and_she_listens(tmp_path):
    with Rig(tmp_path) as rig:
        rig.voice.speaking = rig.voice.busy = True

        rig.actions.press("stop")
        cut_by_the_caller = list(rig.voice.cuts)
        events = rig.voice.wait_for(1)

    assert cut_by_the_caller == [threading.current_thread()]
    assert events == ["listen"]


def test_stop_while_she_says_nothing_does_nothing(tmp_path):
    with Rig(tmp_path) as rig:
        rig.actions.press("stop")

    assert len(rig.voice.cuts) == 1
    assert rig.voice.events == []


def test_stop_while_the_pause_holds_only_cuts_her_off(tmp_path, caplog):
    with Rig(tmp_path) as rig:
        rig.pause.start()
        rig.voice.speaking = True

        with caplog.at_level(logging.INFO, logger="waifu.hotkey_actions"):
            rig.actions.press("stop")

    assert rig.voice.speaking is False
    assert rig.voice.events == []
    assert rig.pause.paused()
    assert "the listen pause holds, so she does not listen" in caplog.text


def test_repeat_runs_on_the_worker_and_never_holds_up_the_hotkey_thread(tmp_path):
    with Rig(tmp_path) as rig:
        rig.voice.repeat_gate.clear()  # the repeat takes as long as her words

        rig.actions.press("repeat")
        assert rig.voice.wait_for(1) == ["repeat"]
        rig.voice.speaking = True
        rig.actions.press("stop")  # while the repeat still speaks
        cut = list(rig.voice.cuts)

    assert cut == [threading.current_thread()]


def test_quiet_starts_the_pause_and_says_until_when_without_a_follow_up(tmp_path):
    with Rig(tmp_path) as rig:
        rig.actions.press("quiet")
        rig.voice.wait_for(1)

        assert rig.pause.paused()

    assert rig.voice.said == [
        (
            "Oke Master, aku tidak mendengarkan dulu sampai jam 16.30. Tekan tombol yang sama "
            "kalau mau ngobrol lagi.",
            False,
        )
    ]


def test_quiet_again_ends_the_pause_and_says_so_as_a_reply(tmp_path):
    with Rig(tmp_path) as rig:
        rig.pause.start()

        rig.actions.press("quiet")
        rig.voice.wait_for(1)

        assert not rig.pause.paused()

    assert rig.voice.said == [("Oke Master, aku dengar lagi.", True)]


def test_a_pause_whose_file_cannot_be_written_is_said_and_logged(tmp_path, caplog):
    (tmp_path / "data").write_text("a file where the folder should be", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="waifu.hotkey_actions"), Rig(tmp_path) as rig:
        rig.actions.press("quiet")
        rig.voice.wait_for(1)

    assert rig.voice.said == [
        ("Maaf Master, aku belum bisa berhenti mendengarkan. Coba lagi sebentar.", False)
    ]
    assert "Could not start the listen pause" in caplog.text


def test_a_pause_that_cannot_be_ended_is_said_and_holds(tmp_path, monkeypatch):
    with Rig(tmp_path) as rig:
        rig.pause.start()

        def locked() -> bool:
            raise PermissionError(32, "The file is used by another process")

        monkeypatch.setattr(rig.pause, "end", locked)
        rig.actions.press("quiet")
        rig.voice.wait_for(1)

    assert rig.voice.said == [
        ("Maaf Master, aku belum bisa mendengarkan lagi. Coba lagi sebentar.", False)
    ]
    assert rig.pause.paused()


def test_quiet_without_a_pause_is_logged_and_ignored(tmp_path, caplog):
    with caplog.at_level(logging.WARNING), Rig(tmp_path, with_pause=False) as rig:
        rig.actions.press("quiet")

    assert rig.voice.said == []
    assert "no listen pause" in caplog.text


def test_a_failing_action_is_logged_and_the_worker_goes_on(tmp_path, caplog):
    with Rig(tmp_path) as rig:
        rig.voice.repeat_error = RuntimeError("broken voice")

        rig.actions.press("repeat")
        rig.actions.press("quiet")
        rig.voice.wait_for(2)

    assert "The hotkey action repeat failed" in caplog.text
    assert rig.pause.paused()


def test_a_full_queue_drops_a_press_with_a_warning(tmp_path, caplog):
    with Rig(tmp_path) as rig:
        rig.voice.repeat_gate.clear()
        rig.actions.press("repeat")
        rig.voice.wait_for(1)  # the worker is busy with the first

        with caplog.at_level(logging.WARNING, logger="waifu.hotkey_actions"):
            for _ in range(QUEUE_SIZE + 1):
                rig.actions.press("repeat")

    assert caplog.text.count("dropping repeat") == 1


def test_an_unknown_action_is_logged_and_ignored(tmp_path, caplog):
    with caplog.at_level(logging.WARNING), Rig(tmp_path) as rig:
        rig.actions.press("dance")

    assert rig.voice.events == [] and rig.voice.cuts == []
    assert "Ignoring an unknown hotkey action 'dance'" in caplog.text


def test_after_stop_a_press_does_nothing(tmp_path):
    rig = Rig(tmp_path)
    with rig:
        pass
    rig.voice.speaking = True

    for action in ("talk", "stop", "repeat", "quiet"):
        rig.actions.press(action)

    assert rig.voice.cuts == [] and rig.voice.events == []
    assert not rig.pause.paused()


def test_the_worker_is_a_named_daemon_that_stops(tmp_path):
    with Rig(tmp_path):
        (worker,) = [t for t in threading.enumerate() if t.name == hotkey_actions.WORKER_NAME]
        assert worker.daemon

    assert not worker.is_alive()


def test_starting_twice_runs_one_worker(tmp_path):
    with Rig(tmp_path) as rig:
        rig.actions.start()
        workers = [t for t in threading.enumerate() if t.name == hotkey_actions.WORKER_NAME]

    assert len(workers) == 1


@pytest.mark.parametrize("action", ["repeat", "quiet"])
def test_repeat_and_quiet_never_run_on_the_calling_thread(tmp_path, action):
    threads: list[threading.Thread] = []
    done = threading.Event()

    def record(*args: object, **kwargs: object) -> None:
        threads.append(threading.current_thread())
        done.set()

    rig = Rig(tmp_path)
    actions = HotkeyActions(
        reactions=rig.reactions,
        interrupt=rig.voice.interrupt,
        repeat=record,
        speak=record,
        pause=rig.pause,
    )
    with actions:
        actions.press(action)
        assert done.wait(WAIT_S)

    assert len(threads) == 1 and threads[0] is not threading.current_thread()
