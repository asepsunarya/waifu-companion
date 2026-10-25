"""Command-line entry point: `waifu <command>` (also `python -m waifu`).

Each subcommand assembles what it needs from config.yaml through the
module-level builders imported below, so tests can swap any of them for a
fake. Output for the owner is Indonesian; logs go to stderr in English.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import http.client
import io
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import import_module
from importlib.util import find_spec
from itertools import count, groupby
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, Literal, get_args

import httpx
import numpy as np
from pydantic import SecretStr, ValidationError

from waifu import autostart, memory
from waifu.alarm import AlarmBell
from waifu.app import CARD_AGAIN, NO_CARD, Assistant, AudioPlayer, PhoneAnswers, split_reply
from waifu.app_reminders import AppReminderStore, AppReminderTeller
from waifu.autostart import RunKey, WindowsRunKey
from waifu.avatar.bus import STATES as AVATAR_STATES
from waifu.avatar.bus import AvatarBus
from waifu.avatar.emotion import EMOTIONS, NEUTRAL, split_emotion
from waifu.avatar.interactions import InteractionHandler
from waifu.avatar.lipsync import SHAPE_CENTRE_HZ
from waifu.avatar.server import AvatarError, AvatarServer, file_under
from waifu.avatar.window import AvatarWindow
from waifu.brain.alarm_tools import AlarmTools
from waifu.brain.app_reminder_tools import AppReminderTools
from waifu.brain.attachments import CameraTurn, TurnImages
from waifu.brain.base import Brain, ToolCallRecord
from waifu.brain.clipboard_tools import ClipboardTools
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.factory import build_brain, confirmation_gate
from waifu.brain.finance_tools import FinanceTools
from waifu.brain.focus_tools import FocusTools
from waifu.brain.guest_tools import GuestTools
from waifu.brain.japanese_tools import JapaneseTools
from waifu.brain.journal_tools import JournalTools
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.music_tools import MusicTools
from waifu.brain.network_tools import NetworkTools
from waifu.brain.offers_tools import OffersTools
from waifu.brain.pc_tools import PcTools
from waifu.brain.persona import WEEKDAYS_ID
from waifu.brain.play_time_tools import PlayTimeTools
from waifu.brain.prayer_tools import PrayerTools
from waifu.brain.recap_tools import DailyRecapTools
from waifu.brain.schedule_tools import ScheduleTools
from waifu.brain.screen_tools import ScreenTools
from waifu.brain.todo_tools import TodoTools
from waifu.brain.tools import ToolProvider, logged_result
from waifu.brain.usage_tools import UsageTools
from waifu.brain.voice_volume_tools import VoiceVolumeTools
from waifu.brain.weather_tools import (
    PARTS,
    ForecastError,
    Period,
    WeatherForecastTools,
    period_of,
    periods,
)
from waifu.break_watch import BreakWatcher
from waifu.briefing import DAILY_BRIEFING, BriefingTimer, BriefingTools
from waifu.card import Card, is_card_request
from waifu.config import (
    APP_URL_SCHEMES,
    TAILSCALE_HOST_PATTERN,
    AppConfig,
    AudioConfig,
    AvatarConfig,
    BriefingConfig,
    ConfigError,
    DeviceConfig,
    FallbackTtsConfig,
    GameId,
    GameModeConfig,
    NetworkConfig,
    NetworkHostsConfig,
    NotifyConfig,
    OllamaConfig,
    PhoneConfig,
    RoutineStep,
    RvcConfig,
    VadConfig,
    VersionNoticeConfig,
    Weekday,
    cookie_names,
    load_config,
)
from waifu.config_patch import Change, apply_changes, masked, snippet
from waifu.devices.base import DeviceError, DeviceResult
from waifu.devices.ir_codes import COMMAND_PATTERN, IrCodeStoreError
from waifu.devices.ir_device import IrDevice
from waifu.devices.registry import DeviceRegistry, build_registry
from waifu.finance.tepira import TepiraClient
from waifu.finance.values import (
    DEFAULT_TRANSACTIONS,
    MAX_GOAL_QUERY_CHARS,
    MAX_TRANSACTIONS,
    UNREADABLE,
    WIB,
    FinanceData,
    FinanceError,
    FinanceSource,
)
from waifu.focus import FocusStore, focus_totals
from waifu.focus_timer import FocusTimer
from waifu.gacha.code_store import CodeStore
from waifu.gacha.code_tools import RECENT_CODES, CodeTools
from waifu.gacha.code_watcher import OUTCOME_TEXT, CodeWatcher, ReadCodes
from waifu.gacha.codes import CODES_SOURCE, FINAL_OUTCOMES, CodesError, RedeemCode, read_codes
from waifu.gacha.game_data import (
    GAME_MODES,
    MODE_GAMES,
    Banner,
    ChallengeWindow,
    Endgame,
    GameCalendar,
    GameDataSource,
    GameEvent,
    is_running,
)
from waifu.gacha.game_data_tools import GameDataTools
from waifu.gacha.games import GAMES, Game, next_daily_reset
from waifu.gacha.genshin_impact import GenshinCharacter
from waifu.gacha.history import (
    BannerStats,
    GachaHistory,
    GachaLogApi,
    GachaStore,
    TopPull,
    find_cached_gacha_url,
)
from waifu.gacha.hoyolab import (
    REDEEM_CODES_AT_MOST,
    REDEEM_GAP_S,
    GachaError,
    GameStatus,
    HoyolabClient,
    preferred_uids,
)
from waifu.gacha.income import GameIncome, income_months, month_text
from waifu.gacha.income_tools import IncomeTools
from waifu.gacha.planner import CalendarReader
from waifu.gacha.reminders import duration_text
from waifu.gacha.starrail import (
    ENDGAME_MODES,
    HsrCalendar,
    HsrChallengeWindow,
    HsrCharacter,
    HsrEndgame,
    HsrEvent,
    HsrWarp,
)
from waifu.gacha.tools import GachaTools
from waifu.gacha.watcher import GachaWatcher
from waifu.gacha.weekly import (
    WEEK,
    WEEKLY_REPORT,
    GameWeek,
    WeeklyReportTimer,
    WeeklyReportTools,
)
from waifu.gacha.zenless import EVENT_REWARD, ZzzAgent
from waifu.game_mode import GameMode
from waifu.gate import WakeGate
from waifu.guest import GuestMode
from waifu.hotkey_actions import HotkeyActions
from waifu.hotkeys import (
    ERROR_HOTKEY_ALREADY_REGISTERED,
    Hotkey,
    HotkeyStatus,
    HotkeyThread,
    Registration,
    read_status,
    write_status,
)
from waifu.input_idle import read_input_idle
from waifu.japanese import (
    JapaneseStore,
    Word,
    learn_word_of_the_day,
    lesson_counts,
    load_words,
    word_of_the_day,
)
from waifu.journal import JOURNAL_READ, JournalError, JournalStore, JournalTimer
from waifu.lexicon import BUILT_IN_READINGS, Lexicon, LexiconError
from waifu.listen_pause import ListenPause
from waifu.memory import MemoryStore, memory_prompt_section
from waifu.music import MusicService, open_in_browser, open_link, search_link, youtube_link
from waifu.network import ADVICE, MEANINGS, Finding, NetworkCheck, NetworkState, Status
from waifu.network_ask import NetworkAnswers
from waifu.network_history import NetworkRecord, NetworkStore, outage_totals
from waifu.network_probe import NetworkProbe, Reading
from waifu.network_watch import NetworkWatcher
from waifu.notify import Notifier, build_notifier
from waifu.offers.checks import official_link
from waifu.offers.errors import OffersError
from waifu.offers.games import GAMERPOWER, GAMERPOWER_PLATFORMS, read_free_games
from waifu.offers.store import OffersStore
from waifu.offers.values import AiOffer, GameGiveaway
from waifu.offers.watcher import OffersWatcher, ReadGames, ScanAiOffers
from waifu.pc_health import CpuLoad, Drive, GpuStatus, PcSnapshot, gpu_status, read_snapshot
from waifu.pc_watch import PcWatcher
from waifu.phone.actions import QuickActions
from waifu.phone.auth import CODE_LIFETIME, MAX_TRIES, PhoneAuth
from waifu.phone.desk import PC_WHERE, DeskServer
from waifu.phone.server import LOOPBACK, PhoneError, PhoneServer
from waifu.play_time import (
    PERIODS,
    PlayRecord,
    PlayTimeStore,
    PlayTotal,
    period_window,
    play_totals,
    played_seconds,
    playing_now,
    whole_minutes,
    wib_now,
)
from waifu.play_watch import ClosedGames, PlayTimeWatcher
from waifu.prayer import PrayerError, PrayerPlace
from waifu.prayer_times import PRAYERS, prayer_times
from waifu.prayer_watch import PrayerWatcher
from waifu.processes import running_executables
from waifu.rain_watch import RainWatcher, RainWindow, rain_window, raining_now, spoken_hour
from waifu.recap import (
    DailyRecap,
    FocusSeconds,
    GamePulls,
    OutageDay,
    Planned,
    PlayDay,
    RecapError,
    StoredPulls,
    TasksDay,
    WordsDay,
    recap_day,
)
from waifu.routines import describe_routine, describe_step, routine_problems, run_routine
from waifu.schedule import (
    Action,
    AlarmAction,
    DeviceAction,
    Entry,
    Reminder,
    Repeat,
    ScheduleError,
    ScheduleStore,
)
from waifu.scheduler import MONTHS_ID, SCHEDULE_TAG, Scheduler
from waifu.screen import capture_screen
from waifu.single_instance import InstanceLock
from waifu.todo import Task, TodoError, TodoStore
from waifu.tuya_import import (
    CATEGORY_KINDS,
    CloudDevice,
    TuyaImportError,
    TuyaTarget,
    read_cloud_devices,
    suggest,
    tuya_changes,
    tuya_targets,
)
from waifu.tuya_import import PLACEHOLDER as TUYA_PLACEHOLDER
from waifu.usage import DayUsage, TokenUsage, UsageLedger, UsageSummary, cost_usd
from waifu.version import (
    INSTALL_COMMAND,
    REPO_ROOT,
    CodeVersion,
    Release,
    RunningVersion,
    dependencies_changed,
    newer_releases,
    process_alive,
    read_code_version,
    read_running,
    write_running,
)
from waifu.version_watch import VersionWatcher
from waifu.voice.audio import (
    MicrophoneError,
    MicStream,
    Player,
    list_devices,
    restart_portaudio,
)
from waifu.voice.diagnosis import UtteranceLog
from waifu.voice.hotwords import Vocabulary
from waifu.voice.mic_check import (
    InputDevice,
    Levels,
    input_devices,
    level_bar,
    measure,
    same_device_name,
    windows_default_input,
)
from waifu.voice.mic_source import DeviceList, MicSource, windows_input_names
from waifu.voice.nod import NodBank, retry_after_s
from waifu.voice.panel import VoicePanel
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
from waifu.voice.volume import LEVELS, NORMAL, LevelHold, VolumePlayer
from waifu.voice.windows_voices import (
    WindowsVoice,
    WindowsVoiceError,
    choose_voice,
    language_name,
    list_windows_voices,
    missing_voice_hint,
)
from waifu.voice_level import VoiceLevel
from waifu.voice_override import VoiceOverride, VoiceOverrideStore
from waifu.weather import Location, WeatherClient, WeatherError

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger(__name__)

OK, FAIL, WARN = "✓", "✗", "!"
Check = tuple[str, str]
# How `waifu voices` names the speech APIs and genders Windows reports.
_VOICE_APIS = {"onecore": "OneCore", "sapi": "SAPI desktop"}
_GENDERS = {"female": "perempuan", "male": "laki-laki"}

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
MIC_CHECK_SECONDS = 3.0  # how long `waifu mic` listens to each microphone
# How long a line about the mic she switched to may wait while a focus block or a game holds
# what she says by herself: later the mic may have changed again, and the Suara page tells.
MIC_NOTICE_S = 60
NO_MIC_FOUND = (
    "Windows tidak menemukan mikrofon. Sambungkan mic atau headset, lalu cek Windows Settings, "
    "Privacy & security, Microphone."
)
NO_MIC_HEARD = (
    "Tidak ada mic yang mendengar suara sekeras yang dibutuhkan Cyrene. Dekatkan mic, naikkan "
    "volume rekamannya di Windows (Settings, System, Sound, Input), lalu jalankan '{command}' "
    "lagi."
)

DEFAULT_CONFIG = Path("config.yaml")
RUN_LOCK = Path("data") / "run.lock"
# What the running `waifu run` started with, for `check` and `waifu version`; next to the lock.
RUNNING_FILE = Path("data") / "running.json"
# Which global hotkeys the running `waifu run` registered, for `check`; only while it runs.
HOTKEYS_FILE = Path("data") / "hotkeys.json"
# What each global hotkey does, as `check` names it.
HOTKEY_ACTION_TEXT = {
    "talk": "bicara",
    "stop": "potong",
    "repeat": "ulangi",
    "quiet": "berhenti mendengarkan",
    "translate": "artinya apa",
}
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
# `waifu avatar` without --demo and `waifu tombol` only wait; Ctrl+C ends a sleep at once.
AVATAR_WAIT_S = 60.0
# What gives `waifu tombol` its buttons.
QUICK_PAGE_HINT = (
    "tambahkan lampu, colokan, perangkat IR, atau rutinitas di config.yaml, atau periksa "
    "phone.quick_actions"
)
# `say` and the login greeting speak right after opening the window; a speak event only
# reaches pages already connected, so they give the loading page this long to connect.
AVATAR_VIEWER_WAIT_S = 5.0
NOTIFY_OFF = (
    "Notifikasi HP mati. Isi notify.topic lalu notify.enabled: true di config.yaml "
    "(lihat README, bagian Notifikasi ke HP)."
)
NOTIFY_TEST_MESSAGE = "Tes notifikasi dari {name}. Kalau ini muncul di HP, notifikasi sudah siap."
NOTIFY_TAG = "video_game"  # ntfy shows it as a game controller emoji
# `waifu setup`: its steps in the order `all` runs them, and what Master may answer.
SETUP_STEPS = ("mic", "tuya", "ir", "notify")
SETUP_TITLES = {
    "mic": "Mikrofon",
    "tuya": "Perangkat Tuya",
    "ir": "IR blaster",
    "notify": "Notifikasi HP",
}
YES_WORDS = frozenset({"y", "ya", "iya", "yes", "ok", "oke"})
NO_WORDS = frozenset({"n", "no", "tidak", "ga", "gak", "nggak", "enggak", "t"})
SKIP_WORD = "l"  # lewati
StepOutcome = Literal["written", "failed", "unchanged"]
SETUP_OUTCOME_TEXT: dict[StepOutcome, str] = {
    "written": "ditulis ke config.yaml",
    "unchanged": "tidak diubah",
    "failed": "gagal; ubah sendiri dengan potongan di atas",
}
# What tinytuya's wizard writes into the folder it runs in (tinytuya/core/const.py); the first
# and the last hold the cloud credentials (tuya-raw.json also every local key).
TUYA_DEVICE_FILE = "devices.json"
TUYA_SNAPSHOT_FILE = "snapshot.json"
TUYA_CREDENTIAL_FILES = ("tinytuya.json", "tuya-raw.json")
TUYA_WIZARD_FILES = ("tinytuya.json", TUYA_DEVICE_FILE, TUYA_SNAPSHOT_FILE, "tuya-raw.json")
TUYA_IOT_URL = "https://iot.tuya.com"
# The kind of a wizard device's Tuya category, as Master reads it in the list.
TUYA_KIND_NAMES = {"light": "lampu", "plug": "colokan", "ir": "IR blaster"}
TUYA_GUIDE_URL = "https://github.com/jasonacox/tinytuya#get-the-tuya-device-local-key"
# The Broadlink address config.example.yaml ships with.
EXAMPLE_BLASTER_HOST = "192.168.1.50"
GACHA_DESCRIPTION = (
    "Teman main game HoYoverse: status stamina dan daily, check-in harian HoYoLAB, "
    "riwayat pull (pity, 50/50), data akun game (karakter, endgame, kalender event), kode "
    "redeem, pendapatan bulanan, dan laporan mingguan. Perintah ini jalan walau "
    "gacha.enabled: false; yang "
    "dinyalakan gacha.enabled hanyalah tool game, pengingat, dan laporan mingguan saat "
    "'waifu run'."
)
# What `waifu gacha income` ends with.
INCOME_NOTE = (
    "Catatan: pull dihitung 160 per pull dari mata uang premium saja, tanpa pass atau tape; "
    "catatan bulanan HoYoLAB bisa tertinggal dari game."
)
# What `waifu gacha report` ends with: HoYoverse lists a pull about an hour late.
WEEKLY_REPORT_NOTE = (
    "Catatan: HoYoverse mencatat pull sekitar satu jam setelahnya, jadi pull barusan mungkin "
    "belum ada."
)
# `waifu gacha report`'s codes line; with the games off no watcher keeps the codes fresh, so
# the line says they are the stored list (an empty or stale file reads the same as none left).
UNREDEEMED_CODES = "Kode belum ditukarkan"
UNREDEEMED_STORED_CODES = (
    "Kode belum ditukarkan (daftar tersimpan, perbarui dengan 'waifu gacha codes')"
)
# The community source of the redeem codes (see gacha.codes), credited wherever its codes show.
CODES_CREDIT = f"Sumber kode: {CODES_SOURCE} (API komunitas di balik Hoyo Buddy)"
REDEEM_NEEDS_GAME = "--redeem butuh satu game, misalnya: waifu gacha codes hsr --redeem"
# Where the games keep their logs, which lead to their history caches; tests point it elsewhere.
GAME_LOGS_DIR = Path.home() / "AppData" / "LocalLow"
DATA_VIEWS = ("characters", "endgame", "events")
# What each game's `waifu gacha <game> <view>` shows, for its --help.
DATA_VIEW_HELP = {
    "hsr": "characters: karakter dan Light Cone; endgame: bintang dan lantai musim ini; "
    "events: warp, event, dan jadwal endgame",
    "genshin": "characters: karakter dan weapon; endgame: bintang, act, dan difficulty musim "
    "ini; events: banner, event, dan jadwal endgame",
    "zzz": "characters: agent; endgame: rating, skor, dan bintang musim ini; "
    "events: banner dan event",
}
HSR_WARP_KINDS = {"character": "karakter", "light_cone": "Light Cone"}
# The Genshin and ZZZ banner kinds the calendar prints in other words; any other as it is.
BANNER_KINDS = {"character": "karakter", "w_engine": "W-Engine"}
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
DELAYED_STEPS_IN_RUN = (
    "Langkah yang ditunda dijalankan pada jamnya oleh Cyrene yang sedang berjalan ('waifu run'): "
    "biarkan PC dan 'waifu run' menyala sampai saat itu. Batalkan dengan "
    "'waifu schedule cancel <id>'."
)
DRY_RUN_DELAYED_STEPS = (
    "Mode simulasi: langkah yang ditunda hanya dicoba di jadwal sementara yang hilang saat "
    "ditutup. Jadwal asli ({path}) tidak disentuh."
)
DELAYED_STEPS_WITHOUT_RUN = (
    "'waifu run' tidak sedang berjalan: langkah rutinitas yang ditunda tetap masuk jadwal, "
    "tapi hanya dijalankan kalau 'waifu run' menyala pada jamnya. Jalankan 'waifu run' dan "
    "biarkan menyala sampai saat itu, atau batalkan dengan 'waifu schedule cancel <id>'."
)
ROUTINE_DESCRIPTION = (
    "Rutinitas Master: daftar perintah perangkat yang ditulis di bagian routines di "
    "config.yaml dan dijalankan sekaligus; langkah dengan after_minutes masuk jadwal dan jalan "
    "nanti. Ubah rutinitas dengan mengedit config.yaml; dengan --dry-run langkahnya dijalankan "
    "pada perangkat simulasi."
)
NO_ROUTINES = (
    "Belum ada rutinitas. Tulis di bagian routines di config.yaml "
    "(lihat README, bagian Jadwal, pengingat, dan rutinitas)."
)
TODO_DESCRIPTION = (
    "Daftar tugas Master: hal yang perlu dikerjakan, dengan tenggat (tanggal) kalau ada, tanpa "
    "jam; pengingat pada jam tertentu tetap lewat jadwal. Tanpa aksi, 'waifu todo' menampilkan "
    "tugas yang belum selesai. Perintah ini jalan walau todo.enabled: false; Cyrene yang sedang "
    "berjalan langsung melihat perubahannya."
)
LEXICON_DESCRIPTION = (
    "Kamus Cyrene: cara suara Indonesianya membaca sebuah kata, misalnya HSR dibaca "
    '"Ha Es Er". Aturan bawaan selalu ada; aturanmu sendiri disimpan di tts.lexicon_path dan '
    'bisa juga diajarkan lewat suara: "Cyrene, cara baca Acheron itu A Ke Ron". Cyrene yang '
    "sedang berjalan memakainya mulai kalimat berikutnya. Suara Jepang dan teks di HP tidak "
    "diubah."
)
JOURNAL_DESCRIPTION = (
    "Jurnal harian Master: catatan singkat per tanggal, dengan kata-kata Master sendiri. Tanpa "
    "aksi, 'waifu jurnal' menampilkan hari yang ada catatannya. Perintah ini jalan walau "
    "journal.enabled: false; Cyrene yang sedang berjalan langsung melihat perubahannya. "
    "Menghapus catatan hanya bisa di sini, tidak lewat suara."
)
JOURNAL_PREVIEW_CHARS = 50
APP_REMINDER_DESCRIPTION = (
    'Pengingat yang Cyrene ucapkan saat Master baru membuka game, misalnya "pas buka HSR nanti, '
    "ingetin aku farming relic\" (dibuat lewat suara atau chat). Tanpa aksi, 'waifu "
    "pengingat-game' menampilkan semuanya. Perintah ini jalan walau app_reminders.enabled: false; "
    "Cyrene yang sedang berjalan langsung melihat perubahannya."
)
TEPIRA_DESCRIPTION = (
    "Keuangan Master dari tepira (aplikasi keuangan Master) lewat API khusus Cyrene: ringkasan "
    "bulan ini, saldo dompet, tujuan tabungan, dan transaksi terbaru. Tanpa aksi, 'waifu tepira' "
    "menampilkan ringkasan. 'waifu tepira token' membuat token API baru tanpa internet, juga "
    "sebelum tepira dinyalakan. Mencatat pengeluaran hanya lewat suara."
)
TEPIRA_OFF = (
    "Keuangan tepira mati (tepira.enabled: false). Buat token dengan: waifu tepira token, lalu "
    "isi bagian tepira di config.yaml."
)
TEPIRA_TOKEN_USE = (
    "Isi tepira.token di config.yaml dengan baris token (rahasia seperti kata sandi, jangan "
    "dibagikan), lalu tempel baris sha256 ke TEPIRA_CYRENE_TOKEN_SHA256 di Vercel (Production, "
    "Sensitive) dan simpan juga untuk rotasi nanti."
)
TEPIRA_TOKEN_BYTES = 32  # tepira's README makes its tokens from 32 random bytes too
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
PC_UNREADABLE = "tidak terbaca"
PC_WITHOUT_GPU = "tidak terbaca (bukan NVIDIA atau nvidia-smi tidak ada)"
OFFERS_DESCRIPTION = (
    "Promo gratis: game gratis untuk disimpan (Epic Games Store dari Epic sendiri, Steam, GOG, "
    "dan lainnya dari GamerPower.com) dan promo AI (kredit gratis, paket gratis, program "
    "startup atau pelajar) dari pencarian mingguan Cyrene, dengan linknya. Cyrene hanya memberi "
    "tahu: klaim dan daftar dilakukan Master sendiri. Perintah ini jalan walau "
    "free_offers.enabled: false."
)
GAMES_OFF = "Pencarian game gratis dimatikan (free_offers.games: false)."
SCAN_RUNNING = (
    "Pencarian promo AI lain sedang berjalan, misalnya oleh 'waifu run'. Coba lagi setelah selesai."
)
UNVERIFIED_LINK = "(belum terverifikasi: bukan situs resmi penyedia yang dikenal)"
# GamerPower.com's terms: free for any use with attribution and a link to it.
GAMERPOWER_CREDIT = f"Sumber: {GAMERPOWER} (https://www.gamerpower.com)"
GAME_PLATFORM_NAMES = {"epic": "Epic", **GAMERPOWER_PLATFORMS}
AI_KIND_TEXT = {
    "free_credits": "kredit gratis",
    "free_tier": "paket gratis",
    "startup_program": "program startup",
    "student_program": "program pelajar",
    "trial": "uji coba gratis",
    "other": "program lain",
}
AI_ACCESS_TEXT = {"claim": "bisa langsung diklaim", "apply": "perlu mendaftar"}
YOUTUBE_DESCRIPTION = (
    "Buka lagu di browser default: link video atau playlist YouTube (youtube.com atau youtu.be) "
    "dibuka langsung, kata lain membuka halaman pencarian YouTube (YouTube Music dengan "
    "music.service: youtube_music). Perintah ini jalan walau music.enabled: false; yang "
    "dimatikan music.enabled hanyalah tool lagu Cyrene."
)
MUSIC_SERVICE_NAMES: dict[MusicService, str] = {
    "youtube": "YouTube",
    "youtube_music": "YouTube Music",
}
# Organization-level API keys must name a workspace on every request (see brain.claude).
CLAUDE_WORKSPACE_HEADER = "anthropic-workspace-id"
# Weekly, monthly, and yearly entries also name their day (`_repeat_text`).
REPEAT_TEXT: dict[Repeat, str] = {
    "none": "sekali",
    "daily": "tiap hari",
    "weekdays": "tiap hari kerja",
    "weekends": "tiap akhir pekan",
}
_YEAR_MONTH = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")
# date.fromisoformat also reads 20261023 and 2026-W43-5; a due date is only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
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
    command(
        "tombol",
        _quick_page,
        "Buka tombol cepat (lampu, AC, colokan, rutinitas) di browser PC ini, tanpa otak; tutup "
        "dengan Ctrl+C.",
    )
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
    say.add_argument(
        "--fallback",
        action="store_true",
        help="dengan suara cadangan offline (suara Windows), yang dipakai kalau edge-tts gagal",
    )
    say.add_argument(
        "--volume",
        type=_voice_volume_percent,
        metavar="PERSEN",
        help=(
            f"volume suaranya {LEVELS[0]} sampai {LEVELS[1]} persen untuk mencoba, tanpa "
            "menyimpannya (default: volume suaranya sekarang)"
        ),
    )
    command("mics", _mics, "Daftar perangkat audio.")
    mic = command(
        "mic",
        _mic,
        "Cari mic yang mendengar suara Master: tiap mic didengarkan sebentar sambil Master "
        "bicara, tanpa menyimpan rekaman.",
    )
    mic.add_argument(
        "--seconds",
        type=_positive_seconds,
        default=MIC_CHECK_SECONDS,
        metavar="DETIK",
        help=f"lama mendengarkan tiap mic (default {MIC_CHECK_SECONDS:g})",
    )
    setup = command(
        "setup",
        _setup,
        "Panduan setup pertama, langkah demi langkah: jawabanmu ditulis langsung ke config.yaml "
        "(isi sebelumnya disimpan di folder data). Tiap langkah bisa dilewati.",
    )
    setup.add_argument(
        "step",
        nargs="?",
        default="all",
        choices=[*SETUP_STEPS, "all"],
        help="satu langkah saja; tanpa ini (all) semua langkah berurutan, lalu waifu check",
    )
    command(
        "voices",
        _voices,
        "Daftar suara Windows per bahasa dan suara cadangan offline yang dipakai Cyrene.",
    )
    command(
        "briefing", _briefing, "Tampilkan ringkasan hari ini (tanggal, cuaca, jadwal) tanpa otak."
    )
    command(
        "rain",
        _rain,
        f"Tampilkan kapan hujan berikutnya dalam {RAIN_LOOKAHEAD_HOURS} jam di kota "
        "briefing.city, tanpa otak.",
    )
    weather_ahead = command(
        "cuaca",
        _weather_ahead,
        "Tampilkan prakiraan cuaca sisa hari ini, besok, akhir pekan, atau tanggal lain sampai "
        "enam hari lagi di kota briefing.city, per bagian hari, tanpa otak.",
    )
    weather_ahead.add_argument(
        "day",
        nargs="?",
        type=_forecast_day,
        default="hari-ini",
        metavar="hari",
        help="hari-ini (sisa hari ini, bawaan), besok, akhir-pekan, atau tanggal YYYY-MM-DD",
    )
    recap = command(
        "rekap",
        _recap,
        "Tampilkan rekap hari ini, kemarin, atau tanggal lain sampai 30 hari ke belakang dari "
        "catatan di PC ini (tugas, fokus, jam main, pull, kata Jepang, jurnal, jadwal), tanpa "
        "otak.",
    )
    recap.add_argument(
        "day",
        nargs="?",
        type=_recap_day,
        default="hari-ini",
        metavar="hari",
        help="hari-ini (sampai sekarang, bawaan), kemarin, atau tanggal YYYY-MM-DD",
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
    command(
        "pc",
        _pc,
        "Tampilkan kondisi PC: beban CPU, RAM, sisa disk, GPU NVIDIA, dan waktu sejak restart.",
    )
    command(
        "internet",
        _internet,
        "Periksa internet sekarang (router, internet, DNS, server HoYoLAB/HoYoverse) dan "
        "tampilkan kapan saja putus hari ini, dari catatan Cyrene selama 'waifu run' menyala.",
    )
    playtime = command(
        "playtime",
        _playtime,
        "Tampilkan berapa lama tiap game dibuka (HSR, Genshin, ZZZ), dari catatan Cyrene "
        "selama 'waifu run' menyala.",
    )
    playtime.add_argument(
        "period",
        nargs="?",
        choices=PERIODS,
        default="today",
        help="today (hari ini, bawaan), yesterday (kemarin), week (minggu ini, sejak Senin), "
        "atau month (bulan ini)",
    )
    command(
        "focus",
        _focus,
        "Tampilkan waktu fokus hari ini dan minggu ini, dari catatan teman fokus Cyrene selama "
        "'waifu run' menyala.",
    )
    japanese = command(
        "jepang",
        _japanese,
        "Belajar bahasa Jepang: tampilkan kata hari ini atau kemajuan Master, tanpa otak dan "
        "tanpa mencatat apa pun.",
    )
    japanese.add_argument(
        "view",
        nargs="?",
        choices=JAPANESE_VIEWS,
        default="hari-ini",
        help="hari-ini (kata hari ini dengan contohnya, bawaan) atau progres (berapa kata yang "
        "sudah dipelajari, dikuasai, dan perlu diulang hari ini)",
    )
    prayer = command(
        "sholat",
        _prayer,
        "Tampilkan waktu sholat hari ini (atau tanggal lain) untuk tempat Master, dihitung di PC "
        "ini dengan metode Kemenag RI, tanpa otak.",
    )
    prayer.add_argument(
        "date",
        nargs="?",
        type=_due_date,
        default=None,
        help="tanggalnya, YYYY-MM-DD (bawaan: hari ini)",
    )
    youtube = commands.add_parser(
        "youtube",
        parents=[shared],
        help="Buka lagu di YouTube: link video atau playlist, atau judul lagunya.",
        description=YOUTUBE_DESCRIPTION,
    )
    youtube.set_defaults(handler=_youtube)
    youtube.add_argument(
        "words",
        nargs="+",
        metavar="lagu",
        help="link video atau playlist YouTube, atau judul lagu dan penyanyinya",
    )
    offers_command = commands.add_parser(
        "offers",
        parents=[shared],
        help="Promo gratis: game gratis dan promo AI, dengan linknya.",
        description=OFFERS_DESCRIPTION,
    )
    offers_command.set_defaults(handler=_offers)
    offers_kinds = offers_command.add_subparsers(dest="action", metavar="<jenis>")
    command(
        "games", _offers_games, "Game gratis sekarang dan segera, dengan linknya.", offers_kinds
    )
    ai_offers = command(
        "ai", _offers_ai, "Promo AI dari pencarian terakhir, dengan linknya.", offers_kinds
    )
    ai_offers.add_argument(
        "--scan",
        action="store_true",
        help="cari sekarang dengan Claude dan pencarian web (berbayar, bisa beberapa menit)",
    )
    command("check", _check, "Periksa konfigurasi, perangkat, dan dependensi.")
    command(
        "version",
        _version,
        "Tampilkan versi Cyrene di disk dan versi 'waifu run' yang sedang berjalan.",
    )
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
        help="Teman main game HoYoverse: status, check-in, riwayat pull, data akun game, kode "
        "redeem, pendapatan bulanan, laporan mingguan.",
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
    for game in GAMES.values():
        data = command(
            game.id,
            _gacha_data,
            f"Data akun {game.short} dari HoYoLAB: karakter, endgame musim ini, atau kalender "
            "event.",
            gacha_actions,
        )
        data.set_defaults(game=game.id)
        data.add_argument("view", choices=DATA_VIEWS, help=DATA_VIEW_HELP[game.id])
    codes = command(
        "codes",
        _gacha_codes,
        "Kode redeem baru tiap game: yang belum ditukarkan dan hasil terakhir.",
        gacha_actions,
    )
    codes.add_argument(
        "game", nargs="?", choices=list(GAMES), help="satu game saja (wajib untuk --redeem)"
    )
    codes.add_argument(
        "--redeem",
        action="store_true",
        help="tukarkan kode game itu yang belum ditukarkan di akun HoYoLAB Master",
    )
    income = command(
        "income",
        _gacha_income,
        "Pendapatan Primogem, Stellar Jade, dan Polychrome sebulan dari catatan bulanan HoYoLAB.",
        gacha_actions,
    )
    income.add_argument("game", nargs="?", choices=list(GAMES), help="satu game saja")
    income.add_argument(
        "--month",
        type=_year_month,
        metavar="YYYY-MM",
        help="bulan yang ditampilkan, misalnya 2026-09 (default bulan ini; HoYoLAB hanya "
        "menyimpan bulan ini dan dua bulan sebelumnya)",
    )
    command(
        "report",
        _gacha_report,
        "Laporan 7 hari terakhir tiap game: pull dan 50/50, pity, endgame, dan yang segera "
        "berakhir.",
        gacha_actions,
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
    lexicon_command = commands.add_parser(
        "lafal",
        parents=[shared],
        help="Kamus Cyrene: cara suara Indonesianya membaca sebuah kata.",
        description=LEXICON_DESCRIPTION,
    )
    lexicon_actions = lexicon_command.add_subparsers(dest="action", required=True, metavar="<aksi>")
    command(
        "list",
        _lafal_list,
        "Tampilkan aturanmu, lalu aturan bawaan yang tidak kamu ganti.",
        lexicon_actions,
    )
    lafal_add = command("add", _lafal_add, "Simpan cara baca satu kata.", lexicon_actions)
    lafal_add.add_argument(
        "term", metavar="kata", help='kata yang dibaca, misalnya Acheron ("Star Rail" pakai kutip)'
    )
    lafal_add.add_argument("reading", nargs="+", metavar="bacaan", help="misalnya A Ke Ron")
    lafal_remove = command(
        "remove", _lafal_remove, "Hapus aturanmu untuk satu kata.", lexicon_actions
    )
    lafal_remove.add_argument("term", metavar="kata")
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
    todo_command = commands.add_parser(
        "todo",
        parents=[shared],
        help="Daftar tugas Master: lihat, tambah, selesaikan, dan hapus tugas.",
        description=TODO_DESCRIPTION,
    )
    todo_command.set_defaults(handler=_todo_list, done=False)
    todo_actions = todo_command.add_subparsers(dest="action", metavar="<aksi>")
    todo_list = command(
        "list",
        _todo_list,
        "Tampilkan tugas yang belum selesai, yang terlambat dan tenggatnya paling dekat dulu.",
        todo_actions,
    )
    todo_list.add_argument(
        "--done",
        action="store_true",
        help="tampilkan juga tugas yang sudah selesai (disimpan todo.keep_done_days hari)",
    )
    todo_add = command("add", _todo_add, "Tambah satu tugas.", todo_actions)
    todo_add.add_argument("text", nargs="+")
    todo_add.add_argument(
        "--due", type=_due_date, metavar="YYYY-MM-DD", help="tenggatnya, misalnya 2026-10-23"
    )
    todo_done = command("done", _todo_done, "Tandai satu tugas selesai.", todo_actions)
    todo_done.add_argument("task_id", metavar="id", help="id dari 'waifu todo', misalnya t3")
    todo_remove = command("remove", _todo_remove, "Hapus satu tugas.", todo_actions)
    todo_remove.add_argument("task_id", metavar="id", help="id dari 'waifu todo', misalnya t3")
    journal_command = commands.add_parser(
        "jurnal",
        parents=[shared],
        help="Jurnal harian Master: lihat, tambah, dan hapus catatan per tanggal.",
        description=JOURNAL_DESCRIPTION,
    )
    journal_command.set_defaults(handler=_journal_list)
    journal_actions = journal_command.add_subparsers(dest="action", metavar="<aksi>")
    command(
        "list",
        _journal_list,
        "Tampilkan hari yang ada catatannya, yang paling lama dulu.",
        journal_actions,
    )
    journal_show = command("show", _journal_show, "Tampilkan catatan satu hari.", journal_actions)
    journal_show.add_argument(
        "day", type=_due_date, metavar="YYYY-MM-DD", help="tanggalnya, misalnya 2026-10-17"
    )
    journal_add = command("add", _journal_add, "Tambah satu catatan.", journal_actions)
    journal_add.add_argument("text", nargs="+")
    journal_add.add_argument(
        "--date",
        type=_due_date,
        metavar="YYYY-MM-DD",
        help="tanggal catatannya kalau bukan hari ini, misalnya 2026-10-16",
    )
    journal_forget = command(
        "forget", _journal_forget, "Hapus semua catatan satu hari.", journal_actions
    )
    journal_forget.add_argument(
        "day", type=_due_date, metavar="YYYY-MM-DD", help="tanggalnya, misalnya 2026-10-17"
    )
    journal_forget.add_argument(
        "--yes", action="store_true", help="benar-benar hapus (tanpa ini hanya dihitung)"
    )
    app_reminder_command = commands.add_parser(
        "pengingat-game",
        parents=[shared],
        help="Pengingat saat game dibuka: lihat dan hapus.",
        description=APP_REMINDER_DESCRIPTION,
    )
    app_reminder_command.set_defaults(handler=_app_reminder_list)
    app_reminder_actions = app_reminder_command.add_subparsers(dest="action", metavar="<aksi>")
    app_reminder_remove = command(
        "hapus", _app_reminder_remove, "Hapus satu pengingat game.", app_reminder_actions
    )
    app_reminder_remove.add_argument(
        "reminder_id", metavar="id", help="id dari 'waifu pengingat-game', misalnya g2 (atau 2)"
    )
    tepira_command = commands.add_parser(
        "tepira",
        parents=[shared],
        help="Keuangan Master dari tepira: ringkasan, dompet, tujuan tabungan, transaksi, token.",
        description=TEPIRA_DESCRIPTION,
    )
    tepira_command.set_defaults(handler=_tepira_summary)
    tepira_actions = tepira_command.add_subparsers(dest="action", metavar="<aksi>")
    command(
        "ringkasan",
        _tepira_summary,
        "Pengeluaran, pemasukan, dan budget bulan ini (WIB).",
        tepira_actions,
    )
    command("dompet", _tepira_wallets, "Saldo tiap dompet dan kekayaan bersih.", tepira_actions)
    goals = command("tujuan", _tepira_goals, "Tujuan tabungan dan progresnya.", tepira_actions)
    goals.add_argument(
        "goal_name", nargs="*", metavar="nama", help="sebagian nama tujuan, misalnya laptop"
    )
    transactions = command("transaksi", _tepira_transactions, "Transaksi terbaru.", tepira_actions)
    transactions.add_argument(
        "-n",
        dest="count",
        type=_transaction_count,
        default=DEFAULT_TRANSACTIONS,
        metavar="N",
        help=f"jumlah transaksi, 1 sampai {MAX_TRANSACTIONS} (default {DEFAULT_TRANSACTIONS})",
    )
    command(
        "token",
        _tepira_token,
        "Buat token API Cyrene baru dan hash-nya untuk Vercel (tanpa internet).",
        tepira_actions,
    )
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


def _positive_seconds(text: str) -> float:
    invalid = argparse.ArgumentTypeError(f"harus angka lebih dari 0, bukan {text!r}")
    try:
        seconds = float(text)
    except ValueError:
        raise invalid from None
    if not (math.isfinite(seconds) and seconds > 0):
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


def _due_date(text: str) -> date:
    """`2026-10-23` as a date: exactly YYYY-MM-DD, a day that exists."""
    if _ISO_DATE.fullmatch(text):
        try:
            return date.fromisoformat(text)
        except ValueError:  # a day that does not exist, such as 2026-02-30
            pass
    raise argparse.ArgumentTypeError(
        f"tulis tanggalnya YYYY-MM-DD, misalnya 2026-10-23, bukan {text!r}"
    )


def _forecast_day(text: str) -> str | date:
    """`hari-ini`, `besok`, or `akhir-pekan` as the forecast's day word, or a date written
    exactly YYYY-MM-DD."""
    if text in FORECAST_DAYS:
        return FORECAST_DAYS[text]
    try:
        return _due_date(text)
    except argparse.ArgumentTypeError:
        raise argparse.ArgumentTypeError(
            "tulis hari-ini, besok, akhir-pekan, atau tanggal YYYY-MM-DD, misalnya 2026-10-25, "
            f"bukan {text!r}"
        ) from None


def _recap_day(text: str) -> date:
    """`hari-ini`, `kemarin`, or a date written exactly YYYY-MM-DD, from 30 days ago up to
    today (WIB)."""
    if text in RECAP_DAYS:
        day = RECAP_DAYS[text]
    else:
        try:
            day = _due_date(text)
        except argparse.ArgumentTypeError:
            raise argparse.ArgumentTypeError(
                "tulis hari-ini, kemarin, atau tanggal YYYY-MM-DD, misalnya 2026-10-23, "
                f"bukan {text!r}"
            ) from None
    try:
        return recap_day(day, wib_now().date())
    except RecapError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _transaction_count(text: str) -> int:
    """How many of tepira's newest transactions to show: a whole number it serves at once."""
    if not (text.isascii() and text.isdigit() and 1 <= int(text) <= MAX_TRANSACTIONS):
        raise argparse.ArgumentTypeError(
            f"harus bilangan bulat 1 sampai {MAX_TRANSACTIONS}, bukan {text!r}"
        )
    return int(text)


def _voice_volume_percent(text: str) -> int:
    """A level of her voice to try with `say`: a whole percentage she can be played at."""
    low, high = LEVELS
    if not (text.isascii() and text.isdigit() and low <= int(text) <= high):
        raise argparse.ArgumentTypeError(
            f"harus bilangan bulat {low} sampai {high}, bukan {text!r}"
        )
    return int(text)


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
    # genshin.py logs every request's URL at DEBUG, the HoYoLAB uid in its query included.
    # Not on "genshin" itself: every new genshin.Client sets that one back to NOTSET.
    logging.getLogger("genshin.client").setLevel(logging.INFO)
    # genshin.py warns "Unknown <enum>: ..., please report to developer" from genshin.models
    # whenever HoYoLAB sends a value it has no name for (on every HSR calendar read): the
    # library's own business. The HoYoLAB errors Cyrene logs herself are not affected.
    logging.getLogger("genshin.models").setLevel(logging.ERROR)
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
    # Before the delay: the code this process runs was imported when it started.
    running = _record_running_version()
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
    # What Master picked on the phone's Suara page: a mic that goes first, and the speaker
    # threshold and the VAD's gate in place of the config's (never written to config.yaml).
    override = _voice_override(config)
    if verifier is not None and override.speaker_threshold is not None:
        verifier.threshold = override.speaker_threshold
    registry = build_registry(config, dry_run=args.dry_run)
    gacha = build_gacha(config) if config.gacha.enabled else None
    usage = _usage_recorder(config)
    pc = _session_pc_reader(config)  # the PC tool and the PC watcher read the PC through it
    notifier = build_notifier(config.notify)
    # Only while the phone page offers its camera (the phone server then takes photos): the photo
    # a phone message carries waits for its turn in `turn_images`, shared by the brain and the
    # Assistant, and `camera_turn`, shared by the Assistant and the screen tool, tells which
    # turns were sent through the camera.
    camera_offered = _camera_offered(config)
    turn_images = TurnImages() if camera_offered else None
    camera_turn = CameraTurn() if camera_offered else None
    # One focus timer for this run: her tool starts and stops it, and while a block is under way
    # the Assistant says only what Master asked for himself.
    focus = _focus_timer(config)
    # One place for the prayer times while they are on, shared by her tool and the reminders, so
    # a city is looked up once per run (at its first use, never at startup).
    prayer = _prayer_place(config) if config.prayer.enabled else None
    # One guest mode for this run, in memory only: the brain refuses the private tools and starts
    # afresh as it switches, the briefing leaves Master's tasks out, and the Assistant skips her
    # private turns while it is on and forgets her last words (for "ulangi") as it switches.
    guest_mode = GuestMode(max_hours=config.guest_mode.max_hours)
    # What she heard lately and what she did with it, in memory only, for the phone's Suara page
    # and "tadi kamu denger apa?"; forgotten as guest mode switches.
    heard = UtteranceLog(marker=guest_mode.generation)
    # The pause of her hearing that Ctrl+Alt+Q starts, shared by the voice loop, the hotkeys, and
    # the phone's "Dengar lagi"; one an earlier run left is cleared.
    pause = _cleared_listen_pause(config)
    # The words Whisper should expect: the config's, and the roster names the brain reads.
    vocabulary = Vocabulary(config)
    # The brain's game data tools, built once for this run: each roster they read joins Whisper's
    # hotwords, and the gacha planner reads the event calendars through their cache.
    game_data = _game_data_tools(config, gacha, on_roster=vocabulary.set_roster)
    # Master's readings for her Indonesian voices, re-read at every clip, which he teaches her by
    # voice too.
    lexicon = _lexicon(config)
    # Her own voice level: her tool sets it, and the player reads it at every clip she says, except
    # an alarm's ring, which holds a level of its own for its clip.
    voice_level = _voice_level(config)
    hold = LevelHold(voice_level.level_now)
    # The games Master closes, and whether one is still open, from the play time watcher to the
    # gacha planner's overnight line.
    closed_games = _closed_games(config)
    # What this run knows of the internet while the watcher is on: one probe and one state for
    # the watcher, her answers, her tool, and Claude's apology naming an outage.
    internet = _internet_watch(config)
    with _session_schedule(config, dry_run=args.dry_run) as schedule:
        # One bell for the alarms: the scheduler starts it, she rings it, and her tool and the
        # phone's buttons stop it.
        alarm = _alarm_bell(config, schedule, dry_run=args.dry_run, day_level=voice_level.day_level)
        # While a game runs (the play time watcher tells it), game mode may hold what she says
        # by herself, and it runs Master's start and stop routines on this run's devices.
        game_mode = _game_mode(config, registry, schedule)
        quiet = _quiet(config, focus, game_mode)
        brain = _companion_brain(
            config,
            registry,
            gacha,
            schedule,
            usage,
            pc,
            notifier,
            turn_images,
            camera_turn,
            focus=focus,
            guest_mode=guest_mode,
            voice_level=voice_level,
            prayer=prayer,
            alarm=alarm,
            game_data=game_data,
            internet=internet,
        )
        stt = WhisperStt(config.stt, name, hotwords=vocabulary.text)
        gate = WakeGate(config.assistant.wake_words, config.assistant.followup_seconds)
        segmenter = EnergySegmenter(
            config.audio.sample_rate,
            _vad_with(config.audio.vad, override),
            on_too_short=heard.add_too_short,
        )
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
            speech = build_speech_router(config.tts, converter=converter, lexicon=lexicon.readings)
            if internet is not None:
                # While the internet is down her offline voice speaks at once, without waiting
                # for edge-tts to fail; once it is back edge-tts speaks again.
                internet.state.follow(speech.internet_changed)
            # Her fillers for a slow reply, in the same voice as her replies (RVC and Master's
            # readings included); made in the background once she is ready, the ones left out
            # tried again once the router's pause on a failing voice has passed.
            nods = (
                NodBank(
                    speech,
                    owner_call=config.assistant.owner_call,
                    retry_s=retry_after_s(config.tts.fallback.retry_s),
                )
                if config.nod.enabled
                else None
            )
            assistant = Assistant(
                config,
                brain=brain,
                stt=stt,
                speech=speech,
                # Only the PC's speaker: the phone's replies are synthesised apart.
                player=VolumePlayer(Player(config.audio.output_device), hold.level_now),
                gate=gate,
                verifier=verifier,
                speaker_check=speaker_check,
                avatar=avatar,
                turn_images=turn_images,
                camera_turn=camera_turn,
                quiet=quiet,
                game_holding=None if game_mode is None else game_mode.holding,
                guest_mode=guest_mode.is_on,
                guest_marker=guest_mode.generation,
                alarm=alarm,
                hold_level=hold.held,
                diagnosis=heard,
                listen_pause=pause,
                lexicon=lexicon,
                nods=nods,
                network=(
                    None
                    if internet is None
                    else NetworkAnswers(internet.check, owner_call=config.assistant.owner_call)
                ),
            )
            reactions = InteractionHandler(
                config.avatar.interactions,
                speak=assistant.speak_reaction,
                listen=assistant.listen_now,
                is_busy=lambda: assistant.busy,
                interrupt=assistant.interrupt,
                quiet=quiet,
                owner_call=config.assistant.owner_call,
            )
            # Through a failing mic she goes on with the next one, says which, and switches back
            # when a preferred one returns; the phone's Suara page shows and switches it.
            mic = _mic_source(
                config,
                override,
                announce=_mic_notice(assistant),
                on_swap=segmenter.relearn,
                live=True,
            )
            voice = VoicePanel(
                mic,
                segmenter,
                heard,
                VoiceOverrideStore(config.audio.override_path),
                vad=config.audio.vad,
                verifier=verifier,
                speaker_threshold=config.speaker.threshold,
            )
            with mic, _masters_input(config, touches, reactions, assistant, pause):
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
                # After the "siap" line: the RVC server converts one clip at a time.
                if nods is not None:
                    _prepare_nods(nods)
                try:
                    with (
                        _gacha_watcher(
                            config,
                            gacha,
                            assistant,
                            game_mode,
                            guest_mode=guest_mode.is_on,
                            closed_games=closed_games,
                            read_calendar=None if game_data is None else game_data.calendar,
                        ),
                        _code_watcher(config, gacha, assistant, notifier, game_mode),
                        _pc_watcher(config, pc, assistant, notifier, game_mode),
                        # One item: this block is at Python's limit of nested blocks.
                        _together(
                            _play_time_watcher(
                                config, assistant, game_mode, closed_games, notifier, internet
                            ),
                            _network_watcher(config, internet, assistant, game_mode),
                        ),
                        _focus_session(config, focus, assistant, notifier),
                        _break_watcher(config, focus, assistant, notifier, game_mode),
                        _rain_watcher(config, assistant, notifier, game_mode),
                        _version_watcher(config, running, assistant, notifier, game_mode),
                        _offers_watcher(config, assistant, notifier, usage, game_mode),
                        # Master set the reminders: they speak in a focus block or a game too.
                        _masters_reminders(
                            config, schedule, registry, prayer, assistant, notifier, alarm
                        ),
                        _briefing_timer(config, assistant, notifier),
                        _weekly_report_timer(config, gacha, assistant, notifier),
                        _journal_timer(config, assistant),
                        _budget_told(usage, _budget_teller(config, assistant, notifier, game_mode)),
                        _phone_server(
                            config,
                            assistant,
                            camera=camera_offered,
                            actions=QuickActions(
                                registry,
                                config.routines,
                                schedule=schedule,
                                chosen=config.phone.quick_actions,
                                alarm=alarm,
                                listen_pause=pause,
                            ),
                            voice=voice,
                            guest_mode=guest_mode.is_on,
                        ),
                    ):
                        assistant.run(mic, segmenter)
                except SttError as exc:
                    logger.error("Whisper stopped working: %s", exc)
                    print(f"{FAIL} {exc}")
                    return 1
    return 0


def _game_mode(
    config: AppConfig, registry: DeviceRegistry, schedule: ScheduleStore | None
) -> GameMode | None:
    """The game mode of one `run`, or None while it changes nothing (the default, or play time
    off): its routines run on the run's devices, their delayed steps in the run's schedule."""
    if not config.play_time.enabled or config.game_mode == GameModeConfig():
        return None
    return GameMode(
        config.game_mode,
        registry=registry,
        routines=config.routines,
        schedule=schedule,
        poll=timedelta(seconds=config.play_time.poll_seconds),
    )


def _quiet(
    config: AppConfig, focus: FocusTimer | None, game_mode: GameMode | None
) -> Callable[[], bool] | None:
    """When what she would say by herself waits: during a focus block, and while game mode
    holds her announcements; None when nothing ever holds them. Without a game mode that holds
    them, exactly the focus timer's `in_block`, as before game mode."""
    if game_mode is None or config.game_mode.announcements == "normal":
        return None if focus is None else focus.in_block
    holding = game_mode.holding
    if focus is None:
        return holding
    in_block = focus.in_block
    return lambda: in_block() or holding()


def _announcer(
    assistant: Assistant,
    game_mode: GameMode | None,
    *,
    patient: bool = False,
    pushed: bool = False,
) -> Callable[..., None]:
    """How an automatic notice is queued, `patient` ones also waiting while the follow-up window
    is open: with game mode on `phone`, a notice that also went to the phone (`pushed`) is
    left unsaid while a game runs. Any other notice is queued, and her quiet holds it while a
    game holds her announcements."""

    def announce(
        text: str,
        emotion: str = NEUTRAL,
        *,
        expires_at: datetime | None = None,
        game_expires_at: datetime | None = None,
    ) -> None:
        if pushed and game_mode is not None and game_mode.diverting():
            logger.info("Not saying a notice while a game runs: it went to the phone")
            return
        assistant.announce(
            text,
            emotion,
            wait_for_followup=patient,
            expires_at=expires_at,
            game_expires_at=game_expires_at,
        )

    return announce


@contextmanager
def _together(*managers: AbstractContextManager[object]) -> Iterator[None]:
    """`managers` entered in order as one item of a `with` statement, and left in reverse, as
    their own items would be."""
    with ExitStack() as stack:
        for manager in managers:
            stack.enter_context(manager)
        yield


def _chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    gacha = build_gacha(config) if config.gacha.enabled else None
    usage = _usage_recorder(config)
    name = config.assistant.name
    owner_call = config.assistant.owner_call
    # Left out of Master's words when they ask for her last card again (`is_card_request`).
    names = (name, *config.assistant.wake_words, owner_call)
    # Her voice level is the file's, shared with a running `waifu run`, which hears a change
    # made here at its next clip.
    voice_level = _voice_level(config)
    # This process's own guest mode: a running `waifu run` keeps its own.
    guest_mode = GuestMode(max_hours=config.guest_mode.max_hours)
    cards = _ChatCards(guest_mode)
    with _session_schedule(config, dry_run=args.dry_run) as schedule:
        # Only the offers tool pushes from chat, when Master asks for a link.
        brain = _companion_brain(
            config,
            registry,
            gacha,
            schedule,
            usage,
            _session_pc_reader(config),
            build_notifier(config.notify),
            guest_mode=guest_mode,
            voice_level=voice_level,
            # Its tool stops or snoozes a ring of `waifu run` through the bell's file; nothing
            # rings in chat, whose scheduler says a due alarm once.
            alarm=_alarm_bell(config, schedule, dry_run=args.dry_run),
        )
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
            speech = (
                build_speech_router(
                    config.tts, converter=converter, lexicon=_lexicon(config).readings
                )
                if args.speak
                else None
            )
            player = (
                VolumePlayer(Player(config.audio.output_device), voice_level.level_now)
                if args.speak
                else None
            )
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
                    if is_card_request(line, names):
                        _print_card_again(name, owner_call, cards.last())
                        continue
                    marker = cards.marker()
                    emotion, reply, card = split_reply(brain.respond(line), owner_call=owner_call)
                    card = cards.keep(card, marker)
                    if args.verbose:
                        for call in reply.tool_calls:
                            print(_describe_tool_call(call))
                    print(f"{name}: {reply.text}")
                    if card is not None:
                        _print_card(card)
                    if speech is not None and player is not None:
                        _speak(speech, player, reply.text, avatar=avatar, emotion=emotion)
    return 0


class _ChatCards:
    """The text card `waifu chat` printed last, for "tampilkan lagi kartunya", in memory only and
    forgotten once guest mode switches; a turn that switched it prints none, as on the PC."""

    def __init__(self, guest_mode: GuestMode) -> None:
        self._guest_mode = guest_mode
        self._last: tuple[Card, int] | None = None

    def marker(self) -> int:
        """Guest mode's generation now: read before each turn."""
        return self._guest_mode.generation()

    def keep(self, card: Card | None, marker: int) -> Card | None:
        """The card to print of a turn that began at `marker`, kept as the last one; None, and
        the last one forgotten, when the turn switched guest mode."""
        if self._guest_mode.generation() != marker:
            self._last = None
            return None
        if card is not None:
            self._last = (card, marker)
        return card

    def last(self) -> Card | None:
        """The last card kept, unless guest mode has switched since."""
        last = self._last
        return last[0] if last is not None and last[1] == self._guest_mode.generation() else None


def _print_card(card: Card) -> None:
    """Her text card under her words in `waifu chat`: its title, then its lines indented."""
    print(f"  Kartu: {card.title}" if card.title else "  Kartu")
    for line in card.body.split("\n"):
        print(f"    {line}" if line else "")


def _print_card_again(name: str, owner_call: str, card: Card | None) -> None:
    """Her answer in `waifu chat` to "tampilkan lagi kartunya": `card` printed again, or that
    there is none, without the brain."""
    if card is None:
        print(f"{name}: {NO_CARD.format(owner=owner_call)}")
        return
    print(f"{name}: {CARD_AGAIN.format(owner=owner_call)}")
    _print_card(card)


def _companion_brain(
    config: AppConfig,
    registry: DeviceRegistry,
    gacha: GachaParts | None,
    schedule: ScheduleStore | None,
    usage: _UsageRecorder | None,
    pc: Callable[[], PcSnapshot] | None,
    notifier: Notifier | None,
    turn_images: TurnImages | None = None,
    camera_turn: CameraTurn | None = None,
    *,
    focus: FocusTimer | None = None,
    guest_mode: GuestMode,
    voice_level: VoiceLevel,
    prayer: PrayerPlace | None = None,
    alarm: AlarmBell | None = None,
    game_data: GameDataTools | None = None,
    internet: _InternetWatch | None = None,
) -> Brain:
    """The brain with `guest_mode`'s tool first, her own voice volume (`voice_level`), then the
    game tools when they are on, her long-term memory when it is on, her schedule when it is on
    (with `alarm`, Master's alarms rung by that bell),
    Master's to-do list when it is on, Master's journal when it is on, with `focus` (only `run`
    has one) Master's focus timer, Master's Japanese lessons when they are on, a look back at
    Master's day when any of the records it reads is on, the day's briefing when it is on, the
    weather of the days ahead whenever the weather has a place (with the briefing's client), the
    prayer times when they are on (at `prayer`, the place `run`
    shares with the reminders, else a place of their own), with `pc` how the PC is doing, how
    the internet is when its watcher is on (through `internet`, `run`'s view, whose outage
    Claude's apology also names, else a view of its own), how
    long Master's games were open when play time is on (and with app reminders on, his reminders
    for a game's next start), the free offers when they are on (their
    links pushed through `notifier`), Master's money from tepira when it is on, songs on YouTube
    when they are on, with Claude her eyes on the screen when vision is on, what Master copied on
    the PC when his own words name it, and with `usage` what her requests cost. With `game_data`
    (only `run` has one), the game data tools are that one, whose rosters join Whisper's hotwords
    and whose calendars the gacha planner shares.

    With memory, every new session lists the notes as they are in the file at that moment.
    The to-do list is the real one also in a dry run (it moves no device), and the briefing
    tells its tasks due today and overdue. So are Master's Japanese words, and the briefing
    tells the word of the day, learned once in the same store as the lesson tool's.
    With `usage`, every Claude response is recorded in its ledger. With `turn_images` (only
    while the phone camera is offered), Claude sees the photo a phone message carries, and
    with `camera_turn` the screen tool looks at the PC in a turn sent through the phone camera
    only when Master names the screen. While `guest_mode` is on, the brain refuses the private
    tools, a new session's prompt hides the notes, and the briefing leaves out Master's tasks and
    reminder texts.
    """
    owner_call = config.assistant.owner_call
    # One gate for Master's yes, shared by the brain (turning the PC off) and the finance tools
    # (recording an expense), so one question per turn covers both.
    confirmations = confirmation_gate(config)
    notes = _memory_store(config) if config.memory.enabled else None
    todo = _todo_store(config) if config.todo.enabled else None
    journal = _journal_store(config) if config.journal.enabled else None
    japanese = _japanese_store(config) if config.japanese.enabled else None
    words = load_words() if japanese is not None else ()
    providers: list[ToolProvider] = [
        GuestTools(guest_mode),
        VoiceVolumeTools(voice_level),
        *_game_tools(config, gacha, game_data=game_data),
    ]
    if notes is not None:
        providers.append(MemoryTools(notes, owner_call=owner_call))
    if schedule is not None:
        providers.append(
            ScheduleTools(
                schedule, registry=registry, routines=config.routines, owner_call=owner_call
            )
        )
        if alarm is not None:
            providers.append(AlarmTools(schedule, alarm, guest_mode=guest_mode.is_on))
    if todo is not None:
        providers.append(TodoTools(todo))
    if journal is not None:
        # Master's answer to her nightly question comes in the question's session.
        answer_window = timedelta(minutes=config.assistant.session_idle_minutes)
        providers.append(JournalTools(journal, now=wib_now, answer_window=answer_window))
    if focus is not None:
        providers.append(FocusTools(focus, now=wib_now))
    if japanese is not None:
        providers.append(JapaneseTools(words, japanese, now=wib_now, guest_mode=guest_mode.is_on))
    recap = _daily_recap(config, schedule, focus)
    if recap.parts:
        providers.append(DailyRecapTools(recap))
    # One client for the briefing and the days ahead, so the city is looked up once.
    weather = _weather_client(config.briefing)
    if config.briefing.enabled:
        providers.append(
            _briefing_tools(
                weather,
                schedule,
                todo=todo,
                guest_mode=guest_mode.is_on,
                japanese=None if japanese is None else _japanese_word_of_the_day(words, japanese),
            )
        )
    if weather is not None:
        # The days ahead need only a place, as the rain alerts do; she names the city as written.
        providers.append(WeatherForecastTools(weather, place=config.briefing.city))
    if config.prayer.enabled:
        providers.append(
            PrayerTools(
                prayer or _prayer_place(config),
                ihtiyath_minutes=config.prayer.ihtiyath_minutes,
                now=wib_now,
            )
        )
    if pc is not None:
        providers.append(PcTools(pc, owner_call=owner_call))
    # `chat` checks with a view of its own: only `run` watches.
    network = internet or _internet_watch(config)
    if network is not None:
        providers.append(NetworkTools(network.check, _network_store(config)))
    if config.play_time.enabled:
        providers.append(PlayTimeTools(_play_time_store(config), config.play_time, now=wib_now))
        if config.app_reminders.enabled:
            providers.append(
                AppReminderTools(
                    _app_reminder_store(config),
                    _play_time_store(config),
                    config.play_time,
                    now=wib_now,
                )
            )
    if config.free_offers.enabled:
        providers.append(_offers_tools(config, notifier))
    if config.tepira.enabled:
        providers.append(_finance_tools(config, confirmations))
    if config.music.enabled:
        # Both backends: only Claude with her web search can look a song's link up, so only
        # she is offered one; any other brain opens YouTube's search page, never a link it made
        # up. Her name and wake words never ask for music.
        providers.append(
            MusicTools(
                service=config.music.service,
                names=[config.assistant.name, *config.assistant.wake_words],
                links=config.brain.backend == "claude" and config.web_search.enabled,
                startfile=open_in_browser,
            )
        )
    if config.vision.enabled and config.brain.backend == "claude":
        # Ollama's local model cannot see images, so it is never offered the screen. Her name,
        # wake words, and what she calls the owner are left out of Master's words, as for the
        # confirmation gate.
        providers.append(
            ScreenTools(
                capture=capture_screen,
                max_side=config.vision.max_side,
                names=[config.assistant.name, *config.assistant.wake_words, owner_call],
                camera_turn=camera_turn,
            )
        )
    # What Master copied on the PC, read only when his own words name it, with her names, wake
    # words, and what she calls the owner left out; both brains, no switch: guest mode closes it.
    providers.append(
        ClipboardTools(names=[config.assistant.name, *config.assistant.wake_words, owner_call])
    )
    if usage is not None:
        providers.append(UsageTools(usage.ledger, idr_per_usd=config.usage.idr_per_usd))
    on_usage = None if usage is None else usage.record
    network_note = None if internet is None else internet.state.outage_note
    if notes is None:
        return build_brain(
            config,
            registry,
            providers=providers,
            on_usage=on_usage,
            turn_images=turn_images,
            confirmations=confirmations,
            guest_mode=guest_mode,
            network_note=network_note,
        )
    return build_brain(
        config,
        registry,
        providers=providers,
        memory_section=lambda: memory_prompt_section(notes.facts()),
        on_usage=on_usage,
        turn_images=turn_images,
        confirmations=confirmations,
        guest_mode=guest_mode,
        network_note=network_note,
    )


def _describe_tool_call(call: ToolCallRecord) -> str:
    arguments = json.dumps(call.arguments, ensure_ascii=False)
    failed = " (gagal)" if call.is_error else ""
    return f"  [{call.name}] {arguments} -> {logged_result(call.name, call.result)}{failed}"


def _speak(
    speech: SpeechRouter,
    player: AudioPlayer,
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


def _prepare_nods(nods: NodBank) -> None:
    """Make her fillers on a daemon thread of their own, so neither her start nor Master's
    first words wait for them; one not made yet is simply not said. The thread tries the ones
    left out again until every filler is made."""
    threading.Thread(target=nods.keep_preparing, name="nod-voice", daemon=True).start()


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


# --- global hotkeys ------------------------------------------------------------------------------


def _hotkeys_on(config: AppConfig) -> bool:
    """Whether `waifu run` registers global hotkeys: on Windows, with any of them switched on."""
    return sys.platform == "win32" and config.hotkeys.enabled and bool(config.hotkeys.bindings)


def _listen_pause(config: AppConfig) -> ListenPause | None:
    """The pause of her hearing that Ctrl+Alt+Q starts, in its file; None without that hotkey."""
    settings = config.hotkeys
    if not _hotkeys_on(config) or settings.quiet is None:
        return None
    return ListenPause(settings.pause_path, hours=settings.quiet_hours)


def _cleared_listen_pause(config: AppConfig) -> ListenPause | None:
    """`run`'s listen pause, cleared: a pause never outlives a restart, which would leave her
    deaf without a word."""
    pause = _listen_pause(config)
    if pause is None:
        return None
    try:
        if pause.end():
            logger.info("Cleared a listen pause left by an earlier run")
    except OSError as exc:
        logger.warning("Could not clear the listen pause left by an earlier run: %s", exc)
    return pause


@contextmanager
def _masters_input(
    config: AppConfig,
    touches: _InteractionRelay,
    reactions: InteractionHandler,
    assistant: Assistant,
    pause: ListenPause | None,
) -> Iterator[None]:
    """Master's own input reaches her while the block runs: the avatar's touches and the global
    hotkeys, answered by the same interaction handler."""
    with touches.answered_by(reactions), _global_hotkeys(config, assistant, reactions, pause):
        yield


@contextmanager
def _global_hotkeys(
    config: AppConfig,
    assistant: Assistant,
    reactions: InteractionHandler,
    pause: ListenPause | None,
) -> Iterator[None]:
    """The global hotkeys of `hotkeys` while the block runs, on Windows; their registrations are
    recorded in HOTKEYS_FILE for `check` until the block ends. Nothing with them off."""
    if not _hotkeys_on(config):
        yield
        return
    hotkeys = [Hotkey(action, key) for action, key in config.hotkeys.bindings.items()]
    actions = HotkeyActions(
        reactions=reactions,
        interrupt=assistant.interrupt,
        repeat=assistant.repeat_now,
        speak=assistant.speak,
        ask=assistant.ask_now,
        pause=pause,
        owner_call=config.assistant.owner_call,
    )
    record = _HotkeysRecord()
    with actions, HotkeyThread(hotkeys, actions.press, on_lost=record.lost) as thread:
        record.registered(thread.registrations())
        try:
            yield
        finally:
            record.forget()


class _HotkeysRecord:
    """HOTKEYS_FILE while `run` holds the global hotkeys: their registrations, then none held once
    the hotkey thread lost them, then no file once `run` lets them go. A loss comes from that
    thread, maybe before the registrations are recorded: neither of the later two is undone."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._let_go = False  # lost or forgotten: nothing may claim them held any more

    def registered(self, registrations: Sequence[Registration]) -> None:
        with self._lock:
            if not self._let_go:
                self._write(registrations)

    def lost(self) -> None:
        with self._lock:
            if not self._let_go:
                self._let_go = True
                self._write(())

    def forget(self) -> None:
        with self._lock:
            self._let_go = True
            try:
                HOTKEYS_FILE.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove %s: %s", HOTKEYS_FILE, exc)

    @staticmethod
    def _write(registrations: Sequence[Registration]) -> None:
        try:
            write_status(HOTKEYS_FILE, os.getpid(), registrations)
        except OSError as exc:
            logger.warning("Could not record the global hotkeys in %s: %s", HOTKEYS_FILE, exc)


# --- speaker verification --------------------------------------------------------------------


def _speaker_verifier(config: AppConfig) -> SpeakerVerifier:
    speaker = config.speaker
    embedder = EcapaEmbedder(speaker.model_source, speaker.model_dir, speaker.device)
    return SpeakerVerifier(embedder, speaker.voiceprint_path, speaker.threshold)


def _open_mic(config: AppConfig) -> MicSource:
    """The mic `waifu run` listens through first (for enroll and speaker-test): the phone's pick,
    then `audio.input_device`'s list, then the Windows default, the first that opens."""
    return _mic_source(config, _voice_override(config))


def _mic_source(
    config: AppConfig,
    override: VoiceOverride,
    *,
    announce: Callable[[str], None] | None = None,
    on_swap: Callable[[], None] | None = None,
    live: bool = False,
) -> MicSource:
    """The mics in order of preference. `live` (`waifu run`) restarts PortAudio to find a mic
    that came back, unless a device is numbered, reads Windows' list of inputs as they come
    and go, and lists them for the Suara page."""
    audio = config.audio
    return MicSource(
        audio.input_preferences,
        open_stream=lambda device: MicStream(audio.sample_rate, audio.vad.frame_ms, device),
        sample_rate=audio.sample_rate,
        frame_ms=audio.vad.frame_ms,
        picked=override.input_device,
        announce=announce,
        on_swap=on_swap,
        restart=_portaudio_restart(audio) if live else None,
        present=windows_input_names if live and sys.platform == "win32" else None,
        listing=_input_listing if live else None,
    )


def _mic_notice(assistant: Assistant) -> Callable[[str], None]:
    """How she says by herself which mic she listens through now: queued like any notice, and
    dropped once MIC_NOTICE_S has passed unsaid."""

    def announce(text: str) -> None:
        assistant.announce(text, expires_at=datetime.now() + timedelta(seconds=MIC_NOTICE_S))

    return announce


def _portaudio_restart(audio: AudioConfig) -> Callable[[], None] | None:
    """`restart_portaudio`, or None when a device is configured by number: a number names a place
    in PortAudio's list, which a restart would move."""
    if isinstance(audio.output_device, int) or any(
        isinstance(device, int) for device in audio.input_preferences
    ):
        logger.info(
            "An audio device is configured by number, so PortAudio is never restarted: a mic "
            "that comes back is used again after waifu run restarts"
        )
        return None
    return restart_portaudio


def _input_listing() -> DeviceList:
    """The inputs and the Windows default for the Suara page (PortAudio's list)."""
    import sounddevice

    return input_devices(sounddevice), windows_default_input(sounddevice)


def _voice_override(config: AppConfig) -> VoiceOverride:
    """What Master picked on the phone's Suara page; nothing when its file cannot be read."""
    store = VoiceOverrideStore(config.audio.override_path)
    try:
        return store.read()
    except OSError as exc:
        logger.warning("Could not read the voice settings picked on the phone: %s", exc)
        return VoiceOverride()


def _vad_with(vad: VadConfig, override: VoiceOverride) -> VadConfig:
    """The config's VAD with the threshold and margin picked on the phone, if any."""
    picked = {"threshold_db": override.vad_threshold_db, "margin_db": override.vad_margin_db}
    return vad.model_copy(update={key: value for key, value in picked.items() if value is not None})


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
    phone_threshold = _voice_override(config).speaker_threshold
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
    suggested = _suggested_threshold(scores)
    if phone_threshold is None:
        print(
            f"Saran threshold: {suggested:.2f} (sekarang {config.speaker.threshold:.2f}). Cek "
            "dulu dengan 'waifu speaker-test', lalu ubah speaker.threshold di config.yaml."
        )
    else:
        print(
            f"Saran threshold: {suggested:.2f} (sekarang {phone_threshold:.2f}, disetel di "
            "halaman Suara di HP). Cek dulu dengan 'waifu speaker-test', lalu ubah ambangnya di "
            "halaman Suara; speaker.threshold di config.yaml baru dipakai lagi sesudah 'Reset "
            "ke default' di sana."
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
    # Judged as `waifu run` judges: with the threshold and the VAD gate set on the phone, if any.
    override = _voice_override(config)
    if override.speaker_threshold is not None:
        verifier.threshold = override.speaker_threshold
    segmenter = EnergySegmenter(config.audio.sample_rate, _vad_with(config.audio.vad, override))
    source = "" if override.speaker_threshold is None else " (disetel di HP)"
    print(
        "Silakan bicara (Ctrl+C untuk berhenti). "
        f"Threshold sekarang {verifier.threshold:.2f}{source}."
    )
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


def _quick_page(args: argparse.Namespace) -> int:
    """Serve the quick buttons on 127.0.0.1 and open them in the default browser until Ctrl+C.

    The phone's buttons (`phone.quick_actions`) on the devices (simulated with --dry-run). A
    routine's delayed steps go into her schedule for `waifu run`, as with `waifu routine run`;
    a dry run's into a schedule of its own that is gone when it ends. Unlike the phone's page,
    this one runs without `waifu run`, so when a button has delayed steps it says who runs
    them. A browser that cannot be opened leaves the page served at the printed address.
    """
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    with _session_schedule(config, dry_run=args.dry_run) as schedule:
        actions = QuickActions(
            registry,
            config.routines,
            schedule=schedule,
            chosen=config.phone.quick_actions,
            where=PC_WHERE,
            # Another process than `waifu run`: the bell's file shows a ring of the run's, and
            # the pause's file a pause of its hearing.
            alarm=_alarm_bell(config, schedule, dry_run=args.dry_run),
            listen_pause=_listen_pause(config),
        )
        groups = actions.groups()
        if not groups:
            print(f"{WARN} Belum ada tombol cepat: {QUICK_PAGE_HINT}.")
            return 1
        delayed = any(
            step.after_minutes is not None
            for group in groups
            for action in group.actions
            if action.routine
            for step in config.routines[action.routine].steps
        )
        mode = " (perangkat simulasi)" if args.dry_run else ""
        try:
            with DeskServer(actions, owner_call=config.assistant.owner_call) as server:
                try:
                    open_in_browser(server.url)
                except OSError as exc:
                    print(
                        f"{WARN} Browser tidak bisa dibuka otomatis ({exc}). Buka alamat di bawah "
                        "ini di browser."
                    )
                print(
                    f"Tombol cepat di PC: {server.url}{mode}. Tekan Ctrl+C untuk menutup.",
                    flush=True,
                )
                if delayed:
                    print(_delayed_steps_note(config, dry_run=args.dry_run), flush=True)
                while True:
                    sleep(AVATAR_WAIT_S)
        except KeyboardInterrupt:
            print("\nSelesai.")
    return 0


def _delayed_steps_note(config: AppConfig, *, dry_run: bool) -> str:
    """Who runs the delayed steps the quick buttons on the PC schedule: a dry run's schedule
    is gone when it ends, and her schedule runs only while a `waifu run` is on."""
    if dry_run:
        return DRY_RUN_DELAYED_STEPS.format(path=config.schedule.path)
    if _instance_running(RUN_LOCK):
        return DELAYED_STEPS_IN_RUN
    return f"{WARN} {DELAYED_STEPS_WITHOUT_RUN}"


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
    if args.fallback and not config.tts.fallback.enabled:
        print(f"{WARN} Suara cadangan mati (tts.fallback.enabled: false).")
        return 1
    rvc = config.tts.rvc
    if not (args.raw or rvc.enabled) and rvc.model is not None:
        # Comparing checkpoints this way would play the same plain voice for each one.
        print(
            f"{WARN} Suara anime mati (tts.rvc.enabled: false), jadi tts.rvc.model tidak dipakai."
        )
    emotion, text = split_emotion(" ".join(args.text))
    # Her voice level now (the file read, never created), or the one to try, saved nowhere.
    volume: int | None = args.volume
    level = _voice_level(config).level_now if volume is None else lambda: volume
    with _avatar_bus(config) as avatar, _voice_converter(config, wanted=not args.raw) as converter:
        spoken = _speak(
            build_speech_router(
                config.tts,
                converter=converter,
                fallback_only=args.fallback,
                lexicon=_lexicon(config).readings,
            ),
            VolumePlayer(Player(config.audio.output_device), level),
            text,
            avatar=avatar,
            emotion=emotion,
            viewer_wait_s=_avatar_viewer_wait_s(config),
        )
    return 0 if spoken else 1


def _voice_level(config: AppConfig) -> VoiceLevel:
    """Her own voice level and whisper mode, in the file `run`, `chat`, and `say` share."""
    return VoiceLevel(config.voice.state_path, config.voice.night)


def _mics(args: argparse.Namespace) -> int:
    print(list_devices())
    return 0


def _mic(args: argparse.Namespace) -> int:
    """Every input device, marked with the one Cyrene uses and the Windows default, then each
    one's levels while Master speaks, and the config line for the one that heard him best.
    Nothing is saved. Runs beside `waifu run`: its streams are shared, like a second app's."""
    config = load_config(args.config)
    import sounddevice

    devices = input_devices(sounddevice)
    if not devices:
        print(f"{FAIL} {NO_MIC_FOUND}")
        return 0
    picked, used = _list_mics(sounddevice, devices, config)
    print(
        f"\nSebut 'halo Cyrene' beberapa kali selama {args.seconds:g} detik di tiap mikrofon, "
        "dengan suara biasa. Rekamannya tidak disimpan."
    )
    heard = _measure_mics(sounddevice, devices, args.seconds, config.audio)
    print()
    _suggest_mic(heard, used, picked)
    return 0


def _list_mics(
    sd: Any, devices: list[InputDevice], config: AppConfig
) -> tuple[str | None, str | None]:
    """Says which mic Cyrene uses and lists `devices`, marking it and the Windows default; the
    mic picked on the phone, if any, and the name of the mic in use."""
    default = windows_default_input(sd)
    picked = _voice_override(config).input_device
    used = _mic_in_use(sd, picked, config.audio.input_preferences, default)
    print("Mikrofon di PC ini:")
    for number, device in enumerate(devices, start=1):
        marks = [
            mark
            for mark, name in (("dipakai Cyrene", used), ("default Windows", default))
            if name is not None and same_device_name(device.name, name)
        ]
        print(f"  {number}. {device.config_value}" + (f"  <- {', '.join(marks)}" if marks else ""))
    return picked, used


def _measure_mics(
    sd: Any, devices: list[InputDevice], seconds: float, audio: AudioConfig
) -> list[tuple[InputDevice, Levels]]:
    """Listens to each of `devices` in turn while Master speaks, saying each one's levels; the
    ones that heard speech, with their levels."""
    heard: list[tuple[InputDevice, Levels]] = []
    for number, device in enumerate(devices, start=1):
        print(f"[{number}/{len(devices)}] {device.config_value}: mendengarkan...", flush=True)
        try:
            found = measure(sd, device, seconds, audio)
        except MicrophoneError as exc:  # it opened, then stopped delivering audio
            print(f"  {FAIL} {exc}")
            continue
        except Exception as exc:  # unplugged, a headset that went to sleep, a driver error
            print(f"  {FAIL} Tidak bisa dibuka: {exc}")
            continue
        print(f"  {_mic_levels(found)}")
        if found.heard_speech:
            heard.append((device, found))
    return heard


def _mic_in_use(
    sd: Any, picked: str | None, listed: list[int | str], default: str | None
) -> str | None:
    """The name of the input Cyrene opens first, said: the mic `picked` on the phone, then the
    first of `listed` (`audio.input_device`) that resolves, then the Windows `default`, as
    `waifu run` falls back to it; None (after saying why) when there is none."""
    if picked is None and not listed:
        if default is None:
            print(f"{FAIL} audio.input_device kosong, dan Windows tidak punya mic default.")
        else:
            print(
                f"Cyrene memakai mic default Windows, sekarang {default} (audio.input_device "
                "kosong). Default yang berubah baru dipakai setelah 'waifu run' dijalankan ulang."
            )
        return default
    sources = [("mic yang dipilih di HP", picked)] if picked is not None else []
    for source, value in [*sources, *(("audio.input_device", value) for value in listed)]:
        try:
            name = sd.query_devices(value, "input")["name"]
        except Exception as exc:  # a name that matches no input, or several
            print(f"{FAIL} {source} {value!r} tidak bisa dipakai: {exc}")
            continue
        print(f"Cyrene memakai {source} {value!r}: {name}.")
        return name
    if default is None:
        print(f"{FAIL} Windows juga tidak punya mic default.")
    else:
        print(f"Jadi Cyrene memakai mic default Windows, sekarang {default}.")
    return default


def _mic_levels(found: Levels) -> str:
    """One measurement: its levels, its bar, and whether the VAD would have heard speech."""
    if found.silent:
        verdict = (
            f"{FAIL} hampir tanpa suara: mic di-mute atau diblokir Windows, atau headset "
            "Bluetooth belum siap (coba --seconds 5)"
        )
    elif found.heard_speech:
        verdict = f"{OK} suara terdengar"
    else:
        verdict = f"{FAIL} suara tidak terdengar (butuh {found.speech_bar_db:.0f} dB)"
    loudness = f"puncak {found.peak_db:.0f} dB, RMS {found.rms_db:.0f} dB"
    return f"{loudness} {level_bar(found)} {verdict}"


def _suggest_mic(
    heard: list[tuple[InputDevice, Levels]], used: str | None, picked: str | None
) -> None:
    """Which mic to use: the one whose speech level stood furthest above the VAD's bar, picked
    on the phone while a phone's pick leads (`picked`), else in audio.input_device."""
    if not heard:
        print(f"{FAIL} {NO_MIC_HEARD.format(command='waifu mic')}")
        return
    best, _ = max(heard, key=lambda pair: _speech_margin(pair[1]))
    if used is not None and same_device_name(best.name, used):
        print(f"{OK} Suaramu paling jelas di mic yang dipakai Cyrene: {best.name}.")
        return
    if picked is not None:
        print(
            f"{OK} Suaramu paling jelas di {best.name}. Mic yang dipilih di HP selalu dicoba "
            "duluan, jadi pilih mic itu di halaman Suara di HP (Ganti mic, lalu Pakai). Lalu "
            "daftarkan ulang suaramu dengan 'waifu enroll' lewat mic itu."
        )
        return
    print(
        f"{OK} Suaramu paling jelas di {best.name}. Supaya Cyrene memakainya, isi "
        "audio.input_device di config.yaml (baris ini di bawah audio:):"
    )
    print(f"    {best.config_line}")
    print(
        "Lalu jalankan ulang 'waifu run', dan daftarkan ulang suaramu dengan 'waifu enroll' "
        "lewat mic itu."
    )


def _speech_margin(found: Levels) -> float:
    """How far Master's speech stood above the VAD's bar: the higher, the clearer he is heard."""
    return found.speech_db - found.speech_bar_db


def _voices(args: argparse.Namespace) -> int:
    """The speech voices installed in Windows per language, then each language's fallback."""
    config = load_config(args.config)
    try:
        voices = list_windows_voices()
    except WindowsVoiceError as exc:
        print(f"{FAIL} Suara Windows tidak terbaca: {exc}")
        return 1
    if voices:
        print(f"Suara Windows di PC ini ({_VOICE_APIS.get(voices[0].api, voices[0].api)}):")
        in_order = sorted(voices, key=lambda voice: (voice.language, voice.name))
        for language, group in groupby(in_order, key=lambda voice: voice.language):
            print(f"  {language}: {', '.join(_voice_with_gender(voice) for voice in group)}")
    else:
        print("Belum ada suara Windows di PC ini.")
    if config.tts.fallback.enabled:
        lines = list(_fallback_voice_lines(config.tts.fallback, voices))
    else:
        lines = [_fallback_off_check(config)]
    for mark, text in lines:
        print(f"{mark} {text}")
    return 0


def _voice_with_gender(voice: WindowsVoice) -> str:
    """`Microsoft Zira (perempuan)`; a voice of another gender by its name alone."""
    gender = _GENDERS.get(voice.gender.lower())
    return f"{voice.name} ({gender})" if gender else voice.name


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


def _game_tools(
    config: AppConfig, gacha: GachaParts | None, *, game_data: GameDataTools | None = None
) -> list[ToolProvider]:
    """The game tools while gacha is on, with `game_data` as the data tools when given."""
    if gacha is None:
        return []
    tools: list[ToolProvider] = [
        GachaTools(
            gacha.hoyolab,
            gacha.history,
            config.gacha.games,
            server_utc_offset=config.gacha.server_utc_offset,
        ),
        game_data if game_data is not None else GameDataTools(gacha.hoyolab, config.gacha.games),
        IncomeTools(
            gacha.hoyolab,
            config.gacha.games,
            server_utc_offset=config.gacha.server_utc_offset,
        ),
    ]
    if config.redeem_codes.enabled:
        tools.append(CodeTools(_code_store(config), gacha.hoyolab, config.gacha.games))
    if config.weekly_report.enabled:
        tools.append(_weekly_report_tools(config, gacha))
    return tools


def _game_data_tools(
    config: AppConfig,
    gacha: GachaParts | None,
    *,
    on_roster: Callable[[str, list[str]], None] | None = None,
) -> GameDataTools | None:
    """The data tools over Master's game accounts while gacha is on; `on_roster` gets each
    roster they read."""
    if gacha is None:
        return None
    return GameDataTools(gacha.hoyolab, config.gacha.games, on_roster=on_roster)


def _weekly_report_tools(config: AppConfig, gacha: GachaParts) -> WeeklyReportTools:
    """The week in the configured games from the pull history in `gacha.data_dir`, HoYoLAB
    (this month's income too), with the redeem codes on, the codes `waifu run` keeps, with
    play time on, how long each game was open, and with the internet watcher on, its outages;
    the week starts from the report's slot even when the report is off, so `waifu gacha report`
    shows her week."""
    return WeeklyReportTools(
        config.gacha.games,
        history=gacha.history,
        store=GachaStore(config.gacha.data_dir),
        source=gacha.hoyolab,
        codes=_code_store(config) if config.redeem_codes.enabled else None,
        report_day=config.weekly_report.day,
        report_time=config.weekly_report.time,
        server_utc_offset=config.gacha.server_utc_offset,
        extra_standard=config.gacha.off_banner,
        play_time=_play_time_store(config) if config.play_time.enabled else None,
        income=gacha.hoyolab,
        network=_network_store(config) if config.network.enabled else None,
    )


def _gacha_watcher(
    config: AppConfig,
    gacha: GachaParts | None,
    assistant: Assistant,
    game_mode: GameMode | None,
    *,
    guest_mode: Callable[[], bool],
    closed_games: ClosedGames | None,
    read_calendar: CalendarReader | None,
) -> AbstractContextManager[object]:
    """The reminders and check-in while she listens, also pushed to the phone when
    notifications are on; nothing to watch without the cookie. Its planner reads the event
    calendars through `read_calendar`, is silent while `guest_mode` says guests are around,
    takes the games Master closed from `closed_games`, and counts the morning from the
    briefing's time."""
    if gacha is None or config.gacha.cookie is None:
        return nullcontext()
    notifier = build_notifier(config.notify)
    return GachaWatcher(
        gacha.hoyolab,
        config.gacha,
        notifier=notifier,
        announce=_announcer(assistant, game_mode, pushed=notifier is not None),
        title=config.assistant.name,
        owner_call=config.assistant.owner_call,
        read_calendar=read_calendar,
        guest_mode=guest_mode,
        closed_games=closed_games,
        morning=config.briefing.time,
    )


def _closed_games(config: AppConfig) -> ClosedGames | None:
    """What the play time watcher tells the gacha planner, only while both run with the planner
    on: the gacha watcher (`gacha.enabled` and a cookie) takes what the other puts."""
    gacha = config.gacha
    watched = gacha.enabled and gacha.cookie is not None
    if watched and gacha.reminders.planner and config.play_time.enabled:
        return ClosedGames()
    return None


def _code_store(config: AppConfig) -> CodeStore:
    """The redeem codes she knows, what redeeming them did, and which she told, in the file
    `redeem_codes.state_path` names (`run`, `chat`, and `waifu gacha codes` share it)."""
    return CodeStore(config.redeem_codes.state_path)


def _codes_reader() -> ReadCodes:
    """Reads the given games' redeem codes from the community source, with a connection of its
    own each time (reads are hours apart)."""

    def read(games: Sequence[GameId]) -> dict[GameId, list[tuple[str, str]]] | CodesError:
        with httpx.Client() as http:
            return read_codes(games, http=http)

    return read


@contextmanager
def _code_watcher(
    config: AppConfig,
    gacha: GachaParts | None,
    assistant: Assistant,
    notifier: Notifier | None,
    game_mode: GameMode | None,
) -> Iterator[None]:
    """Her notices about new redeem codes while the block runs (redeemed first with
    `auto_redeem`), also pushed to the phone when notifications are on; with their notices off
    the watcher only keeps the codes fresh for her answers, and none runs with the games or
    the codes off. Listing codes needs no cookie, so it watches without one."""
    codes = config.redeem_codes
    if gacha is None or not codes.enabled:
        yield
        return
    watcher = CodeWatcher(
        _code_store(config),
        _codes_reader(),
        gacha.hoyolab,
        codes,
        config.gacha.games,
        announce=_announcer(assistant, game_mode, pushed=notifier is not None),
        notifier=notifier,
        owner_call=config.assistant.owner_call,
        title=config.assistant.name,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


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
    top = _top_word(game)
    for stats in report.stats:
        print(_pity_line(stats))
        if stats.recent_tops:
            tops = ", ".join(_top_pull_text(pull) for pull in stats.recent_tops)
            print(f"  {top} terakhir: {tops}")
    if report.hint:
        print(f"{WARN} {report.hint}")
    return 0 if report.problem is None else 1


def _top_word(game: Game) -> str:
    return "S-rank" if game.id == "zzz" else "5★"


def _pity_line(stats: BannerStats) -> str:
    guarantee = {True: ", berikutnya pasti limited", False: ", berikutnya 50/50", None: ""}
    return (
        f"{stats.banner.name}: pity {stats.pity}/{stats.banner.hard_pity}"
        f"{guarantee[stats.guaranteed]}, total {stats.total} pull"
    )


def _top_pull_text(pull: TopPull) -> str:
    result = {True: ", menang 50/50", False: ", kalah 50/50", None: ""}[pull.won_5050]
    return f"{pull.name} ({pull.pulls} pull{result})"


def _gacha_data(args: argparse.Namespace) -> int:
    """Master's account in one game, read from HoYoLAB on every call (only the brain's tools
    cache it). Times left are counted after the read, which may take a while."""
    source: GameDataSource = build_gacha(load_config(args.config)).hoyolab
    views = {"characters": _characters_view, "endgame": _endgame_view, "events": _events_view}
    try:
        return views[args.view](args.game, source)
    except GachaError as error:
        print(f"{FAIL} {error.message}")
        return 1


def _characters_view(game: str, source: GameDataSource) -> int:
    if game == "hsr":
        return _print_hsr_characters(source.hsr_characters())
    if game == "genshin":
        return _print_genshin_characters(source.genshin_characters())
    return _print_zzz_agents(source.zzz_characters())


def _endgame_view(game: str, source: GameDataSource) -> int:
    if game == "hsr":
        hsr_modes = source.hsr_endgame(list(ENDGAME_MODES))
        return _print_hsr_endgame(hsr_modes, datetime.now().astimezone())
    asked = list(GAME_MODES[game])
    modes = source.genshin_endgame(asked) if game == "genshin" else source.zzz_endgame(asked)
    return _print_endgame(game, modes, datetime.now().astimezone())


def _events_view(game: str, source: GameDataSource) -> int:
    if game == "hsr":
        hsr_calendar = source.hsr_calendar()
        _print_hsr_calendar(hsr_calendar, datetime.now().astimezone())
        return 0
    calendar = source.genshin_calendar() if game == "genshin" else source.zzz_calendar()
    _print_game_calendar(game, calendar, datetime.now().astimezone())
    return 0


def _print_hsr_characters(characters: Sequence[HsrCharacter]) -> int:
    if not characters:
        print(f"{WARN} Battle Chronicle HoYoLAB tidak memuat satu pun karakter HSR.")
        return 1
    # The Path tells apart the characters HoYoLAB names alike: the Trailblazer, March 7th.
    for character in sorted(characters, key=lambda c: (-c.rarity, -c.level, c.name, c.path)):
        cone = character.light_cone
        equipped = (
            f"{cone.name} Lv{cone.level} S{cone.superimposition}" if cone else "tanpa Light Cone"
        )
        print(
            f"{character.name} ★{character.rarity} Lv{character.level} E{character.eidolon} "
            f"({character.path}), {equipped}"
        )
    return 0


def _print_genshin_characters(characters: Sequence[GenshinCharacter]) -> int:
    ordered = sorted(characters, key=lambda c: (-c.rarity, -c.level, c.name))
    return _print_roster(
        [
            f"{c.name} ★{c.rarity} Lv{c.level} C{c.constellation} ({c.element}, {c.weapon_type}), "
            f"{c.weapon.name} Lv{c.weapon.level} R{c.weapon.refinement}"
            for c in ordered
        ],
        "karakter Genshin",
    )


def _print_zzz_agents(agents: Sequence[ZzzAgent]) -> int:
    # S-rank agents first, then A-rank, each by level.
    ordered = sorted(agents, key=lambda a: (a.rarity != "S", -a.level, a.name))
    return _print_roster(
        [
            f"{a.name} {a.rarity} Lv{a.level} M{a.mindscape} ({a.element}, {a.specialty})"
            for a in ordered
        ],
        "agent ZZZ",
    )


def _print_roster(lines: list[str], whom: str) -> int:
    if not lines:
        print(f"{WARN} Battle Chronicle HoYoLAB tidak memuat satu pun {whom}.")
        return 1
    for line in lines:
        print(line)
    return 0


def _print_hsr_endgame(results: Mapping[str, HsrEndgame | GachaError], now: datetime) -> int:
    failed = False
    for mode, name in ENDGAME_MODES.items():
        result = results[mode]
        if isinstance(result, GachaError):
            print(f"{name}: {FAIL} {result.message}")
            failed = True
            continue
        parts = _hsr_endgame_facts(result) if result.has_data else ["belum dimainkan musim ini"]
        print(_labelled(name, [*parts, *_ends(result.ends_at, now)]))
    return 1 if failed else 0


def _hsr_endgame_facts(endgame: HsrEndgame) -> list[str]:
    extra = f" (+{endgame.extra_stars} ekstra)" if endgame.extra_stars else ""
    facts = [f"{endgame.stars} bintang{extra}"]
    if endgame.deepest_floor:
        facts.append(f"lantai terdalam {endgame.deepest_floor}")
    return facts


def _print_endgame(game: str, results: Mapping[str, Endgame | GachaError], now: datetime) -> int:
    """This season of each Genshin or ZZZ mode, one line each; a failed mode fails the view."""
    failed = False
    for mode, name in GAME_MODES[game].items():
        result = results[mode]
        if isinstance(result, GachaError):
            print(f"{name}: {FAIL} {result.message}")
            failed = True
            continue
        facts = _endgame_facts(result) if result.has_data else ["belum dimainkan musim ini"]
        print(_labelled(name, [*facts, *_ends(result.ends_at, now)]))
    return 1 if failed else 0


def _endgame_facts(endgame: Endgame) -> list[str]:
    """The facts a mode gives, in the order they are printed."""
    score: int | str | None = endgame.score
    if score is not None and endgame.max_score is not None:
        score = f"{score}/{endgame.max_score}"
    facts = (
        ("{} bintang", endgame.stars),
        ("terdalam {}", endgame.deepest),
        ("difficulty {}", endgame.difficulty),
        ("rating {}", endgame.rating),
        ("skor {}", score),
        ("{} detik", endgame.seconds),
    )
    return [text.format(value) for text, value in facts if value is not None]


def _print_hsr_calendar(calendar: HsrCalendar, now: datetime) -> None:
    print(f"Kalender HSR versi {calendar.version}" if calendar.version else "Kalender HSR")
    _print_section("Warp", [_hsr_warp_text(warp, now) for warp in calendar.warps])
    _print_running_events(calendar.events, now)
    _print_running_windows(calendar.challenges, now)


def _print_game_calendar(game: str, calendar: GameCalendar, now: datetime) -> None:
    """A Genshin or ZZZ calendar, printed as the HSR one is."""
    short = GAMES[game].short
    print(f"Kalender {short} versi {calendar.version}" if calendar.version else f"Kalender {short}")
    tops = ("S", "A") if game == "zzz" else ("5★", "4★")
    _print_section("Banner", [_game_banner_text(banner, tops, now) for banner in calendar.banners])
    # A ZZZ event's progress is the Polychrome it gives, so it is printed with its unit.
    _print_running_events(calendar.events, now, unit=EVENT_REWARD if game == "zzz" else None)
    if game == "genshin":  # ZZZ's calendars carry no endgame windows
        _print_running_windows(calendar.challenges, now)


def _print_running_events(
    events: Sequence[HsrEvent | GameEvent], now: datetime, *, unit: str | None = None
) -> None:
    # A calendar keeps upcoming events and endgame windows; only the running ones are
    # listed, the soonest end first ("which event ends soon?"), events without an end last.
    running = sorted(
        (event for event in events if is_running(event.starts_at, event.ends_at, now)),
        key=lambda e: (e.ends_at is None, e.ends_at or now),
    )
    _print_section("Event", [_event_text(event, now, unit) for event in running])


def _print_running_windows(
    windows: Sequence[HsrChallengeWindow | ChallengeWindow], now: datetime
) -> None:
    running = [window for window in windows if is_running(window.starts_at, window.ends_at, now)]
    _print_section("Endgame", [_window_text(window, now) for window in running])


def _print_section(title: str, lines: list[str]) -> None:
    if not lines:
        print(f"{title}: tidak ada")
        return
    print(f"{title}:")
    for line in lines:
        print(f"  {line}")


def _hsr_warp_text(warp: HsrWarp, now: datetime) -> str:
    return _banner_text(warp, "Warp", HSR_WARP_KINDS[warp.kind], ("5★", "4★"), now)


def _game_banner_text(banner: Banner, tops: tuple[str, str], now: datetime) -> str:
    return _banner_text(banner, "Banner", BANNER_KINDS.get(banner.kind, banner.kind), tops, now)


def _banner_text(
    banner: HsrWarp | Banner, word: str, kind: str, tops: tuple[str, str], now: datetime
) -> str:
    """One banner: `tops` names its two rarities, such as 5★ and 4★ or S and A."""
    top, second = tops
    parts = []
    if banner.featured:
        parts.append(f"{top} {', '.join(banner.featured)}")
    if banner.four_stars:
        parts.append(f"{second} {', '.join(banner.four_stars)}")
    if banner.starts_at is not None and banner.starts_at > now:
        parts.append(_moment("mulai", banner.starts_at, now))
    else:
        parts.extend(_ends(banner.ends_at, now))
    # Semicolons: the names inside a part are already separated by commas.
    # HoYoLAB's calendars often name no banner (ZZZ's never do): the kind and the featured
    # names say which one it is.
    label = f"{banner.name} ({kind})" if banner.name else f"{word} {kind}"
    return _labelled(label, parts, separator="; ")


def _event_text(event: HsrEvent | GameEvent, now: datetime, unit: str | None) -> str:
    progress = ["selesai"] if event.done else _progress(event.current, event.total, unit)
    return _labelled(event.name, [*progress, *_ends(event.ends_at, now)])


def _window_text(window: HsrChallengeWindow | ChallengeWindow, now: datetime) -> str:
    parts = [*_progress(window.current, window.total), *_ends(window.ends_at, now)]
    mode = GAME_MODES[MODE_GAMES[window.mode]][window.mode]
    return _labelled(f"{mode} ({window.name})" if window.name else mode, parts)


def _progress(current: int | None, total: int | None, unit: str | None = None) -> list[str]:
    """`current/total`, with what it counts when that is said (`240/300 Polychrome`)."""
    if not total:
        return []
    return [f"{current}/{total} {unit}" if unit else f"{current}/{total}"]


def _ends(ends_at: datetime | None, now: datetime) -> list[str]:
    return [] if ends_at is None else [_moment("berakhir", ends_at, now)]


def _moment(word: str, moment: datetime, now: datetime) -> str:
    """`berakhir 2026-10-20 03:59 (4 hari 12 jam lagi)`, in local time."""
    return f"{word} {moment.astimezone():%Y-%m-%d %H:%M} ({_time_left(moment - now)})"


def _time_left(left: timedelta) -> str:
    minutes = math.ceil(left.total_seconds() / 60)
    if minutes <= 0:
        return "sudah lewat"
    days, minutes = divmod(minutes, 24 * 60)
    if not days:
        return f"{duration_text(minutes)} lagi"
    hours = minutes // 60
    return f"{days} hari {hours} jam lagi" if hours else f"{days} hari lagi"


def _labelled(label: str, parts: list[str], *, separator: str = ", ") -> str:
    return f"{label}: {separator.join(parts)}" if parts else label


def _gacha_income(args: argparse.Namespace) -> int:
    """Print what each game's monthly diary on HoYoLAB says Master earned in a month, read on
    every call, without the brain; it works whether or not `gacha.enabled` is on. A month
    HoYoLAB no longer keeps is refused (exit 2) without asking it; exit 1 when a game could not
    be read, the others printed all the same."""
    config = load_config(args.config)
    months = income_months(datetime.now().astimezone(), config.gacha.server_utc_offset)
    month = date(*args.month, 1) if args.month else months[0]
    if month not in months:
        kept = ", ".join(month_text(each) for each in months)
        print(f"{FAIL} Pendapatan hanya ada untuk bulan ini dan dua bulan sebelumnya: {kept}.")
        return 2
    this_month = month == months[0]
    games = [args.game] if args.game else config.gacha.games
    results = build_gacha(config).hoyolab.income(games, month, this_month=this_month)
    so_far = ", sampai sekarang" if this_month else ""
    print(f"Pendapatan gacha {MONTHS_ID[month.month - 1]} {month.year}{so_far}:")
    shown = "bulan ini" if this_month else MONTHS_ID[month.month - 1]
    failed = False
    for game_id, result in results.items():
        game = GAMES[game_id]
        print(f"{game.short} ({game.name})")
        if isinstance(result, GachaError):
            print(f"  {FAIL} {result.message}")
            failed = True
            continue
        for line in _income_lines(game, result, shown):
            print(f"  {line}")
    print(INCOME_NOTE)
    return 1 if failed else 0


def _income_lines(game: Game, income: GameIncome, shown: str) -> list[str]:
    """The premium currency with its pulls and day figures, then the month before, the sources,
    and the other currencies, each when the diary gives it. HoYoLAB's change is the month
    shown against the one before, so it names the month `shown` (bulan ini, or its name)."""
    days = [f"hari ini {_thousands(income.today)}"] if income.today is not None else []
    if income.yesterday is not None:
        days.append(f"kemarin {_thousands(income.yesterday)}")
    earned = f"{_thousands(income.earned)} ({income.pulls} pull)"
    lines = [", ".join([f"{game.currency}: {earned}", *days])]
    if income.last_month is not None:
        change = income.change_percent
        percent = "" if change is None else f"{change:+d}%" if change else "0%"
        compared = f"; {shown} {percent} dibanding itu" if percent else ""
        lines.append(f"Bulan sebelumnya: {_thousands(income.last_month)}{compared}")
    if income.sources:
        sources = (
            f"{each.name} {_thousands(each.amount)} ({each.percent}%)" for each in income.sources
        )
        lines.append(f"Sumber: {', '.join(sources)}")
    if income.others:
        others = (f"{each.name} {_thousands(each.amount)}" for each in income.others)
        lines.append(f"Lainnya: {', '.join(others)}")
    return lines


def _thousands(amount: int) -> str:
    """`1.234.567`: a dot per thousand, as Indonesian writes numbers."""
    return f"{amount:,}".replace(",", ".")


def _gacha_report(args: argparse.Namespace) -> int:
    """Print the last 7 days in Master's games as her weekly report gathers them, without the
    brain, and the internet outages when there were any; it works whether or not
    `gacha.enabled` and `weekly_report.enabled` are on. Exit 1 when a part of a game or the
    internet history could not be read; the rest is printed all the same."""
    config = load_config(args.config)
    codes_label = UNREDEEMED_CODES if config.gacha.enabled else UNREDEEMED_STORED_CODES
    print("Membaca riwayat pull dan HoYoLAB, tunggu sebentar...", flush=True)
    report = _weekly_report_tools(config, build_gacha(config)).gather()
    since = report.since
    print(
        f"Laporan game 7 hari terakhir, sejak {WEEKDAYS_ID[since.weekday()]}, "
        f"{since:%d-%m-%Y %H:%M}"
    )
    failed = False
    for week in report.games:
        print(f"{week.game.short} ({week.game.name})")
        for line in _game_week_lines(week, report.now, codes_label):
            print(f"  {line}")
        failed = failed or bool(week.problems)
    if report.network_problem is not None:
        print(f"{FAIL} {report.network_problem}")
        failed = True
    elif report.network is not None and report.network.count:
        outages = report.network
        total = _play_duration(outages.seconds)
        print(f"Internet putus minggu ini: {outages.count} kali, total {total}")
    print(WEEKLY_REPORT_NOTE)
    return 1 if failed else 0


def _game_week_lines(week: GameWeek, now: datetime, codes_label: str) -> list[str]:
    """One game's week: its pulls and pity, its endgame, what ends within 7 days, its codes
    after `codes_label`, its play time, and this month's income, each problem marked where its
    part would be."""
    lines = []
    if week.pulls_problem is not None:
        # With a pity the stored history still answered; without one nothing did.
        lines.append(f"{WARN if week.pity else FAIL} {week.pulls_problem}")
    if week.pity:
        lines += _week_pulls_lines(week)
    lines += [_season_line(week.game, season, now) for season in week.endgame]
    if week.endgame_problem is not None:
        lines.append(f"{FAIL} {week.endgame_problem}")
    if week.calendar_problem is not None:
        lines.append(f"{FAIL} {week.calendar_problem}")
    else:
        ending = [_ending_banner_text(week.game, banner, now) for banner in week.banners]
        unit = EVENT_REWARD if week.game.id == "zzz" else None
        ending += [_event_text(event, now, unit) for event in week.events]
        lines.append("Berakhir dalam 7 hari:" if ending else "Berakhir dalam 7 hari: tidak ada")
        lines += [f"  {line}" for line in ending]
    if week.codes_problem is not None:
        lines.append(f"{FAIL} {week.codes_problem}")
    elif week.codes is not None:
        lines.append(f"{codes_label}: {', '.join(week.codes) or 'tidak ada'}")
    if week.play_problem is not None:
        lines.append(f"{FAIL} {week.play_problem}")
    elif week.play is not None:
        lines.append(f"Jam main minggu ini: {_week_play_text(week.play)}")
    if week.income_problem is not None:
        lines.append(f"{FAIL} {week.income_problem}")
    elif week.income is not None:
        lines.append(f"Pendapatan bulan ini: {_month_income_text(week.game, week.income)}")
    return lines


def _month_income_text(game: Game, income: GameIncome) -> str:
    """`5.400 Primogem (33 pull), hari ini 120`."""
    text = f"{_thousands(income.earned)} {game.currency} ({income.pulls} pull)"
    return text if income.today is None else f"{text}, hari ini {_thousands(income.today)}"


def _week_play_text(play: PlayTotal) -> str:
    """`5 jam 20 menit dalam 6 sesi`, or `belum main`."""
    if not play.sessions:
        return "belum main"
    return f"{_play_duration(play.seconds)} dalam {play.sessions} sesi"


def _week_pulls_lines(week: GameWeek) -> list[str]:
    count = sum(banner.count for banner in week.pulls)
    if not count:
        lines = ["Pull minggu ini: belum ada"]
    else:
        banners = ", ".join(f"{banner.banner.name} {banner.count}" for banner in week.pulls)
        lines = [f"Pull minggu ini: {count} ({banners})"]
    tops = sorted(
        (top for banner in week.pulls for top in banner.tops),
        key=lambda top: (top.time, top.id),
        reverse=True,
    )
    if tops:
        pulled = ", ".join(_top_pull_text(top) for top in tops)
        lines.append(f"{_top_word(week.game)} minggu ini: {pulled}")
    lines += [_pity_line(stats) for stats in week.pity]
    if week.hint:
        lines.append(f"{WARN} {week.hint}")
    return lines


def _season_line(game: Game, season: HsrEndgame | Endgame, now: datetime) -> str:
    """This season of one mode; a season ending within 7 days ends `segera`."""
    if not season.has_data:
        facts = ["belum dimainkan musim ini"]
    elif isinstance(season, HsrEndgame):
        facts = _hsr_endgame_facts(season)
    else:
        facts = _endgame_facts(season)
    ends_at = season.ends_at
    if ends_at is not None and now < ends_at <= now + WEEK:
        end = [_moment("segera berakhir", ends_at, now)]
    else:
        end = _ends(ends_at, now)
    return _labelled(GAME_MODES[game.id][season.mode], [*facts, *end])


def _ending_banner_text(game: Game, banner: HsrWarp | Banner, now: datetime) -> str:
    if isinstance(banner, HsrWarp):
        return _hsr_warp_text(banner, now)
    return _game_banner_text(banner, ("S", "A") if game.id == "zzz" else ("5★", "4★"), now)


def _gacha_codes(args: argparse.Namespace) -> int:
    """Read the redeem codes fresh, keep them, and print each game's codes to redeem and the
    last ones tried; with --redeem, redeem one game's codes to redeem instead and print each
    result. Exit 1 when a game could not be read or a code is left to redeem again. It works
    whether or not `gacha.enabled` and `redeem_codes.enabled` are on."""
    if args.redeem and args.game is None:
        print(f"{FAIL} {REDEEM_NEEDS_GAME}")
        return 2
    config = load_config(args.config)
    games: list[GameId] = [args.game] if args.game else config.gacha.games
    store = _code_store(config)
    new, every_game_read = _keep_fresh_codes(store, games)
    if args.redeem:
        settled = _redeem_kept_codes(build_gacha(config).hoyolab, store, args.game)
        return 0 if every_game_read and settled else 1
    for game in games:
        _print_codes(store, game, new)
    print(CODES_CREDIT)
    return 0 if every_game_read else 1


def _keep_fresh_codes(
    store: CodeStore, games: Sequence[GameId]
) -> tuple[set[tuple[GameId, str]], bool]:
    """Read the games' codes from the source and keep them: the new ones as (game, code), and
    whether every game was read. A game that could not be read is pointed out; its kept codes
    still count. They stay unannounced, so a running `waifu run` still pushes them."""
    found = _codes_reader()(games)
    if isinstance(found, CodesError):
        print(f"{WARN} {found} Ini kode yang tersimpan.")
        return set(), False
    now = datetime.now().astimezone()
    new: set[tuple[GameId, str]] = set()
    for game in games:
        if game not in found:
            print(
                f"{WARN} Kode {GAMES[game].short} tidak terbaca dari {CODES_SOURCE}; "
                "ini yang tersimpan."
            )
            continue
        new.update((game, code.code) for code in store.add(game, found[game], now))
    return new, all(game in found for game in games)


def _print_codes(store: CodeStore, game: GameId, new: set[tuple[GameId, str]]) -> None:
    """`5S6ZHRWTDNJB  60 stellar jade  (baru)` for each code still to redeem, then the last
    ones tried with what happened, as the redeem_codes_list tool gives them."""
    print(f"{GAMES[game].short} ({GAMES[game].name})")
    to_redeem = [
        f"  {code.code}  {code.rewards or 'hadiah tidak disebutkan'}  "
        f"({_first_seen_text(code, (game, code.code) in new)})"
        for code in store.unredeemed(game)
    ]
    _print_section("  Belum ditukarkan", to_redeem)
    recent = [
        f"  {code.code}  {OUTCOME_TEXT[outcome]}" + (f"  ({code.rewards})" if code.rewards else "")
        for code, outcome in store.recent(game, RECENT_CODES)
    ]
    _print_section("  Terakhir dicoba", recent)


def _first_seen_text(code: RedeemCode, new: bool) -> str:
    return "baru" if new else f"sejak {code.first_seen.astimezone():%d-%m-%Y}"


def _redeem_kept_codes(hoyolab: HoyolabClient, store: CodeStore, game: GameId) -> bool:
    """Redeem the first REDEEM_CODES_AT_MOST codes of `game` still to redeem (the newest
    first), record and print each result, and mark them told (Master saw them here); whether
    none is left to redeem again. A store that cannot be written is pointed out after the
    results, which HoYoLAB has given by then."""
    short = GAMES[game].short
    codes = store.unredeemed(game)
    if not codes:
        print(f"Tidak ada kode {short} yang belum ditukarkan.")
        return True
    trying = codes[:REDEEM_CODES_AT_MOST]
    print(
        f"Menukarkan {len(trying)} kode {short} di HoYoLAB satu per satu, sekitar "
        f"{REDEEM_GAP_S:g} detik per kode...",
        flush=True,
    )
    try:
        results = hoyolab.redeem_codes(game, [code.code for code in trying])
    except GachaError as error:
        print(f"{FAIL} {error.message}")
        return False
    unsaved: OSError | None = None
    try:
        store.record(results)
        store.mark_announced(trying)
    except OSError as exc:
        unsaved = exc
    for result in results:
        print(f"{_outcome_mark(result.outcome)} {result.message}")
    if unsaved is not None:
        print(f"{WARN} Hasil penukaran tidak bisa disimpan di {store.path}: {unsaved}")
    left = len(codes) - len(trying)
    if left > 0:
        print(f"{WARN} Masih ada {left} kode {short}; jalankan perintah ini lagi.")
    return all(result.outcome in FINAL_OUTCOMES for result in results)


def _outcome_mark(outcome: str) -> str:
    """✓ for rewards, ! for a code settled otherwise (used, expired, ...), ✗ for one left to
    redeem again."""
    if outcome == "redeemed":
        return OK
    return WARN if outcome in FINAL_OUTCOMES else FAIL


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


@contextmanager
def _masters_reminders(
    config: AppConfig,
    schedule: ScheduleStore | None,
    registry: DeviceRegistry,
    prayer: PrayerPlace | None,
    assistant: Assistant,
    notifier: Notifier | None,
    alarm: AlarmBell | None,
) -> Iterator[None]:
    """The reminders Master asked for while the block runs, queued as his own (`remind`): what
    he scheduled (his alarms rung by `alarm`), and with `prayer` the prayer times. One item of
    `run`'s watchers, which are at Python's limit of blocks nested in one function."""
    with (
        _scheduler(
            config,
            schedule,
            registry,
            announce=assistant.remind,
            notifier=notifier,
            alarm=alarm,
        ),
        _prayer_watcher(config, prayer, assistant, notifier),
    ):
        yield


def _scheduler(
    config: AppConfig,
    store: ScheduleStore | None,
    registry: DeviceRegistry,
    *,
    announce: Callable[[str, str], None],
    notifier: Notifier | None,
    alarm: AlarmBell | None = None,
) -> AbstractContextManager[object]:
    """What comes due fires while the block runs; nothing fires with the schedule off. With
    `alarm` a due alarm rings, its first ring pushed (`alarm.push`) with a link to the phone
    page when there is one; without it, it is said once."""
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
        alarm=alarm,
        alarm_push=config.alarm.push,
        alarm_click=_phone_page(config),
    )


def _alarm_bell(
    config: AppConfig,
    schedule: ScheduleStore | None,
    *,
    dry_run: bool,
    day_level: Callable[[], int] = lambda: NORMAL,
) -> AlarmBell | None:
    """The bell of Master's alarms for this process, or None with the alarm or the schedule off.
    A dry run's ring file is in its temporary folder, beside its schedule: its alarms never ring
    in the real file. `day_level` is her level without whisper mode, the first ring's."""
    if schedule is None or not config.alarm.enabled:
        return None
    settings = config.alarm
    path = schedule.path.with_name(settings.state_path.name) if dry_run else settings.state_path
    return AlarmBell(
        path,
        schedule,
        repeat_seconds=settings.repeat_seconds,
        max_minutes=settings.max_minutes,
        snooze_minutes=settings.snooze_minutes,
        day_level=day_level,
        owner_call=config.assistant.owner_call,
    )


def _phone_page(config: AppConfig) -> str | None:
    """The phone page's address through Tailscale, for a push to open; None without one."""
    phone = config.phone
    return f"https://{phone.host}/" if phone.enabled and phone.host is not None else None


def _schedule_list(args: argparse.Namespace) -> int:
    entries = _schedule_store(load_config(args.config)).entries()
    if not entries:
        print("Belum ada jadwal.")
        return 0
    rows = [
        (entry.id, _schedule_time(entry.at), _repeat_text(entry), _action_text(entry.action))
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
        f"{_schedule_time(entry.at)}, {_repeat_text(entry)}"
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
    """Run one routine now on the devices (simulated with --dry-run), printing every step.

    Its delayed steps go into her schedule, for `waifu run` to run on time; a dry run puts
    them in a schedule of its own that is gone when it ends, as `chat --dry-run` does.
    """
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
    registry = build_registry(config, dry_run=args.dry_run)
    started = datetime.now()
    with _session_schedule(config, dry_run=args.dry_run) as schedule:
        results = run_routine(registry, routine, name=name, schedule=schedule, now=lambda: started)
    for number, (step, result) in enumerate(zip(routine.steps, results, strict=True), start=1):
        mark = OK if result.ok else FAIL
        outcome = _step_outcome(step, result, started)
        print(f"{mark} Langkah {number} ({describe_step(step)}): {outcome}")
    steps = zip(routine.steps, results, strict=True)
    if any(step.after_minutes is not None and result.ok for step, result in steps):
        if args.dry_run:
            print(DRY_RUN_DELAYED_STEPS.format(path=config.schedule.path))
        else:
            print(DELAYED_STEPS_IN_RUN)
    return 0 if all(result.ok for result in results) else 1


def _step_outcome(step: RoutineStep, result: DeviceResult, started: datetime) -> str:
    """What one step of `waifu routine run` did; a scheduled one names its day and its id.

    `Dijadwalkan besok jam 01.00 (j12).`: on screen `besok` and a 24-hour time cannot be
    misheard, and the id is what `waifu schedule cancel` takes. Her spoken result has neither.
    """
    if step.after_minutes is None or not result.ok:
        return result.message
    at = datetime.fromisoformat(result.state["at"])
    day = "besok " if at.date() != started.date() else ""
    return f"Dijadwalkan {day}jam {at:%H.%M} ({result.state['id']})."


def _schedule_time(moment: datetime) -> str:
    """`Selasa 13-10-2026 15:00`: a local time of the schedule as the owner reads it."""
    return f"{WEEKDAYS_ID[moment.weekday()]} {moment:%d-%m-%Y %H:%M}"


def _repeat_text(entry: Entry) -> str:
    """`tiap hari kerja`, `tiap Senin`, `tiap tanggal 31`, `tiap 5 Mei`: how an entry repeats.

    A monthly or yearly entry names its own day, also while it sits on a shorter month's last.
    """
    if entry.repeat == "weekly":
        return f"tiap {WEEKDAYS_ID[entry.at.weekday()]}"
    if entry.repeat == "monthly":
        return f"tiap tanggal {entry.day}"
    if entry.repeat == "yearly":
        return f"tiap {entry.day} {MONTHS_ID[entry.at.month - 1]}"
    return REPEAT_TEXT[entry.repeat]


def _action_text(action: Action) -> str:
    if isinstance(action, Reminder):
        return f'pengingat "{action.text}"'
    if isinstance(action, DeviceAction):
        routine = f" (rutinitas {action.routine})" if action.routine is not None else ""
        return f"perangkat {_command_text(action.device, action.command, action.value)}{routine}"
    if isinstance(action, AlarmAction):
        return "alarm" if action.label is None else f'alarm "{action.label}"'
    return f"rutinitas {action.name}"


def _command_text(device: str, command: str, value: int | None) -> str:
    """`ac temp 26`: a device command as the owner writes it."""
    return f"{device} {command}" if value is None else f"{device} {command} {value}"


# --- to-do list ------------------------------------------------------------------------------


def _todo_store(config: AppConfig) -> TodoStore:
    """Master's tasks, in the file `todo.path` names, whether the list is on or not."""
    settings = config.todo
    return TodoStore(
        settings.path,
        max_tasks=settings.max_tasks,
        max_chars=settings.max_chars,
        keep_done_days=settings.keep_done_days,
    )


def _todo_list(args: argparse.Namespace) -> int:
    """The open tasks, the overdue ones first; with --done also the ones finished lately."""
    store = _todo_store(load_config(args.config))
    today = date.today()
    tasks = store.open_tasks()
    if tasks:
        rows = [(task.id, _due_text(task.due, today), task.text) for task in tasks]
        print(_format_table(("ID", "TENGGAT", "TUGAS"), rows))
    else:
        print("Tidak ada tugas terbuka.")
    if not args.done:
        return 0
    print()
    done = store.done_tasks()
    if done:
        print("Sudah selesai:")
        rows = [(task.id, _day_text(task.done.date()), task.text) for task in done]
        print(_format_table(("ID", "SELESAI", "TUGAS"), rows))
    else:
        print("Belum ada tugas yang selesai.")
    return 0


def _todo_add(args: argparse.Namespace) -> int:
    try:
        task = _todo_store(load_config(args.config)).add(" ".join(args.text), args.due)
    except TodoError as exc:
        print(f"{FAIL} {exc}")
        return 1
    due = "" if task.due is None else f" (tenggat {_day_text(task.due)})"
    print(f"{OK} Tugas {task.id} ditambahkan: {task.text}{due}")
    return 0


def _todo_done(args: argparse.Namespace) -> int:
    task = _todo_store(load_config(args.config)).complete(args.task_id)
    return _todo_changed(task, args.task_id, "Selesai")


def _todo_remove(args: argparse.Namespace) -> int:
    task = _todo_store(load_config(args.config)).remove(args.task_id)
    return _todo_changed(task, args.task_id, "Dihapus")


def _todo_changed(task: Task | None, task_id: str, change: str) -> int:
    """Say which task the change (`Selesai`, `Dihapus`) was made to; exit 1 when the list holds
    no task `task_id`."""
    if task is None:
        print(f"{FAIL} Tidak ada tugas dengan id {task_id}.")
        return 1
    print(f"{OK} {change} {task.id}: {task.text}")
    return 0


def _due_text(due: date | None, today: date) -> str:
    """`Senin 06-01-2020 (terlambat)`: a task's due date as the owner reads it, `-` without one."""
    if due is None:
        return "-"
    if due < today:
        return f"{_day_text(due)} (terlambat)"
    if due == today:
        return f"{_day_text(due)} (hari ini)"
    return _day_text(due)


# --- the pronunciation dictionary ------------------------------------------------------------


def _lexicon(config: AppConfig) -> Lexicon:
    """Master's pronunciation rules, in the file `tts.lexicon_path` names."""
    return Lexicon(config.tts.lexicon_path)


def _lafal_list(args: argparse.Namespace) -> int:
    lexicon = _lexicon(load_config(args.config))
    try:
        rules = lexicon.read()
    except OSError as exc:
        print(f"{FAIL} Kamus {lexicon.path} tidak bisa dibaca: {exc}")
        return 1
    for term, reading in rules.items():
        print(f"{term}: {reading}")
    replaced = {term.casefold() for term in rules}
    for term, reading in BUILT_IN_READINGS.items():
        if term.casefold() not in replaced:
            print(f"{term}: {reading} (bawaan)")
    return 0


def _lafal_add(args: argparse.Namespace) -> int:
    lexicon = _lexicon(load_config(args.config))
    try:
        term, reading = lexicon.add(args.term, " ".join(args.reading))
    except LexiconError as exc:
        print(f"{FAIL} {exc}")
        return 1
    except OSError as exc:
        print(f"{FAIL} Kamus {lexicon.path} tidak bisa disimpan: {exc}")
        return 1
    print(f"{OK} {term} dibaca: {reading}")
    return 0


def _lafal_remove(args: argparse.Namespace) -> int:
    """Drop Master's rule for the word; a built-in rule can only be replaced (`add`)."""
    lexicon = _lexicon(load_config(args.config))
    try:
        removed = lexicon.remove(args.term)
    except OSError as exc:
        print(f"{FAIL} Kamus {lexicon.path} tidak bisa disimpan: {exc}")
        return 1
    if removed is not None:
        print(f"{OK} Dihapus: {removed}")
        return 0
    wanted = " ".join(args.term.split()).casefold()
    built_in = next((term for term in BUILT_IN_READINGS if term.casefold() == wanted), None)
    if built_in is not None:
        print(
            f"{FAIL} {built_in} adalah aturan bawaan; ganti bacaannya dengan "
            f"'waifu lafal add {built_in} <bacaan>'."
        )
    else:
        print(f"{FAIL} Tidak ada aturan untuk {args.term}.")
    return 1


# --- the journal -----------------------------------------------------------------------------


def _journal_store(config: AppConfig) -> JournalStore:
    """Master's journal, in the file `journal.path` names, whether the journal is on or not."""
    settings = config.journal
    return JournalStore(
        settings.path,
        max_chars=settings.max_chars,
        max_notes_per_day=settings.max_notes_per_day,
        now=wib_now,
    )


def _journal_timer(config: AppConfig, assistant: Assistant) -> AbstractContextManager[object]:
    """Her question about Master's day, asked of her once a night while the block runs; none
    with the journal off or without `journal.ask_at`. A question whose turn never read the
    journal (`journal_read`) is the brain's apology for a failure, so it is not said. It starts
    a new session, so no earlier answer's figure reaches it, and it is never pushed: the
    journal stays between Master and her. It is private: skipped while guest mode is on."""
    journal = config.journal
    if not journal.enabled or journal.ask_at is None:
        return nullcontext()
    return JournalTimer(
        journal,
        request=lambda prompt, expires_at: assistant.ask_aloud(
            prompt,
            required_tool=JOURNAL_READ,
            new_session=True,
            expires_at=expires_at,
            private=True,
        ),
    )


def _journal_list(args: argparse.Namespace) -> int:
    """The days with notes, oldest first, each with how many and the first note cut short."""
    store = _journal_store(load_config(args.config))
    try:
        days = store.notes()
    except OSError as exc:
        return _journal_failed(store, exc)
    if not days:
        print("Jurnal masih kosong.")
        return 0
    rows = [
        (_day_text(day), str(len(notes)), _preview(notes[0].text)) for day, notes in days.items()
    ]
    print(_format_table(("TANGGAL", "CATATAN", "ISI"), rows))
    return 0


def _journal_show(args: argparse.Namespace) -> int:
    """One day's notes, each with the time it was written."""
    store = _journal_store(load_config(args.config))
    try:
        notes = store.notes(args.day, args.day).get(args.day, ())
    except OSError as exc:
        return _journal_failed(store, exc)
    if not notes:
        print(f"Tidak ada catatan jurnal untuk {_day_text(args.day)}.")
        return 0
    print(f"Jurnal {_day_text(args.day)}:")
    for note in notes:
        print(f"{note.at.astimezone(WIB):%H:%M}  {note.text}")
    return 0


def _journal_add(args: argparse.Namespace) -> int:
    store = _journal_store(load_config(args.config))
    try:
        saved = store.add(" ".join(args.text), args.date)
    except JournalError as exc:
        print(f"{FAIL} {exc}")
        return 1
    except OSError as exc:
        return _journal_failed(store, exc)
    done = "Dicatat di" if saved.added else "Sudah ada di"
    print(f"{OK} {done} jurnal {_day_text(saved.day)}: {saved.note.text}")
    return 0


def _journal_forget(args: argparse.Namespace) -> int:
    """Delete a day's notes with --yes; without it, only say how many would go."""
    store = _journal_store(load_config(args.config))
    day = _day_text(args.day)
    try:
        if args.yes:
            count = store.forget(args.day)
        else:
            count = len(store.notes(args.day, args.day).get(args.day, ()))
    except OSError as exc:
        return _journal_failed(store, exc)
    if count == 0:
        print(f"Tidak ada catatan jurnal untuk {day}.")
    elif args.yes:
        print(f"{OK} {count} catatan jurnal {day} dihapus.")
    else:
        print(
            f"Akan menghapus {count} catatan jurnal {day}. Jalankan lagi dengan --yes untuk "
            "menghapusnya."
        )
    return 0


def _journal_failed(store: JournalStore, exc: OSError) -> int:
    print(f"{FAIL} File jurnal {store.path} tidak bisa dibuka: {exc}")
    return 1


def _preview(text: str) -> str:
    """`text` cut to `JOURNAL_PREVIEW_CHARS` characters, an ellipsis marking a cut."""
    if len(text) <= JOURNAL_PREVIEW_CHARS:
        return text
    return text[: JOURNAL_PREVIEW_CHARS - 1] + "…"


# --- Japanese lessons ------------------------------------------------------------------------

JAPANESE_VIEWS = ("hari-ini", "progres")
JAPANESE_NOTE = (
    "Kata hari ini dihitung sudah dipelajari saat {name} menyebutnya, di sapaan pagi atau saat "
    "ditanya."
)


def _japanese_store(config: AppConfig) -> JapaneseStore:
    """Master's Japanese words, in the file `japanese.path` names, whether the lessons are on or
    not (`run`, `chat`, `waifu jepang`, and `check` share it)."""
    return JapaneseStore(config.japanese.path, now=wib_now)


def _japanese_word_of_the_day(
    words: Sequence[Word], store: JapaneseStore
) -> Callable[[date], Word]:
    """The briefing's Japanese word for a day, learned once in `store`."""

    def word_for(day: date) -> Word:
        return learn_word_of_the_day(words, store, day)[0]

    return word_for


def _japanese(args: argparse.Namespace) -> int:
    """Print today's Japanese word, or with `progres` how many words Master learned, from the
    built-in list and the lesson file alone: it records nothing, creates no file, and works with
    the lessons off too. Exit 1 when the lesson file cannot be read."""
    config = load_config(args.config)
    if args.view == "progres":
        return _japanese_progress(config, wib_now().date())
    _warn_when_japanese_is_off(config)
    today = wib_now().date()
    word = word_of_the_day(load_words(), today)
    written = word.kana if word.kanji is None else f"{word.kana} ({word.kanji})"
    print(f"Kata hari ini, {_day_text(today)}: {written}, artinya {word.meaning}")
    print(f"Contoh: {word.example}")
    print(f"Artinya: {word.example_meaning}")
    print(JAPANESE_NOTE.format(name=config.assistant.name))
    return 0


def _japanese_progress(config: AppConfig, today: date) -> int:
    """The words of the list Master learned, mastered, and has due today, then the words he
    kept himself, when there are any."""
    store = _japanese_store(config)
    try:
        progress = store.progress()
        custom = store.custom_words()
    except OSError as exc:
        print(f"{FAIL} File belajar bahasa Jepang {store.path} tidak bisa dibaca: {exc}")
        return 1
    _warn_when_japanese_is_off(config)
    counts = lesson_counts(load_words(), progress, today)
    if not counts.learned:
        # With words of his own kept, "no word" names the list, or the next line contradicts it.
        of_the_list = " dari daftar" if custom else ""
        print(f"Belum ada kata{of_the_list} yang dipelajari (0 dari {counts.total}).")
    else:
        print(f"Dipelajari: {counts.learned} dari {counts.total} kata, {counts.mastered} dikuasai")
        if counts.due:
            print(f"Perlu diulang hari ini: {counts.due} kata")
        else:
            print("Tidak ada kata yang perlu diulang hari ini.")
    if custom:
        due = sum(progress[word_id].due <= today for word_id in custom if word_id in progress)
        when = f"{due} perlu diulang" if due else "tidak ada yang perlu diulang"
        print(f"Kata tambahanmu: {len(custom)} kata, {when} hari ini.")
    return 0


def _warn_when_japanese_is_off(config: AppConfig) -> None:
    if not config.japanese.enabled:
        print(f"{WARN} Belajar bahasa Jepang mati (japanese.enabled: false).")


# --- morning briefing ------------------------------------------------------------------------


def _weather_client(
    briefing: BriefingConfig, *, failure_level: int = logging.WARNING
) -> WeatherClient | None:
    """Open-Meteo for the place `briefing` names (its coordinates win over its city), logging
    its failures at `failure_level`; None when it names none. Nothing is asked until the
    weather is wanted."""
    if briefing.city is None and briefing.latitude is None:
        return None
    return WeatherClient(
        latitude=briefing.latitude,
        longitude=briefing.longitude,
        city=briefing.city,
        failure_level=failure_level,
    )


def _briefing_tools(
    weather: WeatherClient | None,
    schedule: ScheduleStore | None,
    *,
    todo: TodoStore | None = None,
    guest_mode: Callable[[], bool] | None = None,
    japanese: Callable[[date], Word] | None = None,
) -> BriefingTools:
    """The day's date, the weather from `weather` (none without a place), entries of `schedule`
    (none without one), with `todo` the tasks due today and overdue, and with `japanese` the
    word of the day, for the brain; while `guest_mode` says guests are around, without the tasks
    and the reminder texts."""
    return BriefingTools(
        weather=weather,
        schedule=schedule,
        todo=todo,
        japanese=japanese,
        guest_mode=guest_mode,
    )


def _briefing_timer(
    config: AppConfig, assistant: Assistant, notifier: Notifier | None
) -> AbstractContextManager[object]:
    """Her morning greeting, asked of her once a day while the block runs and also pushed to
    the phone when `briefing.push` and notifications are on; none with the briefing off.
    A greeting whose turn never read the day (`daily_briefing`) is the brain's apology for a
    failure, so it is neither said nor pushed. It starts a new session: the greeting needs no
    earlier words, and must not repeat figures from a finance answer given moments before."""
    briefing = config.briefing
    if not briefing.enabled:
        return nullcontext()
    on_reply = (
        _phone_push(notifier, config.assistant.name, BRIEFING_TAG)
        if briefing.push and notifier is not None
        else None
    )
    return BriefingTimer(
        briefing,
        request=lambda prompt, expires_at: assistant.ask_aloud(
            prompt,
            on_reply=on_reply,
            required_tool=DAILY_BRIEFING,
            new_session=True,
            expires_at=expires_at,
        ),
    )


def _weekly_report_timer(
    config: AppConfig, gacha: GachaParts | None, assistant: Assistant, notifier: Notifier | None
) -> AbstractContextManager[object]:
    """Her weekly game report, asked of her once a week while the block runs and also pushed
    to the phone with the game tag when `weekly_report.push` and notifications are on; none
    with the games or the report off. As for the morning greeting, a report whose turn never
    read the week (`weekly_report`) is the brain's apology for a failure, so it is neither said
    nor pushed, and it starts a new session, so no earlier answer's figure reaches it. It is
    private: skipped while guest mode is on."""
    weekly = config.weekly_report
    if gacha is None or not weekly.enabled:
        return nullcontext()
    on_reply = (
        _phone_push(notifier, config.assistant.name, NOTIFY_TAG)
        if weekly.push and notifier is not None
        else None
    )
    return WeeklyReportTimer(
        weekly,
        request=lambda prompt, expires_at: assistant.ask_aloud(
            prompt,
            on_reply=on_reply,
            required_tool=WEEKLY_REPORT,
            new_session=True,
            expires_at=expires_at,
            private=True,
        ),
    )


def _phone_push(notifier: Notifier, title: str, tag: str) -> Callable[[str], None]:
    """Sends what she said by herself to the phone with `tag` (the notifier logs a failed
    push)."""

    def push(text: str) -> None:
        notifier.send(title, text, tags=[tag])

    return push


def _briefing(args: argparse.Namespace) -> int:
    """Print the date, weather, and schedule the brain would get now (its tasks are what
    `waifu todo` lists), without the brain: the way to check the weather. Exit 1 when a part of
    it failed; the rest is printed all the same."""
    config = load_config(args.config)
    schedule = _schedule_store(config) if config.schedule.enabled else None
    weather = _weather_client(config.briefing)
    result, is_error = _briefing_tools(weather, schedule).run(DAILY_BRIEFING, {})
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
        f"hari ini {_temperature_text(weather['min_c'], weather['max_c'])}, "
        f"peluang hujan {weather['rain_chance']}%; besok pagi {weather['tomorrow_morning']}"
    )


# --- rain alerts -----------------------------------------------------------------------------

RAIN_LOOKAHEAD_HOURS = 24  # how far `waifu rain` looks; the forecast covers today and tomorrow


@contextmanager
def _rain_watcher(
    config: AppConfig, assistant: Assistant, notifier: Notifier | None, game_mode: GameMode | None
) -> Iterator[None]:
    """Her warnings shortly before rain at the briefing's place while the block runs, also pushed
    to the phone when `rain_alert.push` and notifications are on; none with the alerts off or
    without a place. She says one once the follow-up window has closed, so a conversation is
    never interrupted, but never once its rain has started, and names the city as written (none
    with coordinates only). Its client
    logs failures at DEBUG: the watcher logs a run of them once itself."""
    rain = config.rain_alert
    weather = (
        _weather_client(config.briefing, failure_level=logging.DEBUG) if rain.enabled else None
    )
    if weather is None:
        yield
        return
    push = notifier if rain.push else None
    watcher = RainWatcher(
        weather.forecast,
        rain,
        announce=_announcer(assistant, game_mode, patient=True, pushed=push is not None),
        notifier=push,
        owner_call=config.assistant.owner_call,
        place=config.briefing.city,
        title=config.assistant.name,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _rain(args: argparse.Namespace) -> int:
    """Print when the next rain is expected at the briefing's place, read as the rain alerts read
    it (`rain_alert.threshold`), without the brain; it works with the alerts off. Exit 1 when
    there is no forecast."""
    config = load_config(args.config)
    weather = _weather_client(config.briefing)
    if weather is None:
        print(f"{FAIL} Cuaca belum diatur (isi briefing.city di config.yaml)")
        return 1
    try:
        forecast = weather.forecast()
    except WeatherError as exc:
        print(f"{FAIL} {exc}")
        return 1
    threshold = config.rain_alert.threshold
    print(
        f"Prakiraan hujan di {forecast.place}, per {forecast.now:%H:%M} "
        f"(peluang {threshold}% ke atas):"
    )
    rains_now = raining_now(forecast)
    if rains_now:
        print("Sekarang sedang hujan.")
    window = rain_window(forecast, threshold, within=timedelta(hours=RAIN_LOOKAHEAD_HOURS))
    if window is not None:
        print(f"Hujan berikutnya: {_rain_window_text(window, forecast.now)}.")
    else:
        other = " lain" if rains_now else ""
        print(f"Tidak ada hujan{other} dalam {RAIN_LOOKAHEAD_HOURS} jam ke depan.")
    return 0


def _rain_window_text(window: RainWindow, now: datetime) -> str:
    """`sekitar jam tiga sore (15:00-17:00, peluang 80%)`: the hour as she would say it, then
    when the rain starts and ends (`besok` before either on a later date than the one before
    it) and the highest known chance."""
    start_day = "besok " if window.start.date() > now.date() else ""
    end_day = "besok " if window.last.date() > window.start.date() else ""
    clock = f"{start_day}{window.start:%H:%M}-{end_day}{window.last:%H:%M}"
    chance = "" if window.chance is None else f", peluang {window.chance}%"
    return f"sekitar {spoken_hour(window.start.hour)} ({clock}{chance})"


# --- the weather of the days ahead -------------------------------------------------------------

# `waifu cuaca`'s words for the forecast's days.
FORECAST_DAYS = {"hari-ini": "today", "besok": "tomorrow", "akhir-pekan": "weekend"}


def _weather_ahead(args: argparse.Namespace) -> int:
    """Print the weather of the day asked (the rest of today by default) at the briefing's place,
    each day with its parts not passed, read as the `weather_forecast` tool reads it, without the
    brain; it works with the briefing off. Exit 1 when there is no forecast for it."""
    config = load_config(args.config)
    weather = _weather_client(config.briefing)
    if weather is None:
        print(f"{FAIL} Cuaca belum diatur (isi briefing.city di config.yaml)")
        return 1
    try:
        outlook = weather.outlook()
        days = periods(outlook, args.day, None)
    except (WeatherError, ForecastError) as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"Prakiraan cuaca di {outlook.place}, per {outlook.now:%H:%M}:")
    for day in days:
        rest = "" if day.part is None else f", {day.part}"
        print(f"{_day_text(day.day)}{rest}: {_forecast_text(day)}")
        for part in PARTS:
            found = period_of(outlook, day.day, part)
            if found is not None:
                print(f"  {found.part}: {_forecast_text(found)}")
    return 0


def _forecast_text(period: Period) -> str:
    """`hujan, 18.9-28.3°C, peluang hujan 80%, curah hujan 7.5 mm`, unknown values left out."""
    texts = [] if period.condition is None else [period.condition]
    if period.min_c is not None and period.max_c is not None:
        texts.append(_temperature_text(period.min_c, period.max_c))
    if period.rain_chance is not None:
        texts.append(f"peluang hujan {period.rain_chance}%")
    if period.rain_mm is not None:
        texts.append(f"curah hujan {period.rain_mm:g} mm")
    return ", ".join(texts) or "tidak ada data"


def _temperature_text(min_c: float, max_c: float) -> str:
    """`18.9-28.3°C`, or one value (`20°C`) when the lowest and the highest read the same."""
    lowest, highest = f"{min_c:g}", f"{max_c:g}"
    return f"{highest}°C" if lowest == highest else f"{lowest}-{highest}°C"


# --- rekap hari ini --------------------------------------------------------------------------


def _daily_recap(
    config: AppConfig, schedule: ScheduleStore | None, focus: FocusTimer | None = None
) -> DailyRecap:
    """Master's day from the records of the features that are on: `schedule` is the session's
    (a dry run's own) or None with the schedule off; with `focus` (only `run` has one) the focus
    timer, so the block under way counts, else the focus log; the pull history as stored in
    `gacha.data_dir`, never refreshed; the internet outages the watcher kept."""
    gacha = config.gacha
    japanese = config.japanese.enabled
    return DailyRecap(
        todo=_todo_store(config) if config.todo.enabled else None,
        focus=_focus_seconds(config, focus),
        play_time=_play_time_store(config) if config.play_time.enabled else None,
        gacha=(
            StoredPulls(
                GachaStore(gacha.data_dir),
                gacha.games,
                server_utc_offset=gacha.server_utc_offset,
                extra_standard=gacha.off_banner,
            )
            if gacha.enabled
            else None
        ),
        japanese=_japanese_store(config) if japanese else None,
        words=load_words() if japanese else (),
        journal=_journal_store(config) if config.journal.enabled else None,
        schedule=schedule,
        network=_network_store(config) if config.network.enabled else None,
        now=wib_now,
    )


def _focus_seconds(config: AppConfig, timer: FocusTimer | None) -> FocusSeconds | None:
    """How long Master focused in a span: the timer's count with the block under way, else the
    focus log's while the focus companion is on."""
    if timer is not None:
        return timer.focused_seconds
    if not config.focus.enabled:
        return None
    store = _focus_store(config)
    return lambda since, until: store.totals(since, until).seconds


# `waifu rekap`'s words for the days.
RECAP_DAYS: dict[str, Literal["today", "yesterday"]] = {"hari-ini": "today", "kemarin": "yesterday"}
# The config sections whose switches turn the recap's parts on, in the parts' order.
RECAP_SWITCHES = (
    "todo",
    "focus",
    "play_time",
    "gacha",
    "japanese",
    "journal",
    "schedule",
    "network",
)
RECAP_NOTE = (
    "Hanya dari catatan di PC ini: jam main, fokus, dan internet putus dihitung selama 'waifu "
    "run' menyala, dan pull dari riwayat yang terakhir disimpan."
)


def _recap(args: argparse.Namespace) -> int:
    """Print the recap of the day asked (today up to now by default) from the records of the
    features that are on, read as the `daily_recap` tool reads them, without the brain: a line
    per part, and a `✗` line for a part that cannot be read. Exit 1 when one cannot."""
    config = load_config(args.config)
    schedule = _schedule_store(config) if config.schedule.enabled else None
    recap = _daily_recap(config, schedule)
    if not recap.parts:
        print(
            f"{WARN} Rekap kosong: semua catatan yang dibacanya mati ({', '.join(RECAP_SWITCHES)})."
        )
        return 0
    day = recap.gather(args.day)
    until = f", sampai {day.now:%H:%M}" if day.is_today else ""
    print(f"Rekap {_day_text(day.day)}{until}, dari catatan {config.assistant.name}:")
    for part in recap.parts:
        value = getattr(day, part)
        if value is not None:
            print("\n".join(_RECAP_LINES[part](value)))
        if part in day.problems:
            print(f"{FAIL} {day.problems[part]}")
    print(RECAP_NOTE)
    return 1 if day.problems else 0


def _tasks_lines(tasks: TasksDay) -> list[str]:
    done = tasks.done
    if done is not None and not (done or tasks.still_due):
        return ["Tugas: tidak ada"]
    finished = (
        f"yang selesai tidak tersimpan lagi (todo.keep_done_days: {tasks.keep_done_days})"
        if done is None
        else f"{len(done)} selesai"
    )
    return [
        f"Tugas: {finished}, {len(tasks.still_due)} masih menunggu",
        *(f"  selesai: {task.text}" for task in done or ()),
        *(
            f"  menunggu: {task.text}, tenggat {_day_text(task.due)}"
            for task in tasks.still_due
            if task.due is not None
        ),
    ]


def _focus_lines(seconds: int) -> list[str]:
    return [f"Fokus: {_play_duration(seconds) if whole_minutes(seconds) else 'tidak ada'}"]


def _play_lines(play: PlayDay) -> list[str]:
    if not play.played:
        return ["Main game: tidak ada"]
    games = ", ".join(
        f"{GAMES[game.game].name} {_play_duration(game.seconds)}" for game in play.played
    )
    return [f"Main game: {_play_duration(play.seconds)} ({games})"]


def _pulls_lines(pulls: tuple[GamePulls, ...]) -> list[str]:
    if not pulls:
        return ["Gacha: tidak ada"]
    return [f"Gacha: {'; '.join(_game_pulls_text(game) for game in pulls)}"]


def _game_pulls_text(pulls: GamePulls) -> str:
    """`Honkai: Star Rail 10 pull, 5★ Acheron (10 pull, menang 50/50)`, in the words of
    `waifu gacha history` (an S-rank in ZZZ)."""
    game = pulls.game
    tops = [top for banner in pulls.banners for top in banner.tops]
    texts = [f"{game.name} {pulls.count} pull"]
    for top in sorted(tops, key=lambda top: (top.time, top.id)):
        texts.append(f"{_top_word(game)} {_top_pull_text(top)}")
    return ", ".join(texts)


def _words_lines(words: WordsDay) -> list[str]:
    if not (words.learned or words.reviewed):
        return ["Kata Jepang: tidak ada"]
    learned = ", ".join(f"{word.kana}: {word.meaning}" for word in words.learned)
    new = f"{len(words.learned)} kata baru ({learned})" if words.learned else "0 kata baru"
    return [f"Kata Jepang: {new}, {words.reviewed} kata diulang"]


def _journal_lines(notes: int) -> list[str]:
    return [f"Jurnal: {notes} catatan" if notes else "Jurnal: tidak ada"]


def _schedule_lines(planned: tuple[Planned, ...]) -> list[str]:
    if not planned:
        return ["Jadwal: tidak ada"]
    entries = "; ".join(
        f"{item.at:%H:%M} {item.what}{'' if item.passed else ' (nanti)'}" for item in planned
    )
    return [f"Jadwal: {entries}"]


def _outage_lines(outages: OutageDay) -> list[str]:
    if not outages.known:
        return [f"Internet putus: tidak tersimpan lagi (network.keep_days: {outages.keep_days})"]
    if not outages.count:
        return ["Internet putus: tidak ada"]
    return [f"Internet putus: {outages.count} kali, total {_play_duration(outages.seconds)}"]


# Each part of the recap as `waifu rekap` prints it, by its name in `recap.PARTS`.
_RECAP_LINES: dict[str, Callable[[Any], list[str]]] = {
    "tasks": _tasks_lines,
    "focus": _focus_lines,
    "play_time": _play_lines,
    "gacha": _pulls_lines,
    "japanese": _words_lines,
    "journal": _journal_lines,
    "schedule": _schedule_lines,
    "network": _outage_lines,
}


# --- the running version -----------------------------------------------------------------------


def _record_running_version() -> CodeVersion:
    """What this process runs, read with git and recorded in RUNNING_FILE for `check` and
    `waifu version`; a part it could not read is logged, and so is a failed write, never
    raised: a missing git or a locked file must not stop her."""
    code = read_code_version(run=subprocess.run)
    for problem in code.problems:
        logger.warning("%s", problem)
    logger.info("Running %s", _logged_code(code))
    try:
        write_running(RUNNING_FILE, RunningVersion(code, datetime.now(), os.getpid()))
    except OSError as exc:
        logger.warning("Could not record the running version in %s: %s", RUNNING_FILE, exc)
    return code


def _logged_code(code: CodeVersion) -> str:
    """`v1.25 (peringatan hujan), commit ec4aadf...`, for the log."""
    release = f"{code.release.version} ({code.release.title})" if code.release else "no release"
    if code.commit is not None:
        return f"{release}, commit {code.commit}"
    if code.source_time is not None:
        return f"{release}, source files of {code.source_time:%Y-%m-%d %H:%M:%S}"
    return release


@contextmanager
def _version_watcher(
    config: AppConfig,
    running: CodeVersion,
    assistant: Assistant,
    notifier: Notifier | None,
    game_mode: GameMode | None,
) -> Iterator[None]:
    """Her notice that newer code is on disk than `running`, the code this run started with,
    while the block runs, also pushed to the phone when `version_notice.push` and notifications
    are on; none with the notice off. She says it once the follow-up window has closed, so a
    conversation is never interrupted. It reads only the CHANGELOG and pyproject, never git."""
    notice = config.version_notice
    if not notice.enabled:
        yield
        return
    push = notifier if notice.push else None
    watcher = VersionWatcher(
        lambda: read_code_version(run=None),
        running,
        notice,
        announce=_announcer(assistant, game_mode, patient=True, pushed=push is not None),
        notifier=push,
        owner_call=config.assistant.owner_call,
        name=config.assistant.name,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _running_status(lock: Path, record_path: Path) -> tuple[bool, RunningVersion | None]:
    """Whether the run lock at `lock` is held, and a record at `record_path`. While held, the
    record of the `waifu run` holding it: None when it recorded nothing (a run from before
    v1.26, or `enroll`/`speaker-test` holds the lock) or its record names a process that ended.
    While free, the last run's record, for the packages it ran with. Only the lock tells that
    something runs: a console closed by hand never removes the record."""
    record = read_running(record_path)
    if not _instance_running(lock):
        return False, record
    if record is None or not process_alive(record.pid):
        return True, None
    return True, record


def _running_version_check(disk: CodeVersion, held: bool, running: RunningVersion | None) -> Check:
    """Whether the running `waifu run` runs the code on `disk`, and what to do when not. With
    the lock free, `running` is the last run's record: packages changed since it ran still need
    pip install before the next start, as she asked to be closed first."""
    if disk.release is None:
        return WARN, "waifu run: versi kode di disk tidak terbaca (lihat CHANGELOG.md)"
    newest = disk.release.version
    if not held:
        on_disk = f"waifu run tidak berjalan; versi di disk {_release_text(disk.release)}"
        if running is not None and dependencies_changed(running.code, disk):
            return WARN, (
                f"{on_disk}; paket Python-nya berubah sejak terakhir dijalankan: kalau belum, "
                f"jalankan {INSTALL_COMMAND} di folder proyek sebelum waifu run"
            )
        return OK, on_disk
    if running is None:
        return WARN, (
            "waifu run yang sedang jalan versinya tidak tercatat (lebih lama dari v1.26); "
            f"mulai ulang supaya {newest} aktif"
        )
    started = f"dimulai {running.started:%Y-%m-%d}"
    if running.code.release is None:
        return WARN, (
            f"waifu run yang sedang jalan versinya tidak terbaca ({started}); "
            f"mulai ulang supaya {newest} aktif"
        )
    version = running.code.release.version
    newer = newer_releases(running.code, disk)
    packages = dependencies_changed(running.code, disk)
    if not newer and not packages:
        if _changed_since(running.code, disk):
            return OK, (
                f"waifu run: {version}, terbaru; ada perubahan kecil sejak dimulai, "
                "mulai ulang kalau sempat"
            )
        return OK, f"waifu run: {version}, terbaru"
    old = f"waifu run yang sedang jalan masih {version} ({started})"
    active = f" supaya {newest} aktif" if newer else ""
    if packages:
        return WARN, (
            f"{old} dan paket Python-nya berubah: tutup Cyrene, jalankan {INSTALL_COMMAND} di "
            f"folder proyek, lalu mulai ulang{active}"
        )
    return WARN, f"{old}; mulai ulang{active}"


def _changed_since(running: CodeVersion, disk: CodeVersion) -> bool:
    """Whether the code on disk changed since the run started without a new release: another
    commit, or without commits on both sides a newer source file."""
    if running.commit is not None and disk.commit is not None:
        return running.commit != disk.commit
    if running.commit is None and disk.commit is None:
        return (
            running.source_time is not None
            and disk.source_time is not None
            and disk.source_time > running.source_time
        )
    return False


def _version_notice_check(notice: VersionNoticeConfig) -> Check:
    """How often and in which hours she compares the code on disk; nothing is read."""
    if not notice.enabled:
        return WARN, "Kabar versi baru mati"
    hours = f"{notice.active_from:%H:%M}-{notice.active_until:%H:%M}"
    return OK, f"Kabar versi baru: dicek tiap {notice.check_minutes} menit, jam {hours}"


def _version(args: argparse.Namespace) -> int:
    """Print the version on disk, the running one while a recorded `waifu run` runs, and the
    check line comparing them; no config needed. The run lock and record are read in the
    project folder, as the code is, so it answers the same from any working directory."""
    disk = read_code_version(run=subprocess.run)
    held, running = _running_status(REPO_ROOT / RUN_LOCK, REPO_ROOT / RUNNING_FILE)
    print(f"Kode di disk: {_code_text(disk)}")
    if held and running is not None:
        print(
            f"Yang berjalan: {_code_text(running.code)}, dimulai {running.started:%Y-%m-%d %H:%M}"
        )
    mark, text = _running_version_check(disk, held, running)
    print(f"{mark} {text}")
    return 0


def _code_text(code: CodeVersion) -> str:
    """`v1.25, peringatan hujan (commit ec4aadf)`, or `(file terbaru 2026-10-17 09:12)`
    without a commit."""
    if code.commit is not None:
        detail = f" (commit {code.commit[:7]})"
    elif code.source_time is not None:
        detail = f" (file terbaru {code.source_time:%Y-%m-%d %H:%M})"
    else:
        detail = ""
    return f"{_release_text(code.release)}{detail}"


def _release_text(release: Release | None) -> str:
    return "versi tidak terbaca" if release is None else f"{release.version}, {release.title}"


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
    return UsageLedger(
        usage.path,
        prices=usage.prices,
        default_model=usage.default_model,
        web_search_per_1000_usd=usage.web_search_per_1000_usd,
    )


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
    config: AppConfig, assistant: Assistant, notifier: Notifier | None, game_mode: GameMode | None
) -> Callable[[str], None]:
    """Has her say a budget notice by herself and push it to the phone when notifications are
    on (the notifier logs a failed push).

    A notice comes while she thinks up the reply that crossed the line, so she says it once
    Master has had the chance to answer that reply (the follow-up window has closed).
    """

    announce = _announcer(assistant, game_mode, patient=True, pushed=notifier is not None)

    def tell(notice: str) -> None:
        announce(notice, "surprised")
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
    # Web searches get a column only in a month that has any.
    searches = summary.tokens.web_searches > 0
    header = ("TANGGAL", "PERMINTAAN", "INPUT", "OUTPUT", "CACHE TULIS", "CACHE BACA")
    header += ("PENCARIAN WEB", "USD") if searches else ("USD",)
    if rate is not None:
        header += ("IDR",)
    rows = [_usage_row(f"{day.day:%d-%m-%Y}", day, rate, searches) for day in summary.by_day]
    rows.append(_usage_row("Total", summary, rate, searches))
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
    label: str, used: DayUsage | UsageSummary, idr_per_usd: float | None, searches: bool
) -> tuple[str, ...]:
    """One line of `waifu usage`: requests, tokens, web searches when `searches`, the cost, and
    whether it is an estimate."""
    tokens = used.tokens
    counts = [used.requests, tokens.input, tokens.output, tokens.cache_write, tokens.cache_read]
    if searches:
        counts.append(tokens.web_searches)
    cells = [label, *map(str, counts), f"${used.cost_usd:.2f}"]
    if idr_per_usd is not None:
        cells.append(_rupiah(used.cost_usd * idr_per_usd))
    cells.append("(perkiraan)" if used.estimated else "")
    return tuple(cells)


def _rupiah(amount: float) -> str:
    """`Rp3.808`: whole rupiah, thousands separated by dots as written in Indonesia; a debt is
    `-Rp1.200.000`."""
    sign = "-" if round(amount) < 0 else ""
    return f"{sign}Rp{abs(round(amount)):,}".replace(",", ".")


# --- Cyrene on the phone ---------------------------------------------------------------------


def _camera_offered(config: AppConfig) -> bool:
    """Whether the phone page offers its camera: the phone and `phone.camera` on, and Claude with
    vision on to see the photos (Ollama's model cannot see images)."""
    phone = config.phone
    return (
        phone.enabled
        and phone.camera
        and config.vision.enabled
        and config.brain.backend == "claude"
    )


@contextmanager
def _phone_server(
    config: AppConfig,
    assistant: Assistant,
    *,
    camera: bool,
    actions: QuickActions,
    voice: VoicePanel,
    guest_mode: Callable[[], bool],
) -> Iterator[None]:
    """Serve her to the paired phone for the block, when `phone.enabled`.

    The server listens on 127.0.0.1 only; `tailscale serve` shares it with the owner's tailnet.
    With `camera`, a message may carry a photo whose longer side is at most `vision.max_side`;
    otherwise the server refuses photos. `actions` are the page's quick buttons, on the run's
    own devices; `voice` answers the Suara page, hidden while `guest_mode` holds. One that cannot
    start (its port is taken) only costs the phone, never her voice.
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
            camera_max_side=config.vision.max_side if camera else None,
            actions=actions,
            voice=voice,
            guest_mode=guest_mode,
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
    through Funnel), phone.host, the avatar's model, the camera, and the paired phones. Asks
    Tailscale only about its state, never changes it."""
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
    yield _phone_camera_check(config)
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


def _phone_camera_check(config: AppConfig) -> Check:
    """Whether the phone page offers its camera, as `run` decides it, or what keeps it off: the
    owner's own switches first, then the backend."""
    if _camera_offered(config):
        return OK, "Kamera HP: aktif"
    if not config.phone.camera:
        return WARN, "Kamera HP mati (phone.camera: false)"
    if not config.vision.enabled:
        return WARN, "Kamera HP mati (vision.enabled: false)"
    return WARN, "Kamera HP: tidak tersedia dengan Ollama"


def _paired_phones_check(path: Path) -> Check:
    try:
        count = len(PhoneAuth(path).devices())  # moves a broken file aside, as the server would
    except OSError as exc:
        return FAIL, f"File HP terpasang {path} tidak bisa dibaca: {exc}"
    if count == 0:
        return WARN, "Belum ada HP yang dipasangkan; jalankan 'waifu phone pair'"
    return OK, f"HP terpasang: {count} (lihat 'waifu phone devices')"


# --- PC health -------------------------------------------------------------------------------


def _session_pc_reader(config: AppConfig) -> Callable[[], PcSnapshot] | None:
    """How one `run` or `chat` reads the PC, or None with the PC health off.

    The PC tool and the PC watcher of a `run` share one reader: a reading within 10 s of the
    previous one, by either of them, gives the CPU load since it without waiting for a fresh
    half-second sample.
    """
    if not config.pc_health.enabled:
        return None
    cpu = CpuLoad()
    return lambda: read_snapshot(cpu=cpu)


@contextmanager
def _pc_watcher(
    config: AppConfig,
    pc: Callable[[], PcSnapshot] | None,
    assistant: Assistant,
    notifier: Notifier | None,
    game_mode: GameMode | None,
) -> Iterator[None]:
    """Her notices about a nearly full drive or a GPU that stays hot while the block runs, also
    pushed to the phone when notifications are on; none with the PC health or its notices
    off."""
    if pc is None or not config.pc_health.notices:
        yield
        return
    watcher = PcWatcher(
        pc,
        config.pc_health,
        announce=_announcer(assistant, game_mode, pushed=notifier is not None),
        notifier=notifier,
        owner_call=config.assistant.owner_call,
        title=config.assistant.name,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _pc(args: argparse.Namespace) -> int:
    """Print how the PC is doing now, read as the `pc_health` tool reads it (the first reading
    samples the CPU over half a second). It needs no config, so it works with the PC health
    off too."""
    for line in _pc_lines(read_snapshot(cpu=CpuLoad())):
        print(line)
    return 0


def _uptime_text(seconds: int) -> str:
    """Under a day as hours and minutes, else as days and hours: "26 hari 13 jam"."""
    days, rest = divmod(seconds, 86400)
    if not days:
        return duration_text(rest // 60)
    hours = rest // 3600
    return f"{days} hari {hours} jam" if hours else f"{days} hari"


def _pc_lines(snapshot: PcSnapshot) -> list[str]:
    """One line per part of the PC, rounded as the tool rounds them; a part that could not be
    read says so."""
    cpu = PC_UNREADABLE if snapshot.cpu_percent is None else f"{round(snapshot.cpu_percent)}%"
    drives = [f"Disk {drive.letter} {_drive_text(drive)}" for drive in snapshot.drives]
    uptime = PC_UNREADABLE if snapshot.uptime_s is None else _uptime_text(snapshot.uptime_s)
    return [
        f"CPU: {cpu}",
        f"RAM: {_ram_text(snapshot.ram_used_gb, snapshot.ram_total_gb)}",
        *(drives or [f"Disk: {PC_UNREADABLE}"]),
        f"GPU: {_gpu_text(snapshot.gpu)}",
        f"Sejak restart: {uptime}",
    ]


def _ram_text(used_gb: float | None, total_gb: float | None) -> str:
    """`12,3 dari 31,9 GB terpakai (39%)`."""
    if used_gb is None or total_gb is None:
        return PC_UNREADABLE
    used, total = _decimal_comma(used_gb), _decimal_comma(total_gb)
    return f"{used} dari {total} GB terpakai ({_share(used_gb, total_gb)}%)"


def _drive_text(drive: Drive) -> str:
    """`tinggal 120,5 dari 465,1 GB (26% kosong)`."""
    free, total = _decimal_comma(drive.free_gb), _decimal_comma(drive.total_gb)
    return f"tinggal {free} dari {total} GB ({_share(drive.free_gb, drive.total_gb)}% kosong)"


def _gpu_text(gpu: GpuStatus | None) -> str:
    """The GPU's name and each reading nvidia-smi could give."""
    if gpu is None:
        return PC_WITHOUT_GPU
    readings = [gpu.name]
    if gpu.temp_c is not None:
        readings.append(f"{gpu.temp_c}°C")
    if gpu.load_percent is not None:
        readings.append(f"beban {gpu.load_percent}%")
    if gpu.vram_used_gb is not None and gpu.vram_total_gb is not None:
        used, total = _decimal_comma(gpu.vram_used_gb), _decimal_comma(gpu.vram_total_gb)
        readings.append(f"VRAM {used} dari {total} GB")
    if gpu.power_w is not None:
        readings.append(f"daya {_decimal_comma(gpu.power_w)} W")
    return ", ".join(readings)


def _share(part: float, whole: float) -> int:
    """`part` as a whole percent of `whole`; of nothing (a drive reporting no size) it is 0."""
    return round(100 * part / whole) if whole > 0 else 0


def _decimal_comma(value: float) -> str:
    """`8,4`: one decimal, written with the Indonesian decimal comma."""
    return f"{value:.1f}".replace(".", ",")


# --- play time -------------------------------------------------------------------------------

PERIOD_NAMES = {"today": "hari ini", "yesterday": "kemarin", "week": "minggu ini"}
PLAY_TIME_NOTE = (
    "Dihitung hanya selama 'waifu run' menyala, sejak {since}; game yang terbuka tapi tidak "
    "dimainkan ikut terhitung."
)


def _play_time_store(config: AppConfig) -> PlayTimeStore:
    """The sessions of the tracked games, in the file `play_time.path` names (`run` writes it;
    the tool, `waifu playtime`, `waifu gacha report`, and `check` read it)."""
    play = config.play_time
    return PlayTimeStore(play.path, games=list(play.games), keep_days=play.keep_days, now=wib_now)


@contextmanager
def _play_time_watcher(
    config: AppConfig,
    assistant: Assistant,
    game_mode: GameMode | None,
    closed_games: ClosedGames | None,
    notifier: Notifier | None,
    internet: _InternetWatch | None,
) -> Iterator[None]:
    """Counting how long Master's games are open while the block runs, from the process list's
    names only, telling game mode (and with `internet`, the internet watcher's state, which checks
    the HoYo hosts only while a game runs) after each poll whether a game runs, and with
    `play_time.bedtime` the once-a-night nudge, said once the follow-up window has closed, so a
    conversation is never interrupted; nothing with play time off. Stopping ends the open
    sessions at their last poll. While game mode holds her announcements, the nudge is said
    like Master's reminders: it is due only while a game is open, so held until the game has
    closed it would come too late. Each game just closed goes into `closed_games`, when
    given, and each poll's word goes to it as to game mode. With app reminders on, each game
    Master just opened gets his reminders for its start (`_app_reminder_teller`)."""
    if not config.play_time.enabled:
        yield
        return
    holds = config.game_mode.announcements != "normal"
    teller = (
        _app_reminder_teller(config, assistant, notifier) if config.app_reminders.enabled else None
    )
    watcher = PlayTimeWatcher(
        running_executables,
        _play_time_store(config),
        config.play_time,
        announce=assistant.remind if holds else _announcer(assistant, None, patient=True),
        owner_call=config.assistant.owner_call,
        on_poll=_poll_listeners(
            closed_games, game_mode, None if internet is None else internet.state
        ),
        on_finished=None if closed_games is None else closed_games.put,
        on_started=None if teller is None else teller.game_started,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _app_reminder_store(config: AppConfig) -> AppReminderStore:
    """Master's game start reminders, in the file `app_reminders.path` names (`run`'s teller, the
    tool, `waifu pengingat-game`, and `check`), whether they are on or not."""
    return AppReminderStore(config.app_reminders.path, now=wib_now)


def _app_reminder_teller(
    config: AppConfig, assistant: Assistant, notifier: Notifier | None
) -> AppReminderTeller:
    """Says a game's reminders at its start as Master's own (`remind`: also in a focus block or a
    game hold, whatever `game_mode.announcements`) and, with notifications on, pushes them as a
    schedule reminder is pushed, from a thread of its own: the play time watcher's thread must
    never wait for a push."""
    push = None if notifier is None else _phone_push(notifier, config.assistant.name, SCHEDULE_TAG)

    def tell(text: str, emotion: str) -> None:
        assistant.remind(text, emotion)
        if push is not None:
            threading.Thread(
                target=push, args=(text,), name="app-reminder-push", daemon=True
            ).start()

    return AppReminderTeller(
        _app_reminder_store(config), tell=tell, owner_call=config.assistant.owner_call
    )


APP_REMINDER_WHEN = {False: "sekali", True: "tiap dibuka"}
APP_REMINDERS_OFF = "Pengingat saat game dibuka mati"
APP_REMINDERS_WITHOUT_PLAY_TIME = (
    f"{APP_REMINDERS_OFF}: game yang dibuka dilihat dari play_time, yang mati"
)


def _app_reminder_list(args: argparse.Namespace) -> int:
    """Every game start reminder, in the order made, from the file alone: it works with them off
    too, after saying so. Exit 1 when the file cannot be read."""
    config = load_config(args.config)
    store = _app_reminder_store(config)
    try:
        reminders = store.reminders()
    except OSError as exc:
        print(f"{FAIL} File pengingat game {store.path} tidak bisa dibaca: {exc}")
        return 1
    if not config.app_reminders.enabled:
        print(
            f"{WARN} {APP_REMINDERS_OFF} (app_reminders.enabled: false): tidak ada yang diucapkan."
        )
    elif not config.play_time.enabled:
        print(f"{WARN} {APP_REMINDERS_WITHOUT_PLAY_TIME}.")
    if not reminders:
        print("Belum ada pengingat game.")
        return 0
    rows = [(r.id, GAMES[r.game].short, APP_REMINDER_WHEN[r.every_time], r.text) for r in reminders]
    print(_format_table(("ID", "GAME", "KAPAN", "PENGINGAT"), rows))
    return 0


def _app_reminder_remove(args: argparse.Namespace) -> int:
    """Remove one game start reminder by its id (`g2`, or just `2`); exit 1 when there is no such
    reminder or the file cannot be read."""
    store = _app_reminder_store(load_config(args.config))
    given = args.reminder_id
    reminder_id = f"g{given}" if given.isdecimal() else given
    try:
        removed = store.remove(reminder_id)
    except OSError as exc:
        print(f"{FAIL} File pengingat game {store.path} tidak bisa dibaca: {exc}")
        return 1
    if removed is None:
        print(f"{FAIL} Tidak ada pengingat game dengan id {reminder_id}.")
        return 1
    print(f"{OK} Dihapus {removed.id}: {GAMES[removed.game].short}, {removed.text}")
    return 0


def _poll_listeners(
    *listeners: ClosedGames | GameMode | NetworkState | None,
) -> Callable[[bool, bool], None] | None:
    """Each play time poll's word (whether a game is open, whether the first poll resumed one)
    for every listener given, in order; None without any."""
    observers = [listener.observe for listener in listeners if listener is not None]
    if not observers:
        return None

    def observe(playing: bool, resumed: bool) -> None:
        for each in observers:
            each(playing, resumed)

    return observe


def _playtime(args: argparse.Namespace) -> int:
    """Print how long each tracked game was open in a WIB period, from the play time file alone:
    it works with play time off too. Exit 1 when the file cannot be read."""
    config = load_config(args.config)
    store = _play_time_store(config)
    try:
        record = store.record()
    except OSError as exc:
        print(f"{FAIL} File jam main {store.path} tidak bisa dibaca: {exc}")
        return 1
    if not config.play_time.enabled:
        print(
            f"{WARN} Jam main mati (play_time.enabled: false): tidak ada yang dihitung; ini "
            "catatan lama."
        )
    tracked = [s for s in (*record.sessions, *record.open) if s.game in store.games]
    if not tracked:
        print("Belum ada catatan jam main.")
        return 0
    now = wib_now()
    since, until = period_window(args.period, now)
    print(f"Jam main {_period_text(args.period, since)}, dari catatan {config.assistant.name}:")
    rows = _play_rows(config, record, since, until, now)
    print(_format_table(("GAME", "WAKTU", "SESI", ""), rows))
    first = min(session.start for session in tracked).astimezone(WIB)
    print(PLAY_TIME_NOTE.format(since=_schedule_time(first)))
    return 0


def _period_text(period: str, since: datetime) -> str:
    """`hari ini, Sabtu 17-10-2026`: the period and the days it covers."""
    if period == "month":
        return f"bulan ini, {MONTHS_ID[since.month - 1]} {since.year}"
    day = f"{WEEKDAYS_ID[since.weekday()]} {since:%d-%m-%Y}"
    return f"{PERIOD_NAMES[period]}, {'sejak ' if period == 'week' else ''}{day}"


def _play_rows(
    config: AppConfig, record: PlayRecord, since: datetime, until: datetime, now: datetime
) -> list[tuple[str, ...]]:
    """A row per tracked game, its time and sessions in the period (and whether it is open now,
    when the period reaches now), then the total, in which time two games were open together
    counts once."""
    games = list(config.play_time.games)
    open_now = (
        playing_now(record, now, poll=timedelta(seconds=config.play_time.poll_seconds))
        if until >= now
        else frozenset()
    )
    totals = play_totals(record, games, since, until)
    rows = [
        (
            GAMES[game].name,
            _play_duration(total.seconds) if total.sessions else "-",
            str(total.sessions),
            "sedang main" if game in open_now else "",
        )
        for game, total in totals.items()
    ]
    sessions = [s for s in (*record.sessions, *record.open) if s.game in games]
    total_seconds = played_seconds(sessions, since, until)
    count = sum(total.sessions for total in totals.values())
    rows.append(("Total", _play_duration(total_seconds) if count else "-", str(count), ""))
    return rows


def _play_duration(seconds: int) -> str:
    """`2 jam 39 menit`, rounded to the minute."""
    return duration_text(whole_minutes(seconds))


# --- pemantau internet -----------------------------------------------------------------------


def _network_store(config: AppConfig) -> NetworkStore:
    """The internet outages, in the file `network.path` names (`run`'s watcher writes it; the
    tool, the recap, the weekly report, `waifu internet`, and `check` read it)."""
    network = config.network
    return NetworkStore(network.path, keep_days=network.keep_days, now=wib_now)


@dataclass(frozen=True)
class _InternetWatch:
    """What one command knows of the internet: one probe, so a check never starts a probe of a
    target still running from another, and one state, which the checks note."""

    probe: NetworkProbe
    state: NetworkState
    check: NetworkCheck  # the probe's checks, classified with the state and noted in it


def _internet_watch(config: AppConfig) -> _InternetWatch | None:
    """A new view of the internet with the watcher on; None with it off."""
    return _internet_view(config.network) if config.network.enabled else None


def _internet_view(network: NetworkConfig) -> _InternetWatch:
    """A new view of the internet as `network` says to probe and judge it."""
    probe = NetworkProbe(network.hosts)
    state = NetworkState(network.slow_window)
    return _InternetWatch(probe, state, NetworkCheck(probe, state, slow_ms=network.slow_ms))


@contextmanager
def _network_watcher(
    config: AppConfig,
    internet: _InternetWatch | None,
    assistant: Assistant,
    game_mode: GameMode | None,
) -> Iterator[None]:
    """Watching the internet while the block runs, with `internet` (the watcher on): an outage
    kept in `network.path` and in the state, and her line once it is back, said once the
    follow-up window has closed and held like her other notices (never pushed). Stopping ends an
    open outage at its last failed poll."""
    if internet is None:
        yield
        return
    watcher = NetworkWatcher(
        internet.probe,
        _network_store(config),
        config.network,
        internet.state,
        announce=_announcer(assistant, game_mode, patient=True),
        owner_call=config.assistant.owner_call,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


INTERNET_OFF = "Pemantau internet mati"
INTERNET_NOTE = "Putusnya dicatat hanya selama 'waifu run' menyala."
_STATUS_MARKS: dict[Status, str] = {
    "ok": OK,
    "slow": WARN,
    "dns": WARN,
    "hoyo": WARN,
    "offline": FAIL,
    "router": FAIL,
}


def _internet(args: argparse.Namespace) -> int:
    """Check the internet now, with the lookup and the HoYo hosts, and print what answered (the
    router's address only here) and today's outages from the history alone: it works with the
    watcher off too, after saying so. Writes nothing; a broken history is moved aside, as `run`
    would. Exit 1 when the history cannot be read."""
    config = load_config(args.config)
    network = config.network
    if not network.enabled:
        print(
            f"{WARN} {INTERNET_OFF} (network.enabled: false): tidak ada yang dicatat; riwayatnya "
            "catatan lama."
        )
    print("Memeriksa internet sekarang, tunggu sebentar...", flush=True)
    finding = _internet_view(network).check.now(hoyo=True)
    print("\n".join(_finding_lines(finding, network.hosts)))
    store = _network_store(config)
    try:
        record = store.record()
    except OSError as exc:
        print(f"{FAIL} File riwayat internet {store.path} tidak bisa dibaca: {exc}")
        return 1
    print(_outages_today_line(record, wib_now()))
    print(INTERNET_NOTE)
    return 0


def _finding_lines(finding: Finding, hosts: NetworkHostsConfig) -> list[str]:
    """The status, then how the router, the anchors, DNS, and the HoYo hosts answered, and what
    Master can try for a problem he can act on."""
    reading = finding.reading
    lines = [
        f"{_STATUS_MARKS[finding.status]} Status: {MEANINGS[finding.status]}",
        f"  {_router_text(reading)}",
        f"  Internet ({', '.join(hosts.anchors)}): {_answer_text(reading.anchor_ms)}",
        f"  DNS ({hosts.dns}): {_answer_text(reading.dns_ms)}",
    ]
    hoyo = finding.hoyo
    if hoyo is not None:
        answered = (
            "tidak ada yang menjawab"
            if hoyo.median_ms is None
            else f"{hoyo.answered} dari {hoyo.of} menjawab, sekitar {_ms_text(hoyo.median_ms)}"
        )
        lines.append(f"  Server HoYoLAB/HoYoverse: {answered} (bukan ping server game)")
    advice = ADVICE.get(finding.status)
    if advice is not None:
        lines.append(f"  Saran: {advice}")
    return lines


def _router_text(reading: Reading) -> str:
    if not reading.route:
        return "Router: tidak ada rute ke internet (Wi-Fi atau kabel tidak tersambung)"
    if reading.gateway is None:
        return "Router: tidak ada, PC tersambung langsung"
    if reading.gateway_ms is None:
        return (
            f"Router {reading.gateway}: tidak menjawab ping (banyak router memang mengabaikannya)"
        )
    return f"Router {reading.gateway}: {_answer_text(reading.gateway_ms)}"


def _answer_text(took_ms: float | None) -> str:
    return "tidak menjawab" if took_ms is None else f"menjawab dalam {_ms_text(took_ms)}"


def _ms_text(took_ms: float) -> str:
    """`24 ms`, whole and a half up; `<1 ms` for a LAN's 0."""
    whole = int(took_ms + 0.5)
    return f"{whole} ms" if whole else "<1 ms"


def _outages_today_line(record: NetworkRecord, now: datetime) -> str:
    """`Hari ini: putus 2 kali, total 16 menit: 21.03-21.15, 22.30-22.34`: the outages with time
    today, each from its start to its end, an open one to its last failed poll."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = outage_totals(record, midnight, now)
    if not total.count:
        return "Hari ini: tidak ada yang putus"

    def today(start: datetime, end: datetime) -> bool:
        return min(end, now) > max(start, midnight)

    spans = [
        f"{outage.start:%H.%M}-{outage.end:%H.%M}"
        for outage in record.outages
        if today(outage.start, outage.end)
    ]
    opened = record.open
    if opened is not None and today(opened.start, opened.last_failed):
        spans.append(f"{opened.start:%H.%M}-{opened.last_failed:%H.%M} (belum terlihat nyambung)")
    return (
        f"Hari ini: putus {total.count} kali, total {_play_duration(total.seconds)}: "
        f"{', '.join(spans)}"
    )


# --- focus companion -------------------------------------------------------------------------

FOCUS_TAG = "hourglass_flowing_sand"  # ntfy shows it as an hourglass emoji
FOCUS_NOTE = (
    "Dicatat hanya selama 'waifu run' menyala; sesi fokus dimulai dan dihentikan lewat {name}."
)


def _focus_store(config: AppConfig) -> FocusStore:
    """The focus blocks, in the file `focus.path` names (`run` writes it; `waifu focus` and
    `check` read it)."""
    return FocusStore(config.focus.path, keep_days=config.focus.keep_days, now=wib_now)


def _focus_timer(config: AppConfig) -> FocusTimer | None:
    """The focus timer of one `run`, or None with the focus companion off."""
    if not config.focus.enabled:
        return None
    return FocusTimer(_focus_store(config), owner_call=config.assistant.owner_call, now=wib_now)


@contextmanager
def _focus_session(
    config: AppConfig, focus: FocusTimer | None, assistant: Assistant, notifier: Notifier | None
) -> Iterator[None]:
    """The focus timer runs while the block runs, saying its lines through `assistant.remind`
    (Master asked for the session, so they come during a block too) and, with `focus.push` and
    notifications on, pushing them as well. Stopping ends a session in progress, so a restart
    ends it; nothing without a timer."""
    if focus is None:
        yield
        return
    focus.start(_focus_teller(config, assistant, notifier))
    try:
        yield
    finally:
        focus.stop()


def _focus_teller(
    config: AppConfig, assistant: Assistant, notifier: Notifier | None
) -> Callable[[str, str], None]:
    """Queues a focus line for her to say and, with `focus.push` and notifications on, pushes it
    from a thread of its own: the timer calls this under its lock, which must never wait for a
    push."""
    push = (
        _phone_push(notifier, config.assistant.name, FOCUS_TAG)
        if config.focus.push and notifier is not None
        else None
    )

    def tell(text: str, emotion: str) -> None:
        assistant.remind(text, emotion)
        if push is not None:
            threading.Thread(target=push, args=(text,), name="focus-push", daemon=True).start()

    return tell


def _focus(args: argparse.Namespace) -> int:
    """Print today's and this week's focus time from the focus log alone: it works with the focus
    companion off too. Exit 1 when the log cannot be read."""
    config = load_config(args.config)
    store = _focus_store(config)
    try:
        blocks = store.blocks()
    except OSError as exc:
        print(f"{FAIL} File fokus {store.path} tidak bisa dibaca: {exc}")
        return 1
    if not config.focus.enabled:
        print(f"{WARN} Teman fokus mati (focus.enabled: false): ini catatan lama.")
    if not blocks:
        print("Belum ada catatan fokus.")
        return 0
    now = wib_now()
    for period in ("today", "week"):
        since, until = period_window(period, now)
        total = focus_totals(blocks, since, until)
        focused = (
            f"{_play_duration(total.seconds)} dalam {total.blocks} blok"
            if total.blocks
            else "belum ada"
        )
        print(f"Fokus {_period_text(period, since)}: {focused}")
    print(FOCUS_NOTE.format(name=config.assistant.name))
    return 0


# --- break reminders -------------------------------------------------------------------------


@contextmanager
def _break_watcher(
    config: AppConfig,
    focus: FocusTimer | None,
    assistant: Assistant,
    notifier: Notifier | None,
    game_mode: GameMode | None,
) -> Iterator[None]:
    """Her gentle reminder to rest after every `breaks.every_minutes` of keyboard and mouse
    activity while the block runs, never during a focus block (the watcher asks the focus
    timer), also pushed to the phone when `breaks.push` and notifications are on; none with the
    reminder off. She says it once the follow-up window has closed, so a conversation is never
    interrupted."""
    breaks = config.breaks
    if not breaks.enabled:
        yield
        return
    push = notifier if breaks.push else None
    watcher = BreakWatcher(
        read_input_idle,
        breaks,
        announce=_break_teller(config, assistant, game_mode, pushed=push is not None),
        notifier=push,
        owner_call=config.assistant.owner_call,
        title=config.assistant.name,
        in_focus_block=None if focus is None else focus.in_block,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _break_teller(
    config: AppConfig, assistant: Assistant, game_mode: GameMode | None, *, pushed: bool
) -> Callable[..., None]:
    """How a break reminder is queued: like the rain alert (held while game mode holds her
    announcements, left unsaid in `phone` mode when it was pushed), but with
    `breaks.during_games`, while a game holds them, like Master's reminders, so it is said while
    he plays. The watcher never gives one during a focus block, which `remind` would not wait
    for."""
    announce = _announcer(assistant, game_mode, patient=True, pushed=pushed)
    if not config.breaks.during_games or game_mode is None:
        return announce
    holding = game_mode.holding

    def tell(
        text: str,
        emotion: str = NEUTRAL,
        *,
        expires_at: datetime | None = None,
        game_expires_at: datetime | None = None,
    ) -> None:
        if holding():
            assistant.remind(text, emotion)
            return
        announce(text, emotion, expires_at=expires_at, game_expires_at=game_expires_at)

    return tell


# --- waktu sholat ----------------------------------------------------------------------------

PRAYER_ROWS = (
    ("imsak", "Imsak"),
    ("subuh", "Subuh"),
    ("terbit", "Terbit"),
    ("dzuhur", "Dzuhur"),
    ("ashar", "Ashar"),
    ("maghrib", "Maghrib"),
    ("isya", "Isya"),
)
PRAYER_NO_TIME = "--: matahari tidak sampai ke sudut waktunya di tempat ini pada hari itu."
PRAYER_METHOD = "Metode Kemenag RI: Subuh -20°, Isya -18°, Ashar bayangan 1x, ihtiyath {} menit."


def _prayer_place(config: AppConfig) -> PrayerPlace:
    """Where the prayer times are for: `prayer`'s coordinates, else the briefing's place (its
    coordinates, else its city, geocoded at the first use and kept); none without either.
    Named by `briefing.city` as the weather names it; without a city the name is the
    coordinates, so the place is not `named` and her tool never tells them. Nothing is looked
    up here. The weather client logs its failures at DEBUG: the tool, the watcher, and
    `waifu sholat` tell them themselves."""
    prayer = config.prayer
    city = config.briefing.city
    if prayer.latitude is not None and prayer.longitude is not None:
        place = Location(
            city or f"{prayer.latitude}, {prayer.longitude}", prayer.latitude, prayer.longitude
        )
        return PrayerPlace(lambda: place, named=city is not None)
    weather = _weather_client(config.briefing, failure_level=logging.DEBUG)
    return PrayerPlace(None if weather is None else weather.location, named=city is not None)


@contextmanager
def _prayer_watcher(
    config: AppConfig,
    place: PrayerPlace | None,
    assistant: Assistant,
    notifier: Notifier | None,
) -> Iterator[None]:
    """Her reminder as each prayer of `prayer.remind` comes in while the block runs, at `place`
    (none with the prayer times off) and through Master's own reminder queue: he turned it on,
    so a focus block or a game never holds it, and game mode's `phone` mode never leaves it
    unsaid. Also pushed to the phone when `prayer.push` and notifications are on; none without a
    prayer to remind of."""
    prayer = config.prayer
    if place is None or not prayer.remind:
        yield
        return
    watcher = PrayerWatcher(
        place,
        prayer,
        remind=assistant.remind,
        notifier=notifier if prayer.push else None,
        owner_call=config.assistant.owner_call,
        title=config.assistant.name,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _prayer(args: argparse.Namespace) -> int:
    """Print a day's prayer times (today in WIB by default) at the place, without the brain; it
    works with the prayer times off too. Exit 1 without a place or when it cannot be found."""
    config = load_config(args.config)
    prayer = config.prayer
    if not prayer.enabled:
        print(f"{WARN} Waktu sholat mati (prayer.enabled: false).")
    try:
        place = _prayer_place(config).locate()
    except PrayerError as exc:
        print(f"{FAIL} {exc}")
        return 1
    day = args.date or wib_now().date()
    times = prayer_times(
        day, place.latitude, place.longitude, ihtiyath_minutes=prayer.ihtiyath_minutes
    )
    print(f"Waktu sholat di {place.place}, {_day_text(day)} (WIB):")
    missing = False
    for name, label in PRAYER_ROWS:
        moment = getattr(times, name)
        missing = missing or moment is None
        print(f"  {label:<8} {'--' if moment is None else f'{moment:%H:%M}'}")
    if missing:
        print(PRAYER_NO_TIME)
    print(PRAYER_METHOD.format(prayer.ihtiyath_minutes))
    return 0


# --- free offers -----------------------------------------------------------------------------


def _offers_store(config: AppConfig) -> OffersStore:
    """The games already told, the AI offers her scans found, and the last scan, in the file
    `free_offers.state_path` names (`run`, `chat`, and `waifu offers` share it)."""
    return OffersStore(config.free_offers.state_path)


def _games_reader(config: AppConfig) -> ReadGames:
    """Reads the free games on `free_offers.game_platforms` from Epic and GamerPower.com, with a
    connection of its own each time (reads are half an hour or hours apart)."""
    platforms = tuple(config.free_offers.game_platforms)

    def read() -> tuple[GameGiveaway, ...] | OffersError:
        with httpx.Client() as http:
            return read_free_games(platforms, http=http, now=datetime.now().astimezone())

    return read


def _games_off() -> OffersError:
    return OffersError(GAMES_OFF)


def _offers_tools(config: AppConfig, notifier: Notifier | None) -> OffersTools:
    """The free games (unless `free_offers.games` is off) and the AI offers her scans kept, with
    their links pushed through `notifier` when Master asks."""
    read = _games_reader(config) if config.free_offers.games else _games_off
    return OffersTools(
        _offers_store(config), read, notifier=notifier, owner_call=config.assistant.owner_call
    )


def _claude_client() -> anthropic.Anthropic:
    """A Claude client for the AI scan, made as the Claude brain makes one: with the workspace
    an organization-level API key needs, and refused without credentials. It keeps the SDK's
    own timeout and retries: the scan runs in the background, and a search may take minutes."""
    import anthropic

    from waifu.brain.claude import WORKSPACE_ENV

    workspace_id = os.environ.get(WORKSPACE_ENV, "").strip()
    client = anthropic.Anthropic(
        default_headers={CLAUDE_WORKSPACE_HEADER: workspace_id} if workspace_id else None
    )
    if client.api_key is None and client.auth_token is None and client.credentials is None:
        raise anthropic.CredentialsError("no Claude API key, auth token, or profile found")
    return client


def _ai_scan(config: AppConfig, on_usage: Callable[[str, TokenUsage], None]) -> ScanAiOffers:
    """The AI offers scan: the configured Claude model with web search, given the offers already
    kept; `on_usage` hears what each of its responses used.

    One scan at a time across processes (`waifu run`'s watcher and `waifu offers ai --scan`),
    held by a lock file next to the offers file: a second one would pay for the same searches.
    One that finds the lock held is an error that cost nothing, so the watcher tries again at
    its next poll."""
    state_path = config.free_offers.state_path
    lock_path = state_path.with_name(f"{state_path.name}.scan.lock")

    def scan(known: Sequence[AiOffer]) -> list[AiOffer] | OffersError:
        # Here, so that a run with Ollama loads the Anthropic SDK only once a scan is due.
        from waifu.offers.ai_scan import scan_ai_offers

        lock = InstanceLock(lock_path)
        if not lock.acquire():
            logger.info("Another AI offers scan holds %s; not scanning", lock_path)
            return OffersError(SCAN_RUNNING)
        try:
            return scan_ai_offers(
                client_factory=_claude_client,
                model=config.brain.claude.model,
                fallbacks=config.brain.claude.fallbacks,
                max_searches=config.free_offers.ai_max_searches,
                on_usage=on_usage,
                known=known,
            )
        finally:
            lock.release()

    return scan


def _not_recorded(model: str, tokens: TokenUsage) -> None:
    """Without the cost monitor (Ollama, or `usage.enabled` off) no response is recorded."""


@contextmanager
def _offers_watcher(
    config: AppConfig,
    assistant: Assistant,
    notifier: Notifier | None,
    usage: _UsageRecorder | None,
    game_mode: GameMode | None,
) -> Iterator[None]:
    """Her notices about new free games and new AI offers while the block runs, also pushed to
    the phone when notifications are on; none with the offers or their notices off. The scan's
    responses are recorded with her other Claude costs when the cost monitor is on."""
    offers = config.free_offers
    if not (offers.enabled and offers.notices):
        yield
        return
    watcher = OffersWatcher(
        _offers_store(config),
        _games_reader(config),
        _ai_scan(config, usage.record if usage is not None else _not_recorded),
        offers,
        announce=_announcer(assistant, game_mode, pushed=notifier is not None),
        notifier=notifier,
        owner_call=config.assistant.owner_call,
        title=config.assistant.name,
    )
    watcher.start()
    try:
        yield
    finally:
        watcher.stop()


def _offers(args: argparse.Namespace) -> int:
    """Print the free games, then the AI offers; exit 1 when the games could not be read."""
    config = load_config(args.config)
    games_read = _print_games(config)
    print()
    _print_ai_offers(_offers_store(config))
    return 0 if games_read else 1


def _offers_games(args: argparse.Namespace) -> int:
    return 0 if _print_games(load_config(args.config)) else 1


def _offers_ai(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.scan:
        return _scan_ai_offers_now(config)
    _print_ai_offers(_offers_store(config))
    return 0


def _print_games(config: AppConfig) -> bool:
    """Print the games free now, then those free soon, each with its link, and GamerPower.com's
    credit when a game came from it; False when they could not be read. The switches only
    concern her, so the games are read whatever `free_offers` says."""
    games = _games_reader(config)()
    if isinstance(games, OffersError):
        print(f"{FAIL} {games}")
        return False
    if not games:
        print("Tidak ada game gratis saat ini.")
        return True
    now = datetime.now().astimezone()
    soon = [game for game in games if game.starts_at is not None and game.starts_at > now]
    free = [game for game in games if game not in soon]
    for heading, listed in (("Game gratis sekarang:", free), ("Segera gratis:", soon)):
        if listed:
            print(heading)
        for game in listed:
            print(f"  {_game_text(game, now)}")
            print(f"    {game.url}")
    if any(game.source == GAMERPOWER for game in games):
        print(GAMERPOWER_CREDIT)
    return True


def _game_text(game: GameGiveaway, now: datetime) -> str:
    """`Portal di Steam (biasanya $9.99), sampai Minggu 25-10-2026`; a game free soon says when
    it starts."""
    parts = [f"{game.title} di {game.platform}"]
    if game.worth_usd is not None:
        parts[0] += f" (biasanya ${game.worth_usd:.2f})"
    if game.starts_at is not None and game.starts_at > now:
        parts.append(f"mulai {_schedule_time(game.starts_at.astimezone())}")
    if game.ends_at is not None:
        parts.append(f"sampai {_schedule_time(game.ends_at.astimezone())}")
    elif game.ends_on is not None:
        parts.append(f"sampai {_day_text(game.ends_on)}")
    return ", ".join(parts)


def _print_ai_offers(store: OffersStore) -> None:
    """Print the kept AI offers, the newest first, each with its link, and when the last scan
    ran; or that none ran yet."""
    last_scan = store.last_ai_scan()
    if last_scan is None:
        print(
            "Promo AI belum pernah dicari. Jalankan 'waifu offers ai --scan' (memakai Claude "
            "dengan pencarian web, berbayar)."
        )
        return
    offers = store.ai_offers()
    scanned = _schedule_time(last_scan)
    if not offers:
        print(f"Belum ada promo AI yang tersimpan (pencarian terakhir {scanned}).")
        return
    print(f"Promo AI (pencarian terakhir {scanned}):")
    _print_offer_list(sorted(offers, key=lambda offer: offer.first_seen.timestamp(), reverse=True))


def _print_offer_list(offers: Sequence[AiOffer]) -> None:
    for offer in offers:
        terms = f"{AI_KIND_TEXT[offer.kind]}, {AI_ACCESS_TEXT[offer.access]}"
        if offer.deadline is not None:
            terms += f", sampai {_day_text(offer.deadline)}"
        print(f"  {offer.title} dari {offer.provider}: {terms}")
        print(f"    {offer.summary}")
        print(f"    Untuk: {offer.eligibility}")
        # The scan reads pages anyone can write: a link off the provider's site may be a lure.
        print(f"    {offer.url}" + ("" if official_link(offer.url) else f"  {UNVERIFIED_LINK}"))


def _scan_ai_offers_now(config: AppConfig) -> int:
    """Run the AI scan now, keep what it found, and print the new offers and what the scan cost
    by the usage prices. Its responses are recorded with her other Claude costs when the cost
    monitor is on; exit 1 when it failed."""
    store = _offers_store(config)
    recorder = _usage_recorder(config)
    # Built before the scan, so a price table that cannot price it stops before any spending.
    ledger = recorder.ledger if recorder is not None else _usage_ledger(config)
    used: list[tuple[str, TokenUsage]] = []

    def on_usage(model: str, tokens: TokenUsage) -> None:
        used.append((model, tokens))
        if recorder is not None:
            recorder.record(model, tokens)

    print("Mencari promo AI dengan Claude dan pencarian web, bisa beberapa menit...", flush=True)
    found = _ai_scan(config, on_usage)(store.ai_offers())
    if isinstance(found, OffersError):
        print(f"{FAIL} {found}")
    else:
        new = store.merge_ai_offers(found, datetime.now())
        if new:
            print(f"{OK} {len(new)} promo AI baru:")
            _print_offer_list(new)
        else:
            print(f"{OK} Tidak ada promo AI baru. Semua yang tersimpan: 'waifu offers ai'.")
    if used:
        print(_scan_cost_text(used, ledger, config.usage.web_search_per_1000_usd))
        if recorder is None:
            print(
                "(Tidak dicatat di 'waifu usage': biaya API hanya dicatat dengan brain.backend: "
                "claude dan usage.enabled: true.)"
            )
    return 1 if isinstance(found, OffersError) else 0


def _scan_cost_text(
    used: Sequence[tuple[str, TokenUsage]], ledger: UsageLedger, web_search_per_1000_usd: float
) -> str:
    """`Perkiraan biaya pencarian ini: $0.18 (1 permintaan, 6 pencarian web).`: the tokens by
    the ledger's prices and the searches by `usage.web_search_per_1000_usd`."""
    searches = sum(tokens.web_searches for _, tokens in used)
    tokens_usd = sum(
        cost_usd(model, tokens, ledger.prices, ledger.default_model)[0] for model, tokens in used
    )
    cost = tokens_usd + searches * web_search_per_1000_usd / 1000
    return (
        f"Perkiraan biaya pencarian ini: ${cost:.2f} ({len(used)} permintaan, {searches} "
        "pencarian web)."
    )


def _day_text(day: date) -> str:
    """`Minggu 25-10-2026`: a day as the owner reads it."""
    return f"{WEEKDAYS_ID[day.weekday()]} {day:%d-%m-%Y}"


# --- Master's money from tepira --------------------------------------------------------------


def _finance_tools(config: AppConfig, confirmations: ConfirmationGate) -> FinanceTools:
    """Master's money from tepira over one connection for the process, so a later question
    reuses it. Recording and its undo come only with `tepira.writes` and Claude, the brain that
    picks tools reliably, and an expense waits for Master's yes on `confirmations`, the brain's
    own gate."""
    tepira = config.tepira
    return FinanceTools(
        _tepira_client(config, httpx.Client()),
        writes=tepira.writes and config.brain.backend == "claude",
        default_wallet=tepira.default_wallet,
        confirmations=confirmations,
        owner_call=config.assistant.owner_call,
    )


def _tepira_client(config: AppConfig, http: httpx.Client) -> TepiraClient:
    """tepira's Cyrene API at `tepira.url` with the token, over `http`, which the caller owns."""
    token = config.tepira.token
    assert token is not None  # tepira.enabled needs the token (TepiraConfig)
    return TepiraClient(http, config.tepira.url, token)


def _tepira_summary(args: argparse.Namespace) -> int:
    """This month's spending, income, and budget, the month in WIB as she counts it."""
    today = datetime.now(WIB)
    return _tepira_view(
        args,
        lambda source: source.summary(today.month, today.year),
        lambda summary: _summary_lines(summary, today.month, today.year),
    )


def _tepira_wallets(args: argparse.Namespace) -> int:
    return _tepira_view(args, lambda source: source.wallets(), _wallet_lines)


def _tepira_goals(args: argparse.Namespace) -> int:
    query = " ".join(" ".join(args.goal_name).split()) or None
    # tepira measures the name as JavaScript does, in UTF-16 units: an emoji counts twice.
    if query is not None and len(query.encode("utf-16-le")) // 2 > MAX_GOAL_QUERY_CHARS:
        print(f"{FAIL} Nama tujuan paling banyak {MAX_GOAL_QUERY_CHARS} karakter.")
        return 2
    return _tepira_view(
        args, lambda source: source.goals(query), lambda goals: _goal_lines(goals, query)
    )


def _tepira_transactions(args: argparse.Namespace) -> int:
    return _tepira_view(
        args, lambda source: source.transactions(args.count, None), _transaction_lines
    )


def _tepira_token(args: argparse.Namespace) -> int:
    """A new Cyrene API token and its SHA-256, printed once and never logged: the token goes
    into config.yaml, the hash into Vercel. Needs neither the config nor the network."""
    token = f"tpcy_{secrets.token_urlsafe(TEPIRA_TOKEN_BYTES)}"
    print(f"token : {token}")
    print(f"sha256: {hashlib.sha256(token.encode('ascii')).hexdigest()}")
    print(TEPIRA_TOKEN_USE)
    return 0


def _tepira_view(
    args: argparse.Namespace,
    read: Callable[[FinanceSource], FinanceData],
    lines: Callable[[FinanceData], list[str]],
) -> int:
    """Print what `read` gets from tepira as `lines`, over a connection for this command only:
    exit 2 with tepira off, 1 when tepira gave no answer or one without a field the lines show."""
    config = load_config(args.config)
    if not config.tepira.enabled:
        print(TEPIRA_OFF)
        return 2
    try:
        with httpx.Client() as http:
            data = read(_tepira_client(config, http))
    except FinanceError as error:
        print(f"{FAIL} {error.message}")
        return 1
    try:
        shown = lines(data)
    except (AttributeError, KeyError, TypeError, ValueError):
        # The client checks only the fields every answer needs; these lines show a few more.
        logger.warning("tepira's answer lacks a field 'waifu tepira' shows")
        print(f"{FAIL} {UNREADABLE}")
        return 1
    print("\n".join(shown))
    return 0


def _summary_lines(summary: FinanceData, month: int, year: int) -> list[str]:
    """`Keuangan Oktober 2026 (per 2026-10-16 09:12 WIB)`, then spending, income, the budget
    and its categories, what fell outside them, and the weekly score; what tepira left out (a
    month without a salary row, no score yet) is left out of the lines."""
    spent, income = summary["spent"], summary["income"]
    spending = f"Pengeluaran: {_rupiah(spent['total'])} dari {spent['count']} transaksi"
    sources = _amounts(spent, ("gaji", "gaji"), ("bonus", "bonus"))
    if sources:
        spending += f" ({sources})"
    recent = _amounts(
        spent, ("today", "hari ini"), ("yesterday", "kemarin"), ("this_week", "minggu ini")
    )
    if recent:
        spending += f"; {recent}"
    lines = [
        f"Keuangan {MONTHS_ID[month - 1]} {year} (per {_wib_stamp(summary['as_of'])})",
        spending,
    ]
    earned = _amounts(
        income, ("salary", "gaji"), ("bonus", "bonus"), ("starting_balance", "saldo awal")
    )
    left = _amounts(income, ("net", "sisa"), ("ending_estimate", "perkiraan saldo akhir"))
    if earned or left:
        lines.append(f"Pemasukan: {'; '.join(part for part in (earned, left) if part)}")
    lines.append(_budget_line(summary["budget"]))
    lines += [_category_line(row) for row in summary["categories"]]
    other = summary["other_spent"]
    lines.append(
        f"Tanpa kategori {_rupiah(other['uncategorized'])}; "
        f"di luar budget {_rupiah(other['unbudgeted'])}"
    )
    insight = summary.get("insight")
    if insight is not None:
        lines.append(
            f"Skor mingguan: {insight['health_score']} ({insight['status']}), "
            f"minggu mulai {insight['week_start']}"
        )
    return lines


def _budget_line(budget: Mapping[str, Any]) -> str:
    """`Budget (gaji+bonus): Rp3.000.000, terpakai Rp2.350.000 (78,3%), sisa Rp650.000`."""
    title = f"Budget ({budget['basis']})"
    if budget["status"] == "NO_BUDGET":
        return f"{title}: belum diatur"
    used = f"terpakai {_rupiah(budget['spent'])} ({_percent(budget['used_percent'])})"
    return f"{title}: {_rupiah(budget['allocated'])}, {used}, {_left(budget['remaining'])}"


def _category_line(row: Mapping[str, Any]) -> str:
    """`- Makan harian: sisa Rp220.000 dari Rp1.200.000 (81,7%)`, `lewat` when it is over."""
    return (
        f"- {row['category']}: {_left(row['remaining'])} dari {_rupiah(row['allocated'])} "
        f"({_percent(row['used_percent'])})"
    )


def _wallet_lines(answer: FinanceData) -> list[str]:
    """`- GoPay (e-wallet): Rp250.000` per wallet, a debt below zero, then the totals."""
    lines = [
        f"- {wallet['name']} ({wallet['type']}): {_rupiah(wallet['balance'])}"
        for wallet in answer["wallets"]
    ]
    if answer["more"]:
        lines.append(f"dan {answer['more']} dompet lagi")
    totals = answer["totals"]
    lines.append(
        f"Aset {_rupiah(totals['assets'])}, kewajiban {_rupiah(totals['debts'])}, "
        f"kekayaan bersih {_rupiah(totals['net_worth'])}"
    )
    return lines


def _goal_lines(answer: FinanceData, query: str | None) -> list[str]:
    """One line per goal, unfinished first as tepira orders them."""
    if not answer["goals"]:
        return ["Tidak ada tujuan tabungan yang cocok." if query else "Belum ada tujuan tabungan."]
    lines = [_goal_line(goal) for goal in answer["goals"]]
    if answer["more"]:
        lines.append(f"dan {answer['more']} tujuan lagi")
    return lines


def _goal_line(goal: Mapping[str, Any]) -> str:
    """`- Laptop baru: Rp6.200.000 dari Rp15.000.000 (41,3%), kurang Rp8.800.000, tenggat
    2027-03-31 (166 hari lagi), perlu Rp1.600.000 per bulan`; a goal without a deadline has
    neither of the last two."""
    parts = [
        f"{_rupiah(goal['saved'])} dari {_rupiah(goal['target'])} ({_percent(goal['percent'])})",
        f"kurang {_rupiah(goal['remaining'])}" if goal["remaining"] > 0 else "tercapai",
    ]
    if "deadline" in goal:
        days = f" ({goal['days_left']} hari lagi)" if "days_left" in goal else ""
        parts.append(f"tenggat {goal['deadline']}{days}")
    if "per_month_needed" in goal:
        parts.append(f"perlu {_rupiah(goal['per_month_needed'])} per bulan")
    return f"- {goal['title']}: {', '.join(parts)}"


def _transaction_lines(answer: FinanceData) -> list[str]:
    """`- 2026-10-16 08:41 Rp18.000 Nongkrong / jajan: kopi susu (GoPay)`, newest first."""
    if not answer["transactions"]:
        return ["Belum ada transaksi."]
    lines = []
    for row in answer["transactions"]:
        line = f"- {row['date']} {_rupiah(row['amount'])} {row['category'] or 'tanpa kategori'}"
        if row["note"]:
            line += f": {row['note']}"
        if row["wallet"]:
            line += f" ({row['wallet']})"
        lines.append(line)
    return lines


def _amounts(values: Mapping[str, Any], *labelled: tuple[str, str]) -> str:
    """`gaji Rp5.000.000, bonus Rp500.000`: each (key, label) whose amount `values` holds."""
    return ", ".join(f"{label} {_rupiah(values[key])}" for key, label in labelled if key in values)


def _left(remaining: int) -> str:
    """`sisa Rp650.000`, or `lewat Rp42.000` when more was spent than allocated."""
    return f"sisa {_rupiah(remaining)}" if remaining >= 0 else f"lewat {_rupiah(-remaining)}"


def _percent(value: float) -> str:
    """`78,3%` with the Indonesian decimal comma; tepira sends a whole percent as an integer."""
    return f"{value}%" if isinstance(value, int) else f"{_decimal_comma(value)}%"


def _wib_stamp(as_of: str) -> str:
    """`2026-10-16 09:12 WIB` for tepira's `2026-10-16T09:12:00+07:00`."""
    return f"{datetime.fromisoformat(as_of).astimezone(WIB):%Y-%m-%d %H:%M} WIB"


# --- songs on YouTube ------------------------------------------------------------------------


def _youtube(args: argparse.Namespace) -> int:
    """Open a YouTube video or playlist link as its canonical link, or else the search page for
    the words, in the default browser on `music.service`. The owner typed it, so it works with
    the music tool off too; exit 1 when nothing was opened."""
    service = load_config(args.config).music.service
    words = " ".join(args.words).strip()
    if not words:
        print(f"{FAIL} Tulis link YouTube atau judul lagunya.")
        return 1
    name = MUSIC_SERVICE_NAMES[service]
    link, opened = youtube_link(words, service=service), f"Dibuka di {name}"
    if link is None:
        link, opened = search_link(words, service=service), f"Pencarian dibuka di {name}"
    try:
        open_link(link, startfile=open_in_browser)
    except OSError as exc:
        print(f"{FAIL} Browser tidak bisa dibuka: Windows gagal membuka halamannya ({exc}).")
        return 1
    print(f"{OK} {opened}: {link}")
    return 0


# --- panduan setup ---------------------------------------------------------------------------


class _SetupStopped(Exception):
    """Master's input ended: setup stops where it is. `outcome` is what the step it stopped in
    had already done to config.yaml before a later question (None: nothing yet)."""

    def __init__(self, outcome: StepOutcome | None = None) -> None:
        super().__init__(outcome)
        self.outcome = outcome


def _setup(args: argparse.Namespace) -> int:
    """The first setup, step by step in the terminal; with `all`, `check` afterwards."""
    steps = SETUP_STEPS if args.step == "all" else (args.step,)
    outcomes: list[StepOutcome] = []
    stopped = False
    try:
        for number, step in enumerate(steps, start=1):
            if len(steps) > 1:
                print(f"\n== {number}/{len(steps)} {SETUP_TITLES[step]} ==")
            outcomes.append(_SETUP_HANDLERS[step](args))
    except _SetupStopped as stop:
        if stop.outcome is not None:
            outcomes.append(stop.outcome)
        print("Setup berhenti.")
        stopped = True
    running = _running_status(RUN_LOCK, RUNNING_FILE)[0]
    if "written" in outcomes and running:
        print(
            f"{WARN} 'waifu run' sedang berjalan: tutup lalu jalankan lagi supaya perubahan ini "
            "dipakai."
        )
    if stopped:
        return 1 if "failed" in outcomes else 0
    _setup_recap(args.config, dict(zip(steps, outcomes, strict=True)), running=running)
    if args.step == "all":
        print("\n== Pemeriksaan (waifu check) ==")
        return _check(args)
    return 1 if "failed" in outcomes else 0


def _setup_recap(config_path: Path, outcomes: dict[str, StepOutcome], *, running: bool) -> None:
    """What each step did and what is left to do, together at the end: the follow-ups said
    between the steps scroll away behind the later steps and `check`."""
    if len(outcomes) > 1:
        print("\nRingkasan setup:")
        for step, outcome in outcomes.items():
            print(f"  {SETUP_TITLES[step]}: {SETUP_OUTCOME_TEXT[outcome]}")
    try:
        config = load_config(config_path)
    except ConfigError:
        return  # `check` says why
    left = [
        line for step, outcome in outcomes.items() for line in _left_after(step, outcome, config)
    ]
    if "written" in outcomes.values() and not running:
        left.append("waifu run: jalankan supaya perubahan ini dipakai")
    if left:
        print("Langkah berikutnya:")
        print("".join(f"  - {line}\n" for line in left), end="")


def _left_after(step: str, outcome: StepOutcome, config: AppConfig) -> list[str]:
    """What is still to do for `step`, each line led by its command; nothing after a failed
    write, whose snippet to paste is the next step."""
    if outcome == "failed":
        return []
    if step == "mic":
        if outcome == "written":
            return ["waifu enroll: daftarkan ulang suaramu lewat mic yang baru"]
        if config.audio.input_device is None:
            return ["waifu setup mic: mic belum dipilih"]
        return []
    if step == "tuya":
        empty = [target.name for target in tuya_targets(config) if not target.filled]
        return [f"waifu setup tuya: {', '.join(empty)} belum diisi"] if empty else []
    if step == "ir":
        try:
            missing = [(device, device.missing_commands()) for device in _ir_devices(config)]
        except IrCodeStoreError:
            return []  # the step said the codes cannot be read
        return [
            f"waifu learn-ir {device.id} --missing: {len(commands)} tombol remote {device.name} "
            "belum dipelajari"
            for device, commands in missing
            if commands
        ]
    return [] if config.notify.enabled else ["waifu setup notify: notifikasi HP masih mati"]


def _ask_after(outcome: StepOutcome, ask: Callable[[], object]) -> StepOutcome:
    """Asks a question after a step wrote (or failed to write) config.yaml; when Master's input
    ends there, setup still learns what the step did."""
    try:
        ask()
    except _SetupStopped:
        raise _SetupStopped(outcome) from None
    return outcome


def _ask(question: str) -> str:
    """Master's answer, stripped; the end of his input stops setup."""
    try:
        return input(f"{question} ").strip()
    except EOFError:
        print()
        raise _SetupStopped from None


def _ask_yes(question: str, *, default: bool) -> bool:
    hint = "(Y/n)" if default else "(y/N)"
    while True:
        reply = _ask(f"{question} {hint}").lower()
        if not reply:
            return default
        if reply in YES_WORDS:
            return True
        if reply in NO_WORDS:
            return False
        print("Jawab y atau n.")


def _ask_numbers(question: str, count: int) -> list[int] | None:
    """Numbers 1 to `count` in the order typed (Enter = 1); None when Master skips."""
    while True:
        reply = _ask(question).lower()
        if reply == SKIP_WORD:
            return None
        if not reply:
            return [1]
        words = reply.replace(",", " ").split()
        numbers = [int(word) for word in words if word.isascii() and word.isdecimal()]
        unique = len(numbers) == len(words) == len(set(numbers))
        if unique and all(1 <= number <= count for number in numbers):
            return numbers
        print(f"Ketik nomor 1 sampai {count} (beberapa dipisah koma), atau {SKIP_WORD}.")


def _write_config(config_path: Path, changes: list[Change]) -> bool:
    """Writes `changes` into config.yaml through the patcher; on failure, the snippet to paste
    by hand, every local key hidden."""
    outcome = apply_changes(config_path, changes)
    if outcome.written:
        print(f"{OK} {config_path} diperbarui: {_changed_keys(changes)}.")
        print(f"  Isi sebelumnya tersimpan di {outcome.backup}.")
        return True
    print(f"{FAIL} {config_path} tidak diperbarui: {outcome.problem}.")
    if outcome.backup is not None:
        print(f"  Isi sebelumnya tersimpan di {outcome.backup}.")
    print(f"Ubah sendiri di {config_path}, hanya kunci yang disebut di sini:")
    print("".join(f"    {line}\n" for line in snippet(changes).splitlines()), end="")
    if any(change.secret for change in changes):
        print(
            'local_key yang disembunyikan: salin nilai "key" dari devices.json pada perangkat '
            'yang "id"-nya sama dengan device_id di atas.'
        )
    return False


def _changed_keys(changes: list[Change]) -> str:
    """The changed keys, grouped by their block: `devices[ac].ir.host`, or
    `devices[lampu_kamar].tuya (device_id, local_key)`."""
    blocks: dict[str, list[str]] = {}
    for change in changes:
        block, _, key = change.path.rpartition(".")
        blocks.setdefault(block, []).append(key)
    return ", ".join(
        f"{block}.{keys[0]}" if len(keys) == 1 else f"{block} ({', '.join(keys)})"
        for block, keys in blocks.items()
    )


def _setup_mic(args: argparse.Namespace) -> StepOutcome:
    """Measures each mic while Master speaks and writes the one, or the ones in order, he picks
    as audio.input_device."""
    config = load_config(args.config)
    import sounddevice

    devices = input_devices(sounddevice)
    if not devices:
        print(f"{FAIL} {NO_MIC_FOUND}")
        return "unchanged"
    picked, _ = _list_mics(sounddevice, devices, config)
    print(
        f"\nTiap mic didengarkan {MIC_CHECK_SECONDS:g} detik: sebut 'halo Cyrene' beberapa kali "
        "dengan suara biasa. Rekamannya tidak disimpan."
    )
    if not _ask_yes("Ukur sekarang?", default=True):
        print("Mikrofon dilewati.")
        return "unchanged"
    heard = _measure_mics(sounddevice, devices, MIC_CHECK_SECONDS, config.audio)
    print()
    if not heard:
        print(f"{FAIL} {NO_MIC_HEARD.format(command='waifu setup mic')}")
        return "unchanged"
    ranked = sorted(heard, key=lambda pair: _speech_margin(pair[1]), reverse=True)
    print("Mic yang mendengar suaramu, paling jelas dulu:")
    for number, (device, found) in enumerate(ranked, start=1):
        margin = _speech_margin(found)
        print(f"  {number}. {device.config_value} ({margin:+.0f} dB dari batas dengar)")
    numbers = _ask_numbers(
        "Pilih nomornya. Beberapa nomor (misalnya 1,2) = urutan cadangan kalau yang pertama "
        f"terputus. Enter = 1, {SKIP_WORD} = lewati:",
        len(ranked),
    )
    if numbers is None:
        print("Mikrofon dilewati.")
        return "unchanged"
    names = [ranked[number - 1][0].config_value for number in numbers]
    chosen: str | list[str | int] = names[0] if len(names) == 1 else list(names)
    if config.audio.input_device == chosen:
        print(f"{OK} audio.input_device sudah begitu; tidak ada yang diubah.")
        return "unchanged"
    if picked is not None:
        print(
            f"{WARN} Mic yang dipilih di HP ({picked}) tetap dicoba lebih dulu daripada "
            "audio.input_device; ganti di halaman Suara di HP kalau perlu."
        )
    if not _write_config(args.config, [Change(("audio", "input_device"), chosen)]):
        return "failed"
    print("Lalu daftarkan ulang suaramu lewat mic itu: waifu enroll")
    return "written"


def _run_tuya_wizard(command: Sequence[str], folder: Path) -> int:
    """Runs tinytuya's wizard in `folder` with Master's terminal attached, so he types his own
    cloud keys into it; its exit code. Ctrl+C reaches both, and raises KeyboardInterrupt."""
    return subprocess.run(list(command), cwd=folder, check=False).returncode


TUYA_WIZARD_RUNNER: Callable[[Sequence[str], Path], int] = _run_tuya_wizard


def _setup_tuya(args: argparse.Namespace) -> StepOutcome:
    """Runs tinytuya's wizard on Master's yes, then writes the devices he matches."""
    targets = tuya_targets(load_config(args.config))
    if not targets:
        print(
            "Tidak ada perangkat Tuya di config.yaml (tuya_light, tuya_plug, atau IR dengan "
            "backend: tuya); tambahkan dulu (README, bagian Lampu dan colokan Tuya)."
        )
        return "unchanged"
    print("Perangkat Tuya di config.yaml:")
    for target in targets:
        state = "sudah terisi" if target.filled else "belum diisi"
        print(f"  - {target.name} ({target.device_id}): {state}")
    folder = args.config.resolve().parent
    _explain_tuya_wizard(folder)
    if (folder / TUYA_DEVICE_FILE).exists():
        print(f"{TUYA_DEVICE_FILE} dari wizard sebelumnya sudah ada di {folder}.")
        run_wizard = _ask_yes(
            f"Jalankan wizard lagi? Tanpa itu, {TUYA_DEVICE_FILE} yang ada yang dibaca.",
            default=False,
        )
    else:
        run_wizard = _ask_yes("Jalankan wizard tinytuya sekarang?", default=True)
        if not run_wizard:
            print("Perangkat Tuya dilewati.")
            return "unchanged"
    try:
        if run_wizard and not _wizard_wrote_devices(folder):
            outcome: StepOutcome = "unchanged"
        else:
            outcome = _import_tuya(args.config, targets, folder)
        return _ask_after(outcome, lambda: _offer_tuya_cleanup(folder))
    except (_SetupStopped, KeyboardInterrupt):
        _say_tuya_credentials_left(folder)
        raise


def _wizard_wrote_devices(folder: Path) -> bool:
    """Runs tinytuya's wizard; whether it wrote devices.json. After a Tuya cloud error it prints
    the error, leaves devices.json as it was, and still exits 0, so only a devices.json written
    now says it got the list: an older one may hold a re-paired device's old local key."""
    devices = folder / TUYA_DEVICE_FILE
    before = _file_stamp(devices)
    try:
        code = TUYA_WIZARD_RUNNER([sys.executable, "-m", "tinytuya", "wizard"], folder)
    except KeyboardInterrupt:
        print(f"\n{WARN} Wizard dihentikan.")
        raise
    if code != 0:
        print(f"{WARN} Wizard berhenti dengan kode {code}.")
    if _file_stamp(devices) != before:
        return True
    print(
        f"{FAIL} Wizard tidak menulis {TUYA_DEVICE_FILE} baru, jadi tidak ada yang diimpor: "
        "daftar perangkat tidak didapat dari cloud Tuya. Lihat pesan wizard di atas:\n"
        '  - "Check API Key and Secret": API Key atau API Secret salah, atau layanan IoT Core '
        f'project-mu sudah habis ("subscription has expired"): perpanjang di {TUYA_IOT_URL}.\n'
        '  - "Check DeviceID and Region": Device ID salah, atau region-nya bukan data center '
        "project-mu.\n"
        '  - "Scan failed to detect a device": ketik Device ID dari tab Devices project-mu.\n'
        "Setelah diperbaiki, jalankan lagi 'waifu setup tuya' dan jawab y untuk menjalankan "
        "wizard."
    )
    return False


def _file_stamp(path: Path) -> tuple[int, int] | None:
    """When `path` was last written, and its size; None when it is not there."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _explain_tuya_wizard(folder: Path) -> None:
    print(
        "\nWizard tinytuya mengambil device_id dan local_key tiap perangkat dari cloud Tuya. "
        "Siapkan dulu di Tuya IoT Platform:\n"
        f"  1. Daftar atau login di {TUYA_IOT_URL}, lalu Cloud > Development > Create Cloud "
        "Project (Development Method: Smart Home; Data Center: sama dengan akun Smart Life-mu).\n"
        "  2. Di project itu: Devices > Link App Account > Add App Account, lalu pindai QR-nya "
        "dari aplikasi Smart Life (tab Saya/Me, ikon pindai). Kalau tab Devices tetap kosong "
        "setelah dipindai, data center project-nya salah: ubah data center project (atau buat "
        "project baru) sampai perangkatmu muncul.\n"
        "  3. Dari halaman Overview project, salin Access ID/Client ID (itulah API Key) dan "
        "Access Secret/Client Secret (itulah API Secret).\n"
        "  4. Layanan IoT Core project gratis tapi berlaku terbatas (awalnya 1 bulan); sesudah "
        f"habis, wizard tidak bisa mengambil daftar perangkat. Perpanjang di {TUYA_IOT_URL} "
        "(1, 3, atau 6 bulan, cukup mengisi formulir), juga sebelum menjalankan wizard lagi "
        "setelah pairing ulang.\n"
        f"  Panduan bergambar: {TUYA_GUIDE_URL}\n"
        "Wizard lalu menanyakan API Key, API Secret, satu Device ID (salin dari tab Devices "
        "project-mu, atau ketik scan untuk mencarinya di jaringan), dan region data center "
        "project-mu. Kamu mengetiknya langsung ke wizard; Cyrene tidak membacanya.\n"
        f"{WARN} Wizard menampilkan API Secret dan semua local key di layar: jangan merekam atau "
        "membagikan layar selama wizard berjalan.\n"
        f"Wizard menulis {', '.join(TUYA_WIZARD_FILES)} ke {folder} (semuanya ada di "
        ".gitignore).\n"
        'Di akhir, "Poll local devices? (Y/n)": Y memindai jaringan rumah untuk mencari IP dan '
        "versi tiap perangkat (diisi ke address dan version); n membiarkan address: Auto "
        "(dicari sekitar 6 detik saat perangkat pertama kali dipakai)."
    )


def _import_tuya(config_path: Path, targets: list[TuyaTarget], folder: Path) -> StepOutcome:
    """Asks which wizard device each Tuya device of config.yaml is, then writes them."""
    try:
        devices = read_cloud_devices(folder / TUYA_DEVICE_FILE, folder / TUYA_SNAPSHOT_FILE)
    except TuyaImportError as exc:
        print(f"{FAIL} {exc}; jalankan 'waifu setup tuya' lagi setelah wizard selesai.")
        return "unchanged"
    listed = [index for index, device in enumerate(devices) if not device.sub]
    if not listed:
        print(f"{FAIL} {TUYA_DEVICE_FILE} tidak berisi perangkat yang bisa dipakai.")
        return "unchanged"
    print(f"\nPerangkat di {TUYA_DEVICE_FILE}:")
    for number, index in enumerate(listed, start=1):
        print(f"  {number}. {_cloud_device_text(devices[index])}")
    if len(listed) < len(devices):
        print(
            f"  ({len(devices) - len(listed)} sub-perangkat, misalnya remote di bawah IR blaster, "
            "tidak dicantumkan)"
        )
    suggested = suggest(targets, devices)
    taken: dict[int, str] = {}  # a light's or plug's entry -> the device it went to
    changes: list[Change] = []
    summary: list[str] = []
    for target in targets:
        index = _pick_cloud_device(target, devices, listed, suggested[target.device_id], taken)
        if index is None:
            print(f"  {target.name} dilewati.")
            continue
        device = devices[index]
        if target.filled and target.current.device_id != device.id:
            replace = _ask_yes(
                f"  {target.name} sudah terisi dengan perangkat lain. Ganti?", default=False
            )
            if not replace:
                print(f"  {target.name} tetap.")
                continue
        if target.kind != "ir":
            taken[index] = target.name
        if not (target_changes := tuya_changes(target, device)):
            print(f"  {OK} {target.name} sudah sama dengan {TUYA_DEVICE_FILE}.")
            continue
        changes.extend(target_changes)
        summary.append(_tuya_summary(target, device))
    if not changes:
        print("Tidak ada yang diubah.")
        return "unchanged"
    if not _write_config(config_path, changes):
        return "failed"
    print("\n".join(summary))
    return "written"


def _cloud_device_text(device: CloudDevice) -> str:
    kind = TUYA_KIND_NAMES.get(CATEGORY_KINDS.get(device.category, ""), "lainnya")
    where = device.ip or "IP belum diketahui"
    version = f", versi {device.version:g}" if device.version is not None else ""
    return f"{device.name or '(tanpa nama)'} ({kind}, {where}{version})"


def _pick_cloud_device(
    target: TuyaTarget,
    devices: list[CloudDevice],
    listed: list[int],
    suggested: int | None,
    taken: dict[int, str],
) -> int | None:
    """The index in `devices` Master picks for `target`, or None when he skips it. A light's or
    plug's entry another device took is never offered or taken."""
    label = f"{target.name} ({target.device_id})"
    if suggested is not None and suggested not in taken:
        question = (
            f"{label}: pakai {listed.index(suggested) + 1}. {devices[suggested].name}? Enter = "
            f"ya, nomor lain = pilih, {SKIP_WORD} = lewati:"
        )
    else:
        suggested = None
        question = f"{label}: nomor perangkatnya? Enter atau {SKIP_WORD} = lewati:"
    while True:
        reply = _ask(question).lower()
        if reply == SKIP_WORD or (not reply and suggested is None):
            return None
        if not reply:
            return suggested
        if reply.isascii() and reply.isdecimal() and 1 <= int(reply) <= len(listed):
            index = listed[int(reply) - 1]
            if index not in taken:
                return index
            print(f"Nomor {reply} sudah dipakai untuk {taken[index]}.")
            continue
        print(f"Ketik nomor 1 sampai {len(listed)}, atau {SKIP_WORD}.")


def _tuya_summary(target: TuyaTarget, device: CloudDevice) -> str:
    parts = [f"device_id {device.id}", f"local_key ({masked(device.key)})"]
    parts.append(f"address {device.ip}" if device.ip is not None else "address tetap")
    if device.version is not None:
        parts.append(f"version {device.version:g}")
    return f"{OK} {target.name}: {', '.join(parts)}"


def _tuya_credential_files(folder: Path) -> list[Path]:
    return [folder / name for name in TUYA_CREDENTIAL_FILES if (folder / name).exists()]


def _say_tuya_credentials_left(folder: Path) -> None:
    """Setup stopped before offering to delete them: says the credential files stay."""
    if present := _tuya_credential_files(folder):
        names = " dan ".join(path.name for path in present)
        print(
            f"{WARN} {names} tetap ada di {folder}: berisi API Key dan API Secret akun Tuya-mu. "
            "Hapus sendiri kalau tidak perlu, dan jangan dibagikan."
        )


def _offer_tuya_cleanup(folder: Path) -> None:
    """Offers to delete the wizard's files that hold the Tuya cloud credentials."""
    present = _tuya_credential_files(folder)
    if present:
        names = " dan ".join(path.name for path in present)
        print(
            f"\n{names} berisi API Key dan API Secret akun Tuya-mu (tuya-raw.json juga semua "
            "local key); Cyrene tidak membutuhkannya."
        )
        if _ask_yes(f"Hapus {names} sekarang?", default=True):
            try:
                for path in present:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"{FAIL} Tidak bisa menghapus {names}: {exc.strerror or exc}")
            else:
                print(
                    f"{OK} {names} dihapus. Wizard berikutnya menanyakan API Key dan API Secret "
                    "lagi."
                )
        else:
            print(f"{WARN} {names} tetap ada di {folder}; jangan dibagikan.")
    if (folder / TUYA_DEVICE_FILE).exists():
        print(
            f"{TUYA_DEVICE_FILE} dan {TUYA_SNAPSHOT_FILE} masih berisi local key (dibaca lagi "
            "kalau wizard dijalankan ulang); keduanya ada di .gitignore, jangan dibagikan."
        )


def _setup_ir(args: argparse.Namespace) -> StepOutcome:
    """Asks each Broadlink blaster's address and writes it to every device on it, then says
    which remote buttons are still to be learned."""
    config = load_config(args.config)
    ir_devices = [device for device in config.devices if device.ir is not None]
    if not ir_devices:
        print(
            "Tidak ada perangkat IR di config.yaml; tambahkan dulu (README, bagian IR blaster "
            "dan remote)."
        )
        return "unchanged"
    on_tuya = [device.name for device in ir_devices if device.ir.backend == "tuya"]
    if on_tuya:
        print(f"IR lewat Tuya ({', '.join(on_tuya)}): datanya diisi lewat 'waifu setup tuya'.")
    blasters: dict[str, list[DeviceConfig]] = {}
    for device in ir_devices:
        if device.ir.backend == "broadlink" and device.ir.host:
            blasters.setdefault(device.ir.host, []).append(device)
    if blasters:
        print(
            "Cari IP blaster Broadlink di aplikasi Broadlink (info perangkat) atau di daftar "
            "perangkat router, dan beri IP tetap (reservasi DHCP) supaya tidak berubah."
        )
    changes: list[Change] = []
    for host, devices in blasters.items():
        example = (
            " (alamat contoh dari config.example.yaml)" if host == EXAMPLE_BLASTER_HOST else ""
        )
        print(f"Blaster Broadlink untuk {', '.join(d.name for d in devices)}: {host}{example}.")
        address = _ask_ipv4(f"Alamat IP blaster? Enter = tetap {host}:")
        if address is not None and address != host:
            changes.extend(Change(("devices", d.id, "ir", "host"), address) for d in devices)
    outcome: StepOutcome = "unchanged"
    if changes:
        if not _write_config(args.config, changes):
            return "failed"
        outcome = "written"
        config = load_config(args.config)
    _learn_ir_hints(config)
    return outcome


def _ask_ipv4(question: str) -> str | None:
    """An IPv4 address Master types; None for Enter (keep what is there)."""
    while True:
        reply = _ask(question)
        if not reply:
            return None
        try:
            return str(ipaddress.IPv4Address(reply))
        except ValueError:
            print(f"'{reply}' bukan alamat IPv4 (contoh 192.168.1.23).")


def _learn_ir_hints(config: AppConfig) -> None:
    """Which IR devices still have remote buttons to learn, with the command that learns them.
    Building the registry never connects."""
    devices = _ir_devices(config)
    if devices:
        print("Lalu ajarkan tombol remote-nya (arahkan remote ke blaster):")
    for device in devices:
        try:
            missing = device.missing_commands()
            learned = device.commands()
        except IrCodeStoreError as exc:
            print(f"{FAIL} Kode IR tidak bisa dibaca: {exc}")
            return
        if missing:
            print(
                f"  Ajarkan tombol remote {device.name} ({len(missing)} belum): "
                f"waifu learn-ir {device.id} --missing"
            )
        elif learned:
            print(f"  {OK} {device.name}: semua tombol sudah dipelajari.")
        else:
            print(f"  {device.name}: belum ada tombol; waifu learn-ir {device.id} <perintah>")


def _ir_devices(config: AppConfig) -> list[IrDevice]:
    return [device for device in build_registry(config).all() if isinstance(device, IrDevice)]


def _setup_notify(args: argparse.Namespace) -> StepOutcome:
    """Turns notify on with a new random ntfy topic (or the one there), shows how to subscribe
    to it, and sends a test push on Master's yes."""
    config = load_config(args.config)
    notify = config.notify
    current = notify.topic.get_secret_value() if notify.topic is not None else None
    topic = current
    if notify.enabled and current is not None:
        print(f"Notifikasi HP sudah menyala lewat {notify.server} (topik {current[:4]}…).")
        if _ask_yes("Buat topik baru (HP perlu berlangganan topik baru itu)?", default=False):
            topic = _new_ntfy_topic()
    elif current is not None:
        print(f"Ada topik di notify.topic ({current[:4]}…), tapi notifikasi HP masih mati.")
        if not _ask_yes("Nyalakan notifikasi HP?", default=True):
            print("Notifikasi HP dilewati.")
            return "unchanged"
        if _ask_yes("Buat topik baru (HP perlu berlangganan topik baru itu)?", default=False):
            topic = _new_ntfy_topic()
    else:
        print(
            "Notifikasi HP lewat ntfy (aplikasi gratis, tanpa akun): pengingat dan laporan "
            f"{config.assistant.name} juga muncul di HP."
        )
        if not _ask_yes("Nyalakan notifikasi HP?", default=True):
            print("Notifikasi HP dilewati.")
            return "unchanged"
        topic = _new_ntfy_topic()
    assert topic is not None
    changes = [] if notify.enabled else [Change(("notify", "enabled"), True)]
    if topic != current:
        changes.append(Change(("notify", "topic"), topic))
    outcome: StepOutcome = "unchanged"
    if changes:  # a topic kept while notify was off may never have been subscribed to
        print(
            "Di HP:\n"
            "  1. Pasang aplikasi ntfy (Play Store atau App Store; gratis, tanpa akun).\n"
            f"  2. Ketuk +, isi topik ini, server bawaan ({notify.server}):\n"
            f"       {topic}\n"
            "  3. Ketuk Subscribe.\n"
            "Topik ini rahasia: siapa pun yang tahu namanya bisa membaca notifikasinya."
        )
        outcome = "written" if _write_config(args.config, changes) else "failed"
    turned_on = NotifyConfig(enabled=True, server=notify.server, topic=SecretStr(topic))
    return _ask_after(outcome, lambda: _offer_test_push(config.assistant.name, turned_on))


def _new_ntfy_topic() -> str:
    """A topic nobody can guess: anyone who knows it can read the notifications."""
    return "cyrene-" + secrets.token_urlsafe(12)


def _offer_test_push(name: str, notify: NotifyConfig) -> None:
    if not _ask_yes("Kirim notifikasi tes sekarang?", default=False):
        return
    notifier = build_notifier(notify)
    if notifier is not None and notifier.send(
        name, NOTIFY_TEST_MESSAGE.format(name=name), tags=[NOTIFY_TAG]
    ):
        print(
            f"{OK} Notifikasi tes terkirim. Kalau tidak muncul di HP, cek langganan topiknya di "
            "aplikasi ntfy."
        )
        return
    print(
        f"{FAIL} Notifikasi tes tidak terkirim ke {notify.server}. Cek koneksi internet; "
        "detailnya ada di log."
    )


_SETUP_HANDLERS: dict[str, Callable[[argparse.Namespace], StepOutcome]] = {
    "mic": _setup_mic,
    "tuya": _setup_tuya,
    "ir": _setup_ir,
    "notify": _setup_notify,
}


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
    disk = read_code_version(run=subprocess.run)  # with the commit, for a small change
    yield _running_version_check(disk, *_running_status(RUN_LOCK, RUNNING_FILE))
    yield _version_notice_check(config.version_notice)
    yield from _device_checks(config, config_path)
    yield from _routine_checks(config)
    yield _quick_page_check(config)
    yield _voiceprint_check(config)
    yield _brain_check(config)
    yield _vision_check(config)
    yield _web_search_check(config)
    yield _memory_check(config)
    yield _schedule_check(config)
    yield _alarm_check(config)
    yield _todo_check(config)
    yield _journal_check(config)
    yield _japanese_check(config)
    yield _music_check(config)
    yield _briefing_check(config.briefing)
    yield _rain_alert_check(config)
    yield _usage_check(config)
    yield _pc_health_check(config)
    yield _internet_watch_check(config)
    yield _play_time_check(config)
    yield _app_reminders_check(config)
    yield _focus_check(config)
    yield _game_mode_check(config)
    yield _guest_mode_check(config)
    yield _voice_volume_check(config)
    yield _breaks_check(config)
    yield _prayer_check(config)
    yield _free_offers_check(config)
    yield from _voice_package_checks(config)
    yield _microphone_check(config)
    yield from _hotkeys_check(config)
    yield from _fallback_voice_checks(config)
    yield from _anime_voice_checks(config)
    yield from _avatar_checks(config)
    yield from _gacha_checks(config)
    yield _redeem_codes_check(config)
    yield _weekly_report_check(config)
    yield _tepira_check(config)
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
    yield from _setup_hint_checks(config)
    for device_config in config.devices:
        for slug, target in (device_config.apps or {}).items():
            yield _app_check(slug, target)


def _setup_hint_checks(config: AppConfig) -> Iterator[Check]:
    """The example's placeholders still in config.yaml, each with the setup step that fills it:
    a Tuya device (or Tuya IR blaster) without the wizard's values, and the example's Broadlink
    address, which may really be the blaster's."""
    for target in tuya_targets(config):
        if not target.filled:
            yield (
                WARN,
                (
                    f"{target.name}: device_id atau local_key masih contoh ({TUYA_PLACEHOLDER}). "
                    "Jalankan: waifu setup tuya"
                ),
            )
    on_example = [
        device.name
        for device in config.devices
        if device.ir is not None
        and device.ir.backend == "broadlink"
        and device.ir.host == EXAMPLE_BLASTER_HOST
    ]
    if on_example:
        yield (
            WARN,
            (
                f"IR blaster {EXAMPLE_BLASTER_HOST} ({', '.join(on_example)}): alamat contoh dari "
                "config.example.yaml; kalau memang alamat blastermu, abaikan. Kalau bukan, "
                "jalankan: waifu setup ir"
            ),
        )


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


def _quick_page_check(config: AppConfig) -> Check:
    """How many buttons `waifu tombol` shows, counted on the real devices (nothing connects)."""
    actions = QuickActions(
        build_registry(config), config.routines, chosen=config.phone.quick_actions
    )
    count = sum(len(group.actions) for group in actions.groups())
    if count == 0:
        return WARN, f"Tombol cepat di PC: belum ada tombol ({QUICK_PAGE_HINT})"
    return OK, f"Tombol cepat di PC: {count} tombol, buka dengan 'waifu tombol'"


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


def _web_search_check(config: AppConfig) -> Check:
    """Whether she can search the web: Claude's server tool, so only with Claude. `max_uses`
    caps each request, and one question may take a request per tool round and one more."""
    if not config.web_search.enabled:
        return WARN, "Pencarian web mati"
    if config.brain.backend != "claude":
        return WARN, "Pencarian web: tidak tersedia dengan Ollama"
    searches = config.web_search.max_uses
    per_question = searches * (config.brain.max_tool_rounds + 1)
    return OK, (
        f"Pencarian web: aktif (Claude, paling banyak {searches} pencarian per permintaan, "
        f"{per_question} per pertanyaan)"
    )


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


def _alarm_check(config: AppConfig) -> Check:
    """How many alarms the schedule holds, how a ring repeats and how long, whether its first
    ring goes to the phone, that it rings only while `waifu run` runs on an awake PC, until when
    one rings now, and where a broken ring file was moved, if one was."""
    if not config.alarm.enabled:
        return WARN, "Alarm mati"
    if not config.schedule.enabled:
        return WARN, "Alarm mati (schedule.enabled: false)"
    settings = config.alarm
    path = settings.state_path
    schedule = _schedule_store(config)
    try:
        ringing = _alarm_bell(config, schedule, dry_run=False).ring_state()  # as `run` reads it
    except OSError as exc:
        return FAIL, f"File alarm {path} tidak bisa dibaca: {exc}"
    try:
        alarms = sum(isinstance(entry.action, AlarmAction) for entry in schedule.entries())
    except OSError:  # the schedule line above already fails
        status = "Alarm: "
    else:
        status = f"Alarm: {alarms} alarm, "
    status += f"bunyi tiap {settings.repeat_seconds} detik sampai {settings.max_minutes} menit"
    if settings.push and config.notify.enabled:
        status += ", juga ke HP"
    status += "; hanya saat 'waifu run' jalan dan PC menyala"
    if ringing is not None:
        status += f"; sedang berbunyi sampai {ringing.until:%H:%M}"
    moved = _set_aside_files(path)
    if moved:
        return WARN, f"{status}; file alarm yang rusak dipindah ke {', '.join(map(str, moved))}"
    return OK, status


def _todo_check(config: AppConfig) -> Check:
    """How many tasks are open, and where a broken to-do file was moved, if one was."""
    if not config.todo.enabled:
        return WARN, "Daftar tugas mati"
    path = config.todo.path
    try:
        count = len(_todo_store(config).open_tasks())  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File daftar tugas {path} tidak bisa dibaca: {exc}"
    status = f"Daftar tugas: {count} terbuka"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file daftar tugas yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(cara memulihkannya ada di README, bagian Daftar tugas)"
        )
    return OK, status


def _journal_check(config: AppConfig) -> Check:
    """How many days the journal holds, when she asks about Master's day, and where a broken
    journal was moved, if one was."""
    journal = config.journal
    if not journal.enabled:
        return WARN, "Jurnal mati"
    try:
        days = len(_journal_store(config).notes())  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File jurnal {journal.path} tidak bisa dibaca: {exc}"
    question = (
        "pertanyaan malam mati"
        if journal.ask_at is None
        else f"pertanyaan malam jam {journal.ask_at:%H:%M}"
    )
    status = f"Jurnal: {days} hari tercatat, {question}"
    moved = _set_aside_files(journal.path)
    if moved:
        return WARN, (
            f"{status}; file jurnal yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Jurnal harian)"
        )
    return OK, status


def _japanese_check(config: AppConfig) -> Check:
    """How many words of the built-in list Master learned and mastered, and where a broken
    lesson file was moved, if one was."""
    japanese = config.japanese
    if not japanese.enabled:
        return WARN, "Belajar bahasa Jepang mati"
    try:
        progress = _japanese_store(config).progress()  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File belajar bahasa Jepang {japanese.path} tidak bisa dibaca: {exc}"
    counts = lesson_counts(load_words(), progress, wib_now().date())
    status = (
        f"Belajar bahasa Jepang: {counts.learned} dari {counts.total} kata dipelajari, "
        f"{counts.mastered} dikuasai"
    )
    moved = _set_aside_files(japanese.path)
    if moved:
        return WARN, (
            f"{status}; file belajar bahasa Jepang yang rusak dipindah ke "
            f"{', '.join(map(str, moved))} (lihat README, bagian Belajar bahasa Jepang)"
        )
    return OK, status


def _music_check(config: AppConfig) -> Check:
    """Whether she opens songs on YouTube when asked, and on which service; nothing is opened."""
    if not config.music.enabled:
        return WARN, "Lagu YouTube mati"
    return OK, f"Lagu YouTube: aktif ({MUSIC_SERVICE_NAMES[config.music.service]})"


def _briefing_check(briefing: BriefingConfig) -> Check:
    """When she greets by herself and where the weather is for (coordinates win over the city,
    which still names the place); Open-Meteo is never asked."""
    if not briefing.enabled:
        return WARN, "Sapaan pagi mati"
    place = _weather_place(briefing)
    if place is None:
        return WARN, "Sapaan pagi: cuaca belum diatur (isi briefing.city)"
    hours = f"{briefing.time:%H:%M}-{briefing.until:%H:%M}"
    return OK, f"Sapaan pagi: jam {hours}, cuaca untuk {place}"


def _rain_alert_check(config: AppConfig) -> Check:
    """How long ahead, from what chance, in which hours, and where she warns of rain; Open-Meteo
    is never asked."""
    rain = config.rain_alert
    if not rain.enabled:
        return WARN, "Peringatan hujan mati"
    place = _weather_place(config.briefing)
    if place is None:
        return WARN, "Peringatan hujan: cuaca belum diatur (isi briefing.city)"
    hours = f"{rain.active_from:%H:%M}-{rain.active_until:%H:%M}"
    return OK, (
        f"Peringatan hujan: {rain.lead_minutes} menit sebelumnya, peluang {rain.threshold}% "
        f"ke atas, jam {hours}, untuk {place}"
    )


def _weather_place(briefing: BriefingConfig) -> str | None:
    """Where the weather is for, as the check lines write it: `<city>`, `<lat>, <lon>`, or
    `<city> (<lat>, <lon>)` (the coordinates win; the city still names the place); None when
    `briefing` names no place."""
    if briefing.latitude is not None:
        coordinates = f"{briefing.latitude}, {briefing.longitude}"
        return coordinates if briefing.city is None else f"{briefing.city} ({coordinates})"
    return briefing.city


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


def _pc_health_check(config: AppConfig) -> Check:
    """Whether she reads the PC, and the GPU nvidia-smi names; it is asked only while on."""
    if not config.pc_health.enabled:
        return WARN, "Kesehatan PC mati"
    gpu = gpu_status()
    if gpu is None:
        return WARN, "Kesehatan PC: aktif, GPU tidak terbaca"
    return OK, f"Kesehatan PC: aktif, GPU {gpu.name}"


def _internet_watch_check(config: AppConfig) -> Check:
    """Whether she watches the internet and how often, and where a broken history was moved, if
    one was; nothing is probed."""
    network = config.network
    if not network.enabled:
        return WARN, INTERNET_OFF
    try:
        _network_store(config).record()  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File riwayat internet {network.path} tidak bisa dibaca: {exc}"
    status = f"Pemantau internet: aktif, tiap {network.poll_seconds} detik"
    moved = _set_aside_files(network.path)
    if moved:
        return WARN, (
            f"{status}; file riwayat internet yang rusak dipindah ke "
            f"{', '.join(map(str, moved))} (lihat README, bagian Pemantau internet)"
        )
    return OK, status


def _play_time_check(config: AppConfig) -> Check:
    """Which games she counts and the bedtime nudge, and where a broken play time file was moved,
    if one was; no process is listed."""
    play = config.play_time
    if not play.enabled:
        return WARN, "Jam main mati"
    try:
        _play_time_store(config).record()  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File jam main {play.path} tidak bisa dibaca: {exc}"
    status = f"Jam main: aktif ({', '.join(GAMES[game].short for game in play.games)})"
    if play.bedtime is not None:
        status += f", pengingat tidur {play.bedtime:%H:%M}"
    moved = _set_aside_files(play.path)
    if moved:
        return WARN, (
            f"{status}; file jam main yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Jam main)"
        )
    return OK, status


def _app_reminders_check(config: AppConfig) -> Check:
    """How many game start reminders are kept, whether she says them, and where a broken file
    was moved, if one was."""
    path = config.app_reminders.path
    if not config.app_reminders.enabled:
        return WARN, APP_REMINDERS_OFF
    if not config.play_time.enabled:
        return WARN, APP_REMINDERS_WITHOUT_PLAY_TIME
    try:
        count = len(_app_reminder_store(config).reminders())  # moves a broken file aside
    except OSError as exc:
        return FAIL, f"File pengingat game {path} tidak bisa dibaca: {exc}"
    status = f"Pengingat saat game dibuka: {count} tersimpan"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file pengingat game yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Pengingat saat game dibuka)"
        )
    return OK, status


def _focus_check(config: AppConfig) -> Check:
    """Whether she keeps Master company while they focus, whether her focus lines go to the phone
    too, and where a broken focus log was moved, if one was."""
    focus = config.focus
    if not focus.enabled:
        return WARN, "Teman fokus mati"
    try:
        _focus_store(config).blocks()  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File fokus {focus.path} tidak bisa dibaca: {exc}"
    status = "Teman fokus: aktif"
    if focus.push and config.notify.enabled:
        status += ", juga ke HP"
    moved = _set_aside_files(focus.path)
    if moved:
        return WARN, (
            f"{status}; file fokus yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Teman fokus)"
        )
    return OK, status


# What each game mode does with her own announcements while a game runs.
GAME_MODE_TEXT = {
    "normal": "pengumuman tetap diucapkan saat main",
    "hold": "pengumuman ditahan selama main",
    "phone": "pengumuman ke HP selama main",
}


def _game_mode_check(config: AppConfig) -> Check:
    """What game mode does with her announcements while a game runs and which routines it runs;
    `phone` without notifications holds them instead, which the owner may not expect."""
    game = config.game_mode
    if game == GameModeConfig():
        return WARN, "Mode game mati"
    routines = [
        f"{name} saat game {when}"
        for name, when in ((game.on_start, "dibuka"), (game.on_stop, "ditutup"))
        if name is not None
    ]
    routine_text = f"; rutinitas {', '.join(routines)}" if routines else ""
    if game.announcements == "phone" and not config.notify.enabled:
        return WARN, (
            "Mode game: ke HP, tapi notify mati: pengumuman ditahan sampai game ditutup"
            + routine_text
        )
    return OK, f"Mode game: {GAME_MODE_TEXT[game.announcements]}{routine_text}"


def _guest_mode_check(config: AppConfig) -> Check:
    """How guest mode is switched and when it ends by itself; a warning when speaker
    verification is off, since any voice may then end it."""
    text = (
        'Mode tamu: "mode tamu" dan "mode tamu selesai" lewat suara, chat, atau HP; berakhir '
        f"sendiri setelah {config.guest_mode.max_hours} jam atau jam 05:00"
    )
    if not config.speaker.enabled:
        return WARN, f"{text}; verifikasi suara mati, jadi suara tamu juga bisa mengakhirinya"
    return OK, text


def _voice_volume_check(config: AppConfig) -> Check:
    """Her own voice level, whether whisper mode is on and its window, the level she is heard
    at now when whisper mode holds her back, and where a broken file was moved, if one was."""
    voice_level = _voice_level(config)
    path = config.voice.state_path
    try:
        state = voice_level.state()  # moves a broken file aside, as `run` would
    except OSError as exc:
        return FAIL, f"File volume suara {path} tidak bisa dibaca: {exc}"
    night = config.voice.night
    status = (
        f"Volume suaraku: {state.level}%, mode bisik {'aktif' if state.night else 'mati'} "
        f"({night.from_:%H:%M}-{night.until:%H:%M} paling keras {night.level}%)"
    )
    heard = voice_level.level_for(state)
    if heard != state.level:
        status += f", sekarang {heard}%"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file volume yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Volume suaraku)"
        )
    return OK, status


def _breaks_check(config: AppConfig) -> Check:
    """How often she suggests a break, which pause counts as one, her hours, whether she says it
    while a game holds her announcements, and whether it goes to the phone too; from the config
    alone, never reading the keyboard or mouse."""
    breaks = config.breaks
    if not breaks.enabled:
        return WARN, "Pengingat istirahat mati"
    status = (
        f"Pengingat istirahat: tiap {breaks.every_minutes} menit di depan layar, jeda "
        f"{breaks.idle_reset_minutes} menit dihitung istirahat"
    )
    if breaks.active_from is not None and breaks.active_until is not None:
        status += f", jam {breaks.active_from:%H:%M}-{breaks.active_until:%H:%M}"
    if breaks.during_games:
        status += ", juga saat main"
    if breaks.push and config.notify.enabled:
        status += ", juga ke HP"
    return OK, status


def _prayer_check(config: AppConfig) -> Check:
    """For which place the prayer times are, the ihtiyath, which prayers she reminds of and how
    early, whether they go to the phone too, and where a broken state file was moved, if one
    was; from the config alone, never looking the place up."""
    prayer = config.prayer
    if not prayer.enabled:
        return WARN, "Waktu sholat mati"
    if prayer.latitude is not None:
        place: str | None = f"{prayer.latitude}, {prayer.longitude}"
        if config.briefing.city is not None:
            place = f"{config.briefing.city} ({place})"
    else:
        place = _weather_place(config.briefing)
    if place is None:
        return WARN, (
            "Waktu sholat: lokasi belum diatur (isi briefing.city, atau prayer.latitude dan "
            "prayer.longitude)"
        )
    status = f"Waktu sholat: untuk {place}, ihtiyath {prayer.ihtiyath_minutes} menit"
    if not prayer.remind:
        status += ", tanpa pengingat"
    else:
        status += f", pengingat {', '.join(name for name in PRAYERS if name in prayer.remind)}"
        if prayer.minutes_before:
            status += f" {prayer.minutes_before} menit sebelumnya"
        if prayer.push and config.notify.enabled:
            status += ", juga ke HP"
    moved = _set_aside_files(prayer.state_path)
    if moved:
        return WARN, (
            f"{status}; file pengingat sholat yang rusak dipindah ke "
            f"{', '.join(map(str, moved))} (lihat README, bagian Waktu sholat)"
        )
    return OK, status


def _free_offers_check(config: AppConfig) -> Check:
    """Which stores she reads the free games of, when the AI offers were last searched for,
    and where a broken offers file was moved, if one was; nothing is asked of the internet."""
    offers = config.free_offers
    if not offers.enabled:
        return WARN, "Promo gratis mati"
    path = offers.state_path
    try:
        last_scan = _offers_store(config).last_ai_scan()  # moves a broken file aside, as run would
    except OSError as exc:
        return FAIL, f"File promo gratis {path} tidak bisa dibaca: {exc}"
    platforms = ", ".join(GAME_PLATFORM_NAMES[platform] for platform in offers.game_platforms)
    parts = [f"game {platforms}" if offers.games else "game mati"]
    if not offers.ai:
        parts.append("promo AI mati")
    elif last_scan is None:
        parts.append("promo AI belum pernah dicari")
    else:
        parts.append(f"promo AI terakhir dicari {_schedule_time(last_scan)}")
    if not offers.notices:
        # The watcher both tells and scans: without it only 'waifu offers ai --scan' searches.
        parts.append(
            "tanpa pemberitahuan dan tanpa pencarian promo AI mingguan"
            if offers.ai
            else "tanpa pemberitahuan"
        )
    status = f"Promo gratis: {'; '.join(parts)}"
    moved = _set_aside_files(path)
    if moved:
        return WARN, (
            f"{status}; file promo yang rusak dipindah ke {', '.join(map(str, moved))} "
            "(lihat README, bagian Pencarian web dan promo gratis)"
        )
    return OK, status


def _set_aside_files(path: Path) -> list[Path]:
    """The copies a broken memory, schedule, to-do, journal, Japanese lesson, usage, offers, play
    time, game start reminder, focus, or voice volume file was renamed to
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
    """The input Cyrene opens first (the mic picked on the phone, then `audio.input_device`'s
    list in order, then the Windows default), whether it is the Windows default, and the voice
    settings changed on the phone; nothing is recorded."""
    override = _voice_override(config)
    picked = [] if override.input_device is None else [override.input_device]
    listed = config.audio.input_preferences
    try:
        import sounddevice

        default = windows_default_input(sounddevice)
    except Exception as exc:  # missing package or PortAudio
        return FAIL, f"Mikrofon tidak ditemukan: {exc}"
    problem: Exception | None = None
    for value in [*picked, *listed, None]:
        try:
            name = sounddevice.query_devices(value, "input")["name"]
        except Exception as exc:  # no input device, a device name that matches none
            problem = problem or exc
            continue
        mark, line = _microphone_in_use(config, value, name, picked, listed, default)
        return mark, line + _phone_voice_settings(override)
    return FAIL, f"Mikrofon tidak ditemukan: {problem}"


def _hotkeys_check(config: AppConfig) -> Iterator[Check]:
    """The global hotkeys switched on and what each does, or those the running `waifu run` holds
    (none: a warning), each one another program holds, and a config changed since that run
    started, from what that run recorded (trusted only while the run lock is held and the process
    that recorded it is alive); and a listen pause under way. No Win32 call: registering here
    would take the combos from the running `waifu run`."""
    settings = config.hotkeys
    if sys.platform != "win32":
        yield WARN, "Hotkey global hanya ada di Windows"
        return
    if not settings.enabled:
        yield WARN, "Hotkey global mati (hotkeys.enabled: false)"
        return
    if not settings.bindings:
        yield WARN, "Hotkey global mati (semua tombolnya null)"
        return
    configured = [Hotkey(action, key) for action, key in settings.bindings.items()]
    status = _running_hotkeys()
    recorded = () if status is None else status.registrations
    held = [registration.hotkey for registration in recorded if registration.registered]
    if status is None:
        mark, line = (
            OK,
            f"Hotkey global: {_hotkey_combos(configured)}; dicoba saat 'waifu run' jalan",
        )
    elif held:
        mark, line = OK, f"Hotkey global: {_hotkey_combos(held)}; aktif di 'waifu run'"
    elif recorded:  # each refusal follows
        mark, line = WARN, "Hotkey global tidak aktif di 'waifu run'"
    else:  # its thread could not start, or lost them
        mark, line = (
            WARN,
            "Hotkey global tidak aktif di 'waifu run' (sebabnya ada di log); jalankan ulang "
            "'waifu run'",
        )
    pause = _listen_pause(config)
    until = None if pause is None else pause.until()  # an ended pause's file goes, as in `run`
    if until is not None:
        line += f"; sedang tidak mendengarkan sampai {until:%H:%M}"
    yield mark, line
    for registration in recorded:
        if registration.registered:
            continue
        hotkey = registration.hotkey
        if registration.error == ERROR_HOTKEY_ALREADY_REGISTERED:
            why = "sudah dipakai program lain"
        else:
            why = f"tidak bisa didaftarkan (Windows error {registration.error})"
        named = f"Hotkey {hotkey.combo} ({HOTKEY_ACTION_TEXT[hotkey.action]})"
        yield WARN, f"{named} {why}; ganti hotkeys.{hotkey.action} di config.yaml"
    if recorded and {registration.hotkey for registration in recorded} != set(configured):
        yield (
            WARN,
            f"Hotkey global di config.yaml ({_hotkey_combos(configured)}) baru dipakai setelah "
            "'waifu run' dijalankan ulang",
        )


def _hotkey_combos(hotkeys: Sequence[Hotkey]) -> str:
    """The combos of `hotkeys` and what each does: "Ctrl+Alt+C bicara, Ctrl+Alt+X potong"."""
    return ", ".join(f"{hotkey.combo} {HOTKEY_ACTION_TEXT[hotkey.action]}" for hotkey in hotkeys)


def _running_hotkeys() -> HotkeyStatus | None:
    """What the running `waifu run` recorded of its global hotkeys; None while none runs, or
    when the record is another process's (one that ended without removing it)."""
    if not _instance_running(RUN_LOCK):
        return None
    status = read_status(HOTKEYS_FILE)
    if status is None or not process_alive(status.pid):
        return None
    return status


def _microphone_in_use(
    config: AppConfig,
    value: int | str | None,
    name: str,
    picked: list[str],
    listed: list[int | str],
    default: str | None,
) -> Check:
    """The mic line for `name`, opened as `value`, one of `picked` (the phone's) or `listed`
    (`audio.input_device`'s), or the Windows default (None)."""
    if value is None:
        if listed:
            return WARN, (
                f"Mikrofon: {name}, default Windows, karena mic di audio.input_device tidak "
                "ditemukan"
            )
        if picked:
            return WARN, f"Mikrofon: {name}, default Windows, karena mic yang dipilih di HP hilang"
        return OK, f"Mikrofon: {name}, default Windows (audio.input_device kosong)"
    if value in picked:
        source = f", dipilih di HP ({config.audio.override_path})"
    else:
        rank = listed.index(value) + 1
        source = " dari audio.input_device" + (
            f", pilihan {rank} dari {len(listed)}" if rank > 1 else ""
        )
    if default is not None and same_device_name(name, default):
        return OK, f"Mikrofon: {name}{source}, sama dengan default Windows"
    other = f"default Windows: {default}" if default is not None else "Windows tanpa mic default"
    return OK, f"Mikrofon: {name}{source} ({other})"


def _phone_voice_settings(override: VoiceOverride) -> str:
    """What the mic line adds for the voice settings changed on the phone's Suara page."""
    settings = []
    if override.speaker_threshold is not None:
        settings.append(f"ambang suara {override.speaker_threshold:.2f}")
    if override.vad_threshold_db is not None:
        settings.append(f"VAD {override.vad_threshold_db:g} dB")
    if override.vad_margin_db is not None:
        settings.append(f"margin {override.vad_margin_db:g} dB")
    return f"; setelan suara dari HP: {', '.join(settings)}" if settings else ""


def _fallback_voice_checks(config: AppConfig) -> Iterator[Check]:
    """The Windows voice that speaks each language when her voice fails; one listing."""
    if not config.tts.fallback.enabled:
        yield _fallback_off_check(config)
        return
    try:
        voices = list_windows_voices()
    except WindowsVoiceError as exc:
        reason = str(exc).rstrip(".")
        unusable = "suara cadangan offline tidak bisa dipakai"
        yield WARN, f"Suara Windows tidak terbaca ({reason}); {unusable}"
        return
    yield from _fallback_voice_lines(config.tts.fallback, voices)


def _fallback_off_check(config: AppConfig) -> Check:
    return WARN, (
        "Suara cadangan offline mati (tts.fallback.enabled: false); "
        f"kalau edge-tts gagal, {config.assistant.name} diam"
    )


def _fallback_voice_lines(
    fallback: FallbackTtsConfig, voices: list[WindowsVoice]
) -> Iterator[Check]:
    """A bonus, so a missing voice is a warning: she still shows her text without it."""
    for language, wanted in fallback.voices.items():
        label = f"Suara cadangan bahasa {language_name(language)}"
        voice = choose_voice(voices, language, wanted)
        if voice is not None:
            gender = _GENDERS.get(voice.gender.lower())
            details = f"{voice.language}, {gender}" if gender else voice.language
            yield OK, f"{label}: {voice.name} ({details})"
        elif wanted:
            setting = f"tts.fallback.voices.{language}"
            yield WARN, f"{label}: '{wanted}' ({setting}) tidak terpasang; lihat 'waifu voices'"
        else:
            yield WARN, f"{label}: {missing_voice_hint(language)}"


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


def _redeem_codes_check(config: AppConfig) -> Check:
    """Whether she watches for redeem codes and redeems them by herself, and whether the cookie
    holds cookie_token_v2, which redeeming needs (by its name only); the source is never
    asked."""
    codes = config.redeem_codes
    if not (config.gacha.enabled and codes.enabled):
        off = "redeem_codes.enabled" if config.gacha.enabled else "gacha.enabled"
        return WARN, f"Kode redeem mati ({off}: false); 'waifu gacha codes' tetap bisa dipakai"
    every = f"cek tiap {codes.poll_hours} jam"
    if not codes.notices:
        plan = f"{every} tanpa pemberitahuan, ditukarkan hanya kalau diminta"
        without_cookie = "penukaran gagal sampai cookie diisi"
    elif codes.auto_redeem:
        plan = f"{every}, kode baru langsung ditukarkan (auto_redeem)"
        without_cookie = "kode hanya diberitahukan dan belum ditukarkan otomatis"
    else:
        plan = f"{every}, ditukarkan hanya kalau diminta"
        without_cookie = "kode hanya diberitahukan"
    status = f"Kode redeem: aktif, {plan}"
    cookie = config.gacha.cookie
    if cookie is None:
        return WARN, f"{status}; cookie HoYoLAB belum diisi (gacha.cookie), jadi {without_cookie}"
    if "cookie_token_v2" not in cookie_names(cookie.get_secret_value()):
        return WARN, (
            f"{status}; cookie HoYoLAB tanpa cookie_token_v2, jadi HoYoLAB akan menolak "
            "penukarannya"
        )
    return OK, f"{status}; cookie memuat cookie_token_v2"


def _weekly_report_check(config: AppConfig) -> Check:
    """When she gives the weekly game report by herself, whether the phone gets it, and whether
    HoYoLAB can be read for it (by the cookie's presence only); nothing is read."""
    weekly = config.weekly_report
    if not (config.gacha.enabled and weekly.enabled):
        off = "weekly_report.enabled" if config.gacha.enabled else "gacha.enabled"
        return (
            WARN,
            f"Laporan game mingguan mati ({off}: false); 'waifu gacha report' tetap bisa dipakai",
        )
    day = WEEKDAYS_ID[get_args(Weekday).index(weekly.day)]
    status = f"Laporan game mingguan: tiap {day} jam {weekly.time:%H:%M}"
    if weekly.push and config.notify.enabled:
        status += ", juga ke HP"
    if config.gacha.cookie is None:
        return WARN, (
            f"{status}; cookie HoYoLAB belum diisi (gacha.cookie), jadi endgame dan event tidak "
            "terbaca"
        )
    return OK, status


def _tepira_check(config: AppConfig) -> Check:
    """Whether she reads Master's money from tepira, and records it; offline, and never any part
    of the token (unlike the ntfy topic, it is a password)."""
    tepira = config.tepira
    if not tepira.enabled:
        return WARN, "Keuangan tepira mati (tepira.enabled: false)"
    reads = f"Keuangan tepira: {tepira.url.removeprefix('https://')}, token ada"
    test = "uji dengan: waifu tepira"
    if not tepira.writes:
        return OK, f"{reads}; catat transaksi mati; {test}"
    if config.brain.backend != "claude":
        return WARN, f"{reads}; catat transaksi tidak tersedia dengan Ollama; {test}"
    return OK, f"{reads}; catat transaksi aktif ke dompet {tepira.default_wallet}; {test}"


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
