"""The free offers watcher: no test reaches Epic, GamerPower, Claude, or the phone.

Every `OffersWatcher` gets a fake games reader, a fake AI scan, a real `OffersStore` under
tmp_path, a fake local time, a fake announce, and a fake notifier. Game times are made in
this PC's own zone, so the notices read the same wherever the suite runs. The thread tests
run a real thread whose waits are real or last a few milliseconds.
"""

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from waifu.config import FreeOffersConfig
from waifu.offers.errors import OffersError
from waifu.offers.store import OffersStore
from waifu.offers.values import AiOffer, GameGiveaway
from waifu.offers.watcher import OffersWatcher

NOW = datetime(2026, 10, 16, 9, 30)  # a Friday, local time as the store keeps it
HOUR = 3600.0


def local(*moment: int) -> datetime:
    """An aware time in this PC's zone, like Epic's UTC times once read here."""
    return datetime(*moment).astimezone()


def game(
    title: str,
    *,
    platform: str = "Epic Games Store",
    ends_at: datetime | None = None,
    ends_on: date | None = None,
    starts_at: datetime | None = None,
) -> GameGiveaway:
    source = "Epic Games Store" if platform == "Epic Games Store" else "GamerPower.com"
    slug = title.lower().replace(" ", "-")
    return GameGiveaway(
        id=f"{'epic' if source == platform else 'gamerpower'}:{slug}",
        title=title,
        platform=platform,
        source=source,
        worth_usd=None,
        starts_at=starts_at,
        ends_at=ends_at,
        ends_on=ends_on,
        url=f"https://example.com/{slug}",
    )


THURSDAY_NIGHT = local(2026, 10, 22, 22, 0)
BAD_CHEESE = game("Bad Cheese", starts_at=local(2026, 10, 15, 22, 0), ends_at=THURSDAY_NIGHT)
LOST_CASTLE = game("Lost Castle", starts_at=local(2026, 10, 15, 22, 0), ends_at=THURSDAY_NIGHT)
SKER_RITUAL = game(  # free from next Thursday
    "Sker Ritual", starts_at=THURSDAY_NIGHT, ends_at=local(2026, 10, 29, 22, 0)
)
PORTAL = game("Portal", platform="Steam", ends_on=date(2026, 10, 25))
EPIC_NOTICE = (
    "Master, ada game gratis baru: Bad Cheese dan Lost Castle di Epic, sampai Kamis 22.00."
)


# Each provider's own site, where its offers' links lead.
SITES = {
    "Anthropic": "www.anthropic.com",
    "Google": "ai.google.dev",
    "AWS": "aws.amazon.com",
    "Mistral AI": "mistral.ai",
    "OpenAI": "openai.com",
    "xAI": "x.ai",
}


def offer(title: str, provider: str, **changes: object) -> AiOffer:
    return replace(
        AiOffer(
            id=f"ai:{title.lower().replace(' ', '')[:12]}",
            title=title,
            provider=provider,
            kind="free_credits",
            access="claim",
            eligibility="Siapa saja.",
            deadline=None,
            url=f"https://{SITES[provider]}/{title.lower().replace(' ', '-')}",
            summary="Kredit gratis.",
            first_seen=NOW,
        ),
        **changes,
    )


CLAUDE_STARTUPS = offer("Claude for Startups", "Anthropic", kind="startup_program", access="apply")
GEMINI_FREE = offer("Gemini API free tier", "Google", kind="free_tier")
FIRST_LINK = "Link yang pertama ada di notifikasi HP; minta aku kalau mau link yang lain."
AI_NOTICE = (
    "Master, aku menemukan 2 promo AI baru: Claude for Startups dari Anthropic dan Gemini API "
    f"free tier dari Google. {FIRST_LINK}"
)
CREDIT = ", infonya dari GamerPower.com."


class FakeReadGames:
    """Stands in for offers.games.read_free_games: hands out `games`, or raises it."""

    def __init__(self, *games: GameGiveaway) -> None:
        self.games: tuple[GameGiveaway, ...] | OffersError | Exception = games
        self.calls = 0

    def __call__(self) -> tuple[GameGiveaway, ...] | OffersError:
        self.calls += 1
        if isinstance(self.games, Exception) and not isinstance(self.games, OffersError):
            raise self.games
        return self.games


class FakeScan:
    """Stands in for offers.ai_scan.scan_ai_offers: remembers the offers it was told are known
    and hands out `found`, or raises it."""

    def __init__(self, *found: AiOffer) -> None:
        self.found: list[AiOffer] | OffersError | Exception = list(found)
        self.known: list[list[AiOffer]] = []

    def __call__(self, known: Sequence[AiOffer]) -> list[AiOffer] | OffersError:
        self.known.append(list(known))
        if isinstance(self.found, Exception) and not isinstance(self.found, OffersError):
            raise self.found
        return self.found


class FakeNotifier:
    def __init__(self, outcome: bool | Exception = True) -> None:
        self.sent: list[tuple[str, str, tuple[str, ...], str | None]] = []
        self.outcome = outcome

    def send(self, title, message, *, tags=(), click=None):
        self.sent.append((title, message, tuple(tags), click))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Rig:
    """A watcher and the fakes around it."""

    def __init__(
        self,
        tmp_path,
        *games: GameGiveaway,
        found: Sequence[AiOffer] = (),
        owner_call: str = "Master",
        notifier: FakeNotifier | None = None,
        **config: object,
    ) -> None:
        self.now = NOW
        self.store = OffersStore(tmp_path / "free_offers.json", now=lambda: self.now)
        self.read = FakeReadGames(*games)
        self.scan = FakeScan(*found)
        self.notifier = notifier if notifier is not None else FakeNotifier()
        self.announced: list[tuple[str, str]] = []
        self.watcher = OffersWatcher(
            self.store,
            self.read,
            self.scan,
            FreeOffersConfig(**config),
            announce=lambda text, emotion: self.announced.append((text, emotion)),
            notifier=self.notifier,
            owner_call=owner_call,
            now=lambda: self.now,
        )

    def poll(self, *, after: timedelta = timedelta(0)) -> list[str]:
        self.now += after
        return self.watcher.poll_once()


def games_only(tmp_path, *games: GameGiveaway, **config: object) -> Rig:
    return Rig(tmp_path, *games, ai=False, **config)


def ai_only(tmp_path, *found: AiOffer, **config: object) -> Rig:
    return Rig(tmp_path, found=found, games=False, **config)


# --- free games ----------------------------------------------------------------------------


def test_new_free_games_are_one_happy_notice_pushed_with_the_first_game_s_link(tmp_path):
    rig = games_only(tmp_path, BAD_CHEESE, LOST_CASTLE)

    notices = rig.poll()

    assert notices == [EPIC_NOTICE]
    assert rig.announced == [(EPIC_NOTICE, "happy")]
    assert rig.notifier.sent == [("Cyrene", EPIC_NOTICE, ("video_game",), BAD_CHEESE.url)]


def test_games_already_told_are_not_told_again(tmp_path):
    rig = games_only(tmp_path, BAD_CHEESE, LOST_CASTLE)
    rig.poll()

    again = rig.poll(after=timedelta(hours=6))
    rig.read.games = (BAD_CHEESE, LOST_CASTLE, PORTAL)
    only_new = rig.poll(after=timedelta(hours=6))

    assert again == []
    assert only_new == [f"Master, ada game gratis baru: Portal di Steam, sampai 25 Oktober{CREDIT}"]
    assert rig.read.calls == 3


def test_a_game_free_only_soon_is_told_once_it_is_free(tmp_path):
    rig = games_only(tmp_path, SKER_RITUAL)

    soon = rig.poll()
    rig.now = datetime(2026, 10, 22, 23, 0)
    free = rig.poll()

    assert soon == []
    # A week away from today is a date, not a weekday that could be today's.
    assert free == ["Master, ada game gratis baru: Sker Ritual di Epic, sampai 29 Oktober 22.00."]


def test_games_of_several_stores_are_grouped_by_store_and_end(tmp_path):
    later = game("Lost Castle", ends_at=local(2026, 11, 2, 23, 0))
    rig = games_only(tmp_path, BAD_CHEESE, PORTAL, later)

    assert rig.poll() == [
        "Master, ada game gratis baru: Bad Cheese di Epic, sampai Kamis 22.00; Portal di Steam, "
        f"sampai 25 Oktober; Lost Castle di Epic, sampai 2 November 23.00{CREDIT}"
    ]


def test_at_most_three_games_are_named_and_the_rest_counted(tmp_path):
    games = [game(f"Game {number}", platform="GOG") for number in range(5)]
    rig = games_only(tmp_path, *games)

    assert rig.poll() == [
        "Master, ada game gratis baru: Game 0, Game 1, dan Game 2 di GOG, dan 2 game lainnya"
        f"{CREDIT}"
    ]
    assert rig.notifier.sent[0][3] == games[0].url


@pytest.mark.parametrize(
    ("ends", "until"),
    [
        ({"ends_at": local(2026, 10, 16, 23, 59)}, "hari ini 23.59"),
        ({"ends_at": local(2026, 10, 17, 4, 0)}, "besok 04.00"),
        ({"ends_on": date(2026, 10, 16)}, "hari ini"),
        ({"ends_on": date(2026, 10, 17)}, "besok"),
        ({"ends_on": date(2026, 10, 22)}, "Kamis"),
        ({"ends_on": date(2026, 10, 23)}, "23 Oktober"),
    ],
)
def test_the_end_is_said_as_today_tomorrow_a_weekday_or_a_date(tmp_path, ends, until):
    rig = games_only(tmp_path, game("Portal", platform="Steam", **ends))

    assert rig.poll() == [f"Master, ada game gratis baru: Portal di Steam, sampai {until}{CREDIT}"]


def test_a_game_without_an_end_is_told_without_one(tmp_path):
    rig = games_only(tmp_path, game("Portal", platform="itch.io"))

    assert rig.poll() == [f"Master, ada game gratis baru: Portal di itch.io{CREDIT}"]


def test_a_gamerpower_game_only_counted_is_still_credited(tmp_path):
    epic = [game(f"Epic {number}") for number in range(3)]
    rig = games_only(tmp_path, *epic, PORTAL)

    assert rig.poll() == [
        "Master, ada game gratis baru: Epic 0, Epic 1, dan Epic 2 di Epic, dan 1 game lainnya"
        f"{CREDIT}"
    ]


def test_epic_games_alone_need_no_credit(tmp_path):
    rig = games_only(tmp_path, BAD_CHEESE, LOST_CASTLE)

    [notice] = rig.poll()

    assert "GamerPower" not in notice


def test_a_failed_read_is_logged_tells_nothing_and_the_next_poll_reads_again(tmp_path, caplog):
    rig = games_only(tmp_path, BAD_CHEESE, LOST_CASTLE)
    rig.read.games = OffersError("Daftar game gratis tidak bisa diambil.")

    with caplog.at_level(logging.WARNING, logger="waifu.offers.watcher"):
        failed = rig.poll()
    rig.read.games = (BAD_CHEESE, LOST_CASTLE)
    later = rig.poll(after=timedelta(hours=6))

    assert (failed, later) == ([], [EPIC_NOTICE])
    assert "Could not read the free games" in caplog.text


def test_a_reader_that_raises_is_logged_and_the_watcher_goes_on(tmp_path, caplog):
    rig = games_only(tmp_path)
    rig.read.games = RuntimeError("socket gone")

    with caplog.at_level(logging.ERROR, logger="waifu.offers.watcher"):
        assert rig.poll() == []

    assert "socket gone" in caplog.text


def test_with_the_games_off_they_are_never_read(tmp_path):
    rig = ai_only(tmp_path)

    rig.poll()

    assert rig.read.calls == 0


# --- AI offers -----------------------------------------------------------------------------


def test_the_first_poll_scans_when_no_scan_ever_ran_and_tells_the_new_offers(tmp_path):
    rig = ai_only(tmp_path, CLAUDE_STARTUPS, GEMINI_FREE)

    notices = rig.poll()

    assert notices == [AI_NOTICE]
    assert rig.scan.known == [[]]
    assert rig.announced == [(AI_NOTICE, "happy")]
    assert rig.notifier.sent == [("Cyrene", AI_NOTICE, ("robot",), CLAUDE_STARTUPS.url)]
    assert rig.store.ai_offers() == [CLAUDE_STARTUPS, GEMINI_FREE]
    assert rig.store.last_ai_scan() == NOW


def test_the_next_scan_comes_ai_scan_days_after_the_last_one_and_knows_its_offers(tmp_path):
    rig = ai_only(tmp_path, CLAUDE_STARTUPS, ai_scan_days=7)
    rig.poll()

    rig.scan.found = [CLAUDE_STARTUPS, GEMINI_FREE]
    too_soon = rig.poll(after=timedelta(days=7) - timedelta(minutes=1))
    due = rig.poll(after=timedelta(minutes=1))

    assert too_soon == []
    assert len(rig.scan.known) == 2
    assert rig.scan.known[1] == [CLAUDE_STARTUPS]
    assert due == [
        "Master, aku menemukan 1 promo AI baru: Gemini API free tier dari Google. "
        "Linknya ada di notifikasi HP."
    ]


def test_a_scan_that_finds_nothing_new_tells_nothing_but_counts_as_the_last_scan(tmp_path):
    rig = ai_only(tmp_path)

    assert rig.poll() == []
    assert rig.announced == [] and rig.notifier.sent == []
    assert rig.store.last_ai_scan() == NOW


def test_a_failed_scan_is_logged_and_tried_again_at_the_next_poll(tmp_path, caplog):
    rig = ai_only(tmp_path, GEMINI_FREE)
    rig.scan.found = OffersError("Server Claude sedang sibuk.")

    with caplog.at_level(logging.WARNING, logger="waifu.offers.watcher"):
        failed = rig.poll()
    rig.scan.found = [GEMINI_FREE]
    retried = rig.poll(after=timedelta(hours=6))

    assert failed == []
    assert rig.store.last_ai_scan() == NOW + timedelta(hours=6)
    assert len(retried) == 1
    assert "The AI offers scan failed" in caplog.text


PAID_FAILURE = OffersError(
    "Claude selesai mencari tanpa melaporkan promo AI yang ditemukan.", billed=True
)


def test_a_scan_that_failed_after_it_was_paid_for_waits_a_day_even_across_a_restart(
    tmp_path, caplog
):
    rig = ai_only(tmp_path, GEMINI_FREE)
    rig.scan.found = PAID_FAILURE

    with caplog.at_level(logging.WARNING, logger="waifu.offers.watcher"):
        failed = rig.poll()
    next_poll = rig.poll(after=timedelta(hours=6))
    restarted = ai_only(tmp_path, GEMINI_FREE)
    restarted.now = rig.now
    after_restart = restarted.poll(after=timedelta(hours=12))
    a_day_later = restarted.poll(after=timedelta(hours=6))

    assert (failed, next_poll, after_restart) == ([], [], [])
    assert len(rig.scan.known) == 1
    assert len(restarted.scan.known) == 1  # only a day after the paid failure
    assert len(a_day_later) == 1
    assert rig.store.last_failed_ai_scan() == NOW
    assert "paid for; trying again in a day" in caplog.text


@pytest.mark.parametrize("kept", ["last_ai_scan", "last_failed_ai_scan"])
def test_a_scan_time_in_the_future_from_a_clock_set_back_counts_as_due(tmp_path, kept):
    rig = ai_only(tmp_path, GEMINI_FREE)
    future = NOW + timedelta(days=40)
    if kept == "last_ai_scan":
        rig.store.merge_ai_offers([], future)
    else:
        rig.store.record_failed_ai_scan(future)

    assert len(rig.poll()) == 1
    assert rig.scan.known == [[]]


def test_a_scan_that_raises_is_logged_and_the_watcher_goes_on(tmp_path, caplog):
    rig = Rig(tmp_path, BAD_CHEESE, LOST_CASTLE)
    rig.scan.found = RuntimeError("SDK broke")

    with caplog.at_level(logging.ERROR, logger="waifu.offers.watcher"):
        notices = rig.poll()

    assert notices == [EPIC_NOTICE]  # the games were still told
    assert rig.store.last_ai_scan() is None
    assert "SDK broke" in caplog.text


def test_with_the_ai_offers_off_there_is_never_a_scan(tmp_path):
    rig = games_only(tmp_path)

    rig.poll()

    assert rig.scan.known == []


def test_at_most_three_offers_are_named_and_one_named_after_its_provider_says_it_once(tmp_path):
    offers = [
        offer("Anthropic Claude for Education", "Anthropic"),
        offer("AWS Activate", "AWS"),
        offer("Mistral free tier", "Mistral AI"),
        offer("OpenAI credits", "OpenAI"),
        offer("xAI credits", "xAI"),
    ]
    rig = ai_only(tmp_path, *offers)

    assert rig.poll() == [
        "Master, aku menemukan 5 promo AI baru: Anthropic Claude for Education, AWS Activate, "
        f"Mistral free tier dari Mistral AI, dan 2 lainnya. {FIRST_LINK}"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://claude-pro-free.example/login",
        "https://anthropic.com@evil.example/claim",
        "https://anthropic.com.evil.example/claim",
        "https://claude-credits.example\\.anthropic.com/claim",
        "https://github.com/attacker/free-claude-credits",
    ],
)
def test_a_link_off_the_provider_s_own_site_never_opens_from_the_notice(tmp_path, url):
    rig = ai_only(tmp_path, replace(CLAUDE_STARTUPS, url=url), GEMINI_FREE)

    [notice] = rig.poll()

    assert notice.endswith("Minta aku kalau mau linknya dikirim ke HP.")
    assert rig.notifier.sent == [("Cyrene", notice, ("robot",), None)]


def test_without_notifications_she_offers_no_link_and_pushes_nothing(tmp_path):
    rig = ai_only(tmp_path, GEMINI_FREE)
    rig.watcher = OffersWatcher(
        rig.store,
        rig.read,
        rig.scan,
        FreeOffersConfig(games=False),
        announce=lambda text, emotion: rig.announced.append((text, emotion)),
        notifier=None,
        owner_call="Master",
        now=lambda: rig.now,
    )

    rig.poll()

    assert rig.announced == [
        (
            "Master, aku menemukan 1 promo AI baru: Gemini API free tier dari Google. Tanya aku "
            "kalau mau tahu lebih lanjut.",
            "happy",
        )
    ]


# --- both ----------------------------------------------------------------------------------


def test_new_games_and_new_offers_in_one_poll_are_two_notices(tmp_path):
    rig = Rig(tmp_path, BAD_CHEESE, LOST_CASTLE, found=[CLAUDE_STARTUPS, GEMINI_FREE])

    assert rig.poll() == [EPIC_NOTICE, AI_NOTICE]
    assert [click for *_, click in rig.notifier.sent] == [BAD_CHEESE.url, CLAUDE_STARTUPS.url]


@pytest.mark.parametrize("owner_call", ["sayang", "Sayang"])
def test_the_notices_call_the_owner_what_she_calls_them(tmp_path, owner_call):
    rig = Rig(tmp_path, BAD_CHEESE, found=[GEMINI_FREE], owner_call=owner_call)

    notices = rig.poll()

    assert [notice.split(",")[0] for notice in notices] == ["Sayang", "Sayang"]


def test_an_announcement_that_raises_is_logged_and_the_push_still_goes(tmp_path, caplog):
    rig = games_only(tmp_path, BAD_CHEESE, LOST_CASTLE)

    def announce(text: str, emotion: str) -> None:
        raise RuntimeError("the voice loop is gone")

    rig.watcher._announce = announce

    with caplog.at_level(logging.ERROR, logger="waifu.offers.watcher"):
        assert rig.poll() == [EPIC_NOTICE]

    assert len(rig.notifier.sent) == 1
    assert "the voice loop is gone" in caplog.text


@pytest.mark.parametrize("outcome", [False, OSError("no internet")])
def test_a_push_that_fails_or_raises_never_stops_anything(tmp_path, outcome, caplog):
    rig = Rig(tmp_path, BAD_CHEESE, LOST_CASTLE, found=[GEMINI_FREE])
    rig.notifier.outcome = outcome

    with caplog.at_level(logging.ERROR, logger="waifu.offers.watcher"):
        notices = rig.poll()

    assert len(notices) == 2
    assert [text for text, _ in rig.announced] == notices
    assert ("no internet" in caplog.text) == isinstance(outcome, OSError)


def test_a_watcher_without_a_voice_only_pushes(tmp_path):
    rig = games_only(tmp_path, BAD_CHEESE, LOST_CASTLE)
    rig.watcher._announce = None

    assert rig.poll() == [EPIC_NOTICE]
    assert len(rig.notifier.sent) == 1


# --- the thread ----------------------------------------------------------------------------


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


class SignallingRead(FakeReadGames):
    def __init__(self, *games: GameGiveaway) -> None:
        super().__init__(*games)
        self.read = threading.Event()

    def __call__(self) -> tuple[GameGiveaway, ...] | OffersError:
        self.read.set()
        return super().__call__()


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "offers-watcher"]


def make_watcher(tmp_path, read, *, config: FreeOffersConfig, **options) -> OffersWatcher:
    return OffersWatcher(
        OffersStore(tmp_path / "free_offers.json", now=lambda: NOW),
        read,
        FakeScan(),
        config,
        announce=None,
        notifier=None,
        owner_call="Master",
        now=lambda: NOW,
        **options,
    )


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once(tmp_path):
    read = SignallingRead()
    watcher = make_watcher(tmp_path, read, config=FreeOffersConfig(games_poll_hours=48))

    watcher.start()
    try:
        assert read.read.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1  # although the next poll was two days away
    assert watcher_threads() == []
    assert read.calls == 1


def test_the_next_poll_comes_games_poll_hours_after_the_last_one_began(tmp_path):
    read = SignallingRead()
    waits = ShortWaits()
    seconds = iter([1000.0, 1090.0] + [2000.0, 2000.0] * 100)  # the first poll took 90 s
    watcher = make_watcher(
        tmp_path,
        read,
        config=FreeOffersConfig(games_poll_hours=6),
        clock=lambda: next(seconds),
        wait=waits,
    )

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while len(waits.asked) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[:2] == [6 * HOUR - 90, 6 * HOUR]
    assert read.calls >= 2


@pytest.mark.parametrize("switch", ["enabled", "notices"])
def test_nothing_runs_with_the_offers_or_their_notices_off(tmp_path, switch):
    read = SignallingRead()
    watcher = make_watcher(tmp_path, read, config=FreeOffersConfig(**{switch: False}))

    watcher.start()
    try:
        assert watcher_threads() == []
    finally:
        watcher.stop()

    assert read.calls == 0


def test_starting_twice_runs_one_thread(tmp_path):
    watcher = make_watcher(tmp_path, FakeReadGames(), config=FreeOffersConfig(), wait=ShortWaits())

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []
