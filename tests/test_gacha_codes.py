import copy
import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from waifu.gacha.codes import (
    FINAL_OUTCOMES,
    REDEEM_OUTCOMES,
    CodesError,
    RedeemCode,
    RedeemResult,
    normalize_code,
    read_codes,
    readable_rewards,
)


def test_a_code_holds_its_game_rewards_and_when_it_was_first_seen():
    code = RedeemCode("hsr", "5S6ZHRWTDNJB", "60 stellar jade", datetime(2026, 10, 17, tzinfo=UTC))

    assert (code.game, code.code, code.rewards) == ("hsr", "5S6ZHRWTDNJB", "60 stellar jade")
    with pytest.raises(dataclasses.FrozenInstanceError):
        code.code = "OTHER"  # type: ignore[misc]


def test_a_result_says_what_happened_to_a_code():
    result = RedeemResult("genshin", "GENSHINGIFT", "claimed", "Kode ini sudah pernah dipakai.")

    assert result.outcome in REDEEM_OUTCOMES
    assert REDEEM_OUTCOMES == (
        "redeemed",
        "claimed",
        "expired",
        "invalid",
        "region",
        "level",
        "cooldown",
        "cookie",
        "error",
    )


def test_every_outcome_but_cooldown_cookie_and_error_is_final():
    assert set(REDEEM_OUTCOMES) - {"cooldown", "cookie", "error"} == FINAL_OUTCOMES


# --- the code format ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("5S6ZHRWTDNJB", "5S6ZHRWTDNJB"),
        ("  starrailgift \n", "STARRAILGIFT"),
        ("Abc12", "ABC12"),
        ("A" * 24, "A" * 24),
        ("ABCD", None),  # too short
        ("A" * 25, None),  # too long
        ("STAR RAIL GIFT", None),
        ("HSR-2026", None),
        ("", None),
        ("ＳＴＡＲＲＡＩＬ", None),  # full-width letters
        ("ſtarrailgift", None),  # a long s that uppercases to S
    ],
)
def test_a_code_is_trimmed_uppercased_and_only_5_to_24_letters_or_digits(text, code):
    assert normalize_code(text) == code


# --- reading the source ------------------------------------------------------------------------

SOURCE_HOST = "hoyo-codes.seria.moe"

# GET https://hoyo-codes.seria.moe/codes?game=hkrpg as it answered on 2026-10-17 (the first
# three of its ten entries).
HSR_SAMPLE = {
    "codes": [
        {
            "id": 158,
            "code": "5S6ZHRWTDNJB",
            "status": "OK",
            "game": "hkrpg",
            "rewards": "60 stellar jade",
        },
        {
            "id": 157,
            "code": "4TKSX77Y58QK",
            "status": "OK",
            "game": "hkrpg",
            "rewards": (
                "30 stellar jade, three traveler’s guides, five condensed aether, four lost gold "
                "fragments, and 20k credits"
            ),
        },
        {
            "id": 13,
            "code": "STARRAILGIFT",
            "status": "OK",
            "game": "hkrpg",
            "rewards": "50 stellar jade, 10k credits, two traveler’s guides, and five bottled soda",
        },
    ],
    "game": "hkrpg",
}
HSR_CODES = [
    ("5S6ZHRWTDNJB", "60 stellar jade"),
    (
        "4TKSX77Y58QK",
        "30 stellar jade, three traveler’s guides, five condensed aether, four lost gold "
        "fragments, and 20k credits",
    ),
    ("STARRAILGIFT", "50 stellar jade, 10k credits, two traveler’s guides, and five bottled soda"),
]
GENSHIN_SAMPLE = {
    "codes": [
        {
            "id": 160,
            "code": "GENSHINGIFT",
            "status": "OK",
            "game": "genshin",
            "rewards": "50 primogems and three hero’s wit",
        }
    ],
    "game": "genshin",
}
ZZZ_SAMPLE = {
    "codes": [
        {
            "id": 159,
            "code": "ZENLESSGIFT",
            "status": "OK",
            "game": "nap",
            "rewards": "50 polychromes and three official investigator logs",
        }
    ],
    "game": "nap",
}

Reply = Any  # a JSON value (HTTP 200), an httpx.Response, or an exception to raise


class FakeSource:
    """The code source behind httpx's MockTransport: nothing leaves this PC."""

    def __init__(self, replies: dict[str, Reply]) -> None:
        self.replies = replies  # by the source's game name
        self.requests: list[httpx.Request] = []
        self.http = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == SOURCE_HOST, f"unexpected request to {request.url}"
        reply = self.replies[request.url.params["game"]]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


def hsr_sample_with(*entries: Any) -> dict:
    sample = copy.deepcopy(HSR_SAMPLE)
    sample["codes"] = list(entries)
    return sample


def entry(code: Any = "5S6ZHRWTDNJB", **fields: Any) -> dict:
    return {
        "id": 1,
        "code": code,
        "status": "OK",
        "game": "hkrpg",
        "rewards": "60 stellar jade",
    } | (fields)


def read(source: FakeSource, games: list[str]) -> dict:
    codes = read_codes(games, http=source.http)
    assert not isinstance(codes, CodesError), codes
    return codes


def test_the_working_codes_of_the_real_sample_come_with_their_rewards():
    source = FakeSource({"hkrpg": HSR_SAMPLE})

    assert read(source, ["hsr"]) == {"hsr": HSR_CODES}


def test_each_game_is_asked_once_by_its_source_name_with_a_15_second_timeout():
    source = FakeSource({"hkrpg": HSR_SAMPLE, "genshin": GENSHIN_SAMPLE, "nap": ZZZ_SAMPLE})

    codes = read(source, ["hsr", "genshin", "zzz"])

    assert codes == {
        "hsr": HSR_CODES,
        "genshin": [("GENSHINGIFT", "50 primogems and three hero’s wit")],
        "zzz": [("ZENLESSGIFT", "50 polychromes and three official investigator logs")],
    }
    assert [request.url.params["game"] for request in source.requests] == [
        "hkrpg",
        "genshin",
        "nap",
    ]
    for request in source.requests:
        assert request.method == "GET"
        assert request.url.copy_with(query=None) == "https://hoyo-codes.seria.moe/codes"
        assert dict(request.url.params) == {"game": request.url.params["game"]}
        assert request.extensions["timeout"] == {
            "connect": 15.0,
            "read": 15.0,
            "write": 15.0,
            "pool": 15.0,
        }
        assert request.headers["User-Agent"].startswith("waifu-companion/")


def test_a_game_listed_twice_is_asked_once():
    source = FakeSource({"hkrpg": HSR_SAMPLE})

    read(source, ["hsr", "hsr"])

    assert len(source.requests) == 1


def test_codes_found_not_to_work_are_left_out():
    source = FakeSource(
        {
            "hkrpg": hsr_sample_with(
                entry("HSRVER30PREDICT", status="NOT_OK"),
                entry("5S6ZHRWTDNJB"),
                entry("OLDCODE2025", status=None),
                {key: value for key, value in entry("NOSTATUS1").items() if key != "status"},
            )
        }
    )

    assert read(source, ["hsr"]) == {"hsr": [("5S6ZHRWTDNJB", "60 stellar jade")]}


def test_codes_are_trimmed_and_uppercased_and_bad_ones_are_skipped(caplog):
    source = FakeSource(
        {
            "hkrpg": hsr_sample_with(
                entry(" starrailgift\n", rewards="50 stellar jade"),
                entry("ABCD"),
                entry("A" * 25),
                entry("STAR RAIL"),
                entry("<script>"),
                entry(12345678),
                entry(None),
            )
        }
    )

    with caplog.at_level(logging.WARNING, logger="waifu.gacha.codes"):
        codes = read(source, ["hsr"])

    assert codes == {"hsr": [("STARRAILGIFT", "50 stellar jade")]}
    assert "redeem code" in caplog.text


def test_a_code_listed_twice_is_kept_once_with_its_first_rewards():
    source = FakeSource(
        {
            "hkrpg": hsr_sample_with(
                entry("STARRAILGIFT", rewards="50 stellar jade"),
                entry("starrailgift", rewards="something else"),
            )
        }
    )

    assert read(source, ["hsr"]) == {"hsr": [("STARRAILGIFT", "50 stellar jade")]}


@pytest.mark.parametrize(
    ("long_rewards", "cut"),
    [
        ("60 stellar jade, " * 20, ("60 stellar jade, " * 20)[:200]),  # 340 characters
        ("a" * 199 + " and more", "a" * 199),  # no space left at the end of the cut
    ],
    ids=["mid-word", "at a space"],
)
def test_long_rewards_are_cut_to_200_characters(long_rewards, cut):
    source = FakeSource({"hkrpg": hsr_sample_with(entry(rewards=long_rewards))})

    ((_, rewards),) = read(source, ["hsr"])["hsr"]

    assert rewards == cut
    assert len(rewards) <= 200


def test_rewards_whitespace_is_collapsed_and_missing_rewards_are_empty():
    source = FakeSource(
        {
            "hkrpg": hsr_sample_with(
                entry("FIRSTCODE1", rewards="  60 stellar\njade\t and  credits "),
                entry("SECONDCODE2", rewards=None),
                {key: value for key, value in entry("THIRDCODE3").items() if key != "rewards"},
            )
        }
    )

    assert read(source, ["hsr"]) == {
        "hsr": [
            ("FIRSTCODE1", "60 stellar jade and credits"),
            ("SECONDCODE2", ""),
            ("THIRDCODE3", ""),
        ]
    }


def test_entries_that_cannot_be_read_are_skipped_and_logged(caplog):
    source = FakeSource(
        {
            "hkrpg": hsr_sample_with(
                "5S6ZHRWTDNJB",
                None,
                entry("LISTEDGIFT1", rewards=["60 stellar jade"]),
                entry("GENSHINGIFT", game="genshin"),
                entry("STARRAILGIFT"),
            )
        }
    )

    with caplog.at_level(logging.WARNING, logger="waifu.gacha.codes"):
        codes = read(source, ["hsr"])

    assert codes == {"hsr": [("STARRAILGIFT", "60 stellar jade")]}
    assert caplog.text.count("Skipping") == 4


def test_a_game_without_working_codes_has_an_empty_list():
    source = FakeSource({"hkrpg": hsr_sample_with(entry(status="NOT_OK")), "nap": {"codes": []}})

    assert read(source, ["hsr", "zzz"]) == {"hsr": [], "zzz": []}


FAILURES = {
    "HTTP 500": httpx.Response(500, text="Internal Server Error"),
    "HTTP 404": httpx.Response(404, json={"detail": "Not Found"}),
    "a redirect": httpx.Response(302, headers={"Location": "https://example.com/"}),
    "a timeout": httpx.ReadTimeout("timed out"),
    "no connection": httpx.ConnectError("connection refused"),
    "not json": httpx.Response(200, text="<html>Cloudflare</html>"),
    "not utf-8": httpx.Response(200, content=b'{"codes": ["\xff"]}'),
    "a list": [],
    "no codes key": {"game": "genshin"},
    "codes an object": {"codes": {"GENSHINGIFT": "50 primogems"}},
    "codes null": {"codes": None},
}


@pytest.mark.parametrize("failure", FAILURES.values(), ids=FAILURES.keys())
def test_a_failing_game_is_logged_and_left_out_while_the_others_still_count(caplog, failure):
    source = FakeSource({"hkrpg": HSR_SAMPLE, "genshin": failure, "nap": ZZZ_SAMPLE})

    with caplog.at_level(logging.WARNING, logger="waifu.gacha.codes"):
        codes = read(source, ["hsr", "genshin", "zzz"])

    assert list(codes) == ["hsr", "zzz"]
    assert "Genshin" in caplog.text


def test_every_game_failing_is_a_codes_error_in_indonesian():
    source = FakeSource(
        {
            "hkrpg": httpx.ConnectError("connection refused"),
            "genshin": httpx.Response(503, text="Service Unavailable"),
            "nap": httpx.ReadTimeout("timed out"),
        }
    )

    error = read_codes(["hsr", "genshin", "zzz"], http=source.http)

    assert isinstance(error, CodesError)
    assert str(error) == (
        "Kode redeem tidak bisa diambil: hoyo-codes.seria.moe tidak bisa dihubungi atau sedang "
        "bermasalah. Cek koneksi internet, lalu coba lagi nanti."
    )
    assert len(source.requests) == 3


def test_no_games_reads_nothing():
    source = FakeSource({})

    assert read(source, []) == {}
    assert source.requests == []


@pytest.mark.parametrize(
    ("listed", "readable"),
    [
        ("Primogem*60", "60 Primogem"),
        ("Primogem*30;Mora*20000;Hero's Wit*2", "30 Primogem, 20.000 Mora, 2 Hero's Wit"),
        ("Polychrome*20;Denny*2,222", "20 Polychrome, 2.222 Denny"),
        ("Primogem*20; Geode of Replication*160", "20 Primogem, 160 Geode of Replication"),
        ("60 stellar jade and one fuel", "60 stellar jade and one fuel"),
        ("a*b weird; text", "a*b weird; text"),
    ],
)
def test_rewards_in_the_name_times_count_form_read_as_counted_items(listed, readable):
    # She speaks the rewards in her notices as they are: "Primogem*60;Mora*20000" would be read
    # out with its stars and semicolons.
    assert readable_rewards(listed) == readable
