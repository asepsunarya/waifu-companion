# Spec: waifu-companion

## Objective

An anime-style AI companion named **Cyrene** (renamable in `config.yaml`) that
listens through one USB mic on the owner's Windows PC, responds **only to the
owner's voice**, chats in Indonesian with Japanese phrases, and controls the
house: Tuya Wi-Fi lights and plugs, an IR blaster (Broadlink or Tuya) for the
Samsung AC, Wanbo Dali projector, and TV, and the PC itself.

Hardware budget is ≤ Rp1 juta, so there is no always-on hub: the PC runs
everything. Turning the PC *on* is out of scope for the voice loop (the owner
uses a smart plug + BIOS "power on AC restore" or Wake-on-LAN from a phone).

### User stories

1. "Cyrene, nyalakan lampu kamar" → light turns on, she answers in voice within ~3 s of the owner finishing speaking (local STT + LLM on the RTX 5060 Ti).
2. "Cyrene, AC dua puluh empat derajat dan matikan proyektor" → two device calls, one spoken summary.
3. Right after she answers, "terus nyalakan TV juga" works without the wake word (follow-up window).
4. A guest saying "Cyrene, matikan lampu" is ignored (speaker verification).
5. "Cyrene, matikan komputer" → she asks for confirmation first; on "iya" Windows schedules a 30 s shutdown that "batalkan" can cancel.
6. Without any hardware or GPU, `waifu chat --dry-run` lets the owner type and test the persona and tool calls against fake devices.

## Capability map

| Module id | Responsibility | Depends on |
|---|---|---|
| config | Typed `config.yaml` (done: `src/waifu/config.py`) | — |
| devices | Device contract, registry, Tuya, Broadlink, PC, mock, IR code store | config |
| brain | Tool specs from the registry, persona prompt, Claude and Ollama backends | config, devices |
| voice | Mic/speaker I/O, utterance segmenter, speaker verification, STT, TTS | config |
| app | Wake gate, listen → verify → transcribe → think → speak loop, CLI | all |

Build order: config → devices, voice → brain → app. `devices` and `voice` are
independent; `brain` only needs `devices.registry`'s public surface.

## Tech Stack

Python 3.14 venv at `.venv` (3.11+ supported). Core: `anthropic` 1.13, `ollama`
0.6, `pydantic` 2, `pyyaml`, `numpy` 2, `tinytuya` 1.20, `broadlink` 0.19,
`wakeonlan` 4, `edge-tts` 7, `soundfile`, `sounddevice`. Voice extra:
`faster-whisper` 1.2 (+ `ctranslate2`), `speechbrain` 1.1, `torch`/`torchaudio`
from the cu128 index (RTX 50-series needs CUDA 12.8+ builds).

## Commands

```
Install core:   .venv\Scripts\python -m pip install -e ".[dev]"
Install voice:  .venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
                .venv\Scripts\python -m pip install -e ".[voice]"
Test:           .venv\Scripts\python -m pytest
Lint:           .venv\Scripts\python -m ruff check src tests
Format:         .venv\Scripts\python -m ruff format src tests
Run:            .venv\Scripts\waifu run [--config config.yaml] [--dry-run] [--no-speaker-check]
```

## Project Structure

See `CLAUDE.md` → Project Structure. One test module per source module:
`src/waifu/devices/registry.py` → `tests/test_devices_registry.py`.

## Module contracts

The abstract contracts are already committed in `devices/base.py`,
`brain/base.py`, and `voice/base.py`. The concrete public surface below is what
other modules may rely on.

### devices

- `devices/mock.py` — `MockDevice(device_id, name, aliases=None, commands=None, kind="mock")`. Accepts any listed command (default `["on", "off"]`); like `IrDevice`, a `value` with a listed `f"{command}_{value}"` resolves to that command. Records `calls: list[tuple[str, int | None]]` (resolved command, value), updates `state["power"]` for on/off and `state["last_command"]`, returns ok results. Unknown commands raise `DeviceError`. Used by tests and `--dry-run`.
- `devices/ir_codes.py` — `IrCodeStore(path)`: JSON file `{device_id: {command: base64}}`. `get(device_id, command) -> str | None`, `commands(device_id) -> list[str]` (sorted), `put(device_id, command, code)` writes atomically (temp file + `os.replace`), creates the parent folder. Command names must match `^[a-z0-9_]+$`.
- `devices/tuya.py` — `TuyaLight` (commands `on`, `off`, `toggle`, `brightness` with value 1–100), `TuyaPlug` (`on`, `off`, `toggle`), `TuyaIrBlaster` (send/learn base64 codes via `tinytuya.Contrib.IRRemoteControlDevice`). Each takes a `TuyaConnection` and an injectable factory for the tinytuya object; connections are created lazily on first command and use a short socket timeout (≤ 5 s). Tuya error payloads (dicts with `Error`/`Err`) become `DeviceError`.
- `devices/broadlink_ir.py` — `BroadlinkBlaster(host, *, factory=broadlink.hello)`: lazy `auth()`, `send(code_b64)`, `learn(timeout_s=20) -> str` (enter learning, poll `check_data()` until data or timeout). Reconnects once on a socket/auth error.
- `devices/ir_device.py` — `IrDevice(device_id, name, aliases, blaster, store)`: commands are the learned names in the store. `execute(command, value)`: if `value` is given and `f"{command}_{value}"` is learned, send that (so `temp` + 24 → `temp_24`); unknown commands raise `DeviceError` listing what is learned. Tracks `state["last_command"]`. Blaster protocol: `send(code_b64) -> None`, `learn(timeout_s) -> str`.
- `devices/pc.py` — `PcDevice(device_id, name, aliases, conn: PcConnection, *, run, send_wol, suspend, timer_factory)`. Commands: `wake` (needs `mac`), and when `conn.local`: `shutdown` (`shutdown /s /t <delay>`; Windows force-closes apps when the delay is > 0, and the reply says so), `restart` (`shutdown /r /t <delay>`), `cancel_shutdown` (`shutdown /a`, also cancels a pending sleep), `sleep` (PowrProf `SetSuspendState(0, 0, 0)` via ctypes — never rundll32, which cannot pass arguments and hibernates — delayed by a ~5 s daemon timer). **Power interlock:** when `WAIFU_DISABLE_POWER` is set to anything but `0`, every real power call refuses; the test suite sets it in `tests/conftest.py`, which also refuses `shutdown`/`rundll32`/PowrProf in every test.
- `devices/registry.py` — `DeviceRegistry(devices)`: `get(id) -> Device | None`, `all() -> list[Device]`, `describe_all() -> list[dict]`, `execute(device_id, command, value=None) -> DeviceResult` which **never raises** (unknown device, unknown command, `DeviceError`, timeouts, and unexpected exceptions all become `ok=False` with an Indonesian message; unexpected ones are logged with traceback). `build_registry(config: AppConfig, *, dry_run=False) -> DeviceRegistry`: one blaster instance per IR host/Tuya id shared by every IR device on it; `dry_run` swaps every device for a `MockDevice` with the same id, name, aliases, and commands, keeping the real `kind` (IR commands = learned codes ∪ the device's declared `commands`, PC commands from its type and `local` flag). No network or subprocess call happens while building, in either mode.
- For `ir` devices, `DeviceConfig.commands` declares the commands the owner *intends* to learn (e.g. `on`, `off`, `temp_16`…`temp_30`). Real mode only exposes learned ones; `waifu check` lists declared-but-unlearned commands and `waifu learn-ir <id> --missing` walks through them.

### brain

- `brain/tools.py` — `build_tool_specs(registry) -> list[ToolSpec]` with exactly two tools:
  - `control_device` `{device: enum[device ids], command: string, value?: integer}` — description lists each device's commands so the model picks valid ones.
  - `list_devices` `{}` — returns current `describe_all()`.
  Schemas use `additionalProperties: false`, no numeric `minimum`/`maximum` (unsupported by strict tool use). `run_tool(registry, name, arguments) -> tuple[str, bool]` returns a compact JSON string (`ensure_ascii=False`) and `is_error`; unknown tools and invalid arguments are errors, never exceptions. `to_anthropic(spec)` adds `strict: True`; `to_ollama(spec)` returns `{"type": "function", "function": {...}}`.
- `brain/persona.py` — `build_system_prompt(persona_text, assistant_name, registry) -> str`: persona with `{name}` filled, plus a stable device list (id, name, aliases, commands — no live state). `format_user_turn(text, now) -> str` prefixes `[<Indonesian weekday>, DD-MM-YYYY HH:MM]` so the model knows the time without editing the system prompt. `load_persona(path) -> str`.
- `brain/session.py` — `SessionClock(idle_minutes, max_turns, clock=time.monotonic)`: `should_reset() -> bool`, `record_turn()`, `reset()`. Shared by both backends.
- `brain/claude.py` — `ClaudeBrain(config: BrainConfig, registry, system_prompt, *, client=None, clock=...)`. Uses `client.beta.messages.create` with model `config.claude.model`, `max_tokens`, `output_config={"effort": ...}`, adaptive thinking by omission, strict tools, top-level `cache_control={"type": "ephemeral"}`, and when `config.claude.fallbacks` is on `betas=["server-side-fallback-2026-07-01"]` + `fallbacks="default"`. Manual tool loop up to `max_tool_rounds`; all tool results of one round go back in a single user message. History is **append-only** within a session (preserved-thinking rule): always append the full `response.content`; never edit or trim earlier turns; when a session should end, start a new empty one. Before echoing a response that contains a `fallback` block, drop `thinking`/`redacted_thinking`/`tool_use` blocks that precede the last `fallback` block. A failed turn (API error) is rolled back by truncating to the length before the turn, then a short Indonesian apology is returned. `stop_reason == "refusal"` → apology, turn rolled back. `pause_turn` → resend. Text is the concatenation of `text` blocks of the final response.
- `brain/ollama_brain.py` — `OllamaBrain(config: BrainConfig, registry, system_prompt, *, client=None, clock=...)`. `ollama.Client(host).chat(model, messages, tools=[to_ollama...], think=config.ollama.think, keep_alive=...)`; tool calls from `response.message.tool_calls`; tool results appended as `{"role": "tool", "content": ..., "tool_name": ...}`. Same session, rollback, and apology rules. Strips any `<think>...</think>` text from the final reply.
- `brain/factory.py` — `build_brain(config: AppConfig, registry, *, persona_text=None) -> Brain` (persona read from `assistant.persona_file` when not given).

### voice

- `voice/audio.py` — `list_devices() -> str`, `resample(audio, src_rate, dst_rate) -> np.ndarray` (linear interpolation, float32), `MicStream(sample_rate, frame_ms, device=None)`: context manager; opens `sounddevice.InputStream` at the device's default rate and resamples to `sample_rate`; `frames()` yields fixed-size float32 frames; `pause()`/`resume()` drop audio while the assistant speaks (half-duplex, so she never hears herself). `Player(device=None)`: `play(audio, sample_rate)` blocking.
- `voice/segmenter.py` — `EnergySegmenter(sample_rate, vad: VadConfig, clock=time.monotonic)`: `feed(frame) -> Utterance | None`. Adaptive noise floor (slow EMA of non-speech frame dB); a frame is speech when its dB ≥ max(`threshold_db`, floor + `margin_db`). Keeps `pre_roll_ms` before onset, ends after `silence_ms` of non-speech, force-ends at `max_speech_s`, drops bursts shorter than `min_speech_ms`. `reset()` clears state.
- `voice/speaker.py` — `SpeakerVerifier(embedder: SpeakerEmbedder, voiceprint_path, threshold)`: `enrolled`, `enroll(samples, sample_rate)` (mean of L2-normalised embeddings, renormalised, saved with `np.save`), `score(audio, sr) -> float` (cosine), `verify(audio, sr) -> tuple[bool, float]`. `EcapaEmbedder(model_source, model_dir, device)`: lazy speechbrain `EncoderClassifier.from_hparams(..., run_opts={"device": ...}, local_strategy=LocalStrategy.COPY)` (Windows has no symlinks without admin), resamples to 16 kHz, returns a 1-D numpy embedding.
- `voice/stt.py` — `WhisperStt(config: SttConfig, assistant_name, *, model_factory=None)`: lazy `faster_whisper.WhisperModel(model, device, compute_type, download_root)`; before loading on Windows, register CUDA DLL folders from the `nvidia-*` pip packages and `torch/lib` with `os.add_dll_directory` when present. `transcribe` resamples to 16 kHz, runs with `vad_filter=True`, `condition_on_previous_text=False`, an `initial_prompt` containing the assistant name, auto language; if the detected language is not in `config.languages`, re-runs with `languages[0]`. Drops known Whisper hallucinations (e.g. "Terima kasih telah menonton", "ご視聴ありがとうございました", subtitle credits) and returns an empty transcript for them.
- `voice/tts.py` — `split_by_language(text, default="id") -> list[tuple[str, str]]`: runs of Japanese script (hiragana, katakana, CJK ideographs, Japanese punctuation, long-vowel mark) become `"ja"`, everything else the default; neighbouring punctuation/space stays with the current run; empty runs dropped. `clean_for_speech(text)` removes emoji, markdown, and `*actions*`. `EdgeTts(voice: EdgeVoiceConfig)` (async `edge_tts.Communicate(...).stream()` collected in a sync wrapper, MP3 decoded with `soundfile`). `GptSovitsTts(voice: GptSovitsVoiceConfig, *, http_get=...)` (GET `{url}/tts` with `text`, `text_lang`, `ref_audio_path`, `prompt_text`, `prompt_lang`, `speed_factor`, `media_type=wav`). `SpeechRouter(engines: dict[str, TextToSpeech], default_language)`: `synthesize(text) -> tuple[np.ndarray, int]` cleans, splits, synthesises each run with its language's engine (falling back to the default language's engine), resamples to one rate, joins with 120 ms of silence. `build_speech_router(config: TtsConfig)`.

### app

- `gate.py` — `WakeGate(wake_words, followup_seconds, clock=time.monotonic)`: `check(text) -> GateResult(accepted, command)`. Normalises (lowercase, strip punctuation), matches a wake word anywhere with fuzzy tolerance (difflib ratio ≥ 0.8 per word, so "syrene"/"cyrene" match; wake words containing Japanese script match as substrings because Japanese has no spaces), removes it from `command`. Inside the follow-up window any text is accepted unchanged. `open_followup()` after each spoken reply; `close()`.
- `app.py` — `Assistant(config, *, brain, stt, speech, player, gate, verifier=None, speaker_check=True, now=datetime.now)`: `handle_utterance(utt) -> str | None` (verify owner → transcribe → gate → brain → speak → open follow-up; returns the reply text or None when ignored; a bare wake word gets a short acknowledgement like "はい, Master?"), `handle_text(text) -> BrainReply`, `run(mic, segmenter)` (loop with mic paused while speaking; Ctrl+C exits cleanly). Logs each decision (rejected speaker with score, gate reject, transcript, tool calls).
- `cli.py` — `main(argv=None) -> int`, subcommands: `run`, `chat [--speak]`, `enroll [--samples 6 --seconds 4]` (prompts Indonesian phrases to read aloud), `speaker-test` (live scores vs threshold), `devices`, `device <id> <command> [value]`, `learn-ir <device_id> <command>` or `learn-ir <device_id> --missing`, `say <text>`, `mics`, `check` (config valid, CUDA available, Ollama reachable or API key present, voiceprint present, IR codes per device). Shared `--config` (default `config.yaml`) and `--dry-run`. `python -m waifu` works via `__main__.py`.

## Code Style

See `CLAUDE.md` → Code Conventions and Patterns. Dataclasses for value
objects, ABCs from the `base.py` files for contracts, constructor injection for
every vendor boundary, lazy imports for heavy libraries, `logging` not `print`.

## Testing Strategy

- `pytest`, one `tests/test_<package>_<module>.py` per module, run without network, GPU, mic, or speakers.
- Fakes at the vendor boundary: fake tinytuya objects, fake `broadlink` device, fake `subprocess.run`, fake Anthropic client returning SDK-shaped objects (`SimpleNamespace` with `.type`, `.content`, `.stop_reason`), fake ollama client, fake embedder, fake Whisper model, fake `sounddevice`.
- Pure logic (segmenter, gate, language split, cosine, session reset, fallback echo rule, append-only history) gets direct unit tests with synthetic inputs.
- Every task ends with the full suite green and `ruff check` + `ruff format --check` clean.
- Hardware/GPU verification is a separate manual checklist in `README.md`, not part of the suite.

## Boundaries

- Always: lazy-import heavy libraries; verify vendor APIs against installed source; keep spoken text free of markdown/emoji; keep Claude history append-only.
- Ask first: new dependencies, multi-GB downloads, changes to `base.py` contracts.
- Never: commit `config.yaml`, `data/`, `models/`, keys; power off the real PC from tests; let an exception escape `Brain.respond` or `DeviceRegistry.execute`.

## Success Criteria

1. `pytest` and `ruff` are clean on a machine with only the core dependencies.
2. `waifu chat --dry-run` with the Ollama or Claude backend turns "nyalakan lampu kamar dan AC 24 derajat" into `control_device` calls on the mock devices and a short spoken-style reply.
3. `waifu say "Halo Master, おかえりなさい!"` produces Indonesian then Japanese speech.
4. With the voice extra installed: `waifu check` reports CUDA and the Whisper/ECAPA libraries as importable (models load on first `run`/`enroll`); `waifu enroll` then `waifu speaker-test` separates the owner from another voice.
5. `waifu run` completes user stories 1–5 on real hardware (manual checklist).

## Open Questions

1. Wake-word spellings: Whisper may write "Cyrene" as "Sairin"/"Sirene"/キュレネ; the example config lists variants, tune after real use.
2. Samsung AC model: if it has Wi-Fi/SmartThings, a SmartThings backend could replace IR later (out of scope for v1).
3. Anime voice: GPT-SoVITS needs a reference clip the owner provides; RVC voice conversion for Indonesian is a later add-on.
