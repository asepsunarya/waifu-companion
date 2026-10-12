import json
import logging
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest

from waifu.config import GachaRemindersConfig
from waifu.gacha.hoyolab import CheckinResult, GachaError, GameStatus, WeeklyTask
from waifu.gacha.reminders import (
    Notice,
    ReminderState,
    checkin_notice,
    due_notices,
    problem_notice,
)

WIB = timezone(timedelta(hours=7))
ASIA = 8
RULES = GachaRemindersConfig()  # 60 minutes, 20:00, weekly and expeditions on


def wib(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=WIB)


MONDAY_NOON = wib(12, 12)  # 2026-10-12 is a Monday

HSR = GameStatus(
    game="hsr",
    stamina=180,
    stamina_max=300,
    stamina_full_in_s=6 * 3600,
    daily_current=500,
    daily_max=500,
    daily_done=True,
    weekly=(WeeklyTask("Echo of War", 3, 3), WeeklyTask("Simulated Universe", 14000, 14000)),
    expeditions_total=4,
    expeditions_finished=2,
    checked_in=True,
)


def evaluate(status: GameStatus, now: datetime, state: ReminderState, rules=RULES) -> list[Notice]:
    return due_notices({status.game: status}, now, state, rules, ASIA)


def kinds(notices: list[Notice]) -> list[str]:
    return [notice.kind for notice in notices]


# --- stamina -------------------------------------------------------------------------------------


def test_stamina_close_to_full_is_mentioned_once():
    state = ReminderState()
    near = replace(HSR, stamina=295, stamina_full_in_s=30 * 60)

    [notice] = evaluate(near, MONDAY_NOON, state)
    again = evaluate(replace(near, stamina=297, stamina_full_in_s=18 * 60), MONDAY_NOON, state)

    assert notice == Notice(
        "hsr",
        "stamina",
        "Master, Trailblaze Power di HSR sudah 295 dari 300, penuh sekitar 30 menit lagi. "
        "Jangan sampai terbuang ya.",
        "surprised",
    )
    assert again == []


def test_stamina_is_mentioned_again_after_master_spent_it():
    state = ReminderState()
    near = replace(HSR, stamina=295, stamina_full_in_s=30 * 60)
    evaluate(near, MONDAY_NOON, state)

    assert evaluate(replace(HSR, stamina_full_in_s=5 * 3600), MONDAY_NOON, state) == []
    assert kinds(evaluate(near, MONDAY_NOON, state)) == ["stamina"]


def test_full_stamina_says_so():
    full = replace(HSR, stamina=300, stamina_full_in_s=0)

    [notice] = evaluate(full, MONDAY_NOON, ReminderState())

    assert notice.text.startswith("Master, Trailblaze Power di HSR sudah penuh, 300 dari 300.")


def test_stamina_far_from_full_is_left_alone():
    assert evaluate(HSR, MONDAY_NOON, ReminderState()) == []


def test_with_zero_minutes_only_full_stamina_is_mentioned():
    rules = GachaRemindersConfig(stamina_minutes=0)
    near = replace(HSR, stamina=299, stamina_full_in_s=6 * 60)

    assert evaluate(near, MONDAY_NOON, ReminderState(), rules) == []
    full = replace(HSR, stamina=300, stamina_full_in_s=0)
    assert kinds(evaluate(full, MONDAY_NOON, ReminderState(), rules)) == ["stamina"]


def test_a_long_wait_is_said_in_hours_and_minutes():
    rules = GachaRemindersConfig(stamina_minutes=180)
    near = replace(HSR, stamina=280, stamina_full_in_s=2 * 3600 + 5 * 60)

    [notice] = evaluate(near, MONDAY_NOON, ReminderState(), rules)

    assert "penuh sekitar 2 jam 5 menit lagi" in notice.text


# --- dailies -------------------------------------------------------------------------------------


UNFINISHED = replace(HSR, daily_current=300, daily_done=False)


def test_unfinished_dailies_are_mentioned_from_the_reminder_time_once_a_game_day():
    state = ReminderState()

    assert evaluate(UNFINISHED, wib(12, 19, 59), state) == []
    [notice] = evaluate(UNFINISHED, wib(12, 20, 0), state)
    assert evaluate(UNFINISHED, wib(12, 23, 0), state) == []
    assert evaluate(UNFINISHED, wib(13, 2, 0), state) == []  # still the same game day
    assert evaluate(UNFINISHED, wib(13, 20, 0), state)[0].kind == "daily"

    assert notice.text == (
        "Master, Daily Training HSR belum selesai, baru 300 dari 500. Reset jam 03:00 nanti."
    )
    assert notice.emotion == "surprised"


def test_finished_dailies_are_not_mentioned():
    assert evaluate(HSR, wib(12, 21, 0), ReminderState()) == []


def test_dailies_done_but_not_claimed_say_to_collect_the_reward():
    genshin = replace(HSR, game="genshin", daily_current=4, daily_max=4, daily_done=False)

    [notice] = evaluate(genshin, wib(12, 21, 0), ReminderState())

    assert notice.text == (
        "Master, Daily Commissions Genshin sudah selesai, tinggal ambil hadiahnya. "
        "Reset jam 03:00 nanti."
    )


def test_a_reminder_time_after_midnight_still_belongs_to_that_game_day():
    rules = GachaRemindersConfig(daily_time=time(1, 0))
    state = ReminderState()

    assert evaluate(UNFINISHED, wib(12, 22, 0), state, rules) == []
    assert kinds(evaluate(UNFINISHED, wib(13, 1, 30), state, rules)) == ["daily"]


# --- weeklies ------------------------------------------------------------------------------------


LEFT_THIS_WEEK = replace(
    HSR,
    weekly=(WeeklyTask("Echo of War", 1, 3), WeeklyTask("Simulated Universe", 14000, 14000)),
)
SUNDAY_EVENING = wib(18, 20, 30)


def test_weeklies_left_are_mentioned_on_the_last_evening_of_the_week_once():
    state = ReminderState()

    assert evaluate(LEFT_THIS_WEEK, wib(17, 21, 0), state) == []  # Saturday
    [notice] = evaluate(LEFT_THIS_WEEK, SUNDAY_EVENING, state)
    assert evaluate(LEFT_THIS_WEEK, wib(18, 23, 0), state) == []

    assert notice.kind == "weekly"
    assert notice.text == (
        "Master, reset mingguan HSR jam 03:00 nanti. Masih tersisa Echo of War 1 dari 3."
    )


def test_finished_weeklies_or_weekly_reminders_off_say_nothing():
    assert evaluate(HSR, SUNDAY_EVENING, ReminderState()) == []
    rules = GachaRemindersConfig(weekly=False)
    assert evaluate(LEFT_THIS_WEEK, SUNDAY_EVENING, ReminderState(), rules) == []


# --- expeditions ---------------------------------------------------------------------------------


def test_finished_expeditions_are_mentioned_once_per_batch():
    state = ReminderState()
    done = replace(HSR, expeditions_finished=4)

    [notice] = evaluate(done, MONDAY_NOON, state)
    assert evaluate(done, MONDAY_NOON, state) == []
    assert evaluate(HSR, MONDAY_NOON, state) == []  # Master sent a new batch
    assert kinds(evaluate(done, MONDAY_NOON, state)) == ["expeditions"]

    assert notice.text == "Master, semua ekspedisi HSR sudah selesai. Ambil hadiahnya yuk."


def test_no_expeditions_or_expedition_reminders_off_say_nothing():
    none_sent = replace(HSR, expeditions_total=0, expeditions_finished=0)
    assert evaluate(none_sent, MONDAY_NOON, ReminderState()) == []
    rules = GachaRemindersConfig(expeditions=False)
    done = replace(HSR, expeditions_finished=4)
    assert evaluate(done, MONDAY_NOON, ReminderState(), rules) == []


def test_several_games_are_checked_together():
    near = replace(HSR, stamina=295, stamina_full_in_s=30 * 60)
    zzz = replace(HSR, game="zzz", stamina=240, stamina_max=240, stamina_full_in_s=0)

    notices = due_notices({"hsr": near, "zzz": zzz}, MONDAY_NOON, ReminderState(), RULES, ASIA)

    assert [(n.game, n.kind) for n in notices] == [("hsr", "stamina"), ("zzz", "stamina")]
    assert "Battery Charge di ZZZ" in notices[1].text


# --- problems and check-in -----------------------------------------------------------------------


def test_a_cookie_problem_is_told_once_a_day_for_all_games():
    state = ReminderState()
    error = GachaError("cookie", "Cookie HoYoLAB tidak berlaku lagi.")

    first = problem_notice("hsr", error, MONDAY_NOON, state, ASIA)
    second = problem_notice("zzz", error, wib(12, 13), state, ASIA)
    next_day = problem_notice("hsr", error, wib(13, 12), state, ASIA)

    assert first == Notice(None, "problem", "Master, Cookie HoYoLAB tidak berlaku lagi.", "sad")
    assert second is None
    assert next_day is not None


def test_a_game_specific_problem_is_told_once_a_day_per_game():
    state = ReminderState()
    error = GachaError("notes_off", "Real-Time Notes HSR di HoYoLAB belum aktif.")

    assert problem_notice("hsr", error, MONDAY_NOON, state, ASIA).game == "hsr"
    assert problem_notice("hsr", error, wib(12, 18), state, ASIA) is None
    assert problem_notice("genshin", error, wib(12, 18), state, ASIA) is not None


@pytest.mark.parametrize("kind", ["network", "rate_limit", "other"])
def test_passing_problems_are_only_logged(kind):
    assert problem_notice("hsr", GachaError(kind, "x"), MONDAY_NOON, ReminderState(), ASIA) is None


def test_the_checkin_notice_names_what_she_claimed():
    results = [
        CheckinResult("hsr", True, "20 Stellar Jade"),
        CheckinResult("genshin", False, None),
        CheckinResult("zzz", True, "5000 Dennies"),
    ]

    notice = checkin_notice(results)

    assert notice == Notice(
        None,
        "checkin",
        "Check-in harian HoYoLAB sudah aku ambil: HSR dapat 20 Stellar Jade dan ZZZ dapat "
        "5000 Dennies.",
        "happy",
    )


def test_nothing_new_claimed_means_no_checkin_notice():
    assert checkin_notice([CheckinResult("hsr", False, None)]) is None
    assert checkin_notice([]) is None


# --- the state file ------------------------------------------------------------------------------


def test_the_state_survives_a_restart(tmp_path):
    path = tmp_path / "gacha" / "reminders.json"
    state = ReminderState()
    evaluate(UNFINISHED, wib(12, 20, 0), state)
    state.save(path, today=date(2026, 10, 12))

    restored = ReminderState.load(path)

    assert evaluate(UNFINISHED, wib(12, 21, 0), restored) == []


def test_old_entries_are_dropped_when_saving(tmp_path):
    path = tmp_path / "reminders.json"
    state = ReminderState()
    evaluate(UNFINISHED, wib(12, 20, 0), state, RULES)
    state.save(path, today=date(2026, 10, 12) + timedelta(days=15))

    assert json.loads(path.read_text(encoding="utf-8"))["fired"] == {}


def test_a_missing_or_broken_state_file_starts_fresh(tmp_path, caplog):
    assert ReminderState.load(tmp_path / "none.json").fired == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert ReminderState.load(broken).fired == {}

    assert "broken.json" in caplog.text
