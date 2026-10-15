"""The redeem code watcher: no test reaches the code source, HoYoLAB, or the phone.

Every `CodeWatcher` gets a fake code source, a fake HoYoLAB client that records what it was
asked to redeem (nothing is ever redeemed), a real `CodeStore` under tmp_path, a fixed time,
a fake announce, and a fake notifier. The tests of stopping while redeeming use a real
`HoyolabClient` over a fake genshin.py client with a made-up cookie. The thread tests run a
real thread whose waits last a few milliseconds.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

import pytest

from waifu.config import RedeemCodesConfig
from waifu.gacha import code_watcher
from waifu.gacha.code_store import CodeStore
from waifu.gacha.code_watcher import FIRST_POLL_DELAY_S, CodeWatcher, Wait
from waifu.gacha.codes import CodesError, RedeemResult
from waifu.gacha.games import GAMES
from waifu.gacha.hoyolab import NO_COOKIE, REDEEM_MESSAGES, GachaError, HoyolabClient

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 17, 9, 30, tzinfo=WIB)
HOUR = 3600.0

# From the source's real answer for HSR, Genshin, and ZZZ on 2026-10-17.
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
ZZZ_GIFT = ("ZENLESSGIFT", "50 polychromes and three official investigator logs")
COOKIE_REFUSED = REDEEM_MESSAGES["cookie"]
UNREACHABLE = "HoYoLAB tidak bisa dihubungi. Cek koneksi internet."

ASK_HSR = f"Master, ada 2 kode HSR baru: {JADE_60[1]}, dan {JADE_30[1]}. Mau kutukarkan?"
ASK_HSR_PUSH = f"{ASK_HSR}\n{JADE_60[0]}: {JADE_60[1]}\n{JADE_30[0]}: {JADE_30[1]}"
REDEEMED_HSR = (
    f"Master, aku sudah menukarkan 2 kode HSR baru: {JADE_60[1]}, dan {JADE_30[1]}. "
    "Hadiahnya ada di surat dalam game."
)


class FakeReadCodes:
    """Stands in for codes.read_codes: hands out `found` (or raises it) and remembers the
    games it was asked for."""

    def __init__(self, found: dict | CodesError | Exception | None = None) -> None:
        self.found = found if found is not None else {}
        self.asked: list[list[str]] = []

    def __call__(self, games: Sequence[str]) -> dict | CodesError:
        self.asked.append(list(games))
        if isinstance(self.found, Exception) and not isinstance(self.found, CodesError):
            raise self.found
        return self.found


class FakeHoyolab:
    """Stands in for HoyolabClient.redeem_codes: records each call and answers each code with
    its outcome in `outcomes` (redeemed by default) and HoyolabClient's message for it (an
    error's problem is `problem`), as HoyolabClient does for at most 10, handing each result
    over as it goes. With `cut_after`, the call is cut short after that many results. It
    ignores `should_stop`: the tests of stopping use a real HoyolabClient."""

    def __init__(
        self,
        outcomes: dict[str, str] | None = None,
        failure: Exception | None = None,
        *,
        problem: str = UNREACHABLE,
        cut_after: int | None = None,
    ) -> None:
        self.outcomes = outcomes or {}
        self.failure = failure
        self.problem = problem
        self.cut_after = cut_after
        self.calls: list[tuple[str, list[str]]] = []

    def redeem_codes(
        self, game_id: str, codes: Sequence[str], *, on_result=None, should_stop=None
    ) -> list[RedeemResult]:
        self.calls.append((game_id, list(codes)))
        if self.failure is not None:
            raise self.failure
        results = []
        for code in codes[:10]:
            if len(results) == self.cut_after:
                raise RuntimeError("cut short")
            outcome = self.outcomes.get(code, "redeemed")
            message = REDEEM_MESSAGES[outcome].format(
                code=code, game=GAMES[game_id].short, problem=self.problem
            )
            results.append(RedeemResult(game_id, code, outcome, message))  # type: ignore[arg-type]
            if on_result is not None:
                on_result(results[-1])
        return results


class FakeGenshinClient:
    """genshin.Client's redeem_code: records each code and answers that it was redeemed (nothing
    reaches HoYoLAB); `during_first` runs while the first code is answered."""

    def __init__(self, during_first: Callable[[], None] = lambda: None) -> None:
        self.during_first = during_first
        self.redeemed: list[str] = []

    async def redeem_code(self, code, uid=None, *, game=None, lang=None, region=None) -> None:
        self.redeemed.append(code)
        if len(self.redeemed) == 1:
            self.during_first()


def real_hoyolab(genshin_client: FakeGenshinClient) -> HoyolabClient:
    """A real HoyolabClient over `genshin_client`, with a made-up cookie and uid, that never
    waits between codes."""

    async def no_gap(seconds: float) -> None:
        return None

    return HoyolabClient(
        "cookie_token_v2=made-up; account_id_v2=1",
        uids={"hsr": 800000001},
        client_factory=lambda cookie: genshin_client,
        sleep=no_gap,
    )


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
        found: dict | CodesError | Exception | None = None,
        *,
        hoyolab: FakeHoyolab | HoyolabClient | None = None,
        games: tuple[str, ...] = ("hsr", "genshin", "zzz"),
        owner_call: str = "Master",
        notifier: FakeNotifier | None = None,
        wait: Wait = threading.Event.wait,
        **config: object,
    ) -> None:
        self.now = NOW
        self.store = CodeStore(tmp_path / "redeem_codes.json", now=lambda: self.now)
        self.read = FakeReadCodes(found)
        self.hoyolab = hoyolab or FakeHoyolab()
        self.notifier = notifier if notifier is not None else FakeNotifier()
        self.announced: list[tuple[str, str]] = []
        self.watcher = CodeWatcher(
            self.store,
            self.read,
            self.hoyolab,
            RedeemCodesConfig(**config),
            list(games),  # type: ignore[arg-type]
            announce=lambda text, emotion: self.announced.append((text, emotion)),
            notifier=self.notifier,
            owner_call=owner_call,
            now=lambda: self.now,
            wait=wait,
        )

    def poll(self) -> list[str]:
        return self.watcher.poll_once()


def restarted(rig: Rig) -> CodeWatcher:
    """A new watcher on the same file, as after Cyrene restarts."""
    return CodeWatcher(
        CodeStore(rig.store.path, now=lambda: rig.now),
        rig.read,
        rig.hoyolab,
        rig.watcher._config,
        ["hsr", "genshin", "zzz"],
        announce=lambda text, emotion: rig.announced.append((text, emotion)),
        notifier=rig.notifier,
        owner_call="Master",
        now=lambda: rig.now,
    )


# --- telling and asking ------------------------------------------------------------------------


def test_new_codes_are_said_and_pushed_with_their_codes_and_she_asks_to_redeem_them(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]})

    notices = rig.poll()

    assert notices == [ASK_HSR]
    assert rig.announced == [(ASK_HSR, "happy")]
    assert rig.notifier.sent == [("Cyrene", ASK_HSR_PUSH, ("gift",), None)]
    assert rig.hoyolab.calls == []  # nothing is redeemed without Master asking


def test_the_source_is_asked_for_the_configured_games_once_per_poll(tmp_path):
    rig = Rig(tmp_path, games=("hsr", "zzz"))

    rig.poll()
    rig.poll()

    assert rig.read.asked == [["hsr", "zzz"], ["hsr", "zzz"]]


def test_the_codes_are_kept_as_seen_now(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60]})

    rig.poll()

    [kept] = rig.store.unredeemed("hsr")
    assert (kept.code, kept.rewards, kept.first_seen) == (JADE_60[0], JADE_60[1], NOW)


def test_announced_codes_are_never_announced_again_also_after_a_restart(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]})
    rig.poll()

    assert rig.poll() == []
    assert restarted(rig).poll_once() == []
    assert len(rig.announced) == 1
    assert len(rig.notifier.sent) == 1


def test_only_the_codes_new_since_the_last_poll_are_told(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60]})
    rig.poll()
    rig.read.found = {"hsr": [JADE_60, JADE_30]}

    [notice] = rig.poll()

    assert notice == f"Master, ada 1 kode HSR baru: {JADE_30[1]}. Mau kutukarkan?"


def test_each_game_with_new_codes_is_its_own_notice_in_the_configured_order(tmp_path):
    rig = Rig(tmp_path, {"zzz": [ZZZ_GIFT], "hsr": [], "genshin": [GENSHIN_GIFT]})

    notices = rig.poll()

    assert notices == [
        f"Master, ada 1 kode Genshin baru: {GENSHIN_GIFT[1]}. Mau kutukarkan?",
        f"Master, ada 1 kode ZZZ baru: {ZZZ_GIFT[1]}. Mau kutukarkan?",
    ]
    assert [message.split("\n")[1:] for _, message, _, _ in rig.notifier.sent] == [
        [f"{GENSHIN_GIFT[0]}: {GENSHIN_GIFT[1]}"],
        [f"{ZZZ_GIFT[0]}: {ZZZ_GIFT[1]}"],
    ]


def test_at_most_three_rewards_are_said_and_the_rest_counted_while_the_push_lists_all(tmp_path):
    codes = [(f"CODE{n}AAAA", f"{n}0 stellar jade") for n in range(1, 6)]
    rig = Rig(tmp_path, {"hsr": codes})

    [notice] = rig.poll()

    assert notice == (
        "Master, ada 5 kode HSR baru: 10 stellar jade, 20 stellar jade, 30 stellar jade, dan 2 "
        "kode lainnya. Mau kutukarkan?"
    )
    assert rig.notifier.sent[0][1].split("\n")[1:] == [f"{code}: {gives}" for code, gives in codes]


def test_a_code_without_rewards_text_is_said_as_such_and_pushed_alone(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60, ("NOREWARD123", "")]})

    [notice] = rig.poll()

    assert notice == (
        f"Master, ada 2 kode HSR baru: {JADE_60[1]}, dan satu kode tanpa keterangan hadiah. "
        "Mau kutukarkan?"
    )
    assert rig.notifier.sent[0][1].split("\n")[1:] == [f"{JADE_60[0]}: {JADE_60[1]}", "NOREWARD123"]


def test_a_code_redeemed_before_she_told_it_is_never_told(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]})
    rig.store.add("hsr", [JADE_60], NOW)  # `waifu gacha codes hsr --redeem` found and used it
    rig.store.record([RedeemResult("hsr", JADE_60[0], "redeemed", "Berhasil.")])

    [notice] = rig.poll()

    assert notice == f"Master, ada 1 kode HSR baru: {JADE_30[1]}. Mau kutukarkan?"


def test_a_game_the_source_failed_alone_leaves_the_others_told(tmp_path):
    rig = Rig(tmp_path, {"genshin": [GENSHIN_GIFT]})  # HSR and ZZZ left out by read_codes

    assert rig.poll() == [f"Master, ada 1 kode Genshin baru: {GENSHIN_GIFT[1]}. Mau kutukarkan?"]


@pytest.mark.parametrize("owner_call", ["Sayang", "sayang"])
def test_the_notices_call_the_owner_what_she_calls_them(tmp_path, owner_call):
    rig = Rig(tmp_path, {"hsr": [JADE_60]}, owner_call=owner_call)

    [notice] = rig.poll()

    assert notice.startswith("Sayang, ada 1 kode HSR baru")


# --- redeeming by herself ------------------------------------------------------------------------


def test_with_auto_redeem_new_codes_are_redeemed_recorded_and_the_outcome_told(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True)

    notices = rig.poll()

    assert rig.hoyolab.calls == [("hsr", [JADE_60[0], JADE_30[0]])]
    assert notices == [REDEEMED_HSR]
    assert rig.announced == [(REDEEMED_HSR, "happy")]
    assert rig.notifier.sent == [
        (
            "Cyrene",
            f"{REDEEMED_HSR}\n{JADE_60[0]} ({JADE_60[1]}): berhasil ditukarkan\n"
            f"{JADE_30[0]} ({JADE_30[1]}): berhasil ditukarkan",
            ("gift",),
            None,
        )
    ]
    assert rig.store.unredeemed("hsr") == []
    assert [outcome for _, outcome in rig.store.recent("hsr", 5)] == ["redeemed", "redeemed"]


def test_with_auto_redeem_a_restart_never_redeems_or_tells_them_again(tmp_path):
    rig = Rig(
        tmp_path,
        {"hsr": [JADE_60]},
        auto_redeem=True,
        hoyolab=FakeHoyolab({JADE_60[0]: "cooldown"}),
    )
    rig.poll()

    restarted(rig).poll_once()
    rig.poll()

    assert rig.hoyolab.calls == [("hsr", [JADE_60[0]])]
    assert len(rig.announced) == 1


def test_codes_that_did_not_all_work_are_told_by_outcome_after_the_redeemed_ones(tmp_path):
    hoyolab = FakeHoyolab({JADE_30[0]: "claimed", GIFT[0]: "expired"})
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30, GIFT]}, auto_redeem=True, hoyolab=hoyolab)

    [notice] = rig.poll()

    assert notice == (
        f"Master, aku sudah menukarkan 1 kode HSR baru: {JADE_60[1]}. Hadiahnya ada di surat "
        "dalam game. Selain itu, 1 kode sudah pernah ditukarkan, dan 1 kode sudah kedaluwarsa."
    )
    assert rig.notifier.sent[0][1].split("\n")[1:] == [
        f"{JADE_60[0]} ({JADE_60[1]}): berhasil ditukarkan",
        f"{JADE_30[0]} ({JADE_30[1]}): sudah pernah ditukarkan",
        f"{GIFT[0]} ({GIFT[1]}): sudah kedaluwarsa",
    ]


def test_codes_none_of_which_worked_are_told_plainly(tmp_path):
    hoyolab = FakeHoyolab({JADE_60[0]: "claimed", JADE_30[0]: "claimed"})
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True, hoyolab=hoyolab)

    [notice] = rig.poll()

    assert notice == "Master, ada 2 kode HSR baru, tapi 2 kode sudah pernah ditukarkan."
    assert rig.announced[0][1] == "neutral"


def test_a_refused_cookie_is_told_with_what_to_do_and_the_codes_stay_to_redeem(tmp_path):
    hoyolab = FakeHoyolab({JADE_60[0]: "cookie", JADE_30[0]: "cookie"})
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True, hoyolab=hoyolab)

    [notice] = rig.poll()

    assert notice == (
        f"Master, ada 2 kode HSR baru, tapi 2 kode belum bisa ditukarkan. {COOKIE_REFUSED} "
        "Minta aku mencoba lagi nanti."
    )
    assert rig.announced[0][1] == "sad"
    assert [code.code for code in rig.store.unredeemed("hsr")] == [JADE_60[0], JADE_30[0]]
    assert rig.store.unannounced("hsr") == []


def test_one_reason_codes_were_not_redeemed_is_said_and_every_push_line_names_its_own(tmp_path):
    # ZZZ is among the games, but the HoYoLAB account has no ZZZ role.
    hoyolab = FakeHoyolab(
        {ZZZ_GIFT[0]: "error"}, problem="Akun ZZZ tidak ditemukan di HoYoLAB ini."
    )
    rig = Rig(tmp_path, {"zzz": [ZZZ_GIFT]}, auto_redeem=True, hoyolab=hoyolab)

    [notice] = rig.poll()

    assert notice == (
        "Master, ada 1 kode ZZZ baru, tapi 1 kode belum bisa ditukarkan. Akun ZZZ tidak "
        "ditemukan di HoYoLAB ini. Minta aku mencoba lagi nanti."
    )
    assert rig.notifier.sent[0][1].split("\n")[1:] == [
        f"{ZZZ_GIFT[0]} ({ZZZ_GIFT[1]}): belum bisa ditukarkan. Akun ZZZ tidak ditemukan di "
        "HoYoLAB ini."
    ]


def test_different_reasons_codes_were_not_redeemed_are_left_to_the_push(tmp_path):
    hoyolab = FakeHoyolab({JADE_60[0]: "cooldown", JADE_30[0]: "error"})
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True, hoyolab=hoyolab)

    [notice] = rig.poll()

    assert notice == (
        "Master, ada 2 kode HSR baru, tapi 2 kode belum bisa ditukarkan. Minta aku mencoba "
        "lagi nanti."
    )
    assert rig.notifier.sent[0][1].split("\n")[1:] == [
        f"{JADE_60[0]} ({JADE_60[1]}): belum bisa ditukarkan. HoYoLAB sedang membatasi "
        "penukaran kode. Coba lagi sebentar lagi.",
        f"{JADE_30[0]} ({JADE_30[1]}): belum bisa ditukarkan. {UNREACHABLE}",
    ]


def test_with_auto_redeem_a_store_that_cannot_record_still_tells_what_was_redeemed(
    tmp_path, monkeypatch, caplog
):
    # HoYoLAB has redeemed them by then: only the bookkeeping may be lost.
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True)

    def file_in_use(results) -> None:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(rig.store, "record", file_in_use)

    with caplog.at_level(logging.ERROR):
        notices = rig.poll()

    assert notices == [REDEEMED_HSR]
    assert rig.announced == [(REDEEMED_HSR, "happy")]
    assert len(rig.notifier.sent) == 1
    assert "Could not record" in caplog.text


def test_a_redemption_cut_short_keeps_the_outcomes_already_known(tmp_path):
    # As when Cyrene stops in the middle of redeeming: what HoYoLAB did is kept.
    hoyolab = FakeHoyolab(cut_after=1)
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True, hoyolab=hoyolab)

    assert rig.poll() == []

    assert rig.store.recent("hsr", 5)[0][1] == "redeemed"
    assert [code.code for code in rig.store.unredeemed("hsr")] == [JADE_30[0]]


def stopping_rig(tmp_path, genshin_client: FakeGenshinClient) -> Rig:
    """A rig with auto_redeem, three new HSR codes, and a real HoyolabClient over
    `genshin_client`, whose watcher stops while HoYoLAB answers the first code."""
    rig = Rig(
        tmp_path,
        {"hsr": [JADE_60, JADE_30, GIFT]},
        auto_redeem=True,
        hoyolab=real_hoyolab(genshin_client),
    )
    genshin_client.during_first = rig.watcher.stop
    return rig


def test_stopping_while_redeeming_redeems_no_more_codes_and_says_and_pushes_nothing(tmp_path):
    genshin_client = FakeGenshinClient()
    rig = stopping_rig(tmp_path, genshin_client)

    assert rig.poll() == []

    assert genshin_client.redeemed == [JADE_60[0]]
    assert (rig.announced, rig.notifier.sent) == ([], [])
    assert [(code.code, outcome) for code, outcome in rig.store.recent("hsr", 5)] == [
        (JADE_60[0], "redeemed")
    ]


def test_codes_a_stopped_redemption_left_untried_are_redeemed_and_told_at_the_next_start(
    tmp_path,
):
    # The code HoYoLAB redeemed while she stopped is kept in her recent outcomes, untold.
    genshin_client = FakeGenshinClient()
    rig = stopping_rig(tmp_path, genshin_client)
    rig.poll()

    notices = restarted(rig).poll_once()

    assert genshin_client.redeemed == [JADE_60[0], JADE_30[0], GIFT[0]]
    assert notices == [
        f"Master, aku sudah menukarkan 2 kode HSR baru: {JADE_30[1]}, dan {GIFT[1]}. "
        "Hadiahnya ada di surat dalam game."
    ]
    assert rig.store.unannounced("hsr") == []


def test_with_auto_redeem_but_no_cookie_she_asks_instead(tmp_path, caplog):
    hoyolab = FakeHoyolab(failure=GachaError("no_cookie", NO_COOKIE))
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, auto_redeem=True, hoyolab=hoyolab)

    with caplog.at_level(logging.INFO):
        notices = rig.poll()

    assert notices == [ASK_HSR]
    assert rig.store.unannounced("hsr") == []
    assert "no HoYoLAB cookie" in caplog.text


def test_with_auto_redeem_more_than_ten_new_codes_are_redeemed_ten_at_a_poll(tmp_path):
    codes = [(f"CODE{n:02d}AAA", "10 stellar jade") for n in range(12)]
    rig = Rig(tmp_path, {"hsr": codes}, auto_redeem=True)

    rig.poll()
    rig.poll()

    assert rig.hoyolab.calls == [
        ("hsr", [code for code, _ in codes[:10]]),
        ("hsr", [code for code, _ in codes[10:]]),
    ]


def test_a_redemption_that_raises_is_logged_and_tried_again_at_the_next_poll(tmp_path, caplog):
    hoyolab = FakeHoyolab(failure=RuntimeError("boom"))
    rig = Rig(tmp_path, {"hsr": [JADE_60]}, auto_redeem=True, hoyolab=hoyolab)

    with caplog.at_level(logging.ERROR):
        assert rig.poll() == []
    hoyolab.failure = None
    notices = rig.poll()

    assert "Could not tell or redeem new HSR codes" in caplog.text
    assert notices == [
        f"Master, aku sudah menukarkan 1 kode HSR baru: {JADE_60[1]}. Hadiahnya ada di surat "
        "dalam game."
    ]


# --- failures ------------------------------------------------------------------------------------


def test_a_source_that_cannot_be_read_is_logged_tells_nothing_and_is_read_again(tmp_path, caplog):
    rig = Rig(tmp_path, CodesError("Kode redeem tidak bisa diambil."))

    with caplog.at_level(logging.WARNING):
        assert rig.poll() == []
    rig.read.found = {"hsr": [JADE_60]}

    assert len(rig.poll()) == 1
    assert "trying again at the next poll" in caplog.text


def test_a_source_that_raises_is_logged_and_the_watcher_goes_on(tmp_path, caplog):
    rig = Rig(tmp_path, RuntimeError("boom"))

    with caplog.at_level(logging.ERROR):
        assert rig.poll() == []

    assert "trying again at the next poll" in caplog.text
    assert rig.announced == []


def test_an_announcement_that_raises_is_logged_and_the_push_still_goes(tmp_path, caplog):
    rig = Rig(tmp_path, {"hsr": [JADE_60]})

    def broken(text: str, emotion: str) -> None:
        raise RuntimeError("no voice")

    rig.watcher._announce = broken

    with caplog.at_level(logging.ERROR):
        rig.poll()

    assert len(rig.notifier.sent) == 1
    assert "Could not say" in caplog.text
    assert rig.store.unannounced("hsr") == []


def test_a_notice_given_but_not_marked_told_is_logged_and_still_counts(
    tmp_path, monkeypatch, caplog
):
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]})

    def file_in_use(codes) -> None:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(rig.store, "mark_announced", file_in_use)

    with caplog.at_level(logging.ERROR):
        notices = rig.poll()

    assert notices == [ASK_HSR]
    assert "Could not mark" in caplog.text


# --- with the notices off ------------------------------------------------------------------------


def test_with_notices_off_new_codes_are_kept_but_never_told_or_redeemed(tmp_path):
    rig = Rig(tmp_path, {"hsr": [JADE_60, JADE_30]}, notices=False, auto_redeem=True)

    assert rig.poll() == []

    assert (rig.announced, rig.notifier.sent, rig.hoyolab.calls) == ([], [], [])
    assert [code.code for code in rig.store.unredeemed("hsr")] == [JADE_60[0], JADE_30[0]]
    assert len(rig.store.unannounced("hsr")) == 2  # told once the notices are on


@pytest.mark.parametrize("outcome", [False, OSError("offline")])
def test_a_push_that_fails_or_raises_never_stops_anything(tmp_path, outcome, caplog):
    rig = Rig(tmp_path, {"hsr": [JADE_60]}, notifier=FakeNotifier(outcome))

    with caplog.at_level(logging.ERROR):
        notices = rig.poll()

    assert len(notices) == 1
    assert len(rig.announced) == 1
    assert rig.store.unannounced("hsr") == []


def test_without_notifications_she_only_says_it_and_without_a_voice_she_only_pushes(tmp_path):
    quiet = Rig(tmp_path / "a", {"hsr": [JADE_60]})
    quiet.watcher._notifier = None
    pushed = Rig(tmp_path / "b", {"hsr": [JADE_60]})
    pushed.watcher._announce = None

    assert len(quiet.poll()) == 1
    assert len(pushed.poll()) == 1
    assert (len(quiet.announced), len(quiet.notifier.sent)) == (1, 0)
    assert (len(pushed.announced), len(pushed.notifier.sent)) == (0, 1)


# --- the thread ----------------------------------------------------------------------------------


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


class SignallingRead(FakeReadCodes):
    def __init__(self) -> None:
        super().__init__({})
        self.read = threading.Event()

    def __call__(self, games: Sequence[str]) -> dict | CodesError:
        self.read.set()
        return super().__call__(games)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "code-watcher"]


def make_watcher(tmp_path, read, *, config: RedeemCodesConfig, **options) -> CodeWatcher:
    return CodeWatcher(
        CodeStore(tmp_path / "redeem_codes.json", now=lambda: NOW),
        read,
        FakeHoyolab(),
        config,
        ["hsr"],
        announce=None,
        notifier=None,
        owner_call="Master",
        now=lambda: NOW,
        **options,
    )


def test_the_first_poll_comes_soon_after_start_and_then_every_poll_hours(tmp_path):
    read = SignallingRead()
    waits = ShortWaits()
    watcher = make_watcher(tmp_path, read, config=RedeemCodesConfig(poll_hours=6), wait=waits)

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while len(waits.asked) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert FIRST_POLL_DELAY_S <= 120
    assert waits.asked[:3] == [FIRST_POLL_DELAY_S, 6 * HOUR, 6 * HOUR]
    assert len(read.asked) >= 2


def test_the_daemon_thread_stops_at_once_although_its_next_poll_is_far_away(tmp_path):
    read = SignallingRead()
    watcher = make_watcher(tmp_path, read, config=RedeemCodesConfig(poll_hours=48))

    watcher.start()
    try:
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1
    assert watcher_threads() == []
    assert read.asked == []  # stopped before its first poll


def test_nothing_runs_with_the_codes_off(tmp_path):
    read = SignallingRead()
    watcher = make_watcher(
        tmp_path, read, config=RedeemCodesConfig(enabled=False), wait=ShortWaits()
    )

    watcher.start()
    try:
        assert watcher_threads() == []
    finally:
        watcher.stop()

    assert read.asked == []


def test_with_the_notices_off_it_still_reads_the_codes_so_her_answers_stay_current(tmp_path):
    read = SignallingRead()
    read.found = {"hsr": [JADE_60]}
    watcher = make_watcher(
        tmp_path, read, config=RedeemCodesConfig(notices=False), wait=ShortWaits()
    )

    watcher.start()
    try:
        assert read.read.wait(5)
    finally:
        watcher.stop()

    assert [code.code for code in watcher._store.unredeemed("hsr")] == [JADE_60[0]]


class BlockingRead(FakeReadCodes):
    """A read that waits until released, as a slow source does."""

    def __init__(self, found: dict) -> None:
        super().__init__(found)
        self.reading = threading.Event()
        self.release = threading.Event()

    def __call__(self, games: Sequence[str]) -> dict | CodesError:
        self.reading.set()
        self.release.wait(5)
        return super().__call__(games)


def test_a_poll_stopped_while_reading_redeems_says_and_pushes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(code_watcher, "STOP_TIMEOUT_S", 0.01)
    read = BlockingRead({"hsr": [JADE_60]})
    hoyolab = FakeHoyolab()
    notifier = FakeNotifier()
    announced: list[tuple[str, str]] = []
    watcher = CodeWatcher(
        CodeStore(tmp_path / "redeem_codes.json", now=lambda: NOW),
        read,
        hoyolab,
        RedeemCodesConfig(auto_redeem=True),
        ["hsr"],
        announce=lambda text, emotion: announced.append((text, emotion)),
        notifier=notifier,
        owner_call="Master",
        now=lambda: NOW,
        wait=ShortWaits(),
    )
    watcher.start()
    [thread] = watcher_threads()
    assert read.reading.wait(5)

    watcher.stop()
    read.release.set()
    thread.join(5)

    assert not thread.is_alive()
    assert (hoyolab.calls, announced, notifier.sent) == ([], [], [])


def test_after_stop_returns_a_redemption_in_flight_redeems_no_more_codes(tmp_path, monkeypatch):
    # stop() gives up waiting while HoYoLAB still answers the first code; the daemon thread
    # then lives on until the process ends, and must not redeem the other codes meanwhile.
    monkeypatch.setattr(code_watcher, "STOP_TIMEOUT_S", 0.01)
    answering, answered = threading.Event(), threading.Event()

    def slow_answer() -> None:
        answering.set()
        answered.wait(5)

    genshin_client = FakeGenshinClient(slow_answer)
    rig = Rig(
        tmp_path,
        {"hsr": [JADE_60, JADE_30, GIFT]},
        auto_redeem=True,
        hoyolab=real_hoyolab(genshin_client),
        games=("hsr",),
        wait=ShortWaits(),
    )
    rig.watcher.start()
    [thread] = watcher_threads()
    assert answering.wait(5)

    rig.watcher.stop()
    answered.set()
    thread.join(5)

    assert not thread.is_alive()
    assert genshin_client.redeemed == [JADE_60[0]]
    assert (rig.announced, rig.notifier.sent) == ([], [])


def test_starting_twice_runs_one_thread(tmp_path):
    watcher = make_watcher(
        tmp_path, SignallingRead(), config=RedeemCodesConfig(), wait=ShortWaits()
    )

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []
