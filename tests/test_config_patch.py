from pathlib import Path

import pytest
import yaml

from waifu.config import AppConfig
from waifu.config_patch import Change, PatchError, masked, patch_text, snippet, yaml_value

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"
EXAMPLE_TEXT = EXAMPLE.read_bytes().decode("utf-8")
SECRET = "a1b2c3d4e5f6g7h8"
TOPIC = "cyrene-AbCdEf123456_-x"

LAMPU_TUYA = (
    "    tuya:\n"
    '      device_id: "isi_dari_tinytuya_wizard"\n'
    '      local_key: "isi_dari_tinytuya_wizard"\n'
    "      address: Auto\n"
    "      version: 3.3\n"
)
COLOKAN_TUYA_END = '      local_key: "isi_dari_tinytuya_wizard"\n\n  # Musik dan volume PC'
INPUT_DEVICE_COMMENT = (
    "    # null = mic default; atau nama dari `waifu mic` (nomor dari `waifu mics`)\n"
)


def tuya(device_id: str, key: str, value, *, secret: bool = False) -> Change:
    return Change(("devices", device_id, "tuya", key), value, secret=secret)


def replaced_once(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


def loaded(text: str) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(text))


# --- the example config ----------------------------------------------------------------------


def test_replaces_only_the_targeted_tuya_values_of_a_device():
    changes = [
        tuya("lampu_kamar", "device_id", "bf1234567890abcdef"),
        tuya("lampu_kamar", "local_key", SECRET, secret=True),
        tuya("lampu_kamar", "address", "192.168.1.60"),
        tuya("lampu_kamar", "version", 3.4),
    ]

    patched = patch_text(EXAMPLE_TEXT, changes)

    assert patched == replaced_once(
        EXAMPLE_TEXT,
        LAMPU_TUYA,
        "    tuya:\n"
        '      device_id: "bf1234567890abcdef"\n'
        f'      local_key: "{SECRET}"\n'
        '      address: "192.168.1.60"\n'
        "      version: 3.4\n",
    )
    lamp = loaded(patched).devices[0].tuya
    assert (lamp.device_id, lamp.local_key, lamp.address, lamp.version) == (
        "bf1234567890abcdef",
        SECRET,
        "192.168.1.60",
        3.4,
    )


def test_inserts_missing_keys_after_the_last_child_at_its_indentation():
    changes = [tuya("colokan_pc", "address", "192.168.1.61"), tuya("colokan_pc", "version", 3.3)]

    patched = patch_text(EXAMPLE_TEXT, changes)

    assert patched == replaced_once(
        EXAMPLE_TEXT,
        COLOKAN_TUYA_END,
        '      local_key: "isi_dari_tinytuya_wizard"\n'
        '      address: "192.168.1.61"\n'
        "      version: 3.3\n"
        "\n  # Musik dan volume PC",
    )


def test_keeps_the_comment_after_a_replaced_value():
    patched = patch_text(
        EXAMPLE_TEXT, [Change(("audio", "input_device"), "Headset (Polaris-BT), MME")]
    )

    assert patched == replaced_once(
        EXAMPLE_TEXT,
        "  input_device: null" + INPUT_DEVICE_COMMENT,
        '  input_device: "Headset (Polaris-BT), MME"' + INPUT_DEVICE_COMMENT,
    )


def test_writes_a_list_of_mics_and_replaces_a_list_with_one_mic():
    mics = ["Headset (Polaris-BT), MME", "Microphone (USB), MME"]

    listed = patch_text(EXAMPLE_TEXT, [Change(("audio", "input_device"), mics)])
    single = patch_text(listed, [Change(("audio", "input_device"), "Microphone (USB), MME")])

    assert (
        '  input_device: ["Headset (Polaris-BT), MME", "Microphone (USB), MME"]'
        + INPUT_DEVICE_COMMENT
    ) in listed
    assert loaded(listed).audio.input_device == mics
    assert single == replaced_once(
        EXAMPLE_TEXT,
        "  input_device: null" + INPUT_DEVICE_COMMENT,
        '  input_device: "Microphone (USB), MME"' + INPUT_DEVICE_COMMENT,
    )


def test_sets_the_blaster_host_of_ir_devices_beside_a_multi_line_command_list():
    changes = [
        Change(("devices", "ac", "ir", "host"), "192.168.1.23"),
        Change(("devices", "proyektor", "ir", "host"), "192.168.1.23"),
    ]

    patched = patch_text(EXAMPLE_TEXT, changes)

    assert patched == EXAMPLE_TEXT.replace(
        "      host: 192.168.1.50\n", '      host: "192.168.1.23"\n'
    )
    assert [device.ir.host for device in loaded(patched).devices if device.ir] == [
        "192.168.1.23",
        "192.168.1.23",
    ]


def test_turns_notify_on_and_puts_the_topic_after_the_section_s_comments():
    changes = [Change(("notify", "enabled"), True), Change(("notify", "topic"), TOPIC)]

    patched = patch_text(EXAMPLE_TEXT, changes)

    expected = replaced_once(
        EXAMPLE_TEXT, "notify:\n  enabled: false\n", "notify:\n  enabled: true\n"
    )
    expected = replaced_once(
        expected,
        "  # topic: cyrene-isi-dengan-hasil-perintah-di-atas\n",
        f'  # topic: cyrene-isi-dengan-hasil-perintah-di-atas\n  topic: "{TOPIC}"\n',
    )
    assert patched == expected
    assert loaded(patched).notify.topic.get_secret_value() == TOPIC


def test_keeps_crlf_line_endings_also_on_inserted_lines():
    crlf = EXAMPLE_TEXT.replace("\n", "\r\n")
    changes = [
        tuya("lampu_kamar", "device_id", "bf1234567890abcdef"),
        tuya("colokan_pc", "address", "192.168.1.61"),
    ]

    patched = patch_text(crlf, changes)

    assert patched == patch_text(EXAMPLE_TEXT, changes).replace("\n", "\r\n")


# --- missing sections and file ends ----------------------------------------------------------


def test_appends_a_missing_section_after_a_blank_line():
    text = "assistant:\n  name: Cyrene\n"

    patched = patch_text(
        text, [Change(("notify", "enabled"), True), Change(("notify", "topic"), TOPIC)]
    )

    assert (
        patched == f'assistant:\n  name: Cyrene\n\nnotify:\n  enabled: true\n  topic: "{TOPIC}"\n'
    )


def test_appends_a_section_to_a_file_without_a_final_newline():
    patched = patch_text("assistant:\n  name: Cyrene", [Change(("audio", "input_device"), "Mic")])

    assert patched == 'assistant:\n  name: Cyrene\n\naudio:\n  input_device: "Mic"\n'


def test_appends_a_section_with_the_file_s_crlf_endings():
    patched = patch_text(
        "assistant:\r\n  name: Cyrene\r\n", [Change(("audio", "input_device"), "Mic")]
    )

    assert patched == 'assistant:\r\n  name: Cyrene\r\n\r\naudio:\r\n  input_device: "Mic"\r\n'


def test_inserts_after_a_last_line_without_a_newline():
    patched = patch_text("notify:\n  enabled: false", [Change(("notify", "topic"), TOPIC)])

    assert patched == f'notify:\n  enabled: false\n  topic: "{TOPIC}"'


def test_inserts_under_an_empty_section_with_two_spaces():
    patched = patch_text(
        "notify:\naudio:\n  input_device: null\n", [Change(("notify", "topic"), TOPIC)]
    )

    assert patched == f'notify:\n  topic: "{TOPIC}"\naudio:\n  input_device: null\n'


# --- finding the device ----------------------------------------------------------------------

DASH_AT_COLUMN_0 = """\
devices:
- id: lampu
  name: Lampu
  type: tuya_light
  tuya:
    device_id: "lampu-id"
    local_key: "lampu-key"
- name: Lampu kamar
  id: "lampu_kamar"   # the second lamp
  type: tuya_light
  tuya:  # Smart Life
    device_id: 'old-id'
    local_key: old-key
"""


def test_finds_a_device_at_dash_column_0_by_its_quoted_id_given_after_another_key():
    changes = [tuya("lampu_kamar", "device_id", "new-id"), tuya("lampu_kamar", "local_key", SECRET)]

    patched = patch_text(DASH_AT_COLUMN_0, changes)

    assert patched == DASH_AT_COLUMN_0.replace(
        "    device_id: 'old-id'\n    local_key: old-key\n",
        f'    device_id: "new-id"\n    local_key: "{SECRET}"\n',
    )


def test_never_takes_a_similar_id_for_the_one_asked():
    patched = patch_text(DASH_AT_COLUMN_0, [tuya("lampu", "device_id", "new-id")])

    assert patched == DASH_AT_COLUMN_0.replace('device_id: "lampu-id"', 'device_id: "new-id"')


def test_skips_commented_out_items_and_keys():
    text = """\
devices:
  # - id: lampu_kamar
  #   tuya:
  #     device_id: "commented"
  - id: lampu_kamar
    name: Lampu kamar
    type: tuya_light
    tuya:
      # device_id: "commented too"
      device_id: "old"
      local_key: "old"
"""

    patched = patch_text(text, [tuya("lampu_kamar", "device_id", "new")])

    assert patched == text.replace('      device_id: "old"', '      device_id: "new"')


def test_reaches_the_tuya_block_of_an_ir_device():
    text = """\
devices:
  - id: tv
    name: TV
    type: ir
    ir:
      backend: tuya
      tuya:
        device_id: "isi_dari_tinytuya_wizard"
        local_key: "isi_dari_tinytuya_wizard"
    commands: [power]
"""
    keys = ("devices", "tv", "ir", "tuya")
    changes = [
        Change((*keys, "device_id"), "blaster-id"),
        Change((*keys, "address"), "192.168.1.70"),
    ]

    patched = patch_text(text, changes)

    assert patched == text.replace(
        '        device_id: "isi_dari_tinytuya_wizard"\n'
        '        local_key: "isi_dari_tinytuya_wizard"\n',
        '        device_id: "blaster-id"\n'
        '        local_key: "isi_dari_tinytuya_wizard"\n'
        '        address: "192.168.1.70"\n',
    )


def test_leaves_quick_action_device_lists_and_routine_steps_alone():
    text = """\
phone:
  quick_actions:
    devices: [lampu_kamar]
routines:
  tidur:
    steps:
      - {device: lampu_kamar, command: off}
devices:
  - id: lampu_kamar
    name: Lampu kamar
    type: tuya_light
    tuya:
      device_id: "old"
      local_key: "old"
"""

    patched = patch_text(text, [tuya("lampu_kamar", "device_id", "new")])

    assert patched == text.replace('device_id: "old"', 'device_id: "new"')


# --- values ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        'say "hi"',
        "a#b",
        "a #b",
        "key: value",
        "[x]",
        "{y}",
        "&anchor",
        "*alias",
        "!tag",
        " leading space",
        "yes",
        "null",
        "3.3",
        "~",
        "Mic 🎤 kamar",
        "back\\slash",
        "it's",
        "%x",
        "@x",
        "|x",
        ">x",
        "Headset (Polaris-BT), MME",
        "line separator",
        "del\x7fchar",
    ],
)
def test_quotes_a_string_so_it_loads_back_exactly(value):
    patched = patch_text(
        "audio:\n  input_device: null\n", [Change(("audio", "input_device"), value)]
    )

    assert yaml.safe_load(patched)["audio"]["input_device"] == value


def test_writes_numbers_booleans_lists_and_null_as_yaml():
    assert [yaml_value(value) for value in (3.4, 2, True, False, None, ["A", 1])] == [
        "3.4",
        "2",
        "true",
        "false",
        "null",
        '["A", 1]',
    ]


def test_replaces_single_quoted_and_flow_list_values_keeping_their_comments():
    text = "audio:\n  input_device: ['it''s', \"b\"]  # two mics\n  output_device: 'x'\n"

    patched = patch_text(text, [Change(("audio", "input_device"), "Mic")])

    assert patched == "audio:\n  input_device: \"Mic\"  # two mics\n  output_device: 'x'\n"


def test_fills_an_empty_value_before_its_comment():
    patched = patch_text(
        "audio:\n  input_device:   # empty\n", [Change(("audio", "input_device"), "Mic")]
    )

    assert patched == 'audio:\n  input_device: "Mic"   # empty\n'


def test_names_a_change_by_its_path():
    assert tuya("lampu_kamar", "local_key", SECRET).path == "devices[lampu_kamar].tuya.local_key"
    assert Change(("audio", "input_device"), "Mic").path == "audio.input_device"


# --- refusals --------------------------------------------------------------------------------

ONE_LAMP = """\
devices:
  - id: lampu_kamar
    name: Lampu kamar
    type: tuya_light
    tuya:
      device_id: "old"
      local_key: "old"
"""


@pytest.mark.parametrize(
    ("text", "change"),
    [
        pytest.param(
            "audio:\n\tinput_device: null\n",
            Change(("audio", "input_device"), SECRET),
            id="tab indentation",
        ),
        pytest.param(
            ONE_LAMP.replace(
                '    tuya:\n      device_id: "old"\n      local_key: "old"\n',
                "    tuya: {device_id: old, local_key: old}\n",
            ),
            tuya("lampu_kamar", "local_key", SECRET),
            id="flow mapping",
        ),
        pytest.param(
            "audio:\n  input_device: |\n    Headset\n",
            Change(("audio", "input_device"), SECRET),
            id="block scalar",
        ),
        pytest.param(
            'audio:\n  input_device:\n    - "A"\n    - "B"\n',
            Change(("audio", "input_device"), SECRET),
            id="block sequence",
        ),
        pytest.param(
            'audio:\n  input_device:\n  - "A"\n',
            Change(("audio", "input_device"), SECRET),
            id="block sequence at the key's column",
        ),
        pytest.param(
            'audio:\n  input_device: "Headset\n    (Polaris)"\n',
            Change(("audio", "input_device"), SECRET),
            id="unclosed quote",
        ),
        pytest.param(
            'audio:\n  input_device: ["A",\n    "B"]\n',
            Change(("audio", "input_device"), SECRET),
            id="unclosed flow list",
        ),
        pytest.param(
            ONE_LAMP.replace("    tuya:\n", "    tuya: &lamp\n"),
            tuya("lampu_kamar", "local_key", SECRET),
            id="anchor",
        ),
        pytest.param(
            ONE_LAMP.replace('      local_key: "old"', "      local_key: *key"),
            tuya("lampu_kamar", "device_id", SECRET),
            id="alias",
        ),
        pytest.param(
            ONE_LAMP.replace('      device_id: "old"', "      device_id: !!str old"),
            tuya("lampu_kamar", "local_key", SECRET),
            id="tag",
        ),
        pytest.param(
            ONE_LAMP.replace("    tuya:\n", "    tuya:\n      <<: *base\n"),
            tuya("lampu_kamar", "local_key", SECRET),
            id="merge key",
        ),
        pytest.param(
            "audio:\n  input_device: null\naudio:\n  output_device: null\n",
            Change(("audio", "input_device"), SECRET),
            id="duplicated section",
        ),
        pytest.param(
            ONE_LAMP + ONE_LAMP.removeprefix("devices:\n"),
            tuya("lampu_kamar", "local_key", SECRET),
            id="duplicated device id",
        ),
        pytest.param(
            ONE_LAMP.replace(
                '      local_key: "old"', '      local_key: "old"\n      local_key: "x"'
            ),
            tuya("lampu_kamar", "local_key", SECRET),
            id="duplicated key",
        ),
        pytest.param(
            "notify: null\n",
            Change(("notify", "topic"), SECRET),
            id="inline parent value",
        ),
        pytest.param(
            "notify: {enabled: false}\n",
            Change(("notify", "topic"), SECRET),
            id="flow section",
        ),
        pytest.param(
            "devices: []\n",
            tuya("lampu_kamar", "local_key", SECRET),
            id="flow device list",
        ),
        pytest.param(
            ONE_LAMP,
            tuya("lampu", "local_key", SECRET),
            id="missing device",
        ),
        pytest.param(
            ONE_LAMP,
            Change(("devices", "lampu_kamar", "ir", "host"), SECRET),
            id="missing nested parent",
        ),
        pytest.param(
            ONE_LAMP,
            Change(("assistant", "voice", "name"), SECRET),
            id="missing parent below the top level",
        ),
    ],
)
def test_refuses_a_layout_it_cannot_patch_safely_without_naming_the_value(text, change):
    with pytest.raises(PatchError) as raised:
        patch_text(text, [change])

    assert SECRET not in str(raised.value)
    assert str(raised.value)


# --- snippet and masking ---------------------------------------------------------------------


def test_snippet_groups_the_changes_as_yaml_with_secrets_hidden():
    changes = [
        tuya("lampu_kamar", "device_id", "bf12"),
        tuya("lampu_kamar", "local_key", SECRET, secret=True),
        tuya("colokan_pc", "device_id", "bf34"),
        Change(("audio", "input_device"), ["A", "B"]),
    ]

    text = snippet(changes)

    assert SECRET not in text
    assert text == (
        "devices:\n"
        "  - id: lampu_kamar\n"
        "    tuya:\n"
        '      device_id: "bf12"\n'
        '      local_key: "..."   # disembunyikan (16 karakter)\n'
        "  - id: colokan_pc\n"
        "    tuya:\n"
        '      device_id: "bf34"\n'
        "audio:\n"
        '  input_device: ["A", "B"]\n'
    )


def test_masking_shows_only_the_length():
    assert masked(SECRET) == "disembunyikan, 16 karakter"
