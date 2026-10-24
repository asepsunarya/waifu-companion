"""The suite-wide guard in conftest.py must stop real power commands before they run."""

import asyncio
import contextlib
import ctypes
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import aiohttp
import httpx
import pytest

from waifu.gacha.games import GAMES
from waifu.gacha.history import find_cached_gacha_url
from waifu.version import process_alive
from waifu.voice.windows_voices import list_windows_voices


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


# The version reader finds the repository from its own file, so a forgotten fake would ask the
# real repository the builder commits to; "--version" only prints, so a broken guard does no harm.
@pytest.mark.parametrize(
    "args",
    [
        ["git", "--version"],
        [r"C:\Program Files\Git\cmd\git.exe", "--version"],
        "git --version",
    ],
)
def test_running_git_is_refused(args):
    with pytest.raises(AssertionError, match="a test tried to run git"):
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


# The offline fallback voice speaks through PowerShell: a forgotten fake would make the owner's
# Windows voices speak, or list them. "exit 0" does nothing, so a broken guard does no harm.
@pytest.mark.parametrize(
    "program",
    [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "powershell", "pwsh.exe"],
)
def test_running_powershell_is_refused(program, refused_system_calls):
    with pytest.raises(AssertionError, match="system-change command"):
        subprocess.run(
            [program, "-NoProfile", "-Command", "exit 0"], capture_output=True, check=False
        )
    assert refused_system_calls == ["powershell"]  # also failed at teardown
    refused_system_calls.clear()  # this one was on purpose


# `waifu setup tuya` runs tinytuya's wizard, which asks for the owner's Tuya cloud keys, reaches
# the cloud, and scans the LAN. "--help" only prints, so a broken guard does no harm.
@pytest.mark.parametrize(
    "args",
    [[sys.executable, "-m", "tinytuya", "--help"], f'"{sys.executable}" -m tinytuya --help'],
)
def test_running_tinytuya_is_refused(args, refused_system_calls):
    with pytest.raises(AssertionError, match="system-change command"):
        subprocess.run(args, capture_output=True, check=False)
    assert refused_system_calls == ["tinytuya"]  # also failed at teardown
    refused_system_calls.clear()  # this one was on purpose


def test_a_powershell_refusal_the_fallback_voice_s_callers_swallow_is_still_recorded(
    refused_system_calls,
):
    # waifu commands, Assistant.speak and the phone's reply catch every exception, and a
    # forgotten fake reaches PowerShell through the real listing ("-List" only reads).
    with contextlib.suppress(Exception):
        list_windows_voices()
    assert refused_system_calls == ["powershell"]  # so teardown fails the test
    refused_system_calls.clear()  # this one was on purpose


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


@pytest.mark.parametrize(
    "address",
    [("store-site-backend-static-ipv4.ak.epicgames.com", 443), ("203.0.113.7", 443)],
)
def test_socket_connections_beyond_this_pc_are_refused(address):
    # Every client (httpx, the anthropic SDK's httpx2, anything else) ends up here.
    with pytest.raises(AssertionError, match="internet"):
        socket.create_connection(address, timeout=1)


def test_httpx_requests_beyond_this_pc_are_refused():
    with pytest.raises(AssertionError, match="internet"):
        httpx.get("https://www.gamerpower.com/api/giveaways", timeout=1)


def test_socket_connections_to_loopback_still_go_out():
    # Port 9 (discard) is closed: refused, or timed out (Windows retries a closed loopback port
    # for about 2 s), proves the guard let it through, which would raise AssertionError.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 9), timeout=0.5)
    with pytest.raises(OSError):
        socket.create_connection(("localhost", 9), timeout=0.5)


def test_reading_the_owners_game_caches_is_refused(real_game_cache_reads):
    # The logs lead to the history caches, which hold the owner's authkeys.
    with pytest.raises(AssertionError, match="real game logs"):
        find_cached_gacha_url(GAMES["hsr"], Path.home() / "AppData" / "LocalLow")

    assert real_game_cache_reads == ["hsr"]  # also failed at teardown, even if swallowed
    real_game_cache_reads.clear()  # this one was on purpose


def test_game_caches_elsewhere_can_still_be_read(tmp_path):
    assert find_cached_gacha_url(GAMES["hsr"], tmp_path) is None


def test_opening_a_program_or_url_with_startfile_is_refused(tmp_path, refused_system_calls):
    if not hasattr(os, "startfile"):
        pytest.skip("os.startfile only exists on Windows")
    # A missing file, so even a broken guard would only raise FileNotFoundError.
    with pytest.raises(AssertionError, match="startfile"):
        os.startfile(str(tmp_path / "missing.exe"))
    assert refused_system_calls == ["os.startfile"]  # also failed at teardown
    refused_system_calls.clear()  # this one was on purpose


@pytest.mark.parametrize("function", ["SendInput", "keybd_event"])
def test_pressing_real_keys_through_user32_is_refused(function, refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")
    # Refused as soon as the function is looked up, so no key is ever pressed.
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.WinDLL("user32"), function)
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.windll.user32, function)
    assert refused_system_calls == [f"user32.{function}"] * 2
    refused_system_calls.clear()  # these were on purpose


@pytest.mark.parametrize(
    ("library", "function"),
    [("gdi32", "BitBlt"), ("gdi32", "StretchBlt"), ("user32", "PrintWindow")],
)
def test_capturing_the_real_screen_is_refused(library, function, refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("gdi32 and user32 only exist on Windows")
    # Refused as soon as the function is looked up, so nothing is ever captured.
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.WinDLL(library), function)
    with pytest.raises(AssertionError, match=function):
        getattr(getattr(ctypes.windll, library), function)
    assert refused_system_calls == [f"{library}.{function}"] * 2
    refused_system_calls.clear()  # these were on purpose


def test_a_refusal_the_code_swallows_is_still_recorded(refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("gdi32 only exists on Windows")
    with contextlib.suppress(Exception):  # like a catch-all in the code under test
        _ = ctypes.WinDLL("gdi32").StretchBlt
    assert refused_system_calls == ["gdi32.StretchBlt"]  # so teardown fails the test
    refused_system_calls.clear()  # this one was on purpose


def test_other_gdi32_functions_still_load():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("gdi32 only exists on Windows")
    assert ctypes.WinDLL("gdi32").GetStockObject is not None


def test_other_user32_functions_still_work():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")
    sm_cxscreen = 0
    assert ctypes.WinDLL("user32").GetSystemMetrics(sm_cxscreen) > 0
    assert ctypes.windll.user32.GetSystemMetrics(sm_cxscreen) > 0


def test_reading_the_owner_s_real_input_through_user32_is_refused(refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")
    # Refused as soon as the function is looked up: the break reminder tests would otherwise
    # pass or fail by whether the owner is at the keyboard.
    with pytest.raises(AssertionError, match="GetLastInputInfo"):
        _ = ctypes.WinDLL("user32", use_last_error=True).GetLastInputInfo
    with pytest.raises(AssertionError, match="GetLastInputInfo"):
        _ = ctypes.windll.user32.GetLastInputInfo
    assert refused_system_calls == ["user32.GetLastInputInfo"] * 2
    refused_system_calls.clear()  # these were on purpose


@pytest.mark.parametrize(
    "function",
    [
        "RegisterHotKey",
        "UnregisterHotKey",
        "GetMessageW",
        "PeekMessageW",
        "PostThreadMessageW",
    ],
)
def test_global_hotkeys_and_message_queues_through_user32_are_refused(
    function, refused_system_calls
):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")
    # Refused as soon as the function is looked up: a forgotten fake would take Ctrl+Alt combos
    # from the owner's other programs, or wait forever on a real message queue.
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.WinDLL("user32", use_last_error=True), function)
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.windll.user32, function)
    assert refused_system_calls == [f"user32.{function}"] * 2
    refused_system_calls.clear()  # these were on purpose


def test_listing_the_real_processes_through_kernel32_is_refused(refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("kernel32 only exists on Windows")
    # Refused as soon as the function is looked up, so no process is ever listed: the play
    # time tests would otherwise pass or fail by whether the owner is playing.
    with pytest.raises(AssertionError, match="CreateToolhelp32Snapshot"):
        _ = ctypes.WinDLL("kernel32", use_last_error=True).CreateToolhelp32Snapshot
    with pytest.raises(AssertionError, match="CreateToolhelp32Snapshot"):
        _ = ctypes.windll.kernel32.CreateToolhelp32Snapshot
    assert refused_system_calls == ["kernel32.CreateToolhelp32Snapshot"] * 2
    refused_system_calls.clear()  # these were on purpose


def test_other_kernel32_functions_still_work():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("kernel32 only exists on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    assert kernel32.GetTickCount64() > 0
    assert ctypes.windll.kernel32.GetCurrentProcessId() == os.getpid()
    assert process_alive(os.getpid())  # OpenProcess on this process, as `waifu check` does


@pytest.mark.parametrize("function", ["waveInGetNumDevs", "waveInGetDevCapsW"])
def test_listing_the_owner_s_real_microphones_through_winmm_is_refused(
    function, refused_system_calls
):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("winmm only exists on Windows")
    # Refused as soon as the function is looked up: the mic tests would otherwise pass or fail
    # by which microphones the owner has plugged in.
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.WinDLL("winmm"), function)
    with pytest.raises(AssertionError, match=function):
        getattr(ctypes.windll.winmm, function)
    assert refused_system_calls == [f"winmm.{function}"] * 2
    refused_system_calls.clear()  # these were on purpose


@pytest.fixture(scope="module")
def real_sounddevice():
    """The installed sounddevice, imported before each test's guards are set up (it loads
    PortAudio, which lists the devices but opens none)."""
    try:
        return pytest.importorskip("sounddevice")
    except OSError:  # no PortAudio library on this machine
        pytest.skip("PortAudio is not installed")


# A device no PC has: even a broken guard stops at sounddevice's name lookup, before PortAudio.
NO_SUCH_DEVICE = "a microphone no PC has"


@pytest.mark.parametrize(
    ("call", "function", "purpose"),
    [
        (
            lambda sd: sd.InputStream(device=NO_SUCH_DEVICE),
            "InputStream",
            "open a real audio stream",
        ),
        (
            lambda sd: sd.RawInputStream(device=NO_SUCH_DEVICE),
            "RawInputStream",
            "open a real audio stream",
        ),
        (
            lambda sd: sd.OutputStream(device=NO_SUCH_DEVICE),
            "OutputStream",
            "open a real audio stream",
        ),
        (lambda sd: sd.Stream(device=NO_SUCH_DEVICE), "Stream", "open a real audio stream"),
        (lambda sd: sd.play([0.0], 16000, device=NO_SUCH_DEVICE), "play", "play real audio"),
        (lambda sd: sd.rec(1, 16000, 1, device=NO_SUCH_DEVICE), "rec", "record real audio"),
        (
            lambda sd: sd.playrec([[0.0]], 16000, 1, device=NO_SUCH_DEVICE),
            "playrec",
            "play real audio",
        ),
    ],
    ids=["InputStream", "RawInputStream", "OutputStream", "Stream", "play", "rec", "playrec"],
)
def test_real_audio_streams_are_refused(
    call, function, purpose, real_sounddevice, refused_system_calls
):
    # Refused before PortAudio is asked: a forgotten fake would open the owner's mic or play
    # through his speakers.
    with pytest.raises(AssertionError, match=purpose):
        call(real_sounddevice)
    assert refused_system_calls == [f"sounddevice.{function}"]  # also failed at teardown
    refused_system_calls.clear()  # this one was on purpose


def test_restarting_the_real_portaudio_is_refused(real_sounddevice, refused_system_calls):
    # A broken guard would only count one more start of PortAudio here...
    with pytest.raises(AssertionError, match="restart the real PortAudio"):
        real_sounddevice._initialize()
    try:
        with pytest.raises(AssertionError, match="restart the real PortAudio"):
            real_sounddevice._terminate()
    except pytest.fail.Exception:
        real_sounddevice._initialize()  # ...and here it stopped PortAudio: start it again
        raise
    assert refused_system_calls == ["sounddevice._initialize", "sounddevice._terminate"]
    refused_system_calls.clear()  # these were on purpose


@pytest.fixture
def stub_sounddevice(tmp_path, monkeypatch):
    """`import sounddevice` in the test loads this stub as if for the first time in the run (no
    PortAudio is loaded); whatever sys.modules held before is put back afterwards."""
    (tmp_path / "sounddevice.py").write_text(
        "def InputStream(*args, **kwargs):\n"
        "    return 'a real stream'\n"
        "\n\n"
        "def play(*args, **kwargs):\n"
        "    return 'played'\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # recorded, so it is put back...
    del sys.modules["sounddevice"]  # ...and gone for the test
    monkeypatch.syspath_prepend(str(tmp_path))


def test_sounddevice_imported_for_the_first_time_in_a_test_is_refused_at_once(
    stub_sounddevice, refused_system_calls
):
    # A forgotten fake in a test run on its own is the first to import sounddevice (the code
    # imports it lazily): its real streams must be refused from that import on.
    import sounddevice

    with pytest.raises(AssertionError, match="open a real audio stream"):
        sounddevice.InputStream()
    with pytest.raises(AssertionError, match="play real audio"):
        sounddevice.play([0.0], 16000)
    assert refused_system_calls == ["sounddevice.InputStream", "sounddevice.play"]
    refused_system_calls.clear()  # these were on purpose


def test_the_real_sounddevice_still_matches_device_names(real_sounddevice, monkeypatch):
    listing = [{"name": "Headset (Polaris-BT)", "hostapi": 0, "max_input_channels": 1}]
    monkeypatch.setattr(real_sounddevice, "query_devices", lambda *args: listing)
    monkeypatch.setattr(real_sounddevice, "query_hostapis", lambda index=None: {"name": "MME"})

    found = real_sounddevice._get_device_id("Headset (Polaris-BT), MME", "input")

    assert found == 0
