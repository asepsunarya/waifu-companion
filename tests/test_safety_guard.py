"""The suite-wide guard in conftest.py must stop real power commands before they run."""

import asyncio
import ctypes
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import aiohttp
import pytest

from waifu.gacha.games import GAMES
from waifu.gacha.history import find_cached_gacha_url


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


@pytest.mark.parametrize(
    "program",
    [
        r"C:\x\avatar-app\node_modules\electron\dist\electron.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
)
def test_opening_a_real_avatar_window_is_refused(program):
    with pytest.raises(AssertionError, match="system-change command"):
        subprocess.run([program, "--version"], capture_output=True, check=False)


# A push to the owner's phone or a HoYoLAB check-in must never come from a test.
@pytest.mark.parametrize(
    "url", ["https://ntfy.sh/", "https://sg-public-api.hoyolab.com/x", "http://192.168.1.50/"]
)
def test_urllib_requests_beyond_this_pc_are_refused(url):
    with pytest.raises(AssertionError, match="internet"):
        urllib.request.urlopen(url, timeout=1)


def test_urllib_requests_to_loopback_still_go_out():
    # Port 9 (discard) is closed: reaching the socket layer proves the guard let it through.
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)


async def _aiohttp_get(url: str) -> None:
    async with aiohttp.ClientSession() as session, session.get(url) as response:
        await response.read()


def test_aiohttp_requests_beyond_this_pc_are_refused():
    with pytest.raises(AssertionError, match="internet"):
        asyncio.run(_aiohttp_get("https://sg-public-api.hoyolab.com/event/game_record"))


def test_aiohttp_requests_to_loopback_still_go_out():
    with pytest.raises(aiohttp.ClientConnectionError):
        asyncio.run(_aiohttp_get("http://127.0.0.1:9/"))


def test_reading_the_owners_game_caches_is_refused(real_game_cache_reads):
    # The logs lead to the history caches, which hold the owner's authkeys.
    with pytest.raises(AssertionError, match="real game logs"):
        find_cached_gacha_url(GAMES["hsr"], Path.home() / "AppData" / "LocalLow")

    assert real_game_cache_reads == ["hsr"]  # also failed at teardown, even if swallowed
    real_game_cache_reads.clear()  # this one was on purpose


def test_game_caches_elsewhere_can_still_be_read(tmp_path):
    assert find_cached_gacha_url(GAMES["hsr"], tmp_path) is None


def test_opening_a_program_or_url_with_startfile_is_refused(tmp_path):
    if not hasattr(os, "startfile"):
        pytest.skip("os.startfile only exists on Windows")
    # A missing file, so even a broken guard would only raise FileNotFoundError.
    with pytest.raises(AssertionError, match="startfile"):
        os.startfile(str(tmp_path / "missing.exe"))


@pytest.mark.parametrize("function", ["SendInput", "keybd_event"])
def test_pressing_real_keys_through_user32_is_refused(function):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")
    # Refused as soon as the function is looked up, so no key is ever pressed.
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.WinDLL("user32"), function)
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.windll.user32, function)


def test_other_user32_functions_still_work():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")
    sm_cxscreen = 0
    assert ctypes.WinDLL("user32").GetSystemMetrics(sm_cxscreen) > 0
    assert ctypes.windll.user32.GetSystemMetrics(sm_cxscreen) > 0
