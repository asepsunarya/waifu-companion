"""QuickActions: the phone page's quick buttons, built from the registry and the routines.

Every device is a MockDevice (or an IrDevice over a code file under tmp_path with a blaster
that only records), so nothing here reaches a real lamp, AC, plug, or the PC; the schedule
lives under tmp_path with a fixed clock.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

import pytest

from waifu.alarm import AlarmBell
from waifu.config import QuickActionsConfig, RoutineConfig, RoutineStep
from waifu.devices.base import DeviceError, DeviceResult
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.listen_pause import ListenPause
from waifu.phone.actions import ACTION_ID, QuickAction, QuickActions
from waifu.schedule import AlarmAction, DeviceAction, Entry, ScheduleStore

AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]
BEDTIME = datetime(2026, 10, 17, 22, 0, 40)
WAIT_S = 5.0


def house() -> dict[str, MockDevice]:
    return {
        "lampu_kamar": MockDevice(
            "lampu_kamar",
            "Lampu kamar",
            commands=["on", "off", "toggle", "brightness"],
            kind="light",
        ),
        "ac": MockDevice("ac", "AC Samsung", commands=AC_COMMANDS, kind="ir"),
        "kipas": MockDevice("kipas", "Kipas", commands=["on", "off", "toggle"], kind="plug"),
        "komputer": MockDevice(
            "komputer", "Komputer", commands=["wake", "shutdown", "restart", "sleep"], kind="pc"
        ),
        "colokan_pc": MockDevice(
            "colokan_pc", "Colokan PC", commands=["on", "off", "toggle"], kind="plug"
        ),
        "musik": MockDevice("musik", "Musik PC", commands=["play_pause", "mute"], kind="media"),
        "aplikasi": MockDevice("aplikasi", "Aplikasi PC", commands=["open_spotify"], kind="apps"),
    }


def registry_of(devices: dict[str, MockDevice]) -> DeviceRegistry:
    return DeviceRegistry(list(devices.values()), pc_power_ids=["colokan_pc"])


def step(device: str, command: str, value: int | None = None, after: int | None = None):
    return RoutineStep(device=device, command=command, value=value, after_minutes=after)


def tidur() -> RoutineConfig:
    """Lamp off and AC to 26 now, the AC off three hours later."""
    return RoutineConfig(
        description="Mode tidur",
        steps=[step("lampu_kamar", "off"), step("ac", "temp", 26), step("ac", "off", after=180)],
    )


def quick(
    devices: dict[str, MockDevice] | None = None,
    routines: dict[str, RoutineConfig] | None = None,
    **kwargs,
) -> QuickActions:
    return QuickActions(
        registry_of(house() if devices is None else devices), routines or {}, **kwargs
    )


def buttons(actions: QuickActions) -> dict[str, list[tuple[str, str]]]:
    """Each group's name with its buttons as (id, label)."""
    return {
        group.name: [(action.id, action.label) for action in group.actions]
        for group in actions.groups()
    }


def store(tmp_path: Path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedule.json", now=lambda: BEDTIME)


class RecordingBlaster:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, code_b64: str) -> None:
        self.sent.append(code_b64)

    def learn(self, timeout_s: float) -> str:
        raise AssertionError("no test learns a code")


# --- which buttons -----------------------------------------------------------------------------


def test_a_light_has_its_switches_and_four_brightness_presets() -> None:
    assert buttons(quick())["Lampu kamar"] == [
        ("dev.lampu_kamar.on", "Nyala"),
        ("dev.lampu_kamar.off", "Mati"),
        ("dev.lampu_kamar.toggle", "Nyala/mati"),
        ("dev.lampu_kamar.brightness.10", "10%"),
        ("dev.lampu_kamar.brightness.30", "30%"),
        ("dev.lampu_kamar.brightness.60", "60%"),
        ("dev.lampu_kamar.brightness.100", "100%"),
    ]


def test_a_plug_has_on_off_and_toggle() -> None:
    assert buttons(quick())["Kipas"] == [
        ("dev.kipas.on", "Nyala"),
        ("dev.kipas.off", "Mati"),
        ("dev.kipas.toggle", "Nyala/mati"),
    ]


def test_the_ac_has_its_switches_and_the_preset_temperatures_it_learned() -> None:
    assert buttons(quick())["AC Samsung"] == [
        ("dev.ac.on", "Nyala"),
        ("dev.ac.off", "Mati"),
        ("dev.ac.temp_18", "18°C"),
        ("dev.ac.temp_20", "20°C"),
        ("dev.ac.temp_22", "22°C"),
        ("dev.ac.temp_24", "24°C"),
        ("dev.ac.temp_26", "26°C"),
        ("dev.ac.temp_28", "28°C"),
    ]


def test_an_ir_device_shows_only_what_it_learned() -> None:
    devices = {
        "ac": MockDevice("ac", "AC", commands=["temp_24", "temp_25", "swing"], kind="ir"),
        "proyektor": MockDevice(
            "proyektor", "Proyektor", commands=["power", "volume_up", "home", "ok"], kind="ir"
        ),
        "tv": MockDevice("tv", "TV", commands=["input", "channel_5"], kind="ir"),
    }

    assert buttons(quick(devices)) == {
        "AC": [("dev.ac.temp_24", "24°C")],
        "Proyektor": [("dev.proyektor.power", "Tombol power")],
    }


def test_the_pc_its_plug_media_keys_and_apps_never_get_a_button() -> None:
    assert list(buttons(quick())) == ["Lampu kamar", "AC Samsung", "Kipas"]


def test_a_device_the_owner_wrote_as_a_mock_gets_its_switches() -> None:
    devices = {"lampu_meja": MockDevice("lampu_meja", "Lampu meja")}

    assert buttons(quick(devices)) == {
        "Lampu meja": [("dev.lampu_meja.on", "Nyala"), ("dev.lampu_meja.off", "Mati")]
    }


def test_every_routine_shows_in_one_group_by_its_description_or_name() -> None:
    routines = {
        "tidur": tidur(),
        "redup": RoutineConfig(steps=[step("lampu_kamar", "brightness", 20)]),
    }

    assert buttons(quick(routines=routines))["Rutinitas"] == [
        ("rt.tidur", "Mode tidur"),
        ("rt.redup", "redup"),
    ]


def test_without_routines_there_is_no_routine_group() -> None:
    assert "Rutinitas" not in buttons(quick())


def test_the_owner_picks_and_orders_the_devices_and_routines() -> None:
    routines = {"tidur": tidur(), "redup": RoutineConfig(steps=[step("lampu_kamar", "off")])}
    chosen = QuickActionsConfig(devices=["kipas", "lampu_kamar"], routines=["redup"])

    shown = buttons(quick(routines=routines, chosen=chosen))

    assert list(shown) == ["Kipas", "Lampu kamar", "Rutinitas"]
    assert shown["Rutinitas"] == [("rt.redup", "redup")]


def test_an_empty_choice_shows_none_of_them() -> None:
    chosen = QuickActionsConfig(devices=[], routines=[])

    assert quick(routines={"tidur": tidur()}, chosen=chosen).groups() == []


def test_a_choice_never_brings_back_the_pc_or_its_plug() -> None:
    # The config refuses these names; the list leaves them out on its own as well.
    chosen = QuickActionsConfig(devices=["komputer", "colokan_pc", "musik", "kipas"])

    assert list(buttons(quick(chosen=chosen))) == ["Kipas"]


def test_a_device_whose_codes_cannot_be_read_is_left_out_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    codes = tmp_path / "ir_codes.json"
    codes.write_text("{not json", encoding="utf-8")
    broken = IrDevice("ac", "AC Samsung", [], RecordingBlaster(), IrCodeStore(codes))
    devices = {"ac": broken, "kipas": house()["kipas"]}

    with caplog.at_level(logging.WARNING, logger="waifu.phone.actions"):
        shown = buttons(quick(devices))

    assert list(shown) == ["Kipas"]
    assert any("ac" in record.getMessage() for record in caplog.records)


def test_a_code_learned_meanwhile_shows_at_the_next_listing(tmp_path: Path) -> None:
    codes = IrCodeStore(tmp_path / "ir_codes.json")
    codes.put("ac", "on", "Y29kZQ==")
    actions = quick({"ac": IrDevice("ac", "AC Samsung", [], RecordingBlaster(), codes)})
    assert buttons(actions) == {"AC Samsung": [("dev.ac.on", "Nyala")]}

    codes.put("ac", "temp_24", "Y29kZTI0")

    assert buttons(actions)["AC Samsung"] == [("dev.ac.on", "Nyala"), ("dev.ac.temp_24", "24°C")]


def test_every_id_is_a_plain_slug() -> None:
    ids = [
        action.id
        for group in quick(routines={"tidur": tidur()}).groups()
        for action in group.actions
    ]

    assert ids
    assert all(ACTION_ID.fullmatch(action_id) for action_id in ids)


# --- finding one ---------------------------------------------------------------------------


def test_find_looks_an_id_up_in_the_list_of_now(tmp_path: Path) -> None:
    codes = IrCodeStore(tmp_path / "ir_codes.json")
    codes.put("ac", "temp_24", "Y29kZTI0")
    actions = quick(
        {**house(), "ac": IrDevice("ac", "AC", [], RecordingBlaster(), codes)},
        routines={"tidur": tidur()},
    )
    assert actions.find("dev.ac.temp_24") is not None

    tmp_path.joinpath("ir_codes.json").write_text("{}", encoding="utf-8")

    assert actions.find("dev.ac.temp_24") is None
    assert actions.find("rt.tidur") == QuickAction("rt.tidur", "Mode tidur", routine="tidur")


@pytest.mark.parametrize(
    "action_id",
    [
        "dev.komputer.shutdown",
        "dev.komputer.wake",
        "dev.colokan_pc.off",
        "dev.colokan_pc.on",
        "dev.musik.play_pause",
        "dev.aplikasi.open_spotify",
        "dev.lampu_kamar.brightness.55",
        "dev.ac.temp_17",
        "dev.kulkas.on",
        "rt.siang",
        "",
    ],
)
def test_find_knows_no_other_button(action_id: str) -> None:
    assert quick(routines={"tidur": tidur()}).find(action_id) is None


# --- running one ---------------------------------------------------------------------------


def run(actions: QuickActions, action_id: str):
    action = actions.find(action_id)
    assert action is not None, action_id
    return actions.run(action)


def test_a_switch_runs_on_its_device_and_tells_its_message() -> None:
    devices = house()

    result = run(quick(devices), "dev.lampu_kamar.on")

    assert result.ok
    assert result.message == "Lampu kamar: perintah 'on' berhasil (simulasi)."
    assert devices["lampu_kamar"].calls == [("on", None)]


def test_a_brightness_preset_sets_its_percentage() -> None:
    devices = house()

    assert run(quick(devices), "dev.lampu_kamar.brightness.30").ok

    assert devices["lampu_kamar"].calls == [("brightness", 30)]


def test_a_temperature_sends_its_learned_code() -> None:
    devices = house()

    assert run(quick(devices), "dev.ac.temp_24").ok

    assert devices["ac"].calls == [("temp_24", None)]


def test_a_device_that_fails_says_so_honestly() -> None:
    class Unplugged(MockDevice):
        def execute(self, command: str, value: int | None = None) -> DeviceResult:
            raise DeviceError("Kipas tidak menjawab.")

    devices = {"kipas": Unplugged("kipas", "Kipas", kind="plug")}

    result = run(quick(devices), "dev.kipas.on")

    assert not result.ok
    assert result.message == "Kipas tidak menjawab."


@pytest.mark.parametrize(
    ("device", "command"),
    [("komputer", "shutdown"), ("komputer", "wake"), ("colokan_pc", "off"), ("colokan_pc", "on")],
)
def test_the_pc_and_its_plug_are_refused_even_when_asked_directly(
    device: str, command: str
) -> None:
    devices = house()
    action = QuickAction(f"dev.{device}.{command}", "Mati", device=device, command=command)

    result = quick(devices).run(action)

    assert not result.ok
    assert "PC" in result.message and "tombol cepat HP" in result.message
    assert devices[device].calls == []


def test_the_pc_refusal_names_where_the_buttons_are() -> None:
    devices = house()
    action = QuickAction("dev.komputer.shutdown", "Mati", device="komputer", command="shutdown")

    result = quick(devices, where="tombol cepat PC").run(action)

    assert (
        result.message == "Komputer adalah PC, dan PC tidak pernah dijalankan dari tombol cepat PC."
    )
    assert devices["komputer"].calls == []


def test_a_routine_runs_every_step_and_sums_it_up(tmp_path: Path) -> None:
    devices = house()
    schedule = store(tmp_path)

    result = run(
        quick(devices, routines={"tidur": tidur()}, schedule=schedule, now=lambda: BEDTIME),
        "rt.tidur",
    )

    assert result.ok
    assert result.message == (
        "Mode tidur: 3 dari 3 langkah berhasil. Dijadwalkan jam 01.00 dini hari nanti."
    )
    assert devices["lampu_kamar"].calls == [("off", None)]
    assert devices["ac"].calls == [("temp_26", 26)]
    [entry] = schedule.entries()
    assert entry.action == DeviceAction("ac", "off", None, routine="tidur")


def test_a_routine_tells_its_first_failure() -> None:
    routines = {
        "redup": RoutineConfig(
            steps=[step("lampu_kamar", "dim"), step("kipas", "off"), step("ac", "swing")]
        )
    }

    result = run(quick(routines=routines), "rt.redup")

    assert not result.ok
    assert result.message.startswith("redup: 1 dari 3 langkah berhasil. Lampu kamar tidak punya")
    assert "AC Samsung" not in result.message


def test_a_routine_without_a_schedule_says_its_delayed_step_was_not_scheduled() -> None:
    result = run(quick(routines={"tidur": tidur()}, now=lambda: BEDTIME), "rt.tidur")

    assert not result.ok
    assert result.message.startswith("Mode tidur: 2 dari 3 langkah berhasil. Langkah yang ditunda")


def test_actions_run_one_at_a_time() -> None:
    class Slow(MockDevice):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.entered = threading.Event()
            self.release = threading.Event()
            self.running = 0
            self.most_at_once = 0

        def execute(self, command: str, value: int | None = None) -> DeviceResult:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            self.entered.set()
            assert self.release.wait(WAIT_S)
            self.running -= 1
            return super().execute(command, value)

    slow = Slow("kipas", "Kipas", kind="plug")
    actions = quick({"kipas": slow})
    first = threading.Thread(target=run, args=(actions, "dev.kipas.on"))
    second = threading.Thread(target=run, args=(actions, "dev.kipas.off"))
    first.start()
    assert slow.entered.wait(WAIT_S)

    second.start()
    second.join(0.2)
    waiting = second.is_alive()
    slow.release.set()
    first.join(WAIT_S)
    second.join(WAIT_S)

    assert waiting
    assert slow.most_at_once == 1
    assert slow.calls == [("on", None), ("off", None)]


# --- the alarm ---------------------------------------------------------------------------------

WAKE_AT = datetime(2026, 10, 17, 22, 0)


def bell(tmp_path: Path, *, max_entries: int = 50, snooze_minutes: int = 5) -> AlarmBell:
    schedule = ScheduleStore(
        tmp_path / "schedule.json", max_entries=max_entries, now=lambda: BEDTIME
    )
    return AlarmBell(
        tmp_path / "alarm.json",
        schedule,
        snooze_minutes=snooze_minutes,
        now=lambda: BEDTIME,
        clock=lambda: 0.0,
    )


def ring(alarm: AlarmBell) -> None:
    alarm.start(Entry("j9", WAKE_AT, "none", AlarmAction("berangkat kerja"), WAKE_AT))


def test_while_an_alarm_rings_its_buttons_come_first(tmp_path: Path) -> None:
    alarm = bell(tmp_path, snooze_minutes=10)
    ring(alarm)

    groups = buttons(quick(routines={"tidur": tidur()}, alarm=alarm))

    assert list(groups)[0] == "Alarm"
    assert groups["Alarm"] == [("al.stop", "Matikan alarm"), ("al.snooze", "Tunda 10 menit")]
    assert "Rutinitas" in groups


def test_without_a_ring_there_is_no_alarm_group(tmp_path: Path) -> None:
    assert "Alarm" not in buttons(quick(alarm=bell(tmp_path)))
    assert "Alarm" not in buttons(quick())


def test_the_alarm_ids_are_plain_slugs(tmp_path: Path) -> None:
    alarm = bell(tmp_path)
    ring(alarm)

    ids = [action.id for action in quick(alarm=alarm).groups()[0].actions]

    assert all(ACTION_ID.fullmatch(action_id) for action_id in ids)


def test_matikan_alarm_stops_the_ring(tmp_path: Path) -> None:
    alarm = bell(tmp_path)
    ring(alarm)

    result = run(quick(alarm=alarm), "al.stop")

    assert (result.ok, result.message) == (True, "Alarm dimatikan.")
    assert alarm.ringing() is False


def test_tunda_snoozes_the_ring(tmp_path: Path) -> None:
    alarm = bell(tmp_path)
    ring(alarm)

    result = run(quick(alarm=alarm), "al.snooze")

    assert (result.ok, result.message) == (True, "Alarm ditunda sampai jam 22.06.")
    assert alarm.ringing() is False


def test_a_tap_after_the_ring_ended_gets_a_friendly_answer(tmp_path: Path) -> None:
    actions = quick(alarm=bell(tmp_path))

    stopped = run(actions, "al.stop")
    snoozed = run(actions, "al.snooze")

    assert (stopped.ok, stopped.message) == (True, "Alarm sudah tidak berbunyi.")
    assert (snoozed.ok, snoozed.message) == (
        False,
        "Alarm sudah tidak berbunyi, jadi tidak ada yang ditunda.",
    )


def test_a_snooze_the_schedule_refuses_says_why_and_the_ring_goes_on(tmp_path: Path) -> None:
    alarm = bell(tmp_path, max_entries=1)
    full = ScheduleStore(tmp_path / "schedule.json", now=lambda: BEDTIME)
    full.add(WAKE_AT.replace(hour=23), "none", DeviceAction("ac", "off"))
    ring(alarm)

    result = run(quick(alarm=alarm), "al.snooze")

    assert (result.ok, result.message) == (
        False,
        "Alarm tidak bisa ditunda: Jadwal sudah penuh, 1 entri. Batalkan jadwal lama dulu.",
    )
    assert alarm.ringing() is True


@pytest.mark.parametrize("action_id", ["al.stop", "al.snooze"])
def test_without_a_bell_the_alarm_buttons_are_unknown(action_id: str) -> None:
    assert quick().find(action_id) is None


def test_a_stop_never_waits_for_a_slow_device(tmp_path: Path) -> None:
    class Slow(MockDevice):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.entered = threading.Event()
            self.release = threading.Event()

        def execute(self, command: str, value: int | None = None) -> DeviceResult:
            self.entered.set()
            assert self.release.wait(WAIT_S)
            return super().execute(command, value)

    alarm = bell(tmp_path)
    ring(alarm)
    slow = Slow("kipas", "Kipas", kind="plug")
    actions = quick({"kipas": slow}, alarm=alarm)
    pressing = threading.Thread(target=run, args=(actions, "dev.kipas.on"))
    pressing.start()
    assert slow.entered.wait(WAIT_S)

    try:
        stopped = run(actions, "al.stop")
    finally:
        slow.release.set()
        pressing.join(WAIT_S)

    assert stopped.ok is True
    assert alarm.ringing() is False


# --- the listen pause ----------------------------------------------------------------------------


def listen_pause(tmp_path: Path) -> ListenPause:
    return ListenPause(tmp_path / "listen_pause.json", now=lambda: BEDTIME)


def test_while_the_pause_holds_dengar_lagi_comes_after_the_alarm_and_before_the_devices(
    tmp_path: Path,
) -> None:
    alarm, pause = bell(tmp_path), listen_pause(tmp_path)
    ring(alarm)
    pause.start()

    groups = buttons(quick(routines={"tidur": tidur()}, alarm=alarm, listen_pause=pause))

    assert list(groups)[:3] == ["Alarm", "Mikrofon", "Lampu kamar"]
    assert groups["Mikrofon"] == [("mic.listen", "Dengar lagi")]


def test_without_a_pause_there_is_no_microphone_group(tmp_path: Path) -> None:
    assert "Mikrofon" not in buttons(quick(listen_pause=listen_pause(tmp_path)))
    assert "Mikrofon" not in buttons(quick())


def test_the_pause_id_is_a_plain_slug(tmp_path: Path) -> None:
    pause = listen_pause(tmp_path)
    pause.start()

    (action,) = quick(listen_pause=pause).groups()[0].actions

    assert ACTION_ID.fullmatch(action.id)
    assert action.listen is True


def test_dengar_lagi_ends_the_pause(tmp_path: Path) -> None:
    pause = listen_pause(tmp_path)
    pause.start()

    result = run(quick(listen_pause=pause), "mic.listen")

    assert (result.ok, result.message) == (True, "Mendengarkan lagi.")
    assert pause.paused() is False


def test_a_tap_after_the_pause_ended_gets_a_friendly_answer(tmp_path: Path) -> None:
    result = run(quick(listen_pause=listen_pause(tmp_path)), "mic.listen")

    assert (result.ok, result.message) == (True, "Sudah mendengarkan.")


def test_a_pause_that_cannot_be_ended_says_so(tmp_path: Path, monkeypatch) -> None:
    pause = listen_pause(tmp_path)
    pause.start()

    def locked() -> bool:
        raise PermissionError(32, "The file is used by another process")

    monkeypatch.setattr(pause, "end", locked)

    result = run(quick(listen_pause=pause), "mic.listen")

    assert (result.ok, result.message) == (
        False,
        "Belum bisa mendengarkan lagi. Coba lagi sebentar.",
    )


def test_without_a_pause_dengar_lagi_is_unknown() -> None:
    assert quick().find("mic.listen") is None


def test_dengar_lagi_never_waits_for_a_slow_device(tmp_path: Path) -> None:
    class Slow(MockDevice):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.entered = threading.Event()
            self.release = threading.Event()

        def execute(self, command: str, value: int | None = None) -> DeviceResult:
            self.entered.set()
            assert self.release.wait(WAIT_S)
            return super().execute(command, value)

    pause = listen_pause(tmp_path)
    pause.start()
    slow = Slow("kipas", "Kipas", kind="plug")
    actions = quick({"kipas": slow}, listen_pause=pause)
    pressing = threading.Thread(target=run, args=(actions, "dev.kipas.on"))
    pressing.start()
    assert slow.entered.wait(WAIT_S)

    try:
        result = run(actions, "mic.listen")
    finally:
        slow.release.set()
        pressing.join(WAIT_S)

    assert result.ok is True
    assert pause.paused() is False
