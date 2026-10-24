"""The mic and the voice settings Master picked on the phone's Suara page (SPEC, diagnosa suara
(v1.47)), kept apart from config.yaml, which stays his own.

The file holds the mic picked (`input_device`, a "name, host API" from the list the page
offers), the speaker threshold, and the VAD's threshold and margin, each only when set. It
follows the rules of `voice_level.py`: re-read at every call, replaced atomically, never created
by reading, and a file that is not such an override is moved aside, never overwritten; one lock
serialises the calls of every store in this process. Each setting stays within its bounds, also
when the file is edited by hand.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

FILE_VERSION = 1
SPEAKER_BOUNDS = (0.30, 0.70)
VAD_THRESHOLD_BOUNDS = (-70.0, -25.0)
VAD_MARGIN_BOUNDS = (3.0, 30.0)
SPEAKER_PROBLEM = "Ambang kecocokan suara harus 0,30 sampai 0,70."
VAD_THRESHOLD_PROBLEM = "Ambang VAD harus -70 sampai -25 dB."
VAD_MARGIN_PROBLEM = "Margin VAD harus 3 sampai 30 dB."
_SETTINGS = ("input_device", "speaker_threshold", "vad_threshold_db", "vad_margin_db")

# One lock for every store: two of them on the same file must not lose a change.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class VoiceOverride:
    """What the phone set; None leaves the config's value."""

    input_device: str | None = None
    speaker_threshold: float | None = None
    vad_threshold_db: float | None = None
    vad_margin_db: float | None = None

    @property
    def tunes(self) -> bool:
        """Whether it changes the speaker threshold or the VAD's gate."""
        return any(
            value is not None
            for value in (self.speaker_threshold, self.vad_threshold_db, self.vad_margin_db)
        )


def checked_tuning(
    *,
    speaker_threshold: object = None,
    vad_threshold_db: object = None,
    vad_margin_db: object = None,
) -> tuple[float | None, float | None, float | None]:
    """The settings given (None: not given) as floats; ValueError (Indonesian) for one that is
    not a finite number (a bool is none) within its bounds."""
    return (
        _within(speaker_threshold, SPEAKER_BOUNDS, SPEAKER_PROBLEM),
        _within(vad_threshold_db, VAD_THRESHOLD_BOUNDS, VAD_THRESHOLD_PROBLEM),
        _within(vad_margin_db, VAD_MARGIN_BOUNDS, VAD_MARGIN_PROBLEM),
    )


def _within(value: object, bounds: tuple[float, float], problem: str) -> float | None:
    if value is None:
        return None
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(problem)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(problem)
    return float(value)


class VoiceOverrideStore:
    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = datetime.now) -> None:
        """`now` names a broken file moved aside."""
        self.path = Path(path)
        self._now = now

    def read(self) -> VoiceOverride:
        """What the phone set; a missing file sets nothing. OSError when it cannot be read."""
        with _LOCK:
            return self._read()

    def update(self, change: Callable[[VoiceOverride], VoiceOverride]) -> VoiceOverride:
        """Write what `change` makes of the override; returns it."""
        with _LOCK:
            changed = change(self._read())
            content: dict[str, object] = {"version": FILE_VERSION}
            content |= {
                key: getattr(changed, key) for key in _SETTINGS if getattr(changed, key) is not None
            }
            write_json_atomic(self.path, content)
        return changed

    def _read(self) -> VoiceOverride:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return VoiceOverride()
        except ValueError as problem:  # also bad JSON, bad UTF-8, and a setting out of bounds
            self._set_aside(problem)
            return VoiceOverride()

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not an override, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "The voice override %s is not one (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "The voice override %s is not one (%s); moved it to %s and started anew",
            self.path,
            problem,
            target,
        )


def _parse(raw: object) -> VoiceOverride:
    """The override in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or "version" not in raw:
        raise ValueError("expected an object with a version")
    if not raw.keys() <= {"version", *_SETTINGS}:
        raise ValueError(f"expected only the keys version, {', '.join(_SETTINGS)}")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    device = raw.get("input_device")
    if device is not None and (not isinstance(device, str) or not device.strip()):
        raise ValueError("input_device must be a mic's name")
    speaker, threshold, margin = checked_tuning(
        speaker_threshold=raw.get("speaker_threshold"),
        vad_threshold_db=raw.get("vad_threshold_db"),
        vad_margin_db=raw.get("vad_margin_db"),
    )
    return VoiceOverride(device, speaker, threshold, margin)
