"""Master's game start reminders (v1.53): the store, and what she says when a game starts.

Every store is a real file in tmp_path with a clock the test sets; the teller gets a fake `tell`
and never speaks or pushes.
"""

import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from waifu import app_reminders
from waifu.app_reminders import (
    AppReminder,
    AppReminderError,
    AppReminderStore,
    AppReminderTeller,
    reminder_line,
)
from waifu.finance.values import WIB

NOW = datetime(2026, 10, 24, 20, 15, 30, 123456, tzinfo=WIB)  # a Saturday evening
STAMP = NOW.replace(microsecond=0)
CORRUPT_NAME = "app_reminders.json.corrupt-20261024-201530"


class Clock:
    """A settable aware clock."""

    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


def make_store(tmp_path, clock=None, **limits) -> AppReminderStore:
    return AppReminderStore(tmp_path / "app_reminders.json", now=clock or Clock(), **limits)


def saved(store: AppReminderStore) -> object:
    return json.loads(store.path.read_text(encoding="utf-8"))


def entry(reminder_id, game, text, every_time=False, created="2026-10-24T19:00:00+07:00"):
    return {
        "id": reminder_id,
        "game": game,
        "text": text,
        "every_time": every_time,
        "created": created,
    }


def write_reminders(path, reminders, next_id) -> None:
    path.write_text(
        json.dumps({"version": 1, "next_id": next_id, "reminders": reminders}), encoding="utf-8"
    )


def texts(reminders) -> list[str]:
    return [reminder.text for reminder in reminders]


def test_a_reminder_has_a_game_a_text_and_whether_it_is_said_every_time():
    reminder = AppReminder("g1", "hsr", "farming relic", False, datetime(2026, 10, 24, tzinfo=UTC))

    assert (reminder.id, reminder.game, reminder.text, reminder.every_time) == (
        "g1",
        "hsr",
        "farming relic",
        False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        reminder.text = "lain"  # type: ignore[misc]


# --- reading -----------------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_list_and_no_call_creates_it(tmp_path):
    store = AppReminderStore(tmp_path / "data" / "app_reminders.json", now=Clock())

    assert store.reminders() == []
    assert store.for_game("hsr") == []
    assert store.remove("g1") is None
    store.drop(["g1"])

    assert not (tmp_path / "data").exists()


def test_every_call_rereads_the_file(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", "farming relic")

    write_reminders(store.path, [entry("g4", "zzz", "claim battle pass")], next_id=5)

    assert texts(store.reminders()) == ["claim battle pass"]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    store = make_store(tmp_path)
    content = json.dumps({"version": 1, "next_id": 2, "reminders": [entry("g1", "hsr", "daily")]})
    store.path.write_bytes(b"\xef\xbb\xbf" + content.encode())

    assert texts(store.reminders()) == ["daily"]


# --- adding ------------------------------------------------------------------------------------


def test_add_returns_the_first_reminder_once_by_default_stamped_to_the_second(tmp_path):
    reminder = make_store(tmp_path).add("hsr", "farming relic")

    assert reminder == AppReminder("g1", "hsr", "farming relic", False, STAMP)


def test_add_keeps_a_reminder_for_every_start(tmp_path):
    reminder = make_store(tmp_path).add("genshin", "cek ekspedisi", every_time=True)

    assert (reminder.game, reminder.every_time) == ("genshin", True)


def test_add_writes_the_file_in_readable_utf8_with_aware_times(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", "farming relic ✨")
    store.add("genshin", "cek ekspedisi", every_time=True)

    assert saved(store) == {
        "version": 1,
        "next_id": 3,
        "reminders": [
            entry("g1", "hsr", "farming relic ✨", created="2026-10-24T20:15:30+07:00"),
            entry("g2", "genshin", "cek ekspedisi", True, created="2026-10-24T20:15:30+07:00"),
        ],
    }
    assert "✨" in store.path.read_text(encoding="utf-8")


def test_reminders_come_in_the_order_they_were_made_and_for_game_keeps_one_game(tmp_path):
    store = make_store(tmp_path)
    store.add("zzz", "claim battle pass")
    store.add("hsr", "farming relic")
    store.add("zzz", "cek toko", every_time=True)

    assert [r.id for r in store.reminders()] == ["g1", "g2", "g3"]
    assert texts(store.for_game("zzz")) == ["claim battle pass", "cek toko"]
    assert store.for_game("genshin") == []


def test_text_is_stripped_and_its_whitespace_collapsed(tmp_path):
    reminder = make_store(tmp_path).add("hsr", "  farming \n relic\t ")

    assert reminder.text == "farming relic"


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_an_empty_reminder_is_refused(tmp_path, text):
    store = make_store(tmp_path)

    with pytest.raises(AppReminderError, match="^Pengingatnya kosong\\.$"):
        store.add("hsr", text)

    assert not store.path.exists()


def test_a_reminder_up_to_max_chars_is_kept_and_a_longer_one_refused(tmp_path):
    store = make_store(tmp_path, max_chars=20)
    store.add("hsr", "x" * 20)

    with pytest.raises(AppReminderError) as refused:
        store.add("hsr", "y" * 21)

    assert str(refused.value) == "Pengingatnya terlalu panjang, paling banyak 20 karakter."
    assert texts(store.reminders()) == ["x" * 20]


def test_a_reminder_that_holds_a_secret_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(AppReminderError) as refused:
        store.add("hsr", "login pakai password Rahasia123")

    assert str(refused.value) == (
        "Pengingat itu terlihat seperti rahasia, misalnya kata sandi, PIN, token, atau nomor "
        "kartu, jadi tidak disimpan."
    )
    assert not store.path.exists()


def test_the_31st_reminder_is_refused_honestly_and_a_removal_makes_room(tmp_path):
    store = make_store(tmp_path)
    for number in range(30):
        store.add("hsr", f"tugas {number}")

    with pytest.raises(AppReminderError) as refused:
        store.add("hsr", "satu lagi")
    store.remove("g1")
    reminder = store.add("hsr", "satu lagi")

    assert str(refused.value) == "Pengingat game sudah penuh, 30 pengingat. Hapus yang lama dulu."
    assert reminder.id == "g31"


def test_a_game_that_is_not_tracked_anywhere_is_a_value_error(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="game"):
        store.add("wuwa", "daily")  # type: ignore[arg-type]

    assert not store.path.exists()


def test_the_refusal_is_an_exception_with_its_message():
    assert str(AppReminderError("Pengingatnya kosong.")) == "Pengingatnya kosong."


@pytest.mark.parametrize(
    "call",
    [
        lambda store: store.add("hsr", "daily"),
        lambda store: store.reminders(),
        lambda store: store.for_game("hsr"),
        lambda store: store.remove("g1"),
        lambda store: store.drop(["g1"]),
    ],
)
def test_a_naive_clock_is_refused_before_it_could_spoil_the_file(tmp_path, call):
    store = make_store(tmp_path, clock=Clock(NOW.replace(tzinfo=None)))

    with pytest.raises(ValueError, match="aware"):
        call(store)


# --- removing ----------------------------------------------------------------------------------


def test_remove_deletes_one_reminder_and_returns_it(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", "farming relic")
    store.add("zzz", "claim battle pass")

    removed = store.remove("g1")

    assert removed is not None and removed.text == "farming relic"
    assert [r.id for r in store.reminders()] == ["g2"]


def test_remove_of_an_unknown_id_gives_none_and_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", "farming relic")
    before = store.path.read_bytes()

    assert store.remove("g9") is None
    assert store.path.read_bytes() == before


def test_drop_removes_the_ids_still_there_in_one_write(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("zzz", "claim battle pass")
    store.add("zzz", "cek toko", every_time=True)
    store.add("zzz", "beli bangboo")
    writes: list[object] = []
    real_write = app_reminders.write_json_atomic

    def counting(path, payload):
        writes.append(payload)
        real_write(path, payload)

    monkeypatch.setattr(app_reminders, "write_json_atomic", counting)
    store.drop(["g1", "g3", "g7"])

    assert [r.id for r in store.reminders()] == ["g2"]
    assert len(writes) == 1


def test_drop_of_ids_no_longer_there_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", "farming relic")
    before = store.path.read_bytes()

    store.drop(["g5"])
    store.drop([])

    assert store.path.read_bytes() == before


def test_a_removed_id_is_never_reused_also_after_a_restart(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", "farming relic")
    store.add("hsr", "daily")
    store.drop(["g1", "g2"])

    reminder = make_store(tmp_path).add("hsr", "warp")

    assert reminder.id == "g3"


def test_an_id_written_by_hand_beyond_next_id_is_never_handed_out_again(tmp_path):
    store = make_store(tmp_path)
    write_reminders(store.path, [entry("g7", "hsr", "daily")], next_id=2)

    assert store.add("hsr", "warp").id == "g8"


# --- corrupt files -----------------------------------------------------------------------------

VALID = entry("g1", "hsr", "farming relic")


def reminder_bytes(reminders, next_id=2, **changes) -> bytes:
    return json.dumps({"version": 1, "next_id": next_id, "reminders": reminders} | changes).encode()


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "next_id": 2, "reminders": [\xff\xfe]}',
    "a list": b"[]",
    "missing reminders": json.dumps({"version": 1, "next_id": 2}).encode(),
    "an extra key": reminder_bytes([], notes=[]),
    "another version": reminder_bytes([], version=2),
    "version true": reminder_bytes([], version=True),
    "next_id text": reminder_bytes([], next_id="2"),
    "next_id zero": reminder_bytes([], next_id=0),
    "reminders an object": reminder_bytes({}),
    "reminder a string": reminder_bytes(["farming relic"]),
    "reminder without every_time": reminder_bytes(
        [{k: v for k, v in VALID.items() if k != "every_time"}]
    ),
    "reminder with an extra key": reminder_bytes([VALID | {"at": "21:00"}]),
    "numeric id": reminder_bytes([VALID | {"id": 1}]),
    "a task id": reminder_bytes([VALID | {"id": "t1"}]),
    "id g0": reminder_bytes([VALID | {"id": "g0"}]),
    "unknown game": reminder_bytes([VALID | {"game": "wuwa"}]),
    "numeric text": reminder_bytes([VALID | {"text": 42}]),
    "every_time a string": reminder_bytes([VALID | {"every_time": "yes"}]),
    "every_time a number": reminder_bytes([VALID | {"every_time": 1}]),
    "created without an offset": reminder_bytes([VALID | {"created": "2026-10-24T19:00:00"}]),
    "created null": reminder_bytes([VALID | {"created": None}]),
    "duplicate ids": reminder_bytes([VALID, VALID | {"text": "daily"}], next_id=3),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_list_starts_empty(tmp_path, caplog, content):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.app_reminders"):
        assert store.reminders() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_corrupt_file_is_never_overwritten_by_a_later_reminder(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    reminder = store.add("hsr", "farming relic")

    assert reminder.id == "g1"
    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert texts(store.reminders()) == ["farming relic"]


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    store.path.write_bytes(b"second")

    assert store.reminders() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(app_reminders.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        store.add("hsr", "farming relic")

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["app_reminders.json"]


def test_a_reminder_s_time_is_kept_with_its_offset(tmp_path):
    store = make_store(tmp_path)
    write_reminders(
        store.path, [entry("g1", "hsr", "daily", created="2026-10-24T12:00:00+00:00")], next_id=2
    )

    [reminder] = store.reminders()

    assert reminder.created == datetime(2026, 10, 24, 12, tzinfo=UTC)
    assert reminder.created.utcoffset() == timedelta(0)


# --- what she says when a game starts ------------------------------------------------------------


def test_the_line_names_the_game_she_saw_open_and_what_not_to_forget():
    assert reminder_line("Master", "hsr", ["farming relic"]) == (
        "Master, kamu baru buka HSR, jangan lupa farming relic."
    )


def test_she_calls_the_owner_as_asked_at_sentence_start():
    assert reminder_line("sayang", "genshin", ["cek ekspedisi"]) == (
        "Sayang, kamu baru buka Genshin, jangan lupa cek ekspedisi."
    )


def test_several_reminders_come_in_one_line_joined_as_she_says_them():
    assert reminder_line("Master", "zzz", ["claim battle pass", "cek toko"]) == (
        "Master, kamu baru buka ZZZ, jangan lupa claim battle pass dan cek toko."
    )
    assert reminder_line("Master", "zzz", ["claim battle pass", "cek toko", "beli bensin"]) == (
        "Master, kamu baru buka ZZZ, jangan lupa claim battle pass, cek toko, dan beli bensin."
    )


def test_a_text_s_own_full_stop_is_dropped_and_a_repeat_is_said_once():
    assert reminder_line("Master", "hsr", ["farming relic.", "daily!", "farming relic"]) == (
        "Master, kamu baru buka HSR, jangan lupa farming relic dan daily."
    )


def test_a_repeat_in_another_case_is_said_once_as_it_was_first_made():
    assert reminder_line("Master", "zzz", ["Claim battle pass.", "claim battle pass"]) == (
        "Master, kamu baru buka ZZZ, jangan lupa Claim battle pass."
    )


@pytest.mark.parametrize(
    "text", ["jangan lupa daily", "Jangan lupa, daily.", "inget daily", "Ingat: daily"]
)
def test_a_text_opening_with_what_she_says_anyway_is_not_said_twice(text):
    assert reminder_line("Master", "hsr", [text, "daily"]) == (
        "Master, kamu baru buka HSR, jangan lupa daily."
    )


def test_a_text_that_is_nothing_but_those_words_is_still_said():
    assert reminder_line("Master", "hsr", ["Ingat!"]) == (
        "Master, kamu baru buka HSR, jangan lupa Ingat."
    )


@pytest.mark.parametrize("text", ["jangan top up dulu", "jangan lupa jangan top up dulu"])
def test_something_not_to_do_is_said_as_a_warning_not_a_thing_to_do(text):
    assert reminder_line("Master", "genshin", [text]) == (
        "Master, kamu baru buka Genshin, ingat ya, jangan top up dulu."
    )


def test_things_not_to_do_come_after_the_things_to_do_in_a_sentence_of_their_own():
    texts = ["cek ekspedisi", "Jangan top up dulu", "claim daily", "jangan begadang"]

    assert reminder_line("Master", "genshin", texts) == (
        "Master, kamu baru buka Genshin, jangan lupa cek ekspedisi dan claim daily. "
        "Ingat ya, Jangan top up dulu dan jangan begadang."
    )


class Told:
    """Stands in for her reminder path: what she would say, with its emotion."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __call__(self, text: str, emotion: str) -> None:
        self.lines.append((text, emotion))


def make_teller(store: AppReminderStore, told: Told) -> AppReminderTeller:
    return AppReminderTeller(store, tell=told, owner_call="Master")


def test_a_start_tells_every_reminder_of_that_game_in_one_happy_line(tmp_path):
    store, told = make_store(tmp_path), Told()
    store.add("zzz", "claim battle pass")
    store.add("hsr", "farming relic")
    store.add("zzz", "cek toko", every_time=True)

    make_teller(store, told).game_started("zzz")

    assert told.lines == [
        ("Master, kamu baru buka ZZZ, jangan lupa claim battle pass dan cek toko.", "happy")
    ]


def test_a_once_reminder_is_gone_after_its_start_and_an_every_time_one_stays(tmp_path):
    store, told = make_store(tmp_path), Told()
    store.add("zzz", "claim battle pass")
    store.add("hsr", "farming relic")
    store.add("zzz", "cek toko", every_time=True)
    teller = make_teller(store, told)

    teller.game_started("zzz")
    teller.game_started("zzz")

    assert [r.id for r in store.reminders()] == ["g2", "g3"]
    assert told.lines[1] == ("Master, kamu baru buka ZZZ, jangan lupa cek toko.", "happy")


def test_a_start_of_a_game_without_reminders_says_nothing_and_writes_nothing(tmp_path):
    store, told = make_store(tmp_path), Told()
    store.add("hsr", "farming relic")
    before = store.path.read_bytes()

    make_teller(store, told).game_started("genshin")

    assert told.lines == []
    assert store.path.read_bytes() == before


class BrokenStore(AppReminderStore):
    """A real store whose reads or drops fail, as a broken disk would."""

    def __init__(self, path, *, failing_read=False, failing_drop=False) -> None:
        super().__init__(path, now=Clock())
        self.failing_read, self.failing_drop = failing_read, failing_drop

    def for_game(self, game):
        if self.failing_read:
            raise OSError("the disk is gone")
        return super().for_game(game)

    def drop(self, reminder_ids) -> None:
        if self.failing_drop:
            raise OSError("the disk is full")
        super().drop(reminder_ids)


def test_a_failed_read_says_nothing_and_is_logged(tmp_path, caplog):
    store, told = BrokenStore(tmp_path / "app_reminders.json", failing_read=True), Told()

    with caplog.at_level(logging.WARNING, logger="waifu.app_reminders"):
        make_teller(store, told).game_started("hsr")

    assert told.lines == []
    assert "Could not read the reminders for a game's start" in caplog.text


def test_a_once_reminder_that_cannot_be_removed_is_still_said(tmp_path, caplog):
    # Master asked to be reminded: a repeat at the next start is better than none at all.
    store, told = BrokenStore(tmp_path / "app_reminders.json"), Told()
    store.add("hsr", "farming relic")
    store.failing_drop = True

    with caplog.at_level(logging.WARNING, logger="waifu.app_reminders"):
        make_teller(store, told).game_started("hsr")

    assert told.lines == [("Master, kamu baru buka HSR, jangan lupa farming relic.", "happy")]
    assert texts(store.reminders()) == ["farming relic"]
    assert "may be said again at its next start" in caplog.text


def test_the_log_counts_the_reminders_and_never_says_them_above_debug(tmp_path, caplog):
    store, told = make_store(tmp_path), Told()
    store.add("hsr", "beli hadiah buat Ayu")

    with caplog.at_level(logging.INFO, logger="waifu.app_reminders"):
        make_teller(store, told).game_started("hsr")

    assert "Saying 1 reminder(s) at a game's start" in caplog.text
    assert "Ayu" not in caplog.text
