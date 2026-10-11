# Tasks: waifu-companion v1

Contracts and module surfaces are in `SPEC.md` → Module contracts. Each task:
failing test first, then code, then its tests + ruff clean. Test files are
named `tests/test_<package>_<module>.py`.

## Task 1: Config models and module contracts ✅

**Acceptance criteria:**
- [x] `load_config` validates `config.example.yaml` and rejects unknown keys, missing connection blocks, duplicate ids
- [x] Contracts in `devices/base.py`, `brain/base.py`, `voice/base.py`

**Files:** `src/waifu/config.py`, `src/waifu/*/base.py`, `tests/test_config.py`, `config.example.yaml`, `config/persona.md`, `pyproject.toml`

## Task 2: Device registry, mock device, IR code store, IR device ✅

**Acceptance criteria:**
- [x] `DeviceRegistry.execute` never raises: unknown device, unknown command, `DeviceError`, and unexpected exceptions return `ok=False` with an Indonesian message
- [x] `IrCodeStore` round-trips codes through an atomic JSON write and lists commands sorted; invalid command names rejected
- [x] `IrDevice` maps `command` + `value` to `command_value` when learned and lists learned commands on an unknown one

**Dependencies:** 1 · **Files:** `devices/registry.py` (class only), `devices/mock.py`, `devices/ir_codes.py`, `devices/ir_device.py` + tests · **Scope:** M

## Task 3: Tuya light, plug, and IR blaster ✅

**Acceptance criteria:**
- [x] `TuyaLight` on/off/toggle/brightness (1–100, rejects out of range) call the right tinytuya methods; state updated
- [x] Tuya error payloads become `DeviceError`; connection created lazily once with a short timeout
- [x] `TuyaIrBlaster.send/learn` use `tinytuya.Contrib.IRRemoteControlDevice`, learn times out cleanly

**Dependencies:** 1 · **Files:** `devices/tuya.py` + test · **Scope:** S

## Task 4: Broadlink IR blaster ✅

**Acceptance criteria:**
- [x] `send` authenticates lazily once and sends decoded base64
- [x] `learn` polls `check_data` until data (returns base64) or raises `DeviceError` on timeout
- [x] One reconnect attempt on a socket/auth error

**Dependencies:** 1 · **Files:** `devices/broadlink_ir.py` + test · **Scope:** S

## Task 5: PC power device ✅

**Acceptance criteria:**
- [x] Local PC: `shutdown`/`restart` schedule with the configured delay, `cancel_shutdown` runs `shutdown /a`, `sleep` is deferred on a timer
- [x] `wake` sends a magic packet to `mac`/`broadcast`; missing MAC is a `DeviceError`
- [x] Non-local PC exposes only `wake`; tests never run real commands

**Dependencies:** 1 · **Files:** `devices/pc.py` + test · **Scope:** S

## Task 6: Registry factory from config ✅

**Acceptance criteria:**
- [x] `build_registry` creates the right class per `type`, sharing one blaster per IR host / Tuya device id
- [x] `dry_run=True` returns `MockDevice`s with identical ids, names, aliases, and commands
- [x] The example config builds in dry-run without network access

**Dependencies:** 2, 3, 4, 5 · **Files:** `devices/registry.py` (factory) + test · **Scope:** S

## Task 7: Audio I/O and energy segmenter ✅

**Acceptance criteria:**
- [x] Segmenter emits one utterance for synthetic speech-in-silence with pre-roll, ignores short clicks, force-splits at `max_speech_s`, adapts to a raised noise floor
- [x] `resample` preserves duration and dtype; `MicStream.pause()` drops frames
- [x] No real audio device is opened in tests

**Dependencies:** 1 · **Files:** `voice/segmenter.py`, `voice/audio.py` + tests · **Scope:** M

## Task 8: Speaker verification ✅

**Acceptance criteria:**
- [x] Enrollment stores a normalised mean voiceprint; `verify` accepts the owner and rejects another voice with a fake embedder
- [x] Not enrolled → clear error from `verify`; voiceprint survives reload
- [x] `EcapaEmbedder` imports speechbrain lazily and uses `LocalStrategy.COPY`

**Dependencies:** 1 · **Files:** `voice/speaker.py` + test · **Scope:** S

## Task 9: Whisper speech-to-text ✅

**Acceptance criteria:**
- [x] Transcribes via an injected model; disallowed detected language triggers a re-run with the fallback language
- [x] Known hallucination phrases yield an empty transcript
- [x] CUDA DLL directories registered before the lazy model load (no-op when absent)

**Dependencies:** 1 · **Files:** `voice/stt.py` + test · **Scope:** S

## Task 10: Bilingual text-to-speech router ✅

**Acceptance criteria:**
- [x] `split_by_language("Halo Master, おかえりなさい! Mau apa?")` → id / ja / id runs
- [x] `clean_for_speech` strips emoji, markdown, `*actions*`
- [x] Router uses each language's engine, falls back to the default, resamples, joins with silence; Edge and GPT-SoVITS engines tested with fakes

**Dependencies:** 1 · **Files:** `voice/tts.py` + test · **Scope:** M

## Task 11: Tool specs, persona prompt, session clock ✅

**Acceptance criteria:**
- [x] Two tool specs with strict-compatible schemas; device enum matches the registry
- [x] `run_tool` returns JSON results and error flags without raising for bad input
- [x] System prompt fills the name and lists devices without live state; user turn carries an Indonesian timestamp; `SessionClock` resets on idle or turn cap

**Dependencies:** 2 · **Files:** `brain/tools.py`, `brain/persona.py`, `brain/session.py` + tests · **Scope:** M

## Task 12: Claude backend ✅

**Acceptance criteria:**
- [x] Tool loop with a fake client: two `tool_use` blocks → two device calls → results in one user message → final text
- [x] Request carries model, effort, strict tools, cache_control, and fallbacks beta when enabled; history append-only across turns; session reset starts empty
- [x] API errors and refusals roll the turn back and return an apology; `fallback` echo rule applied

**Dependencies:** 11 · **Files:** `brain/claude.py` + test · **Scope:** M

## Task 13: Ollama backend and brain factory ✅

**Acceptance criteria:**
- [x] Tool loop with a fake client appends assistant + tool messages and returns the final text without `<think>` content
- [x] Connection errors roll back and apologise; session reset works
- [x] `build_brain` returns the backend selected in config

**Dependencies:** 11 · **Files:** `brain/ollama_brain.py`, `brain/factory.py` + tests · **Scope:** M

## Task 14: Wake gate and Assistant loop ✅

**Acceptance criteria:**
- [x] Gate matches wake words fuzzily anywhere, strips them, and accepts anything during the follow-up window
- [x] `handle_utterance` ignores non-owner speech and non-addressed speech, answers addressed speech, speaks, and opens follow-up
- [x] `run` pauses the mic while speaking and exits cleanly on KeyboardInterrupt

**Dependencies:** 6, 7–10, 13 · **Files:** `gate.py`, `app.py` + tests · **Scope:** M

## Task 15: CLI and `python -m waifu` ✅

**Acceptance criteria:**
- [x] All subcommands in SPEC parse; `devices --dry-run`, `device ... --dry-run`, `chat --dry-run` (with fake brain in tests) work end-to-end
- [x] Missing/invalid config prints the `ConfigError` message and exits 2
- [x] `check` reports each dependency's status without crashing when the voice extra is absent

**Dependencies:** 14 · **Files:** `cli.py`, `__main__.py` + test · **Scope:** M

## Task 16: README setup guide and hardware checklist ✅

**Acceptance criteria:**
- [x] Indonesian README: install (core + voice), Ollama/Claude setup, Tuya wizard, learning IR codes, enrolling voice, BIOS/smart-plug PC power trick, troubleshooting
- [x] Manual hardware checklist mapping to user stories 1–5

**Dependencies:** 15 · **Files:** `README.md` · **Scope:** S
