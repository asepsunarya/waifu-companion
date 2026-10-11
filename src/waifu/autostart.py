"""Starts Cyrene when the owner logs in to Windows, through the per-user Run key.

The Run value runs `cmd.exe /c start ...` rather than `waifu.exe` directly: a Run
entry cannot choose its working directory, and `start /d` makes it the project
directory, where `config.yaml`, `data/` and `logs/` are resolved. `start /min` gives
Cyrene a minimised console window of her own; closing it stops her.

Only HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run is ever
touched. The registry sits behind the `RunKey` protocol, so tests use a dict-backed
fake and never write the real registry.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Protocol

logger = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Cyrene"
DEFAULT_DELAY_S = 15
LOG_FILE = Path("logs") / "waifu.log"

_WINDOW_TITLE = "Cyrene"


class RunKey(Protocol):
    """The per-user Run key: one named command per program started at login."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, command: str) -> None: ...

    def delete(self, name: str) -> bool:
        """Remove `name`; False when it was not there."""
        ...


class WindowsRunKey:
    """`RunKey` on the real registry: HKEY_CURRENT_USER and RUN_KEY_PATH only, REG_SZ values.

    `winreg_module` is the `winreg` module (imported lazily, so this module imports on
    any OS) or a stand-in with the same functions and constants for tests.
    """

    def __init__(self, winreg_module: Any = None) -> None:
        self._winreg_module = winreg_module

    def get(self, name: str) -> str | None:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
                value, _value_type = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return str(value)

    def set(self, name: str, command: str) -> None:
        winreg = self._winreg()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)

    def delete(self, name: str) -> bool:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return False
        return True

    def _winreg(self) -> Any:
        if self._winreg_module is not None:
            return self._winreg_module
        import winreg

        return winreg


@dataclass(frozen=True)
class AutostartStatus:
    enabled: bool
    command: str | None
    matches_expected: bool


def build_command(
    project_dir: PurePath,
    waifu_exe: PurePath,
    *,
    delay_s: int = DEFAULT_DELAY_S,
    comspec: str | None = None,
) -> str:
    """The Run value that starts `waifu run` minimised, in `project_dir`, after `delay_s`.

    Both paths must be absolute and free of `"` and `%` (see `_checked_path`).
    `comspec` defaults to the ComSpec environment variable (Windows variable names are
    case-insensitive), then plain `cmd.exe`.
    """
    if isinstance(delay_s, bool) or not isinstance(delay_s, int) or delay_s < 0:
        raise ValueError(f"startup delay must be a whole number of seconds >= 0, got {delay_s!r}")
    directory = _checked_path("project_dir", project_dir)
    program = _checked_path("waifu_exe", waifu_exe)
    shell = comspec or os.environ.get("COMSPEC") or "cmd.exe"
    if " " in shell or "\t" in shell:
        shell = f'"{shell}"'
    # `cmd /c` removes the first and the last quote of the rest of the line when that rest
    # starts with a quote (see `cmd /?`). Here it starts with `start`, so every quote stays.
    # The quoted title comes first because `start` would take a quoted program as the title.
    return (
        f'{shell} /c start "{_WINDOW_TITLE}" /min /d "{directory}" "{program}" '
        f"run --startup-delay {delay_s} --announce --log-file {PureWindowsPath(LOG_FILE)}"
    )


def enable(run_key: RunKey, command: str) -> None:
    """Start Cyrene with `command` at every login, replacing any older command."""
    run_key.set(RUN_VALUE, command)
    logger.info("Autostart enabled: %s", command)


def disable(run_key: RunKey) -> bool:
    """Stop starting Cyrene at login; False when autostart was already off."""
    removed = run_key.delete(RUN_VALUE)
    logger.info("Autostart disabled" if removed else "Autostart was already disabled")
    return removed


def status(run_key: RunKey, expected_command: str) -> AutostartStatus:
    """Whether autostart is on, and whether it still runs exactly `expected_command`.

    A stored command that differs is stale (the project or its venv moved, or the delay
    changed): it would start the wrong thing, or nothing, at the next login.
    """
    command = run_key.get(RUN_VALUE)
    return AutostartStatus(
        enabled=command is not None,
        command=command,
        matches_expected=command == expected_command,
    )


def _checked_path(label: str, path: PurePath) -> str:
    """`path` as text, refused when it cannot sit safely inside the quoted Run command.

    A double quote would end the quoted argument early. A percent sign is refused
    because the Run value runs through cmd.exe, which expands `%NAME%` on its command
    line before running it and has no reliable escape for `%` inside quotes, so
    `C:\\100%USERPROFILE%x` would silently become a different path.
    """
    text = str(path)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path, got {text!r}")
    if '"' in text:
        raise ValueError(f"{label} must not contain a double quote: {text!r}")
    if "%" in text:
        raise ValueError(f"{label} must not contain %, which cmd.exe would expand: {text!r}")
    return text
