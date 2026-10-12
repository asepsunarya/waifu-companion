"""Command-line entry point: `waifu <command>` (also `python -m waifu`).

Each subcommand assembles what it needs from config.yaml through the
module-level builders imported below, so tests can swap any of them for a
fake. Output for the owner is Indonesian; logs go to stderr in English.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import logging
import math
import os
import re
import socket
import sys
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from importlib import import_module
from importlib.util import find_spec
from itertools import count
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep

import numpy as np
from pydantic import ValidationError

from waifu import autostart
from waifu.app import Assistant, split_reply
from waifu.autostart import RunKey, WindowsRunKey
from waifu.avatar.bus import STATES as AVATAR_STATES
from waifu.avatar.bus import AvatarBus
from waifu.avatar.emotion import EMOTIONS, NEUTRAL, split_emotion
from waifu.avatar.lipsync import SHAPE_CENTRE_HZ
from waifu.avatar.server import AvatarError, AvatarServer
from waifu.avatar.window import AvatarWindow
from waifu.brain.base import ToolCallRecord
from waifu.brain.factory import build_brain
from waifu.config import (
    AppConfig,
    AvatarConfig,
    ConfigError,
    OllamaConfig,
    RvcConfig,
    load_config,
)
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import COMMAND_PATTERN, IrCodeStoreError
from waifu.devices.ir_device import IrDevice
from waifu.devices.registry import build_registry
from waifu.gate import WakeGate
from waifu.single_instance import InstanceLock
from waifu.voice.audio import MicStream, Player, list_devices
from waifu.voice.rvc import (
    SERVER_SCRIPT,
    RvcClient,
    RvcError,
    RvcServerProcess,
    require_loopback_host,
    require_served_model,
)
from waifu.voice.segmenter import EnergySegmenter, frame_db
from waifu.voice.speaker import MIN_ENROLL_SAMPLES, EcapaEmbedder, SpeakerError, SpeakerVerifier
from waifu.voice.stt import CUDA_HINT, SttError, WhisperStt, register_cuda_dll_dirs
from waifu.voice.tts import SpeechRouter, TtsError, build_speech_router

logger = logging.getLogger(__name__)

OK, FAIL, WARN = "✓", "✗", "!"
Check = tuple[str, str]

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_FILE_MAX_BYTES = 1_000_000
LOG_FILE_BACKUPS = 3

EXIT_WORDS = frozenset({"keluar", "exit"})
COUNTDOWN_S = 3
OLLAMA_TIMEOUT_S = 2.0
# (label, module `waifu run` imports to load that model); package = the module's top level.
VOICE_LIBRARIES = (("Whisper", "faster_whisper"), ("ECAPA", "speechbrain.inference.classifiers"))
VOICE_EXTRA_MISSING = "belum terpasang (paket suara untuk 'waifu run'; lihat README)"
VOICE_REINSTALL_HINT = 'Pasang ulang paket suara: pip install -e ".[voice]" (lihat README).'
_CUDA_PROBLEM = re.compile(r"cuda|cublas|cudnn|\.dll", re.IGNORECASE)
ENROLL_PHRASES = (
    "{name}, tolong nyalakan lampu kamar, ya.",
    "Hari ini cuacanya cerah sekali, enak untuk jalan-jalan sore.",
    "{name}, atur AC ke dua puluh empat derajat.",
    "Aku baru pulang dan ingin istirahat sebentar sambil minum teh.",
    "{name}, matikan proyektor lalu nyalakan TV.",
    "Besok pagi aku harus bangun jam enam untuk berangkat kerja.",
)
NOT_ENROLLED = "Suara Master belum terdaftar. Jalankan 'waifu enroll' dulu."
COMMAND_NAME_RULE = "pakai huruf kecil, angka, atau garis bawah (misalnya temp_24)"
# Self-scores are inflated because every sample is part of the voiceprint it is scored
# against; half the weakest one is a forgiving start that stays above other voices.
THRESHOLD_SHARE_OF_WEAKEST = 0.5
MIN_SUGGESTED_THRESHOLD = 0.25

DEFAULT_CONFIG = Path("config.yaml")
RUN_LOCK = Path("data") / "run.lock"
ALREADY_RUNNING = (
    "Cyrene sudah berjalan (mungkin dari autostart). Tutup jendelanya dulu, "
    "atau jalankan 'waifu autostart status'."
)
# The per-user Run key behind `waifu autostart`; tests swap in a dict-backed fake.
RUN_KEY_FACTORY: Callable[[], RunKey] = WindowsRunKey
AUTOSTART_DESCRIPTION = (
    "Jalankan Cyrene otomatis saat login Windows. --config dan --dry-run tidak berpengaruh "
    "di sini: yang didaftarkan selalu 'waifu run' dengan config.yaml di folder ini dan "
    "perangkat asli."
)
LOG_TAIL_LINES = 5
AVATAR_OFF = (
    "Avatar 3D mati. Isi avatar.model lalu avatar.enabled: true di config.yaml "
    "(lihat README, bagian Avatar 3D)."
)
ELECTRON_SETUP = "npm install lalu node node_modules/electron/install.js"
# `waifu avatar --demo`: a new state and emotion every step, mouthing a made-up voice with
# one tone syllable per mouth shape (the lip-sync's vowel centres), low to high.
DEMO_STEP_S = 3.0
DEMO_SAMPLE_RATE = 24000
DEMO_SYLLABLE_S = 0.25
DEMO_PAUSE_S = 0.1
DEMO_VOLUME = 0.3
AVATAR_WAIT_S = 60.0  # `waifu avatar` without --demo only waits; Ctrl+C ends a sleep at once
# `say` and the login greeting speak right after opening the window; a speak event only
# reaches pages already connected, so they give the loading page this long to connect.
AVATAR_VIEWER_WAIT_S = 5.0
_REGISTERED_DELAY = re.compile(r" --startup-delay (\d+) ")
# build_command always quotes the folder, and a path in it never contains a quote.
_REGISTERED_PROJECT = re.compile(r' /d "([^"]+)" ')


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _use_utf8_for_redirected_streams()
    _configure_logging(args.verbose)
    # Every fatal path is logged at ERROR as well as printed: under autostart the console
    # window closes when waifu.exe exits, and --log-file is all that is left to read.
    # The traceback is only for -v, so the last lines of the log stay readable.
    with _file_logging(args.log_file):
        try:
            return args.handler(args)
        except ConfigError as exc:
            problem = _config_problem(exc)
            logger.error("Configuration problem: %s", problem)
            print(f"Konfigurasi bermasalah: {problem}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("\nDihentikan.")
            return 130
        except ImportError as exc:
            logger.error(
                "Command '%s' needs the missing package %s",
                args.command,
                exc.name or exc,
                exc_info=args.verbose,
            )
            print(
                f"Paket Python '{exc.name or exc}' belum terpasang. "
                'Untuk fitur suara, ikuti README (pip install -e ".[voice]").',
                file=sys.stderr,
            )
            return 1
        except Exception as exc:
            logger.error(
                "Command '%s' failed: %s: %s",
                args.command,
                type(exc).__name__,
                exc,
                exc_info=args.verbose,
            )
            print(
                f"Terjadi kesalahan tak terduga: {exc} (jalankan dengan -v untuk detail).",
                file=sys.stderr,
            )
            return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waifu", description="Asisten AI bergaya anime yang mengendalikan rumah dengan suara."
    )
    _add_shared_options(parser, suppress_defaults=False)
    # Subcommands accept the shared options too; SUPPRESS keeps the top-level value
    # when an option is given before the subcommand rather than after it.
    shared = argparse.ArgumentParser(add_help=False)
    _add_shared_options(shared, suppress_defaults=True)
    commands = parser.add_subparsers(dest="command", required=True, metavar="<perintah>")

    def command(
        name: str,
        handler: Callable[[argparse.Namespace], int],
        help_text: str,
        group: argparse._SubParsersAction[argparse.ArgumentParser] = commands,
    ) -> argparse.ArgumentParser:
        sub = group.add_parser(name, parents=[shared], help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    run = command("run", _run, "Dengarkan mikrofon dan jawab dengan suara.")
    run.add_argument(
        "--no-speaker-check", action="store_true", help="layani semua suara, bukan hanya Master"
    )
    run.add_argument(
        "--startup-delay",
        type=_non_negative_seconds,
        default=0.0,
        metavar="DETIK",
        help="tunggu sekian detik sebelum mulai, misalnya setelah login (default 0)",
    )
    run.add_argument(
        "--announce", action="store_true", help="ucapkan bahwa dia sudah siap setelah model dimuat"
    )
    chat = command("chat", _chat, "Ngobrol lewat teks (tanpa mikrofon).")
    chat.add_argument("--speak", action="store_true", help="ucapkan juga setiap jawaban")
    enroll = command("enroll", _enroll, "Daftarkan suara Master untuk verifikasi.")
    enroll.add_argument("--samples", type=int, default=6, help="jumlah rekaman (default 6)")
    enroll.add_argument(
        "--seconds", type=float, default=4.0, help="lama tiap rekaman dalam detik (default 4)"
    )
    command("speaker-test", _speaker_test, "Tampilkan skor kecocokan suara secara langsung.")
    command("devices", _devices, "Daftar perangkat dan perintahnya.")
    device = command("device", _device, "Jalankan satu perintah pada satu perangkat.")
    device.add_argument("device_id", metavar="id")
    device.add_argument("device_command", metavar="command")
    device.add_argument("value", type=int, nargs="?", help="angka, misalnya suhu atau kecerahan")
    learn = command("learn-ir", _learn_ir, "Pelajari kode tombol remote IR.")
    learn.add_argument("device_id", metavar="id")
    learn.add_argument("ir_command", metavar="command", nargs="?")
    learn.add_argument(
        "--missing", action="store_true", help="pelajari semua perintah yang belum ada"
    )
    learn.add_argument(
        "--timeout", type=float, default=20.0, help="detik menunggu tombol (default 20)"
    )
    say = command("say", _say, "Ucapkan teks.")
    say.add_argument("text", nargs="+")
    say.add_argument(
        "--raw",
        action="store_true",
        help="tanpa suara anime (RVC), untuk membandingkan dengan suara biasa",
    )
    command("mics", _mics, "Daftar perangkat audio.")
    command("check", _check, "Periksa konfigurasi, perangkat, dan dependensi.")
    avatar = command(
        "avatar", _show_avatar, "Tampilkan avatar 3D saja (tanpa mendengarkan) untuk mengeceknya."
    )
    avatar.add_argument(
        "--demo",
        action="store_true",
        help=f"ganti ekspresi dan keadaan setiap {DEMO_STEP_S:g} detik, mulutnya ikut bergerak",
    )
    autostart_command = commands.add_parser(
        "autostart",
        parents=[shared],
        help="Jalankan Cyrene otomatis saat login Windows.",
        description=AUTOSTART_DESCRIPTION,
    )
    actions = autostart_command.add_subparsers(dest="action", required=True, metavar="<aksi>")
    enable = command(
        "enable", _autostart_enable, "Jalankan Cyrene setiap kali login Windows.", actions
    )
    enable.add_argument(
        "--delay",
        type=_non_negative_whole_seconds,
        default=autostart.DEFAULT_DELAY_S,
        metavar="DETIK",
        help=f"jeda setelah login sebelum model dimuat (default {autostart.DEFAULT_DELAY_S})",
    )
    enable.add_argument(
        "--force",
        action="store_true",
        help="tetap aktifkan walau config.yaml atau voiceprint belum siap",
    )
    command("disable", _autostart_disable, "Jangan jalankan Cyrene lagi saat login.", actions)
    command(
        "status",
        _autostart_status,
        "Status autostart, apakah Cyrene sedang berjalan, dan isi log terakhir.",
        actions,
    )
    return parser


def _add_shared_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    def default(value: object) -> object:
        return argparse.SUPPRESS if suppress_defaults else value

    parser.add_argument(
        "--config", type=Path, default=default(DEFAULT_CONFIG), help="file konfigurasi"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=default(False), help="pakai perangkat simulasi"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=default(False), help="log lebih rinci"
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=default(None),
        metavar="PATH",
        help="tulis log juga ke file ini (UTF-8, diganti baru tiap 1 MB, simpan 3 file lama)",
    )


def _non_negative_whole_seconds(text: str) -> int:
    if not text.isdecimal():
        raise argparse.ArgumentTypeError(f"harus bilangan bulat 0 atau lebih, bukan {text!r}")
    return int(text)


def _non_negative_seconds(text: str) -> float:
    invalid = argparse.ArgumentTypeError(f"harus angka 0 atau lebih, bukan {text!r}")
    try:
        seconds = float(text)
    except ValueError:
        raise invalid from None
    if not (math.isfinite(seconds) and seconds >= 0):
        raise invalid
    return seconds


def _config_problem(exc: ConfigError) -> str:
    """The ConfigError text without the values pydantic quotes, which can be a local_key."""
    cause = exc.__cause__
    if not isinstance(cause, ValidationError):
        return str(exc)
    headline = str(exc).partition("\n")[0]
    problems = [
        f"  {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in cause.errors(include_url=False, include_input=False)
    ]
    return "\n".join([headline, *problems])


def _use_utf8_for_redirected_streams() -> None:
    """Read and write redirected streams as UTF-8; runs before anything reads stdin.

    The Windows console already speaks UTF-8, but a pipe, a file, or a mintty
    terminal falls back to the ANSI code page, which cannot carry ✓/✗ or Japanese.
    """
    for stream in (sys.stdout, sys.stderr):
        if _needs_utf8(stream):
            stream.reconfigure(encoding="utf-8")
    if _needs_utf8(sys.stdin):
        # Stray bytes that are not UTF-8 become U+FFFD instead of ending `waifu chat`.
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def _needs_utf8(stream: object) -> bool:
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    return isinstance(stream, io.TextIOWrapper) and encoding != "utf8"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S"
    )
    # tinytuya logs the device local_key at DEBUG (e.g. after a wrong key or version),
    # and -v output gets pasted when asking for help, so it stays at INFO even then.
    logging.getLogger("tinytuya").setLevel(logging.INFO)
    if not verbose:
        # httpx logs every Ollama request at INFO, which would bury the conversation.
        logging.getLogger("httpx").setLevel(logging.WARNING)


@contextmanager
def _file_logging(path: Path | None) -> Iterator[None]:
    """Also send every log record to a rotating UTF-8 file at `path` while a command runs.

    A file that cannot be opened is reported and skipped: the command (often `waifu run`
    started at login) is worth more than its log.
    """
    handler = _rotating_log_file(path) if path is not None else None
    root = logging.getLogger()
    if handler is not None:
        root.addHandler(handler)
    try:
        yield
    finally:
        if handler is not None:
            root.removeHandler(handler)
            handler.close()


def _rotating_log_file(path: Path) -> logging.Handler | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
        )
    except OSError as exc:
        print(
            f"{WARN} File log {path} tidak bisa dibuat ({exc}); log hanya tampil di layar.",
            file=sys.stderr,
        )
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


# --- voice loop ------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    return _holding_run_lock(_listen, args)


def _holding_run_lock(
    command: Callable[[argparse.Namespace], int], args: argparse.Namespace
) -> int:
    """Run `command` holding the run lock; refuse (exit 2) while a Cyrene is running.

    `run` must never start twice, and `enroll` and `speaker-test` must not record while
    a running Cyrene hears the owner too (and obeys the enrollment phrases). The lock
    comes before the config, so the refusal names the program (the autostart window is
    always titled "Cyrene"), not the configured assistant name.
    """
    lock = InstanceLock(RUN_LOCK)
    if not lock.acquire():
        logger.warning("Another instance holds %s; not starting", RUN_LOCK)
        print(ALREADY_RUNNING)
        return 2
    with lock:
        return command(args)


def _listen(args: argparse.Namespace) -> int:
    if args.startup_delay > 0:
        logger.info("Waiting %g s before starting (--startup-delay)", args.startup_delay)
        sleep(args.startup_delay)
    config = load_config(args.config)
    name = config.assistant.name
    speaker_check = config.speaker.enabled and not args.no_speaker_check
    verifier = _speaker_verifier(config) if speaker_check else None
    if verifier is not None and not verifier.enrolled:
        logger.error("No voiceprint at %s; not listening", config.speaker.voiceprint_path)
        print(f"{NOT_ENROLLED} Atau pakai 'waifu run --no-speaker-check' untuk semua suara.")
        return 2
    registry = build_registry(config, dry_run=args.dry_run)
    brain = build_brain(config, registry)
    stt = WhisperStt(config.stt, name)
    gate = WakeGate(config.assistant.wake_words, config.assistant.followup_seconds)
    segmenter = EnergySegmenter(config.audio.sample_rate, config.audio.vad)
    print(f"Memuat model suara {name}, tunggu sebentar...", flush=True)
    try:
        # The speaker model first: it reads the voiceprint, so a broken one fails fast.
        if verifier is not None:
            verifier.preload()
        stt.preload()
    except (SpeakerError, SttError) as exc:
        logger.error("Could not load the voice models: %s", exc)
        print(f"{FAIL} {exc}")
        return 1
    # The avatar and the anime voice load last: she can listen without them, but not without
    # the models above. The avatar's window opens first, so it loads while RVC starts.
    with _avatar_bus(config) as avatar, _voice_converter(config) as converter:
        assistant = Assistant(
            config,
            brain=brain,
            stt=stt,
            speech=build_speech_router(config.tts, converter=converter),
            player=Player(config.audio.output_device),
            gate=gate,
            verifier=verifier,
            speaker_check=speaker_check,
            avatar=avatar,
        )
        with _open_mic(config) as mic:
            mode = " (perangkat simulasi)" if args.dry_run else ""
            print(
                f"{name} siap mendengarkan{mode}. "
                f"Panggil dengan: {', '.join(config.assistant.wake_words)}. "
                "Tekan Ctrl+C untuk berhenti."
            )
            if not speaker_check:
                print("Verifikasi suara mati: semua suara akan dilayani.")
            if args.announce:
                assistant.set_mic(mic, segmenter)  # so she does not hear her own announcement
                if avatar is not None:
                    _await_avatar_viewer(avatar, _avatar_viewer_wait_s(config))
                # Not a reply, so no follow-up window: after login only her name starts a
                # conversation, as in a plain `run`.
                assistant.speak(f"{name} siap, Master.", followup=False)
            try:
                assistant.run(mic, segmenter)
            except SttError as exc:
                logger.error("Whisper stopped working: %s", exc)
                print(f"{FAIL} {exc}")
                return 1
    return 0


def _chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    brain = build_brain(config, registry)
    with (
        _avatar_bus(config, wanted=args.speak) as avatar,
        _voice_converter(config, wanted=args.speak) as converter,
    ):
        speech = build_speech_router(config.tts, converter=converter) if args.speak else None
        player = Player(config.audio.output_device) if args.speak else None
        name = config.assistant.name
        mode = " (perangkat simulasi)" if args.dry_run else ""
        print(f"Ngobrol dengan {name}{mode}. Ketik 'keluar' untuk berhenti.")
        while True:
            try:
                line = input("Kamu: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in EXIT_WORDS:
                break
            emotion, reply = split_reply(brain.respond(line))
            if args.verbose:
                for call in reply.tool_calls:
                    print(_describe_tool_call(call))
            print(f"{name}: {reply.text}")
            if speech is not None and player is not None:
                _speak(speech, player, reply.text, avatar=avatar, emotion=emotion)
    return 0


def _describe_tool_call(call: ToolCallRecord) -> str:
    arguments = json.dumps(call.arguments, ensure_ascii=False)
    failed = " (gagal)" if call.is_error else ""
    return f"  [{call.name}] {arguments} -> {call.result}{failed}"


def _speak(
    speech: SpeechRouter,
    player: Player,
    text: str,
    *,
    avatar: AvatarBus | None = None,
    emotion: str = NEUTRAL,
    viewer_wait_s: float = 0.0,
) -> bool:
    """Say `text` (already without its emotion tag), the avatar mouthing it with `emotion`.

    With `viewer_wait_s`, a just-opened avatar page gets that long, after the voice is
    ready, to connect before she speaks.
    """
    try:
        audio, sample_rate = speech.synthesize(text)
    except TtsError as exc:
        print(f"(Suara gagal dibuat: {exc})")
        return False
    if avatar is not None:
        try:
            _await_avatar_viewer(avatar, viewer_wait_s)
            avatar.speak(audio, sample_rate, emotion)
        except Exception as exc:  # the avatar is a bonus: she speaks without it
            logger.warning("The avatar could not show this reply: %s", exc)
    player.play(audio, sample_rate)
    return True


def _avatar_viewer_wait_s(config: AppConfig) -> float:
    """How long to wait for a just-opened avatar window; none opens with `window: none`."""
    return 0.0 if config.avatar.window == "none" else AVATAR_VIEWER_WAIT_S


def _await_avatar_viewer(avatar: AvatarBus, timeout_s: float) -> None:
    """Give the avatar page up to `timeout_s` to connect, so it mouths her first words."""
    if timeout_s > 0 and not avatar.wait_for_viewer(timeout_s):
        logger.info("No avatar viewer connected within %g s; speaking without it", timeout_s)


@contextmanager
def _voice_converter(config: AppConfig, *, wanted: bool = True) -> Iterator[RvcClient | None]:
    """The anime-voice (RVC) converter for the block; None when it is off, unwanted, or broken.

    Starts the RVC server unless one already answers, and stops a server it started
    however the block ends. A server that cannot start only costs the anime voice:
    she speaks with the plain voice instead of not at all.
    """
    rvc = config.tts.rvc
    if not (wanted and rvc.enabled):
        yield None
        return
    server = RvcServerProcess(rvc)
    try:
        yield _start_voice_conversion(server, config)
    finally:
        server.stop()


def _start_voice_conversion(server: RvcServerProcess, config: AppConfig) -> RvcClient | None:
    try:
        server.ensure_running()
    except (RvcError, OSError) as exc:  # OSError: logs/rvc_server.log cannot be written
        logger.warning("Anime voice unavailable, speaking with the plain voice: %s", exc)
        reason = str(exc).rstrip(".")
        print(
            f"{WARN} Suara anime tidak tersedia: {reason}. "
            f"{config.assistant.name} memakai suara biasa."
        )
        return None
    return RvcClient(config.tts.rvc)


@contextmanager
def _avatar_bus(config: AppConfig, *, wanted: bool = True) -> Iterator[AvatarBus | None]:
    """The avatar's event bus for the block; None when it is off, unwanted, or cannot start.

    The page is served and the window opened until the block ends. An avatar that
    cannot start (port in use, no Electron) only costs the avatar, never her voice.
    """
    if not (wanted and config.avatar.enabled):
        yield None
        return
    with ExitStack() as shown:
        try:
            avatar, _ = shown.enter_context(_open_avatar(config))
        except AvatarError as exc:
            logger.warning("Avatar unavailable, going on without it: %s", exc)
            reason = str(exc).rstrip(".")
            print(
                f"{WARN} Avatar tidak tersedia: {reason}. "
                f"{config.assistant.name} tetap berjalan tanpa avatar."
            )
            avatar = None
        yield avatar


@contextmanager
def _open_avatar(config: AppConfig) -> Iterator[tuple[AvatarBus, str]]:
    """Serve the avatar page and open its window for the block; yields the bus and page URL.

    Raises AvatarError (Indonesian) when either cannot start; nothing is left running then.
    """
    settings = config.avatar
    bus = AvatarBus(name=config.assistant.name, framing=settings.framing)
    with AvatarServer(settings, bus) as server, AvatarWindow(settings, server.url):
        yield bus, server.url


# --- speaker verification --------------------------------------------------------------------


def _speaker_verifier(config: AppConfig) -> SpeakerVerifier:
    speaker = config.speaker
    embedder = EcapaEmbedder(speaker.model_source, speaker.model_dir, speaker.device)
    return SpeakerVerifier(embedder, speaker.voiceprint_path, speaker.threshold)


def _open_mic(config: AppConfig) -> MicStream:
    audio = config.audio
    return MicStream(audio.sample_rate, audio.vad.frame_ms, audio.input_device)


def _enroll(args: argparse.Namespace) -> int:
    if args.samples < MIN_ENROLL_SAMPLES or args.seconds <= 0:
        print(
            f"Butuh minimal {MIN_ENROLL_SAMPLES} rekaman dengan durasi lebih dari 0 detik "
            f"(diminta {args.samples} x {args.seconds:g} detik)."
        )
        return 2
    return _holding_run_lock(_record_voiceprint, args)


def _record_voiceprint(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    verifier = _speaker_verifier(config)
    sample_rate = config.audio.sample_rate
    print(
        f"Pendaftaran suara: bacakan {args.samples} kalimat dengan suara biasa, "
        f"masing-masing {args.seconds:g} detik."
    )
    samples = []
    for index in range(args.samples):
        phrase = ENROLL_PHRASES[index % len(ENROLL_PHRASES)].format(name=config.assistant.name)
        print(f'\n[{index + 1}/{args.samples}] Bacakan: "{phrase}"')
        _countdown()
        print("Merekam... bicara sekarang!")
        audio = _record(config, args.seconds)
        if frame_db(audio) < config.audio.vad.threshold_db:
            print(f"{WARN} Rekaman ini sangat pelan; periksa mikrofon atau bicara lebih dekat.")
        samples.append(audio)
    try:
        verifier.enroll(samples, sample_rate)
        scores = [verifier.score(sample, sample_rate) for sample in samples]
    except SpeakerError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"\n{OK} Suara Master tersimpan di {config.speaker.voiceprint_path}.")
    print("Kecocokan tiap rekaman dengan voiceprint:")
    for index, score in enumerate(scores, start=1):
        print(f"  Rekaman {index}: {score:.2f}")
    print(
        f"Saran threshold: {_suggested_threshold(scores):.2f} "
        f"(sekarang {config.speaker.threshold:.2f}). Cek dulu dengan 'waifu speaker-test', "
        "lalu ubah speaker.threshold di config.yaml."
    )
    return 0


def _countdown() -> None:
    for remaining in range(COUNTDOWN_S, 0, -1):
        print(f"{remaining}...", end=" ", flush=True)
        sleep(1)
    print()


def _record(config: AppConfig, seconds: float) -> np.ndarray:
    needed = round(seconds * config.audio.sample_rate)
    frames: list[np.ndarray] = []
    recorded = 0
    with _open_mic(config) as mic:
        for frame in mic.frames():
            frames.append(frame)
            recorded += len(frame)
            if recorded >= needed:
                break
    return np.concatenate(frames)[:needed]


def _suggested_threshold(scores: list[float]) -> float:
    return max(MIN_SUGGESTED_THRESHOLD, min(scores) * THRESHOLD_SHARE_OF_WEAKEST)


def _speaker_test(args: argparse.Namespace) -> int:
    return _holding_run_lock(_score_live_speech, args)


def _score_live_speech(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    verifier = _speaker_verifier(config)
    if not verifier.enrolled:
        print(NOT_ENROLLED)
        return 2
    segmenter = EnergySegmenter(config.audio.sample_rate, config.audio.vad)
    print(f"Silakan bicara (Ctrl+C untuk berhenti). Threshold sekarang {verifier.threshold:.2f}.")
    try:
        with _open_mic(config) as mic:
            for frame in mic.frames():
                utterance = segmenter.feed(frame)
                if utterance is None:
                    continue
                accepted, score = verifier.verify(utterance.audio, utterance.sample_rate)
                mark = OK if accepted else FAIL
                print(f"{mark} skor {score:.2f} ({utterance.duration:.1f} detik)")
    except KeyboardInterrupt:
        print("\nSelesai.")
    except SpeakerError as exc:
        print(f"{FAIL} {exc}")
        return 1
    return 0


# --- devices ---------------------------------------------------------------------------------


def _devices(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    rows = [
        (info["id"], info["kind"], info["name"], ", ".join(info["commands"]) or "(belum ada)")
        for info in registry.describe_all()
    ]
    if not rows:
        print(f"Belum ada perangkat di {args.config}.")
        return 0
    print(_format_table(("ID", "JENIS", "NAMA", "PERINTAH"), rows))
    return 0


def _format_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    table = [header, *rows]
    widths = [max(len(row[column]) for row in table) for column in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in table
    )


def _device(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    result = registry.execute(args.device_id, args.device_command, args.value)
    print(result.message)
    return 0 if result.ok else 1


def _learn_ir(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("learn-ir butuh IR blaster sungguhan, jadi tidak bisa dipakai dengan --dry-run.")
        return 2
    if (args.ir_command is None) != args.missing:
        print(
            "Sebutkan satu nama perintah (misalnya: waifu learn-ir ac on) "
            "atau pakai --missing, tidak keduanya."
        )
        return 2
    if args.ir_command is not None and not COMMAND_PATTERN.match(args.ir_command):
        print(f"Nama perintah '{args.ir_command}' tidak valid: {COMMAND_NAME_RULE}.")
        return 2
    config = load_config(args.config)
    device = build_registry(config).get(args.device_id)
    if device is None:
        print(f"Perangkat '{args.device_id}' tidak ada di {args.config}.")
        return 1
    if not isinstance(device, IrDevice):
        print(f"{device.name} bukan perangkat IR, jadi tidak ada kode remote untuk dipelajari.")
        return 1
    commands = [args.ir_command] if args.ir_command else device.missing_commands()
    invalid = _invalid_command_names(device) if args.missing else []
    if invalid:
        print(f"{FAIL} Dilewati: {_invalid_names_problem(invalid, args.config)}.")
        commands = [command for command in commands if command not in invalid]
    elif not commands:
        print(f"Semua perintah {device.name} di {args.config} sudah dipelajari.")
        return 0
    skipped = _learn_commands(device, commands, args.timeout)
    if len(commands) > 1:
        print(f"Selesai: {len(commands) - len(skipped)} dipelajari, {len(skipped)} dilewati.")
        if skipped:
            print(f"Ulangi yang dilewati dengan: waifu learn-ir {device.id} --missing")
    return 1 if skipped or invalid else 0


def _invalid_command_names(device: IrDevice) -> list[str]:
    """Commands declared in config.yaml that can never be learned under that name."""
    return [name for name in device.declared_commands if not COMMAND_PATTERN.match(name)]


def _invalid_names_problem(names: list[str], config_path: Path | str) -> str:
    return (
        f"nama perintah di {config_path} tidak valid ({', '.join(names)}); "
        f"ganti namanya: {COMMAND_NAME_RULE}"
    )


def _learn_commands(device: IrDevice, commands: list[str], timeout_s: float) -> list[str]:
    """Learn each command in turn; returns the ones skipped because nothing was captured."""
    skipped = []
    for index, command in enumerate(commands, start=1):
        step = f"[{index}/{len(commands)}] " if len(commands) > 1 else ""
        print(
            f"{step}Arahkan remote ke blaster lalu tekan tombol {command}... "
            f"(menunggu {timeout_s:g} detik)"
        )
        try:
            device.learn(command, timeout_s=timeout_s)
        except (DeviceError, OSError) as exc:
            print(f"{FAIL} {command} dilewati: {exc}")
            skipped.append(command)
            continue
        print(f"{OK} Kode '{command}' tersimpan untuk {device.name}.")
    return skipped


# --- audio utilities -------------------------------------------------------------------------


def _say(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rvc = config.tts.rvc
    if not (args.raw or rvc.enabled) and rvc.model is not None:
        # Comparing checkpoints this way would play the same plain voice for each one.
        print(
            f"{WARN} Suara anime mati (tts.rvc.enabled: false), jadi tts.rvc.model tidak dipakai."
        )
    emotion, text = split_emotion(" ".join(args.text))
    with _avatar_bus(config) as avatar, _voice_converter(config, wanted=not args.raw) as converter:
        spoken = _speak(
            build_speech_router(config.tts, converter=converter),
            Player(config.audio.output_device),
            text,
            avatar=avatar,
            emotion=emotion,
            viewer_wait_s=_avatar_viewer_wait_s(config),
        )
    return 0 if spoken else 1


def _mics(args: argparse.Namespace) -> int:
    print(list_devices())
    return 0


# --- avatar ----------------------------------------------------------------------------------


def _show_avatar(args: argparse.Namespace) -> int:
    """Show the avatar without listening until Ctrl+C; `--demo` runs through her expressions."""
    config = load_config(args.config)
    if not config.avatar.enabled:
        print(AVATAR_OFF)
        return 2
    try:
        with _open_avatar(config) as (avatar, url):
            print(f"Halaman avatar {config.assistant.name}: {url}. Tekan Ctrl+C untuk berhenti.")
            if args.demo:
                _run_demo(avatar)
            else:
                while True:
                    sleep(AVATAR_WAIT_S)
    except AvatarError as exc:
        logger.error("The avatar could not be shown: %s", exc)
        print(f"{FAIL} {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nSelesai.")
    return 0


def _run_demo(avatar: AvatarBus) -> None:
    """Every DEMO_STEP_S: the next state and emotion, mouthing the demo voice; until Ctrl+C."""
    voice = _demo_voice()
    for step in count():
        state = AVATAR_STATES[step % len(AVATAR_STATES)]
        emotion = EMOTIONS[step % len(EMOTIONS)]
        print(f"Keadaan: {state}, ekspresi: {emotion}", flush=True)
        avatar.state(state)
        avatar.emotion(emotion, hold_s=DEMO_STEP_S)
        avatar.speak(voice, DEMO_SAMPLE_RATE, emotion)
        sleep(DEMO_STEP_S)


def _demo_voice() -> np.ndarray:
    """A stand-in for her voice: one tone syllable per mouth shape, each followed by a pause."""
    times = np.arange(round(DEMO_SYLLABLE_S * DEMO_SAMPLE_RATE)) / DEMO_SAMPLE_RATE
    envelope = np.sin(np.pi * times / DEMO_SYLLABLE_S) ** 2
    pause = np.zeros(round(DEMO_PAUSE_S * DEMO_SAMPLE_RATE))
    syllables = [
        part
        for centre_hz in sorted(SHAPE_CENTRE_HZ.values())
        for part in (envelope * np.sin(2 * np.pi * centre_hz * times), pause)
    ]
    return (DEMO_VOLUME * np.concatenate(syllables)).astype(np.float32)


# --- check -----------------------------------------------------------------------------------


def _check(args: argparse.Namespace) -> int:
    failed = False
    for mark, text in _checks(args.config):
        print(f"{mark} {text}", flush=True)
        failed = failed or mark == FAIL
    return 1 if failed else 0


def _checks(config_path: Path) -> Iterator[Check]:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        yield FAIL, f"Konfigurasi tidak valid: {_config_problem(exc)}"
        return
    yield OK, f"Konfigurasi valid ({config_path})"
    yield from _device_checks(config, config_path)
    yield _voiceprint_check(config)
    yield _brain_check(config)
    yield from _voice_package_checks(config)
    yield _microphone_check(config)
    yield from _anime_voice_checks(config)
    yield from _avatar_checks(config)


def _device_checks(config: AppConfig, config_path: Path) -> Iterator[Check]:
    # Always the real registry: building it never connects, and it keeps IR devices
    # inspectable, so the report is the same with or without --dry-run.
    devices = build_registry(config).all()
    if devices:
        yield OK, f"{len(devices)} perangkat terdaftar"
    else:
        yield WARN, "Belum ada perangkat di config.yaml"
    for device in devices:
        if isinstance(device, IrDevice):
            yield _ir_check(device, config_path)


def _ir_check(device: IrDevice, config_path: Path) -> Check:
    invalid = _invalid_command_names(device)
    if invalid:
        return FAIL, f"{device.name}: {_invalid_names_problem(invalid, config_path)}"
    try:
        missing = device.missing_commands()
        learned = device.commands()
    except IrCodeStoreError as exc:
        return FAIL, f"Kode IR tidak bisa dibaca: {exc}"
    if missing:
        return WARN, (
            f"{device.name}: {len(missing)} perintah belum dipelajari ({', '.join(missing)}). "
            f"Jalankan: waifu learn-ir {device.id} --missing"
        )
    if not learned:
        return WARN, (
            f"{device.name}: belum ada kode IR. Jalankan: waifu learn-ir {device.id} <perintah>"
        )
    return OK, f"{device.name}: semua {len(learned)} kode IR sudah dipelajari"


def _voiceprint_check(config: AppConfig) -> Check:
    path = config.speaker.voiceprint_path
    if not config.speaker.enabled:
        return WARN, "Verifikasi suara dimatikan (speaker.enabled: false): semua suara dilayani"
    if path.exists():
        return OK, f"Voiceprint Master ada ({path})"
    return FAIL, f"Voiceprint Master belum ada ({path}). Jalankan: waifu enroll"


def _brain_check(config: AppConfig) -> Check:
    if config.brain.backend == "ollama":
        return _ollama_check(config.brain.ollama)
    model = config.brain.claude.model
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        workspace = f" (workspace {workspace_id})" if workspace_id else ""
        return OK, f"Claude ({model}): ANTHROPIC_API_KEY sudah diatur{workspace}"
    return WARN, (
        f"Claude ({model}): ANTHROPIC_API_KEY belum diatur; "
        "profil dari 'ant auth login' mungkin tetap bisa dipakai"
    )


def _ollama_check(ollama: OllamaConfig) -> Check:
    url = f"{ollama.host.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=OLLAMA_TIMEOUT_S) as response:
            tags = json.loads(response.read())
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return FAIL, (
            f"Ollama tidak bisa dihubungi di {ollama.host} ({exc}). "
            "Jalankan aplikasi Ollama atau 'ollama serve'"
        )
    pulled = _pulled_models(tags)
    if ollama.model in pulled or f"{ollama.model}:latest" in pulled:
        return OK, f"Ollama siap dengan model {ollama.model}"
    return FAIL, f"Model {ollama.model} belum diunduh. Jalankan: ollama pull {ollama.model}"


def _pulled_models(tags: object) -> set[str]:
    models = tags.get("models") if isinstance(tags, dict) else None
    return {
        str(model.get("model") or model.get("name"))
        for model in models or []
        if isinstance(model, dict)
    }


def _voice_package_checks(config: AppConfig) -> Iterator[Check]:
    # The DLL folders WhisperStt registers before loading, so imports fail here as in `run`.
    register_cuda_dll_dirs()
    for label, module in VOICE_LIBRARIES:
        yield _voice_library_check(label, module)
    if not _is_installed("torch"):
        yield FAIL, f"torch {VOICE_EXTRA_MISSING}"
        return
    yield OK, "torch terpasang"
    yield _cuda_check(config)


def _voice_library_check(label: str, module: str) -> Check:
    package = module.partition(".")[0]
    name = f"{label} ({package})"
    if not _is_installed(package):
        return FAIL, f"{name} {VOICE_EXTRA_MISSING}"
    try:
        import_module(module)
    except Exception as exc:  # a broken native library (CUDA DLL, ABI) only shows on import
        hint = CUDA_HINT if _CUDA_PROBLEM.search(str(exc)) else VOICE_REINSTALL_HINT
        return FAIL, f"{name} gagal dimuat ({exc}). {hint}"
    return OK, f"{name} dapat dimuat"


def _is_installed(package: str) -> bool:
    try:
        return find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def _cuda_check(config: AppConfig) -> Check:
    try:
        import torch

        if torch.cuda.is_available():
            return OK, f"CUDA tersedia: {torch.cuda.get_device_name()}"
    except Exception as exc:  # a broken torch install (DLL load, ABI) must not end the report
        return FAIL, f"torch gagal dimuat: {exc}"
    mark = FAIL if config.stt.device.startswith("cuda") else WARN
    return mark, (
        "CUDA tidak tersedia. Pasang torch dari index cu128 (lihat README) "
        'atau ubah stt.device menjadi "cpu"'
    )


def _microphone_check(config: AppConfig) -> Check:
    try:
        import sounddevice

        info = sounddevice.query_devices(config.audio.input_device, "input")
    except Exception as exc:  # missing package or PortAudio, no input device, bad device name
        return FAIL, f"Mikrofon tidak ditemukan: {exc}"
    return OK, f"Mikrofon: {info['name']}"


def _anime_voice_checks(config: AppConfig) -> Iterator[Check]:
    """The files the RVC server needs, then whether one answers; never starts one."""
    rvc = config.tts.rvc
    if not rvc.enabled:
        name = config.assistant.name
        yield WARN, f"Suara anime (RVC) mati (tts.rvc.enabled: false); {name} memakai suara biasa"
        return
    yield _path_check("Folder Applio", rvc.applio_dir, "tts.rvc.applio_dir", folder=True)
    yield _path_check("Python Applio", rvc.python_path, "tts.rvc.python atau applio_dir")
    yield _path_check("Model RVC", rvc.model, "tts.rvc.model")
    if rvc.index is not None:
        yield _path_check("Index RVC", rvc.index, "tts.rvc.index")
    yield _path_check("Skrip server RVC", SERVER_SCRIPT, "folder tools/ di repo ini")
    try:
        require_loopback_host(rvc.host)
    except RvcError as exc:
        yield FAIL, str(exc)
        return
    yield _rvc_server_check(RvcClient(rvc), rvc)


def _rvc_server_check(client: RvcClient, rvc: RvcConfig) -> Check:
    served = client.served_model()
    if served is None:
        # The normal state before `run`: it starts the server itself, so this is no failure.
        return WARN, (
            f"Server suara RVC belum berjalan di {client.base_url}; "
            "'waifu run', 'say', dan 'chat --speak' menyalakannya sendiri"
        )
    try:
        require_served_model(rvc, served, client.base_url)
    except RvcError as exc:
        return FAIL, str(exc)
    return OK, f"Server suara RVC menjawab di {client.base_url} (model {served})"


def _path_check(label: str, path: Path | None, setting: str, *, folder: bool = False) -> Check:
    if path is None:
        return FAIL, f"{label} belum diisi ({setting})"
    if path.is_dir() if folder else path.is_file():
        return OK, f"{label}: {path}"
    return FAIL, f"{label} tidak ditemukan: {path} (periksa {setting})"


def _avatar_checks(config: AppConfig) -> Iterator[Check]:
    """The VRM, the window's program, and whether the port is free; never starts anything."""
    avatar = config.avatar
    if not avatar.enabled:
        name = config.assistant.name
        yield WARN, f"Avatar 3D mati (avatar.enabled: false); {name} berjalan tanpa avatar"
        return
    yield _path_check("Model VRM avatar", avatar.model, "avatar.model")
    yield _avatar_window_check(avatar)
    yield _avatar_port_check(avatar)


def _avatar_window_check(avatar: AvatarConfig) -> Check:
    url = f"http://{avatar.host}:{avatar.port}/"
    if avatar.window == "none":
        return OK, (
            f"Jendela avatar tidak dibuka (avatar.window: none); buka {url} di browser "
            "selama 'waifu run' berjalan"
        )
    program = AvatarWindow(avatar, url).program()
    label = (
        "Jendela maskot avatar (Electron)"
        if avatar.window == "mascot"
        else "Jendela avatar (Microsoft Edge)"
    )
    if program is not None and program.is_file():
        return OK, f"{label}: {program}"
    if avatar.window == "mascot":
        return FAIL, (
            f"{label} tidak ditemukan: {program}. Pasang dulu: di folder "
            f"{avatar.electron_dir} jalankan {ELECTRON_SETUP}"
        )
    return FAIL, (
        "Microsoft Edge untuk jendela avatar tidak ditemukan. Pasang dengan "
        "'winget install --id Microsoft.Edge', atau pakai avatar.window: mascot di config.yaml"
    )


def _avatar_port_check(avatar: AvatarConfig) -> Check:
    address = f"{avatar.host}:{avatar.port}"
    if port_is_free(avatar.host, avatar.port):
        return OK, f"Port avatar {address} bebas"
    if _instance_running(RUN_LOCK):
        return WARN, f"Port avatar {address} dipakai Cyrene yang sedang berjalan"
    return FAIL, (
        f"Port avatar {address} sudah dipakai program lain; tutup program itu atau ganti "
        "avatar.port di config.yaml"
    )


def port_is_free(host: str, port: int) -> bool:
    """Whether a server could listen on host:port now: binds a plain socket briefly, as the
    avatar server would (no address reuse), and closes it at once; never connects."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


# --- autostart -------------------------------------------------------------------------------


def installed_waifu_exe() -> Path:
    """The `waifu.exe` launcher pip installed into this environment's Scripts folder."""
    return Path(sys.prefix) / "Scripts" / "waifu.exe"


def _autostart_enable(args: argparse.Namespace) -> int:
    if args.dry_run or args.config != DEFAULT_CONFIG:
        print(
            f"Catatan: autostart selalu menjalankan 'waifu run' dengan {DEFAULT_CONFIG} di folder "
            "ini dan perangkat asli; --config dan --dry-run tidak berpengaruh."
        )
    waifu_exe = installed_waifu_exe()
    if not waifu_exe.is_file():
        print(
            f"{FAIL} {waifu_exe} tidak ditemukan. Pasang dulu paketnya di venv ini "
            '(pip install -e ".[dev]", lihat README), lalu ulangi.'
        )
        return 1
    problem = None if args.force else _autostart_blocker()
    if problem is not None:
        print(f"{FAIL} Autostart belum diaktifkan: {problem}")
        print(
            "Perbaiki dulu, atau pakai 'waifu autostart enable --force' untuk tetap mengaktifkan."
        )
        return 2
    try:
        command = _login_command(args.delay)
    except ValueError as exc:
        print(f"{FAIL} Folder ini tidak bisa dipakai untuk autostart ({exc}).")
        return 1
    autostart.enable(RUN_KEY_FACTORY(), command)
    print(
        f"{OK} Autostart aktif. Terdaftar di HKCU\\{autostart.RUN_KEY_PATH} "
        f'dengan nama "{autostart.RUN_VALUE}":'
    )
    print(f"  {command}")
    print(
        'Setelah login Windows, jendela "Cyrene" terbuka dalam keadaan minimize; '
        f"{args.delay} detik kemudian model suara dimuat, lalu dia bilang sudah siap."
    )
    print('Untuk menghentikannya, tutup jendela "Cyrene".')
    print("Untuk mematikan autostart: waifu autostart disable.")
    # Task Manager labels a Run entry with its program's description, here cmd.exe's.
    print(
        'Di Task Manager → Startup apps entri ini bernama "Windows Command Processor" '
        "(Microsoft Corporation). Jangan matikan dari sana: 'waifu autostart enable' dan "
        "'status' tidak bisa melihatnya."
    )
    return 0


def _autostart_blocker() -> str | None:
    """Why `waifu run` would stop right after login, or None when it can start."""
    try:
        config = load_config(DEFAULT_CONFIG)
    except ConfigError as exc:
        return f"konfigurasi bermasalah: {_config_problem(exc)}"
    if config.speaker.enabled and not config.speaker.voiceprint_path.exists():
        return NOT_ENROLLED
    return None


def _login_command(delay_s: int) -> str:
    """The Run value `enable` writes from here: `waifu run` in this folder after `delay_s`.

    Raises ValueError when this folder's path cannot sit inside the command (`%` or `"`).
    """
    return autostart.build_command(Path.cwd().resolve(), installed_waifu_exe(), delay_s=delay_s)


def _autostart_disable(args: argparse.Namespace) -> int:
    if autostart.disable(RUN_KEY_FACTORY()):
        print(
            f"{OK} Autostart dimatikan: Cyrene tidak berjalan lagi saat login. "
            "Jendela yang sedang terbuka tetap jalan sampai ditutup."
        )
    else:
        print("Autostart memang belum aktif; tidak ada yang dihapus.")
    return 0


def _autostart_status(args: argparse.Namespace) -> int:
    run_key = RUN_KEY_FACTORY()
    stored = run_key.get(autostart.RUN_VALUE)
    here = Path.cwd().resolve()
    # The lock and the log live in the folder autostart starts Cyrene in, wherever this runs.
    project = _registered_project(stored) or here
    # Compared with the owner's own --delay: only the project folder and venv can go stale.
    try:
        expected = _login_command(_registered_delay(stored))
    except ValueError as exc:
        expected = None
        print(f"{WARN} Folder ini tidak bisa dipakai untuk autostart ({exc}).")
    state = autostart.status(run_key, expected or "")
    if not state.enabled:
        print("Autostart: mati")
        print("Aktifkan dengan: waifu autostart enable")
    else:
        print("Autostart: aktif")
        print(f"Perintah tersimpan: {state.command}")
        if state.matches_expected:
            print(f"{OK} Perintah tersimpan sesuai dengan folder proyek dan venv ini.")
        elif project != here and not (here / DEFAULT_CONFIG).exists():
            print(
                f"{WARN} Folder ini bukan folder proyek, jadi perintah tersimpan tidak bisa "
                f"dicek. Jalankan 'waifu autostart status' dari folder proyek: {project}"
            )
        else:
            print(
                f"{WARN} Perintah tersimpan berbeda dengan yang didaftarkan dari folder ini "
                "(proyek atau venv dipindah?). Jalankan lagi 'waifu autostart enable' dari "
                "folder proyek."
            )
            if expected is not None:
                print(f"  Dari folder ini: {expected}")
    running = (
        "sedang berjalan" if _instance_running(project / RUN_LOCK) else "tidak sedang berjalan"
    )
    print(f"Cyrene {running}.")
    _print_log_tail(project / autostart.LOG_FILE)
    return 0


def _registered_delay(command: str | None) -> int:
    """The --startup-delay inside a stored Run command; the default when there is none."""
    match = _REGISTERED_DELAY.search(command or "")
    return int(match.group(1)) if match else autostart.DEFAULT_DELAY_S


def _registered_project(command: str | None) -> Path | None:
    """The existing folder a stored Run command starts Cyrene in (its `start /d`), or None."""
    match = _REGISTERED_PROJECT.search(command or "")
    if match is None:
        return None
    project = Path(match.group(1))
    return project if project.is_dir() else None


def _instance_running(lock_path: Path) -> bool:
    """Whether a `waifu run` holds the instance lock at `lock_path` right now."""
    if not lock_path.exists():
        # `run` creates the file, so nothing has run there; probing would create it.
        return False
    probe = InstanceLock(lock_path)
    if not probe.acquire():
        return True
    probe.release()
    return False


def _print_log_tail(path: Path) -> None:
    location = path.resolve()
    if not path.exists():
        print(f"Log: {location} (belum ada)")
        return
    print(f"Log: {location} ({LOG_TAIL_LINES} baris terakhir)")
    try:
        with path.open(encoding="utf-8", errors="replace") as file:
            tail = deque(file, maxlen=LOG_TAIL_LINES)
    except OSError as exc:
        print(f"  {WARN} Log tidak bisa dibaca: {exc}")
        return
    for line in tail:
        print(f"  {line.rstrip()}")
