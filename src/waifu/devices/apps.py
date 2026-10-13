"""The owner's PC apps and games: opens only what config.yaml lists, never closes anything.

Each app is a program, a shortcut, or a launcher URL (`steam://...`), opened through
an injected starter (Windows' own "open", `os.startfile`), so tests never start one.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from pathlib import PureWindowsPath

from waifu.config import APP_URL_SCHEMES
from waifu.devices.base import Device, DeviceError, DeviceResult
from waifu.devices.ir_device import resolve_command

logger = logging.getLogger(__name__)

OPEN_PREFIX = "open_"
# winerror.h: Master answered No to Windows' administrator (UAC) prompt.
ERROR_CANCELLED = 1223


def _shell_open(target: str, folder: str | None) -> None:
    """Open `target` like a double-click in Explorer, in working folder `folder` (None leaves
    it to a shortcut's own "Start in"). Follows shortcuts and launcher URLs and returns without
    waiting for the program, but only once an administrator prompt it raises is answered.
    Raises OSError when Windows cannot open it.

    `os.startfile` is looked up on every call, never bound at import, so the test suite's
    guard on it (tests/conftest.py) also covers this default.
    """
    os.startfile(target, cwd=folder)


class AppLauncher(Device):
    """Opens the apps listed in config.yaml, one `open_<slug>` command each.

    `value` only picks an app whose name ends in that number, by the rule the registry and
    the dry-run mock use (`open_gta` + 5 opens `gta_5`). Otherwise it is ignored: an app
    has no setting, and the dry-run mock and the schedule tools accept a stray value too,
    so refusing it would only fail for real.
    """

    kind = "apps"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        apps: Mapping[str, str],
        *,
        start: Callable[[str, str | None], None] = _shell_open,
        exists: Callable[[str], bool] = os.path.exists,
    ) -> None:
        super().__init__(device_id, name, aliases)
        self._apps = dict(apps)
        self._start = start
        self._exists = exists

    def commands(self) -> list[str]:
        return [f"{OPEN_PREFIX}{slug}" for slug in self._apps]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        resolved = resolve_command(command, value, self.commands())
        if resolved is None:
            raise DeviceError(
                f"{self.name} tidak punya perintah '{command}'. "
                f"Perintah yang tersedia: {', '.join(self.commands())}."
            )
        slug = resolved.removeprefix(OPEN_PREFIX)
        target = self._apps[slug]
        folder = None
        if not target.lower().startswith(APP_URL_SCHEMES):
            # ShellExecute is not documented to accept "/" separators, which config.yaml allows.
            path = PureWindowsPath(target)
            target = str(path)
            if not self._exists(target):
                logger.warning("App '%s' of %s not found at %s", slug, self.id, target)
                raise DeviceError(
                    f"{slug} tidak bisa dibuka: file-nya tidak ditemukan. Mungkin sudah "
                    "dipindah atau di-uninstall; perbarui path-nya di config.yaml."
                )
            # A program starts in its own folder, as from Explorer, not in Cyrene's (the
            # project); a shortcut brings its own "Start in" folder.
            if path.suffix.lower() == ".exe":
                folder = str(path.parent)
        try:
            self._start(target, folder)
        except OSError as exc:
            logger.warning("Could not open app '%s' of %s: %s", slug, self.id, exc)
            if getattr(exc, "winerror", None) == ERROR_CANCELLED:
                raise DeviceError(
                    f"Izin administrator untuk {slug} ditolak di jendela izin Windows, jadi "
                    f"{slug} tidak dibuka."
                ) from exc
            raise DeviceError(
                f"Windows gagal membuka {slug}. Pastikan program atau launcher-nya masih terpasang."
            ) from exc
        logger.info("Opened app '%s' of %s", slug, self.id)
        self.state["last_opened"] = slug
        return DeviceResult(ok=True, message=f"{slug} dibuka.", state=dict(self.state))
