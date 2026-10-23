import logging
from datetime import datetime

import pytest

from waifu.brain.focus_tools import FOCUS_TIMER
from waifu.brain.japanese_tools import JAPANESE_LESSON
from waifu.brain.memory_tools import FORGET, REMEMBER
from waifu.brain.play_time_tools import PLAY_TIME
from waifu.brain.schedule_tools import CANCEL_SCHEDULE, LIST_SCHEDULES, RUN_ROUTINE, SCHEDULE
from waifu.brain.screen_tools import LOOK_AT_SCREEN
from waifu.brain.todo_tools import (
    ADD_TASK,
    COMPLETE_TASK,
    EDIT_TASK,
    LIST_TASKS,
    REMOVE_TASK,
    REOPEN_TASK,
)
from waifu.brain.tools import CONTROL_DEVICE, LIST_DEVICES
from waifu.brain.usage_tools import API_USAGE
from waifu.briefing import DAILY_BRIEFING
from waifu.finance.values import FINANCE_TOOLS
from waifu.gacha.code_tools import REDEEM_CODES, REDEEM_CODES_LIST
from waifu.gacha.game_data_tools import GAME_CHARACTERS, GAME_ENDGAME, GAME_EVENTS
from waifu.gacha.income_tools import GACHA_INCOME
from waifu.gacha.tools import GACHA_HISTORY, GAME_STATUS
from waifu.gacha.weekly import WEEKLY_REPORT
from waifu.guest import GUEST_REFUSAL, PRIVATE_TOOLS, GuestMode, GuestStatus, Toggle
from waifu.journal import JOURNAL_TOOLS


class Clock:
    """The PC's local wall clock, moved by the test."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute)


def guest_mode(clock: Clock, max_hours: int = 6) -> GuestMode:
    return GuestMode(max_hours=max_hours, now=clock)


def test_guest_mode_starts_off():
    guest = guest_mode(Clock(at(24, 20)))

    assert not guest.is_on()
    assert guest.status() == GuestStatus(on=False, until=None)


@pytest.mark.parametrize(
    ("turned_on", "until"),
    [
        (at(24, 20), at(25, 2)),  # six hours, before 05:00
        (at(25, 1), at(25, 5)),  # 05:00 comes first
        (at(25, 4, 59), at(25, 5)),
        (at(24, 23), at(25, 5)),  # six hours would be 05:00 too
        (at(25, 5), at(25, 11)),  # the next 05:00 is tomorrow's
        (at(25, 10), at(25, 16)),
    ],
)
def test_turning_it_on_lasts_max_hours_or_until_05_00_whichever_comes_first(turned_on, until):
    guest = guest_mode(Clock(turned_on))

    assert guest.turn_on() == Toggle(changed=True, until=until)
    assert guest.status() == GuestStatus(on=True, until=until)


def test_max_hours_sets_how_long_it_lasts():
    guest = guest_mode(Clock(at(24, 20)), max_hours=2)

    assert guest.turn_on().until == at(24, 22)


def test_turning_it_on_again_renews_it_without_a_new_generation():
    clock = Clock(at(24, 20))
    guest = guest_mode(clock)
    guest.turn_on()
    generation = guest.generation()
    clock.moment = at(24, 21)

    assert guest.turn_on() == Toggle(changed=False, until=at(25, 3))
    assert guest.generation() == generation


def test_turning_it_off_ends_it_once():
    guest = guest_mode(Clock(at(24, 20)))
    guest.turn_on()
    generation = guest.generation()

    assert guest.turn_off() == Toggle(changed=True, until=None)
    assert not guest.is_on()
    assert guest.generation() == generation + 1
    assert guest.turn_off() == Toggle(changed=False, until=None)
    assert guest.generation() == generation + 1


def test_every_switch_moves_the_generation_on():
    guest = guest_mode(Clock(at(24, 20)))
    seen = [guest.generation()]

    guest.turn_on()
    seen.append(guest.generation())
    guest.turn_off()
    seen.append(guest.generation())
    guest.turn_on()
    seen.append(guest.generation())

    assert len(set(seen)) == 4


def test_it_ends_by_itself_at_its_end_and_says_so_in_the_log(caplog):
    caplog.set_level(logging.INFO, logger="waifu.guest")
    clock = Clock(at(24, 20))
    guest = guest_mode(clock)
    guest.turn_on()
    generation = guest.generation()
    clock.moment = at(25, 1, 59)
    assert guest.is_on()

    clock.moment = at(25, 2)

    assert not guest.is_on()
    assert guest.status() == GuestStatus(on=False, until=None)
    assert guest.generation() == generation + 1  # once, however often it is asked
    assert "Guest mode ended by itself at 2026-10-25 02:00" in caplog.text


def test_the_generation_alone_notices_the_end():
    # The brains read only the generation at the start of a turn: an end nobody asked about
    # must still start a new session.
    clock = Clock(at(24, 20))
    guest = guest_mode(clock)
    guest.turn_on()
    generation = guest.generation()
    clock.moment = at(25, 3)

    assert guest.generation() == generation + 1


def test_turning_off_a_guest_mode_that_already_ended_changes_nothing():
    clock = Clock(at(24, 20))
    guest = guest_mode(clock)
    guest.turn_on()
    clock.moment = at(25, 6)

    assert guest.turn_off() == Toggle(changed=False, until=None)


def test_turning_on_logs_until_when_and_off_logs_the_end(caplog):
    caplog.set_level(logging.INFO, logger="waifu.guest")
    clock = Clock(at(24, 20))
    guest = guest_mode(clock)

    guest.turn_on()
    clock.moment = at(24, 21)
    guest.turn_on()
    guest.turn_off()

    assert [record.getMessage() for record in caplog.records] == [
        "Guest mode on until 2026-10-25 02:00",
        "Guest mode renewed until 2026-10-25 03:00",
        "Guest mode off",
    ]


@pytest.mark.parametrize("tool", sorted(PRIVATE_TOOLS))
def test_every_private_tool_is_refused_while_it_is_on(tool):
    guest = guest_mode(Clock(at(24, 20)))
    assert guest.refusal(tool) is None

    guest.turn_on()

    assert guest.refusal(tool) == GUEST_REFUSAL


@pytest.mark.parametrize(
    "tool",
    [
        CONTROL_DEVICE,
        LIST_DEVICES,
        RUN_ROUTINE,
        SCHEDULE,
        DAILY_BRIEFING,
        FOCUS_TIMER,
        JAPANESE_LESSON,  # beginner words and how many Master knows are low-stakes
    ],
)
def test_the_house_the_day_the_focus_timer_and_the_japanese_lessons_are_never_refused(tool):
    guest = guest_mode(Clock(at(24, 20)))
    guest.turn_on()

    assert guest.refusal(tool) is None


def test_the_game_calendar_is_refused_since_it_carries_master_s_own_progress():
    # game_events answers with Master's event progress, Polychrome earned, and endgame stars,
    # the same kind of data game_endgame and gacha_income are closed for.
    guest = guest_mode(Clock(at(24, 20)))
    guest.turn_on()

    assert guest.refusal(GAME_EVENTS) == GUEST_REFUSAL


def test_the_refusal_ends_with_guest_mode():
    clock = Clock(at(24, 20))
    guest = guest_mode(clock)
    guest.turn_on()
    clock.moment = at(25, 2)

    assert guest.refusal(REMEMBER) is None


def test_the_private_tools_are_master_s_own_records_and_accounts():
    assert {
        *FINANCE_TOOLS,
        *JOURNAL_TOOLS,
        REMEMBER,
        FORGET,
        ADD_TASK,
        LIST_TASKS,
        COMPLETE_TASK,
        REOPEN_TASK,
        REMOVE_TASK,
        EDIT_TASK,
        PLAY_TIME,
        GAME_STATUS,
        GACHA_HISTORY,
        GACHA_INCOME,
        GAME_CHARACTERS,
        GAME_ENDGAME,
        GAME_EVENTS,
        WEEKLY_REPORT,
        REDEEM_CODES,
        REDEEM_CODES_LIST,
        LIST_SCHEDULES,
        CANCEL_SCHEDULE,
        API_USAGE,
        LOOK_AT_SCREEN,
    } == PRIVATE_TOOLS
