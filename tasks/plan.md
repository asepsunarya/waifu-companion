# Implementation Plan: waifu-companion v1

## Overview

Build the voice companion described in `SPEC.md` as a Python package that runs
entirely on the owner's PC: devices → voice → brain → app. Every module is
testable without hardware; real-hardware checks are a manual checklist at the end.

## Architecture Decisions

- **Contracts first.** `devices/base.py`, `brain/base.py`, `voice/base.py`, and `config.py` are committed in Task 1 so modules can be built in parallel against a fixed surface.
- **Wake word from the transcript, not a keyword model.** The name is configurable and Indonesian; training an openWakeWord model per name is heavy. Speaker verification runs first, so only the owner's speech is transcribed. The RTX 5060 Ti makes continuous Whisper cheap.
- **Energy segmenter, not a neural VAD.** Boring, dependency-free, testable with synthetic audio; Whisper's own `vad_filter` and speaker verification reject what slips through.
- **Two LLM backends behind one `Brain` contract.** Ollama `qwen3:8b` is the free default; Claude (`claude-opus-5-5`, effort `low`, server-side fallbacks on) is a config switch. The Claude history is append-only per session because thinking blocks are bound to the conversation prefix; sessions end by starting fresh, never by trimming.
- **Two tools only** (`control_device`, `list_devices`) with the device list in a stable system prompt: fewer round trips, valid prompt caching, and new devices need no code.
- **IR devices are "learned command" devices.** Commands are whatever the owner teaches with `waifu learn-ir`; `temp` + 24 maps to `temp_24`. Works for any remote, including the Samsung AC and Wanbo Dali.
- **Half-duplex audio.** The mic is paused while she speaks; no echo cancellation needed.

## Task List

### Phase 1: Foundation
- [x] Task 1: Config models and module contracts

### Phase 2: Devices
- [x] Task 2: Device registry, mock device, IR code store, IR device
- [x] Task 3: Tuya light, plug, and IR blaster
- [x] Task 4: Broadlink IR blaster
- [x] Task 5: PC power device
- [x] Task 6: Registry factory from config (incl. dry-run)

### Checkpoint: Devices
- [x] Full suite and ruff clean; `build_registry(load_config("config.example.yaml"), dry_run=True)` lists all example devices

### Phase 3: Voice
- [x] Task 7: Audio I/O and energy segmenter
- [x] Task 8: Speaker verification
- [x] Task 9: Whisper speech-to-text
- [x] Task 10: Bilingual text-to-speech router

### Checkpoint: Voice
- [x] Full suite and ruff clean

### Phase 4: Brain
- [x] Task 11: Tool specs, persona prompt, session clock
- [x] Task 12: Claude backend
- [x] Task 13: Ollama backend and brain factory

### Checkpoint: Brain
- [x] Full suite and ruff clean; fake-client tests show a two-device request producing two tool calls and one reply

### Phase 5: App
- [x] Task 14: Wake gate and Assistant loop
- [x] Task 15: CLI and `python -m waifu`
- [x] Task 16: README setup guide and hardware checklist

### Checkpoint: Complete
- [x] All acceptance criteria met, full suite and ruff clean
- [x] `waifu chat --dry-run` and `waifu devices --dry-run` run against the example config
- [ ] Review with human before real-hardware testing

### Phase 6: Autostart (v1.1, see SPEC → autostart)
Decision: a per-user `HKCU\...\Run` value that opens a minimized console window, not a
hidden process or a scheduled task: no admin rights, visible and closable, listed in Task
Manager → Startup apps. A file lock keeps a manual `waifu run` from doubling the instance.
- [x] Task 17: Single-instance lock
- [x] Task 18: Autostart Run-key manager
- [x] Task 19: Preload Whisper and speaker models
- [x] Task 20: CLI `autostart`, `run --startup-delay/--announce`, `--log-file`, README

### Checkpoint: Autostart
- [x] Full suite and ruff clean; no test touched the real registry (conftest guard)
- [ ] `waifu autostart status` works on the owner's PC; enabling is left to the owner after `waifu enroll`

### Phase 7: Anime voice (v1.2, see SPEC → anime voice)
Decision: RVC (Applio) converts every reply, Indonesian and Japanese, into one voice trained
locally from the Tsukuyomi-chan corpus; GPT-SoVITS is skipped because it cannot speak
Indonesian. Applio runs in its own Python 3.12 env behind a loopback HTTP server so the model
loads once; on any converter failure Cyrene falls back to the plain edge-tts voice.
- [x] Task 21a: `tts.rvc` config (committed with its tests)
- [x] Task 21: RVC HTTP server script (`tools/rvc_server.py`)
- [x] Task 22: RVC client and server-process manager (`voice/rvc.py`)
- [x] Task 23: SpeechRouter voice conversion with fallback
- [x] Task 24: CLI wiring (`run`/`say`/`chat --speak`, `say --raw`, `check`), README, example config

### Checkpoint: Anime voice
- [x] Full suite and ruff clean; no test spawned the real server or loaded Applio
- [x] On the owner's PC: converted speech is closer to the Tsukuyomi voiceprint than to edge-tts (ECAPA) and Indonesian stays intelligible (Whisper) — measured 2026-10-12: 0.82 vs 0.47, identical transcripts, +0.4 s per reply

### Phase 8: Avatar (v1.3, see SPEC → avatar)
Decision: the official Tsukuyomi-chan VRM (matches the voice; personal use allowed, credit
shown) rendered by three.js + three-vrm in a page Cyrene serves on loopback, shown as a
transparent always-on-top Electron mascot; events (state, emotion, viseme track) flow over
Server-Sent Events so Python stays the only audio player and needs no new dependency.
Lip-sync uses a viseme track computed from the very audio about to be played.
- [x] Task 24a: `avatar` config, vendored three.js/three-vrm, Electron manifest (committed)
- [x] Task 25: Emotion tags in replies (`avatar/emotion.py`, persona)
- [x] Task 26: Viseme track from audio (`avatar/lipsync.py`)
- [x] Task 27: Event bus and loopback server with SSE (`avatar/bus.py`, `avatar/server.py`)
- [x] Task 28: Viewer page (`avatar/web/index.html`, `logic.js`, `avatar.js`, JS tests)
- [x] Task 29: Window launcher and Electron shell (`avatar/window.py`, `avatar-app/`)
- [x] Task 30: Integration: Assistant, CLI (`avatar`, run/say/chat, check), README, example config

### Checkpoint: Avatar
- [x] Python and JS suites, ruff clean; no test opened a window or loaded the real VRM
- [ ] On the owner's PC: the VRM renders in the page, lip-sync and emotions follow a real reply, and the mascot window is transparent and draggable

### Phase 9: Gacha buddy (v1.4, see SPEC → notify, gacha buddy)
Decisions: HoYoLAB (notes, check-in, accounts) through genshin.py behind a sync client
that runs each call in its own `asyncio.run`, so the voice loop and the watcher thread
share nothing async. Pull history comes from the game's own history API with the authkey
the game left in its web cache (verified on the owner's PC), falling back to a cookie-made
authkey; genshin.py's history routes are stale for HSR and ZZZ, so requests use the cached
URL's base. Reminders are pure rules over a status snapshot plus a small JSON state, run by
a watcher thread inside `waifu run`; she speaks them between utterances and pushes them to
the phone through ntfy (no account, JSON publish). The brain gets the game tools through a
`ToolProvider` protocol, so devices and games stay independent. No quiet hours (owner's
choice).
- [x] Task 30a: `gacha` and `notify` config, `genshin` dependency, example config (committed with its tests)
- [x] Task 31: Push notifications (`notify.py`, `waifu notify-test`) and the no-internet test guard
- [x] Task 32: Game facts and server-time helpers (`gacha/games.py`)
- [x] Task 33: HoYoLAB client: accounts, status, check-in, authkey, error kinds (`gacha/hoyolab.py`)
- [x] Task 34: Authkey from the game cache and the history API (`gacha/history.py`, part 1)
- [x] Task 35: History store, pity and 50/50 stats, history report (`gacha/history.py`, part 2)
- [x] Task 36: Tool providers for the brain, the game tools, persona (`brain/tools.py`, `gacha/tools.py`)
- [x] Task 37: Reminder rules and their state (`gacha/reminders.py`)
- [x] Task 38: Watcher thread and spoken announcements (`gacha/watcher.py`, `Assistant.announce`)
- [x] Task 39: CLI `gacha` commands, `run`/`chat`/`check` wiring, README

### Checkpoint: Gacha buddy
- [x] Full suite and ruff clean; no test reached the internet (conftest guard)
- [x] On the owner's PC: `waifu gacha history hsr --full` imports the real HSR history from the cached authkey (1101 pulls, 2026-10-12); the owner still compares the pity with the game
- [ ] With the owner's cookie and ntfy topic: `gacha status`, `gacha checkin`, `notify-test`, and one spoken + pushed reminder in `run`

### Phase 10: Live2D and interactions (v1.5, see SPEC → Live2D and interactions)
Decision: a second renderer (PixiJS 8 + untitled-pixi-live2d-engine for Cubism 5) behind the
same page and event protocol, so voice, lip-sync, emotions, and the mascot shell stay shared;
Live2D's proprietary Cubism Core comes from the owner's own SDK download and is never
committed. Interactions run in the page and reach Python through one token-protected POST
route; spoken reactions use prepared lines, not the LLM, so they are instant.
- [x] Task 40a: avatar engine/interactions config, vendored PixiJS 8.21 and the Live2D engine, Core ignore rule (committed)
- [x] Task 40: Server: `/config.json`, Live2D model and Core routes, Host check, token-protected `POST /interact`
- [x] Task 41: `InteractionHandler` and Assistant hooks (`busy`, `speak_reaction`, `listen_now`)
- [x] Task 42: Pure interaction and Live2D logic in `logic.js` with node tests
- [x] Task 43: Live2D renderer (`live2d.js`) and the engine switch in `index.html` (browser-checked 2026-10-13 with the owner's Core 6 and model)
- [x] Task 44: Shared input layer (`interaction.js`) and VRM renderer hooks
- [x] Task 45: Electron preload: cursor feed, custom drag, click-through
- [x] Task 46: CLI wiring, `check`, README, example config

### Checkpoint: Live2D and interactions
- [x] Python and JS suites, ruff clean; no test loaded the real model or Core or opened a window (2026-10-12: 1715 pytest, 204 node tests)
- [x] On the owner's PC: the Live2D model renders, lip-syncs, shows emotions, follows the cursor, and reacts to pats/pokes (in-app browser, 2026-10-13); the Electron mascot loads it (all model files served, the window closes with Cyrene)
- [ ] On the owner's PC, by hand: the mascot drags, lets clicks through on transparent pixels, and stays above normal windows

### Phase 11: Long-term memory (v1.6, see SPEC → long-term memory)
Decision: a small JSON list of facts the model saves through two tools (`remember`, `forget`),
put whole into the system prompt at the start of every session. Within a session the prompt
stays byte-identical (preserved thinking, prompt cache), so a note saved mid-session lives in
the conversation until the next session lists it. Secrets are refused in code, not only by the
prompt. No embeddings: 100 short notes fit easily.
- [x] Task 47a: `MemoryConfig` (`memory.enabled/path/max_facts/max_chars`) with tests (committed)
- [x] Task 47: `memory.py`: `MemoryStore`, `Fact`, `looks_secret`, `memory_prompt_section`
- [x] Task 48: `brain/memory_tools.py`: `MemoryTools` provider (`remember`, `forget`)
- [x] Task 49: Brains read a callable system prompt once per session; `build_brain(memory_section=...)`
- [x] Task 50: CLI wiring (`run`, `chat`, `waifu memory`, `check`), README, example config

### Checkpoint: Long-term memory
- [x] Python suite and ruff clean; no test touched the repository's `data/memory.json` or called an LLM (2026-10-13: 1966 pytest)
- [ ] On the owner's PC: a fact told in one session is used in the next one; "lupakan ..." deletes it; a password is refused

### Phase 12: Schedules, reminders, and routines (v1.7, see SPEC → schedules, reminders, and routines)
Decision: entries the model creates through tools, kept in `data/schedule.json` with the same
file rules as the memory, and a daemon thread in `waifu run` that fires them like the gacha
watcher announces (spoken + phone push). Device actions run silently and speak only on failure,
since the owner may be asleep. Routines are written by the owner in config.yaml, never by the
model. The PC itself (shutdown, restart, sleep) is never scheduled.
- [x] Task 51a: `schedule` and `routines` config, routines validated against the devices
- [x] Task 51: `schedule.py`: entries, time rules, `ScheduleStore`, `due`
- [x] Task 52: `routines.py`: `run_routine`, `describe_routine`, `routine_problems`
- [x] Task 53: `scheduler.py`: the firing thread
- [x] Task 54: `brain/schedule_tools.py`: `schedule`, `list_schedules`, `cancel_schedule`, `run_routine`
- [x] Task 55: CLI wiring (`run`, `chat`, `waifu schedule`, `waifu routine`, `check`), README, example config
- [x] Task 56: Guard the plug that powers the PC (`powers_pc`) like the PC; tell missed one-shot reminders

### Checkpoint: Schedules, reminders, and routines
- [x] Python suite and ruff clean; no test touched `data/`, ran a real device, slept for real, or called an LLM (2026-10-13: 2350 pytest)
- [ ] On the owner's PC: a reminder in a few minutes is spoken and pushed; a repeating one moves to the next day; a routine runs by voice

### Phase 13: PC media and apps (v1.8, see SPEC → PC media and apps)
Decision: two new device types instead of new tools, so voice, schedules, routines, and dry
runs work with them unchanged. Media and volume go through the keyboard's own media keys
(`SendInput`, no new dependency); apps start only from the owner's list in config.yaml, and
nothing is ever closed. The test guards are strengthened first so no test can press a real
key or open a real program.
- [x] Task 57a: `media` and `apps` device config; conftest refuses `os.startfile` and real key presses
- [x] Task 57: `devices/media.py`: `MediaKeys`
- [x] Task 58: `devices/apps.py`: `AppLauncher`
- [x] Task 59: registry, value ranges, `check`, README, example config

### Checkpoint: PC media and apps
- [x] Python suite and ruff clean; no test pressed a key or started a program (2026-10-13: 2482 pytest)
- [ ] On the owner's PC: "pause musiknya", "volume 30", and "buka Spotify" work by voice; a routine with `media stop` runs

### Phase 14: Confirmation in code (v1.9, see SPEC → confirmation in code)
Decision: the persona's "ask before turning the PC off" becomes a gate in the tool path. The
first call is refused and remembered; the same call runs only in the owner's very next turn
when their own words are a yes. The model can never confirm for them.
- [x] Task 60: `brain/confirm.py`: `is_yes`, `needs_confirmation`, `ConfirmationGate`
- [x] Task 61: `run_tool`, both brains, `build_brain`, persona, README

### Checkpoint: Confirmation in code
- [x] Python suite and ruff clean; no test reached a real power action (2026-10-13: 2635 pytest)
- [ ] On the owner's PC: "matikan komputer" → she asks; "tidak" → nothing happens; "iya" → the 30-second shutdown starts and "batalkan" cancels it

### Phase 15: Looking at the screen (v1.10, see SPEC → looking at the screen)
Decision: one screenshot per request, only when the owner's own words in that turn ask her to
look, sent to Claude as an image in the tool result. GDI capture and a stdlib PNG encoder keep
it free of new dependencies; the test guards are strengthened first so no test can capture the
owner's real screen.
- [x] Task 62a: `vision` config; conftest refuses real screen capture
- [x] Task 62: `screen.py`: `capture_screen`, `encode_png`
- [x] Task 63: `ScreenTools`, provider hooks in the brains, image tool results, CLI, check, README

### Checkpoint: Looking at the screen
- [x] Python suite and ruff clean; no test captured the real screen or called Claude (2026-10-13: 2758 pytest)
- [ ] On the owner's PC: "Cyrene, lihat layarku" with the Claude backend gets an answer about what is on screen; a question that does not ask her to look never captures

### Phase 16: Cutting her off (v1.11, see SPEC → cutting her off)
Decision: a double-click on the avatar while she speaks stops the playing clip at once and
opens listening; the microphone stays paused during her speech (no voice barge-in without
echo cancellation).
- [x] Task 64: `Player.play`/`stop`, `Assistant.interrupt` and `speaking`
- [x] Task 65: handler interrupt on double-click, renderers close the mouth, CLI wiring, README

### Checkpoint: Cutting her off
- [x] Python and JS suites, ruff clean; no test played or recorded real audio (2026-10-13: 2786 pytest, 223 JS)
- [ ] On the owner's PC: a double-click during a long answer stops her at once and she listens

### Phase 17: Morning briefing (v1.12, see SPEC → morning briefing)
Decision: one tool (`daily_briefing`: date, Open-Meteo weather, today's schedule) used both on
request and by a once-a-day greeting. The greeting is a queued brain request handled between
utterances, like announcements, so it never races a reply; the model words it with her
persona and memory, and calls the game tool itself.
- [x] Task 66a: `briefing` config
- [x] Task 66: `weather.py`: `WeatherClient` (Open-Meteo, geocoding, WMO conditions)
- [x] Task 67: `briefing.py`: `BriefingTools`, `BriefingTimer`
- [x] Task 68: `Assistant.ask_aloud`, wiring, `waifu briefing`, check, README

### Checkpoint: Morning briefing
- [x] Python suite and ruff clean; no test reached the internet, slept for real, or touched `data/` (2026-10-14: 2985 pytest on the committed tree plus tasks 66-68)
- [ ] On the owner's PC: with `briefing.city` set, the morning greeting comes once, names the weather and the day's schedule, and "cuaca hari ini?" works any time

### Phase 17b: How she calls the owner (v1.12.1, see SPEC → how she calls the owner)
- [x] Task 69a: `assistant.owner_call`, `{owner}` in the persona and `build_system_prompt`
- [x] Task 69: every spoken, pushed, or printed sentence uses `owner_call`; gates skip it

### Phase 18: API cost monitor (v1.13, see SPEC → API cost monitor)
Decision: record the `usage` of every Claude response in a local ledger and price it with the
published per-model rates (overridable); the owner asks by voice or `waifu usage`, and an
optional monthly budget warns at 80 % and 100 % without ever stopping her.
- [x] Task 70a: `usage` config
- [x] Task 70: `usage.py`: `TokenUsage`, `cost_usd`, `UsageLedger`
- [x] Task 71: `ClaudeBrain(on_usage=...)` and `brain/usage_tools.py`
- [x] Task 72: wiring (ledger, budget notices), `waifu usage`, check, README

### Checkpoint: API cost monitor
- [x] Python suite and ruff clean; no test called Claude or touched `data/` (2026-10-14: 3235 pytest on the committed tree plus tasks 70-72)
- [ ] On the owner's PC: after a few replies, "biaya hari ini berapa?" and `waifu usage` show plausible tokens and dollars

### Phase 19: Cyrene on the phone (v1.14, see SPEC → Cyrene on the phone)
Decision: a second, authenticated web server inside `waifu run`, bound to 127.0.0.1 and exposed
only through `tailscale serve` (real HTTPS for the phone microphone, reachable only from the
owner's tailnet), plus one-time pairing. The phone page reuses the avatar renderers; the
Assistant answers phone turns under the same turn lock and returns her voice and mouth track to
the phone instead of speaking on the PC.
- [x] Task 73a: `phone` config
- [x] Task 73: `phone/auth.py`: pairing codes and devices
- [x] Task 74: `phone/server.py`: routes, auth, file serving, `PhoneBackend`
- [x] Task 75: the phone page (PWA, chat, hold-to-talk, playback) and its pure JS helpers
- [x] Task 76: `Assistant.reply_for_phone`, `run` wiring, `waifu phone`, check, README

### Checkpoint: Cyrene on the phone
- [x] Python and JS suites, ruff clean; no test reached the network, Tailscale, a real brain, or `data/` (2026-10-14: 3731 pytest and 313 node tests on the committed tree plus tasks 73-76)
- [x] In a browser at phone size against a fake backend: pairing, chat, a talk round trip, audio and mouth (task 75's check, repeated 2026-10-14 after the review fixes against the real PhoneServer with a fake backend: wrong code 403, pairing, chat with the mouth moving, a long reply shown from its first line, and a talk round trip with a stubbed microphone; the in-app browser cannot register any service worker, so installing the app is checked on the phone)
- [ ] On the owner's Android: install via Tailscale URL, pair, chat and talk, "matikan lampu" works

### Phase 20: seeing through the phone camera (v1.15, see SPEC → seeing through the phone camera)
Decision: the photo rides in the owner's own message (an image block before the turn's text), not
a tool call: the owner opened the viewfinder, so the photo is already what they want her to see,
and one request answers it. A `TurnImages` holder injected into the Claude brain carries it, so
the `Brain` contract stays as it is; the PC webcam and presence detection wait for a later version.
- [x] Task 77a: `phone.camera` config
- [x] Task 77: `TurnImages` and photos in the Claude brain's user messages
- [x] Task 78: photos on the phone server (`/chat`, `/talk` JSON, `jpeg.py`, `/config.json`)
- [x] Task 79: the viewfinder on the phone page and its pure JS helpers
- [x] Task 80: Assistant and `run` wiring, check, README

### Checkpoint: seeing through the phone camera
- [x] Python and JS suites, ruff clean; no test opened a camera or reached Claude (2026-10-14: 3970 pytest and 380 node tests on the committed tree plus tasks 77-80 and the review fixes)
- [x] In a browser at phone size with a stubbed camera: viewfinder, flip, a photo with a chat and with a talk, the thumbnail, the camera off after close/hide/2 minutes (2026-10-14 after both fix rounds, against the real PhoneServer with the camera on and a fake backend: a 1280x720 JPEG reached it with camera:true for a chat and a talk, flip asked for the user camera and ended the old track, close and a hidden page ended the track, a message after close went without photo or camera flag; the 2-minute timer is covered by mock-timer node tests)
- [ ] On the owner's Android: "ini apa?" with the camera open is answered about what it shows

### Phase 20.1: keeping her through fullscreen games (v1.15.1, bug fix, see SPEC)
Decision: keep painting regardless of Chromium's occlusion guess, recover a lost WebGL context or a
crashed GPU/renderer process by reloading the page, restart a crashed Electron, re-place her after
display changes, and log every such event so the owner's next game session shows the real cause.
She still hides behind a fullscreen game (the owner's choice).
- [x] Task 85: Electron shell and page (occlusion, throttling, reloads, display changes, diagnostics, 30 fps)
- [x] Task 86: `avatar/window.py` (piped diagnostics, supervision) and README troubleshooting

### Checkpoint: fullscreen games
- [x] Python and JS suites, ruff clean; no test spawned Electron (2026-10-15: 4005 pytest and 454 node tests on the committed tree plus tasks 85-86 and the review fixes; main.js and preload.js run against a fake `electron` in a VM context)
- [x] A lost WebGL context recovers in a browser; Electron's stdout reaches Python (windowless probe) (2026-10-15: the real AvatarServer page reloaded and rendered again after WEBGL_lose_context; task 85 also checked the phone page redraw; a windowless Electron 44 app's console.log/console.error reached a Python pipe)
- [ ] On the owner's PC: a Fullscreen game session; she is back after Alt+Tab or on the other monitor, and the `Cyrene mascot:` lines are recorded

### Phase 21: HSR data (v1.16, see SPEC → HSR data)
Decision: three read-only tools in a sibling provider (`HsrTools`) over three new HoyolabClient
methods, with compact capped JSON for voice and a short in-memory cache; data only, no advice
code, no reminders or briefing changes, never a HoYoLAB setting changed. The calendar's times are
anchored on its own `now` to undo genshin.py 1.7.31's relabelled timezone.
- [x] Task 81: contract: `gacha/starrail.py` dataclasses, `gacha/hsr_tools.py` static specs and argument checks
- [x] Task 82: HoYoLAB side: `hsr_characters`, `hsr_endgame`, `hsr_calendar`, `chronicle_private`, the readers
- [x] Task 83: tool results, name matching, size caps, and the cache
- [x] Task 84: wiring (`run`/`chat`), `waifu gacha hsr`, persona, README

### Checkpoint: HSR data
- [x] Python suite and ruff clean; no test reached HoYoLAB; `update_settings` never called (2026-10-15: 4231 pytest and 454 node tests on the committed tree plus tasks 81-84 and both review-fix rounds; genshin.py faked with real models from deep-copied sample dicts, tests assert `update_settings` is never called, and `src` has no `update_settings` call)
- [x] One real read-only run of `waifu gacha hsr characters|endgame|events` on the owner's account: privacy behaviour, latency, calendar times vs the game (2026-10-15: no chronicle_private with the owner's settings; the full roster in 3.2 s; endgame ends at 03:00 WIB = the 04:00 server reset; warps end 10:59 WIB = 11:59 server time, so the calendar anchoring holds; HoYoLAB gives warps no name, now left out; genshin.py warns once about an unknown event status 'DoubleRewardActStatusFinish')
- [ ] By voice or chat: "Acheron-ku Eidolon berapa?", "Memory of Chaos-ku berapa bintang?", "banner apa sekarang?"

### Phase 22: PC health (v1.17, see SPEC → PC health)
Decision: no new dependency: Windows' own counters through ctypes and nvidia-smi for the GPU; one
read-only tool, a `waifu pc` report, and a small watcher that speaks up about a nearly full drive
or a GPU that stays hot. No CPU temperature (Windows has none without a kernel driver).
- [x] Task 87a: `pc_health` config and the snapshot values
- [x] Task 87: `pc_health.py` readers (CPU, RAM, drives, uptime, nvidia-smi)
- [x] Task 88: `brain/pc_tools.py` and `pc_watch.py`
- [x] Task 89: wiring (`run`/`chat`), `waifu pc`, check, README

### Checkpoint: PC health
- [x] Python suite and ruff clean; no test touched the real counters, nvidia-smi, audio, or a push (2026-10-16: 4374 pytest and 454 node tests on the committed tree plus tasks 87-89 and both review-fix rounds; `test_pc_health.py` refuses `ctypes.WinDLL` and `subprocess.Popen` outright, `test_cli.py` fakes `CpuLoad`, `read_snapshot`, `gpu_status`, and `PcWatcher` for every test, the watcher tests use a fake notifier; task 87 ran the readers once outside pytest, read-only, and they matched Windows' own numbers)
- [x] `waifu pc` on the owner's PC matches Task Manager and nvidia-smi (2026-10-16: RAM 24.7/31.9 GB, C: 63.6 and E: 26.1 GB free, GPU 53°C 10% 5.1/15.9 GB 37 W, uptime 26 days 13 h since the 19 Sep boot, all equal to Windows' own figures and nvidia-smi; 2.3 s)
- [ ] By voice or chat: "PC-ku gimana?", "GPU-ku panas nggak?"

### Phase 23: web search and free offers (v1.18, see SPEC → web search and free offers)
Decision: Claude's server-side web search in her conversation; free games from Epic's own feed
and GamerPower (credited); AI programs from a weekly scan by Claude with web search in a request
that has no other tools; new offers announced and their links pushed to the phone. No new
dependency; nothing is ever claimed or applied for on the owner's behalf.
- [x] Task 90a: `web_search`, `free_offers`, `usage.web_search_per_1000_usd` config and the offer values
- [x] Task 90: web search in the brain (server tool, system note, fallback echo rule, search fees)
- [x] Task 91: free games (Epic + GamerPower readers)
- [x] Task 92: AI offers scan and the offers store
- [x] Task 93: tools, watcher, notifier click links, `waifu offers`, check, README

### Checkpoint: web search and free offers
- [x] Python suite and ruff clean; no test reached Epic, GamerPower, Claude, or ntfy (2026-10-16: 4885 pytest and 454 node tests on the committed tree plus tasks 90-93 and both review-fix rounds; ruff check and format clean on src, tests, and tools; the full suite run once more with a socket guard outside the repo that refuses and records every DNS lookup or connection beyond loopback: 0 attempts. Games are read through an injected httpx client on httpx's MockTransport, Claude through injected fake clients, ntfy through a fake poster; `test_cli.py` refuses `read_free_games` and a real `anthropic.Anthropic` for every test. `tests/conftest.py` still guards only urllib and aiohttp, not httpx)
- [x] `waifu offers games` on the owner's PC matches the Epic store; one real `waifu offers ai --scan` with its cost (2026-10-16: Agent A and Bad Cheese free until Thu 22:00 WIB, Home and Sker Ritual from then, one Steam key via GamerPower, 4.7 s; scan 1 found 1 offer for $0.20 with 6 searches, so the prompt now names the program families and the default is 10 searches; scan 2 found 4 more (Claude for Startups, AI for Science, OpenAI Researcher Access, Google for Startups Cloud) for $0.26)
- [ ] By voice or chat: a web question, "ada game gratis apa?", "ada promo AI gratis?"

### Phase 24: redeem codes (v1.19, see SPEC → redeem codes)
Decision: codes from the community API behind Hoyo Buddy (checked against HoYoLAB with an Asia
account), polled every few hours; she tells the owner and redeems through HoYoLAB with
genshin.py only when asked, unless `auto_redeem` is on; one code at a time with HoYoLAB's
cooldown respected.
- [x] Task 97a: `redeem_codes` config and the code values
- [x] Task 97: the code source and the code store
- [x] Task 98: `HoyolabClient.redeem_codes`
- [x] Task 99: tools, watcher, `waifu gacha codes`, check, README

### Checkpoint: redeem codes
- [x] Python suite and ruff clean; no test reached the source or HoYoLAB, nothing was redeemed (2026-10-16: 5213 pytest and 454 node tests on the committed tree plus tasks 97-99 and both review-fix rounds; ruff check and format clean on src, tests, and tools; the full suite run once more with a recorder outside the repo for every DNS lookup, socket connection, aiohttp or urllib request beyond loopback and every call to the real `genshin.Client.redeem_code`: the only 7 attempts were `test_safety_guard.py`'s own tests that the guards refuse them, all refused; none to hoyo-codes.seria.moe, none from a v1.19 test, and no real `redeem_code` call. The source is read through an injected httpx client on httpx's MockTransport, genshin.py through fake clients with made-up cookies, the waits, clock, and ntfy through fakes; `test_cli.py` refuses `read_codes` and replaces `CodeWatcher` for every test, and every store lives under tmp_path; `tests/conftest.py` now refuses sockets beyond loopback below every client)
- [x] `waifu gacha codes hsr` on the owner's PC lists the codes the API has (2026-10-17: HSR 10, Genshin 10, ZZZ 4 codes; 'Item*count;Item*count' rewards now read as counted items for the spoken notices; nothing redeemed)
- [ ] The owner redeems once by voice or with `waifu gacha codes hsr --redeem`

### Phase 25: to-do list (v1.20, see SPEC → to-do list)
Decision: a small local task store beside the schedules (tasks have dates, never times), six
strict tools, the due and overdue tasks in her morning greeting, and `waifu todo`.
- [x] Task 103a: `todo` config and the `Task` value
- [x] Task 103: `TodoStore`
- [x] Task 104: `TodoTools` and the briefing's tasks
- [x] Task 105: wiring, `waifu todo`, check, README

### Checkpoint: to-do list
- [x] Python suite and ruff clean (2026-10-16: 5543 pytest and 454 node tests on the committed tree plus tasks 103-105 and the review fixes; ruff check and format clean on src, tests, and tools. Every store lives under tmp_path with an injected clock; `test_cli.py` refuses a to-do file outside tmp_path. A test sends the Claude request with every tool on and checks Anthropic's strict tool limits: only the tools that require an argument are strict)
- [ ] By voice or chat: add a task with a due day, list, finish one; the next morning greeting mentions a due task

### Phase 26: songs on YouTube (v1.21, see SPEC → songs on YouTube)
Decision: no API key and no scraping: with Claude she finds the song's YouTube link through her
web search and opens it in the default browser; otherwise the search page opens. Only YouTube
addresses, only when Master's words ask for music; pause and skip stay with the media device.
- [x] Task 108a: `music` config
- [x] Task 108: `music.py` and `MusicTools`
- [x] Task 109: wiring, `waifu youtube`, check, README

### Checkpoint: songs on YouTube
- [x] Python suite and ruff clean; no test opened anything (2026-10-16: 5877 pytest and 454 node tests on the committed tree plus tasks 108-109 and both review-fix rounds; ruff check and format clean on src, tests, and tools. Every opener in the music tests is a recording fake: `MusicTools` and `open_link` take `startfile`, `test_cli.py` replaces `cli.open_in_browser`, and the default `music.open_in_browser` looks `os.startfile` up on every call, so the `tests/conftest.py` guard that refuses it and fails the test at teardown covers it too, and the suite passes with that guard on)
- [ ] By voice or chat: "putar lagu ..." opens and plays the song; "pause" pauses it

### Phase 26.1: small fixes (v1.21.1)
Decision: five small defects found while building v1.16-v1.21, each fixed with a failing test
first (Prove-It); no new feature, config key, or dependency.
- [x] A: a cited answer's pieces no longer double a space where they meet, and a line break there stays (`brain/claude.py`)
- [x] B: `api_usage` also gives the web searches and their fees, already in the cost, at the current search price and never more than the cost (`brain/usage_tools.py`, `usage.py`)
- [x] C: genshin.py's "please report to developer" parsing notices stay out of the console and the log (`cli.py`)
- [x] D: a secret word alone is no secret; only one followed by its value is refused, in notes and tasks; the words that tie a secret to Master do not count, digit groups are one value, and only a 20-digit token is the value of "token listrik" (`memory.py`)
- [x] E: GamerPower titles lose their Key word and store brackets ("Dwarven Realms (Steam) Key Giveaway" → "Dwarven Realms"), while a game's own Key stays ("Skeleton Key (Steam) Giveaway" → "Skeleton Key") (`offers/games.py`)

### Phase 27: Genshin and ZZZ data (v1.23, see SPEC → Genshin and ZZZ data)
Decision: the three HSR tools become `game_characters`, `game_endgame`, and `game_events`, each
with an optional `game` from `gacha.games`, so they stay non-strict and the strict budget does
not move; HSR results stay as they are. Genshin and ZZZ readers feed shared frozen dataclasses
through thin HoyolabClient methods; detail only for the at most three characters a name
matches (ZZZ asks once per agent); read-only, never a HoYoLAB setting changed. Tasks 111-114
build in parallel on the contract of 111a.
- [x] Task 111a: contract: SPEC, `gacha/game_data.py`, the Genshin and ZZZ dataclasses, `CHRONICLE_PRIVATE` per game, the 9-mode request table, `_endgame`
- [x] Task 111: Genshin source: `gacha/genshin_impact.py` readers and the four `genshin_*` HoyolabClient methods
- [x] Task 112: ZZZ source: `gacha/zenless.py` readers and the four `zzz_*` HoyolabClient methods
- [x] Task 113: `GameDataTools` (renamed tools, game resolution, Genshin and ZZZ results, cache), wiring, both personas
- [x] Task 114: `waifu gacha genshin|zzz characters|endgame|events`
- [x] Task 115: integration, CHANGELOG, README, the strict limits

### Checkpoint: Genshin and ZZZ data
- [x] Python suite and ruff clean; no test reached HoYoLAB; `update_settings` never called
- [ ] One real read-only run of `waifu gacha genshin|zzz characters|endgame|events` on the owner's account: privacy behaviour, latency, calendar times and stage names vs the game
- [ ] By voice or chat: "Furina-ku C berapa?", "Abyss-ku lantai 12 berapa bintang?", "Shiyu Defense-ku rating apa?", "banner ZZZ apa sekarang?"

### Phase 28: Keuangan dari tepira (v1.24, see SPEC → Keuangan dari tepira)
Decision: Cyrene reads tepira only through its owner-only Cyrene API (tepira PR #21) with a
bearer token kept as a SecretStr, never the database or OpenClaw's key; four non-strict read
tools on both backends, and with `tepira.writes` and Claude a strict `record_expense` held by
the code's yes gate (generalized from v1.9, one question per turn) plus `undo_expense` for her
own last entry within 15 minutes. Finance only when Master asks: never in the briefing, a
notice, or a push; arguments never in the INFO log. Off by default until PR #21 is deployed.
Tasks 116 and 117 build in parallel on the contract of 116a.
- [x] Task 116a: contract: SPEC, `TepiraConfig`, the example block, `finance/values.py` (`FinanceError`, `FinanceSource`, the categories, `WIB`, the value types, the spoken amount and the question)
- [x] Task 116: `finance/tepira.py` `TepiraClient` (time budget, retry rule, the status table, shapes, logging)
- [x] Task 117: the generalized gate (`check_action`, per-request order words, `held_for_yes`, `confirmation_gate`), `FinanceTools` (six tools, cache, briefing guard, record and undo), both personas
- [x] Task 118: integration: wiring with the shared gate, `waifu tepira`, check line, private-argument logging, the strict limits, README, CHANGELOG

### Checkpoint: Keuangan dari tepira
- [x] Python suite and ruff clean; no test reached tepira; the token sentinel in no log, check line, exception, or URL
- [ ] After PR #21 is merged and the owner set the Vercel env: `waifu tepira`, `dompet`, `tujuan`, `transaksi` against tepira's dashboard
- [ ] By voice or chat: "pengeluaran bulan ini berapa?", "sisa budget makan?", "saldo GoPay berapa?", "tabungan laptop sudah berapa?", "transaksi terakhir apa?"; with writes: "catat jajan kopi 18 ribu" → "iya" → recorded, then "batalkan yang tadi"; the next morning greeting names no money

### Phase 29: Peringatan hujan (v1.25, see SPEC → peringatan hujan)
Decision: a `RainWatcher` thread built like the PC watcher reads the briefing's Open-Meteo
forecast every 15 minutes during the active hours (the hourly request gains the precipitation
probability and amount) and tells a rain starting within `lead_minutes` once per rain episode,
between utterances and never inside a conversation, pushing it too when notifications are on.
Detection is a pure function over the forecast; the told episode lives in the process. No new
dependency; `waifu rain` shows the next rain.
- [x] Task 119a: contract: SPEC, tasks, `RainAlertConfig`
- [x] Task 119: `weather.py`: the hourly rain fields, `Hour`, `Forecast`, `WeatherClient.forecast()`
- [x] Task 120: `rain_watch.py`: rain detection (`is_rainy`, `raining_now`, `rain_window`, `follow_rain`, `spoken_hour`) and `RainWatcher`
- [x] Task 121: wiring in `run`, `waifu rain`, check line, example config, README, CHANGELOG

### Checkpoint: Peringatan hujan
- [x] Python suite and ruff clean; no test reached Open-Meteo, slept for real, spoke, or pushed (2026-10-17: 7170 pytest)
- [ ] On the owner's PC with `briefing.city: Cianjur`: `waifu rain` agrees with a weather app's hourly rain; before an afternoon rain she says the sentence once (and the phone gets it), then stays quiet through the rain, during a conversation until it ends, and at night

### Phase 30: Versi baru (v1.26, see SPEC → versi baru)
Decision: `waifu run` records what it started with (the CHANGELOG's newest version, a hash of
the core dependencies, the git commit or else the newest source file's time) in
`data/running.json`, and a `VersionWatcher` thread built like the rain watcher compares the
CHANGELOG and pyproject on disk every 30 minutes during the active hours, telling a newer
version (and changed packages, with the pip command in the push) once, between utterances.
News is a CHANGELOG version, never a bare commit, so fixes and half-built versions stay quiet.
Whether a run is running comes from the run lock plus a live process id in the record, never
from cleanup on exit (closing the console skips it). She never restarts herself or runs pip.
No new dependency; `waifu version` and a `check` line show the running version.
- [x] Task 122a: contract: SPEC, tasks, `VersionNoticeConfig`
- [x] Task 122: `version.py`: releases, dependency hash, commit or source time, `read_code_version`, `newer_releases`, the running record, `process_alive`
- [x] Task 123: `version_watch.py`: `version_news`, the notice and push texts, `VersionWatcher`
- [x] Task 124: wiring in `run`, the git guard, `check` lines, `waifu version`, example config
- [x] Task 125: README, CHANGELOG

### Checkpoint: Versi baru
- [x] Python suite and ruff clean; no test ran git, wrote outside tmp_path, touched the real run lock, slept for real, spoke, or pushed (2026-10-17: 7362 pytest)
- [ ] On the owner's PC: after one restart by hand, `waifu check` says `✓ waifu run: v1.26, terbaru`; after the next CHANGELOG version lands she says the sentence once in the active hours (and the phone gets it), and `waifu check` names the old version until the restart

### Phase 31: Laporan game mingguan (v1.27, see SPEC → laporan game mingguan)
Decision: once a week (Sunday 19:00 by default) a `WeeklyReportTimer`, the briefing's
once-a-day timer moved into `prompt_timer.py` and shared, asks the brain for her own weekly
game report in a new session. The facts come from one `weekly_report` tool that gathers them
in code: this week's pulls and the pity from the stored pull history (refreshed only for games
that already have one), this season's endgame and what ends within 7 days from the same
HoYoLAB calls the game data tools make, and the unredeemed codes, each section failing on its
own inside a 30 s budget. The finance tools refuse in that turn as in the morning greeting.
No new dependency; `waifu gacha report` prints the facts.
- [x] Task 126a: contract: SPEC, tasks, `WeeklyReportConfig`
- [x] Task 126: `pulls_since` and the facts gatherer (`gacha/weekly.py`)
- [x] Task 127: `PromptTimer` refactor, `weekly_report` tool, `WEEKLY_REPORT_PROMPT`, `WeeklyReportTimer`, finance guard
- [x] Task 128: wiring in `run`/`chat`, `waifu gacha report`, check line, example config
- [x] Task 129: README, CHANGELOG

### Checkpoint: Laporan game mingguan
- [x] Python suite and ruff clean; no test reached HoYoLAB or the history API, slept for real, spoke, or pushed (2026-10-17: 7495 pytest)
- [ ] On the owner's PC: `waifu gacha report` agrees with the games' own history and endgame screens; on Sunday at 19:00 she says the round-up once (and the phone gets it), never names money, and a restart that evening stays quiet

### Phase 32: Suara cadangan offline (v1.28, see SPEC → suara cadangan offline)
Decision: when edge-tts fails (Microsoft's 403 on 2026-10-16, no internet), a run is spoken
by a speech voice installed in Windows instead of being skipped. A shipped PowerShell 5.1
script drives the WinRT speech API (System.Speech only when WinRT cannot load), reading her
text on stdin and writing the WAV to stdout, so there is no new dependency, no temp file, and
her words are never on a command line. After a failure that language uses the Windows voice
for 10 minutes before edge-tts is tried again, like the RVC pause. The fallback still goes
through RVC, with its own pitch per voice gender (a male Andika and a female Japanese voice
need different shifts), so the RVC client takes a pitch per request.
- [x] Task 130a: contract: SPEC, tasks, `tts.fallback` config
- [x] Task 130: the PowerShell boundary (`windows_voices.py` + `.ps1`) and `WindowsTts`
- [x] Task 131: the router's fallback with its pause and logging; RVC pitch per piece
- [x] Task 132: wiring, `waifu say --fallback`, `waifu voices`, check line, example config
- [x] Task 133: README, CHANGELOG

### Checkpoint: Suara cadangan offline
- [x] Python suite and ruff clean; no test started PowerShell or played audio; one real synthesis with an installed voice to a scratch WAV by hand (2026-10-17: 7614 pytest)
- [ ] On the owner's PC: after adding the Indonesian (and Japanese) voice in Settings, `waifu check` names them; with the network cable out she still answers aloud through the anime voice, and `waifu say --fallback` sounds right (tune `tts.fallback.rvc_pitch`)

### Phase 33: Jam main (v1.29, see SPEC → jam main)
Decision: a `PlayTimeWatcher` in `waifu run` lists the running processes every minute from
the documented Toolhelp snapshot (kernel32 through ctypes, executable names only: never a
handle on a game, which runs kernel anti-cheat) and follows HSR, Genshin, and ZZZ by their
executable names into sessions: a start at the first poll that sees a game, an end after two
polls without it, a break across a sleep, and a restart mid-game resumed when the game was
seen at most 5 minutes before. Finished and open sessions live in `data/playtime.json` for
90 days. A non-strict `play_time` tool answers per period in WIB (only when asked); an
optional bedtime nudge, off by default, says once a night that it is late while a game is
open. The weekly game report adds the week's play time. No new dependency; `waifu playtime`
prints the totals.
- [x] Task 134a: contract: SPEC, tasks, `PlayTimeConfig`
- [x] Task 134: the conftest guard and the process lister (`processes.py`)
- [x] Task 135: sessions, periods, and totals (pure) and `PlayTimeStore` (`play_time.py`)
- [x] Task 136: `PlayTimeWatcher` with the bedtime nudge (`play_watch.py`)
- [x] Task 137: the `play_time` tool and the weekly report's play time
- [x] Task 138: wiring in `run`/`chat`, `waifu playtime`, check line, example config
- [x] Task 139: README, CHANGELOG

### Checkpoint: Jam main
- [x] Python suite and ruff clean; no test listed the real processes, slept for real, or spoke; one real listing by hand (2026-10-17: 7830 pytest; the listing saw StarRail.exe, and later the HoYoPlay launcher alone counted nothing)
- [ ] On the owner's PC: with Genshin and ZZZ each open once, `waifu playtime` shows them (confirming `GenshinImpact.exe` and `ZenlessZoneZero.exe`; else fix `play_time.games`), the HoYoPlay launcher alone counts nothing, a restart of `waifu run` mid-game keeps one session, and with `bedtime` set she says the nudge once that night

### Phase 34: Teman fokus (v1.30, see SPEC → teman fokus)
Decision: a `FocusTimer` thread in `waifu run` keeps one pomodoro session at a time (blocks of
5-120 minutes, breaks of 1-60, up to 8 rounds) against the wall clock, in memory only: a restart
ends it. A non-strict `focus_timer` tool starts, stops, and reads it when Master asks. During a
focus block the Assistant holds what she would say by herself (announcements and her own turns)
and says it at the break, while the owner's own reminders, through a new `remind` queue, and his
own words go on as always. Focus blocks are logged in `data/focus.json` for 90 days, so she can
tell today's and this week's focus time, and `waifu focus` prints them. No push by default, no
new dependency. The Scheduler is not reused: it is minute-precise, persistent, and has no phases.
- [x] Task 140a: contract: SPEC, tasks
- [ ] Task 140: the focus log (`focus.py`)
- [ ] Task 141: `FocusTimer` (`focus_timer.py`)
- [ ] Task 142: quiet during a block (`Assistant(quiet=...)`, `remind`)
- [ ] Task 143: the `focus_timer` tool and the persona line
- [ ] Task 144: wiring in `run`, `waifu focus`, check line, config and example
- [ ] Task 145: README, CHANGELOG

### Checkpoint: Teman fokus
- [ ] Python suite and ruff clean; no test slept for real, spoke, or pushed
- [ ] On the owner's PC: "temani aku fokus 25 menit" starts a block; a reminder set for that time still speaks; a gacha or rain notice waits for the break and comes with its line; "sisa berapa menit?" and "berhenti fokusnya" work; `waifu focus` shows the block

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| CUDA DLLs for ctranslate2 not found on Windows | STT fails to load | Register `nvidia-*`/`torch/lib` DLL dirs before loading; `waifu check` reports it |
| Disk space (24 GB free) | Voice extra + models ≈ 7–8 GB | Install voice extra only after the owner agrees; models go to `models/` |
| Speaker verification on very short clips ("Cyrene!") | False rejects | Threshold is configurable; `speaker-test` shows live scores; `--no-speaker-check` fallback |
| Tuya local keys and IR learning need the owner's hardware | Can't verify in CI | Fakes in tests; manual checklist in README |
| LLM claims an action without a tool call | Wrong spoken confirmation | Persona forbids it; replies are spoken only after tool results return |
| HoYoverse changes an endpoint or the cache layout | Status or history stops working | Errors become honest spoken messages; history uses the URL the game itself cached; genshin.py pinned and upgradable |
| Unofficial HoYoLAB API (check-in) | Account risk, however small | Read-only except the check-in HoYoLAB itself offers; `gacha.checkin: false` turns it off; README says so |
| Cookie or authkey leaks into logs | Account takeover / history exposure | `SecretStr`, never logged; URL queries never logged; tests use made-up secrets and assert they never show up |
| Wrong 50/50 call (HSR Celestial Invitation pick) | Misleading reaction | `gacha.off_banner`; the tool description says it is judged from a list |
| Whisper mishears an amount ("18 ribu" as "80 ribu") | A wrong expense and wallet balance in tepira | The code asks first, naming the amount, what for, and the wallet, and only Master's next plain yes records it; at most 5 juta; tepira deduplicates repeats for 2 minutes; `undo_expense` within 15 minutes |
| Finance figures spoken or pushed when nobody asked | Money said in front of guests or sent to ntfy | Tools only when asked (descriptions, persona); the briefing turn gets an error from every finance tool in code; no finance in any notice; `tepira.enabled: false` with guests |
| The weekly report's HoYoLAB and history reads hang | Her turn, and so the microphone, waits too long | One 30 s budget for every read: a late section says its time ran out and the rest is told; the history is refreshed only for games with a stored history, never imported for the first time |
| Play time tracking touches a game's anti-cheat (mhyprot, HoYoKProtect) | An account flag or a game that refuses to start | Only the process snapshot, which names processes without opening them; never `OpenProcess`, a module or thread snapshot, or WMI on a game; written in SPEC and in `processes.py` |

## Open Questions

See `SPEC.md` → Open Questions (wake-word spellings, SmartThings AC).
