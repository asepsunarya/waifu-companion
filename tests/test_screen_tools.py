"""The screen tool: no test captures the real screen.

Every `ScreenTools` gets a fake capture that hands out a PNG of a synthetic array and records
the size it was asked for (`tests/conftest.py` also refuses the real StretchBlt).
"""

import base64
import json
import logging

import numpy as np
import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.attachments import CameraTurn
from waifu.brain.base import ToolImage
from waifu.brain.screen_tools import ScreenTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.screen import ScreenImage, encode_png

SCREEN_PNG = encode_png(np.full((9, 16, 3), 200, dtype=np.uint8))
SCREENSHOT = ScreenImage(png=SCREEN_PNG, width=16, height=9)
LOOKED = '{"ok":true,"width":16,"height":9}'


class FakeCapture:
    """Stands in for capture_screen: records each requested size, never touches the screen."""

    def __init__(
        self, error: Exception | None = None, screenshot: ScreenImage = SCREENSHOT
    ) -> None:
        self.sizes: list[int] = []
        self.error = error
        self.screenshot = screenshot

    def __call__(self, max_side: int) -> ScreenImage:
        self.sizes.append(max_side)
        if self.error is not None:
            raise self.error
        return self.screenshot


class BusyScreenCapture:
    """A busy game screen of the given size, as capture_screen would scale it: random noise,
    which PNG compresses worst of all. Synthetic pixels, never the real screen."""

    def __init__(self, screen: tuple[int, int]) -> None:
        self.screen = screen
        self.sizes: list[int] = []

    def __call__(self, max_side: int) -> ScreenImage:
        self.sizes.append(max_side)
        scale = min(1.0, max_side / max(self.screen))
        width, height = (round(side * scale) for side in self.screen)
        rng = np.random.default_rng(len(self.sizes))
        pixels = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
        return ScreenImage(png=encode_png(pixels), width=width, height=height)


def look(tools: ScreenTools) -> tuple[dict, bool]:
    result, is_error = tools.run("look_at_screen", {})
    return json.loads(result), is_error


# --- spec ----------------------------------------------------------------------------------


def test_one_tool_that_takes_no_arguments_so_it_needs_no_strict():
    [spec] = ScreenTools(capture=FakeCapture()).specs()

    assert spec.name == "look_at_screen"
    assert spec.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "look_at_screen"


def test_description_says_when_to_look_and_what_never_to_read_out():
    [spec] = ScreenTools(capture=FakeCapture()).specs()

    for guidance in [
        "one screenshot",
        "main monitor",
        "only when Master's current message asks",
        "describe",
        "never read out private details",
        "messages",
        "emails",
        "account numbers",
        "unless Master asks",
    ]:
        assert guidance in spec.description


def test_description_says_never_to_save_what_she_sees_unless_master_asks():
    # Notes are written to disk and listed in every later session's system prompt.
    [spec] = ScreenTools(capture=FakeCapture()).specs()

    assert (
        "never save anything you see on the screen with `remember` unless Master asks you to"
        in spec.description
    )


def test_description_says_her_own_avatar_may_be_in_the_screenshot():
    # Her mascot window is layered and always on top, so the capture includes it.
    [spec] = ScreenTools(capture=FakeCapture()).specs()

    for guidance in ["your avatar", "anime figure", "that is you", "ignore it"]:
        assert guidance in spec.description


def test_description_explains_an_all_black_screenshot_so_she_can_give_the_tip():
    [spec] = ScreenTools(capture=FakeCapture()).specs()

    for guidance in ["all black", "exclusive fullscreen", "borderless", "windowed"]:
        assert guidance in spec.description


def test_the_spec_stays_identical_across_turns_for_prompt_caching():
    tools = ScreenTools(capture=FakeCapture())
    first = tools.specs()

    tools.begin_turn("Cyrene, lihat layarku")
    look(tools)
    tools.take_images()

    assert tools.specs() == first


# --- only when Master asks -----------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "Cyrene, lihat layarku, menurutmu tim ini gimana?",
        "liat deh build-ku",
        "ini error apa ya, coba lihat",
        "LIHAT!",
        "lihatin dong yang ini",
        "liatin bentar",
        "tengok sebentar, Cyrene",
        "tengokin ini",
        "ada yang aneh di layar",
        "cek screen-ku",
        "ambil screenshot",
        "monitor-ku kenapa ya?",
        "キュレネ、見て!",
    ],
)
def test_master_asking_to_look_lets_her_take_one_screenshot(words):
    capture = FakeCapture()
    tools = ScreenTools(capture=capture, max_side=1024)

    tools.begin_turn(words)
    result, is_error = tools.run("look_at_screen", {})

    assert (result, is_error) == (LOOKED, False)
    assert capture.sizes == [1024]


@pytest.mark.parametrize(
    "words",
    [
        "Cyrene, nyalakan lampu kamar.",
        "aku bosan",
        "menurutmu tim ini gimana?",
        "ini error apa ya?",
        "これ何?",
        "",
    ],
)
def test_without_a_look_word_from_master_the_screen_is_never_captured(words):
    capture = FakeCapture()
    tools = ScreenTools(capture=capture)

    tools.begin_turn(words)
    result, is_error = look(tools)

    assert is_error is True
    assert result["ok"] is False
    assert "Master tidak meminta" in result["message"]
    assert capture.sizes == []
    assert tools.take_images() == []


def test_her_names_never_count_as_asking_to_look():
    capture = FakeCapture()
    tools = ScreenTools(capture=capture, names=["Monitor", "hai screen"])

    for words in ["Monitor, nyalakan lampu kamar.", "Hai screen, aku pulang."]:
        tools.begin_turn(words)
        assert look(tools)[1] is True
    tools.begin_turn("Monitor, lihat layarku.")
    assert look(tools)[1] is False

    assert capture.sizes == [1280]


def test_before_any_turn_she_cannot_look():
    capture = FakeCapture()

    result, is_error = look(ScreenTools(capture=capture))

    assert is_error is True
    assert capture.sizes == []


def test_asking_counts_only_for_the_turn_it_was_said_in():
    capture = FakeCapture()
    tools = ScreenTools(capture=capture)

    tools.begin_turn("Cyrene, lihat layarku")
    look(tools)
    tools.begin_turn("terus menurutmu gimana?")
    result, is_error = look(tools)

    assert is_error is True
    assert "Master tidak meminta" in result["message"]
    assert capture.sizes == [1280]


def test_one_screenshot_per_turn_and_a_new_request_may_look_again():
    capture = FakeCapture()
    tools = ScreenTools(capture=capture)

    tools.begin_turn("Cyrene, lihat layarku")
    first = look(tools)
    second = look(tools)
    tools.begin_turn("lihat lagi dong")
    third = look(tools)

    assert first == (json.loads(LOOKED), False)
    assert second[1] is True
    assert "sekali" in second[0]["message"]
    assert third == (json.loads(LOOKED), False)
    assert capture.sizes == [1280, 1280]


# --- while Master shows her something through the phone camera --------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "coba lihat ini",
        "lihat ini, bagus nggak bajuku?",
        "liat deh",
        "tengokin ini",
        "キュレネ、見て!",
    ],
)
def test_a_bare_look_word_sent_through_the_phone_camera_never_captures_the_pc_screen(words):
    capture = FakeCapture()
    camera = CameraTurn()
    tools = ScreenTools(capture=capture, camera_turn=camera)

    camera.begin()
    tools.begin_turn(words)
    result, is_error = look(tools)

    assert is_error is True
    assert "kamera HP" in result["message"]
    assert "layar PC" in result["message"]
    assert capture.sizes == []
    assert tools.take_images() == []


@pytest.mark.parametrize(
    "words",
    [
        "lihat layarku",
        "ada yang aneh di layar",
        "cek screen-ku",
        "ambil screenshot",
        "monitor-ku kenapa ya?",
    ],
)
def test_naming_the_pc_screen_through_the_phone_camera_still_lets_her_look(words):
    capture = FakeCapture()
    camera = CameraTurn()
    tools = ScreenTools(capture=capture, camera_turn=camera)

    camera.begin()
    tools.begin_turn(words)

    assert tools.run("look_at_screen", {}) == (LOOKED, False)
    assert capture.sizes == [1280]


def test_a_bare_look_word_asks_again_once_the_camera_turn_has_ended():
    capture = FakeCapture()
    camera = CameraTurn()
    tools = ScreenTools(capture=capture, camera_turn=camera)

    camera.begin()
    tools.begin_turn("coba lihat ini")
    refused = look(tools)[1]
    camera.end()
    tools.begin_turn("coba lihat ini")

    assert refused is True
    assert tools.run("look_at_screen", {}) == (LOOKED, False)
    assert capture.sizes == [1280]


# --- the image -----------------------------------------------------------------------------


def test_take_images_hands_over_the_screenshot_once():
    tools = ScreenTools(capture=FakeCapture())
    tools.begin_turn("Cyrene, lihat layarku")
    look(tools)

    assert tools.take_images() == [ToolImage(media_type="image/png", data=SCREEN_PNG)]
    assert tools.take_images() == []


def test_an_image_never_taken_is_forgotten_when_the_next_turn_begins():
    tools = ScreenTools(capture=FakeCapture())
    tools.begin_turn("Cyrene, lihat layarku")
    look(tools)

    tools.begin_turn("lihat lagi")

    assert tools.take_images() == []


# The API refuses an image whose base64 data is over 5 MB (5 * 2**20 bytes).
API_IMAGE_LIMIT = 5 * 2**20


@pytest.mark.parametrize("screen", [(2560, 1600), (1920, 1080), (1600, 1200)])
def test_a_busy_screen_too_large_for_the_api_is_taken_again_smaller(screen):
    capture = BusyScreenCapture(screen)
    tools = ScreenTools(capture=capture, max_side=1568)
    tools.begin_turn("Cyrene, lihat layarku")

    result, is_error = look(tools)

    [image] = tools.take_images()
    payload = len(base64.b64encode(image.data))
    assert payload <= API_IMAGE_LIMIT
    assert capture.sizes[0] == 1568
    assert len(capture.sizes) == 2
    assert capture.sizes[1] < 1568
    assert is_error is False
    assert max(result["width"], result["height"]) == capture.sizes[1]


def test_a_screen_that_fits_is_taken_once_at_max_side():
    capture = BusyScreenCapture((1280, 720))
    tools = ScreenTools(capture=capture, max_side=1280)
    tools.begin_turn("Cyrene, lihat layarku")

    look(tools)

    assert capture.sizes == [1280]
    [image] = tools.take_images()
    payload = len(base64.b64encode(image.data))
    assert payload <= API_IMAGE_LIMIT


def test_a_screenshot_still_too_large_when_taken_smaller_is_an_error_result_without_an_image():
    too_large = ScreenImage(png=bytes(4 * 2**20), width=1568, height=980)  # 5.3 MB as base64
    capture = FakeCapture(screenshot=too_large)
    tools = ScreenTools(capture=capture, max_side=1568)
    tools.begin_turn("Cyrene, lihat layarku")

    result, is_error = look(tools)

    assert is_error is True
    assert result["message"].startswith("Layar tidak bisa dilihat")
    assert "terlalu besar" in result["message"]
    assert len(capture.sizes) == 2
    assert tools.take_images() == []


def test_tool_image_repr_leaves_out_the_image_bytes():
    image = ToolImage(media_type="image/png", data=b"secret pixels")

    assert "secret" not in repr(image)
    assert "image/png" in repr(image)


# --- failures never raise ------------------------------------------------------------------


def test_a_failed_capture_is_an_indonesian_error_result_without_an_image(caplog):
    capture = FakeCapture(error=OSError("StretchBlt failed (Windows error 5)"))
    tools = ScreenTools(capture=capture)
    tools.begin_turn("Cyrene, lihat layarku")

    with caplog.at_level(logging.WARNING):
        result, is_error = look(tools)

    assert is_error is True
    assert result == {
        "ok": False,
        "message": "Layar tidak bisa dilihat: Windows gagal mengambilnya.",
    }
    assert tools.take_images() == []
    assert "StretchBlt failed" in caplog.text


def test_an_unexpected_failure_is_an_error_result_too():
    tools = ScreenTools(capture=FakeCapture(error=ValueError("bad pixels")))
    tools.begin_turn("Cyrene, lihat layarku")

    result, is_error = look(tools)

    assert is_error is True
    assert result == {"ok": False, "message": "Layar tidak bisa dilihat saat ini."}


@pytest.mark.parametrize("arguments", [{"monitor": 2}, "layar", None, []], ids=repr)
def test_arguments_other_than_an_empty_object_are_refused_without_a_capture(arguments):
    capture = FakeCapture()
    tools = ScreenTools(capture=capture)
    tools.begin_turn("Cyrene, lihat layarku")

    result, is_error = tools.run("look_at_screen", arguments)

    assert is_error is True
    assert json.loads(result)["message"].startswith("Argumen look_at_screen tidak valid")
    assert capture.sizes == []


def test_another_tool_name_is_an_error_result():
    result, is_error = ScreenTools(capture=FakeCapture()).run("control_device", {})

    assert (json.loads(result), is_error) == (
        {"ok": False, "message": "Tool 'control_device' bukan tool layar."},
        True,
    )


def test_the_image_never_reaches_the_log(caplog):
    tools = ScreenTools(capture=FakeCapture())

    with caplog.at_level(logging.DEBUG):
        tools.begin_turn("Cyrene, lihat layarku")
        look(tools)
        tools.take_images()

    assert "PNG" not in caplog.text
    assert base64.b64encode(SCREEN_PNG).decode("ascii")[:24] not in caplog.text
