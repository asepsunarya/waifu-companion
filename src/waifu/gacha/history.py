"""Pull history: the game's history API, a local store per account, pity and 50/50.

When the owner opens the history page in a game, the game's web view caches that
page's URL, authkey included (valid about a day), in `webCaches/*/Cache/Cache_Data/
data_2` inside the game's data folder; the game's log under LocalLow names that
folder. The history API is read with that authkey (or one made from the HoYoLAB
cookie), newest first, 20 records a page. genshin.py's own history routes are not
used: HSR and ZZZ moved their endpoints, and the cached URL always carries the
current one. Authkeys and URL queries never reach a log line or an error message.

Records are kept per account in `data/gacha/<game>-<uid>.json`, so a refresh only
reads what is newer than the store; pity and the 50/50 are worked out from them.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from waifu.gacha.files import write_json_atomic
from waifu.gacha.games import GAMES, Banner, Game, is_standard, server_timezone
from waifu.gacha.hoyolab import GachaError

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
FIRST_IMPORT_PAGES = 5  # a first read during a voice reply stops after 100 pulls a banner
RECENT_TOPS = 5
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
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


def _open_history_first(game: Game) -> GachaError:
    return GachaError(
        "authkey", f"Buka riwayat {game.pull_word} di {game.short} sekali, lalu tanya lagi."
    )


def _api_error(game: Game, retcode: object, message: str) -> GachaError:
    lowered = message.lower()
    if retcode in AUTHKEY_RETCODES or ("auth" in lowered and "key" in lowered):
        return _open_history_first(game)
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


# --- the store, pity and 50/50 -----------------------------------------------------------------


@dataclass(frozen=True)
class StoredHistory:
    uid: int | None
    records: list[GachaRecord]  # oldest first
    complete: bool  # every page was read once, not only the newest ones


class GachaStore:
    """One JSON file per game account; `load` returns the account saved last."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def load(self, game: Game) -> StoredHistory:
        saved = self._saved(game)
        if not saved:
            return StoredHistory(None, [], False)
        latest = max(saved, key=lambda raw: raw["sequence"])
        uid = int(latest["uid"])
        records = [GachaRecord(uid=uid, **record) for record in latest["records"]]
        return StoredHistory(uid, records, bool(latest["complete"]))

    def save(self, game: Game, uid: int, records: Iterable[GachaRecord], *, complete: bool) -> None:
        # A counter, not file times: NTFS times move in ~15 ms steps, too coarse to order saves.
        sequence = 1 + max((raw["sequence"] for raw in self._saved(game)), default=0)
        data = {
            "version": 1,
            "game": game.id,
            "uid": uid,
            "complete": complete,
            "sequence": sequence,
            "records": [
                {
                    "id": record.id,
                    "banner": record.banner,
                    "name": record.name,
                    "rarity": record.rarity,
                    "item_type": record.item_type,
                    "time": record.time,
                }
                for record in sorted(records, key=lambda record: record.id)
            ],
        }
        write_json_atomic(self.directory / f"{game.id}-{uid}.json", data)

    def _saved(self, game: Game) -> list[dict[str, Any]]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.directory.glob(f"{game.id}-*.json"))
        ]


@dataclass(frozen=True)
class TopPull:
    """A top-rarity item and how it came."""

    id: int
    name: str
    pulls: int  # pulls it took on its banner
    won_5050: bool | None  # None: no 50/50 on that banner, or the pull was guaranteed
    time: str


@dataclass(frozen=True)
class BannerStats:
    banner: Banner
    pity: int  # pulls since the last top-rarity item
    total: int
    guaranteed: bool | None  # the next top-rarity item is the featured one; None: no 50/50
    recent_tops: tuple[TopPull, ...]  # newest first

    @property
    def to_hard_pity(self) -> int:
        return max(0, self.banner.hard_pity - self.pity)


def banner_stats(
    game: Game, records: Iterable[GachaRecord], extra_standard: Iterable[str] = ()
) -> list[BannerStats]:
    """Each banner's pity, total, guarantee, and last top-rarity items, in display order."""
    extra = list(extra_standard)
    stats = []
    for banner, pulls in _by_banner(game, records):
        tops, pity, guaranteed = _walk(game, banner, pulls, extra)
        stats.append(
            BannerStats(
                banner=banner,
                pity=pity,
                total=len(pulls),
                guaranteed=guaranteed if banner.fifty_fifty else None,
                recent_tops=tuple(reversed(tops[-RECENT_TOPS:])),
            )
        )
    return stats


def _by_banner(
    game: Game, records: Iterable[GachaRecord]
) -> list[tuple[Banner, list[GachaRecord]]]:
    grouped: dict[int, list[GachaRecord]] = {}
    for record in sorted(records, key=lambda record: record.id):
        grouped.setdefault(record.banner, []).append(record)
    return [(banner, grouped[banner.type]) for banner in game.banners if banner.type in grouped]


def _walk(
    game: Game, banner: Banner, pulls: list[GachaRecord], extra: Sequence[str]
) -> tuple[list[TopPull], int, bool]:
    """Every top-rarity pull oldest first, the pity since the last one, and the guarantee."""
    tops: list[TopPull] = []
    since = 0
    guaranteed = False
    for record in pulls:
        since += 1
        if record.rarity != game.top_rarity:
            continue
        won: bool | None = None
        if banner.fifty_fifty:
            if guaranteed:
                guaranteed = False
            elif is_standard(game, record.name, extra):
                won, guaranteed = False, True
            else:
                won = True
        tops.append(TopPull(record.id, record.name, since, won, record.time))
        since = 0
    return tops, since, guaranteed


# --- the report --------------------------------------------------------------------------------


class AuthkeyMaker(Protocol):
    """The part of `HoyolabClient` that makes history authkeys from the cookie."""

    @property
    def has_cookie_token(self) -> bool: ...

    def authkey(self, game_id: str) -> str: ...


@dataclass(frozen=True)
class NewPull:
    banner: Banner
    name: str
    rarity: int
    time: str
    pulls: int | None  # for a top-rarity item: the pulls it took
    won_5050: bool | None


@dataclass(frozen=True)
class HistoryReport:
    game: Game
    updated: bool  # read from the game just now
    problem: str | None  # why it could not be read (the saved history answers instead)
    complete: bool
    stats: tuple[BannerStats, ...]
    new_pulls: tuple[NewPull, ...]  # the new items of the top two rarities, newest first
    new_count: int
    latest_pull: datetime | None  # local time

    @property
    def hint(self) -> str | None:
        if self.complete or not self.stats:
            return None
        return (
            f"Riwayat {self.game.short} baru terbaca sebagian. Jalankan "
            f"'waifu gacha history {self.game.id} --full' sekali untuk impor lengkap."
        )


def _local_now() -> datetime:
    return datetime.now().astimezone()


class GachaHistory:
    """Refreshes a game's history from the game and reports pity, 50/50, and new pulls."""

    def __init__(
        self,
        store: GachaStore,
        api: GachaLogApi,
        *,
        locallow: Path | None = None,
        hoyolab: AuthkeyMaker | None = None,
        extra_standard: Mapping[str, Sequence[str]] | None = None,
        server_utc_offset: int = 8,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._store = store
        self._api = api
        self._locallow = locallow or Path.home() / "AppData" / "LocalLow"
        self._hoyolab = hoyolab
        self._extra = extra_standard or {}
        self._offset = server_utc_offset
        self._now = now

    def report(self, game_id: str, *, refresh: bool = True, full: bool = False) -> HistoryReport:
        """Never raises `GachaError`: a failed refresh reports the saved history with a problem."""
        game = GAMES[game_id]
        stored = self._store.load(game)
        records, complete, new = stored.records, stored.complete, []
        problem = None
        if refresh:
            try:
                uid, records, new, complete = self._refresh(game, stored, full)
            except GachaError as error:
                records, complete, problem = stored.records, stored.complete, error.message
            else:
                if new or complete != stored.complete:
                    self._store.save(game, uid, records, complete=complete)
        extra = self._extra.get(game.id, ())
        return HistoryReport(
            game=game,
            updated=refresh and problem is None,
            problem=problem,
            complete=complete,
            stats=tuple(banner_stats(game, records, extra)),
            new_pulls=self._new_pulls(game, records, new, extra),
            new_count=len(new),
            latest_pull=self._local_time(max(records, key=lambda r: r.id).time)
            if records
            else None,
        )

    def _refresh(
        self, game: Game, stored: StoredHistory, full: bool
    ) -> tuple[int | None, list[GachaRecord], list[GachaRecord], bool]:
        fetched, cut_off = self._read(game, stored.records, full)
        if fetched and stored.uid is not None and fetched[0].uid != stored.uid:
            # Another account: its history starts from nothing.
            stored = StoredHistory(None, [], False)
            fetched, cut_off = self._read(game, [], full)
        uid = fetched[0].uid if fetched else stored.uid
        known = {record.id for record in stored.records}
        new = [record for record in fetched if record.id not in known]
        records = sorted([*stored.records, *new], key=lambda record: record.id)
        complete = full or (not cut_off and (stored.complete or not stored.records))
        return uid, records, new, complete

    def _read(
        self, game: Game, stored: list[GachaRecord], full: bool
    ) -> tuple[list[GachaRecord], bool]:
        """Records newer than the store (every record when `full`), with the cached authkey or
        else one made from the cookie; and whether a first read stopped at the page limit."""
        cached = find_cached_gacha_url(game, self._locallow)
        if cached is not None:
            try:
                return self._read_with(game, cached.base, cached.authkey, stored, full)
            except GachaError as error:
                if error.kind != "authkey":
                    raise
        base = cached.base if cached is not None else game.history_api
        return self._read_with(game, base, self._cookie_authkey(game), stored, full)

    def _cookie_authkey(self, game: Game) -> str:
        if self._hoyolab is not None and self._hoyolab.has_cookie_token:
            try:
                return self._hoyolab.authkey(game.id)
            except GachaError as error:
                logger.info("No %s history authkey from the cookie: %s", game.short, error.kind)
        raise _open_history_first(game)

    def _read_with(
        self, game: Game, base: str, authkey: str, stored: list[GachaRecord], full: bool
    ) -> tuple[list[GachaRecord], bool]:
        newest: dict[int, int] = {}
        for record in stored:
            newest[record.banner] = max(newest.get(record.banner, 0), record.id)
        fetched: list[GachaRecord] = []
        cut_off = False
        for banner in game.banners:
            stop = 0 if full else newest.get(banner.type, 0)
            limit = None if full or stop else FIRST_IMPORT_PAGES
            batch = self._api.fetch(base, game, banner, authkey, stop_at_id=stop, max_pages=limit)
            cut_off = cut_off or (limit is not None and len(batch) >= limit * PAGE_SIZE)
            fetched.extend(batch)
        return fetched, cut_off

    def _new_pulls(
        self, game: Game, records: list[GachaRecord], new: list[GachaRecord], extra: Sequence[str]
    ) -> tuple[NewPull, ...]:
        tops = {
            top.id: top
            for banner, pulls in _by_banner(game, records)
            for top in _walk(game, banner, pulls, extra)[0]
        }
        banners = {banner.type: banner for banner in game.banners}
        notable = [
            record
            for record in sorted(new, key=lambda record: record.id, reverse=True)
            if record.rarity >= game.top_rarity - 1
        ]
        return tuple(
            NewPull(
                banner=banners[record.banner],
                name=record.name,
                rarity=record.rarity,
                time=record.time,
                pulls=tops[record.id].pulls if record.id in tops else None,
                won_5050=tops[record.id].won_5050 if record.id in tops else None,
            )
            for record in notable
        )

    def _local_time(self, server_time: str) -> datetime:
        moment = datetime.strptime(server_time, TIME_FORMAT)
        return moment.replace(tzinfo=server_timezone(self._offset)).astimezone(self._now().tzinfo)
