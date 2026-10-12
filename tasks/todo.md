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

## Task 17: Single-instance lock ✅

**Acceptance criteria:**
- [x] A second `InstanceLock` on the same file (from another process) cannot acquire while the first holds it; release frees it
- [x] A killed holder leaves no stale lock (OS lock, not a PID file)
- [x] Parent folder is created; works as a context manager

**Dependencies:** none · **Files:** `src/waifu/single_instance.py`, `tests/test_single_instance.py` · **Scope:** S

## Task 18: Autostart Run-key manager ✅

**Acceptance criteria:**
- [x] `build_command` quotes paths with spaces and round-trips through Windows command-line parsing
- [x] `enable`/`disable`/`status` work against an injected fake Run key; `status` flags a stale command
- [x] `WindowsRunKey` only touches `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; no test writes the real registry

**Dependencies:** none · **Files:** `src/waifu/autostart.py`, `tests/test_autostart.py` · **Scope:** S

## Task 19: Preload Whisper and speaker models ✅

**Acceptance criteria:**
- [x] `WhisperStt.preload()` loads the model once (same lazy path as `transcribe`)
- [x] `SpeakerVerifier.preload()` loads the embedder model and the voiceprint once
- [x] Preloading twice does not load twice

**Dependencies:** none · **Files:** `src/waifu/voice/stt.py`, `src/waifu/voice/speaker.py` + tests · **Scope:** S

## Task 20: CLI autostart, run options, log file, README ✅

**Acceptance criteria:**
- [x] `waifu autostart enable|disable|status` per SPEC, including the voiceprint/config refusal and `--force`
- [x] `run` takes the instance lock first, honours `--startup-delay`, preloads models, then prints/announces ready (`--announce`)
- [x] Global `--log-file` writes a rotating UTF-8 log; README (Indonesian) documents autostart, how to stop/disable it, and the log
- [x] Every error that ends a command reaches the log file at ERROR (traceback only with `-v`); a refused `run` logs a WARNING
- [x] `--announce` leaves the follow-up window closed; `enroll` and `speaker-test` refuse (exit 2) while the run lock is held
- [x] `autostart status` checks the lock and log in the registered project folder, and from another folder points there instead of calling the command stale
- [x] README names the Task Manager entry ("Windows Command Processor") and says the log holds transcripts (Privasi)

**Dependencies:** 17, 18, 19 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md` · **Scope:** M

## Task 21: RVC HTTP server script ✅

**Acceptance criteria:**
- [x] `GET /health` and `POST /convert` behave per SPEC with an injected fake converter (200/400/413/500 paths)
- [x] Applio is imported only inside `load_applio_converter`; the module imports without Applio
- [x] Binds to the configured host only; query parameters validated (pitch int -24..24, rates 0..1)
- [x] A short body is a 400 and a stalled client cannot block the server (socket timeout), both tested
- [x] An index path Applio would rewrite ("trained" → "added") to a missing file is refused before Applio loads; a legacy trained_/added_ pair is kept

**Dependencies:** 21a · **Files:** `tools/rvc_server.py`, `tests/test_rvc_server.py` · **Scope:** S

## Task 22: RVC client and server-process manager ✅

**Acceptance criteria:**
- [x] `RvcClient.convert` round-trips audio through a fake HTTP layer with the configured parameters; HTTP/timeouts become `RvcError`
- [x] `RvcServerProcess.ensure_running` reuses a healthy server, else validates paths, spawns with the right argv/cwd/log, waits for health, and fails clearly on exit/timeout
- [x] `stop()` terminates only a child it started; no test spawns the real server (the only real child is a throwaway sleeper in the real-job test)
- [x] On Windows the spawned server is put in a kill-on-close job, so it dies with Cyrene even when her console window is closed; `stop()` closes the job
- [x] A server on the port is reused only when `/health` reports the configured model (else `RvcError`); a server answering after the own child exited is reused, not owned
- [x] Starting is serialized across commands by a lock file under `data/`; a second command waits for the first command's server
- [x] `tts.rvc.host` must be an IPv4 loopback address; the child gets `PYTHONIOENCODING=utf-8` and `PYTHONUNBUFFERED=1`; the convert timeout scales with the clip (5 s + 0.5 × length, ≤ 30 s)

**Dependencies:** 21 · **Files:** `src/waifu/voice/rvc.py`, `tests/test_voice_rvc.py` · **Scope:** M

## Task 23: SpeechRouter voice conversion with fallback ✅

**Acceptance criteria:**
- [x] With a converter, the joined audio is converted once and returned at the converter's rate
- [x] A failing converter yields the unconverted audio and a logged warning (not one per call during a failure streak)
- [x] `build_speech_router(config, converter=...)` passes it through; existing behaviour unchanged without one
- [x] After an unreachable converter, replies skip conversion for 60 s, then retry; an HTTP error from a running server causes no pause

**Dependencies:** 22 · **Files:** `src/waifu/voice/tts.py`, `tests/test_voice_tts.py` · **Scope:** S

## Task 24: CLI wiring, README, example config ✅

**Acceptance criteria:**
- [x] `run`, `say`, `chat --speak` start/stop the RVC server when enabled (run: inside the lock, before ready); `say --raw` skips it
- [x] `check` reports RVC status without spawning anything, including the served model (✗ when it is not `tts.rvc.model`) and a non-loopback host
- [x] `say` warns when `tts.rvc.model` is set but the anime voice is off
- [x] README (Indonesian) explains the voice, Applio setup, training commands, the corpus terms/credit, and troubleshooting; `config.example.yaml` documents `tts.rvc`

**Dependencies:** 23 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 25: Emotion tags in replies ✅

**Acceptance criteria:**
- [x] `split_emotion` maps Indonesian and English tags (case/space-insensitive) to the six emotions, removes every emotion tag, leaves other brackets alone, defaults to neutral
- [x] `config/persona.md` asks for exactly one leading Indonesian tag per reply (also on the text after a tool call, the only text spoken) and lists them
- [x] No tag can reach TTS or printed chat output (covered again in task 30)

**Dependencies:** none · **Files:** `src/waifu/avatar/emotion.py`, `tests/test_avatar_emotion.py`, `config/persona.md` · **Scope:** S

## Task 26: Viseme track from audio ✅

**Acceptance criteria:**
- [x] Frame count is `ceil(duration*fps)`; silence gives all zeros; weights per frame sum to at most the frame's openness and stay within 0..1
- [x] A low tone favours ou/oh, a mid band aa, a high band ee/ih (synthetic signals)
- [x] Attack is faster than release (a burst opens within ~2 frames and closes over several)

**Dependencies:** none · **Files:** `src/waifu/avatar/lipsync.py`, `tests/test_avatar_lipsync.py` · **Scope:** S

## Task 27: Event bus and loopback server with SSE ✅

**Acceptance criteria:**
- [x] Subscribers get hello + last state first, then published events; a slow subscriber never blocks `publish` (oldest dropped)
- [x] Server routes per SPEC: index, static files with traversal protection, `/model.vrm`, SSE with pings and clean disconnect, 405 for non-GET, port-in-use `AvatarError`
- [x] `stop()` ends open SSE responses and frees the port (tests on 127.0.0.1 port 0)

**Dependencies:** none · **Files:** `src/waifu/avatar/bus.py`, `src/waifu/avatar/server.py`, `tests/test_avatar_bus.py`, `tests/test_avatar_server.py` · **Scope:** M

## Task 28: Viewer page ✅

**Acceptance criteria:**
- [x] `logic.js` functions per SPEC pass `node --test "tests/js/*.test.mjs"` (Node 21+ takes files or globs, not folders)
- [x] The page loads the VRM through the import map (every static import resolves to a vendored file: `tests/test_avatar_web.py`), stands in a relaxed pose, blinks, breathes, follows state/emotion/speak events, and reconnects the event stream
- [x] Verified in a real browser against the real VRM (screenshot, no console errors), transparent background in mascot mode, credit line visible — re-checked 2026-10-12 on the final code: real Electron mascot loaded every module, the VRM and /events; emotions and visemes changed in the demo; mascot page html/body rgba(0,0,0,0), alpha WebGL context, credit shown

**Dependencies:** none (protocol from SPEC) · **Files:** `src/waifu/avatar/web/index.html`, `src/waifu/avatar/web/logic.js`, `src/waifu/avatar/web/avatar.js`, `tests/js/logic.test.mjs` · **Scope:** M

## Task 29: Window launcher and Electron shell ✅

**Acceptance criteria:**
- [x] `AvatarWindow` builds the exact Electron/Edge argv per mode, puts the child in a kill-on-close job, explains a missing Electron/Edge, and closes only what it started
- [x] `avatar-app/lib.js` `parseArgs`/`cornerPosition` pass `node --test "avatar-app/test/*.test.js"`; `main.js` creates the transparent frameless window per SPEC with sandboxed web preferences and blocked navigation
- [x] No test spawns Electron or Edge

**Dependencies:** none · **Files:** `src/waifu/avatar/window.py`, `tests/test_avatar_window.py`, `avatar-app/lib.js`, `avatar-app/main.js`, `avatar-app/test/lib.test.js` · **Scope:** M

## Task 30: Integration, CLI, README, example config ✅

**Acceptance criteria:**
- [x] `Assistant` publishes thinking → speak (with the reply's emotion and viseme track) → listening, strips tags before speaking, and survives avatar errors
- [x] `waifu avatar [--demo]`, and `run`/`say`/`chat --speak` start and stop server + window when enabled (run: inside the lock before ready); `check` reports the avatar without spawning
- [x] README (Indonesian) covers setup (model download, Electron install), use, mascot controls, credit/terms; `config.example.yaml` documents `avatar`

**Dependencies:** 25-29 · **Files:** `src/waifu/app.py`, `src/waifu/cli.py`, `tests/test_app.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 30a: Gacha and notify config, genshin dependency ✅

**Acceptance criteria:**
- [x] `gacha` and `notify` sections validate per SPEC (cookie keys checked by name, `SecretStr`, HH:MM `daily_time`, unique games, https ntfy server, 16-64 char topic when enabled)
- [x] A bad cookie or topic is reported without echoing its value
- [x] `genshin==1.7.31` (`>=1.7.31,<2`) in the core dependencies; `config.example.yaml` documents both sections (disabled)

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `pyproject.toml`, `config.example.yaml` · **Scope:** S

## Task 31: Push notifications and the no-internet test guard ✅

**Acceptance criteria:**
- [x] `NtfyNotifier.send` posts the JSON body to the server root and returns True on 2xx; HTTP errors, timeouts, and `OSError` return False with a warning that does not contain the topic
- [x] `waifu notify-test` sends the test message and prints ✓/✗ with hints; disabled notify explains how to enable it
- [x] `tests/conftest.py` refuses urllib and aiohttp requests to non-loopback hosts (covered in `test_safety_guard.py`); the full suite still passes

**Dependencies:** 30a · **Files:** `src/waifu/notify.py`, `tests/test_notify.py`, `tests/conftest.py`, `tests/test_safety_guard.py`, `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 32: Game facts and server-time helpers ✅

**Acceptance criteria:**
- [x] Each game lists its banners in display order with hard pity, 50/50 flag, and endpoint (HSR 21/22 → `getLdGachaLog`); standard pools per SPEC
- [x] `game_day` changes at 04:00 and `checkin_day` at 00:00 server time, for a local time in WIB (UTC+7) and across midnight
- [x] `reminder_due` and `is_last_day_of_week` match the reset rules, including a `daily_time` after midnight

**Dependencies:** none · **Files:** `src/waifu/gacha/__init__.py`, `src/waifu/gacha/games.py`, `tests/test_gacha_games.py` · **Scope:** S

## Task 33: HoYoLAB client ✅

**Acceptance criteria:**
- [x] `status` maps real genshin.py note models (built from sample payloads) of all three games to `GameStatus`, with `checked_in` from the reward info and None when only that call fails
- [x] `checkin` returns the reward, or `claimed=False` on `AlreadyClaimed`; `authkey` calls `fetch_authkey` for the right uid
- [x] Each genshin.py error, a timeout, and a missing cookie become the right `GachaError` kind; no message contains the cookie
- [x] uids come from config, else the highest-level account (looked up once)

**Dependencies:** 32 · **Files:** `src/waifu/gacha/hoyolab.py`, `tests/test_gacha_hoyolab.py` · **Scope:** M

## Task 34: Authkey from the game cache and the history API ✅

**Acceptance criteria:**
- [x] `find_cached_gacha_url` follows the game log to the newest `data_2` holding a history URL (fixture folders) and returns its base and decoded authkey; no log/no URL → None; a locked file → `GachaError("locked")`
- [x] `GachaLogApi.fetch` pages with `end_id`, stops at `stop_at_id`/empty page/`max_pages`, sends the SPEC query, and maps -100/-101/"auth key time out" → authkey, -110 → rate_limit, transport errors → network
- [x] The authkey never appears in logs or error messages

**Dependencies:** 32 · **Files:** `src/waifu/gacha/history.py`, `tests/test_gacha_history.py` · **Scope:** S

## Task 35: History store, stats, report ✅

**Acceptance criteria:**
- [x] `GachaStore` merges by id, returns only new records, and writes atomically per account
- [x] `banner_stats` gives pity, totals, the last five top-rarity items with pulls, `won_5050`, and `guaranteed` (a guaranteed win is neither won nor lost), honouring `off_banner`
- [x] `GachaHistory.report` refreshes incrementally, falls back to the cookie authkey, then to saved data with a problem message; `full` reads every page; an incomplete first import says so

**Dependencies:** 34 · **Files:** `src/waifu/gacha/history.py`, `tests/test_gacha_history.py` · **Scope:** M

## Task 36: Tool providers and the game tools

**Acceptance criteria:**
- [ ] `build_tool_specs`/`run_tool` include providers, reject duplicate names, and turn a provider exception into an error result; both brains and `build_brain` pass providers through
- [ ] `game_status` and `gacha_history` have strict-compatible schemas (game enum from config) and return the SPEC JSON; errors per game, `is_error` only when nothing worked
- [ ] Persona section added; existing brain tests unchanged and green

**Dependencies:** 33, 35 · **Files:** `src/waifu/brain/tools.py`, `src/waifu/brain/claude.py`, `src/waifu/brain/ollama_brain.py`, `src/waifu/brain/factory.py`, `src/waifu/gacha/tools.py`, `config/persona.md` + tests · **Scope:** M

## Task 37: Reminder rules and state

**Acceptance criteria:**
- [ ] Stamina fires once inside the window and re-arms after leaving it; daily fires once per game day after `daily_time` when not done; weekly only on the last game day; expeditions once per finished batch
- [ ] Problem notices once per game day per kind; check-in notice names claimed rewards and is None when nothing was claimed
- [ ] State survives a reload (atomic JSON) and drops keys older than 14 days

**Dependencies:** 32, 33 · **Files:** `src/waifu/gacha/reminders.py`, `tests/test_gacha_reminders.py` · **Scope:** M

## Task 38: Watcher and spoken announcements

**Acceptance criteria:**
- [ ] `poll_once` turns statuses into one push and one announcement, claims check-in once per check-in day (retrying after a network error), and saves the state
- [ ] `start`/`stop` run and end the thread promptly; a failing poll is logged and polling continues
- [ ] `Assistant.announce` queues from any thread; `run` speaks queued text between utterances only (`Segmenter.busy`), with `followup=False`

**Dependencies:** 31, 37 · **Files:** `src/waifu/gacha/watcher.py`, `src/waifu/app.py`, `src/waifu/voice/segmenter.py`, `tests/test_gacha_watcher.py`, `tests/test_app.py`, `tests/test_voice_segmenter.py` · **Scope:** M

## Task 39: CLI, wiring, README

**Acceptance criteria:**
- [ ] `waifu gacha accounts|status|checkin|history` per SPEC with fakes in tests; `run` and `chat` get the game tools, `run` starts/stops the watcher with a cookie; `check` reports gacha and notify without network or secrets
- [ ] README (Indonesian): cookie and Real-Time Notes setup, ntfy app and topic, config, voice examples, reminders, history and pity, privacy and terms, troubleshooting
- [ ] Real run on the owner's PC: `waifu gacha history hsr --full` imports the HSR history

**Dependencies:** 31-38 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M
