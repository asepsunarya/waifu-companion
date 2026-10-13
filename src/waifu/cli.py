"""Command-line entry point: `waifu <command>` (also `python -m waifu`).

Each subcommand assembles what it needs from config.yaml through the
module-level builders imported below, so tests can swap any of them for a
fake. Output for the owner is Indonesian; logs go to stderr in English.
"""

from __future__ import annotations

import argparse
import configparser
import http.client
import io
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
from importlib.util import find_spec
from itertools import count
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep

import numpy as np
from pydantic import ValidationError

from waifu import autostart, memory
from waifu.app import Assistant, PhoneAnswers, split_reply
from waifu.autostart import RunKey, WindowsRunKey
from waifu.avatar.bus import STATES as AVATAR_STATES
from waifu.avatar.bus import AvatarBus
from waifu.avatar.emotion import EMOTIONS, NEUTRAL, split_emotion
from waifu.avatar.interactions import InteractionHandler
from waifu.avatar.lipsync import SHAPE_CENTRE_HZ
from waifu.avatar.server import AvatarError, AvatarServer, file_under
from waifu.avatar.window import AvatarWindow
from waifu.brain.base import Brain, ToolCallRecord
from waifu.brain.factory import build_brain
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.persona import WEEKDAYS_ID
from waifu.brain.schedule_tools import ScheduleTools
from waifu.brain.screen_tools import ScreenTools
from waifu.brain.tools import ToolProvider
from waifu.brain.usage_tools import UsageTools
from waifu.briefing import DAILY_BRIEFING, BriefingTimer, BriefingTools
from waifu.config import (
    APP_URL_SCHEMES,
    TAILSCALE_HOST_PATTERN,
    AppConfig,
    AvatarConfig,
    BriefingConfig,
    ConfigError,
    OllamaConfig,
    PhoneConfig,
    RvcConfig,
    cookie_names,
    load_config,
)
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import COMMAND_PATTERN, IrCodeStoreError
from waifu.devices.ir_device import IrDevice
from waifu.devices.registry import DeviceRegistry, build_registry
from waifu.gacha.games import GAMES, Game, next_daily_reset
from waifu.gacha.history import (
    GachaHistory,
    GachaLogApi,
    GachaStore,
    TopPull,
    find_cached_gacha_url,
)
from waifu.gacha.hoyolab import GachaError, GameStatus, HoyolabClient, preferred_uids
from waifu.gacha.reminders import duration_text
from waifu.gacha.tools import GachaTools
from waifu.gacha.watcher import GachaWatcher
from waifu.gate import WakeGate
from waifu.memory import MemoryStore, memory_prompt_section
from waifu.notify import Notifier, build_notifier
from waifu.phone.auth import CODE_LIFETIME, MAX_TRIES, PhoneAuth
from waifu.phone.server import LOOPBACK, PhoneError, PhoneServer
from waifu.routines import describe_routine, routine_problems, run_routine
from waifu.schedule import (
    Action,
    DeviceAction,
    Reminder,
    Repeat,
    ScheduleError,
    ScheduleStore,
)
from waifu.scheduler import MONTHS_ID, Scheduler
from waifu.screen import capture_screen
from waifu.single_instance import InstanceLock
from waifu.usage import DayUsage, TokenUsage, UsageLedger, UsageSummary
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
from waifu.weather import WeatherClient

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
# The owner's role name, written into reaction lines copied from the example before `{owner}`.
_MASTER_WORD = re.compile(r"\bMaster\b")
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
    "Avatar mati. Isi avatar.model lalu avatar.enabled: true di config.yaml "
    "(lihat README, bagian Avatar 3D atau Avatar Live2D & interaksi)."
)
AVATAR_ENGINES = {"vrm": "VRM 3D", "live2d": "Live2D"}
ELECTRON_SETUP = "npm install lalu node node_modules/electron/install.js"
CUBISM_CORE_SETUP = (
    "unduh Cubism SDK for Web dari situs resmi Live2D (setujui lisensinya), lalu salin "
    "Core/live2dcubismcore.min.js ke C:\\AI\\avatars\\live2d\\ (lihat README, bagian "
    "Avatar Live2D & interaksi)"
)
CUBISM_CORE_STAYS_OUT = (
    "file milik Live2D: simpan di luar folder proyek, jangan pernah disalin ke repo"
)
# `waifu avatar` only shows her: a double-click there cannot make her listen.
LISTEN_NEEDS_RUN = "(Klik dua kali membuat {name} mendengarkan, tapi hanya selama 'waifu run'.)"
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
NOTIFY_OFF = (
    "Notifikasi HP mati. Isi notify.topic lalu notify.enabled: true di config.yaml "
    "(lihat README, bagian Notifikasi ke HP)."
)
NOTIFY_TEST_MESSAGE = "Tes notifikasi dari {name}. Kalau ini muncul di HP, notifikasi sudah siap."
NOTIFY_TAG = "video_game"  # ntfy shows it as a game controller emoji
GACHA_DESCRIPTION = (
    "Teman main game HoYoverse: status stamina dan daily, check-in harian HoYoLAB, dan "
    "riwayat pull (pity, 50/50). Perintah ini jalan walau gacha.enabled: false; yang "
    "dinyalakan gacha.enabled hanyalah tool game dan pengingat saat 'waifu run'."
)
# Where the games keep their logs, which lead to their history caches; tests point it elsewhere.
GAME_LOGS_DIR = Path.home() / "AppData" / "LocalLow"
MEMORY_DESCRIPTION = (
    "Ingatan Cyrene tentang Master: catatan yang dia baca di awal setiap sesi obrolan. "
    "Perintah ini jalan walau memory.enabled: false; Cyrene yang sedang berjalan melihat "
    "perubahannya mulai sesi obrolan berikutnya."
)
SCHEDULE_DESCRIPTION = (
    "Jadwal Cyrene: pengingat, perintah perangkat, dan rutinitas yang dia jalankan pada "
    "jamnya selama 'waifu run' menyala. Jadwal baru dibuat lewat suara atau 'waifu chat'. "
    "Perintah ini jalan walau schedule.enabled: false; Cyrene yang sedang berjalan melihat "
    "perubahannya dalam 15 detik."
)
SCHEDULE_IN_RUN = (
    "Jadwal dijalankan oleh Cyrene yang sedang berjalan ('waifu run'); di sini jadwal hanya "
    "dibuat, dilihat, dan dibatalkan."
)
DRY_RUN_SCHEDULE = (
    "Mode simulasi: jadwal yang dibuat di sini hanya percobaan pada perangkat simulasi dan "
    "hilang saat ditutup. Jadwal asli ({path}) tidak disentuh."
)
ROUTINE_DESCRIPTION = (
    "Rutinitas Master: daftar perintah perangkat yang ditulis di bagian routines di "
    "config.yaml dan dijalankan sekaligus. Ubah rutinitas dengan mengedit config.yaml; "
    "dengan --dry-run langkahnya dijalankan pada perangkat simulasi."
)
NO_ROUTINES = (
    "Belum ada rutinitas. Tulis di bagian routines di config.yaml "
    "(lihat README, bagian Jadwal, pengingat, dan rutinitas)."
)
BRIEFING_TAG = "sunrise"  # ntfy shows it as a sunrise emoji
BUDGET_TAG = "money_with_wings"  # ntfy shows it as an emoji of money flying away
PHONE_DESCRIPTION = (
    "Cyrene di HP: halaman dengan avatar, chat, dan tombol bicara yang dibagikan ke HP Master "
    "lewat Tailscale (lihat README, bagian Cyrene di HP). Perintah ini jalan walau "
    "phone.enabled: false; yang menyalakan server HP hanyalah phone.enabled saat 'waifu run'."
)
# The Tailscale CLI, which `waifu phone setup` and `check` only ever ask about its state: looked
# for on the PATH (TAILSCALE_SEARCH_PATH; None is the environment's), else where its Windows
# installer puts it. Tests point both under tmp_path and swap TAILSCALE_RUNNER for a fake.
TAILSCALE_COMMAND = "tailscale"
TAILSCALE_SEARCH_PATH: str | None = None
TAILSCALE_INSTALLED = Path("C:/Program Files/Tailscale/tailscale.exe")
TAILSCALE_TIMEOUT_S = 10.0
TAILSCALE_DOWNLOAD = "https://tailscale.com/download"
# The page works only on HTTPS port 443: the phone server accepts https://<phone.host> as origin.
PHONE_HTTPS_PORT = 443
PHONE_WITHOUT_MODEL_TEXT = (
    "avatar.model belum diisi, jadi halaman HP hanya menampilkan chat tanpa avatar. Isi "
    "avatar.model (dan avatar.live2d_core untuk Live2D) di config.yaml, juga kalau "
    "avatar.enabled: false"
)
REPEAT_TEXT: dict[Repeat, str] = {
    "none": "sekali",
    "daily": "tiap hari",
    "weekdays": "tiap hari kerja",
    "weekends": "tiap akhir pekan",
}
_YEAR_MONTH = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")
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
    command(
        "briefing", _briefing, "Tampilkan ringkasan hari ini (tanggal, cuaca, jadwal) tanpa otak."
    )
    usage = command(
        "usage",
        _usage,
        "Tampilkan perkiraan biaya API Claude per hari dalam sebulan, dari catatan Cyrene.",
    )
    usage.add_argument(
        "--month",
        type=_year_month,
        metavar="YYYY-MM",
        help="bulan yang ditampilkan, misalnya 2026-09 (default bulan ini)",
    )
    command("check", _check, "Periksa konfigurasi, perangkat, dan dependensi.")
    command("notify-test", _notify_test, "Kirim notifikasi tes ke HP lewat ntfy.")
    avatar = command(
        "avatar",
        _show_avatar,
        "Tampilkan avatar saja (tanpa mendengarkan) untuk mengecek model dan reaksinya.",
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
    gacha_command = commands.add_parser(
        "gacha",
        parents=[shared],
        help="Teman main game HoYoverse: status, check-in, riwayat pull.",
        description=GACHA_DESCRIPTION,
    )
    gacha_actions = gacha_command.add_subparsers(dest="action", required=True, metavar="<aksi>")
    command(
        "accounts",
        _gacha_accounts,
        "Akun game di cookie HoYoLAB, dan akun mana yang dipakai.",
        gacha_actions,
    )
    status = command(
        "status",
        _gacha_status,
        "Stamina, daily, mingguan, ekspedisi, dan check-in tiap game.",
        gacha_actions,
    )
    status.add_argument("game", nargs="?", choices=list(GAMES), help="satu game saja")
    command(
        "checkin", _gacha_checkin, "Ambil hadiah check-in harian HoYoLAB sekarang.", gacha_actions
    )
    history = command(
        "history", _gacha_history, "Perbarui riwayat pull lalu tampilkan pity.", gacha_actions
    )
    history.add_argument("game", choices=list(GAMES))
    history.add_argument(
        "--full", action="store_true", help="baca semua halaman riwayat (impor pertama)"
    )
    memory_command = commands.add_parser(
        "memory",
        parents=[shared],
        help="Ingatan Cyrene tentang Master: lihat, tambah, dan hapus catatan.",
        description=MEMORY_DESCRIPTION,
    )
    memory_actions = memory_command.add_subparsers(dest="action", required=True, metavar="<aksi>")
    command("list", _memory_list, "Tampilkan semua catatan.", memory_actions)
    add = command("add", _memory_add, "Simpan satu catatan baru.", memory_actions)
    add.add_argument("text", nargs="+")
    forget = command("forget", _memory_forget, "Hapus satu catatan.", memory_actions)
    forget.add_argument("fact_id", metavar="id", help="id dari 'waifu memory list', misalnya m3")
    clear = command("clear", _memory_clear, "Hapus semua catatan.", memory_actions)
    clear.add_argument(
        "--yes", action="store_true", help="benar-benar hapus (tanpa ini hanya dihitung)"
    )
    schedule_command = commands.add_parser(
        "schedule",
        parents=[shared],
        help="Jadwal Cyrene: lihat dan batalkan pengingat dan perintah terjadwal.",
        description=SCHEDULE_DESCRIPTION,
    )
    schedule_actions = schedule_command.add_subparsers(
        dest="action", required=True, metavar="<aksi>"
    )
    command(
        "list", _schedule_list, "Tampilkan semua jadwal, yang paling dekat dulu.", schedule_actions
    )
    cancel = command("cancel", _schedule_cancel, "Batalkan satu jadwal.", schedule_actions)
    cancel.add_argument("entry_id", metavar="id", help="id dari 'waifu schedule list', misalnya j3")
    clear_schedule = command("clear", _schedule_clear, "Hapus semua jadwal.", schedule_actions)
    clear_schedule.add_argument(
        "--yes", action="store_true", help="benar-benar hapus (tanpa ini hanya dihitung)"
    )
    routine_command = commands.add_parser(
        "routine",
        parents=[shared],
        help="Rutinitas Master dari config.yaml: lihat dan jalankan.",
        description=ROUTINE_DESCRIPTION,
    )
    routine_actions = routine_command.add_subparsers(dest="action", required=True, metavar="<aksi>")
    command("list", _routine_list, "Tampilkan setiap rutinitas dan langkahnya.", routine_actions)
    run_now = command("run", _routine_run, "Jalankan satu rutinitas sekarang.", routine_actions)
    run_now.add_argument("routine_name", metavar="nama", help="nama rutinitas, misalnya tidur")
    phone_command = commands.add_parser(
        "phone",
        parents=[shared],
        help="Cyrene di HP lewat Tailscale: siapkan, pasangkan HP, lihat dan lupakan HP.",
        description=PHONE_DESCRIPTION,
    )
    phone_actions = phone_command.add_subparsers(dest="action", required=True, metavar="<aksi>")
    command(
        "setup",
        _phone_setup,
        "Cari Tailscale, lalu tampilkan perintah 'tailscale serve', alamat HP, dan phone.host.",
        phone_actions,
    )
    command(
        "pair",
        _phone_pair,
        "Buat kode 6 angka untuk memasangkan satu HP "
        f"(berlaku {CODE_LIFETIME // timedelta(minutes=1)} menit).",
        phone_actions,
    )
    command("devices", _phone_devices, "Tampilkan HP yang sudah dipasangkan.", phone_actions)
    forget_phone = command(
        "forget", _phone_forget, "Lupakan satu HP; HP itu harus dipasangkan lagi.", phone_actions
    )
    forget_phone.add_argument(
        "device_id", metavar="id", help="id dari 'waifu phone devices', misalnya d1"
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


def _year_month(text: str) -> tuple[int, int]:
    """`2026-10` as (2026, 10)."""
    match = _YEAR_MONTH.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError(
            f"tulis bulannya YYYY-MM, misalnya 2026-10, bukan {text!r}"
        )
    return int(match[1]), int(match[2])


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
    # The Anthropic SDK logs every request in full at DEBUG ("Request options"): the whole
    # conversation, and every screenshot of the session as base64. INFO, even with -v.
    logging.getLogger("anthropic").setLevel(logging.INFO)
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
    gacha = build_gacha(config) if config.gacha.enabled else None
    usage = _usage_recorder(config)
    with _session_schedule(config, dry_run=args.dry_run) as schedule:
        brain = _companion_brain(config, registry, gacha, schedule, usage)
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
        # the models above. The avatar's window opens first, so it loads while RVC starts; the
        # pats and pokes on it reach her once she can answer them.
        touches = _InteractionRelay()
        with (
            _avatar_bus(config, on_interact=touches.handle) as avatar,
            _voice_converter(config) as converter,
        ):
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
            reactions = InteractionHandler(
                config.avatar.interactions,
                speak=assistant.speak_reaction,
                listen=assistant.listen_now,
                is_busy=lambda: assistant.busy,
                interrupt=assistant.interrupt,
                owner_call=config.assistant.owner_call,
            )
            with _open_mic(config) as mic, touches.answered_by(reactions):
                mode = " (perangkat simulasi)" if args.dry_run else ""
                print(
                    f"{name} siap mendengarkan{mode}. "
                    f"Panggil dengan: {', '.join(config.assistant.wake_words)}. "
                    "Tekan Ctrl+C untuk berhenti."
                )
                if not speaker_check:
                    print("Verifikasi suara mati: semua suara akan dilayani.")
                if args.dry_run and schedule is not None:
                    print(DRY_RUN_SCHEDULE.format(path=config.schedule.path))
                if args.announce:
                    # So she does not hear her own announcement.
                    assistant.set_mic(mic, segmenter)
                    if avatar is not None:
                        _await_avatar_viewer(avatar, _avatar_viewer_wait_s(config))
                    # Not a reply, so no follow-up window: after login only her name starts a
                    # conversation, as in a plain `run`.
                    assistant.speak(f"{name} siap, {config.assistant.owner_call}.", followup=False)
                notifier = build_notifier(config.notify)
                try:
                    with (
                        _gacha_watcher(config, gacha, assistant),
                        _scheduler(
                            config,
                            schedule,
                            registry,
                            announce=assistant.announce,
                            notifier=notifier,
                        ),
                        _briefing_timer(config, assistant, notifier),
                        _budget_told(usage, _budget_teller(config, assistant, notifier)),
                        _phone_server(config, assistant),
                    ):
                        assistant.run(mic, segmenter)
                except SttError as exc:
                    logger.error("Whisper stopped working: %s", exc)
                    print(f"{FAIL} {exc}")
                    return 1
    return 0


def _chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    gacha = build_gacha(config) if config.gacha.enabled else None
    usage = _usage_recorder(config)
    name = config.assistant.name
    with _session_schedule(config, dry_run=args.dry_run) as schedule:
        brain = _companion_brain(config, registry, gacha, schedule, usage)
        # One scheduler fires the real schedule, and one teller gives the budget notices: a
        # running `waifu run`, which speaks and pushes, rather than this chat, which would take
        # the entries and the notices and only print them. A dry run has a schedule of its own,
        # but its responses go to the real records.
        run_is_running = _instance_running(RUN_LOCK)
        left_to_run = schedule is not None and not args.dry_run and run_is_running
        with (
            _avatar_bus(config, wanted=args.speak) as avatar,
            _voice_converter(config, wanted=args.speak) as converter,
        ):
            speech = build_speech_router(config.tts, converter=converter) if args.speak else None
            player = Player(config.audio.output_device) if args.speak else None
            mode = " (perangkat simulasi)" if args.dry_run else ""
            print(f"Ngobrol dengan {name}{mode}. Ketik 'keluar' untuk berhenti.")
            if left_to_run:
                print(SCHEDULE_IN_RUN)
            elif args.dry_run and schedule is not None:
                print(DRY_RUN_SCHEDULE.format(path=config.schedule.path))
            # What comes due while Master types, and a budget notice, are printed like a reply,
            # never pushed.
            with (
                _scheduler(
                    config,
                    None if left_to_run else schedule,
                    registry,
                    announce=lambda text, emotion: print(f"{name}: {text}", flush=True),
                    notifier=None,
                ),
                _budget_told(
                    None if run_is_running else usage,
                    lambda notice: print(f"{name}: {notice}", flush=True),
                ),
            ):
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
                    emotion, reply = split_reply(
                        brain.respond(line), owner_call=config.assistant.owner_call
                    )
                    if args.verbose:
                        for call in reply.tool_calls:
                            print(_describe_tool_call(call))
                    print(f"{name}: {reply.text}")
                    if speech is not None and player is not None:
                        _speak(speech, player, reply.text, avatar=avatar, emotion=emotion)
    return 0


def _companion_brain(
    config: AppConfig,
    registry: DeviceRegistry,
    gacha: GachaParts | None,
    schedule: ScheduleStore | None,
    usage: _UsageRecorder | None,
) -> Brain:
    """The brain with the game tools when they are on, her long-term memory when it is on,
    then her schedule when it is on, the day's briefing when it is on, with Claude her eyes on
    the screen when vision is on, and with `usage` what her requests cost.

    With memory, every new session lists the notes as they are in the file at that moment.
    With `usage`, every Claude response is recorded in its ledger.
    """
    owner_call = config.assistant.owner_call
    notes = _memory_store(config) if config.memory.enabled else None
    providers = _game_tools(config, gacha)
    if notes is not None:
        providers.append(MemoryTools(notes, owner_call=owner_call))
    if schedule is not None:
        providers.append(
            ScheduleTools(
                schedule, registry=registry, routines=config.routines, owner_call=owner_call
            )
        )
    if config.briefing.enabled:
        providers.append(_briefing_tools(config.briefing, schedule))
    if config.vision.enabled and config.brain.backend == "claude":
        # Ollama's local model cannot see images, so it is never offered the screen. Her name,
        # wake words, and what she calls the owner are left out of Master's words, as for the
        # confirmation gate.
        providers.append(
            ScreenTools(
                capture=capture_screen,
                max_side=config.vision.max_side,
                names=[config.assistant.name, *config.assistant.wake_words, owner_call],
            )
        )
    if usage is not None:
        providers.append(UsageTools(usage.ledger, idr_per_usd=config.usage.idr_per_usd))
    on_usage = None if usage is None else usage.record
    if notes is None:
        return build_brain(config, registry, providers=providers, on_usage=on_usage)
    return build_brain(
        config,
        registry,
        providers=providers,
        memory_section=lambda: memory_prompt_section(notes.facts()),
        on_usage=on_usage,
    )


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
def _avatar_bus(
    config: AppConfig,
    *,
    wanted: bool = True,
    on_interact: Callable[[str], None] | None = None,
) -> Iterator[AvatarBus | None]:
    """The avatar's event bus for the block; None when it is off, unwanted, or cannot start.

    The page is served and the window opened until the block ends; the page's pats,
    pokes, and double-clicks go to `on_interact`. An avatar that cannot start (port in
    use, no Electron) only costs the avatar, never her voice.
    """
    if not (wanted and config.avatar.enabled):
        yield None
        return
    with ExitStack() as shown:
        try:
            avatar, _ = shown.enter_context(_open_avatar(config, on_interact=on_interact))
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
def _open_avatar(
    config: AppConfig, *, on_interact: Callable[[str], None] | None = None
) -> Iterator[tuple[AvatarBus, str]]:
    """Serve the avatar page and open its window for the block; yields the bus and page URL.

    The page's interactions go to `on_interact`. Raises AvatarError (Indonesian) when
    either cannot start; nothing is left running then.
    """
    settings = config.avatar
    bus = AvatarBus(name=config.assistant.name, framing=settings.framing)
    with (
        AvatarServer(settings, bus, on_interact=on_interact) as server,
        AvatarWindow(settings, server.url),
    ):
        yield bus, server.url


class _InteractionRelay:
    """Hands the avatar page's interactions to the handler answering them now, if any.

    The page is served before anything can answer it (the window loads while her voice
    starts), so the server gets `handle` at once and `answered_by` connects the handler
    for a block; interactions outside that block are dropped.
    """

    def __init__(self) -> None:
        self._handler: InteractionHandler | None = None

    def handle(self, kind: str) -> None:
        handler = self._handler
        if handler is not None:
            handler.handle(kind)

    @contextmanager
    def answered_by(self, handler: InteractionHandler) -> Iterator[None]:
        """Run `handler` and hand it every interaction until the block ends."""
        with handler:
            self._handler = handler
            try:
                yield
            finally:
                self._handler = None


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
    """Show the avatar without listening until Ctrl+C; `--demo` runs through her expressions.

    Pats, pokes, and double-clicks get her reaction printed and shown on the avatar.
    """
    config = load_config(args.config)
    if not config.avatar.enabled:
        print(AVATAR_OFF)
        return 2
    touches = _InteractionRelay()
    try:
        with (
            _open_avatar(config, on_interact=touches.handle) as (avatar, url),
            touches.answered_by(_shown_reactions(config, avatar)),
        ):
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


def _shown_reactions(config: AppConfig, avatar: AvatarBus) -> InteractionHandler:
    """Reactions for `waifu avatar`: each line printed and mouthed with the demo voice.

    Nothing is spoken and no microphone is opened; a double-click only explains that
    listening needs `waifu run` (the handler then shows a listen line as usual).
    """
    name = config.assistant.name
    voice = _demo_voice()

    def show(line: str, emotion: str) -> None:
        print(f"{name}: {line}", flush=True)
        avatar.emotion(emotion)
        avatar.speak(voice, DEMO_SAMPLE_RATE, emotion)

    def explain_listening() -> None:
        print(LISTEN_NEEDS_RUN.format(name=name), flush=True)

    return InteractionHandler(
        config.avatar.interactions,
        speak=show,
        listen=explain_listening,
        is_busy=lambda: False,
        owner_call=config.assistant.owner_call,
    )


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


# --- phone notifications ---------------------------------------------------------------------


def _notify_test(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    notifier = build_notifier(config.notify)
    if notifier is None:
        print(NOTIFY_OFF)
        return 2
    name = config.assistant.name
    if notifier.send(name, NOTIFY_TEST_MESSAGE.format(name=name), tags=[NOTIFY_TAG]):
        print(
            f"{OK} Notifikasi tes terkirim. Kalau tidak muncul di HP, langganan topik yang sama "
            "dengan notify.topic di aplikasi ntfy."
        )
        return 0
    print(
        f"{FAIL} Notifikasi tidak terkirim ke {config.notify.server}. Cek koneksi internet dan "
        "notify.server; detailnya ada di log."
    )
    return 1


# --- HoYoverse games -------------------------------------------------------------------------


@dataclass(frozen=True)
class GachaParts:
    hoyolab: HoyolabClient
    history: GachaHistory


def build_gacha(config: AppConfig) -> GachaParts:
    """The HoYoLAB client and the pull history, as `gacha` in config.yaml describes them."""
    gacha = config.gacha
    cookie = gacha.cookie.get_secret_value() if gacha.cookie is not None else None
    hoyolab = HoyolabClient(cookie, uids=gacha.uids)
    history = GachaHistory(
        GachaStore(gacha.data_dir),
        GachaLogApi(),
        locallow=GAME_LOGS_DIR,
        hoyolab=hoyolab,
        extra_standard=gacha.off_banner,
        server_utc_offset=gacha.server_utc_offset,
    )
    return GachaParts(hoyolab, history)


def _game_tools(config: AppConfig, gacha: GachaParts | None) -> list[ToolProvider]:
    if gacha is None:
        return []
    tools = GachaTools(
        gacha.hoyolab,
        gacha.history,
        config.gacha.games,
        server_utc_offset=config.gacha.server_utc_offset,
    )
    return [tools]


def _gacha_watcher(
    config: AppConfig, gacha: GachaParts | None, assistant: Assistant
) -> AbstractContextManager[object]:
    """The reminders and check-in while she listens; nothing to watch without the cookie."""
    if gacha is None or config.gacha.cookie is None:
        return nullcontext()
    return GachaWatcher(
        gacha.hoyolab,
        config.gacha,
        notifier=build_notifier(config.notify),
        announce=assistant.announce,
        title=config.assistant.name,
        owner_call=config.assistant.owner_call,
    )


def _gacha_accounts(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        accounts = build_gacha(config).hoyolab.accounts()
    except GachaError as error:
        print(f"{FAIL} {error.message}")
        return 1
    if not accounts:
        print(f"{WARN} Cookie ini tidak terhubung ke akun HSR, Genshin, atau ZZZ.")
        return 1
    chosen = {**preferred_uids(accounts), **config.gacha.uids}  # config.yaml wins
    for account in accounts:
        mark = "→" if chosen.get(account.game) == account.uid else " "
        print(
            f"{mark} {GAMES[account.game].short:<8} {account.uid}  {account.nickname} "
            f"(level {account.level}, {account.server_name})"
        )
    print("→ = akun yang dipakai. Pilih akun lain dengan gacha.uids di config.yaml.")
    return 0


def _gacha_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    games = [args.game] if args.game else config.gacha.games
    now = datetime.now().astimezone()
    failed = False
    for game_id, result in build_gacha(config).hoyolab.status(games).items():
        game = GAMES[game_id]
        print(f"{game.short} ({game.name})")
        if isinstance(result, GachaError):
            print(f"  {FAIL} {result.message}")
            failed = True
            continue
        for line in _status_lines(game, result, now, config.gacha.server_utc_offset):
            print(f"  {line}")
    return 1 if failed else 0


def _status_lines(game: Game, status: GameStatus, now: datetime, offset: int) -> list[str]:
    stamina = f"{game.stamina} {status.stamina}/{status.stamina_max}"
    if status.stamina >= status.stamina_max or status.stamina_full_in_s <= 0:
        stamina += " (sudah penuh)"
    else:
        full_at = now + timedelta(seconds=status.stamina_full_in_s)
        wait = duration_text(math.ceil(status.stamina_full_in_s / 60))
        stamina += f" (penuh {full_at:%H:%M}, {wait} lagi)"
    daily_state = "selesai" if status.daily_done else "belum selesai"
    lines = [stamina, f"{game.daily} {status.daily_current}/{status.daily_max}: {daily_state}"]
    if status.weekly:
        weekly = ", ".join(f"{task.name} {task.current}/{task.max}" for task in status.weekly)
        lines.append(f"Mingguan: {weekly}")
    if status.expeditions_total:
        finished = f"{status.expeditions_finished}/{status.expeditions_total}"
        lines.append(f"Ekspedisi selesai {finished}")
    checked_in = {True: "sudah", False: "belum", None: "tidak diketahui"}[status.checked_in]
    lines.append(f"Check-in HoYoLAB hari ini: {checked_in}")
    lines.append(f"Reset harian jam {next_daily_reset(now, offset):%H:%M}")
    return lines


def _gacha_checkin(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    hoyolab = build_gacha(config).hoyolab
    failed = False
    for game_id in config.gacha.games:
        short = GAMES[game_id].short
        try:
            result = hoyolab.checkin(game_id)
        except GachaError as error:
            print(f"{FAIL} {short}: {error.message}")
            failed = True
            continue
        reward = f"dapat {result.reward}" if result.claimed else "sudah diambil hari ini"
        print(f"{OK} {short}: {reward}")
    return 1 if failed else 0


def _gacha_history(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    game = GAMES[args.game]
    note = " (semua halaman, bisa beberapa menit)" if args.full else ""
    print(f"Membaca riwayat {game.pull_word} {game.short}{note}...", flush=True)
    report = build_gacha(config).history.report(args.game, full=args.full)
    if report.problem:
        print(f"{WARN} {report.problem}")
    if not report.stats:
        print(f"{FAIL} Belum ada riwayat {game.short} yang tersimpan.")
        return 1
    if report.updated:
        latest = f", terakhir {report.latest_pull:%Y-%m-%d %H:%M}" if report.latest_pull else ""
        print(f"{OK} {report.new_count} pull baru tersimpan{latest}.")
    top = "S-rank" if game.id == "zzz" else "5★"
    for stats in report.stats:
        guarantee = {True: ", berikutnya pasti limited", False: ", berikutnya 50/50", None: ""}
        print(
            f"{stats.banner.name}: pity {stats.pity}/{stats.banner.hard_pity}"
            f"{guarantee[stats.guaranteed]}, total {stats.total} pull"
        )
        if stats.recent_tops:
            tops = ", ".join(_top_pull_text(pull) for pull in stats.recent_tops)
            print(f"  {top} terakhir: {tops}")
    if report.hint:
        print(f"{WARN} {report.hint}")
    return 0 if report.problem is None else 1


def _top_pull_text(pull: TopPull) -> str:
    result = {True: ", menang 50/50", False: ", kalah 50/50", None: ""}[pull.won_5050]
    return f"{pull.name} ({pull.pulls} pull{result})"


# --- long-term memory ------------------------------------------------------------------------


def _memory_store(config: AppConfig) -> MemoryStore:
    """Her notes about Master, in the file `memory.path` names, whether memory is on or not."""
    settings = config.memory
    return MemoryStore(settings.path, max_facts=settings.max_facts, max_chars=settings.max_chars)


def _memory_list(args: argparse.Namespace) -> int:
    facts = _memory_store(load_config(args.config)).facts()
    if not facts:
        print("Belum ada catatan.")
        return 0
    width = max(len(fact.id) for fact in facts)
    for fact in facts:
        print(f"{fact.id.ljust(width)}  {fact.updated:%d-%m-%Y}  {fact.text}")
    return 0


def _memory_add(args: argparse.Namespace) -> int:
    store = _memory_store(load_config(args.config))
    saved_ids = {fact.id for fact in store.facts()}
    try:
        fact = store.add(" ".join(args.text))
    except memory.MemoryError as exc:
        print(f"{FAIL} {exc}")
        return 1
    if fact.id in saved_ids:  # the same text was saved before: the store gave that note back
        print(f"{OK} Catatan itu sudah ada sebagai {fact.id}: {fact.text}")
    else:
        print(f"{OK} Tersimpan sebagai {fact.id}: {fact.text}")
    return 0


def _memory_forget(args: argparse.Namespace) -> int:
    try:
        fact = _memory_store(load_config(args.config)).forget(args.fact_id)
    except memory.MemoryError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"{OK} Dihapus {fact.id}: {fact.text}")
    return 0


def _memory_clear(args: argparse.Namespace) -> int:
    """Delete every note with --yes; without it, only say how many would go."""
    store = _memory_store(load_config(args.config))
    count = store.clear() if args.yes else len(store.facts())
    if count == 0:
        print("Belum ada catatan; tidak ada yang dihapus.")
    elif args.yes:
        print(f"{OK} {count} catatan dihapus.")
    else:
        print(
            f"Ada {count} catatan yang akan dihapus. Jalankan 'waifu memory clear --yes' "
            "untuk benar-benar menghapusnya."
        )
    return 0


# --- schedules, reminders, and routines ------------------------------------------------------


def _schedule_store(config: AppConfig) -> ScheduleStore:
    """Her schedule, in the file `schedule.path` names, whether the schedule is on or not."""
    return ScheduleStore(config.schedule.path, max_entries=config.schedule.max_entries)


@contextmanager
def _session_schedule(config: AppConfig, *, dry_run: bool) -> Iterator[ScheduleStore | None]:
    """The schedule of one `run` or `chat`, or None with the schedule off.

    A dry run gets an empty schedule of its own in a temporary folder, deleted when it ends:
    its simulated devices never use up the real entries, and what it schedules (checked
    against the simulated devices) never runs on the real ones.
    """
    if not config.schedule.enabled:
        yield None
    elif not dry_run:
        yield _schedule_store(config)
    else:
        with tempfile.TemporaryDirectory(
            prefix="waifu-dry-run-", ignore_cleanup_errors=True
        ) as folder:
            yield ScheduleStore(
                Path(folder) / config.schedule.path.name,
                max_entries=config.schedule.max_entries,
            )


def _scheduler(
    config: AppConfig,
    store: ScheduleStore | None,
    registry: DeviceRegistry,
    *,
    announce: Callable[[str, str], None],
    notifier: Notifier | None,
) -> AbstractContextManager[object]:
    """What comes due fires while the block runs; nothing fires with the schedule off."""
    if store is None:
        return nullcontext()
    return Scheduler(
        store,
        registry=registry,
        routines=config.routines,
        announce=announce,
        notifier=notifier,
        grace_minutes=config.schedule.late_grace_minutes,
        title=config.assistant.name,
        owner_call=config.assistant.owner_call,
    )


def _schedule_list(args: argparse.Namespace) -> int:
    entries = _schedule_store(load_config(args.config)).entries()
    if not entries:
        print("Belum ada jadwal.")
        return 0
    rows = [
        (entry.id, _schedule_time(entry.at), REPEAT_TEXT[entry.repeat], _action_text(entry.action))
        for entry in entries
    ]
    print(_format_table(("ID", "BERIKUTNYA", "ULANG", "ISI"), rows))
    return 0


def _schedule_cancel(args: argparse.Namespace) -> int:
    try:
        entry = _schedule_store(load_config(args.config)).cancel(args.entry_id)
    except ScheduleError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(
        f"{OK} Dibatalkan {entry.id}: {_action_text(entry.action)}, "
        f"{_schedule_time(entry.at)}, {REPEAT_TEXT[entry.repeat]}"
    )
    return 0


def _schedule_clear(args: argparse.Namespace) -> int:
    """Delete every entry with --yes; without it, only say how many would go."""
    store = _schedule_store(load_config(args.config))
    count = store.clear() if args.yes else len(store.entries())
    if count == 0:
        print("Belum ada jadwal; tidak ada yang dihapus.")
    elif args.yes:
        print(f"{OK} {count} jadwal dihapus.")
    else:
        print(
            f"Ada {count} jadwal yang akan dihapus. Jalankan 'waifu schedule clear --yes' "
            "untuk benar-benar menghapusnya."
        )
    return 0


def _routine_list(args: argparse.Namespace) -> int:
    routines = load_config(args.config).routines
    if not routines:
        print(NO_ROUTINES)
        return 0
    for name, routine in routines.items():
        print(describe_routine(name, routine))
    return 0


def _routine_run(args: argparse.Namespace) -> int:
    """Run one routine now on the devices (simulated with --dry-run), printing every step."""
    config = load_config(args.config)
    name = args.routine_name
    routine = config.routines.get(name)
    if routine is None:
        known = ", ".join(config.routines) or "(belum ada)"
        print(
            f"{FAIL} Tidak ada rutinitas bernama '{name}' di {args.config}. "
            f"Rutinitas yang ada: {known}."
        )
        return 1
    mode = " (perangkat simulasi)" if args.dry_run else ""
    print(f"Menjalankan rutinitas {name}{mode}...", flush=True)
    results = run_routine(build_registry(config, dry_run=args.dry_run), routine)
    for number, (step, result) in enumerate(zip(routine.steps, results, strict=True), start=1):
        mark = OK if result.ok else FAIL
        step_text = _command_text(step.device, step.command, step.value)
        print(f"{mark} Langkah {number} ({step_text}): {result.message}")
    return 0 if all(result.ok for result in results) else 1


def _schedule_time(moment: datetime) -> str:
    """`Selasa 13-10-2026 15:00`: a local time of the schedule as the owner reads it."""
    return f"{WEEKDAYS_ID[moment.weekday()]} {moment:%d-%m-%Y %H:%M}"


def _action_text(action: Action) -> str:
    if isinstance(action, Reminder):
        return f'pengingat "{action.text}"'
    if isinstance(action, DeviceAction):
        return f"perangkat {_command_text(action.device, action.command, action.value)}"
    return f"rutinitas {action.name}"


def _command_text(device: str, command: str, value: int | None) -> str:
    """`ac temp 26`: a device command as the owner writes it."""
    return f"{device} {command}" if value is None else f"{device} {command} {value}"


# --- morning briefing ------------------------------------------------------------------------


def _weather_client(briefing: BriefingConfig) -> WeatherClient | None:
    """Open-Meteo for the place `briefing` names (its coordinates win over its city); None
    when it names none. Nothing is asked until the weather is wanted."""
    if briefing.city is None and briefing.latitude is None:
        return None
    return WeatherClient(
        latitude=briefing.latitude, longitude=briefing.longitude, city=briefing.city
    )


def _briefing_tools(briefing: BriefingConfig, schedule: ScheduleStore | None) -> BriefingTools:
    """The day's date, weather, and entries of `schedule` (none without one) for the brain."""
    return BriefingTools(weather=_weather_client(briefing), schedule=schedule)


def _briefing_timer(
    config: AppConfig, assistant: Assistant, notifier: Notifier | None
) -> AbstractContextManager[object]:
    """Her morning greeting, asked of her once a day while the block runs and also pushed to
    the phone when `briefing.push` and notifications are on; none with the briefing off.
    A greeting whose turn never read the day (`daily_briefing`) is the brain's apology for a
    failure, so it is neither said nor pushed."""
    briefing = config.briefing
    if not briefing.enabled:
        return nullcontext()
    on_reply = (
        _phone_push(notifier, config.assistant.name)
        if briefing.push and notifier is not None
        else None
    )
    return BriefingTimer(
        briefing,
        request=lambda prompt: assistant.ask_aloud(
            prompt, on_reply=on_reply, required_tool=DAILY_BRIEFING
        ),
    )


def _phone_push(notifier: Notifier, title: str) -> Callable[[str], None]:
    """Sends what she said by herself to the phone (the notifier logs a failed push)."""

    def push(text: str) -> None:
        notifier.send(title, text, tags=[BRIEFING_TAG])

    return push


def _briefing(args: argparse.Namespace) -> int:
    """Print the briefing data the brain would get now, without the brain: the way to check the
    weather. Exit 1 when a part of it failed; the rest is printed all the same."""
    config = load_config(args.config)
    schedule = _schedule_store(config) if config.schedule.enabled else None
    result, is_error = _briefing_tools(config.briefing, schedule).run(DAILY_BRIEFING, {})
    briefing = json.loads(result)
    if is_error:
        print(f"{FAIL} {briefing['message']}")
        return 1
    print(f"Ringkasan hari ini: {briefing['date']}")
    if briefing["weather"] is not None:
        print(f"{OK} {_weather_text(briefing['weather'])}")
    else:
        print(f"{FAIL} Cuaca: {briefing['weather_problem']}")
    entries = briefing["schedule_today"]
    if briefing["schedule_problem"] is not None:
        print(f"{FAIL} Jadwal: {briefing['schedule_problem']}")
    elif schedule is None:
        print(f"{WARN} Jadwal mati (schedule.enabled: false)")
    elif not entries:
        print("Tidak ada jadwal hari ini.")
    else:
        print("Jadwal hari ini:")
        for entry in entries:
            print(f"  {entry['time']}  {entry['what']}")
    failed = briefing["weather"] is None or briefing["schedule_problem"] is not None
    return 1 if failed else 0


def _weather_text(weather: dict[str, object]) -> str:
    """`Cuaca di Bandung: berawan, 21.4°C; hari ini 18.6-27.9°C, ...`: the `daily_briefing`
    weather as the owner reads it."""
    return (
        f"Cuaca di {weather['place']}: {weather['condition']}, {weather['temperature_c']:g}°C; "
        f"hari ini {weather['min_c']:g}-{weather['max_c']:g}°C, "
        f"peluang hujan {weather['rain_chance']}%; besok pagi {weather['tomorrow_morning']}"
    )


# --- API cost monitor ------------------------------------------------------------------------


class _UsageRecorder:
    """Records what each Claude response used in the API cost ledger and, with a monthly
    budget, has Master told once at 80 % and once at 100 % of it.

    The brain gets `record` when it is built, before she can say anything, so `telling`
    connects how she tells Master for a block. Notices are only taken from the ledger, which
    marks them told, while one is connected: none is used up unsaid, and the next response
    gives it instead.
    """

    def __init__(self, ledger: UsageLedger, *, budget_usd: float | None, owner_call: str) -> None:
        self.ledger = ledger
        self._budget_usd = budget_usd
        self._owner_call = owner_call
        self._tell: Callable[[str], None] | None = None

    def record(self, model: str, tokens: TokenUsage) -> None:
        self.ledger.record(model, tokens)
        tell = self._tell
        if tell is None or self._budget_usd is None:
            return
        for notice in self.ledger.budget_notices(self._budget_usd, owner_call=self._owner_call):
            tell(notice)

    @contextmanager
    def telling(self, tell: Callable[[str], None]) -> Iterator[None]:
        """Hand each budget notice to `tell` until the block ends."""
        self._tell = tell
        try:
            yield
        finally:
            self._tell = None


def _usage_ledger(config: AppConfig) -> UsageLedger:
    """Her API cost records, in the file `usage.path` names, whatever the backend and whether
    the monitor is on or not; the configured prices go over the built-in ones."""
    usage = config.usage
    return UsageLedger(usage.path, prices=usage.prices, default_model=usage.default_model)


def _usage_recorder(config: AppConfig) -> _UsageRecorder | None:
    """The cost monitor of one `run` or `chat`: only with Claude (Ollama is free) and while
    `usage.enabled`."""
    if config.brain.backend != "claude" or not config.usage.enabled:
        return None
    return _UsageRecorder(
        _usage_ledger(config),
        budget_usd=config.usage.monthly_budget_usd,
        owner_call=config.assistant.owner_call,
    )


def _budget_told(
    usage: _UsageRecorder | None, tell: Callable[[str], None]
) -> AbstractContextManager[object]:
    """Budget notices go to `tell` while the block runs; there are none without the monitor."""
    if usage is None:
        return nullcontext()
    return usage.telling(tell)


def _budget_teller(
    config: AppConfig, assistant: Assistant, notifier: Notifier | None
) -> Callable[[str], None]:
    """Has her say a budget notice by herself and push it to the phone when notifications are
    on (the notifier logs a failed push).

    A notice comes while she thinks up the reply that crossed the line, so she says it once
    Master has had the chance to answer that reply (the follow-up window has closed).
    """

    def tell(notice: str) -> None:
        assistant.announce(notice, "surprised", wait_for_followup=True)
        if notifier is not None:
            # She is in the middle of a reply, which a push taking its whole timeout would hold up.
            threading.Thread(
                target=notifier.send,
                args=(config.assistant.name, notice),
                kwargs={"tags": [BUDGET_TAG]},
                name="budget-push",
                daemon=True,
            ).start()

    return tell


def _usage(args: argparse.Namespace) -> int:
    """Print what her Claude requests cost in one month, day by day, from the ledger alone:
    it works with any backend and with the monitor off."""
    config = load_config(args.config)
    now = datetime.now()
    year, month = args.month or (now.year, now.month)
    summary = _usage_ledger(config).month(year, month)
    if not summary.by_day:
        print("Belum ada catatan biaya.")
        return 0
    rate = config.usage.idr_per_usd
    header = ("TANGGAL", "PERMINTAAN", "INPUT", "OUTPUT", "CACHE TULIS", "CACHE BACA", "USD")
    if rate is not None:
        header += ("IDR",)
    rows = [_usage_row(f"{day.day:%d-%m-%Y}", day, rate) for day in summary.by_day]
    rows.append(_usage_row("Total", summary, rate))
    name = config.assistant.name
    print(f"Biaya API Claude {MONTHS_ID[month - 1]} {year}, dari catatan {name} sendiri:")
    print(_format_table((*header, ""), rows))
    if summary.estimated:
        print(
            "(perkiraan): sebagian respons datang dari model yang tidak ada di tabel harga, jadi "
            f"dihitung dengan harga {config.usage.default_model}."
        )
    return 0


def _usage_row(
    label: str, used: DayUsage | UsageSummary, idr_per_usd: float | None
) -> tuple[str, ...]:
    """One line of `waifu usage`: requests, tokens, the cost, and whether it is an estimate."""
    tokens = used.tokens
    counts = (used.requests, tokens.input, tokens.output, tokens.cache_write, tokens.cache_read)
    cells = [label, *map(str, counts), f"${used.cost_usd:.2f}"]
    if idr_per_usd is not None:
        cells.append(_rupiah(used.cost_usd * idr_per_usd))
    cells.append("(perkiraan)" if used.estimated else "")
    return tuple(cells)


def _rupiah(amount: float) -> str:
    """`Rp3.808`: whole rupiah, thousands separated by dots as written in Indonesia."""
    return f"Rp{round(amount):,}".replace(",", ".")


# --- Cyrene on the phone ---------------------------------------------------------------------


@contextmanager
def _phone_server(config: AppConfig, assistant: Assistant) -> Iterator[None]:
    """Serve her to the paired phone for the block, when `phone.enabled`.

    The server listens on 127.0.0.1 only; `tailscale serve` shares it with the owner's tailnet.
    One that cannot start (its port is taken) only costs the phone, never her voice.
    """
    phone = config.phone
    if not phone.enabled:
        yield
        return
    name = config.assistant.name
    try:
        server = PhoneServer(
            phone,
            config.avatar,
            PhoneAuth(phone.state_path),
            PhoneAnswers(assistant),
            owner_call=config.assistant.owner_call,
            assistant_name=name,
        )
    except PhoneError as exc:
        logger.warning("Phone server unavailable, going on without it: %s", exc)
        reason = str(exc).rstrip(".")
        print(f"{WARN} {name} di HP tidak tersedia: {reason}. {name} tetap berjalan tanpa HP.")
        yield
        return
    with server:
        print(_phone_served_text(name, phone, server.url))
        yield


def _phone_served_text(name: str, phone: PhoneConfig, url: str) -> str:
    if phone.host is None:
        return (
            f"{WARN} {name} di HP berjalan di {url}, tapi phone.host belum diisi, jadi HP belum "
            "bisa membukanya lewat Tailscale. Jalankan 'waifu phone setup'."
        )
    return f"{name} di HP: https://{phone.host}/ (pasangkan HP dengan 'waifu phone pair')"


def _phone_setup(args: argparse.Namespace) -> int:
    """Find Tailscale and this PC's tailnet name, then print what the owner does once: the
    `tailscale serve` command, the phone.host line, and the page's URL. Never changes Tailscale."""
    config = load_config(args.config)
    tailscale = _find_tailscale()
    if tailscale is None:
        print(f"{FAIL} {_tailscale_missing()}")
        return 1
    print(f"{OK} Tailscale: {tailscale.exe}")
    try:
        host = _tailscale_name(_tailscale_json(tailscale, "status", "--json"))
    except _TailscaleError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"{OK} Nama PC ini di Tailscale: {host}")
    print()
    print(
        f"1. Jalankan sekali di Command Prompt. Tailscale lalu membagikan {config.assistant.name} "
        "lewat HTTPS hanya ke perangkat di jaringan Tailscale-mu, juga setelah PC dinyalakan "
        "ulang:"
    )
    print(f"   {_serve_command(tailscale, config.phone.port)}")
    print("2. Isi bagian phone di config.yaml:")
    print("   phone:")
    print("     enabled: true")
    print(f"     host: {host}")
    print(
        "3. Jalankan ulang 'waifu run', lalu 'waifu phone pair', dan buka alamat ini di Chrome HP:"
    )
    print(f"   https://{host}/")
    print()
    print(
        "Perintah ini hanya membaca status Tailscale; 'tailscale serve' kamu jalankan sendiri. "
        f"Untuk berhenti membagikan: {tailscale.typed} serve --https={PHONE_HTTPS_PORT} off"
    )
    return 0


def _phone_pair(args: argparse.Namespace) -> int:
    """Make a pairing code and print it with its expiry; it is never logged or saved in clear."""
    config = load_config(args.config)
    phone = config.phone
    name = config.assistant.name
    # Before the code is made, so the time shown is never later than the code's own expiry.
    expires = datetime.now() + CODE_LIFETIME
    code = PhoneAuth(phone.state_path).new_code()
    print(f"Kode pasangan HP: {code}")
    print(
        f"Berlaku sampai jam {expires:%H.%M} untuk satu HP; kode baru menggantikan yang lama, dan "
        f"{MAX_TRIES} kali salah membuangnya."
    )
    if phone.host is None:
        print(
            f"{WARN} phone.host belum diisi: jalankan 'waifu phone setup' dulu supaya HP bisa "
            f"membuka {name}."
        )
    else:
        print(
            f"Buka https://{phone.host}/ di Chrome HP selama 'waifu run' berjalan, lalu "
            "masukkan kode ini."
        )
    if not phone.enabled:
        print(
            f"{WARN} phone.enabled masih false: isi phone.enabled: true di config.yaml, lalu "
            "jalankan ulang 'waifu run'."
        )
    return 0


def _phone_devices(args: argparse.Namespace) -> int:
    devices = PhoneAuth(load_config(args.config).phone.state_path).devices()
    if not devices:
        print("Belum ada HP yang dipasangkan. Pasangkan dengan 'waifu phone pair'.")
        return 0
    rows = [
        (
            device.id,
            device.name,
            f"{device.created:%d-%m-%Y %H:%M}",
            f"{device.last_used:%d-%m-%Y %H:%M}",
        )
        for device in devices
    ]
    print(_format_table(("ID", "NAMA", "DIPASANGKAN", "TERAKHIR DIPAKAI"), rows))
    return 0


def _phone_forget(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not PhoneAuth(config.phone.state_path).forget(args.device_id):
        print(
            f"{FAIL} Tidak ada HP dengan id {args.device_id}. Lihat daftarnya dengan "
            "'waifu phone devices'."
        )
        return 1
    print(
        f"{OK} HP {args.device_id} dilupakan: HP itu tidak bisa membuka {config.assistant.name} "
        "lagi sampai dipasangkan ulang."
    )
    return 0


class _TailscaleError(Exception):
    """The Tailscale CLI could not tell what Cyrene needs; the message is Indonesian."""


@dataclass(frozen=True)
class _Tailscale:
    exe: Path  # the CLI that was found
    typed: str  # how the owner types it in Command Prompt


def _find_tailscale() -> _Tailscale | None:
    """The Tailscale CLI on the PATH, else where its Windows installer puts it; None without it."""
    on_path = shutil.which(TAILSCALE_COMMAND, path=TAILSCALE_SEARCH_PATH)
    if on_path is not None:
        return _Tailscale(Path(on_path), TAILSCALE_COMMAND)
    if TAILSCALE_INSTALLED.is_file():
        return _Tailscale(TAILSCALE_INSTALLED, subprocess.list2cmdline([str(TAILSCALE_INSTALLED)]))
    return None


def _tailscale_missing() -> str:
    return (
        f"Tailscale belum terpasang di PC ini (tidak ada di PATH atau di {TAILSCALE_INSTALLED}). "
        f"Pasang dari {TAILSCALE_DOWNLOAD} dan login, lalu jalankan 'waifu phone setup' (lihat "
        "README, bagian Cyrene di HP)"
    )


def _run_tailscale(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Runs one Tailscale CLI command with its output captured; only commands that read."""
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TAILSCALE_TIMEOUT_S,
        check=False,
    )


TAILSCALE_RUNNER: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_tailscale


def _tailscale_json(tailscale: _Tailscale, *arguments: str) -> object:
    """What `tailscale <arguments>` prints as JSON; _TailscaleError when that cannot be had."""
    shown = " ".join([TAILSCALE_COMMAND, *arguments])
    try:
        finished = TAILSCALE_RUNNER([str(tailscale.exe), *arguments])
    except subprocess.TimeoutExpired:
        raise _TailscaleError(
            f"'{shown}' tidak menjawab dalam {TAILSCALE_TIMEOUT_S:g} detik"
        ) from None
    except OSError as exc:
        raise _TailscaleError(f"Tailscale CLI tidak bisa dijalankan ({exc})") from exc
    if finished.returncode != 0:
        complaints = [line.strip() for line in (finished.stderr or "").splitlines() if line.strip()]
        detail = complaints[-1] if complaints else f"kode keluar {finished.returncode}"
        raise _TailscaleError(
            f"'{shown}' gagal: {detail}. Pastikan aplikasi Tailscale di PC ini berjalan"
        )
    try:
        return json.loads(finished.stdout)
    except ValueError:
        raise _TailscaleError(f"Jawaban '{shown}' bukan JSON yang bisa dibaca") from None


def _tailscale_name(status: object) -> str:
    """This PC's *.ts.net name in `tailscale status --json`, without its final dot.

    _TailscaleError while Tailscale is not connected, or without a MagicDNS name.
    """
    state = status.get("BackendState") if isinstance(status, dict) else None
    if state != "Running":
        raise _TailscaleError(
            f"Tailscale belum tersambung (status: {state or 'tidak diketahui'}). Buka aplikasi "
            "Tailscale di PC ini dan login dengan akun yang sama seperti di HP"
        )
    me = status.get("Self")
    dns_name = me.get("DNSName") if isinstance(me, dict) else None
    host = dns_name.rstrip(".").lower() if isinstance(dns_name, str) else ""
    if not TAILSCALE_HOST_PATTERN.match(host):
        raise _TailscaleError(
            "Nama PC ini di Tailscale (*.ts.net) tidak terbaca. Nyalakan MagicDNS di admin "
            "console Tailscale (halaman DNS), lalu ulangi"
        )
    return host


def _serve_command(tailscale: _Tailscale, port: int) -> str:
    """The command the owner runs once so that Tailscale shares the phone server over HTTPS."""
    return f"{tailscale.typed} serve --bg --https={PHONE_HTTPS_PORT} http://{LOOPBACK}:{port}"


def _served_host(serve: object, port: int) -> str | None:
    """The *.ts.net name whose HTTPS port 443 passes to this PC's `port` in
    `tailscale serve status --json`, or None.

    A serve started without --bg (under "Foreground") ends with its command, so it does not count.
    """
    sites = serve.get("Web") if isinstance(serve, dict) else None
    if not isinstance(sites, dict):
        return None
    for host_port, site in sites.items():
        host, _, https_port = str(host_port).rpartition(":")
        handlers = site.get("Handlers") if isinstance(site, dict) else None
        root = handlers.get("/") if isinstance(handlers, dict) else None
        target = root.get("Proxy") if isinstance(root, dict) else None
        if https_port == str(PHONE_HTTPS_PORT) and _is_phone_server(target, port):
            return host
    return None


def _funnel_on(serve: object, host: str) -> bool:
    """Whether `tailscale serve status --json` has Funnel on for `host`'s HTTPS port 443.

    Funnel opens everything that host and port serve to the whole internet, every path
    included."""
    funnel = serve.get("AllowFunnel") if isinstance(serve, dict) else None
    return isinstance(funnel, dict) and funnel.get(f"{host}:{PHONE_HTTPS_PORT}") is True


def _is_phone_server(target: object, port: int) -> bool:
    """Whether a serve proxy target is http://127.0.0.1:<port> or http://localhost:<port>."""
    if not isinstance(target, str):
        return False
    url = urllib.parse.urlsplit(target)
    try:
        target_port = url.port
    except ValueError:  # a port that is not a number from 0 to 65535
        return False
    return url.scheme == "http" and url.hostname in (LOOPBACK, "localhost") and target_port == port


def _phone_checks(config: AppConfig) -> Iterator[Check]:
    """The phone server, Tailscale (its CLI, its login, and its serve of the server's port, never
    through Funnel), phone.host, the avatar's model, and the paired phones. Asks Tailscale only
    about its state, never changes it."""
    phone = config.phone
    label = f"{config.assistant.name} di HP"
    if not phone.enabled:
        yield WARN, f"{label} mati (phone.enabled: false)"
        return
    yield OK, f"{label}: server di {LOOPBACK}:{phone.port} selama 'waifu run'"
    tailscale = _find_tailscale()
    tailnet_host = None
    if tailscale is None:
        yield FAIL, _tailscale_missing()
    else:
        yield OK, f"Tailscale: {tailscale.exe}"
        try:
            tailnet_host = _tailscale_name(_tailscale_json(tailscale, "status", "--json"))
        except _TailscaleError as exc:
            yield FAIL, str(exc)
        else:
            yield OK, f"Tailscale tersambung; nama PC ini {tailnet_host}"
        yield _serve_check(tailscale, phone.port)
    yield _phone_host_check(phone.host, tailnet_host)
    if config.avatar.model is None:
        yield WARN, PHONE_WITHOUT_MODEL_TEXT
    yield _paired_phones_check(phone.state_path)


def _serve_check(tailscale: _Tailscale, port: int) -> Check:
    try:
        serve = _tailscale_json(tailscale, "serve", "status", "--json")
    except _TailscaleError as exc:
        return FAIL, str(exc)
    host = _served_host(serve, port)
    if host is None:
        return FAIL, (
            f"tailscale serve belum membagikan {LOOPBACK}:{port} lewat HTTPS port "
            f"{PHONE_HTTPS_PORT}. Jalankan sekali di Command Prompt: "
            f"{_serve_command(tailscale, port)}"
        )
    if _funnel_on(serve, host):
        return FAIL, (
            f"Tailscale Funnel menyala untuk {host}: server HP terbuka ke seluruh internet, "
            "bukan hanya ke jaringan Tailscale-mu. Matikan sekali di Command Prompt: "
            f"{tailscale.typed} funnel --https={PHONE_HTTPS_PORT} off, lalu bagikan lagi dengan: "
            f"{_serve_command(tailscale, port)}"
        )
    return OK, f"tailscale serve membagikan https://{host}/ ke {LOOPBACK}:{port}"


def _phone_host_check(host: str | None, tailnet_host: str | None) -> Check:
    """phone.host, the only outside name the phone server answers, against this PC's name."""
    if host is None:
        return FAIL, (
            "phone.host belum diisi; jalankan 'waifu phone setup' lalu isi baris host yang "
            "ditampilkannya"
        )
    if tailnet_host is not None and host != tailnet_host:
        return FAIL, (
            f"phone.host {host} bukan nama PC ini di Tailscale; ganti dengan host: {tailnet_host}"
        )
    return OK, f"phone.host: {host}"


def _paired_phones_check(path: Path) -> Check:
    try:
        count = len(PhoneAuth(path).devices())  # moves a broken file aside, as the server would
    except OSError as exc:
        return FAIL, f"File HP terpasang {path} tidak bisa dibaca: {exc}"
    if count == 0:
        return WARN, "Belum ada HP yang dipasangkan; jalankan 'waifu phone pair'"
    return OK, f"HP terpasang: {count} (lihat 'waifu phone devices')"


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
    yield from _routine_checks(config)
    yield _voiceprint_check(config)
    yield _brain_check(config)
    yield _vision_check(config)
    yield _memory_check(config)
    yield _schedule_check(config)
    yield _briefing_check(config.briefing)
    yield _usage_check(config)
    yield from _voice_package_checks(config)
    yield _microphone_check(config)
    yield from _anime_voice_checks(config)
    yield from _avatar_checks(config)
    yield from _gacha_checks(config)
    yield _notify_check(config)
    yield from _phone_checks(config)


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
    for device_config in config.devices:
        for slug, target in (device_config.apps or {}).items():
            yield _app_check(slug, target)


def _app_check(slug: str, target: str) -> Check:
    """Whether an app's program or shortcut is on disk, as `open_<slug>` will look for it, and
    whether a `.url` shortcut opens a launcher URL: Windows opens whatever it names.

    A launcher URL can only be tried by opening it, and `check` never opens anything.
    """
    if target.lower().startswith(APP_URL_SCHEMES):
        return OK, f"Aplikasi {slug}: {target} (URL, tidak dicek)"
    path = Path(target)
    if not path.exists():
        return FAIL, f"Aplikasi {slug}: file tidak ditemukan ({target})"
    if path.suffix.lower() == ".url":
        url = _internet_shortcut_url(path)
        if url is None:
            return FAIL, f"Aplikasi {slug}: alamat di shortcut ini tidak bisa dibaca ({target})"
        if not url.lower().startswith(APP_URL_SCHEMES):
            return FAIL, (
                f"Aplikasi {slug}: shortcut ini membuka {url}, bukan URL steam:// atau "
                f"com.epicgames.launcher:// ({target})"
            )
    return OK, f"Aplikasi {slug}: {target}"


def _internet_shortcut_url(path: Path) -> str | None:
    """The `URL=` of a `.url` shortcut (an INI file), or None when it has none or is unreadable."""
    shortcut = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        shortcut.read_string(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, configparser.Error):
        return None
    return shortcut.get("InternetShortcut", "URL", fallback=None) or None


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


def _routine_checks(config: AppConfig) -> Iterator[Check]:
    """Whether every step of each routine could run now, by the rule the registry uses.

    Like the device checks, always against the real devices, and nothing is executed.
    """
    if not config.routines:
        return
    registry = build_registry(config)
    for name, routine in config.routines.items():
        try:
            problems = routine_problems(registry, routine)
        except IrCodeStoreError as exc:
            yield FAIL, f"Rutinitas {name}: kode IR tidak bisa dibaca ({exc})"
            continue
        if problems:
            yield FAIL, f"Rutinitas {name}: {' '.join(problems)}"
        else:
            yield OK, f"Rutinitas {describe_routine(name, routine)}"


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


def _vision_check(config: AppConfig) -> Check:
    """Whether she can look at the screen: only with Claude, and only when vision is on."""
    if not config.vision.enabled:
        return WARN, "Melihat layar mati"
    if config.brain.backend != "claude":
        return WARN, "Melihat layar: tidak tersedia dengan Ollama"
    return OK, "Melihat layar: aktif (Claude)"


def _memory_check(config: AppConfig) -> Check:
    """How many notes she has, and where a broken memory file was moved, if one was."""
    if not config.memory.enabled:
        return WARN, "Ingatan mati (memory.enabled: false)"
    path = config.memory.path
    try:
        count = len(_memory_store(config).facts())  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File ingatan {path} tidak bisa dibaca: {exc}"
    status = f"Ingatan: {count} catatan ({path})"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file ingatan yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(cara memulihkannya ada di README, bagian Ingatan Cyrene)"
        )
    return OK, status


def _schedule_check(config: AppConfig) -> Check:
    """How many entries her schedule holds, and where a broken schedule file was moved, if one
    was."""
    if not config.schedule.enabled:
        return WARN, "Jadwal mati (schedule.enabled: false)"
    path = config.schedule.path
    try:
        count = len(_schedule_store(config).entries())  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File jadwal {path} tidak bisa dibaca: {exc}"
    status = f"Jadwal: {count} entri ({path})"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file jadwal yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(cara memulihkannya ada di README, bagian Jadwal, pengingat, dan rutinitas)"
        )
    return OK, status


def _briefing_check(briefing: BriefingConfig) -> Check:
    """When she greets by herself and where the weather is for (coordinates win over the city,
    which still names the place); Open-Meteo is never asked."""
    if not briefing.enabled:
        return WARN, "Sapaan pagi mati"
    if briefing.latitude is not None:
        coordinates = f"{briefing.latitude}, {briefing.longitude}"
        place = coordinates if briefing.city is None else f"{briefing.city} ({coordinates})"
    elif briefing.city is not None:
        place = briefing.city
    else:
        return WARN, "Sapaan pagi: cuaca belum diatur (isi briefing.city)"
    hours = f"{briefing.time:%H:%M}-{briefing.until:%H:%M}"
    return OK, f"Sapaan pagi: jam {hours}, cuaca untuk {place}"


def _usage_check(config: AppConfig) -> Check:
    """What her Claude requests cost this month by her own records, and where a broken usage
    file was moved, if one was; the API is never asked."""
    if not config.usage.enabled:
        return WARN, "Pencatatan biaya mati"
    if config.brain.backend != "claude":
        return WARN, "Biaya API dicatat hanya dengan backend Claude"
    path = config.usage.path
    now = datetime.now()
    try:
        month = _usage_ledger(config).month(now.year, now.month)  # moves a broken file aside
    except ConfigError as exc:  # a default model without prices: `run` would refuse to start
        return FAIL, f"Biaya API: {exc}"
    except OSError as exc:
        return FAIL, f"File biaya API {path} tidak bisa dibaca: {exc}"
    status = f"Biaya API: ${month.cost_usd:.2f} bulan ini ({month.requests} permintaan)"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file biaya yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Biaya API Claude)"
        )
    return OK, status


def _set_aside_files(path: Path) -> list[Path]:
    """The copies a broken memory, schedule, or usage file was renamed to
    (`<name>.corrupt-<time>`), oldest first.

    They stay until the owner repairs or deletes them, so `check` points at them every time.
    """
    prefix = f"{path.name}.corrupt-"
    try:
        return sorted(copy for copy in path.parent.iterdir() if copy.name.startswith(prefix))
    except OSError:  # no folder yet: nothing was ever saved there
        return []


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


def _gacha_checks(config: AppConfig) -> Iterator[Check]:
    """The cookie (key names only), genshin.py, and each game's history cache; no network."""
    gacha = config.gacha
    if not gacha.enabled:
        yield WARN, "Teman main game mati (gacha.enabled: false); 'waifu gacha' tetap bisa dipakai"
        return
    if gacha.cookie is None:
        yield (
            WARN,
            (
                "Cookie HoYoLAB belum diisi (gacha.cookie): status, pengingat, dan check-in mati; "
                "riwayat pull tetap terbaca dari cache game"
            ),
        )
    elif "cookie_token_v2" in cookie_names(gacha.cookie.get_secret_value()):
        yield OK, "Cookie HoYoLAB ada, termasuk cookie_token_v2 untuk riwayat pull"
    else:
        yield (
            WARN,
            (
                "Cookie HoYoLAB ada tanpa cookie_token_v2: riwayat pull butuh riwayat di game "
                "dibuka dalam sehari terakhir"
            ),
        )
    if find_spec("genshin") is None:
        yield FAIL, 'Paket genshin belum terpasang. Jalankan: pip install -e ".[dev]"'
    for game_id in gacha.games:
        game = GAMES[game_id]
        try:
            cached = find_cached_gacha_url(game, GAME_LOGS_DIR)
        except GachaError as error:
            yield WARN, f"Riwayat {game.short}: {error.message}"
            continue
        if cached is not None:
            yield OK, f"Riwayat {game.short}: alamat riwayat ada di cache game"
        else:
            yield (
                WARN,
                (
                    f"Riwayat {game.short}: belum ada di cache game; buka riwayat {game.pull_word} "
                    "di game sekali"
                ),
            )


def _notify_check(config: AppConfig) -> Check:
    notify = config.notify
    if not notify.enabled or notify.topic is None:
        return WARN, "Notifikasi HP mati (notify.enabled: false)"
    topic = f"{notify.topic.get_secret_value()[:4]}…"
    return OK, f"Notifikasi HP lewat {notify.server}, topik {topic}; uji dengan: waifu notify-test"


def _avatar_checks(config: AppConfig) -> Iterator[Check]:
    """The engine and its model files, the window's program, and whether the port is free.

    Never starts anything, and never loads a model or the Cubism Core.
    """
    avatar = config.avatar
    if not avatar.enabled:
        name = config.assistant.name
        yield WARN, f"Avatar 3D mati (avatar.enabled: false); {name} berjalan tanpa avatar"
        return
    yield OK, f"Mesin avatar: {AVATAR_ENGINES[avatar.engine]} (avatar.engine: {avatar.engine})"
    if avatar.engine == "live2d":
        yield from _live2d_checks(avatar)
    else:
        yield _path_check("Model VRM avatar", avatar.model, "avatar.model")
    yield _avatar_window_check(avatar)
    yield _avatar_port_check(avatar)
    yield from _reaction_lines_checks(config)


def _reaction_lines_checks(config: AppConfig) -> Iterator[Check]:
    """A warning when reaction lines still say "Master" to an owner she calls otherwise: such
    a line is said as written, since only `{owner}` is filled."""
    owner_call = config.assistant.owner_call
    interactions = config.avatar.interactions
    if owner_call == "Master" or not interactions.enabled:
        return
    kinds = [
        kind
        for kind, lines in interactions.lines.items()
        if any(_MASTER_WORD.search(line) for line in lines)
    ]
    if kinds:
        yield (
            WARN,
            (
                'Kalimat reaksi avatar masih menyebut "Master", padahal assistant.owner_call: '
                f"{owner_call} (avatar.interactions.lines: {', '.join(kinds)}); ganti dengan "
                '"{owner}" atau hapus jenisnya untuk memakai bawaannya'
            ),
        )


def _live2d_checks(avatar: AvatarConfig) -> Iterator[Check]:
    """The model3.json, the moc3 and textures it names, and the owner's Cubism Core."""
    model_check = _path_check("Model Live2D avatar", avatar.model, "avatar.model")
    yield model_check
    if model_check[0] == OK and avatar.model is not None:
        yield _live2d_files_check(avatar.model)
    core_mark, core_text = _path_check(
        "Cubism Core Live2D", avatar.live2d_core, "avatar.live2d_core"
    )
    if core_mark == OK:
        yield OK, f"{core_text} ({CUBISM_CORE_STAYS_OUT})"
    else:
        yield FAIL, f"{core_text}. Cara memasang: {CUBISM_CORE_SETUP}; {CUBISM_CORE_STAYS_OUT}"


def _live2d_files_check(model3: Path) -> Check:
    """Whether the moc3 and every texture the model3.json names are in the model's folder.

    The avatar page loads them from that folder only, so a file outside it counts as missing.
    """
    try:
        moc, textures = _model3_files(model3)
    except (OSError, ValueError) as exc:
        return FAIL, f"{model3.name} bukan model Live2D yang bisa dibaca: {exc}"
    folder = model3.parent.resolve()
    missing = [name for name in [moc, *textures] if not _is_file_under(folder, name)]
    if missing:
        return FAIL, (
            f"File model Live2D tidak ditemukan di {model3.parent}: {', '.join(missing)} "
            f"(disebut {model3.name}; salin seluruh folder modelnya)"
        )
    return OK, f"File model Live2D: {moc} dan {len(textures)} tekstur"


def _model3_files(model3: Path) -> tuple[str, list[str]]:
    """The moc3 and the textures a model3.json names; ValueError when it names none."""
    data = json.loads(model3.read_text(encoding="utf-8-sig"))
    references = data.get("FileReferences") if isinstance(data, dict) else None
    if isinstance(references, dict):
        moc, textures = references.get("Moc"), references.get("Textures")
        if (
            isinstance(moc, str)
            and isinstance(textures, list)
            and all(isinstance(texture, str) for texture in textures)
        ):
            return moc, textures
    raise ValueError("FileReferences tidak menyebut Moc dan Textures")


def _is_file_under(folder: Path, reference: str) -> bool:
    """Whether a model3.json `reference` names a file inside `folder` as the page resolves it.

    The page resolves it as a URL, where a backslash is a slash. Anything else (a UNC, drive,
    absolute, or URL name) is refused before it is resolved (`file_under`): a downloaded model
    must not make `waifu check` contact another computer.
    """
    return file_under(folder, reference.replace("\\", "/")) is not None


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
