import json
import logging
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.base import ToolCallRecord, ToolSpec
from waifu.brain.memory_tools import MemoryTools, changed_memory
from waifu.brain.tools import build_tool_specs, run_tool, to_anthropic, to_ollama
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.gacha.tools import GachaTools
from waifu.memory import MemoryStore

NOW = datetime(2026, 10, 12, 14, 30)
UNSUPPORTED_STRICT_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
}


def make_store(tmp_path, **kwargs) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.json", now=lambda: NOW, **kwargs)


def spec(tools: MemoryTools, name: str) -> ToolSpec:
    return next(s for s in tools.specs() if s.name == name)


def run(tools: MemoryTools, name: str, arguments: object) -> tuple[dict, bool]:
    result, is_error = tools.run(name, arguments)
    return json.loads(result), is_error


def texts(store: MemoryStore) -> list[tuple[str, str]]:
    return [(fact.id, fact.text) for fact in store.facts()]


def schema_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from schema_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from schema_nodes(child)


def house() -> DeviceRegistry:
    return DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar", kind="light")])


class Unreachable:
    """HoYoLAB and the pull history: listing the game tools must not reach them."""

    def status(self, games):
        raise AssertionError("HoYoLAB must not be reached")

    def report(self, game_id, *, refresh=True, full=False):
        raise AssertionError("the pull history must not be read")


# --- specs ---------------------------------------------------------------------------------


def test_two_tools_remember_and_forget_with_strict_schemas(tmp_path):
    tools = MemoryTools(make_store(tmp_path))

    assert [s.name for s in tools.specs()] == ["remember", "forget"]
    assert spec(tools, "remember").input_schema == {
        "type": "object",
        "properties": {"text": {"type": "string"}, "replaces": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    assert spec(tools, "forget").input_schema == {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    }


def test_schemas_stay_within_strict_tool_use_limits(tmp_path):
    for tool_spec in MemoryTools(make_store(tmp_path)).specs():
        for node in schema_nodes(tool_spec.input_schema):
            assert not UNSUPPORTED_STRICT_KEYWORDS & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False


def test_remember_says_what_to_save_how_and_what_never_to_save(tmp_path):
    description = spec(MemoryTools(make_store(tmp_path, max_chars=150)), "remember").description

    for guidance in [
        "lasting",
        "asks you to remember",
        "Indonesian",
        "third person",
        "Master",
        "one fact per call",
        "date",
        "at most 150 characters",
        "small talk",
        "device commands",
        "already",
        "passwords",
        "PINs",
        "OTPs",
        "tokens",
        "cookies",
        "card numbers",
        "`replaces`",
        "id",
    ]:
        assert guidance in description


# Written in lowercase, the word still opens the example sentence with a capital.
@pytest.mark.parametrize("owner_call", ["Sayang", "sayang"])
def test_the_note_example_calls_the_owner_what_she_calls_them(tmp_path, owner_call):
    default = spec(MemoryTools(make_store(tmp_path)), "remember").description
    sayang = spec(MemoryTools(make_store(tmp_path), owner_call=owner_call), "remember").description

    assert "('Master alergi udang.')" in default
    assert "('Sayang alergi udang.')" in sayang
    assert "'Master" not in sayang
    assert "fact about Master" in sayang  # her instructions keep Master


def test_forget_deletes_by_id_and_points_changed_facts_to_replaces(tmp_path):
    description = spec(MemoryTools(make_store(tmp_path)), "forget").description

    assert "id" in description
    assert "asks you to forget" in description
    assert "`replaces`" in description


def test_specs_stay_identical_while_notes_change_for_prompt_caching(tmp_path):
    tools = MemoryTools(make_store(tmp_path))
    first = tools.specs()

    tools.run("remember", {"text": "Master suka kopi."})
    tools.run("forget", {"id": "m1"})

    assert tools.specs() == first


# --- remember ------------------------------------------------------------------------------


def test_remember_saves_a_note_and_answers_with_its_id(tmp_path):
    store = make_store(tmp_path)

    result, is_error = MemoryTools(store).run("remember", {"text": "Master alergi udang."})

    assert result == '{"ok":true,"id":"m1","message":"Tersimpan."}'
    assert is_error is False
    assert texts(store) == [("m1", "Master alergi udang.")]


def test_remembering_a_saved_note_again_returns_its_id_without_a_second_note(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    store.add("Master alergi udang.")

    data, is_error = run(MemoryTools(store), "remember", {"text": "master ALERGI udang."})

    assert (data, is_error) == ({"ok": True, "id": "m2", "message": "Tersimpan."}, False)
    assert texts(store) == [("m1", "Master suka kopi."), ("m2", "Master alergi udang.")]


def test_remember_with_replaces_updates_that_note_in_place(tmp_path):
    store = make_store(tmp_path)
    store.add("Master kerja di Bandung.")
    store.add("Master suka kopi.")

    data, is_error = run(
        MemoryTools(store), "remember", {"text": "Master kerja di Jakarta.", "replaces": "m1"}
    )

    assert (data, is_error) == ({"ok": True, "id": "m1", "message": "Diperbarui."}, False)
    assert texts(store) == [("m1", "Master kerja di Jakarta."), ("m2", "Master suka kopi.")]


def test_an_explicit_null_replaces_saves_a_new_note(tmp_path):
    # Local models may send null for an optional argument instead of leaving it out.
    store = make_store(tmp_path)

    data, is_error = run(
        MemoryTools(store), "remember", {"text": "Master suka kopi.", "replaces": None}
    )

    assert (data, is_error) == ({"ok": True, "id": "m1", "message": "Tersimpan."}, False)


@pytest.mark.parametrize(
    "arguments",
    [
        {"text": "password wifi Master: kucing123"},
        {"text": "password wifi Master: kucing123", "replaces": "m1"},
    ],
    ids=["new note", "replacement"],
)
def test_a_secret_is_refused_and_never_echoed(tmp_path, arguments):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")

    result, is_error = MemoryTools(store).run("remember", arguments)

    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    assert "rahasia" in data["message"]
    assert "kucing123" not in result
    assert texts(store) == [("m1", "Master suka kopi.")]


def test_a_full_memory_refuses_a_new_note_and_says_what_to_do(tmp_path):
    store = make_store(tmp_path, max_facts=1)
    store.add("Master suka kopi.")

    data, is_error = run(MemoryTools(store), "remember", {"text": "Master main Genshin."})

    assert is_error is True
    assert data["ok"] is False
    assert "penuh" in data["message"]
    assert "Gabungkan" in data["message"]
    assert texts(store) == [("m1", "Master suka kopi.")]


def test_a_note_longer_than_the_limit_is_refused(tmp_path):
    store = make_store(tmp_path, max_chars=20)

    data, is_error = run(MemoryTools(store), "remember", {"text": "Master suka kopi tanpa gula."})

    assert (data, is_error) == (
        {"ok": False, "message": "Catatannya terlalu panjang, paling banyak 20 karakter."},
        True,
    )
    assert store.facts() == []


def test_replacing_with_the_text_of_another_note_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    store.add("Master suka teh.")

    data, is_error = run(
        MemoryTools(store), "remember", {"text": "Master suka kopi.", "replaces": "m2"}
    )

    assert is_error is True
    assert "m1" in data["message"]
    assert texts(store) == [("m1", "Master suka kopi."), ("m2", "Master suka teh.")]


# --- forget --------------------------------------------------------------------------------


def test_forget_deletes_the_note_and_says_which_in_readable_json(tmp_path):
    store = make_store(tmp_path)
    store.add("Master suka lagu 夜に駆ける.")
    store.add("Master alergi udang.")

    result, is_error = MemoryTools(store).run("forget", {"id": "m1"})

    assert result == '{"ok":true,"message":"Dihapus: Master suka lagu 夜に駆ける."}'
    assert is_error is False
    assert texts(store) == [("m2", "Master alergi udang.")]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("forget", {"id": "m9"}),
        ("remember", {"text": "Master suka teh.", "replaces": "m9"}),
    ],
)
def test_an_unknown_id_is_an_error_result(tmp_path, name, arguments):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")

    data, is_error = run(MemoryTools(store), name, arguments)

    assert (data, is_error) == ({"ok": False, "message": "Tidak ada catatan dengan id itu."}, True)
    assert texts(store) == [("m1", "Master suka kopi.")]


# --- bad calls -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("remember", None),
        ("remember", "Master suka teh."),
        ("remember", ["Master suka teh."]),
        ("remember", {}),
        ("remember", {"text": ""}),
        ("remember", {"text": " \n "}),
        ("remember", {"text": 42}),
        ("remember", {"text": None}),
        ("remember", {"text": "Master suka teh.", "replaces": ""}),
        ("remember", {"text": "Master suka teh.", "replaces": 1}),
        ("remember", {"text": "Master suka teh.", "tags": ["minuman"]}),
        ("forget", None),
        ("forget", {}),
        ("forget", {"id": ""}),
        ("forget", {"id": 1}),
        ("forget", {"id": "m1", "text": "Master suka kopi."}),
    ],
)
def test_invalid_arguments_are_error_results_that_leave_the_notes_alone(tmp_path, name, arguments):
    store = make_store(tmp_path)
    store.add("Master suka kopi.")
    saved = store.path.read_bytes()

    data, is_error = run(MemoryTools(store), name, arguments)

    assert is_error is True
    assert data["ok"] is False
    assert data["message"].startswith(f"Argumen {name} tidak valid: ")
    assert store.path.read_bytes() == saved


def test_an_unknown_tool_name_is_an_error_result(tmp_path):
    data, is_error = run(MemoryTools(make_store(tmp_path)), "control_device", {"id": "m1"})

    assert (data, is_error) == (
        {"ok": False, "message": "Tool 'control_device' bukan tool ingatan."},
        True,
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("remember", {"text": "Master suka kopi."}), ("forget", {"id": "m1"})],
)
def test_a_memory_file_that_cannot_be_read_is_logged_and_a_generic_error(
    tmp_path, caplog, name, arguments
):
    # A folder where the file should be: reading it raises an OSError, not a refused note.
    store = make_store(tmp_path)
    store.path.mkdir()

    with caplog.at_level(logging.ERROR, logger="waifu.brain.memory_tools"):
        data, is_error = run(MemoryTools(store), name, arguments)

    assert (data, is_error) == (
        {"ok": False, "message": "Ingatan tidak bisa diubah saat ini."},
        True,
    )
    assert name in caplog.text
    assert any(record.exc_info for record in caplog.records)


# --- beside the other tools ----------------------------------------------------------------


def test_memory_tools_come_after_the_device_and_game_tools(tmp_path):
    games = GachaTools(Unreachable(), Unreachable(), ["hsr", "genshin", "zzz"])

    specs = build_tool_specs(house(), [games, MemoryTools(make_store(tmp_path))])

    assert [s.name for s in specs] == [
        "control_device",
        "list_devices",
        "game_status",
        "gacha_history",
        "remember",
        "forget",
    ]


def test_memory_tools_convert_to_strict_claude_tools_and_ollama_tools(tmp_path):
    for tool_spec in MemoryTools(make_store(tmp_path)).specs():
        claude_tool = to_anthropic(tool_spec)
        assert claude_tool["strict"] is True
        assert (
            claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
        )

        ollama_tool = ollama.Tool.model_validate(to_ollama(tool_spec))
        assert ollama_tool.function.parameters.required == tool_spec.input_schema["required"]


def test_run_tool_hands_remember_and_forget_to_the_memory_tools(tmp_path):
    store = make_store(tmp_path)
    registry = house()
    providers = [MemoryTools(store)]

    saved, saved_error = run_tool(registry, "remember", {"text": "Master suka kopi."}, providers)
    assert (json.loads(saved), saved_error) == (
        {"ok": True, "id": "m1", "message": "Tersimpan."},
        False,
    )
    assert texts(store) == [("m1", "Master suka kopi.")]

    removed, removed_error = run_tool(registry, "forget", {"id": "m1"}, providers)
    assert (json.loads(removed), removed_error) == (
        {"ok": True, "message": "Dihapus: Master suka kopi."},
        False,
    )
    assert store.facts() == []
    assert registry.get("lampu_kamar").calls == []


# --- a rolled-back turn ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("calls", "changed"),
    [
        ([ToolCallRecord("remember", {"text": "Master suka kopi."}, "{}", False)], True),
        ([ToolCallRecord("forget", {"id": "m1"}, "{}", False)], True),
        ([ToolCallRecord("remember", {"text": "PIN ATM 4321"}, "{}", True)], False),
        ([ToolCallRecord("forget", {"id": "m9"}, "{}", True)], False),
        ([ToolCallRecord("control_device", {"device": "lampu_kamar"}, "{}", False)], False),
        (
            [
                ToolCallRecord("list_devices", {}, "{}", False),
                ToolCallRecord("remember", {"text": "Master suka teh."}, "{}", False),
            ],
            True,
        ),
        ([], False),
    ],
    ids=[
        "saved",
        "deleted",
        "refused",
        "unknown id",
        "device only",
        "saved after a device call",
        "no calls",
    ],
)
def test_changed_memory_tells_whether_a_turn_saved_or_deleted_a_note(calls, changed):
    assert changed_memory(calls) is changed
