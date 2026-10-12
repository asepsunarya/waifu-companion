"""Pull history: the authkey the game cached, and the game's own history API.

When the owner opens the history page in a game, the game's web view caches that
page's URL, authkey included (valid about a day), in `webCaches/*/Cache/Cache_Data/
data_2` inside the game's data folder; the game's log under LocalLow names that
folder. The history API is read with that authkey, newest first, 20 records a page.
genshin.py's own history routes are not used: HSR and ZZZ moved their endpoints,
and the cached URL always carries the current one. Authkeys and URL queries never
reach a log line or an error message.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from waifu.gacha.games import Banner, Game
from waifu.gacha.hoyolab import GachaError

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
AUTHKEY_RETCODES = frozenset({-100, -101})
RATE_LIMIT_RETCODE = -110
RATE_LIMIT_BACKOFF_S = (1.0, 2.0)
_HISTORY_URL = re.compile(
    rb"https://[^\s\"'<>\x00]+?/(?:getGachaLog|getLdGachaLog)\?[^\s\"'<>\x00]+"
)
_AUTHKEY = re.compile(r"[?&]authkey=([^&#]+)")

HttpGet = Callable[[str, float], Any]


@dataclass(frozen=True)
class CachedGachaUrl:
    base: str  # the API folder, ending with "/"
    authkey: str = field(repr=False)


@dataclass(frozen=True)
class GachaRecord:
    id: int
    uid: int
    banner: int  # the banner type it was asked for (Genshin's 400 comes inside 301)
    name: str
    rarity: int
    item_type: str
    time: str  # server time, as the API writes it: YYYY-MM-DD HH:MM:SS


def find_cached_gacha_url(game: Game, locallow: Path) -> CachedGachaUrl | None:
    """The history URL the game cached last, or None when there is none."""
    data_dir = _data_folder(game, locallow)
    if data_dir is None:
        return None
    try:
        caches = [
            path / "Cache" / "Cache_Data" / "data_2" for path in (data_dir / "webCaches").iterdir()
        ]
    except OSError:
        return None
    for data_2 in sorted((p for p in caches if p.is_file()), key=_modified, reverse=True):
        urls = _HISTORY_URL.findall(_read_cache(game, data_2))
        if urls:
            return _parse_history_url(urls[-1].decode("utf-8", errors="replace"))
    return None


def _data_folder(game: Game, locallow: Path) -> Path | None:
    pattern = re.compile(r"([A-Za-z]:[\\/][^\r\n]*?" + re.escape(game.data_folder) + ")")
    for name in game.log_files:
        try:
            log = (locallow / name).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise _locked(game) from exc
        match = pattern.search(log)
        if match:
            return Path(match[1])
    return None


def _read_cache(game: Game, path: Path) -> bytes:
    try:
        return path.read_bytes()
    except PermissionError as exc:
        raise _locked(game) from exc


def _locked(game: Game) -> GachaError:
    return GachaError(
        "locked",
        f"Riwayat {game.short} sedang dikunci game. Tutup {game.short} sebentar, lalu coba lagi.",
    )


def _modified(path: Path) -> float:
    return path.stat().st_mtime


def _parse_history_url(url: str) -> CachedGachaUrl | None:
    match = _AUTHKEY.search(url)
    if match is None:
        return None
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path.rsplit('/', 1)[0]}/"
    # unquote, not unquote_plus: a literal "+" in an authkey must stay a "+".
    return CachedGachaUrl(base, urllib.parse.unquote(match[1]))


def get_json(url: str, timeout_s: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


class GachaLogApi:
    """Reads one banner's history from the game's history API, newest first.

    Requests stay `request_gap_s` apart, across banners too: on the owner's PC five
    back-to-back requests drew retcode -110 ("visit too frequently") while twelve
    0.5 s apart all passed. A -110 is retried after 1 s and then 2 s.
    """

    def __init__(
        self,
        *,
        http_get: HttpGet = get_json,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        request_gap_s: float = 0.5,
        timeout_s: float = 15.0,
    ) -> None:
        self._get = http_get
        self._sleep = sleep
        self._clock = clock
        self._request_gap_s = request_gap_s
        self._timeout_s = timeout_s
        self._last_request: float | None = None

    def fetch(
        self,
        base: str,
        game: Game,
        banner: Banner,
        authkey: str,
        *,
        stop_at_id: int = 0,
        max_pages: int | None = None,
    ) -> list[GachaRecord]:
        """Records newer than `stop_at_id`, newest first, reading at most `max_pages` pages."""
        records: list[GachaRecord] = []
        end_id = 0
        pages = 0
        while max_pages is None or pages < max_pages:
            batch = self._page(base, game, banner, authkey, end_id)
            pages += 1
            for record in batch:
                if record.id <= stop_at_id:
                    return records
                records.append(record)
            if len(batch) < PAGE_SIZE:
                break
            end_id = batch[-1].id
        return records

    def _page(
        self, base: str, game: Game, banner: Banner, authkey: str, end_id: int
    ) -> list[GachaRecord]:
        query = {
            "authkey_ver": "1",
            "sign_type": "2",
            "auth_appid": "webview_gacha",
            "authkey": authkey,
            "lang": "en",
            "game_biz": game.game_biz,
            "gacha_type": str(banner.type),
            "real_gacha_type": str(banner.type),
            "size": str(PAGE_SIZE),
            "end_id": str(end_id),
        }
        url = f"{base}{banner.endpoint}?{urllib.parse.urlencode(query)}"
        for backoff_s in (*RATE_LIMIT_BACKOFF_S, None):
            data = self._request(url, game)
            retcode = data.get("retcode")
            if retcode != RATE_LIMIT_RETCODE or backoff_s is None:
                break
            logger.info("The %s history API asked to slow down; retrying", game.short)
            self._sleep(backoff_s)
        if retcode != 0:
            raise _api_error(game, retcode, str(data.get("message", "")))
        items = (data.get("data") or {}).get("list") or []
        return [_record(item, banner) for item in items]

    def _request(self, url: str, game: Game) -> Any:
        if self._last_request is not None:
            wait_s = self._request_gap_s - (self._clock() - self._last_request)
            if wait_s > 0:
                self._sleep(wait_s)
        try:
            return self._get(url, self._timeout_s)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            logger.warning("The %s history API is unreachable: %s", game.short, type(exc).__name__)
            raise GachaError(
                "network",
                f"Server riwayat {game.short} tidak bisa dihubungi. Cek koneksi internet.",
            ) from None
        finally:
            self._last_request = self._clock()


def _api_error(game: Game, retcode: object, message: str) -> GachaError:
    lowered = message.lower()
    if retcode in AUTHKEY_RETCODES or ("auth" in lowered and "key" in lowered):
        return GachaError(
            "authkey",
            f"Buka riwayat {game.pull_word} di {game.short} sekali, lalu tanya lagi.",
        )
    if retcode == RATE_LIMIT_RETCODE:
        return GachaError(
            "rate_limit", f"Server riwayat {game.short} minta pelan-pelan. Coba lagi sebentar lagi."
        )
    logger.warning("The %s history API answered retcode %s", game.short, retcode)
    return GachaError("other", f"Riwayat {game.short} tidak bisa dibaca (kode {retcode}).")


def _record(item: dict[str, Any], banner: Banner) -> GachaRecord:
    return GachaRecord(
        id=int(item["id"]),
        uid=int(item["uid"]),
        banner=banner.type,
        name=item["name"],
        rarity=int(item["rank_type"]),
        item_type=item["item_type"],
        time=item["time"],
    )
