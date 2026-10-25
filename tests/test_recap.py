"""The daily recap's gatherer: a day's parts from the local stores in tmp_path at a fixed WIB
clock, each part on its own, and the JSON the brain gets."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest

from waifu.finance.values import WIB
from waifu.gacha.games import GAMES
from waifu.gacha.history import GachaRecord, GachaStore
from waifu.japanese import JapaneseStore, Word
from waifu.journal import JournalStore
from waifu.network_history import NetworkStore
from waifu.play_time import PlayTimeStore, Session
from waifu.recap import (
    PARTS,
    DailyRecap,
    OutageDay,
    RecapError,
    StoredPulls,
    recap_day,
    recap_json,
)
from waifu.schedule import (
    AlarmAction,
    DeviceAction,
    Reminder,
    RoutineAction,
    ScheduleStore,
)
from waifu.todo import TodoStore

TODAY = date(2026, 10, 24)  # a Saturday
YESTERDAY = date(2026, 10, 23)
MIDNIGHT = datetime(2026, 10, 24, tzinfo=WIB)
NOW = MIDNIGHT + timedelta(hours=21, minutes=30)
NEKO = Word("neko", "ねこ", "猫", "kucing", "ねこがいます。", "Ada kucing.")
ARIGATOU = Word("arigatou", "ありがとう", None, "terima kasih", "ありがとう。", "Terima kasih.")
INU = Word("inu", "いぬ", "犬", "anjing", "いぬがいます。", "Ada anjing.")
WORDS = (NEKO, ARIGATOU, INU)


def at(hour: float, day: int = 0) -> datetime:
    """`hour` hours after midnight WIB `day` days from today."""
    return MIDNIGHT + timedelta(days=day, seconds=round(hour * 3600))


class Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def recap(**sources) -> DailyRecap:
    return DailyRecap(now=lambda: NOW, **sources)


# --- the day asked -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("today", TODAY),
        ("yesterday", YESTERDAY),
        (date(2026, 10, 20), date(2026, 10, 20)),
        (date(2026, 9, 24), date(2026, 9, 24)),  # 30 days back
        (TODAY, TODAY),
    ],
)
def test_the_day_is_today_yesterday_or_a_date_up_to_30_days_back(value, expected):
    assert recap_day(value, TODAY) == expected


def test_a_day_more_than_30_days_back_says_how_far_the_recap_goes():
    with pytest.raises(RecapError) as refused:
        recap_day(date(2026, 9, 23), TODAY)

    assert (
        str(refused.value)
        == "Rekap hanya sampai 30 hari ke belakang, paling jauh Kamis 24-09-2026."
    )


def test_a_day_still_to_come_has_no_recap():
    with pytest.raises(RecapError) as refused:
        recap_day(date(2026, 10, 25), TODAY)

    assert str(refused.value) == (
        "Hari itu belum terjadi; rekap hanya untuk hari ini atau hari yang sudah lewat."
    )


def test_today_is_the_wib_date_of_the_clock():
    utc = DailyRecap(now=lambda: datetime(2026, 10, 23, 18, 30, tzinfo=UTC))

    assert utc.today() == TODAY  # 01:30 WIB


# --- the parts that are on ---------------------------------------------------------------------


def test_the_parts_on_are_named_in_the_recap_s_order(tmp_path):
    on = recap(
        schedule=ScheduleStore(tmp_path / "schedule.json"),
        journal=JournalStore(tmp_path / "journal.json", max_chars=500, max_notes_per_day=10),
        focus=lambda since, until: 0,
    )

    assert on.parts == ("focus", "journal", "schedule")
    assert PARTS == (
        "tasks",
        "focus",
        "play_time",
        "gacha",
        "japanese",
        "journal",
        "schedule",
        "network",
    )


def test_with_every_source_off_no_part_is_on_and_nothing_is_readable():
    nothing = recap()

    assert nothing.parts == ()
    assert nothing.gather(TODAY).readable is False


# --- tasks -------------------------------------------------------------------------------------


def todo_store(tmp_path, clock: Clock) -> TodoStore:
    return TodoStore(
        tmp_path / "todo.json", max_tasks=100, max_chars=200, keep_done_days=7, now=clock
    )


def done_at(store: TodoStore, clock: Clock, text: str, moment: datetime) -> None:
    clock.moment = moment
    store.complete(store.add(text).id)


@pytest.fixture
def todo(tmp_path) -> TodoStore:
    """Tasks done yesterday 23:59 and today 00:00 and 20:00; open ones due yesterday, today,
    tomorrow, and with no due date."""
    clock = Clock()
    store = todo_store(tmp_path, clock)
    done_at(store, clock, "cuci piring", at(23.99, day=-1))
    done_at(store, clock, "beli susu", at(0))
    done_at(store, clock, "kirim laporan", at(20))
    clock.moment = NOW
    store.add("bayar listrik", TODAY)
    store.add("servis motor", YESTERDAY)
    store.add("potong rambut", date(2026, 10, 25))
    store.add("baca buku")
    return store


def test_the_tasks_are_the_ones_done_that_wib_day_and_the_open_ones_due_by_it(todo):
    tasks = recap(todo=todo).gather(TODAY).tasks

    assert [task.text for task in tasks.done] == ["kirim laporan", "beli susu"]
    assert [task.text for task in tasks.still_due] == ["servis motor", "bayar listrik"]


def test_yesterday_s_tasks_are_the_ones_done_yesterday_and_due_by_yesterday(todo):
    tasks = recap(todo=todo).gather(YESTERDAY).tasks

    assert [task.text for task in tasks.done] == ["cuci piring"]
    assert [task.text for task in tasks.still_due] == ["servis motor"]


def test_the_done_tasks_of_a_day_the_list_no_longer_keeps_whole_are_unknown_not_none(tmp_path):
    clock = Clock()
    store = todo_store(tmp_path, clock)  # keeps a done task 7 days
    done_at(store, clock, "cuci mobil", at(22, day=-7))  # still kept: 6 days 23:30 ago
    done_at(store, clock, "ganti oli", at(0, day=-6))
    clock.moment = NOW
    store.add("bayar listrik", date(2026, 10, 16))

    seven_back = recap(todo=store).gather(date(2026, 10, 17))
    six_back = recap(todo=store).gather(date(2026, 10, 18))

    assert seven_back.tasks.done is None  # its morning is forgotten already
    assert [task.text for task in seven_back.tasks.still_due] == ["bayar listrik"]
    assert recap_json(seven_back)["tasks"] == {
        "done_known": False,
        "still_due_count": 1,
        "still_due": [{"text": "bayar listrik", "due": "2026-10-16"}],
    }
    assert [task.text for task in six_back.tasks.done] == ["ganti oli"]


# --- focus -------------------------------------------------------------------------------------


def test_focus_is_asked_for_today_from_midnight_until_now():
    windows = []

    def focused(since: datetime, until: datetime) -> int:
        windows.append((since, until))
        return 3000

    assert recap(focus=focused).gather(TODAY).focus == 3000
    assert windows == [(MIDNIGHT, NOW)]


def test_focus_of_a_past_day_is_asked_from_its_midnight_to_the_next():
    windows = []

    def focused(since: datetime, until: datetime) -> int:
        windows.append((since, until))
        return 0

    assert recap(focus=focused).gather(YESTERDAY).focus == 0
    assert windows == [(at(0, day=-1), MIDNIGHT)]


# --- play time ---------------------------------------------------------------------------------


@pytest.fixture
def play(tmp_path) -> PlayTimeStore:
    """Genshin from Friday 23:00 until 00:30, HSR 20:00 until 21:00 and ZZZ 20:30 until 21:15
    together, HSR open again since 21:20, last seen at 21:29."""
    store = PlayTimeStore(
        tmp_path / "playtime.json", games=["hsr", "genshin", "zzz"], keep_days=90, now=lambda: NOW
    )
    store.save(
        [Session("hsr", at(21 + 20 / 60), at(21 + 29 / 60))],
        [
            Session("genshin", at(23, day=-1), at(0.5)),
            Session("hsr", at(20), at(21)),
            Session("zzz", at(20.5), at(21.25)),
        ],
    )
    return store


def test_the_play_time_is_each_game_s_time_that_day_and_the_total_counting_together_once(play):
    played = recap(play_time=play).gather(TODAY).play_time

    assert [(g.game, g.seconds, g.sessions) for g in played.games] == [
        ("hsr", 69 * 60, 2),
        ("genshin", 30 * 60, 1),
        ("zzz", 45 * 60, 1),
    ]
    assert played.seconds == (30 + 75 + 9) * 60  # 20:00-21:15 counted once


def test_play_is_left_out_when_no_game_was_open_a_whole_minute_though_together_they_make_one(
    tmp_path,
):
    store = PlayTimeStore(
        tmp_path / "playtime.json", games=["hsr", "genshin"], keep_days=90, now=lambda: NOW
    )
    seconds_25 = timedelta(seconds=25)
    store.save(
        [],
        [
            Session("hsr", at(20), at(20) + seconds_25),
            Session("genshin", at(21), at(21) + seconds_25),
        ],
    )

    day = recap(play_time=store).gather(TODAY)

    assert day.play_time.seconds == 50
    assert day.play_time.played == ()
    assert "play_time" not in recap_json(day)


def test_a_session_across_midnight_counts_on_each_day(play):
    played = recap(play_time=play).gather(YESTERDAY).play_time

    assert [(g.game, g.seconds) for g in played.games if g.seconds] == [("genshin", 3600)]
    assert played.seconds == 3600


# --- gacha -------------------------------------------------------------------------------------


def hsr_pull(record_id: int, server_time: str, name: str = "Arrows", **kwargs) -> GachaRecord:
    values = {"banner": 11, "rarity": 3, "item_type": "Light Cone"} | kwargs
    return GachaRecord(id=record_id, uid=800000001, name=name, time=server_time, **values)


@pytest.fixture
def pulls(tmp_path) -> StoredPulls:
    """HSR: 50 pulls last week, Acheron (lost to Himeko first) won today at 74 pulls on the
    character banner, 9 light cone pulls today, one pull yesterday; no Genshin history."""
    store = GachaStore(tmp_path / "gacha")
    records = [
        *[hsr_pull(i, "2026-10-17 12:00:00") for i in range(1, 50)],
        hsr_pull(50, "2026-10-17 12:00:01", "Himeko", rarity=5),
        hsr_pull(51, "2026-10-23 22:00:00"),  # 21:00 WIB yesterday
        *[hsr_pull(i, "2026-10-24 21:00:00") for i in range(52, 124)],
        hsr_pull(124, "2026-10-24 21:00:01", "Acheron", rarity=5),  # 20:00 WIB today
        *[hsr_pull(i, "2026-10-24 21:05:00", banner=12) for i in range(125, 134)],
    ]
    store.save(GAMES["hsr"], 800000001, records, complete=True)
    return StoredPulls(store, games=["hsr", "genshin"], server_utc_offset=8)


def test_the_day_s_pulls_come_per_game_from_the_stored_history_with_each_top(pulls):
    [hsr] = recap(gacha=pulls).gather(TODAY).gacha

    assert hsr.game.id == "hsr"
    assert hsr.count == 82
    [character, light_cone] = hsr.banners
    assert (character.count, light_cone.count) == (73, 9)
    assert [(top.name, top.pulls, top.won_5050) for top in character.tops] == [
        ("Acheron", 74, None)  # guaranteed after Himeko
    ]


def test_a_game_without_pulls_that_day_is_left_out(pulls):
    [hsr] = recap(gacha=pulls).gather(YESTERDAY).gacha

    assert hsr.count == 1
    assert recap(gacha=pulls).gather(date(2026, 10, 22)).gacha == ()


def test_the_server_offset_decides_the_day_of_a_pull(pulls):
    behind = StoredPulls(pulls.store, games=["hsr"], server_utc_offset=-5)  # +13 h in WIB

    [hsr] = recap(gacha=behind).gather(TODAY).gacha

    assert hsr.count == 1  # yesterday's 22:00 server time is today 10:00 WIB


# --- Japanese ----------------------------------------------------------------------------------


@pytest.fixture
def lessons(tmp_path) -> JapaneseStore:
    """Neko learned today and right once; arigatou learned yesterday, right once yesterday and
    wrong once today; inu learned yesterday and never asked; a word not in the list today."""
    store = JapaneseStore(tmp_path / "japanese.json", now=lambda: NOW)
    store.learn("arigatou", YESTERDAY)
    store.grade("arigatou", True, YESTERDAY)
    store.grade("arigatou", False, TODAY)
    store.learn("inu", YESTERDAY)
    store.learn("neko", TODAY)
    store.grade("neko", True, TODAY)
    store.learn("sayonara", TODAY)  # a later list dropped it
    return store


def test_the_words_learned_and_last_reviewed_that_day_from_the_word_list(lessons):
    words = recap(japanese=lessons, words=WORDS).gather(TODAY).japanese

    assert words.learned == (NEKO,)
    assert words.reviewed == 2  # neko and arigatou


def test_a_review_followed_by_another_later_counts_only_on_the_last_day(lessons):
    words = recap(japanese=lessons, words=WORDS).gather(YESTERDAY).japanese

    assert words.learned == (ARIGATOU, INU)
    assert words.reviewed == 0  # arigatou's last review is today


# --- the journal -------------------------------------------------------------------------------


def test_the_journal_gives_the_day_s_note_count_only(tmp_path):
    journal = JournalStore(
        tmp_path / "journal.json", max_chars=500, max_notes_per_day=10, now=lambda: NOW
    )
    journal.add("laporannya selesai")
    journal.add("makan malam sama Ibu")
    journal.add("capek banget", YESTERDAY)

    assert recap(journal=journal).gather(TODAY).journal == 2
    assert recap(journal=journal).gather(date(2026, 10, 20)).journal == 0


# --- the schedule ------------------------------------------------------------------------------


@pytest.fixture
def schedule(tmp_path) -> ScheduleStore:
    """Made Friday 08:00: a daily medicine reminder at 07:00 (fired today, so at tomorrow), a
    one-shot reminder today 22:00, the sleep routine on weekdays at 23:00, the AC off at 02:00
    daily, and a reminder on Monday."""
    clock = Clock(at(8, day=-1))
    store = ScheduleStore(tmp_path / "schedule.json", now=lambda: clock().replace(tzinfo=None))
    local = lambda moment: moment.replace(tzinfo=None)  # noqa: E731
    store.add(local(at(7)), "daily", Reminder("minum obat"))
    store.add(local(at(22)), "none", Reminder("telepon Ibu"))
    store.add(local(at(23, day=-1)), "weekdays", RoutineAction("tidur"))
    store.add(local(at(2)), "daily", DeviceAction("ac", "off"))
    store.add(local(at(9, day=2)), "none", Reminder("rapat"))
    clock.moment = NOW
    store.fired("j1", local(at(7)))
    return store


def test_the_schedule_is_the_reminders_and_routines_due_that_day_and_whether_they_passed(
    schedule,
):
    planned = recap(schedule=schedule).gather(TODAY).schedule

    assert [(p.at.strftime("%H:%M"), p.what, p.passed) for p in planned] == [
        ("07:00", "minum obat", True),
        ("22:00", "telepon Ibu", False),
    ]


def test_yesterday_s_schedule_holds_what_was_due_after_the_entries_were_made(schedule):
    planned = recap(schedule=schedule).gather(YESTERDAY).schedule

    # Friday 07:00 is before the entries were made at 08:00.
    assert [(p.at.strftime("%H:%M"), p.what, p.passed) for p in planned] == [
        ("23:00", "rutinitas tidur", True)
    ]


def test_the_schedule_names_an_alarm_due_that_day_with_its_label(tmp_path):
    made = at(8, day=-1).replace(tzinfo=None)
    store = ScheduleStore(tmp_path / "schedule.json", now=lambda: made)
    store.add(at(6).replace(tzinfo=None), "daily", AlarmAction("berangkat kerja"))
    store.add(at(22).replace(tzinfo=None), "none", AlarmAction())

    planned = recap(schedule=store).gather(TODAY).schedule

    assert [(p.at.strftime("%H:%M"), p.what, p.passed) for p in planned] == [
        ("06:00", "alarm berangkat kerja", True),
        ("22:00", "alarm", False),
    ]


def test_a_repeating_reminder_set_to_start_on_a_later_day_is_not_due_before_it(tmp_path):
    # Made Thursday 09:00: "tiap hari jam 8 mulai Senin".
    made = at(9, day=-2).replace(tzinfo=None)
    store = ScheduleStore(tmp_path / "schedule.json", now=lambda: made)
    store.add(at(8, day=2).replace(tzinfo=None), "daily", Reminder("minum obat"))

    assert recap(schedule=store).gather(YESTERDAY).schedule == ()
    assert recap(schedule=store).gather(TODAY).schedule == ()


# --- one part at a time ------------------------------------------------------------------------


def test_a_part_that_fails_has_its_problem_and_the_rest_is_still_read(tmp_path, play, caplog):
    def broken(since: datetime, until: datetime) -> int:
        raise OSError("the focus file is locked")

    caplog.set_level(logging.DEBUG)
    day = recap(focus=broken, play_time=play).gather(TODAY)

    assert day.focus is None
    assert day.problems == {"focus": "Catatan fokus tidak bisa dibaca saat ini."}
    assert day.play_time is not None
    assert day.readable is True
    assert "the focus file is locked" in caplog.text
    assert not [r for r in caplog.records if r.levelno == logging.INFO]


@pytest.mark.parametrize(
    ("part", "problem"),
    [
        ("todo", "Daftar tugas tidak bisa dibaca saat ini."),
        ("play_time", "Jam main tidak bisa dibaca saat ini."),
        ("japanese", "Catatan bahasa Jepang tidak bisa dibaca saat ini."),
        ("journal", "Jurnal tidak bisa dibaca saat ini."),
        ("schedule", "Jadwal tidak bisa dibaca saat ini."),
        ("network", "Riwayat internet tidak bisa dibaca saat ini."),
    ],
)
def test_each_part_that_fails_says_so_in_indonesian(part, problem):
    class Broken:
        games = ("hsr",)

        def __getattr__(self, name):
            raise OSError("unreadable")

    day = recap(**{part: Broken()}).gather(TODAY)

    assert list(day.problems.values()) == [problem]
    assert day.readable is False


def test_a_game_whose_history_cannot_be_read_says_so_and_the_others_are_still_told(pulls):
    class HalfBroken:
        def load(self, game, uid=None):
            if game.id == "genshin":
                raise PermissionError("access denied")
            return pulls.store.load(game, uid)

    day = recap(gacha=StoredPulls(HalfBroken(), games=["genshin", "hsr"])).gather(TODAY)

    assert [game.game.id for game in day.gacha] == ["hsr"]
    assert day.problems == {"gacha": "Riwayat pull Genshin tidak bisa dibaca saat ini."}


# --- internet outages --------------------------------------------------------------------------


@pytest.fixture
def network(tmp_path) -> NetworkStore:
    """Down from 23:50 yesterday to 00:10 today, 08:00 to 08:04, and 21:20 until its last
    failed poll at 21:28, still open."""
    store = NetworkStore(tmp_path / "network.json", keep_days=7, now=lambda: NOW)
    for start, end, kind in (
        (at(-0.1667), at(1 / 6), "offline"),
        (at(8), at(8 + 4 / 60), "router"),
    ):
        store.begin(start, start, kind)
        store.end(end)
    store.begin(at(21 + 1 / 3), at(21 + 28 / 60), "offline")
    return store


def test_the_day_s_outages_are_counted_with_their_time_that_day(network):
    day = recap(network=network).gather(TODAY)

    assert day.network == OutageDay(count=3, seconds=(10 + 4 + 8) * 60, keep_days=7, known=True)


def test_an_outage_across_midnight_counts_on_each_day(network):
    day = recap(network=network).gather(YESTERDAY)

    assert day.network == OutageDay(count=1, seconds=10 * 60, keep_days=7, known=True)


def test_a_day_older_than_the_history_keeps_is_unknown_not_without_outages(network):
    day = recap(network=network).gather(TODAY - timedelta(days=8))

    assert day.network == OutageDay(count=0, seconds=0, keep_days=7, known=False)


def test_the_json_tells_the_outages_only_when_there_were_any(network, tmp_path):
    quiet = NetworkStore(tmp_path / "quiet.json", keep_days=7, now=lambda: NOW)

    assert recap_json(recap(network=network).gather(TODAY))["network"] == {
        "outages": 3,
        "minutes": 22,
    }
    assert "network" not in recap_json(recap(network=quiet).gather(TODAY))
    assert "network" not in recap_json(recap(network=network).gather(TODAY - timedelta(days=8)))


# --- the JSON ----------------------------------------------------------------------------------


def test_the_json_holds_the_parts_with_something_compactly_with_today_s_time(
    tmp_path, todo, play, pulls, lessons, schedule
):
    journal = JournalStore(
        tmp_path / "journal.json", max_chars=500, max_notes_per_day=10, now=lambda: NOW
    )
    journal.add("laporannya selesai")
    journal.add("makan malam sama Ibu")

    day = recap(
        todo=todo,
        focus=lambda since, until: 50 * 60,
        play_time=play,
        gacha=pulls,
        japanese=lessons,
        words=WORDS,
        journal=journal,
        schedule=schedule,
    ).gather(TODAY)

    text = json.dumps(recap_json(day), ensure_ascii=False, separators=(",", ":"))
    assert json.loads(text) == {
        "date": "2026-10-24",
        "day": "Sabtu",
        "as_of": "21:30",
        "tasks": {
            "done_count": 2,
            "done": ["kirim laporan", "beli susu"],
            "still_due_count": 2,
            "still_due": [
                {"text": "servis motor", "due": "2026-10-23"},
                {"text": "bayar listrik", "due": "2026-10-24"},
            ],
        },
        "focus": {"hours": 0, "minutes": 50},
        "play_time": {
            "games": [
                {"name": "Honkai: Star Rail", "hours": 1, "minutes": 9, "sessions": 2},
                {"name": "Genshin Impact", "hours": 0, "minutes": 30, "sessions": 1},
                {"name": "Zenless Zone Zero", "hours": 0, "minutes": 45, "sessions": 1},
            ],
            "total": {"hours": 1, "minutes": 54},
        },
        "gacha": [
            {
                "name": "Honkai: Star Rail",
                "pulls": 82,
                "top_pulls": [
                    {
                        "banner": "Character Event Warp",
                        "name": "Acheron",
                        "rarity": "5★",
                        "pulls": 74,
                        "won_5050": None,
                    }
                ],
            }
        ],
        "japanese": {
            "learned_count": 1,
            "learned": [{"kana": "ねこ", "kanji": "猫", "meaning": "kucing"}],
            "reviewed": 2,
        },
        "journal": {"notes": 2},
        "schedule": {
            "count": 2,
            "entries": [
                {"time": "07:00", "what": "minum obat", "passed": True},
                {"time": "22:00", "what": "telepon Ibu", "passed": False},
            ],
        },
    }


def test_a_past_day_has_no_time_now_and_parts_with_nothing_are_left_out(tmp_path):
    journal = JournalStore(
        tmp_path / "journal.json", max_chars=500, max_notes_per_day=10, now=lambda: NOW
    )

    day = recap(journal=journal, focus=lambda since, until: 29).gather(YESTERDAY)

    assert recap_json(day) == {"date": "2026-10-23", "day": "Jumat"}


def test_the_json_lists_at_most_ten_with_the_whole_counts(tmp_path):
    clock = Clock()
    todo = todo_store(tmp_path, clock)
    for number in range(12):
        done_at(todo, clock, f"tugas {number}", at(8 + number))

    tasks = recap_json(recap(todo=todo).gather(TODAY))["tasks"]

    assert tasks["done_count"] == 12
    assert tasks["done"] == [f"tugas {number}" for number in range(11, 1, -1)]


def test_the_json_names_each_problem(pulls):
    def broken(since: datetime, until: datetime) -> int:
        raise OSError("locked")

    day = recap(focus=broken, gacha=pulls).gather(TODAY)

    assert recap_json(day)["problems"] == ["Catatan fokus tidak bisa dibaca saat ini."]


def test_a_zzz_s_rank_is_an_s(tmp_path):
    store = GachaStore(tmp_path / "gacha")
    store.save(
        GAMES["zzz"],
        1300000001,
        [
            GachaRecord(1, 1300000001, 2, "Ellen", 4, "Agents", "2026-10-24 12:00:00"),
        ],
        complete=True,
    )

    day = recap(gacha=StoredPulls(store, games=["zzz"])).gather(TODAY)

    [zzz] = recap_json(day)["gacha"]
    assert zzz["top_pulls"][0]["rarity"] == "S"
