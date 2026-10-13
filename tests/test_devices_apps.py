import errno
import os

import pytest

from waifu.devices.apps import AppLauncher, _shell_open
from waifu.devices.base import DeviceError

GENSHIN = "C:\\Games\\HoYoPlay\\launcher.exe"
SPOTIFY = "C:\\Pintasan\\Spotify.lnk"
ZZZ = "steam://rungameid/2507950"
FORTNITE = "com.epicgames.launcher://apps/fn?action=launch"
APPS = {"genshin": GENSHIN, "spotify": SPOTIFY, "zzz": ZZZ, "fortnite": FORTNITE}
ERROR_CANCELLED = 1223  # winerror.h: Master answered No to Windows' administrator prompt


class FakeStart:
    """Stands in for os.startfile: records each target and folder instead of opening it."""

    def __init__(self, error: OSError | None = None) -> None:
        self.error = error
        self.targets: list[str] = []
        self.folders: list[str | None] = []

    def __call__(self, target: str, folder: str | None) -> None:
        self.targets.append(target)
        self.folders.append(folder)
        if self.error is not None:
            raise self.error


class FakeExists:
    """Stands in for os.path.exists: only the given paths exist."""

    def __init__(self, *paths: str) -> None:
        self.paths = set(paths)
        self.checked: list[str] = []

    def __call__(self, path: str) -> bool:
        self.checked.append(path)
        return path in self.paths


def make_launcher(
    apps: dict[str, str] = APPS,
    *,
    start: FakeStart | None = None,
    exists: FakeExists | None = None,
) -> AppLauncher:
    return AppLauncher(
        "aplikasi",
        "Aplikasi PC",
        ["aplikasi"],
        apps,
        start=start if start is not None else FakeStart(),
        exists=exists if exists is not None else FakeExists(GENSHIN, SPOTIFY),
    )


def test_the_launcher_offers_one_open_command_per_app_in_config_order():
    launcher = make_launcher()

    assert launcher.commands() == ["open_genshin", "open_spotify", "open_zzz", "open_fortnite"]
    assert launcher.describe()["kind"] == "apps"


def test_opening_an_app_starts_its_target_and_remembers_it():
    start = FakeStart()
    launcher = make_launcher(start=start)

    result = launcher.execute("open_genshin")

    assert start.targets == [GENSHIN]
    assert result.ok
    assert result.message == "genshin dibuka."
    assert result.state == {"last_opened": "genshin"}
    assert launcher.state == {"last_opened": "genshin"}


@pytest.mark.parametrize("url", [ZZZ, FORTNITE, "Steam://rungameid/2507950"])
def test_a_launcher_url_is_started_without_checking_for_a_file(url):
    start, exists = FakeStart(), FakeExists()
    launcher = make_launcher({"game": url}, start=start, exists=exists)

    assert launcher.execute("open_game").ok
    assert start.targets == [url]
    assert exists.checked == []


def test_a_path_written_with_forward_slashes_is_opened_with_backslashes():
    start, exists = FakeStart(), FakeExists(GENSHIN)
    launcher = make_launcher(
        {"genshin": "C:/Games/HoYoPlay/launcher.exe"}, start=start, exists=exists
    )

    launcher.execute("open_genshin")

    assert exists.checked == [GENSHIN]
    assert start.targets == [GENSHIN]


def test_a_program_starts_in_its_own_folder_as_from_explorer():
    # Without a folder Windows would use Cyrene's own working folder (the project), so a game
    # that reads or writes files next to itself would look for them, or leave them, there.
    start, exists = FakeStart(), FakeExists("D:\\Games\\Foo\\Foo.EXE")
    launcher = make_launcher({"foo": "D:/Games/Foo/Foo.EXE"}, start=start, exists=exists)

    launcher.execute("open_foo")

    assert start.targets == ["D:\\Games\\Foo\\Foo.EXE"]
    assert start.folders == ["D:\\Games\\Foo"]


@pytest.mark.parametrize("target", [SPOTIFY, "C:\\Users\\Lynx\\Desktop\\Hades.url", ZZZ, FORTNITE])
def test_shortcuts_and_launcher_urls_start_without_a_folder(target):
    # A shortcut brings its own "Start in" folder; a URL has none.
    start = FakeStart()
    launcher = make_launcher({"game": target}, start=start, exists=FakeExists(target))

    launcher.execute("open_game")

    assert start.folders == [None]


def test_the_default_starter_hands_the_folder_to_os_startfile(monkeypatch):
    calls = []
    # A recording stand-in for this test only: the real os.startfile is never reached.
    monkeypatch.setattr(
        os, "startfile", lambda *args, **kwargs: calls.append((args, kwargs)), raising=False
    )

    _shell_open("D:\\Games\\Foo\\Foo.exe", "D:\\Games\\Foo")
    _shell_open(SPOTIFY, None)

    assert calls == [
        (("D:\\Games\\Foo\\Foo.exe",), {"cwd": "D:\\Games\\Foo"}),
        ((SPOTIFY,), {"cwd": None}),
    ]


def test_a_missing_program_or_shortcut_is_refused_naming_the_app():
    start = FakeStart()
    launcher = make_launcher(start=start, exists=FakeExists(GENSHIN))

    with pytest.raises(DeviceError, match="spotify") as error:
        launcher.execute("open_spotify")

    assert "tidak ditemukan" in str(error.value)
    assert start.targets == []
    assert launcher.state == {}


def test_a_failed_start_becomes_a_device_error_naming_the_app():
    start = FakeStart(error=OSError(1155, "No application is associated"))
    launcher = make_launcher(start=start)

    with pytest.raises(DeviceError, match="zzz") as error:
        launcher.execute("open_zzz")

    assert isinstance(error.value.__cause__, OSError)
    assert launcher.state == {}


def test_a_refused_administrator_prompt_is_told_as_such_not_as_a_missing_install():
    # How os.startfile reports No on Windows' "allow this app to make changes" prompt.
    refused = OSError(errno.EINVAL, "The operation was canceled by the user")
    refused.winerror = ERROR_CANCELLED  # set by CPython on Windows; set here for any OS
    launcher = make_launcher(start=FakeStart(error=refused))

    with pytest.raises(DeviceError) as error:
        launcher.execute("open_genshin")

    assert str(error.value) == (
        "Izin administrator untuk genshin ditolak di jendela izin Windows, jadi genshin "
        "tidak dibuka."
    )
    assert launcher.state == {}


@pytest.mark.parametrize("command", ["open_notepad", "genshin", "close_genshin", "open_"])
def test_an_unknown_command_is_refused_and_starts_nothing(command):
    start = FakeStart()
    launcher = make_launcher(start=start)

    with pytest.raises(DeviceError, match="open_genshin, open_spotify, open_zzz, open_fortnite"):
        launcher.execute(command)

    assert start.targets == []


def test_an_app_named_with_a_trailing_number_also_opens_as_command_plus_value():
    # The model sees open_gta_5 as "open_gta (value 5)" (brain/tools.describe_commands) and
    # sends command open_gta with value 5, which the registry and the dry-run mock accept.
    start = FakeStart()
    launcher = make_launcher({"gta_5": GENSHIN}, start=start, exists=FakeExists(GENSHIN))

    result = launcher.execute("open_gta", 5)

    assert result.ok
    assert start.targets == [GENSHIN]
    assert result.state == {"last_opened": "gta_5"}


def test_a_value_is_ignored_because_an_app_has_no_setting():
    start = FakeStart()
    launcher = make_launcher(start=start)

    assert launcher.execute("open_spotify", 30).ok
    assert start.targets == [SPOTIFY]


@pytest.mark.skipif(not hasattr(os, "startfile"), reason="os.startfile only exists on Windows")
def test_the_default_starter_looks_up_os_startfile_when_opening(tmp_path, refused_system_calls):
    # tests/conftest.py replaces os.startfile after this module is imported: only a starter that
    # looks it up when opening reaches the guard. The target does not exist, so a starter bound
    # to the real function at import would fail on the missing file instead of opening anything.
    launcher = AppLauncher(
        "aplikasi",
        "Aplikasi PC",
        [],
        {"game": str(tmp_path / "missing.exe")},
        exists=lambda _: True,
    )

    with pytest.raises(AssertionError, match="startfile"):
        launcher.execute("open_game")
    assert refused_system_calls == ["os.startfile"]
    refused_system_calls.clear()  # this one was on purpose
