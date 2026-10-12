import re
from pathlib import Path

import pytest

from waifu.avatar.emotion import EMOTIONS, split_emotion

ROOT = Path(__file__).resolve().parent.parent


def test_emotions_are_the_six_names_of_the_event_protocol():
    assert EMOTIONS == ("neutral", "happy", "sad", "angry", "surprised", "relaxed")


@pytest.mark.parametrize(
    ("tag", "emotion"),
    [
        ("senang", "happy"),
        ("sedih", "sad"),
        ("marah", "angry"),
        ("kaget", "surprised"),
        ("santai", "relaxed"),
        ("netral", "neutral"),
    ],
)
def test_indonesian_leading_tag_selects_its_emotion_and_is_removed(tag, emotion):
    assert split_emotion(f"[{tag}] Siap, Master.") == (emotion, "Siap, Master.")


@pytest.mark.parametrize("emotion", ["neutral", "happy", "sad", "angry", "surprised", "relaxed"])
def test_english_emotion_names_are_tags_for_themselves(emotion):
    assert split_emotion(f"[{emotion}] Siap, Master.") == (emotion, "Siap, Master.")


@pytest.mark.parametrize(
    "tagged",
    ["[Senang] Halo", "[SENANG] Halo", "[ Senang ] Halo", "  [senang]Halo", "[Happy] Halo"],
)
def test_tags_ignore_case_and_spacing(tagged):
    assert split_emotion(tagged) == ("happy", "Halo")


def test_reply_without_a_tag_is_neutral_and_unchanged():
    assert split_emotion("Lampu kamar sudah menyala, Master.") == (
        "neutral",
        "Lampu kamar sudah menyala, Master.",
    )


def test_later_tags_are_removed_without_changing_the_leading_emotion():
    assert split_emotion("[senang] Halo [sedih] Master, [marah] apa kabar?") == (
        "happy",
        "Halo Master, apa kabar?",
    )


def test_a_tag_that_does_not_lead_is_removed_and_the_reply_stays_neutral():
    assert split_emotion("Halo [senang] Master") == ("neutral", "Halo Master")


def test_adjacent_tags_leave_a_single_space():
    assert split_emotion("[senang][santai] Halo [sedih] [marah] Master") == (
        "happy",
        "Halo Master",
    )


def test_no_space_is_left_before_punctuation_that_followed_a_removed_tag():
    assert split_emotion("[senang] Siap, Master [santai]!") == ("happy", "Siap, Master!")


def test_line_breaks_the_model_wrote_are_kept():
    assert split_emotion("[santai] Baris satu.\nBaris dua.") == (
        "relaxed",
        "Baris satu.\nBaris dua.",
    )


@pytest.mark.parametrize("bracketed", ["[1]", "[catatan]", "[senangnya]", "[Senin, 12-10-2026]"])
def test_other_bracketed_text_is_kept(bracketed):
    assert split_emotion(f"[kaget] Lihat {bracketed} ya.") == (
        "surprised",
        f"Lihat {bracketed} ya.",
    )


def test_bracketed_text_that_is_not_an_emotion_does_not_count_as_a_leading_tag():
    assert split_emotion("[1] Halo") == ("neutral", "[1] Halo")


@pytest.mark.parametrize("only_tag", ["[senang]", "  [ senang ]  ", "[senang] [sedih]"])
def test_reply_that_was_only_a_tag_leaves_no_text(only_tag):
    assert split_emotion(only_tag) == ("happy", "")


def test_japanese_text_after_a_tag_survives_intact():
    assert split_emotion("[kaget] えっ、本当ですか？「おかえりなさい」、マスター！") == (
        "surprised",
        "えっ、本当ですか？「おかえりなさい」、マスター！",
    )


def test_tag_followed_by_a_full_width_space_before_japanese_text():
    assert split_emotion("[senang]　おかえりなさい") == ("happy", "おかえりなさい")


@pytest.mark.parametrize(
    "tagged",
    ["［senang］おかえりなさい", "【senang】おかえりなさい", "［ Senang ］ おかえりなさい"],
)
def test_a_tag_in_full_width_brackets_selects_its_emotion_and_is_removed(tagged):
    assert split_emotion(tagged) == ("happy", "おかえりなさい")


@pytest.mark.parametrize("tag", ["［sedih］", "【sedih】"])
def test_a_later_tag_in_full_width_brackets_is_removed_too(tag):
    assert split_emotion(f"Halo {tag} Master") == ("neutral", "Halo Master")


@pytest.mark.parametrize("bracketed", ["【注意】", "［1］", "【senangnya】"])
def test_other_text_in_full_width_brackets_is_kept(bracketed):
    assert split_emotion(f"{bracketed} Halo") == ("neutral", f"{bracketed} Halo")


def test_committed_persona_lists_a_recognised_tag_for_every_emotion():
    persona = (ROOT / "config" / "persona.md").read_text(encoding="utf-8")

    tags = re.findall(r"\[[^\[\]]*\]", persona)

    assert {split_emotion(tag) for tag in tags} == {(emotion, "") for emotion in EMOTIONS}


def test_committed_persona_asks_for_the_tag_on_the_text_after_a_tool_call_too():
    # Only the model's last message is spoken, so a tag written before a tool call is lost.
    persona = (ROOT / "config" / "persona.md").read_text(encoding="utf-8")

    expressions = persona.split("Ekspresi wajah", 1)[1].split("\n\n", 1)[0].lower()

    assert "setelah memanggil tool" in expressions
    assert "terakhir" in expressions
