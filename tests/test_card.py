import pytest

from waifu.card import (
    MAX_BODY_CHARS,
    MAX_BODY_LINES,
    MAX_TITLE_CHARS,
    MESSAGE_MAX_BYTES,
    Card,
    before_card,
    card_message,
    is_card_request,
    split_card,
)

GIST = "[senang] Ini ramalan seminggu, kutampilkan di layar ya. Intinya hujan Rabu dan Kamis."
FORECAST = "- Senin cerah\n- Selasa berawan\n- Rabu hujan"
NAMES = ("Cyrene", "Sairin", "Master")

# --- split_card --------------------------------------------------------------------------------


def test_a_reply_without_a_marker_comes_back_unchanged_without_a_card():
    text = "  [senang] Lampu kamar sudah menyala, Master.  "

    assert split_card(text) == (text, None)


def test_the_marker_line_splits_the_gist_from_the_card():
    speech, card = split_card(f"{GIST}\n[[kartu: Cuaca 7 hari]]\n{FORECAST}")

    assert speech == GIST
    assert card == Card("Cuaca 7 hari", FORECAST)


@pytest.mark.parametrize(
    "marker",
    [
        "[[kartu: Cuaca 7 hari]]",
        "[[KARTU: Cuaca 7 hari]]",
        "[[ Kartu :Cuaca 7 hari ]]",
        "[[kartu:Cuaca 7 hari]]",
        "[kartu: Cuaca 7 hari]",
        "［［kartu：Cuaca 7 hari］］",
        "[[kartu: Cuaca 7 hari",
        "[[kartu: Cuaca 7 hari]",
        "[[kartu Cuaca 7 hari]]",
        "[kartu Cuaca 7 hari]",
        "  [Kartu Cuaca 7 hari]  ",
    ],
)
def test_every_shape_of_the_marker_is_read(marker):
    speech, card = split_card(f"{GIST}\n{marker}\n{FORECAST}")

    assert speech == GIST
    assert card == Card("Cuaca 7 hari", FORECAST)


@pytest.mark.parametrize("marker", ["[[kartu]]", "[[kartu:]]", "[[ KARTU ]]", "[[kartu"])
def test_a_marker_without_a_title_gives_an_untitled_card(marker):
    speech, card = split_card(f"{GIST}\n{marker}\n{FORECAST}")

    assert speech == GIST
    assert card == Card("", FORECAST)


def test_a_marker_in_the_middle_of_a_line_still_splits_there():
    # Claude joins the passages around a web search with a space.
    speech, card = split_card(f"{GIST} [[kartu: Cuaca 7 hari]]\n{FORECAST}")

    assert speech == GIST
    assert card == Card("Cuaca 7 hari", FORECAST)


def test_text_after_the_marker_on_its_line_is_the_first_line_of_the_card():
    speech, card = split_card("Ini daftarnya. [[kartu: Banner]] Acheron rerun\nFirefly baru")

    assert speech == "Ini daftarnya."
    assert card == Card("Banner", "Acheron rerun\nFirefly baru")


def test_a_reply_that_is_only_a_card_has_no_speech():
    assert split_card(f"[[kartu: Cuaca 7 hari]]\n{FORECAST}") == (
        "",
        Card("Cuaca 7 hari", FORECAST),
    )


@pytest.mark.parametrize("after", ["", "\n", "\n\n   \n", "\n[senang]\n"])
def test_a_marker_without_a_body_gives_no_card_and_its_title_is_never_spoken(after):
    speech, card = split_card(f"{GIST}\n[[kartu: Cuaca 7 hari]]{after}")

    assert speech == GIST
    assert card is None


def test_a_second_marker_becomes_a_line_of_its_title():
    text = f"{GIST}\n[[kartu: Cuaca]]\n- Senin cerah\n[[kartu: Kualitas udara]]\n- Baik"

    _, card = split_card(text)

    assert card == Card("Cuaca", "- Senin cerah\nKualitas udara\n- Baik")


def test_emotion_tags_leave_the_card_line_by_line_and_its_title():
    text = f"{GIST}\n[[kartu: [senang] Cuaca]]\n[senang] - Senin cerah\n- Selasa [sedih] hujan"

    _, card = split_card(text)

    assert card == Card("Cuaca", "- Senin cerah\n- Selasa hujan")


def test_the_body_keeps_its_indentation_and_one_blank_line_between_parts():
    body = "Langkah:\n\n\n\n1. Buka Pengaturan\n   - pilih Jaringan\n\n2. Ganti DNS"

    _, card = split_card(f"{GIST}\n[[kartu: DNS]]\n\n{body}\n\n\n")

    assert card == Card(
        "DNS", "Langkah:\n\n1. Buka Pengaturan\n   - pilih Jaringan\n\n2. Ganti DNS"
    )


@pytest.mark.parametrize("marker", ["[[kartu: Cuaca]]", "[kartu Cuaca]"])
def test_windows_line_breaks_and_trailing_spaces_are_cleaned(marker):
    _, card = split_card(f"{GIST}\r\n{marker}\r\n- Senin cerah   \r\n- Rabu hujan\r\n")

    assert card == Card("Cuaca", "- Senin cerah\n- Rabu hujan")


def test_runs_of_spaces_in_the_title_become_one():
    _, card = split_card(f"{GIST}\n[[kartu:   Cuaca    7   hari ]]\n{FORECAST}")

    assert card is not None
    assert card.title == "Cuaca 7 hari"


def test_a_long_title_is_cut_with_an_ellipsis():
    title = "Jadwal " + "sangat panjang " * 10

    _, card = split_card(f"{GIST}\n[[kartu: {title}]]\n{FORECAST}")

    assert card is not None
    assert len(card.title) <= MAX_TITLE_CHARS
    assert card.title.endswith("…")
    assert card.title.startswith("Jadwal sangat panjang")


def test_a_card_over_sixty_lines_keeps_fifty_nine_and_an_ellipsis_line():
    lines = [f"- baris {number}" for number in range(1, 81)]

    _, card = split_card(f"{GIST}\n[[kartu: Panjang]]\n" + "\n".join(lines))

    assert card is not None
    kept = card.body.split("\n")
    assert len(kept) == MAX_BODY_LINES
    assert kept[:-1] == lines[: MAX_BODY_LINES - 1]
    assert kept[-1] == "…"


def test_a_body_over_four_thousand_characters_is_cut_with_an_ellipsis():
    _, card = split_card(f"{GIST}\n[[kartu: Panjang]]\n" + "kata " * 1200)

    assert card is not None
    assert len(card.body) <= MAX_BODY_CHARS
    assert card.body.endswith("…")
    assert card.body.startswith("kata kata")


def test_a_bracket_that_is_not_a_card_stays_in_the_speech():
    text = "[senang] Lihat catatan [1] dan [[catatan]] di sana, kartunya bagus."

    assert split_card(text) == (text, None)


@pytest.mark.parametrize(
    "text",
    [
        "Kamu bisa bayar pakai [kartu kredit] atau transfer bank.",
        "[Kartu Keluarga] dibawa ya, Master.",
        "Buka https://example.com/a?q=[kartu]&x=1 saja.",
    ],
)
def test_one_bracket_around_a_kartu_phrase_in_a_sentence_is_no_marker(text):
    assert split_card(text) == (text, None)


def test_a_kartu_phrase_in_brackets_before_the_marker_stays_in_the_speech():
    gist = "Ini syarat daftar [Kartu Prakerja], kutampilkan di layar ya."

    speech, card = split_card(f"{gist}\n[[kartu: Syarat Prakerja]]\n- KTP\n- NIK")

    assert speech == gist
    assert card == Card("Syarat Prakerja", "- KTP\n- NIK")


def test_a_kartu_phrase_in_brackets_inside_the_body_stays_as_written():
    body = (
        "- Daftar di [Kartu Prakerja](https://www.prakerja.go.id/)\n"
        "- [kartu nama] = meishi\n"
        "[kartu kredit]"
    )

    _, card = split_card(f"{GIST}\n[[kartu: Kosakata]]\n{body}")

    assert card == Card("Kosakata", body)


def test_a_later_marker_with_one_bracket_and_a_colon_becomes_a_line_of_its_title():
    _, card = split_card(f"{GIST}\n[[kartu: Cuaca]]\n- Senin cerah\n[kartu: Udara]\n- Baik")

    assert card == Card("Cuaca", "- Senin cerah\nUdara\n- Baik")


# --- before_card -------------------------------------------------------------------------------

QUESTION = "Matikan komputernya sekarang, Master?"


@pytest.mark.parametrize(
    ("reply", "heard"),
    [
        ("Baik, Master.", f"Baik, Master. {QUESTION}"),
        ("", QUESTION),
    ],
)
def test_without_a_card_the_question_follows_the_reply_as_before(reply, heard):
    assert before_card(reply, QUESTION) == heard


def test_with_a_card_the_question_ends_the_spoken_part_before_the_marker():
    reply = f"{GIST}\n[[kartu: Cuaca]]\n{FORECAST}"

    placed = before_card(reply, QUESTION)

    assert placed == f"{GIST} {QUESTION}\n[[kartu: Cuaca]]\n{FORECAST}"
    assert split_card(placed) == (f"{GIST} {QUESTION}", Card("Cuaca", FORECAST))


def test_with_only_a_card_the_question_is_the_spoken_part():
    placed = before_card(f"[senang] [[kartu: Cuaca]]\n{FORECAST}", QUESTION)

    assert split_card(placed) == (f"[senang] {QUESTION}", Card("Cuaca", FORECAST))


# --- card_message ------------------------------------------------------------------------------


def test_a_message_without_a_card_is_the_speech():
    assert card_message("Selamat pagi, Master.", None) == "Selamat pagi, Master."


def test_a_message_with_a_card_adds_its_title_and_body_after_a_blank_line():
    message = card_message("Laporan minggu ini.", Card("Rincian", "- HSR: 40 pull"))

    assert message == "Laporan minggu ini.\n\nRincian\n- HSR: 40 pull"


def test_a_message_with_an_untitled_card_adds_only_its_body():
    assert card_message("Ini.", Card("", "- satu")) == "Ini.\n\n- satu"


def test_a_long_message_is_cut_to_fit_one_push_by_its_utf8_bytes():
    card = Card("Kata", "\n".join(["- ありがとう ございます"] * 200))

    message = card_message("Ini kosakatanya.", card)

    assert len(message.encode("utf-8")) <= MESSAGE_MAX_BYTES
    assert message.startswith("Ini kosakatanya.\n\nKata\n- ありがとう")
    assert message.endswith("…")


# --- Card.allows_link ----------------------------------------------------------------------------


def test_a_card_allows_only_links_written_in_it():
    card = Card("Tautan", "- Docs: https://example.com/docs?a=1.\n- lain")

    assert card.allows_link("https://example.com/docs?a=1")
    assert not card.allows_link("https://evil.example/")
    assert not card.allows_link("")


# --- is_card_request ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "tampilkan lagi kartunya",
        "Tampilin lagi kartunya dong.",
        "Cyrene, kartu tadi?",
        "kartunya tadi",
        "mana kartunya?",
        "Tunjukin kartunya lagi, Master.",
        "tampilkan kartunya",
        "kartu yang tadi",
        "buka lagi kartunya ya",
        "lihat kartu itu lagi",
        "Sairin, tolong munculin kartunya lagi",
        "ＫＡＲＴＵ tadi",
        "tampilkan lagi kartu terakhir",
        "kartu terakhir",
        "tampilkan kartunya sekali lagi",
    ],
)
def test_a_whole_re_show_phrase_is_a_card_request(text):
    assert is_card_request(text, NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "kartunya",
        "kartu",
        "buka kartu",
        "kartu kredit",
        "kartu tadi isinya apa?",
        "tampilkan lagi",
        "ulangi",
        "kartu tadi kartunya",
        "tampilkan lagi lagi lagi lagi kartunya",
        "simpan kartunya",
        "tampilkan cuaca seminggu",
        "Kartu yang mana?",
        "kartu mana",
        "kartu mana yang",
    ],
)
def test_other_words_are_no_card_request(text):
    assert not is_card_request(text, NAMES)
