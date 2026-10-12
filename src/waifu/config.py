"""Typed configuration loaded from config.yaml.

Every other module receives these models instead of raw dicts, so a typo in
config.yaml fails at startup with a clear message rather than mid-conversation.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEVICE_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


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


class _Strict(BaseModel):
    # hide_input_in_errors: a validation error must never echo a local_key back to the console.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class AssistantConfig(_Strict):
    name: str = "Cyrene"
    wake_words: list[str] = Field(default_factory=lambda: ["cyrene", "hai cyrene", "hei cyrene"])
    followup_seconds: float = Field(default=8.0, ge=0)
    persona_file: Path = Path("config/persona.md")
    session_idle_minutes: float = Field(default=10.0, gt=0)
    max_session_turns: int = Field(default=40, ge=1)

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
    type: Literal["tuya_light", "tuya_plug", "ir", "pc", "mock"]
    tuya: TuyaConnection | None = None
    ir: IrConnection | None = None
    pc: PcConnection | None = None
    commands: list[str] = Field(default_factory=list)

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


class AvatarConfig(_Strict):
    """A VRM avatar that lip-syncs her replies, shown as a desktop mascot or a window."""

    enabled: bool = False
    model: Path | None = None
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
    def _enabled_needs_model(self) -> AvatarConfig:
        if self.enabled and self.model is None:
            raise ValueError("avatar.model (the .vrm file) is required when the avatar is enabled")
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
    devices: list[DeviceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _device_ids_unique(self) -> AppConfig:
        seen: set[str] = set()
        for device in self.devices:
            if device.id in seen:
                raise ValueError(f"duplicate device id '{device.id}'")
            seen.add(device.id)
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
