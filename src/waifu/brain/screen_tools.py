"""The tool that lets Cyrene look at Master's screen: one screenshot, only when Master asks.

The brain hands every turn's words, as heard, to `begin_turn`; the screen is captured only
when those words ask her to look (a look word such as "lihat" or "layar", her names left
out), at most once per turn, whatever the model wants. In a turn sent through the phone
camera (`CameraTurn`), Master is showing her something on the phone, so only words that name
the screen itself ("layar", "screenshot") ask her to look at the PC: "coba lihat ini" is about
the camera, also when its photo could not be taken. The screenshot goes back to the model
with the tool's result (`take_images`, which only the Claude brain asks for) and is never
written to disk or logged; one too large for the API's image limit is taken once more,
smaller. The spec carries no state, so it stays byte-identical for the brain's lifetime and
keeps the prompt cache warm.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping

from waifu.brain.attachments import CameraTurn
from waifu.brain.base import ToolImage, ToolSpec
from waifu.screen import ScreenImage, capture_screen

logger = logging.getLogger(__name__)

LOOK_AT_SCREEN = "look_at_screen"
# Words that name the screen itself: in a turn sent through the phone camera, Master's words
# must hold one of these for her to look at the PC in that turn.
SCREEN_WORDS = frozenset({"layar", "layarku", "screen", "screenshot", "monitor"})
# Master's words in any other turn must hold one of these for her to look in that turn.
LOOK_WORDS = SCREEN_WORDS | frozenset(
    {"lihat", "liat", "lihatin", "liatin", "tengok", "tengokin", "見て"}
)
DEFAULT_MAX_SIDE = 1280
# An image's base64 data may be at most 5 MB: the smallest per-image limit of the platforms
# that serve Claude. A busy screen (a detailed game) barely compresses, so at a max_side above
# about 1450 its PNG may not fit.
MAX_IMAGE_BASE64_BYTES = 5 * 2**20
# A PNG's size follows its pixel count, so a screenshot that is too large is taken once
# more with each side shrunk by the square root of how much too large it was, and this.
_SHRINK_MARGIN = 0.9

# Runs of letters and digits in any script, as in brain.confirm, so "見て" is a word too.
_WORD = re.compile(r"[^\W_]+")
_PNG_MEDIA_TYPE = "image/png"
_NOT_ASKED = (
    "Master tidak meminta melihat layar di pesan ini, jadi layarnya tidak dilihat. Lihat layar "
    "hanya kalau pesan Master sendiri memintanya."
)
_SHOWN_ON_CAMERA = (
    "Pesan ini dikirim saat kamera HP Master terbuka: yang Master tunjukkan ada di kamera HP, "
    "bukan di layar PC, jadi layar PC tidak dilihat. Kalau pesan ini tidak membawa foto, fotonya "
    "gagal terkirim. Lihat layar PC hanya kalau Master menyebut layarnya."
)
_ONCE_PER_TURN = "Layar hanya dilihat sekali untuk setiap pesan Master."
_CAPTURE_FAILED = "Layar tidak bisa dilihat: Windows gagal mengambilnya."
_TOO_LARGE = "Layar tidak bisa dilihat: gambarnya terlalu besar untuk dikirim."
_FAILED = "Layar tidak bisa dilihat saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class ScreenTools:
    """A `ToolProvider` for the brain: one screenshot of the main monitor when Master asks.

    `camera_turn`, shared with the Assistant while the phone camera is offered, tells which
    turns were sent through the phone camera.
    """

    def __init__(
        self,
        *,
        capture: Callable[[int], ScreenImage] = capture_screen,
        max_side: int = DEFAULT_MAX_SIDE,
        names: Iterable[str] = (),
        camera_turn: CameraTurn | None = None,
    ) -> None:
        self._capture = capture
        self._max_side = max_side
        self._names = frozenset(word for name in names for word in _words(name))
        self._camera_turn = camera_turn
        self._asked = False  # whether this turn's words ask her to look
        self._on_camera = False  # whether this turn was sent through the phone camera
        self._looked = False  # whether this turn already tried to capture
        self._image: ToolImage | None = None  # the last screenshot, until handed over

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=LOOK_AT_SCREEN,
                description=(
                    "Take one screenshot of Master's main monitor to see what Master is "
                    "showing you; it comes back as an image with this result. Call it only "
                    "when Master's current message asks you to look at the screen ('lihat "
                    "layarku', 'liat deh', 'coba lihat'); otherwise it is refused. At most "
                    "one screenshot per message. Then describe and answer about what is "
                    "visible. The screen may show private things: never read out private "
                    "details (messages, emails, account numbers) unless Master asks about them, "
                    "and never save anything you see on the screen with `remember` unless "
                    "Master asks you to. If your avatar is on, the screenshot may show it as an "
                    "anime figure, usually in a corner: that is you, not part of what Master "
                    "is showing, so ignore it unless Master asks. A screenshot that is all "
                    "black usually means a game in exclusive fullscreen, which cannot be "
                    "captured: suggest Master switch the game to borderless or windowed mode."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with Master's words as heard; only these can ask her to look, and in a
        turn sent through the phone camera only by naming the screen.

        A screenshot never taken from the turn before is forgotten.
        """
        self._on_camera = self._camera_turn is not None and self._camera_turn.active
        asking = SCREEN_WORDS if self._on_camera else LOOK_WORDS
        self._asked = not asking.isdisjoint(set(_words(user_text)) - self._names)
        self._looked = False
        self._image = None

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == LOOK_AT_SCREEN:
                return self._look(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Screen tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool layar.")

    def take_images(self) -> list[ToolImage]:
        """The screenshot of the last successful look, handed over once and then forgotten."""
        image, self._image = self._image, None
        return [] if image is None else [image]

    def _look(self, arguments: object) -> tuple[str, bool]:
        if not isinstance(arguments, Mapping) or arguments:
            raise _InvalidArguments("tool ini tidak menerima argumen; kirim objek kosong {}.")
        if not self._asked and self._on_camera:
            logger.info("Not looking at the screen: Master is showing the phone camera")
            return _error(_SHOWN_ON_CAMERA)
        if not self._asked:
            logger.info("Not looking at the screen: Master did not ask to in this turn")
            return _error(_NOT_ASKED)
        if self._looked:
            return _error(_ONCE_PER_TURN)
        self._looked = True
        try:
            screenshot = self._capture_within_limit()
        except OSError as exc:
            logger.warning("Could not capture the screen: %s", exc)
            return _error(_CAPTURE_FAILED)
        if screenshot is None:
            return _error(_TOO_LARGE)
        self._image = ToolImage(media_type=_PNG_MEDIA_TYPE, data=screenshot.png)
        return _to_json({"ok": True, "width": screenshot.width, "height": screenshot.height}), False

    def _capture_within_limit(self) -> ScreenImage | None:
        """A screenshot whose base64 data fits the API's image limit, taken again smaller when
        the first does not fit; None when even that one does not."""
        screenshot = self._capture(self._max_side)
        size = _base64_size(screenshot.png)
        if size <= MAX_IMAGE_BASE64_BYTES:
            return screenshot
        longer = max(screenshot.width, screenshot.height)
        smaller = int(longer * math.sqrt(MAX_IMAGE_BASE64_BYTES / size) * _SHRINK_MARGIN)
        logger.info(
            "The screenshot is too large to send (%d KB as base64); taking it again at %d pixels",
            size // 1024,
            smaller,
        )
        screenshot = self._capture(smaller)
        size = _base64_size(screenshot.png)
        if size <= MAX_IMAGE_BASE64_BYTES:
            return screenshot
        logger.warning("The smaller screenshot is still too large to send (%d KB)", size // 1024)
        return None


def _words(text: str) -> list[str]:
    """The words of `text` as brain.confirm splits Master's answer: NFKC, lowercase."""
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())


def _base64_size(data: bytes) -> int:
    """The length of `data` as base64: four characters for every three bytes, padded."""
    return 4 * math.ceil(len(data) / 3)


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
