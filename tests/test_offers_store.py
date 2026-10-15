import dataclasses
import json
import logging
import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from waifu.offers import store as store_module
from waifu.offers.store import OffersStore
from waifu.offers.values import AiOffer, GameGiveaway

NOW = datetime(2026, 10, 16, 9, 30, 15)
TODAY = NOW.date()
CORRUPT_NAME = "free_offers.json.corrupt-20261016-093015"


class Clock:
    """A settable local clock."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(tmp_path, clock: Clock | None = None) -> OffersStore:
    return OffersStore(tmp_path / "free_offers.json", now=clock or Clock())


def epic_game(game_id: str = "epic:6a84", *, ends_at: datetime | None = None) -> GameGiveaway:
    # Noon UTC is the same day in every time zone from UTC-11 to UTC+11.
    ends = ends_at or datetime(2026, 10, 22, 12, tzinfo=UTC)
    return GameGiveaway(
        game_id,
        "Bad Cheese",
        "Epic Games Store",
        "Epic Games Store",
        12.99,
        datetime(2026, 10, 15, 12, tzinfo=UTC),
        ends,
        None,
        "https://store.epicgames.com/en-US/p/bad-cheese-4256d9",
    )


def gamerpower_game(
    game_id: str = "gamerpower:3801", *, ends_on: date | None = date(2026, 10, 30)
) -> GameGiveaway:
    return GameGiveaway(
        game_id,
        "Agent A",
        "Steam",
        "GamerPower.com",
        None,
        None,
        None,
        ends_on,
        "https://www.gamerpower.com/open/agent-a",
    )


def ai_offer(
    offer_id: str = "ai:1f3a9c2b7d4e",
    *,
    title: str = "Claude for Startups",
    summary: str = "Kredit API Claude gratis untuk startup.",
    deadline: date | None = None,
    first_seen: datetime = NOW,
) -> AiOffer:
    return AiOffer(
        offer_id,
        title,
        "Anthropic",
        "startup_program",
        "apply",
        "Startup yang didukung investor mitra Anthropic.",
        deadline,
        "https://www.anthropic.com/startups",
        summary,
        first_seen,
    )


def saved(store: OffersStore) -> dict:
    return json.loads(store.path.read_text(encoding="utf-8"))


# --- reading -----------------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_store_and_reading_creates_nothing(tmp_path):
    folder = tmp_path / "data"
    store = OffersStore(folder / "free_offers.json", now=Clock())

    assert store.games() == {}
    assert store.ai_offers() == []
    assert store.last_ai_scan() is None
    assert not folder.exists()


def test_every_call_rereads_the_file_so_another_process_sees_the_changes(tmp_path):
    reader = make_store(tmp_path)
    assert reader.ai_offers() == []

    make_store(tmp_path).merge_ai_offers([ai_offer()], NOW)

    assert reader.ai_offers() == [ai_offer()]
    assert reader.last_ai_scan() == NOW


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    store = make_store(tmp_path)
    store.remember_games([epic_game()])
    store.path.write_bytes(b"\xef\xbb\xbf" + store.path.read_bytes())

    assert store.games() == {"epic:6a84": date(2026, 10, 22)}


# --- announced games ---------------------------------------------------------------------------


def test_remembered_games_are_saved_in_the_spec_file_shape(tmp_path):
    store = make_store(tmp_path)

    new = store.remember_games([epic_game(), gamerpower_game()])

    assert new == [epic_game(), gamerpower_game()]
    assert saved(store) == {
        "version": 1,
        "games": {"epic:6a84": "2026-10-22", "gamerpower:3801": "2026-10-30"},
        "ai_offers": [],
        "last_ai_scan": None,
        "last_failed_ai_scan": None,
    }


def test_only_games_not_announced_before_are_new(tmp_path):
    store = make_store(tmp_path)
    store.remember_games([epic_game()])

    new = store.remember_games([epic_game(), gamerpower_game()])

    assert new == [gamerpower_game()]
    assert store.games() == {"epic:6a84": date(2026, 10, 22), "gamerpower:3801": date(2026, 10, 30)}


def test_a_game_listed_twice_in_one_poll_is_new_once(tmp_path):
    store = make_store(tmp_path)

    assert store.remember_games([epic_game(), epic_game()]) == [epic_game()]


def test_an_epic_end_counts_as_its_local_day(tmp_path):
    store = make_store(tmp_path)
    ends_at = datetime(2026, 10, 22, 12, tzinfo=UTC)

    store.remember_games([epic_game(ends_at=ends_at)])

    assert store.games() == {"epic:6a84": ends_at.astimezone().date()}


def test_a_game_is_forgotten_30_days_after_it_ended_and_is_new_again(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.remember_games([gamerpower_game()])  # ends 2026-10-30

    clock.now = datetime(2026, 11, 29, 23, 59)
    assert store.games() == {"gamerpower:3801": date(2026, 10, 30)}
    clock.now = datetime(2026, 11, 30, 0, 0)
    assert store.games() == {}
    given_again = gamerpower_game(ends_on=date(2026, 12, 7))
    assert store.remember_games([given_again]) == [given_again]


def test_a_game_without_an_end_is_kept_30_days_after_it_was_last_listed(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    endless = gamerpower_game(ends_on=None)
    store.remember_games([endless])
    assert store.games() == {"gamerpower:3801": TODAY}

    clock.now = NOW + timedelta(days=20)
    assert store.remember_games([endless]) == []
    assert store.games() == {"gamerpower:3801": TODAY + timedelta(days=20)}

    clock.now = NOW + timedelta(days=50)
    assert store.games() == {"gamerpower:3801": TODAY + timedelta(days=20)}
    clock.now = NOW + timedelta(days=51)
    assert store.games() == {}


def test_a_later_end_keeps_a_game_longer_and_an_earlier_one_never_shortens_it(tmp_path):
    store = make_store(tmp_path)
    store.remember_games([gamerpower_game(ends_on=date(2026, 10, 30))])

    store.remember_games([gamerpower_game(ends_on=date(2026, 11, 6))])
    assert store.games() == {"gamerpower:3801": date(2026, 11, 6)}
    store.remember_games([gamerpower_game(ends_on=date(2026, 10, 20))])
    assert store.games() == {"gamerpower:3801": date(2026, 11, 6)}


def test_forgotten_games_leave_the_file_at_the_next_write(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.remember_games([gamerpower_game()])

    clock.now = datetime(2026, 12, 1, 8, 0)
    store.remember_games([epic_game("epic:7b95", ends_at=datetime(2026, 12, 8, 12, tzinfo=UTC))])

    assert list(saved(store)["games"]) == ["epic:7b95"]


def test_remembering_games_already_saved_writes_nothing(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.remember_games([epic_game()])
    before = store.path.read_bytes()

    def refused_replace(src, dst):
        raise AssertionError("nothing new to save")

    monkeypatch.setattr(store_module.os, "replace", refused_replace)

    assert store.remember_games([epic_game()]) == []
    assert store.path.read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["free_offers.json"]


def test_an_empty_poll_creates_no_file(tmp_path):
    store = make_store(tmp_path)

    assert store.remember_games([]) == []
    assert not store.path.exists()


# --- AI offers ---------------------------------------------------------------------------------


def test_merged_ai_offers_are_saved_in_the_spec_file_shape_with_the_scan_time(tmp_path):
    store = make_store(tmp_path)
    offer = ai_offer(deadline=date(2026, 12, 31))

    new = store.merge_ai_offers([offer], NOW)

    assert new == [offer]
    assert store.ai_offers() == [offer]
    assert store.last_ai_scan() == NOW
    assert saved(store) == {
        "version": 1,
        "games": {},
        "ai_offers": [
            {
                "id": "ai:1f3a9c2b7d4e",
                "title": "Claude for Startups",
                "provider": "Anthropic",
                "kind": "startup_program",
                "access": "apply",
                "eligibility": "Startup yang didukung investor mitra Anthropic.",
                "deadline": "2026-12-31",
                "url": "https://www.anthropic.com/startups",
                "summary": "Kredit API Claude gratis untuk startup.",
                "first_seen": "2026-10-16T09:30:15",
                "last_seen": "2026-10-16T09:30:15",
            }
        ],
        "last_ai_scan": "2026-10-16T09:30:15",
        "last_failed_ai_scan": None,
    }


def test_an_offer_seen_again_keeps_its_first_seen_takes_the_rest_and_is_not_new(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.merge_ai_offers([ai_offer()], NOW)
    later = NOW + timedelta(days=7)
    clock.now = later
    changed = ai_offer(summary="Sampai 25.000 dolar kredit API.", first_seen=later)

    new = store.merge_ai_offers([changed], later)

    assert new == []
    assert store.ai_offers() == [dataclasses.replace(changed, first_seen=NOW)]
    assert saved(store)["ai_offers"][0]["last_seen"] == later.isoformat()
    assert store.last_ai_scan() == later


def test_new_offers_come_back_in_order_after_the_ones_already_known(tmp_path):
    store = make_store(tmp_path)
    store.merge_ai_offers([ai_offer("ai:a")], NOW)

    new = store.merge_ai_offers([ai_offer("ai:b"), ai_offer("ai:a"), ai_offer("ai:c")], NOW)

    assert [offer.id for offer in new] == ["ai:b", "ai:c"]
    assert [offer.id for offer in store.ai_offers()] == ["ai:a", "ai:b", "ai:c"]


def test_an_offer_reported_twice_in_one_scan_is_new_once_with_its_last_report(tmp_path):
    store = make_store(tmp_path)
    second = ai_offer(summary="Ringkasan kedua.")

    new = store.merge_ai_offers([ai_offer(), second], NOW)

    assert new == [second]
    assert store.ai_offers() == [second]


def test_a_scan_that_found_nothing_still_records_its_time(tmp_path):
    store = make_store(tmp_path)

    assert store.merge_ai_offers([], NOW) == []

    assert store.last_ai_scan() == NOW
    assert saved(store)["ai_offers"] == []


def test_a_paid_scan_that_failed_is_kept_apart_from_the_last_scan(tmp_path):
    store = make_store(tmp_path)
    store.merge_ai_offers([ai_offer()], NOW - timedelta(days=8))

    store.record_failed_ai_scan(NOW)

    assert store.last_failed_ai_scan() == NOW
    assert store.last_ai_scan() == NOW - timedelta(days=8)
    assert store.ai_offers() == [ai_offer()]
    assert saved(store)["last_failed_ai_scan"] == "2026-10-16T09:30:15"


def test_before_any_failed_scan_there_is_no_failed_scan_time(tmp_path):
    store = make_store(tmp_path)
    store.merge_ai_offers([], NOW)

    assert store.last_failed_ai_scan() is None


def test_an_offer_is_dropped_the_day_after_its_deadline(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    offer = ai_offer(deadline=date(2026, 10, 31))
    store.merge_ai_offers([offer], NOW)

    clock.now = datetime(2026, 10, 31, 23, 59)
    assert store.ai_offers() == [offer]
    clock.now = datetime(2026, 11, 1, 0, 0)
    assert store.ai_offers() == []


def test_an_offer_whose_deadline_already_passed_is_neither_new_nor_kept(tmp_path):
    store = make_store(tmp_path)

    new = store.merge_ai_offers([ai_offer(deadline=date(2026, 10, 15))], NOW)

    assert new == []
    assert store.ai_offers() == []
    assert saved(store)["ai_offers"] == []


def test_an_offer_is_dropped_90_days_after_a_scan_last_reported_it(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.merge_ai_offers([ai_offer()], NOW)

    clock.now = NOW + timedelta(days=60)
    store.merge_ai_offers([ai_offer(first_seen=clock.now)], clock.now)  # reported again
    clock.now = NOW + timedelta(days=150)
    assert [offer.id for offer in store.ai_offers()] == ["ai:1f3a9c2b7d4e"]
    clock.now = NOW + timedelta(days=151)
    assert store.ai_offers() == []


def test_an_offer_dropped_as_unseen_is_new_when_a_scan_finds_it_again(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.merge_ai_offers([ai_offer()], NOW)
    clock.now = NOW + timedelta(days=91)
    again = ai_offer(first_seen=clock.now)

    assert store.merge_ai_offers([again], clock.now) == [again]
    assert store.ai_offers() == [again]


def test_games_and_ai_offers_share_the_file_without_losing_each_other(tmp_path):
    store = make_store(tmp_path)

    store.remember_games([epic_game()])
    store.merge_ai_offers([ai_offer()], NOW)
    store.remember_games([gamerpower_game()])

    assert set(store.games()) == {"epic:6a84", "gamerpower:3801"}
    assert store.ai_offers() == [ai_offer()]
    assert store.last_ai_scan() == NOW


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


def test_concurrent_writes_from_two_stores_keep_every_game(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]

    def remember(worker):
        for index in range(5):
            stores[worker % 2].remember_games([epic_game(f"epic:{worker}-{index}")])

    assert run_in_threads(remember) == []
    assert len(stores[0].games()) == 40


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    store = make_store(tmp_path)

    store.merge_ai_offers([ai_offer()], NOW)

    assert [path.name for path in tmp_path.iterdir()] == ["free_offers.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.remember_games([epic_game()])
    before = store.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store_module.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.remember_games([gamerpower_game()])

    assert store.path.read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["free_offers.json"]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "promo gratis" / "tawaran Master.json"

    OffersStore(path, now=Clock()).remember_games([epic_game()])

    assert set(OffersStore(path, now=Clock()).games()) == {"epic:6a84"}


# --- corrupt files -----------------------------------------------------------------------------

VALID_OFFER = {
    "id": "ai:1f3a9c2b7d4e",
    "title": "Claude for Startups",
    "provider": "Anthropic",
    "kind": "startup_program",
    "access": "apply",
    "eligibility": "Startup.",
    "deadline": None,
    "url": "https://www.anthropic.com/startups",
    "summary": "Kredit API.",
    "first_seen": "2026-10-16T09:30:15",
    "last_seen": "2026-10-16T09:30:15",
}


def file_with(**changes):
    empty = {"version": 1, "games": {}, "ai_offers": [], "last_ai_scan": None}
    return json.dumps(empty | changes).encode()


def offer_with(**changes):
    return [VALID_OFFER | changes]


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "games": {"\xff": "2026-10-22"}}',
    "a list": b"[]",
    "missing last scan": json.dumps({"version": 1, "games": {}, "ai_offers": []}).encode(),
    "an extra key": file_with(notices=[]),
    "another version": file_with(version=2),
    "version true": file_with(version=True),
    "games a list": file_with(games=["epic:6a84"]),
    "game end a number": file_with(games={"epic:6a84": 20261022}),
    "game end with a time": file_with(games={"epic:6a84": "2026-10-22T15:00"}),
    "game end that does not exist": file_with(games={"epic:6a84": "2026-02-30"}),
    "game end written another way": file_with(games={"epic:6a84": "20261022"}),
    "offers an object": file_with(ai_offers={}),
    "offer a string": file_with(ai_offers=["ai:1f3a9c2b7d4e"]),
    "offer without summary": file_with(
        ai_offers=[{key: value for key, value in VALID_OFFER.items() if key != "summary"}]
    ),
    "offer with an extra key": file_with(ai_offers=offer_with(notes="")),
    "offer id empty": file_with(ai_offers=offer_with(id="")),
    "offer title a number": file_with(ai_offers=offer_with(title=5)),
    "offer unknown kind": file_with(ai_offers=offer_with(kind="discount")),
    "offer unknown access": file_with(ai_offers=offer_with(access="buy")),
    "offer link not https": file_with(ai_offers=offer_with(url="http://example.com")),
    "offer deadline not a date": file_with(ai_offers=offer_with(deadline="soon")),
    "offer first seen not a time": file_with(ai_offers=offer_with(first_seen="kemarin")),
    "offer last seen null": file_with(ai_offers=offer_with(last_seen=None)),
    "two offers share an id": file_with(ai_offers=[VALID_OFFER, VALID_OFFER]),
    "last scan not a time": file_with(last_ai_scan="minggu lalu"),
    "last scan a number": file_with(last_ai_scan=1760000000),
    "failed scan not a time": file_with(last_failed_ai_scan="kemarin"),
    "offer title too long": file_with(ai_offers=offer_with(title="A" * 121)),
    "offer provider too long": file_with(ai_offers=offer_with(provider="A" * 121)),
    "offer eligibility too long": file_with(ai_offers=offer_with(eligibility="A" * 301)),
    "offer summary too long": file_with(ai_offers=offer_with(summary="A" * 301)),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_store_starts_empty(tmp_path, caplog, content):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.offers.store"):
        assert store.ai_offers() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_valid_hand_written_file_is_read(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(
        file_with(
            games={"epic:6a84": "2026-10-22"},
            ai_offers=offer_with(deadline="2026-12-31"),
            last_ai_scan="2026-10-09T08:00:00",
        )
    )

    assert store.games() == {"epic:6a84": date(2026, 10, 22)}
    assert [offer.deadline for offer in store.ai_offers()] == [date(2026, 12, 31)]
    assert store.last_ai_scan() == datetime(2026, 10, 9, 8, 0)
    assert store.last_failed_ai_scan() is None  # a file written before failed scans were kept


def test_a_hand_written_failed_scan_time_is_read(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(file_with(last_failed_ai_scan="2026-10-15T08:00:00"))

    assert store.last_failed_ai_scan() == datetime(2026, 10, 15, 8, 0)


def test_a_corrupt_file_is_never_overwritten_by_a_later_write(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    store.remember_games([epic_game()])

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert set(store.games()) == {"epic:6a84"}


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    store.path.write_bytes(b"second")

    assert store.games() == {}

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(store_module.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        store.merge_ai_offers([ai_offer()], NOW)

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["free_offers.json"]
