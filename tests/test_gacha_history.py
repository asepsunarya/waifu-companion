import dataclasses
import http.server
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from waifu.gacha.games import GAMES
from waifu.gacha.history import (
    CachedGachaUrl,
    GachaHistory,
    GachaLogApi,
    GachaRecord,
    GachaStore,
    banner_stats,
    find_cached_gacha_url,
    get_json,
    partial_history_hint,
    pulls_since,
)
from waifu.gacha.hoyolab import GachaError

HSR = GAMES["hsr"]
AUTHKEY = "SECRETKEY+/abc="
QUOTED_AUTHKEY = urllib.parse.quote(AUTHKEY, safe="")
HSR_URL = (
    "https://public-operation-hkrpg-sg.hoyoverse.com/common/hkrpg_gacha_record/api/getGachaLog"
    f"?authkey_ver=1&sign_type=2&auth_appid=webview_gacha&lang=id&authkey={QUOTED_AUTHKEY}"
    "&game_biz=hkrpg_global&gacha_type=11&page=1&size=5&end_id=0"
)


# --- the authkey the game left in its web cache --------------------------------------------------


def game_install(locallow: Path, game_id: str, *, log_text: str | None = None) -> Path:
    """A game's log under LocalLow naming its data folder, like the real launcher leaves it."""
    game = GAMES[game_id]
    data_dir = locallow.parent / "Games" / game.short / game.data_folder
    data_dir.mkdir(parents=True)
    log = locallow / game.log_files[0]
    log.parent.mkdir(parents=True)
    text = (
        log_text
        or f"[Subsystems] Discovering subsystems at path {data_dir.as_posix()}/UnitySubsystems\n"
    )
    log.write_text(f"Mono path[0] = 'x'\n{text}Initialize engine version\n", encoding="utf-8")
    return data_dir


def web_cache(data_dir: Path, version: str, content: bytes, *, age_s: int = 0) -> Path:
    data_2 = data_dir / "webCaches" / version / "Cache" / "Cache_Data" / "data_2"
    data_2.parent.mkdir(parents=True)
    data_2.write_bytes(content)
    stamp = 1_800_000_000 - age_s
    os.utime(data_2, (stamp, stamp))
    return data_2


def cache_bytes(*urls: str) -> bytes:
    junk = b"\x00\x01\xffkey\x00"
    return junk + b"".join(url.encode() + junk for url in urls)


@pytest.fixture
def locallow(tmp_path) -> Path:
    path = tmp_path / "AppData" / "LocalLow"
    path.mkdir(parents=True)
    return path


def test_the_newest_cached_history_url_gives_the_api_base_and_the_decoded_authkey(locallow):
    data_dir = game_install(locallow, "hsr")
    old_url = HSR_URL.replace(QUOTED_AUTHKEY, "OLDKEY")
    web_cache(data_dir, "2.53.0.0", cache_bytes(HSR_URL), age_s=3600)
    web_cache(data_dir, "2.54.0.0", cache_bytes(old_url, HSR_URL.replace("&lang=id", "&lang=en")))

    found = find_cached_gacha_url(HSR, locallow)

    assert found == CachedGachaUrl(
        "https://public-operation-hkrpg-sg.hoyoverse.com/common/hkrpg_gacha_record/api/", AUTHKEY
    )


def test_a_newer_cache_without_a_history_url_is_skipped(locallow):
    data_dir = game_install(locallow, "hsr")
    web_cache(data_dir, "2.53.0.0", cache_bytes(HSR_URL), age_s=3600)
    web_cache(data_dir, "2.54.0.0", cache_bytes("https://webstatic.hoyoverse.com/index.html"))

    assert find_cached_gacha_url(HSR, locallow).authkey == AUTHKEY


def test_the_collab_history_url_counts_too(locallow):
    data_dir = game_install(locallow, "hsr")
    web_cache(data_dir, "2.54.0.0", cache_bytes(HSR_URL.replace("getGachaLog", "getLdGachaLog")))

    assert find_cached_gacha_url(HSR, locallow).base.endswith("/hkrpg_gacha_record/api/")


@pytest.mark.parametrize("game_id", ["genshin", "zzz"])
def test_each_game_is_found_through_its_own_log(locallow, game_id):
    game = GAMES[game_id]
    data_dir = game_install(locallow, game_id)
    url = f"{game.history_api}getGachaLog?authkey_ver=1&authkey={QUOTED_AUTHKEY}&gacha_type=1"
    web_cache(data_dir, "2.55.0.0", cache_bytes(url))

    assert find_cached_gacha_url(game, locallow) == CachedGachaUrl(game.history_api, AUTHKEY)


def test_without_the_game_log_or_its_cache_there_is_no_url(locallow):
    assert find_cached_gacha_url(HSR, locallow) is None
    game_install(locallow, "hsr")
    assert find_cached_gacha_url(HSR, locallow) is None


def test_a_log_that_names_no_data_folder_gives_no_url(locallow):
    game_install(locallow, "hsr", log_text="Nothing about the install here\n")
    assert find_cached_gacha_url(HSR, locallow) is None


def test_a_cache_locked_by_the_running_game_says_to_close_it(locallow, monkeypatch):
    data_dir = game_install(locallow, "hsr")
    locked = web_cache(data_dir, "2.54.0.0", cache_bytes(HSR_URL))
    read_bytes = Path.read_bytes

    def refuse(path: Path) -> bytes:
        if path == locked:
            raise PermissionError(13, "in use")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", refuse)

    with pytest.raises(GachaError) as caught:
        find_cached_gacha_url(HSR, locallow)

    assert caught.value.kind == "locked"
    assert "HSR" in caught.value.message


def test_the_cached_authkey_is_not_shown_in_reprs():
    assert AUTHKEY not in repr(CachedGachaUrl("https://x/api/", AUTHKEY))


# --- the game's history API ----------------------------------------------------------------------


def item(record_id: int, *, name="Arrows", rank="3", gacha_type="11", time="2026-10-11 22:18:10"):
    return {
        "uid": "800000001",
        "gacha_id": "2140",
        "gacha_type": gacha_type,
        "item_id": "20000",
        "count": "1",
        "time": time,
        "name": name,
        "lang": "en-us",
        "item_type": "Light Cone",
        "rank_type": rank,
        "id": str(record_id),
    }


def page(*items, retcode=0, message="OK"):
    return {"retcode": retcode, "message": message, "data": {"list": list(items), "size": "20"}}


class FakeHistoryApi:
    """The history endpoint: answers each request with the next prepared page."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout_s: float):
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def query(self, index: int) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.urls[index]).query))


def full_page(first_id: int) -> dict:
    return page(*(item(first_id - offset) for offset in range(20)))


def make_api(fake: FakeHistoryApi, sleeps: list[float] | None = None) -> GachaLogApi:
    """An API on a clock that stands still, so every request after the first waits the full gap."""
    record = sleeps.append if sleeps is not None else (lambda seconds: None)
    return GachaLogApi(http_get=fake, sleep=record, clock=lambda: 0.0)


BASE = "https://public-operation-hkrpg-sg.hoyoverse.com/common/hkrpg_gacha_record/api/"
CHARACTER = HSR.banners[0]


def test_fetch_asks_for_the_banner_with_the_authkey_and_english_names():
    fake = FakeHistoryApi(page(item(5)))

    records = make_api(fake).fetch(BASE, HSR, CHARACTER, AUTHKEY)

    assert records == [
        GachaRecord(
            id=5,
            uid=800000001,
            banner=11,
            name="Arrows",
            rarity=3,
            item_type="Light Cone",
            time="2026-10-11 22:18:10",
        )
    ]
    assert fake.urls[0].startswith(f"{BASE}getGachaLog?")
    assert fake.query(0) == {
        "authkey_ver": "1",
        "sign_type": "2",
        "auth_appid": "webview_gacha",
        "authkey": AUTHKEY,
        "lang": "en",
        "game_biz": "hkrpg_global",
        "gacha_type": "11",
        "real_gacha_type": "11",
        "size": "20",
        "end_id": "0",
    }


def test_fetch_pages_back_with_end_id_until_a_short_page():
    fake = FakeHistoryApi(full_page(100), full_page(80), page(item(60), item(59)))
    sleeps: list[float] = []

    records = make_api(fake, sleeps).fetch(BASE, HSR, CHARACTER, AUTHKEY)

    assert [r.id for r in records] == [*range(100, 80, -1), *range(80, 60, -1), 60, 59]
    assert [fake.query(i)["end_id"] for i in range(3)] == ["0", "81", "61"]
    assert sleeps == [0.5, 0.5]  # between requests, never before the first


def test_requests_stay_apart_across_banners_too():
    # Measured on the owner's PC: five back-to-back requests drew -110, 0.5 s apart none did.
    fake = FakeHistoryApi(page(item(5)), page(item(9)))
    sleeps: list[float] = []
    api = make_api(fake, sleeps)

    api.fetch(BASE, HSR, CHARACTER, AUTHKEY)
    api.fetch(BASE, HSR, HSR.banners[1], AUTHKEY)

    assert sleeps == [0.5]


def test_requests_already_far_enough_apart_do_not_wait():
    fake = FakeHistoryApi(page(item(5)), page(item(9)))
    sleeps: list[float] = []
    now = [0.0]
    api = GachaLogApi(http_get=fake, sleep=sleeps.append, clock=lambda: now[0])

    api.fetch(BASE, HSR, CHARACTER, AUTHKEY)
    now[0] = 3.0
    api.fetch(BASE, HSR, HSR.banners[1], AUTHKEY)

    assert sleeps == []


def test_a_rate_limited_request_is_retried_after_a_pause():
    fake = FakeHistoryApi(page(retcode=-110, message="visit too frequently"), page(item(5)))
    sleeps: list[float] = []

    records = make_api(fake, sleeps).fetch(BASE, HSR, CHARACTER, AUTHKEY)

    assert [r.id for r in records] == [5]
    assert sleeps == [1.0, 0.5]  # the back-off, then the usual gap before the retry
    assert fake.query(0) == fake.query(1)


def test_fetch_stops_at_the_newest_record_already_stored():
    fake = FakeHistoryApi(full_page(100), full_page(80))

    records = make_api(fake).fetch(BASE, HSR, CHARACTER, AUTHKEY, stop_at_id=95)

    assert [r.id for r in records] == [100, 99, 98, 97, 96]
    assert len(fake.urls) == 1


def test_fetch_reads_at_most_max_pages():
    fake = FakeHistoryApi(full_page(100), full_page(80), full_page(60))

    records = make_api(fake).fetch(BASE, HSR, CHARACTER, AUTHKEY, max_pages=2)

    assert len(records) == 40
    assert len(fake.urls) == 2


def test_an_empty_history_is_one_request():
    fake = FakeHistoryApi(page())
    assert make_api(fake).fetch(BASE, HSR, CHARACTER, AUTHKEY) == []
    assert len(fake.urls) == 1


def test_hsr_collab_banners_use_their_own_endpoint():
    fake = FakeHistoryApi(page())
    make_api(fake).fetch(BASE, HSR, HSR.banners[3], AUTHKEY)
    assert fake.urls[0].startswith(f"{BASE}getLdGachaLog?")


def test_records_keep_the_banner_they_were_asked_for():
    # Genshin returns the second character banner (400) inside the 301 history.
    genshin = GAMES["genshin"]
    fake = FakeHistoryApi(page(item(7, gacha_type="400", rank="5", name="Furina")))

    [record] = make_api(fake).fetch(genshin.history_api, genshin, genshin.banners[0], AUTHKEY)

    assert (record.banner, record.rarity, record.name) == (301, 5, "Furina")


RATE_LIMITED = page(retcode=-110, message="visit too frequently")


@pytest.mark.parametrize(
    ("answers", "kind"),
    [
        ([page(retcode=-101, message="authkey timeout")], "authkey"),
        ([page(retcode=-100, message="authkey error")], "authkey"),
        ([page(retcode=-1, message="auth key time out")], "authkey"),  # ZZZ's wording
        ([RATE_LIMITED, RATE_LIMITED, RATE_LIMITED], "rate_limit"),  # still limited after 2 retries
        ([page(retcode=-999, message="something else")], "other"),
        ([urllib.error.URLError("getaddrinfo failed")], "network"),
        ([TimeoutError("timed out")], "network"),
        ([ValueError("not JSON")], "network"),
    ],
)
def test_history_api_failures_become_gacha_errors(answers, kind, caplog):
    fake = FakeHistoryApi(*answers)

    with caplog.at_level(logging.DEBUG), pytest.raises(GachaError) as caught:
        make_api(fake).fetch(BASE, HSR, CHARACTER, AUTHKEY)

    assert caught.value.kind == kind
    assert AUTHKEY not in caught.value.message
    assert QUOTED_AUTHKEY not in caplog.text and AUTHKEY not in caplog.text


def test_an_expired_authkey_says_to_open_the_history_in_the_game():
    fake = FakeHistoryApi(page(retcode=-101, message="authkey timeout"))

    with pytest.raises(GachaError) as caught:
        make_api(fake).fetch(BASE, HSR, CHARACTER, AUTHKEY)

    assert "riwayat warp" in caught.value.message


class _HistoryHandler(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_GET(self):  # noqa: N802 - http.server naming
        self.seen.append((self.path, self.headers["User-Agent"]))
        body = json.dumps(page(item(1, name="がんばって"))).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_get_json_reads_utf8_json_with_a_browser_user_agent():
    server = http.server.HTTPServer(("127.0.0.1", 0), _HistoryHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        data = get_json(f"http://127.0.0.1:{server.server_port}/api/getGachaLog?x=1", 5.0)
    finally:
        thread.join(5)
        server.server_close()

    assert data["data"]["list"][0]["name"] == "がんばって"
    path, user_agent = _HistoryHandler.seen[-1]
    assert path == "/api/getGachaLog?x=1"
    assert user_agent.startswith("Mozilla/5.0")


# --- the store, pity and 50/50, and the report -------------------------------------------------


def pull(record_id: int, name: str = "Arrows", *, banner: int = 11, rarity: int = 3, uid=800000001):
    second = record_id % 60
    return GachaRecord(
        id=record_id,
        uid=uid,
        banner=banner,
        name=name,
        rarity=rarity,
        item_type="Character" if rarity == 5 else "Light Cone",
        time=f"2026-10-11 22:{(record_id // 60) % 60:02d}:{second:02d}",
    )


def pulls_until(start_id: int, count: int, top: str | None = None, **kwargs) -> list[GachaRecord]:
    """`count` pulls from `start_id`; the last is the 5-star `top` when given."""
    records = [pull(start_id + i, **kwargs) for i in range(count)]
    if top is not None:
        records[-1] = pull(start_id + count - 1, top, rarity=5, **kwargs)
    return records


def test_pity_totals_and_the_fifty_fifty_follow_the_character_banner():
    records = [
        *pulls_until(1, 10, "Acheron"),  # won the 50/50
        *pulls_until(11, 60, "Himeko"),  # lost it to a standard character
        *pulls_until(71, 30, "Firefly"),  # guaranteed, so neither won nor lost
        *pulls_until(101, 12),
    ]

    [character] = banner_stats(HSR, records)

    assert (character.banner.type, character.pity, character.total) == (11, 12, 112)
    assert character.to_hard_pity == 78
    assert character.guaranteed is False
    assert [(t.name, t.pulls, t.won_5050) for t in character.recent_tops] == [
        ("Firefly", 30, None),
        ("Himeko", 60, False),
        ("Acheron", 10, True),
    ]


def test_a_lost_fifty_fifty_guarantees_the_next_five_star():
    [character] = banner_stats(HSR, [*pulls_until(1, 75, "Welt"), *pulls_until(76, 3)])
    assert character.guaranteed is True


def test_the_owners_off_banner_pick_counts_as_a_lost_fifty_fifty():
    records = pulls_until(1, 40, "Yunli")
    assert banner_stats(HSR, records)[0].recent_tops[0].won_5050 is True
    assert banner_stats(HSR, records, ["Yunli"])[0].recent_tops[0].won_5050 is False


def test_banners_without_a_fifty_fifty_only_count_pity():
    records = [
        *pulls_until(1, 50, "Night on the Milky Way", banner=12),
        *pulls_until(51, 5, banner=1),
    ]

    light_cone, standard = banner_stats(HSR, records)

    assert (light_cone.banner.type, light_cone.pity, light_cone.guaranteed) == (12, 0, None)
    assert light_cone.recent_tops[0].won_5050 is None
    assert light_cone.to_hard_pity == 80
    assert (standard.banner.type, standard.pity, standard.recent_tops) == (1, 5, ())


def test_only_the_last_five_five_stars_are_listed():
    records = [record for i in range(7) for record in pulls_until(1 + 10 * i, 10, f"Unit {i}")]
    tops = banner_stats(HSR, records)[0].recent_tops
    assert [t.name for t in tops] == ["Unit 6", "Unit 5", "Unit 4", "Unit 3", "Unit 2"]


def test_a_zzz_s_rank_is_its_top_rarity():
    records = [*pulls_until(1, 20, banner=2), pull(21, "Ellen", banner=2, rarity=4)]
    [exclusive] = banner_stats(GAMES["zzz"], records)
    assert (exclusive.pity, exclusive.recent_tops[0].name) == (0, "Ellen")


def test_the_store_keeps_one_file_per_account_and_reads_the_latest(tmp_path):
    store = GachaStore(tmp_path / "gacha")
    first = [pull(1), pull(2, "Acheron", rarity=5)]
    store.save(HSR, 800000001, first, complete=True)
    store.save(HSR, 800000009, [pull(5, uid=800000009)], complete=False)

    loaded = store.load(HSR)

    assert (loaded.uid, loaded.complete) == (800000009, False)
    assert loaded.records == [pull(5, uid=800000009)]
    assert sorted(p.name for p in (tmp_path / "gacha").iterdir()) == [
        "hsr-800000001.json",
        "hsr-800000009.json",
    ]
    saved = json.loads((tmp_path / "gacha" / "hsr-800000001.json").read_text(encoding="utf-8"))
    assert (saved["version"], saved["game"], saved["uid"], saved["complete"]) == (
        1,
        "hsr",
        800000001,
        True,
    )
    assert saved["records"][1] == {
        "id": 2,
        "banner": 11,
        "name": "Acheron",
        "rarity": 5,
        "item_type": "Character",
        "time": "2026-10-11 22:00:02",
    }


def test_an_empty_store_has_no_account_yet(tmp_path):
    loaded = GachaStore(tmp_path / "nothing").load(GAMES["zzz"])
    assert (loaded.uid, loaded.records, loaded.complete) == (None, [], False)


class FakeLogApi:
    """GachaLogApi.fetch: newest-first records per banner, honouring stop_at_id and max_pages."""

    def __init__(self, history: dict[int, list[GachaRecord]], failure: GachaError | None = None):
        self.history = history
        self.failure = failure
        self.calls: list[dict] = []

    def fetch(self, base, game, banner, authkey, *, stop_at_id=0, max_pages=None):
        self.calls.append(
            {
                "base": base,
                "banner": banner.type,
                "authkey": authkey,
                "stop": stop_at_id,
                "pages": max_pages,
            }
        )
        if self.failure is not None:
            raise self.failure
        newest_first = sorted(self.history.get(banner.type, []), key=lambda r: r.id, reverse=True)
        fresh = [r for r in newest_first if r.id > stop_at_id]
        return fresh if max_pages is None else fresh[: max_pages * 20]


class FakeAuthkeys:
    def __init__(self, *, has_cookie_token=True, failure: GachaError | None = None):
        self.has_cookie_token = has_cookie_token
        self.failure = failure
        self.asked: list[str] = []

    def authkey(self, game_id: str) -> str:
        self.asked.append(game_id)
        if self.failure is not None:
            raise self.failure
        return "COOKIEKEY"


def make_history(tmp_path, locallow, api, *, hoyolab=None, extra=None) -> GachaHistory:
    return GachaHistory(
        GachaStore(tmp_path / "gacha"),
        api,
        locallow=locallow,
        hoyolab=hoyolab,
        extra_standard=extra,
        now=lambda: wib_now,
    )


wib_now = datetime(2026, 10, 12, 13, 0, tzinfo=timezone(timedelta(hours=7)))


@pytest.fixture
def hsr_cache(locallow) -> Path:
    data_dir = game_install(locallow, "hsr")
    web_cache(data_dir, "2.54.0.0", cache_bytes(HSR_URL))
    return data_dir


def test_the_first_report_reads_a_few_pages_per_banner_and_saves_them(
    tmp_path, locallow, hsr_cache
):
    character = [*pulls_until(1, 30, "Acheron"), *pulls_until(31, 4)]
    api = FakeLogApi({11: character, 1: pulls_until(100, 3, banner=1)})

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert (report.updated, report.problem, report.complete) == (True, None, True)
    assert [c["banner"] for c in api.calls] == [11, 12, 1, 21, 22]
    assert {(c["stop"], c["pages"], c["authkey"]) for c in api.calls} == {(0, 5, AUTHKEY)}
    assert {c["base"] for c in api.calls} == {BASE}
    assert report.new_count == 37
    assert [(p.name, p.rarity, p.pulls, p.won_5050) for p in report.new_pulls] == [
        ("Acheron", 5, 30, True)
    ]
    assert [(s.banner.type, s.pity) for s in report.stats] == [(11, 4), (1, 3)]
    assert GachaStore(tmp_path / "gacha").load(HSR).complete is True


def test_a_first_report_cut_off_by_the_page_limit_is_incomplete(tmp_path, locallow, hsr_cache):
    api = FakeLogApi({11: pulls_until(1, 130)})

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert report.complete is False
    assert report.stats[0].total == 100
    assert "waifu gacha history hsr" in report.hint


def test_later_reports_read_only_what_is_newer_than_the_store(tmp_path, locallow, hsr_cache):
    character = pulls_until(1, 30)
    api = FakeLogApi({11: character})
    history = make_history(tmp_path, locallow, api)
    history.report("hsr")
    character.extend([pull(31, "Robin", rarity=5), pull(32, rarity=4, name="Tingyun")])
    api.calls.clear()

    report = history.report("hsr")

    assert [(c["banner"], c["stop"], c["pages"]) for c in api.calls][0] == (11, 30, None)
    assert [(p.name, p.rarity) for p in report.new_pulls] == [("Tingyun", 4), ("Robin", 5)]
    assert report.new_pulls[1].pulls == 31
    assert report.stats[0].total == 32


def test_a_full_report_reads_every_page_and_completes_the_store(tmp_path, locallow, hsr_cache):
    api = FakeLogApi({11: pulls_until(1, 130)})
    history = make_history(tmp_path, locallow, api)
    history.report("hsr")

    report = history.report("hsr", full=True)

    assert {c["pages"] for c in api.calls[-5:]} == {None}
    assert report.complete is True
    assert report.stats[0].total == 130


def test_an_expired_cached_authkey_falls_back_to_one_made_from_the_cookie(
    tmp_path, locallow, hsr_cache
):
    class ExpiredThenFine(FakeLogApi):
        def fetch(self, base, game, banner, authkey, **kwargs):
            if authkey == AUTHKEY:
                self.calls.append({"authkey": authkey})
                raise GachaError("authkey", "expired")
            return super().fetch(base, game, banner, authkey, **kwargs)

    api = ExpiredThenFine({11: pulls_until(1, 5)})
    hoyolab = FakeAuthkeys()

    report = make_history(tmp_path, locallow, api, hoyolab=hoyolab).report("hsr")

    assert hoyolab.asked == ["hsr"]
    assert report.updated is True
    assert {c["authkey"] for c in api.calls[1:]} == {"COOKIEKEY"}
    assert {c["base"] for c in api.calls[1:]} == {BASE}


def test_without_a_cache_the_cookie_authkey_uses_the_known_endpoint(tmp_path, locallow):
    api = FakeLogApi({11: pulls_until(1, 5)})

    make_history(tmp_path, locallow, api, hoyolab=FakeAuthkeys()).report("hsr")

    assert {c["base"] for c in api.calls} == {HSR.history_api}


@pytest.mark.parametrize("hoyolab", [None, FakeAuthkeys(has_cookie_token=False)])
def test_without_any_authkey_the_report_asks_to_open_the_history_in_the_game(
    tmp_path, locallow, hoyolab
):
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000001, pulls_until(1, 7), complete=True)
    api = FakeLogApi({})

    report = make_history(tmp_path, locallow, api, hoyolab=hoyolab).report("hsr")

    assert api.calls == []
    assert report.updated is False
    assert report.problem == "Buka riwayat warp di HSR sekali, lalu tanya lagi."
    assert report.stats[0].pity == 7  # the saved history still answers


def test_a_failed_cookie_authkey_still_asks_to_open_the_history(tmp_path, locallow):
    hoyolab = FakeAuthkeys(failure=GachaError("cookie", "Cookie HoYoLAB tidak berlaku lagi."))

    report = make_history(tmp_path, locallow, FakeLogApi({}), hoyolab=hoyolab).report("hsr")

    assert report.problem == "Buka riwayat warp di HSR sekali, lalu tanya lagi."


def test_a_failure_while_reading_keeps_the_saved_history_and_saves_nothing(
    tmp_path, locallow, hsr_cache
):
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000001, pulls_until(1, 7), complete=True)
    api = FakeLogApi({}, failure=GachaError("network", "Server riwayat HSR tidak bisa dihubungi."))

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert (report.updated, report.problem) == (False, "Server riwayat HSR tidak bisa dihubungi.")
    assert report.stats[0].total == 7
    assert store.load(HSR).records == pulls_until(1, 7)


def test_a_report_without_refresh_only_reads_the_store(tmp_path, locallow, hsr_cache):
    GachaStore(tmp_path / "gacha").save(HSR, 800000001, pulls_until(1, 3), complete=True)
    api = FakeLogApi({11: pulls_until(1, 9)})

    report = make_history(tmp_path, locallow, api).report("hsr", refresh=False)

    assert api.calls == []
    assert (report.updated, report.problem, report.stats[0].total) == (False, None, 3)


def test_the_latest_pull_is_shown_in_local_time(tmp_path, locallow, hsr_cache):
    api = FakeLogApi({11: [pull(1)]})  # 2026-10-11 22:00:01 server time (UTC+8)

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert report.latest_pull == datetime(
        2026, 10, 11, 21, 0, 1, tzinfo=timezone(timedelta(hours=7))
    )


def test_another_account_starts_its_own_history(tmp_path, locallow, hsr_cache):
    # Record ids grow with time across accounts, so the new account's pulls come after.
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000009, pulls_until(1, 50, uid=800000009), complete=True)
    api = FakeLogApi({11: pulls_until(51, 4, uid=800000001)})

    report = make_history(tmp_path, locallow, api).report("hsr")

    # Read again from the start once the records showed another account.
    assert [c["stop"] for c in api.calls if c["banner"] == 11] == [50, 0]
    assert report.stats[0].total == 4
    assert store.load(HSR).uid == 800000001
    assert GachaStore(tmp_path / "gacha").load(HSR).records == pulls_until(51, 4, uid=800000001)


def test_an_empty_history_saves_nothing_and_later_reports_still_work(tmp_path, locallow, hsr_cache):
    history = make_history(tmp_path, locallow, FakeLogApi({}))

    first = history.report("hsr")
    second = history.report("hsr")

    assert (first.updated, first.stats) == (True, ())
    assert (second.updated, second.problem, second.stats) == (True, None, ())
    assert not (tmp_path / "gacha").exists()


def test_the_store_skips_files_it_cannot_read(tmp_path, caplog):
    directory = tmp_path / "gacha"
    store = GachaStore(directory)
    store.save(HSR, 800000001, pulls_until(1, 3), complete=True)
    broken_uid = {"version": 1, "game": "hsr", "uid": None, "complete": True, "sequence": 9}
    (directory / "hsr-None.json").write_text(json.dumps({**broken_uid, "records": []}))
    (directory / "hsr-broken.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        loaded = store.load(HSR)

    assert (loaded.uid, len(loaded.records)) == (800000001, 3)
    assert "hsr-broken.json" in caplog.text and "hsr-None.json" in caplog.text


def test_switching_back_to_an_account_carries_on_from_its_own_history(
    tmp_path, locallow, hsr_cache
):
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000001, pulls_until(1, 300), complete=True)  # account A, read in full
    store.save(HSR, 800000009, pulls_until(301, 5, uid=800000009), complete=True)  # then alt B
    a_history = [*pulls_until(1, 300), *pulls_until(400, 5)]  # back on A, five new pulls
    api = FakeLogApi({11: a_history})

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert [c["stop"] for c in api.calls if c["banner"] == 11] == [305, 300]
    a = store.load(HSR, uid=800000001)
    assert (len(a.records), a.complete) == (305, True)
    assert (report.stats[0].total, report.complete) == (305, True)
    assert len(store.load(HSR, uid=800000009).records) == 5  # B's own history is untouched


def test_the_report_names_the_account_it_read_also_when_nothing_new_was_saved(
    tmp_path, locallow, hsr_cache
):
    store = GachaStore(tmp_path / "gacha")
    main = [*pulls_until(1, 40), *pulls_until(41, 5, banner=12)]
    store.save(HSR, 800000001, main, complete=True)  # account A
    store.save(HSR, 800000009, pulls_until(100, 10, uid=800000009), complete=True)  # then alt B
    api = FakeLogApi({11: pulls_until(1, 40), 12: pulls_until(41, 5, banner=12)})  # back on A

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert (report.uid, report.new_count) == (800000001, 0)
    assert [stats.total for stats in report.stats] == [40, 5]
    assert store.load(HSR).uid == 800000009  # nothing new on A, so nothing was saved


def test_a_report_that_could_not_refresh_names_the_stored_account(tmp_path, locallow, hsr_cache):
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000009, pulls_until(1, 10, uid=800000009), complete=True)
    api = FakeLogApi({}, failure=GachaError("network", "Server riwayat HSR tidak bisa dihubungi."))

    report = make_history(tmp_path, locallow, api).report("hsr")

    assert (report.uid, report.problem) == (800000009, "Server riwayat HSR tidak bisa dihubungi.")


# --- this week's pulls ---------------------------------------------------------------------------

WIB = timezone(timedelta(hours=7))
# A week back from Sunday 18 October 2026, 19:00 WIB: 20:00 on the Asia server (UTC+8).
WEEK_START = datetime(2026, 10, 11, 19, 0, tzinfo=WIB)


def pulled_at(record_id: int, server_time: str, name: str = "Arrows", **kwargs) -> GachaRecord:
    """A pull made at `server_time`, as the history API writes it (UTC+8)."""
    return dataclasses.replace(pull(record_id, name, **kwargs), time=server_time)


def test_this_weeks_pulls_count_each_banner_and_tell_each_top_and_its_fifty_fifty():
    records = [
        *[pulled_at(i, "2026-10-05 12:00:00") for i in range(1, 50)],
        pulled_at(50, "2026-10-05 12:00:01", "Himeko", rarity=5),  # lost last week
        *[pulled_at(i, "2026-10-12 21:00:00") for i in range(51, 80)],
        pulled_at(80, "2026-10-12 21:00:01", "Castorice", rarity=5),  # guaranteed this week
        *[pulled_at(i, "2026-10-13 09:00:00") for i in range(81, 100)],
        pulled_at(100, "2026-10-13 09:00:01", "Anaxa", rarity=5),  # won this week
        pulled_at(101, "2026-10-14 10:00:00", banner=12),
        pulled_at(102, "2026-10-14 10:00:00", "Long May Rainbows Adorn", banner=12, rarity=5),
        pulled_at(103, "2026-10-01 10:00:00", banner=1),  # no pull this week
    ]

    character, light_cone = pulls_since(HSR, records, WEEK_START, 8)

    assert (character.banner.type, character.count) == (11, 50)
    assert [(t.name, t.pulls, t.won_5050) for t in character.tops] == [
        ("Anaxa", 20, True),
        ("Castorice", 30, None),
    ]
    assert (light_cone.banner.type, light_cone.count) == (12, 2)
    assert [(t.name, t.pulls, t.won_5050) for t in light_cone.tops] == [
        ("Long May Rainbows Adorn", 2, None)
    ]


def test_the_week_starts_just_after_since_in_server_time():
    records = [
        pulled_at(1, "2026-10-11 20:00:00"),  # 19:00 WIB exactly: last week's report saw it
        pulled_at(2, "2026-10-11 20:00:01"),
        pulled_at(3, "2026-10-18 19:59:59"),
    ]

    [character] = pulls_since(HSR, records, WEEK_START, 8)

    assert character.count == 2


def test_until_also_bounds_the_pulls_from_above():
    records = [
        pulled_at(1, "2026-10-12 01:00:00"),  # 00:00 WIB exactly: the day before's
        pulled_at(2, "2026-10-12 01:00:01"),
        pulled_at(3, "2026-10-13 01:00:00"),  # the next midnight: still this day's
        pulled_at(4, "2026-10-13 01:00:01"),
    ]
    midnight = datetime(2026, 10, 12, 0, 0, tzinfo=WIB)

    [character] = pulls_since(HSR, records, midnight, 8, until=midnight + timedelta(days=1))

    assert character.count == 2
    assert [banner.count for banner in pulls_since(HSR, records, midnight, 8)] == [3]


def test_the_server_offset_decides_when_a_pull_was_made():
    records = [pulled_at(1, "2026-10-11 19:30:00")]  # 18:30 WIB on UTC+8, 20:30 WIB on UTC+6

    assert pulls_since(HSR, records, WEEK_START, 8) == []
    assert [banner.count for banner in pulls_since(HSR, records, WEEK_START, 6)] == [1]


def test_a_week_without_pulls_has_no_banner():
    records = pulls_until(1, 30, "Acheron")  # all on 2026-10-11 before 23:00 server time

    assert pulls_since(HSR, records, datetime(2026, 10, 12, 0, 0, tzinfo=WIB), 8) == []
    assert pulls_since(HSR, [], WEEK_START, 8) == []


def test_the_owners_off_banner_pick_this_week_is_a_lost_fifty_fifty():
    records = [*[pulled_at(i, "2026-10-12 21:00:00") for i in range(1, 40)]]
    records.append(pulled_at(40, "2026-10-12 21:00:01", "Yunli", rarity=5))

    [character] = pulls_since(HSR, records, WEEK_START, 8, ["Yunli"])

    assert [(t.name, t.won_5050) for t in character.tops] == [("Yunli", False)]


def test_a_zzz_s_rank_is_a_top_of_the_week():
    zzz = GAMES["zzz"]
    records = [
        *[pulled_at(i, "2026-10-12 21:00:00", banner=2, rarity=3) for i in range(1, 10)],
        pulled_at(10, "2026-10-12 21:00:00", "Lycaon", banner=2, rarity=3),  # an A-rank
        pulled_at(11, "2026-10-12 21:00:01", "Ellen", banner=2, rarity=4),
    ]

    [exclusive] = pulls_since(zzz, records, WEEK_START, 8)

    assert (exclusive.count, [(t.name, t.pulls) for t in exclusive.tops]) == (11, [("Ellen", 11)])


def test_the_partial_history_hint_is_the_reports_own():
    hint = partial_history_hint(HSR)

    assert hint == (
        "Riwayat HSR baru terbaca sebagian. Jalankan 'waifu gacha history hsr --full' sekali "
        "untuk impor lengkap."
    )
