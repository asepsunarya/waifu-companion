"""The tool that plays a song for Master: its YouTube page, opened in the default browser.

The brain hands every turn's words, as heard, to `begin_turn`; a page opens only when those
words pass the word gate in `_asks_for_a_song` (her names left out), at most once per turn,
whatever the model wants. The gate matches words, not meaning: words holding a look-up or
question word ("cari", "siapa") or a next or previous word never pass, so in such a turn a web
page she read cannot get anything opened. Words that pass (a play verb such as "putar", or a
music word such as "lagu" without a player word) may still be a remark that names a song;
there, as in a real request, a web page she read could sway which one YouTube page opens.
With `links` (Claude with her web search) she finds the song's YouTube link and passes it as
`url`, which opens only when `music.youtube_link` accepts it; otherwise, and with any brain
that cannot search, YouTube's search page for `query` opens and Master picks, so a brain that
cannot search never opens a link it made up. Opening a browser takes the focus from a
fullscreen game, so she only does it when asked. The result never holds the link, which is
never read aloud. The spec carries no state, so it stays byte-identical for the brain's
lifetime and keeps the prompt cache warm.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from waifu.brain.base import ToolSpec
from waifu.music import MusicService, open_in_browser, open_link, search_link, youtube_link

logger = logging.getLogger(__name__)

PLAY_YOUTUBE = "play_youtube"
# One of these in Master's words asks for a song in that turn, unless the words also hold a
# look-up or track word.
PLAY_VERBS = frozenset(
    {"putar", "puterin", "putarkan", "mainkan", "mainin", "setelin", "play", "dengerin"}
)
# One of these asks for a song too, unless the words also hold a look-up, track, or player
# word.
MUSIC_WORDS = frozenset({"lagu", "musik", "music", "playlist", "youtube"})
# Searching: with a play verb and a music word it is "find it and play it" ("cari lagu lofi
# terus putar"); otherwise a turn that sends her to the web, where "play" is often a game.
SEARCH_WORDS = frozenset({"cari", "carikan", "cariin", "nyari", "nyariin", "mencari", "search"})
# Asking about something: a turn that sends her to the web, where "play" is often a game and
# "dengerin" a person. These and the words below also count with "-nya".
LOOK_UP_WORDS = frozenset(
    {
        "info",
        "berita",
        "lirik",
        "lyrics",
        "harga",
        "judul",
        "penyanyi",
        "siapa",
        "apa",
        "apakah",
        "kapan",
        "kenapa",
        "mengapa",
        "bagaimana",
        "gimana",
        "berapa",
    }
)
# The next or previous song: the media device's keys, also after a play verb.
TRACK_WORDS = frozenset({"next", "berikutnya", "selanjutnya", "previous", "prev", "sebelumnya"})
# The other media keys and the volume, which the media device handles.
PLAYER_WORDS = frozenset(
    {
        "pause",
        "jeda",
        "jedain",
        "stop",
        "berhenti",
        "hentikan",
        "matikan",
        "matiin",
        "off",
        "lanjut",
        "lanjutkan",
        "lanjutin",
        "resume",
        "skip",
        "lewati",
        "lewatin",
        "pindah",
        "volume",
        "kecilkan",
        "kecilin",
        "besarkan",
        "besarin",
        "gedein",
        "pelankan",
        "pelanin",
        "keraskan",
        "kerasin",
        "down",
        "up",
        "mute",
    }
)

# Runs of letters and digits in any script, as in brain.screen_tools.
_WORD = re.compile(r"[^\W_]+")
_KEYS = frozenset({"query", "url"})
# What each canonical link's path opens.
_PAGE_KINDS = {"/watch": "video", "/playlist": "playlist"}
_ASK = (
    "Play a song or music for Master: open its YouTube page in Master's default browser on the "
    "PC, where it plays. Call it only when Master's current message asks you to play a song or "
    "music ('putar lagu ...', 'puterin playlist ...', 'play ...'); otherwise it is refused. At "
    "most one page per message. Put the song or playlist as Master said it (title and artist) "
    "in `query`. "
)
_FIND_A_LINK = (
    "First search the web for the song's YouTube page and pass its youtube.com or youtu.be "
    "video or playlist link as `url`; never make one up. Without such a link, pass only "
    "`query`: YouTube's search page for it opens and Master picks the song; any other link "
    "opens the search page too. "
)
_SEARCH_ONLY = "YouTube's search page for it opens and Master picks the song. "
_SAY_AND_LIMITS = (
    "Say what you opened in one short sentence (the song, or that the search page is open); "
    "never read a link aloud. Never use it to pause, skip, go back, stop, or change the "
    "volume, even when there is no media device: those go through the media device. Opening "
    "a page takes the focus from a game Master is playing, so only do it when Master asks."
)
_NOT_ASKED = (
    "Master tidak meminta lagu atau musik di pesan ini, jadi tidak ada yang dibuka. Buka lagu "
    "hanya kalau pesan Master sendiri memintanya."
)
_ONCE_PER_TURN = "Hanya satu halaman lagu yang dibuka untuk setiap pesan Master."
_ALREADY_OPEN = "Satu halaman lagu sudah dibuka untuk pesan Master ini; tidak ada yang dibuka lagi."
_OPEN_FAILED = "Browser tidak bisa dibuka: Windows gagal membuka halamannya."
_FAILED = "Lagu tidak bisa dibuka saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class MusicTools:
    """A `ToolProvider` for the brain: a song's YouTube page when Master asks for music."""

    def __init__(
        self,
        *,
        service: MusicService,
        names: Iterable[str],
        links: bool,
        startfile: Callable[[str], None] = open_in_browser,
    ) -> None:
        """`names` are her name and wake words, which never count as asking for music. With
        `links` (a brain that searches the web), the model may pass a song's YouTube link;
        otherwise it is never offered one, and any it passes opens the search page instead."""
        self._service = service
        self._names = frozenset(word for name in names for word in _words(name))
        self._links = links
        self._startfile = startfile
        self._asked = False  # whether this turn's words ask for music
        self._tried = False  # whether this turn already tried to open a page
        self._opened = False  # whether that try opened it

    def specs(self) -> list[ToolSpec]:
        properties = {"query": {"type": "string"}}
        how = _SEARCH_ONLY
        if self._links:
            properties["url"] = {"type": "string"}
            how = _FIND_A_LINK
        return [
            ToolSpec(
                name=PLAY_YOUTUBE,
                description=_ASK + how + _SAY_AND_LIMITS,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        ]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with Master's words as heard; only these can ask for music."""
        self._asked = _asks_for_a_song(set(_words(user_text)) - self._names)
        self._tried = False
        self._opened = False

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == PLAY_YOUTUBE:
                return self._play(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Music tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool lagu.")

    def _play(self, arguments: object) -> tuple[str, bool]:
        query, url = _query_and_url(arguments)
        if not self._asked:
            logger.info("Not opening a song: Master did not ask for music in this turn")
            return _error(_NOT_ASKED)
        if self._tried:
            # A repeat after a page opened is no failure: the local model often sends the same
            # call twice, and an error would have her say that something failed.
            if self._opened:
                return _to_json({"ok": True, "opened": "already", "message": _ALREADY_OPEN}), False
            return _error(_ONCE_PER_TURN)
        link, opened = self._page(query, url)
        self._tried = True  # one try per turn: a failing browser is not opened again
        try:
            open_link(link, startfile=self._startfile)
        except OSError as exc:
            logger.warning("Could not open the YouTube page: %s", exc)
            return _error(_OPEN_FAILED)
        self._opened = True
        logger.info("Opened a YouTube %s page on %s", opened, self._service)
        return _to_json({"ok": True, "opened": opened, "service": self._service}), False

    def _page(self, query: str, url: str | None) -> tuple[str, str]:
        """The link to open and what it opens: the video or playlist at `url` when this brain
        may pass links and `music.youtube_link` accepts it, otherwise the search page for
        `query`, so a refused link never leaves an error for the model to retry."""
        if url is not None:
            accepted = youtube_link(url, service=self._service) if self._links else None
            if accepted is not None:
                return accepted, _PAGE_KINDS[urlsplit(accepted).path]
            logger.info("Opening the search page in place of the link the model passed")
        return search_link(query, service=self._service), "search"


def _query_and_url(arguments: object) -> tuple[str, str | None]:
    """The words to search for and the link to open, if any; a null `url` counts as left out,
    as local models send it."""
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _InvalidArguments("'query' wajib diisi dengan judul lagu atau kata pencariannya.")
    url = arguments.get("url")
    if url is not None and not isinstance(url, str):
        raise _InvalidArguments("'url' harus berupa teks link.")
    return query, url


def _asks_for_a_song(words: set[str]) -> bool:
    """Whether Master's `words` ask for a song: never when they ask about something or want
    the next or previous song; when they search, only with a play verb and a music word ("cari
    lagu lofi terus putar"); otherwise when they hold a play verb, or name music without being
    about the player (pausing, skipping, the volume). "apa" with "aja"/"saja" is "anything",
    not a question. Only the closing words also count with "-nya" ("liriknya",
    "volumenya"), so "lagunya bagus" stays a remark."""
    stems = words | {word.removesuffix("nya") for word in words}
    asking = LOOK_UP_WORDS - ({"apa"} if words & {"aja", "saja"} else set())
    if not asking.isdisjoint(stems) or not TRACK_WORDS.isdisjoint(stems):
        return False
    playing = not PLAY_VERBS.isdisjoint(words)
    if not SEARCH_WORDS.isdisjoint(stems):
        return playing and not MUSIC_WORDS.isdisjoint(words)
    if playing:
        return True
    return not MUSIC_WORDS.isdisjoint(words) and PLAYER_WORDS.isdisjoint(stems)


def _words(text: str) -> list[str]:
    """The words of `text` as brain.confirm splits Master's answer: NFKC, lowercase."""
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
