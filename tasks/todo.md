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

**Dependencies:** 162 · **Files:** `src/waifu/gacha/hoyolab.py`, `tests/test_gacha_hoyolab.py` · **Scope:** M

## Task 164: The `gacha_income` tool and the persona line

**Acceptance criteria:**
- [ ] A non-strict `gacha_income {game?, month?}` in its own provider; without `game` every configured game with failures under `errors`; without `month` this month with `today`; an earlier month without it; a month outside the window, a bad format, an unknown game or key are invalid without asking HoYoLAB; every game failing is an error result
- [ ] Reads kept 15 minutes per game and month (this month's per server day); the description says what each game gives, pulls at 160 from the premium currency only, information for planning, never a push to pull, spend, or top up, only when asked
- [ ] The persona names `gacha_income` under the game tools; the strict budget unchanged

**Dependencies:** 163 · **Files:** `src/waifu/gacha/income_tools.py`, `tests/test_gacha_income_tools.py`, `config/persona.md`, `tests/test_brain_persona.py` · **Scope:** M

## Task 165: Wiring and `waifu gacha income`

**Acceptance criteria:**
- [ ] `run` and `chat` add `IncomeTools` after the game data tools when the games are on; the budget test lists `gacha_income`
- [ ] `waifu gacha income [game] [--month YYYY-MM]` prints each game's month (also with the games off), exits 1 when a game failed, and refuses a month outside the window with exit 2 before asking HoYoLAB

**Dependencies:** 164 · **Files:** `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 166: The weekly report's income part

**Acceptance criteria:**
- [ ] With an income source, one more lane in the same budget reads this month for every game; each game's `income` or `income_problem`; a late read ran out of time; without a source nothing changes (the exact JSON and the calls stay as before)
- [ ] `income_this_month` and `income_problem` in the JSON only with a source; the description and the prompt name it; `waifu gacha report` prints one line a game

**Dependencies:** 163 · **Files:** `src/waifu/gacha/weekly.py`, `tests/test_gacha_weekly.py`, `src/waifu/cli.py`, `tests/test_cli.py` · **Scope:** M

## Task 167: Docs

**Acceptance criteria:**
- [ ] README "Pendapatan gacha bulanan", a command row, a line in "Laporan game mingguan", checklist items; CHANGELOG v1.33; full suite and ruff clean

**Dependencies:** 165, 166 · **Files:** `README.md`, `CHANGELOG.md`, `tasks/plan.md`, `tasks/todo.md` · **Scope:** S
