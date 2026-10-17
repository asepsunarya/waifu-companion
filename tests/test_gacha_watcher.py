import json
import logging
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from waifu.config import GachaConfig
from waifu.gacha.hoyolab import CheckinResult, GachaError, GameStatus, WeeklyTask
from waifu.gacha.watcher import GachaWatcher

WIB = timezone(timedelta(hours=7))

QUIET = GameStatus(
    game="hsr",
    stamina=100,
    stamina_max=300,
    stamina_full_in_s=8 * 3600,
    daily_current=500,
    daily_max=500,
    daily_done=True,
    weekly=(WeeklyTask("Echo of War", 3, 3),),
    expeditions_total=4,
    expeditions_finished=1,
    checked_in=True,
)
NEAR_FULL = replace(QUIET, stamina=295, stamina_full_in_s=30 * 60)


class FakeHoyolab:
    def __init__(self, statuses=None, checkins=None):
        self.statuses = statuses or {"hsr": QUIET}
        self.checkins = checkins or {}
        self.status_calls = 0
        self.checkin_calls: list[str] = []

    def status(self, games):
        self.status_calls += 1
        return {game: self.statuses[game] for game in games}

    def checkin(self, game_id):
        self.checkin_calls.append(game_id)
        result = self.checkins.get(game_id, CheckinResult(game_id, False, None))
        if isinstance(result, BaseException):
            raise result
        return result


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []

    def send(self, title, message, *, tags=()):
        self.sent.append((title, message, tuple(tags)))
        return True


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Announced(list):
    """Stands in for her voice: records each announcement as (text, emotion), and in
    `expiries` until when each may be said."""

    def __init__(self) -> None:
        super().__init__()
        self.expiries: list[datetime | None] = []

    def __call__(self, text: str, emotion: str, *, expires_at: datetime | None) -> None:
        self.append((text, emotion))
        self.expiries.append(expires_at)


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 12, 12, 0, tzinfo=WIB))


def make_watcher(tmp_path, hoyolab, clock, *, games=("hsr",), **config) -> tuple:
    notifier = FakeNotifier()
    announced = Announced()
    gacha = GachaConfig(enabled=True, games=list(games), data_dir=tmp_path / "gacha", **config)
    watcher = GachaWatcher(
        hoyolab,
        gacha,
        notifier=notifier,
        announce=announced,
        title="Cyrene",
        now=clock,
    )
    return watcher, notifier, announced


def test_a_reminder_is_pushed_and_announced_once(tmp_path, clock):
    watcher, notifier, announced = make_watcher(tmp_path, FakeHoyolab({"hsr": NEAR_FULL}), clock)

    notices = watcher.poll_once()
    watcher.poll_once()

    text = notices[0].text
    assert notifier.sent == [("Cyrene", text, ("video_game",))]
    assert announced == [(text, "surprised")]


def test_several_notices_are_one_push_and_said_in_order_with_the_strongest_emotion(tmp_path, clock):
    statuses = {
        "hsr": NEAR_FULL,
        "zzz": GachaError("captcha", "HoYoLAB minta verifikasi captcha ZZZ."),
    }
    hoyolab = FakeHoyolab(statuses, {"hsr": CheckinResult("hsr", True, "20 Stellar Jade")})
    watcher, notifier, announced = make_watcher(tmp_path, hoyolab, clock, games=("hsr", "zzz"))

    notices = watcher.poll_once()

    assert [n.kind for n in notices] == ["stamina", "problem", "checkin"]
    [(_, message, _)] = notifier.sent
    assert message.split("\n") == [n.text for n in notices]
    # The reminder, which may go out of date, apart from the problem and the check-in.
    assert " ".join(text for text, _ in announced) == " ".join(n.text for n in notices)
    assert [emotion for _, emotion in announced] == ["sad", "sad"]


def test_a_reminder_about_a_game_s_state_may_be_said_only_until_the_next_poll(tmp_path, clock):
    # Held through a game (or a focus block), it would tell a state play has likely changed.
    clock.now = datetime(2026, 10, 12, 20, 30, tzinfo=WIB)  # evening, dailies still open
    open_dailies = replace(NEAR_FULL, daily_current=300, daily_done=False)
    watcher, _, announced = make_watcher(tmp_path, FakeHoyolab({"hsr": open_dailies}), clock)

    watcher.poll_once()

    assert len(announced) == 1
    assert announced.expiries == [clock.now + timedelta(minutes=15)]  # poll_minutes


def test_a_problem_and_the_checkin_are_said_however_long_they_wait(tmp_path, clock):
    statuses = {"hsr": GachaError("captcha", "HoYoLAB minta verifikasi captcha HSR.")}
    hoyolab = FakeHoyolab(statuses, {"hsr": CheckinResult("hsr", True, "20 Stellar Jade")})
    watcher, _, announced = make_watcher(tmp_path, hoyolab, clock, poll_minutes=30)

    watcher.poll_once()

    assert len(announced) == 1
    assert announced.expiries == [None]


def test_reminders_and_problems_call_the_owner_what_she_calls_them(tmp_path, clock):
    statuses = {"hsr": NEAR_FULL, "zzz": GachaError("captcha", "HoYoLAB minta captcha ZZZ.")}
    checkins = {"hsr": GachaError("captcha", "HoYoLAB minta captcha untuk check-in HSR.")}
    announced = Announced()
    watcher = GachaWatcher(
        FakeHoyolab(statuses, checkins),
        GachaConfig(enabled=True, games=["hsr", "zzz"], data_dir=tmp_path / "gacha"),
        notifier=None,
        announce=announced,
        owner_call="Sayang",
        now=clock,
    )

    notices = watcher.poll_once()

    assert [(notice.game, notice.kind) for notice in notices] == [
        ("hsr", "stamina"),
        ("zzz", "problem"),
        ("hsr", "problem"),
    ]
    assert all(notice.text.startswith("Sayang, ") for notice in notices)
    assert "Master" not in announced[0][0]


def test_nothing_to_tell_stays_quiet(tmp_path, clock):
    watcher, notifier, announced = make_watcher(tmp_path, FakeHoyolab(), clock)

    assert watcher.poll_once() == []
    assert (notifier.sent, announced) == ([], [])


def test_the_checkin_is_claimed_once_per_checkin_day(tmp_path, clock):
    hoyolab = FakeHoyolab(checkins={"hsr": CheckinResult("hsr", True, "20 Stellar Jade")})
    watcher, _, announced = make_watcher(tmp_path, hoyolab, clock)

    watcher.poll_once()
    watcher.poll_once()
    clock.now += timedelta(hours=12)  # 00:00 server time has passed
    watcher.poll_once()

    assert hoyolab.checkin_calls == ["hsr", "hsr"]
    assert announced[0] == (
        "Check-in harian HoYoLAB sudah aku ambil: HSR dapat 20 Stellar Jade.",
        "happy",
    )


def test_a_checkin_cut_off_by_the_network_is_tried_again_next_poll(tmp_path, clock):
    hoyolab = FakeHoyolab(
        {"hsr": QUIET, "genshin": replace(QUIET, game="genshin")},
        {
            "hsr": CheckinResult("hsr", True, "20 Stellar Jade"),
            "genshin": GachaError("network", "HoYoLAB tidak bisa dihubungi."),
        },
    )
    watcher, _, announced = make_watcher(tmp_path, hoyolab, clock, games=("hsr", "genshin"))

    watcher.poll_once()
    hoyolab.checkins = {
        "hsr": CheckinResult("hsr", False, None),
        "genshin": CheckinResult("genshin", True, "20 Primogem"),
    }
    watcher.poll_once()
    watcher.poll_once()

    assert hoyolab.checkin_calls == ["hsr", "genshin", "hsr", "genshin"]
    assert [text for text, _ in announced] == [
        "Check-in harian HoYoLAB sudah aku ambil: HSR dapat 20 Stellar Jade.",
        "Check-in harian HoYoLAB sudah aku ambil: Genshin dapat 20 Primogem.",
    ]


def test_a_checkin_that_needs_a_captcha_is_told_and_not_retried_today(tmp_path, clock):
    failure = GachaError("captcha", "HoYoLAB minta verifikasi captcha HSR.")
    hoyolab = FakeHoyolab(checkins={"hsr": failure})
    watcher, _, announced = make_watcher(tmp_path, hoyolab, clock)

    watcher.poll_once()
    watcher.poll_once()

    assert hoyolab.checkin_calls == ["hsr"]
    assert announced == [("Master, HoYoLAB minta verifikasi captcha HSR.", "sad")]


def test_checkin_turned_off_is_never_claimed(tmp_path, clock):
    hoyolab = FakeHoyolab()
    watcher, _, _ = make_watcher(tmp_path, hoyolab, clock, checkin=False)

    watcher.poll_once()

    assert hoyolab.checkin_calls == []


def test_a_daily_reminder_is_not_repeated_after_a_restart(tmp_path, clock):
    clock.now = datetime(2026, 10, 12, 20, 30, tzinfo=WIB)  # evening, dailies still open
    hoyolab = FakeHoyolab({"hsr": replace(QUIET, daily_current=300, daily_done=False)})
    first, _, told_first = make_watcher(tmp_path, hoyolab, clock)
    first.poll_once()

    second, notifier, announced = make_watcher(tmp_path, hoyolab, clock)
    second.poll_once()

    assert [emotion for _, emotion in told_first] == ["surprised"]
    assert (notifier.sent, announced) == ([], [])
    saved = json.loads((tmp_path / "gacha" / "reminders.json").read_text(encoding="utf-8"))
    assert "daily:hsr:2026-10-12" in saved["fired"]


def test_a_full_stamina_is_told_again_after_a_restart(tmp_path, clock):
    # Spending it between the runs would go unseen, so a new run tells it once more.
    hoyolab = FakeHoyolab({"hsr": NEAR_FULL})
    first, _, _ = make_watcher(tmp_path, hoyolab, clock)
    first.poll_once()

    second, _, announced = make_watcher(tmp_path, hoyolab, clock)
    second.poll_once()
    second.poll_once()

    assert len(announced) == 1


def test_without_a_notifier_or_voice_it_still_keeps_track(tmp_path, clock):
    gacha = GachaConfig(enabled=True, games=["hsr"], data_dir=tmp_path / "gacha")
    watcher = GachaWatcher(
        FakeHoyolab({"hsr": NEAR_FULL}), gacha, notifier=None, announce=None, now=clock
    )

    assert [n.kind for n in watcher.poll_once()] == ["stamina"]
    assert watcher.poll_once() == []


class BlockingHoyolab(FakeHoyolab):
    """Signals each poll, and fails the first one."""

    def __init__(self):
        super().__init__()
        self.polled = threading.Event()

    def status(self, games):
        self.polled.set()
        if self.status_calls == 0:
            self.status_calls += 1
            raise RuntimeError("HoYoLAB exploded")
        return super().status(games)


def test_the_thread_polls_at_once_and_stops_promptly(tmp_path, clock, caplog):
    hoyolab = BlockingHoyolab()
    watcher, _, _ = make_watcher(tmp_path, hoyolab, clock)

    with caplog.at_level(logging.ERROR), watcher:
        assert hoyolab.polled.wait(5)
    # Leaving the block stopped it although the next poll was minutes away.

    assert not any(thread.name == "gacha-watcher" for thread in threading.enumerate())
    assert "HoYoLAB exploded" in caplog.text
