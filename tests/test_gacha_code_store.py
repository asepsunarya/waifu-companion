import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest

from waifu.gacha import code_store as store_module
from waifu.gacha.code_store import CodeStore
from waifu.gacha.codes import RedeemCode, RedeemResult

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 17, 9, 30, 15, tzinfo=WIB)
CORRUPT_NAME = "redeem_codes.json.corrupt-20261017-093015"

# From the source's real answer for HSR on 2026-10-17.
JADE_60 = ("5S6ZHRWTDNJB", "60 stellar jade")
JADE_30 = (
    "4TKSX77Y58QK",
    "30 stellar jade, three traveler’s guides, five condensed aether, four lost gold "
    "fragments, and 20k credits",
)
GIFT = (
    "STARRAILGIFT",
    "50 stellar jade, 10k credits, two traveler’s guides, and five bottled soda",
)
GENSHIN_GIFT = ("GENSHINGIFT", "50 primogems and three hero’s wit")


class Clock:
    """A settable aware clock."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(tmp_path, clock: Clock | None = None) -> CodeStore:
    return CodeStore(tmp_path / "redeem_codes.json", now=clock or Clock())


def code(listed: tuple[str, str], game: str = "hsr", first_seen: datetime = NOW) -> RedeemCode:
    return RedeemCode(game, listed[0], listed[1], first_seen)  # type: ignore[arg-type]


def result(listed: tuple[str, str], outcome: str, game: str = "hsr") -> RedeemResult:
    return RedeemResult(game, listed[0], outcome, "Pesan untuk Master.")  # type: ignore[arg-type]


def saved(store: CodeStore) -> dict:
    return json.loads(store.path.read_text(encoding="utf-8"))


# --- reading -----------------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_store_and_reading_creates_nothing(tmp_path):
    folder = tmp_path / "data"
    store = CodeStore(folder / "redeem_codes.json", now=Clock())

    assert store.unredeemed("hsr") == []
    assert store.unannounced("hsr") == []
    assert store.recent("hsr", 5) == []
    assert not folder.exists()


def test_every_call_rereads_the_file_so_another_process_sees_the_changes(tmp_path):
    reader = make_store(tmp_path)
    assert reader.unredeemed("hsr") == []

    make_store(tmp_path).add("hsr", [JADE_60], NOW)

    assert reader.unredeemed("hsr") == [code(JADE_60)]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    store.path.write_bytes(b"\xef\xbb\xbf" + store.path.read_bytes())

    assert store.unredeemed("hsr") == [code(JADE_60)]


# --- adding codes ------------------------------------------------------------------------------


def test_added_codes_are_new_and_saved_in_the_file_shape(tmp_path):
    store = make_store(tmp_path)

    new = store.add("hsr", [JADE_60, JADE_30], NOW)

    assert new == [code(JADE_60), code(JADE_30)]
    assert saved(store) == {
        "version": 1,
        "games": {
            "hsr": [
                {
                    "code": "5S6ZHRWTDNJB",
                    "rewards": "60 stellar jade",
                    "first_seen": "2026-10-17T09:30:15+07:00",
                    "last_seen": "2026-10-17T09:30:15+07:00",
                    "listed": True,
                    "named": False,
                    "outcome": None,
                    "outcome_at": None,
                    "announced": False,
                },
                {
                    "code": "4TKSX77Y58QK",
                    "rewards": JADE_30[1],
                    "first_seen": "2026-10-17T09:30:15+07:00",
                    "last_seen": "2026-10-17T09:30:15+07:00",
                    "listed": True,
                    "named": False,
                    "outcome": None,
                    "outcome_at": None,
                    "announced": False,
                },
            ]
        },
    }


def test_only_codes_not_seen_before_are_new_and_they_keep_their_first_seen(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    later = NOW + timedelta(hours=6)

    new = store.add("hsr", [JADE_60, GIFT], later)

    assert new == [code(GIFT, first_seen=later)]
    assert store.unredeemed("hsr") == [code(GIFT, first_seen=later), code(JADE_60)]
    assert [entry["last_seen"] for entry in saved(store)["games"]["hsr"]] == [
        later.isoformat(),
        later.isoformat(),
    ]


def test_codes_to_redeem_and_to_announce_come_newest_first_in_the_source_s_order(tmp_path):
    # The source lists its newest codes first; a later read's new code comes before them.
    store = make_store(tmp_path)
    store.add("hsr", [JADE_30, GIFT], NOW)
    later = NOW + timedelta(days=20)

    store.add("hsr", [JADE_60, JADE_30, GIFT], later)

    newest_first = [code(JADE_60, first_seen=later), code(JADE_30), code(GIFT)]
    assert store.unredeemed("hsr") == newest_first
    assert store.unannounced("hsr") == newest_first


def test_a_code_listed_twice_in_one_read_is_new_once(tmp_path):
    store = make_store(tmp_path)

    assert store.add("hsr", [JADE_60, JADE_60], NOW) == [code(JADE_60)]
    assert store.unredeemed("hsr") == [code(JADE_60)]


def test_codes_are_kept_per_game(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)

    assert store.add("genshin", [GENSHIN_GIFT, JADE_60], NOW) == [
        code(GENSHIN_GIFT, "genshin"),
        code(JADE_60, "genshin"),
    ]
    assert store.unredeemed("hsr") == [code(JADE_60)]
    assert store.unredeemed("zzz") == []


def test_codes_are_normalized_when_added(tmp_path):
    store = make_store(tmp_path)

    assert store.add("hsr", [(" starrailgift ", GIFT[1])], NOW) == [code(GIFT)]
    assert store.add("hsr", [("STARRAILGIFT", GIFT[1])], NOW) == []


def test_newer_rewards_text_replaces_the_old_and_an_empty_one_keeps_it(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [("STARRAILGIFT", "")], NOW)  # listed without its rewards

    store.add("hsr", [GIFT], NOW)
    assert store.unredeemed("hsr") == [code(GIFT)]
    store.add("hsr", [("STARRAILGIFT", "")], NOW)
    assert store.unredeemed("hsr") == [code(GIFT)]


def test_a_read_with_nothing_new_still_records_when_the_codes_were_seen(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    later = NOW + timedelta(hours=6)

    assert store.add("hsr", [JADE_60], later) == []
    assert saved(store)["games"]["hsr"][0]["last_seen"] == later.isoformat()


def test_an_empty_read_creates_no_file(tmp_path):
    store = make_store(tmp_path)

    assert store.add("hsr", [], NOW) == []
    assert not store.path.exists()


def test_adding_the_same_codes_at_the_same_time_writes_nothing(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)

    def refused_replace(src, dst):
        raise AssertionError("nothing new to save")

    monkeypatch.setattr(store_module.os, "replace", refused_replace)

    assert store.add("hsr", [JADE_60], NOW) == []


@pytest.mark.parametrize(
    ("game", "listed", "now"),
    [
        ("hsr", [("ABCD", "too short")], NOW),
        ("hsr", [("HSR-2026", "")], NOW),
        ("hsr", [("STARRAILGIFT", "x" * 201)], NOW),
        ("hsr", [JADE_60], NOW.replace(tzinfo=None)),
        ("honkai3", [JADE_60], NOW),
    ],
    ids=["code too short", "code with a dash", "rewards too long", "naive time", "unknown game"],
)
def test_what_the_file_cannot_hold_is_refused_and_nothing_is_written(tmp_path, game, listed, now):
    store = make_store(tmp_path)

    with pytest.raises(ValueError):
        store.add(game, [GIFT, *listed], now)

    assert not store.path.exists()


# --- codes the source no longer lists ----------------------------------------------------------


def test_a_code_the_latest_read_left_out_is_no_longer_to_redeem_or_to_announce(tmp_path):
    # The source drops a code once it finds it no longer works (an expired livestream code).
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)

    store.add("hsr", [GIFT], NOW + timedelta(days=2))

    assert store.unredeemed("hsr") == [code(GIFT)]
    assert store.unannounced("hsr") == [code(GIFT)]
    assert [(entry["code"], entry["listed"]) for entry in saved(store)["games"]["hsr"]] == [
        ("5S6ZHRWTDNJB", False),
        ("STARRAILGIFT", True),
    ]


def test_a_code_listed_again_is_not_new_and_is_to_redeem_again(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)
    store.add("hsr", [GIFT], NOW + timedelta(days=2))

    assert store.add("hsr", [JADE_60, GIFT], NOW + timedelta(days=3)) == []
    assert store.unredeemed("hsr") == [code(JADE_60), code(GIFT)]


def test_reading_one_game_never_changes_what_another_game_has_to_redeem(tmp_path):
    store = make_store(tmp_path)
    store.add("genshin", [GENSHIN_GIFT], NOW)

    store.add("hsr", [JADE_60], NOW + timedelta(days=1))

    assert store.unredeemed("genshin") == [code(GENSHIN_GIFT, "genshin")]


# --- codes Master named ------------------------------------------------------------------------


def test_codes_master_named_stay_to_redeem_though_no_read_lists_them_and_count_as_told(tmp_path):
    store = make_store(tmp_path)
    store.add_named("hsr", [" mastercode1 "], NOW)
    later = NOW + timedelta(hours=6)

    store.add("hsr", [GIFT], later)

    assert store.unredeemed("hsr") == [code(GIFT, first_seen=later), code(("MASTERCODE1", ""))]
    assert store.unannounced("hsr") == [code(GIFT, first_seen=later)]
    [entry, _] = saved(store)["games"]["hsr"]
    assert (entry["code"], entry["named"], entry["announced"]) == ("MASTERCODE1", True, True)


def test_a_listed_code_master_names_keeps_its_rewards_and_stays_to_redeem_once_dropped(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)

    store.add_named("hsr", ["5S6ZHRWTDNJB"], NOW + timedelta(hours=1))
    store.add("hsr", [GIFT], NOW + timedelta(days=2))

    assert store.unredeemed("hsr") == [code(JADE_60), code(GIFT)]
    assert store.unannounced("hsr") == [code(GIFT)]  # Master knows the codes they name


def test_a_named_code_the_source_lists_later_gets_its_rewards_and_is_not_new(tmp_path):
    store = make_store(tmp_path)
    store.add_named("hsr", ["STARRAILGIFT"], NOW)

    assert store.add("hsr", [GIFT], NOW + timedelta(hours=6)) == []
    assert store.unredeemed("hsr") == [code(GIFT)]


@pytest.mark.parametrize(
    ("game", "named", "now"),
    [
        ("hsr", ["ABCD"], NOW),
        ("hsr", ["STARRAILGIFT"], NOW.replace(tzinfo=None)),
        ("honkai3", ["STARRAILGIFT"], NOW),
    ],
    ids=["code too short", "naive time", "unknown game"],
)
def test_named_codes_the_file_cannot_hold_are_refused_and_nothing_is_written(
    tmp_path, game, named, now
):
    store = make_store(tmp_path)

    with pytest.raises(ValueError):
        store.add_named(game, ["GOODCODE1", *named], now)

    assert not store.path.exists()


# --- forgetting --------------------------------------------------------------------------------


def test_a_code_gone_from_the_source_for_60_days_is_forgotten(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)

    store.add("hsr", [GIFT], NOW + timedelta(days=59, hours=23, minutes=59))
    assert [entry["code"] for entry in saved(store)["games"]["hsr"]] == [
        "5S6ZHRWTDNJB",
        "STARRAILGIFT",
    ]
    store.add("hsr", [GIFT], NOW + timedelta(days=60))
    assert [entry["code"] for entry in saved(store)["games"]["hsr"]] == ["STARRAILGIFT"]


def test_a_forgotten_code_listed_again_is_new_again(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    store.add("hsr", [], NOW + timedelta(days=61))
    again = NOW + timedelta(days=62)

    assert store.add("hsr", [JADE_60], again) == [code(JADE_60, first_seen=again)]


def test_a_code_still_listed_after_a_long_pause_keeps_what_happened_to_it(tmp_path):
    # Cyrene was off for three months: the codes still listed are not new again.
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)
    store.record([result(GIFT, "redeemed")])
    store.mark_announced([code(JADE_60)])

    assert store.add("hsr", [JADE_60, GIFT], NOW + timedelta(days=90)) == []
    assert store.unredeemed("hsr") == [code(JADE_60)]
    assert store.unannounced("hsr") == []


def test_reading_one_game_never_forgets_another_games_codes(tmp_path):
    store = make_store(tmp_path)
    store.add("genshin", [GENSHIN_GIFT], NOW)

    store.add("hsr", [JADE_60], NOW + timedelta(days=90))

    assert store.unredeemed("genshin") == [code(GENSHIN_GIFT, "genshin")]


def test_when_the_last_code_of_a_game_is_forgotten_the_game_leaves_the_file(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    store.add("genshin", [GENSHIN_GIFT], NOW)

    store.add("hsr", [], NOW + timedelta(days=60))

    assert list(saved(store)["games"]) == ["genshin"]


# --- outcomes ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome", ["redeemed", "claimed", "expired", "invalid", "region", "level"]
)
def test_a_final_outcome_takes_the_code_off_the_list_to_redeem(tmp_path, outcome):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)

    store.record([result(JADE_60, outcome)])

    assert store.unredeemed("hsr") == [code(GIFT)]


@pytest.mark.parametrize("outcome", ["cooldown", "cookie", "error"])
def test_a_failed_attempt_leaves_the_code_to_redeem(tmp_path, outcome):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)

    store.record([result(JADE_60, outcome)])

    assert store.unredeemed("hsr") == [code(JADE_60)]
    assert store.recent("hsr", 5) == [(code(JADE_60), outcome)]


def test_outcomes_are_saved_with_when_they_were_recorded(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add("hsr", [JADE_60], NOW)
    clock.now = NOW + timedelta(minutes=5)

    store.record([result(JADE_60, "redeemed")])

    (entry,) = saved(store)["games"]["hsr"]
    assert (entry["outcome"], entry["outcome_at"]) == ("redeemed", "2026-10-17T09:35:15+07:00")


def test_a_failed_attempt_never_replaces_a_final_outcome(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    store.record([result(JADE_60, "redeemed")])

    store.record([result(JADE_60, "cookie")])

    assert store.unredeemed("hsr") == []
    assert store.recent("hsr", 5) == [(code(JADE_60), "redeemed")]


def test_a_later_outcome_replaces_an_earlier_one(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    store.record([result(JADE_60, "cooldown")])

    store.record([result(JADE_60, "redeemed")])

    assert store.recent("hsr", 5) == [(code(JADE_60), "redeemed")]


def test_an_outcome_for_a_code_not_kept_is_logged_and_skipped(tmp_path, caplog):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)

    with caplog.at_level(logging.WARNING, logger="waifu.gacha.code_store"):
        store.record([result(GIFT, "redeemed"), result(JADE_60, "redeemed", game="zzz")])

    assert store.recent("hsr", 5) == []
    assert store.recent("zzz", 5) == []
    assert "STARRAILGIFT" in caplog.text


def test_an_unknown_outcome_is_refused_and_nothing_is_recorded(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)

    with pytest.raises(ValueError):
        store.record([result(GIFT, "redeemed"), result(JADE_60, "lost")])

    assert store.recent("hsr", 5) == []


def test_a_naive_clock_is_refused_before_it_could_spoil_the_file(tmp_path):
    store = make_store(tmp_path, Clock(NOW.replace(tzinfo=None)))
    store.add("hsr", [JADE_60], NOW)

    with pytest.raises(ValueError):
        store.record([result(JADE_60, "redeemed")])

    assert store.unredeemed("hsr") == [code(JADE_60)]
    assert store.recent("hsr", 5) == []


def test_recent_gives_the_last_outcomes_newest_first(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add("hsr", [JADE_60, JADE_30, GIFT], NOW)
    store.record([result(GIFT, "claimed")])
    clock.now = NOW + timedelta(minutes=1)
    store.record([result(JADE_60, "redeemed")])
    clock.now = NOW + timedelta(minutes=2)
    store.record([result(JADE_30, "expired")])

    assert store.recent("hsr", 2) == [(code(JADE_30), "expired"), (code(JADE_60), "redeemed")]
    assert store.recent("hsr", 5) == [
        (code(JADE_30), "expired"),
        (code(JADE_60), "redeemed"),
        (code(GIFT), "claimed"),
    ]
    assert store.recent("genshin", 5) == []


# --- announcements -----------------------------------------------------------------------------


def test_new_codes_wait_to_be_announced_until_they_are_marked(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)
    assert store.unannounced("hsr") == [code(JADE_60), code(GIFT)]

    store.mark_announced([code(JADE_60)])

    assert store.unannounced("hsr") == [code(GIFT)]
    assert [entry["announced"] for entry in saved(store)["games"]["hsr"]] == [True, False]


def test_an_announced_code_stays_announced_after_a_restart(tmp_path):
    make_store(tmp_path).add("hsr", [JADE_60], NOW)
    make_store(tmp_path).mark_announced([code(JADE_60)])

    assert make_store(tmp_path).unannounced("hsr") == []


def test_a_code_already_settled_is_not_waiting_to_be_announced(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60, GIFT], NOW)

    store.record([result(JADE_60, "redeemed"), result(GIFT, "error")])

    assert store.unannounced("hsr") == [code(GIFT)]


def test_marking_codes_not_kept_changes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    before = store.path.read_bytes()

    store.mark_announced([code(GIFT), code(JADE_60, "genshin")])

    assert store.path.read_bytes() == before


# --- concurrency -------------------------------------------------------------------------------


def run_in_threads(target, count=8):
    errors = []

    def guarded(worker):
        try:
            target(worker)
        except Exception as exc:  # surfaced by the caller's assertion
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(worker,)) for worker in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_writes_from_two_stores_keep_every_code(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]

    def add(worker):
        for index in range(5):
            stores[worker % 2].add("hsr", [(f"CODE{worker}X{index}", "")], NOW)

    assert run_in_threads(add) == []
    assert len(saved(stores[0])["games"]["hsr"]) == 40


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    store = make_store(tmp_path)

    store.add("hsr", [JADE_60], NOW)

    assert [path.name for path in tmp_path.iterdir()] == ["redeem_codes.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("hsr", [JADE_60], NOW)
    before = store.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store_module.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.add("hsr", [GIFT], NOW)

    assert store.path.read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["redeem_codes.json"]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "kode redeem" / "kode Master.json"

    CodeStore(path, now=Clock()).add("hsr", [JADE_60], NOW)

    assert CodeStore(path, now=Clock()).unredeemed("hsr") == [code(JADE_60)]


# --- corrupt files -----------------------------------------------------------------------------

VALID_ENTRY = {
    "code": "5S6ZHRWTDNJB",
    "rewards": "60 stellar jade",
    "first_seen": "2026-10-17T09:30:15+07:00",
    "last_seen": "2026-10-17T09:30:15+07:00",
    "listed": True,
    "named": False,
    "outcome": None,
    "outcome_at": None,
    "announced": False,
}


def file_with(**changes):
    return json.dumps({"version": 1, "games": {}} | changes).encode()


def hsr_entries(*entries):
    return {"hsr": list(entries)}


def entry_with(**changes):
    return hsr_entries(VALID_ENTRY | changes)


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "games": {"\xff": []}}',
    "a list": b"[]",
    "missing games": json.dumps({"version": 1}).encode(),
    "an extra key": file_with(notes=[]),
    "another version": file_with(version=2),
    "version true": file_with(version=True),
    "games a list": file_with(games=[]),
    "unknown game": file_with(games={"honkai3": [VALID_ENTRY]}),
    "game codes an object": file_with(games={"hsr": VALID_ENTRY}),
    "entry a string": file_with(games=hsr_entries("5S6ZHRWTDNJB")),
    "entry without rewards": file_with(
        games=hsr_entries({key: value for key, value in VALID_ENTRY.items() if key != "rewards"})
    ),
    "entry with an extra key": file_with(games=entry_with(notes="")),
    "code lowercase": file_with(games=entry_with(code="starrailgift")),
    "code too short": file_with(games=entry_with(code="ABCD")),
    "code a number": file_with(games=entry_with(code=12345678)),
    "rewards a list": file_with(games=entry_with(rewards=["60 stellar jade"])),
    "rewards too long": file_with(games=entry_with(rewards="x" * 201)),
    "first seen not a time": file_with(games=entry_with(first_seen="kemarin")),
    "first seen without a zone": file_with(games=entry_with(first_seen="2026-10-17T09:30:15")),
    "last seen null": file_with(games=entry_with(last_seen=None)),
    "unknown outcome": file_with(
        games=entry_with(outcome="lost", outcome_at="2026-10-17T09:30:15+07:00")
    ),
    "outcome without its time": file_with(games=entry_with(outcome="redeemed")),
    "outcome time without an outcome": file_with(
        games=entry_with(outcome_at="2026-10-17T09:30:15+07:00")
    ),
    "announced a string": file_with(games=entry_with(announced="yes")),
    "announced a number": file_with(games=entry_with(announced=1)),
    "listed a string": file_with(games=entry_with(listed="yes")),
    "named a number": file_with(games=entry_with(named=0)),
    "entry without listed": file_with(
        games=hsr_entries({key: value for key, value in VALID_ENTRY.items() if key != "listed"})
    ),
    "two entries share a code": file_with(games=hsr_entries(VALID_ENTRY, VALID_ENTRY)),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_store_starts_empty(tmp_path, caplog, content):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.code_store"):
        assert store.unredeemed("hsr") == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_valid_hand_written_file_is_read(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(
        file_with(
            games={
                "hsr": [
                    VALID_ENTRY,
                    VALID_ENTRY
                    | {
                        "code": "STARRAILGIFT",
                        "rewards": "",
                        "outcome": "claimed",
                        "outcome_at": "2026-10-17T10:00:00+07:00",
                        "announced": True,
                    },
                    VALID_ENTRY | {"code": "NOLONGERLISTED", "listed": False},
                    VALID_ENTRY | {"code": "MASTERCODE1", "listed": False, "named": True},
                ],
                "zzz": [],
            }
        )
    )

    assert store.unredeemed("hsr") == [code(JADE_60), code(("MASTERCODE1", JADE_60[1]))]
    assert store.recent("hsr", 5) == [(code(("STARRAILGIFT", "")), "claimed")]
    assert store.unredeemed("zzz") == []


def test_a_corrupt_file_is_never_overwritten_by_a_later_write(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    store.add("hsr", [JADE_60], NOW)

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert store.unredeemed("hsr") == [code(JADE_60)]


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    store.path.write_bytes(b"second")

    assert store.unredeemed("hsr") == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(store_module.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        store.add("hsr", [JADE_60], NOW)

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["redeem_codes.json"]
