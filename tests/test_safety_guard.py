"""The suite-wide guard in conftest.py must stop real power commands before they run."""

import ctypes
import os
import subprocess
import sys

import pytest


def test_power_lock_env_is_set_for_the_whole_suite():
    assert os.environ.get("WAIFU_DISABLE_POWER") == "1"


@pytest.mark.parametrize(
    "args",
    [["shutdown", "/a"], ["C:\\Windows\\System32\\shutdown.exe", "/a"], "shutdown /a"],
)
def test_spawning_shutdown_is_refused(args):
    # "/a" only aborts a pending shutdown, so even a broken guard would do no harm here.
    with pytest.raises(AssertionError, match="real power command"):
        subprocess.run(args, capture_output=True, check=False)


def test_loading_powrprof_is_refused():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("ctypes.WinDLL only exists on Windows")
    with pytest.raises(AssertionError, match="PowrProf"):
        ctypes.WinDLL("PowrProf")


def test_ordinary_subprocesses_still_run():
    result = subprocess.run(
        [sys.executable, "-c", "print('ok')"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "ok"


def test_registry_writes_are_refused():
    winreg = pytest.importorskip("winreg")
    for writer in (winreg.SetValueEx, winreg.DeleteValue, winreg.CreateKeyEx):
        with pytest.raises(AssertionError, match="registry"):
            writer(None, None)


def test_registry_reads_still_work():
    winreg = pytest.importorskip("winreg")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        assert winreg.QueryInfoKey(key)[1] >= 0


# Bare verbs without arguments only print usage, so even a broken guard changes nothing.
@pytest.mark.parametrize(
    "args", [["reg", "add"], [r"C:\Windows\System32\reg.exe", "delete"], ["schtasks", "/create"]]
)
def test_registry_and_scheduled_task_changes_are_refused(args):
    with pytest.raises(AssertionError, match="system-change command"):
        subprocess.run(args, capture_output=True, check=False)


def test_spawning_the_real_rvc_server_is_refused():
    # The script path does not need to exist: the guard must refuse before anything starts.
    args = [sys.executable, "tools/rvc_server.py", "--port", "1"]
    with pytest.raises(AssertionError, match="system-change command"):
        subprocess.run(args, capture_output=True, check=False)
