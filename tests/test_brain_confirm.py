import threading
import time

import pytest

from waifu.brain.confirm import (
    ORDER_WORDS,
    RECORD_ORDER_WORDS,
    ConfirmationGate,
    is_yes,
    needs_confirmation,
)
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry

PC_COMMANDS = ["wake", "shutdown", "restart", "cancel_shutdown", "sleep"]
PLUG_COMMANDS = ["on", "off", "toggle"]
# Her name and wake words, as build_brain hands them to the gate.
NAMES = ("Cyrene", "cyrene", "hai cyrene", "hei cyrene")
SHUTDOWN_QUESTION = "Komputer mau kumatikan sekarang, Master? Jawab iya kalau jadi."
PLUG_QUESTION = (
    "Colokan PC mau kumatikan sekarang, Master? Listrik PC bisa langsung putus. "
    "Jawab iya kalau jadi."
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class SlowClock(FakeClock):
    """Sleeps on every read, so threads interleave inside a check."""

    def __call__(self) -> float:
        time.sleep(0.005)
        return self.now


def house() -> DeviceRegistry:
    """Mock devices only: the PC, the plug that feeds it, another plug, and a light."""
    return DeviceRegistry(
        [
            MockDevice("komputer", "Komputer", commands=PC_COMMANDS, kind="pc"),
            MockDevice("colokan_pc", "Colokan PC", commands=PLUG_COMMANDS, kind="plug"),
            MockDevice("colokan_kipas", "Colokan kipas", commands=PLUG_COMMANDS, kind="plug"),
            MockDevice("lampu", "Lampu kamar", commands=["on", "off", "toggle"], kind="light"),
        ],
        pc_power_ids={"colokan_pc"},
    )


def refused_then_answered(
    answer: str, clock: FakeClock | None = None, *, seconds_later: float = 0
) -> tuple[ConfirmationGate, DeviceRegistry]:
    """A gate that refused the PC's shutdown last turn, now in the turn Master says `answer`."""
    clock = clock or FakeClock()
    registry = house()
    gate = ConfirmationGate(clock=clock, names=NAMES)
    gate.begin_turn("Cyrene, matikan komputer.")
    assert gate.check(registry, "komputer", "shutdown", None) is not None
    clock.now += seconds_later
    gate.begin_turn(answer)
    return gate, registry


@pytest.mark.parametrize(
    "text",
    [
        "Iya, matikan.",
        "iya",
        "oke gas",
        "boleh deh",
        "はい",
        "はい、どうぞ",
        "YES!",
        "Lanjutkan",
        "iya matikan komputernya sekarang",
        "ya udah, matiin aja",
        "iya, restart",
        "Yakin, Master.",
        "yaudah matiin",
        "siap",
    ],
)
def test_is_yes_hears_a_plain_yes(text):
    assert is_yes(text, NAMES)


@pytest.mark.parametrize("text", ["Cyrene, iya.", "iya Cyrene", "hai cyrene, boleh", "iya, Master"])
def test_is_yes_skips_the_names_she_answers_to_and_master(text):
    assert is_yes(text, NAMES)


def test_is_yes_without_her_names_counts_a_name_as_another_word():
    assert not is_yes("cyrene iya")


def test_is_yes_skips_what_she_calls_the_owner_when_given_with_her_names():
    assert is_yes("iya sayang", (*NAMES, "Sayang"))
    assert not is_yes("iya sayang", NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "jangan",
        "eh jangan, tunggu",
        "ya jangan",
        "tidak",
        "ga usah",
        "nanti aja",
        "iya tapi nanti",
        "いいえ",
        "",
        "Cyrene",
        "matikan komputer",
        "matikan yang itu",
        # A "ya" in thanks, another request, or a reason to wait is no yes.
        "oke makasih ya",
        "makasih ya Cyrene",
        "nyalakan lampu kamar ya",
        "ya kali, aku lagi main game",
        "ok bentar, aku save dulu",
        "iya, sebentar",
        "iya entar aja",
        "iya ntar",
        "ya kagak lah",
        "ya ndak",
        "ok stop",
        "ok cancel",
        "ya gausah",
        "ya gajadi",
        "はい、待って",
        "ntar aja ya",
        "ntar dulu ya",
        "gausah ya",
        "bentar ya, aku save dulu",
        "sebentar ya",
        "makasih ya",
        "apa ya?",
        "lanjutkan musiknya",
        "skip aja ya",
        "aduh, ntar dulu ya cyrene",
        "ya udah, lampunya aja",
        # A question is never a yes, and doubt is no yes either.
        "iya?",
        "oke？",
        "kurang yakin",
        "yakin nih?",
        "not sure",
    ],
)
def test_is_yes_hears_nothing_but_a_plain_yes(text):
    assert not is_yes(text, NAMES)


@pytest.mark.parametrize(
    ("device_id", "command", "value", "expected"),
    [
        ("komputer", "shutdown", None, True),
        ("komputer", "restart", None, True),
        ("komputer", "sleep", None, True),
        ("komputer", "shutdown", 30, True),
        ("komputer", "wake", None, False),
        ("komputer", "cancel_shutdown", None, False),
        ("colokan_pc", "off", None, True),
        ("colokan_pc", "toggle", None, True),
        ("colokan_pc", "on", None, False),
        ("colokan_kipas", "off", None, False),
        ("lampu", "off", None, False),
        ("komputer", "matikan", None, False),
        ("kulkas", "off", None, False),
    ],
)
def test_needs_confirmation_covers_turning_the_pc_off_and_cutting_its_power(
    device_id, command, value, expected
):
    assert needs_confirmation(house(), device_id, command, value) is expected


def test_needs_confirmation_skips_a_command_the_pc_does_not_offer():
    remote_pc = DeviceRegistry([MockDevice("komputer", "Komputer", commands=["wake"], kind="pc")])

    assert not needs_confirmation(remote_pc, "komputer", "shutdown", None)


def test_needs_confirmation_judges_the_command_the_device_resolves():
    registry = DeviceRegistry(
        [MockDevice("colokan_pc", "Colokan PC", commands=["on", "off", "off_1"], kind="plug")],
        pc_power_ids={"colokan_pc"},
    )

    assert not needs_confirmation(registry, "colokan_pc", "off", 1)
    assert needs_confirmation(registry, "colokan_pc", "off", 2)


def test_first_request_is_refused_with_a_message_for_the_model():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("Cyrene, matikan komputer.")

    message = gate.check(registry, "komputer", "shutdown", None)

    assert message is not None
    assert "belum dijalankan" in message
    assert "'shutdown'" in message
    assert SHUTDOWN_QUESTION in message
    assert "Jangan menanyakannya lagi" in message
    assert "menjawab ya" in message
    assert registry.get("komputer").calls == []


@pytest.mark.parametrize(
    ("device_id", "command", "question"),
    [
        ("komputer", "shutdown", SHUTDOWN_QUESTION),
        (
            "komputer",
            "restart",
            "Komputer mau kunyalakan ulang sekarang, Master? Jawab iya kalau jadi.",
        ),
        ("komputer", "sleep", "Komputer mau kutidurkan sekarang, Master? Jawab iya kalau jadi."),
        ("colokan_pc", "off", PLUG_QUESTION),
        (
            "colokan_pc",
            "toggle",
            "Colokan PC mau kubalik sekarang, Master? Listrik PC bisa langsung putus. "
            "Jawab iya kalau jadi.",
        ),
    ],
)
def test_the_reply_ends_with_the_codes_own_question_naming_the_action(device_id, command, question):
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("Cyrene, matikan semua di meja.")
    gate.check(registry, device_id, command, None)

    assert gate.with_question("Lampu meja sudah mati, Master.") == (
        f"Lampu meja sudah mati, Master. {question}"
    )


def test_the_question_is_the_whole_reply_when_the_model_said_nothing():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan komputer")
    gate.check(registry, "komputer", "shutdown", None)

    assert gate.with_question("") == SHUTDOWN_QUESTION


def test_a_reply_without_a_request_this_turn_gets_no_question():
    gate, registry = refused_then_answered("hmm")

    assert gate.with_question("Baik, Master.") == "Baik, Master."
    assert gate.check(registry, "lampu", "off", None) is None
    assert gate.with_question("Lampunya mati.") == "Lampunya mati."


def test_a_discarded_turn_asks_nothing_and_leaves_nothing_to_confirm():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan komputer")
    gate.check(registry, "komputer", "shutdown", None)

    gate.discard_turn()
    gate.begin_turn("iya")

    assert gate.with_question("Maaf Master.") == "Maaf Master."
    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_a_forgotten_question_is_not_answered_by_the_yes_of_its_turn():
    # A new session never saw the question, so the yes said to it must run nothing.
    gate, registry = refused_then_answered("iya")
    assert gate.awaiting_answer()

    gate.forget_question()

    assert not gate.awaiting_answer()
    assert gate.check(registry, "komputer", "shutdown", None) is not None
    assert gate.with_question("") == SHUTDOWN_QUESTION


def test_the_gate_hears_master_by_the_names_it_was_given():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock(), names=NAMES)
    gate.begin_turn("matikan komputer")
    gate.check(registry, "komputer", "shutdown", None)
    gate.begin_turn("Cyrene, iya.")

    assert gate.check(registry, "komputer", "shutdown", None) is None


def test_the_question_calls_the_owner_what_she_calls_them():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock(), owner_call="Sayang")
    gate.begin_turn("matikan komputer")

    message = gate.check(registry, "komputer", "shutdown", None)

    question = "Komputer mau kumatikan sekarang, Sayang? Jawab iya kalau jadi."
    assert gate.with_question("") == question
    assert question in message


def test_iya_sayang_answers_the_gate_of_an_owner_she_calls_sayang():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock(), names=NAMES, owner_call="Sayang")
    gate.begin_turn("Cyrene, matikan komputer.")
    gate.check(registry, "komputer", "shutdown", None)
    gate.begin_turn("Iya, Sayang.")

    assert gate.check(registry, "komputer", "shutdown", None) is None


def test_yes_in_the_next_turn_runs_the_request_once():
    gate, registry = refused_then_answered("Iya, matikan.")

    assert gate.check(registry, "komputer", "shutdown", None) is None
    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_yes_in_the_same_turn_as_the_request_never_counts():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("iya, matikan komputer sekarang")

    assert gate.check(registry, "komputer", "shutdown", None) is not None
    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_yes_more_than_one_turn_later_never_counts():
    gate, registry = refused_then_answered("hmm, sebentar")
    gate.begin_turn("iya")

    assert gate.check(registry, "komputer", "shutdown", None) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "jangan",
        "eh jangan, tunggu",
        "nanti aja",
        "lampunya dulu",
        "oke makasih ya",
        "bentar ya, aku save dulu",
        "iya ntar",
        "ya kali, aku lagi main game",
    ],
)
def test_an_answer_that_is_not_a_yes_refuses_again(answer):
    gate, registry = refused_then_answered(answer)

    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_bentar_ya_keeps_the_plug_that_feeds_the_pc_on():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock(), names=NAMES)
    gate.begin_turn("Cyrene, matikan colokan PC")
    assert gate.check(registry, "colokan_pc", "off", None) is not None

    gate.begin_turn("bentar ya, aku save dulu")

    assert gate.check(registry, "colokan_pc", "off", None) is not None


def test_yes_counts_when_master_says_it_within_window_s_seconds_of_the_request():
    gate, registry = refused_then_answered("iya", seconds_later=60)

    assert gate.check(registry, "komputer", "shutdown", None) is None


def test_yes_said_after_window_s_seconds_is_stale():
    gate, registry = refused_then_answered("iya", seconds_later=60.001)

    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_the_window_ends_when_masters_words_arrive_not_when_the_model_calls_again():
    clock = FakeClock()
    gate, registry = refused_then_answered("iya", clock, seconds_later=55)

    clock.now += 20  # the model takes its time before it calls again

    assert gate.check(registry, "komputer", "shutdown", None) is None


def test_window_s_is_configurable():
    clock = FakeClock()
    registry = house()
    gate = ConfirmationGate(clock=clock, window_s=10)
    gate.begin_turn("matikan komputer")
    gate.check(registry, "komputer", "shutdown", None)
    clock.now += 11
    gate.begin_turn("iya")

    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_awaiting_answer_only_in_the_turn_after_the_question_and_within_the_window():
    clock = FakeClock()
    registry = house()
    gate = ConfirmationGate(clock=clock)
    gate.begin_turn("matikan komputer")
    before = gate.awaiting_answer()
    gate.check(registry, "komputer", "shutdown", None)
    same_turn = gate.awaiting_answer()

    gate.begin_turn("tidak")
    next_turn = gate.awaiting_answer()
    gate.begin_turn("hmm")
    turn_after = gate.awaiting_answer()

    assert (before, same_turn, next_turn, turn_after) == (False, False, True, False)


def test_awaiting_answer_ends_with_the_window():
    gate, _ = refused_then_answered("iya", seconds_later=60.001)

    assert not gate.awaiting_answer()


@pytest.mark.parametrize(
    ("device_id", "command", "value"),
    [
        ("komputer", "restart", None),
        ("komputer", "shutdown", 30),
        ("colokan_pc", "off", None),
    ],
)
def test_yes_covers_only_the_request_it_answers(device_id, command, value):
    gate, registry = refused_then_answered("iya")

    assert gate.check(registry, device_id, command, value) is not None


def test_only_the_first_request_of_a_turn_is_asked_and_the_others_are_told_to_wait():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan komputer dan colokannya")

    first = gate.check(registry, "komputer", "shutdown", None)
    second = gate.check(registry, "colokan_pc", "off", None)

    assert SHUTDOWN_QUESTION in first
    assert "belum dijalankan" in second
    assert "satu perintah" in second
    assert "Colokan PC mau" not in second
    assert gate.with_question("") == SHUTDOWN_QUESTION


def test_the_same_call_again_in_its_turn_keeps_the_same_question():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan komputer")
    gate.check(registry, "komputer", "shutdown", None)

    again = gate.check(registry, "komputer", "shutdown", None)

    assert SHUTDOWN_QUESTION in again
    assert gate.with_question("") == SHUTDOWN_QUESTION


@pytest.mark.parametrize(
    "order", [["shutdown", "off"], ["off", "shutdown"]], ids=["same_order", "reversed"]
)
def test_two_requests_in_one_turn_are_confirmed_one_yes_at_a_time(order):
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    calls = {"shutdown": ("komputer", "shutdown"), "off": ("colokan_pc", "off")}
    gate.begin_turn("matikan komputer dan colokannya")
    for name in ["shutdown", "off"]:
        gate.check(registry, *calls[name], None)
    gate.begin_turn("iya")

    results = {name: gate.check(registry, *calls[name], None) for name in order}

    assert results["shutdown"] is None
    assert results["off"] is not None
    assert gate.with_question("Komputer mati dalam 30 detik.") == (
        f"Komputer mati dalam 30 detik. {PLUG_QUESTION}"
    )
    gate.begin_turn("iya")
    assert gate.check(registry, "colokan_pc", "off", None) is None


def test_a_request_refused_in_the_answer_turn_waits_for_the_next_yes():
    gate, registry = refused_then_answered("iya")
    assert gate.check(registry, "komputer", "restart", None) is not None

    gate.begin_turn("iya, restart")

    assert gate.check(registry, "komputer", "restart", None) is None


def test_the_plug_that_feeds_the_pc_is_confirmed_like_the_pc():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan colokan pc")
    assert gate.check(registry, "colokan_pc", "off", None) is not None

    gate.begin_turn("boleh")

    assert gate.check(registry, "colokan_pc", "off", None) is None


def test_commands_that_need_no_confirmation_run_and_leave_the_request_alone():
    gate, registry = refused_then_answered("iya")

    assert gate.check(registry, "lampu", "off", None) is None
    assert gate.check(registry, "komputer", "wake", None) is None
    assert gate.check(registry, "colokan_pc", "on", None) is None
    assert gate.check(registry, "komputer", "shutdown", None) is None


def test_commands_that_need_no_confirmation_run_before_any_turn():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())

    assert gate.check(registry, "lampu", "off", None) is None
    assert gate.check(registry, "kulkas", "off", None) is None


def test_concurrent_calls_use_up_a_confirmed_request_once():
    gate, registry = refused_then_answered("iya", SlowClock())
    start = threading.Barrier(8)
    results: list[str | None] = []

    def confirm() -> None:
        start.wait()
        results.append(gate.check(registry, "komputer", "shutdown", None))

    threads = [threading.Thread(target=confirm) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(None) == 1


def test_the_device_messages_stay_byte_for_byte_as_in_v1_9():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan komputer dan colokannya")

    first = gate.check(registry, "komputer", "shutdown", None)
    second = gate.check(registry, "colokan_pc", "off", None)

    assert first == (
        "Perintah 'shutdown' untuk Komputer belum dijalankan: Master harus mengiyakannya dulu. "
        f'Setelah jawabanmu, pertanyaan ini otomatis dibacakan untuk Master: "{SHUTDOWN_QUESTION}" '
        "Jangan menanyakannya lagi. Panggil perintah yang sama lagi hanya setelah Master menjawab "
        "ya."
    )
    assert second == (
        "Perintah 'off' untuk Colokan PC belum dijalankan dan tidak ditanyakan: hanya satu "
        "perintah seperti ini yang ditanyakan ke Master dalam satu giliran. Bilang ke Master "
        "bahwa yang ini perlu dia minta lagi nanti."
    )


# Any action, such as recording an expense (v1.24)

RECORD_KEY = ("record_expense", 18000, "jajan kopi", None, "gopay")
RECORD_LABEL = "Pencatatan pengeluaran 18000 rupiah untuk jajan kopi di GoPay"
RECORD_QUESTION = (
    "Catat delapan belas ribu untuk jajan kopi di GoPay, Master? Jawab iya kalau jadi."
)


def check_record(gate: ConfirmationGate, key: tuple = RECORD_KEY) -> str | None:
    return gate.check_action(
        key, label=RECORD_LABEL, question=RECORD_QUESTION, order_words=RECORD_ORDER_WORDS
    )


def record_asked_then_answered(answer: str, clock: FakeClock | None = None) -> ConfirmationGate:
    """A gate that held recording an expense last turn, now in the turn Master says `answer`."""
    gate = ConfirmationGate(clock=clock or FakeClock(), names=NAMES)
    gate.begin_turn("Cyrene, catat jajan kopi 18 ribu.")
    assert check_record(gate) is not None
    gate.begin_turn(answer)
    return gate


@pytest.mark.parametrize("text", ["iya, catat aja", "Iya, catat.", "oke catetin", "boleh, simpan"])
def test_the_record_order_words_hear_a_yes_to_recording(text):
    assert is_yes(text, NAMES, RECORD_ORDER_WORDS)
    assert not is_yes(text, NAMES)


def test_the_record_order_words_never_take_a_pc_order_word():
    assert is_yes("iya matikan", NAMES)
    assert not is_yes("iya matikan", NAMES, RECORD_ORDER_WORDS)
    assert not is_yes("iya, catat nanti", NAMES, RECORD_ORDER_WORDS)


def test_the_pc_order_words_are_unchanged_by_the_record_words():
    added = RECORD_ORDER_WORDS - ORDER_WORDS

    assert added == {
        "catat",
        "catatkan",
        "catet",
        "catetin",
        "dicatat",
        "simpan",
    }
    assert "catat" not in ORDER_WORDS


def test_the_first_action_is_held_with_its_label_and_question():
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("catat jajan kopi 18 ribu")

    message = check_record(gate)

    assert message == (
        f"{RECORD_LABEL} belum dijalankan: Master harus mengiyakannya dulu. Setelah jawabanmu, "
        f'pertanyaan ini otomatis dibacakan untuk Master: "{RECORD_QUESTION}" Jangan '
        "menanyakannya lagi. Panggil perintah yang sama lagi hanya setelah Master menjawab ya."
    )
    assert gate.with_question("Baik, Master.") == f"Baik, Master. {RECORD_QUESTION}"


def test_an_action_runs_once_on_the_next_turns_yes_by_its_own_order_words():
    gate = record_asked_then_answered("Iya, catat aja.")

    assert check_record(gate) is None
    assert check_record(gate) is not None


def test_a_pc_order_word_does_not_answer_a_recording_question():
    gate = record_asked_then_answered("iya, matikan")

    assert check_record(gate) is not None


def test_a_record_order_word_does_not_answer_the_pcs_question():
    gate, registry = refused_then_answered("iya, catat")

    assert gate.check(registry, "komputer", "shutdown", None) is not None


@pytest.mark.parametrize(
    "key",
    [
        ("record_expense", 80000, "jajan kopi", None, "gopay"),
        ("record_expense", 18000, "jajan kopi", None, "ovo"),
        ("record_expense", 18000, "jajan kopi", "Nongkrong / jajan", "gopay"),
    ],
    ids=["another_amount", "another_wallet", "another_category"],
)
def test_a_different_action_on_the_answer_turn_is_a_new_request(key):
    gate = record_asked_then_answered("iya")

    message = gate.check_action(
        key, label="Pencatatan lain", question="Catat yang lain?", order_words=RECORD_ORDER_WORDS
    )

    assert message is not None
    assert '"Catat yang lain?"' in message
    assert gate.with_question("") == "Catat yang lain?"
    gate.begin_turn("iya")
    assert check_record(gate) is not None


def test_a_yes_in_the_turn_of_the_action_never_counts():
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("iya, catat jajan kopi 18 ribu")

    assert check_record(gate) is not None
    assert check_record(gate) is not None


def test_a_yes_more_than_one_turn_after_the_action_never_counts():
    gate = record_asked_then_answered("hmm")
    gate.begin_turn("iya")

    assert check_record(gate) is not None


def test_a_yes_to_an_action_after_window_s_is_stale():
    clock = FakeClock()
    gate = ConfirmationGate(clock=clock)
    gate.begin_turn("catat jajan kopi 18 ribu")
    check_record(gate)
    clock.now += 60.001
    gate.begin_turn("iya")

    assert check_record(gate) is not None


def test_one_question_per_turn_covers_the_pc_and_an_action_together():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("matikan komputer dan catat jajan kopi 18 ribu")

    pc = gate.check(registry, "komputer", "shutdown", None)
    record = check_record(gate)

    assert SHUTDOWN_QUESTION in pc
    assert record == (
        f"{RECORD_LABEL} belum dijalankan dan tidak ditanyakan: hanya satu perintah seperti ini "
        "yang ditanyakan ke Master dalam satu giliran. Bilang ke Master bahwa yang ini perlu dia "
        "minta lagi nanti."
    )
    assert gate.with_question("") == SHUTDOWN_QUESTION


def test_an_action_asked_first_leaves_the_pc_for_another_turn():
    registry = house()
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("catat jajan kopi 18 ribu lalu matikan komputer")

    check_record(gate)
    pc = gate.check(registry, "komputer", "shutdown", None)

    assert pc.startswith("Perintah 'shutdown' untuk Komputer belum dijalankan dan tidak ditanyakan")
    assert gate.with_question("") == RECORD_QUESTION
    gate.begin_turn("iya")
    assert gate.check(registry, "komputer", "shutdown", None) is not None


def test_a_held_action_keeps_the_question_it_was_asked_with():
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("catat jajan kopi 18 ribu")
    check_record(gate)

    again = gate.check_action(
        RECORD_KEY, label=RECORD_LABEL, question="Pertanyaan lain?", order_words=RECORD_ORDER_WORDS
    )

    assert f'"{RECORD_QUESTION}"' in again
    assert gate.with_question("") == RECORD_QUESTION


def test_a_discarded_action_is_never_confirmed():
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("catat jajan kopi 18 ribu")
    check_record(gate)

    gate.discard_turn()
    gate.begin_turn("iya")

    assert check_record(gate) is not None


def test_a_forgotten_action_is_not_confirmed_by_the_yes_of_its_turn():
    gate = record_asked_then_answered("iya")
    assert gate.awaiting_answer()

    gate.forget_question()

    assert check_record(gate) is not None


def test_an_action_without_order_words_is_answered_by_the_pcs_list():
    gate = ConfirmationGate(clock=FakeClock())
    gate.begin_turn("lakukan itu")
    assert gate.check_action("aksi", label="Aksi", question="Jadi?") is not None

    gate.begin_turn("iya sekarang")

    assert gate.check_action("aksi", label="Aksi", question="Jadi?") is None


def test_concurrent_checks_use_up_a_confirmed_action_once():
    gate = record_asked_then_answered("iya", SlowClock())
    start = threading.Barrier(8)
    results: list[str | None] = []

    def confirm() -> None:
        start.wait()
        results.append(check_record(gate))

    threads = [threading.Thread(target=confirm) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(None) == 1
