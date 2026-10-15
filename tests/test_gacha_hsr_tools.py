import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from waifu.brain.tools import to_anthropic
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.hsr_tools import HSR_CHARACTERS, HSR_ENDGAME, HSR_EVENTS, HsrTools
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

WIB = timezone(timedelta(hours=7))
CST = timezone(timedelta(hours=8))  # the Asia server's time, as HoYoLAB gives it
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=WIB)
TOOL_NAMES = [HSR_CHARACTERS, HSR_ENDGAME, HSR_EVENTS]
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


class FakeSource:
    """HoyolabClient's HSR methods: a GachaError result is raised, or returned for its mode."""

    def __init__(
        self,
        characters: tuple[HsrCharacter, ...] | GachaError,
        endgames: dict[str, HsrEndgame | GachaError],
        hsr_calendar: HsrCalendar | GachaError,
    ) -> None:
        self.characters = characters
        self.endgames = endgames
        self.calendar = hsr_calendar
        self.calls: list[str] = []

    def hsr_characters(self) -> tuple[HsrCharacter, ...]:
        self.calls.append("characters")
        return _raise_error(self.characters)

    def hsr_endgame(self, modes):
        modes = list(modes)
        self.calls.append(f"endgame:{','.join(modes)}")
        return {mode: self.endgames[mode] for mode in modes}

    def hsr_calendar(self) -> HsrCalendar:
        self.calls.append("calendar")
        return _raise_error(self.calendar)


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
]


def sample_source() -> FakeSource:
    name, light_cone, path, season, floor, version, warp, featured, event = SAMPLE_LIVE_TEXT
    return FakeSource(
        (character(name, light_cone, path),),
        {mode: endgame(mode, season, floor) for mode in ENDGAME_MODES},
        calendar(version, warp, featured, event),
    )


def other_source() -> FakeSource:
    name, light_cone, path, season, floor, version, warp, featured, event = OTHER_LIVE_TEXT
    return FakeSource(
        (character(name, light_cone, path),),
        {mode: endgame(mode, season, floor) for mode in ENDGAME_MODES},
        calendar(version, warp, featured, event),
    )


def make_tools(source: FakeSource | None = None) -> HsrTools:
    return HsrTools(source or sample_source(), now=lambda: NOW)


def spec(tools: HsrTools, name: str):
    return next(s for s in tools.specs() if s.name == name)


def wire(tools: HsrTools) -> str:
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


def test_three_hsr_tools_with_their_arguments():
    tools = make_tools()

    assert [s.name for s in tools.specs()] == ["hsr_characters", "hsr_endgame", "hsr_events"]
    assert spec(tools, HSR_CHARACTERS).input_schema == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    assert spec(tools, HSR_ENDGAME).input_schema == {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": list(ENDGAME_MODES)}},
        "additionalProperties": False,
    }
    assert spec(tools, HSR_EVENTS).input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_every_object_forbids_additional_properties_within_strict_tool_use():
    for tool in make_tools().specs():
        for node in schema_nodes(tool.input_schema):
            assert not UNSUPPORTED_STRICT_KEYWORDS & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False


def test_optional_arguments_are_not_required():
    for tool in make_tools().specs():
        assert tool.input_schema.get("required", []) == []


def test_the_mode_enum_is_the_endgame_modes_in_display_order():
    mode = spec(make_tools(), HSR_ENDGAME).input_schema["properties"]["mode"]

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


def test_the_endgame_description_names_every_mode():
    description = spec(make_tools(), HSR_ENDGAME).description

    for name in ENDGAME_MODES.values():
        assert name in description


def test_specs_are_byte_identical_across_calls_and_source_data_for_prompt_caching():
    source = sample_source()
    tools = make_tools(source)
    first = wire(tools)

    for name in TOOL_NAMES:
        tools.run(name, {})

    assert wire(tools) == first
    assert wire(make_tools(other_source())) == first


def test_specs_never_read_the_source():
    source = sample_source()

    make_tools(source).specs()

    assert source.calls == []


def test_descriptions_carry_no_live_data():
    descriptions = " ".join(s.description for s in make_tools().specs())

    for text in SAMPLE_LIVE_TEXT + OTHER_LIVE_TEXT:
        assert text not in descriptions


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
        "Eidolon",
        "Light Cone",
        "Superimposition",
        "Memory of Chaos",
        "new to you",
        "Never suggest pulling, spending money, or topping up",
        "Do not save this data with `remember` unless Master asks",
        # The morning briefing turn offers every tool: banners and seasons ending are never
        # brought up unasked.
        "Call it only when Master asks about Honkai: Star Rail",
        "never in your own greeting or the morning briefing",
    ],
)
def test_every_description_carries_the_answer_rules(tool, rule):
    assert rule in spec(make_tools(), tool).description


@pytest.mark.parametrize("tool", [HSR_ENDGAME, HSR_EVENTS])
def test_endgame_descriptions_never_let_her_claim_missing_stars(tool):
    assert "Never claim how many stars are missing" in spec(make_tools(), tool).description


def test_the_events_description_presents_banners_only_as_information():
    assert "only as information" in spec(make_tools(), HSR_EVENTS).description


def test_the_characters_description_says_a_path_can_be_named_and_more_names_the_others():
    description = spec(make_tools(), HSR_CHARACTERS).description

    assert "a name with its Path, such as Trailblazer Harmony, gives that one" in description
    assert "`more` names the others with their Path" in description


# --- arguments -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (HSR_CHARACTERS, "Acheron"),
        (HSR_CHARACTERS, None),
        (HSR_CHARACTERS, ["Acheron"]),
        (HSR_CHARACTERS, {"name": ""}),
        (HSR_CHARACTERS, {"name": "   "}),
        (HSR_CHARACTERS, {"name": 5}),
        (HSR_CHARACTERS, {"name": ["Acheron"]}),
        (HSR_CHARACTERS, {"name": "x" * 61}),
        (HSR_CHARACTERS, {"name": "Acheron", "eidolon": 2}),
        (HSR_CHARACTERS, {"query": "Acheron"}),
        (HSR_ENDGAME, {"mode": "forgotten_hall"}),
        (HSR_ENDGAME, {"mode": "Memory of Chaos"}),
        (HSR_ENDGAME, {"mode": 1}),
        (HSR_ENDGAME, {"mode": ["pure_fiction"]}),
        (HSR_ENDGAME, {"modes": ["pure_fiction"]}),
        (HSR_ENDGAME, "pure_fiction"),
        (HSR_ENDGAME, None),
        (HSR_EVENTS, {"month": 10}),
        (HSR_EVENTS, ["warps"]),
        (HSR_EVENTS, None),
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
        (HSR_CHARACTERS, {}),
        (HSR_CHARACTERS, {"name": None}),
        (HSR_CHARACTERS, {"name": "Acheron"}),
        (HSR_CHARACTERS, {"name": "  march 7th "}),
        (HSR_CHARACTERS, {"name": "x" * 60}),
        (HSR_ENDGAME, {}),
        (HSR_ENDGAME, {"mode": None}),
        *((HSR_ENDGAME, {"mode": mode}) for mode in ENDGAME_MODES),
        (HSR_EVENTS, {}),
    ],
)
def test_valid_arguments_are_accepted(tool, arguments):
    result, _ = make_tools().run(tool, arguments)

    assert "tidak valid" not in json.loads(result).get("message", "")


def test_an_unknown_tool_name_is_an_error_without_asking_hoyolab():
    source = sample_source()

    result, is_error = make_tools(source).run("hsr_relics", {})

    assert is_error is True
    assert json.loads(result) == {"ok": False, "message": "Tool 'hsr_relics' bukan tool HSR."}
    assert source.calls == []


# --- results: shared fixtures ---------------------------------------------------------------

NETWORK_ERROR = GachaError("network", "HoYoLAB tidak bisa dihubungi. Cek koneksi internet.")
REFUSED_ERROR = GachaError("other", "HoYoLAB menolak permintaan HSR (kode -1).")
EMPTY_CALENDAR = HsrCalendar(None, (), (), ())


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
    hsr_calendar: HsrCalendar | GachaError = EMPTY_CALENDAR,
) -> FakeSource:
    return FakeSource(characters, endgames or {}, hsr_calendar)


def answer(tools: HsrTools, tool: str, arguments: dict) -> dict:
    result, is_error = tools.run(tool, arguments)
    assert is_error is False, result
    return json.loads(result)


def compact(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


# --- hsr_characters: the roster -------------------------------------------------------------

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

    data = answer(make_tools(source), HSR_CHARACTERS, {})

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
    assert source.calls == ["characters"]


def test_results_are_compact_json_that_keeps_non_ascii_names():
    tools = make_tools(source_with((hsr_character("Dan Heng • Imbibitor Lunae"),)))

    result, _ = tools.run(HSR_CHARACTERS, {})

    assert "Dan Heng • Imbibitor Lunae" in result
    assert result == compact(json.loads(result))


def test_a_roster_beyond_120_characters_keeps_the_first_120_rows_and_says_so():
    characters = tuple(hsr_character(f"Sample {i:03}", level=i % 80 + 1) for i in range(130))

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {})

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

    result, is_error = make_tools(source_with(characters)).run(HSR_CHARACTERS, {})

    assert is_error is False
    assert len(json.loads(result)["rows"]) == 80
    assert utf8_size(result) <= 8_000


# --- hsr_characters: one character ----------------------------------------------------------


def test_a_named_character_comes_with_light_cone_stats_and_relics():
    data = answer(make_tools(source_with(ROSTER)), HSR_CHARACTERS, {"name": "acheron"})

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
    data = answer(make_tools(source_with(ROSTER)), HSR_CHARACTERS, {"name": "Castorice"})

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


def matched_names(tools: HsrTools, query: str) -> list[str]:
    return [c["name"] for c in answer(tools, HSR_CHARACTERS, {"name": query})["characters"]]


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

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": query})

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

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "trailblazer"})

    assert [c["path"] for c in data["characters"]] == ["Destruction", "Preservation", "Remembrance"]


TRAILBLAZERS = (
    hsr_character("Trailblazer", path="Destruction", element="Physical"),
    hsr_character("Trailblazer", path="Preservation", element="Fire"),
    hsr_character("Trailblazer", path="Remembrance", element="Ice"),
    hsr_character("Trailblazer", path="Harmony", element="Imaginary"),
)


def test_a_fourth_trailblazer_path_is_named_under_more_so_it_can_be_asked_for():
    characters = (*TRAILBLAZERS, FIREFLY)

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "Trailblazer"})

    assert [c["path"] for c in data["characters"]] == ["Destruction", "Preservation", "Remembrance"]
    assert data["more"] == [{"name": "Trailblazer", "path": "Harmony"}]


@pytest.mark.parametrize(
    "query", ["Trailblazer Harmony", "harmony trailblazer", "Trailblazer (Harmony)"]
)
def test_a_name_with_its_path_gives_that_one_character(query):
    characters = (*TRAILBLAZERS, FIREFLY)

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": query})

    assert [(c["name"], c["path"]) for c in data["characters"]] == [("Trailblazer", "Harmony")]
    assert "more" not in data


def test_a_two_word_path_names_one_march_7th():
    characters = (
        hsr_character("March 7th", rarity=4, path="The Hunt", element="Imaginary"),
        hsr_character("March 7th", rarity=4, path="Preservation", element="Ice"),
    )

    data = answer(
        make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "March 7th The Hunt"}
    )

    assert [(c["name"], c["path"]) for c in data["characters"]] == [("March 7th", "The Hunt")]


def test_at_most_three_characters_come_back_and_more_names_the_others():
    characters = tuple(hsr_character(f"Sample Hero {i}") for i in range(5))

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "sample hero"})

    assert [c["name"] for c in data["characters"]] == [f"Sample Hero {i}" for i in range(3)]
    assert data["more"] == [
        {"name": "Sample Hero 3", "path": "Nihility"},
        {"name": "Sample Hero 4", "path": "Nihility"},
    ]


def test_more_names_at_most_six_others():
    characters = tuple(hsr_character(f"Sample Hero {i:02}") for i in range(12))

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "sample hero"})

    assert [entry["name"] for entry in data["more"]] == [f"Sample Hero {i:02}" for i in range(3, 9)]


def test_a_misheard_name_gives_close_names_instead_of_an_error():
    result, is_error = make_tools(source_with(ROSTER)).run(HSR_CHARACTERS, {"name": "Aceron"})

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

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "marsh 7th"})

    assert data["close"] == ["March 7th"]


def test_at_most_three_close_names():
    characters = tuple(hsr_character(f"Sample Hero {letter}") for letter in "ABCD")

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "sample hera"})

    assert data["characters"] == []
    assert len(data["close"]) == 3


def test_a_name_without_letters_or_digits_matches_nothing():
    data = answer(make_tools(source_with(ROSTER)), HSR_CHARACTERS, {"name": "?!"})

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

    data = answer(make_tools(source_with(characters)), HSR_CHARACTERS, {"name": "Trailblazer"})

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


# --- hsr_endgame ----------------------------------------------------------------------------

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

    data = answer(make_tools(source), HSR_ENDGAME, {"mode": "memory_of_chaos"})

    assert data == {"as_of": "2026-10-15 20:00", "modes": [MEMORY_OF_CHAOS_JSON]}
    assert source.calls == ["endgame:memory_of_chaos"]


def test_pure_fiction_floors_carry_their_score():
    data = answer(make_tools(source_with(endgames=ENDGAMES)), HSR_ENDGAME, {"mode": "pure_fiction"})

    assert data["modes"] == [PURE_FICTION_JSON]


def test_values_a_season_does_not_have_are_left_out():
    tools = make_tools(source_with(endgames=ENDGAMES))

    data = answer(tools, HSR_ENDGAME, {"mode": "apocalyptic_shadow"})

    assert data["modes"] == [APOCALYPTIC_SHADOW_JSON]


def test_without_a_mode_every_endgame_mode_comes_in_display_order():
    source = source_with(endgames=ENDGAMES)

    data = answer(make_tools(source), HSR_ENDGAME, {})

    assert data == {
        "as_of": "2026-10-15 20:00",
        "modes": [
            MEMORY_OF_CHAOS_JSON,
            PURE_FICTION_JSON,
            APOCALYPTIC_SHADOW_JSON,
            ANOMALY_ARBITRATION_JSON,
        ],
    }
    assert source.calls == [f"endgame:{','.join(ENDGAME_MODES)}"]


def test_the_endgame_never_gives_a_maximum_of_stars():
    data = answer(make_tools(source_with(endgames=ENDGAMES)), HSR_ENDGAME, {})

    assert [key for mode in data["modes"] for key in mode if "max" in key] == []


def test_a_mode_that_failed_is_under_errors_without_hiding_the_others():
    endgames = {**ENDGAMES, "pure_fiction": REFUSED_ERROR}

    result, is_error = make_tools(source_with(endgames=endgames)).run(HSR_ENDGAME, {})

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

    result, is_error = make_tools(source_with(endgames=endgames)).run(HSR_ENDGAME, arguments)

    assert is_error is True
    assert json.loads(result) == {"ok": False, "message": NETWORK_ERROR.message}


def test_different_failures_of_every_mode_are_each_told_once():
    endgames = {**dict.fromkeys(ENDGAME_MODES, NETWORK_ERROR), "pure_fiction": REFUSED_ERROR}

    result, is_error = make_tools(source_with(endgames=endgames)).run(HSR_ENDGAME, {})

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": f"{NETWORK_ERROR.message} {REFUSED_ERROR.message}",
    }


def test_hours_left_never_go_below_zero():
    ended = replace(MEMORY_OF_CHAOS, ends_at=NOW - timedelta(minutes=5))
    tools = make_tools(source_with(endgames={"memory_of_chaos": ended}))

    data = answer(tools, HSR_ENDGAME, {"mode": "memory_of_chaos"})

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

    result, is_error = make_tools(source_with(endgames=endgames)).run(HSR_ENDGAME, {})

    assert is_error is False
    assert len(json.loads(result)["modes"]) == 4
    assert utf8_size(result) <= 2_000


# --- hsr_events -----------------------------------------------------------------------------

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

    data = answer(make_tools(source), HSR_EVENTS, {})

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
    assert source.calls == ["calendar"]


def test_a_warp_gives_its_start_only_until_it_starts():
    clock = Clock()
    starts, ends = NOW + timedelta(minutes=30), NOW + timedelta(days=21)
    warp = HsrWarp("character", "Next Warp", ("Firefly",), ("Lynx",), starts, ends)
    tools = HsrTools(source_with(hsr_calendar=HsrCalendar("3.7", (warp,), (), ())), now=clock)

    before = answer(tools, HSR_EVENTS, {})["warps"][0]
    clock.advance(minutes=40)
    after = answer(tools, HSR_EVENTS, {})["warps"][0]

    assert (before["starts"], before["starts_in_hours"]) == ("2026-10-15 20:30", 1)
    assert "starts" not in after
    assert "starts_in_hours" not in after
    assert after["ends"] == "2026-11-05 20:00"


def test_a_warp_hoyolab_gives_no_name_leaves_the_name_out():
    # HoYoLAB's calendar often names no warp; the featured names say which one it is.
    warp = HsrWarp("character", "", ("Firefly",), ("Lynx",), None, NOW + timedelta(days=5))
    tools = make_tools(source_with(hsr_calendar=HsrCalendar("3.7", (warp,), (), ())))

    shown = answer(tools, HSR_EVENTS, {})["warps"][0]

    assert "name" not in shown
    assert shown["featured"] == ["Firefly"]


def test_a_calendar_without_a_version_leaves_it_out():
    data = answer(make_tools(source_with()), HSR_EVENTS, {})

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

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), HSR_EVENTS, {})

    assert [warp["name"] for warp in data["warps"]] == [f"Warp {i}" for i in range(8)]
    assert [event["name"] for event in data["events"]] == [
        f"Event {i:02}" for i in range(19, 4, -1)
    ]
    assert [window["name"] for window in data["endgame"]] == [f"Window {i}" for i in range(4)]


def test_event_names_are_cut_to_60_characters():
    name = "Sample Event With a Name Far Longer Than Anyone Would Say Out Loud Twice"
    calendar_ = HsrCalendar(None, (), (HsrEvent(name, None, None, None, None, False),), ())

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), HSR_EVENTS, {})

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
    result, is_error = make_tools(source_with(hsr_calendar=busy_calendar())).run(HSR_EVENTS, {})

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

    result, is_error = tools.run(HSR_EVENTS, {})

    data = json.loads(result)
    assert is_error is False
    assert [len(data[key]) for key in ("warps", "events", "endgame")] == [8, 15, 4]
    assert {len(event["name"]) for event in data["events"]} == {60}
    assert utf8_size(result) <= 5_000


# --- failures and privacy -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (HSR_CHARACTERS, {}),
        (HSR_CHARACTERS, {"name": "Acheron"}),
        (HSR_ENDGAME, {"mode": "pure_fiction"}),
        (HSR_EVENTS, {}),
    ],
)
def test_a_hoyolab_error_becomes_an_error_result_with_its_message(tool, arguments):
    source = FakeSource(NETWORK_ERROR, dict.fromkeys(ENDGAME_MODES, NETWORK_ERROR), NETWORK_ERROR)

    result, is_error = make_tools(source).run(tool, arguments)

    assert is_error is True
    assert json.loads(result) == {"ok": False, "message": NETWORK_ERROR.message}


def test_no_result_holds_the_uid_or_the_nickname():
    source = sample_source()
    source.uid = 800000001
    source.nickname = "SampleNickname"
    tools = make_tools(source)

    results = [
        tools.run(tool, arguments)[0]
        for tool, arguments in [
            (HSR_CHARACTERS, {}),
            (HSR_CHARACTERS, {"name": "Acheron"}),
            (HSR_CHARACTERS, {"name": "Sampel"}),
            (HSR_ENDGAME, {}),
            (HSR_EVENTS, {}),
        ]
    ]

    for result in results:
        assert "800000001" not in result
        assert "SampleNickname" not in result


# --- the cache ------------------------------------------------------------------------------


def test_a_second_question_about_characters_within_15_minutes_reuses_the_roster():
    clock = Clock()
    source = source_with(ROSTER)
    tools = HsrTools(source, now=clock)

    tools.run(HSR_CHARACTERS, {})
    clock.advance(minutes=14)
    data = answer(tools, HSR_CHARACTERS, {"name": "Acheron"})

    assert source.calls == ["characters"]
    assert data["as_of"] == "2026-10-15 20:00"


@pytest.mark.parametrize(
    ("tool", "arguments", "call", "ttl_minutes"),
    [
        (HSR_CHARACTERS, {}, "characters", 15),
        (HSR_ENDGAME, {"mode": "pure_fiction"}, "endgame:pure_fiction", 10),
        (HSR_EVENTS, {}, "calendar", 60),
    ],
)
def test_a_result_is_kept_for_its_time_then_read_again(tool, arguments, call, ttl_minutes):
    clock = Clock()
    source = sample_source()
    tools = HsrTools(source, now=clock)

    tools.run(tool, arguments)
    clock.advance(minutes=ttl_minutes - 0.5)
    tools.run(tool, arguments)
    kept_calls = list(source.calls)
    clock.advance(minutes=0.5)
    data = answer(tools, tool, arguments)

    assert kept_calls == [call]
    assert source.calls == [call, call]
    assert data["as_of"] == (NOW + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M")


def test_a_clock_set_back_reads_again():
    clock = Clock()
    source = source_with(ROSTER)
    tools = HsrTools(source, now=clock)

    tools.run(HSR_CHARACTERS, {})
    clock.advance(minutes=-60)
    tools.run(HSR_CHARACTERS, {})

    assert source.calls == ["characters", "characters"]


@pytest.mark.parametrize(
    ("tool", "attribute", "good"),
    [(HSR_CHARACTERS, "characters", ROSTER), (HSR_EVENTS, "calendar", CALENDAR)],
)
def test_a_failed_read_is_not_kept(tool, attribute, good):
    source = sample_source()
    setattr(source, attribute, NETWORK_ERROR)
    tools = make_tools(source)

    _, first_failed = tools.run(tool, {})
    setattr(source, attribute, good)
    _, second_failed = tools.run(tool, {})

    assert first_failed is True
    assert second_failed is False
    assert len(source.calls) == 2


def test_asking_for_every_mode_reads_only_the_modes_not_kept():
    clock = Clock()
    source = source_with(endgames=ENDGAMES)
    tools = HsrTools(source, now=clock)

    tools.run(HSR_ENDGAME, {"mode": "pure_fiction"})
    clock.advance(minutes=5)
    data = answer(tools, HSR_ENDGAME, {})
    tools.run(HSR_ENDGAME, {})

    assert source.calls == [
        "endgame:pure_fiction",
        "endgame:memory_of_chaos,apocalyptic_shadow,anomaly_arbitration",
    ]
    assert [mode["mode"] for mode in data["modes"]] == list(ENDGAME_MODES)
    assert data["as_of"] == "2026-10-15 20:00"  # the oldest read among the modes shown


def test_a_failed_mode_is_asked_again_while_the_others_are_kept():
    source = source_with(endgames={**ENDGAMES, "pure_fiction": REFUSED_ERROR})
    tools = make_tools(source)

    tools.run(HSR_ENDGAME, {})
    source.endgames = ENDGAMES
    data = answer(tools, HSR_ENDGAME, {})

    assert source.calls == [f"endgame:{','.join(ENDGAME_MODES)}", "endgame:pure_fiction"]
    assert "errors" not in data
    assert data["modes"][1] == PURE_FICTION_JSON


def test_hours_left_are_counted_when_answering_so_a_kept_result_stays_right():
    clock = Clock()
    soon = replace(MEMORY_OF_CHAOS, ends_at=NOW + timedelta(hours=2, minutes=5))
    tools = HsrTools(source_with(endgames={"memory_of_chaos": soon}), now=clock)

    first = answer(tools, HSR_ENDGAME, {"mode": "memory_of_chaos"})
    clock.advance(minutes=9)
    later = answer(tools, HSR_ENDGAME, {"mode": "memory_of_chaos"})

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
    tools = HsrTools(source, now=clock)

    tools.run(HSR_EVENTS, {})
    clock.advance(minutes=50)
    data = answer(tools, HSR_EVENTS, {})

    assert source.calls == ["calendar"]
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
    tools = HsrTools(source, now=clock)

    before = answer(tools, HSR_EVENTS, {})
    clock.advance(minutes=40)
    after = answer(tools, HSR_EVENTS, {})

    assert source.calls == ["calendar"]
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

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), HSR_EVENTS, {})

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

    data = answer(make_tools(source_with(hsr_calendar=calendar_)), HSR_EVENTS, {})

    assert [warp["name"] for warp in data["warps"]] == [f"Warp {i}" for i in range(8)]
    assert [event["name"] for event in data["events"]] == [f"Event {i:02}" for i in range(15)]
    assert len(data["endgame"]) == 4
    assert "Ended Window" not in [window["name"] for window in data["endgame"]]


class AskingDuringFetch(FakeSource):
    """While the characters are read, asks the tools about events from another thread."""

    tools: HsrTools
    answered_during_fetch = False

    def hsr_characters(self) -> tuple[HsrCharacter, ...]:
        other_turn = threading.Thread(target=self.tools.run, args=(HSR_EVENTS, {}), daemon=True)
        other_turn.start()
        other_turn.join(timeout=5)
        self.answered_during_fetch = not other_turn.is_alive()
        return super().hsr_characters()


def test_the_cache_lock_is_not_held_while_hoyolab_is_read():
    source = AskingDuringFetch(ROSTER, {}, CALENDAR)
    source.tools = make_tools(source)

    _, is_error = source.tools.run(HSR_CHARACTERS, {})

    assert is_error is False
    assert source.answered_during_fetch is True
    assert source.calls == ["calendar", "characters"]
