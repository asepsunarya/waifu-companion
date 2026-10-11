"""Learned IR codes, kept in one small JSON file: {device_id: {command: base64}}.

The file is re-read on every call so a code learned from another process
(`waifu learn-ir`) is visible immediately, and every write is flushed to disk
and then replaces the file atomically, so neither a crash mid-write nor a power
cut right after it loses previously learned codes.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

COMMAND_PATTERN = re.compile(r"^[a-z0-9_]+$")

Codes = dict[str, dict[str, str]]


class IrCodeStoreError(ValueError):
    """The IR code file exists but is not valid JSON of the expected shape."""


def validate_command_name(command: str) -> str:
    """Return `command` unchanged, or raise ValueError if it is not a lowercase slug."""
    if not COMMAND_PATTERN.match(command):
        raise ValueError(
            f"invalid IR command name '{command}': use lowercase letters, digits, or underscores"
        )
    return command


class IrCodeStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def get(self, device_id: str, command: str) -> str | None:
        return self._load().get(device_id, {}).get(command)

    def commands(self, device_id: str) -> list[str]:
        return sorted(self._load().get(device_id, {}))

    def put(self, device_id: str, command: str, code: str) -> None:
        validate_command_name(command)
        codes = self._load()
        codes.setdefault(device_id, {})[command] = code
        self._write(codes)

    def _load(self) -> Codes:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise IrCodeStoreError(f"{self.path} is not valid JSON: {exc}") from exc
        if not _has_expected_shape(raw):
            raise IrCodeStoreError(
                f"{self.path} must map device ids to {{command: base64 code}} objects"
            )
        return raw

    def _write(self, codes: Codes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(codes, temp_file, indent=2, ensure_ascii=False, sort_keys=True)
                temp_file.write("\n")
                # NTFS can commit the rename before the data, so a power cut right
                # after it would leave an empty file; push the data to disk first.
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise


def _has_expected_shape(raw: object) -> bool:
    return isinstance(raw, dict) and all(
        isinstance(device_codes, dict)
        and all(isinstance(code, str) for code in device_codes.values())
        for device_codes in raw.values()
    )
