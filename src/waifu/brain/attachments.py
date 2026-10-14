"""What Master sends through the phone camera with a message: a photo, and the camera itself.

The phone's turn puts its photo here right before the brain turn and clears it afterwards,
both under the Assistant's turn lock, so a failed turn never leaks its photo into the next
one. The Claude brain takes it at the start of that turn and sends it inside Master's own
message. At most one image waits at a time: a newer one replaces it. The bytes are only held
in memory, never written or logged.

A message sent while the phone's viewfinder was open makes its turn a camera turn, with or
without the photo (one that could not be taken): Master is showing her something through the
phone camera, so a bare "lihat ini" is about the camera, not the PC screen. The Assistant marks
such a turn in the same way, and the screen tool reads the mark when the turn begins.
"""

from __future__ import annotations

import threading

from waifu.brain.base import ToolImage


class TurnImages:
    """The image waiting for the next brain turn, shared by the Assistant and the brain."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._image: ToolImage | None = None

    def put(self, image: ToolImage) -> None:
        """Make `image` the one the next turn takes, replacing any image still waiting."""
        with self._lock:
            self._image = image

    def take(self) -> list[ToolImage]:
        """The waiting image, handed over once and then forgotten."""
        with self._lock:
            image, self._image = self._image, None
        return [] if image is None else [image]

    def clear(self) -> None:
        """Forget the waiting image without handing it over."""
        with self._lock:
            self._image = None


class CameraTurn:
    """Whether the brain turn under way was sent through the phone camera, shared by the
    Assistant and the screen tool."""

    def __init__(self) -> None:
        self._active = threading.Event()

    def begin(self) -> None:
        """Mark the turn about to be thought up as sent through the phone camera."""
        self._active.set()

    def end(self) -> None:
        """The marked turn is over; later turns are not sent through the camera."""
        self._active.clear()

    @property
    def active(self) -> bool:
        """Whether the turn under way was sent through the phone camera."""
        return self._active.is_set()
