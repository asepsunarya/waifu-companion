import http.server
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from waifu.gacha.games import GAMES
from waifu.gacha.history import (
    CachedGachaUrl,
    GachaLogApi,
    GachaRecord,
    find_cached_gacha_url,
    get_json,
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
