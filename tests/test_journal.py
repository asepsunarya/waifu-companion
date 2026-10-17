"""Master's journal: the store in tmp_path at a WIB clock moved by the test, her nightly
question, and its timer."""

import dataclasses
import json
import logging
import threading
from datetime import UTC, date, datetime, time, timedelta

import pytest

from waifu import journal
from waifu.brain.confirm import is_yes
from waifu.brain.music_tools import MusicTools
from waifu.brain.screen_tools import ScreenTools
from waifu.config import AssistantConfig, JournalConfig
from waifu.finance.values import WIB
from waifu.journal import (
    JOURNAL_PROMPT,
    JournalError,
    JournalStore,
    JournalTimer,
    Note,
    Saved,
)
from waifu.screen import ScreenImage

NOW = datetime(2026, 10, 17, 21, 30, 15, 123456, tzinfo=WIB)  # a Saturday night
TODAY = NOW.date()
YESTERDAY = TODAY - timedelta(days=1)
STAMP = NOW.replace(microsecond=0)
CORRUPT_NAME = "journal.json.corrupt-20261017-213015"


class Clock:
    """A settable aware clock."""

    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


def make_store(tmp_path, clock=None, **limits):
    settings = {"max_chars": 500, "max_notes_per_day": 20} | limits
    return JournalStore(tmp_path / "journal.json", now=clock or Clock(), **settings)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_journal(path, days):
    path.write_text(json.dumps({"version": 1, "days": days}), encoding="utf-8")


def entry(text, at="2026-10-16T22:00:00+07:00"):
    return {"text": text, "at": at}


def texts(store, day=TODAY):
    return [note.text for note in store.notes(day, day).get(day, ())]


def test_a_note_has_its_text_and_when_it_was_written():
    note = Note("hari ini capek", STAMP)
    saved = Saved(TODAY, note, added=True)

    assert (note.text, note.at, saved.day, saved.note, saved.added) == (
        "hari ini capek",
        STAMP,
        TODAY,
        note,
        True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.text = "lain"  # type: ignore[misc]


# --- reading -----------------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_journal_and_reading_never_creates_it(tmp_path):
    folder = tmp_path / "data"
    store = JournalStore(folder / "journal.json", max_chars=500, max_notes_per_day=20, now=Clock())

    assert store.notes() == {}
    assert store.forget(TODAY) == 0

    assert not folder.exists()


def test_the_store_exposes_its_path_and_limits(tmp_path):
    store = make_store(tmp_path, max_chars=300, max_notes_per_day=5)

    assert (store.path, store.max_chars, store.max_notes_per_day) == (
        tmp_path / "journal.json",
        300,
        5,
    )


def test_every_call_rereads_a_file_edited_by_hand(tmp_path):
    store = make_store(tmp_path)
    store.add("ketemu Rina")

    write_journal(store.path, {"2026-10-17": [entry("diedit dengan Notepad")]})

    assert texts(store) == ["diedit dengan Notepad"]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    store = make_store(tmp_path)
    content = json.dumps({"version": 1, "days": {"2026-10-17": [entry("pakai BOM")]}})
    store.path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    assert texts(store) == ["pakai BOM"]


def test_notes_come_back_by_day_oldest_first_with_aware_times(tmp_path):
    store = make_store(tmp_path)
    write_journal(
        store.path,
        {
            "2026-10-17": [entry("hari ini", "2026-10-17T21:00:00+07:00")],
            "2026-10-15": [entry("kamis", "2026-10-16T00:40:00+07:00")],
        },
    )

    assert store.notes() == {
        date(2026, 10, 15): (Note("kamis", datetime(2026, 10, 16, 0, 40, tzinfo=WIB)),),
        date(2026, 10, 17): (Note("hari ini", datetime(2026, 10, 17, 21, tzinfo=WIB)),),
    }


def test_notes_between_two_days_include_both_days(tmp_path):
    store = make_store(tmp_path)
    write_journal(
        store.path,
        {day: [entry(day)] for day in ("2026-10-10", "2026-10-12", "2026-10-15", "2026-10-17")},
    )

    found = store.notes(date(2026, 10, 12), date(2026, 10, 15))
    assert list(found) == [date(2026, 10, 12), date(2026, 10, 15)]
    assert list(store.notes(since=date(2026, 10, 15))) == [date(2026, 10, 15), date(2026, 10, 17)]
    assert list(store.notes(until=date(2026, 10, 11))) == [date(2026, 10, 10)]


def test_a_day_left_empty_by_hand_is_no_day_with_notes(tmp_path):
    store = make_store(tmp_path)
    write_journal(store.path, {"2026-10-16": [], "2026-10-17": [entry("ada")]})

    assert list(store.notes()) == [TODAY]


# --- adding ------------------------------------------------------------------------------------


def test_add_writes_today_in_wib_stamped_to_the_second(tmp_path):
    store = make_store(tmp_path)

    saved = store.add("hari ini laporannya selesai")

    assert saved == Saved(TODAY, Note("hari ini laporannya selesai", STAMP), added=True)
    assert texts(store) == ["hari ini laporannya selesai"]


def test_today_is_the_wib_date_whatever_offset_the_clock_gives(tmp_path):
    # 23:30 UTC on the 16th is already 06:30 on the 17th in WIB.
    store = make_store(tmp_path, Clock(datetime(2026, 10, 16, 23, 30, tzinfo=UTC)))

    saved = store.add("pagi-pagi")

    assert saved.day == TODAY
    assert saved.note.at == datetime(2026, 10, 17, 6, 30, tzinfo=WIB)
    assert read_json(store.path)["days"]["2026-10-17"][0]["at"] == "2026-10-17T06:30:00+07:00"


def test_add_writes_the_file_in_readable_utf8_with_days_in_order(tmp_path):
    store = make_store(tmp_path)
    store.add("malam ini makan ラーメン")
    store.add("kemarin hujan deras", YESTERDAY)

    raw = store.path.read_text(encoding="utf-8")

    assert "ラーメン" in raw
    assert read_json(store.path) == {
        "version": 1,
        "days": {
            "2026-10-16": [{"text": "kemarin hujan deras", "at": "2026-10-17T21:30:15+07:00"}],
            "2026-10-17": [{"text": "malam ini makan ラーメン", "at": "2026-10-17T21:30:15+07:00"}],
        },
    }
    assert list(read_json(store.path)["days"]) == ["2026-10-16", "2026-10-17"]


def test_add_creates_the_parent_folder_on_the_first_write(tmp_path):
    store = JournalStore(
        tmp_path / "data" / "journal.json", max_chars=500, max_notes_per_day=20, now=Clock()
    )

    store.add("catatan pertama")

    assert (tmp_path / "data" / "journal.json").exists()


def test_a_day_holds_several_notes_in_the_order_they_were_written(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)

    store.add("pagi ke kampus")
    clock.current += timedelta(minutes=5)
    store.add("malam main HSR sama Rina")

    assert texts(store) == ["pagi ke kampus", "malam main HSR sama Rina"]
    assert [note.at for note in store.notes()[TODAY]] == [STAMP, STAMP + timedelta(minutes=5)]


def test_a_note_after_midnight_may_go_on_the_day_before(tmp_path):
    clock = Clock(datetime(2026, 10, 18, 0, 40, tzinfo=WIB))
    store = make_store(tmp_path, clock)

    saved = store.add("tadi seru", TODAY)

    assert saved.day == TODAY
    assert saved.note.at == datetime(2026, 10, 18, 0, 40, tzinfo=WIB)
    assert texts(store) == ["tadi seru"]


def test_a_day_after_today_is_refused(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(JournalError, match="hari ini atau hari yang sudah lewat"):
        store.add("besok ujian", TODAY + timedelta(days=1))

    assert not store.path.exists()


def test_a_time_given_as_the_day_is_refused(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="date, not a datetime"):
        store.add("catatan", NOW)
    with pytest.raises(ValueError, match="date, not a datetime"):
        store.forget(NOW)

    assert not store.path.exists()


def test_text_is_stripped_and_its_whitespace_collapsed(tmp_path):
    store = make_store(tmp_path)

    assert store.add("  hari ini\n  capek   banget \t").note.text == "hari ini capek banget"


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_text_is_refused(tmp_path, text):
    store = make_store(tmp_path)

    with pytest.raises(JournalError, match="kosong"):
        store.add(text)

    assert not store.path.exists()


def test_text_up_to_max_chars_is_saved(tmp_path):
    store = make_store(tmp_path, max_chars=20)

    assert store.add("a" * 20).note.text == "a" * 20


def test_text_longer_than_max_chars_is_refused_without_repeating_it(tmp_path):
    store = make_store(tmp_path, max_chars=20)
    text = "hari ini panjang sekali ceritanya"

    with pytest.raises(JournalError, match="20 karakter") as caught:
        store.add(text)

    assert text not in str(caught.value)
    assert not store.path.exists()


@pytest.mark.parametrize(
    "text",
    [
        "hari ini ganti password wifi jadi kucing123",
        "PIN ATM baruku 1234",
        "kode OTP 482910",
        "kartu 4111 1111 1111 1111",
    ],
)
def test_a_note_that_holds_a_secret_is_refused_without_repeating_it(tmp_path, text):
    store = make_store(tmp_path)

    with pytest.raises(JournalError, match="rahasia") as caught:
        store.add(text)

    assert text not in str(caught.value)
    assert not store.path.exists()


def test_a_note_that_names_a_secret_without_its_value_is_saved(tmp_path):
    store = make_store(tmp_path)

    assert store.add("hari ini lupa password wifi, malu banget").added is True


def test_the_same_note_that_day_is_kept_once_and_nothing_is_written(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    first = store.add("Ketemu Rina di kampus")
    before = store.path.read_bytes()
    clock.current += timedelta(minutes=1)

    again = store.add("  ketemu rina  di KAMPUS ")

    assert again == Saved(TODAY, first.note, added=False)
    assert store.path.read_bytes() == before


def test_the_same_note_on_another_day_is_a_new_note(tmp_path):
    store = make_store(tmp_path)
    store.add("capek", YESTERDAY)

    assert store.add("capek").added is True
    assert texts(store, YESTERDAY) == texts(store) == ["capek"]


def test_a_full_day_is_refused_and_says_what_to_do(tmp_path):
    store = make_store(tmp_path, max_notes_per_day=2)
    store.add("satu")
    store.add("dua")

    with pytest.raises(JournalError, match="2 catatan") as caught:
        store.add("tiga")

    assert "waifu jurnal" in str(caught.value)
    assert texts(store) == ["satu", "dua"]
    assert store.add("tiga", YESTERDAY).added is True


def test_the_same_note_on_a_full_day_is_still_the_note_there(tmp_path):
    store = make_store(tmp_path, max_notes_per_day=1)
    first = store.add("satu")

    assert store.add("satu") == Saved(TODAY, first.note, added=False)


def test_journal_error_is_an_exception_with_its_message():
    assert str(JournalError("Catatan jurnalnya kosong.")) == "Catatan jurnalnya kosong."


# --- forgetting --------------------------------------------------------------------------------


def test_forget_deletes_every_note_of_the_day_and_says_how_many(tmp_path):
    store = make_store(tmp_path)
    store.add("satu")
    store.add("dua")
    store.add("kemarin", YESTERDAY)

    assert store.forget(TODAY) == 2

    assert list(store.notes()) == [YESTERDAY]
    assert list(read_json(store.path)["days"]) == ["2026-10-16"]


def test_forget_of_a_day_without_notes_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("satu")
    before = store.path.read_bytes()

    assert store.forget(YESTERDAY) == 0

    assert store.path.read_bytes() == before


# --- aware times only --------------------------------------------------------------------------

CALLS = {
    "add": lambda store: store.add("catatan"),
    "notes": lambda store: store.notes(),
    "forget": lambda store: store.forget(TODAY),
}


@pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
def test_a_naive_clock_is_refused_before_it_could_spoil_the_file(tmp_path, call):
    make_store(tmp_path).add("satu")
    store = make_store(tmp_path, Clock(NOW.replace(tzinfo=None)))
    before = store.path.read_bytes()

    with pytest.raises(ValueError, match="aware"):
        call(store)

    assert store.path.read_bytes() == before


# --- several stores and threads ----------------------------------------------------------------


def test_a_second_store_on_the_same_file_sees_changes_at_once(tmp_path):
    cyrene = make_store(tmp_path)
    terminal = make_store(tmp_path)

    cyrene.add("dari suara")
    terminal.add("dari terminal")

    assert texts(cyrene) == ["dari suara", "dari terminal"]


def test_concurrent_adds_from_two_stores_keep_every_note(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]
    errors = []

    def add(worker):
        try:
            for index in range(5):
                stores[worker % 2].add(f"catatan {worker}-{index}", TODAY - timedelta(worker % 3))
        except Exception as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sum(len(notes) for notes in stores[0].notes().values()) == 40


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    store = make_store(tmp_path)

    store.add("satu")

    assert [path.name for path in tmp_path.iterdir()] == ["journal.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("satu")
    before = store.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(journal.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.add("dua")

    assert store.path.read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["journal.json"]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "jurnal Master.json"
    settings = {"max_chars": 500, "max_notes_per_day": 20}

    JournalStore(path, now=Clock(), **settings).add("satu")

    assert list(JournalStore(path, now=Clock(), **settings).notes()) == [TODAY]


# --- corrupt files -----------------------------------------------------------------------------

VALID = entry("satu")


def journal_bytes(days, **changes):
    return json.dumps({"version": 1, "days": days} | changes).encode()


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "days": {"2026-10-17": [\xff\xfe]}}',
    "a list": b"[]",
    "missing days": json.dumps({"version": 1}).encode(),
    "an extra key": journal_bytes({}, mood="senang"),
    "another version": journal_bytes({}, version=2),
    "version true": journal_bytes({}, version=True),
    "days a list": journal_bytes([]),
    "day a weekday": journal_bytes({"Sabtu": [VALID]}),
    "day without dashes": journal_bytes({"20261017": [VALID]}),
    "day that does not exist": journal_bytes({"2026-02-30": [VALID]}),
    "day a time": journal_bytes({"2026-10-17T21:00:00+07:00": [VALID]}),
    "notes an object": journal_bytes({"2026-10-17": {}}),
    "note a string": journal_bytes({"2026-10-17": ["satu"]}),
    "note without at": journal_bytes({"2026-10-17": [{"text": "satu"}]}),
    "note with an extra key": journal_bytes({"2026-10-17": [VALID | {"mood": 1}]}),
    "numeric text": journal_bytes({"2026-10-17": [VALID | {"text": 42}]}),
    "null text": journal_bytes({"2026-10-17": [VALID | {"text": None}]}),
    "at not a time": journal_bytes({"2026-10-17": [VALID | {"at": "tadi malam"}]}),
    "at without an offset": journal_bytes({"2026-10-17": [VALID | {"at": "2026-10-17T21:00"}]}),
    "at a number": journal_bytes({"2026-10-17": [VALID | {"at": 20261017}]}),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_journal_starts_empty(
    tmp_path, caplog, content
):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.journal"):
        assert store.notes() == {}

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_the_log_never_repeats_a_note_of_a_corrupt_file(tmp_path, caplog):
    store = make_store(tmp_path)
    store.path.write_bytes(journal_bytes({"2026-10-17": [{"text": "RAHASIAKU", "at": "x"}]}))

    with caplog.at_level(logging.ERROR, logger="waifu.journal"):
        store.notes()

    assert "RAHASIAKU" not in caplog.text


def test_a_corrupt_file_is_never_overwritten_by_a_later_note(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    store.add("satu")

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert texts(store) == ["satu"]


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    store.path.write_bytes(b"third")

    assert store.notes() == {}

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(journal.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        store.add("satu")

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["journal.json"]


# --- the nightly question ------------------------------------------------------------------------

HER_NAMES = [AssistantConfig().name, *AssistantConfig().wake_words]


def test_the_question_says_it_is_hers_and_how_the_answer_is_written():
    assert JOURNAL_PROMPT.startswith("Ini bukan ucapan Master:")
    for guidance in [
        "sekali sehari",
        "journal_read untuk hari ini saja, tanpa tool lain",
        "satu kalimat pendek bagaimana hari Master",
        "apakah ada yang mau ditambahkan",
        "jangan menulis apa pun dulu",
        "Kalau Master menjawab pertanyaan ini di ucapan berikutnya",
        "kata-kata Master sendiri lewat journal_write",
        "kalau Master tidak mau bercerita, jangan simpan apa pun",
    ]:
        assert guidance in JOURNAL_PROMPT


def test_the_question_never_counts_as_master_saying_yes():
    # The brain hands the prompt to the confirmation gate as this turn's words: a yes here
    # could confirm a PC shutdown Master was asked about just before the question.
    assert is_yes(JOURNAL_PROMPT, HER_NAMES) is False


def test_the_question_never_lets_her_look_at_the_screen():
    captured: list[int] = []

    def capture(max_side: int) -> ScreenImage:
        captured.append(max_side)
        return ScreenImage(png=b"\x89PNG", width=1, height=1)

    screen = ScreenTools(capture=capture, names=HER_NAMES)

    screen.begin_turn(JOURNAL_PROMPT)
    result, is_error = screen.run("look_at_screen", {})
    assert is_error is True
    assert "tidak meminta melihat layar" in json.loads(result)["message"]
    assert captured == []

    # The same tools do look when Master's own words ask, so the check above is not vacuous.
    screen.begin_turn("Cyrene, lihat layarku")
    assert screen.run("look_at_screen", {})[1] is False


def test_the_question_never_lets_her_open_a_song():
    # The question comes by itself, maybe while Master plays a fullscreen game: a browser
    # opening would take the focus from it. The opener only records.
    opened: list[str] = []
    music = MusicTools(service="youtube", names=HER_NAMES, links=True, startfile=opened.append)

    music.begin_turn(JOURNAL_PROMPT)
    result, is_error = music.run("play_youtube", {"query": "lofi"})
    assert is_error is True
    assert "tidak meminta lagu" in json.loads(result)["message"]
    assert opened == []

    music.begin_turn("Cyrene, putar lagu lofi")
    assert music.run("play_youtube", {"query": "lofi"})[1] is False


class Requests:
    """Stands in for `assistant.ask_aloud`: records each prompt, until when it may be said, and
    what the state file said at that moment."""

    def __init__(self, state_path, error: Exception | None = None) -> None:
        self.state_path = state_path
        self.error = error
        self.prompts: list[str] = []
        self.expiries: list[datetime] = []
        self.states_seen: list[object] = []

    def __call__(self, prompt: str, expires_at: datetime) -> None:
        self.prompts.append(prompt)
        self.expiries.append(expires_at)
        self.states_seen.append(read_json(self.state_path))
        if self.error is not None:
            raise self.error


class Evening:
    """A timer over a state file in tmp_path, a fake WIB clock, and a fake request."""

    def __init__(self, tmp_path, *, ask_at=time(21, 0), error: Exception | None = None) -> None:
        self.config = JournalConfig(ask_at=ask_at, state_path=tmp_path / "journal_question.json")
        self.state_path = self.config.state_path
        self.clock = Clock()
        self.requests = Requests(self.state_path, error)
        self.restart()

    def restart(self) -> None:
        """A new timer on the same state file, like `waifu run` started again."""
        self.timer = JournalTimer(self.config, request=self.requests, now=self.clock)

    def tick_at(self, hour: int, minute: int = 0, *, day: int = 17) -> None:
        self.clock.current = datetime(2026, 10, day, hour, minute, tzinfo=WIB)
        self.timer.tick_once()


def test_the_first_tick_from_ask_at_asks_once_that_day(tmp_path):
    evening = Evening(tmp_path)

    evening.tick_at(21, 0)
    evening.tick_at(21, 1)
    evening.tick_at(23, 59)

    assert evening.requests.prompts == [JOURNAL_PROMPT]
    assert read_json(evening.state_path) == {"version": 1, "last_date": "2026-10-17"}


def test_the_question_may_be_asked_until_midnight_wib_that_ends_its_day(tmp_path, caplog):
    # Held past midnight, she would ask about "hari ini" on the new date.
    evening = Evening(tmp_path)

    with caplog.at_level(logging.INFO, logger="waifu.prompt_timer"):
        evening.tick_at(23, 59)

    [expiry] = evening.requests.expiries
    assert expiry == datetime(2026, 10, 18, 0, 0, tzinfo=WIB)
    assert expiry.tzinfo is WIB
    assert (
        "Asking for the nightly journal question of 2026-10-17 (said until 2026-10-18 00:00)"
        in caplog.text
    )


def test_the_day_is_saved_before_the_question_so_a_crash_never_asks_twice(tmp_path):
    evening = Evening(tmp_path)

    evening.tick_at(21, 30)

    assert evening.requests.states_seen == [{"version": 1, "last_date": "2026-10-17"}]


@pytest.mark.parametrize(
    ("hour", "minute", "asks"),
    [(0, 30, False), (12, 0, False), (20, 59, False), (21, 0, True), (23, 59, True)],
    ids=["after midnight", "noon", "just before", "exactly ask_at", "just before midnight"],
)
def test_it_asks_only_from_ask_at_until_midnight(tmp_path, hour, minute, asks):
    evening = Evening(tmp_path)

    evening.tick_at(hour, minute)

    assert evening.requests.prompts == ([JOURNAL_PROMPT] if asks else [])
    assert evening.state_path.exists() is asks


def test_a_pc_turned_on_late_asks_when_it_comes(tmp_path):
    evening = Evening(tmp_path, ask_at=time(19, 0))

    evening.tick_at(22, 45)

    assert evening.requests.prompts == [JOURNAL_PROMPT]


def test_a_restart_the_same_night_never_asks_again(tmp_path):
    evening = Evening(tmp_path)
    evening.tick_at(21, 0)

    evening.restart()
    evening.tick_at(22, 0)

    assert evening.requests.prompts == [JOURNAL_PROMPT]


def test_the_next_night_asks_again_and_not_after_midnight(tmp_path):
    evening = Evening(tmp_path)
    evening.tick_at(21, 0)

    evening.tick_at(0, 30, day=18)
    assert evening.requests.prompts == [JOURNAL_PROMPT]
    evening.tick_at(21, 0, day=18)

    assert evening.requests.prompts == [JOURNAL_PROMPT, JOURNAL_PROMPT]


def test_a_question_that_fails_is_logged_and_not_asked_again_that_day(tmp_path, caplog):
    evening = Evening(tmp_path, error=RuntimeError("the voice loop is gone"))

    with caplog.at_level(logging.ERROR, logger="waifu.prompt_timer"):
        evening.tick_at(21, 0)
        evening.tick_at(21, 1)

    assert evening.requests.prompts == [JOURNAL_PROMPT]
    assert "nightly journal question" in caplog.text


def test_the_timer_needs_ask_at():
    with pytest.raises(ValueError, match="ask_at"):
        JournalTimer(JournalConfig(), request=lambda prompt, expires_at: None)


def test_the_thread_is_named_journal_and_stops_at_once(tmp_path):
    asked = threading.Event()
    config = JournalConfig(ask_at=time(21, 0), state_path=tmp_path / "journal_question.json")
    timer = JournalTimer(
        config, request=lambda prompt, expires_at: asked.set(), now=Clock(), tick_seconds=3600
    )

    with timer:
        assert asked.wait(5)
        assert any(thread.name == "journal" for thread in threading.enumerate())

    assert not any(thread.name == "journal" for thread in threading.enumerate())
