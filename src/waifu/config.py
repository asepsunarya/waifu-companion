"""Typed configuration loaded from config.yaml.

Every other module receives these models instead of raw dicts, so a typo in
config.yaml fails at startup with a clear message rather than mid-conversation.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import time
from pathlib import Path, PureWindowsPath
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

DEVICE_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
GameId = Literal["hsr", "genshin", "zzz"]
# Either pair logs in to HoYoLAB; hoyolab.com sets the v2 pair today.
HOYOLAB_LOGIN_COOKIES = (("ltuid_v2", "ltoken_v2"), ("ltuid", "ltoken"))
# Anyone who knows a topic on the public ntfy server can read it, so it must be hard to guess.
NTFY_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# A Tailscale MagicDNS name: <machine>.<tailnet>.ts.net, no scheme or port.
TAILSCALE_HOST_PATTERN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+\.ts\.net$")
# What an `apps` device may start: launcher URLs, or a program or shortcut on a local drive.
# The target itself is never a script (.bat, .cmd, .ps1, .vbs); what a listed shortcut points
# to is the owner's choice.
APP_URL_SCHEMES = ("steam://", "com.epicgames.launcher://")
APP_FILE_SUFFIXES = (".exe", ".lnk", ".url")
MAX_APPS = 30
# The web search's location: an ISO 3166-1 alpha-2 country and an IANA time zone (this Python
# has no tz database to look names up in, so the form is checked; the API checks the name).
COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
IANA_TIMEZONE_PATTERN = re.compile(
    r"^(UTC|(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific|Etc)"
    r"(/[A-Za-z0-9_+-]+){1,2})$"
)
GamePlatform = Literal["epic", "steam", "gog", "itchio", "pc"]


def cookie_names(cookie: str) -> set[str]:
    """The names in a `name=value; name=value` cookie string."""
    return {part.split("=", 1)[0].strip() for part in cookie.split(";") if "=" in part}


class ConfigError(Exception):
    """config.yaml is missing, unreadable, or invalid."""


def _ipv4_loopback(value: str, field: str) -> str:
    """Local servers have no password and listen on IPv4 only ("localhost" would try ::1
    first and cost ~2 s per request on Windows), so they must bind an IPv4 loopback."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        address = None
    if address is None or address.version != 4 or not address.is_loopback:
        raise ValueError(f"{field} must be an IPv4 loopback address such as 127.0.0.1")
    return value


def at_sentence_start(word: str) -> str:
    """`word` as it opens a sentence: first letter capitalised, the rest as written.

    `owner_call` is used as written mid-sentence ("Sudah, sayang."), but "sayang" opens a
    sentence as "Sayang, ...".
    """
    return word[:1].upper() + word[1:]


class _Strict(BaseModel):
    # hide_input_in_errors: a validation error must never echo a local_key back to the console.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class AssistantConfig(_Strict):
    name: str = "Cyrene"
    # What she calls the owner when she speaks ("Master", "Sayang"); instructions keep "Master".
    owner_call: str = Field(default="Master", max_length=30)
    wake_words: list[str] = Field(default_factory=lambda: ["cyrene", "hai cyrene", "hei cyrene"])
    followup_seconds: float = Field(default=8.0, ge=0)
    persona_file: Path = Path("config/persona.md")
    session_idle_minutes: float = Field(default=10.0, gt=0)
    max_session_turns: int = Field(default=40, ge=1)

    @field_validator("owner_call")
    @classmethod
    def _owner_call_is_a_plain_word(cls, value: str) -> str:
        value = value.strip()
        # Square brackets mark her emotion tag, and braces are prompt placeholders.
        if not value or any(mark in value for mark in "[]{}"):
            raise ValueError("owner_call must be a short word without brackets or braces")
        return value

    @field_validator("wake_words")
    @classmethod
    def _wake_words_not_empty(cls, value: list[str]) -> list[str]:
        cleaned = [w.strip().lower() for w in value if w.strip()]
        if not cleaned:
            raise ValueError("at least one wake word is required")
        return cleaned


class VadConfig(_Strict):
    threshold_db: float = -45.0
    margin_db: float = 12.0
    frame_ms: int = Field(default=30, gt=0)
    min_speech_ms: int = Field(default=300, ge=0)
    silence_ms: int = Field(default=700, gt=0)
    max_speech_s: float = Field(default=15.0, gt=0)
    pre_roll_ms: int = Field(default=300, ge=0)


class AudioConfig(_Strict):
    input_device: int | str | None = None
    output_device: int | str | None = None
    sample_rate: int = 16000
    vad: VadConfig = Field(default_factory=VadConfig)


class SpeakerConfig(_Strict):
    enabled: bool = True
    voiceprint_path: Path = Path("data/voiceprint.npy")
    # 0.30 let a different female voice through in testing (0.37); the owner scored 0.75.
    threshold: float = Field(default=0.45, ge=-1.0, le=1.0)
    model_source: str = "speechbrain/spkrec-ecapa-voxceleb"
    model_dir: Path = Path("models/spkrec-ecapa")
    device: str = "cuda"


class SttConfig(_Strict):
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    model_dir: Path = Path("models/whisper")
    languages: list[str] = Field(default_factory=lambda: ["id", "ja"])
    beam_size: int = Field(default=5, ge=1)

    @field_validator("languages")
    @classmethod
    def _languages_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one language is required; the first is the fallback")
        return value


class ClaudeConfig(_Strict):
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # The SDK refuses non-streaming requests above 21,333 tokens (expected > 10 minutes).
    max_tokens: int = Field(default=16000, gt=0, le=21_333)
    fallbacks: bool = True


class OllamaConfig(_Strict):
    host: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    think: bool = False
    keep_alive: str = "30m"


class BrainConfig(_Strict):
    backend: Literal["ollama", "claude"] = "ollama"
    max_tool_rounds: int = Field(default=5, ge=1)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class EdgeVoiceConfig(_Strict):
    engine: Literal["edge"] = "edge"
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"


class GptSovitsVoiceConfig(_Strict):
    engine: Literal["gpt_sovits"] = "gpt_sovits"
    url: str = "http://127.0.0.1:9880"
    ref_audio_path: str
    prompt_text: str
    prompt_lang: str = "ja"
    speed_factor: float = Field(default=1.0, gt=0)


VoiceConfig = Annotated[EdgeVoiceConfig | GptSovitsVoiceConfig, Field(discriminator="engine")]


class RvcConfig(_Strict):
    """Voice conversion of every spoken reply through an RVC model served by Applio."""

    enabled: bool = False
    applio_dir: Path = Path("C:/AI/Applio")
    python: Path | None = None
    model: Path | None = None
    index: Path | None = None
    # +4 semitones lifts edge-tts (median ~275 Hz) to the Tsukuyomi corpus (~351 Hz).
    pitch: int = Field(default=4, ge=-24, le=24)
    index_rate: float = Field(default=0.75, ge=0.0, le=1.0)
    protect: float = Field(default=0.33, ge=0.0, le=0.5)
    host: str = "127.0.0.1"
    port: int = Field(default=9881, ge=1024, le=65535)
    startup_timeout_s: float = Field(default=180.0, gt=0)

    @property
    def python_path(self) -> Path:
        return self.python or self.applio_dir / "env" / "Scripts" / "python.exe"

    @field_validator("host")
    @classmethod
    def _host_is_ipv4_loopback(cls, value: str) -> str:
        return _ipv4_loopback(value, "tts.rvc.host")

    @model_validator(mode="after")
    def _enabled_needs_model(self) -> RvcConfig:
        if self.enabled and self.model is None:
            raise ValueError("tts.rvc.model (the .pth file) is required when tts.rvc is enabled")
        return self


class TtsConfig(_Strict):
    voices: dict[str, VoiceConfig] = Field(
        default_factory=lambda: {
            "id": EdgeVoiceConfig(voice="id-ID-GadisNeural", pitch="+15Hz"),
            "ja": EdgeVoiceConfig(voice="ja-JP-NanamiNeural", pitch="+10Hz"),
        }
    )
    default_language: str = "id"
    rvc: RvcConfig = Field(default_factory=RvcConfig)

    @model_validator(mode="after")
    def _default_language_has_voice(self) -> TtsConfig:
        if self.default_language not in self.voices:
            raise ValueError(f"tts.default_language '{self.default_language}' has no voice")
        return self


class TuyaConnection(_Strict):
    device_id: str
    local_key: str
    address: str = "Auto"
    version: float = 3.3


class IrConnection(_Strict):
    backend: Literal["broadlink", "tuya"]
    host: str | None = None
    tuya: TuyaConnection | None = None

    @model_validator(mode="after")
    def _backend_has_connection(self) -> IrConnection:
        if self.backend == "broadlink" and not self.host:
            raise ValueError("ir.backend 'broadlink' needs ir.host (the blaster's IP address)")
        if self.backend == "tuya" and self.tuya is None:
            raise ValueError("ir.backend 'tuya' needs an ir.tuya block")
        return self


class PcConnection(_Strict):
    mac: str | None = None
    broadcast: str = "255.255.255.255"
    local: bool = True
    shutdown_delay_s: int = Field(default=30, ge=0)


class DeviceConfig(_Strict):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    type: Literal["tuya_light", "tuya_plug", "ir", "pc", "media", "apps", "mock"]
    tuya: TuyaConnection | None = None
    ir: IrConnection | None = None
    pc: PcConnection | None = None
    commands: list[str] = Field(default_factory=list)
    # A plug that feeds the PC: switching it off cuts the PC's power like pulling the cable, so
    # it is guarded like the PC itself (never scheduled, never a routine step).
    powers_pc: bool = False
    # type: apps only: app slug -> what to start (see APP_URL_SCHEMES, APP_FILE_SUFFIXES).
    apps: dict[str, str] | None = None

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, value: str) -> str:
        if not DEVICE_ID_PATTERN.match(value):
            raise ValueError("device id must be lowercase letters, digits, or underscores")
        return value

    @field_validator("commands", mode="before")
    @classmethod
    def _yaml_booleans_are_on_off(cls, value: object) -> object:
        # YAML 1.1 parses bare on/off as booleans; for a command list they always mean the words.
        if isinstance(value, list):
            return [("on" if v else "off") if isinstance(v, bool) else v for v in value]
        return value

    @field_validator("commands")
    @classmethod
    def _commands_are_slugs(cls, value: list[str]) -> list[str]:
        bad = [command for command in value if not DEVICE_ID_PATTERN.match(command)]
        if bad:
            raise ValueError(
                f"command names must be lowercase letters, digits, or underscores: {bad}"
            )
        return value

    @model_validator(mode="after")
    def _type_has_connection(self) -> DeviceConfig:
        needed = {"tuya_light": "tuya", "tuya_plug": "tuya", "ir": "ir", "pc": "pc"}.get(self.type)
        if needed and getattr(self, needed) is None:
            raise ValueError(f"device '{self.id}' of type '{self.type}' needs a '{needed}' block")
        return self

    @field_validator("apps")
    @classmethod
    def _apps_are_slugs_with_safe_targets(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        for name, target in (value or {}).items():
            if not DEVICE_ID_PATTERN.match(name):
                raise ValueError(
                    f"app name '{name}' must be lowercase letters, digits, or underscores"
                )
            if not _is_app_target(target):
                raise ValueError(
                    f"app '{name}': the target must be a steam:// or com.epicgames.launcher:// "
                    "URL, or a full path on a drive ending in .exe, .lnk, or .url"
                )
        return value

    @model_validator(mode="after")
    def _apps_only_for_an_apps_device(self) -> DeviceConfig:
        if self.type == "apps" and not (self.apps and len(self.apps) <= MAX_APPS):
            raise ValueError(f"device '{self.id}' of type 'apps' needs 1-{MAX_APPS} apps")
        if self.type != "apps" and self.apps is not None:
            raise ValueError(f"device '{self.id}': apps are only for type 'apps'")
        return self

    @model_validator(mode="after")
    def _only_a_plug_powers_the_pc(self) -> DeviceConfig:
        if self.powers_pc and self.type not in ("tuya_plug", "mock"):
            raise ValueError(
                f"device '{self.id}': powers_pc is only for the plug that feeds the PC "
                f"(type tuya_plug), not for type '{self.type}'"
            )
        return self


# `{owner}` is filled with `assistant.owner_call` when she says the line, in these and in lines
# the owner writes.
_DEFAULT_INTERACTION_LINES: dict[str, list[str]] = {
    "head_pat": [
        "えへへ, geli, {owner}!",
        "Hehe, makasih elusannya, {owner}.",
        "Mm, enak dielus begini.",
    ],
    "body_touch": ["Hei! Kaget aku, {owner}.", "Eh? Ada apa, {owner}?", "Jangan iseng, ya!"],
    "welcome_back": ["おかえりなさい, {owner}!", "{owner} sudah kembali! Aku kangen."],
    "listen": ["はい, {owner}?", "Iya, aku dengar."],
}


class AvatarInteractionsConfig(_Strict):
    """How the avatar reacts to the mouse: looking at the cursor, pats, pokes, double-clicks."""

    enabled: bool = True
    cursor_follow: bool = True
    head_pat: bool = True
    body_touch: bool = True
    double_click_listen: bool = True
    idle: bool = True
    # Greet the owner when the mouse moves again after this many idle minutes (0: never).
    welcome_back_after_min: int = Field(default=10, ge=0, le=24 * 60)
    # Minimum seconds between two spoken reactions, so clicking does not spam replies.
    reaction_cooldown_s: float = Field(default=3.0, ge=0.0, le=600.0)
    lines: dict[str, list[str]] = Field(default_factory=lambda: dict(_DEFAULT_INTERACTION_LINES))

    @field_validator("lines")
    @classmethod
    def _lines_complete(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        merged = {**_DEFAULT_INTERACTION_LINES, **value}
        for kind, lines in merged.items():
            cleaned = [line.strip() for line in lines if line.strip()]
            if not cleaned:
                raise ValueError(f"avatar.interactions.lines.{kind} needs at least one line")
            merged[kind] = cleaned
        return merged


class AvatarConfig(_Strict):
    """A VRM or Live2D avatar that lip-syncs her replies, shown as a desktop mascot or a window."""

    enabled: bool = False
    # vrm: three.js + three-vrm; live2d: PixiJS + a Cubism 5 engine and the owner's Cubism Core.
    engine: Literal["vrm", "live2d"] = "vrm"
    model: Path | None = None
    live2d_core: Path | None = None
    credit: str = "つくよみちゃん（© Rei Yumesaki）"
    interactions: AvatarInteractionsConfig = Field(default_factory=AvatarInteractionsConfig)
    host: str = "127.0.0.1"
    port: int = Field(default=9882, ge=1024, le=65535)
    # mascot: transparent always-on-top Electron window; app: Edge app window; none: server only.
    window: Literal["mascot", "app", "none"] = "mascot"
    electron_dir: Path = Path("avatar-app")
    width: int = Field(default=360, ge=120, le=2000)
    height: int = Field(default=640, ge=160, le=2000)
    corner: Literal["bottom-right", "bottom-left", "top-right", "top-left"] = "bottom-right"
    always_on_top: bool = True
    framing: Literal["full", "upper"] = "full"

    @field_validator("host")
    @classmethod
    def _host_is_ipv4_loopback(cls, value: str) -> str:
        return _ipv4_loopback(value, "avatar.host")

    @model_validator(mode="after")
    def _engine_has_its_files(self) -> AvatarConfig:
        if self.model is not None:
            name = self.model.name.lower()
            if self.engine == "vrm" and not name.endswith(".vrm"):
                raise ValueError("avatar.model must be a .vrm file for engine: vrm")
            if self.engine == "live2d" and not name.endswith(".model3.json"):
                raise ValueError("avatar.model must be a .model3.json file for engine: live2d")
        if not self.enabled:
            return self
        if self.model is None:
            raise ValueError("avatar.model is required when the avatar is enabled")
        if self.engine == "live2d" and self.live2d_core is None:
            raise ValueError(
                "avatar.live2d_core (live2dcubismcore.min.js from the official Cubism SDK for "
                "Web) is required for engine: live2d"
            )
        return self


class GachaRemindersConfig(_Strict):
    # Remind when stamina will be full within this many minutes (0: only once it is full).
    stamina_minutes: int = Field(default=60, ge=0, le=1440)
    # Local time from which unfinished dailies (and, on the last day, weeklies) are mentioned.
    daily_time: time = time(20, 0)
    weekly: bool = True
    expeditions: bool = True

    @field_validator("daily_time", mode="before")
    @classmethod
    def _yaml_sexagesimal_is_hh_mm(cls, value: object) -> object:
        return _clock_from_yaml(value)


class RedeemCodesConfig(_Strict):
    """New redeem codes for the gacha buddy's games, redeemed on HoYoLAB when Master asks."""

    enabled: bool = True
    poll_hours: int = Field(default=6, ge=1, le=48)
    # Redeem new codes without asking first; off by default: it acts on Master's account.
    auto_redeem: bool = False
    notices: bool = True
    state_path: Path = Path("data/redeem_codes.json")


class GachaConfig(_Strict):
    """The HoYoverse games companion: status, reminders, check-in, and pull history."""

    enabled: bool = False
    # The hoyolab.com login cookie: as secret as a password, never logged or printed.
    cookie: SecretStr | None = None
    games: list[GameId] = Field(default_factory=lambda: ["hsr", "genshin", "zzz"])
    uids: dict[GameId, Annotated[int, Field(gt=0)]] = Field(default_factory=dict)
    server_utc_offset: int = Field(default=8, ge=-12, le=14)  # Asia server: UTC+8
    poll_minutes: float = Field(default=15.0, ge=5, le=240)
    checkin: bool = True
    reminders: GachaRemindersConfig = Field(default_factory=GachaRemindersConfig)
    # Extra 5-star names that mean a lost 50/50 (HSR lets the owner pick one since 4.2).
    off_banner: dict[GameId, list[str]] = Field(default_factory=dict)
    data_dir: Path = Path("data/gacha")

    @field_validator("cookie")
    @classmethod
    def _cookie_logs_in(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        names = cookie_names(value.get_secret_value())
        if not any(uid in names and token in names for uid, token in HOYOLAB_LOGIN_COOKIES):
            raise ValueError(
                "gacha.cookie must contain ltuid_v2 and ltoken_v2 "
                "(copy the whole cookie of hoyolab.com, see README)"
            )
        return value

    @field_validator("games")
    @classmethod
    def _games_unique(cls, value: list[GameId]) -> list[GameId]:
        if not value:
            raise ValueError("at least one game is required")
        if len(set(value)) != len(value):
            raise ValueError("each game may be listed once")
        return value


def _is_app_target(target: str) -> bool:
    if target.lower().startswith(APP_URL_SCHEMES):
        return True
    path = PureWindowsPath(target)
    # A drive letter, not a network share (//server/share) or a relative path.
    return len(path.drive) == 2 and path.is_absolute() and path.suffix.lower() in APP_FILE_SUFFIXES


def _clock_from_yaml(value: object) -> object:
    """A clock time YAML 1.1 read as a number: a bare 20:00 is base 60, 1200 minutes."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 24 * 60:
        return time(value // 60, value % 60)
    return value


# `datetime.time` under a name a field called `time` cannot shadow.
_Clock = time


class BriefingConfig(_Strict):
    """The once-a-day greeting with the weather and the day's schedule."""

    enabled: bool = True
    time: _Clock = _Clock(7, 0)
    until: _Clock = _Clock(12, 0)
    # Where the weather is for: a city name (geocoded by Open-Meteo), or exact coordinates.
    city: str | None = Field(default=None, min_length=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    push: bool = True
    state_path: Path = Path("data/briefing.json")

    @field_validator("time", "until", mode="before")
    @classmethod
    def _yaml_sexagesimal_is_hh_mm(cls, value: object) -> object:
        return _clock_from_yaml(value)

    @model_validator(mode="after")
    def _a_window_and_whole_coordinates(self) -> BriefingConfig:
        if self.time >= self.until:
            raise ValueError("briefing.time must be before briefing.until")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("briefing.latitude and briefing.longitude go together")
        return self


class PhoneConfig(_Strict):
    """Cyrene on the owner's phone: a page served on 127.0.0.1 and shared through Tailscale."""

    enabled: bool = False
    port: int = Field(default=9883, ge=1, le=65535)
    # The PC's Tailscale name (`tailscale status`), the only outside Host the server answers.
    host: str | None = None
    state_path: Path = Path("data/phone.json")
    # The phone page's camera; offered only with vision on and the Claude backend.
    camera: bool = True

    @field_validator("host")
    @classmethod
    def _host_is_a_tailscale_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not TAILSCALE_HOST_PATTERN.match(value):
            raise ValueError("phone.host must be the PC's Tailscale name, like pc.tailnet.ts.net")
        return value


class ModelPrices(_Strict):
    """US dollars per million tokens of one Claude model."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_write: float = Field(ge=0)
    cache_read: float = Field(ge=0)


class UsageConfig(_Strict):
    """The local record of what Claude costs: tokens per day and an estimated price."""

    enabled: bool = True
    path: Path = Path("data/usage.json")
    monthly_budget_usd: float | None = Field(default=None, gt=0)
    idr_per_usd: float | None = Field(default=None, gt=0)
    # Merged over the built-in table in usage.py (model id -> prices).
    prices: dict[str, ModelPrices] = Field(default_factory=dict)
    # Whose prices a response from a model missing from the table is estimated with.
    default_model: str = Field(default="claude-opus-5-5", min_length=1)
    # Claude's server-side web search, billed per search on top of the tokens.
    web_search_per_1000_usd: float = Field(default=10.0, ge=0)


class WebSearchConfig(_Strict):
    """Claude's server-side web search in her conversation (Claude backend only)."""

    enabled: bool = True
    max_uses: int = Field(default=3, ge=1, le=10)  # searches per request to Claude
    country: str = "ID"
    timezone: str = "Asia/Jakarta"
    # None: briefing.city.
    city: str | None = Field(default=None, min_length=1)

    @field_validator("country")
    @classmethod
    def _country_is_two_letters(cls, value: str) -> str:
        if not COUNTRY_CODE_PATTERN.fullmatch(value):
            raise ValueError("web_search.country must be two uppercase letters, like ID")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_is_iana(cls, value: str) -> str:
        if not IANA_TIMEZONE_PATTERN.fullmatch(value):
            raise ValueError("web_search.timezone must be an IANA time zone, like Asia/Jakarta")
        return value


class FreeOffersConfig(_Strict):
    """Free games and AI programs she finds, and the notices about new ones."""

    enabled: bool = True
    games: bool = True
    game_platforms: list[GamePlatform] = Field(
        default_factory=lambda: ["epic", "steam", "gog"], min_length=1
    )
    games_poll_hours: int = Field(default=6, ge=1, le=48)
    ai: bool = True
    ai_scan_days: int = Field(default=7, ge=1, le=30)
    ai_max_searches: int = Field(default=10, ge=1, le=20)
    notices: bool = True
    state_path: Path = Path("data/free_offers.json")

    @field_validator("game_platforms")
    @classmethod
    def _platforms_are_unique(cls, value: list[GamePlatform]) -> list[GamePlatform]:
        if len(set(value)) != len(value):
            raise ValueError("free_offers.game_platforms lists a platform twice")
        return value


class VisionConfig(_Strict):
    """Looking at the screen when Master asks (Claude backend only)."""

    enabled: bool = True
    # The screenshot's longer side: Claude reads up to about 1568 pixels. A busy screen whose PNG
    # is over the API's 5 MB image limit is taken again smaller (see screen_tools).
    max_side: int = Field(default=1280, ge=512, le=1568)


class PcHealthConfig(_Strict):
    """Reading the PC's load, memory, disks, and GPU, and speaking up when it needs care."""

    enabled: bool = True
    # The proactive part: a nearly full drive, a GPU that stays hot. Off keeps the tool.
    notices: bool = True
    poll_seconds: int = Field(default=60, ge=15, le=3600)
    disk_warn_gb: int = Field(default=10, ge=1, le=2000)
    gpu_warn_c: int = Field(default=85, ge=60, le=100)
    gpu_warn_minutes: int = Field(default=3, ge=1, le=60)


class MusicConfig(_Strict):
    """Songs on YouTube, opened in the default browser when Master asks."""

    enabled: bool = True
    service: Literal["youtube", "youtube_music"] = "youtube"


class TodoConfig(_Strict):
    """Master's to-do list: tasks with an optional due date, never a time of day."""

    enabled: bool = True
    path: Path = Path("data/todo.json")
    max_tasks: int = Field(default=100, ge=1, le=500)  # open tasks
    max_chars: int = Field(default=200, ge=20, le=1000)
    # Done tasks are kept this long ("what did I finish?"), then forgotten.
    keep_done_days: int = Field(default=7, ge=0, le=90)


class MemoryConfig(_Strict):
    """Cyrene's long-term memory: facts about the owner kept between sessions."""

    enabled: bool = True
    path: Path = Path("data/memory.json")
    max_facts: int = Field(default=100, ge=1, le=500)
    max_chars: int = Field(default=200, ge=20, le=1000)


class ScheduleConfig(_Strict):
    """Reminders and timed device commands that fire while `waifu run` is on."""

    enabled: bool = True
    path: Path = Path("data/schedule.json")
    max_entries: int = Field(default=50, ge=1, le=200)
    # How late an entry may still fire after the PC was off or Cyrene was stopped.
    late_grace_minutes: int = Field(default=30, ge=0, le=720)


class RoutineStep(_Strict):
    device: str
    command: str
    value: int | None = None

    @field_validator("command", mode="before")
    @classmethod
    def _yaml_boolean_is_on_off(cls, value: object) -> object:
        # YAML 1.1 parses a bare on/off as a boolean; as a command it always means the word.
        return ("on" if value else "off") if isinstance(value, bool) else value


class RoutineConfig(_Strict):
    """A named list of device commands the owner wrote ("tidur": lights off, AC to 26)."""

    description: str = ""
    steps: list[RoutineStep] = Field(min_length=1, max_length=20)


class NotifyConfig(_Strict):
    """Push notifications to the owner's phone through ntfy."""

    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: SecretStr | None = None

    @field_validator("server")
    @classmethod
    def _server_uses_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("notify.server must start with https://")
        return value.rstrip("/")

    @field_validator("topic")
    @classmethod
    def _topic_is_hard_to_guess(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not NTFY_TOPIC_PATTERN.match(value.get_secret_value()):
            raise ValueError(
                "notify.topic must be 16-64 letters, digits, '-' or '_' (anyone who knows it "
                "can read the notifications)"
            )
        return value

    @model_validator(mode="after")
    def _enabled_needs_topic(self) -> NotifyConfig:
        if self.enabled and self.topic is None:
            raise ValueError("notify.topic is required when notify is enabled")
        return self


class AppConfig(_Strict):
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    speaker: SpeakerConfig = Field(default_factory=SpeakerConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    brain: BrainConfig = Field(default_factory=BrainConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    ir_codes_path: Path = Path("data/ir_codes.json")
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    gacha: GachaConfig = Field(default_factory=GachaConfig)
    redeem_codes: RedeemCodesConfig = Field(default_factory=RedeemCodesConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    todo: TodoConfig = Field(default_factory=TodoConfig)
    music: MusicConfig = Field(default_factory=MusicConfig)
    pc_health: PcHealthConfig = Field(default_factory=PcHealthConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    free_offers: FreeOffersConfig = Field(default_factory=FreeOffersConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    usage: UsageConfig = Field(default_factory=UsageConfig)
    phone: PhoneConfig = Field(default_factory=PhoneConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    routines: dict[str, RoutineConfig] = Field(default_factory=dict)
    devices: list[DeviceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _device_ids_unique(self) -> AppConfig:
        seen: set[str] = set()
        for device in self.devices:
            if device.id in seen:
                raise ValueError(f"duplicate device id '{device.id}'")
            seen.add(device.id)
        return self

    @model_validator(mode="after")
    def _routines_use_known_devices_but_never_the_pc(self) -> AppConfig:
        devices = {device.id: device for device in self.devices}
        for name, routine in self.routines.items():
            if not DEVICE_ID_PATTERN.match(name):
                raise ValueError(
                    f"routine name '{name}' must be lowercase letters, digits, or underscores"
                )
            for step in routine.steps:
                device = devices.get(step.device)
                if device is None:
                    raise ValueError(f"routine '{name}' uses unknown device '{step.device}'")
                if device.powers_pc:
                    raise ValueError(
                        f"routine '{name}' may not use '{step.device}': it powers the PC"
                    )
                if device.type == "pc":
                    # The PC is turned off, restarted, or put to sleep only by voice, with a
                    # confirmation: never as one step of a routine.
                    raise ValueError(f"routine '{name}' may not use the PC '{step.device}'")
        return self


def load_config(path: Path | str) -> AppConfig:
    """Read and validate a YAML config file. Relative paths inside stay relative to the CWD."""
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(
            f"{config_path} not found. Copy config.example.yaml to {config_path} first."
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a mapping at the top level")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{config_path} is invalid:\n{exc}") from exc
