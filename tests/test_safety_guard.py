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
