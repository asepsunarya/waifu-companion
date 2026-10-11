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
import os
import re
import sys
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from time import sleep

import numpy as np
from pydantic import ValidationError

from waifu.app import Assistant
from waifu.brain.base import ToolCallRecord
from waifu.brain.factory import build_brain
from waifu.config import AppConfig, ConfigError, OllamaConfig, load_config
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import COMMAND_PATTERN, IrCodeStoreError
from waifu.devices.ir_device import IrDevice
from waifu.devices.registry import build_registry
from waifu.gate import WakeGate
from waifu.voice.audio import MicStream, Player, list_devices
from waifu.voice.segmenter import EnergySegmenter, frame_db
from waifu.voice.speaker import MIN_ENROLL_SAMPLES, EcapaEmbedder, SpeakerError, SpeakerVerifier
from waifu.voice.stt import CUDA_HINT, SttError, WhisperStt, register_cuda_dll_dirs
from waifu.voice.tts import SpeechRouter, TtsError, build_speech_router

logger = logging.getLogger(__name__)

OK, FAIL, WARN = "✓", "✗", "!"
Check = tuple[str, str]

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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _use_utf8_for_redirected_streams()
    _configure_logging(args.verbose)
    try:
        return args.handler(args)
    except ConfigError as exc:
        print(f"Konfigurasi bermasalah: {_config_problem(exc)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nDihentikan.")
        return 130
    except ImportError as exc:
        logger.debug("Command '%s' needs a missing package", args.command, exc_info=True)
        print(
            f"Paket Python '{exc.name or exc}' belum terpasang. "
            'Untuk fitur suara, ikuti README (pip install -e ".[voice]").',
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        logger.debug("Command '%s' failed", args.command, exc_info=True)
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
        name: str, handler: Callable[[argparse.Namespace], int], help_text: str
    ) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[shared], help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    run = command("run", _run, "Dengarkan mikrofon dan jawab dengan suara.")
    run.add_argument(
        "--no-speaker-check", action="store_true", help="layani semua suara, bukan hanya Master"
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
    command("mics", _mics, "Daftar perangkat audio.")
    command("check", _check, "Periksa konfigurasi, perangkat, dan dependensi.")
    return parser


def _add_shared_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    def default(value: object) -> object:
        return argparse.SUPPRESS if suppress_defaults else value

    parser.add_argument(
        "--config", type=Path, default=default(Path("config.yaml")), help="file konfigurasi"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=default(False), help="pakai perangkat simulasi"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=default(False), help="log lebih rinci"
    )


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
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # tinytuya logs the device local_key at DEBUG (e.g. after a wrong key or version),
    # and -v output gets pasted when asking for help, so it stays at INFO even then.
    logging.getLogger("tinytuya").setLevel(logging.INFO)
    if not verbose:
        # httpx logs every Ollama request at INFO, which would bury the conversation.
        logging.getLogger("httpx").setLevel(logging.WARNING)


# --- voice loop ------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    speaker_check = config.speaker.enabled and not args.no_speaker_check
    verifier = _speaker_verifier(config) if speaker_check else None
    if verifier is not None and not verifier.enrolled:
        print(f"{NOT_ENROLLED} Atau pakai 'waifu run --no-speaker-check' untuk semua suara.")
        return 2
    registry = build_registry(config, dry_run=args.dry_run)
    assistant = Assistant(
        config,
        brain=build_brain(config, registry),
        stt=WhisperStt(config.stt, config.assistant.name),
        speech=build_speech_router(config.tts),
        player=Player(config.audio.output_device),
        gate=WakeGate(config.assistant.wake_words, config.assistant.followup_seconds),
        verifier=verifier,
        speaker_check=speaker_check,
    )
    segmenter = EnergySegmenter(config.audio.sample_rate, config.audio.vad)
    with _open_mic(config) as mic:
        mode = " (perangkat simulasi)" if args.dry_run else ""
        print(
            f"{config.assistant.name} siap mendengarkan{mode}. "
            f"Panggil dengan: {', '.join(config.assistant.wake_words)}. "
            "Tekan Ctrl+C untuk berhenti."
        )
        if not speaker_check:
            print("Verifikasi suara mati: semua suara akan dilayani.")
        try:
            assistant.run(mic, segmenter)
        except SttError as exc:
            print(f"{FAIL} {exc}")
            return 1
    return 0


def _chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config, dry_run=args.dry_run)
    brain = build_brain(config, registry)
    speech = build_speech_router(config.tts) if args.speak else None
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
        reply = brain.respond(line)
        if args.verbose:
            for call in reply.tool_calls:
                print(_describe_tool_call(call))
        print(f"{name}: {reply.text}")
        if speech is not None and player is not None:
            _speak(speech, player, reply.text)
    return 0


def _describe_tool_call(call: ToolCallRecord) -> str:
    arguments = json.dumps(call.arguments, ensure_ascii=False)
    failed = " (gagal)" if call.is_error else ""
    return f"  [{call.name}] {arguments} -> {call.result}{failed}"


def _speak(speech: SpeechRouter, player: Player, text: str) -> bool:
    try:
        audio, sample_rate = speech.synthesize(text)
    except TtsError as exc:
        print(f"(Suara gagal dibuat: {exc})")
        return False
    player.play(audio, sample_rate)
    return True


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
    spoken = _speak(
        build_speech_router(config.tts), Player(config.audio.output_device), " ".join(args.text)
    )
    return 0 if spoken else 1


def _mics(args: argparse.Namespace) -> int:
    print(list_devices())
    return 0


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
        return OK, f"Claude ({model}): ANTHROPIC_API_KEY sudah diatur"
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
