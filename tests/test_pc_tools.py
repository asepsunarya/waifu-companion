"""The PC health tool: no test reads the real PC.

Every `PcTools` gets a fake `read` that hands out a made-up snapshot; no Windows counter and
no nvidia-smi is ever touched.
"""

import json
import logging

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.pc_tools import PcTools
from waifu.brain.tools import build_tool_specs, run_tool, to_anthropic, to_ollama
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.pc_health import Drive, GpuStatus, PcSnapshot

GPU = GpuStatus(
    name="NVIDIA GeForce RTX 5060 Ti",
    temp_c=61,
    load_percent=34,
    vram_used_gb=5.234,
    vram_total_gb=15.921,
    power_w=88.44,
)
FULL = PcSnapshot(
    cpu_percent=12.6,
    ram_used_gb=18.349,
    ram_total_gb=31.9,
    drives=(Drive("C:", 24.06, 465.1), Drive("D:", 812.56, 931.5)),
    gpu=GPU,
    uptime_s=7 * 3600 + 59 * 60 + 59,  # 7:59:59 is still 7 hours
)
FULL_JSON = (
    '{"cpu_percent":13,'
    '"ram":{"used_gb":18.3,"total_gb":31.9,"percent":58},'
    '"drives":[{"drive":"C:","free_gb":24.1,"total_gb":465.1,"free_percent":5},'
    '{"drive":"D:","free_gb":812.6,"total_gb":931.5,"free_percent":87}],'
    '"gpu":{"name":"NVIDIA GeForce RTX 5060 Ti","temp_c":61,"load_percent":34,'
    '"vram_used_gb":5.2,"vram_total_gb":15.9,"power_w":88.4},'
    '"uptime_hours":7}'
)
NOTHING_READ = PcSnapshot(None, None, None, (), None, None)
READ_FAILED = {"ok": False, "message": "Kondisi PC Master tidak bisa dibaca saat ini."}


class FakeRead:
    """Stands in for pc_health.read_snapshot: hands out the given snapshots, never reads the PC."""

    def __init__(self, *snapshots: PcSnapshot | Exception) -> None:
        self.snapshots = list(snapshots)
        self.calls = 0

    def __call__(self) -> PcSnapshot:
        self.calls += 1
        snapshot = self.snapshots[min(self.calls, len(self.snapshots)) - 1]
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot


def make_tools(*snapshots: PcSnapshot | Exception, owner_call: str = "Master") -> PcTools:
    return PcTools(FakeRead(*snapshots), owner_call=owner_call)


def check(tools: PcTools) -> tuple[dict, bool]:
    result, is_error = tools.run("pc_health", {})
    return json.loads(result), is_error


# --- the spec ------------------------------------------------------------------------------


def test_one_strict_tool_pc_health_that_takes_no_arguments():
    [spec] = make_tools(FULL).specs()

    assert spec.name == "pc_health"
    assert spec.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is True
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "pc_health"


def test_the_description_says_when_to_use_it_and_how_to_answer():
    [spec] = make_tools(FULL).specs()

    for guidance in [
        "how the PC is doing",
        "temperature",
        "load",
        "memory",
        "disk space",
        "never guess",
        "one or two short sentences",
        "rounded numbers",
        "no CPU temperature",
        "Never suggest buying hardware",
        "closing heavy programs",
        "cleaning the disk",
    ]:
        assert guidance in spec.description


def test_the_description_says_the_uptime_counts_from_the_last_restart_not_from_power_on():
    # GetTickCount64 runs on through sleep and a Fast Startup shutdown (a hibernation).
    [spec] = make_tools(FULL).specs()

    assert "since Windows last restarted" in spec.description
    assert "sleep and a Fast Startup shutdown do not reset it" in spec.description
    assert "how long the PC has been on" not in spec.description


def test_the_description_says_a_missing_part_could_not_be_read():
    [spec] = make_tools(FULL).specs()

    assert "left out" in spec.description
    assert "NVIDIA" in spec.description


def test_the_spec_stays_identical_whatever_the_pc_reads_for_prompt_caching():
    tools = make_tools(FULL, NOTHING_READ, RuntimeError("counters gone"))
    first = tools.specs()

    for _ in range(3):
        tools.run("pc_health", {})

    assert tools.specs() == first


def test_the_spec_is_the_same_whatever_she_calls_the_owner():
    assert make_tools(FULL, owner_call="Sayang").specs() == make_tools(FULL).specs()


# --- the result ----------------------------------------------------------------------------


def test_every_part_comes_back_as_compact_rounded_json():
    result, is_error = make_tools(FULL).run("pc_health", {})

    assert is_error is False
    assert result == FULL_JSON


def test_parts_that_could_not_be_read_are_left_out():
    result, is_error = make_tools(NOTHING_READ).run("pc_health", {})

    assert (result, is_error) == ('{"drives":[]}', False)


def test_gpu_fields_nvidia_smi_could_not_report_are_left_out():
    gpu = GpuStatus("NVIDIA GeForce RTX 5060 Ti", 72, None, None, None, None)
    snapshot = PcSnapshot(3.2, None, None, (Drive("C:", 100.0, 200.0),), gpu, 59 * 60)

    result, _ = make_tools(snapshot).run("pc_health", {})

    assert result == (
        '{"cpu_percent":3,'
        '"drives":[{"drive":"C:","free_gb":100.0,"total_gb":200.0,"free_percent":50}],'
        '"gpu":{"name":"NVIDIA GeForce RTX 5060 Ti","temp_c":72},'
        '"uptime_hours":0}'
    )


def test_ram_is_left_out_unless_both_used_and_total_are_known():
    used_only = PcSnapshot(None, 18.3, None, (), None, None)
    total_only = PcSnapshot(None, None, 31.9, (), None, None)

    assert check(make_tools(used_only)) == ({"drives": []}, False)
    assert check(make_tools(total_only)) == ({"drives": []}, False)


def test_a_drive_with_no_size_reads_as_zero_percent_free():
    snapshot = PcSnapshot(None, None, None, (Drive("E:", 0.0, 0.0),), None, None)

    data, is_error = check(make_tools(snapshot))

    assert is_error is False
    assert data["drives"] == [{"drive": "E:", "free_gb": 0.0, "total_gb": 0.0, "free_percent": 0}]


def test_each_call_reads_the_pc_again():
    hot = PcSnapshot(None, None, None, (), GpuStatus("RTX", 88, None, None, None, None), None)
    read = FakeRead(FULL, hot)
    tools = PcTools(read, owner_call="Master")

    first, _ = check(tools)
    second, _ = check(tools)

    assert (first["gpu"]["temp_c"], second["gpu"]["temp_c"]) == (61, 88)
    assert read.calls == 2


# --- bad calls and failures ----------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [{"drive": "C:"}, {"part": None}, "C:", None, [], 1],
    ids=["drive key", "null key", "string", "null", "list", "number"],
)
def test_arguments_other_than_an_empty_object_are_refused_without_reading_the_pc(arguments):
    read = FakeRead(FULL)

    result, is_error = PcTools(read, owner_call="Master").run("pc_health", arguments)

    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    assert data["message"].startswith("Argumen pc_health tidak valid: ")
    assert read.calls == 0


def test_another_tool_name_is_an_error_result():
    read = FakeRead(FULL)

    result, is_error = PcTools(read, owner_call="Master").run("api_usage", {})

    assert (json.loads(result), is_error) == (
        {"ok": False, "message": "Tool 'api_usage' bukan tool kesehatan PC."},
        True,
    )
    assert read.calls == 0


def test_a_read_that_raises_is_logged_and_becomes_an_error_result(caplog):
    with caplog.at_level(logging.ERROR, logger="waifu.brain.pc_tools"):
        data, is_error = check(make_tools(OSError("GetSystemTimes failed")))

    assert (data, is_error) == (READ_FAILED, True)
    [record] = [record for record in caplog.records if record.name == "waifu.brain.pc_tools"]
    assert isinstance(record.exc_info[1], OSError)


def test_the_error_result_calls_the_owner_what_she_calls_them():
    data, _ = check(make_tools(RuntimeError("no counters"), owner_call="sayang"))

    assert data["message"] == "Kondisi PC sayang tidak bisa dibaca saat ini."


def test_after_a_failed_read_the_next_call_reads_again():
    tools = make_tools(RuntimeError("busy"), FULL)

    failed, _ = check(tools)
    result, is_error = tools.run("pc_health", {})

    assert failed == READ_FAILED
    assert (result, is_error) == (FULL_JSON, False)


# --- beside the other tools ----------------------------------------------------------------


def test_the_pc_tool_comes_after_the_device_tools_and_run_tool_hands_it_over():
    registry = DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar", kind="light")])
    providers = [make_tools(FULL)]

    specs = build_tool_specs(registry, providers)
    result, is_error = run_tool(registry, "pc_health", {}, providers)

    assert [spec.name for spec in specs] == ["control_device", "list_devices", "pc_health"]
    assert (result, is_error) == (FULL_JSON, False)
    assert registry.get("lampu_kamar").calls == []
