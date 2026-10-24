import json
import logging
import threading
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from queue import SimpleQueue

import pytest

from waifu.config import GachaConfig, GachaRemindersConfig
from waifu.gacha.hoyolab import CheckinResult, GachaError, GameStatus, WeeklyTask
from waifu.gacha.starrail import HsrCalendar, HsrEvent
from waifu.gacha.watcher import GachaWatcher
from waifu.play_time import Session

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
    `expiries` until when each may be said: (always, once a game held it)."""

    def __init__(self) -> None:
        super().__init__()
        self.expiries: list[tuple[datetime | None, datetime | None]] = []

    def __call__(
        self,
        text: str,
        emotion: str,
        *,
        expires_at: datetime | None = None,
        game_expires_at: datetime | None = None,
    ) -> None:
        self.append((text, emotion))
        self.expiries.append((expires_at, game_expires_at))


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


def test_a_reminder_about_a_game_s_state_may_be_held_by_a_game_only_until_the_next_poll(
    tmp_path, clock
):
    # Held through a game, it would tell a state play has likely changed; a focus block alone
    # keeps it however long it lasts, as before game mode.
    clock.now = datetime(2026, 10, 12, 20, 30, tzinfo=WIB)  # evening, dailies still open
    open_dailies = replace(NEAR_FULL, daily_current=300, daily_done=False)
    watcher, _, announced = make_watcher(tmp_path, FakeHoyolab({"hsr": open_dailies}), clock)

    watcher.poll_once()

    assert len(announced) == 1
    assert announced.expiries == [(None, clock.now + timedelta(minutes=15))]  # poll_minutes


def test_a_problem_and_the_checkin_are_said_however_long_they_wait(tmp_path, clock):
    statuses = {"hsr": GachaError("captcha", "HoYoLAB minta verifikasi captcha HSR.")}
    hoyolab = FakeHoyolab(statuses, {"hsr": CheckinResult("hsr", True, "20 Stellar Jade")})
    watcher, _, announced = make_watcher(tmp_path, hoyolab, clock, poll_minutes=30)

    watcher.poll_once()

    assert len(announced) == 1
    assert announced.expiries == [(None, None)]


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


# --- the planner (v1.50) -------------------------------------------------------------------------


def wib(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=WIB)


MONDAY_EVENING = wib(12, 20, 0)
GIFT = HsrEvent("Gift of Odyssey", wib(10, 4), MONDAY_EVENING + timedelta(hours=20), 3, 7, False)
ENDING = HsrCalendar("3.7", (), (GIFT,), ())
NOTHING_ENDING = HsrCalendar("3.7", (), (), ())
GIFT_LINE = "Master, event Gift of Odyssey di HSR berakhir sekitar 20 jam lagi, baru 3 dari 7."


class FakeCalendars:
    """Stands in for the HoYoLAB calendar reads: each game's calendar, or the error it raises;
    a list gives one answer per read, its last one for every read after."""

    def __init__(self, calendars=None) -> None:
        self.calendars = calendars or {"hsr": ENDING}
        self.reads: list[str] = []

    def __call__(self, game: str):
        self.reads.append(game)
        answer = self.calendars[game]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Guests:
    def __init__(self, around: bool = False) -> None:
        self.around = around

    def __call__(self) -> bool:
        return self.around


def planner_watcher(
    tmp_path, clock, *, statuses=None, calendars=None, guests=None, closed=None, **config
) -> tuple:
    announced = Announced()
    hoyolab = FakeHoyolab(statuses or {"hsr": QUIET})
    gacha = GachaConfig(enabled=True, games=["hsr"], data_dir=tmp_path / "gacha", **config)
    watcher = GachaWatcher(
        hoyolab,
        gacha,
        notifier=None,
        announce=announced,
        now=clock,
        read_calendar=calendars,
        guest_mode=guests,
        closed_games=closed,
        morning=time(7, 0),
    )
    return watcher, announced, hoyolab


def test_the_evening_line_comes_from_one_calendar_read_a_game_day(tmp_path, clock):
    clock.now = MONDAY_EVENING
    calendars = FakeCalendars()
    watcher, announced, _ = planner_watcher(tmp_path, clock, calendars=calendars)

    notices = watcher.poll_once()
    clock.now += timedelta(minutes=15)
    later = watcher.poll_once()

    assert [(notice.kind, notice.text) for notice in notices] == [("planner", GIFT_LINE)]
    assert later == []
    assert calendars.reads == ["hsr"]
    # Held by a game, it may be said only until the next poll, like the dailies.
    assert announced.expiries == [(None, MONDAY_EVENING + timedelta(minutes=15))]


def test_no_calendar_is_read_before_the_evening(tmp_path, clock):
    calendars = FakeCalendars()
    watcher, _, _ = planner_watcher(tmp_path, clock, calendars=calendars)

    watcher.poll_once()  # noon

    assert calendars.reads == []


def test_a_calendar_with_nothing_ending_is_read_once_that_game_day(tmp_path, clock):
    clock.now = MONDAY_EVENING
    calendars = FakeCalendars({"hsr": NOTHING_ENDING})
    watcher, announced, _ = planner_watcher(tmp_path, clock, calendars=calendars)

    for _ in range(3):
        watcher.poll_once()
        clock.now += timedelta(minutes=15)
    clock.now = MONDAY_EVENING + timedelta(days=1)
    watcher.poll_once()

    assert calendars.reads == ["hsr", "hsr"]
    assert announced == []


def test_a_calendar_cut_off_by_the_network_is_read_again_at_the_next_poll(tmp_path, clock):
    clock.now = MONDAY_EVENING
    cut_off = GachaError("network", "HoYoLAB tidak bisa dihubungi.")
    calendars = FakeCalendars({"hsr": [cut_off, ENDING]})
    watcher, _, _ = planner_watcher(tmp_path, clock, calendars=calendars)

    first = watcher.poll_once()
    second = watcher.poll_once()

    assert first == []
    assert [notice.kind for notice in second] == ["planner"]
    assert calendars.reads == ["hsr", "hsr"]


def test_any_other_calendar_error_gives_up_that_game_day_and_is_logged_once(
    tmp_path, clock, caplog
):
    clock.now = MONDAY_EVENING
    private = GachaError("chronicle_private", "Battle Chronicle HSR disembunyikan.")
    tomorrow = replace(GIFT, ends_at=MONDAY_EVENING + timedelta(hours=40))
    calendars = FakeCalendars({"hsr": [private, HsrCalendar("3.7", (), (tomorrow,), ())]})
    watcher, announced, _ = planner_watcher(tmp_path, clock, calendars=calendars)

    with caplog.at_level(logging.WARNING, logger="waifu.gacha.watcher"):
        watcher.poll_once()
        watcher.poll_once()
    clock.now += timedelta(days=1)
    watcher.poll_once()

    assert calendars.reads == ["hsr", "hsr"]
    assert [record.getMessage() for record in caplog.records] == [
        "Could not read the hsr event calendar (chronicle_private); no planner line for it "
        "this game day"
    ]
    assert len(announced) == 1  # the next evening's line


def test_a_game_whose_status_failed_gets_no_calendar_read(tmp_path, clock):
    clock.now = MONDAY_EVENING
    calendars = FakeCalendars()
    statuses = {"hsr": GachaError("network", "HoYoLAB tidak bisa dihubungi.")}
    watcher, _, _ = planner_watcher(tmp_path, clock, statuses=statuses, calendars=calendars)

    watcher.poll_once()

    assert calendars.reads == []


def test_with_the_planner_off_no_calendar_is_read(tmp_path, clock):
    clock.now = MONDAY_EVENING
    calendars = FakeCalendars()
    rules = GachaRemindersConfig(planner=False)
    watcher, announced, _ = planner_watcher(tmp_path, clock, calendars=calendars, reminders=rules)

    watcher.poll_once()

    assert (calendars.reads, announced) == ([], [])


def test_while_guests_are_around_the_planner_reads_and_spends_nothing(tmp_path, clock):
    clock.now = MONDAY_EVENING
    calendars, guests = FakeCalendars(), Guests(around=True)
    watcher, announced, _ = planner_watcher(tmp_path, clock, calendars=calendars, guests=guests)

    watcher.poll_once()
    held = (list(calendars.reads), list(announced))
    guests.around = False
    clock.now += timedelta(hours=1)
    watcher.poll_once()

    assert held == ([], [])
    assert [text for text, _ in announced] == [GIFT_LINE.replace("20 jam", "19 jam")]


# --- the overnight line --------------------------------------------------------------------------

CLOSED = Session("hsr", wib(12, 22, 30), wib(12, 23, 40))
AFTER_CLOSING = wib(12, 23, 50)
FILLING = replace(QUIET, stamina=208, stamina_full_in_s=(4 * 60 + 20) * 60)  # full at 04:10
OVERNIGHT_LINE = "Master, Trailblaze Power di HSR penuh jam 04.10, sebelum kamu bangun."
FULL = replace(FILLING, stamina=300, stamina_full_in_s=0)


def closed_games(*sessions: Session) -> SimpleQueue:
    queue: SimpleQueue = SimpleQueue()
    for session in sessions:
        queue.put(session)
    return queue


def test_a_game_closed_late_tells_its_stamina_and_holds_the_full_line_until_the_morning(
    tmp_path, clock
):
    clock.now = AFTER_CLOSING
    watcher, _, hoyolab = planner_watcher(
        tmp_path, clock, statuses={"hsr": FILLING}, closed=closed_games(CLOSED)
    )

    told = watcher.poll_once()
    clock.now = wib(13, 3, 10)
    hoyolab.statuses = {"hsr": replace(FILLING, stamina=292, stamina_full_in_s=60 * 60)}
    almost = watcher.poll_once()
    clock.now = wib(13, 4, 15)
    hoyolab.statuses = {"hsr": FULL}
    full_at_night = watcher.poll_once()
    clock.now = wib(13, 7, 0)
    morning = watcher.poll_once()

    assert [(notice.kind, notice.text) for notice in told] == [("planner", OVERNIGHT_LINE)]
    assert (almost, full_at_night) == ([], [])
    assert [notice.kind for notice in morning] == ["stamina"]


def test_a_game_closed_late_with_its_stamina_full_within_the_hour_gets_the_stamina_line_alone(
    tmp_path, clock
):
    clock.now = AFTER_CLOSING
    soon = replace(FILLING, stamina=292, stamina_full_in_s=25 * 60)
    watcher, _, hoyolab = planner_watcher(
        tmp_path, clock, statuses={"hsr": soon}, closed=closed_games(CLOSED)
    )

    told = watcher.poll_once()
    clock.now = wib(13, 0, 20)
    hoyolab.statuses = {"hsr": FULL}
    watcher.poll_once()  # the occasion was told, still full: nothing new
    hoyolab.statuses = {"hsr": replace(FILLING, stamina_full_in_s=8 * 3600)}
    clock.now = wib(13, 1, 0)
    watcher.poll_once()  # spent
    hoyolab.statuses = {"hsr": FULL}
    clock.now = wib(13, 2, 0)
    full_again = watcher.poll_once()

    assert [notice.kind for notice in told] == ["stamina"]
    assert told[0].text.startswith("Master, Trailblaze Power di HSR sudah 292 dari 300, penuh")
    assert [notice.kind for notice in full_again] == ["stamina"]  # never held


def test_the_hold_survives_a_restart_in_the_night(tmp_path, clock):
    clock.now = AFTER_CLOSING
    first, _, _ = planner_watcher(
        tmp_path, clock, statuses={"hsr": FILLING}, closed=closed_games(CLOSED)
    )
    first.poll_once()

    clock.now = wib(13, 4, 15)
    second, _, _ = planner_watcher(tmp_path, clock, statuses={"hsr": FULL})

    assert second.poll_once() == []


def test_a_closed_game_waits_for_a_poll_that_reads_its_status(tmp_path, clock):
    clock.now = AFTER_CLOSING
    failing = {"hsr": GachaError("network", "HoYoLAB tidak bisa dihubungi.")}
    watcher, _, hoyolab = planner_watcher(
        tmp_path, clock, statuses=failing, closed=closed_games(CLOSED)
    )

    first = watcher.poll_once()
    hoyolab.statuses = {"hsr": FILLING}
    clock.now += timedelta(minutes=15)
    second = watcher.poll_once()

    assert first == []
    assert [notice.text for notice in second] == [OVERNIGHT_LINE.replace("04.10", "04.25")]


def test_a_game_closed_in_the_evening_or_not_watched_says_nothing(tmp_path, clock):
    clock.now = wib(12, 21, 10)
    evening = Session("hsr", wib(12, 19, 0), wib(12, 21, 0))
    zzz = Session("zzz", wib(12, 19, 0), wib(12, 23, 40))
    watcher, announced, _ = planner_watcher(
        tmp_path, clock, statuses={"hsr": FILLING}, closed=closed_games(evening, zzz)
    )

    watcher.poll_once()
    clock.now = AFTER_CLOSING
    watcher.poll_once()

    assert announced == []


def test_while_guests_are_around_a_closed_game_waits_for_them_to_leave(tmp_path, clock):
    clock.now = AFTER_CLOSING
    guests = Guests(around=True)
    watcher, announced, _ = planner_watcher(
        tmp_path, clock, statuses={"hsr": FILLING}, guests=guests, closed=closed_games(CLOSED)
    )

    watcher.poll_once()
    held = list(announced)
    guests.around = False
    clock.now += timedelta(minutes=15)
    watcher.poll_once()

    assert held == []
    assert [text for text, _ in announced] == [OVERNIGHT_LINE.replace("04.10", "04.25")]


def test_with_the_planner_off_a_closed_game_says_nothing(tmp_path, clock):
    clock.now = AFTER_CLOSING
    rules = GachaRemindersConfig(planner=False)
    watcher, announced, _ = planner_watcher(
        tmp_path, clock, statuses={"hsr": FILLING}, closed=closed_games(CLOSED), reminders=rules
    )

    watcher.poll_once()

    assert announced == []
