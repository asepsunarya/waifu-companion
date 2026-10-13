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

## Task 36: Tool providers and the game tools ✅

**Acceptance criteria:**
- [x] `build_tool_specs`/`run_tool` include providers, reject duplicate names, and turn a provider exception into an error result; both brains and `build_brain` pass providers through
- [x] `game_status` and `gacha_history` have strict-compatible schemas (game enum from config) and return the SPEC JSON; errors per game, `is_error` only when nothing worked
- [x] Persona section added; existing brain tests unchanged and green

**Dependencies:** 33, 35 · **Files:** `src/waifu/brain/tools.py`, `src/waifu/brain/claude.py`, `src/waifu/brain/ollama_brain.py`, `src/waifu/brain/factory.py`, `src/waifu/gacha/tools.py`, `config/persona.md` + tests · **Scope:** M

## Task 37: Reminder rules and state ✅

**Acceptance criteria:**
- [x] Stamina fires once inside the window and re-arms after leaving it; daily fires once per game day after `daily_time` when not done; weekly only on the last game day; expeditions once per finished batch
- [x] Problem notices once per game day per kind; check-in notice names claimed rewards and is None when nothing was claimed
- [x] State survives a reload (atomic JSON) and drops keys older than 14 days

**Dependencies:** 32, 33 · **Files:** `src/waifu/gacha/reminders.py`, `tests/test_gacha_reminders.py` · **Scope:** M

## Task 38: Watcher and spoken announcements ✅

**Acceptance criteria:**
- [x] `poll_once` turns statuses into one push and one announcement, claims check-in once per check-in day (retrying after a network error), and saves the state
- [x] `start`/`stop` run and end the thread promptly; a failing poll is logged and polling continues
- [x] `Assistant.announce` queues from any thread; `run` speaks queued text between utterances only (`Segmenter.busy`), with `followup=False`

**Dependencies:** 31, 37 · **Files:** `src/waifu/gacha/watcher.py`, `src/waifu/app.py`, `src/waifu/voice/segmenter.py`, `tests/test_gacha_watcher.py`, `tests/test_app.py`, `tests/test_voice_segmenter.py` · **Scope:** M

## Task 39: CLI, wiring, README ✅

**Acceptance criteria:**
- [x] `waifu gacha accounts|status|checkin|history` per SPEC with fakes in tests; `run` and `chat` get the game tools, `run` starts/stops the watcher with a cookie; `check` reports gacha and notify without network or secrets
- [x] README (Indonesian): cookie and Real-Time Notes setup, ntfy app and topic, config, voice examples, reminders, history and pity, privacy and terms, troubleshooting
- [x] Real run on the owner's PC: `waifu gacha history hsr --full` imports the HSR history

**Dependencies:** 31-38 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 40: Server routes for Live2D and interactions ✅

**Acceptance criteria:**
- [x] `/config.json` and `hello` carry engine, model URL, core URL, credit, interaction switches (token only in `/config.json`); `/model/<path>` serves only the Live2D model folder (traversal-protected) and `/live2d/...` only the configured Core, unmodified
- [x] Every request with a foreign `Host` gets 403; `POST /interact` checks Origin, JSON, size, token, kind, and rate (403/400/429), answers 202 and calls `on_interact`
- [x] Every response carries `Cross-Origin-Resource-Policy: same-origin`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`; `file_under` refuses UNC, drive, absolute and URL names before resolving anything
- [x] Existing VRM behaviour and tests unchanged, except the foreign-Host status, which changes from 421 to 403 (the three Host tests in `tests/test_avatar_server.py` now assert 403)

**Dependencies:** 40a · **Files:** `src/waifu/avatar/server.py`, `src/waifu/avatar/bus.py`, `tests/test_avatar_server.py`, `tests/test_avatar_bus.py` · **Scope:** M

## Task 41: InteractionHandler and Assistant hooks ✅

**Acceptance criteria:**
- [x] `handle` never blocks (enqueue + worker); switches, busy skip (a double-click too), cooldown counted from the end of the previous line, no immediate repeat, welcome-back limit honoured
- [x] `Assistant.busy`, `speak_reaction` (normal speak path), `listen_now` (follow-up window + listening state, nothing said; it waits for a reply or announcement that started after the busy check); the handler then says a listen line
- [x] Thread-safe start/stop; exceptions in speak are logged

**Dependencies:** 40a · **Files:** `src/waifu/avatar/interactions.py`, `src/waifu/app.py`, `tests/test_avatar_interactions.py`, `tests/test_app.py` · **Scope:** M

## Task 42: Pure interaction and Live2D logic ✅

**Acceptance criteria:**
- [x] `live2dMouth`, `live2dEmotionParams`, `mapToRange`, `cursorLook`, `classifyPointer` (double-click window 500 ms, Windows' default), `idlePhase`, `live2dHeadBox`, `live2dRegionAt` per SPEC
- [x] `node --test "tests/js/*.test.mjs"` covers each, including edge cases (no movement, tiny jitter, exact thresholds, a chest below a full-body model's head is body)

**Dependencies:** none · **Files:** `src/waifu/avatar/web/logic.js`, `tests/js/logic.test.mjs` · **Scope:** S

## Task 43: Live2D renderer and engine switch ✅

**Acceptance criteria:**
- [x] `index.html` boots from `/config.json`, loads the Core then `live2d.js` for live2d, `avatar.js` for vrm, then the event stream
- [x] `live2d.js` renders the model transparently in mascot mode with lip-sync, emotions, cursor look, thinking, bored, blink and physics, and exposes `hitTest`/`isOpaqueAt`; the head region is measured on the model (drawables that turn with `ParamAngleX/Y`, cut above the chin found with `ParamMouthOpenY`)
- [x] `index.html` has a Content-Security-Policy that keeps every fetch on Cyrene's own server
- [x] Verified in a real browser with the owner's model and Core when the Core is present (otherwise reported as pending), including: a click on her bust gives `body_touch`, never `head_pat` (with all drawables her bust is at about 18–24 % of the full bounds; from the halo to her feet, without the PFZ01/PFZ04 wings, at about 29–39 %), and a click on her hair or forehead gives `head_pat`. Offline check on the owner's moc3 geometry: head 0–16.5 % of the bounds, 0 of 417 chest vertices in it
  - Verified 2026-10-13 in the in-app browser with Cubism Core 06.00.0001 (SDK 5-r.5) and Cyrene20251105: Core 6 first crashed the engine's draw (`drawables.renderOrders` moved to `model.getRenderOrders()`), fixed by `logic.adaptCubismCore`; no console errors after. Read from the live parameters: cursor left/right/up/down gives AngleX -19/+22, AngleY +30/-30 and the eyes follow (and the face turns that way on screen); the six emotions reach their targets, and on this rig BrowAngle +1 lowers the inner brow ends (angry) and -1 raises them (sad), matching `live2dEmotionParams`; a 3 s speak track opens the mouth 0.45-0.9 with the form wide for i/e and round for u/o, closes at the end, and the happy face fades after its tail; thinking tilts the head (AngleZ 14) with the eyes up; listening raises the brows for the follow-up window; bored yawns 2 s in (mouth 0.9, eyes 0.45). Real clicks: head and forehead give `head_pat`, her bust and neck `body_touch`, empty space nothing, a double-click only `double_click`. Head box: hair top to the chin. Mascot mode draws on a transparent background; `framing: upper` was width-bound by her wings (as small as full) until `logic.live2dPlacement` let upper fill the window's height.

**Dependencies:** 42 · **Files:** `src/waifu/avatar/web/live2d.js`, `src/waifu/avatar/web/index.html`, `tests/test_avatar_web.py` · **Scope:** M

## Task 44: Shared input layer and VRM hooks ✅

**Acceptance criteria:**
- [x] `interaction.js` turns pointer input into head_pat / body_touch / double_click / drag per SPEC, sends `POST /interact` with the token, drives click-through and idle/welcome-back (the greeting only from the mascot's cursor feed and presses; never in the app window or when the feed resumes after a minimize)
- [x] `avatar.js` exposes cursor look, `hitTest`, `isOpaqueAt`, and the bored animation
- [x] Local expression feedback is immediate

**Dependencies:** 42 · **Files:** `src/waifu/avatar/web/interaction.js`, `src/waifu/avatar/web/avatar.js` · **Scope:** M

## Task 45: Electron preload, cursor feed, drag, click-through ✅

**Acceptance criteria:**
- [x] `preload.js` exposes exactly `onCursor`, `dragBy`, `setClickThrough` via `contextBridge`; sandbox and context isolation stay on
- [x] `main.js` polls the cursor every 33 ms while visible, marks the first report after a minimize `resumed`, validates IPC input, clamps drags to the work area, toggles `setIgnoreMouseEvents(flag, {forward: true})`
- [x] `lib.js` `relativeCursor`, `clampToWorkArea` and `cursorReport` covered by `node --test`

**Dependencies:** none · **Files:** `avatar-app/preload.js`, `avatar-app/main.js`, `avatar-app/lib.js`, `avatar-app/test/lib.test.js` · **Scope:** M

## Task 46: CLI wiring, check, README, example config ✅

**Acceptance criteria:**
- [x] `run` passes the Assistant-bound handler to the server; `waifu avatar` uses a printing handler; `check` reports the engine, the Live2D files (a UNC, drive, absolute or URL reference in model3.json counts as missing and is never resolved), and the Core
- [x] README (Indonesian): Live2D setup (SDK download, a Core copy command that finds the file in Explorer's nested extraction and stops with an error when it is missing, model folder), the model's rules and credit, mascot controls (drag, pat, poke, double-click, click-through), interaction settings
- [x] `config.example.yaml` documents `engine`, `live2d_core`, `credit`, `interactions`

**Dependencies:** 40-45 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 47a: Memory config ✅

**Acceptance criteria:**
- [x] `AppConfig.memory`: `enabled` (true), `path` (`data/memory.json`), `max_facts` (100, 1-500), `max_chars` (200, 20-1000); unknown keys rejected
- [x] `config.example.yaml` still validates

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** XS

## Task 47: Memory store ✅

**Acceptance criteria:**
- [x] `MemoryStore` add / replace / forget / clear / facts per SPEC: ids `m<n>` never reused, whitespace collapsed, limits and duplicates, Indonesian `MemoryError` messages
- [x] Atomic writes; a missing file reads as empty and is not created by reading; a corrupt file is renamed `<name>.corrupt-<stamp>` and never overwritten; re-read on every call, lock within the process
- [x] `looks_secret` catches the SPEC's words (also with -nya/-ku/-mu or a number attached), long mixed tokens, and Luhn-valid card numbers of 13-19 digits (also followed by an expiry or CVV), and lets ordinary notes (dates, phone-free sentences, short numbers, names like Sandi, token listrik, pin lokasi) through
- [x] `memory_prompt_section` matches the SPEC text exactly for empty and non-empty memories, with no square brackets

**Dependencies:** 47a · **Files:** `src/waifu/memory.py`, `tests/test_memory.py` · **Scope:** M

## Task 48: Memory tools ✅

**Acceptance criteria:**
- [x] `MemoryTools.specs()` gives strict-compatible `remember` and `forget` (`additionalProperties: false`, no numeric bounds) with the SPEC's guidance in the descriptions
- [x] `run` saves, replaces, and forgets through the store; errors (secret, too long, full, unknown id, bad arguments) are `is_error` JSON results; never raises
- [x] Works through `brain.tools.run_tool` and `build_tool_specs` beside the device and game tools (no duplicate names)

**Dependencies:** 47 · **Files:** `src/waifu/brain/memory_tools.py`, `tests/test_memory_tools.py` · **Scope:** S

## Task 49: Per-session system prompt ✅

**Acceptance criteria:**
- [x] `ClaudeBrain` and `OllamaBrain` accept a string or a callable `system_prompt`; a callable is read once at each session start (when its first turn begins, never while the brain is built) and the prompt never changes within a session (rollback of a first turn re-reads it; a rolled-back turn that saved or deleted a note ends the session; tools frozen for the brain's lifetime)
- [x] `build_brain(..., memory_section=...)` appends the section after the unchanged base prompt; a raising section logs and falls back to the SPEC sentence
- [x] Existing brain tests stay green; the request shape is unchanged apart from where `system` comes from

**Dependencies:** 47a · **Files:** `src/waifu/brain/claude.py`, `src/waifu/brain/ollama_brain.py`, `src/waifu/brain/factory.py`, `tests/test_brain_claude.py`, `tests/test_brain_ollama.py`, `tests/test_brain_factory.py` · **Scope:** M

## Task 50: CLI wiring, `waifu memory`, check, README ✅

**Acceptance criteria:**
- [x] `run` and `chat` give the brain `MemoryTools` and the memory section when `memory.enabled`; not when it is off
- [x] `waifu memory list|add|forget|clear [--yes]` per SPEC (with `memory.max_facts`/`max_chars` from the config); `check` reports the memory
- [x] README (Indonesian) section on Cyrene's memory: what she remembers, voice examples, `waifu memory`, privacy (local file; sent to Claude with the Claude backend), editing the file; `config.example.yaml` documents `memory`
- [x] No test reads or writes the repository's `data/memory.json`

**Dependencies:** 47, 48, 49 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 51a: Schedule and routines config ✅

**Acceptance criteria:**
- [x] `AppConfig.schedule` (`enabled`, `path`, `max_entries`, `late_grace_minutes`) and `AppConfig.routines` per SPEC; slug names, 1-20 steps, unknown keys rejected
- [x] A routine step naming an unknown device or a `pc` device is a config error

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 51: Schedule store and time rules ✅

**Acceptance criteria:**
- [x] `Entry`, actions, `parse_local_time`, `next_occurrence` (none, daily, weekdays, weekends; across month and year ends), `due` with the grace window
- [x] `ScheduleStore` add / cancel / fired / clear / entries per SPEC with the memory store's file rules (atomic, re-read, lock, corrupt file moved aside, ids never reused)
- [x] `add` refuses past times, more than 366 days ahead, a full store, bad reminder text, unknown repeat, with Indonesian messages
- [x] `add` accepts the current minute ("in one minute" landing after the minute changed); `skipped` moves a missed repeating entry to its first occurrence at or after `earliest`

**Dependencies:** 51a · **Files:** `src/waifu/schedule.py`, `tests/test_schedule.py` · **Scope:** M

## Task 52: Routines ✅

**Acceptance criteria:**
- [x] `run_routine` runs every step in order, continues after a failure, refuses `pc` devices at run time
- [x] `describe_routine` and `routine_problems` per SPEC

**Dependencies:** 51a · **Files:** `src/waifu/routines.py`, `tests/test_routines.py` · **Scope:** S

## Task 53: Scheduler thread ✅

**Acceptance criteria:**
- [x] `tick_once` fires due reminders (announce + push), device actions and routines (silent on success, announced and pushed on failure), then marks them fired; late entries are skipped per SPEC (a skipped reminder pushed as missed)
- [x] One failing entry never stops the others or the thread; `start`/`stop`/context manager like `GachaWatcher`; no real sleeping in tests
- [x] After days away, today's occurrence within the grace still fires (the stale one is pushed as missed, with its date); entries are handled one at a time from a fresh read, so a cancelled one never runs and one due during a slow routine still fires; a current-minute entry made after the last check fires with no grace

**Dependencies:** 51, 52 · **Files:** `src/waifu/scheduler.py`, `tests/test_scheduler.py` · **Scope:** M

## Task 54: Schedule tools ✅

**Acceptance criteria:**
- [x] Strict-compatible `schedule`, `list_schedules`, `cancel_schedule`, and (with routines) `run_routine`; exactly one action per `schedule` call, device actions validated with `resolve_command`, `pc` devices refused
- [x] Error results with Indonesian messages; `run` never raises; works beside the device, game, and memory tools
- [x] The `schedule` description and the PC refusal tell the model never to schedule turning off a plug that powers the PC, nor offer it instead of the PC

**Dependencies:** 51, 52 · **Files:** `src/waifu/brain/schedule_tools.py`, `tests/test_schedule_tools.py` · **Scope:** M

## Task 55: CLI wiring, `waifu schedule`, `waifu routine`, check, README ✅

**Acceptance criteria:**
- [x] `run` starts the scheduler with `assistant.announce` and the notifier; `chat` prints what fires; both give the brain the schedule tools when `schedule.enabled`
- [x] `waifu schedule list|cancel|clear [--yes]`, `waifu routine list|run <name>`; `check` reports the schedule file and every routine
- [x] README (Indonesian) and `config.example.yaml` (a commented `tidur` routine); no test touches the repository's `data/`
- [x] `--dry-run` (run and chat) uses a throwaway schedule in a temporary folder and never touches `schedule.path`; `chat` starts no scheduler while a `waifu run` holds the run lock

**Dependencies:** 51-54 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 56: Guard the PC's plug; tell missed reminders ✅

**Acceptance criteria:**
- [x] `DeviceConfig.powers_pc` (only `tuya_plug`/`mock`); a routine step on a marked plug is a config error; `DeviceRegistry.powers_pc` covers the PC and marked plugs, also in dry runs
- [x] Routines, the scheduler, and the schedule tools refuse a marked plug like the PC (never executed, never offered in the tool's device enum); the device line tells the model that switching it off turns the PC off, and the persona's confirmation rule names it
- [x] Missed one-shot reminders of a tick are told once, together; missed repeating ones are only pushed
- [x] `config.example.yaml` and the owner's `config.yaml` mark `colokan_pc`; README and SPEC updated

**Dependencies:** 51-55 · **Files:** `src/waifu/config.py`, `src/waifu/devices/registry.py`, `src/waifu/routines.py`, `src/waifu/scheduler.py`, `src/waifu/brain/schedule_tools.py`, `src/waifu/brain/tools.py`, `config/persona.md`, `config.example.yaml`, tests, README, SPEC · **Scope:** M

## Task 57a: Media and apps config, stronger test guards ✅

**Acceptance criteria:**
- [x] `DeviceConfig.type` accepts `media` and `apps`; `apps` required for `type: apps` (1-30, slug names) and refused elsewhere; targets limited to `steam://`, `com.epicgames.launcher://`, or absolute `.exe`/`.lnk`/`.url` paths
- [x] `tests/conftest.py` refuses `os.startfile` and `SendInput`/`keybd_event` through `ctypes.WinDLL` and `ctypes.windll`; `tests/test_safety_guard.py` proves it

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `tests/conftest.py`, `tests/test_safety_guard.py` · **Scope:** S

## Task 57: Media keys device ✅

**Acceptance criteria:**
- [x] `MediaKeys` commands and key sequences per SPEC (volume changes in percent, absolute volume, ranges, errors), Indonesian messages that say which key was pressed, state
- [x] `press_keys` builds correct `INPUT` structures for `SendInput` (checked against the Win32 layout with ctypes sizes, never sent in tests) and raises on a short send; the default sender sends every key (checked through a fake user32)

**Dependencies:** 57a · **Files:** `src/waifu/devices/media.py`, `tests/test_devices_media.py` · **Scope:** M

## Task 58: App launcher device ✅

**Acceptance criteria:**
- [x] `AppLauncher` exposes `open_<slug>` per app, starts only configured targets with the injected starter, refuses missing files, turns start failures into `DeviceError`
- [x] A `.exe` starts in its own folder; a refused administrator prompt is told as such
- [x] Never starts anything in tests

**Dependencies:** 57a · **Files:** `src/waifu/devices/apps.py`, `tests/test_devices_apps.py` · **Scope:** S

## Task 59: Wiring, check, README ✅

**Acceptance criteria:**
- [x] `build_registry` builds both types (and dry-run mocks keep kind and commands); `describe_commands` shows the media value ranges
- [x] Kind value ranges live in the registry and are checked by `execute`, the schedule tools, and `routine_problems`; schedules refuse `play_pause`/`mute` on media; the model is told volume values are percentages and that the toggles toggle
- [x] `waifu check` reports each app target (and where a `.url` shortcut points); README (Indonesian) section and commented `config.example.yaml` devices per SPEC

**Dependencies:** 57, 58 · **Files:** `src/waifu/devices/registry.py`, `src/waifu/brain/tools.py`, `src/waifu/brain/schedule_tools.py`, `src/waifu/routines.py`, `src/waifu/cli.py`, tests, `README.md`, `config.example.yaml` · **Scope:** M

## Task 60: Confirmation gate ✅

**Acceptance criteria:**
- [x] `is_yes` per SPEC: only a plain yes (starts with an affirmative word, then only affirmative and order words, "Master", and her names; never a question); punctuation, case, names, Japanese; thanks, other requests, reasons to wait, and doubt with a "ya" in them are not yes
- [x] `needs_confirmation` covers exactly the SPEC's commands on the PC and on a marked plug, after `resolve_command`
- [x] `ConfirmationGate` runs a gated command only in the next turn, on a yes said within the window, once; same-turn, later-turn, other-command, and stale yeses never count; the first refusal of a turn is its request and the others wait (two gated calls never deadlock or confirm together); `with_question` names the action, `discard_turn` drops a rolled-back turn's request, `awaiting_answer`; thread-safe

**Dependencies:** none · **Files:** `src/waifu/brain/confirm.py`, `tests/test_brain_confirm.py` · **Scope:** S

## Task 61: Gate the tool path ✅

**Acceptance criteria:**
- [x] `run_tool(..., confirmations=...)` refuses a gated `control_device` with `needs_confirmation` and `is_error`, and never calls the device
- [x] Both brains call `begin_turn` on every `respond` and pass the gate; `build_brain` always provides one, with her name and wake words; existing brain tests stay green
- [x] Both brains end a kept turn's reply with the gate's question (canned and empty replies too; Claude's history untouched), drop the request of a rolled-back turn, never speak a held call's message, and hold a session reset while a question waits for its answer
- [x] Persona and README (Indonesian) describe the confirmation (the code asks; the model calls without asking); `waifu device` stays ungated

**Dependencies:** 60 · **Files:** `src/waifu/brain/tools.py`, `src/waifu/brain/claude.py`, `src/waifu/brain/ollama_brain.py`, `src/waifu/brain/factory.py`, their tests, `config/persona.md`, `README.md` · **Scope:** M

## Task 62a: Vision config, screen-capture guards ✅

**Acceptance criteria:**
- [x] `AppConfig.vision` (`enabled` true, `max_side` 1280, 512-1568)
- [x] `tests/conftest.py` refuses `BitBlt`/`StretchBlt` (gdi32) and `PrintWindow` (user32) through `ctypes.WinDLL` and `ctypes.windll`; `tests/test_safety_guard.py` proves it

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `tests/conftest.py`, `tests/test_safety_guard.py` · **Scope:** S

## Task 62: Screen capture and PNG ✅

**Acceptance criteria:**
- [x] `encode_png` writes a valid RGB PNG (decoded back in tests: signature, chunk CRCs, IHDR, pixels) with adaptively filtered rows (all five filter types undone in tests; a smooth screen far smaller than unfiltered)
- [x] `capture_screen` per SPEC: aspect kept, never upscaled, handles released on every path, errors as `OSError`; tested only with fakes (never a real capture)
- [x] `capture_screen` is per-monitor DPI aware while it captures, so a scaled display is copied whole in physical pixels; the thread's own awareness is restored last

**Dependencies:** 62a · **Files:** `src/waifu/screen.py`, `tests/test_screen.py` · **Scope:** M

## Task 63: Screen tool and image results ✅

**Acceptance criteria:**
- [x] `ScreenTools` with the keyword gate, one capture per turn, errors as results, `take_images`
- [x] Both brains call providers' `begin_turn`; `ClaudeBrain` sends image blocks in the `tool_result` and keeps them in history; Ollama unaffected
- [x] `run`/`chat` wiring (Claude only), `check` line, README section
- [x] A screenshot over 5 MB as base64 is taken once more, smaller (an error result when still too large); `ClaudeBrain` starts a new session once its history holds 16 MB of images
- [x] `-v` never logs a screenshot or the conversation (the `anthropic` logger stays at INFO); the description says never to save what she sees unless asked, that her avatar may be in the picture, and what an all-black screenshot means

**Dependencies:** 62 · **Files:** `src/waifu/brain/screen_tools.py`, `src/waifu/brain/base.py`, `src/waifu/brain/claude.py`, `src/waifu/brain/ollama_brain.py`, `src/waifu/cli.py`, tests, `README.md`, `config.example.yaml` · **Scope:** M

## Task 64: Stoppable speech ✅

**Acceptance criteria:**
- [x] `Player.play` returns whether it finished; `Player.stop` stops a playing clip from another thread, never blocks, and reports whether anything played; only the playing thread calls PortAudio (`stop` only asks, `play` stops and closes the stream itself)
- [x] `Assistant.interrupt` and `speaking` per SPEC; an interrupted `speak` still resumes the mic, resets the segmenter, and opens the follow-up window after a reply; announcements queued meanwhile wait until Master is heard or the window has passed

**Dependencies:** none · **Files:** `src/waifu/voice/audio.py`, `tests/test_voice_audio.py`, `src/waifu/app.py`, `tests/test_app.py` · **Scope:** S

## Task 65: Double-click cuts her off ✅

**Acceptance criteria:**
- [x] `InteractionHandler(interrupt=...)`: a double-click while she speaks interrupts at once and then only opens listening; otherwise unchanged
- [x] Both renderers end the speak track on a non-speaking state, and its emotion after the short tail (`logic.emotionEndAfterState`); `run` wires `assistant.interrupt`; README section

**Dependencies:** 64 · **Files:** `src/waifu/avatar/interactions.py`, `tests/test_avatar_interactions.py`, `src/waifu/avatar/web/live2d.js`, `src/waifu/avatar/web/avatar.js`, `src/waifu/avatar/web/logic.js`, `tests/js/logic.test.mjs`, `src/waifu/cli.py`, `tests/test_cli.py`, `README.md` · **Scope:** M
