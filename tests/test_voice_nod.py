import logging
import random

import numpy as np
import pytest

from waifu.voice.nod import NODS, Nod, NodBank
from waifu.voice.tts import TtsError, clean_for_speech, split_by_language

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


def test_a_filler_made_in_a_stand_in_voice_is_left_out(caplog):
    # A Windows voice "Hmm" before a reply in her RVC voice sounds like someone else.
    bank = NodBank(FakeVoice(stand_in=(HMM,)), lines=(HMM, CHECKING))

    with caplog.at_level(logging.INFO, logger="waifu.voice.nod"):
        bank.prepare()

    assert bank.pick().text == CHECKING
    assert bank.pick() is None
    assert any("stand-in" in record.getMessage() for record in nod_logs(caplog, logging.INFO))


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
