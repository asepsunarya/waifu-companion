"""The music tool: a song's YouTube page opens only when Master's own words ask for a song.

Every `MusicTools` gets a fake opener that records each link instead of opening it
(tests/conftest.py also refuses the real os.startfile), and the one brain here a fake Ollama
client.
"""

import errno
import inspect
import json
import logging
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.music_tools import MusicTools
from waifu.brain.ollama_brain import OllamaBrain
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.config import BrainConfig
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.music import open_in_browser

VIDEO = "dQw4w9WgXcQ"
PLAYLIST = "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
SONG = "Hope Is the Thing With Feathers"
SEARCH = "https://www.youtube.com/results?search_query=Hope+Is+the+Thing+With+Feathers"
OPENED_SEARCH = {"ok": True, "opened": "search", "service": "youtube"}
ALREADY_OPEN = {
    "ok": True,
    "opened": "already",
    "message": "Satu halaman lagu sudah dibuka untuk pesan Master ini; tidak ada yang dibuka lagi.",
}


class FakeOpener:
    """Stands in for os.startfile: records each link instead of opening it."""

    def __init__(self, error: Exception | None = None) -> None:
        self.links: list[str] = []
        self.error = error

    def __call__(self, link: str) -> None:
        self.links.append(link)
        if self.error is not None:
            raise self.error


def make_tools(
    opener: FakeOpener,
    *,
    service: str = "youtube",
    names: list[str] | None = None,
    links: bool = True,
) -> MusicTools:
    return MusicTools(service=service, names=names or ["Cyrene"], links=links, startfile=opener)


def play(tools: MusicTools, arguments: object) -> tuple[dict, bool]:
    result, is_error = tools.run("play_youtube", arguments)
    return json.loads(result), is_error


def assert_nothing_opens(words: str) -> None:
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn(words)
    result, is_error = play(tools, {"query": SONG, "url": f"https://youtu.be/{VIDEO}"})

    assert is_error is True
    assert result["ok"] is False
    assert "Master tidak meminta" in result["message"]
    assert opener.links == []


# --- spec ----------------------------------------------------------------------------------


def test_one_strict_tool_whose_url_may_be_left_out():
    [spec] = make_tools(FakeOpener()).specs()

    assert spec.name == "play_youtube"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}, "url": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is True
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "play_youtube"


def test_a_brain_that_cannot_search_the_web_is_never_offered_a_url():
    [spec] = make_tools(FakeOpener(), links=False).specs()

    assert spec.input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    assert to_anthropic(spec)["strict"] is True
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "play_youtube"
    assert "url" not in spec.description
    assert "youtu.be" not in spec.description


@pytest.mark.parametrize("links", [True, False], ids=["links", "no_links"])
@pytest.mark.parametrize(
    "guidance",
    [
        "only when Master's current message asks you to play a song or music",
        "At most one page per message",
        "search page",
        "Master picks",
        "one short sentence",
        "never read a link aloud",
        "Never use it to pause, skip, go back, stop, or change the volume",
        "even when there is no media device",
        "takes the focus from a game",
    ],
)
def test_the_description_says_when_to_use_it_and_how(guidance, links):
    [spec] = make_tools(FakeOpener(), links=links).specs()

    assert guidance in spec.description


@pytest.mark.parametrize(
    "guidance",
    [
        "search the web",
        "youtube.com or youtu.be",
        "as `url`",
        "never make one up",
        "pass only `query`",
        "any other link opens the search page",
    ],
)
def test_with_links_the_description_says_how_to_find_one(guidance):
    [spec] = make_tools(FakeOpener()).specs()

    assert guidance in spec.description


@pytest.mark.parametrize("links", [True, False], ids=["links", "no_links"])
def test_the_spec_is_the_same_for_both_services_and_across_turns_for_prompt_caching(links):
    tools = make_tools(FakeOpener(), links=links)
    first = tools.specs()

    tools.begin_turn("putar lagu lofi")
    play(tools, {"query": "lofi"})

    assert tools.specs() == first
    assert make_tools(FakeOpener(), service="youtube_music", links=links).specs() == first


def test_the_default_opener_is_the_one_that_looks_up_os_startfile_when_opening():
    # Bound to os.startfile at import, a default would escape the guard in tests/conftest.py.
    default = inspect.signature(MusicTools).parameters["startfile"].default

    assert default is open_in_browser


# --- only when Master asks -----------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "Cyrene, putar lagu Hope Is the Thing With Feathers",
        "puterin playlist lofi buat belajar",
        "putarkan yang tadi",
        "mainkan OST Honkai Star Rail",
        "mainin sesuatu yang santai",
        "setel lagu yang semangat dong",
        "setelin Hoyo-Mix",
        "play Hoyo-Mix Cyrene theme",
        "PLAY!",
        "ada lagu yang enak nggak?",
        "aku mau musik",
        "aku mau dengar lagu Hoyo-Mix",
        "some music please",
        "buka YouTube, aku mau dengar Hoyo-Mix",
        "dengerin bareng yuk",
    ],
)
def test_master_asking_for_music_opens_one_page(words):
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn(words)
    result, is_error = play(tools, {"query": SONG})

    assert (result, is_error) == (OPENED_SEARCH, False)
    assert opener.links == [SEARCH]


@pytest.mark.parametrize(
    "words",
    [
        "Cyrene, nyalakan lampu kamar.",
        "pause musiknya",
        "kecilin volumenya",
        "lagunya bagus ya",
        "aku bosan",
        "",
        # "dengar" and "setel" alone also hear a person and set a device.
        "kamu dengar aku?",
        "Dengar, Cyrene, cari berita terbaru HSR",
        "setel AC ke 24 derajat",
    ],
)
def test_without_a_play_word_from_master_nothing_opens(words):
    assert_nothing_opens(words)


@pytest.mark.parametrize(
    "words",
    [
        "cari lirik lagu Hope Is the Thing With Feathers",
        "Cyrene, cari info lagu baru Hoyo-Mix",
        "cariin lagu yang lagi viral",
        "tolong nyari lagu yang lagi viral",
        "ada berita terbaru soal musik Hoyo-Mix?",
        "harga YouTube Premium sekarang berapa?",
        "siapa yang nyanyi lagu Hope Is the Thing With Feathers?",
        "apa judul lagu yang lagi viral di TikTok?",
        "lagu apa yang lagi trending minggu ini?",
        "kapan lagu baru Hoyo-Mix rilis?",
        "liriknya lagu Hope Is the Thing With Feathers gimana?",
        "liriknya lagu Hope Is the Thing With Feathers",
        "judulnya lagu ini",
    ],
)
def test_asking_about_a_song_without_a_play_verb_opens_nothing(words):
    # Turns like these send her to the web, whose pages must not get a page opened.
    assert_nothing_opens(words)


@pytest.mark.parametrize(
    "words",
    [
        "Cyrene, sebelum aku play Genshin, cari kode redeem terbaru dong",
        "cari info Genshin terus play",
        "siapa yang mainin Raiden di anime Genshin?",
        "kenapa kamu nggak dengerin aku?",
    ],
)
def test_looking_something_up_or_asking_opens_nothing_even_with_a_play_verb(words):
    # Such turns send her to the web; "play" there often means a game, "dengerin" a person.
    assert_nothing_opens(words)


@pytest.mark.parametrize(
    "words",
    [
        "Cyrene, lagu berikutnya",
        "lagu selanjutnya",
        "lagu sebelumnya",
        "skip lagu ini",
        "next lagu",
        "prev lagu",
        "pause musik",
        "jeda musik",
        "jedain lagu",
        "stop musik",
        "matikan musik",
        "matiin musik",
        "hentikan musik",
        "musik berhenti dulu",
        "lanjutkan lagu",
        "lanjut lagu",
        "lanjutin musik",
        "resume music",
        "lewati lagu ini",
        "lewatin lagu ini",
        "pindah lagu",
        "Cyrene, pindah lagu dong",
        "kecilkan volume musik",
        "kecilin musik",
        "kecilin suara lagu",
        "besarkan volume musik",
        "besarin musik",
        "gedein musik",
        "pelanin musik",
        "pelankan musik",
        "kerasin musik",
        "keraskan musik",
        "mute musik",
        "turn down the music",
        "turn up the music",
        "turn off the music",
    ],
)
def test_the_media_keys_and_the_volume_without_a_play_verb_open_nothing(words):
    # The media device does these; without one, nothing opens either.
    assert_nothing_opens(words)


@pytest.mark.parametrize(
    "words",
    [
        "putar lagu berikutnya",
        "putar lagu selanjutnya",
        "puterin lagu sebelumnya",
        "play next song",
        "play the previous song",
    ],
)
def test_the_next_or_previous_song_opens_nothing_even_with_a_play_verb(words):
    # That is the media device's next or previous key, not a page.
    assert_nothing_opens(words)


@pytest.mark.parametrize(
    "words",
    [
        "stop musik ini, putar lagu jazz aja",
        "matikan lampu terus play musik",
        "matiin musik yang ini, puterin lofi aja",
    ],
)
def test_a_play_verb_asks_for_a_song_over_pausing_or_the_volume(words):
    assert_the_search_page_opens(words)


@pytest.mark.parametrize(
    "words",
    [
        "cari lagu lofi terus putar",
        "carikan dan putar lagu Hoyo-Mix",
        "tolong cariin playlist lofi terus puterin",
        "putar lagu apa aja",
        "putar lagu apa saja yang santai",
    ],
)
def test_finding_a_song_to_play_or_any_song_still_asks_for_one(words):
    # Searching with a play verb and a music word is "find it and play it"; "apa aja" is
    # "anything", not a question.
    assert_the_search_page_opens(words)


def assert_the_search_page_opens(words: str) -> None:
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn(words)

    assert play(tools, {"query": SONG}) == (OPENED_SEARCH, False)
    assert opener.links == [SEARCH]


def test_her_names_never_count_as_asking_for_music():
    opener = FakeOpener()
    tools = make_tools(opener, names=["Lagu", "hai musik"])

    for words in ["Lagu, nyalakan lampu kamar.", "Hai musik, aku pulang."]:
        tools.begin_turn(words)
        assert play(tools, {"query": SONG})[1] is True
    tools.begin_turn("Lagu, putar lagu ini.")
    assert play(tools, {"query": SONG})[1] is False

    assert opener.links == [SEARCH]


def test_before_any_turn_nothing_opens():
    opener = FakeOpener()

    result, is_error = play(make_tools(opener), {"query": SONG})

    assert is_error is True
    assert opener.links == []


def test_asking_counts_only_for_the_turn_it_was_said_in():
    # A web page she read in a later turn cannot make her open anything.
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu lofi")
    play(tools, {"query": "lofi"})
    tools.begin_turn("cari berita terbaru soal HSR")
    result, is_error = play(tools, {"query": "x", "url": f"https://youtu.be/{VIDEO}"})

    assert is_error is True
    assert "Master tidak meminta" in result["message"]
    assert len(opener.links) == 1


def test_one_page_per_turn_and_a_new_request_may_open_another():
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu lofi")
    first = play(tools, {"query": "lofi"})
    second = play(tools, {"query": "jazz"})
    tools.begin_turn("ganti lagu jazz aja")
    third = play(tools, {"query": "jazz"})

    assert first == (OPENED_SEARCH, False)
    assert second == (ALREADY_OPEN, False)
    assert third == (OPENED_SEARCH, False)
    assert opener.links == [
        "https://www.youtube.com/results?search_query=lofi",
        "https://www.youtube.com/results?search_query=jazz",
    ]


# --- what opens ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "opened", "link"),
    [
        (
            f"https://youtu.be/{VIDEO}?si=Ab12-cd_EF34gh&t=42",
            "video",
            f"https://www.youtube.com/watch?v={VIDEO}",
        ),
        (
            f"https://m.youtube.com/watch?v={VIDEO}&list={PLAYLIST}&index=2",
            "video",
            f"https://www.youtube.com/watch?v={VIDEO}&list={PLAYLIST}",
        ),
        (
            f"https://music.youtube.com/playlist?list={PLAYLIST}&si=x",
            "playlist",
            f"https://www.youtube.com/playlist?list={PLAYLIST}",
        ),
    ],
)
def test_a_youtube_link_opens_as_its_canonical_link(url, opened, link):
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    result, is_error = play(tools, {"query": SONG, "url": url})

    assert (result, is_error) == ({"ok": True, "opened": opened, "service": "youtube"}, False)
    assert opener.links == [link]


def test_with_youtube_music_songs_and_searches_open_on_youtube_music():
    opener = FakeOpener()
    tools = make_tools(opener, service="youtube_music")

    tools.begin_turn("putar lagu itu")
    video = play(tools, {"query": SONG, "url": f"https://youtu.be/{VIDEO}"})
    tools.begin_turn("putar lagu yang lain")
    search = play(tools, {"query": SONG})

    assert video == ({"ok": True, "opened": "video", "service": "youtube_music"}, False)
    assert search == ({"ok": True, "opened": "search", "service": "youtube_music"}, False)
    assert opener.links == [
        f"https://music.youtube.com/watch?v={VIDEO}",
        "https://music.youtube.com/search?q=Hope+Is+the+Thing+With+Feathers",
    ]


def test_the_result_never_holds_the_link():
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    result, _ = tools.run("play_youtube", {"query": SONG, "url": f"https://youtu.be/{VIDEO}"})

    assert "http" not in result
    assert VIDEO not in result


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.example/watch?v={VIDEO}",
        f"https://youtube.com.evil.example/watch?v={VIDEO}",
        f"https://evil-youtube.com/watch?v={VIDEO}",
        f"http://www.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com@evil.example/watch?v={VIDEO}",
        f"https://evil.example\\.youtube.com/watch?v={VIDEO}",
        "https://www.youtube.com/redirect?q=https://evil.example/",
        "file:///C:/Windows/System32/calc.exe",
        "",
    ],
)
def test_a_link_that_is_not_a_youtube_video_or_playlist_opens_the_search_page_instead(url):
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")

    assert play(tools, {"query": SONG, "url": url}) == (OPENED_SEARCH, False)
    assert opener.links == [SEARCH]


def test_after_a_link_was_swapped_for_the_search_page_nothing_else_opens_in_that_turn():
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    first = play(tools, {"query": SONG, "url": "https://evil.example/song"})
    second = play(tools, {"query": SONG, "url": f"https://youtu.be/{VIDEO}"})

    assert first == (OPENED_SEARCH, False)
    assert second == (ALREADY_OPEN, False)
    assert opener.links == [SEARCH]


@pytest.mark.parametrize(
    "url", [f"https://youtu.be/{VIDEO}", f"https://www.youtube.com/playlist?list={PLAYLIST}"]
)
def test_without_links_even_a_youtube_link_opens_the_search_page(url):
    # A brain that cannot search the web could only have made the link up.
    opener = FakeOpener()
    tools = make_tools(opener, links=False)

    tools.begin_turn("putar lagu itu")

    assert play(tools, {"query": SONG, "url": url}) == (OPENED_SEARCH, False)
    assert opener.links == [SEARCH]


class FakeOllama:
    """Stands in for ollama.Client: answers each request with the next queued reply."""

    def __init__(self, *replies: ollama.ChatResponse) -> None:
        self.replies = list(replies)

    def chat(self, **request: object) -> ollama.ChatResponse:
        return self.replies.pop(0)


def ollama_reply(content: str, *calls: ollama.Message.ToolCall) -> ollama.ChatResponse:
    return ollama.ChatResponse(
        model="qwen3:8b",
        message=ollama.Message(role="assistant", content=content, tool_calls=list(calls) or None),
    )


@pytest.mark.parametrize("links", [True, False], ids=["links", "no_links"])
def test_a_link_swapped_for_the_search_page_never_makes_her_say_something_failed(links):
    # The local model often ends its turn with no words; her reply then comes from the tools.
    opener = FakeOpener()
    shorts = ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(
            name="play_youtube",
            arguments={"query": "lofi", "url": f"https://www.youtube.com/shorts/{VIDEO}"},
        )
    )
    brain = OllamaBrain(
        BrainConfig(),
        DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar")]),
        "Kamu adalah Cyrene.",
        client=FakeOllama(ollama_reply("", shorts), ollama_reply("")),
        providers=[make_tools(opener, links=links)],
    )

    reply = brain.respond("Cyrene, putar lagu lofi", now=datetime(2026, 10, 16, 20, 0))

    assert reply.text == "Sudah, Master!"
    assert [record.is_error for record in reply.tool_calls] == [False]
    assert opener.links == ["https://www.youtube.com/results?search_query=lofi"]


def test_the_same_call_twice_in_one_reply_opens_one_page_and_never_sounds_like_a_failure():
    # The local model often sends the same tool call twice in one response.
    opener = FakeOpener()
    lofi = ollama.Message.ToolCall(
        function=ollama.Message.ToolCall.Function(name="play_youtube", arguments={"query": "lofi"})
    )
    brain = OllamaBrain(
        BrainConfig(),
        DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar")]),
        "Kamu adalah Cyrene.",
        client=FakeOllama(ollama_reply("", lofi, lofi), ollama_reply("")),
        providers=[make_tools(opener, links=False)],
    )

    reply = brain.respond("Cyrene, putar lagu lofi", now=datetime(2026, 10, 16, 20, 0))

    assert reply.text == "Sudah, Master!"
    assert [record.is_error for record in reply.tool_calls] == [False, False]
    assert opener.links == ["https://www.youtube.com/results?search_query=lofi"]


def test_a_null_url_counts_as_left_out_as_local_models_send_it():
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")

    assert play(tools, {"query": SONG, "url": None}) == (OPENED_SEARCH, False)
    assert opener.links == [SEARCH]


def test_the_search_page_holds_the_words_collapsed():
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("puterin playlist lofi buat belajar")
    play(tools, {"query": "  playlist lofi\n buat   belajar "})

    assert opener.links == [
        "https://www.youtube.com/results?search_query=playlist+lofi+buat+belajar"
    ]


# --- failures ------------------------------------------------------------------------------


def test_windows_failing_to_open_the_browser_is_an_indonesian_error_result(caplog):
    opener = FakeOpener(OSError(errno.ENOENT, "no browser"))
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    with caplog.at_level(logging.WARNING, logger="waifu.brain.music_tools"):
        result, is_error = play(tools, {"query": SONG})

    assert is_error is True
    assert result == {
        "ok": False,
        "message": "Browser tidak bisa dibuka: Windows gagal membuka halamannya.",
    }
    assert opener.links == [SEARCH]
    assert "no browser" in caplog.text


def test_a_page_that_failed_to_open_is_not_tried_again_in_that_turn():
    opener = FakeOpener(OSError(errno.ENOENT, "no browser"))
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    play(tools, {"query": SONG})
    second = play(tools, {"query": SONG})

    assert second == (
        {"ok": False, "message": "Hanya satu halaman lagu yang dibuka untuk setiap pesan Master."},
        True,
    )
    assert opener.links == [SEARCH]


def test_an_unexpected_failure_is_an_error_result_never_an_exception(caplog):
    opener = FakeOpener(RuntimeError("boom"))
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    with caplog.at_level(logging.ERROR, logger="waifu.brain.music_tools"):
        result, is_error = play(tools, {"query": SONG})

    assert is_error is True
    assert result["ok"] is False
    assert "boom" in caplog.text


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [SONG],
        {},
        {"query": ""},
        {"query": "   "},
        {"query": None},
        {"query": 5},
        {"query": SONG, "url": 5},
        {"query": SONG, "url": ["https://youtu.be/dQw4w9WgXcQ"]},
        {"query": SONG, "service": "spotify"},
    ],
)
def test_invalid_arguments_are_an_error_result_and_open_nothing(arguments):
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    result, is_error = play(tools, arguments)

    assert is_error is True
    assert result["ok"] is False
    assert result["message"].startswith("Argumen play_youtube tidak valid")
    assert opener.links == []


def test_another_tool_name_is_an_error_result():
    opener = FakeOpener()
    tools = make_tools(opener)

    tools.begin_turn("putar lagu itu")
    result, is_error = tools.run("look_at_screen", {})

    assert is_error is True
    assert json.loads(result)["ok"] is False
    assert opener.links == []
