import json
import threading
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from waifu.brain.tools import to_anthropic
from waifu.gacha.game_data import (
    GAME_MODES,
    Banner,
    ChallengeWindow,
    Endgame,
    EndgameStage,
    GameCalendar,
    GameEvent,
)
from waifu.gacha.game_data_tools import GAME_CHARACTERS, GAME_ENDGAME, GAME_EVENTS, GameDataTools
from waifu.gacha.genshin_impact import (
    GenshinArtifact,
    GenshinCharacter,
    GenshinCharacterDetail,
    GenshinWeapon,
)
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.starrail import (
    ENDGAME_MODES,
    HsrCalendar,
    HsrChallengeWindow,
    HsrCharacter,
    HsrEndgame,
    HsrEvent,
    HsrFloor,
    HsrLightCone,
    HsrRelic,
    HsrWarp,
)
from waifu.gacha.zenless import ZzzAgent, ZzzAgentDetail, ZzzDisc, ZzzWEngine

WIB = timezone(timedelta(hours=7))
CST = timezone(timedelta(hours=8))  # the Asia server's time, as HoYoLAB gives it
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=WIB)
TOOL_NAMES = [GAME_CHARACTERS, GAME_ENDGAME, GAME_EVENTS]
HSR = ("hsr",)
ALL_GAMES = ("hsr", "genshin", "zzz")
GENSHIN = {"game": "genshin"}
ZZZ = {"game": "zzz"}
UNSUPPORTED_STRICT_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
}


def character(name: str, light_cone: str, path: str) -> HsrCharacter:
    return HsrCharacter(
        name=name,
        rarity=5,
        level=80,
        eidolon=2,
        path=path,
        element="Lightning",
        light_cone=HsrLightCone(light_cone, 80, 1, 5),
        stats=(("HP", "3512"), ("CRIT Rate", "65.2%")),
        relics=(HsrRelic("Head", "HP", "705", 15),),
        memosprite=None,
    )


def endgame(mode: str, season: str, floor: str) -> HsrEndgame:
    return HsrEndgame(
        mode=mode,
        season=season,
        ends_at=NOW + timedelta(days=9),
        has_data=True,
        stars=27,
        extra_stars=1,
        deepest_floor=floor,
        battles=14,
        floors=(HsrFloor(floor, 3, 4, None),),
    )


def calendar(version: str, warp: str, featured: str, event: str) -> HsrCalendar:
    return HsrCalendar(
        version=version,
        warps=(
            HsrWarp(
                "character",
                warp,
                (featured,),
                ("Pela", "Gallagher", "Hook"),
                NOW - timedelta(days=3),
                NOW + timedelta(days=18),
            ),
        ),
        events=(HsrEvent(event, NOW - timedelta(days=2), NOW + timedelta(days=5), 3, 7, False),),
        challenges=(
            HsrChallengeWindow(
                "pure_fiction", "Pure Fiction", NOW - timedelta(days=42), NOW, 4, 12
            ),
        ),
    )


def genshin_character(
    name: str,
    *,
    ref: int,
    rarity: int = 5,
    level: int = 90,
    constellation: int = 0,
    element: str = "Hydro",
    weapon_type: str = "Sword",
    weapon: GenshinWeapon | None = None,
) -> GenshinCharacter:
    return GenshinCharacter(
        ref,
        name,
        rarity,
        level,
        constellation,
        element,
        weapon_type,
        weapon or GenshinWeapon("Favonius Sword", 90, 5, 4),
    )


def genshin_detail(
    character_: GenshinCharacter,
    *,
    weapon_stats: tuple[tuple[str, str], ...] = (("Base ATK", "454"), ("Energy Recharge", "61.3%")),
    stats: tuple[tuple[str, str], ...] = (),
    artifacts: tuple[GenshinArtifact, ...] = (),
    sets: tuple[tuple[str, int], ...] = (),
) -> GenshinCharacterDetail:
    return GenshinCharacterDetail(character_, weapon_stats, stats, artifacts, sets)


def zzz_agent(
    name: str,
    *,
    ref: int,
    full_name: str | None = None,
    rarity: str = "S",
    level: int = 60,
    mindscape: int = 0,
    element: str = "Ice",
    specialty: str = "Attack",
) -> ZzzAgent:
    return ZzzAgent(ref, name, full_name or name, rarity, level, mindscape, element, specialty)


def zzz_detail(
    agent: ZzzAgent,
    *,
    w_engine: ZzzWEngine | None = None,
    stats: tuple[tuple[str, str], ...] = (),
    discs: tuple[ZzzDisc, ...] = (),
    sets: tuple[tuple[str, int], ...] = (),
) -> ZzzAgentDetail:
    return ZzzAgentDetail(agent, w_engine, stats, discs, sets)


def game_endgame(mode: str, season: str, stage: str) -> Endgame:
    return Endgame(
        mode=mode,
        season=season,
        ends_at=NOW + timedelta(days=9),
        has_data=True,
        stars=27,
        deepest=stage,
        difficulty=None,
        rating=None,
        score=None,
        max_score=None,
        seconds=None,
        battles=None,
        stages=(EndgameStage(stage, 3, None, None, None, None),),
    )


def game_calendar(version: str, banner: str, featured: str, event: str) -> GameCalendar:
    return GameCalendar(
        version=version,
        banners=(
            Banner(
                "character",
                banner,
                (featured,),
                ("Xiangling", "Bennett", "Xingqiu"),
                NOW - timedelta(days=3),
                NOW + timedelta(days=18),
            ),
        ),
        events=(GameEvent(event, NOW - timedelta(days=2), NOW + timedelta(days=5), 3, 7, False),),
        challenges=(),
    )


EMPTY_HSR_CALENDAR = HsrCalendar(None, (), (), ())
EMPTY_CALENDAR = GameCalendar(None, (), (), ())


def detail_ref(detail: GenshinCharacterDetail | ZzzAgentDetail) -> int:
    if isinstance(detail, GenshinCharacterDetail):
        return detail.character.ref
    return detail.agent.ref


class FakeSource:
    """HoyolabClient's game data methods, kept per game: a GachaError is raised, or returned
    for its mode. `details` holds every detail HoYoLAB would give for that game."""

    def __init__(
        self,
        *,
        characters: dict[str, tuple | GachaError] | None = None,
        details: dict[str, tuple | GachaError] | None = None,
        endgames: dict[str, HsrEndgame | Endgame | GachaError] | None = None,
        calendars: dict[str, HsrCalendar | GameCalendar | GachaError] | None = None,
    ) -> None:
        self.characters: dict[str, tuple | GachaError] = {
            "hsr": (),
            "genshin": (),
            "zzz": (),
            **(characters or {}),
        }
        self.details: dict[str, tuple | GachaError] = {"genshin": (), "zzz": (), **(details or {})}
        self.endgames: dict[str, HsrEndgame | Endgame | GachaError] = dict(endgames or {})
        self.calendars: dict[str, HsrCalendar | GameCalendar | GachaError] = {
            "hsr": EMPTY_HSR_CALENDAR,
            "genshin": EMPTY_CALENDAR,
            "zzz": EMPTY_CALENDAR,
            **(calendars or {}),
        }
        self.calls: list[str] = []

    def hsr_characters(self) -> tuple[HsrCharacter, ...]:
        return self._characters("hsr")

    def hsr_endgame(self, modes):
        return self._endgame("hsr", modes)

    def hsr_calendar(self) -> HsrCalendar:
        return self._calendar("hsr")

    def genshin_characters(self) -> tuple[GenshinCharacter, ...]:
        return self._characters("genshin")

    def genshin_character_details(self, refs) -> tuple[GenshinCharacterDetail, ...]:
        return self._details("genshin", refs)

    def genshin_endgame(self, modes):
        return self._endgame("genshin", modes)

    def genshin_calendar(self) -> GameCalendar:
        return self._calendar("genshin")

    def zzz_characters(self) -> tuple[ZzzAgent, ...]:
        return self._characters("zzz")

    def zzz_character_details(self, refs) -> tuple[ZzzAgentDetail, ...]:
        return self._details("zzz", refs)

    def zzz_endgame(self, modes):
        return self._endgame("zzz", modes)

    def zzz_calendar(self) -> GameCalendar:
        return self._calendar("zzz")

    def _characters(self, game: str):
        self.calls.append(f"{game}_characters")
        return _raise_error(self.characters[game])

    def _details(self, game: str, refs):
        refs = list(refs)
        self.calls.append(f"{game}_character_details:{','.join(str(ref) for ref in refs)}")
        details = _raise_error(self.details[game])
        return tuple(detail for detail in details if detail_ref(detail) in refs)

    def _endgame(self, game: str, modes):
        modes = list(modes)
        self.calls.append(f"{game}_endgame:{','.join(modes)}")
        return {mode: self.endgames[mode] for mode in modes}

    def _calendar(self, game: str):
        self.calls.append(f"{game}_calendar")
        return _raise_error(self.calendars[game])


def _raise_error(result):
    if isinstance(result, GachaError):
        raise result
    return result


SAMPLE_LIVE_TEXT = [
    "Acheron",
    "Along the Passing Shore",
    "Nihility",
    "Seasonal Garden of Quiet",
    "Stage Twelve Sample",
    "3.6",
    "Butterfly Sample Warp",
    "Firefly",
    "Gift of Sample Odyssey",
    "Furina",
    "Splendor of Tranquil Waters",
    "Golden Troupe",
    "Zhu Yuan",
    "Riot Suppressor Mark VI",
    "Chaos Jazz",
]
OTHER_LIVE_TEXT = [
    "Castorice",
    "Make Farewells More Beautiful",
    "Remembrance",
    "Eternal Other Night",
    "Stage Eleven Other",
    "3.7",
    "Moonlit Other Warp",
    "Hyacine",
    "Other Realm Festival",
    "Nahida",
    "A Thousand Floating Dreams",
    "Deepwood Memories",
    "Miyabi",
    "Hailstorm Shrine",
    "Branch & Blade Song",
]


def live_source(texts: list[str]) -> FakeSource:
    """A source whose every game holds the given names: none may reach a description."""
    (
        name,
        light_cone,
        path,
        season,
        floor,
        version,
        warp,
        featured,
        event,
        genshin_name,
        weapon,
        artifact_set,
        agent_name,
        w_engine,
        disc_set,
    ) = texts
    genshin_one = genshin_character(genshin_name, ref=1, weapon=GenshinWeapon(weapon, 90, 1, 5))
    agent = zzz_agent(agent_name, ref=2)
    other_modes = [*GAME_MODES["genshin"], *GAME_MODES["zzz"]]
    return FakeSource(
        characters={
            "hsr": (character(name, light_cone, path),),
            "genshin": (genshin_one,),
            "zzz": (agent,),
        },
        details={
            "genshin": (
                genshin_detail(
                    genshin_one,
                    artifacts=(GenshinArtifact("Flower of Life", artifact_set, "HP", "4780", 20),),
                    sets=((artifact_set, 2),),
                ),
            ),
            "zzz": (
                zzz_detail(
                    agent,
                    w_engine=ZzzWEngine(w_engine, 60, 1, "S"),
                    discs=(ZzzDisc(1, disc_set, "HP", "2200", 15),),
                    sets=((disc_set, 2),),
                ),
            ),
        },
        endgames={
            **{mode: endgame(mode, season, floor) for mode in ENDGAME_MODES},
            **{mode: game_endgame(mode, season, floor) for mode in other_modes},
        },
        calendars={
            "hsr": calendar(version, warp, featured, event),
            "genshin": game_calendar(version, warp, featured, event),
            "zzz": game_calendar(version, warp, featured, event),
        },
    )


def sample_source() -> FakeSource:
    return live_source(SAMPLE_LIVE_TEXT)


def other_source() -> FakeSource:
    return live_source(OTHER_LIVE_TEXT)


def make_tools(source: FakeSource | None = None, games: Sequence[str] = HSR) -> GameDataTools:
    return GameDataTools(source or sample_source(), games, now=lambda: NOW)


def spec(tools: GameDataTools, name: str):
    return next(s for s in tools.specs() if s.name == name)


def descriptions(tools: GameDataTools) -> list[str]:
    return [s.description for s in tools.specs()]


def wire(tools: GameDataTools) -> str:
    return json.dumps([to_anthropic(s) for s in tools.specs()], ensure_ascii=False)


def schema_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from schema_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from schema_nodes(child)


# --- the specs -----------------------------------------------------------------------------


def test_three_game_data_tools_with_their_arguments():
    tools = make_tools()
    game = {"type": "string", "enum": ["hsr"]}

    assert [s.name for s in tools.specs()] == ["game_characters", "game_endgame", "game_events"]
    assert spec(tools, GAME_CHARACTERS).input_schema == {
        "type": "object",
        "properties": {"game": game, "name": {"type": "string"}},
        "additionalProperties": False,
    }
    assert spec(tools, GAME_ENDGAME).input_schema == {
        "type": "object",
        "properties": {"game": game, "mode": {"type": "string", "enum": list(ENDGAME_MODES)}},
        "additionalProperties": False,
    }
    assert spec(tools, GAME_EVENTS).input_schema == {
        "type": "object",
        "properties": {"game": game},
        "additionalProperties": False,
    }


def test_the_game_and_mode_enums_hold_the_configured_games_in_config_order():
    tools = make_tools(games=("zzz", "hsr", "genshin"))

    for tool in tools.specs():
        assert tool.input_schema["properties"]["game"]["enum"] == ["zzz", "hsr", "genshin"]
    assert spec(tools, GAME_ENDGAME).input_schema["properties"]["mode"]["enum"] == [
        "shiyu_defense",
        "deadly_assault",
        "memory_of_chaos",
        "pure_fiction",
        "apocalyptic_shadow",
        "anomaly_arbitration",
        "spiral_abyss",
        "imaginarium_theater",
        "stygian_onslaught",
    ]


def test_only_the_modes_of_the_configured_games_are_offered():
    tools = make_tools(games=("genshin",))

    mode = spec(tools, GAME_ENDGAME).input_schema["properties"]["mode"]

    assert mode["enum"] == ["spiral_abyss", "imaginarium_theater", "stygian_onslaught"]


@pytest.mark.parametrize("games", [HSR, ALL_GAMES, ("genshin", "zzz")])
def test_every_object_forbids_additional_properties_within_strict_tool_use(games):
    for tool in make_tools(games=games).specs():
        for node in schema_nodes(tool.input_schema):
            assert not UNSUPPORTED_STRICT_KEYWORDS & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False


@pytest.mark.parametrize("games", [HSR, ALL_GAMES])
def test_optional_arguments_are_not_required_so_the_tools_stay_off_the_strict_budget(games):
    for tool in make_tools(games=games).specs():
        assert tool.input_schema.get("required", []) == []
        assert to_anthropic(tool)["strict"] is False


def test_the_hsr_modes_keep_their_display_order():
    mode = spec(make_tools(), GAME_ENDGAME).input_schema["properties"]["mode"]

    assert mode["enum"] == list(ENDGAME_MODES)
    assert list(ENDGAME_MODES) == [
        "memory_of_chaos",
        "pure_fiction",
        "apocalyptic_shadow",
        "anomaly_arbitration",
    ]
    assert list(ENDGAME_MODES.values()) == [
        "Memory of Chaos",
        "Pure Fiction",
        "Apocalyptic Shadow",
        "Anomaly Arbitration",
    ]


def test_the_endgame_description_names_every_mode_of_the_configured_games():
    description = spec(make_tools(games=ALL_GAMES), GAME_ENDGAME).description

    for modes in GAME_MODES.values():
        for name in modes.values():
            assert name in description


@pytest.mark.parametrize(
    ("games", "names"),
    [
        (HSR, "Honkai: Star Rail = hsr"),
        (ALL_GAMES, "Honkai: Star Rail = hsr, Genshin Impact = genshin, Zenless Zone Zero = zzz"),
        (("zzz", "genshin"), "Zenless Zone Zero = zzz, Genshin Impact = genshin"),
    ],
)
def test_every_description_names_the_configured_games_with_their_ids(games, names):
    for description in descriptions(make_tools(games=games)):
        assert names in description


OWN_TERMS = {
    "hsr": ["Honkai: Star Rail", "Eidolon", "Light Cone", "Superimposition", "Memory of Chaos"],
    "genshin": ["Genshin Impact", "Constellation", "Spiral Abyss", "artifact", "Traveler"],
    "zzz": ["Zenless Zone Zero", "Mindscape Cinema", "W-Engine", "Shiyu Defense", "Drive Disc"],
}


@pytest.mark.parametrize(
    ("games", "rule"),
    [
        (HSR, "say so when a character or Light Cone is new to you"),
        (ALL_GAMES, "say so when a character, Light Cone, weapon, or W-Engine is new to you"),
        (("zzz",), "say so when a character or W-Engine is new to you"),
    ],
)
def test_she_says_so_when_a_character_or_the_gear_of_a_configured_game_is_new_to_her(games, rule):
    for description in descriptions(make_tools(games=games)):
        assert rule in description


@pytest.mark.parametrize("game", ALL_GAMES)
def test_descriptions_name_no_game_that_is_not_configured(game):
    text = " ".join(descriptions(make_tools(games=(game,))))

    for other in ALL_GAMES:
        for term in OWN_TERMS[other]:
            assert (term in text) is (other == game), term


@pytest.mark.parametrize(
    ("games", "terms"),
    [
        (HSR, ["Eidolon", "Light Cone", "Superimposition", "Memory of Chaos"]),
        (("genshin",), ["Constellation", "Refinement", "Spiral Abyss"]),
        (("zzz",), ["Mindscape Cinema", "W-Engine", "Shiyu Defense"]),
        (
            ALL_GAMES,
            [
                "Eidolon",
                "Light Cone",
                "Superimposition",
                "Memory of Chaos",
                "Constellation",
                "Refinement",
                "Spiral Abyss",
                "Mindscape Cinema",
                "W-Engine",
                "Shiyu Defense",
            ],
        ),
    ],
)
def test_every_description_keeps_the_official_terms_of_the_configured_games(games, terms):
    for description in descriptions(make_tools(games=games)):
        rules = description.split("Keep the official English terms such as ", 1)[1]
        for term in terms:
            assert term in rules.split(". ", 1)[0]


def test_specs_are_byte_identical_across_calls_and_source_data_for_prompt_caching():
    source = sample_source()
    tools = make_tools(source, ALL_GAMES)
    first = wire(tools)

    for game in ALL_GAMES:
        for name in TOOL_NAMES:
            tools.run(name, {"game": game})
        tools.run(GAME_CHARACTERS, {"game": game, "name": "Sample"})

    assert source.calls != []
    assert wire(tools) == first
    assert wire(make_tools(other_source(), ALL_GAMES)) == first


def test_specs_never_read_the_source():
    source = sample_source()

    make_tools(source, ALL_GAMES).specs()

    assert source.calls == []


@pytest.mark.parametrize("games", [HSR, ALL_GAMES])
def test_descriptions_carry_no_live_data(games):
    text = " ".join(descriptions(make_tools(games=games)))

    for live in SAMPLE_LIVE_TEXT + OTHER_LIVE_TEXT:
        assert live not in text


@pytest.mark.parametrize("games", [HSR, ALL_GAMES])
@pytest.mark.parametrize("tool", TOOL_NAMES)
@pytest.mark.parametrize(
    "rule",
    [
        "instead of guessing",
        "never guess",
        "one or two short sentences",
        "at most about three names",
        "no lists",
        "no parentheses",
        "official English terms",
        "new to you",
        "Never suggest pulling, spending money, or topping up",
        "Do not save this data with `remember` unless Master asks",
        "Never claim how many stars are missing",
        # The morning briefing turn offers every tool: banners and seasons ending are never
        # brought up unasked.
        "Call it only when Master asks about these games",
        "never in your own greeting or the morning briefing",
    ],
)
def test_every_description_carries_the_answer_rules(games, tool, rule):
    assert rule in spec(make_tools(games=games), tool).description


@pytest.mark.parametrize("games", [HSR, ("hsr", "zzz")])
def test_without_genshin_no_description_lets_her_claim_missing_stars(games):
    for description in descriptions(make_tools(games=games)):
        assert "Never claim how many stars are missing. " in description


def test_with_genshin_she_may_tell_missing_stars_only_on_a_spiral_abyss_floor():
    for description in descriptions(make_tools(games=ALL_GAMES)):
        assert (
            "Never claim how many stars are missing, except on a Spiral Abyss floor, which "
            "gives its `max_stars`. "
        ) in description


def test_the_events_description_presents_banners_only_as_information():
    assert "only as information" in spec(make_tools(games=ALL_GAMES), GAME_EVENTS).description


def test_the_characters_description_says_how_a_name_picks_one_character_and_more_the_others():
    description = spec(make_tools(games=ALL_GAMES), GAME_CHARACTERS).description

    assert "a name with its Path, such as Trailblazer Harmony, gives that one" in description
    assert "a name with its element, such as Traveler Anemo, gives that one" in description
    assert "the full name, such as Ellen Joe, works too" in description
    assert "`more` names the others" in description


# --- arguments -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (GAME_CHARACTERS, "Acheron"),
        (GAME_CHARACTERS, None),
        (GAME_CHARACTERS, ["Acheron"]),
        (GAME_CHARACTERS, {"name": ""}),
        (GAME_CHARACTERS, {"name": "   "}),
        (GAME_CHARACTERS, {"name": 5}),
        (GAME_CHARACTERS, {"name": ["Acheron"]}),
        (GAME_CHARACTERS, {"name": "x" * 61}),
        (GAME_CHARACTERS, {"name": "Acheron", "eidolon": 2}),
        (GAME_CHARACTERS, {"query": "Acheron"}),
        (GAME_CHARACTERS, {"game": "genshin"}),
        (GAME_CHARACTERS, {"game": "HSR"}),
        (GAME_CHARACTERS, {"game": 1}),
        (GAME_CHARACTERS, {"game": ["hsr"]}),
        (GAME_ENDGAME, {"mode": "forgotten_hall"}),
        (GAME_ENDGAME, {"mode": "Memory of Chaos"}),
        (GAME_ENDGAME, {"mode": 1}),
        (GAME_ENDGAME, {"mode": ["pure_fiction"]}),
        (GAME_ENDGAME, {"modes": ["pure_fiction"]}),
        (GAME_ENDGAME, {"mode": "spiral_abyss"}),
        (GAME_ENDGAME, {"game": "zzz", "mode": "shiyu_defense"}),
        (GAME_ENDGAME, "pure_fiction"),
        (GAME_ENDGAME, None),
        (GAME_EVENTS, {"month": 10}),
        (GAME_EVENTS, {"game": "zzz"}),
        (GAME_EVENTS, ["warps"]),
        (GAME_EVENTS, None),
    ],
)
def test_invalid_arguments_are_errors_without_asking_hoyolab(tool, arguments):
    source = sample_source()

    result, is_error = make_tools(source).run(tool, arguments)

    data = json.loads(result)
    assert is_error is True
    assert data["ok"] is False
    assert data["message"].startswith(f"Argumen {tool} tidak valid: ")
    assert source.calls == []


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (GAME_CHARACTERS, {}),
        (GAME_CHARACTERS, {"game": "hsr"}),
        (GAME_CHARACTERS, {"game": None, "name": None}),
        (GAME_CHARACTERS, {"name": "Acheron"}),
        (GAME_CHARACTERS, {"game": "hsr", "name": "  march 7th "}),
        (GAME_CHARACTERS, {"name": "x" * 60}),
        (GAME_ENDGAME, {}),
        (GAME_ENDGAME, {"game": "hsr"}),
        (GAME_ENDGAME, {"mode": None}),
        *((GAME_ENDGAME, {"mode": mode}) for mode in ENDGAME_MODES),
        (GAME_ENDGAME, {"game": "hsr", "mode": "pure_fiction"}),
        (GAME_EVENTS, {}),
        (GAME_EVENTS, {"game": "hsr"}),
    ],
)
def test_valid_arguments_are_accepted(tool, arguments):
    result, _ = make_tools().run(tool, arguments)

    assert "tidak valid" not in json.loads(result).get("message", "")


def test_an_unknown_tool_name_such_as_a_v1_16_name_is_an_error_without_asking_hoyolab():
    source = sample_source()

    result, is_error = make_tools(source).run("hsr_characters", {})

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": "Tool 'hsr_characters' bukan tool data game.",
    }
    assert source.calls == []


# --- which game ----------------------------------------------------------------------------


def invalid_message(tools: GameDataTools, tool: str, arguments: dict) -> str:
    result, is_error = tools.run(tool, arguments)
    assert is_error is True
    return json.loads(result)["message"]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (GAME_CHARACTERS, {}),
        (GAME_CHARACTERS, {"name": "Furina"}),
        (GAME_ENDGAME, {}),
        (GAME_EVENTS, {}),
    ],
)
def test_among_several_games_one_left_out_is_asked_for_without_asking_hoyolab(tool, arguments):
    source = sample_source()

    message = invalid_message(make_tools(source, ALL_GAMES), tool, arguments)

    assert message == (
        f"Argumen {tool} tidak valid: 'game' harus diisi karena ada beberapa game: "
        "hsr, genshin, zzz."
    )
    assert source.calls == []


def test_a_game_that_is_not_configured_is_invalid():
    source = sample_source()

    message = invalid_message(make_tools(source, ("hsr", "zzz")), GAME_EVENTS, GENSHIN)

    assert message == "Argumen game_events tidak valid: 'game' harus salah satu dari: hsr, zzz."
    assert source.calls == []


def test_a_mode_of_no_configured_game_is_invalid():
    source = sample_source()

    message = invalid_message(
        make_tools(source, ("hsr", "zzz")), GAME_ENDGAME, {"mode": "spiral_abyss"}
    )

    assert message == (
        "Argumen game_endgame tidak valid: 'mode' harus salah satu dari: memory_of_chaos, "
        "pure_fiction, apocalyptic_shadow, anomaly_arbitration, shiyu_defense, deadly_assault."
    )
    assert source.calls == []


def test_a_mode_of_another_game_than_the_one_given_is_invalid():
    source = sample_source()

    message = invalid_message(
        make_tools(source, ALL_GAMES), GAME_ENDGAME, {**GENSHIN, "mode": "shiyu_defense"}
    )

    assert message == (
        "Argumen game_endgame tidak valid: 'mode' untuk genshin harus salah satu dari: "
        "spiral_abyss, imaginarium_theater, stygian_onslaught."
    )
    assert source.calls == []


@pytest.mark.parametrize(
    ("mode", "call"),
    [
        ("pure_fiction", "hsr_endgame:pure_fiction"),
        ("stygian_onslaught", "genshin_endgame:stygian_onslaught"),
        ("deadly_assault", "zzz_endgame:deadly_assault"),
    ],
)
def test_a_mode_tells_its_game(mode, call):
    source = sample_source()

    make_tools(source, ALL_GAMES).run(GAME_ENDGAME, {"mode": mode})

    assert source.calls == [call]


@pytest.mark.parametrize(
    ("tool", "arguments", "call"),
    [
        (GAME_CHARACTERS, {}, "{game}_characters"),
        (GAME_ENDGAME, {}, "{game}_endgame:{modes}"),
        (GAME_EVENTS, {}, "{game}_calendar"),
    ],
)
@pytest.mark.parametrize("game", ALL_GAMES)
def test_a_given_game_reads_that_game(game, tool, arguments, call):
    source = sample_source()

    _, is_error = make_tools(source, ALL_GAMES).run(tool, {**arguments, "game": game})

    assert is_error is False
    assert source.calls == [call.format(game=game, modes=",".join(GAME_MODES[game]))]


@pytest.mark.parametrize(
    ("tool", "call"),
    [
        (GAME_CHARACTERS, "{game}_characters"),
        (GAME_ENDGAME, "{game}_endgame:{modes}"),
        (GAME_EVENTS, "{game}_calendar"),
    ],
)
@pytest.mark.parametrize("game", ALL_GAMES)
def test_the_only_configured_game_needs_no_game_argument(game, tool, call):
    source = sample_source()

    _, is_error = make_tools(source, (game,)).run(tool, {})

    assert is_error is False
    assert source.calls == [call.format(game=game, modes=",".join(GAME_MODES[game]))]


class HsrOnlySource:
    """A source with only the HSR methods, as HoyolabClient had them in v1.16."""

    def __init__(self) -> None:
        self._inner = sample_source()

    def hsr_characters(self) -> tuple[HsrCharacter, ...]:
        return self._inner.hsr_characters()

    def hsr_endgame(self, modes):
        return self._inner.hsr_endgame(modes)

    def hsr_calendar(self) -> HsrCalendar:
        return self._inner.hsr_calendar()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_an_answer_about_one_game_looks_up_no_other_game_s_source_method(tool):
    result, is_error = GameDataTools(HsrOnlySource(), HSR, now=lambda: NOW).run(tool, {})

    assert is_error is False, result


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (GAME_CHARACTERS, {}),
        (GAME_CHARACTERS, {"name": "Acheron"}),
        (GAME_CHARACTERS, {"name": "Acheorn"}),
        (GAME_ENDGAME, {}),
        (GAME_ENDGAME, {"mode": "pure_fiction"}),
        (GAME_EVENTS, {}),
    ],
)
def test_hsr_answers_byte_for_byte_alike_whichever_games_are_configured(tool, arguments):
    alone = make_tools(sample_source()).run(tool, arguments)
    among = make_tools(sample_source(), ALL_GAMES).run(tool, {**arguments, "game": "hsr"})

    assert among == alone


# --- results: shared fixtures ---------------------------------------------------------------

NETWORK_ERROR = GachaError("network", "HoYoLAB tidak bisa dihubungi. Cek koneksi internet.")
REFUSED_ERROR = GachaError("other", "HoYoLAB menolak permintaan HSR (kode -1).")


class Clock:
    """The injected local clock, moved forward by the tests."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


def hsr_character(
    name: str,
    *,
    rarity: int = 5,
    level: int = 80,
    eidolon: int = 0,
    path: str = "Nihility",
    element: str = "Lightning",
    light_cone: HsrLightCone | None = None,
    stats: tuple[tuple[str, str], ...] = (),
    relics: tuple[HsrRelic, ...] = (),
    memosprite: str | None = None,
) -> HsrCharacter:
    return HsrCharacter(
        name, rarity, level, eidolon, path, element, light_cone, stats, relics, memosprite
    )


def source_with(
    characters: tuple[HsrCharacter, ...] | GachaError = (),
    endgames: dict[str, HsrEndgame | GachaError] | None = None,
    hsr_calendar: HsrCalendar | GachaError = EMPTY_HSR_CALENDAR,
) -> FakeSource:
    return FakeSource(
        characters={"hsr": characters}, endgames=endgames, calendars={"hsr": hsr_calendar}
    )


def answer(tools: GameDataTools, tool: str, arguments: dict) -> dict:
    result, is_error = tools.run(tool, arguments)
    assert is_error is False, result
    return json.loads(result)


def compact(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


# --- game_characters for HSR: the roster ------------------------------------------------------

ACHERON = hsr_character(
    "Acheron",
    eidolon=2,
    light_cone=HsrLightCone("Along the Passing Shore", 80, 1, 5),
    stats=(("HP", "3512"), ("ATK", "3871"), ("CRIT Rate", "65.2%"), ("CRIT DMG", "184.6%")),
    relics=(HsrRelic("Head", "HP", "705", 15), HsrRelic("Body", "CRIT DMG", "64.8%", 15)),
)
CASTORICE = hsr_character(
    "Castorice", level=70, path="Remembrance", element="Quantum", memosprite="Pollux"
)
FIREFLY = hsr_character(
    "Firefly",
    path="Destruction",
    element="Fire",
    light_cone=HsrLightCone("Whereabouts Should Dreams Rest", 80, 1, 5),
)
PELA = hsr_character(
    "Pela",
    rarity=4,
    eidolon=6,
    element="Ice",
    light_cone=HsrLightCone("Resolution Shines As Pearls of Sweat", 80, 5, 4),
)
ROSTER = (PELA, CASTORICE, FIREFLY, ACHERON)
COLUMNS = [
    "name",
    "rarity",
    "level",
    "eidolon",
    "path",
    "element",
    "light_cone",
    "lc_level",
    "superimposition",
]


def test_the_roster_is_one_row_per_character_by_rarity_then_level_then_name():
    source = source_with(ROSTER)

    data = answer(make_tools(source), GAME_CHARACTERS, {})

    assert data == {
        "as_of": "2026-10-15 20:00",
        "count": 4,
        "columns": COLUMNS,
        "rows": [
            ["Acheron", 5, 80, 2, "Nihility", "Lightning", "Along the Passing Shore", 80, 1],
            ["Firefly", 5, 80, 0, "Destruction", "Fire", "Whereabouts Should Dreams Rest", 80, 1],
            ["Castorice", 5, 70, 0, "Remembrance", "Quantum", None, None, None],
            ["Pela", 4, 80, 6, "Nihility", "Ice", "Resolution Shines As Pearls of Sweat", 80, 5],
        ],
    }
    assert source.calls == ["hsr_characters"]


def test_results_are_compact_json_that_keeps_non_ascii_names():
    tools = make_tools(source_with((hsr_character("Dan Heng • Imbibitor Lunae"),)))

    result, _ = tools.run(GAME_CHARACTERS, {})

    assert "Dan Heng • Imbibitor Lunae" in result
    assert result == compact(json.loads(result))


def test_a_roster_beyond_120_characters_keeps_the_first_120_rows_and_says_so():
    characters = tuple(hsr_character(f"Sample {i:03}", level=i % 80 + 1) for i in range(130))

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {})

    levels = sorted((c.level for c in characters), reverse=True)
    assert data["count"] == 130
    assert [row[2] for row in data["rows"]] == levels[:120]
    assert data["truncated"] is True


ROSTER_NAMES = [
    "Dan Heng • Imbibitor Lunae",
    "Topaz & Numby",
    "Silver Wolf",
    "Black Swan",
    "Trailblazer",
    "March 7th",
    "Dr. Ratio",
    "Jing Yuan",
    "Aventurine",
    "Hyacine",
]
LIGHT_CONES = [
    "Before the Tutorial Mission Starts",
    "Whereabouts Should Dreams Rest",
    "Long May Rainbows Adorn the Sky",
    "Sailing Towards a Second Life",
    "Make Farewells More Beautiful",
    "Resolution Shines As Pearls of Sweat",
]
PATHS = ["Remembrance", "Preservation", "Destruction", "Erudition", "Nihility", "The Hunt"]
ELEMENTS = ["Lightning", "Imaginary", "Physical", "Quantum", "Fire", "Ice", "Wind"]


def test_an_80_character_roster_fits_in_8_kb():
    characters = tuple(
        hsr_character(
            f"{ROSTER_NAMES[i % len(ROSTER_NAMES)]} {i}",
            rarity=4 + i % 2,
            level=80 - i % 3,
            eidolon=i % 7,
            path=PATHS[i % len(PATHS)],
            element=ELEMENTS[i % len(ELEMENTS)],
            light_cone=HsrLightCone(LIGHT_CONES[i % len(LIGHT_CONES)], 80, 1 + i % 5, 5),
        )
        for i in range(80)
    )

    result, is_error = make_tools(source_with(characters)).run(GAME_CHARACTERS, {})

    assert is_error is False
    assert len(json.loads(result)["rows"]) == 80
    assert utf8_size(result) <= 8_000


# --- game_characters for HSR: one character ---------------------------------------------------


def test_a_named_character_comes_with_light_cone_stats_and_relics():
    data = answer(make_tools(source_with(ROSTER)), GAME_CHARACTERS, {"name": "acheron"})

    assert data == {
        "as_of": "2026-10-15 20:00",
        "query": "acheron",
        "characters": [
            {
                "name": "Acheron",
                "rarity": 5,
                "level": 80,
                "eidolon": 2,
                "path": "Nihility",
                "element": "Lightning",
                "light_cone": {
                    "name": "Along the Passing Shore",
                    "level": 80,
                    "superimposition": 1,
                    "rarity": 5,
                },
                "stats": {"HP": "3512", "ATK": "3871", "CRIT Rate": "65.2%", "CRIT DMG": "184.6%"},
                "relics": [
                    {"piece": "Head", "main": "HP", "value": "705", "level": 15},
                    {"piece": "Body", "main": "CRIT DMG", "value": "64.8%", "level": 15},
                ],
            }
        ],
    }


def test_a_character_without_a_light_cone_leaves_it_out_and_names_the_memosprite():
    data = answer(make_tools(source_with(ROSTER)), GAME_CHARACTERS, {"name": "Castorice"})

    assert data["characters"] == [
        {
            "name": "Castorice",
            "rarity": 5,
            "level": 70,
            "eidolon": 0,
            "path": "Remembrance",
            "element": "Quantum",
            "stats": {},
            "relics": [],
            "memosprite": "Pollux",
        }
    ]


NAMED = (
    hsr_character("Dan Heng", rarity=4, path="The Hunt", element="Wind"),
    hsr_character("Dan Heng • Imbibitor Lunae", path="Destruction", element="Imaginary"),
    hsr_character("Topaz & Numby", path="The Hunt", element="Fire"),
    hsr_character("Dr. Ratio", path="The Hunt", element="Imaginary"),
    hsr_character("Ruan Mei", path="Harmony", element="Ice"),
    FIREFLY,
)


def matched_names(tools: GameDataTools, query: str, game: str | None = None) -> list[str]:
    arguments = {"name": query} if game is None else {"game": game, "name": query}
    return [c["name"] for c in answer(tools, GAME_CHARACTERS, arguments)["characters"]]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("DR RATIO", "Dr. Ratio"),
        ("dr.ratio", "Dr. Ratio"),
        ("topaz numby", "Topaz & Numby"),
        ("Dan Heng Imbibitor Lunae", "Dan Heng • Imbibitor Lunae"),
        ("Ruán Méi", "Ruan Mei"),
        ("Ｆｉｒｅｆｌｙ", "Firefly"),
    ],
)
def test_names_match_whatever_the_case_accents_punctuation_spaces_or_width(query, expected):
    assert matched_names(make_tools(source_with(NAMED)), query) == [expected]


def test_an_exact_name_wins_over_names_that_contain_it():
    assert matched_names(make_tools(source_with(NAMED)), "dan heng") == ["Dan Heng"]


def test_part_of_a_name_finds_the_character_that_contains_it():
    tools = make_tools(source_with(NAMED))

    assert matched_names(tools, "imbibitor") == ["Dan Heng • Imbibitor Lunae"]


@pytest.mark.parametrize("query", ["March 7th", "march7th", "march"])
def test_both_march_7th_paths_come_back_together_told_apart_by_path(query):
    characters = (
        ACHERON,
        hsr_character("March 7th", rarity=4, level=70, path="The Hunt", element="Imaginary"),
        hsr_character("March 7th", rarity=4, path="Preservation", element="Ice"),
    )

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": query})

    assert [(c["name"], c["path"]) for c in data["characters"]] == [
        ("March 7th", "Preservation"),
        ("March 7th", "The Hunt"),
    ]


def test_the_trailblazer_paths_come_back_together():
    characters = (
        hsr_character("Trailblazer", path="Destruction", element="Physical"),
        FIREFLY,
        hsr_character("Trailblazer", path="Preservation", element="Fire"),
        hsr_character("Trailblazer", path="Remembrance", element="Ice"),
    )

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "trailblazer"})

    assert [c["path"] for c in data["characters"]] == ["Destruction", "Preservation", "Remembrance"]


TRAILBLAZERS = (
    hsr_character("Trailblazer", path="Destruction", element="Physical"),
    hsr_character("Trailblazer", path="Preservation", element="Fire"),
    hsr_character("Trailblazer", path="Remembrance", element="Ice"),
    hsr_character("Trailblazer", path="Harmony", element="Imaginary"),
)


def test_a_fourth_trailblazer_path_is_named_under_more_so_it_can_be_asked_for():
    characters = (*TRAILBLAZERS, FIREFLY)

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "Trailblazer"})

    assert [c["path"] for c in data["characters"]] == ["Destruction", "Preservation", "Remembrance"]
    assert data["more"] == [{"name": "Trailblazer", "path": "Harmony"}]


@pytest.mark.parametrize(
    "query", ["Trailblazer Harmony", "harmony trailblazer", "Trailblazer (Harmony)"]
)
def test_a_name_with_its_path_gives_that_one_character(query):
    characters = (*TRAILBLAZERS, FIREFLY)

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": query})

    assert [(c["name"], c["path"]) for c in data["characters"]] == [("Trailblazer", "Harmony")]
    assert "more" not in data


def test_a_two_word_path_names_one_march_7th():
    characters = (
        hsr_character("March 7th", rarity=4, path="The Hunt", element="Imaginary"),
        hsr_character("March 7th", rarity=4, path="Preservation", element="Ice"),
    )

    data = answer(
        make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "March 7th The Hunt"}
    )

    assert [(c["name"], c["path"]) for c in data["characters"]] == [("March 7th", "The Hunt")]


def test_at_most_three_characters_come_back_and_more_names_the_others():
    characters = tuple(hsr_character(f"Sample Hero {i}") for i in range(5))

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "sample hero"})

    assert [c["name"] for c in data["characters"]] == [f"Sample Hero {i}" for i in range(3)]
    assert data["more"] == [
        {"name": "Sample Hero 3", "path": "Nihility"},
        {"name": "Sample Hero 4", "path": "Nihility"},
    ]


def test_more_names_at_most_six_others():
    characters = tuple(hsr_character(f"Sample Hero {i:02}") for i in range(12))

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "sample hero"})

    assert [entry["name"] for entry in data["more"]] == [f"Sample Hero {i:02}" for i in range(3, 9)]


def test_a_misheard_name_gives_close_names_instead_of_an_error():
    result, is_error = make_tools(source_with(ROSTER)).run(GAME_CHARACTERS, {"name": "Aceron"})

    assert is_error is False
    assert json.loads(result) == {
        "as_of": "2026-10-15 20:00",
        "query": "Aceron",
        "characters": [],
        "close": ["Acheron"],
    }


def test_close_names_name_each_character_once():
    characters = (
        hsr_character("March 7th", rarity=4, path="Preservation"),
        hsr_character("March 7th", rarity=4, path="The Hunt"),
        ACHERON,
    )

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "marsh 7th"})

    assert data["close"] == ["March 7th"]


def test_at_most_three_close_names():
    characters = tuple(hsr_character(f"Sample Hero {letter}") for letter in "ABCD")

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "sample hera"})

    assert data["characters"] == []
    assert len(data["close"]) == 3


def test_a_name_without_letters_or_digits_matches_nothing():
    data = answer(make_tools(source_with(ROSTER)), GAME_CHARACTERS, {"name": "?!"})

    assert data["characters"] == []
    assert data["close"] == []


LONG_STATS = (
    ("HP", "4012"),
    ("ATK", "3871"),
    ("DEF", "1203"),
    ("SPD", "134.2"),
    ("CRIT Rate", "78.4%"),
    ("CRIT DMG", "212.6%"),
    ("Break Effect", "164.0%"),
    ("Outgoing Healing Boost", "34.6%"),
    ("Energy Regeneration Rate", "119.4%"),
    ("Effect Hit Rate", "67.2%"),
    ("Effect RES", "40.0%"),
    ("Imaginary DMG Boost", "38.8%"),
    ("Max Energy", "140"),
    ("Elation", "42.0%"),
    ("Sample Stat Fifteen", "1.0%"),
    ("Sample Stat Sixteen", "2.0%"),
    ("Sample Stat Seventeen", "3.0%"),
    ("Sample Stat Eighteen", "4.0%"),
    ("Sample Stat Nineteen", "5.0%"),
    ("Sample Stat Twenty", "6.0%"),
)
LONG_RELICS = tuple(
    HsrRelic(piece, main, "38.8%", 15)
    for piece, main in [
        ("Head", "HP"),
        ("Hands", "ATK"),
        ("Body", "Outgoing Healing Boost"),
        ("Feet", "SPD"),
        ("Planar Sphere", "Imaginary DMG Boost"),
        ("Link Rope", "Energy Regeneration Rate"),
        ("Sample Piece Seven", "Sample Main Stat"),
        ("Sample Piece Eight", "Sample Main Stat"),
    ]
)


def test_each_named_character_keeps_14_stats_and_6_relics_within_1_5_kb():
    characters = tuple(
        hsr_character(
            "Trailblazer",
            eidolon=6,
            path=path,
            element="Imaginary",
            light_cone=HsrLightCone("Before the Tutorial Mission Starts", 80, 5, 4),
            stats=LONG_STATS,
            relics=LONG_RELICS,
            memosprite="Sample Memosprite of a Long Name",
        )
        for path in ("Remembrance", "Preservation", "Destruction")
    )

    data = answer(make_tools(source_with(characters)), GAME_CHARACTERS, {"name": "Trailblazer"})

    assert len(data["characters"]) == 3
    for entry in data["characters"]:
        assert entry["stats"] == dict(LONG_STATS[:14])
        assert [relic["piece"] for relic in entry["relics"]] == [
            "Head",
            "Hands",
            "Body",
            "Feet",
            "Planar Sphere",
            "Link Rope",
        ]
        assert utf8_size(compact(entry)) <= 1_500


# --- game_characters for Genshin Impact ---------------------------------------------------------

FURINA = genshin_character(
    "Furina",
    ref=10000089,
    constellation=2,
    weapon=GenshinWeapon("Splendor of Tranquil Waters", 90, 1, 5),
)
NAHIDA = genshin_character(
    "Nahida",
    ref=10000073,
    level=80,
    element="Dendro",
    weapon_type="Catalyst",
    weapon=GenshinWeapon("A Thousand Floating Dreams", 90, 1, 5),
)
TRAVELER = genshin_character(
    "Traveler",
    ref=10000005,
    element="Anemo",
    weapon=GenshinWeapon("Mistsplitter Reforged", 90, 1, 5),
)
VENTI = genshin_character(
    "Venti",
    ref=10000022,
    level=80,
    element="Anemo",
    weapon_type="Bow",
    weapon=GenshinWeapon("Elegy for the End", 90, 1, 5),
)
XIANGLING = genshin_character(
    "Xiangling",
    ref=10000023,
    rarity=4,
    constellation=6,
    element="Pyro",
    weapon_type="Polearm",
    weapon=GenshinWeapon("The Catch", 90, 5, 4),
)
GENSHIN_ROSTER = (XIANGLING, NAHIDA, TRAVELER, FURINA)
GENSHIN_COLUMNS = [
    "name",
    "rarity",
    "level",
    "constellation",
    "element",
    "weapon_type",
    "weapon",
    "weapon_level",
    "refinement",
]
FURINA_DETAIL = genshin_detail(
    FURINA,
    weapon_stats=(("Base ATK", "542"), ("CRIT DMG", "88.2%")),
    stats=(("Max HP", "38214"), ("ATK", "964"), ("CRIT Rate", "62.1%"), ("CRIT DMG", "201.4%")),
    artifacts=(
        GenshinArtifact("Flower of Life", "Golden Troupe", "HP", "4780", 20),
        GenshinArtifact("Plume of Death", "Golden Troupe", "ATK", "311", 20),
        GenshinArtifact("Sands of Eon", "Golden Troupe", "HP", "46.6%", 20),
        GenshinArtifact("Goblet of Eonothem", "Golden Troupe", "HP", "46.6%", 20),
        GenshinArtifact("Circlet of Logos", "Marechaussee Hunter", "CRIT Rate", "31.1%", 20),
    ),
    sets=(("Golden Troupe", 4),),
)
FURINA_JSON = {
    "name": "Furina",
    "rarity": 5,
    "level": 90,
    "constellation": 2,
    "element": "Hydro",
    "weapon_type": "Sword",
    "weapon": {
        "name": "Splendor of Tranquil Waters",
        "level": 90,
        "refinement": 1,
        "rarity": 5,
        "stats": {"Base ATK": "542", "CRIT DMG": "88.2%"},
    },
    "stats": {"Max HP": "38214", "ATK": "964", "CRIT Rate": "62.1%", "CRIT DMG": "201.4%"},
    "artifacts": [
        {
            "piece": "Flower of Life",
            "set": "Golden Troupe",
            "main": "HP",
            "value": "4780",
            "level": 20,
        },
        {
            "piece": "Plume of Death",
            "set": "Golden Troupe",
            "main": "ATK",
            "value": "311",
            "level": 20,
        },
        {
            "piece": "Sands of Eon",
            "set": "Golden Troupe",
            "main": "HP",
            "value": "46.6%",
            "level": 20,
        },
        {
            "piece": "Goblet of Eonothem",
            "set": "Golden Troupe",
            "main": "HP",
            "value": "46.6%",
            "level": 20,
        },
        {
            "piece": "Circlet of Logos",
            "set": "Marechaussee Hunter",
            "main": "CRIT Rate",
            "value": "31.1%",
            "level": 20,
        },
    ],
    "sets": {"Golden Troupe": 4},
}


def genshin_source(
    characters: tuple[GenshinCharacter, ...] | GachaError = GENSHIN_ROSTER,
    details: tuple[GenshinCharacterDetail, ...] | GachaError = (),
) -> FakeSource:
    return FakeSource(characters={"genshin": characters}, details={"genshin": details})


def test_a_genshin_roster_is_one_row_per_character_with_constellation_and_weapon():
    source = genshin_source()

    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, GENSHIN)

    assert data == {
        "as_of": "2026-10-15 20:00",
        "count": 4,
        "columns": GENSHIN_COLUMNS,
        "rows": [
            ["Furina", 5, 90, 2, "Hydro", "Sword", "Splendor of Tranquil Waters", 90, 1],
            ["Traveler", 5, 90, 0, "Anemo", "Sword", "Mistsplitter Reforged", 90, 1],
            ["Nahida", 5, 80, 0, "Dendro", "Catalyst", "A Thousand Floating Dreams", 90, 1],
            ["Xiangling", 4, 90, 6, "Pyro", "Polearm", "The Catch", 90, 5],
        ],
    }
    assert source.calls == ["genshin_characters"]


def test_a_named_genshin_character_comes_with_weapon_stats_artifacts_and_sets():
    source = genshin_source(details=(FURINA_DETAIL,))

    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, {**GENSHIN, "name": "furina"})

    assert data == {"as_of": "2026-10-15 20:00", "query": "furina", "characters": [FURINA_JSON]}
    assert source.calls == ["genshin_characters", "genshin_character_details:10000089"]


@pytest.mark.parametrize("query", ["Traveler Anemo", "anemo traveler"])
def test_a_genshin_name_with_its_element_gives_that_one_character(query):
    traveler_geo = replace(TRAVELER, ref=10000007, element="Geo")
    source = genshin_source((TRAVELER, traveler_geo, VENTI))

    tools = make_tools(source, ALL_GAMES)

    assert matched_names(tools, query, "genshin") == ["Traveler"]
    assert source.calls[-1] == "genshin_character_details:10000005"


def test_genshin_matches_beyond_three_are_named_with_their_element_and_get_no_detail():
    characters = tuple(
        genshin_character(f"Sample Hero {i}", ref=100 + i, element="Cryo") for i in range(5)
    )
    source = genshin_source(characters, tuple(genshin_detail(c) for c in characters))

    data = answer(
        make_tools(source, ALL_GAMES), GAME_CHARACTERS, {**GENSHIN, "name": "sample hero"}
    )

    assert [c["name"] for c in data["characters"]] == [f"Sample Hero {i}" for i in range(3)]
    assert data["more"] == [
        {"name": "Sample Hero 3", "element": "Cryo"},
        {"name": "Sample Hero 4", "element": "Cryo"},
    ]
    assert source.calls == ["genshin_characters", "genshin_character_details:100,101,102"]


def test_a_genshin_name_that_matches_nothing_asks_for_no_detail():
    source = genshin_source(details=(FURINA_DETAIL,))

    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, {**GENSHIN, "name": "Furnia"})

    assert data == {
        "as_of": "2026-10-15 20:00",
        "query": "Furnia",
        "characters": [],
        "close": ["Furina"],
    }
    assert source.calls == ["genshin_characters"]


def test_a_detail_is_kept_per_character_and_only_the_missing_ones_are_asked_for():
    clock = Clock()
    nahida_detail = genshin_detail(NAHIDA)
    source = genshin_source((FURINA, NAHIDA, VENTI), (FURINA_DETAIL, nahida_detail))
    tools = GameDataTools(source, ALL_GAMES, now=clock)

    tools.run(GAME_CHARACTERS, {**GENSHIN, "name": "Furina"})
    clock.advance(minutes=10)
    both = answer(tools, GAME_CHARACTERS, {**GENSHIN, "name": "na"})

    assert [c["name"] for c in both["characters"]] == ["Furina", "Nahida"]
    assert both["as_of"] == "2026-10-15 20:00"  # the oldest read among what is shown
    assert source.calls == [
        "genshin_characters",
        "genshin_character_details:10000089",
        "genshin_character_details:10000073",
    ]


def test_a_kept_detail_is_read_again_after_15_minutes():
    clock = Clock()
    source = genshin_source(details=(FURINA_DETAIL,))
    tools = GameDataTools(source, ALL_GAMES, now=clock)

    tools.run(GAME_CHARACTERS, {**GENSHIN, "name": "Furina"})
    clock.advance(minutes=14.5)
    tools.run(GAME_CHARACTERS, {**GENSHIN, "name": "Furina"})
    clock.advance(minutes=0.5)
    data = answer(tools, GAME_CHARACTERS, {**GENSHIN, "name": "Furina"})

    assert source.calls == [
        "genshin_characters",
        "genshin_character_details:10000089",
        "genshin_characters",
        "genshin_character_details:10000089",
    ]
    assert data["as_of"] == "2026-10-15 20:15"


def test_a_failed_detail_is_an_error_result_and_is_asked_for_again():
    source = genshin_source(details=NETWORK_ERROR)
    tools = make_tools(source, ALL_GAMES)

    result, is_error = tools.run(GAME_CHARACTERS, {**GENSHIN, "name": "Furina"})
    source.details["genshin"] = (FURINA_DETAIL,)
    data = answer(tools, GAME_CHARACTERS, {**GENSHIN, "name": "Furina"})

    assert is_error is True
    assert json.loads(result) == {"ok": False, "message": NETWORK_ERROR.message}
    assert data["characters"] == [FURINA_JSON]
    assert source.calls == [
        "genshin_characters",
        "genshin_character_details:10000089",
        "genshin_character_details:10000089",
    ]


def test_a_genshin_match_hoyolab_gives_no_detail_for_shows_only_its_roster_facts():
    data = answer(
        make_tools(genshin_source(), ALL_GAMES), GAME_CHARACTERS, {**GENSHIN, "name": "Nahida"}
    )

    assert data["characters"] == [
        {
            "name": "Nahida",
            "rarity": 5,
            "level": 80,
            "constellation": 0,
            "element": "Dendro",
            "weapon_type": "Catalyst",
            "weapon": {
                "name": "A Thousand Floating Dreams",
                "level": 90,
                "refinement": 1,
                "rarity": 5,
            },
        }
    ]


def test_a_match_hoyolab_gave_no_detail_for_is_asked_for_again():
    source = genshin_source()
    tools = make_tools(source, ALL_GAMES)

    tools.run(GAME_CHARACTERS, {**GENSHIN, "name": "Nahida"})
    tools.run(GAME_CHARACTERS, {**GENSHIN, "name": "Nahida"})

    assert source.calls == [
        "genshin_characters",
        "genshin_character_details:10000073",
        "genshin_character_details:10000073",
    ]


GENSHIN_NAMES = [
    "Kaedehara Kazuha",
    "Sangonomiya Kokomi",
    "Raiden Shogun",
    "Yumemizuki Mizuki",
    "Arataki Itto",
    "Kamisato Ayaka",
    "Furina",
    "Hu Tao",
    "Xiangling",
    "Nahida",
]
GENSHIN_WEAPONS = [
    "Splendor of Tranquil Waters",
    "Redhorn Stonethresher",
    "A Thousand Floating Dreams",
    "Everlasting Moonglow",
    "Engulfing Lightning",
    "Mistsplitter Reforged",
    "Favonius Sword",
]
GENSHIN_ELEMENTS = ["Anemo", "Electro", "Hydro", "Geo", "Cryo", "Dendro", "Pyro"]
WEAPON_TYPES = ["Catalyst", "Claymore", "Polearm", "Sword", "Bow"]


def test_a_110_character_genshin_roster_fits_in_9_kb():
    characters = tuple(
        genshin_character(
            f"{GENSHIN_NAMES[i % len(GENSHIN_NAMES)]} {i}",
            ref=i,
            rarity=4 + i % 2,
            level=90 - i % 3,
            constellation=i % 7,
            element=GENSHIN_ELEMENTS[i % len(GENSHIN_ELEMENTS)],
            weapon_type=WEAPON_TYPES[i % len(WEAPON_TYPES)],
            weapon=GenshinWeapon(GENSHIN_WEAPONS[i % len(GENSHIN_WEAPONS)], 90, 1 + i % 5, 5),
        )
        for i in range(110)
    )

    result, is_error = make_tools(genshin_source(characters), ALL_GAMES).run(
        GAME_CHARACTERS, GENSHIN
    )

    assert is_error is False
    assert len(json.loads(result)["rows"]) == 110
    assert utf8_size(result) <= 9_000


GENSHIN_LONG_STATS = (
    ("Max HP", "21894"),
    ("ATK", "2140"),
    ("DEF", "903"),
    ("Elemental Mastery", "963"),
    ("CRIT Rate", "45.2%"),
    ("CRIT DMG", "120.4%"),
    ("Healing Bonus", "0.0%"),
    ("Incoming Healing Bonus", "0.0%"),
    ("Energy Recharge", "178.3%"),
    ("CD Reduction", "0.0%"),
    ("Shield Strength", "0.0%"),
    ("Anemo DMG Bonus", "15.0%"),
    ("Physical DMG Bonus", "0.0%"),
    ("Pyro DMG Bonus", "0.0%"),
    ("Sample Stat Fifteen", "1.0%"),
    ("Sample Stat Sixteen", "2.0%"),
)
GENSHIN_LONG_ARTIFACTS = tuple(
    GenshinArtifact(piece, "Nighttime Whispers in the Echoing Woods", main, "187", 20)
    for piece, main in [
        ("Flower of Life", "HP"),
        ("Plume of Death", "ATK"),
        ("Sands of Eon", "Elemental Mastery"),
        ("Goblet of Eonothem", "Elemental Mastery"),
        ("Circlet of Logos", "Elemental Mastery"),
        ("Sample Piece Six", "Sample Main Stat"),
        ("Sample Piece Seven", "Sample Main Stat"),
    ]
)


def test_each_named_genshin_character_keeps_14_stats_and_5_artifacts_within_1_5_kb():
    characters = tuple(
        genshin_character(
            f"Kaedehara Kazuha {i}",
            ref=200 + i,
            constellation=6,
            element="Anemo",
            weapon=GenshinWeapon("Splendor of Tranquil Waters", 90, 5, 5),
        )
        for i in range(3)
    )
    details = tuple(
        genshin_detail(
            c,
            weapon_stats=(("Base ATK", "542"), ("Elemental Mastery", "265")),
            stats=GENSHIN_LONG_STATS,
            artifacts=GENSHIN_LONG_ARTIFACTS,
            sets=(("Nighttime Whispers in the Echoing Woods", 4), ("Gilded Dreams", 2)),
        )
        for c in characters
    )

    data = answer(
        make_tools(genshin_source(characters, details), ALL_GAMES),
        GAME_CHARACTERS,
        {**GENSHIN, "name": "Kaedehara"},
    )

    assert len(data["characters"]) == 3
    for entry in data["characters"]:
        assert entry["stats"] == dict(GENSHIN_LONG_STATS[:14])
        assert [artifact["piece"] for artifact in entry["artifacts"]] == [
            "Flower of Life",
            "Plume of Death",
            "Sands of Eon",
            "Goblet of Eonothem",
            "Circlet of Logos",
        ]
        assert utf8_size(compact(entry)) <= 1_500


# --- game_characters for Zenless Zone Zero ------------------------------------------------------

ELLEN = zzz_agent("Ellen", ref=1191, full_name="Ellen Joe", mindscape=2)
ZHU_YUAN = zzz_agent("Zhu Yuan", ref=1241, level=50, element="Ether")
ANBY = zzz_agent(
    "Anby",
    ref=1011,
    full_name="Anby Demara",
    rarity="A",
    mindscape=6,
    element="Electric",
    specialty="Stun",
)
NICOLE = zzz_agent(
    "Nicole", ref=1031, full_name="Nicole Demara", rarity="A", element="Ether", specialty="Support"
)
ZZZ_ROSTER = (ANBY, ZHU_YUAN, NICOLE, ELLEN)
ELLEN_DETAIL = zzz_detail(
    ELLEN,
    w_engine=ZzzWEngine("Deep Sea Visitor", 60, 1, "S"),
    stats=(("HP", "8104"), ("ATK", "3120"), ("CRIT Rate", "68.4%"), ("CRIT DMG", "152.0%")),
    discs=(
        ZzzDisc(1, "Polar Metal", "HP", "2200", 15),
        ZzzDisc(2, "Polar Metal", "ATK", "316", 15),
        ZzzDisc(3, "Polar Metal", "DEF", "184", 15),
        ZzzDisc(4, "Polar Metal", "CRIT Rate", "24%", 15),
        ZzzDisc(5, "Woodpecker Electro", "Ice DMG Bonus", "30%", 15),
        ZzzDisc(6, "Woodpecker Electro", "ATK", "30%", 15),
    ),
    sets=(("Polar Metal", 4), ("Woodpecker Electro", 2)),
)
ELLEN_JSON = {
    "name": "Ellen",
    "rarity": "S",
    "level": 60,
    "mindscape": 2,
    "element": "Ice",
    "specialty": "Attack",
    "w_engine": {"name": "Deep Sea Visitor", "level": 60, "refinement": 1, "rarity": "S"},
    "stats": {"HP": "8104", "ATK": "3120", "CRIT Rate": "68.4%", "CRIT DMG": "152.0%"},
    "discs": [
        {"slot": 1, "set": "Polar Metal", "main": "HP", "value": "2200", "level": 15},
        {"slot": 2, "set": "Polar Metal", "main": "ATK", "value": "316", "level": 15},
        {"slot": 3, "set": "Polar Metal", "main": "DEF", "value": "184", "level": 15},
        {"slot": 4, "set": "Polar Metal", "main": "CRIT Rate", "value": "24%", "level": 15},
        {
            "slot": 5,
            "set": "Woodpecker Electro",
            "main": "Ice DMG Bonus",
            "value": "30%",
            "level": 15,
        },
        {"slot": 6, "set": "Woodpecker Electro", "main": "ATK", "value": "30%", "level": 15},
    ],
    "sets": {"Polar Metal": 4, "Woodpecker Electro": 2},
}


def zzz_source(
    agents: tuple[ZzzAgent, ...] | GachaError = ZZZ_ROSTER,
    details: tuple[ZzzAgentDetail, ...] | GachaError = (),
) -> FakeSource:
    return FakeSource(characters={"zzz": agents}, details={"zzz": details})


def test_a_zzz_roster_is_one_row_per_agent_s_rank_first():
    source = zzz_source()

    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, ZZZ)

    assert data == {
        "as_of": "2026-10-15 20:00",
        "count": 4,
        "columns": ["name", "rarity", "level", "mindscape", "element", "specialty"],
        "rows": [
            ["Ellen", "S", 60, 2, "Ice", "Attack"],
            ["Zhu Yuan", "S", 50, 0, "Ether", "Attack"],
            ["Anby", "A", 60, 6, "Electric", "Stun"],
            ["Nicole", "A", 60, 0, "Ether", "Support"],
        ],
    }
    assert source.calls == ["zzz_characters"]


def test_a_named_zzz_agent_comes_with_her_w_engine_drive_discs_and_sets():
    source = zzz_source(details=(ELLEN_DETAIL,))

    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, {**ZZZ, "name": "ellen"})

    assert data == {"as_of": "2026-10-15 20:00", "query": "ellen", "characters": [ELLEN_JSON]}
    assert source.calls == ["zzz_characters", "zzz_character_details:1191"]


def test_a_zzz_agent_without_a_w_engine_leaves_it_out():
    source = zzz_source(details=(zzz_detail(ZHU_YUAN),))

    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, {**ZZZ, "name": "Zhu Yuan"})

    assert data["characters"] == [
        {
            "name": "Zhu Yuan",
            "rarity": "S",
            "level": 50,
            "mindscape": 0,
            "element": "Ether",
            "specialty": "Attack",
            "stats": {},
            "discs": [],
            "sets": {},
        }
    ]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("Ellen Joe", ["Ellen"]), ("joe", ["Ellen"]), ("demara", ["Anby", "Nicole"])],
)
def test_a_zzz_agent_is_found_by_her_full_name_or_part_of_it(query, expected):
    tools = make_tools(zzz_source(), ALL_GAMES)

    assert matched_names(tools, query, "zzz") == expected


def test_zzz_matches_beyond_three_are_named_under_more():
    agents = tuple(zzz_agent(f"Sample Agent {i}", ref=300 + i) for i in range(4))

    data = answer(
        make_tools(zzz_source(agents), ALL_GAMES), GAME_CHARACTERS, {**ZZZ, "name": "sample"}
    )

    assert data["more"] == [{"name": "Sample Agent 3"}]


def test_a_zzz_match_hoyolab_gives_no_detail_for_shows_only_its_roster_facts():
    data = answer(make_tools(zzz_source(), ALL_GAMES), GAME_CHARACTERS, {**ZZZ, "name": "Anby"})

    assert data["characters"] == [
        {
            "name": "Anby",
            "rarity": "A",
            "level": 60,
            "mindscape": 6,
            "element": "Electric",
            "specialty": "Stun",
        }
    ]


def test_a_60_agent_zzz_roster_fits_in_4_kb():
    specialties = ["Attack", "Anomaly", "Stun", "Support", "Defense", "Rupture"]
    elements = ["Physical", "Electric", "Ether", "Fire", "Ice"]
    agents = tuple(
        zzz_agent(
            f"Sample Agent {i}",
            ref=i,
            rarity="SA"[i % 2],
            level=60 - i % 3,
            mindscape=i % 7,
            element=elements[i % len(elements)],
            specialty=specialties[i % len(specialties)],
        )
        for i in range(60)
    )

    result, is_error = make_tools(zzz_source(agents), ALL_GAMES).run(GAME_CHARACTERS, ZZZ)

    assert is_error is False
    assert len(json.loads(result)["rows"]) == 60
    assert utf8_size(result) <= 4_000


def test_each_named_zzz_agent_keeps_14_stats_and_6_drive_discs_within_1_5_kb():
    agents = tuple(
        zzz_agent(f"Hoshimi Miyabi {i}", ref=400 + i, full_name=f"Hoshimi Miyabi {i}")
        for i in range(3)
    )
    long_stats = (
        *GENSHIN_LONG_STATS[:12],
        ("Automatic Adrenaline Accumulation", "2.4"),
        ("Anomaly Proficiency", "238"),
        ("Sample Stat Fifteen", "1.0%"),
    )
    discs = tuple(
        ZzzDisc(slot, "Branch & Blade Song", "Anomaly Proficiency", "92", 15)
        for slot in range(1, 9)
    )
    details = tuple(
        zzz_detail(
            agent,
            w_engine=ZzzWEngine("Hailstorm Shrine", 60, 5, "S"),
            stats=long_stats,
            discs=discs,
            sets=(("Branch & Blade Song", 4), ("Woodpecker Electro", 2)),
        )
        for agent in agents
    )

    data = answer(
        make_tools(zzz_source(agents, details), ALL_GAMES),
        GAME_CHARACTERS,
        {**ZZZ, "name": "Miyabi"},
    )

    assert len(data["characters"]) == 3
    for entry in data["characters"]:
        assert entry["stats"] == dict(long_stats[:14])
        assert [disc["slot"] for disc in entry["discs"]] == [1, 2, 3, 4, 5, 6]
        assert utf8_size(compact(entry)) <= 1_500


@pytest.mark.parametrize(
    ("arguments", "source"),
    [
        (GENSHIN, genshin_source(tuple(genshin_character(f"Hero {i}", ref=i) for i in range(130)))),
        (ZZZ, zzz_source(tuple(zzz_agent(f"Agent {i}", ref=i) for i in range(130)))),
    ],
)
def test_a_genshin_or_zzz_roster_beyond_120_keeps_120_rows_and_says_so(arguments, source):
    data = answer(make_tools(source, ALL_GAMES), GAME_CHARACTERS, arguments)

    assert data["count"] == 130
    assert len(data["rows"]) == 120
    assert data["truncated"] is True


def test_no_result_holds_a_character_s_ref():
    source = FakeSource(
        characters={"genshin": GENSHIN_ROSTER, "zzz": ZZZ_ROSTER},
        details={"genshin": (FURINA_DETAIL,), "zzz": (ELLEN_DETAIL,)},
    )
    tools = make_tools(source, ALL_GAMES)

    results = [
        tools.run(GAME_CHARACTERS, arguments)[0]
        for arguments in [
            GENSHIN,
            ZZZ,
            {**GENSHIN, "name": "Furina"},
            {**GENSHIN, "name": "Nahida"},
            {**ZZZ, "name": "Ellen"},
            {**ZZZ, "name": "Demara"},
        ]
    ]

    refs = [c.ref for c in GENSHIN_ROSTER] + [a.ref for a in ZZZ_ROSTER]
    for result in results:
        for ref in refs:
            assert str(ref) not in result


# --- game_endgame for HSR ---------------------------------------------------------------------

MEMORY_OF_CHAOS = HsrEndgame(
    mode="memory_of_chaos",
    season="Breath of Cosmos",
    ends_at=datetime(2026, 10, 20, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=33,
    extra_stars=0,
    deepest_floor="Stage 12",
    battles=14,
    floors=(HsrFloor("Stage 12", 3, 5, None), HsrFloor("Stage 11", 2, 7, None)),
)
PURE_FICTION = HsrEndgame(
    mode="pure_fiction",
    season="Youthful Fantasy",
    ends_at=datetime(2026, 10, 27, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=12,
    extra_stars=0,
    deepest_floor="Stage 4",
    battles=6,
    floors=(HsrFloor("Stage 4", 3, None, 38120),),
)
APOCALYPTIC_SHADOW = HsrEndgame(
    mode="apocalyptic_shadow",
    season=None,
    ends_at=None,
    has_data=False,
    stars=0,
    extra_stars=0,
    deepest_floor=None,
    battles=None,
    floors=(),
)
ANOMALY_ARBITRATION = HsrEndgame(
    mode="anomaly_arbitration",
    season="Sample Peak",
    ends_at=datetime(2026, 11, 3, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=5,
    extra_stars=0,
    deepest_floor="Sample Boss",
    battles=None,
    floors=(),
)
ENDGAMES: dict[str, HsrEndgame | GachaError] = {
    endgame.mode: endgame
    for endgame in (MEMORY_OF_CHAOS, PURE_FICTION, APOCALYPTIC_SHADOW, ANOMALY_ARBITRATION)
}
MEMORY_OF_CHAOS_JSON = {
    "mode": "memory_of_chaos",
    "name": "Memory of Chaos",
    "season": "Breath of Cosmos",
    "ends": "2026-10-20 02:59",
    "ends_in_hours": 103,
    "has_data": True,
    "stars": 33,
    "extra_stars": 0,
    "deepest_floor": "Stage 12",
    "battles": 14,
    "floors": [
        {"name": "Stage 12", "stars": 3, "cycles": 5},
        {"name": "Stage 11", "stars": 2, "cycles": 7},
    ],
}
PURE_FICTION_JSON = {
    "mode": "pure_fiction",
    "name": "Pure Fiction",
    "season": "Youthful Fantasy",
    "ends": "2026-10-27 02:59",
    "ends_in_hours": 271,
    "has_data": True,
    "stars": 12,
    "extra_stars": 0,
    "deepest_floor": "Stage 4",
    "battles": 6,
    "floors": [{"name": "Stage 4", "stars": 3, "score": 38120}],
}
APOCALYPTIC_SHADOW_JSON = {
    "mode": "apocalyptic_shadow",
    "name": "Apocalyptic Shadow",
    "has_data": False,
    "stars": 0,
    "extra_stars": 0,
    "floors": [],
}
ANOMALY_ARBITRATION_JSON = {
    "mode": "anomaly_arbitration",
    "name": "Anomaly Arbitration",
    "season": "Sample Peak",
    "ends": "2026-11-03 02:59",
    "ends_in_hours": 439,
    "has_data": True,
    "stars": 5,
    "extra_stars": 0,
    "deepest_floor": "Sample Boss",
    "floors": [],
}


def test_one_endgame_mode_with_its_local_end_and_hours_left_rounded_up():
    source = source_with(endgames=ENDGAMES)

    data = answer(make_tools(source), GAME_ENDGAME, {"mode": "memory_of_chaos"})

    assert data == {"as_of": "2026-10-15 20:00", "modes": [MEMORY_OF_CHAOS_JSON]}
    assert source.calls == ["hsr_endgame:memory_of_chaos"]


def test_pure_fiction_floors_carry_their_score():
    data = answer(
        make_tools(source_with(endgames=ENDGAMES)), GAME_ENDGAME, {"mode": "pure_fiction"}
    )

    assert data["modes"] == [PURE_FICTION_JSON]


def test_values_a_season_does_not_have_are_left_out():
    tools = make_tools(source_with(endgames=ENDGAMES))

    data = answer(tools, GAME_ENDGAME, {"mode": "apocalyptic_shadow"})

    assert data["modes"] == [APOCALYPTIC_SHADOW_JSON]


def test_without_a_mode_every_endgame_mode_comes_in_display_order():
    source = source_with(endgames=ENDGAMES)

    data = answer(make_tools(source), GAME_ENDGAME, {})

    assert data == {
        "as_of": "2026-10-15 20:00",
        "modes": [
            MEMORY_OF_CHAOS_JSON,
            PURE_FICTION_JSON,
            APOCALYPTIC_SHADOW_JSON,
            ANOMALY_ARBITRATION_JSON,
        ],
    }
    assert source.calls == [f"hsr_endgame:{','.join(ENDGAME_MODES)}"]


def test_the_hsr_endgame_never_gives_a_maximum_of_stars():
    data = answer(make_tools(source_with(endgames=ENDGAMES)), GAME_ENDGAME, {})

    assert [key for mode in data["modes"] for key in mode if "max" in key] == []


def test_a_mode_that_failed_is_under_errors_without_hiding_the_others():
    endgames = {**ENDGAMES, "pure_fiction": REFUSED_ERROR}

    result, is_error = make_tools(source_with(endgames=endgames)).run(GAME_ENDGAME, {})

    data = json.loads(result)
    assert is_error is False
    assert [mode["mode"] for mode in data["modes"]] == [
        "memory_of_chaos",
        "apocalyptic_shadow",
        "anomaly_arbitration",
    ]
    assert data["errors"] == [{"mode": "pure_fiction", "message": REFUSED_ERROR.message}]


@pytest.mark.parametrize("arguments", [{}, {"mode": "pure_fiction"}])
def test_the_endgame_is_an_error_only_when_every_requested_mode_failed(arguments):
    endgames: dict[str, HsrEndgame | GachaError] = dict.fromkeys(ENDGAME_MODES, NETWORK_ERROR)

    result, is_error = make_tools(source_with(endgames=endgames)).run(GAME_ENDGAME, arguments)

    assert is_error is True
    assert json.loads(result) == {"ok": False, "message": NETWORK_ERROR.message}


def test_different_failures_of_every_mode_are_each_told_once():
    endgames = {**dict.fromkeys(ENDGAME_MODES, NETWORK_ERROR), "pure_fiction": REFUSED_ERROR}

    result, is_error = make_tools(source_with(endgames=endgames)).run(GAME_ENDGAME, {})

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": f"{NETWORK_ERROR.message} {REFUSED_ERROR.message}",
    }


def test_hours_left_never_go_below_zero():
    ended = replace(MEMORY_OF_CHAOS, ends_at=NOW - timedelta(minutes=5))
    tools = make_tools(source_with(endgames={"memory_of_chaos": ended}))

    data = answer(tools, GAME_ENDGAME, {"mode": "memory_of_chaos"})

    assert data["modes"][0]["ends"] == "2026-10-15 19:55"
    assert data["modes"][0]["ends_in_hours"] == 0


def busy_endgame(mode: str) -> HsrEndgame:
    cycles = mode == "memory_of_chaos"
    return HsrEndgame(
        mode=mode,
        season="Sample Season of a Rather Long Name",
        ends_at=NOW + timedelta(days=12),
        has_data=True,
        stars=36,
        extra_stars=3,
        deepest_floor="Sample Stage Twelve: Upper Half",
        battles=18,
        floors=tuple(
            HsrFloor(
                f"Sample Stage {12 - i}: Upper Half",
                3,
                10 if cycles else None,
                None if cycles else 40000,
            )
            for i in range(3)
        ),
    )


def test_four_endgame_modes_fit_in_2_kb():
    endgames: dict[str, HsrEndgame | GachaError] = {
        mode: busy_endgame(mode) for mode in ENDGAME_MODES
    }

    result, is_error = make_tools(source_with(endgames=endgames)).run(GAME_ENDGAME, {})

    assert is_error is False
    assert len(json.loads(result)["modes"]) == 4
    assert utf8_size(result) <= 2_000


# --- game_endgame for Genshin Impact and Zenless Zone Zero ---------------------------------------

SPIRAL_ABYSS = Endgame(
    mode="spiral_abyss",
    season=None,
    ends_at=datetime(2026, 11, 1, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=33,
    deepest="12-3",
    difficulty=None,
    rating=None,
    score=None,
    max_score=None,
    seconds=None,
    battles=14,
    stages=(
        EndgameStage("Floor 12", 6, 9, None, None, None),
        EndgameStage("Floor 11", 9, 9, None, None, None),
        EndgameStage("Floor 10", 9, 9, None, None, None),
        EndgameStage("Floor 9", 9, 9, None, None, None),
    ),
)
IMAGINARIUM_THEATER = Endgame(
    mode="imaginarium_theater",
    season=None,
    ends_at=datetime(2026, 10, 31, 20, 0, tzinfo=UTC),
    has_data=True,
    stars=8,
    deepest="Act 8",
    difficulty="Visionary",
    rating=None,
    score=None,
    max_score=None,
    seconds=None,
    battles=None,
    stages=(),
)
STYGIAN_ONSLAUGHT = Endgame(
    mode="stygian_onslaught",
    season="Sample Season",
    ends_at=datetime(2026, 10, 27, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=None,
    deepest=None,
    difficulty="5",
    rating=None,
    score=None,
    max_score=None,
    seconds=142,
    battles=None,
    stages=(
        EndgameStage("Sample Boss One", None, None, None, None, 41),
        EndgameStage("Sample Boss Two", None, None, None, None, 52),
        EndgameStage("Sample Boss Three", None, None, None, None, 49),
    ),
)
SHIYU_DEFENSE = Endgame(
    mode="shiyu_defense",
    season=None,
    ends_at=datetime(2026, 10, 24, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=None,
    deepest=None,
    difficulty=None,
    rating="S+",
    score=61234,
    max_score=65000,
    seconds=312,
    battles=None,
    stages=(
        EndgameStage("Frontier 5 battle 1", None, None, "S", 20412, 104),
        EndgameStage("Frontier 5 battle 2", None, None, "S", 20411, 98),
        EndgameStage("Frontier 5 battle 3", None, None, "A", 20411, 110),
    ),
)
DEADLY_ASSAULT = Endgame(
    mode="deadly_assault",
    season=None,
    ends_at=datetime(2026, 10, 20, 3, 59, 59, tzinfo=CST),
    has_data=True,
    stars=7,
    deepest=None,
    difficulty=None,
    rating=None,
    score=32100,
    max_score=None,
    seconds=None,
    battles=None,
    stages=(
        EndgameStage("Sample Boss Alpha", 3, None, None, 12000, None),
        EndgameStage("Sample Boss Beta", 2, None, None, 10600, None),
        EndgameStage("Sample Boss Gamma", 2, None, None, 9500, None),
    ),
)
GAME_ENDGAMES: dict[str, Endgame | GachaError] = {
    each.mode: each
    for each in (
        SPIRAL_ABYSS,
        IMAGINARIUM_THEATER,
        STYGIAN_ONSLAUGHT,
        SHIYU_DEFENSE,
        DEADLY_ASSAULT,
    )
}
SPIRAL_ABYSS_JSON = {
    "mode": "spiral_abyss",
    "name": "Spiral Abyss",
    "ends": "2026-11-01 02:59",
    "ends_in_hours": 391,
    "has_data": True,
    "stars": 33,
    "deepest": "12-3",
    "battles": 14,
    "stages": [
        {"name": "Floor 12", "stars": 6, "max_stars": 9},
        {"name": "Floor 11", "stars": 9, "max_stars": 9},
        {"name": "Floor 10", "stars": 9, "max_stars": 9},
        {"name": "Floor 9", "stars": 9, "max_stars": 9},
    ],
}
IMAGINARIUM_THEATER_JSON = {
    "mode": "imaginarium_theater",
    "name": "Imaginarium Theater",
    "ends": "2026-11-01 03:00",
    "ends_in_hours": 391,
    "has_data": True,
    "stars": 8,
    "deepest": "Act 8",
    "difficulty": "Visionary",
    "stages": [],
}
STYGIAN_ONSLAUGHT_JSON = {
    "mode": "stygian_onslaught",
    "name": "Stygian Onslaught",
    "season": "Sample Season",
    "ends": "2026-10-27 02:59",
    "ends_in_hours": 271,
    "has_data": True,
    "difficulty": "5",
    "clear_seconds": 142,
    "stages": [
        {"name": "Sample Boss One", "clear_seconds": 41},
        {"name": "Sample Boss Two", "clear_seconds": 52},
        {"name": "Sample Boss Three", "clear_seconds": 49},
    ],
}
SHIYU_DEFENSE_JSON = {
    "mode": "shiyu_defense",
    "name": "Shiyu Defense",
    "ends": "2026-10-24 02:59",
    "ends_in_hours": 199,
    "has_data": True,
    "rating": "S+",
    "score": 61234,
    "max_score": 65000,
    "clear_seconds": 312,
    "stages": [
        {"name": "Frontier 5 battle 1", "rating": "S", "score": 20412, "clear_seconds": 104},
        {"name": "Frontier 5 battle 2", "rating": "S", "score": 20411, "clear_seconds": 98},
        {"name": "Frontier 5 battle 3", "rating": "A", "score": 20411, "clear_seconds": 110},
    ],
}
DEADLY_ASSAULT_JSON = {
    "mode": "deadly_assault",
    "name": "Deadly Assault",
    "ends": "2026-10-20 02:59",
    "ends_in_hours": 103,
    "has_data": True,
    "stars": 7,
    "score": 32100,
    "stages": [
        {"name": "Sample Boss Alpha", "stars": 3, "score": 12000},
        {"name": "Sample Boss Beta", "stars": 2, "score": 10600},
        {"name": "Sample Boss Gamma", "stars": 2, "score": 9500},
    ],
}


def test_every_genshin_endgame_mode_comes_in_display_order_with_what_it_gives():
    source = FakeSource(endgames=GAME_ENDGAMES)

    data = answer(make_tools(source, ALL_GAMES), GAME_ENDGAME, GENSHIN)

    assert data == {
        "as_of": "2026-10-15 20:00",
        "modes": [SPIRAL_ABYSS_JSON, IMAGINARIUM_THEATER_JSON, STYGIAN_ONSLAUGHT_JSON],
    }
    assert source.calls == ["genshin_endgame:spiral_abyss,imaginarium_theater,stygian_onslaught"]


def test_every_zzz_endgame_mode_comes_in_display_order_with_what_it_gives():
    source = FakeSource(endgames=GAME_ENDGAMES)

    data = answer(make_tools(source, ALL_GAMES), GAME_ENDGAME, ZZZ)

    assert data == {
        "as_of": "2026-10-15 20:00",
        "modes": [SHIYU_DEFENSE_JSON, DEADLY_ASSAULT_JSON],
    }
    assert source.calls == ["zzz_endgame:shiyu_defense,deadly_assault"]


def test_an_unplayed_mode_gives_only_its_name_and_has_data():
    unplayed = Endgame(
        "deadly_assault", None, None, False, None, None, None, None, None, None, None, None, ()
    )
    source = FakeSource(endgames={"deadly_assault": unplayed})

    data = answer(make_tools(source, ALL_GAMES), GAME_ENDGAME, {"mode": "deadly_assault"})

    assert data["modes"] == [
        {"mode": "deadly_assault", "name": "Deadly Assault", "has_data": False, "stages": []}
    ]


@pytest.mark.parametrize("ended_ago", [timedelta(hours=16), timedelta(0)])
def test_a_season_already_over_says_it_ended_instead_of_hours_left(ended_ago):
    """Between two Stygian Onslaught seasons HoYoLAB still gives the one that ended, with its
    figures: hours left of 0 would make it a season ending within the hour."""
    ends_at = NOW - ended_ago
    source = FakeSource(endgames={"stygian_onslaught": replace(STYGIAN_ONSLAUGHT, ends_at=ends_at)})

    data = answer(make_tools(source, ALL_GAMES), GAME_ENDGAME, {"mode": "stygian_onslaught"})

    facts = {key: value for key, value in STYGIAN_ONSLAUGHT_JSON.items() if key != "ends_in_hours"}
    ends = ends_at.strftime("%Y-%m-%d %H:%M")
    assert data["modes"] == [{**facts, "ends": ends, "ended": True}]


def test_a_season_kept_past_its_end_says_it_ended_when_answering():
    clock = Clock()
    ends_soon = replace(DEADLY_ASSAULT, ends_at=NOW + timedelta(minutes=5))
    tools = GameDataTools(FakeSource(endgames={"deadly_assault": ends_soon}), ALL_GAMES, now=clock)

    before = answer(tools, GAME_ENDGAME, {"mode": "deadly_assault"})["modes"][0]
    clock.advance(minutes=6)
    after = answer(tools, GAME_ENDGAME, {"mode": "deadly_assault"})["modes"][0]

    assert (before["ends_in_hours"], "ended" in before) == (1, False)
    assert ("ends_in_hours" in after, after["ended"]) == (False, True)


@pytest.mark.parametrize(("games", "told"), [(HSR, False), (("genshin",), True), (ALL_GAMES, True)])
def test_the_endgame_description_says_what_an_ended_season_is_for_genshin_and_zzz(games, told):
    description = spec(make_tools(games=games), GAME_ENDGAME).description

    rule = (
        "A season already over, before the next one starts, has `ended` true instead of "
        "`ends_in_hours`, and its figures are from that season. "
    )
    assert (rule in description) is told


def test_a_mode_keeps_at_most_four_stages():
    stages = tuple(EndgameStage(f"Floor {12 - i}", 9, 9, None, None, None) for i in range(6))
    source = FakeSource(endgames={"spiral_abyss": replace(SPIRAL_ABYSS, stages=stages)})

    data = answer(make_tools(source, ALL_GAMES), GAME_ENDGAME, {"mode": "spiral_abyss"})

    assert [stage["name"] for stage in data["modes"][0]["stages"]] == [
        "Floor 12",
        "Floor 11",
        "Floor 10",
        "Floor 9",
    ]


def test_a_genshin_mode_that_failed_is_under_errors_without_hiding_the_others():
    source = FakeSource(endgames={**GAME_ENDGAMES, "imaginarium_theater": REFUSED_ERROR})

    data = answer(make_tools(source, ALL_GAMES), GAME_ENDGAME, GENSHIN)

    assert [mode["mode"] for mode in data["modes"]] == ["spiral_abyss", "stygian_onslaught"]
    assert data["errors"] == [{"mode": "imaginarium_theater", "message": REFUSED_ERROR.message}]


def busy_game_endgames() -> dict[str, Endgame | GachaError]:
    """Every Genshin and ZZZ mode with all it gives, its stages at their cap, long names."""
    ends = NOW + timedelta(days=12)
    boss = "Sample Boss of a Rather Long Name"
    played = {"ends_at": ends, "has_data": True, "battles": None, "max_score": None}
    modes = [
        Endgame(
            **{**played, "battles": 18},
            mode="spiral_abyss",
            season=None,
            stars=36,
            deepest="12-3",
            difficulty=None,
            rating=None,
            score=None,
            seconds=None,
            stages=tuple(EndgameStage(f"Floor {12 - i}", 9, 9, None, None, None) for i in range(4)),
        ),
        Endgame(
            **played,
            mode="imaginarium_theater",
            season=None,
            stars=10,
            deepest="Act 10",
            difficulty="Arcana Challenge",
            rating=None,
            score=None,
            seconds=None,
            stages=(),
        ),
        Endgame(
            **played,
            mode="stygian_onslaught",
            season="Sample Season of a Rather Long Name",
            stars=None,
            deepest=None,
            difficulty="6",
            rating=None,
            score=None,
            seconds=600,
            stages=tuple(
                EndgameStage(f"{boss} {i}", None, None, None, None, 200) for i in range(4)
            ),
        ),
        Endgame(
            **{**played, "max_score": 65000},
            mode="shiyu_defense",
            season=None,
            stars=None,
            deepest=None,
            difficulty=None,
            rating="S+",
            score=65000,
            seconds=600,
            stages=tuple(
                EndgameStage(f"Frontier 5 battle {i}", None, None, "S", 21666, 200)
                for i in range(1, 5)
            ),
        ),
        Endgame(
            **{**played, "max_score": 60000},
            mode="deadly_assault",
            season=None,
            stars=9,
            deepest=None,
            difficulty=None,
            rating=None,
            score=60000,
            seconds=None,
            stages=tuple(EndgameStage(f"{boss} {i}", 3, None, None, 20000, None) for i in range(3)),
        ),
    ]
    return {each.mode: each for each in modes}


@pytest.mark.parametrize(("game", "budget"), [("genshin", 2_000), ("zzz", 1_500)])
def test_the_endgame_of_every_genshin_or_zzz_mode_fits_its_budget(game, budget):
    source = FakeSource(endgames=busy_game_endgames())

    result, is_error = make_tools(source, ALL_GAMES).run(GAME_ENDGAME, {"game": game})

    assert is_error is False
    assert len(json.loads(result)["modes"]) == len(GAME_MODES[game])
    assert utf8_size(result) <= budget


# --- game_events for HSR ----------------------------------------------------------------------

CALENDAR = HsrCalendar(
    version="3.7",
    warps=(
        HsrWarp(
            "character",
            "Sample Character Warp",
            ("Hyacine",),
            ("Pela", "Gallagher", "Hook"),
            datetime(2026, 10, 12, 12, 0, tzinfo=CST),
            datetime(2026, 11, 4, 14, 59, 59, tzinfo=CST),
        ),
        HsrWarp(
            "light_cone",
            "Sample Light Cone Warp",
            ("Long May Rainbows Adorn the Sky",),
            ("Swordplay", "Fermata", "Dance! Dance! Dance!"),
            datetime(2026, 10, 21, 12, 0, tzinfo=CST),
            None,
        ),
    ),
    events=(
        HsrEvent(
            "Gift of Odyssey",
            datetime(2026, 10, 12, 12, 0, tzinfo=CST),
            datetime(2026, 10, 29, 3, 59, 59, tzinfo=CST),
            5,
            7,
            False,
        ),
        HsrEvent("Sample Login Event", None, None, 7, 7, True),
        HsrEvent(
            "Planar Fissure",
            datetime(2026, 10, 13, 4, 0, tzinfo=CST),
            datetime(2026, 10, 18, 3, 59, 59, tzinfo=CST),
            None,
            None,
            False,
        ),
    ),
    challenges=(
        HsrChallengeWindow(
            "memory_of_chaos",
            "Memory of Chaos",
            datetime(2026, 9, 8, 4, 0, tzinfo=CST),
            datetime(2026, 10, 20, 3, 59, 59, tzinfo=CST),
            33,
            36,
        ),
        HsrChallengeWindow("pure_fiction", "Pure Fiction", None, None, None, None),
    ),
)


def test_the_calendar_gives_warps_events_and_endgame_windows_in_local_time():
    source = source_with(hsr_calendar=CALENDAR)

    data = answer(make_tools(source), GAME_EVENTS, {})

    assert data == {
        "as_of": "2026-10-15 20:00",
        "version": "3.7",
        "warps": [
            {
                "type": "character",
                "name": "Sample Character Warp",
                "featured": ["Hyacine"],
                "four_stars": ["Pela", "Gallagher", "Hook"],
                "ends": "2026-11-04 13:59",
                "ends_in_hours": 474,
            },
            {
                "type": "light_cone",
                "name": "Sample Light Cone Warp",
                "featured": ["Long May Rainbows Adorn the Sky"],
                "four_stars": ["Swordplay", "Fermata", "Dance! Dance! Dance!"],
                "starts": "2026-10-21 11:00",
                "starts_in_hours": 135,
            },
        ],
        "events": [
            {
                "name": "Planar Fissure",
                "ends": "2026-10-18 02:59",
                "ends_in_hours": 55,
                "done": False,
            },
            {
                "name": "Gift of Odyssey",
                "ends": "2026-10-29 02:59",
                "ends_in_hours": 319,
                "current": 5,
                "total": 7,
                "done": False,
            },
            {"name": "Sample Login Event", "current": 7, "total": 7, "done": True},
        ],
        "endgame": [
            {
                "mode": "memory_of_chaos",
                "name": "Memory of Chaos",
                "ends": "2026-10-20 02:59",
                "ends_in_hours": 103,
                "current": 33,
                "total": 36,
            },
            {"mode": "pure_fiction", "name": "Pure Fiction"},
        ],
    }
    assert source.calls == ["hsr_calendar"]


def test_a_warp_gives_its_start_only_until_it_starts():
    clock = Clock()
    starts, ends = NOW + timedelta(minutes=30), NOW + timedelta(days=21)
    warp = HsrWarp("character", "Next Warp", ("Firefly",), ("Lynx",), starts, ends)
    tools = GameDataTools(
        source_with(hsr_calendar=HsrCalendar("3.7", (warp,), (), ())), HSR, now=clock
    )

    before = answer(tools, GAME_EVENTS, {})["warps"][0]
    clock.advance(minutes=40)
    after = answer(tools, GAME_EVENTS, {})["warps"][0]

    assert (before["starts"], before["starts_in_hours"]) == ("2026-10-15 20:30", 1)
    assert "starts" not in after
    assert "starts_in_hours" not in after
    assert after["ends"] == "2026-11-05 20:00"


def test_a_warp_hoyolab_gives_no_name_leaves_the_name_out():
    # HoYoLAB's calendar often names no warp; the featured names say which one it is.
    warp = HsrWarp("character", "", ("Firefly",), ("Lynx",), None, NOW + timedelta(days=5))
    tools = make_tools(source_with(hsr_calendar=HsrCalendar("3.7", (warp,), (), ())))

    shown = answer(tools, GAME_EVENTS, {})["warps"][0]

    assert "name" not in shown
    assert shown["featured"] == ["Firefly"]


def test_a_calendar_without_a_version_leaves_it_out():
    data = answer(make_tools(source_with()), GAME_EVENTS, {})

    assert data == {"as_of": "2026-10-15 20:00", "warps": [], "events": [], "endgame": []}


def test_the_calendar_keeps_8_warps_the_15_events_ending_soonest_and_4_windows():
    calendar_ = HsrCalendar(
        version="3.7",
        warps=tuple(
            HsrWarp("character", f"Warp {i}", ("Sample",), (), None, None) for i in range(10)
        ),
        events=(
            HsrEvent("Event without an end", None, None, None, None, False),
            *(
                HsrEvent(f"Event {i:02}", None, NOW + timedelta(hours=20 - i), None, None, False)
                for i in range(20)
            ),
        ),
        challenges=tuple(
            HsrChallengeWindow(mode, f"Window {i}", None, None, None, None)
            for i, mode in enumerate([*ENDGAME_MODES, "memory_of_chaos"])
        ),
    )

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), GAME_EVENTS, {})

    assert [warp["name"] for warp in data["warps"]] == [f"Warp {i}" for i in range(8)]
    assert [event["name"] for event in data["events"]] == [
        f"Event {i:02}" for i in range(19, 4, -1)
    ]
    assert [window["name"] for window in data["endgame"]] == [f"Window {i}" for i in range(4)]


def test_event_names_are_cut_to_60_characters():
    name = "Sample Event With a Name Far Longer Than Anyone Would Say Out Loud Twice"
    calendar_ = HsrCalendar(None, (), (HsrEvent(name, None, None, None, None, False),), ())

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), GAME_EVENTS, {})

    assert data["events"][0]["name"] == name[:60]


def busy_calendar() -> HsrCalendar:
    starts, ends = NOW - timedelta(days=3), NOW + timedelta(days=18)
    warps = (
        HsrWarp(
            "character",
            "Sample Character Event Warp",
            ("Hyacine",),
            ("Pela", "Gallagher", "Hook"),
            starts,
            ends,
        ),
        HsrWarp(
            "character",
            "Sample Character Rerun Warp",
            ("Castorice",),
            ("Lynx", "Misha", "Guinaifen"),
            starts,
            ends,
        ),
        HsrWarp(
            "light_cone",
            "Sample Light Cone Event Warp",
            ("Long May Rainbows Adorn the Sky",),
            ("Swordplay", "Fermata", "Dance! Dance! Dance!"),
            starts,
            ends,
        ),
        HsrWarp(
            "light_cone",
            "Sample Light Cone Rerun Warp",
            ("Make Farewells More Beautiful",),
            ("Subscribe for More!", "Planetary Rendezvous", "Resolution Shines As Pearls of Sweat"),
            starts,
            ends,
        ),
    )
    events = tuple(
        HsrEvent(
            f"Sample Event of This Version {i}", starts, NOW + timedelta(days=i + 1), i, 10, False
        )
        for i in range(10)
    )
    challenges = tuple(
        HsrChallengeWindow(mode, name, starts, NOW + timedelta(days=9), 20, 36)
        for mode, name in ENDGAME_MODES.items()
    )
    return HsrCalendar("3.7", warps, events, challenges)


def test_a_busy_calendar_fits_in_3_kb():
    result, is_error = make_tools(source_with(hsr_calendar=busy_calendar())).run(GAME_EVENTS, {})

    assert is_error is False
    assert utf8_size(result) <= 3_000


def calendar_at_the_caps() -> HsrCalendar:
    """8 warps (this phase's and the next), 15 events, 4 windows, with long real names and
    event names beyond the 60 characters kept."""
    running, upcoming = (
        (NOW - timedelta(days=3), NOW + timedelta(days=18)),
        (
            NOW + timedelta(days=18),
            NOW + timedelta(days=39),
        ),
    )
    four_stars = {
        "character": ("Gallagher", "Guinaifen", "Moze"),
        "light_cone": (
            "Resolution Shines As Pearls of Sweat",
            "Subscribe for More!",
            "Dreamville Adventure",
        ),
    }
    warps = tuple(
        HsrWarp(kind, name, (featured,), four_stars[kind], *window)
        for kind, name, featured, window in [
            ("character", "Butterfly on Swordtip", "Dan Heng • Imbibitor Lunae", running),
            ("character", "Gentle Eclipse of the Moon", "Castorice", running),
            ("light_cone", "Brilliant Fixation", "Long May Rainbows Adorn the Sky", running),
            ("light_cone", "Bygone Reminiscence", "Whereabouts Should Dreams Rest", running),
            ("character", "Sails of the Final Voyage", "Topaz & Numby", upcoming),
            ("character", "Echoes of a Faded Dream", "Silver Wolf", upcoming),
            ("light_cone", "Epochal Spectrum", "Make Farewells More Beautiful", upcoming),
            ("light_cone", "Lingering Afterglow", "Along the Passing Shore", upcoming),
        ]
    )
    started = running[0]
    events = tuple(
        HsrEvent(
            f"Sample Event of Version {i:02} with a Name Longer Than the Cap Allows",
            started,
            NOW + timedelta(days=i + 1),
            i,
            14,
            False,
        )
        for i in range(15)
    )
    seasons = ("Gleaming Treasure", "Nameless Tales", "Shadow of the Ranger", "Lost Arbitration")
    challenges = tuple(
        HsrChallengeWindow(mode, season, started, NOW + timedelta(days=9), 20, 36)
        for mode, season in zip(ENDGAME_MODES, seasons, strict=True)
    )
    return HsrCalendar("3.7", warps, events, challenges)


def test_a_calendar_at_its_caps_fits_in_5_kb():
    tools = make_tools(source_with(hsr_calendar=calendar_at_the_caps()))

    result, is_error = tools.run(GAME_EVENTS, {})

    data = json.loads(result)
    assert is_error is False
    assert [len(data[key]) for key in ("warps", "events", "endgame")] == [8, 15, 4]
    assert {len(event["name"]) for event in data["events"]} == {60}
    assert utf8_size(result) <= 5_000


# --- game_events for Genshin Impact and Zenless Zone Zero ---------------------------------------

GENSHIN_CALENDAR = GameCalendar(
    version="6.1",
    banners=(
        Banner(
            "character",
            "Sample Character Wish",
            ("Furina",),
            ("Xiangling", "Bennett", "Xingqiu"),
            datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
            datetime(2026, 10, 28, 14, 59, 59, tzinfo=UTC),
        ),
        Banner(
            "weapon",
            "Epitome Invocation",
            ("Splendor of Tranquil Waters", "Mistsplitter Reforged"),
            ("The Flute", "Sacrificial Sword", "Favonius Lance"),
            datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
            datetime(2026, 10, 28, 14, 59, 59, tzinfo=UTC),
        ),
        Banner(
            "chronicled",
            None,
            ("Sample Chronicled Hero",),
            ("Sample Chronicled Friend",),
            datetime(2026, 10, 28, 22, 0, tzinfo=UTC),
            datetime(2026, 11, 18, 14, 59, 59, tzinfo=UTC),
        ),
    ),
    events=(
        GameEvent(
            "Sample Event",
            datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
            datetime(2026, 10, 20, 3, 59, 59, tzinfo=UTC),
            None,
            None,
            False,
        ),
        GameEvent(
            "Done Event", None, datetime(2026, 10, 28, 14, 59, 59, tzinfo=UTC), None, None, True
        ),
    ),
    challenges=(
        ChallengeWindow(
            "spiral_abyss",
            "Abyssal Moon Spire",
            datetime(2026, 9, 30, 20, 0, tzinfo=UTC),
            datetime(2026, 10, 31, 20, 0, tzinfo=UTC),
            33,
            36,
        ),
        ChallengeWindow(
            "imaginarium_theater",
            None,  # HoYoLAB named it after its mode
            datetime(2026, 9, 30, 20, 0, tzinfo=UTC),
            datetime(2026, 10, 31, 20, 0, tzinfo=UTC),
            None,  # its deepest act is game_endgame's
            None,
        ),
    ),
)
ZZZ_CALENDAR = GameCalendar(
    version="2.3",
    banners=(
        Banner(
            "agent",
            None,
            ("Ellen",),
            ("Anby", "Nicole"),
            datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            datetime(2026, 10, 29, 2, 59, 59, tzinfo=UTC),
        ),
        Banner(
            "w_engine",
            None,
            (),
            (),
            datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            datetime(2026, 10, 29, 2, 59, 59, tzinfo=UTC),
        ),
    ),
    events=(
        GameEvent(
            "Sample Event",
            datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            datetime(2026, 10, 22, 3, 59, 59, tzinfo=UTC),
            300,
            1000,
            False,
        ),
        GameEvent("Sample Login Event", None, None, None, None, True),
    ),
    challenges=(),
)


def test_the_genshin_calendar_gives_banners_events_and_endgame_windows_in_local_time():
    source = FakeSource(calendars={"genshin": GENSHIN_CALENDAR})

    data = answer(make_tools(source, ALL_GAMES), GAME_EVENTS, GENSHIN)

    assert data == {
        "as_of": "2026-10-15 20:00",
        "version": "6.1",
        "banners": [
            {
                "type": "character",
                "name": "Sample Character Wish",
                "featured": ["Furina"],
                "four_stars": ["Xiangling", "Bennett", "Xingqiu"],
                "ends": "2026-10-28 21:59",
                "ends_in_hours": 314,
            },
            {
                "type": "weapon",
                "name": "Epitome Invocation",
                "featured": ["Splendor of Tranquil Waters", "Mistsplitter Reforged"],
                "four_stars": ["The Flute", "Sacrificial Sword", "Favonius Lance"],
                "ends": "2026-10-28 21:59",
                "ends_in_hours": 314,
            },
            {
                "type": "chronicled",
                "featured": ["Sample Chronicled Hero"],
                "four_stars": ["Sample Chronicled Friend"],
                "starts": "2026-10-29 05:00",
                "starts_in_hours": 321,
                "ends": "2026-11-18 21:59",
                "ends_in_hours": 818,
            },
        ],
        "events": [
            {
                "name": "Sample Event",
                "ends": "2026-10-20 10:59",
                "ends_in_hours": 111,
                "done": False,
            },
            {"name": "Done Event", "ends": "2026-10-28 21:59", "ends_in_hours": 314, "done": True},
        ],
        "endgame": [
            {
                "mode": "spiral_abyss",
                "name": "Abyssal Moon Spire",
                "ends": "2026-11-01 03:00",
                "ends_in_hours": 391,
                "current": 33,
                "total": 36,
            },
            {"mode": "imaginarium_theater", "ends": "2026-11-01 03:00", "ends_in_hours": 391},
        ],
    }
    assert source.calls == ["genshin_calendar"]


def test_the_zzz_calendar_has_no_endgame_windows_and_w_engine_banners_name_nothing():
    source = FakeSource(calendars={"zzz": ZZZ_CALENDAR})

    data = answer(make_tools(source, ALL_GAMES), GAME_EVENTS, ZZZ)

    assert data == {
        "as_of": "2026-10-15 20:00",
        "version": "2.3",
        "banners": [
            {
                "type": "agent",
                "featured": ["Ellen"],
                "four_stars": ["Anby", "Nicole"],
                "ends": "2026-10-29 09:59",
                "ends_in_hours": 326,
            },
            {
                "type": "w_engine",
                "featured": [],
                "four_stars": [],
                "ends": "2026-10-29 09:59",
                "ends_in_hours": 326,
            },
        ],
        "events": [
            {
                "name": "Sample Event",
                "ends": "2026-10-22 10:59",
                "ends_in_hours": 159,
                "current": 300,
                "total": 1000,
                "done": False,
            },
            {"name": "Sample Login Event", "done": True},
        ],
    }
    assert source.calls == ["zzz_calendar"]


def test_a_kept_genshin_calendar_is_filtered_again_when_answering():
    clock = Clock()
    ends_soon, later = NOW + timedelta(minutes=20), NOW + timedelta(days=5)
    calendar_ = GameCalendar(
        "6.1",
        (
            Banner("character", "Ending Wish", ("Furina",), (), None, ends_soon),
            Banner("character", "Next Wish", ("Nahida",), (), ends_soon, later),
        ),
        (
            GameEvent("Ending Event", None, ends_soon, None, None, False),
            GameEvent("Next Event", ends_soon, later, None, None, False),
        ),
        (
            ChallengeWindow("spiral_abyss", "Old Abyss", None, ends_soon, 36, 36),
            ChallengeWindow("spiral_abyss", "New Abyss", ends_soon, later, 0, 36),
        ),
    )
    source = FakeSource(calendars={"genshin": calendar_})
    tools = GameDataTools(source, ALL_GAMES, now=clock)

    before = answer(tools, GAME_EVENTS, GENSHIN)
    clock.advance(minutes=30)
    after = answer(tools, GAME_EVENTS, GENSHIN)

    assert source.calls == ["genshin_calendar"]
    assert [banner["name"] for banner in before["banners"]] == ["Ending Wish", "Next Wish"]
    assert [event["name"] for event in before["events"]] == ["Ending Event"]
    assert [window["name"] for window in before["endgame"]] == ["Old Abyss"]
    assert [banner["name"] for banner in after["banners"]] == ["Next Wish"]
    assert [event["name"] for event in after["events"]] == ["Next Event"]
    assert [window["name"] for window in after["endgame"]] == ["New Abyss"]


def game_calendar_at_the_caps(game: str) -> GameCalendar:
    """8 banners (this phase's and the next) with long names, the weapon banners with their
    two 5-star and five 4-star weapons (ZZZ's W-Engine banners name none), 15 events beyond
    the 60 characters kept (Genshin's without progress, ZZZ's with their monochromes), and
    for Genshin 4 windows."""
    running = (NOW - timedelta(days=3), NOW + timedelta(days=18))
    upcoming = (NOW + timedelta(days=18), NOW + timedelta(days=39))
    zzz = game == "zzz"
    character_banner = (
        ("Kaedehara Kazuha",),
        ("Sangonomiya Kokomi", "Yumemizuki Mizuki", "Xiangling"),
    )
    weapon_banner = (
        () if zzz else ("Splendor of Tranquil Waters", "Mistsplitter Reforged"),
        ()
        if zzz
        else ("The Flute", "Sacrificial Sword", "Favonius Lance", "Rainslasher", "The Stringless"),
    )
    kinds = ("agent", "w_engine") if zzz else ("character", "weapon")
    banners = tuple(
        Banner(
            kinds[i % 2],
            None if zzz else f"Sample Banner Name Number {i}",
            *(weapon_banner if i % 2 else character_banner),
            *(running if i < 4 else upcoming),
        )
        for i in range(8)
    )
    events = tuple(
        GameEvent(
            f"Sample Event of Version {i:02} with a Name Longer Than the Cap Allows",
            running[0],
            NOW + timedelta(days=i + 1),
            i * 100 if zzz else None,
            1400 if zzz else None,
            False,
        )
        for i in range(15)
    )
    windows = tuple(
        ChallengeWindow(mode, name, running[0], NOW + timedelta(days=9), 30, 36)
        for mode, name in [
            ("spiral_abyss", "Spiral Abyss"),
            ("imaginarium_theater", "Imaginarium Theater"),
            ("spiral_abyss", "Spiral Abyss"),
            ("imaginarium_theater", "Imaginarium Theater"),
        ]
    )
    return GameCalendar("6.1", banners, events, () if game == "zzz" else windows)


@pytest.mark.parametrize("game", ["genshin", "zzz"])
def test_a_genshin_or_zzz_calendar_at_its_caps_fits_in_5_kb(game):
    source = FakeSource(calendars={game: game_calendar_at_the_caps(game)})

    result, is_error = make_tools(source, ALL_GAMES).run(GAME_EVENTS, {"game": game})

    data = json.loads(result)
    assert is_error is False
    assert [len(data["banners"]), len(data["events"]), len(data.get("endgame", ()))] == [
        8,
        15,
        0 if game == "zzz" else 4,
    ]
    assert utf8_size(result) <= 5_000


# --- failures and privacy -------------------------------------------------------------------


def failing_source() -> FakeSource:
    return FakeSource(
        characters=dict.fromkeys(ALL_GAMES, NETWORK_ERROR),
        details=dict.fromkeys(("genshin", "zzz"), NETWORK_ERROR),
        endgames=dict.fromkeys(
            [mode for modes in GAME_MODES.values() for mode in modes], NETWORK_ERROR
        ),
        calendars=dict.fromkeys(ALL_GAMES, NETWORK_ERROR),
    )


@pytest.mark.parametrize("game", ALL_GAMES)
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (GAME_CHARACTERS, {}),
        (GAME_CHARACTERS, {"name": "Acheron"}),
        (GAME_ENDGAME, {}),
        (GAME_EVENTS, {}),
    ],
)
def test_a_hoyolab_error_becomes_an_error_result_with_its_message(game, tool, arguments):
    result, is_error = make_tools(failing_source(), ALL_GAMES).run(
        tool, {**arguments, "game": game}
    )

    assert is_error is True
    assert json.loads(result) == {"ok": False, "message": NETWORK_ERROR.message}


def test_no_result_holds_the_uid_or_the_nickname():
    source = sample_source()
    source.uid = 800000001
    source.nickname = "SampleNickname"
    tools = make_tools(source, ALL_GAMES)

    results = [
        tools.run(tool, {**arguments, "game": game})[0]
        for game in ALL_GAMES
        for tool, arguments in [
            (GAME_CHARACTERS, {}),
            (GAME_CHARACTERS, {"name": "Acheron"}),
            (GAME_CHARACTERS, {"name": "Furina"}),
            (GAME_CHARACTERS, {"name": "Zhu Yuan"}),
            (GAME_CHARACTERS, {"name": "Sampel"}),
            (GAME_ENDGAME, {}),
            (GAME_EVENTS, {}),
        ]
    ]

    for result in results:
        assert "800000001" not in result
        assert "SampleNickname" not in result


# --- the cache ------------------------------------------------------------------------------


def test_a_second_question_about_characters_within_15_minutes_reuses_the_roster():
    clock = Clock()
    source = source_with(ROSTER)
    tools = GameDataTools(source, HSR, now=clock)

    tools.run(GAME_CHARACTERS, {})
    clock.advance(minutes=14)
    data = answer(tools, GAME_CHARACTERS, {"name": "Acheron"})

    assert source.calls == ["hsr_characters"]
    assert data["as_of"] == "2026-10-15 20:00"


@pytest.mark.parametrize(
    ("tool", "arguments", "call", "ttl_minutes"),
    [
        (GAME_CHARACTERS, {}, "hsr_characters", 15),
        (GAME_ENDGAME, {"mode": "pure_fiction"}, "hsr_endgame:pure_fiction", 10),
        (GAME_EVENTS, {}, "hsr_calendar", 60),
        (GAME_CHARACTERS, GENSHIN, "genshin_characters", 15),
        (GAME_ENDGAME, {"mode": "spiral_abyss"}, "genshin_endgame:spiral_abyss", 10),
        (GAME_EVENTS, GENSHIN, "genshin_calendar", 60),
        (GAME_CHARACTERS, ZZZ, "zzz_characters", 15),
        (GAME_ENDGAME, {"mode": "deadly_assault"}, "zzz_endgame:deadly_assault", 10),
        (GAME_EVENTS, ZZZ, "zzz_calendar", 60),
    ],
)
def test_a_result_is_kept_for_its_time_then_read_again(tool, arguments, call, ttl_minutes):
    clock = Clock()
    source = sample_source()
    tools = GameDataTools(source, ALL_GAMES, now=clock)
    arguments = {"game": "hsr", **arguments} if "mode" not in arguments else arguments

    tools.run(tool, arguments)
    clock.advance(minutes=ttl_minutes - 0.5)
    tools.run(tool, arguments)
    kept_calls = list(source.calls)
    clock.advance(minutes=0.5)
    data = answer(tools, tool, arguments)

    assert kept_calls == [call]
    assert source.calls == [call, call]
    assert data["as_of"] == (NOW + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M")


def test_each_game_keeps_its_own_roster_and_calendar():
    source = sample_source()
    tools = make_tools(source, ALL_GAMES)

    for game in ("hsr", "genshin", "hsr", "zzz", "genshin"):
        tools.run(GAME_CHARACTERS, {"game": game})
        tools.run(GAME_EVENTS, {"game": game})

    assert source.calls == [
        "hsr_characters",
        "hsr_calendar",
        "genshin_characters",
        "genshin_calendar",
        "zzz_characters",
        "zzz_calendar",
    ]


def test_a_clock_set_back_reads_again():
    clock = Clock()
    source = source_with(ROSTER)
    tools = GameDataTools(source, HSR, now=clock)

    tools.run(GAME_CHARACTERS, {})
    clock.advance(minutes=-60)
    tools.run(GAME_CHARACTERS, {})

    assert source.calls == ["hsr_characters", "hsr_characters"]


@pytest.mark.parametrize(
    ("tool", "kind", "good"),
    [(GAME_CHARACTERS, "characters", ROSTER), (GAME_EVENTS, "calendars", CALENDAR)],
)
def test_a_failed_read_is_not_kept(tool, kind, good):
    source = sample_source()
    getattr(source, kind)["hsr"] = NETWORK_ERROR
    tools = make_tools(source)

    _, first_failed = tools.run(tool, {})
    getattr(source, kind)["hsr"] = good
    _, second_failed = tools.run(tool, {})

    assert first_failed is True
    assert second_failed is False
    assert len(source.calls) == 2


def test_asking_for_every_mode_reads_only_the_modes_not_kept():
    clock = Clock()
    source = source_with(endgames=ENDGAMES)
    tools = GameDataTools(source, HSR, now=clock)

    tools.run(GAME_ENDGAME, {"mode": "pure_fiction"})
    clock.advance(minutes=5)
    data = answer(tools, GAME_ENDGAME, {})
    tools.run(GAME_ENDGAME, {})

    assert source.calls == [
        "hsr_endgame:pure_fiction",
        "hsr_endgame:memory_of_chaos,apocalyptic_shadow,anomaly_arbitration",
    ]
    assert [mode["mode"] for mode in data["modes"]] == list(ENDGAME_MODES)
    assert data["as_of"] == "2026-10-15 20:00"  # the oldest read among the modes shown


def test_a_failed_mode_is_asked_again_while_the_others_are_kept():
    source = source_with(endgames={**ENDGAMES, "pure_fiction": REFUSED_ERROR})
    tools = make_tools(source)

    tools.run(GAME_ENDGAME, {})
    source.endgames = dict(ENDGAMES)
    data = answer(tools, GAME_ENDGAME, {})

    assert source.calls == [f"hsr_endgame:{','.join(ENDGAME_MODES)}", "hsr_endgame:pure_fiction"]
    assert "errors" not in data
    assert data["modes"][1] == PURE_FICTION_JSON


def test_hours_left_are_counted_when_answering_so_a_kept_result_stays_right():
    clock = Clock()
    soon = replace(MEMORY_OF_CHAOS, ends_at=NOW + timedelta(hours=2, minutes=5))
    tools = GameDataTools(source_with(endgames={"memory_of_chaos": soon}), HSR, now=clock)

    first = answer(tools, GAME_ENDGAME, {"mode": "memory_of_chaos"})
    clock.advance(minutes=9)
    later = answer(tools, GAME_ENDGAME, {"mode": "memory_of_chaos"})

    assert first["modes"][0]["ends_in_hours"] == 3
    assert later["modes"][0]["ends_in_hours"] == 2
    assert later["as_of"] == "2026-10-15 20:00"


def test_a_kept_calendar_leaves_out_what_ended_since_it_was_read():
    clock = Clock()
    ends_soon, later = NOW + timedelta(minutes=20), NOW + timedelta(days=5)
    calendar_ = HsrCalendar(
        "3.7",
        (
            HsrWarp(
                "character", "Ending Warp", ("Acheron",), (), NOW - timedelta(days=20), ends_soon
            ),
            HsrWarp("character", "Next Warp", ("Firefly",), (), ends_soon, later),
        ),
        (
            HsrEvent("Ending Event", None, NOW + timedelta(minutes=30), None, None, False),
            HsrEvent("Later Event", None, later, None, None, False),
        ),
        (
            HsrChallengeWindow(
                "pure_fiction", "Ending Season", None, NOW + timedelta(minutes=40), 4, 12
            ),
            HsrChallengeWindow("memory_of_chaos", "Later Season", None, later, 30, 36),
        ),
    )
    source = source_with(hsr_calendar=calendar_)
    tools = GameDataTools(source, HSR, now=clock)

    tools.run(GAME_EVENTS, {})
    clock.advance(minutes=50)
    data = answer(tools, GAME_EVENTS, {})

    assert source.calls == ["hsr_calendar"]
    assert [warp["name"] for warp in data["warps"]] == ["Next Warp"]
    assert [event["name"] for event in data["events"]] == ["Later Event"]
    assert [window["name"] for window in data["endgame"]] == ["Later Season"]


def test_a_kept_calendar_lists_the_events_and_windows_that_started_since_it_was_read():
    # A season and an event change while the calendar is kept: HoYoLAB listed the new ones
    # as upcoming when it was read.
    clock = Clock()
    change, later = NOW + timedelta(minutes=30), NOW + timedelta(days=21)
    calendar_ = HsrCalendar(
        "3.7",
        (),
        (
            HsrEvent("Old Event", NOW - timedelta(days=21), change, None, None, False),
            HsrEvent("New Event", change, later, None, None, False),
        ),
        (
            HsrChallengeWindow(
                "memory_of_chaos", "Old Season", NOW - timedelta(days=42), change, 36, 36
            ),
            HsrChallengeWindow("memory_of_chaos", "New Season", change, later, 0, 36),
        ),
    )
    source = source_with(hsr_calendar=calendar_)
    tools = GameDataTools(source, HSR, now=clock)

    before = answer(tools, GAME_EVENTS, {})
    clock.advance(minutes=40)
    after = answer(tools, GAME_EVENTS, {})

    assert source.calls == ["hsr_calendar"]
    assert [event["name"] for event in before["events"]] == ["Old Event"]
    assert [window["name"] for window in before["endgame"]] == ["Old Season"]
    assert [event["name"] for event in after["events"]] == ["New Event"]
    assert [window["name"] for window in after["endgame"]] == ["New Season"]


def test_events_and_windows_yet_to_start_take_no_place_among_those_kept():
    starts = NOW + timedelta(minutes=1)
    calendar_ = HsrCalendar(
        "3.7",
        (),
        (
            HsrEvent("Upcoming Event", starts, None, None, None, False),
            *(HsrEvent(f"Event {i:02}", None, None, None, None, False) for i in range(15)),
        ),
        (
            HsrChallengeWindow("memory_of_chaos", "Upcoming Window", starts, None, None, None),
            *(
                HsrChallengeWindow(mode, f"Window {mode}", None, None, None, None)
                for mode in ENDGAME_MODES
            ),
        ),
    )

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), GAME_EVENTS, {})

    assert [event["name"] for event in data["events"]] == [f"Event {i:02}" for i in range(15)]
    assert [window["name"] for window in data["endgame"]] == [
        f"Window {mode}" for mode in ENDGAME_MODES
    ]


def test_what_ended_takes_no_place_among_the_warps_events_and_windows_kept():
    ended = NOW - timedelta(minutes=1)
    calendar_ = HsrCalendar(
        "3.7",
        (
            HsrWarp("character", "Ended Warp", ("Sample",), (), None, ended),
            *(HsrWarp("character", f"Warp {i}", ("Sample",), (), None, None) for i in range(8)),
        ),
        (
            HsrEvent("Ended Event", None, ended, None, None, False),
            *(HsrEvent(f"Event {i:02}", None, None, None, None, False) for i in range(15)),
        ),
        (
            HsrChallengeWindow("memory_of_chaos", "Ended Window", None, ended, None, None),
            *(
                HsrChallengeWindow(mode, f"Window {mode}", None, None, None, None)
                for mode in ENDGAME_MODES
            ),
        ),
    )

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), GAME_EVENTS, {})

    assert [warp["name"] for warp in data["warps"]] == [f"Warp {i}" for i in range(8)]
    assert [event["name"] for event in data["events"]] == [f"Event {i:02}" for i in range(15)]
    assert len(data["endgame"]) == 4
    assert "Ended Window" not in [window["name"] for window in data["endgame"]]


class AskingDuringFetch(FakeSource):
    """While the characters are read, asks the tools about events from another thread."""

    tools: GameDataTools
    answered_during_fetch = False

    def hsr_characters(self) -> tuple[HsrCharacter, ...]:
        other_turn = threading.Thread(target=self.tools.run, args=(GAME_EVENTS, {}), daemon=True)
        other_turn.start()
        other_turn.join(timeout=5)
        self.answered_during_fetch = not other_turn.is_alive()
        return super().hsr_characters()


def test_the_cache_lock_is_not_held_while_hoyolab_is_read():
    source = AskingDuringFetch(characters={"hsr": ROSTER}, calendars={"hsr": CALENDAR})
    source.tools = make_tools(source)

    _, is_error = source.tools.run(GAME_CHARACTERS, {})

    assert is_error is False
    assert source.answered_during_fetch is True
    assert source.calls == ["hsr_calendar", "hsr_characters"]
