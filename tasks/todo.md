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

## Task 66a: Briefing config ✅

**Acceptance criteria:**
- [x] `AppConfig.briefing` per SPEC (`time` before `until`; latitude and longitude both or neither, within range)

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** XS

## Task 66: Weather client ✅

**Acceptance criteria:**
- [x] `WeatherClient.today()` builds the SPEC's requests, parses current/daily/hourly data into `Weather`, geocodes a city once, and maps WMO codes to Indonesian
- [x] Every network or format problem, and a missing location, is a speakable `WeatherError`; tests use a fake fetch only
- [x] One `today()` has 8 s for the city lookup and the forecast together (each request's timeout is the time left; none starts with under 1 s left); with coordinates the city still names the place

**Dependencies:** 66a · **Files:** `src/waifu/weather.py`, `tests/test_weather.py` · **Scope:** S

## Task 67: Briefing tool and timer ✅

**Acceptance criteria:**
- [x] `daily_briefing` returns the SPEC's JSON, filling problem fields instead of failing
- [x] `BriefingTimer` fires once per day inside the window, never again after a restart (state written first), and moves a corrupt state aside

**Dependencies:** 66a · **Files:** `src/waifu/briefing.py`, `tests/test_briefing.py` · **Scope:** M

## Task 68: Wiring, `waifu briefing`, check, README ✅

**Acceptance criteria:**
- [x] `Assistant.ask_aloud` handled between utterances like announcements, spoken as a reply, `on_reply` called with the text on a thread of its own
- [x] A request never follows announcements on the same frame and waits while a follow-up window is open; with `required_tool`, a turn without a successful call of it (the brain's apology) is neither said nor passed on
- [x] `run` wires the tools and the timer (push when `briefing.push` and a notifier; `required_tool="daily_briefing"`); `chat` the tools only; `waifu briefing` (exit 1 when the weather or the schedule fails) and `check` per SPEC
- [x] README (Indonesian) and `config.example.yaml`; no test touches the internet or `data/`

**Dependencies:** 66, 67 · **Files:** `src/waifu/app.py`, `tests/test_app.py`, `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 69a: Owner call setting ✅

**Acceptance criteria:**
- [x] `assistant.owner_call` (default "Master", plain short word) and `{owner}` filled in the system prompt; the committed persona uses it

**Dependencies:** none · **Files:** `src/waifu/config.py`, `src/waifu/brain/persona.py`, `src/waifu/brain/factory.py`, `config/persona.md`, tests · **Scope:** XS

## Task 69: Use the owner call everywhere ✅

**Acceptance criteria:**
- [x] Every hardcoded spoken/pushed/printed Indonesian sentence addressing the owner uses the injected `owner_call` (default "Master"), capitalised where it opens a sentence; avatar default lines use `{owner}`; tool errors Ollama may read out use it too
- [x] `is_yes` and the screen look-word gate skip the owner_call words; CLI wires `config.assistant.owner_call` everywhere
- [x] `check` warns about reaction lines that still say "Master" to an owner called otherwise
- [x] Tests: the default unchanged everywhere, and one non-default word through each path

**Dependencies:** 69a · **Files:** across `src/waifu/` and tests · **Scope:** M

## Task 70a: Usage config ✅

**Acceptance criteria:**
- [x] `AppConfig.usage` per SPEC (budget and rate positive when set; prices non-negative with all four keys)

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** XS

## Task 70: Usage ledger ✅

**Acceptance criteria:**
- [x] `TokenUsage.from_usage` reads the four `usage` fields (missing or None = 0); `cost_usd` per SPEC with the fallback-model estimate flag
- [x] `UsageLedger` records per local day and model, summarises a day and a month, and gives each budget notice once per month; store file rules as memory/schedule
- [x] `UsageLedger` refuses (ConfigError) a `default_model` without prices; a month never counts the same month of another year

**Dependencies:** 70a · **Files:** `src/waifu/usage.py`, `tests/test_usage.py` · **Scope:** M

## Task 71: Brain hook and usage tool ✅

**Acceptance criteria:**
- [x] `ClaudeBrain(on_usage=...)` called after every `messages.create` (tool rounds and refusals too) with `response.model` and the tokens; its exceptions are logged; requests unchanged
- [x] After a server-side fallback, `on_usage` hears every attempt in `usage.iterations` (the declined one too) with its own model and tokens
- [x] `UsageTools.api_usage {period}` per SPEC

**Dependencies:** 70 · **Files:** `src/waifu/brain/claude.py`, `tests/test_brain_claude.py`, `src/waifu/brain/factory.py`, `tests/test_brain_factory.py`, `src/waifu/brain/usage_tools.py`, `tests/test_usage_tools.py` · **Scope:** S

## Task 72: Wiring, `waifu usage`, check, README ✅

**Acceptance criteria:**
- [x] `run`/`chat` (Claude) record usage and offer the tool; `run` announces and pushes budget notices once
- [x] A budget notice waits until the follow-up window after the reply that crossed the line has closed (`Assistant.announce(..., wait_for_followup=True)`); `chat` leaves the notices to a running `waifu run`
- [x] `waifu usage [--month YYYY-MM]`, `check` line, README (Indonesian), `config.example.yaml`; no test touches `data/` or calls Claude

**Dependencies:** 71 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `src/waifu/app.py`, `tests/test_app.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 73a: Phone config ✅

**Acceptance criteria:**
- [x] `AppConfig.phone` (`enabled` false, `port` 9883, `host` none or a `*.ts.net` name, `state_path` `data/phone.json`)

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** XS

## Task 73: Pairing and devices ✅

**Acceptance criteria:**
- [x] Pairing codes (6 digits, 10 minutes, hashed, 5 tries then burned) and devices (32-byte token, SHA-256 stored, name, times; list, revoke) per SPEC, shared through `phone.state_path` with the store file rules

**Dependencies:** 73a · **Files:** `src/waifu/phone/auth.py`, `tests/test_phone_auth.py` · **Scope:** M

## Task 74: Phone server ✅

**Acceptance criteria:**
- [x] Routes, Host and Origin checks, the device cookie on every route but the page shell and pairing, size and concurrency limits, `PhoneBackend` per SPEC; avatar files and the model served with the same protections as the avatar server

**Dependencies:** 73 · **Files:** `src/waifu/phone/server.py`, `tests/test_phone_server.py` (shared helpers in `avatar/server.py` only if extracted without changing its behaviour) · **Scope:** L

## Task 75: Phone page ✅

**Acceptance criteria:**
- [x] Full-screen avatar, chat strip, text box, hold-to-talk (16 kHz mono WAV), playback with the mouth track, pairing screen, PWA files
- [x] Pure helpers covered by `node --test`; checked in a browser at phone size against a fake server

**Dependencies:** 73a · **Files:** `src/waifu/avatar/web/phone/*`, `tests/js/phone*.test.mjs` · **Scope:** L

## Task 76: Assistant, wiring, `waifu phone`, README ✅

**Acceptance criteria:**
- [x] `Assistant.reply_for_phone` under the turn lock, not spoken on the PC, with WAV and visemes; STT for phone audio without speaker verification or wake word
- [x] `run` starts the server when enabled; `waifu phone setup|pair|devices|forget`; `check`; README (Tailscale on PC and Android, HTTPS certificates, `tailscale serve`, pairing, installing the page)

**Dependencies:** 74, 75 · **Files:** `src/waifu/app.py`, `src/waifu/cli.py`, tests, `README.md`, `config.example.yaml` · **Scope:** L

## Task 77a: Phone camera config ✅

**Acceptance criteria:**
- [x] `PhoneConfig.camera` (default true)

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** XS

## Task 77: Photos in the Claude brain ✅

**Acceptance criteria:**
- [x] `TurnImages` (put/take/clear, thread-safe); `ClaudeBrain(..., turn_images=...)` puts a waiting photo before the turn's text with the camera note ("inilah yang {owner} tunjukkan"), plain string user messages otherwise (byte-identical requests)
- [x] `_image_bytes` counts images in tool results and in user messages; the session ends at 10 MiB of images (waiting one turn for a question's answer) and at 20 MiB even while a question awaits, whose yes then runs nothing; rolled-back turns drop them
- [x] `CameraTurn` (begin/end/active); `ScreenTools(..., camera_turn=...)`: in a camera turn only `layar`, `layarku`, `screen`, `screenshot`, `monitor` open the screen gate

**Dependencies:** 77a · **Files:** `src/waifu/brain/attachments.py`, `src/waifu/brain/claude.py`, `src/waifu/brain/screen_tools.py`, `tests/test_brain_attachments.py`, `tests/test_brain_claude.py`, `tests/test_screen_tools.py` · **Scope:** M

## Task 78: Photos on the phone server ✅

**Acceptance criteria:**
- [x] `/chat` with `image` and `camera`, `/talk` as JSON `{audio, camera, image}`, limits chosen from the Content-Type, photo checks (camera on, strict base64, 3.5 MB, SOI, `jpeg_size` within 16..max_side), anything sent through the camera refused while it is off, a non-boolean `camera` refused, `/config.json` camera fields, `PhoneBackend.chat(text, image, *, camera)`; photos never logged

**Dependencies:** 77a · **Files:** `src/waifu/phone/server.py`, `src/waifu/phone/jpeg.py`, `tests/test_phone_server.py`, `tests/test_phone_jpeg.py` · **Scope:** M

## Task 79: The viewfinder on the phone page ✅

**Acceptance criteria:**
- [x] Camera button, viewfinder (back camera, flip, close), a frame with each chat or talk while open (drawn before the recording stops; send and talk disabled until it is sent), `"camera": true` with every message sent while open (also without a photo), thumbnail in the owner's bubble, camera off on close/hide/pagehide/2 minutes after opening, the last photo, or the last talk press, a camera turned off on the PC noticed and hidden, Indonesian errors
- [x] `phone/camera.js` helpers covered by `node --test`; checked in a browser at phone size with a stubbed camera

**Dependencies:** 77a · **Files:** `src/waifu/avatar/web/phone/*`, `tests/js/phone_camera.test.mjs`, `tests/js/phone_ui.test.mjs` · **Scope:** M

## Task 80: Assistant, wiring, check, README ✅

**Acceptance criteria:**
- [x] `reply_for_phone(text, image, *, camera)` and `PhoneAnswers.chat(text, image, *, camera)` put the photo and mark the camera turn inside the turn lock and clear both after (never for voice or text-chat turns); `run` shares one `TurnImages` with the brain, the Assistant, and the server, and one `CameraTurn` with the screen tool and the Assistant, only when the camera is offered
- [x] `check` camera lines; README camera section; `config.example.yaml`

**Dependencies:** 77, 78, 79 · **Files:** `src/waifu/app.py`, `src/waifu/cli.py`, tests, `README.md`, `config.example.yaml` · **Scope:** M

## Task 81: HSR data contract ✅

**Acceptance criteria:**
- [x] `gacha/starrail.py`: `ENDGAME_MODES` and the frozen dataclasses per SPEC (no genshin import)
- [x] `gacha/hsr_tools.py`: `HsrSource` Protocol, tool names, `HsrTools(source, *, now)`, the complete static strict specs, argument validation, the unknown-name error; spec tests (byte-identical, strict, no live data, description rules)

**Dependencies:** none · **Files:** `src/waifu/gacha/starrail.py`, `src/waifu/gacha/hsr_tools.py`, `tests/test_gacha_hsr_tools.py` · **Scope:** S

## Task 82: HoYoLAB side ✅

**Acceptance criteria:**
- [x] `chronicle_private`, the `now` anchor, `hsr_characters`, `hsr_endgame` (per-mode errors, at most 2 requests at once), `hsr_calendar` per SPEC; never `update_settings`, `raw`, or `previous`; no secret or uid in messages or logs
- [x] Readers `read_character`, `read_endgame`, `read_calendar` (anchored calendar times) tested with real genshin.py models

**Dependencies:** 81 · **Files:** `src/waifu/gacha/hoyolab.py`, `src/waifu/gacha/starrail.py` (readers), `tests/test_gacha_hoyolab.py`, `tests/test_gacha_starrail.py` · **Scope:** M

## Task 83: Tool results and cache ✅

**Acceptance criteria:**
- [x] Roster rows, detail matching (NFKD, casefold, substring, close names), endgame and calendar JSON with local times and hours left, caps and size budgets, partial errors
- [x] The TTL cache (successes only, per-mode reuse, lock not held during fetches)

**Dependencies:** 81 · **Files:** `src/waifu/gacha/hsr_tools.py`, `tests/test_gacha_hsr_tools.py` · **Scope:** M

## Task 84: Wiring, CLI, persona, README ✅

**Acceptance criteria:**
- [x] `run`/`chat` add `HsrTools` when the gacha buddy is on and `hsr` is in its games; `waifu gacha hsr characters|endgame|events`
- [x] `config/persona.md` names the three tools; README section (Indonesian)

**Dependencies:** 81 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config/persona.md`, `README.md` · **Scope:** M

## Task 85: Mascot shell and page through fullscreen games ✅

**Acceptance criteria:**
- [x] Occlusion tracking off, per-domain WebGL blocking off, background throttling off; page reloads after a GPU/renderer process is gone (rate-limited); a page without WebGL reloaded while Electron has WebGL, else the mascot exits with code 1 for a fresh one; re-placed (back to her own spot once it is on a display again) and re-pinned after display changes (at once, 1.5 s after the last one, on show/restore); `Cyrene mascot:` diagnostics on stdout; main.js tested against a fake `electron`
- [x] Page: WebGL context loss recovered by a reload; `webgl-unavailable` reported when a renderer gets no WebGL; visibility reported; 30 fps cap in mascot mode; `node --test` coverage of the pure helpers
- [x] Phone page: a lost WebGL context redraws her on a fresh canvas without reloading (renderer `destroy()`, Cubism Core reused)

**Dependencies:** none · **Files:** `avatar-app/main.js`, `avatar-app/lib.js`, `avatar-app/preload.js`, `avatar-app/test/*`, `src/waifu/avatar/web/*.js`, `src/waifu/avatar/web/index.html`, `src/waifu/avatar/web/phone/phone.js`, `tests/js/*` · **Scope:** M

## Task 86: Mascot supervision and diagnostics in Python ✅

**Acceptance criteria:**
- [x] Electron's output piped and logged (`Cyrene mascot:` at INFO/WARNING, the rest at DEBUG); a crashed mascot reopened after 3 s, at most 3 times in 10 minutes; exit code 0 and `close()` respected; the reader and supervisor are daemon threads (tested)
- [x] README troubleshooting "Cyrene hilang saat main game", including how to bring her back once the automatic recovery gave up

**Dependencies:** none · **Files:** `src/waifu/avatar/window.py`, `tests/test_avatar_window.py`, `README.md` · **Scope:** M

## Task 87a: PC health config and values ✅

**Acceptance criteria:**
- [x] `AppConfig.pc_health` (`enabled`, `notices`, `poll_seconds`, `disk_warn_gb`, `gpu_warn_c`, `gpu_warn_minutes`) with ranges; `Drive`, `GpuStatus`, `PcSnapshot` frozen dataclasses in `pc_health.py`

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `src/waifu/pc_health.py` (values only) · **Scope:** XS

## Task 87: PC health readers ✅

**Acceptance criteria:**
- [x] `CpuLoad`, `memory()`, `fixed_drives()`, `uptime_s()`, `gpu_status()`, `read_snapshot()` per SPEC through injected collaborators; unreadable parts are None and logged once; no new dependency

**Dependencies:** 87a · **Files:** `src/waifu/pc_health.py`, `tests/test_pc_health.py` · **Scope:** M

## Task 88: The tool and the watcher ✅

**Acceptance criteria:**
- [x] `PcTools` (strict static `pc_health {}`, rounded compact JSON, None parts left out)
- [x] `PcWatcher` (disk once per drive per day, GPU streak once per hour, announce + notifier, failures logged, prompt stop)

**Dependencies:** 87a · **Files:** `src/waifu/brain/pc_tools.py`, `src/waifu/pc_watch.py`, `tests/test_pc_tools.py`, `tests/test_pc_watch.py` · **Scope:** M

## Task 89: Wiring, `waifu pc`, check, README ✅

**Acceptance criteria:**
- [x] `run`/`chat` add the tool when enabled; `run` starts and stops the watcher; `waifu pc`; check lines; README section "Kesehatan PC"; `config.example.yaml`

**Dependencies:** 87, 88 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** M

## Task 90a: Web search and offers config, offer values ✅

**Acceptance criteria:**
- [x] `AppConfig.web_search`, `AppConfig.free_offers`, `UsageConfig.web_search_per_1000_usd` with defaults and ranges per SPEC; `GameGiveaway` and `AiOffer` frozen dataclasses in `offers/values.py`

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `src/waifu/offers/__init__.py`, `src/waifu/offers/values.py`, `tests/test_offers_values.py` · **Scope:** S

## Task 90: Web search in the brain ✅

**Acceptance criteria:**
- [x] The static `web_search_20260209` server tool when enabled with Claude; server-tool blocks kept as returned; `pause_turn` continued; the fallback echo rule for unpaired `server_tool_use`; the web-data section in the system prompt
- [x] `TokenUsage.web_searches` and their price in the ledger and `waifu usage`

**Dependencies:** 90a · **Files:** `src/waifu/brain/claude.py`, `src/waifu/brain/factory.py`, `src/waifu/brain/persona.py`, `src/waifu/usage.py`, their tests · **Scope:** M

## Task 91: Free games ✅

**Acceptance criteria:**
- [x] Epic free-now and free-soon games with exact times and store links; GamerPower games per platform with dates only, Epic duplicates skipped; one failing source never hides the other

**Dependencies:** 90a · **Files:** `src/waifu/offers/games.py`, `tests/test_offers_games.py` · **Scope:** M

## Task 92: AI offers scan and store ✅

**Acceptance criteria:**
- [x] One Claude conversation with only web search and the strict `report_offers` tool; pause_turn, validation, errors, usage reported; ids and dedupe
- [x] `OffersStore` (store file rules; announced games, AI offers with expiry, last scan)

**Dependencies:** 90a · **Files:** `src/waifu/offers/ai_scan.py`, `src/waifu/offers/store.py`, `tests/test_offers_ai_scan.py`, `tests/test_offers_store.py` · **Scope:** M

## Task 93: Tools, watcher, CLI, README ✅

**Acceptance criteria:**
- [x] `free_offers` and `send_offer_link` tools; `OffersWatcher`; the notifier's `click`; `run`/`chat` wiring; `waifu offers [games|ai] [--scan]`; check lines; README; `config.example.yaml`

**Dependencies:** 90, 91, 92 · **Files:** `src/waifu/brain/offers_tools.py`, `src/waifu/offers/watcher.py`, `src/waifu/notify.py`, `src/waifu/cli.py`, their tests, `README.md`, `config.example.yaml` · **Scope:** L

## Task 97a: Redeem codes config and values ✅

**Acceptance criteria:**
- [x] `AppConfig.redeem_codes` (`enabled`, `poll_hours`, `auto_redeem`, `notices`, `state_path`) with ranges; `RedeemCode`, `RedeemResult`, `REDEEM_OUTCOMES` in `gacha/codes.py`

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `src/waifu/gacha/codes.py` (values only), `tests/test_gacha_codes.py` · **Scope:** XS

## Task 97: Code source and store ✅

**Acceptance criteria:**
- [x] `read_codes` per SPEC (game mapping, OK only, format check, per-game failures); `CodeStore` (file rules, new codes, unredeemed newest first and only codes the latest read listed or Master named, `add_named`, outcomes, announced, forgetting)

**Dependencies:** 97a · **Files:** `src/waifu/gacha/codes.py`, `src/waifu/gacha/code_store.py`, `tests/test_gacha_codes.py`, `tests/test_gacha_code_store.py` · **Scope:** M

## Task 98: Redemption on HoYoLAB ✅

**Acceptance criteria:**
- [x] `HoyolabClient.redeem_codes` (one at a time, 6 s apart, cooldown retry, at most 10, per-attempt timeouts, error → outcome mapping, a refused cookie, unreachable HoYoLAB, rate-limiting, or a captcha stops the rest, `on_result`, no secrets in logs)

**Dependencies:** 97a · **Files:** `src/waifu/gacha/hoyolab.py`, `tests/test_gacha_hoyolab.py` · **Scope:** M

## Task 99: Tools, watcher, CLI, README ✅

**Acceptance criteria:**
- [x] `redeem_codes_list` and `redeem_codes` tools; `CodeWatcher` (announce or auto-redeem, never repeat, keeps reading with notices off, nothing more after `stop()`); recording after redeeming is best effort; `run`/`chat` wiring; `waifu gacha codes [game] [--redeem]`; check line; README; `config.example.yaml`

**Dependencies:** 97, 98 · **Files:** `src/waifu/gacha/code_tools.py`, `src/waifu/gacha/code_watcher.py`, `src/waifu/cli.py`, their tests, `README.md`, `config.example.yaml` · **Scope:** L

## Task 103a: To-do config and value ✅

**Acceptance criteria:**
- [x] `AppConfig.todo` (`enabled`, `path`, `max_tasks`, `max_chars`, `keep_done_days`) with ranges; the `Task` frozen dataclass in `todo.py`

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `src/waifu/todo.py` (value only), `tests/test_todo.py` · **Scope:** XS

## Task 103: The task store ✅

**Acceptance criteria:**
- [x] `TodoStore` per SPEC (file rules, ids never reused, ordering, edits with the keep/clear due sentinel, secret refusal, limits, done tasks forgotten after `keep_done_days`)

**Dependencies:** 103a · **Files:** `src/waifu/todo.py`, `tests/test_todo.py` · **Scope:** M

## Task 104: Tools and the briefing ✅

**Acceptance criteria:**
- [x] The six strict tools per SPEC (a null argument counts as left out, so only `clear_due` true removes a due date; a confirmation that sets a due date names its weekday and date); `daily_briefing` gains the tasks and `BRIEFING_PROMPT` mentions them

**Dependencies:** 103a · **Files:** `src/waifu/brain/todo_tools.py`, `src/waifu/briefing.py`, `tests/test_todo_tools.py`, `tests/test_briefing.py` · **Scope:** M

## Task 105: Wiring, CLI, README ✅

**Acceptance criteria:**
- [x] `run`/`chat` wiring; `waifu todo [add|done|remove|list]`; check line; README; `config.example.yaml`
- [x] With every tool on (Claude, the games, routines), one request stays within Anthropic's strict tool limits (20 strict tools, 24 optional and 16 union-typed parameters): `to_anthropic` makes only the tools that require an argument strict

**Dependencies:** 103, 104 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml`; for the strict limit `src/waifu/brain/tools.py` and the strict assertions of the tool tests · **Scope:** S

## Task 108a: Music config ✅

**Acceptance criteria:**
- [x] `AppConfig.music` (`enabled`, `service`: `youtube` | `youtube_music`)

**Dependencies:** none · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** XS

## Task 108: YouTube links and the tool ✅

**Acceptance criteria:**
- [x] `youtube_link`, `search_link`, `open_link` per SPEC (strict host and id rules, canonical links, service)
- [x] `MusicTools.play_youtube` with the play-word gate (a play verb, or a music word not about looking it up, the media keys, or the volume), one page per turn, `url` only with `links` (any other link opens the search page), Indonesian errors

**Dependencies:** 108a · **Files:** `src/waifu/music.py`, `src/waifu/brain/music_tools.py`, `tests/test_music.py`, `tests/test_music_tools.py` · **Scope:** M

## Task 109: Wiring, CLI, README ✅

**Acceptance criteria:**
- [x] `run`/`chat` wiring (`links` only for Claude with web search); `waifu youtube`; check line; README; `config.example.yaml`; the strict tool limits still hold

**Dependencies:** 108 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `README.md`, `config.example.yaml` · **Scope:** S

## Task 110: Small fixes (v1.21.1) ✅

**Acceptance criteria:**
- [x] A: the reply's text joins a cited answer's pieces with no doubled space where both carry whitespace (their line breaks stay when the junction holds any), strips the reply's ends, and keeps the text and line breaks inside a block (`src/waifu/brain/claude.py`, `tests/test_brain_claude.py`)
- [x] B: `api_usage` gives `web_searches`, `web_search_cost_usd`, and `web_search_cost_idr` only when the period has web searches, and its description says `cost_usd` already includes them; the fees use the current search price and are never more than `cost_usd`; the ledger's `web_search_usd` is the price of one search (`src/waifu/brain/usage_tools.py`, `tests/test_usage_tools.py`, `src/waifu/usage.py`, `tests/test_usage.py`)
- [x] C: the CLI holds the `genshin.models` logger at ERROR, even with `-v`, so genshin.py's "Unknown <enum>: ..., please report to developer" notices (HSR's `DoubleRewardActStatusFinish`) stay off the console and the log file (`src/waifu/cli.py`, `tests/test_cli.py`)
- [x] D: `looks_secret` refuses a secret word only when its value follows in the next four words, not counting the words that tie it to Master ("milik Master adalah") (`:` or `=` with its value even past them, 4 or more digits or 3 after `cvv`, digit groups split by a space or dash joined, a 6-character mix of letters with digits or symbols; rupiah amounts, "Rp100rb" too, are no value; after "token listrik/PLN" only 20 digits are), so "ganti PIN ATM", "beli token listrik", and "token listrik 1300 VA" are kept as notes and tasks (`src/waifu/memory.py`, `tests/test_memory.py`, `tests/test_todo.py`)
- [x] E: GamerPower titles lose their Giveaway suffix with its Key word ("Key Giveaway", "Steam Key Giveaway") and store brackets as often as the title ends with one ("Dwarven Realms (Steam) Key Giveaway" → "Dwarven Realms"), a game's own Key, other brackets, and words such as "DLC" stay ("Skeleton Key (Steam) Giveaway" → "Skeleton Key"), and a title left empty keeps GamerPower's own (`src/waifu/offers/games.py`, `tests/test_offers_games.py`)

**Dependencies:** none · **Files:** the files above, `SPEC.md`, `README.md` · **Scope:** S

## Task 111a: Genshin and ZZZ data contract ✅

**Acceptance criteria:**
- [x] SPEC "Genshin and ZZZ data (v1.23)" and the v1.16 sentences it revises; tasks 111a-115 in the plan and here
- [x] `is_over`/`is_running` moved from `gacha/starrail.py` to the new `gacha/game_data.py` in a refactor commit of their own, behaviour unchanged
- [x] `gacha/game_data.py`: `GAME_MODES` (`starrail.ENDGAME_MODES` is its `hsr` entry), `MODE_GAMES`, the frozen `EndgameStage`, `Endgame`, `Banner`, `GameEvent`, `ChallengeWindow`, `GameCalendar`, `worn_sets` with tests, and the `GameDataSource` Protocol of the 11 source methods
- [x] `gacha/genshin_impact.py` and `gacha/zenless.py` with the per-game frozen dataclasses only (no readers, no genshin import)
- [x] `gacha/hoyolab.py`: `CHRONICLE_PRIVATE` per game (the HSR text unchanged), `_chronicle(request, game)`, the 9-mode `_ENDGAME_REQUESTS`, `_endgame(game, modes, read)` with `hsr_endgame` delegating; the HSR tests unchanged and green; tests for the Genshin and ZZZ requests and their chronicle_private message; no `genshin_*`/`zzz_*` methods yet
- [x] Full suite and ruff clean

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/gacha/game_data.py`, `src/waifu/gacha/genshin_impact.py`, `src/waifu/gacha/zenless.py`, `src/waifu/gacha/starrail.py`, `src/waifu/gacha/hoyolab.py`, `src/waifu/gacha/hsr_tools.py` and `src/waifu/cli.py` (imports only), `tests/test_gacha_game_data.py`, `tests/test_gacha_hoyolab.py` · **Scope:** S

## Task 111: Genshin source ✅

**Acceptance criteria:**
- [x] `read_character`, `read_detail`, `read_endgame(mode, model, fetched_at)` (Spiral Abyss, Imaginarium Theater, Stygian Onslaught), and `read_calendar(model, fetched_at)` per SPEC, tested with real genshin.py models from deep-copied dicts (a fixture fills genshin.py's `CHARACTER_NAMES["en-us"]`): collab rarity, the weapon type's int fallback, weapon and artifact stats, `worn_sets` without `active`, the 4 highest Abyss floors and "0-0", the Theater and Stygian season by window with their fallbacks, calendar timestamps as aware UTC, ended items dropped, upcoming kept, windows from the Abyss and Theater details
- [x] `genshin_characters`, `genshin_character_details(refs)` (one request with `characters=`), `genshin_endgame`, `genshin_calendar`, right after `hsr_calendar`: the explicit uid, the Theater with `need_detail=False`, never `raw`/`previous`/`lang`; a failing mode does not hide the others; chronicle_private names Genshin while the notes keep notes_off; `fetched_at` read before the calendar request; parse failures `other`; account and no_cookie; no cookie, uid, or nickname in results or logs; `update_settings` never called

**Dependencies:** 111a · **Files:** `src/waifu/gacha/genshin_impact.py`, `src/waifu/gacha/hoyolab.py`, `tests/test_gacha_genshin_impact.py` (111a left a values test there), `tests/test_gacha_hoyolab.py` (fake methods after `get_starrail_event_calendar`, tests after the HSR block; 111a's `EndgameRequests` fake and request-table tests follow the HSR block) · **Scope:** M

## Task 112: ZZZ source ✅

**Acceptance criteria:**
- [x] `read_agent`, `read_detail`, `read_endgame(mode, model)` (Shiyu Defense V1 and V2, Deadly Assault), and `read_calendar(events, gacha, fetched_at)` per SPEC, tested with real genshin.py models from deep-copied dicts: element and specialty title-cased, no W-Engine, discs and sets, an unplayed V2 and an unplayed Deadly Assault, monochromes and COMPLETED, S/A split, W-Engine banners without names; the nickname and avatar never reach a dataclass
- [x] `zzz_characters`, `zzz_character_details(refs)` (one `get_zzz_agent_info(ref, uid=uid)` per ref, never a list, at most 2 in flight), `zzz_endgame`, `zzz_calendar` (2 requests), right before `_run`: the explicit uid, never `raw`/`previous`/`lang`; a failing mode does not hide the others; chronicle_private names ZZZ; a Shiyu `hadal_ver` it does not know is `other`; account and no_cookie; no cookie, uid, or nickname (Shiyu V2's `nick_name` too) in results or logs; `update_settings` never called

**Dependencies:** 111a · **Files:** `src/waifu/gacha/zenless.py`, `src/waifu/gacha/hoyolab.py`, `tests/test_gacha_zenless.py` (111a left a values test there), `tests/test_gacha_hoyolab.py` (fake methods after `update_settings`, tests at the end of the file) · **Scope:** M

## Task 113: Game data tools, wiring, personas ✅

**Acceptance criteria:**
- [x] `git mv` `gacha/hsr_tools.py` to `gacha/game_data_tools.py`, `HsrTools` to `GameDataTools(source, games, *, now)`: static non-strict specs with the `game` and `mode` enums from `gacha.games`, byte-identical across calls and without live data; the game-resolution rule and its errors before any source call; HSR results unchanged with `games=[hsr]`; the Genshin and ZZZ roster, detail (the shown refs only, cached per ref, no detail call without a match, no ref in the JSON), endgame, and calendar JSON with their caps and size budgets; cache keys per game
- [x] `git mv` `tests/test_gacha_hsr_tools.py` to `tests/test_gacha_game_data_tools.py` with a game-keyed `FakeSource`
- [x] `cli._game_tools` adds `GameDataTools` after `GachaTools`; the wiring tests and the strict-limit test (`game_events`); both personas' new line and `tests/test_brain_persona.py`

**Dependencies:** 111a · **Files:** `src/waifu/gacha/game_data_tools.py`, `tests/test_gacha_game_data_tools.py`, `src/waifu/cli.py` (the import and `_game_tools` only), `tests/test_cli.py` (wiring and strict-limit tests), `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** M

## Task 114: `waifu gacha genshin|zzz` ✅

**Acceptance criteria:**
- [x] `waifu gacha genshin|zzz characters|endgame|events` through one `_gacha_data(args)` registered with `set_defaults(game=...)`, `DATA_VIEWS`, the "data akun game" help; `waifu gacha hsr ...` prints as before
- [x] The SPEC's lines: roster lines, one endgame line per mode with one failing, banners (a ZZZ W-Engine banner as `Banner W-Engine`), events and windows; an empty roster warns and exits 1; a refusal prints FAIL and exits 1; works with the gacha buddy off; `-v` never shows the uid

**Dependencies:** 111a (builds on the contract with a fake HoyolabClient; a real run needs 111 and 112) · **Files:** `src/waifu/cli.py` (parser, handler, printers), `tests/test_cli.py` (the view tests) · **Scope:** M

## Task 115: Integration and docs ✅

**Acceptance criteria:**
- [x] 111-114 merged; full suite and ruff clean; `waifu chat --dry-run` builds 28 tools with 16 strict
- [x] CHANGELOG v1.23 (the renamed tools, a custom persona to update), README (Genshin and ZZZ section, the Battle Chronicle setting, the `gacha` command list, the manual checklist items), SPEC touch-ups

**Dependencies:** 111, 112, 113, 114 · **Files:** `CHANGELOG.md`, `README.md`, `SPEC.md`, merge fixes · **Scope:** S

## Task 116a: tepira finance contract ✅

**Acceptance criteria:**
- [x] SPEC "Keuangan dari tepira (v1.24)" (stories, non-goals, privacy, the API as PR #21 has it, config, values, client, gate, tools, persona, CLI, check line, logging rule); tasks 116a-118 in the plan and here
- [x] `TepiraConfig` as `AppConfig.tepira` (`enabled`, https host-only `url`, `token` SecretStr in the `tpcy_` form, `writes`, `default_wallet` up to 40 characters; `enabled` needs `token`, `writes` needs `default_wallet`), with tests: no token in errors, repr, or JSON; the example block valid once filled in
- [x] `config.example.yaml` block in Indonesian, off, the token line commented
- [x] `finance/__init__.py` and `finance/values.py`: `WIB`, `CATEGORY_LABELS`, the API limits, the tool names, `FinanceError(kind, message)` and its messages, `FinanceData`, `RecordedExpense`, `UndoneExpense`, the `FinanceSource` Protocol, `spoken_rupiah`, `expense_question`, with tests; no HTTP code
- [x] Full suite and ruff clean

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/config.py`, `tests/test_config.py`, `config.example.yaml`, `src/waifu/finance/__init__.py`, `src/waifu/finance/values.py`, `tests/test_finance_values.py` · **Scope:** S

## Task 116: tepira client ✅

**Acceptance criteria:**
- [x] `TepiraClient(http, url, token, *, clock)` implements `FinanceSource` per SPEC: routes, query parameters, and JSON body exactly as the API takes them; `Authorization: Bearer`, `Accept`, `User-Agent`; the token in no URL; no redirect followed; arguments tepira would refuse raise `ValueError` before sending
- [x] The 8 s budget with each attempt's timeout the time left; one GET retry only after a connect failure with at least 1 s left; POST and DELETE never retried
- [x] The answer table (GET, POST, DELETE by status and transport error) and its messages; the shapes checked, lists cut to tepira's caps, `"ok"` dropped, bodies over 32 KiB unreadable
- [x] Tests over httpx `MockTransport` (a `FakeTepira` as `tests/test_offers_games.py` fakes Epic): method, path, params, body, headers, timeout per attempt, the retry rule with a fake clock, a FAILURES matrix (refused, timeout, 400 with a field, 401, 403, 404 HTML and JSON, 405, 422, 429 with Retry-After, 500, 502 and 504 HTML, not JSON, a JSON list, `ok` false, an unexpected shape, a boolean as an int, an oversized body) each with its kind, message, and a WARNING without the token (the `SECRETTEPIRA` sentinel never in a log record at DEBUG, an exception's text, or a request URL)

**Dependencies:** 116a · **Files:** `src/waifu/finance/tepira.py`, `tests/test_finance_tepira.py` · **Scope:** M

## Task 117: The yes gate for actions, finance tools, personas ✅

**Acceptance criteria:**
- [x] `brain/confirm.py`: `is_yes(..., order_words=ORDER_WORDS)`, `RECORD_ORDER_WORDS`, `ConfirmationGate.check_action(key, *, label, question, order_words)` with `begin_turn` judging the yes by the awaited request's order words; `check` delegates and every v1.9 test passes unchanged (device messages and questions byte for byte); one request per turn across devices and expenses; a yes in the same turn, the model's words, or a late yes never count
- [x] `brain/tools.held_for_yes` for any held call; `brain/factory.confirmation_gate(config)` and `build_brain(..., confirmations=)`
- [x] `brain/finance_tools.py` `FinanceTools` per SPEC: the specs (four non-strict reads; with `writes` the strict `record_expense` and non-strict `undo_expense`), byte-identical across calls, no keyword strict tool use refuses; argument checks (null as left out, unknown keys, booleans are not integers, the WIB default month at 23:30 on a month's last day and 00:30 on the 1st, `year` without `month`); results and errors; the 60 s cache (successes only, emptied by a record or undo); the briefing guard; record: secret descriptions refused, the wallet looked up and its tepira name used, held until the next turn's plain yes, recorded once, `deduped` kept out of the undo; undo within 15 minutes of her own last entry, never another
- [x] Both personas end with the SPEC's section, word for word; `tests/test_brain_persona.py` checks it and that the rule sections stay identical

**Dependencies:** 116a · **Files:** `src/waifu/brain/confirm.py`, `tests/test_brain_confirm.py`, `src/waifu/brain/tools.py` (`held_for_yes` only), `tests/test_brain_tools.py`, `src/waifu/brain/factory.py`, `tests/test_brain_factory.py`, `src/waifu/brain/finance_tools.py`, `tests/test_finance_tools.py`, `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** M

## Task 118: Integration, CLI, logging, docs ✅

**Acceptance criteria:**
- [x] 116 and 117 merged; `_companion_brain` adds `FinanceTools` when `tepira.enabled` over one `TepiraClient` per process, the writes only with Claude, the shared gate from `confirmation_gate`; an autouse `test_cli.py` fixture refuses a real `TepiraClient`
- [x] `PRIVATE_ARGUMENT_TOOLS` and `logged_arguments` in `brain/tools.py`, used by `brain/claude.py` and `app.py`: a finance call's arguments are `…` at INFO and its result only at DEBUG (tests in `test_brain_claude.py` and `test_app.py`)
- [x] `waifu tepira [ringkasan|dompet|tujuan [nama]|transaksi [-n N]|token]` per SPEC: lines, exit codes 2 off, 1 on a `FinanceError`, 0 otherwise; `token` without config or network, printed once and never logged
- [x] `_tepira_check` lines exactly as SPEC, before the notify line; the token sentinel in no check line, log record at DEBUG, or exception text
- [x] The strict-limit test with `enable_tepira(home)` (writes on): 17 of 20 strict tools, 16 of 24 optional parameters, `record_expense` in the must-be-strict set; `waifu chat --dry-run` builds the six finance tools
- [x] README "Keuangan dari tepira" and its Privasi bullet and checklist items; CHANGELOG v1.24; SPEC touch-ups; full suite and ruff clean

**Dependencies:** 116, 117 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `src/waifu/brain/tools.py`, `src/waifu/brain/claude.py`, `src/waifu/app.py`, `tests/test_brain_tools.py`, `tests/test_brain_claude.py`, `tests/test_app.py`, `README.md`, `CHANGELOG.md`, `SPEC.md` · **Scope:** M

## Task 119a: Rain alert contract ✅

**Acceptance criteria:**
- [x] SPEC "peringatan hujan (v1.25)" (stories, non-goals, privacy, config, the weather extension, the detection rules, the watcher, CLI, check line, safety) and the briefing section's forecast parameters; tasks 119a-121 in the plan and here
- [x] `RainAlertConfig` as `AppConfig.rain_alert` (`enabled`, `lead_minutes` 30-180, `threshold` 30-100, `active_from` before `active_until` with bare YAML times read as clock times, `push`) with tests

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 119: Hourly rain in the forecast ✅

**Acceptance criteria:**
- [x] The forecast request asks `hourly=weather_code,precipitation_probability,precipitation`; `today()` and the briefing unchanged (every malformed-reply case still a `WeatherError`)
- [x] `RAIN_CODES`, `Hour`, `Forecast`, and `WeatherClient.forecast()` per SPEC: nulls as None, a malformed reply as the unreadable `WeatherError`, the same 8 s limit and geocoding cache

**Dependencies:** 119a · **Files:** `src/waifu/weather.py`, `tests/test_weather.py`, `tests/test_cli.py` (the Open-Meteo fake's hourly arrays) · **Scope:** S

## Task 120: Rain detection and the watcher ✅

**Acceptance criteria:**
- [x] Pure `is_rainy`, `raining_now`, `rain_window`, `follow_rain`, and `spoken_hour` per SPEC, with the edges: the threshold exactly, 0.5 mm, rain and thunder codes, drizzle, nulls, the lead's edges, rain already falling, a window across midnight, a forecast that drops the rain, a break of three hours (the same rain) and of four (a new one)
- [x] `RainWatcher`: quiet outside the active hours without asking Open-Meteo; one alert per episode; failures never said and logged once per run of failures; announce and push each failure-proof; the owner word at sentence start; a daemon thread that polls at once and every 900 s, stops at once, and starts once

**Dependencies:** 119a, 119 · **Files:** `src/waifu/rain_watch.py`, `tests/test_rain_watch.py` · **Scope:** M

## Task 121: Wiring, `waifu rain`, check, docs ✅

**Acceptance criteria:**
- [x] `run` starts the watcher after "ready" and stops it on exit when `rain_alert.enabled` and `briefing` names a place, announcing once the follow-up window has closed and pushing only with `rain_alert.push` and `notify`; `chat` never; an autouse fake in `test_cli.py`
- [x] `waifu rain` lines and exit codes per SPEC; the check lines right after the briefing line, sharing its place text
- [x] `config.example.yaml` block, valid when written out; README "Peringatan hujan" with its command, check, checklist, and Privasi rows; CHANGELOG v1.25; full suite and ruff clean

**Dependencies:** 119, 120 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml`, `tests/test_config.py`, `README.md`, `CHANGELOG.md` · **Scope:** M

## Task 122a: Version notice contract ✅

**Acceptance criteria:**
- [x] SPEC "versi baru (v1.26)" (stories, non-goals with the self-restart, privacy, config, `version.py`, the watcher, the running record and how a run is found running, CLI, check lines, safety); tasks 122a-125 in the plan and here
- [x] `VersionNoticeConfig` as `AppConfig.version_notice` (`enabled`, `check_minutes` 5-1440, `active_from` before `active_until` with bare YAML times read as clock times, `push`) with tests

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 122: Reading the code's version and the running record ✅

**Acceptance criteria:**
- [x] `parse_releases` (file order, Windows line endings, a byte order mark, three-part versions, no heading), `dependency_hash` (order and comments do not count, the extras do not count, not TOML), `newer_releases` (none, one, several, a CHANGELOG that went back, an unknown side)
- [x] `read_code_version` never raises: the commit from an injected `run` with the exact git arguments, 5 s timeout, and `CREATE_NO_WINDOW`; without git, outside a repository, on a timeout, or on odd output the newest source time without `__pycache__`; a missing or malformed CHANGELOG or pyproject as an empty part with its problem; `run=None` asks no git
- [x] `write_running`/`read_running`: atomic, round trip, never created by reading, a missing, malformed, or foreign file is None and left in place; `process_alive` true for this process and false for one that ended, without a signal

**Dependencies:** none · **Files:** `src/waifu/version.py`, `tests/test_version.py` · **Scope:** M

## Task 123: The version watcher ✅

**Acceptance criteria:**
- [x] `version_news`, `notice_text`, `push_text` per SPEC: one newer version, several (the count in words), changed packages with and without a push, packages alone, nothing new, an unknown side; no parentheses in what she says
- [x] `VersionWatcher`: quiet outside the active hours without reading; one notice per news, a later version told again with its own count; read problems and failures never said and logged once per run of failures; announce and push each failure-proof; the owner word at sentence start; a daemon thread that polls at once and every `check_minutes`, stops at once, and starts once

**Dependencies:** 122a, 122 · **Files:** `src/waifu/version_watch.py`, `tests/test_version_watch.py` · **Scope:** M

## Task 124: Wiring, `check`, `waifu version` ✅

**Acceptance criteria:**
- [x] `run` records its version before `--startup-delay` (a failed write logged, never raised; `enroll`, `speaker-test`, and `chat` never record) and starts the watcher after "ready" when `version_notice.enabled`, announcing once the follow-up window has closed and pushing only with `version_notice.push` and `notify`; an autouse fake watcher and reader in `test_cli.py`; `tests/conftest.py` refuses git
- [x] The `check` lines right after `Konfigurasi valid` per SPEC (lock free, current, a later commit, older, changed packages, no record, a dead process id, no release on disk; the settings line); `waifu version` lines
- [x] `config.example.yaml` block, valid when written out

**Dependencies:** 122, 123 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `tests/conftest.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** M

## Task 125: Docs ✅

**Acceptance criteria:**
- [x] README "Versi baru" with its command, check, checklist, and Privasi rows; CHANGELOG v1.26; full suite and ruff clean

**Dependencies:** 124 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 126a: Weekly game report contract ✅

**Acceptance criteria:**
- [x] SPEC "laporan game mingguan (v1.27)" (stories, non-goals, how the facts are gathered with the refresh rule and the time budget, privacy, config, `prompt_timer.py`, `pulls_since`, `gacha/weekly.py`, the finance guard, CLI, check lines, safety); tasks 126a-129 in the plan and here
- [x] `WeeklyReportConfig` as `AppConfig.weekly_report` (`enabled`, `day` an English weekday name, `time` with bare YAML times read as clock times, `push`, `state_path`) with tests

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 126: This week's pulls and the facts gatherer ✅

**Acceptance criteria:**
- [x] `pulls_since`: the count per banner after `since` in server time, each top with the pulls it took and its 50/50 worked out over the whole banner, banners without a pull left out, the week's edge exactly (WIB against server time), an empty week; `partial_history_hint` shared with `HistoryReport.hint`
- [x] `gather_weekly_report`: per game this week's pulls and the pity, a refresh only for games with a stored history (one after another), a game without one, a failed refresh falling back to the store, the endgame of every mode with the ones ending within 7 days, the banners and events ending within 7 days, the unredeemed codes when tracked; each section failing on its own (a HoYoLAB error for one game, an unexpected exception), one time budget with a late section told as out of time; `readable`; `report_json` with its size caps and no currency

**Dependencies:** 126a · **Files:** `src/waifu/gacha/history.py`, `tests/test_gacha_history.py`, `src/waifu/gacha/weekly.py`, `tests/test_gacha_weekly.py` · **Scope:** M

## Task 127: The weekly report tool, prompt, and timer ✅

**Acceptance criteria:**
- [x] `PromptTimer` in `prompt_timer.py`, with `BriefingTimer` built on it and every briefing test unchanged and green (a separate `refactor:` commit)
- [x] `weekly_report {}`: non-strict, a static description built from the configured games; arguments, nothing readable, and an unexpected failure are error results, never exceptions
- [x] `WEEKLY_REPORT_PROMPT`: her own turn naming only `weekly_report`, 3-4 warm sentences, information only; it never opens the screen or music tool and never counts as a yes
- [x] `WeeklyReportTimer`: once on the configured day from the configured time until midnight, a restart that evening and the next ticks never repeat it, never on another day, the day saved before the request, a failed request not retried that week
- [x] The finance tools refuse in the weekly report turn with their own message, asking tepira nothing; the briefing's refusal unchanged

**Dependencies:** 126 · **Files:** `src/waifu/prompt_timer.py`, `src/waifu/briefing.py` (the briefing tests cover the shared timer), `src/waifu/gacha/weekly.py`, `tests/test_gacha_weekly.py`, `src/waifu/brain/finance_tools.py`, `tests/test_finance_tools.py` · **Scope:** M

## Task 128: Wiring, `waifu gacha report`, check ✅

**Acceptance criteria:**
- [x] `run` with the games and the weekly report on: the tool after `CodeTools` and the timer for the voice loop's lifetime, asking in a new session with `required_tool`, pushing with the game tag only with `weekly_report.push` and `notify`; `chat` the tool only; neither with the games or the report off; an autouse fake timer in `test_cli.py`; the strict-tool budget still holds
- [x] `waifu gacha report` lines and exit codes per SPEC, also with `gacha.enabled: false`; the check line right after the redeem codes line
- [x] `config.example.yaml` block, valid when written out

**Dependencies:** 127 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** M

## Task 129: Docs ✅

**Acceptance criteria:**
- [x] README "Laporan game mingguan" with its command, check, checklist, and Privasi rows; CHANGELOG v1.27; full suite and ruff clean

**Dependencies:** 128 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 130a: Offline fallback voice contract ✅

**Acceptance criteria:**
- [x] SPEC "suara cadangan offline (v1.28)" (stories, non-goals, the PowerShell script and its exit codes, `windows_voices.py`, `WindowsTts`, the router's fallback and pause, RVC pitch per piece and per gender, config, CLI, check lines, safety); tasks 130a-133 in the plan and here
- [x] `FallbackTtsConfig` as `TtsConfig.fallback` (`enabled`, `voices`, `retry_s`, `rvc_pitch` with `female` and `male`) with tests

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 130: Windows voices through PowerShell, and `WindowsTts` ✅

**Acceptance criteria:**
- [x] `tests/conftest.py` refuses spawning `powershell` and `pwsh` (with a guard test)
- [x] `list_windows_voices` and `speak_wav`: the exact command (system powershell.exe, `-File` the shipped script, `CREATE_NO_WINDOW`, a timeout), her text on stdin and the voice in the environment, never on the command line; parsing `-List`; a missing powershell.exe, a timeout, exit 3, another exit code, and unreadable output as `WindowsVoiceError`
- [x] `choose_voice`: a named voice in any language, else a female voice of the language first, else none (never a voice of another language)
- [x] `WindowsTts`: no PowerShell at construction; the voice chosen once on first use; no voice, a failure, and empty audio as `TtsError`; `rvc_pitch` by the voice's gender
- [x] The script ships as package data

**Dependencies:** 130a · **Files:** `tests/conftest.py`, `tests/test_safety_guard.py`, `src/waifu/voice/windows_voices.py`, `src/waifu/voice/windows_voices.ps1`, `tests/test_voice_windows_voices.py`, `src/waifu/voice/tts.py`, `tests/test_voice_tts.py`, `pyproject.toml` · **Scope:** M

## Task 131: The router's fallback and RVC pitch ✅

**Acceptance criteria:**
- [x] `RvcClient.convert(..., pitch=)` sends the given pitch, else `tts.rvc.pitch`
- [x] A run whose primary fails is spoken by its language's fallback; the primary skipped for the pause after that, then tried again; a fallback that cannot speak ends the pause; both failing keeps the primary's message; a language without a fallback unchanged
- [x] One WARNING per outage, DEBUG while it lasts, one INFO when the primary works again, never her words
- [x] `fallback_only`; pieces grouped by pitch for conversion, one conversion for a reply of one pitch

**Dependencies:** 130 · **Files:** `src/waifu/voice/rvc.py`, `tests/test_voice_rvc.py`, `src/waifu/voice/tts.py`, `tests/test_voice_tts.py` · **Scope:** M

## Task 132: Wiring, `waifu say --fallback`, `waifu voices`, check ✅

**Acceptance criteria:**
- [x] `build_speech_router` builds one `WindowsTts` per `tts.fallback.voices` entry when enabled, none when disabled, and never starts PowerShell
- [x] `waifu say --fallback` (also with `--raw`), and its refusal when the fallback is off; `waifu voices`; the check lines per SPEC; an autouse fake `list_windows_voices` in `test_cli.py`
- [x] `config.example.yaml` block, valid when written out

**Dependencies:** 131 · **Files:** `src/waifu/voice/tts.py`, `tests/test_voice_tts.py`, `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** M

## Task 133: Docs ✅

**Acceptance criteria:**
- [x] README "Suara cadangan offline" with its command and check rows, a troubleshooting note, and checklist items; CHANGELOG v1.28; full suite and ruff clean

**Dependencies:** 132 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 134a: Play time contract ✅

**Acceptance criteria:**
- [x] SPEC "jam main (v1.29)" (stories, non-goals, anti-cheat, privacy, config, `processes.py`, the session rules with the two-poll end, the sleep gap, the restart resume and the day split, the store, the watcher and the bedtime nudge, the tool, the weekly report, CLI, check lines, safety); tasks 134a-139 in the plan and here
- [x] `PlayTimeConfig` as `AppConfig.play_time` (`enabled`, `poll_seconds`, `games` with their executable names, `bedtime` with bare YAML times read as clock times, `keep_days`, `path`) with tests

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`, `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 134: The process lister ✅

**Acceptance criteria:**
- [x] `tests/conftest.py` refuses kernel32 `CreateToolhelp32Snapshot` and records the refusal (with guard tests); other kernel32 functions still load
- [x] `running_executables`: one process snapshot walked to its end, the names case-folded, the handle always closed; an invalid handle and a walk that stops with another error than `ERROR_NO_MORE_FILES` as `OSError`; the documented signatures; nothing but the snapshot functions

**Dependencies:** 134a · **Files:** `tests/conftest.py`, `tests/test_safety_guard.py`, `src/waifu/processes.py`, `tests/test_processes.py` · **Scope:** S

## Task 135: Sessions, periods, totals, and the store ✅

**Acceptance criteria:**
- [x] `games_running`: whole executable names ignoring case, never the launcher or a part of a name
- [x] `follow_games`: a start at the first poll that sees a game, one missed poll kept, an end at the last poll that saw it after two missed polls, a sleep or a clock set back ending every session at its last poll, a session of one poll dropped
- [x] `resume_games`: a game seen at most 5 minutes before goes on with its start (finished or open), a crash's open session otherwise ends at its last poll
- [x] `period_window` in WIB; `played_seconds` cut at the edges, across midnight, and the union of overlapping sessions; `play_totals` per game with session counts
- [x] `PlayTimeStore`: a missing file reads empty and stays missing, `save` replaces the open sessions and adds or replaces finished ones by game and start, `keep_days` on read and write, `mark_bedtime`, a broken file moved aside, a naive clock refused

**Dependencies:** 134a · **Files:** `src/waifu/play_time.py`, `tests/test_play_time.py` · **Scope:** M

## Task 136: The play time watcher and the bedtime nudge ✅

**Acceptance criteria:**
- [x] `spoken_clock`: whole hours, half past, other minutes
- [x] `PlayTimeWatcher.poll_once`: resumes at the first poll, follows after it, writes only when something changed, keeps unwritten sessions after a failed write, a failed listing or file logged once at WARNING and never said
- [x] Bedtime: once a night inside the window while a game runs, the night written before it is said, never again that night (also after a restart), never without a game or before bedtime, a failed write saying nothing
- [x] `stop()` ends the open sessions at their last poll in one write; the thread polls at once and every `poll_seconds`, and stops promptly

**Dependencies:** 134, 135 · **Files:** `src/waifu/play_watch.py`, `tests/test_play_watch.py` · **Scope:** M

## Task 137: The play time tool and the weekly report ✅

**Acceptance criteria:**
- [x] `play_time {period?, game?}`: non-strict, a static description, per game time and sessions with `playing_now`, the total as the union, `tracked_since`; today when left out; bad arguments and an unreadable file as error results; refused in the morning greeting and the weekly report turns
- [x] The weekly report: per game this week's play time or its problem when a source is given, nothing otherwise (every existing result unchanged); the description and prompt name it once; the prompt keeps its look, play, and yes rules

**Dependencies:** 135 · **Files:** `src/waifu/brain/play_time_tools.py`, `tests/test_play_time_tools.py`, `src/waifu/gacha/weekly.py`, `tests/test_gacha_weekly.py` · **Scope:** M

## Task 138: Wiring, `waifu playtime`, check ✅

**Acceptance criteria:**
- [x] `run` with play time on: the watcher for the voice loop's lifetime with the real lister and the patient announce; `run` and `chat` get the tool; none with it off; `chat` never watches; an autouse fake watcher and a tmp_path store guard in `test_cli.py`; the strict-tool budget still holds
- [x] `waifu playtime` and `waifu gacha report`'s play time line per SPEC; the check line right after the PC health line
- [x] `config.example.yaml` block, valid when written out

**Dependencies:** 136, 137 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** M

## Task 139: Docs ✅

**Acceptance criteria:**
- [x] README "Jam main" with its command, check, checklist, and Privasi rows and a line in "Laporan game mingguan"; CHANGELOG v1.29; full suite and ruff clean

**Dependencies:** 138 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 140a: Teman fokus contract ✅

**Acceptance criteria:**
- [x] SPEC "teman fokus (v1.30)" (stories, non-goals, privacy, config, the log, the timer with its phases, lines, and the sleep rule, holding her own speech during a block, the tool, CLI, check line, safety); tasks 140a-145 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 140: The focus log ✅

**Acceptance criteria:**
- [x] `focus_totals`: blocks cut at the period's edges, a block across midnight on each day, the blocks with time in the period counted
- [x] `FocusStore`: a missing file reads empty and stays missing, `add` keeps the blocks by start, `keep_days` on read and write, every call re-reads, a byte order mark is read, a broken file moved aside and never overwritten, a naive clock refused

**Dependencies:** 140a · **Files:** `src/waifu/focus.py`, `tests/test_focus.py` · **Scope:** S

## Task 141: The focus timer ✅

**Acceptance criteria:**
- [x] `FocusPlan` ranges; `spoken_duration`
- [x] `begin` only while started and without a session; `tick_once`: a block logged and its line said before the break starts, the break's end and the next block, the last block's praise with the total, several phases since the last check, a phase that ended more than 2 minutes ago ending the session quietly, a failed write or voice logged
- [x] `end`: the block under way logged when at least a minute, the session's total; `in_block`, `state`, `focused_seconds`
- [x] The thread checks at once, waits at most `tick_seconds` or until the phase ends, stops promptly, and `stop()` ends the session; a second `start()` is a no-op

**Dependencies:** 140 · **Files:** `src/waifu/focus_timer.py`, `tests/test_focus_timer.py` · **Scope:** M

## Task 142: Quiet during a focus block ✅

**Acceptance criteria:**
- [x] `Assistant(quiet=...)`: while it holds, plain and patient announcements and requests wait and come out after it, merged as always; `remind` is said while it holds and while the follow-up window is open; Master's utterances are answered as always; without `quiet` nothing changes

**Dependencies:** none · **Files:** `src/waifu/app.py`, `tests/test_app.py` · **Scope:** S

## Task 143: The focus tool and the persona line ✅

**Acceptance criteria:**
- [x] `focus_timer {action?, minutes?, break_minutes?, rounds?}`: non-strict, a static description; start with the defaults and with given lengths, stop, status with today's and this week's time; bad arguments, a `FocusError`, and an unreadable log as error results; refused in the morning greeting and the weekly report turns
- [x] One identical line in both personas

**Dependencies:** 141 · **Files:** `src/waifu/brain/focus_tools.py`, `tests/test_focus_tools.py`, `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** M

## Task 144: Wiring, `waifu focus`, check ✅

**Acceptance criteria:**
- [x] `FocusConfig` as `AppConfig.focus` with tests; `config.example.yaml` block, valid when written out
- [x] `run` with focus on: one timer for the voice loop's lifetime, the tool, `quiet`, the scheduler through `remind`, a push only with `focus.push`; none with it off; `chat` never gets the timer or the tool; a tmp_path store guard and a timer whose thread only waits to be stopped in `test_cli.py`; the strict-tool budget still holds
- [x] `waifu focus` and the check line per SPEC

**Dependencies:** 142, 143 · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml` · **Scope:** M

## Task 145: Docs ✅

**Acceptance criteria:**
- [x] README "Teman fokus" with its command, check, checklist, and Privasi rows; CHANGELOG v1.30; full suite and ruff clean

**Dependencies:** 144 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 146a: Jurnal harian contract ✅

**Acceptance criteria:**
- [x] SPEC "jurnal harian (v1.31)" (stories, non-goals, privacy, config, the store and its file, the tools and their budget, the refusal in her own turns, the nightly question and its follow-up, CLI, check line, known limits, safety); tasks 146a-151 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 146: The journal store ✅

**Acceptance criteria:**
- [x] `JournalStore.add`: today (WIB) by default or a past day, several notes a day in order, stamped to the second; empty, too long, and secret texts refused without repeating them; a day after today refused; the same note that day kept once and not written again; a full day refused
- [x] `notes(since, until)` and `forget(day)`; a missing file reads empty and stays missing, every call re-reads, a byte order mark is read, a broken file moved aside and never overwritten, a naive clock refused, two stores in one process lose nothing

**Dependencies:** 146a · **Files:** `src/waifu/journal.py`, `tests/test_journal.py` · **Scope:** M

## Task 147: The journal tools and the refusal in her own turns ✅

**Acceptance criteria:**
- [x] `journal_write {text, date?}` strict and `journal_read {date?, period?}` non-strict, static descriptions; dates checked in code; the read capped to the newest 40 notes and 6000 characters; bad arguments, a `JournalError`, and an unreadable file as error results
- [x] Both refused in the morning greeting and weekly report turns; their arguments hidden from the INFO log

**Dependencies:** 146 · **Files:** `src/waifu/brain/journal_tools.py`, `tests/test_journal_tools.py`, `src/waifu/brain/tools.py`, `src/waifu/app.py` · **Scope:** M

## Task 148: The nightly question ✅

**Acceptance criteria:**
- [x] `JournalConfig` as `AppConfig.journal` (`ask_at` none, or 12:00-23:59, bare YAML times read)
- [x] `JOURNAL_PROMPT` is not a yes, opens no screen, and plays no song; `journal_write` refuses in its turn and `journal_read` runs; the finance, play time, and focus tools refuse in it
- [x] `JournalTimer` asks once a day from `ask_at` until midnight, the day saved first; a restart never asks twice

**Dependencies:** 147 · **Files:** `src/waifu/config.py`, `tests/test_config.py`, `src/waifu/journal.py`, `tests/test_journal.py`, `src/waifu/brain/journal_tools.py`, `src/waifu/brain/finance_tools.py`, `src/waifu/brain/play_time_tools.py`, `src/waifu/brain/focus_tools.py` and their tests · **Scope:** M

## Task 149: The persona line ✅

**Acceptance criteria:**
- [x] One identical line in both personas naming the journal tools, only when asked or answering the nightly question

**Dependencies:** 147 · **Files:** `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** S

## Task 150: Wiring, `waifu jurnal`, check ✅

**Acceptance criteria:**
- [x] `run` and `chat` get `JournalTools` after the to-do tools when the journal is on; `run` with `ask_at` asks through `ask_aloud` with `journal_read` required, in a new session, without a push; `chat` never asks; the strict-tool budget keeps 2 slots free
- [x] `waifu jurnal [list|show|add|forget --yes]` and the check line per SPEC; `config.example.yaml` block, valid when written out; every store under tmp_path in `test_cli.py`

**Dependencies:** 148, 149 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** M

## Task 151: Docs ✅

**Acceptance criteria:**
- [x] README "Jurnal harian" with its contents entry, command, check, checklist, and Privasi rows; CHANGELOG v1.31; full suite and ruff clean

**Dependencies:** 150 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 152a: Pertanyaan yang tertahan contract ✅

**Acceptance criteria:**
- [x] SPEC "pertanyaan yang tertahan (v1.31.1)" (the bug, stories, non-goals, the expiry of a request and of each timer's, the answer's day, known limits, docs) and the paragraphs it changes; tasks 152a-155 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 152: Requests that expire ✅

**Acceptance criteria:**
- [x] `ask_aloud(..., expires_at=None)`; a request held past its expiry by the follow-up window, a cut, `quiet`, or a phone chat is dropped with an INFO log: never thought up, said, or passed on, the avatar untouched
- [x] One not yet expired is answered after the hold; None never expires; every expired request at the head of the queue goes in one pass and the next live one is answered; a naive `now` against an aware WIB expiry never raises

**Dependencies:** 152a · **Files:** `src/waifu/app.py`, `tests/test_app.py` · **Scope:** S

## Task 153: Each timer's expiry ✅

**Acceptance criteria:**
- [x] `PromptTimer(expires=...)` calls `request(prompt, expires(now))` and logs the expiry; `end_of_day` keeps the time zone
- [x] The greeting asked at 07:30 expires at `until` that day; the weekly report asked Sunday 21:00 at Monday 00:00; the journal question asked 23:59 WIB on the 17th at 18-10 00:00 WIB
- [x] `run` passes each timer's expiry to `ask_aloud`

**Dependencies:** 152 · **Files:** `src/waifu/prompt_timer.py`, `src/waifu/briefing.py`, `src/waifu/gacha/weekly.py`, `src/waifu/journal.py`, `src/waifu/cli.py`, `tests/test_briefing.py`, `tests/test_gacha_weekly.py`, `tests/test_journal.py`, `tests/test_cli.py` · **Scope:** M

## Task 154: A late answer goes on the question's day ✅

**Acceptance criteria:**
- [x] After her question at 23:59:50 WIB on the 17th, an undated answer at 00:00:20 is written on the 17th (logged without the note); an explicit date of the 18th stays the 18th; an answer after the window goes on today; a second question replaces the day; an answer before midnight is as before
- [x] `run` and `chat` pass `session_idle_minutes` as the answer window; the strict-tool budget unchanged
- [x] Fix pass: `journal_write`'s description asks her to date an answer after midnight herself (the window is only the safety net); her question counts as asked only once its turn read the journal

**Dependencies:** 152a · **Files:** `src/waifu/brain/journal_tools.py`, `tests/test_journal_tools.py`, `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 155: Docs ✅

**Acceptance criteria:**
- [x] README "Sapaan pagi dan ringkasan harian", "Laporan game mingguan", and "Jurnal harian" say what happens to a held request; CHANGELOG v1.31.1; full suite and ruff clean

**Dependencies:** 153, 154 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 156a: Mode tidur contract ✅

**Acceptance criteria:**
- [x] SPEC "mode tidur (v1.32)" (stories, non-goals, the step delay and its validation, the routine's tag on a device entry, how a run schedules and replaces its delayed steps, the tool result and description, the CLI, known limits, docs) and the v1.7 paragraphs it changes; tasks 156a-161 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 156: The step delay in the config ✅

**Acceptance criteria:**
- [x] `RoutineStep.after_minutes` (None by default) takes a whole number from 1 to 1440; 0, 1441, a boolean, text ("3h", "180"), and a fraction are ConfigErrors that say how to write it
- [x] A delayed step on the PC or a `powers_pc` plug is refused, as every step is; a delayed step with `schedule.enabled: false` and a delayed `play_pause` or `mute` on a media device are refused at load; `MEDIA_TOGGLES` shared with `ScheduleTools`

**Dependencies:** 156a · **Files:** `src/waifu/config.py`, `src/waifu/brain/schedule_tools.py`, `tests/test_config.py` · **Scope:** S

## Task 157: The routine's tag on a scheduled device action ✅

**Acceptance criteria:**
- [x] `DeviceAction(..., routine=None)`; the file gets `routine` only when it is set, so an untagged action is written as before; a tagged one reads back; a non-text `routine` in the file is a corrupt file; `add` refuses an empty or non-text one

**Dependencies:** 156a · **Files:** `src/waifu/schedule.py`, `tests/test_schedule.py` · **Scope:** S

## Task 158: A routine schedules its delayed steps ✅

**Acceptance criteria:**
- [x] `run_routine(registry, routine, *, name, schedule=None, now=datetime.now)`: the steps of now run; a delayed step never runs then and is added as a one-shot tagged device entry at the start plus its delay, cut to the minute, with a result naming the time and id (`besok` on the next day) and `state` {id, at}
- [x] A delayed step with a problem (unknown device, PC, command) is not scheduled; a full schedule, an unreadable file, or no schedule fails just that step; a new run cancels the routine's own pending delayed entries and nothing else; a routine without delayed steps never creates the file
- [x] `describe_step` and `describe_routine` name the delay (`ac off setelah 3 jam`)
- [x] Fix pass: the result says the time as she would say it, without the id (`jam 01.00 dini hari nanti` for the night after an evening start, `besok` only once that night has passed); `waifu routine run` builds its `Dijadwalkan besok jam 01.00 (j12).` line from `state`

**Dependencies:** 156, 157 · **Files:** `src/waifu/routines.py`, `tests/test_routines.py` · **Scope:** M

## Task 159: The tool and the scheduler run delayed steps ✅

**Acceptance criteria:**
- [x] `ScheduleTools(now=...)`: `run_routine` schedules a delayed step and its result names `after_minutes` and `scheduled` {id, at}; `cancel_schedule` with that id cancels it; a full schedule is an honest error; the description says how to mention and cancel it; `list_schedules` names the routine; schemas and the strict budget unchanged
- [x] A scheduled routine schedules its delayed steps from the moment it fired, silently; the delayed step later fires silently; its failure names the routine

**Dependencies:** 158 · **Files:** `src/waifu/brain/schedule_tools.py`, `src/waifu/scheduler.py`, `tests/test_schedule_tools.py`, `tests/test_scheduler.py` · **Scope:** M

## Task 160: `waifu routine`, `waifu schedule list`, check, example config ✅

**Acceptance criteria:**
- [x] `waifu routine list` and the check line show delays; `waifu routine run` schedules a delayed step in the real schedule (in a temporary one with `--dry-run`) and says who runs it; `waifu schedule list` and `cancel` name the routine
- [x] The example `tidur` routine ends with `ac off` after 180 minutes and stays valid when written out

**Dependencies:** 159 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml` · **Scope:** M

## Task 161: Docs ✅

**Acceptance criteria:**
- [x] README "Mode tidur" with a full example routine, cancelling, running again, and what must stay on; the routine bullets name `after_minutes`; CHANGELOG v1.32; full suite and ruff clean
- [x] Fix pass: the checklist and CHANGELOG tell an owner with a `tidur` routine to add the delayed AC step, and to restart `waifu run` again after the two-minute test; the desktop Power settings page; "Mengatur jadwal dari terminal" names `waifu routine run`

**Dependencies:** 160 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 162a: Pendapatan gacha contract ✅

**Acceptance criteria:**
- [x] SPEC "pendapatan gacha (v1.33)" (stories, non-goals, what each diary gives, the month window and the month check, the values and readers, `HoyolabClient.income`, the tool, the persona line, the weekly report's part, the CLI, privacy, safety); tasks 162a-167 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 162: The income values and readers ✅

**Acceptance criteria:**
- [x] `GameIncome` with `pulls` (earned // 160); the three readers turn real genshin.py models built from sample dicts into it with the month asked, sources most first, today and HSR's yesterday only for this month, and ZZZ's sources named in code
- [x] A ZZZ answer without Polychrome is a ValueError; `answers_month` tells a model of another month; `income_months` is this month and the two before in server time (midnight server time turns the month); `parse_month` takes exactly `YYYY-MM`
- [x] No genshin import, and no uid, nickname, avatar, or region in a value

**Dependencies:** 162a · **Files:** `src/waifu/gacha/income.py`, `tests/test_gacha_income.py` · **Scope:** M

## Task 163: `HoyolabClient.income` ✅

**Acceptance criteria:**
- [x] One call reads every game asked with its explicit uid and the month passed (Genshin 1-12, HSR `YYYYMM`, ZZZ `""` for this month and `YYYYMM` before), never `get_diary` or `update_settings`
- [x] A failing game does not hide the others; no cookie, a timeout, a hidden diary (chronicle_private), a model that does not parse (other), and an answer for another month (other, its own message) are each game's error; no result, error, or log holds the cookie, uid, or nickname
- [x] Fix pass: the answer-for-another-month message says HoYoLAB answered another month (`HoYoLAB menjawab bulan lain untuk pendapatan <short>, ...`) instead of naming the window, which only months inside it ever reach; the README troubleshooting entry follows

**Dependencies:** 162 · **Files:** `src/waifu/gacha/hoyolab.py`, `tests/test_gacha_hoyolab.py` · **Scope:** M

## Task 164: The `gacha_income` tool and the persona line ✅

**Acceptance criteria:**
- [x] A non-strict `gacha_income {game?, month?}` in its own provider; without `game` every configured game with failures under `errors`; without `month` this month with `today`; an earlier month without it; a month outside the window, a bad format, an unknown game or key are invalid without asking HoYoLAB; every game failing is an error result
- [x] Reads kept 15 minutes per game and month (this month's per server day); the description says what each game gives, pulls at 160 from the premium currency only, information for planning, never a push to pull, spend, or top up, only when asked
- [x] The persona (and the HSR persona, word for word) names `gacha_income` under the game tools
- [x] Fix pass: a null `game` or `month` counts as left out, as local models send it (it was refused as invalid)

**Dependencies:** 163 · **Files:** `src/waifu/gacha/income_tools.py`, `tests/test_gacha_income_tools.py`, `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** M

## Task 165: Wiring and `waifu gacha income` ✅

**Acceptance criteria:**
- [x] `run` and `chat` add `IncomeTools` after the game data tools when the games are on; the budget test lists `gacha_income`
- [x] `waifu gacha income [game] [--month YYYY-MM]` prints each game's month (also with the games off), exits 1 when a game failed, and refuses a month outside the window with exit 2 before asking HoYoLAB
- [x] Fix pass: HoYoLAB's change names the month it belongs to (`Bulan sebelumnya: 10.000; bulan ini -52% dibanding itu`, the month's name for an earlier month) instead of reading as last month's drop

**Dependencies:** 164 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 166: The weekly report's income part ✅

**Acceptance criteria:**
- [x] With an income source, one more lane in the same budget reads this month for every game; each game's `income` or `income_problem`; a late read ran out of time; without a source nothing changes (the exact JSON and the calls stay as before)
- [x] `income_this_month` and `income_problem` in the JSON only with a source; the description and the prompt name it; `waifu gacha report` prints one line a game
- [x] Fix pass: with a source, the description's information-only rule covers this month's income too and never judges the amount earned; without one it stays byte-identical

**Dependencies:** 163 · **Files:** `src/waifu/gacha/weekly.py`, `tests/test_gacha_weekly.py`, `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 167: Docs ✅

**Acceptance criteria:**
- [x] README "Pendapatan gacha bulanan", a command row, a line in "Laporan game mingguan", checklist items; CHANGELOG v1.33; full suite and ruff clean

**Dependencies:** 165, 166 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 168a: Mode game contract ✅

**Acceptance criteria:**
- [x] SPEC "mode game (v1.34)" (stories, non-goals, config and its errors, `on_poll`, `GameMode`, the hold through the v1.30 quiet, phone mode without a push of its own, the bedtime nudge, the staleness of held items, the CLI, known limits, privacy, safety); tasks 168a-174 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 168: `GameModeConfig` and its validation ✅

**Acceptance criteria:**
- [x] `game_mode` with `announcements` (`normal` by default, `hold`, `phone`), `on_start` and `on_stop` (none by default); another word is refused naming the setting
- [x] A routine name not in `routines` is a ConfigError naming it; a game mode other than the default with `play_time.enabled: false` is a ConfigError; the default loads with play time off

**Dependencies:** 168a · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 169: The play time watcher's `on_poll` ✅

**Acceptance criteria:**
- [x] After each good poll `on_poll(playing, resumed)`: playing while a tracked game has an open session (one missed poll keeps it, a sleep keeps it), resumed only at the first poll of a run that went on with a session from before
- [x] Never for a failed poll, from `stop()`, or after it; a failing `on_poll` is logged and polling goes on; without one nothing changes
- [x] Fix pass: a restart mid-game whose first poll fails after following the games (the file's write or the bedtime nudge's) tells the resume at the next good poll, so `on_start` never runs again; a game first seen at a failed first poll is still no resume

**Dependencies:** 168a · **Files:** `src/waifu/play_watch.py`, `tests/test_play_watch.py` · **Scope:** S

## Task 170: `GameMode` ✅

**Acceptance criteria:**
- [x] `on_start` on not playing → playing, `on_stop` on the reverse, once per transition, through `run_routine` with the schedule (delayed steps scheduled); the first observation runs `on_start` only when playing and not resumed, never `on_stop`; nothing said; failed steps logged at WARNING (anything raised is logged with its traceback by the watcher)
- [x] `playing()` only while the last observation saw a game at most three polls ago; `holding()` in `hold` and `phone`, `diverting()` in `phone`, each only while playing
- [x] Fix pass: the three polls count from the end of the routine the poll ran, and never run out while it runs, so a slow routine (lamps out of reach) never lets the hold lapse mid-game; a routine that raises still records the time

**Dependencies:** 168 · **Files:** `src/waifu/game_mode.py`, `tests/test_game_mode.py` · **Scope:** M

## Task 171: Announcements that expire ✅

**Acceptance criteria:**
- [x] `announce(..., expires_at=None)`; an announcement still queued at its expiry is dropped with an INFO log, the rest said as before; reminders never expire
- [x] The rain alert is announced with its rain's start as `expires_at`; held past it (a focus block), it is never said
- [x] Fix pass: a text queued more than once is said once; the announcements a game held come out after `GAME_HELD` ("Tadi selama main ada kabar."), once, not when all expired, never for a focus block alone; the gacha reminders about a game's state expire at the next poll (problems and the check-in apart, without), the hot-GPU notice an hour after it was told (the disk notice never)

**Dependencies:** 168a · **Files:** `src/waifu/app.py`, `tests/test_app.py`, `src/waifu/rain_watch.py`, `tests/test_rain_watch.py` · **Scope:** S

## Task 172: Wiring in `run` ✅

**Acceptance criteria:**
- [x] With a game mode other than the default, one `GameMode` built with the run's registry and schedule, its `observe` the watcher's `on_poll`; the quiet of the Assistant and the avatar holds while a game holds (and during a focus block); with `normal` they stay `focus.in_block`
- [x] With `phone` while a game runs, a notice that was pushed is not queued and one that was not is; the rain alert keeps its expiry; the bedtime nudge goes through `remind` in game mode; `chat` has no game mode
- [x] Fix pass: the Assistant gets game mode's `holding` as `game_holding` (None without a game mode)

**Dependencies:** 169, 170, 171 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 173: Check line and example config ✅

**Acceptance criteria:**
- [x] `waifu check` says the mode, the routines, and warns about `phone` without notifications; the default says `! Mode game mati`
- [x] `config.example.yaml` documents `game_mode`; written out it holds the defaults

**Dependencies:** 172 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** S

## Task 174: Docs ✅

**Acceptance criteria:**
- [x] README "Mode game" with a contents entry, checklist items, and a Privasi bullet; "Teman fokus" no longer says a held rain alert may come after the rain; CHANGELOG v1.34; full suite and ruff clean
- [x] Fix pass: a crash at launch leaves the room as `on_stop` sets it (not restored; `brightness` lights a lamp that was off); held notices are lost when `waifu run` stops, also in the 1-2 polls after the game closes (known limits, "Yang dilewati"); the checklist and `config.example.yaml` describe the lead-in and what is dropped as stale

**Dependencies:** 173 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 175: A focus block keeps the game reminders ✅

**Acceptance criteria:**
- [x] Proven first: a real `GachaWatcher` and a real `Assistant` with a 30-minute focus block (no game mode, or no game running) said nothing at the break; now the stamina and daily reminders are said then, and a hot-GPU notice after a two-hour block
- [x] A game still drops them (past the next poll, or an hour), also one opened during the block; one a game held under a poll comes after `GAME_HELD`; the rain alert keeps its expiry for every hold
- [x] CHANGELOG v1.34 says what changes without `game_mode`; SPEC and README describe the game-only expiry

**Dependencies:** none · **Files:** `src/waifu/app.py`, `src/waifu/rain_watch.py`, `src/waifu/gacha/watcher.py`, `src/waifu/pc_watch.py`, `src/waifu/cli.py`, `tests/test_app.py`, `tests/test_gacha_watcher.py`, `tests/test_pc_watch.py`, `tests/test_cli.py`, docs · **Scope:** S

## Task 176: The microphones and their levels ✅

**Acceptance criteria:**
- [x] Each input once, from the default input's host API first; never the default's aliases, outputs, or WDM-KS; MME's 31-character cut counts as the same name
- [x] A measurement listens through `MicStream` (a shared stream at the device's rate, resampled for the VAD) and closes it; one that stops delivering raises instead of waiting; speech is heard only when the VAD would keep an utterance of it (never a click or short bursts), the noise floor alone or a steady fan is not, digital silence is silent
- [x] The config value is `"name, host API"`, which sounddevice's own matcher resolves to that entry where the bare name is refused

**Dependencies:** none · **Files:** `src/waifu/voice/mic_check.py`, `tests/test_voice_mic_check.py` · **Scope:** S

## Task 177: `waifu mic` and the check's mic line ✅

**Acceptance criteria:**
- [x] Lists the inputs marked "dipakai Cyrene" and "default Windows", prompts "Sebut 'halo Cyrene' ...", prints each one's peak, RMS, bar, and verdict, and the `input_device` line for the best mic (or says the one in use is best); `--seconds` (> 0); a mic that cannot be opened is reported and skipped; no input, no default, or a configured name that opens nothing each say why; always exit 0; nothing saved
- [x] `waifu check` says the mic, and whether it is the Windows default, without listening

**Dependencies:** 176 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 178: Docs ✅

**Acceptance criteria:**
- [x] README "Mic tidak terdeteksi atau tidak mau dibuka." leads with `waifu mic` and has the Bluetooth hands-free note; the commands table, enrolment, config table, and checklist name it; CHANGELOG v1.34.1; SPEC "perbaikan fokus dan waifu mic (v1.34.1)"; full suite and ruff clean

**Dependencies:** 175, 177 · **Files:** `README.md`, `CHANGELOG.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 179a: Tombol cepat di HP contract ✅

**Acceptance criteria:**
- [x] SPEC "tombol cepat di HP (v1.35)" (stories, non-goals with the PC decision, which buttons and their ids, `QuickActions`, the config and its errors, the two routes and their order of checks, the page, the CLI, guest mode, privacy, safety); tasks 179a-185 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 179: `phone.quick_actions` and its validation ✅

**Acceptance criteria:**
- [x] `phone.quick_actions` with `devices` and `routines`, each a list or left out (all); unknown keys refused; the default leaves both out
- [x] A ConfigError names an unknown device or routine, a device of type pc, media, or apps, a `powers_pc` plug, and a name given twice

**Dependencies:** 179a · **Files:** `src/waifu/config.py`, `tests/test_config.py` · **Scope:** S

## Task 180: `QuickActions` ✅

**Acceptance criteria:**
- [x] `groups()` lists lights, plugs, IR devices, and mocks in config order with on, off, toggle, and power as they have them, brightness 10/30/60/100 for a light, the learned temperatures among 18-28 (even) for an IR device, and every routine in "Rutinitas" (description, else name); never the PC, a `powers_pc` plug, media, or apps; `chosen` picks and orders; a device whose commands cannot be read is left out and logged
- [x] `find(id)` looks the id up in a list built then (a forgotten IR code or a removed routine is None)
- [x] `run` runs a device action through the registry with its message, a routine through `run_routine` with the schedule and a summary (counts, the first failure, the scheduled times); a `powers_pc` device is refused without being called

**Dependencies:** 179 · **Files:** `src/waifu/phone/actions.py`, `tests/test_phone_actions.py` · **Scope:** M

## Task 181: `GET /actions` and `POST /action` ✅

**Acceptance criteria:**
- [x] `GET /actions` needs the device cookie and lists the groups (empty without actions); `POST /action {"id"}` needs the page's Origin and the cookie, runs the action, and answers `{"ok", "message"}`, a device failure as `ok: false`
- [x] 10 actions per device a minute (429 with `Retry-After`; another device is not limited; the window passes on the server's clock); one at a time per device (429), apart from the chat's; a malformed or oversized body is 400/413/415/411; an unknown id, the PC, and the PC's plug are 404 and run nothing; anything raised is a 500 in Indonesian
- [x] INFO logs name the device and the action, never the token; `RollingLimit` is public
- [x] Fix pass: a press while the phone's last one still runs is a 409 without `Retry-After` ("Perintah tadi masih jalan. Tunggu sebentar."), so the page tells it from the limit's 429

**Dependencies:** 180 · **Files:** `src/waifu/phone/server.py`, `src/waifu/avatar/server.py`, `tests/test_phone_server.py` · **Scope:** M

## Task 182: Wiring in `run` ✅

**Acceptance criteria:**
- [x] `run` with the phone on hands the phone server `QuickActions` over the run's registry (the dry run's mocks), routines, schedule, and `phone.quick_actions`
- [x] Fix pass: a test presses a routine with a delayed step while the run serves and finds the step in the run's own schedule (it failed with `schedule=` dropped)

**Dependencies:** 181 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 183: The page's pure helpers ✅

**Acceptance criteria:**
- [x] `actions.js`: `parseActionGroups` keeps well-formed groups and actions (null without a list), `actionRequest(id)` is the JSON body, `actionOutcome` reads `{ok, message}` (null otherwise)
- [x] `problemForStatus("/action", 404)` is `action_unknown`, 429 `action_limit`; both have Indonesian texts
- [x] Fix pass: 409 on `/action` is `action_busy`, with the server's text

**Dependencies:** 179a · **Files:** `src/waifu/avatar/web/phone/actions.js`, `src/waifu/avatar/web/phone/audio.js`, `tests/js/phone_actions.test.mjs`, `tests/js/phone_audio.test.mjs` · **Scope:** S

## Task 184: The panel ✅

**Acceptance criteria:**
- [x] The toggle shows only when `/actions` lists a group (a failure shows no notice); it opens and closes the panel with `aria-expanded`; Escape closes it; the panel and the viewfinder close each other
- [x] A tap posts the id, marks the button busy and every button disabled until the answer, ignores taps meanwhile, and toasts the message or the problem; 401 shows the pairing form
- [x] 44 px buttons in a grid for 360-412 px, the panel scrolls within 36dvh; the service worker keeps `/phone/actions.js` and passes `/actions` and `/action`; no inline style or script
- [x] Fix pass: the notice sits above the panel, so a toast never moves the buttons (it pushed the panel up a row and back 6 s later, and a second tap hit another button); opening the panel puts the focus on its first button and Escape on the toggle closes it too; the toggle steps aside while the owner types with the panel closed, so the text box is 168 px again at 360 px with the camera button (116 px with the toggle), its height fitted again on focus and blur

**Dependencies:** 183 · **Files:** `src/waifu/avatar/web/phone/index.html`, `phone.js`, `phone.css`, `sw.js`, `tests/js/phone_ui.test.mjs`, `tests/js/phone_page.test.mjs`, `tests/js/phone_sw.test.mjs` · **Scope:** M

## Task 185: Docs ✅

**Acceptance criteria:**
- [x] README "Tombol cepat di HP" in "Cyrene di HP" (the PC is never a button), checklist items; `config.example.yaml` documents `quick_actions`; CHANGELOG v1.35; full suite, JS tests, and ruff clean

**Dependencies:** 182, 184 · **Files:** `README.md`, `CHANGELOG.md`, `config.example.yaml`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 186a: Mode tamu contract ✅

**Acceptance criteria:**
- [x] SPEC "mode tamu (v1.36)" (stories, non-goals with the CLI decision, the private tools and the allowed ones, `GuestMode`, the refusal, the new session, the guest section, `guest_mode`, the self-started turns, the phone, the CLI, known limits, privacy); tasks 186a-195 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 186: `guest_mode.max_hours` ✅

**Acceptance criteria:**
- [x] `guest_mode.max_hours` (6, 1-24), unknown keys refused; `config.example.yaml` documents it and its block is valid once written out

**Dependencies:** 186a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py`, `tests/test_cli.py` · **Scope:** S

## Task 187: `GuestMode` ✅

**Acceptance criteria:**
- [x] `turn_on` sets `until` to the earlier of `max_hours` from now and the next 05:00 strictly after now; again while on renews it; `turn_off` ends it; both say whether anything changed
- [x] `is_on` ends it at `until` (logged); `generation` moves on every on, off, and automatic end, never on a renewal; `status` reports on and until
- [x] `refusal(name)` refuses exactly the private tools while on; `PRIVATE_TOOLS` matches each module's constant
- [x] Fix pass: `game_events` is private (it carries Master's event progress, Polychrome earned, and endgame stars, not just a calendar); `focus_timer` is not (a block started before the guests came must still stop), with a test that stops one through the dispatcher in guest mode

**Dependencies:** 186 · **Files:** `src/waifu/guest.py`, `tests/test_guest.py` · **Scope:** S

## Task 188: The refusal and the new session ✅

**Acceptance criteria:**
- [x] `run_tool(..., refusal=)` answers a refused call with an error and runs nothing, before the confirmation gate
- [x] Both brains pass `tool_refusal` to every call; with `session_marker`, a turn after the marker changed starts a new session, also while a question awaits Master's yes (which is forgotten); an unchanged marker keeps the session

**Dependencies:** 187 · **Files:** `src/waifu/brain/tools.py`, `src/waifu/brain/claude.py`, `src/waifu/brain/ollama_brain.py`, `tests/test_brain_tools.py`, `tests/test_brain_claude.py`, `tests/test_brain_ollama.py` · **Scope:** M

## Task 189: The guest section ✅

**Acceptance criteria:**
- [x] `build_brain(guest_mode=)` hands the brain guest mode's refusal and generation; while guest mode is on a session's prompt ends with the guest section instead of the memory section (the facts unread), with or without memory; off, as before

**Dependencies:** 188 · **Files:** `src/waifu/brain/factory.py`, `tests/test_brain_factory.py` · **Scope:** S

## Task 190: `GuestTools` ✅

**Acceptance criteria:**
- [x] `guest_mode {on?}` is non-strict with a static description; on, off, and status answer `{on, until, message}` in Indonesian; bad arguments are an error

**Dependencies:** 187 · **Files:** `src/waifu/brain/guest_tools.py`, `tests/test_guest_tools.py` · **Scope:** S

## Task 191: The briefing in guest mode ✅

**Acceptance criteria:**
- [x] While guest mode is on `daily_briefing` has no tasks and no reminder entries, and keeps the weather and the device and routine entries; off, as before

**Dependencies:** 187 · **Files:** `src/waifu/briefing.py`, `tests/test_briefing.py` · **Scope:** S

## Task 192: Private requests ✅

**Acceptance criteria:**
- [x] `ask_aloud(..., private=True)` is dropped before its turn while guest mode is on (no reset, nothing said or passed on, logged without the prompt), and answered as before when it is off; a request that is not private is answered in guest mode

**Dependencies:** 186a · **Files:** `src/waifu/app.py`, `tests/test_app.py` · **Scope:** S

## Task 193: Wiring and the check line ✅

**Acceptance criteria:**
- [x] `run` and `chat` give the brain `GuestTools` first and `build_brain(guest_mode=)`; `run` hands the same guest mode to the briefing tool and the Assistant, and marks the journal and weekly report requests private
- [x] `waifu check` says how guest mode is switched and when it ends, with a warning when speaker verification is off
- [x] The strict budget test names `guest_mode` as non-strict; every tool of a full request is either private or allowed in guest mode
- [x] Fix pass: the open list drops `game_events` and adds `focus_timer`; a full chat request carries every private tool

**Dependencies:** 189, 190, 191, 192 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 194: The persona section ✅

**Acceptance criteria:**
- [x] Both personas have the same "Mode tamu:" section before "Keuangan Master:", naming `guest_mode` with on true, on false, and without on, and never bringing up Master's private things while it is on

**Dependencies:** 190 · **Files:** `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** S

## Task 195: Docs ✅

**Acceptance criteria:**
- [x] README "Mode tamu" with a contents entry, checklist items, and a Privasi bullet; the tepira guest advice points to it; CHANGELOG v1.36; full suite and ruff clean
- [x] Fix pass: SPEC, README, CHANGELOG, `config.example.yaml`, the guest section, and the `guest_mode` description sort `game_events` as closed and the focus timer as open; README says `waifu check` warns only for `speaker.enabled: false`, since it cannot see `waifu run --no-speaker-check`

**Dependencies:** 193, 194 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 196a: Volume suaraku contract ✅

**Acceptance criteria:**
- [x] SPEC "volume suaraku (v1.37)" (stories, non-goals, the gain and the limiter, the avatar and the phone, `VolumePlayer`, `VoiceLevel` and its file, the whisper window, the config, `voice_volume`, the media device's line, the CLI, known limits); tasks 196a-201 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 196: The gain and `VolumePlayer` ✅

**Acceptance criteria:**
- [x] `apply_volume` returns the clip itself at 100, scales it below, and above 100 scales it and soft-limits every sample (linear below the knee, below 0.98 always, monotonic), float32 kept, silence silent
- [x] `VolumePlayer` plays the gained clip through the player it wraps, reading the level at every clip, and passes `stop` and both answers through

**Dependencies:** 196a · **Files:** `src/waifu/voice/volume.py`, `tests/test_voice_volume.py` · **Scope:** S

## Task 197: The `voice` config ✅

**Acceptance criteria:**
- [x] `voice.state_path` and `voice.night` (`from` 22:00, `until` 06:00, `level` 50, 10-100), bare YAML times read; a time zone, `from` equal to `until`, `from_`, and unknown keys refused naming the setting; `config.example.yaml` documents it and its block is valid once written out

**Dependencies:** 196a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** S

## Task 198: `VoiceLevel` ✅

**Acceptance criteria:**
- [x] A missing file is level 100 with whisper mode off and stays missing; `set_level`, `change`, and `set_night` write the file atomically and only on a change, steps of 20 held in 10-150
- [x] A broken file is moved aside (a counter for a second one) and read as missing; a failed move raises
- [x] `whispering` holds inside the window, `from` counted and `until` not, also across midnight; `level_now` is the lower of the level and the night level while whispering, the normal level at `until`, and never raises (the last state read, or 100, with one warning)
- [x] Fix pass: a step lands on the next multiple of 20 (75 softer is 60, 10 louder is 20), so after either end the steps reach 100 again; softer steps from the level heard now, so in whisper mode the next clip is softer at once (120 at 23:00 softer is 40); a broken file that cannot be moved aside logs its ERROR once until a good read, not at every clip

**Dependencies:** 197 · **Files:** `src/waifu/voice_level.py`, `tests/test_voice_level.py` · **Scope:** M

## Task 199: `voice_volume` ✅

**Acceptance criteria:**
- [x] `voice_volume {level?, change?, night?}` is non-strict with a static description that sends "suaramu" here and "volume", "musik", and "PC" to the media device; set, step, bounds, whisper on and off, and the state answer in Indonesian with the whisper cap named; bad arguments, a file error, and her own turns are error results
- [x] The media device's line points her own voice to `voice_volume`
- [x] Fix pass: the description says her answer on the PC is heard at `now` (whisper mode may keep it below the level just set), lets the possessive decide ("volume suaramu" is hers, "volume" alone the media device), drops "ngomongnya pelan-pelan" (mostly slowly) for "jangan keras-keras ngomongnya", and says speaking slower or faster is not this tool

**Dependencies:** 198 · **Files:** `src/waifu/brain/voice_volume_tools.py`, `tests/test_voice_volume_tools.py`, `src/waifu/brain/tools.py`, `tests/test_brain_tools.py` · **Scope:** M

## Task 200: Wiring and the check line ✅

**Acceptance criteria:**
- [x] `run` and `chat` give the brain `VoiceVolumeTools`; `run`'s Assistant, `chat --speak`, and `say` play through a `VolumePlayer` at the file's level; `say --volume N` plays at N (10-150, else an argparse error) and saves nothing
- [x] `waifu check` shows the level and whisper mode, and a moved or unreadable file
- [x] The strict budget test names `voice_volume` as non-strict and open in guest mode

**Dependencies:** 196, 198, 199 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `tests/test_app.py` · **Scope:** M

## Task 201: Docs ✅

**Acceptance criteria:**
- [x] README "Volume suaraku" with a contents entry, a link from "Musik, volume, dan aplikasi PC", and checklist items; CHANGELOG v1.37; full suite and ruff clean
- [x] Fix pass: README and SPEC say the grid steps, softer in whisper mode, "volume suaramu" in the table, that her answer is heard at the new level on the PC unless whisper mode holds it, and that reminders and announcements follow her level and whisper mode (with the phone notifications for one that must wake Master)

**Dependencies:** 200 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 202a: Ulangi contract ✅

**Acceptance criteria:**
- [x] SPEC "ulangi (v1.38)" (stories, non-goals, the phrase gate, what she remembers and what not, the guest marker, the repeat in `handle_utterance`, the confirmation gate, quiet, the phone decision, the CLI, known limits); tasks 202a-205 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 202: `is_repeat_request` ✅

**Acceptance criteria:**
- [x] Every phrase of `REPEAT_PHRASES` counts, with or without "maaf" before it, a question mark, her names, wake words, owner call, "master", and the softeners; "maaf, apa?" too
- [x] Anything with another word ("ulangi lagunya", "putar sekali lagi", "apa tadi lampunya sudah mati?", "tadi aku bilang apa?", "apa kabar"), a doubled word, a bare "apa?", or nothing left is not a repeat request
- [x] Fix pass: "sekali lagi" with "coba" or "tolong" ("coba sekali lagi" after a failure) asks her to try again and goes to the brain; the two still soften the other phrases ("coba ulangi")

**Dependencies:** 202a · **Files:** `src/waifu/repeat.py`, `tests/test_repeat.py` · **Scope:** S

## Task 203: Her last words, repeated without the brain ✅

**Acceptance criteria:**
- [x] A repeat request in the follow-up window or with the wake word says her last reply, announcements, or request answer again word for word with its emotion, opens the follow-up window, and asks the brain nothing; a lookalike goes to the brain
- [x] Nothing said yet: "Belum ada yang bisa kuulangi, Master."; the acknowledgement, reactions, the startup line, and a request answer that was not said are never repeated; a reply cut off is repeated whole
- [x] A question waiting for Master's yes stays waiting through a repeat, and "iya" after it confirms; quiet answers a repeat and keeps its held announcements; a phone reply is never repeated, and a phone "ulangi" is a brain turn
- [x] INFO "Repeating what she said last" without the text

**Dependencies:** 202 · **Files:** `src/waifu/app.py`, `tests/test_app.py` · **Scope:** M

## Task 204: Guest mode clears them ✅

**Acceptance criteria:**
- [x] `Assistant(guest_marker=...)`: once the marker has moved since she said it, a repeat request gets the nothing-to-repeat line; words said after the switch are repeated
- [x] Fix pass: the marker is read before the turn (a voice reply, a request's answer), so a reply whose turn switched guest mode ("berapa saldoku, terus nyalakan mode tamu", or a switch from the phone meanwhile) is never repeated
- [x] `run` passes guest mode's `generation` as `guest_marker`

**Dependencies:** 203 · **Files:** `src/waifu/app.py`, `tests/test_app.py`, `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 205: Docs ✅

**Acceptance criteria:**
- [x] README "Ulangi" with a contents entry, an example in "Pemakaian sehari-hari", and checklist items; CHANGELOG v1.38; full suite and ruff clean
- [x] Fix pass: README, SPEC, and CHANGELOG say "coba sekali lagi" goes to the brain and the reply that switched guest mode is not repeated; the privacy lines say only the repeat keeps the text out of the log (announcements were logged as `Announcing: ...` when first said)

**Dependencies:** 204 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 206a: Belajar bahasa Jepang contract ✅

**Acceptance criteria:**
- [x] SPEC "belajar bahasa jepang (v1.39)" (stories, non-goals, the word list and its validation, the word of the day, the Leitner boxes and the store, when a word counts as learned, `japanese_lesson`, the guest mode decision, the briefing's word, the persona, the config, the CLI, known limits); tasks 206a-213 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 206: The word list ✅

**Acceptance criteria:**
- [x] `src/waifu/japanese_words.json` ships as package data with at least 300 N5 words and phrases; `load_words` reads it into `Word`s and refuses a file that is not such a list
- [x] A test validates it: exact keys, unique ids, words, and examples, no empty field, the reading kana only, the kanji form and the example one Japanese run, the Indonesian fields Indonesian only and untouched by `clean_for_speech`
- [x] Fix pass: w088's example writes ten minutes in kana (じゅっぷん), since a lone 十分 reads じゅうぶん ("enough") to a speech engine; a test refuses a lone 十分 in any example (三十分 keeps its one reading)

**Dependencies:** 206a · **Files:** `src/waifu/japanese.py`, `src/waifu/japanese_words.json`, `pyproject.toml`, `tests/test_japanese.py` · **Scope:** M

## Task 207: The word of the day and the Leitner rules ✅

**Acceptance criteria:**
- [x] `word_of_the_day` is the same all day, differs the next day, and goes through every word before one repeats
- [x] `review` moves a right answer up a box (5 stays 5) and a wrong one to box 1, due after 1, 2, 4, 8, or 16 days; `next_quiz` picks the due word with the lowest box, then the earliest due

**Dependencies:** 206 · **Files:** `src/waifu/japanese.py`, `tests/test_japanese.py` · **Scope:** S

## Task 208: `JapaneseStore` ✅

**Acceptance criteria:**
- [x] A missing file is nothing learned and stays missing; `learn` writes a new word in box 1 due the same day and writes nothing for a known one; `grade` writes the review and returns None for a word never learned
- [x] A broken file is moved aside (a counter for a second one) and read as empty; a naive clock is refused; a byte order mark is read

**Dependencies:** 207 · **Files:** `src/waifu/japanese.py`, `tests/test_japanese.py` · **Scope:** M

## Task 209: `japanese_lesson` and the persona ✅

**Acceptance criteria:**
- [x] `japanese_lesson {action?, word_id?, correct?}` is non-strict with a static description; today, quiz, grade, and progress answer as the SPEC says; bad arguments, an unknown or unlearned word, and a file error are error results; her own turns get no quiz or grade
- [x] Both personas hold one identical "Belajar bahasa Jepang:" section before "Mode tamu:"
- [x] Fix pass: `grade` refuses a word not due yet ("Kata itu belum waktunya diulang, jadi tidak dinilai lagi."), writing nothing, so a turn retried after an API error never moves a word up twice; the description and both personas count the meaning of a common homophone (はな heard as 鼻) as right, or have her give a hint and ask again

**Dependencies:** 208 · **Files:** `src/waifu/brain/japanese_tools.py`, `tests/test_japanese_tools.py`, `config/persona.md`, `config/persona_hsr.md`, `tests/test_brain_persona.py` · **Scope:** M

## Task 210: The briefing's word ✅

**Acceptance criteria:**
- [x] With `japanese`, `daily_briefing` holds `word_of_the_day` (learned once), in guest mode too, null after a failure; without it no such field; the spec is the same either way
- [x] `BRIEFING_PROMPT` names the word of the day and still reads as no yes, look word, or play verb
- [x] Fix pass: only her morning greeting's turn (`begin_turn` hears `BRIEFING_PROMPT`) gets `word_of_the_day` and learns it; a weather or schedule question at any hour gets neither, and the spec stays the same

**Dependencies:** 208 · **Files:** `src/waifu/briefing.py`, `tests/test_briefing.py` · **Scope:** S

## Task 211: The `japanese` config ✅

**Acceptance criteria:**
- [x] `japanese.enabled` (false) and `japanese.path`; unknown keys refused naming the setting; `config.example.yaml` documents it and its block written out holds the defaults

**Dependencies:** 206a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** S

## Task 212: Wiring, guest mode, `waifu jepang`, and the check line ✅

**Acceptance criteria:**
- [x] With `japanese.enabled`, `run` and `chat` give the brain `JapaneseTools` and the briefing tool the word of the day; off, neither
- [x] `japanese_lesson` is sorted as open to guests and non-strict in the every-tool test; never refused by guest mode
- [x] `waifu jepang [hari-ini|progres]` prints today's word or the progress without creating the file, warns while off, and exits 1 when the file cannot be read
- [x] `waifu check` shows the progress right after the journal line, a moved file, or an unreadable one

**Dependencies:** 209, 210, 211 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `tests/test_guest.py` · **Scope:** M

## Task 213: Docs ✅

**Acceptance criteria:**
- [x] README "Belajar bahasa Jepang" with a contents entry, mentions in the briefing section and "Mode tamu", checklist items, and a Privasi bullet; CHANGELOG v1.39; full suite and ruff clean
- [x] Fix pass: the README checklist answers the one word wrong on the first day and right the next (a fresh install has no next word); README, SPEC, and CHANGELOG say the word is the greeting's only, that a common homophone's meaning counts, and that an answer is not graded twice

**Dependencies:** 212 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 214a: Pengingat istirahat contract ✅

**Acceptance criteria:**
- [x] SPEC "pengingat istirahat (v1.40)" (stories, non-goals, the input reader and its guard, the continuous-activity rule, the watcher, the config, the CLI, decisions, known limits); tasks 214a-219 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 214: The activity reader and its guard ✅

**Acceptance criteria:**
- [x] `tests/conftest.py` refuses `user32.GetLastInputInfo` (through `WinDLL` and `windll`) and records it; `GetTickCount64` and the other user32 functions still work
- [x] `read_input_idle` reads the last input, then the tick count, and gives the milliseconds since the last input, across the 49.7-day wrap of `dwTime`, 0 for a last input a moment newer than the tick count, and an `OSError` when Windows refuses; it loads user32 and kernel32 with their documented signatures at every call, and the real reader hits the suite's refusal

**Dependencies:** 214a · **Files:** `tests/conftest.py`, `tests/test_safety_guard.py`, `src/waifu/input_idle.py`, `tests/test_input_idle.py` · **Scope:** S

## Task 215: The `breaks` config ✅

**Acceptance criteria:**
- [x] `breaks.enabled` (false), `every_minutes` (60, 20-180), `idle_reset_minutes` (5, 2-15), `during_games` (false), `push` (false), `active_from`/`active_until` (both or neither, not equal, no time zone, across midnight allowed, bare YAML times read as clock times); bad values refused naming the setting
- [x] `config.example.yaml` documents it and its block written out holds the defaults

**Dependencies:** 214a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** S

## Task 216: The continuous-activity rule ✅

**Acceptance criteria:**
- [x] `follow_activity` starts counting at a poll with recent input, gives the line once the last input reaches `every_minutes` and then every `every_minutes` on the same grid, and never while the input is older than the due point
- [x] An idle gap of `idle_reset_minutes`, or a poll gap of that long by the tick count or the wall clock, starts the count again

**Dependencies:** 214 · **Files:** `src/waifu/break_watch.py`, `tests/test_break_watch.py` · **Scope:** S

## Task 217: `BreakWatcher` ✅

**Acceptance criteria:**
- [x] A due line is announced (relaxed, expiring after `idle_reset_minutes`) and pushed with the coffee tag when it has a notifier; either may fail without stopping the other or the watcher
- [x] A focus block and the time outside the active hours read nothing and reset the count; a failed read resets it, logged once at WARNING and its recovery at INFO
- [x] A daemon thread "break-watcher" polls at once and every 30 s from the start of the last poll, stops at once, and starts only once
- [x] Fix pass: the module docstring and the SPEC Decisions line say a line expires `idle_reset_minutes` after the poll that gives it, as the code and the SPEC BreakWatcher bullet already did, and a test pins it with a line given after its time; the voiceless watcher test gets the rig's fake clock like every other watcher

**Dependencies:** 215, 216 · **Files:** `src/waifu/break_watch.py`, `tests/test_break_watch.py` · **Scope:** M

## Task 218: Wiring and the check line ✅

**Acceptance criteria:**
- [x] With `breaks.enabled`, `run` starts the watcher while she listens with the real reader, the focus timer's `in_block`, and the notifier only with `breaks.push` and notifications on; off, none; `chat` never
- [x] The line is a patient announcement held by game mode (unsaid while a game runs in `phone` mode when pushed), and with `during_games` a reminder while game mode holds
- [x] `waifu check` shows the break reminder line right after the voice volume line

**Dependencies:** 217 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 219: Docs ✅

**Acceptance criteria:**
- [x] README "Pengingat istirahat" with a contents entry, mentions in "Teman fokus", "Mode game", and "Mode tamu", checklist items, and a Privasi bullet; CHANGELOG v1.40; full suite and ruff clean

**Dependencies:** 218 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 220a: Waktu sholat contract ✅

**Acceptance criteria:**
- [x] SPEC "waktu sholat (v1.41)" (stories, non-goals, the math, the place, the tool, the reminders and their state file, the config, the CLI, decisions, known limits, privacy, safety); tasks 220a-226 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 220: The prayer time math ✅

**Acceptance criteria:**
- [x] `solar_noon` and `sun_at_altitude` agree with NOAA's independent fractional-year formulas within a minute for every day of a year in Jakarta and Bandung
- [x] `prayer_times` gives Kemenag's schedule for Jakarta within 2 minutes on 3+ dates, Dzuhur about 11:39 in early November and 12:09 in mid-February; the ihtiyath is added and rounded up, terbit has it subtracted and rounded down, imsak is Subuh minus 10 minutes
- [x] Far north: no Subuh, Isya, or imsak at 60°N at the June solstice (Dzuhur and Maghrib sane), no terbit, Ashar, or Maghrib in a polar night
- [x] Fix pass: the SPEC says Subuh goes from about 46.6°N and Isya from about 48.6°N at the June solstice (it said both from 48.6°), and a test pins the three bands at 46.4°, 47.5°, and 48.7°N

**Dependencies:** 220a · **Files:** `src/waifu/prayer_times.py`, `tests/test_prayer_times.py` · **Scope:** M

## Task 221: The `prayer` config ✅

**Acceptance criteria:**
- [x] `prayer.enabled` (false), `latitude`/`longitude` (both or neither, in range), `ihtiyath_minutes` (2, 0-5), `remind` (all five, each once, `[]` allowed), `minutes_before` (0, 0-30), `push` (false), `state_path`; bad values refused naming the setting
- [x] `config.example.yaml` documents it and its block written out holds the defaults

**Dependencies:** 220a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** S

## Task 222: The place ✅

**Acceptance criteria:**
- [x] `WeatherClient.location()` gives coordinates without a request, geocodes a city once for the client's life, and raises `WeatherError` when it fails
- [x] `PrayerPlace.locate()` gives the place, says "lokasi belum diatur" without one, and turns a `WeatherError` into a `PrayerError` with its message

**Dependencies:** 220a · **Files:** `src/waifu/weather.py`, `tests/test_weather.py`, `src/waifu/prayer.py`, `tests/test_prayer.py` · **Scope:** S

## Task 223: `prayer_times` ✅

**Acceptance criteria:**
- [x] Today's or a given day's times for the place as "HH:MM" (null where none), with the place, weekday, method, and ihtiyath; a date not YYYY-MM-DD, unknown keys, or a day more than 366 days away refused; a place that cannot be found is an error with its message; never raises
- [x] Refused in the morning greeting, the weekly report, and the journal question; the spec is static and non-strict
- [x] Fix pass: a place known only by its coordinates (`PrayerPlace(named=False)`) gets a null `place`, which the description says not to name, so the coordinates never reach the model; for the schedule the description asks for the five prayers, imsak or terbit only when Master asks for them

**Dependencies:** 220, 221, 222 · **Files:** `src/waifu/brain/prayer_tools.py`, `tests/test_prayer_tools.py` · **Scope:** M

## Task 224: `PrayerWatcher` ✅

**Acceptance criteria:**
- [x] A prayer in `remind` is said once from `minutes_before` before its time until 30 minutes after it ("sudah masuk waktu", or the minutes left), through `remind` with `neutral`, and pushed with the mosque tag when it has a notifier; either may fail without stopping the other
- [x] The told prayers are written to the state file before she says them, so a restart within the window says nothing; another day's file counts as nothing told; a broken file is moved aside; a file that cannot be read or written is logged and she still says it once
- [x] A place that cannot be found is never said, logged once at WARNING, retried after 15 minutes; a daemon thread "prayer-watcher" polls at once and every 30 s, stops at once, and starts only once

**Dependencies:** 220, 221, 222 · **Files:** `src/waifu/prayer_watch.py`, `tests/test_prayer_watch.py` · **Scope:** M

## Task 225: Wiring, guest mode, `waifu sholat`, and the check line ✅

**Acceptance criteria:**
- [x] With `prayer.enabled`, `run` and `chat` give the brain `PrayerTools`; `run` starts the watcher with one shared place, `assistant.remind`, and the notifier only with `prayer.push` and notifications on; off, or with an empty `remind`, no watcher; `chat` never
- [x] `prayer_times` is sorted as open to guests and non-strict in the every-tool test
- [x] `waifu sholat [YYYY-MM-DD]` prints the times, warns while off, and exits 1 without a place or when the lookup fails
- [x] `waifu check` shows the prayer line right after the break reminder line, and a moved state file
- [x] Fix pass: `prayer` coordinates are named by `briefing.city` as the weather names them (`waifu sholat` prints `Waktu sholat di Bandung, ...`), any place without a city is not `named`, and the check line writes `untuk Bandung (-6.9175, 107.6191)` with both

**Dependencies:** 223, 224 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 226: Docs ✅

**Acceptance criteria:**
- [x] README "Waktu sholat" with a contents entry, mentions in "Teman fokus", "Mode game", and "Mode tamu", checklist items, and a Privasi bullet; CHANGELOG v1.41; full suite and ruff clean

**Dependencies:** 225 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 227a: Tombol cepat di PC contract ✅

**Acceptance criteria:**
- [x] SPEC "tombol cepat di PC (v1.42)" (stories, non-goals, the shared parts, the server and its checks, the page, the CLI, guest mode, decisions with the threat model, known limits, privacy, safety); tasks 227a-231 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 227: Share the quick buttons' limits and the JSON handling ✅

**Acceptance criteria:**
- [x] `ACTION_MAX_BYTES`, `ACTION_RATE_LIMIT`, `ACTION_RATE_WINDOW_S`, `ACTION_UNKNOWN_TEXT`, `ACTION_LIMIT_TEXT`, `ACTION_BUSY_TEXT`, and `group_json` live in `phone/actions.py`; the phone server imports them
- [x] The phone handler's body reading, strict JSON fields, refusal draining, and replies are `JsonRequestHandler`, which `_PhoneRequestHandler` extends; the whole phone server suite passes unchanged

**Dependencies:** 227a · **Files:** `src/waifu/phone/actions.py`, `src/waifu/phone/server.py` · **Scope:** S

## Task 228: `DeskServer` ✅

**Acceptance criteria:**
- [x] `QuickActions(where=...)` names that place in the PC refusal ("tombol cepat PC")
- [x] The page, its files, `actions`, and `action` only under `/t/<token>/` (a wrong or missing token 404, each server its own token); a foreign Host 403 even with the token; a POST from any Origin but the page's own 403; OPTIONS and GET `action` 405 without CORS headers; `PROTECTING_HEADERS` and `Referrer-Policy: no-referrer` on every response
- [x] A press runs its button and answers `{"ok", "message"}`; the PC and its plug 404 with nothing run; ten presses a minute, refused ones counted; 409 while one runs; 400, 411, 413, 415; 500 with `FAILED_TEXT`; the token never logged; a host that is not loopback is a `ValueError`

**Dependencies:** 227 · **Files:** `src/waifu/phone/actions.py`, `src/waifu/phone/desk.py`, `tests/test_phone_actions.py`, `tests/test_phone_desk.py` · **Scope:** M

## Task 229: The page ✅

**Acceptance criteria:**
- [x] `desk.js`: the problem of each status, a request that resolves to data or a problem and never rejects (closed, timeout, bad reply), the PC page's texts
- [x] `tombol.js` shows the groups or why not, posts only the clicked id, marks it busy and the rest unavailable until the answer, ignores clicks meanwhile, and toasts the answer or the problem for 6 s
- [x] `index.html` sets its CSP before any script, runs no inline script or style, and loads only relative files that exist; `tombol.css` has the desktop grid
- [x] Fix pass: `requestDesk` asks for `mode: "cors"` and `referrerPolicy: "same-origin"`, so a POST keeps the page's own Origin under the server's `no-referrer` (with mode "same-origin" the Fetch spec, which Firefox follows, sends `Origin: null`, and every press got a 403)

**Dependencies:** 228 · **Files:** `src/waifu/avatar/web/tombol/*`, `tests/js/tombol_desk.test.mjs`, `tests/js/tombol_page.test.mjs`, `tests/js/tombol_ui.test.mjs`, `SPEC.md` · **Scope:** M

## Task 230: `waifu tombol` and the check line ✅

**Acceptance criteria:**
- [x] `waifu tombol` serves the page, opens exactly its `http://127.0.0.1:` address once, says the address and Ctrl+C, and ends with "Selesai." and the server stopped; `--dry-run` presses the mocks; no buttons is a warning and exit 1 with nothing opened; a browser that cannot be opened still serves the page
- [x] `waifu check` counts the page's buttons right after the routine lines
- [x] Fix pass: when a routine button has a step with `after_minutes`, `waifu tombol` says under the address line who runs it: `DRY_RUN_DELAYED_STEPS` on a dry run, `DELAYED_STEPS_IN_RUN` while the run lock is held, else `! 'waifu run' tidak sedang berjalan: ...`; nothing without such a button

**Dependencies:** 228, 229 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `SPEC.md` · **Scope:** M

## Task 231: Docs ✅

**Acceptance criteria:**
- [x] README "Tombol cepat di PC" with a contents entry, a checklist item, and a Privasi bullet; CHANGELOG v1.42; full suite, JS tests, and ruff clean
- [x] Fix pass: the README command table has `waifu sholat` and `waifu tombol` and its `waifu check` row names both lines; the page opens in the "browser default" (not "bawaan Windows", which reads as Edge); `waifu tombol` must be restarted after a config change; delayed routine steps still need `waifu run` on at their time

**Dependencies:** 230 · **Files:** `README.md`, `CHANGELOG.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 232a: Jadwal mingguan, bulanan, tahunan contract ✅

**Acceptance criteria:**
- [x] SPEC "jadwal mingguan, bulanan, tahunan (v1.43)" (stories, non-goals, the next occurrence, the anchor day and the file, the missed rule, the tools, the CLI, decisions, known limits, safety) and pointers from the v1.7 schedule section; tasks 232a-237 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 232: The next weekly, monthly, and yearly occurrence ✅

**Acceptance criteria:**
- [x] `REPEATS` gains weekly, monthly, and yearly; the refusal of an unknown repeat lists `REPEATS`
- [x] `next_occurrence(at, repeat, after, *, day=None)`: weekly on `at`'s weekday (across a year's end too); monthly on `min(day, the month's length)` (an entry of the 31st walks 30 November, 31 December, 31 January, 28 February 2027, 31 March, and 29 February 2028); yearly on `at`'s month and `min(day, its length)` (29 February is 28 February 2027 and 29 February 2028); always later than `after` at `at`'s clock time, by whole calendar days
- [x] The tests that used "monthly" as an unknown repeat use another one; the `schedule` enum test lists the seven values

**Dependencies:** 232a · **Files:** `src/waifu/schedule.py`, `tests/test_schedule.py`, `tests/test_schedule_tools.py` · **Scope:** S

## Task 233: The anchor day ✅

**Acceptance criteria:**
- [x] `Entry.day` (last field, default None) holds the day of a monthly or yearly entry, from the `at` passed to `add`; `fired` and `skipped` move an entry with it
- [x] `add` starts a weekly, monthly, or yearly entry passed a past `at` at its first occurrence from the current minute; a yearly one ignores the year (2026-05-05 added on 24 October 2026 is 5 May 2027; 2028-02-29 is 28 February 2027); the older repeats still refuse a past time, and a weekly or monthly one over 366 days ahead is still refused
- [x] The file has `day` only on monthly and yearly entries and reads back equal; a `day` on another entry, a missing one, 0, 32, a boolean, text, one `at` disagrees with, or a yearly 31 April is not a schedule

**Dependencies:** 232 · **Files:** `src/waifu/schedule.py`, `tests/test_schedule.py` · **Scope:** M

## Task 234: The tools ✅

**Acceptance criteria:**
- [x] The `schedule` description explains weekly, monthly, and yearly, a past `at` for them, the next month that has the day, birthdays (yearly, 08:00 unless Master says a time, a reminder in the owner's call word) and bills (monthly), and keeps its earlier guidance
- [x] The `schedule` result and `list_schedules` give `day` for a monthly or yearly entry only; the description of `list_schedules` tells her to pick "this month" from the list herself
- [x] No schema changes but the enum; the strict-tool budget stays
- [x] Fix pass: the `schedule` description says to pass a leap year's date such as 2028-02-29 for 29 February (2027-02-29 is refused as a date and 2027-02-28 would store the 28th for good); bills get the 08:00 default and a reminder text as birthdays do, as the README and CHANGELOG promise; a weekly entry's `schedule` and `list_schedules` results name its `weekday` (`WEEKDAYS_ID`), and the descriptions say to use it, so "tiap Senin" never rests on her counting from the date

**Dependencies:** 233 · **Files:** `src/waifu/brain/schedule_tools.py`, `tests/test_schedule_tools.py` · **Scope:** S

## Task 235: `waifu schedule list` and `cancel` ✅

**Acceptance criteria:**
- [x] They say `tiap Senin`, `tiap tanggal 31`, and `tiap 5 Mei` (`tiap 29 Februari`), and the older repeats as before

**Dependencies:** 233 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 236: A missed monthly or yearly reminder is told ✅

**Acceptance criteria:**
- [x] A monthly or yearly reminder missed beyond the grace is told in the tick's one announcement (with its date when not today's) and pushed; a missed daily, weekdays, weekends, or weekly one is still only pushed; the entry moves to its next time
- [x] Fix pass: `_missed_text`'s docstring says it announces missed one-shot, monthly, and yearly reminders, as the module docstring, `_settle`, and `_skip` do

**Dependencies:** 233 · **Files:** `src/waifu/scheduler.py`, `tests/test_scheduler.py` · **Scope:** S

## Task 237: Docs ✅

**Acceptance criteria:**
- [x] README "Jadwal, pengingat, dan rutinitas": weekly, monthly, and yearly examples (birthdays, bills), the month-end and 29 February rules, the missed rule, the list example; CHANGELOG v1.43 with the downgrade note; full suite and ruff clean

**Dependencies:** 234, 235, 236 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 238a: Cuaca beberapa hari contract ✅

**Acceptance criteria:**
- [x] SPEC "cuaca beberapa hari (v1.44)" (stories, non-goals, the client, the days and parts, the tool, the CLI, guest mode, decisions, known limits, privacy, safety) and pointers from the v1.12 briefing and v1.25 rain sections; tasks 238a-243 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 238: `WeatherClient.outlook()` ✅

**Acceptance criteria:**
- [x] `outlook()` asks once with `OUTLOOK_QUERY` (seven days; daily code, range, rain chance, and amount; hourly code, temperature, rain chance, and amount) and reads `Outlook(place, now, days, hours)`; null daily or hourly values are None
- [x] The same city cache, 8 s limit, `failure_level`, and errors as `today()` and `forecast()`; a reply without what it reads is the unreadable `WeatherError`
- [x] `FORECAST_QUERY`, `today()`, and `forecast()` unchanged; `Hour.temperature_c` defaults to None; `condition_of` is public

**Dependencies:** 238a · **Files:** `src/waifu/weather.py`, `tests/test_weather.py` · **Scope:** M

## Task 239: The days and parts asked ✅

**Acceptance criteria:**
- [x] `period_of`: a later day's daily values; the rest of today and any part from the hourly entries after now (highest code, highest chance, summed amount, lowest and highest temperature, known values only); a part from a to b reads the entries after a up to b (night with the next day's 00:00); None when a part of today has passed
- [x] `periods`: today, tomorrow, the weekend (Monday-Friday, Saturday, Sunday), a date of the reply; a date out of reach and a passed part are a `ForecastError`; after midnight today is the new date

**Dependencies:** 238 · **Files:** `src/waifu/brain/weather_tools.py`, `tests/test_weather_tools.py` · **Scope:** M

## Task 240: The `weather_forecast` tool ✅

**Acceptance criteria:**
- [x] Non-strict `weather_forecast {day?, part?}` with a static English description (routing against `daily_briefing`, one call per question, the parts, null place, never in her own turns)
- [x] The result names the place as written (null with coordinates), the days in Indonesian, whole degrees, and a whole day's parts; unknown values left out
- [x] Bad arguments, Open-Meteo failures, out-of-reach questions, and anything unexpected are error results; her own turns get a refusal
- [x] Fix pass: the description sends the weather now and today as a whole to `daily_briefing` only when she has it, and to this tool with `day` today without it (the tool is given with `briefing.enabled: false` too); still one static string

**Dependencies:** 239 · **Files:** `src/waifu/brain/weather_tools.py`, `tests/test_weather_tools.py` · **Scope:** M

## Task 241: The brain gets it ✅

**Acceptance criteria:**
- [x] `run` and `chat` add the tool right after the briefing tool whenever `briefing` names a place, also with the briefing off; the briefing tool and it share one client (one city lookup); nothing is asked at startup
- [x] Guest mode leaves it open; the every-tool request keeps 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions

**Dependencies:** 240 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 242: `waifu cuaca` ✅

**Acceptance criteria:**
- [x] `waifu cuaca [hari-ini|besok|akhir-pekan|YYYY-MM-DD]` prints the place, the time, each day, and its parts not passed; no place, an Open-Meteo failure, or a day out of reach is a `✗` line and exit 1; a bad word or date is an argparse error
- [x] Polish: a lowest and highest that print the same are one temperature (`20°C`, not `20-20°C`), also in `waifu briefing`; the tool's description says to say equal `min_c` and `max_c` once

**Dependencies:** 239 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 243: Docs ✅

**Acceptance criteria:**
- [x] README "Cuaca beberapa hari" after "Peringatan hujan" with a contents entry, the command row, a mention in "Mode tamu", a checklist item, and a Privasi bullet; CHANGELOG v1.44; full suite and ruff clean
- [x] Fix pass: the README, its checklist, and the CHANGELOG say "cuaca hari ini" comes from the rest of today's forecast with the briefing off; the checklist's `waifu cuaca akhir-pekan` names what a Saturday and a Sunday show

**Dependencies:** 241, 242 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 244a: Rekap hari ini contract ✅

**Acceptance criteria:**
- [x] SPEC "rekap hari ini (v1.45)" (stories, non-goals, the additions, the gatherer, the JSON, the tool, the CLI, guest mode, decisions, known limits, privacy, safety); tasks 244a-249 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 244: The day's pulls and the day's schedule ✅

**Acceptance criteria:**
- [x] `pulls_since(..., until=)` counts only `since < moment <= until`; without `until` it counts as before
- [x] `occurrence_on(entry, day)`: a one-shot entry on its date; a repeating one (daily, weekdays, weekends, weekly, monthly with the month-end rule, yearly) at its clock time on its days, also when its `at` has moved on, never before it was created
- [x] Fix pass: `occurrence_on(entry, day, *, now)` counts nothing before a later start not yet come (an `at` later than the entry's first time after now); the window from the last time before the start, and the days before it after the first fire, are SPEC known limits

**Dependencies:** 244a · **Files:** `src/waifu/gacha/history.py`, `src/waifu/schedule.py`, `tests/test_gacha_history.py`, `tests/test_schedule.py` · **Scope:** S

## Task 245: The gatherer ✅

**Acceptance criteria:**
- [x] `DailyRecap.gather(day)`: the tasks done in the WIB day and still due, the focus seconds, the play time per game and its union total, the day's pulls per game with their tops, the words learned and last reviewed, the journal's note count, and the reminders and routines due that day with `passed`; the window ends now for today
- [x] A part that is off is absent, one that fails is logged and gets its Indonesian problem while the rest is read; `recap_day` takes today, yesterday, or a date up to 30 days back
- [x] `recap_json`: compact, the parts with something only, lists of at most 10 with whole counts, `as_of` today only, `problems` when any; never a journal text
- [x] Fix pass: play is there only when a game was open a whole minute (`PlayDay.played`); a day that began more than `todo.keep_done_days` before now has its done tasks unknown (`done` None, `"done_known": false` in the JSON), never none

**Dependencies:** 244 · **Files:** `src/waifu/recap.py`, `tests/test_recap.py` · **Scope:** M

## Task 246: The `daily_recap` tool ✅

**Acceptance criteria:**
- [x] Non-strict `daily_recap {date?}` with a static English description (looking back vs `daily_briefing`, the parts, the journal counted only, no money, highlights first, play time never judged, never in her own turns)
- [x] Bad arguments, a date out of range, every part failing, and anything unexpected are error results; her own turns get a refusal
- [x] Fix pass: the description says `done_known` false is never "none were finished"; the `daily_briefing` description (`src/waifu/briefing.py`) no longer offers itself "for a summary of the day" and sends a look back to `daily_recap` when she has it

**Dependencies:** 245 · **Files:** `src/waifu/brain/recap_tools.py`, `tests/test_recap_tools.py` · **Scope:** M

## Task 247: The brain gets it ✅

**Acceptance criteria:**
- [x] `run` and `chat` add the tool after the Japanese lessons whenever any of its sources is on, with `run`'s focus timer (elsewhere the focus log), the run's schedule, and the local pull store; none with every source off
- [x] `daily_recap` is in `PRIVATE_TOOLS` (a full request carries every private tool, so it lands with the wiring); the every-tool request keeps 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions

**Dependencies:** 246 · **Files:** `src/waifu/cli.py`, `src/waifu/guest.py`, `tests/test_cli.py`, `tests/test_guest.py` · **Scope:** S

## Task 248: `waifu rekap` ✅

**Acceptance criteria:**
- [x] `waifu rekap [hari-ini|kemarin|YYYY-MM-DD]` prints the day, a line per part that is on, and a `✗` line and exit 1 for a part that failed; every part off is a `!` line; a bad word, date, or a date out of range is an argparse error
- [x] Fix pass: `Main game: tidak ada` when no game was open a whole minute (never `()`); `Tugas: yang selesai tidak tersimpan lagi (todo.keep_done_days: N), ...` for unknown done tasks; a ZZZ top pull is an `S-rank`, worded by `_top_word` and `_top_pull_text` as in `waifu gacha history`

**Dependencies:** 245 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 249: Docs ✅

**Acceptance criteria:**
- [x] README "Rekap hari ini" after "Jurnal harian" with a contents entry, the command row, the "Mode tamu" bullet, a checklist item, and a Privasi bullet; CHANGELOG v1.45; full suite and ruff clean
- [x] Fix pass: "Privasi rekap" and the SPEC's privacy line say the spoken answer goes to edge-tts with the `edge` engine, as the other privacy sections do; the README names the later-start limit and the unknown done tasks

**Dependencies:** 247, 248 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 250a: Alarm bangun contract ✅

**Acceptance criteria:**
- [x] SPEC "alarm bangun (v1.46)" (stories, non-goals, the entry, the bell, the phrases, the voice loop, the volume, the scheduler, the tool, the quick buttons, config, CLI, decisions with the strict-budget choice, known limits, privacy, safety); v1.37's known limit names the alarm; tasks 250a-259 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 250: The alarm entry ✅

**Acceptance criteria:**
- [x] `AlarmAction(label)` is added, listed, cancelled, fired, and skipped like any entry, with every repeat; its label is checked (collapsed, empty is None, at most 100 characters); the file writes `{"type": "alarm"[, "label"]}` and reads it back, and refuses a malformed one; a file without alarms is written as before
- [x] `list_schedules`/`cancel_schedule`, `waifu schedule list`/`cancel`, the morning briefing (left out in guest mode), and the daily recap name an alarm, never as a routine

**Dependencies:** 250a · **Files:** `src/waifu/schedule.py`, `src/waifu/brain/schedule_tools.py`, `src/waifu/briefing.py`, `src/waifu/recap.py`, `src/waifu/cli.py` and their tests · **Scope:** M

## Task 251: The bell ✅

**Acceptance criteria:**
- [x] `AlarmBell.start` writes the ring file; a second alarm joins the ring; `due_ring` gives each ring once at `index * repeat_seconds`, its level `min(150, day level + 20 * index)` and its short text; it ends by itself at `until` and deletes the file
- [x] `stop` deletes the file; `snooze` adds a one-shot alarm rounded up to a whole minute and stops, keeps ringing on a refusal, and gives None when nothing rings
- [x] A new bell on a file under way resumes at the index of the time passed; an expired file is deleted; a broken one is moved aside; a stop by another bell on the file is seen within `REREAD_S`; reading never raises
- [x] Fix pass: an ended state kept from an earlier read is read afresh before the delete, so a ring another bell has just started survives; an ended ring's undeletable file is logged once and retried at each read; a snooze whose ring file cannot be deleted cancels the entry it added and raises the OSError; `repeat_seconds` and `max_minutes` are public, beside `snooze_minutes`

**Dependencies:** 250 · **Files:** `src/waifu/alarm.py`, `tests/test_alarm.py` · **Scope:** M

## Task 252: The phrases ✅

**Acceptance criteria:**
- [x] `alarm_request` gives stop for the stop phrases and snooze (with its minutes, digits or words, 1-30) for the snooze phrases, leaving out her names, "master", and the softeners; None for "matikan lampu", "matikan musik", a bare "stop", "matikan", "udah", "bangun", and any phrase with another word
- [x] Fix pass: "tunda N menit lagi" and "snooze N menit lagi" snooze; "tunda sebentar" snoozes for the bell's minutes; "aku bangun", "dah bangun", and "aku dah bangun" stop; "aku bangun jam 6" and "tunda 5 menit lagi lagi" stay no request

**Dependencies:** 250a · **Files:** `src/waifu/alarm.py`, `tests/test_alarm.py` · **Scope:** S

## Task 253: The scheduler and alarms ✅

**Acceptance criteria:**
- [x] A due alarm starts the bell, is pushed once with `alarm_clock` (and the phone page's link) when `alarm_push`, and is marked fired; without a bell its wake text is said once and pushed
- [x] An alarm late by more than two minutes (or the smaller grace) never rings, is pushed as missed (with its date when not today's) and never told; a daily one moves to tomorrow

**Dependencies:** 251 · **Files:** `src/waifu/scheduler.py`, `tests/test_scheduler.py` · **Scope:** S

## Task 254: The volume ✅

**Acceptance criteria:**
- [x] `LevelHold.level_now` is the held level inside `held(level)` and `base()` otherwise; `VoiceLevel.day_level` ignores whisper mode and never raises

**Dependencies:** 250a · **Files:** `src/waifu/voice/volume.py`, `src/waifu/voice_level.py`, `tests/test_voice_volume.py`, `tests/test_voice_level.py` · **Scope:** S

## Task 255: The voice loop ✅

**Acceptance criteria:**
- [x] A due ring is said by itself between utterances, at its level through `hold_level`, as a reply, also while quiet holds and in the follow-up window, never remembered for "ulangi"; requests wait while it rings
- [x] While it rings, a stop or snooze phrase is answered without the brain (no turn, a pending confirmation waits), with or without her name; "matikan lampu" goes to the brain; nothing ringing leaves every utterance as before
- [x] Fix pass: a ring file that cannot be changed (OSError) makes her say the alarm could not be stopped or snoozed yet, logged, and the ring goes on

**Dependencies:** 251, 252, 254 · **Files:** `src/waifu/app.py`, `tests/test_app.py` · **Scope:** M

## Task 256: The `alarm` tool ✅

**Acceptance criteria:**
- [x] Non-strict `alarm {action?, at?, repeat?, label?, id?, minutes?}` sets, lists, cancels (alarms only), stops, and snoozes; bad arguments are error results; guest mode refuses `list` and `cancel`; her own turns are refused
- [x] The `schedule` description sends waking Master to `alarm` with one static sentence
- [x] Fix pass: the description tells the bell's `repeat_seconds` and `max_minutes` ("every 2 minutes", "20 minutes pass"), built once in `__init__`

**Dependencies:** 251 · **Files:** `src/waifu/brain/alarm_tools.py`, `src/waifu/brain/schedule_tools.py`, `tests/test_alarm_tools.py`, `tests/test_schedule_tools.py` · **Scope:** M

## Task 257: The quick buttons ✅

**Acceptance criteria:**
- [x] `QuickActions` lists an "Alarm" group first only while the bell rings, finds `al.stop` and `al.snooze` whenever there is a bell, and runs them outside the devices' lock with friendly messages after the ring ended; both servers accept the ids
- [x] `phone.js` reads the buttons again when the page shows and when the panel opens, hides an empty toggle, and opens the panel for an alarm button; `tombol.js` reads them again every 15 s and on focus, not while a press runs
- [x] Fix pass: both pages read the buttons again right after an alarm button (`isAlarmAction` in `actions.js`); `phone.js` reads them every 15 s while the alarm group shows and the page is visible, stops on hide and pagehide, and rebuilds after a hide so a new ring reopens the panel; the docstring and SPEC say a stop waits for a press its own page still runs

**Dependencies:** 251 · **Files:** `src/waifu/phone/actions.py`, `src/waifu/avatar/web/phone/phone.js`, `src/waifu/avatar/web/tombol/tombol.js`, their Python and JS tests · **Scope:** M

## Task 258: Config, wiring, check ✅

**Acceptance criteria:**
- [x] `AlarmConfig` with its ranges and the example block; `run` builds one bell (a dry run's in its temporary folder) for the Assistant, the brain, the phone, and the scheduler, plays through `LevelHold`; `chat` gives its tool a bell and its scheduler none; `waifu tombol` passes the bell
- [x] The every-tool request keeps 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions with `alarm` non-strict and open in guest mode
- [x] `waifu check` says how alarms ring, warns when they are off or the schedule is, and names a broken file moved aside

**Dependencies:** 253, 255, 256, 257 · **Files:** `src/waifu/config.py`, `config.example.yaml`, `src/waifu/cli.py`, `tests/test_config.py`, `tests/test_cli.py` · **Scope:** M

## Task 259: Docs ✅

**Acceptance criteria:**
- [x] README "Alarm bangun" after "Jadwal, pengingat, dan rutinitas" with a contents entry, checklist items, and a line in "Volume suaraku"; CHANGELOG v1.46 with the downgrade note; full suite, ruff, and JS tests clean

**Dependencies:** 258 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 260a: Diagnosa suara contract ✅

**Acceptance criteria:**
- [x] SPEC "diagnosa suara (v1.47)" (stories, non-goals, the level tap, the segmenter, the records, the heard request, the preferences and the switch, the override file, the panel, the phone server, the page, CLI, decisions with the PortAudio restart, known limits, privacy, safety); tasks 260a-270 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 260: Test guards ✅

**Acceptance criteria:**
- [x] Once sounddevice is imported, a test that opens a real input stream, plays, records, or restarts PortAudio fails, also when the code under test catches the refusal; winmm's `waveInGetNumDevs` and `waveInGetDevCapsW` are refused like the other system calls
- [x] Fix pass: the test that imports sounddevice first (a forgotten fake run on its own) is refused too, by an import hook that patches through the running test's monkeypatch, put back after the test

**Dependencies:** 260a · **Files:** `tests/conftest.py`, `tests/test_safety_guard.py` · **Scope:** S

## Task 261: The level tap ✅

**Acceptance criteria:**
- [x] `MicStream.level` gives the RMS and peak of the last second (paused or not) without keeping samples; `frames()` raises `MicrophoneFlat` after 8 s of exact zeros, not for a quiet mic or while paused; `name` and `host_api` name the opened device
- [x] `restart_portaudio` terminates and initialises PortAudio (only initialises when it is not running) and waits for a clip `Player.play` is playing
- [x] Fix pass: `MicrophoneFlat` is raised once per run of zeros, and `frames()` called again reads on; `restart_portaudio` leaves its log line to the caller

**Dependencies:** 260 · **Files:** `src/waifu/voice/audio.py`, `tests/test_voice_audio.py` · **Scope:** M

## Task 262: The segmenter ✅

**Acceptance criteria:**
- [x] `floor_db` and `bar_db` follow the last frame; `set_gate` changes the bar at the next frame; `relearn` forgets the floor; a segment dropped for too little speech reaches `on_too_short`

**Dependencies:** 260a · **Files:** `src/waifu/voice/segmenter.py`, `tests/test_voice_segmenter.py` · **Scope:** S

## Task 263: The records ✅

**Acceptance criteria:**
- [x] `UtteranceLog` keeps 50 records newest first, filtered by guest mode's marker, `last_heard` skipping too-short and heard-request records; `UtteranceRecord.of` measures a segment
- [x] `handle_utterance` records not-owner (score, threshold), empty, no-wake, and accepted utterances with Whisper's, the brain's, and the voice's times; nothing changes without a log
- [x] Fix pass: `UtteranceLog.stamp()` and `add(record, stamp=...)`: a record carries guest mode's generation as it was heard, so a turn that switched guest mode is never shown or told after it

**Dependencies:** 262 · **Files:** `src/waifu/voice/diagnosis.py`, `src/waifu/app.py`, `tests/test_voice_diagnosis.py`, `tests/test_app.py` · **Scope:** M

## Task 264: "Tadi kamu denger apa?" ✅

**Acceptance criteria:**
- [x] `is_heard_request` takes the heard phrases with her names and the softeners left out and nothing else; no heard phrase is a repeat phrase and no repeat phrase a heard one
- [x] She tells the last utterance and its outcome without the brain, not remembered for "ulangi", forgotten when guest mode switches

**Dependencies:** 263 · **Files:** `src/waifu/heard.py`, `src/waifu/app.py`, `tests/test_heard.py`, `tests/test_app.py` · **Scope:** S

## Task 265: Preferences and the override file ✅

**Acceptance criteria:**
- [x] `audio.input_device` takes a number, a name, a non-empty list of them, or null; `input_preferences` lists them; `audio.override_path`
- [x] `VoiceOverrideStore` reads, writes atomically, sets a broken file aside, and keeps every value within its bounds

**Dependencies:** 260a · **Files:** `src/waifu/config.py`, `src/waifu/voice_override.py`, `tests/test_config.py`, `tests/test_voice_override.py` · **Scope:** S

## Task 266: `MicSource` ✅

**Acceptance criteria:**
- [x] It opens the first preference that opens and says so when it is not the first; a failing or flat mic fails over with one line, the same mic reopened says nothing; with none, silent frames and a retry every 5 s
- [x] A changed Windows input list restarts PortAudio between frames and switches back to a more preferred mic with one line; no restart with a numbered device
- [x] The phone's switch runs on `run`'s thread, rolls back when the mic does not open, answers pending when not taken in time, and keeps a paused mic paused
- [x] Fix pass: flat mics are skipped until the list changes or the phone picks one; with only flat mics she stays on one, says `ALL_FLAT` once, and logs once; `MIC_BACK` only after a list change; a more preferred mic the list gained is retried every `RETRY_S`, at most `RETURN_TRIES` (3), and a phone switch ends the tries; following the config again is kept even when no mic opens; restarts log at DEBUG while no mic opens

**Dependencies:** 261, 262 · **Files:** `src/waifu/voice/mic_source.py`, `tests/test_voice_mic_source.py` · **Scope:** L

## Task 267: The panel and the server ✅

**Acceptance criteria:**
- [x] `VoicePanel.status` gives the mic, the default, the devices, the level, the gate, the speaker threshold, the switch, and the records; `switch_mic` takes only a listed mic and writes the override after it worked; `tune` and `reset` apply at once within the bounds
- [x] GET /voice and POST /voice/set need the cookie (and the POST the page's Origin), count toward the quick buttons' limit, take strict JSON numbers, and refuse guest mode with 403

**Dependencies:** 263, 265, 266 · **Files:** `src/waifu/voice/panel.py`, `src/waifu/phone/server.py`, `tests/test_voice_panel.py`, `tests/test_phone_server.py` · **Scope:** M

## Task 268: The "Suara" sheet ✅

**Acceptance criteria:**
- [x] `voice.js` parses the status, places the level and the gate, names and colours the outcomes, and builds the requests; the sheet polls every 1.5 s only while open and visible, one request at a time, hides itself on 403 or 404, and switches, saves, and resets; `sw.js` keeps `voice.js`
- [x] Fix pass: `tuningFrom` sends only changed boxes and refuses an empty save; `levelLine` names exact zeros; the sheet reads mic (with `#voice-mic-note`), records (with `#voice-records-empty`), then settings; a pending switch's outcome and a failed read's problem show and go in the picker's note; the picker shows the mic in effect at each opening and after each switch, an unplugged pick as "(tidak tersambung)"; `#voice-toggle` has a 44 px hit area

**Dependencies:** 267 · **Files:** `src/waifu/avatar/web/phone/*`, `tests/js/phone_voice.test.mjs`, `tests/js/phone_ui.test.mjs`, `tests/js/phone_sw.test.mjs`, `tests/js/phone_page.test.mjs` · **Scope:** M

## Task 269: Wiring and the check line ✅

**Acceptance criteria:**
- [x] `run` builds the log, the segmenter, the verifier, the `MicSource`, and the panel from the override and the config and hands the panel and guest mode to the phone server; enroll and speaker-test open the same preferences
- [x] `waifu check` names the phone's pick, the list's order, and the phone's tuning
- [x] Fix pass: speaker-test applies the override's threshold and VAD gate; enroll and `waifu mic` point to the Suara page while the override holds a threshold or a mic; `waifu mic` falls back to the Windows default as the mic in use; `run`'s mic lines expire after `MIC_NOTICE_S` (60 s)

**Dependencies:** 264, 266, 267 · **Files:** `src/waifu/cli.py`, `config.example.yaml`, `tests/test_cli.py` · **Scope:** M

## Task 270: Docs ✅

**Acceptance criteria:**
- [x] README "Diagnosa suara" with a contents entry and checklist items, the troubleshooting mic entry led by the Suara page, the list form of `input_device`; CHANGELOG v1.47; full suite, ruff, and JS tests clean

**Dependencies:** 269 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 271a: Hotkey global contract ✅

**Acceptance criteria:**
- [x] SPEC "hotkey global (v1.48)" (stories, non-goals, the hotkey thread, the actions, the listen pause, the voice loop, the avatar, the quick buttons, config, CLI, decisions, known limits, privacy, safety); tasks 271a-279 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 271: Test guards ✅

**Acceptance criteria:**
- [x] A test that looks up user32's `RegisterHotKey`, `UnregisterHotKey`, `GetMessageW`, `PeekMessageW`, or `PostThreadMessageW` fails, also when the code under test catches the refusal; other user32 functions still work

**Dependencies:** 271a · **Files:** `tests/conftest.py`, `tests/test_safety_guard.py` · **Scope:** S

## Task 272: The hotkey thread ✅

**Acceptance criteria:**
- [x] `virtual_key` maps every key of `KEYS`; `Win32User32` calls user32 with the documented signatures and `use_last_error`
- [x] `HotkeyThread` registers each hotkey with `MOD_CONTROL | MOD_ALT | MOD_NOREPEAT` on its own thread, logs a taken combo once and keeps the others, calls `on_press` with the action of each `WM_HOTKEY` (an exception logged), ends on `WM_QUIT` or -1, unregisters on the same thread, and stops without hanging, also before it is ready
- [x] The status file is written atomically and read back, a broken one as None
- [x] Fix pass: `on_lost()` is called on the thread once it has unregistered combos it let go without being stopped (GetMessageW failed); never on `stop()` or when none was registered

**Dependencies:** 271 · **Files:** `src/waifu/hotkeys.py`, `tests/test_hotkeys.py` · **Scope:** M

## Task 273: The config ✅

**Acceptance criteria:**
- [x] `hotkeys` takes one key per action (any case) or null, refuses another key and two the same, bounds `quiet_hours`; `bindings` lists the ones switched on; the example documents it

**Dependencies:** 272 · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** S

## Task 274: The listen pause ✅

**Acceptance criteria:**
- [x] `ListenPause` starts for `hours`, ends, keeps a read for a second, ends by itself (file deleted), sets a broken file aside, and never raises from `until`

**Dependencies:** 271a · **Files:** `src/waifu/listen_pause.py`, `tests/test_listen_pause.py` · **Scope:** S

## Task 275: The voice loop ✅

**Acceptance criteria:**
- [x] While the pause holds, `run` feeds no frame to the segmenter (reset once) yet says reminders, rings, and announcements; an alarm that rings lets her hear; requests wait; the avatar is not shown listening; `listen_now` ends the pause
- [x] A start and an end are logged once and shown by the avatar's `hearing` event; an end by itself is said once
- [x] `repeat_now` repeats like "ulangi" and is ignored while she is busy
- [x] `AvatarBus.hearing` publishes and replays the event; the page shows "Tidak mendengarkan"
- [x] Fix pass: every `hello` hides the badge, so a browser tab that outlives a `run` restarted during a pause no longer says she does not listen; a stranger heard after `listen_now` is still refused before Whisper (test)

**Dependencies:** 274 · **Files:** `src/waifu/app.py`, `src/waifu/avatar/bus.py`, `src/waifu/avatar/web/index.html`, `src/waifu/avatar/web/logic.js`, `tests/test_app.py`, `tests/test_avatar_bus.py`, `tests/test_avatar_web.py`, `tests/js/logic.test.mjs` · **Scope:** M

## Task 276: The actions ✅

**Acceptance criteria:**
- [x] `InteractionHandler.press` takes a double-click or a listen after a cut whatever the avatar's switches say
- [x] `HotkeyActions.press` never blocks: talk as a double-click, stop cuts at once and listens unless paused, repeat and quiet on its worker; quiet switches the pause and says so, also when its file fails

**Dependencies:** 272, 274, 275 · **Files:** `src/waifu/hotkey_actions.py`, `src/waifu/avatar/interactions.py`, `tests/test_hotkey_actions.py`, `tests/test_avatar_interactions.py` · **Scope:** M

## Task 277: "Dengar lagi" ✅

**Acceptance criteria:**
- [x] While the pause holds, the quick buttons list "Dengar lagi" (`mic.listen`) after the alarm's; it is found whenever there is a pause and ends it outside the devices' lock
- [x] `isPassingAction`; the phone page and the PC page read the list again after pressing it, and the phone page every 15 s while it shows

**Dependencies:** 274 · **Files:** `src/waifu/phone/actions.py`, `src/waifu/avatar/web/phone/actions.js`, `src/waifu/avatar/web/phone/phone.js`, `src/waifu/avatar/web/tombol/tombol.js`, `tests/test_phone_actions.py`, `tests/js/*.test.mjs` · **Scope:** S

## Task 278: Wiring and the check line ✅

**Acceptance criteria:**
- [x] `run` on Windows clears and shares one pause, runs the actions and the hotkey thread while she listens, and records their registrations; `waifu tombol` shows the pause's button; `run`'s tests start a fake hotkey thread
- [x] `waifu check` lists the combos, trusts the recorded registrations only while `waifu run` runs, names each refused combo with its setting, and tells a pause under way
- [x] Fix pass: `run` rewrites the record with none held when the thread loses its combos (a locked `_HotkeysRecord`, so a loss never brings back stale combos or a removed file); `check` lists the combos the run holds, warns when it holds none (pointing to the log when it recorded none), and warns that a changed config.yaml is used only after a restart

**Dependencies:** 273, 275, 276, 277 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 279: Docs ✅

**Acceptance criteria:**
- [x] README "Hotkey global" with a contents entry and checklist items; CHANGELOG v1.48; full suite, ruff, and JS tests clean
- [x] Fix pass: the README, the CHANGELOG, config.example.yaml, and the SPEC say that the combos are taken from every other program while `run` holds them, that a game swallowing every combo is handled by pausing before it and "Dengar lagi" (another key helps only for one clashing combo), and that Ctrl+Alt+Q pressed while she speaks is confirmed after her speech (a second press ends it; Ctrl+Alt+X cuts her first); the SPEC names `Message(message, wparam)` and `hearingBadgeShown`

**Dependencies:** 278 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 280a: Kamus Cyrene contract ✅

**Acceptance criteria:**
- [x] SPEC "kamus cyrene (v1.49)" (stories, non-goals, the vocabulary, Whisper, the dictionary, the voices, teaching by voice, config, CLI, decisions, known limits, privacy, safety); tasks 280a-287 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 280: The hotword builder ✅

**Acceptance criteria:**
- [x] `build_hotwords` lists the name, wake words, device names, aliases, routines, app keys, the games when gacha is on, then the roster names (10 per game), skipping blanks and repeats (casefolded), within 40 terms and 400 characters, so the roster is dropped first
- [x] `Vocabulary` builds them at start and again on `set_roster`, safe across threads
- [x] Fix pass: the order is her names, device names, app keys, the games, the roster names (5 per game, taking turns, the roster read last first), routines, then aliases, so a full list drops the aliases first and every game read keeps names

**Dependencies:** 280a · **Files:** `src/waifu/voice/hotwords.py`, `tests/test_voice_hotwords.py` · **Scope:** S

## Task 281: Whisper's hotwords ✅

**Acceptance criteria:**
- [x] `WhisperStt(..., hotwords=)` passes its text on the detected-language decode and on the fallback re-run, None when empty, never a `prefix`; the initial prompt stays short

**Dependencies:** 280 · **Files:** `src/waifu/voice/stt.py`, `tests/test_voice_stt.py` · **Scope:** XS

## Task 282: The roster hook ✅

**Acceptance criteria:**
- [x] `GameDataTools(..., on_roster=)` is called with the game and the roster's names in its order on every successful roster read (also from the cache), never on a failure; the specs are unchanged

**Dependencies:** 280a · **Files:** `src/waifu/gacha/game_data_tools.py`, `tests/test_gacha_game_data_tools.py` · **Scope:** XS

## Task 283: The dictionary ✅

**Acceptance criteria:**
- [x] `pronounce` rewrites the built-in terms case-sensitively and Master's case-insensitively, whole words only, longest first, in one pass; Master's rule replaces a built-in with the same term
- [x] `checked_rule` bounds the term and the reading; `Lexicon` adds, replaces, lists, and removes rules, is never created by reading, sets a broken file aside, refuses rule 201, and `readings` never raises
- [x] Fix pass: a broken file is reported without naming its words

**Dependencies:** 280a · **Files:** `src/waifu/lexicon.py`, `tests/test_lexicon.py` · **Scope:** M

## Task 284: The speech router ✅

**Acceptance criteria:**
- [x] `SpeechRouter(..., lexicon=)` rewrites each Indonesian run before its engine, its fallback, and `fallback_only`, never a Japanese run; without a lexicon only the built-in rules apply; `build_speech_router` passes it through and reads no file

**Dependencies:** 283 · **Files:** `src/waifu/voice/tts.py`, `tests/test_voice_tts.py` · **Scope:** S

## Task 285: Teaching by voice ✅

**Acceptance criteria:**
- [x] `reading_request` takes "cara baca X itu Y" with its casing, names and softeners left out, and refuses a question ("?" or a question word) and a term over 3 or a reading over 8 words
- [x] The Assistant answers it without the brain: the rule saved and the reading said back, a refused rule or a failing file said, never remembered for "ulangi"; the phone's text keeps the reply as written
- [x] Fix pass: remarks (`REMARK_WORDS`), pronouns, her names, and the owner call as the term are refused; `QUESTION_TAGS` count only as the reading's last word; leading "harusnya", "dibaca", ... and trailing "aja", "oke", "nih", and a first "nama"/"kata"/"tulisan" of a longer term are left out; the owner call is left out before the request (`leading`); the confirmation "Oke {owner}. {term} mulai sekarang kubaca {reading}." is synthesised before `add` and played after it, never when the file cannot be written; only the "Heard" line names the words (test over every record)

**Dependencies:** 283 · **Files:** `src/waifu/lexicon.py`, `src/waifu/app.py`, `tests/test_lexicon.py`, `tests/test_app.py` · **Scope:** S

## Task 286: The CLI ✅

**Acceptance criteria:**
- [x] `tts.lexicon_path` with its example; `waifu lafal add|list|remove` with the built-ins marked and protected
- [x] `run` gives Whisper the vocabulary and the brain's game tools its roster hook, and the router and the Assistant the dictionary; `chat --speak` and `say` give the router the dictionary

**Dependencies:** 281, 282, 284, 285 · **Files:** `src/waifu/config.py`, `src/waifu/cli.py`, `config.example.yaml`, `tests/test_config.py`, `tests/test_cli.py` · **Scope:** M

## Task 287: Docs ✅

**Acceptance criteria:**
- [x] README "Kamus Cyrene" with a contents entry and checklist items; CHANGELOG v1.49; full suite, ruff, and JS tests clean
- [x] Fix pass: the README lists what goes to the brain and is not saved (questions, remarks, pronouns and her names, near misses, the phone's chat and voice notes) and says the "Heard" line logs the request; the vocabulary's new order; the CHANGELOG and the SPEC follow

**Dependencies:** 286 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 288a: Sebelum reset contract ✅

**Acceptance criteria:**
- [x] SPEC "sebelum reset (v1.50)" (stories, non-goals, the rules, the stamina hold, the watcher, the play watcher's `on_finished`, config, CLI, decisions, known limits, privacy, safety); tasks 288a-294 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 288: The planner switch ✅

**Acceptance criteria:**
- [x] `gacha.reminders.planner` is on by default and can be turned off; `config.example.yaml` lists it

**Dependencies:** 288a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** XS

## Task 289: The evening rules ✅

**Acceptance criteria:**
- [x] `ending_events` keeps the running events ending within 48 hours with progress below the total and not done, soonest first, at most 2; events without progress or an end never count
- [x] `ending_seasons` keeps the running windows ending within 72 hours below their total; a window without a total never counts
- [x] `evening_due` follows `reminders.planner`, the evening, and the day's key; `evening_notice` makes one line per game per game day, seasons first and each told once, and sets no key when there is nothing to tell
- [x] A real-shaped HSR calendar read through `starrail.read_calendar` gives the expected line

**Dependencies:** 288 · **Files:** `src/waifu/gacha/planner.py`, `tests/test_gacha_planner.py` · **Scope:** M

## Task 290: The overnight rule and the stamina hold ✅

**Acceptance criteria:**
- [x] `overnight_notice` tells once a night a game closed from 22:00 until 03:00 whose stamina fills before the morning, never one already full, closed earlier, or after the morning has come
- [x] `stamina_held` holds that game's stamina line until the morning, and `due_notices(..., stamina_held=)` leaves its occasion for after it
- [x] Fix pass: a stamina full within `reminders.stamina_minutes` at that poll gets no overnight line and no hold (the stamina reminder tells it)

**Dependencies:** 289 · **Files:** `src/waifu/gacha/planner.py`, `src/waifu/gacha/reminders.py`, `tests/test_gacha_planner.py`, `tests/test_gacha_reminders.py` · **Scope:** S

## Task 291: The play watcher tells the sessions it just finished ✅

**Acceptance criteria:**
- [x] `on_finished` hears each session a poll finished within `SLEEP_POLLS` polls of its end, outside the lock, after a poll that worked (kept through a failing write), never one ended by a sleep, never from `stop()`, and a failing callback is logged
- [x] Fix pass: never a session ended after the poll (the clock set back) or of a game the poll still sees (a restart too late to resume); `ClosedGames` carries the sessions (`put`) and whether a tracked game is open (`observe`) to the gacha watcher's thread

**Dependencies:** 288a · **Files:** `src/waifu/play_watch.py`, `tests/test_play_watch.py` · **Scope:** S

## Task 292: The watcher ✅

**Acceptance criteria:**
- [x] The calendar is read once per game per game day, only at the evening with the planner on, guests away, and no line yet; a network or rate-limit error is read again at the next poll, any other gives up that day
- [x] Session ends from the queue give the overnight line at the first poll with that game's status and hold its stamina line until the morning
- [x] Guest mode spends no key or read and the ends wait; `planner` lines are held by a game only until the next poll
- [x] Fix pass: while `ClosedGames.playing()`, a closed game waits (a game closed again replaces its close) and no stamina is held

**Dependencies:** 290, 291 · **Files:** `src/waifu/gacha/watcher.py`, `tests/test_gacha_watcher.py` · **Scope:** M

## Task 293: The CLI wiring ✅

**Acceptance criteria:**
- [x] `run` gives the gacha watcher the calendar reader, guest mode, the morning, and the queue the play watcher fills, only while the planner and play time are on
- [x] Fix pass: `ClosedGames` replaces the queue and hears `on_poll` before game mode; `run` builds the brain's `GameDataTools` once and the planner reads through its `calendar` (the 60-minute cache `game_events` uses)

**Dependencies:** 292 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 294: Docs ✅

**Acceptance criteria:**
- [x] README "Pengingat" with the planner rows; CHANGELOG v1.50; full suite, ruff, and JS tests clean
- [x] Fix pass: the README names ZZZ's endgame (not its events) among what has no progress; the overnight line's wait and the stamina reminder's minutes, and the shared calendar read, in the README, the CHANGELOG, and the SPEC

**Dependencies:** 293 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 295a: Konfirmasi kamus contract ✅

**Acceptance criteria:**
- [x] SPEC "konfirmasi kamus (v1.49.1)" (the bug, stories, non-goals, the question, forgetting by voice, the remark words, docs, decisions, known limits, privacy, safety) and the paragraphs of "kamus cyrene (v1.49)" it changes; tasks 295a-298 in the plan and here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 295: The remark words ✅

**Acceptance criteria:**
- [x] "cara baca jam itu begini", "cara baca buku itu penting", "... itu dengan tartil", "... itu pakai kompas", and the other added words (in the term or the reading) are no reading request
- [x] Every reading request kept by the earlier tests still is one
- [x] Fix pass: a question word or a remark word also counts as a part of a word written with hyphens ("cara baca buku itu pelan-pelan", "... itu biasa-biasa saja"), in a reading request and in a forget request's term; "Ha-Es-Er" and "Al-Quran" stay a reading and a term

**Dependencies:** 295a · **Files:** `src/waifu/lexicon.py`, `tests/test_lexicon.py` · **Scope:** XS

## Task 296: The yes before a reading is kept ✅

**Acceptance criteria:**
- [x] "cara baca X itu Y" is answered "Jadi X kubaca Y? Bilang iya kalau benar." without the brain and keeps nothing; a refused rule is still said at once
- [x] Master's next accepted voice turn keeps the rule when it is a plain yes ("iya", "Iya, Master.", "oke, catat aja", "iya bener") within 60 s, with v1.49's answer; anything else (another command, "bukan", "iya?", a yes after 61 s) keeps nothing, is answered as usual, and leaves no question for a later yes
- [x] An utterance the gate does not accept leaves the question waiting; a phone message (a brain turn) drops it
- [x] The logs never name the word or the reading; guest mode as before
- [x] Fix pass: a bare wake word leaves the question waiting, so "Cyrene" → "はい、Master?" → "iya" keeps the reading and never reaches a shutdown question the brain asked before (the PC turned off); the window still counts from her question
- [x] Fix pass: a reminder or announcement she says drops the question (a yes to the code watcher's "Mau kutukarkan?" kept the reading); one still waiting for the follow-up window leaves it
- [x] Fix pass: "Bener.", "bener, Cyrene", "iya bener banget", and "iya, betul sekali" are a yes (`is_yes` takes the affirmative words; the PC's and the expense's yes stay without "bener")

**Dependencies:** 295a · **Files:** `src/waifu/lexicon.py`, `src/waifu/app.py`, `tests/test_app.py` · **Scope:** S

## Task 297: Forgetting a reading by voice ✅

**Acceptance criteria:**
- [x] `forget_request` takes "lupakan cara baca X" and "hapus cara baca X" with names, softeners, and fillers left out; a question, a demonstrative ("itu"), a pronoun, a remark, or a term over 3 words is none
- [x] The Assistant forgets Master's rule without the brain and names the word; a built-in is refused aloud and kept; no rule is said; a file that cannot be written is said and warned about; the logs never name the word
- [x] Fix pass: a first "untuk", "buat", or "dari" and a last "sekarang", "lagi", "juga", pointing word, or filler are left out of the term ("hapus cara baca untuk Acheron sekarang" forgets Acheron; she said "... untuk untuk Acheron" and kept the rule)

**Dependencies:** 296 · **Files:** `src/waifu/lexicon.py`, `src/waifu/app.py`, `tests/test_lexicon.py`, `tests/test_app.py` · **Scope:** S

## Task 298: Docs ✅

**Acceptance criteria:**
- [x] README "Kamus Cyrene" (the question and the yes, forgetting by voice, the remark words, privacy, checklist) and its example line; CHANGELOG v1.49.1 between v1.50 and v1.49; full suite, ruff, and JS tests clean
- [x] Fix pass: README and CHANGELOG v1.49.1 name "bener", the bare name, the announcements, the forget phrasings, and "pelan-pelan"; one more checklist line

**Dependencies:** 297 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 299a: Panduan setup contract ✅

**Acceptance criteria:**
- [x] SPEC "panduan setup (v1.51)" (the problem, stories, non-goals, the patcher, the wizard's files, the CLI, the check hints, docs, decisions, known limits, privacy, safety); phase 55 and a risk row in the plan; tasks 299a-306 here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 299: The line patcher ✅

**Acceptance criteria:**
- [x] `patch_text` replaces only the targeted values in config.example.yaml (`lampu_kamar`'s tuya keys, `ac`'s and `proyektor`'s `ir.host`, `audio.input_device` with its comment kept) and inserts `colokan_pc`'s missing address and version at the block's indentation; every other byte stays
- [x] CRLF files keep CRLF (inserted lines too), a file without a final newline works, a missing `audio` or `notify` section is appended, a missing `notify.topic` goes after the section's comments
- [x] Devices at dash column 0, `id` not the first key, quoted ids, similar ids (`lampu` beside `lampu_kamar`), commented-out items, a multi-line `commands` list beside the touched key, and an `ir.tuya` block are found
- [x] Values are quoted so they load back exactly (quotes, `#`, `: `, brackets, `&*!`, leading space, yes/null/3.3 look-alikes, emoji, a list)
- [x] `PatchError` without a value for tabs, a flow mapping, a block scalar, a block sequence under the key, an unclosed quote or list, anchors, aliases, tags, merge keys, duplicated sections, ids, or keys, an inline parent value, a missing device or nested parent
- [x] `snippet` groups the changes as YAML with secrets hidden; `masked` shows no character of the secret

**Dependencies:** 299a · **Files:** `src/waifu/config_patch.py`, `tests/test_config_patch.py` · **Scope:** M

## Task 300: Writing config.yaml safely ✅

**Acceptance criteria:**
- [x] `apply_changes` backs the original bytes up to `data/<name>.bak-YYYYMMDD-HHMMSS` (`-2` on a collision), writes atomically, reloads, and compares the whole config with the intended one; the BOM is kept
- [x] A config that does not load, a file that is not UTF-8, an invalid new value, or a `PatchError` writes nothing; a reload that differs (a section held by a `? notify` complex key, appended again) or fails (a section appended after a `...` document end) restores the original bytes and says so; a file that cannot be replaced is left as it was
- [x] The problem text never holds a value or the loader's text; the log names paths only
- [x] Fix pass: `.gitignore` covers the atomic write's temp copy (`.config.yaml.<random>.tmp`, every local key and the topic), which a hard kill before the replace leaves behind; a test checks the name the write really uses

**Dependencies:** 299 · **Files:** `src/waifu/config_patch.py`, `tests/test_config_patch.py` · **Scope:** S

## Task 301: The wizard's files and the matching ✅

**Acceptance criteria:**
- [x] `read_cloud_devices` reads devices.json (skipping entries that are not objects or lack id or key), takes the address and version from it or from snapshot.json, accepts only a private LAN IPv4 and versions 3.1-3.5, never `last_ip`, and keeps the key out of `repr`; a missing or broken devices.json raises `TuyaImportError`
- [x] `tuya_targets` lists `tuya_light`, `tuya_plug`, and IR devices with `backend: tuya`, in config order
- [x] `suggest` matches by name, aliases, and id with a category hint, never a sub-device, a light or plug entry once, an IR blaster for every IR target, nothing under 0.5
- [x] `tuya_changes` sets device_id, local_key (secret), and address and version only when known
- [x] Fix pass: `TuyaTarget.current` is the connection config.yaml has, and `tuya_changes` leaves out each value already there, so a second import of the same list writes nothing

**Dependencies:** 299 · **Files:** `src/waifu/tuya_import.py`, `tests/test_tuya_import.py` · **Scope:** M

## Task 302: `waifu setup mic` ✅

**Acceptance criteria:**
- [x] `waifu mic`'s listing and measuring are helpers it shares with setup; its output is unchanged
- [x] Setup measures each mic on the owner's yes, lists the mics that heard him best first, and writes one name (Enter = the best) or a list ("1,2") as `audio.input_device`, with a backup; it says to run `waifu enroll` again
- [x] A wrong answer is asked again; n skips; the same value writes nothing; a phone pick is named as leading; the end of input stops setup

**Dependencies:** 300 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 303: `waifu setup tuya` ✅

**Acceptance criteria:**
- [x] The steps, the on-screen secret warning, and the poll question are explained before the wizard; the runner gets `[python, -m, tinytuya, wizard]` in the folder of config.yaml; n skips; an existing devices.json is read without running it again unless he says yes; Ctrl+C or a failed wizard is said
- [x] Each Tuya target is confirmed, picked, or skipped; device_id, local_key, address, and version are written; a real device_id is replaced only on his yes; a light or plug entry is not taken twice
- [x] No local key is printed (the summary and the snippet hide it, with the devices.json instruction)
- [x] tinytuya.json and tuya-raw.json are deleted on his yes and kept on no; `.gitignore` lists the four wizard files; tests/conftest.py refuses running tinytuya
- [x] Fix pass: the import reads devices.json only when the wizard wrote it in this run (its time or size changed); after a Tuya cloud error (exit 0, devices.json untouched) she explains the wizard's English errors and imports nothing, never an older list with a re-paired device's old key
- [x] Fix pass: Ctrl+C in the wizard stops setup (exit 130) and imports nothing; stopped by Ctrl+C or the end of input while matching or at the delete question, she says the credential files stay
- [x] Fix pass: a device already holding every value of its pick says "sudah sama dengan devices.json"; the list names each kind in Indonesian (lampu, colokan, IR blaster, lainnya), never the raw category code; the steps warn of an empty Devices tab (wrong data center) and of renewing IoT Core, and take the Device ID from the Devices tab

**Dependencies:** 300, 301 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py`, `tests/conftest.py`, `tests/test_safety_guard.py` · **Scope:** M

## Task 304: `waifu setup ir` and `waifu setup notify` ✅

**Acceptance criteria:**
- [x] ir: each Broadlink address asked once (Enter keeps it, a non-IPv4 asked again), written to every device on it; the `learn-ir --missing` lines follow
- [x] notify: a new topic matching the pattern is written with `enabled: true` and printed once for the subscribe steps; an existing topic is kept unless he says yes; a test push only on yes
- [x] Fix pass: with a topic present but notify off, n to "Nyalakan notifikasi HP?" writes nothing (it used to turn notify on with a new topic); a new topic is its own question (default no); turning notify on with the kept topic shows the subscribe steps with it

**Dependencies:** 300 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 305: `waifu setup all` and the check hints ✅

**Acceptance criteria:**
- [x] `waifu setup` and `waifu setup all` run mic, tuya, ir, notify, then `check`; the end of input stops before `check`; a restart of a running `waifu run` is asked for after a write
- [x] `waifu check` warns for each Tuya device at the placeholder and each Broadlink blaster at 192.168.1.50, naming `waifu setup tuya` and `waifu setup ir`; the example config still exits 0 and no other line changes
- [x] Fix pass: a write or failed write before a later question (the test push, the delete offer) still counts when the input ends there: the restart reminder, exit 1 for a failure
- [x] Fix pass: unless stopped, setup ends with "Ringkasan setup:" (several steps) and "Langkah berikutnya:" from config.yaml as it then is (waifu enroll after a mic write, waifu setup mic/tuya/notify for what is empty or off, each learn-ir command, waifu run after a write when it is not running), before `check`

**Dependencies:** 302, 303, 304 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 306: Docs ✅

**Acceptance criteria:**
- [x] README "Setup pertama" (and in the table of contents), "Lampu dan colokan Tuya" and the Privasi line about the wizard's files corrected; CHANGELOG v1.51; full suite, ruff, and JS tests clean

**Dependencies:** 305 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 307a: Respons instan contract ✅

**Acceptance criteria:**
- [x] SPEC "respons instan (v1.52)" (the problem, stories, non-goals, the fillers, the router's flag, the voice loop, config, CLI, docs, decisions, known limits, privacy, safety) and the app contract lines; phase 56 and a risk row in the plan; tasks 307a-312 here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 307: The stand-in flag of her voice ✅

**Acceptance criteria:**
- [x] `SpeechRouter.degraded` is false for a working voice, true after a run fell back to its Windows voice until the engine works again, true while the converter fails until it converts again, and false with `fallback_only`

**Dependencies:** 307a · **Files:** `src/waifu/voice/tts.py`, `tests/test_voice_tts.py` · **Scope:** XS

## Task 308: The filler bank ✅

**Acceptance criteria:**
- [x] `NodBank.prepare` synthesises each line once, in order, with `{owner}` filled, and keeps it; a `TtsError` (warned once per call), any other exception (logged with its traceback), or a stand-in voice leaves that line out; a second `prepare` makes only the missing lines
- [x] `pick` gives None before `prepare`, a random kept filler other than the last one picked, and None when only the last one is kept
- [x] Every built-in filler is one Indonesian speech run
- [x] Fix pass: `prepare` stops at the first line in a stand-in voice (the router keeps it for `tts.fallback.retry_s`, so the rest would come in it too) and tells whether every filler is kept; `keep_preparing` sleeps `retry_s` and tries again until they are, and `retry_after_s` is past the router's pauses; with the real SpeechRouter, one Edge blip costs the bank only until the retry
- [x] Fix pass: "Tunggu sebentar ya, {owner}." and "Sebentar ya, {owner}…" became "Iya, aku dengar kok, {owner}…" and "Aku cari tahu dulu ya." (at most two say "bentar", at most two open alike, one names the owner); `pick` avoids the last two while three are kept

**Dependencies:** 307 · **Files:** `src/waifu/voice/nod.py`, `tests/test_voice_nod.py` · **Scope:** S

## Task 309: The `nod` settings ✅

**Acceptance criteria:**
- [x] `nod.enabled` (true) and `nod.after_ms` (1200, 600-5000) load and are refused outside their range, naming the setting; the example's commented block written out holds the defaults

**Dependencies:** 307a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** XS

## Task 310: Fillers in the voice turn ✅

**Acceptance criteria:**
- [x] `_speak`'s pausing and playing are shared helpers (a refactor, no test changes), and `_teach_reading`'s early synthesis is the shared `_voice_ahead`
- [x] A brain turn slower than `nod.after_ms` says a filler first: the timer waits `after_ms`, the avatar goes speaking then back to thinking, the mic is paused and resumed, and the reply follows, its voice synthesised while the filler played; a fast turn cancels the timer and a late call says nothing
- [x] The reply never overlaps the filler (threaded test), and a filler whose reply became ready while it waited for the voice is skipped
- [x] No timer for alarm words, the bare wake word, "ulangi", the heard request, a reading asked, confirmed, or forgotten, `ask_aloud`, the phone, or `handle_text`; a filler also while `quiet` holds
- [x] Never the same filler twice in a row, never repeated by "ulangi", not in `tts_s`; a cut filler still lets the reply play; no bank, no timer
- [x] Fix pass: `interrupt` stops a filler but returns False for it (a flag read under a lock held from the player's stop), so a double-click or Ctrl+Alt+X on it opens no follow-up window while she still thinks and nothing he says is dropped; the avatar stays thinking
- [x] Fix pass: a brain turn that raises cancels the timer and closes the turn (a late fire says nothing); the default `_daemon_timer` makes a daemon `threading.Timer` of `after_ms` that says the filler, and a reply ready in time cancels it and its thread ends

**Dependencies:** 308, 309 · **Files:** `src/waifu/app.py`, `tests/test_app.py` · **Scope:** M

## Task 311: `waifu run` prepares the fillers ✅

**Acceptance criteria:**
- [x] With `nod.enabled` the Assistant gets a NodBank voiced by the run's one speech router, prepared on a daemon "nod-voice" thread after the `--announce` line; with it off, `nods=None` and nothing is prepared; the CLI tests replace the preparation
- [x] Fix pass: the thread runs `keep_preparing`, and the bank's `retry_s` is `retry_after_s(tts.fallback.retry_s)`

**Dependencies:** 310 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** S

## Task 312: Docs ✅

**Acceptance criteria:**
- [x] README "Respons instan" (and in the table of contents); CHANGELOG v1.52; full suite, ruff, and JS tests clean
- [x] Fix pass: the owner checkpoint and the README, CHANGELOG, and config.example say the count starts when she starts thinking (about 2 to 3 s after he stops talking) and that with Claude most brain turns get a filler unless `nod.after_ms` is raised; SPEC story 2 is a realistic case; the retries, the new lines, and the cut are described

**Dependencies:** 311 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 313a: Pengingat saat game dibuka contract ✅

**Acceptance criteria:**
- [x] SPEC "pengingat saat game dibuka (v1.53)" (the problem, stories, non-goals, real starts, the watcher's `on_started`, the store, the teller, the tool, config, CLI, docs, decisions, known limits, privacy, safety); phase 57 and a risk row in the plan; tasks 313a-319 here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 313: The `app_reminders` settings ✅

**Acceptance criteria:**
- [x] `app_reminders.enabled` (true) and `app_reminders.path` (`data/app_reminders.json`) load, an unknown key is refused; the example's commented block written out holds the defaults

**Dependencies:** 313a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** XS

## Task 314: The app reminder store ✅

**Acceptance criteria:**
- [x] `add` keeps a reminder with a new id (g1, g2, ..., never reused), the game, the collapsed text, once or every time, and an aware WIB time; `reminders()` in the order made, `for_game`, `remove` (None for an unknown id), `drop` (one write, none when nothing is there)
- [x] Refused with an Indonesian `AppReminderError`: an empty, too long, or secret-looking text, and a 31st reminder; a game that is not a GameId is a ValueError; a naive clock is a ValueError
- [x] Reading never creates the file; a file that is not an app reminder list (bad JSON, wrong keys, version, id, game, text, flag, time, a shared id) is moved aside and an empty list starts; an id at or beyond `next_id` is never handed out again; a Notepad BOM is read

**Dependencies:** 313a · **Files:** `src/waifu/app_reminders.py`, `tests/test_app_reminders.py` · **Scope:** S

## Task 315: Real game starts in the play watcher ✅

**Acceptance criteria:**
- [x] `opened_games` gives the games followed after a poll and not before it; nothing across a sleep gap or a clock set back; a game missed at one poll and back is no start, one gone two polls and back is
- [x] `PlayTimeWatcher(on_started=...)` tells each start once, after `on_poll` and `on_finished`; never at the run's first poll (a game already running, resumed or not), after a sleep, or after a clock step; a start found by a poll whose write failed is told at the next poll that works; never from `stop()`; a failing listener is logged and play time goes on
- [x] Fix pass: a start kept from a poll whose write failed is kept as its new session and told only while the tracking still follows it: nothing after a sleep (the game closed or still open) or a clock set back, nothing once the game closed while the writes failed, and a game closed and opened again meanwhile told once

**Dependencies:** 313a · **Files:** `src/waifu/play_time.py`, `src/waifu/play_watch.py`, `tests/test_play_time.py`, `tests/test_play_watch.py` · **Scope:** S

## Task 316: Telling the reminders at a start ✅

**Acceptance criteria:**
- [x] `reminder_line` says the owner word at sentence start, the game's short name, and the texts joined (a dan b, a, b, dan c), each without trailing punctuation, a repeat said once
- [x] `game_started` tells one line with "happy" for every reminder of that game, drops the once ones and keeps the every-time ones; nothing for a game without reminders; a failed read says nothing; a failed drop still says the line; no text in the INFO log
- [x] Fix pass: `reminder_line` drops an opening "jangan lupa", "inget", or "ingat" (any case, a comma or colon after it; kept when that is all the text holds), says a text once in any case as first made, and says texts opening with "jangan" after "ingat ya", alone or in a sentence of their own after the things to do

**Dependencies:** 314 · **Files:** `src/waifu/app_reminders.py`, `tests/test_app_reminders.py` · **Scope:** S

## Task 317: The `app_reminder` tool ✅

**Acceptance criteria:**
- [x] Non-strict spec (no `required`) with the configured games, byte-identical across calls; add (the default), list, and remove with JSON results; `game_open_now` and its message when the game's session is open now in the play time file
- [x] Bad arguments (unknown key, a key the action does not take, a game not tracked, a text that is not a string, a flag that is not a boolean, a missing game or text or id) and store refusals are error results; an unreadable file is logged; `run` never raises; refused in her own turns
- [x] Fix pass: list takes an optional `game` (only that game's reminders, an empty list for one without); remove takes one too and removes only the reminder of that game ("Pengingat g2 untuk Genshin, bukan HSR, jadi tidak dihapus.", nothing removed); a game not tracked is still refused; the description says both, that the text is said after "jangan lupa", and that a thing not to do keeps its "jangan"
- [x] Fix pass: the `schedule` description sends a reminder tied to opening a game to `app_reminder` when she has it, never a guessed time; `list_schedules` says those are listed with `app_reminder`

**Dependencies:** 314 · **Files:** `src/waifu/brain/app_reminder_tools.py`, `src/waifu/brain/schedule_tools.py`, `tests/test_app_reminder_tools.py`, `tests/test_schedule_tools.py` · **Scope:** S

## Task 318: `waifu run`, `chat`, `pengingat-game`, and `check` ✅

**Acceptance criteria:**
- [x] `run` gives the play watcher `on_started` with app reminders on (none with them off): a start reminds through `assistant.remind` and, with notify on, pushes from a daemon thread with the schedule tag; no new `with` item
- [x] `run` and `chat` offer `app_reminder` after `play_time` when play time and app reminders are on; it joins guest mode's `PRIVATE_TOOLS` (with the registration, so a full chat request carries every private tool at every commit); a full Claude chat request still holds 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions, with `app_reminder` not strict
- [x] `waifu pengingat-game` lists, `hapus <id>` removes (exit 1 for an unknown id); `check` says how many are kept, off, off without play time, unreadable, or a broken file moved

**Dependencies:** 315, 316, 317 · **Files:** `src/waifu/cli.py`, `src/waifu/guest.py`, `tests/test_cli.py`, `tests/test_guest.py` · **Scope:** M

## Task 319: Docs ✅

**Acceptance criteria:**
- [x] README "Pengingat saat game dibuka" (and in the table of contents), the command row, the guest-mode list, and the Privasi bullet; CHANGELOG v1.53; full suite, ruff, and JS tests clean

**Dependencies:** 318 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 320a: Pemantau internet contract ✅

**Acceptance criteria:**
- [x] SPEC "pemantau internet (v1.54)" (the problem, stories, non-goals, the probes, classification, the state, her words, the history, the watcher, asking without Claude, offline speech, the brain, the tool, the recap and weekly report, config, CLI, docs, decisions, known limits, privacy, safety); phase 58 and a risk row in the plan; tasks 320a-331 here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 320: The `network` settings ✅

**Acceptance criteria:**
- [x] `network` loads with its defaults (on, every 60 s, HoYo every 150 s, slow at 200 ms over 5 checks, the back line after 3 minutes at most 3 a day, 7 days in `data/network.json`, the gateway `auto`, 1.1.1.1 and 8.8.8.8, www.google.com, the three HoYo hosts); bounds, an unknown key, an anchor that is not a public IPv4 address, a gateway that is neither `auto` nor IPv4, and a host that is not a host name are refused; the example's commented block written out holds the defaults

**Dependencies:** 320a · **Files:** `src/waifu/config.py`, `config.example.yaml`, `tests/test_config.py` · **Scope:** XS

## Task 321: The probes ✅

**Acceptance criteria:**
- [x] `icmp_echo`, `default_route`, `dns_query_ms`, and `tcp_connect_ms` read Windows' answers as documented (against fake libraries and sockets): a reply only with status 0 from the address asked, the ICMP handle and the DNS answer always released, the next hop or None for a direct route, None for no route; they look the functions up at the call
- [x] `NetworkProbe.check` runs the gateway, the anchors, and with `dns` and `hoyo` the lookup and the HoYo hosts at once, falls back to TCP on 443 when no anchor echoes, counts what has not answered within the budget as failed, never starts a probe of a target still running, and never raises
- [x] tests/conftest.py refuses and records the iphlpapi ICMP and route functions and the dnsapi queries; the probe tests fail on any non-loopback lookup or connect
- [x] Fix pass: `tcp_connect_ms` resolves IPv4 only, like the echoes, so a broken IPv6 path never makes a host that answers over IPv4 look down; a check that finds a target's probe still running (another thread's check at the same moment, or a held lookup) waits for its answer within its own budget instead of counting it failed, and still starts no second thread

**Dependencies:** 320 · **Files:** `src/waifu/network_probe.py`, `tests/conftest.py`, `tests/test_network_probe.py` · **Scope:** M

## Task 322: Classification, the state, and her words ✅

**Acceptance criteria:**
- [x] `classify` gives router, offline, dns, slow, hoyo, and ok in that order (a gateway never seen answering is never blamed; slow by the median with the recent round trips)
- [x] `NetworkState` keeps the answered gateways, the recent round trips, whether the last check failed, whether a HoYo game runs, and the confirmed outage with its note; `NetworkCheck.now` checks with DNS, classifies, and notes
- [x] `answer_line` and `back_line` say each case in Indonesian with the owner word, never an address, the HoYo answers saying they are not the game's server
- [x] Fix pass: `NetworkCheck.now` (a check when asked) is slow by its own round trip and tells that number, while the watcher's `NetworkCheck.poll` keeps the median; `answer_line` says "ping internetnya normal" for ok and hoyo (for an ok internet question adding that the speed or the app may be slow) and "ping-nya" when slow, and ends router, offline, dns, and slow with their `ADVICE` after the since-when; `back_line` says the length with `duration_text`; `outage_note` hedges "Sepertinya internetnya lagi putus." while the last check failed and no outage is confirmed

**Dependencies:** 321 · **Files:** `src/waifu/network.py`, `src/waifu/network_watch.py`, `tests/test_network.py` · **Scope:** M

## Task 323: The history ✅

**Acceptance criteria:**
- [x] `begin`, `still_down`, `end`, and `told_back` keep the open outage, the ended ones, and the day's back count; ended outages older than `keep_days` are dropped; `outage_totals` counts the outages with time in a span and their seconds inside it, the open one up to its last failed poll
- [x] Reading never creates the file; a file that is not a network history is moved aside and an empty one starts; a naive clock is a ValueError

**Dependencies:** 320a · **Files:** `src/waifu/network_history.py`, `tests/test_network_history.py` · **Scope:** S

## Task 324: The watcher ✅

**Acceptance criteria:**
- [x] Two failed polls in a row start an outage at the first (written, told to the state, logged); the poll that answers ends it, and one of at least `back_after_minutes` gets the back line once through `announce` with a 30-minute expiry, at most `max_back_per_day` a day by the file's count
- [x] Nothing counts at the run's first two polls, nor at two polls after a sleep gap or a clock step; an outage open across one ends at its last failed poll without a line; one left open by a crash ends at its last failed poll; `stop()` ends an open one
- [x] The HoYo hosts are checked only while a game runs and at most every `hoyo_poll_seconds`; failures are logged once, nothing is raised

**Dependencies:** 322, 323 · **Files:** `src/waifu/network_watch.py`, `tests/test_network_watch.py` · **Scope:** M

## Task 325: Asking without Claude ✅

**Acceptance criteria:**
- [x] `network_question` takes the whole internet and HoYo phrases (with her names, the owner word, and softeners left out) and nothing with another word
- [x] Fix pass: every subject, the HoYo words too, takes "-nya" joined or split off ("WiFi-nya", "Internet-nya"), and "lagi" may follow the question word and "kok lemot"; a question without a subject ("mati lagi?") is still none
- [x] The Assistant answers them with `network` without a brain turn, said like a reply and repeated by "ulangi"; without `network` they reach the brain; `NetworkAnswers` checks with HoYo for the HoYo topic

**Dependencies:** 322 · **Files:** `src/waifu/network_ask.py`, `src/waifu/app.py`, `tests/test_network_ask.py`, `tests/test_app.py` · **Scope:** S

## Task 326: Claude's apology names the outage ✅

**Acceptance criteria:**
- [x] With `network_note`, a connection failure and a stalled stream add the note after the apology and before the finance changes, unformatted; no note, None, or a note that raises adds nothing; `build_brain` passes it to Claude only

**Dependencies:** 322 · **Files:** `src/waifu/brain/claude.py`, `src/waifu/brain/factory.py`, `tests/test_brain_claude.py`, `tests/test_brain_factory.py` · **Scope:** S

## Task 327: The `network_status` tool ✅

**Acceptance criteria:**
- [x] Non-strict spec without arguments, byte-identical across calls; the result has the status, its meaning, the round trip, the router, DNS, the HoYo hosts, an open outage, and today's outages only when there were any, never an address; an argument is an error; an unreadable history is left out; `run` never raises
- [x] Fix pass: `internet_ms` is this check's round trip; the description says it answers "ping-ku berapa?" and measures delay, not download speed, so an ok check is never called a fine internet when Master finds it slow

**Dependencies:** 322, 323 · **Files:** `src/waifu/brain/network_tools.py`, `tests/test_network_tools.py` · **Scope:** S

## Task 328: The recap and weekly report sections ✅

**Acceptance criteria:**
- [x] The recap's `network` part gives the day's outages (unknown past `keep_days`), its JSON only when there were any; `waifu rekap` prints its line
- [x] The weekly report's `network` gives the outages of the 7 days up to now only when there were any, a problem when the file cannot be read; the spec mentions it only with the source; `waifu gacha report` prints its line

**Dependencies:** 323 · **Files:** `src/waifu/recap.py`, `src/waifu/brain/recap_tools.py`, `src/waifu/gacha/weekly.py`, `src/waifu/cli.py`, tests · **Scope:** M

## Task 329: `run`, `chat`, `waifu internet`, and `check` ✅

**Acceptance criteria:**
- [x] `run` with the network on builds one state, gives the brain its note, the Assistant `NetworkAnswers`, starts the watcher with the play time watcher in one item, and the state hears the play time polls; nothing with it off
- [x] `run` and `chat` offer `network_status`; it is open in guest mode; a full Claude chat request still holds 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions
- [x] `waifu internet` prints the check and today's outages, also with the watcher off, exit 1 for an unreadable file; `check` says on, off, unreadable, or a broken file moved
- [x] Fix pass: `waifu internet` prints the advice as "  Saran: ..." after the check for a status that has one

**Dependencies:** 324, 325, 326, 327, 328 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 330: Speaking offline at once ✅

**Acceptance criteria:**
- [x] While the internet watcher's state says the last check failed (`internet_changed(True)`, told through `state.follow` at each check that changes it), the router speaks the online voices' languages with their fallback without trying the engine, and tries it again at the next run once a check answers, also inside its pause after a failure; a language without a fallback, and one whose fallback cannot speak, is tried as before; a clip spoken that way counts as `degraded`; `build_speech_router` marks the edge voices; `run` has the state tell the router

**Dependencies:** 329 · **Files:** `src/waifu/voice/tts.py`, `src/waifu/network.py`, `src/waifu/cli.py`, `tests/test_voice_tts.py`, `tests/test_network.py`, `tests/test_cli.py`, `SPEC.md` · **Scope:** S

## Task 331: Docs ✅

**Acceptance criteria:**
- [x] README "Pemantau internet" (and in the table of contents), the command row, the guest-mode list, and the Privasi bullet; CHANGELOG v1.54; full suite, ruff, and JS tests clean

**Dependencies:** 330 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 332a: Kartu teks contract ✅

**Acceptance criteria:**
- [x] SPEC "kartu teks (v1.55)" (the problem, stories, non-goals, the reply shape, the parser, the voice loop, the avatar event and link route, the shared card module, the panel, the phone, chat, the speech cut, the gate's question, the self-started prompts, config, guest mode, docs, decisions, known limits, privacy, safety) and the card event in the protocol list; phase 59 and a risk row in the plan; tasks 332a-339 here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 332: The parser ✅

**Acceptance criteria:**
- [x] `split_card` splits at the first marker in any of its shapes (case, spaces, one or two brackets, full width, no title, unclosed, mid-line); text without one comes back unchanged; the body keeps its lines and indentation, loses emotion tags, blank runs, and a second marker's syntax; an empty body is no card; the caps cut with "…"
- [x] `before_card` puts an addition before the marker, as before without one; `card_message` joins the gist and the card within 3800 UTF-8 bytes; `Card.allows_link` only for a URL in the body; `is_card_request` takes the whole re-show phrases and nothing with another word
- [x] Fix pass: one bracket is a marker only with the colon ("[kartu: Judul]") or as a whole line ("[kartu Judul]"), so "[kartu kredit]" in a sentence, a URL's "?q=[kartu]", "[kartu nama] = meishi", and "[Kartu Prakerja](url)" stay as written, in the gist and in the body; in the body only a double-bracket or colon marker becomes its title; "mana" no longer points at the card, so "kartu yang mana?" reaches the brain while "mana kartunya?" still re-shows it; "kartu terakhir" and "sekali lagi" are re-show phrases

**Dependencies:** 332a · **Files:** `src/waifu/card.py`, `tests/test_card.py` · **Scope:** S

## Task 333: The prompt note and the gate's question ✅

**Acceptance criteria:**
- [x] The system prompt holds the card note between the devices and the time note (never "web"), naming the marker as the one other bracket and allowing bullets, URLs, romaji, and kana in the card; the web note forbids URLs only in the spoken part
- [x] The briefing and journal prompts say "Jawab tanpa kartu.", the weekly report allows a card for its details, and none of them opens a tool gate
- [x] `with_question` places the question before a card, as before without one
- [x] Fix pass: the card note keeps markdown out of the card too (no asterisks, "#", code fences, or "|" tables), asking for one "- Rabu: hujan, 27°C" line per table row and code as plain lines with their indentation

**Dependencies:** 332 · **Files:** `src/waifu/brain/persona.py`, `src/waifu/brain/confirm.py`, `src/waifu/briefing.py`, `src/waifu/gacha/weekly.py`, `src/waifu/journal.py`, tests · **Scope:** S

## Task 334: The voice loop ✅

**Acceptance criteria:**
- [x] `split_reply` gives the gist and the card; a card-only reply says `CARD_ONLY`; only the gist is synthesised, repeated, voiced for the phone, and passed on (with the card as plain text for a request's push)
- [x] A voice turn and a request show the card on the avatar before speaking (`card.show_seconds`), never its text in the log; a phone turn puts it in `PhoneReply.card` and shows nothing on the PC; turns without a card show the avatar the same events as before
- [x] "Tampilkan lagi kartunya" re-shows the last card without the brain, or says there is none (also after a guest switch) or that the avatar is not showing; a turn that switches guest mode shows no card and hides the one shown
- [x] `card.show_seconds` (120, 0-3600), the example block written out holds the default

**Dependencies:** 332 · **Files:** `src/waifu/app.py`, `src/waifu/config.py`, `config.example.yaml`, `src/waifu/phone/server.py`, `tests/test_app.py`, `tests/test_config.py` · **Scope:** M

## Task 335: The bus's card event and the link route ✅

**Acceptance criteria:**
- [x] `AvatarBus.card` publishes the card or its hide; a new subscriber gets the card shown with `elapsed` while it still shows; `shown_card` gives it until hidden
- [x] `POST /open-link` opens a URL on the card shown through the injected opener, refusing a foreign origin, a wrong token, a malformed body, too many requests, a URL not on the card or not a plain http(s) link, and saying when Windows cannot open it; the URL is never logged

**Dependencies:** 332 · **Files:** `src/waifu/avatar/bus.py`, `src/waifu/avatar/server.py`, `src/waifu/notify.py`, `src/waifu/music.py`, tests · **Scope:** M

## Task 336: The avatar page's card panel ✅

**Acceptance criteria:**
- [x] `phone/card.js` parses and caps a card, tells when it hides, splits lines into bullets and text, links only http(s) URLs, and builds the card with text nodes; a link click hands the URL on
- [x] `index.html` shows the card in a bottom panel that scrolls, closes, hides by itself, and is replaced by the next; a link click posts `/open-link` with the token; no `innerHTML` anywhere in the pages; the module graph reaches `card.js`
- [x] Fix pass: `cardLines` keeps a text line's indentation and runs of spaces and leaves an indented bullet as a line as written, shown with `white-space: pre-wrap` on both pages; `hideTimer` holds a card's time while the pointer is on it and gives what was left, at least 10 s, once it leaves; with `onLink` a middle click goes to `onLink` too, the link has no target, and its context menu is prevented; the first child of a card keeps clear of the close button; the link pattern's invisible characters are written as escape sequences, and a test keeps such characters out of the card pages; a page test checks that `index.html` passes `onLink`

**Dependencies:** 335 · **Files:** `src/waifu/avatar/web/phone/card.js`, `src/waifu/avatar/web/index.html`, `tests/js/card.test.mjs`, `tests/test_avatar_web.py` · **Scope:** M

## Task 337: The phone's card ✅

**Acceptance criteria:**
- [x] `/chat` and `/talk` carry the card; `parseChatReply` keeps a valid one; her bubble shows the gist and the card with tappable links; the shell caches `card.js`

**Dependencies:** 334, 336 · **Files:** `src/waifu/phone/server.py`, `src/waifu/avatar/web/phone/audio.js`, `phone.js`, `phone.css`, `sw.js`, tests · **Scope:** S

## Task 338: `waifu chat`, the speech cut, and the wiring ✅

**Acceptance criteria:**
- [x] `waifu chat` prints the gist then the card indented, speaks only the gist, and re-shows the last card on the phrase (none after a guest switch)
- [x] `clean_for_speech` drops a card marker and everything after it
- [x] The avatar server opens links with `open_in_browser`

**Dependencies:** 334, 335 · **Files:** `src/waifu/cli.py`, `src/waifu/voice/tts.py`, tests · **Scope:** S

## Task 339: Docs ✅

**Acceptance criteria:**
- [x] README "Kartu teks" (and in the table of contents); CHANGELOG v1.55; full suite, ruff, and JS tests clean
- [x] Fix pass: README's limits say the card shows in the browser tab with `window: none`, that with no page open she still says "kutampilkan di layar" and "Ini kartunya lagi", and that a marker the parser cannot recognise makes the whole reply spoken; the new re-show phrases, the hover pause, the middle click, and the kept indentation are in README, CHANGELOG, and SPEC

**Dependencies:** 338 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 340a: Artinya apa contract ✅

**Acceptance criteria:**
- [x] SPEC "artinya apa (v1.56)" (the problem, stories, non-goals, the reader, the private check, the tool and its gate, the logs, the hotkey and `ask_now`, the Japanese added words, docs, decisions, known limits, privacy, safety); phase 60 and a risk row in the plan; tasks 340a-344 here

**Dependencies:** none · **Files:** `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S

## Task 340: The clipboard reader

**Acceptance criteria:**
- [ ] `read_clipboard` reads `CF_UNICODETEXT` through user32 and kernel32 loaded with `use_last_error=True` at each call and the documented signatures; a password manager's mark gives `PRIVATE` without reading; no text gives `NOT_TEXT`; a held clipboard is tried five times, then `BUSY`; the clipboard is closed and the memory unlocked whatever happens; a text over the cap is cut without a lone surrogate
- [ ] `looks_private` catches the secrets `looks_secret` does, a lone password-like word, an OTP or PIN, an account number, and a private key, and leaves ordinary Japanese and English text alone
- [ ] `tests/conftest.py` refuses the clipboard functions of user32 and the global memory functions of kernel32; the real reader trips it in the suite

**Dependencies:** 340a · **Files:** `src/waifu/clipboard.py`, `tests/test_clipboard.py`, `tests/conftest.py` · **Scope:** M

## Task 341: The `read_clipboard` tool

**Acceptance criteria:**
- [ ] The tool reads only in a turn whose own words name the copy (her names left out), once per turn, takes no argument, and is not strict; its result holds the text (at most 2000 characters, `cut`) and a note that it is data; every refusal leaves the content out
- [ ] It is in guest mode's `PRIVATE_TOOLS`, in the full chat request, and not strict; `logged_result` keeps its text out of the DEBUG lines and `waifu chat --verbose`

**Dependencies:** 340 · **Files:** `src/waifu/brain/clipboard_tools.py`, `src/waifu/brain/tools.py`, `src/waifu/guest.py`, `src/waifu/app.py`, `src/waifu/brain/ollama_brain.py`, `src/waifu/cli.py`, tests · **Scope:** M

## Task 342: Ctrl+Alt+T

**Acceptance criteria:**
- [ ] `hotkeys.translate` (T) is an action of its own; an unwritten T that another hotkey already takes is switched off with a warning; the example block holds it; `waifu check` names it
- [ ] The hotkey asks `CLIPBOARD_QUESTION` without blocking; `ask_now` is ignored while she is busy or a question waits, and `run` answers it on its thread like a voice turn (thinking, filler, card, remembered, follow-up window), before the hold after a cut and also while the listen pause holds
- [ ] `run` wires the hotkey to `ask_now`

**Dependencies:** 341 · **Files:** `src/waifu/hotkeys.py`, `src/waifu/hotkey_actions.py`, `src/waifu/config.py`, `config.example.yaml`, `src/waifu/app.py`, `src/waifu/cli.py`, tests · **Scope:** M

## Task 343: Master's own Japanese words

**Acceptance criteria:**
- [ ] The lesson store keeps added words under an optional `custom` key, validated, deduplicated, capped at 200, and still reads a v1.39 file
- [ ] `japanese_lesson` `add` learns a built-in word, adds a new one, or says it is there already; refused in her own turns and in guest mode; quizzes and grades take added words; progress counts them apart; `waifu jepang progres` names them

**Dependencies:** 340a · **Files:** `src/waifu/japanese.py`, `src/waifu/brain/japanese_tools.py`, `src/waifu/cli.py`, tests · **Scope:** M

## Task 344: Docs

**Acceptance criteria:**
- [ ] README "Artinya apa" (and in the table of contents), Ctrl+Alt+T in the hotkey section, the added words in the Japanese section, the guest-mode list; CHANGELOG v1.56; full suite, ruff, and JS tests clean

**Dependencies:** 342, 343 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S
