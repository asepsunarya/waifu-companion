import logging
import threading

from waifu.config import (
    AppConfig,
    AssistantConfig,
    DeviceConfig,
    GachaConfig,
    RoutineConfig,
    RoutineStep,
)
from waifu.voice.hotwords import (
    HOTWORDS_MAX_CHARS,
    HOTWORDS_MAX_TERMS,
    ROSTER_NAMES_PER_GAME,
    Vocabulary,
    build_hotwords,
)


def home_config(*, gacha: bool = True, games: tuple[str, ...] = ("hsr", "zzz")) -> AppConfig:
    """Her name and wake words, two devices with aliases, an app launcher, and a routine."""
    return AppConfig(
        assistant=AssistantConfig(name="Cyrene", wake_words=["cyrene", "hai cyrene"]),
        devices=[
            DeviceConfig(
                id="lampu_meja", name="Lampu Meja", aliases=["lampu belajar"], type="mock"
            ),
            DeviceConfig(id="ac", name="AC", aliases=["pendingin"], type="mock"),
            DeviceConfig(
                id="aplikasi",
                name="Aplikasi PC",
                type="apps",
                apps={"star_rail": "steam://rungameid/1", "discord": "steam://rungameid/2"},
            ),
        ],
        routines={
            "mau_tidur": RoutineConfig(steps=[RoutineStep(device="ac", command="off")]),
        },
        gacha=GachaConfig(enabled=gacha, games=list(games)),
    )


def terms(text: str) -> list[str]:
    return text.split(", ")


def test_hotwords_list_her_names_devices_apps_games_routines_and_aliases_in_that_order():
    assert terms(build_hotwords(home_config())) == [
        "Cyrene",
        "hai cyrene",
        "Lampu Meja",
        "AC",
        "Aplikasi PC",
        "star rail",
        "discord",
        "HSR",
        "Honkai: Star Rail",
        "ZZZ",
        "Zenless Zone Zero",
        "mau tidur",
        "lampu belajar",
        "pendingin",
    ]


def test_a_term_already_listed_in_another_case_is_skipped():
    config = home_config(gacha=False)
    config.devices[1].aliases.append("lampu meja")

    listed = terms(build_hotwords(config))

    assert "cyrene" not in listed  # the wake word repeats her name
    assert listed.count("Lampu Meja") == 1
    assert "lampu meja" not in listed


def test_the_games_are_left_out_while_gacha_is_off():
    listed = terms(build_hotwords(home_config(gacha=False)))

    assert "HSR" not in listed
    assert listed[-3:] == ["mau tidur", "lampu belajar", "pendingin"]


def roster_part(listed: list[str]) -> list[str]:
    """The names between the games and the routine of `home_config`."""
    return listed[listed.index("Zenless Zone Zero") + 1 : listed.index("mau tidur")]


def test_roster_names_follow_the_games_taking_turns_in_the_order_given_five_per_game():
    rosters = {
        "zzz": ["Ellen", "Ellen Joe"],
        "hsr": [f"Hero {n}" for n in range(1, 13)],
    }

    listed = terms(build_hotwords(home_config(), rosters))

    assert roster_part(listed) == [
        "Ellen",
        "Hero 1",
        "Ellen Joe",
        "Hero 2",
        "Hero 3",
        "Hero 4",
        "Hero 5",
    ]


def test_every_game_read_gets_names_ahead_of_the_aliases():
    games = ("hsr", "genshin", "zzz")
    config = home_config(games=games)
    config.devices[0].aliases.extend(f"lampu {n}" for n in range(20))
    rosters = {game: [f"{game} {n}" for n in range(10)] for game in games}

    listed = terms(build_hotwords(config, rosters))

    assert len(listed) == HOTWORDS_MAX_TERMS
    for game in games:
        assert [term for term in listed if term.startswith(f"{game} ")] == [
            f"{game} {n}" for n in range(ROSTER_NAMES_PER_GAME)
        ]
    assert "mau tidur" in listed
    assert "pendingin" not in listed


def test_a_roster_of_a_game_not_configured_is_left_out():
    listed = terms(build_hotwords(home_config(games=("hsr",)), {"genshin": ["Furina"]}))

    assert "Furina" not in listed


def test_a_full_list_drops_the_aliases_first():
    config = home_config(gacha=False)
    config.devices[0].aliases.extend(f"lampu {n}" for n in range(40))

    listed = terms(build_hotwords(config))

    assert len(listed) == HOTWORDS_MAX_TERMS
    assert listed[:8] == [
        "Cyrene",
        "hai cyrene",
        "Lampu Meja",
        "AC",
        "Aplikasi PC",
        "star rail",
        "discord",
        "mau tidur",
    ]
    assert "pendingin" not in listed


def test_the_list_ends_before_the_first_term_that_would_pass_the_character_cap():
    config = home_config(gacha=False)
    config.devices[0].aliases[:] = ["x" * 120, "y" * 120, "z" * 120, "pendek"]

    text = build_hotwords(config)

    assert len(text) <= HOTWORDS_MAX_CHARS
    assert "z" * 120 not in text
    assert "pendek" not in text  # importance order: a later short term never jumps the queue


def test_blank_terms_are_dropped():
    config = home_config(gacha=False)

    listed = terms(build_hotwords(config, {"hsr": ["  ", ""]}))

    assert all(term.strip() == term and term for term in listed)


def test_the_vocabulary_is_built_at_start_from_the_config():
    vocabulary = Vocabulary(home_config())

    assert vocabulary.text() == build_hotwords(home_config())


def test_a_roster_read_joins_the_vocabulary_and_replaces_that_games_earlier_one(caplog):
    vocabulary = Vocabulary(home_config())

    with caplog.at_level(logging.INFO, logger="waifu.voice.hotwords"):
        vocabulary.set_roster("hsr", ["Acheron", "Kafka"])
        vocabulary.set_roster("zzz", ["Ellen"])
        vocabulary.set_roster("hsr", ["Firefly"])

    listed = terms(vocabulary.text())
    assert roster_part(listed) == ["Firefly", "Ellen"]
    assert "Acheron" not in listed
    assert "Whisper hotwords now hold 16 terms" in caplog.text
    assert "Firefly" not in caplog.text  # the names never reach the log


def test_the_roster_read_last_comes_first():
    vocabulary = Vocabulary(home_config())

    vocabulary.set_roster("hsr", ["Acheron"])
    vocabulary.set_roster("zzz", ["Ellen"])
    assert roster_part(terms(vocabulary.text())) == ["Ellen", "Acheron"]

    vocabulary.set_roster("hsr", ["Acheron"])
    assert roster_part(terms(vocabulary.text())) == ["Acheron", "Ellen"]


def test_the_same_roster_again_logs_nothing(caplog):
    vocabulary = Vocabulary(home_config())
    vocabulary.set_roster("hsr", ["Acheron"])

    with caplog.at_level(logging.INFO, logger="waifu.voice.hotwords"):
        vocabulary.set_roster("hsr", ["Acheron"])

    assert caplog.text == ""


def test_the_vocabulary_is_read_while_another_thread_sets_rosters():
    vocabulary = Vocabulary(home_config())
    texts: list[str] = []

    def read() -> None:
        texts.extend(vocabulary.text() for _ in range(200))

    reader = threading.Thread(target=read)
    reader.start()
    for n in range(200):
        vocabulary.set_roster("hsr", [f"Hero {n}"])
    reader.join()

    assert all(text.startswith("Cyrene, hai cyrene") for text in texts)
    assert roster_part(terms(vocabulary.text())) == ["Hero 199"]
