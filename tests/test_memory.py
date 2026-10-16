import dataclasses
import json
import logging
import threading
from datetime import datetime, timedelta

import pytest

from waifu import memory
from waifu.memory import Fact, MemoryStore, looks_secret, memory_prompt_section

START = datetime(2026, 10, 12, 14, 30, 45, 123456)
CORRUPT_NAME = "memory.json.corrupt-20261012-143045"
FOOTER = (
    "Pakai ingatan ini secara alami kalau relevan; jangan membacakan daftarnya kecuali "
    "Master memintanya. Simpan hal baru yang penting dengan tool remember, perbarui catatan "
    "yang berubah dengan replaces, dan hapus dengan forget kalau Master memintanya."
)


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def make_store(tmp_path, clock=None, **kwargs):
    return MemoryStore(tmp_path / "memory.json", now=clock or Clock(), **kwargs)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_memory(path, facts, next_id):
    path.write_text(
        json.dumps({"version": 1, "next_id": next_id, "facts": facts}), encoding="utf-8"
    )


def note(fact_id, text, created="2026-10-01T08:00", updated=None):
    return {"id": fact_id, "text": text, "created": created, "updated": updated or created}


# --- reading -------------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_memory_and_reading_creates_nothing(tmp_path):
    path = tmp_path / "data" / "memory.json"
    store = MemoryStore(path, now=Clock())

    assert store.facts() == []
    assert not path.parent.exists()


def test_the_store_exposes_its_path_and_limits(tmp_path):
    store = make_store(tmp_path, max_facts=7, max_chars=150)

    assert store.path == tmp_path / "memory.json"
    assert (store.max_facts, store.max_chars) == (7, 150)


def test_the_limits_default_to_the_spec_values(tmp_path):
    store = MemoryStore(tmp_path / "memory.json")

    assert (store.max_facts, store.max_chars) == (100, 200)


def test_every_call_rereads_a_file_edited_by_hand(tmp_path):
    store = make_store(tmp_path)
    write_memory(store.path, [note("m1", "Master suka kopi.")], next_id=2)
    assert [fact.text for fact in store.facts()] == ["Master suka kopi."]

    write_memory(store.path, [note("m1", "Master suka teh.")], next_id=2)

    assert [fact.text for fact in store.facts()] == ["Master suka teh."]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    # Notepad and some editors put a UTF-8 BOM in front of a file they save.
    store = make_store(tmp_path)
    content = json.dumps({"version": 1, "next_id": 2, "facts": [note("m1", "Kucing: Mochi.")]})
    store.path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    assert [fact.text for fact in store.facts()] == ["Kucing: Mochi."]


def test_facts_are_read_back_as_facts_with_datetimes(tmp_path):
    store = make_store(tmp_path)
    write_memory(
        store.path,
        [note("m4", "Master alergi udang.", "2026-10-01T08:00", "2026-10-12T21:05")],
        next_id=5,
    )

    assert store.facts() == [
        Fact(
            id="m4",
            text="Master alergi udang.",
            created=datetime(2026, 10, 1, 8, 0),
            updated=datetime(2026, 10, 12, 21, 5),
        )
    ]


def test_a_fact_is_frozen():
    fact = Fact("m1", "Master suka kopi.", START, START)

    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.text = "Master suka teh."  # type: ignore[misc]


# --- add -------------------------------------------------------------------------------------


def test_add_returns_the_first_note_with_times_at_minute_precision(tmp_path):
    store = make_store(tmp_path)

    fact = store.add("Master alergi udang.")

    minute = datetime(2026, 10, 12, 14, 30)
    assert fact == Fact("m1", "Master alergi udang.", minute, minute)


def test_add_writes_the_spec_file_format_in_readable_utf8(tmp_path):
    store = make_store(tmp_path)

    store.add("Master suka lagu 夜に駆ける.")

    text = store.path.read_text(encoding="utf-8")
    assert "夜に駆ける" in text
    assert json.loads(text) == {
        "version": 1,
        "next_id": 2,
        "facts": [
            {
                "id": "m1",
                "text": "Master suka lagu 夜に駆ける.",
                "created": "2026-10-12T14:30",
                "updated": "2026-10-12T14:30",
            }
        ],
    }


def test_add_creates_the_parent_folder_on_the_first_write(tmp_path):
    path = tmp_path / "data" / "nested" / "memory.json"

    MemoryStore(path, now=Clock()).add("Master suka kopi.")

    assert path.is_file()


def test_ids_count_up_and_notes_are_listed_oldest_first(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    for text in ["Master suka kopi.", "Master main Genshin.", "Kucing Master namanya Mochi."]:
        store.add(text)
        clock.advance(minutes=5)

    assert [(fact.id, fact.text) for fact in store.facts()] == [
        ("m1", "Master suka kopi."),
        ("m2", "Master main Genshin."),
        ("m3", "Kucing Master namanya Mochi."),
    ]


def test_text_is_stripped_and_its_whitespace_collapsed(tmp_path):
    store = make_store(tmp_path)

    fact = store.add("  Master\n suka\t\tkopi　tanpa gula.\r\n ")

    assert fact.text == "Master suka kopi tanpa gula."
    assert store.facts()[0].text == "Master suka kopi tanpa gula."


def test_adding_a_text_already_saved_returns_that_note_unchanged(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    first = store.add("Master suka kopi.")
    saved = store.path.read_bytes()
    clock.advance(days=2)

    again = store.add("  master   SUKA kopi. ")

    assert again == first
    assert store.path.read_bytes() == saved


def test_a_note_already_saved_is_returned_even_when_the_memory_is_full(tmp_path):
    store = make_store(tmp_path, max_facts=1)
    first = store.add("Master suka kopi.")

    assert store.add("Master suka kopi.") == first


@pytest.mark.parametrize("text", ["", "   ", "\n\t 　"])
def test_empty_text_is_refused(tmp_path, text):
    store = make_store(tmp_path)

    with pytest.raises(memory.MemoryError, match="kosong"):
        store.add(text)

    assert not store.path.exists()


def test_text_up_to_max_chars_is_saved(tmp_path):
    store = make_store(tmp_path, max_chars=20)

    assert store.add("  " + "a" * 20 + "  ").text == "a" * 20


def test_text_longer_than_max_chars_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path, max_chars=20)
    text = "Master suka kopi tanpa gula."

    with pytest.raises(memory.MemoryError, match="20 karakter") as caught:
        store.add(text)

    assert text not in str(caught.value)
    assert not store.path.exists()


def test_secret_text_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(memory.MemoryError, match="rahasia") as caught:
        store.add("password wifi Master: kucing123")

    assert "kucing123" not in str(caught.value)
    assert not store.path.exists()


def test_add_is_refused_when_max_facts_notes_exist_and_says_what_to_do(tmp_path):
    store = make_store(tmp_path, max_facts=2)
    store.add("Master suka kopi.")
    store.add("Master main Genshin.")

    with pytest.raises(memory.MemoryError, match="penuh") as caught:
        store.add("Master kerja di Jakarta.")

    message = str(caught.value)
    assert "Gabungkan" in message
    assert "hapus" in message
    assert len(store.facts()) == 2


def test_memory_error_is_an_exception_of_its_own():
    assert issubclass(memory.MemoryError, Exception)
    assert memory.MemoryError is not MemoryError


# --- replace ---------------------------------------------------------------------------------


def test_replace_keeps_the_id_and_created_and_sets_the_text_and_updated(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    old = store.add("Master kerja di Bandung.")
    clock.advance(days=3)

    new = store.replace(old.id, " Master   kerja di Jakarta sejak 2026. ")

    assert new == Fact(
        "m1", "Master kerja di Jakarta sejak 2026.", old.created, datetime(2026, 10, 15, 14, 30)
    )
    assert store.facts() == [new]


def test_replace_keeps_the_note_in_its_place(tmp_path):
    store = make_store(tmp_path)
    for text in ["Master suka kopi.", "Master main Genshin.", "Master punya kucing."]:
        store.add(text)

    store.replace("m2", "Master main Genshin dan HSR.")

    assert [fact.id for fact in store.facts()] == ["m1", "m2", "m3"]
    assert read_json(store.path)["next_id"] == 4


def test_replace_may_change_only_the_case_of_its_own_text(tmp_path):
    store = make_store(tmp_path)
    store.add("master suka kopi.")

    assert store.replace("m1", "Master suka kopi.").text == "Master suka kopi."


def test_replace_of_an_unknown_id_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")

    with pytest.raises(memory.MemoryError, match="Tidak ada catatan"):
        store.replace("m9", "Master suka teh.")

    assert [fact.text for fact in store.facts()] == ["Master suka kopi."]


@pytest.mark.parametrize(
    ("text", "match"),
    [("  ", "kosong"), ("x" * 21, "20 karakter"), ("PIN ATM 4321", "rahasia")],
)
def test_replace_checks_the_text_like_add(tmp_path, text, match):
    store = make_store(tmp_path, max_chars=20)
    store.add("Master suka kopi.")

    with pytest.raises(memory.MemoryError, match=match):
        store.replace("m1", text)

    assert [fact.text for fact in store.facts()] == ["Master suka kopi."]


def test_replace_with_the_text_of_another_note_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    store.add("Master suka teh.")
    saved = store.path.read_bytes()

    with pytest.raises(memory.MemoryError, match="m1"):
        store.replace("m2", "MASTER SUKA KOPI.")

    assert store.path.read_bytes() == saved


# --- forget and clear ----------------------------------------------------------------------


def test_forget_removes_the_note_and_returns_it(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    shrimp = store.add("Master alergi udang.")

    assert store.forget("m2") == shrimp
    assert [fact.id for fact in store.facts()] == ["m1"]


def test_forget_of_an_unknown_id_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    store.forget("m1")

    with pytest.raises(memory.MemoryError, match="Tidak ada catatan"):
        store.forget("m1")


def test_a_forgotten_id_is_never_reused(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    store.add("Master suka teh.")
    store.forget("m2")

    assert store.add("Master suka susu.").id == "m3"


def test_clear_removes_every_note_returns_the_count_and_never_reuses_ids(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    store.add("Master suka teh.")

    assert store.clear() == 2
    assert store.facts() == []
    assert store.add("Master suka susu.").id == "m3"


def test_clear_of_an_empty_memory_writes_nothing(tmp_path):
    store = make_store(tmp_path)

    assert store.clear() == 0
    assert not store.path.exists()


def test_an_id_added_by_hand_beyond_next_id_is_never_reused(tmp_path):
    store = make_store(tmp_path)
    write_memory(store.path, [note("m1", "Master suka kopi."), note("m7", "Master suka teh.")], 3)

    assert store.add("Master suka susu.").id == "m8"


# --- several stores and threads --------------------------------------------------------------


def test_a_second_store_on_the_same_file_sees_changes_at_once(tmp_path):
    cyrene = make_store(tmp_path)
    terminal = make_store(tmp_path)

    cyrene.add("Master suka kopi.")
    assert [fact.text for fact in terminal.facts()] == ["Master suka kopi."]

    terminal.forget("m1")
    assert cyrene.facts() == []
    assert cyrene.add("Master suka teh.").id == "m2"


def test_concurrent_adds_from_threads_keep_every_note(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]
    errors = []

    def add_notes(worker):
        try:
            for index in range(5):
                stores[worker % 2].add(f"Catatan {worker}-{index}.")
        except Exception as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=add_notes, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    facts = stores[0].facts()
    assert sorted(fact.id for fact in facts) == sorted(f"m{n}" for n in range(1, 41))
    assert len({fact.text for fact in facts}) == 40
    assert read_json(stores[0].path)["next_id"] == 41


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    store = make_store(tmp_path)

    store.add("Master suka kopi.")

    assert [path.name for path in tmp_path.iterdir()] == ["memory.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    saved = store.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(memory.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.add("Master suka teh.")

    assert store.path.read_bytes() == saved
    assert [path.name for path in tmp_path.iterdir()] == ["memory.json"]


def test_new_notes_reach_the_disk_before_the_file_is_replaced(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    events = []
    real_fsync, real_replace = memory.os.fsync, memory.os.replace

    def recording_fsync(fd):
        real_fsync(fd)
        (temp_file,) = [path for path in tmp_path.iterdir() if path.name != "memory.json"]
        events.append(("fsync", [fact["id"] for fact in read_json(temp_file)["facts"]]))

    def recording_replace(src, dst):
        events.append(("replace", None))
        real_replace(src, dst)

    monkeypatch.setattr(memory.os, "fsync", recording_fsync)
    monkeypatch.setattr(memory.os, "replace", recording_replace)

    store.add("Master suka teh.")

    assert events == [("fsync", ["m1", "m2"]), ("replace", None)]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "ingatan saya" / "memori Master.json"
    store = MemoryStore(path, now=Clock())

    store.add("Master suka kopi.")

    assert [fact.text for fact in MemoryStore(path).facts()] == ["Master suka kopi."]


# --- corrupt files -----------------------------------------------------------------------------

VALID_NOTE = note("m1", "Master suka kopi.")

CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "next_id": 2, "facts": [\xff\xfe]}',
    "a list": b"[]",
    "missing facts": json.dumps({"version": 1, "next_id": 2}).encode(),
    "an extra key": json.dumps({"version": 1, "next_id": 2, "facts": [], "notes": []}).encode(),
    "another version": json.dumps({"version": 2, "next_id": 2, "facts": []}).encode(),
    "version true": json.dumps({"version": True, "next_id": 2, "facts": []}).encode(),
    "next_id text": json.dumps({"version": 1, "next_id": "2", "facts": []}).encode(),
    "next_id true": json.dumps({"version": 1, "next_id": True, "facts": []}).encode(),
    "next_id zero": json.dumps({"version": 1, "next_id": 0, "facts": []}).encode(),
    "facts an object": json.dumps({"version": 1, "next_id": 2, "facts": {}}).encode(),
    "fact a string": json.dumps({"version": 1, "next_id": 2, "facts": ["kopi"]}).encode(),
    "fact without updated": json.dumps(
        {
            "version": 1,
            "next_id": 2,
            "facts": [{k: v for k, v in VALID_NOTE.items() if k != "updated"}],
        }
    ).encode(),
    "fact with an extra key": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"tag": "makanan"}]}
    ).encode(),
    "numeric id": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"id": 1}]}
    ).encode(),
    "id without m": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"id": "x1"}]}
    ).encode(),
    "id m0": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"id": "m0"}]}
    ).encode(),
    "id with a newline": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"id": "m1\n"}]}
    ).encode(),
    "numeric text": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"text": 42}]}
    ).encode(),
    "null text": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"text": None}]}
    ).encode(),
    "created not a time": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"created": "kemarin"}]}
    ).encode(),
    "updated a number": json.dumps(
        {"version": 1, "next_id": 2, "facts": [VALID_NOTE | {"updated": 20261012}]}
    ).encode(),
    "duplicate ids": json.dumps(
        {"version": 1, "next_id": 3, "facts": [VALID_NOTE, VALID_NOTE | {"text": "Teh."}]}
    ).encode(),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_memory_starts_empty(
    tmp_path, caplog, content
):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.memory"):
        assert store.facts() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_corrupt_file_is_never_overwritten_by_a_later_note(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    fact = store.add("Master suka kopi.")

    assert fact.id == "m1"
    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert [f.text for f in store.facts()] == ["Master suka kopi."]


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    store.path.write_bytes(b"third")

    assert store.facts() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(memory.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        store.add("Master suka kopi.")

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["memory.json"]


# --- looks_secret ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "password wifi Master: kucing123",
        "PASSWORD laptop Master: mochi",
        "Kata sandi email Master: rahasia",
        "Sandi wifi rumah: kucing",
        "Sandi HP Master 1234",
        "Passcode iPhone Master 0000",
        "pw email: rahasia123",
        "PWD laptop Master mochi123",
        "passwd server: kucing",
        "PIN ATM 4321",
        "PIN-nya 4321",
        "Kode OTP dari bank 123456",
        "CVV kartu Master 123",
        "API key OpenAI milik Master: sk-proj-Ab12",
        "Master punya apikey baru 9f8e7d6c5b",
        "my_api_key=Ab12Cd34",
        "Token GitHub Master ghp_Ab12Cd34",
        "Cookie HoYoLAB Master: ltuid_v2=12345678; ltoken_v2=v2_FAKEtokenForTests1234567890",
        "Seed phrase dompet kripto Master: abandon ability able about",
        "ltoken_v2=AbCdEf1234567890XyZq",
        "kuncinya sk-ant-api03-AbCdEfGh1234 ya",
        "4111 1111 1111 1111",
        "Kartu kredit Master 4111-1111-1111-1111, kedaluwarsa 2028",
        "4111111111111111",
        "5500 0000 0000 0004",
        "Amex Master 378282246310005",
    ],
)
def test_looks_secret_catches_secrets(text):
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "PIN ATM 1234",
        "password wifi: hijau123",
        "passwordnya hijau123",
        "token PLN 1234 5678 9012 3456 7890",
        "Token listrik Master 12345678901234567890",
        "kode OTP 482910",
        "Password email Master Xk9#p2",
        "password wifi kucing#lucu",
        "pin=4321",
        "Sandi wifi lantai 2: kucing",
        "PIN-nya ATM BCA Master 4321",
        "PIN ATM BCA milik Master: 123456",
        "password wifi lantai 2 = kucinglucu",
        "Sandi wifi rumah lantai 2: kucing",
        "PIN ATM Mandiri (kartu lama): 123456",
        "Password wifi rumah lantai dua: kucing",
    ],
)
def test_looks_secret_catches_a_secret_word_followed_by_its_value(text):
    # A ':' or '=' with something after it anywhere in the clause; within the four words after
    # the secret word, four or more digits (a 20-digit prepaid electricity token too), or six
    # or more characters mixing letters with digits or symbols.
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Sandi wifi di rumah lantai 2: kucing",
        "Kode OTP login Shopee untuk Master hari ini 482910",
        "PIN ATM BCA milik Master untuk transfer harian 482910",
        "password akun game lama Master yang dulu dipakai Mochi2024#",
    ],
)
def test_looks_secret_catches_a_long_value_later_in_the_same_clause(text):
    # Past the four words, the rest of the clause still counts: a ':' or '=', five or more
    # digits, or a long mix of letters with digits or symbols.
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "ganti PIN ATM, lalu bayar listrik 150000",
        "ganti PIN ATM terus transfer 500000 ke ibu",
        "ingatkan ganti password wifi lalu beli kabel LAN 10 meter",
        "reset password email kantor; tagihan internet 350000",
    ],
)
def test_looks_secret_stops_at_the_end_of_the_clause(text):
    # A number in the next clause ("lalu", "terus", a comma, a semicolon) is not the secret's.
    assert not looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "PIN ATM BCA milik Master 123456",
        "PIN ATM BCA Master adalah 123456",
        "PIN kartu ATM BRI Master 123456",
        "PIN m-banking BCA milik Master 123456",
        "PIN ATM BCA Master yang baru 123456",
        "Kode OTP Shopee Master tadi pagi adalah 482910",
        "CVV kartu kredit BCA Master 123",
        "Password WiFi rumah Master adalah kucing123",
        "Kata sandi laptop kantor Master adalah Mochi2024",
        "password akun Steam Master adalah Mochi2024",
        "PIN kartu ATM BRI punya Master yaitu 123456",
    ],
)
def test_looks_secret_does_not_count_the_words_that_tie_a_secret_to_master(text):
    # The model writes about Master: "Master" and "milik", "punya", "adalah", "ialah",
    # "yaitu", "yakni", "yang", "is" lead to the value without being one of its four words.
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "OTP 482 910",
        "OTP 482-910",
        "kode OTP 482 910",
        "OTP 48 29 10",
        "PIN ATM 12 34",
        "PIN 12-34",
        "PIN 1 2 3 4",
        "CVV 1 2 3",
    ],
)
def test_looks_secret_reads_digits_in_groups_as_one_value(text):
    # An OTP is shown and dictated in groups; a single space or dash joins them.
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Token listrik Master 1234-5678-9012-3456-7890",
        "token_listrik 12345678901234567890",
        "token PLN-nya 1234 5678 9012 3456 7890",
    ],
)
def test_looks_secret_catches_a_prepaid_electricity_token(text):
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Rumah Master pakai token listrik 1300 VA",
        "Rumah Master pakai token listrik 2200VA",
        "Nomor meter token listrik Master 14234567890",
        "ID pelanggan token PLN Master 532100123456",
        "beli token PLN untuk meter 14234567890",
        "beli token listrik 100000",
        "beli token listrik Rp100rb",
        "Master biasa beli token listrik Rp100k",
        "isi token PLN Rp1,5jt",
    ],
)
def test_looks_secret_takes_only_a_20_digit_token_as_the_value_of_token_listrik(text):
    # The meter's power, its meter or customer number (11-12 digits), and an amount are what
    # Master needs to buy a token, and no secret: only the 20 digits of the token itself are.
    assert not looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Master ganti PIN ATM, biayanya Rp10rb",
        "Master top up token game Rp100k",
        "beli token game Rp1,5jt",
    ],
)
def test_looks_secret_takes_an_amount_in_rupiah_with_its_unit_for_no_value(text):
    assert not looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "ganti PIN ATM",
        "ingatkan ganti password wifi",
        "beli token listrik",
        "beli token",
        "minta kode OTP ke bank",
        "Master lupa passwordnya",
        "ganti PIN ATM-ku",
        "ganti password e-mail kantor",
        "ganti password wifi/email",
        "Ganti password wifi (rumah).",
        "beli token listrik Rp100.000",
        "Master isi token PLN Rp1.500.000,- tiap bulan",
        "Master ganti PIN ATM tiap awal bulan sejak 2024",
        "API key OpenAI milik Master",
        "Master punya apikey baru",
        "my_api_key ada di catatan",
        "Token GitHub Master",
        "Seed phrase dompet kripto Master",
    ],
)
def test_looks_secret_lets_a_secret_named_without_its_value_through(text):
    # A hyphen or a slash joins two words, punctuation around a word is no symbol, an amount
    # in rupiah is no value, and a year five words further on is not looked at.
    assert not looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "PWD laptop Master mochi",
        "Passwordnya WiFi rumah kucinglucu.",
        "WiFi rumah Master passwordnya kucinglucu",
    ],
)
def test_looks_secret_cannot_tell_a_value_of_plain_letters_from_a_word(text):
    # "mochi" after "PWD laptop Master" reads like "baru" after "password wifi": without a ':'
    # or '=' only digits or symbols give a value away. The remember and add_task descriptions
    # tell the model never to pass a secret at all.
    assert not looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Kata sandinya kucing123.",
        "Sandiku kucing123",
        "PINnya 4321.",
        "Master minta dicatat pinnya ATM 4321",
        "Tokennya ABC123.",
        "Kode OTPnya 123456",
        "Master: PIN1234",
    ],
)
def test_looks_secret_catches_a_secret_word_with_a_suffix_or_a_number_attached(text):
    # Indonesian attaches -nya, -ku, and -mu to the word itself.
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Kartu kredit Master 4111 1111 1111 1111 12/28",
        "Kartu kredit Master 4111-1111-1111-1111 12-28",
        "Kartu kredit Master 4111-1111-1111-1111-12-28",
        "Kartu kredit Master 4111 1111 1111 1111 123",
        "Master: 5500 0000 0000 0004 12/28",
        "Kartu Master 4111111111111111 1228",
        "Kartu ke 2 4111 1111 1111 1111",
    ],
)
def test_looks_secret_catches_a_card_number_next_to_other_digits(text):
    # Dictated with its expiry or CVV, a card number shares a digit run with them.
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    ["4222222222222", "Kartu Diners Master 30569309025904", "Kartu UnionPay 6212345678901234569"],
)
def test_looks_secret_catches_card_numbers_of_13_to_19_digits(text):
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Master ulang tahun 17-08-2003",
        "Master suka kopi tanpa gula",
        "Master main Genshin AR 60",
        "Kucing Master namanya Mochi",
        "Master kerja di Jakarta Selatan sejak 2024",
        "Nomor rumah Master 12",
        "Master suka pinjam buku di perpustakaan",
        "Rumah Master di pinggir kota",
        "Master belajar tokenisasi teks",
        "Master suka pinguin",
        "Master pinter masak",
        "Master sedang mengerjakan pertanggungjawabannya",
        "Master punya kartu 4111 1111 1111 1112",
        "Nomor pelanggan Master 123456789012",
        "Nomor pelanggan Master 123456789015",
        "Nomor meter Master 12345678901234567894",
        "Master suka lagu 夜に駆ける",
    ],
)
def test_looks_secret_lets_ordinary_notes_through(text):
    assert not looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Sahabat Master namanya Sandi",
        "Master belajar sandi Morse di Pramuka",
        "Master isi token listrik tiap awal bulan",
        "Master beli token PLN tiap tanggal 1",
        "Master suka kue cookie cokelat",
        "Master main Cookie Run Kingdom",
        "Master pakai pin Genshin di tas",
        "Master punya pin koleksi Honkai",
        "Master kirim pin lokasi ke temannya",
    ],
)
def test_looks_secret_lets_people_routines_and_likes_named_like_a_secret_through(text):
    # A name, prepaid electricity, a biscuit, a game, a badge, or a map pin is no secret.
    assert not looks_secret(text)


# --- prompt section ----------------------------------------------------------------------------


def test_the_prompt_section_without_notes():
    assert memory_prompt_section([]) == ("Ingatanmu tentang Master: belum ada catatan.\n" + FOOTER)


def test_the_prompt_section_lists_each_note_with_its_updated_date():
    facts = [
        Fact("m1", "Master suka kopi.", datetime(2026, 9, 1, 8, 0), datetime(2026, 9, 1, 8, 0)),
        Fact(
            "m3", "Master alergi udang.", datetime(2026, 9, 2, 9, 0), datetime(2026, 10, 12, 21, 5)
        ),
    ]

    assert memory_prompt_section(facts) == (
        "Ingatanmu tentang Master (catatan dari obrolan sebelumnya, tersimpan di PC ini):\n"
        "- m1 (01-09-2026): Master suka kopi.\n"
        "- m3 (12-10-2026): Master alergi udang.\n" + FOOTER
    )


@pytest.mark.parametrize("facts", [[], [Fact("m1", "Master suka kopi.", START, START)]])
def test_the_prompt_section_has_no_square_brackets(facts):
    section = memory_prompt_section(facts)

    assert "[" not in section
    assert "]" not in section
