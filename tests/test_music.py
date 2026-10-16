"""YouTube links: only YouTube videos, playlists, and the search page this code builds ever open.

No test opens anything: `open_link` gets a fake opener that records each link instead
(tests/conftest.py also refuses the real os.startfile).
"""

import errno
import inspect
import os

import pytest

from waifu.music import (
    MAX_QUERY_CHARS,
    open_in_browser,
    open_link,
    search_link,
    youtube_link,
)

VIDEO = "dQw4w9WgXcQ"
PLAYLIST = "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
ALBUM = "OLAK5uy_kVs7qtgmUEbE9vD2kfDmHi6cLcMbE2nPk"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"
MUSIC_WATCH = f"https://music.youtube.com/watch?v={VIDEO}"


class FakeOpener:
    """Stands in for os.startfile: records each link instead of opening it."""

    def __init__(self, error: Exception | None = None) -> None:
        self.links: list[str] = []
        self.error = error

    def __call__(self, link: str) -> None:
        self.links.append(link)
        if self.error is not None:
            raise self.error


# --- youtube_link: what is accepted, as its canonical link ------------------------------------

VIDEO_URLS = {
    "www": f"https://www.youtube.com/watch?v={VIDEO}",
    "bare host": f"https://youtube.com/watch?v={VIDEO}",
    "mobile": f"https://m.youtube.com/watch?v={VIDEO}",
    "youtube music": f"https://music.youtube.com/watch?v={VIDEO}",
    "short link": f"https://youtu.be/{VIDEO}",
    "share link with si": f"https://youtu.be/{VIDEO}?si=Ab12-cd_EF34gh",
    "share link with si and t": f"https://youtu.be/{VIDEO}?si=Ab12-cd_EF34gh&t=42",
    "timestamp": f"https://www.youtube.com/watch?v={VIDEO}&t=1m30s",
    "other parameters around v": f"https://www.youtube.com/watch?si=xyz&v={VIDEO}&pp=ygUEbG9maQ",
    "feature and start_radio": f"https://m.youtube.com/watch?v={VIDEO}&feature=share&start_radio=1",
    "fragment": f"https://www.youtube.com/watch?v={VIDEO}#t=30",
    "upper-case scheme and host": f"HTTPS://WWW.YouTube.COM/watch?v={VIDEO}",
}


@pytest.mark.parametrize("url", VIDEO_URLS.values(), ids=VIDEO_URLS.keys())
def test_a_video_url_becomes_its_canonical_watch_link(url):
    assert youtube_link(url, service="youtube") == WATCH


@pytest.mark.parametrize("url", VIDEO_URLS.values(), ids=VIDEO_URLS.keys())
def test_with_youtube_music_a_video_opens_on_youtube_music(url):
    assert youtube_link(url, service="youtube_music") == MUSIC_WATCH


PLAYLIST_URLS = {
    "www": f"https://www.youtube.com/playlist?list={PLAYLIST}",
    "bare host": f"https://youtube.com/playlist?list={PLAYLIST}",
    "mobile": f"https://m.youtube.com/playlist?list={PLAYLIST}",
    "youtube music": f"https://music.youtube.com/playlist?list={PLAYLIST}",
    "si dropped": f"https://www.youtube.com/playlist?list={PLAYLIST}&si=Ab12-cd_EF34gh",
}


@pytest.mark.parametrize("url", PLAYLIST_URLS.values(), ids=PLAYLIST_URLS.keys())
def test_a_playlist_url_becomes_its_canonical_playlist_link(url):
    assert youtube_link(url, service="youtube") == (
        f"https://www.youtube.com/playlist?list={PLAYLIST}"
    )
    assert youtube_link(url, service="youtube_music") == (
        f"https://music.youtube.com/playlist?list={PLAYLIST}"
    )


def test_an_album_on_youtube_music_is_a_playlist_too():
    url = f"https://music.youtube.com/playlist?list={ALBUM}&feature=share"

    assert youtube_link(url, service="youtube_music") == (
        f"https://music.youtube.com/playlist?list={ALBUM}"
    )


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}&index=3",
        f"https://youtu.be/{VIDEO}?list={PLAYLIST}&si=Ab12-cd_EF34gh",
        f"https://m.youtube.com/watch?list={PLAYLIST}&v={VIDEO}&pp=sAQB",
    ],
)
def test_a_video_in_a_playlist_keeps_both_ids_and_nothing_else(url):
    assert youtube_link(url, service="youtube") == (
        f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}"
    )


@pytest.mark.parametrize(
    "video",
    ["a-b_c-d_e-f", "___________", "-----------", "ABCDEFGHIJK", "0123456789z"],
)
def test_every_well_formed_video_id_is_accepted(video):
    assert youtube_link(f"https://youtu.be/{video}", service="youtube") == (
        f"https://www.youtube.com/watch?v={video}"
    )


@pytest.mark.parametrize("playlist", ["a" * 10, "Z" * 64, "RDdQw4w9WgXcQ", "PL_-09azAZ"])
def test_every_well_formed_playlist_id_is_accepted(playlist):
    url = f"https://www.youtube.com/playlist?list={playlist}"

    assert youtube_link(url, service="youtube") == url


# --- youtube_link: everything else is refused -------------------------------------------------

REFUSED_URLS = {
    # Not https.
    "http": f"http://www.youtube.com/watch?v={VIDEO}",
    "http short link": f"http://youtu.be/{VIDEO}",
    "no scheme": f"www.youtube.com/watch?v={VIDEO}",
    "scheme-relative": f"//www.youtube.com/watch?v={VIDEO}",
    "ftp": f"ftp://www.youtube.com/watch?v={VIDEO}",
    "javascript": "javascript:alert(1)",
    "file": "file:///C:/Windows/System32/calc.exe",
    "launcher": "steam://rungameid/2507950",
    "empty": "",
    # Another host, lookalikes included.
    "another site": f"https://evil.example/watch?v={VIDEO}",
    "youtube.com as a subdomain": f"https://youtube.com.evil.example/watch?v={VIDEO}",
    "www.youtube.com as a subdomain": f"https://www.youtube.com.evil.example/watch?v={VIDEO}",
    "hyphenated lookalike": f"https://evil-youtube.com/watch?v={VIDEO}",
    "prefixed lookalike": f"https://notyoutube.com/watch?v={VIDEO}",
    "other top-level domain": f"https://youtube.co/watch?v={VIDEO}",
    "short link lookalike": f"https://youtu.be.evil.example/{VIDEO}",
    "www short link": f"https://www.youtu.be/{VIDEO}",
    "other youtube subdomain": f"https://gaming.youtube.com/watch?v={VIDEO}",
    "trailing dot": f"https://youtube.com./watch?v={VIDEO}",
    "cyrillic o": f"https://www.y\u043eutube.com/watch?v={VIDEO}",
    "punycode": f"https://xn--ytube-mua.com/watch?v={VIDEO}",
    "ip address": f"https://[::1]/watch?v={VIDEO}",
    # A user name, a port, or a backslash that hides the real host.
    "youtube as the user name": f"https://www.youtube.com@evil.example/watch?v={VIDEO}",
    "user name": f"https://user@www.youtube.com/watch?v={VIDEO}",
    "user and password": f"https://user:pass@www.youtube.com/watch?v={VIDEO}",
    "default port": f"https://www.youtube.com:443/watch?v={VIDEO}",
    "other port": f"https://www.youtube.com:8080/watch?v={VIDEO}",
    "backslash in the host": f"https://evil.example\\.youtube.com/watch?v={VIDEO}",
    "backslash before an at": f"https://www.youtube.com\\@evil.example/watch?v={VIDEO}",
    "backslash in the path": f"https://www.youtube.com/watch\\?v={VIDEO}",
    "space": f"https://www.youtube.com /watch?v={VIDEO}",
    "newline": f"https://www.youtube.com/watch?v={VIDEO}\n",
    # Other pages.
    "home page": "https://www.youtube.com/",
    "redirect": "https://www.youtube.com/redirect?q=https://evil.example/",
    "attribution link": f"https://www.youtube.com/attribution_link?u=/watch%3Fv%3D{VIDEO}",
    "shorts": f"https://www.youtube.com/shorts/{VIDEO}",
    "embed": f"https://www.youtube.com/embed/{VIDEO}",
    "channel": "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
    "search page": "https://www.youtube.com/results?search_query=lofi",
    "watch with a trailing slash": f"https://www.youtube.com/watch/?v={VIDEO}",
    "short link with more path": f"https://youtu.be/{VIDEO}/extra",
    "short link without an id": "https://youtu.be/",
    "short link to a watch page": f"https://youtu.be/watch?v={VIDEO}",
    # Missing or malformed ids.
    "watch without v": "https://www.youtube.com/watch",
    "watch with a playlist but no v": f"https://www.youtube.com/watch?list={PLAYLIST}",
    "empty v": "https://www.youtube.com/watch?v=",
    "ten-character video": "https://www.youtube.com/watch?v=dQw4w9WgXc",
    "twelve-character video": "https://www.youtube.com/watch?v=dQw4w9WgXcQQ",
    "video with a bad character": "https://www.youtube.com/watch?v=dQw4w9WgX!Q",
    "video with an encoded newline": f"https://www.youtube.com/watch?v={VIDEO}%0A",
    "video smuggling a parameter": f"https://www.youtube.com/watch?v={VIDEO}%26list%3D{PLAYLIST}",
    "two videos": f"https://www.youtube.com/watch?v={VIDEO}&v=aaaaaaaaaaa",
    "a video and an empty one": f"https://www.youtube.com/watch?v={VIDEO}&v=",
    "short link with a bad id": "https://youtu.be/dQw4w9WgX!Q",
    "playlist without list": "https://www.youtube.com/playlist",
    "empty playlist": "https://www.youtube.com/playlist?list=",
    "nine-character playlist": "https://www.youtube.com/playlist?list=aaaaaaaaa",
    "65-character playlist": f"https://www.youtube.com/playlist?list={'a' * 65}",
    "playlist with markup": "https://www.youtube.com/playlist?list=PL%3Cscript%3Ealert",
    "two playlists": f"https://www.youtube.com/playlist?list={PLAYLIST}&list={ALBUM}",
    "video with a bad playlist": f"https://www.youtube.com/watch?v={VIDEO}&list=bad!",
    "short link with a bad playlist": f"https://youtu.be/{VIDEO}?list=short",
}


@pytest.mark.parametrize("url", REFUSED_URLS.values(), ids=REFUSED_URLS.keys())
def test_anything_but_a_youtube_video_or_playlist_is_refused(url):
    assert youtube_link(url, service="youtube") is None
    assert youtube_link(url, service="youtube_music") is None


# --- search_link ---------------------------------------------------------------------------


def test_the_search_page_of_each_service_holds_the_words_urlencoded():
    words = "Hope Is the Thing With Feathers"

    assert search_link(words, service="youtube") == (
        "https://www.youtube.com/results?search_query=Hope+Is+the+Thing+With+Feathers"
    )
    assert search_link(words, service="youtube_music") == (
        "https://music.youtube.com/search?q=Hope+Is+the+Thing+With+Feathers"
    )


def test_whitespace_in_the_words_is_collapsed():
    words = "  lofi \n\t buat\u3000belajar   "

    assert search_link(words, service="youtube") == (
        "https://www.youtube.com/results?search_query=lofi+buat+belajar"
    )


def test_characters_that_mean_something_in_a_link_are_encoded():
    words = "AC/DC & Queen #1 ?=+%\\"

    assert search_link(words, service="youtube") == (
        "https://www.youtube.com/results?search_query=AC%2FDC+%26+Queen+%231+%3F%3D%2B%25%5C"
    )


def test_words_in_any_script_are_encoded_as_utf8():
    assert search_link("キュレネ", service="youtube_music") == (
        "https://music.youtube.com/search?q=%E3%82%AD%E3%83%A5%E3%83%AC%E3%83%8D"
    )


def test_the_words_are_cut_to_120_characters():
    assert MAX_QUERY_CHARS == 120

    link = search_link("a" * 200, service="youtube")

    assert link == f"https://www.youtube.com/results?search_query={'a' * 120}"


def test_words_cut_at_a_space_lose_the_trailing_space():
    link = search_link("a" * 119 + " " + "b" * 10, service="youtube")

    assert link == f"https://www.youtube.com/results?search_query={'a' * 119}"


# --- open_link -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "service"),
    [
        (f"https://youtu.be/{VIDEO}?si=x", "youtube"),
        (f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}", "youtube"),
        (f"https://www.youtube.com/playlist?list={PLAYLIST}", "youtube"),
        (f"https://youtu.be/{VIDEO}", "youtube_music"),
        (f"https://music.youtube.com/playlist?list={ALBUM}", "youtube_music"),
    ],
)
def test_a_link_youtube_link_made_opens_as_it_is(url, service):
    opener = FakeOpener()
    link = youtube_link(url, service=service)

    open_link(link, startfile=opener)

    assert opener.links == [link]


@pytest.mark.parametrize("service", ["youtube", "youtube_music"])
@pytest.mark.parametrize(
    "words",
    ["lofi buat belajar", "AC/DC & Queen #1", "キュレネ", "a" * 119 + " " + "b" * 10, "x" * 300],
)
def test_a_link_search_link_made_opens_as_it_is(words, service):
    opener = FakeOpener()
    link = search_link(words, service=service)

    open_link(link, startfile=opener)

    assert opener.links == [link]


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example/",
        "file:///C:/Windows/System32/calc.exe",
        "steam://rungameid/2507950",
        "C:\\Windows\\System32\\calc.exe",
        f"https://youtu.be/{VIDEO}",
        f"https://youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}&t=42",
        f"https://www.youtube.com/watch?v={VIDEO}#t=42",
        f"https://www.youtube.com.evil.example/watch?v={VIDEO}",
        "https://www.youtube.com/results?search_query=lofi&next=https://evil.example/",
        "https://www.youtube.com/results?search_query=lofi+",
        "https://www.youtube.com/results?search_query=lofi%20belajar",
        "https://www.youtube.com/results?search_query=lofi#x",
        f"https://www.youtube.com/results?search_query={'a' * 121}",
        "https://music.youtube.com/results?search_query=lofi",
        "https://www.youtube.com/search?q=lofi",
        "https://evil.example/results?search_query=lofi",
    ],
)
def test_any_other_link_is_refused_and_never_opened(link):
    opener = FakeOpener()

    with pytest.raises(ValueError):
        open_link(link, startfile=opener)

    assert opener.links == []


def test_windows_failing_to_open_the_link_reaches_the_caller():
    opener = FakeOpener(OSError(errno.ENOENT, "no browser"))

    with pytest.raises(OSError, match="no browser"):
        open_link(WATCH, startfile=opener)

    assert opener.links == [WATCH]


def test_the_default_opener_is_the_one_that_looks_up_os_startfile_when_opening():
    # Bound to os.startfile at import, a default would escape the guard in tests/conftest.py.
    assert inspect.signature(open_link).parameters["startfile"].default is open_in_browser


def test_the_default_opener_hands_the_link_to_os_startfile(tmp_path, monkeypatch):
    calls = []
    # A recording stand-in for this test only. The target is a missing file, never a link: an
    # opener bound to the real os.startfile at import would only fail on it, opening nothing.
    monkeypatch.setattr(
        os, "startfile", lambda *args, **kwargs: calls.append((args, kwargs)), raising=False
    )
    missing = str(tmp_path / "missing.html")

    open_in_browser(missing)

    assert calls == [((missing,), {})]
