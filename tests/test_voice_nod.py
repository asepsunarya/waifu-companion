import logging
import random

import numpy as np
import pytest

from waifu.voice.nod import NODS, Nod, NodBank, retry_after_s
from waifu.voice.tts import (
    CONVERSION_RETRY_S,
    SpeechRouter,
    TtsError,
    clean_for_speech,
    split_by_language,
)

HMM = "Hmm, bentar ya…"
CHECKING = "Sebentar, kucek dulu ya."
THINKING = "Oke, kupikirkan dulu…"


class FakeVoice:
    """Voices each text as a clip as long as the text, at 24 kHz. `failures` maps a text to what
    it raises; a text in `stand_in` is voiced while her voice is degraded (a fallback speaks)."""

    def __init__(
        self, failures: dict[str, Exception] | None = None, stand_in: tuple[str, ...] = ()
    ) -> None:
        self.failures = dict(failures or {})
        self.stand_in = stand_in
        self.texts: list[str] = []
        self.degraded = False

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.texts.append(text)
        self.degraded = text in self.stand_in
        if text in self.failures:
            raise self.failures[text]
        return np.full(len(text), 0.5, dtype=np.float32), 24000


def nod_logs(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "waifu.voice.nod" and record.levelno == level
    ]


def test_a_bank_has_nothing_to_say_before_it_is_prepared():
    voice = FakeVoice()
    bank = NodBank(voice, lines=(HMM,))

    assert bank.pick() is None
    assert voice.texts == []


def test_prepare_voices_each_filler_once_in_order_with_the_owner_named():
    voice = FakeVoice()

    NodBank(voice, owner_call="Kak", lines=(HMM, "Tunggu sebentar ya, {owner}.")).prepare()

    assert voice.texts == [HMM, "Tunggu sebentar ya, Kak."]


def test_a_picked_filler_carries_its_words_and_its_clip():
    bank = NodBank(FakeVoice(), owner_call="Kak", lines=("Sebentar ya, {owner}…",))
    bank.prepare()

    nod = bank.pick()

    assert isinstance(nod, Nod)
    assert nod.text == "Sebentar ya, Kak…"
    assert (len(nod.audio), nod.sample_rate) == (len("Sebentar ya, Kak…"), 24000)


def test_a_filler_is_never_picked_twice_in_a_row():
    bank = NodBank(FakeVoice(), lines=(HMM, CHECKING, THINKING), rng=random.Random(7))
    bank.prepare()

    picked = [bank.pick() for _ in range(60)]

    texts = [nod.text for nod in picked if nod is not None]
    assert len(texts) == 60
    assert all(first != second for first, second in zip(texts, texts[1:], strict=False))
    assert set(texts) == {HMM, CHECKING, THINKING}


def test_with_three_kept_a_filler_is_never_one_of_the_two_picked_before_it():
    # Back-to-back slow turns are common with Claude: A, B, A sounds as repetitive as A, A.
    bank = NodBank(FakeVoice(), lines=(HMM, CHECKING, THINKING), rng=random.Random(7))
    bank.prepare()

    texts = [bank.pick().text for _ in range(60)]

    assert all(len({a, b, c}) == 3 for a, b, c in zip(texts, texts[1:], texts[2:], strict=False))


def test_with_two_kept_the_fillers_take_turns():
    bank = NodBank(FakeVoice(), lines=(HMM, CHECKING), rng=random.Random(7))
    bank.prepare()

    texts = [bank.pick().text for _ in range(6)]

    assert texts[0::2] == [texts[0]] * 3
    assert texts[1::2] == [texts[1]] * 3
    assert texts[0] != texts[1]


def test_with_only_the_last_picked_filler_kept_there_is_none_to_say():
    bank = NodBank(FakeVoice(), lines=(HMM,))
    bank.prepare()

    first, second, third = bank.pick(), bank.pick(), bank.pick()

    assert first is not None and first.text == HMM
    assert (second, third) == (None, None)


def test_a_filler_her_voice_could_not_say_is_left_out_and_warned_once(caplog):
    voice = FakeVoice({HMM: TtsError("edge mati"), CHECKING: TtsError("edge mati")})
    bank = NodBank(voice, lines=(HMM, CHECKING, THINKING))

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.nod"):
        bank.prepare()

    assert bank.pick().text == THINKING
    assert bank.pick() is None
    [warning] = nod_logs(caplog, logging.WARNING)
    assert "edge mati" in warning.getMessage()
    assert len([r for r in nod_logs(caplog, logging.DEBUG) if "edge mati" in r.getMessage()]) == 1


def test_an_unexpected_failure_is_logged_with_its_traceback_and_left_out(caplog):
    bank = NodBank(FakeVoice({HMM: RuntimeError("rusak")}), lines=(HMM, CHECKING))

    with caplog.at_level(logging.ERROR, logger="waifu.voice.nod"):
        bank.prepare()

    [error] = nod_logs(caplog, logging.ERROR)
    assert error.exc_info is not None
    assert bank.pick().text == CHECKING
    assert bank.pick() is None


def test_a_stand_in_voice_leaves_that_filler_and_the_rest_out_for_a_later_prepare(caplog):
    # A Windows voice "Hmm" before a reply in her RVC voice sounds like someone else, and the
    # router keeps the stand-in for a while: the next lines would come in it too.
    voice = FakeVoice(stand_in=(HMM,))
    bank = NodBank(voice, lines=(HMM, CHECKING))

    with caplog.at_level(logging.INFO, logger="waifu.voice.nod"):
        kept_all = bank.prepare()

    assert kept_all is False
    assert voice.texts == [HMM]
    assert bank.pick() is None
    assert any("stand-in" in record.getMessage() for record in nod_logs(caplog, logging.INFO))
    voice.stand_in = ()
    assert bank.prepare() is True
    assert {bank.pick().text, bank.pick().text} == {HMM, CHECKING}


def test_prepare_tells_whether_every_filler_is_kept():
    voice = FakeVoice({CHECKING: TtsError("edge mati")})
    bank = NodBank(voice, lines=(HMM, CHECKING))

    first = bank.prepare()
    voice.failures.clear()

    assert (first, bank.prepare()) == (False, True)


class Sleeps:
    """Notes each wait instead of waiting, then runs `meanwhile` (the time passing)."""

    def __init__(self, meanwhile=lambda seconds: None) -> None:
        self.waits: list[float] = []
        self.meanwhile = meanwhile

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.meanwhile(seconds)


def test_keep_preparing_tries_the_missing_fillers_again_after_retry_s_until_all_are_kept():
    voice = FakeVoice(stand_in=(HMM,))
    sleeps = Sleeps(meanwhile=lambda seconds: setattr(voice, "stand_in", ()))
    bank = NodBank(voice, lines=(HMM, CHECKING), retry_s=605.0, sleep=sleeps)

    bank.keep_preparing()

    assert sleeps.waits == [605.0]
    assert voice.texts == [HMM, HMM, CHECKING]
    assert {bank.pick().text, bank.pick().text} == {HMM, CHECKING}


def test_keep_preparing_waits_for_nothing_once_every_filler_is_kept():
    sleeps = Sleeps()
    bank = NodBank(FakeVoice(), lines=(HMM, CHECKING), sleep=sleeps)

    bank.keep_preparing()

    assert sleeps.waits == []


def test_a_retry_waits_past_her_voices_pauses_on_a_failing_engine_and_converter():
    assert retry_after_s(600.0) > 600.0
    assert retry_after_s(30.0) > CONVERSION_RETRY_S


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class BlippingEdge:
    """Edge failing only on its first call (a short outage), at 24 kHz."""

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        self.calls += 1
        if self.calls == 1:
            raise TtsError("koneksi edge-tts terputus")
        return np.full(len(text), 0.5, dtype=np.float32), 24000


class WindowsVoice:
    """The offline fallback: always works."""

    rvc_pitch = None

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        return np.full(len(text), 0.25, dtype=np.float32), 16000


def test_one_edge_blip_while_preparing_costs_the_fillers_only_until_the_retry():
    clock, edge = FakeClock(), BlippingEdge()
    router = SpeechRouter(
        {"id": edge}, "id", fallbacks={"id": WindowsVoice()}, fallback_retry_s=600.0, clock=clock
    )

    def time_passes(seconds: float) -> None:
        clock.now += seconds

    bank = NodBank(
        router,
        retry_s=retry_after_s(600.0),
        sleep=Sleeps(meanwhile=time_passes),
        rng=random.Random(3),
    )

    bank.keep_preparing()

    assert edge.calls == 1 + len(NODS)  # the blip, then each filler once in her own voice
    picked = {bank.pick().text for _ in range(40)}
    assert len(picked) == len(NODS)


def test_prepare_again_voices_only_the_fillers_still_missing():
    voice = FakeVoice({CHECKING: TtsError("edge mati")})
    bank = NodBank(voice, lines=(HMM, CHECKING))
    bank.prepare()

    voice.failures.clear()
    bank.prepare()

    assert voice.texts == [HMM, CHECKING, CHECKING]
    assert {bank.pick().text, bank.pick().text} == {HMM, CHECKING}


def test_prepare_says_how_many_fillers_it_kept_never_their_words(caplog):
    bank = NodBank(FakeVoice({THINKING: TtsError("edge mati")}), lines=(HMM, CHECKING, THINKING))

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.nod"):
        bank.prepare()

    assert "Prepared 2 of 3 fillers" in caplog.text
    assert "bentar" not in caplog.text and "kucek" not in caplog.text


@pytest.mark.parametrize("line", NODS)
def test_every_built_in_filler_is_one_indonesian_speech_run(line):
    # One run is one TTS call and one RVC conversion: the bank is made fast and in one voice.
    [(language, _)] = split_by_language(clean_for_speech(line.replace("{owner}", "Master")))

    assert language == "id"


def test_the_built_in_fillers_are_several_and_all_different():
    assert len(NODS) >= 4
    assert len(set(NODS)) == len(NODS)


def test_the_built_in_fillers_vary_their_words():
    # Most of Claude's turns get one: lines alike ("Tunggu sebentar ya", "Sebentar ya") sound
    # like the same filler again. Two of the owner's own three say "bentar"; no more do.
    openings = [line.split()[0].strip(",").lower() for line in NODS]

    assert len([line for line in NODS if "bentar" in line.lower()]) <= 2
    assert max(openings.count(opening) for opening in openings) <= 2


def test_one_built_in_filler_names_the_owner():
    assert len([line for line in NODS if "{owner}" in line]) == 1
