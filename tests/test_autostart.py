import os
import subprocess
from pathlib import PureWindowsPath

import pytest

from waifu.autostart import (
    DEFAULT_DELAY_S,
    LOG_FILE,
    RUN_KEY_PATH,
    RUN_VALUE,
    AutostartStatus,
    WindowsRunKey,
    build_command,
    disable,
    enable,
    status,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="the Run key exists only on Windows")

CMD = r"C:\WINDOWS\system32\cmd.exe"
PROJECT = PureWindowsPath(r"C:\Users\Lynx Test\waifu companion")
WAIFU_EXE = PROJECT / ".venv" / "Scripts" / "waifu.exe"
MOVED_PROJECT = PureWindowsPath(r"D:\Proyek Baru\waifu companion")
MOVED_EXE = MOVED_PROJECT / ".venv" / "Scripts" / "waifu.exe"


def split_windows_args(text: str) -> list[str]:
    """Split a command line like the MS C runtime does (the inverse of list2cmdline)."""
    args: list[str] = []
    current: list[str] = []
    in_quotes = False
    in_token = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            end = index
            while end < len(text) and text[end] == "\\":
                end += 1
            count = end - index
            if end < len(text) and text[end] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    end += 1
            else:
                current.append("\\" * count)
            in_token = True
            index = end
            continue
        if char == '"':
            in_quotes = not in_quotes
            in_token = True
        elif char in " \t" and not in_quotes:
            if in_token:
                args.append("".join(current))
                current = []
                in_token = False
        else:
            current.append(char)
            in_token = True
        index += 1
    if in_token:
        args.append("".join(current))
    return args


@pytest.mark.parametrize(
    "args",
    [
        ["start", "Cyrene", "/d", r"C:\Users\Lynx Test\waifu companion"],
        [r"C:\dir with space\\", 'say "hai"', "", r"logs\waifu.log", "a\tb"],
    ],
)
def test_the_test_splitter_inverts_list2cmdline(args):
    assert split_windows_args(subprocess.list2cmdline(args)) == args


class FakeRunKey:
    """A Run key held in a dict; starts with someone else's entry that must survive."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = {"OneDrive": r'"C:\OneDrive.exe" /background', **(values or {})}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, command: str) -> None:
        self.values[name] = command

    def delete(self, name: str) -> bool:
        return self.values.pop(name, None) is not None


# --- build_command ---------------------------------------------------------------


def test_build_command_writes_the_exact_run_key_command():
    command = build_command(PROJECT, WAIFU_EXE, delay_s=15, comspec=CMD)

    assert command == (
        r'C:\WINDOWS\system32\cmd.exe /c start "Cyrene" /min'
        r' /d "C:\Users\Lynx Test\waifu companion"'
        r' "C:\Users\Lynx Test\waifu companion\.venv\Scripts\waifu.exe"'
        r" run --startup-delay 15 --announce --log-file logs\waifu.log"
    )


def test_build_command_round_trips_paths_with_spaces_through_windows_parsing():
    command = build_command(PROJECT, WAIFU_EXE, delay_s=20, comspec=CMD)

    assert split_windows_args(command) == [
        CMD,
        "/c",
        "start",
        "Cyrene",
        "/min",
        "/d",
        str(PROJECT),
        str(WAIFU_EXE),
        "run",
        "--startup-delay",
        "20",
        "--announce",
        "--log-file",
        str(PureWindowsPath(LOG_FILE)),
    ]


def test_text_after_slash_c_starts_with_start_so_cmd_keeps_every_quote():
    command = build_command(PROJECT, WAIFU_EXE, comspec=CMD)

    after_c = command.split(" /c ", 1)[1]
    assert after_c.startswith('start "Cyrene" ')


def test_build_command_quotes_a_comspec_with_spaces():
    comspec = r"C:\Program Files\Shell\cmd.exe"

    command = build_command(PROJECT, WAIFU_EXE, comspec=comspec)

    assert command.startswith(f'"{comspec}" /c start ')
    assert split_windows_args(command)[:2] == [comspec, "/c"]


def test_build_command_takes_comspec_from_the_environment(monkeypatch):
    monkeypatch.setenv("COMSPEC", r"D:\Tools\cmd.exe")

    command = build_command(PROJECT, WAIFU_EXE)

    assert command.startswith(r"D:\Tools\cmd.exe /c start ")


def test_build_command_falls_back_to_cmd_exe_without_comspec(monkeypatch):
    monkeypatch.delenv("COMSPEC", raising=False)

    command = build_command(PROJECT, WAIFU_EXE)

    assert command.startswith("cmd.exe /c start ")


def test_build_command_waits_the_default_delay_unless_told_otherwise():
    default = split_windows_args(build_command(PROJECT, WAIFU_EXE, comspec=CMD))
    no_wait = split_windows_args(build_command(PROJECT, WAIFU_EXE, delay_s=0, comspec=CMD))

    assert DEFAULT_DELAY_S == 15
    assert default[default.index("--startup-delay") + 1] == "15"
    assert no_wait[no_wait.index("--startup-delay") + 1] == "0"


@pytest.mark.parametrize(
    ("project_dir", "waifu_exe"),
    [
        (PureWindowsPath("waifu companion"), WAIFU_EXE),
        (PROJECT, PureWindowsPath(r".venv\Scripts\waifu.exe")),
        (PureWindowsPath(r"C:waifu"), WAIFU_EXE),
    ],
)
def test_build_command_rejects_relative_paths(project_dir, waifu_exe):
    with pytest.raises(ValueError, match="absolute"):
        build_command(project_dir, waifu_exe, comspec=CMD)


@pytest.mark.parametrize(
    ("project_dir", "waifu_exe"),
    [
        (PureWindowsPath(r'C:\Users\Lynx "Test"\waifu'), WAIFU_EXE),
        (PROJECT, PureWindowsPath(r'C:\bin"\waifu.exe')),
    ],
)
def test_build_command_rejects_paths_with_a_double_quote(project_dir, waifu_exe):
    with pytest.raises(ValueError, match="quote"):
        build_command(project_dir, waifu_exe, comspec=CMD)


@pytest.mark.parametrize(
    ("project_dir", "waifu_exe"),
    [
        (PureWindowsPath(r"C:\Users\%USERNAME%\waifu"), WAIFU_EXE),
        (PROJECT, PureWindowsPath(r"C:\100% waifu\waifu.exe")),
    ],
)
def test_build_command_rejects_paths_with_a_percent_sign(project_dir, waifu_exe):
    with pytest.raises(ValueError, match="%"):
        build_command(project_dir, waifu_exe, comspec=CMD)


@pytest.mark.parametrize("delay_s", [-1, 1.5, True, "15"])
def test_build_command_rejects_a_delay_that_is_not_a_whole_non_negative_number(delay_s):
    with pytest.raises(ValueError, match="delay"):
        build_command(PROJECT, WAIFU_EXE, delay_s=delay_s, comspec=CMD)


# --- enable / disable / status ---------------------------------------------------


def test_enable_stores_the_command_under_the_cyrene_value():
    run_key = FakeRunKey()

    enable(run_key, "the command")

    assert RUN_VALUE == "Cyrene"
    assert run_key.values["Cyrene"] == "the command"


def test_enable_replaces_an_older_command():
    run_key = FakeRunKey({"Cyrene": "old command"})

    enable(run_key, "new command")

    assert run_key.values["Cyrene"] == "new command"


def test_disable_removes_only_the_cyrene_value():
    run_key = FakeRunKey({"Cyrene": "the command"})

    assert disable(run_key) is True
    assert "Cyrene" not in run_key.values
    assert "OneDrive" in run_key.values


def test_disable_reports_false_when_autostart_was_already_off():
    run_key = FakeRunKey()

    assert disable(run_key) is False
    assert "OneDrive" in run_key.values


def test_status_when_disabled():
    assert status(FakeRunKey(), "expected") == AutostartStatus(
        enabled=False, command=None, matches_expected=False
    )


def test_status_when_enabled_with_the_expected_command():
    expected = build_command(PROJECT, WAIFU_EXE, comspec=CMD)
    run_key = FakeRunKey()
    enable(run_key, expected)

    assert status(run_key, expected) == AutostartStatus(
        enabled=True, command=expected, matches_expected=True
    )


def test_status_flags_a_stale_command_after_the_project_moved():
    stored = build_command(PROJECT, WAIFU_EXE, comspec=CMD)
    run_key = FakeRunKey({"Cyrene": stored})

    result = status(run_key, build_command(MOVED_PROJECT, MOVED_EXE, comspec=CMD))

    assert result == AutostartStatus(enabled=True, command=stored, matches_expected=False)


def test_status_compares_the_exact_string():
    expected = build_command(PROJECT, WAIFU_EXE, comspec=CMD)
    run_key = FakeRunKey({"Cyrene": expected + " "})

    assert status(run_key, expected).matches_expected is False


# --- WindowsRunKey ---------------------------------------------------------------


class FakeKey:
    def __init__(self, hive: object, path: str, access: int) -> None:
        self.hive = hive
        self.path = path
        self.access = access
        self.closed = False

    def __enter__(self) -> "FakeKey":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True


class FakeWinreg:
    """The slice of `winreg` that WindowsRunKey uses, with access checks like the real API.

    Constants deliberately differ from the real ones, so the code must take them from
    the injected module instead of hard-coding numbers.
    """

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 101
    KEY_SET_VALUE = 102
    REG_SZ = 201
    REG_EXPAND_SZ = 202

    def __init__(self, values: dict[str, str] | None = None, *, key_exists: bool = True) -> None:
        self.keys: dict[tuple[object, str], dict[str, tuple[object, int]]] = {}
        if key_exists:
            run = self.keys.setdefault((self.HKEY_CURRENT_USER, RUN_KEY_PATH), {})
            for name, value in (values or {}).items():
                run[name] = (value, self.REG_SZ)
        self.opened: list[tuple[str, object, str, int, int]] = []
        self.handles: list[FakeKey] = []

    def OpenKey(self, key: object, sub_key: str, reserved: int = 0, access: int = 101) -> FakeKey:
        self.opened.append(("OpenKey", key, sub_key, reserved, access))
        if (key, sub_key) not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return self._handle(key, sub_key, access)

    def CreateKeyEx(self, key: object, sub_key: str, reserved: int = 0, access: int = 0) -> FakeKey:
        self.opened.append(("CreateKeyEx", key, sub_key, reserved, access))
        self.keys.setdefault((key, sub_key), {})
        return self._handle(key, sub_key, access)

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[object, int]:
        self._require(key, self.KEY_READ)
        values = self.keys[(key.hive, key.path)]
        if name not in values:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return values[name]

    def SetValueEx(self, key: FakeKey, name: str, reserved: int, type: int, value: str) -> None:
        self._require(key, self.KEY_SET_VALUE)
        self.keys[(key.hive, key.path)][name] = (value, type)

    def DeleteValue(self, key: FakeKey, name: str) -> None:
        self._require(key, self.KEY_SET_VALUE)
        values = self.keys[(key.hive, key.path)]
        if name not in values:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        del values[name]

    def run_values(self) -> dict[str, tuple[object, int]]:
        return self.keys[(self.HKEY_CURRENT_USER, RUN_KEY_PATH)]

    def _handle(self, hive: object, path: str, access: int) -> FakeKey:
        handle = FakeKey(hive, path, access)
        self.handles.append(handle)
        return handle

    def _require(self, key: FakeKey, access: int) -> None:
        assert not key.closed, "used a closed registry key"
        if key.access != access:
            raise PermissionError(5, "Access is denied")


def test_run_key_path_is_the_per_user_run_key():
    assert RUN_KEY_PATH == r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_windows_run_key_set_writes_a_reg_sz_value_in_the_per_user_run_key():
    winreg = FakeWinreg()

    WindowsRunKey(winreg_module=winreg).set("Cyrene", "the command")

    assert winreg.opened == [("CreateKeyEx", "HKCU", RUN_KEY_PATH, 0, FakeWinreg.KEY_SET_VALUE)]
    assert winreg.run_values()["Cyrene"] == ("the command", FakeWinreg.REG_SZ)
    assert all(handle.closed for handle in winreg.handles)


def test_windows_run_key_get_reads_the_value_with_read_access():
    winreg = FakeWinreg({"Cyrene": "the command"})

    assert WindowsRunKey(winreg_module=winreg).get("Cyrene") == "the command"
    assert winreg.opened == [("OpenKey", "HKCU", RUN_KEY_PATH, 0, FakeWinreg.KEY_READ)]
    assert all(handle.closed for handle in winreg.handles)


def test_windows_run_key_get_returns_none_when_the_value_is_absent():
    assert WindowsRunKey(winreg_module=FakeWinreg()).get("Cyrene") is None


def test_windows_run_key_get_returns_none_when_the_run_key_is_absent():
    assert WindowsRunKey(winreg_module=FakeWinreg(key_exists=False)).get("Cyrene") is None


def test_windows_run_key_delete_removes_the_value():
    winreg = FakeWinreg({"Cyrene": "the command", "OneDrive": "onedrive.exe"})

    assert WindowsRunKey(winreg_module=winreg).delete("Cyrene") is True
    assert winreg.opened == [("OpenKey", "HKCU", RUN_KEY_PATH, 0, FakeWinreg.KEY_SET_VALUE)]
    assert winreg.run_values() == {"OneDrive": ("onedrive.exe", FakeWinreg.REG_SZ)}
    assert all(handle.closed for handle in winreg.handles)


def test_windows_run_key_delete_returns_false_when_the_value_is_absent():
    winreg = FakeWinreg({"OneDrive": "onedrive.exe"})

    assert WindowsRunKey(winreg_module=winreg).delete("Cyrene") is False
    assert winreg.run_values() == {"OneDrive": ("onedrive.exe", FakeWinreg.REG_SZ)}


def test_windows_run_key_delete_returns_false_when_the_run_key_is_absent():
    winreg = FakeWinreg(key_exists=False)

    assert WindowsRunKey(winreg_module=winreg).delete("Cyrene") is False
    assert winreg.keys == {}


def test_windows_run_key_only_ever_touches_the_per_user_run_key():
    winreg = FakeWinreg()
    run_key = WindowsRunKey(winreg_module=winreg)

    enable(run_key, "the command")
    status(run_key, "the command")
    disable(run_key)

    assert {(hive, path) for _, hive, path, _, _ in winreg.opened} == {("HKCU", RUN_KEY_PATH)}


@windows_only
def test_windows_run_key_reads_the_real_registry_without_writing():
    # Read-only: tests/conftest.py makes every real registry write fail the test.
    assert WindowsRunKey().get("CyreneTestDoesNotExist") is None
