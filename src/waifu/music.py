"""Songs on YouTube: the only pages Cyrene ever opens in the default browser.

`youtube_link` turns the URL of a YouTube video or playlist (from her web search, or typed in
the terminal) into its canonical link on www.youtube.com or music.youtube.com and refuses
everything else: another host or a lookalike, http, a user name or port, a backslash, another
page, a malformed id. `search_link` builds the search page for some words. `open_link` opens
only a link one of them made, through an injected opener (Windows' own "open",
`os.startfile`), so tests never open anything.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Literal
from urllib.parse import parse_qs, unquote_plus, urlencode, urlsplit

from waifu.notify import is_https_link

MusicService = Literal["youtube", "youtube_music"]
SERVICES: tuple[MusicService, ...] = ("youtube", "youtube_music")
MAX_QUERY_CHARS = 120
# The hosts a video or playlist URL may name; every link opens on its service's own host.
YOUTUBE_HOSTS = frozenset({"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"})
SHORT_HOST = "youtu.be"

_SERVICE_HOSTS: dict[MusicService, str] = {
    "youtube": "www.youtube.com",
    "youtube_music": "music.youtube.com",
}
# Each service's search page and the parameter that carries the words.
_SEARCH_PAGES: dict[MusicService, tuple[str, str]] = {
    "youtube": ("https://www.youtube.com/results", "search_query"),
    "youtube_music": ("https://music.youtube.com/search", "q"),
}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{10,64}")


def open_in_browser(link: str) -> None:
    """Open `link` in the default browser with Windows' own "open", which hands it to the
    browser as it is, never through a shell. Raises OSError when Windows cannot open it.

    `os.startfile` is looked up on every call, never bound at import, so the test suite's
    guard on it (tests/conftest.py) also covers this default.
    """
    os.startfile(link)


def youtube_link(url: str, *, service: MusicService) -> str | None:
    """The canonical link of a YouTube video or playlist URL on `service`'s host, keeping only
    the video and playlist ids (a timestamp `t` or a share code `si` is dropped); None for
    anything else."""
    ids = _video_and_playlist(url)
    if ids is None:
        return None
    video, playlist = ids
    base = f"https://{_SERVICE_HOSTS[service]}"
    if video is None:
        return f"{base}/playlist?list={playlist}"
    if playlist is None:
        return f"{base}/watch?v={video}"
    return f"{base}/watch?v={video}&list={playlist}"


def search_link(query: str, *, service: MusicService) -> str:
    """`service`'s search page for `query`, its whitespace collapsed and cut to
    MAX_QUERY_CHARS."""
    words = " ".join(query.split())[:MAX_QUERY_CHARS].rstrip()
    page, parameter = _SEARCH_PAGES[service]
    return f"{page}?{urlencode({parameter: words})}"


def open_link(link: str, *, startfile: Callable[[str], None] = open_in_browser) -> None:
    """Open `link`, which `youtube_link` or `search_link` made, in the default browser.

    Raises ValueError for any other link (it is never opened), and OSError when Windows cannot
    open it.
    """
    if not _made_here(link):
        raise ValueError("only a link that youtube_link or search_link made is opened")
    startfile(link)


def _video_and_playlist(url: str) -> tuple[str | None, str | None] | None:
    """The well-formed video and playlist ids of a YouTube video or playlist URL, at least one
    of them; None for anything else."""
    if not is_https_link(url):  # no user name, port, backslash, or space hides the host
        return None
    parts = urlsplit(url)
    host = parts.netloc.lower()
    params = parse_qs(parts.query, keep_blank_values=True)
    videos, playlists = params.get("v", []), params.get("list", [])
    if len(videos) > 1 or len(playlists) > 1:
        return None  # which of two ids plays would be the browser's guess
    playlist = playlists[0] if playlists else None
    video: str | None
    if host == SHORT_HOST:
        video = parts.path.removeprefix("/")
    elif host in YOUTUBE_HOSTS and parts.path == "/watch":
        video = videos[0] if videos else ""
    elif host in YOUTUBE_HOSTS and parts.path == "/playlist" and playlist is not None:
        video = None
    else:
        return None
    if video is not None and not _VIDEO_ID.fullmatch(video):
        return None
    if playlist is not None and not _PLAYLIST_ID.fullmatch(playlist):
        return None
    return video, playlist


def _made_here(link: str) -> bool:
    """Whether `link` is exactly what `youtube_link` or `search_link` gives: such a link reads
    back as itself, and no other does."""
    for service in SERVICES:
        if youtube_link(link, service=service) == link:
            return True
        page, parameter = _SEARCH_PAGES[service]
        prefix = f"{page}?{parameter}="
        if link.startswith(prefix):
            words = unquote_plus(link.removeprefix(prefix))
            if search_link(words, service=service) == link:
                return True
    return False
