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
- [x] Task 140: the focus log (`focus.py`)
- [x] Task 141: `FocusTimer` (`focus_timer.py`)
- [x] Task 142: quiet during a block (`Assistant(quiet=...)`, `remind`)
- [x] Task 143: the `focus_timer` tool and the persona line
- [x] Task 144: wiring in `run`, `waifu focus`, check line, config and example
- [x] Task 145: README, CHANGELOG

### Checkpoint: Teman fokus
- [x] Python suite and ruff clean; no test slept for real, spoke, or pushed (2026-10-17: 7977 pytest)
- [ ] On the owner's PC: "temani aku fokus 25 menit" starts a block; a reminder set for that time still speaks; a gacha or rain notice waits for the break and comes with its line; "sisa berapa menit?" and "berhenti fokusnya" work; `waifu focus` shows the block

### Phase 35: Jurnal harian (v1.31, see SPEC → jurnal harian)
Decision: a `JournalStore` keeps Master's notes by WIB date in `data/journal.json` (several short
notes a day, in order, kept until the owner forgets a day; secrets refused as for memory and the
to-do list). A strict `journal_write` (the one new strict tool: 18 of 20 with every feature on)
and a non-strict `journal_read` (a day or a WIB period, newest 40 notes at most) work only when
Master asks or answers her nightly question, and refuse in the turns she starts herself, as the
finance, play time, and focus tools do, which now also refuse in the journal question's turn.
Deleting is only in the terminal (`waifu jurnal forget <tanggal> --yes`): a misheard deletion
cannot be undone. The nightly question is off by default: `journal.ask_at` (12:00-23:59) makes a
`PromptTimer` ask once a day, in a new session, without a push; the follow-up window after her
question lets Master answer without her name, and that answer is written. No new dependency.
- [x] Task 146a: contract: SPEC, tasks
- [x] Task 146: the journal store (`journal.py`)
- [x] Task 147: the journal tools and the refusal in her own turns
- [x] Task 148: the nightly question (`JournalConfig`, `JOURNAL_PROMPT`, `JournalTimer`)
- [x] Task 149: the persona line
- [x] Task 150: wiring in `run`/`chat`, `waifu jurnal`, check line, example config
- [x] Task 151: README, CHANGELOG

### Checkpoint: Jurnal harian
- [x] Python suite and ruff clean; no test asked a real brain, spoke, or pushed; the strict-tool budget keeps 2 slots free (2026-10-17: 8176 pytest; 18 of 20 strict tools, 17 of 24 optional parameters, no union)
- [ ] On the owner's PC: "catat di jurnal: ..." writes a note `waifu jurnal show` prints; "kemarin aku nulis apa di jurnal?" is summed up; with `ask_at` a few minutes ahead she asks once, the answer within the follow-up window is written, and a restart does not ask again that day

### Phase 35.1: pertanyaan yang tertahan (v1.31.1, bug fix, see SPEC → pertanyaan yang tertahan)
Decision: every `ask_aloud` request may carry an `expires_at`; one still held when it passes is
dropped (logged at INFO), never thought up or said. Each `PromptTimer` hands its own: the greeting
`briefing.until` that day, the weekly report and the journal question the midnight that ends
their day. The journal question is not cut earlier, because an undated `journal_write` in a turn
that begins within `session_idle_minutes` of the question goes on the question's WIB day, so an
answer just after midnight is never put on the new date. No new tool, tool parameter, config key,
or dependency.
- [x] Task 152a: contract: SPEC, tasks
- [x] Task 152: requests that expire (`app.ask_aloud(expires_at=...)`)
- [x] Task 153: each timer's expiry (`PromptTimer(expires=...)`, the three timers, `run` wiring)
- [x] Task 154: a late answer goes on the question's day (`JournalTools(answer_window=...)`)
- [x] Task 155: README, CHANGELOG

### Checkpoint: pertanyaan yang tertahan
- [x] Python suite and ruff clean; no test asked a real brain, spoke, or pushed; the strict-tool budget unchanged (2026-10-17: 8207 pytest after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, no schema changed)
- [ ] On the owner's PC: with `ask_at` a few minutes before midnight and a focus block running past it, the question is not asked and the log says why; asked a minute before midnight, an answer just after it shows under that day in `waifu jurnal show`

### Phase 36: Mode tidur (v1.32, see SPEC → mode tidur)
Decision: a routine step may wait, with `after_minutes` (1-1440, a whole number like every
duration in the config). Running the routine puts a delayed step into the existing schedule as a
one-shot device entry tagged with the routine's name, at the routine's start plus the delay, so it
survives a restart, shows in `waifu schedule list` and `list_schedules`, and is cancelled by
`cancel_schedule`; it fires silently like any timed device command. The tag lets a new run cancel
the routine's own pending delayed steps first (the latest run wins) without touching what Master
scheduled himself, and is written to the file only when set, so `FILE_VERSION` stays 1. The PC
and `powers_pc` plugs are refused as before; the config also refuses a delayed step with the
schedule off, and a delayed media toggle. No new tool, tool parameter, or dependency: her spoken
confirmation comes from the step's result (`scheduled: {id, at}`) and the tool description.
- [x] Task 156a: contract: SPEC, tasks
- [x] Task 156: the step delay in the config (`RoutineStep.after_minutes`, validation)
- [x] Task 157: the routine's tag on a scheduled device action (`DeviceAction.routine`)
- [x] Task 158: a routine schedules its delayed steps (`run_routine(name, schedule, now)`, `describe_step`)
- [x] Task 159: the tool and the scheduler run delayed steps; her result names them
- [x] Task 160: `waifu routine`, `waifu schedule list`, check, example config
- [x] Task 161: README "Mode tidur", CHANGELOG

### Checkpoint: Mode tidur
- [x] Python suite and ruff clean; no test touched `data/schedule.json`, ran a real device, or slept; the strict-tool budget unchanged (2026-10-17: 8272 pytest after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, no schema changed)
- [ ] On the owner's PC: "mode tidur" turns the lamp off and the AC to 26 and she names the time the AC goes off; `waifu schedule list` shows it; "batalkan AC mati nanti" cancels it; run again with a delay of a few minutes, the AC goes off then without a word

### Phase 37: Pendapatan gacha (v1.33, see SPEC → pendapatan gacha)
Decision: HoYoLAB's monthly diaries (Genshin's Traveler's Diary, HSR's Trailblaze Monthly
Calendar, ZZZ's Inter-Knot monthly report, all three in genshin.py 1.7.31) are read only, with
the month always passed in server time and limited in code to this month and the two before
(HoYoLAB's window). An answer for another month than the one asked is refused, never told under
the wrong name; ZZZ's earlier months are sent as `YYYYMM`, assumed and checked. Pure readers turn
the models into a frozen `GameIncome` (the premium currency, its worth in pulls at 160, the other
currencies, the month before, the sources, today's and HSR's yesterday's figure). One
`HoyolabClient.income(games, month, *, this_month)` call reads every game asked, a failing game
not hiding the others. A non-strict `gacha_income {game?, month?}` in its own provider, kept 15
minutes per game and month, answers only when asked, as information for planning pulls; the
weekly report gets one more lane in the same 30 s budget, one short part a game. No new config
key, strict tool, or dependency.
- [x] Task 162a: contract: SPEC, tasks
- [x] Task 162: the income values and readers (`gacha/income.py`)
- [x] Task 163: `HoyolabClient.income`
- [x] Task 164: the `gacha_income` tool and the persona line
- [x] Task 165: wiring in `run`/`chat`, `waifu gacha income`
- [x] Task 166: the weekly report's income part
- [x] Task 167: README, CHANGELOG

### Checkpoint: Pendapatan gacha
- [x] Python suite and ruff clean; no test reached HoYoLAB or built a real genshin.Client; the strict-tool budget unchanged (2026-10-18: 8377 pytest after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, `gacha_income` non-strict)
- [ ] On the owner's PC: "bulan ini aku dapat berapa Primogem?" matches the Traveler's Diary in HoYoLAB; `waifu gacha income` prints each game; `waifu gacha income zzz --month <last month>` reads ZZZ's earlier month (checks the assumed `YYYYMM` format); the weekly report names this month's income

### Phase 38: Mode game (v1.34, see SPEC → mode game)
Decision: the play time watcher tells a `GameMode` after every good poll whether a tracked game
runs (the two-poll rule already debounces alt-tab and launcher restarts) and whether its first
poll resumed a session from before a restart. `GameMode` runs `on_start`/`on_stop` once per
transition and answers `holding()`/`diverting()`, a game counting only while its last observation
is at most three polls old. The hold reuses the v1.30 quiet: the Assistant's and the avatar's
`quiet` become `focus.in_block() or game_mode.holding()` (unchanged with `normal`), so Master's
reminders still speak and her own turns wait with their v1.31.1 expiry. Phone mode pushes
nothing new: every watcher pushes on its own, so a notice already pushed is just not spoken while
a game runs and the rest is held. The bedtime nudge goes through `remind` in game mode (it is due
only while a game is open). Announcements gain `expires_at`, set by the rain alert (its
rain's start), which also ends the v1.30 known limit, and after the fix pass by the gacha
reminders about a game's state (the next poll) and the hot-GPU notice (an hour); what a game
held comes out after "Tadi selama main ada kabar.", a repeated text once. No new tool, tool
parameter, or dependency.
- [x] Task 168a: contract: SPEC, tasks
- [x] Task 168: `GameModeConfig` and its validation
- [x] Task 169: the play time watcher's `on_poll` (playing, resumed)
- [x] Task 170: `GameMode` (transitions, routines once, staleness, holding/diverting)
- [x] Task 171: announcements that expire; the rain alert expires at its rain's start
- [x] Task 172: wiring in `run` (game mode, combined quiet, phone announcer, bedtime nudge)
- [x] Task 173: `waifu check` line, `config.example.yaml`
- [x] Task 174: README "Mode game", CHANGELOG

### Checkpoint: Mode game
- [x] Python suite and ruff clean; no test listed the real processes, ran a real device, slept, or pushed; the strict-tool budget unchanged (2026-10-18: 8463 pytest after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, no tool or schema changed)
- [ ] On the owner's PC: with `hold`, a notice queued while HSR runs is said once it has been closed for two polls; a reminder still speaks during the game; with `on_start`/`on_stop` routines the lamp dims at launch and comes back after closing, and alt-tab or restarting `waifu run` mid-game changes nothing

### Phase 38.1: perbaikan fokus dan waifu mic (v1.34.1, bug fix, see SPEC → perbaikan fokus dan waifu mic)
Decision: the expiries of the gacha reminders about a game's state and of the hot-GPU notice
move to `announce(..., game_expires_at=...)`, counted only for an announcement a game held (marked
`held_by_game` one by one by the quiet frames that see a game hold it), so a focus block alone
behaves as in v1.30 and game mode keeps v1.34's drop; the rain alert keeps `expires_at`. `waifu
mic` lists each input once (from the default input's host API, never WDM-KS or the default's
aliases), measures each through `MicStream` while Master speaks (shared and stall-checked,
nothing saved, no run lock), judges speech by the VAD's own onset and minimum-speech rules, and
prints the `"name, host API"` value sounddevice resolves to
that entry alone; `waifu check` names the mic and whether it is the Windows default. No new
config key, tool, tool parameter, or dependency.
- [x] Task 175: a focus block keeps the gacha reminders and the hot-GPU notice (`game_expires_at`)
- [x] Task 176: `voice/mic_check.py` (the inputs once, levels against the VAD's bar, the config value)
- [x] Task 177: `waifu mic`, and the check's mic line
- [x] Task 178: README, CHANGELOG, SPEC

### Checkpoint: perbaikan fokus dan waifu mic
- [x] Python suite and ruff clean; no test opened an audio stream, played audio, spoke, or pushed; the strict-tool budget unchanged (2026-10-23: 8504 pytest, 8514 after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, no tool or schema changed)
- [x] By hand on the owner's PC (2026-10-23, `waifu mic --seconds 2` beside the running `waifu run`): two inputs listed, the headset marked as the Windows default; ambient levels only, as nobody spoke. Again after the fix pass: one input listed (the headset was not connected then), the onboard jack, marked as used and the Windows default, at -52 dB RMS, "butuh -40 dB"
- [ ] On the owner's PC: `waifu mic` while Master speaks names the mic that hears him; after setting `audio.input_device` to its line and restarting `waifu run`, `waifu check` names that mic and "Cyrene" is answered; a 30-minute focus block with a resin reminder queued says it at the break

### Phase 39: Tombol cepat di HP (v1.35, see SPEC → tombol cepat di HP)
Decision: Python owns every rule. `phone/actions.py` builds the buttons afresh from the registry
and the config at every request (lights, plugs, IR, and mocks; on/off/toggle/power, brightness
presets, the learned AC temperatures among six presets; every routine), and the server's POST
takes only an id that it looks up in that list, so the phone never names a device, command, or
value. The PC and a `powers_pc` plug are never buttons (refused in the config, never listed,
refused again at run time) instead of a second confirm step with a token: simpler and safer.
`/action` is a POST route behind the existing Origin and cookie checks, limited per paired device
(`RollingLimit`, the avatar server's, made public) and one at a time per device; the actions run
one at a time under the module's own lock. A device's failure is a 200 with `ok: false`, since the
page shows only its own texts for an error status. No brain tool, tool parameter, or dependency.
- [x] Task 179a: contract: SPEC, tasks
- [x] Task 179: `phone.quick_actions` and its validation
- [x] Task 180: `QuickActions` (the buttons, `find`, `run`)
- [x] Task 181: `GET /actions` and `POST /action` (`RollingLimit` made public)
- [x] Task 182: wiring in `run`
- [x] Task 183: the page's pure helpers (`actions.js`, the problem kinds)
- [x] Task 184: the panel (HTML, CSS, `phone.js`, the service worker)
- [x] Task 185: README, CHANGELOG, `config.example.yaml`

### Checkpoint: Tombol cepat di HP
- [x] Python suite, JS tests, and ruff clean; no test ran a real device or sent a PC power command; the strict-tool budget unchanged (2026-10-23: 8612 pytest, 481 JS; 8613 pytest, 489 JS after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, no tool or schema changed)
- [x] By hand: a phone server on a free port (never 9883) with a mock registry answers `/actions` and `/action` with curl, and refuses a foreign Origin, a missing cookie, an unknown id, and the eleventh action in a minute (2026-10-23, port 9893: the lamp, AC 24, the projector's power, and "Mode tidur" with its delayed step ran; the PC's shutdown and wake and the PC plug's off were 404; an injected id 400; the eleventh 429 with `Retry-After: 60`; the token never in the log. The page paired in a browser at 360 and 412 px: three buttons a row, the panel scrolls, toasts show, Escape closes it and focuses the toggle). Again after the fix pass (2026-10-24, port 9895, mock devices, every request taken as paired by the scratch script): at 360 px the panel's top stayed at 397.6 px while a toast showed and after it hid, and the spot of Mati was still Mati; at 412 px a two-line "Mode tidur" toast left it at 474.4 px; opening focuses Nyala, Tab goes to Mati, Escape on the toggle closes it; the text box was 168 px while typing, 116 px otherwise; a second press while the first ran was a 409 without `Retry-After`, toasted "Perintah tadi masih jalan. Tunggu sebentar."
- [ ] On the owner's PC: the lamp button turns the lamp on and off from the phone with a toast; an AC preset sends its code; "Mode tidur" runs and names its scheduled step; there is no button for the PC or its plug; the panel fits at 360 px

### Phase 40: Mode tamu (v1.36, see SPEC → mode tamu)
Decision: one `GuestMode` per process (memory only) shared like `TurnImages`: the shared
dispatcher `run_tool` refuses the private tools (a fixed list in `guest.py`, checked against
each module's constant and against every tool of a full request) before any provider or the
confirmation gate runs, and the brains end their session at the next turn whenever guest
mode's generation moved (on, off, or the lazy automatic end at `max_hours` or 05:00), a
waiting yes-question forgotten. While it is on, a new session's prompt carries a static guest
section instead of the remembered facts, the briefing drops tasks and reminder texts, and the
journal question and weekly report (private requests) are dropped before their turn. The
voice, chat, and phone chat switch it with one non-strict tool, `guest_mode {on?}`; no CLI
command, since the CLI cannot reach the running process. The self-started prompts, the fixed
announcements, the quick buttons, and the terminal commands are unchanged. No new dependency;
18 of 20 strict tools and 17 of 24 optional parameters stay.
- [x] Task 186a: contract: SPEC, tasks
- [x] Task 186: `guest_mode.max_hours`, `config.example.yaml`
- [x] Task 187: `GuestMode` (on, off, renewal, automatic end, generation, the private tools)
- [x] Task 188: `run_tool`'s refusal; the brains' `tool_refusal` and `session_marker`
- [x] Task 189: `build_brain(guest_mode=...)`: the guest section instead of the memory
- [x] Task 190: `GuestTools` (`guest_mode`)
- [x] Task 191: the briefing without tasks and reminder texts in guest mode
- [x] Task 192: the Assistant drops private requests in guest mode
- [x] Task 193: wiring in `run` and `chat`, the check line, the strict budget and tool sorting tests
- [x] Task 194: the persona section in both personas
- [x] Task 195: README "Mode tamu" and Privasi, CHANGELOG

### Checkpoint: Mode tamu
- [x] Python suite and ruff clean; no test ran a real device, slept, spoke, or pushed; the strict-tool budget unchanged (2026-10-24: 8723 pytest, 489 JS; 8725 pytest, 489 JS after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, 41 tools with `guest_mode` non-strict)
- [x] By hand (2026-10-24, a scratch folder with the example config, dry-run mocks, and a scripted Ollama client): `_companion_brain` with a real `GuestMode`; "mode tamu" turned it on (until 05:00, the night's end coming first), the next turn began a new session whose prompt ended with the guest section instead of the remembered fact, `list_tasks` was refused while the lamp turned on, "mode tamu selesai" turned it off, and the turn after it began a new session with the memory section again; the log showed the switches and the refusal
- [ ] On the owner's PC: "Cyrene, mode tamu" turns it on; "pengeluaranku bulan ini berapa?" is refused briefly; the lamp still answers; "mode tamu selesai" turns it off and the next answer knows her memory again; `waifu check` shows the guest mode line

### Phase 41: Volume suaraku (v1.37, see SPEC → volume suaraku)
Decision: the gain lives in a `VolumePlayer` around the PC's `Player`, so it lands after synthesis
and RVC (whose 16-bit PCM upload would hard-clip a gain above 100), covers `run`, `chat --speak`,
and `say` in one place, leaves the avatar the clip before the gain (the mouth keeps its size), and
never touches the phone, whose replies are synthesised apart. Linear amplitude; 100 is the clip
untouched; above 100 a tanh soft limiter keeps every peak below 0.98. The level and the whisper
switch live in `data/voice.json` (re-read at every clip, so the three commands agree and the
greeting after `until` is at the normal level), and a file that cannot be read never silences
her. Whisper mode is switched by voice; the config holds only its window (`from`, an alias, and
`until`, across midnight) and its level. One non-strict tool, `voice_volume {level?, change?,
night?}`, open in guest mode; the media device's line points "suaramu" to it. No persona line, no
new dependency; 18 of 20 strict tools and 17 of 24 optional parameters stay.
- [x] Task 196a: contract: SPEC, tasks
- [x] Task 196: `apply_volume` (the gain and the soft limiter) and `VolumePlayer`
- [x] Task 197: the `voice` config (the whisper window), `config.example.yaml`
- [x] Task 198: `VoiceLevel` (the state file, the steps, the whisper window, `level_now`)
- [x] Task 199: `VoiceVolumeTools` (`voice_volume`) and the media device's line
- [x] Task 200: wiring in `run`, `chat`, and `say --volume`, the check line, the strict budget and tool sorting tests
- [x] Task 201: README "Volume suaraku", CHANGELOG

### Checkpoint: Volume suaraku
- [x] Python suite and ruff clean; no test played audio, spoke, or pushed; the strict-tool budget unchanged (2026-10-24: 8870 pytest; 8886 pytest after the fix pass; 18 of 20 strict tools, 17 of 24 optional parameters, `voice_volume` non-strict among the 41 client tools of a full chat request)
- [x] By hand (2026-10-24, a scratch folder with the example config, no audio played): `apply_volume` on a full-scale sine peaked at 0.9798 at 150, 0.5 at 50, and returned the clip itself at 100; the check line read `✓ Volume suaraku: 100%, mode bisik mati (22:00-06:00 paling keras 50%)`; at 23:00 the tool went softer (80), whisper on, louder (100, "tapi mode bisik menahannya di 50 persen sampai jam 06:00"), level 30, and a `VolumePlayer` around a recorder played peaks 1.0, 0.8, 0.5, 0.5, 0.3; `_companion_brain` with a scripted Ollama client answered "kecilkan suaramu" with `voice_volume {change: softer}`, writing `{"version":1,"level":80,"night":false}`
- [ ] On the owner's PC: "Cyrene, kecilkan suaramu" makes her softer without moving the Windows volume; "volume 30" still moves the Windows volume; `waifu say --volume 150 Halo` is loud but not crackly; "nyalakan mode bisik" whispers after 22:00 and the morning greeting is at the normal level

### Phase 42: Ulangi (v1.38, see SPEC → ulangi)
Decision: the repeat is answered in the voice loop, never by the brain: a pure gate
(`repeat.py`) accepts only a small set of whole repeat phrases once her names and a few softeners
are left out, and `handle_utterance` then says her last words on the PC again through `speak`.
What she remembers is written only on `run`'s thread, right before it is spoken, by the three
callers that say her own words (a reply, the announcements, a request's answer), never by `speak`
(reactions and the startup line use it from other threads). Since the repeat is no turn, the
confirmation gate never sees it, so a pending question stays pending and Master's next real turn
answers it. Guest mode's generation, passed as `guest_marker`, is stored with the words: once it
has moved, nothing is repeated. The phone is not intercepted (its page shows her words) and its
replies are never remembered. No tool, config, dependency, or persona change.
- [x] Task 202a: contract: SPEC, tasks
- [x] Task 202: `is_repeat_request` (the strict phrase set)
- [x] Task 203: the Assistant remembers her last words on the PC and repeats them without the brain
- [x] Task 204: guest mode clears them (`guest_marker`, wiring in `run`)
- [x] Task 205: README "Ulangi", CHANGELOG

### Checkpoint: Ulangi
- [x] Python suite and ruff clean; no test played audio, spoke, or pushed; the strict-tool budget unchanged (2026-10-24: 9025 pytest; no tool, schema, or config changed, so 18 of 20 strict tools and 17 of 24 optional parameters stay)
- [x] By hand (2026-10-24, a scratch script, nothing played): a real Assistant, WakeGate, and GuestMode with a real ClaudeBrain and ConfirmationGate over a scripted SDK-shaped client and mock devices; "Cyrene, ulangi" first said "Belum ada yang bisa kuulangi, Master."; "Cyrene, matikan komputer" asked its question (2 model requests), "hah?" said it again with no new request and no PC call, "iya" then ran the mock shutdown; a reminder said by `run` was repeated on "Cyrene, tadi bilang apa?"; after `turn_on()` of guest mode "Cyrene, ulangi" had nothing to repeat; 4 model requests in all, and the log showed "Repeating what she said last" and "Nothing to repeat" without the text
- [x] Fix pass (2026-10-24): "coba sekali lagi" and "tolong sekali lagi" go to the brain as a retry; the guest marker is read before the turn, so a reply whose turn switched guest mode is never repeated; the privacy lines say announcements were logged as `Announcing: ...` when first said; 9034 pytest and ruff clean, no tool, schema, or config changed
- [ ] On the owner's PC: "Cyrene, ulangi" right after a reply says it again at once; "tadi bilang apa?" in the follow-up window too; "ulangi lagunya" still goes to the brain; after "mode tamu", "Cyrene, ulangi" says there is nothing to repeat

### Phase 43: Belajar bahasa Jepang (v1.39, see SPEC → belajar bahasa jepang)
Decision: the words are a JSON list shipped in the package (`src/waifu/japanese_words.json`, not
under a `data` folder, which `.gitignore` ignores anywhere), read with the `__file__` pattern and
validated by a test against the voice's own language split, so every Japanese field reaches the
Japanese voice whole. The word of the day keeps no state: a fixed shuffle of the ids (SHA-256)
walked one word per day, so none repeats before all have come. Master's progress is a Leitner
store in `data/japanese.json` with the rules of `todo.py`; a word counts as learned when she
introduces it (the greeting's `word_of_the_day` or the tool's `today`), and `waifu jepang` only
reads. One non-strict tool, `japanese_lesson {action?, word_id?, correct?}`: the model judges
Master's answer and records it with `grade`, which her own turns can never call. Open in guest
mode (low-stakes, like the focus totals). Off by default, so the provider order and every
existing test stay as they are; no new dependency; 18 of 20 strict tools and 17 of 24 optional
parameters stay.
- [x] Task 206a: contract: SPEC, tasks
- [x] Task 206: the word list and `load_words`, validated
- [x] Task 207: `word_of_the_day`, `review`, `next_quiz`
- [x] Task 208: `JapaneseStore`
- [x] Task 209: `JapaneseTools` (`japanese_lesson`) and the persona section
- [x] Task 210: the briefing's `word_of_the_day` and `BRIEFING_PROMPT`
- [x] Task 211: the `japanese` config, `config.example.yaml`
- [x] Task 212: wiring in `run` and `chat`, guest mode sorting, `waifu jepang`, the check line
- [x] Task 213: README "Belajar bahasa Jepang", CHANGELOG

### Checkpoint: Belajar bahasa Jepang
- [x] Python suite and ruff clean; no test spoke or pushed; the strict-tool budget unchanged (2026-10-24: 9178 pytest; with `japanese.enabled` in the every-tool request, 18 of 20 strict tools and 17 of 24 optional parameters, `japanese_lesson` non-strict among the 42 client tools)
- [x] By hand (2026-10-24, a scratch folder with the example config and `japanese.enabled: true`, no network): `waifu jepang` printed `Kata hari ini, Sabtu 24-10-2026: ちいさい (小さい), artinya kecil` with its example and created no file, `waifu jepang progres` said `Belum ada kata yang dipelajari (0 dari 300).`; a scratch script over the same store had the briefing give `ちいさい`/`kecil` (learned), the quiz refused in `BRIEFING_PROMPT`'s turn and then ask `w214` in Master's turn, grade it right (box 2, due in 2 days), find nothing more due, and count `learned 1`; `_japanese_check` read `✓ Belajar bahasa Jepang: 1 dari 300 kata dipelajari, 0 dikuasai`, and the file held `{"version":1,"words":{"w214":{"box":2,"due":"2026-10-26","learned":"2026-10-24"}}}`; `split_by_language` sent `ちいさい` of the greeting line to the Japanese voice and the rest to the Indonesian one
- [x] Fix pass (2026-10-24): `grade` refuses a word not due yet, so a turn retried after an API error never moves a word up twice; only her morning greeting's turn gets `word_of_the_day` (a weather question at 15:00 neither hears nor learns it); the description and both personas count a common homophone's meaning as right or give a hint; w088's example writes じゅっぷん; the README checklist fits a first day; 9186 pytest and ruff clean, no strict tool, schema, or config changed (18 of 20 strict tools and 17 of 24 optional parameters stay). By hand, a scratch script through a real ClaudeBrain over a scripted SDK-shaped client and a temp store: the greeting's `daily_briefing` held `ちいさい`/`kecil`, "cuaca hari ini gimana?" got no word, the quiz asked `w214`, the grade moved it to box 2, and the repeated grade was refused with the file still `{"w214":{"box":2,"due":"2026-10-26",...}}`
- [ ] On the owner's PC: the morning greeting ends with the word of the day in kana and its meaning, spoken by the Japanese voice; "kuis bahasa Jepang dong" asks one word and waits; `waifu jepang progres` counts it

### Phase 44: Pengingat istirahat (v1.40, see SPEC → pengingat istirahat)
Decision: activity is the time since the last keyboard or mouse input (`GetLastInputInfo`, read
with `GetTickCount64` through ctypes, loaded at every call, injected, and refused in the suite by
`tests/conftest.py`), never the screen or the process list. A pure rule (`follow_activity`) counts
continuous activity on Windows' tick count: an idle gap of `idle_reset_minutes`, or a poll that
comes that long after the last one (a sleep), starts it again, and a line is due once the last
input reaches the next point of a fixed grid, so she never speaks to an empty chair. A thin
`BreakWatcher` (the rain watcher's pattern) resets the count in a focus block and outside the
active hours, and queues the line through the rain alert's announcer, or through `remind` with
`during_games` while a game holds. The line expires after `idle_reset_minutes`. Off by default; no
tool, persona line, or dependency, so 18 of 20 strict tools and 17 of 24 optional parameters
stay.
- [x] Task 214a: contract: SPEC, tasks
- [x] Task 214: `read_input_idle` and the conftest refusal of `GetLastInputInfo`
- [x] Task 215: the `breaks` config, `config.example.yaml`
- [x] Task 216: `follow_activity` (continuous activity, breaks, sleeps, the grid)
- [x] Task 217: `BreakWatcher` (focus, active hours, failures, delivery, the thread)
- [x] Task 218: wiring in `run` (game mode, `during_games`, push), the check line
- [x] Task 219: README "Pengingat istirahat", CHANGELOG

### Checkpoint: Pengingat istirahat
- [x] Python suite and ruff clean; no test read the real input, slept, spoke, or pushed; the strict-tool budget unchanged (2026-10-24: 9261 pytest; no tool, schema, persona, or brain file changed, so 18 of 20 strict tools and 17 of 24 optional parameters stay)
- [x] By hand (2026-10-24, a scratch folder with the example config and `breaks.enabled: true`, 08:00-02:00, `during_games`, `push`, notify off): `_breaks_check` read `✓ Pengingat istirahat: tiap 60 menit di depan layar, jeda 5 menit dihitung istirahat, jam 08:00-02:00, juga saat main` and the example config `! Pengingat istirahat mati`; one call of the real `read_input_idle` returned the tick count and an idle time without error; a real `BreakWatcher` on a fake reader and clocks said the line at 21:00 and "dua jam" at 22:00 (relaxed, expiring at :05, pushed with the coffee tag); a 5.5-minute pause at 22:05 ended the stretch, a focus block from 22:40 to 23:20 read nothing and nothing came in the 50 minutes after it, and after a sleep from 00:10 to 01:30 the next line came at 02:30, an hour after the wake
- [x] Fix pass (2026-10-24): the module docstring and the SPEC Decisions line now say a line expires `idle_reset_minutes` after the poll that gives it, as the code and the SPEC BreakWatcher bullet did (a pause from 56:00 to 60:55 gives the line at 61:00, out of date at 66:00), pinned by a new test; the voiceless watcher test gets the rig's fake clock; 9262 pytest and ruff clean, no behaviour, tool, schema, or config changed (18 of 20 strict tools and 17 of 24 optional parameters stay)
- [ ] On the owner's PC: after an hour at the keyboard she says the line once, a five-minute pause starts the count again, and nothing is said in a focus block

### Phase 45: Waktu sholat (v1.41, see SPEC → waktu sholat)
Decision: the five prayer times are computed offline in a pure module (`prayer_times.py`) with
the USNO low-precision sun and the Kemenag RI angles (Subuh -20°, Isya -18°, Ashar shadow 1,
Maghrib at sunset), the ihtiyath added and rounded up, terbit minus it rounded down; tests hold
it to Kemenag schedules for Jakarta within 2 minutes and to NOAA's independent noon and sunset
within a minute. The place is `prayer` coordinates, else the briefing's, else its city geocoded
once per run through the one `WeatherClient` a `PrayerPlace` wraps (a new public
`WeatherClient.location()`). One non-strict tool, `prayer_times {date?}`, refused in her own
turns and open to guests. A `PrayerWatcher` says each prayer once through `Assistant.remind`
(Master opted in, so focus blocks and games never hold it), at most 30 minutes late, with the
told prayers in a small state file under the rules of `prompt_timer.py`, so a restart never says
one twice. Off by default; no persona line or dependency, so 18 of 20 strict tools and 17 of 24
optional parameters stay.
- [x] Task 220a: contract: SPEC, tasks
- [x] Task 220: `prayer_times.py` (the sun, noon, the altitude times, the Kemenag times)
- [x] Task 221: the `prayer` config, `config.example.yaml`
- [x] Task 222: `WeatherClient.location()` and `PrayerPlace`
- [x] Task 223: `PrayerTools` (`prayer_times`)
- [x] Task 224: `PrayerWatcher` (the window, the lines, the state file, delivery, the thread)
- [x] Task 225: wiring in `run` and `chat`, guest sorting, `waifu sholat`, the check line
- [x] Task 226: README "Waktu sholat", CHANGELOG

### Checkpoint: Waktu sholat
- [x] Python suite and ruff clean; no test spoke, pushed, or reached Open-Meteo; the strict-tool budget unchanged (2026-10-24: 9405 pytest, 489 JS; `prayer_times` is non-strict in the every-tool request, so 18 of 20 strict tools and 17 of 24 optional parameters stay)
- [x] By hand (2026-10-24, a scratch folder with the example config and `prayer` at -6.9175, 107.6191, `minutes_before: 10`, no network): `waifu sholat` printed Sabtu 24-10-2026 as imsak 03:58, subuh 04:08, terbit 05:22, dzuhur 11:36, ashar 14:47, maghrib 17:45, isya 18:57 and created no file; `_prayer_check` read `✓ Waktu sholat: untuk -6.9175, 107.6191, ihtiyath 2 menit, pengingat subuh, dzuhur, ashar, maghrib, isya 10 menit sebelumnya` and the example config `! Waktu sholat mati`; a real `PrayerWatcher` polled every 30 s through the day on a fake clock said "Sayang, sepuluh menit lagi masuk waktu ..." once each at 03:58, 11:26, 14:37, 17:35, and 18:47 (neutral, pushed with the mosque tag), a restart at 12:00 inside Dzuhur's window said nothing, and the file ended `{"version":1,"date":"2026-10-24","told":["subuh","dzuhur","ashar","maghrib","isya"]}`
- [x] Fix pass (2026-10-24): `prayer` coordinates are named by `briefing.city` as the weather's are, and a place without a city gets a null `place` in the tool (its coordinates never reach the model or her voice; `waifu sholat` and `waifu check` still show them, the check as `Bandung (-6.9175, 107.6191)`); the description gives the five prayers for the schedule, imsak or terbit only when asked; the SPEC says Subuh goes from about 46.6°N and Isya from 48.6°N, pinned by a test. By hand in a scratch folder (no network): with `briefing.city: Bandung` the tool said `"place":"Bandung"` and `waifu sholat` `Waktu sholat di Bandung, Sabtu 24-10-2026 (WIB):`, without it `"place":null`; 9413 pytest and ruff clean, no JS touched; `prayer_times` stays non-strict with the same schema, so 18 of 20 strict tools and 17 of 24 optional parameters stay
- [ ] On the owner's PC: with `prayer.enabled` and his city, `waifu sholat` matches the Kemenag schedule within a minute or two, "jadwal sholat besok" reads tomorrow's times, and Dzuhur is said once at its time

### Phase 46: Tombol cepat di PC (v1.42, see SPEC → tombol cepat di PC)
Decision: `waifu tombol` serves the phone's quick buttons to the PC's own browser from a small
server of its own in the foreground (`DeskServer`, 127.0.0.1 port 0), not from `waifu run`'s
avatar server, which runs only with the avatar on: no new port or setting, and the token is made
by the process that opens the browser. The token is a path prefix (`/t/<token>/`), since
cookies are shared by every port of 127.0.0.1; with the Host allowlist, the page's own Origin,
JSON only, and no CORS header, another website in the browser can neither press nor read.
The phone's `QuickActions` (with `where="tombol cepat PC"`), its limits, and its request handling
(`JsonRequestHandler`, extracted first without a behaviour change) are reused, and the page
reuses `actions.js`; the opener is `open_in_browser`, injected in tests. No brain tool, setting,
or dependency, so 18 of 20 strict tools and 17 of 24 optional parameters stay.
- [x] Task 227a: contract: SPEC, tasks
- [x] Task 227: share the quick buttons' limits and the phone server's JSON handling (refactor)
- [x] Task 228: `QuickActions(where=...)` and `DeskServer`
- [x] Task 229: the page (`web/tombol/`: `desk.js`, `tombol.js`, HTML, CSS)
- [x] Task 230: `waifu tombol` and the check line
- [x] Task 231: README, CHANGELOG

### Checkpoint: Tombol cepat di PC
- [x] Python suite, JS tests, and ruff clean; no test opened a browser, ran a real device, or sent a PC power command; the strict-tool budget unchanged (2026-10-24: 9549 pytest, 527 JS; no tool, schema, persona, brain, or config file changed, so 18 of 20 strict tools and 17 of 24 optional parameters stay)
- [x] By hand: a `DeskServer` on a free port with a mock registry answers the page, `actions`, and `action` with curl, and refuses a foreign Host, a foreign Origin, a wrong token, the PC's buttons, and the eleventh press in a minute (2026-10-24, port 59208 from a scratch script, never 9882/9883: the page, `tombol/tombol.js`, `tombol/desk.js`, `tombol/tombol.css`, and `phone/actions.js` 200 with `no-store` on the page, `Referrer-Policy: no-referrer`, and the protecting headers, `phone/phone.js` and `index.html` 404; `/` and a wrong token 404; Host `evil.example` 403 with the token; the lamp, AC 24, and "Mode tidur" ran, its delayed step in the scratch schedule; a foreign or missing Origin 403, a form post 415, a preflight 405 with `Allow: POST` and no CORS header; the PC's shutdown and wake and the PC plug's off 404; an injected id 400; the eleventh press 429 with `Retry-After: 60`; the token nowhere in the DEBUG log, the request lines as `/t/<token>/`. In the browser pane at 1280 px light: three cards a row, a click toasted the 429 and, a minute later, the lamp's message, no CSP error and no favicon request; at 360 px dark: one column, 44 px buttons, no sideways scroll; with the server stopped, a click toasted "Tombol cepat di PC sudah ditutup. ...". `cli.main(["tombol", "--dry-run"])` on the example config in a scratch folder with a fake opener served the list (200), printed the address, and ended with "Selesai."; `check` there read `✓ Tombol cepat di PC: 7 tombol, buka dengan 'waifu tombol'`)
- [x] Fix pass (2026-10-24): `requestDesk` uses `mode: "cors"` and `referrerPolicy: "same-origin"`, so a press keeps the page's Origin in Firefox too (mode "same-origin" under the server's `no-referrer` makes the Fetch spec send `Origin: null`, a 403); `waifu tombol` says who runs a routine button's delayed steps (a `!` warning while `waifu run` is off); README command-table rows for `waifu sholat` and `waifu tombol`, "browser default", and a restart after a config change. By hand (scratch folder, no network): a `DeskServer` with the example config's mocks in the browser pane (Chromium) loaded the new `desk.js` and ran the lamp's "Nyala" (POST 200, `PC page ran quick action dev.lampu_kamar.on: ok`); `cli.main(["tombol"])` with a fake opener and a routine with `after_minutes` printed the address and `! 'waifu run' tidak sedang berjalan: ...`, with `--dry-run` the dry-run note, and created no file. 9553 pytest, 528 JS, ruff clean; no tool, schema, or config file changed, so 18 of 20 strict tools and 17 of 24 optional parameters stay
- [ ] On the owner's PC: `waifu tombol` opens the page in his browser, the lamp turns on and off from it with a toast, "Mode tidur" runs, and Ctrl+C closes it (and a press works in Firefox, if it is ever installed)

### Phase 47: Jadwal mingguan, bulanan, tahunan (v1.43, see SPEC → jadwal mingguan, bulanan, tahunan)
Decision: three more repeats in the existing schedule, `weekly`, `monthly`, and `yearly`, and no
new tool or parameter: the strict `schedule` tool's `repeat` enum gains the values, and
`list_schedules` stays without a filter (the model picks "this month" from at most 50 entries).
The next occurrence stays one pure function. A monthly or yearly entry keeps the day it repeats
on in a `day` key of its own, written only for those entries so `FILE_VERSION` stays 1, since a
clamped `at` (30 November for the 31st) loses it. The three accept a past `at` and start at
their next time, and a yearly one ignores the year. A missed monthly or yearly reminder is told
like a one-shot one, since a birthday comes back only a year later. 18 of 20 strict tools, 17 of
24 optional parameters, and 0 of 16 unions stay.
- [x] Task 232a: contract: SPEC, tasks
- [x] Task 232: `next_occurrence` for weekly, monthly, and yearly (pure)
- [x] Task 233: the anchor day: `Entry.day`, `add`, `fired`/`skipped`, and the file
- [x] Task 234: the `schedule` and `list_schedules` tools (enum, descriptions, `day`)
- [x] Task 235: `waifu schedule list` and `cancel` say how an entry repeats
- [x] Task 236: a missed monthly or yearly reminder is told
- [x] Task 237: README, CHANGELOG

### Checkpoint: Jadwal mingguan, bulanan, tahunan
- [x] Python suite and ruff clean; no test touched `data/schedule.json`, spoke, or pushed; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-24: 9671 pytest, no JS touched; only the `repeat` enum of `schedule` grew, and the every-tool Claude request counted 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions)
- [x] By hand: in a scratch folder, the tools add a yearly birthday, a monthly entry of the 31st, and a weekly one; `waifu schedule list` shows `tiap 5 Mei`, `tiap tanggal 31`, and `tiap Senin`; firing them on a fake clock walks the months' last days and 29 February (2026-10-24, a scratch folder with the example config, its mock devices, and a fake clock at 24-10-2026 14:30, deleted afterwards: the `schedule` tool answered 2026-05-05 yearly with `"at":"2027-05-05 08:00","repeat":"yearly","day":5`, 2026-10-20 monthly with 2026-11-20 and `"day":20`, 2026-10-19 weekly with 2026-10-26, and 2028-02-29 yearly with 2027-02-28 and `"day":29`; 2026-11-31 was refused as a date that does not exist and a past daily time as "sudah lewat"; `waifu schedule list` printed `tiap Senin`, `tiap tanggal 31`, `tiap tanggal 20`, `tiap 29 Februari`, and `tiap 5 Mei`, and `waifu schedule cancel j3` ended `..., tiap tanggal 20`; a real `Scheduler` firing the 31st on time said it on 31-10, 30-11, 31-12, 31-01, 28-02-2027, ... 31-01-2028, and 29-02-2028; 29 February fired on 2028-02-29, 2029-02-28 to 2031-02-28, and 2032-02-29; Ibu's birthday missed with the PC off from 4 to 7 May 2027 was told at 09:00 on the 7th as "Master, ada pengingat yang terlewat tanggal 5 jam 08.00: Master, hari ini ulang tahun Ibu!" and moved to 2028-05-05, and one missed by 40 minutes on 5 May 2028 as "... jam 08.00: ...")
- [x] Fix pass (2026-10-24): the `schedule` description says to pass a leap year's date such as 2028-02-29 for a 29 February yearly entry, never 28 February; bills get the 08:00 default and a reminder text as birthdays do, matching the README and CHANGELOG, and the SPEC says the same; a weekly entry's `schedule` and `list_schedules` results carry `weekday` (`WEEKDAYS_ID`) and the descriptions say to use it; `_missed_text`'s docstring names monthly and yearly reminders. By hand in a scratch folder (fake clock at 24-10-2026 14:30, deleted afterwards): weekly 2026-10-26 answered `"weekday":"Senin"` and 2026-10-27 `"weekday":"Selasa"`, 2028-02-29 yearly `"at":"2027-02-28 08:00",...,"day":29`, 2027-02-29 the date refusal, and `list_schedules` carried both weekdays. 9672 pytest and ruff clean, no JS touched; only descriptions and a result field changed, no parameter, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "ingatkan ulang tahun Ibu tiap 5 Mei" makes a yearly entry at 08:00, "tiap tanggal 20 ingatkan bayar listrik" a monthly one, and "jadwal bulan ini apa?" names them

### Phase 48: Cuaca beberapa hari (v1.44, see SPEC → cuaca beberapa hari)
Decision: a non-strict `weather_forecast {day?, part?}` tool beside `daily_briefing`, fed by a
request of its own (`OUTLOOK_QUERY`, seven days of daily and hourly values) so the briefing's and
the rain watcher's request stay as they are. The days and parts are pure functions of the reply:
today without a part is the rest of today, a whole day carries its parts' condition and rain
chance so one call answers, and a part follows `spoken_hour`'s words and Open-Meteo's
hour-before rule. The tool exists whenever the weather has a place, shares the briefing tool's
client, names the place as the rain alert does, refuses in her own turns, and stays open in guest
mode. 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay.
- [x] Task 238a: contract: SPEC, tasks
- [x] Task 238: `WeatherClient.outlook()`: seven days with hourly temperatures
- [x] Task 239: the days and parts asked (`periods`, pure)
- [x] Task 240: the `weather_forecast` tool
- [x] Task 241: the brain gets it: a shared client, the place gate, guest mode, the strict budget
- [x] Task 242: `waifu cuaca`
- [x] Task 243: README, CHANGELOG

### Checkpoint: Cuaca beberapa hari
- [x] Python suite and ruff clean; no test reached Open-Meteo; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions); `FORECAST_QUERY`, the `daily_briefing` spec, and `waifu briefing`/`waifu rain` unchanged (2026-10-24: 9783 pytest, no JS touched; the every-tool Claude request with `briefing.city` set counted 45 tools, 18 of 20 strict, 17 of 24 optional parameters, and 0 of 16 unions; the briefing's and the rain watcher's request tests and the `waifu briefing`/`waifu rain` tests pass unchanged)
- [x] By hand: with a fake Open-Meteo, the tool answers today, tonight, tomorrow, the weekend, a date, a part, a passed part, and a date out of reach; `waifu cuaca` prints each day with its parts (2026-10-24, a scratch folder with the example config plus `briefing.city: Cianjur` and a fake Open-Meteo for Saturday 24-10-2026, deleted afterwards: at 14:05 the tool gave the rest of today as `sisa hari ini` with siang, sore, and malam, `today`+`evening` the showers of 15:00-19:00 (`hujan lokal`, 70%, 4.4 mm), `tonight` malam, `tomorrow` Minggu with its four parts (siang and sore `hujan`, 85%), `weekend`+`afternoon` Sabtu and Minggu, 2026-10-28 Rabu, 2026-10-31 "Prakiraan cuaca hanya ada untuk hari ini sampai Jumat 30-10-2026.", `today`+`morning` "Pagi ini sudah lewat.", `lusa` the argument refusal, and the greeting turn the refusal; the nine questions made eight forecast requests (the refused argument asked none) and one city lookup; `waifu cuaca`, `cuaca besok`, `cuaca akhir-pekan` at 20:15 (Sabtu with only malam, then Minggu), and `cuaca 2026-10-30` (no chance or amount on the last day) exited 0, `cuaca 2026-11-01` printed the ✗ line and exited 1)
- [x] Fix pass (2026-10-24): the `weather_forecast` description sends the weather now and today as a whole to `daily_briefing` "when you have it" and, without it (`briefing.enabled: false`, where the tool is still given), to this tool with `day` today, so the model is never pointed at a tool it lacks; the SPEC, README, its checklist, and the CHANGELOG say the same; the checklist's `waifu cuaca akhir-pekan` says a Saturday shows the rest of today and Sunday and a Sunday only the rest of today. By hand, a scratch script (deleted afterwards) built the tool twice with a fetch that refuses the network: the Claude tool's description held "use daily_briefing when you have it, and never both for one question; without daily_briefing, use this tool with day today for those too", `strict` False, and both specs equal. 9784 pytest and ruff clean, no JS touched; only a description changed, no parameter, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [x] Polish (2026-10-24): `waifu cuaca` and `waifu briefing` print one temperature (`20°C`) instead of `20-20°C` when the lowest and highest read the same (late in the day, one hourly entry left); the `weather_forecast` description says equal `min_c` and `max_c` are one temperature, said once; the result's keys are unchanged, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "besok cuaca gimana?", "nanti sore hujan nggak?", and "akhir pekan hujan nggak?" get short answers for Cianjur

### Phase 49: Rekap hari ini (v1.45, see SPEC → rekap hari ini)
Decision: a non-strict `daily_recap {date?}` tool whose facts are gathered in code (`recap.py`)
from the local stores only: the tasks done and still due, the focus time, the play time, the
day's pulls in the stored pull history (no refresh, no network), the Japanese words learned and
reviewed, the journal's note count (never its text), and the reminders and routines scheduled
that day (the scheduler records no fired times). Each part follows its own feature's switch and
fails on its own; no money. Today, yesterday, or a date up to 30 days back. Private in guest
mode, refused in her own turns, and printed by `waifu rekap`. 18 of 20 strict tools, 17 of 24
optional parameters, and 0 of 16 unions stay.
- [x] Task 244a: contract: SPEC, tasks
- [x] Task 244: the day's pulls (`pulls_since(until=)`) and the day's schedule (`occurrence_on`)
- [x] Task 245: the gatherer (`recap.py`): the parts, per-part isolation, the JSON
- [x] Task 246: the `daily_recap` tool, her own turns
- [x] Task 247: the brain gets it: the sources, guest mode, the strict budget
- [x] Task 248: `waifu rekap`
- [x] Task 249: README, CHANGELOG

### Checkpoint: Rekap hari ini
- [x] Python suite and ruff clean; no test read the repository's `data/` or reached the network; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions); the weekly report's pulls unchanged (2026-10-24: 9883 pytest and 528 JS tests, no JS touched; the every-tool Claude request, counted by a scratch wrapper around its own test, held 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions, with `daily_recap` non-strict; the weekly report and `pulls_since` tests pass unchanged)
- [x] By hand: in a scratch folder with filled stores and a fake clock, the tool answers today, yesterday, a date, a date out of range, and a broken store; `waifu rekap` prints each part (2026-10-24, a scratch folder with the example config plus gacha and Japanese on, a fake WIB clock at Saturday 24-10-2026 21:30, deleted afterwards: `_daily_recap` gave all seven parts and a non-strict spec; today held two tasks done and one due, 50 minutes of focus, HSR 1 h and ZZZ 45 min with a 1 h 15 min total, 74 HSR pulls with Acheron won at 74, one word learned and one reviewed, 2 journal notes (their text absent from the result), and 22:00 telepon Ibu and 23:00 rutinitas tidur not passed; yesterday held its done task, its hour of HSR, and the routine passed; 2026-10-20 the routine only; 2026-09-01 "Rekap hanya sampai 30 hari ke belakang, paling jauh Kamis 24-09-2026.", 2026-10-25 the not-yet message, `besok` the argument refusal, the greeting turn the refusal; a folder in place of `focus.json` left out the focus with `problems` and the rest told; `waifu rekap` and `waifu rekap kemarin` printed each part and exited 0, and no file was created)
- [x] Fix pass (2026-10-24): a repeating entry set to start later ("tiap hari jam 8 mulai Senin") is not due before its start while its `at` is later than its first time after now (`occurrence_on(..., now=)`; the window from its last time before the start, and the days before the start after its first fire, are SPEC known limits, since the schedule file keeps no start); play is told only when a game was open a whole minute, so a 1-minute total never stands beside no game; a day the to-do list no longer keeps whole has its done tasks unknown (`done_known: false`, `yang selesai tidak tersimpan lagi`), never none; the `daily_briefing` description leaves a look back to `daily_recap`; `waifu rekap` names a ZZZ top pull `S-rank`; "Privasi rekap" says the spoken answer goes to edge-tts with the `edge` engine. By hand, a scratch folder with the example config plus gacha on (deleted afterwards) at Saturday 24-10-2026 19:22: a daily "minum obat" made Thursday 09:00 to start Monday 08:00 gave `Jadwal: tidak ada` today and yesterday; two 25-second sessions of HSR and Genshin yesterday gave `Main game: tidak ada`; a ZZZ Ellen yesterday gave `Gacha: Zenless Zone Zero 1 pull, S-rank Ellen (1 pull, menang 50/50)`; `waifu rekap 2026-10-17` gave `Tugas: yang selesai tidak tersimpan lagi (todo.keep_done_days: 7), 1 masih menunggu`; all exited 0. 9896 pytest and ruff clean, no JS touched; only descriptions changed, no parameter, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "hari ini aku ngapain aja?" and "rekap kemarin dong" get a short warm recap

### Phase 50: Alarm bangun (v1.46, see SPEC → alarm bangun)
Decision: an alarm is a fourth kind of schedule action (`AlarmAction(label)`), so it keeps every
repeat, the listing, and cancelling, and the file stays at version 1. A new non-strict `alarm`
tool sets, lists, cancels, stops, and snoozes them (the strict `schedule` tool cannot take a
`kind` without an 18th optional parameter); only its description points `schedule` at it. The
scheduler starts a passive `AlarmBell` (a ring file, so a restart and `waifu tombol` see it) and
marks the entry fired; the voice loop says each due ring between utterances, louder each time,
never whispered, until a strict local phrase ("aku sudah bangun", "tunda"), a quick button on
the phone or the PC, the brain, or ten minutes stop it. A missed alarm is only pushed. 18 of 20
strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay.
- [x] Task 250a: contract: SPEC, tasks
- [x] Task 250: the alarm entry (`AlarmAction`) and every place that names an entry
- [x] Task 251: the bell (`alarm.py`): the ring file, start and merge, the rings and their levels, stop, snooze, restart
- [x] Task 252: the phrases (`alarm_request`)
- [x] Task 253: the scheduler rings, pushes, and misses alarms
- [x] Task 254: the volume: `LevelHold`, `VoiceLevel.day_level`
- [x] Task 255: the voice loop rings and hears the phrases
- [x] Task 256: the `alarm` tool and the `schedule` description
- [x] Task 257: the quick buttons on the phone and the PC
- [x] Task 258: config, the CLI wiring, the check line
- [x] Task 259: README, CHANGELOG

### Checkpoint: Alarm bangun
- [x] Python suite, ruff, and JS tests clean; no test played audio, pushed, or read the repository's `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) with `alarm` non-strict and open in guest mode (2026-10-24: 10143 pytest and 542 JS tests; the every-tool Claude request, counted by a scratch wrapper around its own test, held 47 tools, 18 of 20 strict, 17 of 24 optional parameters, and 0 of 16 unions, with `alarm` not strict)
- [x] By hand: in a scratch folder with a fake clock, an alarm set by the tool rings in the voice loop louder each time, stops by "aku sudah bangun", snoozes by "tunda", never stops on "matikan lampu", shows and runs its quick buttons, ends after its minutes, and a missed one is pushed (2026-10-24, a scratch script over the real store, bell, tool, scheduler, Assistant, `LevelHold`, and `QuickActions` with fake speech, player, and notifier, at Sunday 25-10-2026, deleted afterwards: whisper mode on held her replies at 50 percent while the 05:30 alarm "berangkat kerja" rang at 100, 120, and 140 percent (peaks 0.5, 0.6, 0.7) with the three lines in turn and one ring per minute however often the loop asked; the first ring was pushed once with `alarm_clock` and the phone page's link; the buttons listed "Alarm" first; "Cyrene, matikan lampu" went to the brain and the alarm rang on; "aku sudah bangun" answered "Oke Master, alarmnya kumatikan." without the brain and deleted the ring file; "tunda" at 05:40:03 answered "Oke, kubangunkan lagi jam 05.46." and the snoozed alarm rang at 05:46; "Matikan alarm" stopped it and a second tap said "Alarm sudah tidak berbunyi."; a 05:50 alarm rang ten times and ended by itself; a daily 06:30 alarm checked at 06:35 was pushed as "Alarm jam 06.30 terlewat." without a ring and moved to tomorrow)
- [x] Fix pass (2026-10-24): an ended ring kept from an earlier read is read afresh before its file is deleted, so `waifu tombol` or `waifu chat` never deletes a ring `waifu run` has just started; an ended ring's file that cannot be deleted is logged once and tried again at each read; a snooze whose ring file cannot be deleted cancels the alarm it just added and raises, and the voice loop says "Maaf Master, alarmnya belum bisa kumatikan/kutunda. Coba lagi sebentar." instead of nothing; the `alarm` description tells `alarm.repeat_seconds` and `alarm.max_minutes` as configured, built once per brain; the phrase gate hears "tunda/snooze N menit lagi", "tunda sebentar", "aku bangun", "dah bangun", and "aku dah bangun"; the phone page reads the buttons again after an alarm button and every 15 s while the alarm group shows, and the PC page after an alarm button, so the group goes once the alarm stops; the docs no longer promise that a stop never waits for a press its own page still runs (known limit). By hand, a scratch folder (deleted afterwards) over the real store, bells, and tool, with a ring file held open by a real second handle (WinError 32): two bells on one file kept the 06:10 alarm ringing after the 06:00 ring ended and `due_ring` gave its first ring; four reads of an ended, held file logged one INFO and one WARNING, and the file went once released; a snooze on a held file raised PermissionError with no entry left and the ring going on, and the next snooze left one entry; repeat 120 s and 20 minutes read "every 2 minutes ... or 20 minutes pass". 10165 pytest, 547 JS tests, ruff clean; only a description changed, no parameter, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "bangunkan aku jam 6" rings at six and "aku sudah bangun" stops it

### Phase 51: Diagnosa suara (v1.47, see SPEC → diagnosa suara)
Decision: the mic shows itself. `MicStream` taps a 1 s level on the audio thread (arithmetic
only, samples never kept) and raises `MicrophoneFlat` after 8 s of exact zeros; the voice loop
fills a 50-record ring (memory only) at the points where it already decides; a `MicSource`
keeps her listening through a failing mic over a preference list (the phone's pick, then
`audio.input_device`'s list, then the Windows default), restarting PortAudio only with the mic
closed and the player idle when Windows' list of inputs changes, never with a numbered device.
The paired phone gets GET /voice and POST /voice/set (refused in guest mode) and a "Suara"
sheet; an override file holds the phone's mic and tuning, never `config.yaml`. "Tadi kamu
denger apa?" is a strict local phrase like "ulangi". No tool: 18 of 20 strict tools, 17 of 24
optional parameters, and 0 of 16 unions stay.
- [x] Task 260a: contract: SPEC, tasks
- [x] Task 260: test guards: sounddevice streams, playback, and restarts; winmm's input listing
- [x] Task 261: `MicStream`'s level tap, flat run, and name; `restart_portaudio` under the player's lock
- [x] Task 262: the segmenter's floor and bar, `set_gate`, `relearn`, and too-short segments
- [x] Task 263: the records (`voice/diagnosis.py`) and the voice loop fills them
- [x] Task 264: "tadi kamu denger apa?" (`heard.py`) and her answer
- [x] Task 265: the preference list in the config and the override file (`voice_override.py`)
- [x] Task 266: `MicSource`: preferences, failover, coming back, the phone's switch, retrying
- [x] Task 267: the panel (`voice/panel.py`) and GET /voice, POST /voice/set
- [x] Task 268: the phone page's "Suara" sheet (`voice.js`)
- [x] Task 269: the CLI wiring, `_open_mic`, the check line
- [x] Task 270: README, CHANGELOG

### Checkpoint: Diagnosa suara
- [x] Python suite, ruff, and JS tests clean; no test opened a stream, played audio, restarted PortAudio, listed the real inputs, or read the repository's `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-24: 10401 pytest and 578 JS tests, ruff clean; tests/conftest.py now refuses real sounddevice streams, playback, recording, and PortAudio restarts once sounddevice is imported, and winmm's input listing; the every-tool Claude request, counted by a scratch plugin around its own test, held 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions)
- [x] By hand: in a scratch folder with fake streams and clocks, the voice loop records each outcome, a flat and a failing mic fail over and come back with one line each, the phone switches and rolls back, and "tadi kamu denger apa?" tells the last utterance (2026-10-24, a scratch script over the real Assistant, EnergySegmenter, UtteranceLog, MicSource, VoicePanel, override store, and a real PhoneServer on 127.0.0.1 with fake streams, Whisper, brain, speech, and player, deleted afterwards: one synthetic headset stream gave a no-wake "matikan lampu", an empty, a not-owner at 0.25 against 0.45 with no transcript, a too-short click, and an accepted reply with Whisper 0.4 s, brain 1.5 s, voice 0.3 s; "Cyrene, tadi kamu denger apa?" answered 'Tadi aku dengar "Cyrene, nyalakan lampu kamar", dan sudah kujawab.' without the brain; the headset going flat gave "Sekarang aku pakai mic Microphone (USB)." once (close, restart PortAudio, open); a changed input list gave "Mic Headset (Polaris-BT) tersambung lagi, aku pakai itu lagi."; GET /voice with a paired cookie gave the mic, two devices, and six records; POST /voice/set switched to the USB mic and wrote the override file, a gone headset was refused with "Tetap memakai Microphone (USB).", a tuning applied at once (threshold 0.5, gate -50 dB), a margin of 40 dB was a 400 "Margin VAD harus 3 sampai 30 dB.", reset kept the mic, and guest mode made GET /voice a 403; the Suara sheet's styles were checked at 375 px in the browser pane over a static copy: no sideways scroll, 44 px targets)
- [x] Fix pass (2026-10-24): a mic that went flat is skipped until Windows' input list changes or the phone picks it, so she never flaps between flat mics, and when only flat mics open she stays on one, kept open (`MicStream` reports a run of zeros once and reads on), with one ERROR line and one spoken `ALL_FLAT`; `MIC_BACK` only after the list changed; a more preferred mic the list gained but that did not open yet is tried again every 5 s, at most 3 times; "Ikuti urutan di config" is written to the override file even when no mic opens; PortAudio restarts while no mic opens log at DEBUG; mic lines expire after 60 s unsaid; a record carries guest mode's generation as it was heard (`UtteranceLog.stamp`), so the turn that switched guest mode is never told after it; tests/conftest.py also refuses sounddevice's real audio in the test that imports it first (an import hook through the test's monkeypatch); speaker-test applies the override, enroll and `waifu mic` point to the Suara page while it holds a threshold or a mic, and `waifu mic` falls back to the Windows default like `run`; the Suara sheet saves only changed boxes, refuses an empty save, shows a pending switch's outcome beside the picker, clears a failed read's problem, reads mic, records, then settings, explains an empty record list and a level of exact zeros, keeps the picker on the mic in effect (an unplugged pick as "(tidak tersambung)"), and gives "Suara" a 44 px hit area; the README says when "tadi kamu denger apa?" cannot help. By hand, a scratch script (deleted afterwards) over the real `MicStream`, `MicSource`, and `restart_portaudio` with a fake sounddevice and clock: two minutes of zeros on the headset, the USB mic, and the default opened 4 streams with 3 PortAudio restarts, said "Sekarang aku pakai mic Microphone (USB).", "Sekarang aku pakai mic Microphone (Onboard).", and the `ALL_FLAT` line once, logged 5 ERROR and 3 WARNING lines in all, and the first unmuted block was heard at once; a static copy of the sheet at 360x640 in the browser pane on loopback put "Pakai" at 276-320 px with its note at 324-387 px, the records from 431 px (above the fold), the settings at 817 px, no sideways scroll, and the 60x29 px "Suara" pill answered taps 7 px above and below it. 10421 pytest and 592 JS tests, ruff clean; no tool changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: the Suara page shows the headset's level moving while he speaks, and "tadi kamu denger apa?" tells what she heard

### Phase 52: Hotkey global (v1.48, see SPEC → hotkey global)
Decision: four Ctrl+Alt combos through `RegisterHotKey` on a thread of their own, which registers,
waits in `GetMessageW`, and unregisters, never a keyboard hook. The message loop never waits for
her voice: talk and stop go to the avatar's interaction handler as a double-click does (without
its switches), repeat and the pause to a worker. Ctrl+Alt+Q pauses her hearing in `run` (frames
skip the segmenter, `_speak_up` still runs), never `mic.pause()`, so reminders and alarms speak and
an alarm is heard; the pause is a small file so `waifu tombol`'s "Dengar lagi" ends it, cleared
when `run` starts, over after 2 h. No tool: 18 of 20 strict tools, 17 of 24 optional parameters,
and 0 of 16 unions stay.
- [x] Task 271a: contract: SPEC, tasks
- [x] Task 271: test guards: user32's hotkey and message-queue functions
- [x] Task 272: the hotkey thread (`hotkeys.py`) over a fake user32, and its status file
- [x] Task 273: the `hotkeys` config section
- [x] Task 274: the listen pause (`listen_pause.py`)
- [x] Task 275: the voice loop: the pause gates `run`, `repeat_now`, the avatar's `hearing`
- [x] Task 276: the actions (`hotkey_actions.py`) and `InteractionHandler.press`
- [x] Task 277: the quick button "Dengar lagi" on the phone and the PC page
- [x] Task 278: the CLI wiring and the check line
- [x] Task 279: README, CHANGELOG

### Checkpoint: Hotkey global
- [x] Python suite, ruff, and JS tests clean; no test registered a real hotkey or waited on a real message queue; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-25: 10596 pytest and 601 JS tests, ruff clean; tests/conftest.py now refuses user32's RegisterHotKey, UnregisterHotKey, GetMessageW, PeekMessageW, and PostThreadMessageW, and test_cli.py starts a fake hotkey thread for every `waifu run`; nothing under src/waifu/brain changed, so no tool schema moved)
- [x] By hand: in a scratch folder with a fake user32 and fake speech, the four hotkeys reach her through the real thread, the pause gates the voice loop while a reminder and a ring are still said, and "Dengar lagi" ends it (2026-10-25, a scratch script, deleted afterwards, over the real HotkeyThread, HotkeyActions, InteractionHandler, Assistant.run, ListenPause, AlarmBell, AvatarBus, and QuickActions, with a queue for user32's messages and fake speech, player, Whisper, brain, mic, and segmenter: Ctrl+Alt+C said "はい, Master?" and "nyalakan lampu kamar" without her name was answered; Ctrl+Alt+X cut a 5 s answer within the same millisecond and she listened next; Ctrl+Alt+R said the cut answer again; Ctrl+Alt+Q said "Oke Master, aku tidak mendengarkan dulu sampai jam 02.41. ...", and for 0.7 s of frames with words in them the segmenter was fed 0 frames and Whisper ran 0 times, while "Master, waktunya minum obat!" was still said; an alarm started during the pause rang, "aku sudah bangun" was heard and stopped it, and the pause held again after it (0 frames fed); a second ListenPause, as `waifu tombol` holds, listed "Mikrofon" and "Dengar lagi" answered "Mendengarkan lagi.", the voice loop logged "Listening again" 0.65 s later and heard the next words; a pause whose two hours passed logged its end once and said "Aku mendengarkan lagi sekarang, Master."; the avatar bus got hearing off, on, off, on. The real avatar page served on 127.0.0.1 in the browser pane in mascot mode showed "Tidak mendengarkan" at 6,6 px, 118x20 px, fully opaque, over the page's "model could not load" message)
- [x] Fix pass (2026-10-25): every `hello` hides the avatar's "Tidak mendengarkan" badge, so a browser tab that outlives a `run` restarted during a pause stops showing it; the hotkey thread calls `on_lost()` once it unregistered combos it let go without being stopped, and `run` then records none held (a locked record, so a loss never restores stale combos or a removed file); `waifu check` lists the combos the run holds, warns when it holds none, and asks for a restart after config.yaml changed; a test pins that a stranger is refused before Whisper after `listen_now` (a skip of the owner check inside the follow-up window fails only that test); the docs say which apps lose the combos, what helps against a game that swallows them, and that Ctrl+Alt+Q while she speaks is confirmed after her speech; the SPEC names `Message(message, wparam)` and `hearingBadgeShown`. By hand, a scratch script (deleted afterwards) over the real `HotkeyThread`, `_global_hotkeys`, and `_hotkeys_check` with a fake user32 and the run lock held: a GetMessageW failure unregistered 1-4, rewrote the record to `"hotkeys": []`, and turned the line from `✓ ... aktif di 'waifu run'` into `! Hotkey global tidak aktif di 'waifu run' (sebabnya ada di log); jalankan ulang 'waifu run'`; a queue that could not be made gave the same warning; with talk: F9 in the config while the run held Ctrl+Alt+C, check listed Ctrl+Alt+C and warned that the F9 config is used after a restart; no file was left after the block. The real avatar page served on 127.0.0.1 in the browser pane showed the badge after `hearing` off, and after the server and bus were replaced on the same port it reconnected without a reload and hid the badge. 10607 pytest and 601 JS tests, ruff clean; nothing under src/waifu/brain changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: Ctrl+Alt+C and Ctrl+Alt+Q work while a fullscreen game has the focus

### Phase 53: Kamus Cyrene (v1.49, see SPEC → kamus cyrene)
Decision: her ears get faster-whisper's `hotwords`, a capped list built from the config in order of
importance, with the names of the rosters the brain read in this run last (never fetched for it);
the initial prompt stays short. Her mouth gets a pronunciation dictionary applied only to the
Indonesian runs inside the speech router, so Edge's Indonesian voice and its Windows fallback read
"HSR" as "Ha Es Er" while the Japanese voice, the phone's text, the history, and the logs keep the
words as written. Master's own rules live in `data/tts_lexicon.json`, written by `waifu lafal` and
by a strict voice phrase, "cara baca X itu Y", answered without the brain. No tool: 18 of 20
strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay.
- [x] Task 280a: contract: SPEC, tasks
- [x] Task 280: the hotword builder and `Vocabulary` (`voice/hotwords.py`)
- [x] Task 281: Whisper passes the hotwords on both decodes
- [x] Task 282: the roster hook in `GameDataTools`
- [x] Task 283: the dictionary: rules, `pronounce`, and the store (`lexicon.py`)
- [x] Task 284: the speech router rewrites the Indonesian runs
- [x] Task 285: teaching by voice: the "cara baca X itu Y" gate and `_teach_reading`
- [x] Task 286: the CLI: `waifu lafal`, `tts.lexicon_path`, and the wiring of `run`, `chat`, and `say`
- [x] Task 287: README, CHANGELOG

### Checkpoint: Kamus Cyrene
- [x] Python suite, ruff, and JS tests clean; no test loaded Whisper, played audio, or read the repository's `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-25: 10751 pytest and 601 JS tests, ruff clean; test_cli.py now refuses any `Lexicon` outside tmp_path, as for the other stores, and test_voice_tts.py builds its routers without a rules file; nothing under src/waifu/brain changed and a test pins that `on_roster` leaves the game data tools' specs as they were, so no tool schema moved)
- [x] By hand: in a scratch folder with a fake Whisper model and fake voices, the hotwords reach both decodes and grow after a roster read, "HSR" is read "Ha Es Er" by the Indonesian voice only, and "cara baca X itu Y" writes the dictionary (2026-10-25, a scratch script, deleted afterwards, over the real Vocabulary, WhisperStt, GameDataTools, Lexicon, SpeechRouter, and Assistant with a fake Whisper model that detected English first, fake voices, a fake brain, and a fake HSR source: both decodes of every utterance got the same hotwords and never a prefix; "Cyrene, cara baca Acheron itu A Ke Ron." answered "Oke Master. Mulai sekarang kubaca: A Ke Ron." without the brain and wrote `{"version":1,"rules":{"Acheron":"A Ke Ron"}}`; "cara baca HSR itu gimana?" went to the brain; a `game_characters` read added "Acheron, Kafka, Pela" at the end of the hotwords ("Whisper hotwords now hold 15 terms"), used by the next decodes; the reply "Pity HSR-mu 70, Acheron siap. はい、Master!" reached the Indonesian voice as "Pity Ha Es Er-mu 70, A Ke Ron siap." and "Master!", the Japanese voice only as "はい、"; faster-whisper 1.2.1's own `get_prompt` put the hotwords in the prompt without a prefix and left them out with one; the Whisper tokenizer file alone (no model loaded) counted a realistic 400-character list of names at 147 tokens)
- [x] Fix pass (2026-10-25): "cara baca X itu Y" refuses remarks and pronouns ("cara baca HSR itu salah", "cara baca kamu itu salah", "... itu dari kanan ke kiri", "... itu susah banget") and a term that is her name, a wake word, or the owner call, so they go to the brain instead of becoming rules; "ga", "gak", "kah", "kan", "mana" make a question only as the reading's last word, so "Gallagher itu Ga Le Ger" is kept and "itu Ha Es Er kan" is not; "harusnya", "dibaca", ... after "itu", "aja", "oke", "nih" at the end, and a first "nama"/"kata"/"tulisan" of a longer term are left out; the owner call is left out before the request; the confirmation names the word ("Oke Master. Acheron mulai sekarang kubaca A Ke Ron."), synthesised before the rule is kept so the word keeps its old reading, never played when the file cannot be written, and a voice failure never loses the rule; the hotwords put the games and their rosters (5 names each, taking turns, the one read last first) before the routines and the aliases, so three rosters read with the example config's devices each keep 5 names (it was 10, 5, and 0); a broken dictionary is reported without its words, and the README, the SPEC, and the docstring say the "Heard" line logs the request in full; the README lists what goes to the brain and is not saved (questions, remarks, pronouns and her names, near misses, the phone). By hand, a scratch script (deleted afterwards) over the real Assistant, SpeechRouter, and Lexicon with a recording voice and owner_call "Sayang": "Cyrene, cara baca HSR itu salah." and "cara baca kamu itu salah" went to the brain; "Cara baca Gallagher itu Ga Le Ger." and "Sayang, cara baca nama Acheron itu harusnya A Ke Ron aja." were answered without the brain, the voice getting "Oke Sayang. Acheron mulai sekarang kubaca A Ke Ron." as written; "cara baca HSR itu Ha Es Er kan" went to the brain; the next reply reached the voice as "A Ke Ron sama Ga Le Ger siap, Sayang. Ha Es Er malam ini?" and the file held only Gallagher and Acheron. The example config with its commented devices, gacha on for hsr, genshin, and zzz, and three 10-name rosters gave 36 terms and 400 characters with 5 names per game. 10809 pytest and 601 JS tests, ruff clean; nothing under src/waifu/brain changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "Cyrene" and a device alias are heard more often with the TV on, and Edge says "Ha Es Er"

### Phase 53.1: konfirmasi kamus (v1.49.1, bug fix, see SPEC → konfirmasi kamus)
Decision: "cara baca X itu Y" kept ordinary sentences as rules ("cara baca jam itu begini" made
every "jam" sound "begini"). She now asks "Jadi X kubaca Y? Bilang iya kalau benar." and keeps the
rule only when Master's next accepted voice turn is a plain yes (`is_yes` with the record order
words and "bener") within 60 s; anything else, and any brain turn, drops it silently. "lupakan
cara baca X" / "hapus cara baca X" forgets a rule without the brain (a built-in cannot be
forgotten), and more remark words (begini, dengan, pakai, penting, dulu, ...) send a sentence to
the brain. No tool, config key, or dependency: 18 of 20 strict tools, 17 of 24 optional
parameters, and 0 of 16 unions stay.
- [x] Task 295a: contract: SPEC, tasks
- [x] Task 295: the remark words
- [x] Task 296: the yes before a reading is kept (`_ask_reading`, the yes, the window, brain turns)
- [x] Task 297: forgetting a reading by voice (`forget_request`, `_forget_reading`)
- [x] Task 298: README, CHANGELOG

### Checkpoint: konfirmasi kamus
- [x] Python suite and ruff clean; no test spoke, played audio, or read the repository's `data/`; the strict-tool budget unchanged (2026-10-25: 10960 pytest and 601 JS tests, ruff clean; nothing under src/waifu/brain or config/ changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay)
- [x] By hand: in a scratch folder with fake voices and a fake brain over the real Assistant and Lexicon, the four sentences keep nothing, a yes keeps the reading, a non-yes drops it, and "lupakan cara baca X" forgets it (2026-10-25, a scratch script in a mktemp folder, deleted afterwards, over the real Assistant, WakeGate, Lexicon, and SpeechRouter with a recording voice, a fake brain, and a fake Whisper: "cara baca buku itu penting", "... jam itu begini", and "... peta itu pakai kompas" went to the brain without a question, "cara baca buku itu pelan-pelan" was asked and then dropped by "nyalakan lampu kamar", and the file stayed missing; the brain's "Penuh jam 04.10." reached the voice unchanged; "cara baca Acheron itu A Ke Ron." then "iya" kept `{"Acheron": "A Ke Ron"}`; "bukan" and a yes after 61 s kept nothing and went to the brain; "lupakan cara baca acheron" answered "Oke Master. Cara baca Acheron sudah kulupakan.", "hapus cara baca HSR" was refused as built-in (voiced "Ha Es Er"), "lupakan cara baca Kafka" had none, and "lupakan cara baca itu" went to the brain; the log named no word outside the "Heard" lines)
- [x] Fix pass (2026-10-25): a bare wake word leaves her reading question waiting, so "Cyrene" → "はい、Master?" → "iya" keeps the reading instead of dropping it and sending the "iya" to the brain, where it confirmed a PC shutdown asked shortly before; a reminder or announcement she says drops the question, so a yes to the code watcher's "Mau kutukarkan?" keeps no reading; "Bener." and "iya bener banget" are a yes to her question (`is_yes` takes the affirmative words; the PC's and the expense's yes are unchanged); "hapus cara baca untuk Acheron" and "lupakan cara baca Acheron sekarang" forget Acheron instead of saying "... untuk untuk Acheron" and keeping the rule; "pelan-pelan" and other hyphenated remark or question words go to the brain without a question. By hand, a scratch script in a mktemp folder (deleted afterwards) over the real Assistant, WakeGate, and Lexicon with the test rig's fakes and its AskingBrain (a real ConfirmationGate): "cara baca buku itu pelan-pelan" went to the brain and wrote nothing; "matikan PC", the reading question, "Cyrene" 15 s later, then "iya" kept `{"Acheron": "A Ke Ron"}` with no shutdown run; the question, the code notice said 20 s later, then "Cyrene, iya" went to the brain and wrote nothing; "Bener." kept the reading; "hapus cara baca untuk Acheron sekarang" forgot it. 10994 pytest passed, ruff clean; JS untouched; `brain/confirm.py`'s `is_yes` gained a parameter but no tool spec changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "Cyrene, cara baca Acheron itu A Ke Ron", then "iya": `waifu lafal list` shows it; "Cyrene, lupakan cara baca Acheron" removes it

### Phase 54: Sebelum reset (v1.50, see SPEC → sebelum reset)
Decision: pure planner rules over the event calendar and the status the gacha watcher reads. The
watcher read no calendar before, so it reads each game's once per game day at the evening line's
time (about 4 requests a day, under 1% of its load); events ending within 48 hours and endgame
seasons ending within 72 hours with progress below the total make one evening line per game per
day, a season told once. A game closed between 22:00 and 03:00 whose stamina fills before the
morning (`briefing.time`) gets its own once-a-night line, and the stamina line waits for the
morning. The play watcher tells the sessions it just finished through a queue; guest mode silences
the planner. No tool: 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay.
- [x] Task 288a: contract: SPEC, tasks
- [x] Task 288: `gacha.reminders.planner`
- [x] Task 289: the evening rules: ending events and seasons (`gacha/planner.py`)
- [x] Task 290: the overnight rule and the stamina hold
- [x] Task 291: the play watcher tells the sessions it just finished
- [x] Task 292: the watcher: the calendar once a game day, the queue, guest mode, the `planner` kind
- [x] Task 293: the CLI wiring
- [x] Task 294: README, CHANGELOG

### Checkpoint: Sebelum reset
- [x] Python suite, ruff, and JS tests clean; no test made a request or read the repository's `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-25: 10865 pytest and 601 JS tests, ruff clean; the calendars, HoYoLAB, the clocks, and the process listing are fakes, and every state file is under tmp_path; nothing under src/waifu/brain or in a tool spec changed, so no tool schema moved)
- [x] By hand: in a scratch folder with a fake HoYoLAB and real-shaped calendars, the evening line, the overnight line, and the held stamina line come as the SPEC says (2026-10-25, a scratch script, deleted afterwards, over the real GachaWatcher, PlayTimeWatcher with a real store, planner, and `HoyolabClient.hsr_calendar` with a fake genshin.py client answering a real-shaped `HSREventCalendar`: Saturday 20:00 said "Master, Memory of Chaos di HSR berakhir sekitar 55 jam lagi, baru dapat 30 dari 36. Event Gift of Odyssey berakhir sekitar 7 jam lagi, baru 3 dari 7." with one calendar read over two polls; HSR open 22:30 to 23:40 put one closed session in the queue, and the 23:50 poll said "Master, Trailblaze Power di HSR penuh jam 04.10, sebelum kamu bangun."; the 03:10 poll (60 minutes left) and the 04:15 poll (full) said nothing; 07:00 said "Master, Trailblaze Power di HSR sudah penuh, 300 dari 300. Jangan sampai terbuang ya."; each line was held by a game only until the next poll)
- [x] Fix pass (2026-10-25): the play watcher never tells as just closed a session ended after the poll (the clock set back) or of a game the poll still sees (a restart too late to resume); the overnight line and its hold leave a fill within `reminders.stamina_minutes` to the stamina reminder; `ClosedGames` replaces the queue and also carries `on_poll`'s word, so a game closed while another stays open, or opened again, waits until no tracked game is open, and no stamina is held while one is (the line was said to a Master still playing, or dropped unheard by game mode while its hold muted the stamina until the morning); `run` builds the brain's `GameDataTools` once and the planner reads its calendar through their 60-minute cache, so a question after the evening line costs no second read; the README names ZZZ's endgame, not its events, among what has no progress. By hand, a scratch script (deleted afterwards) over the real PlayTimeWatcher with a real store, ClosedGames, GachaWatcher, and GameDataTools with fake listings, a fake HoYoLAB, and a moved clock: HSR closed at 23:40 with Genshin open said nothing at the 23:46 gacha poll, and with Genshin closed at 00:20 the 00:23 poll said "Master, Trailblaze Power di HSR penuh jam 04.23, sebelum kamu bangun." and "Master, Original Resin di Genshin penuh jam 05.23, sebelum kamu bangun."; the clock set back 4 minutes with HSR running told no session; the 20:00 evening line and a `game_events` answer two minutes later made one calendar read. 10879 pytest passed, ruff clean (one earlier full run failed `test_the_calendar_fetch_time_defaults_to_the_aware_utc_now` in the untouched test_gacha_hoyolab.py, a wall-clock test; it passed alone three times, with the gacha and the CLI tests, and in the next full run); JS untouched; nothing under src/waifu/brain or in a tool spec changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] The owner accepts the about 4 HoYoLAB calendar reads a day (the task allowed none beyond those cached or fetched before; the evening line cannot be made without them) and up to two planner lines for one game in one game day (the evening line and the overnight line, against "one per game per day"), or turns `gacha.reminders.planner` off
- [ ] On the owner's PC: an evening line names a real event or season, and HSR's window progress matches the stars in the game

### Phase 55: Panduan setup (v1.51, see SPEC → panduan setup)
Decision: `waifu setup [mic|tuya|ir|notify|all]` asks in the terminal and writes config.yaml
itself, the first command that does. A small patcher changes only the targeted lines (comments,
order, quoting, and line endings kept; ruamel is not installed), backs the file up to `data/`
first, reloads it with `load_config` and compares the whole config with the intended one, and
restores it on any difference, handing over a snippet with every local key hidden. tinytuya's
own wizard runs in the project folder with the owner's terminal attached; Cyrene reads only the
devices.json and snapshot.json it writes, matches them to the Tuya devices in config.yaml by
name with the owner's confirmation, and offers to delete the files holding the cloud credentials.
`waifu check` points at `waifu setup` while the placeholders remain. No tool or dependency: 18 of
20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay.
- [x] Task 299a: contract: SPEC, tasks
- [x] Task 299: the line patcher (`config_patch.patch_text`, `snippet`, `masked`)
- [x] Task 300: writing config.yaml safely (`apply_changes`: backup, atomic write, reload and compare, restore)
- [x] Task 301: the wizard's files and the matching (`tuya_import.py`)
- [x] Task 302: `waifu setup mic` (and the mic helpers `waifu mic` shares)
- [x] Task 303: `waifu setup tuya` (the wizard runner, the import, the credential files, the test guard)
- [x] Task 304: `waifu setup ir` and `waifu setup notify`
- [x] Task 305: `waifu setup all` and the check hints
- [x] Task 306: README, CHANGELOG

### Checkpoint: Panduan setup
- [x] Python suite, ruff, and JS tests clean; no test ran tinytuya, reached the network, opened a mic, or read the repository's config.yaml or `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-25: 11168 pytest and 601 JS tests, ruff check and format clean; the wizard is a fake runner writing made-up devices.json and snapshot.json, the mics the fake sounddevice, the notifier a fake, and every config a temp copy of config.example.yaml or a small one; tests/conftest.py now also refuses any subprocess running tinytuya; nothing under src/waifu/brain or in a tool spec changed, so no tool schema moved)
- [x] By hand: in a scratch folder with a copy of config.example.yaml and made-up wizard files, `waifu setup tuya`, `ir`, and `notify` write the values, keep every comment, and leave a backup; a broken layout gets the snippet with the key hidden (2026-10-25, mktemp folders deleted afterwards: through `python -m waifu setup` with piped answers on the example turned CRLF with a BOM and a made-up devices.json, `setup tuya` read the list without running the wizard and wrote the lamp's four keys, `setup ir` moved both Broadlink devices to 192.168.1.23 and listed both `learn-ir --missing` commands, and `setup notify` wrote `enabled: true` and a new topic; the BOM and CRLF on all 755 lines stayed, each step left its own backup, only the targeted lines changed (diff), the summary showed the key's length only, and `waifu check` then warned only for the plug still at the placeholder; a fake wizard runner in-process wrote both devices and deleted tinytuya.json on yes; the flow-mapping and loader-failure paths are covered by tests)
- [x] Fix pass (2026-10-25): with a topic present but notify off, n to setup notify writes nothing (it turned notify on with a new random topic), a new topic is its own question, and turning it on with the kept topic shows the subscribe steps; the import reads devices.json only when the wizard wrote it in this run, so a Tuya cloud error (exit 0) imports nothing and never an older list with a re-paired lamp's old key, and the wizard's English errors are explained; Ctrl+C in the wizard stops setup; a second import of the same list writes nothing (no backup, no restart reminder); a write before the test push or the delete offer still counts when the input ends there, and a stopped tuya step says the credential files stay; setup ends with a recap and the next steps; the wizard list names kinds in Indonesian; the Tuya steps warn of the data center and the IoT Core renewal; `.gitignore` covers config.yaml's temp copy. By hand, mktemp folders deleted afterwards, `python -m waifu setup` with piped answers on a copy of config.example.yaml: notify with a topic and `enabled: false` answered n left config.yaml byte-identical with no backup, answered Enter, Enter, n wrote only `enabled: true` and showed the subscribe steps with the kept topic; `setup tuya` over a made-up devices.json wrote the lamp and the plug (lampu, colokan, IR blaster in the list), and a second run said "sudah sama dengan devices.json" twice, "Tidak ada yang diubah.", and left the file and the single backup as they were; a fake runner exiting 0 without writing printed the no-list explanation. 11185 pytest passed, ruff check and format clean; JS untouched; nothing under src/waifu/brain or in a tool spec changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: `waifu setup` fills the mic, the Tuya devices, the blaster, and notify, and `waifu check` shows no placeholder line

### Phase 56: Respons instan (v1.52, see SPEC → respons instan)
Decision: when the brain has not answered Master's own words after `nod.after_ms` (1200 ms), she
says one short Indonesian filler ("Hmm, bentar ya…") from a small bank synthesised once at start
in the background through her own voice (RVC included) and kept in memory; a filler made in a
stand-in voice is left out. A timer per turn and a begin/close handshake under the voice lock keep
it from ever overlapping the reply or starting once the reply is ready; the reply's voice is made
while the filler plays. Only the voice turn's brain branch has one: fast paths, self-started turns,
the phone, and the text chat never do. No tool or dependency: 18 of 20 strict tools, 17 of 24
optional parameters, and 0 of 16 unions stay.
- [x] Task 307a: contract: SPEC, tasks
- [x] Task 307: the stand-in flag of her voice (`SpeechRouter.degraded`)
- [x] Task 308: the filler bank (`voice/nod.py`)
- [x] Task 309: the `nod` settings
- [x] Task 310: fillers in the voice turn (`_think_with_nod`, `_say_nod`, the reply's voice made ahead)
- [x] Task 311: `waifu run` prepares the fillers in the background
- [x] Task 312: README, CHANGELOG

### Checkpoint: Respons instan
- [x] Python suite, ruff, and JS tests clean; no test spoke, played audio, opened a mic, or reached the network; the strict-tool budget unchanged (2026-10-25: 11245 pytest and 601 JS tests, ruff check and format clean; the timers are fakes the tests fire, the brains, voices, and player fakes, and the CLI tests replace the background preparation; nothing under src/waifu/brain or config/ changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay)
- [x] By hand: in a scratch folder with a slow fake brain over the real Assistant, SpeechRouter, and NodBank, a slow turn says a filler before the reply, a fast one none, and two slow turns differ (2026-10-25, a mktemp folder deleted afterwards, the real Assistant, WakeGate, SpeechRouter, NodBank, and threading.Timer with a 0.2 s fake Edge engine, a Windows fallback fake, a brain taking 1.5 s, 0.3 s, and 1.5 s, and a player that sleeps each clip: the bank kept 6 of 6; the first slow turn said "Sebentar, kucek dulu ya." 1.21 s after it began and the reply after it, never at once; the 0.3 s turn had no filler and its cancelled timer never picked; the second slow turn said "Sebentar ya, Master…"; no timer thread was left; with the engine failing, all 6 came in the fallback voice and were left out, and `pick` gave None)
- [x] Fix pass (2026-10-25): a cut filler no longer invites Master to talk (`interrupt` returns False for it, so the double-click's and Ctrl+Alt+X's listening after a cut never opens the window while the voice thread waits for the brain, which lost his words); the bank stops at the first stand-in line, and `keep_preparing` tries the missing ones again just past `tts.fallback.retry_s` until all six are kept (one Edge blip at start cost all six for the run); two near-repeat lines replaced and `pick` avoids the last two; tests for a failed brain turn's timer and for the default daemon timer; README, CHANGELOG, config.example, and SPEC say the count starts at the brain call (about 2 to 3 s after Master stops talking) and that with Claude most brain turns get one. By hand, a mktemp folder deleted afterwards: the real SpeechRouter with an Edge fake failing once and a 0.3 s pause, NodBank, and the real `_prepare_nods` thread kept 6 of 6 after one retry (7 Edge calls, the thread then ended); the real Assistant with its own threading.Timer (600 ms), the real InteractionHandler's worker, a 1.5 s brain, and a player taking 0.4 s a clip: a double-click during the filler cut it, 1.2 s in the follow-up window was still closed and the avatar thinking, and the reply then played in full. 11261 pytest and 601 JS tests passed, ruff check and format clean; nothing under src/waifu/brain or config/ changed, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: a slow question gets a filler in her RVC voice, right before the answer

### Phase 57: Pengingat saat game dibuka (v1.53, see SPEC → pengingat saat game dibuka)
Decision: "pas buka HSR nanti, ingetin aku ..." is kept in a store of its own
(`data/app_reminders.json`; the schedule file cannot carry a new trigger without older versions
setting it aside) and said when the play time watcher sees that game start: a game followed after
a poll and not before it, never at a run's first poll, across a sleep gap, or after a clock step.
Delivered through the owner's reminder path (`remind`, and a push with notify on, from a thread of
its own), once by default or every time. One non-strict tool, private in guest mode: 18 of 20
strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay. No dependency.
- [x] Task 313a: contract: SPEC, tasks
- [x] Task 313: the `app_reminders` settings
- [x] Task 314: the store (`app_reminders.py`)
- [x] Task 315: real game starts (`opened_games`, `PlayTimeWatcher(on_started=)`)
- [x] Task 316: telling the reminders at a start (`AppReminderTeller`)
- [x] Task 317: the `app_reminder` tool
- [x] Task 318: `run`, `chat` (and guest mode), `waifu pengingat-game`, and `check`
- [x] Task 319: README, CHANGELOG

### Checkpoint: Pengingat saat game dibuka
- [x] Python suite, ruff, and JS tests clean; no test listed the real processes, spoke, pushed, or read `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-25: 11409 pytest and 601 JS tests, ruff check and format clean; the watcher tests use a fake listing and a WIB clock the test moves, the teller a fake `tell`, the CLI tests the autouse fake PlayTimeWatcher and a FakeNotifier, every store under tmp_path; `app_reminder` has no `required`, so `to_anthropic` leaves it non-strict and the strict, optional, and union counts, taken over strict schemas only, do not move)
- [x] By hand: in a scratch folder, a fake process listing opening HSR after the first poll says the reminder once, and one running from the first poll or across a fake sleep says nothing (2026-10-25, a mktemp folder deleted afterwards: the real PlayTimeWatcher, PlayTimeStore, AppReminderStore, and AppReminderTeller with a fake listing and clock: HSR open at the run's first poll said nothing; closed two polls and opened again said "Master, kamu baru buka HSR, jangan lupa farming relic dan daily." once and kept only the every-time one; Genshin found after a 2 h gap, HSR after the clock set back 5 minutes, and HSR at a restarted watcher's first poll said nothing; Genshin opened later said its line and was dropped; `python -m waifu pengingat-game` on a copy of config.example.yaml listed, `hapus 2` removed g2, `hapus g9` exited 1)
- [x] Fix pass (2026-10-25): a start kept from a poll whose play time write failed is told only while that poll's tracking still follows its session (it was told after a sleep, a clock step, or the game closing, and twice for a game reopened while the writes failed); `app_reminder` list takes an optional `game` and remove one that must be the reminder's own (both refused it, and the error ended her turn); her line drops an opening "jangan lupa", "inget", or "ingat", says a text once in any case, and says things not to do after "ingat ya"; the `schedule` and `list_schedules` descriptions point a game start reminder to `app_reminder`. By hand, a mktemp folder deleted afterwards: the real PlayTimeWatcher over a PlayTimeStore whose save failed once, the real AppReminderStore, AppReminderTeller, and AppReminderTools, a fake listing and clock: HSR first seen at the failed write, then closed and an 8 h sleep, said nothing; HSR opened later said "Master, kamu baru buka HSR, jangan lupa farming relic. Ingat ya, jangan top up dulu." once (the text kept as "jangan lupa farming relic") and kept only the every-time one; list with game hsr gave HSR's two; remove g3 with game hsr was refused and kept it. 11431 pytest passed, ruff check and format clean, no JS touched; the new arguments sit in the non-strict `app_reminder` and the descriptions are static, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC: "pas buka HSR nanti, ingetin aku ..." is said when HSR next starts, and not when `waifu run` starts with HSR open

### Phase 58: Pemantau internet (v1.54, see SPEC → pemantau internet)
Decision: probes with the standard library and ctypes only (an ICMP echo through iphlpapi, the
route through GetBestRoute, DNS through DnsQuery_W past the cache, TCP connects), each on a daemon
thread within one 3 s budget, injected everywhere and refused by tests/conftest.py. The watcher in
`waifu run` polls the gateway and the anchors every minute (the HoYo hosts only while a game runs);
an outage is two failed polls with both anchors silent by echo and TCP, never across a sleep, a clock
step, or startup (a grace of two polls), kept a week in `data/network.json`, and its end said once
after 3 minutes or more. "Internetnya kenapa?" is answered by a local phrase gate without Claude,
spoken by the offline voice when edge-tts cannot be reached; Claude's connection apology names the
outage. One non-strict tool, open in guest mode: 18 of 20 strict tools, 17 of 24 optional
parameters, and 0 of 16 unions stay. No dependency.
- [x] Task 320a: contract: SPEC, tasks
- [x] Task 320: the `network` settings
- [x] Task 321: the probes (`network_probe.py`) and the conftest refusals
- [x] Task 322: classification, the state, a check when asked, and her words (`network.py`)
- [x] Task 323: the history (`network_history.py`)
- [x] Task 324: the watcher (`network_watch.py`)
- [x] Task 325: asking without Claude (`network_ask.py`, the Assistant)
- [x] Task 326: Claude's connection apology names the outage
- [x] Task 327: the `network_status` tool
- [x] Task 328: the recap and weekly report sections
- [x] Task 329: `run`, `chat`, `waifu internet`, and `check`
- [x] Task 330: the speech router speaks offline at once while the internet is down
- [x] Task 331: README, CHANGELOG

### Checkpoint: Pemantau internet
- [x] Python suite, ruff, and JS tests clean; no test pinged, resolved, connected beyond loopback, spoke, or read `data/`; the strict-tool budget unchanged (18 of 20 strict tools, 17 of 24 optional parameters, 0 of 16 unions) (2026-10-25: 11766 pytest and 601 JS tests, ruff check and format clean; tests/conftest.py refuses and records iphlpapi's echo and route functions and dnsapi's queries, the probe tests fail on any lookup or connect beyond loopback, the CLI tests use the autouse fake NetworkProbe and NetworkWatcher and keep every NetworkStore under tmp_path; measured on the full Claude chat request: 18 strict tools, 17 optional parameters, 0 unions, `network_status` having no `required`)
- [x] By hand: in a scratch folder, the real watcher with fake probes and clocks: two failed polls start an outage, the poll that answers ends it and says the back line once, a sleep gap and startup failures say nothing (2026-10-25, a mktemp folder deleted afterwards: the real NetworkWatcher, NetworkProbe with fake route, echo, connect, and lookup, NetworkStore, and NetworkState with fake clocks and announce: the run's first two failed polls counted nothing; the next two started an outage at 21:00 with the note "Internetnya lagi putus sejak jam 21.00."; the answering poll at 21:13 said "Master, internetnya sudah nyambung lagi, tadi putus sekitar 13 menit, sejak jam 21.00." once; an outage open across a 2 h gap ended at its last failed poll 21:17 without a line; a +1 h clock step and the poll after it counted nothing; the file held both outages and `back_told` 1)
- [x] Fix pass (2026-10-25): the HoYo connects resolve IPv4 only (a broken IPv6 path made the HoYo hosts look down); a check made while another probes the same target waits for that probe within its budget (a question during the watcher's HoYo poll found the HoYo hosts silent); a check when asked is slow by its own round trip and tells it (four quick polls outvoted a 450 ms reading, so "kok lemot?" got "lancar, sekitar 22 milidetik"), the watcher's poll keeps the median; she says "ping internetnya normal" and that the speed or the app may be slow, never a flat "lancar"; router, offline, DNS, and slow end with one piece of advice, also as `waifu internet`'s "Saran:" line; "WiFi-nya", "Internet-nya", and a trailing "lagi" are heard; Claude's apology hedges "Sepertinya internetnya lagi putus." before an outage is confirmed; the back line says "2 jam 5 menit". By hand, a mktemp folder deleted afterwards: the real NetworkProbe with fake route, echo, connect, and lookup, NetworkState, NetworkCheck, and NetworkAnswers: four polls at 20-23 ms, then "kok lemot?" at 450 ms said "Master, internetnya lagi lambat, ping-nya sekitar 450 milidetik. Mungkin ada yang sedang download, atau jaringannya lagi padat."; at 24 ms "Master, ping internetnya normal, sekitar 24 milidetik. Kalau masih terasa lemot, mungkin kecepatannya yang turun, atau dari aplikasinya."; "server HoYo down?" asked 50 ms into the watcher's HoYo poll with 300 ms connects told the hosts answering at 48 ms, both checks read (48, 48, 48) from 3 connects in all; with the anchors silent and no outage the note was "Sepertinya internetnya lagi putus.", once confirmed "Internetnya lagi putus sejak jam 21.03."; "WiFi-nya mati ya?", "Internetnya putus lagi?", and "kok lemot lagi" were internet questions and "mati lagi?" none; the back line for 125 minutes said "sekitar 2 jam 5 menit". 11790 pytest passed, ruff check and format clean, no JS touched; `network_status` still has no `required` and a static description, so 18 of 20 strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay
- [ ] On the owner's PC (the lead): `waifu internet` with the real probes; unplugging the network for 4 minutes gives the back line; "internetnya kenapa?" is answered while it is down

### Phase 59: Kartu teks (v1.55, see SPEC → kartu teks)
Decision: the card travels in the reply text: a static system-prompt note asks for the spoken gist
first, then a line "[[kartu: judul]]" and the body; a pure, tolerant parser (`card.py`) splits the
reply after `respond`, so only the gist reaches TTS, "ulangi", and the phone's voice note, the
history keeps the whole reply, and no tool, `BrainReply` field, or dependency is added (18 of 20
strict tools, 17 of 24 optional parameters, and 0 of 16 unions stay). The avatar shows the card in
a bottom panel of her window through a new bus event (replayed after a reload), rendered with text
nodes only; links open on a click through a loopback route that checks them against the card
shown. The phone bubble and `waifu chat` show it too; "tampilkan lagi kartunya" re-shows it
without the brain; a turn that switches guest mode shows none and hides the one shown.
- [x] Task 332a: contract: SPEC, tasks
- [x] Task 332: the parser (`card.py`)
- [x] Task 333: the system-prompt note, the web note, the self-started prompts, and the gate's question before the card
- [ ] Task 334: the voice loop: only the gist is spoken, the card shown, re-shown, and dropped on a guest switch (`card.show_seconds`)
- [ ] Task 335: the bus's card event and `POST /open-link`
- [ ] Task 336: the avatar page's card panel (`phone/card.js`, `index.html`)
- [ ] Task 337: the phone's card in her bubble
- [ ] Task 338: `waifu chat`, the speech cut, and the CLI wiring
- [ ] Task 339: README, CHANGELOG

### Checkpoint: Kartu teks
- [ ] Python suite, ruff, and JS tests clean; no test opened a browser, spoke, or read `data/`; the strict-tool budget unchanged
- [ ] On the owner's PC (the lead): a long answer shows a card beside the mascot without taking focus, a link opens in the default browser on a click, the card hides after 2 minutes, "tampilkan lagi kartunya" shows it again; the phone shows the card in her bubble

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
| Finance figures spoken or pushed when nobody asked | Money said in front of guests or sent to ntfy | Tools only when asked (descriptions, persona); the briefing turn gets an error from every finance tool in code; no finance in any notice; `tepira.enabled: false` with guests, or guest mode (v1.36) |
| The weekly report's HoYoLAB and history reads hang | Her turn, and so the microphone, waits too long | One 30 s budget for every read: a late section says its time ran out and the rest is told; the history is refreshed only for games with a stored history, never imported for the first time |
| Play time tracking touches a game's anti-cheat (mhyprot, HoYoKProtect) | An account flag or a game that refuses to start | Only the process snapshot, which names processes without opening them; never `OpenProcess`, a module or thread snapshot, or WMI on a game; written in SPEC and in `processes.py` |
| Journal entries spoken, pushed, or remembered when nobody asked | Private notes said in front of guests or sent away | Tools only when asked (descriptions, persona); the greeting and weekly report turns get an error from both tools in code; the nightly question has no push and cannot write; never in the system prompt or memory unless Master asks |
| Guest mode leaves something private reachable | Master's money, journal, tasks, or memory said in front of guests | One shared refusal in `run_tool` for a fixed list, a test that sorts every tool of a full request, a new session without the remembered facts at the next turn, the briefing reduced and the private self-started turns dropped; the switching turn and the fixed announcements are documented limits |
| A repeat request answers a confirmation question or repeats private words to guests | The PC turned off on "ulangi", or what Master heard before guests came said in front of them | The repeat never reaches the brain, so the gate never sees it and a pending question waits for Master's next real turn; only whole repeat phrases count; the remembered words carry guest mode's generation and are forgotten once it has moved |
| The break watcher misreads activity (a tick wrap after 49.7 days, a sleep, controller play) | A nudge to an empty chair, a nudge right after waking the PC, or none while playing | The idle time is the masked 32-bit difference, read input first; a poll gap longer than a break by the tick count or the wall clock starts the count again; a line needs input at its due point and expires after a break's length; gamepad input is a documented limit |
| An alarm that never wakes Master, or a device command that stops it | Master oversleeps, or "matikan lampu" ends the alarm | It rings louder each minute, never whispered, until a whole stop phrase, a button, the brain, or 10 minutes; the first ring and a missed alarm are pushed; while it rings only whole phrases stop it, tested against device commands; the tool, the README, and `waifu check` say it rings only while `waifu run` runs on an awake PC |
| A mic switch or a PortAudio restart while audio runs | A crash inside PortAudio, a cut clip, or a deaf Cyrene | Every open, close, and restart on `run`'s thread between frames, a restart only with the mic closed, the player's lock and the listing's lock held, and never with a numbered device; a mic that does not open is rolled back; with none, silent frames keep rings and reminders going while she retries every 5 s |
| A global hotkey that blocks or leaks keys, or a pause that leaves her deaf | Ctrl+Alt+X never reaches her while she speaks, Master's typing is seen, or she seems broken | `RegisterHotKey` sees only its combos; the message loop calls only `interrupt` and queues the rest; the pause is said aloud, shown on the avatar and in `waifu check`, and ends by Ctrl+Alt+Q, a double-click, Ctrl+Alt+C, the "Dengar lagi" button, a restart, or after 2 h; tests refuse the real user32 functions |
| `waifu setup` writes config.yaml wrongly or shows a secret | A config that no longer loads, another value changed, a lost comment, or a local key on screen | Only the targeted lines change, layouts it cannot read are refused, a backup goes to `data/` first, the file is reloaded and the whole config compared with the intended one, and any difference restores it and hands over a snippet with every local key hidden; the wizard's credential files are offered for deletion |
| A filler overlaps her reply, comes after it, or sounds like another voice | Two voices at once, a filler after the answer, or a robotic "Hmm" before an anime-voiced reply | One voice lock for both, a per-turn begin/close handshake so no filler starts once the reply is ready, the filler picked inside the lock, and a filler made in a stand-in voice (Windows fallback, plain voice) left out of the bank |
| A game reminder said at the wrong moment or lost | A reminder at login or after waking the PC for a game already open, twice for one start, or never | Starts come from the watcher's own tracking across one poll, never at a run's first poll or across a sleep gap or clock step; told once after the poll worked, kept through a failed write; a once reminder whose removal fails is still said; a separate file, so the schedule of older versions is never set aside |
| The internet watcher cries wolf or hangs | A "nyambung lagi" after every sleep, a router blamed for dropped pings, a voice thread stuck on a lookup, or threads piling up | Two failed polls with both anchors silent by echo and TCP; a grace of two polls after a break (sleep, clock step, startup); the gateway blamed only once it answered; every probe on a daemon thread within a 3 s budget, at most one per target; tests refuse the real ICMP, route, and DNS calls |
| A text card leaks into her voice, runs as HTML, or opens links by itself | The card body or the marker read aloud, model output running script on the avatar page (which holds the interaction token), or a page opened without a click | Only the text before the marker reaches TTS, also cut again in `clean_for_speech`; the gate's question placed before the card; the card built from text nodes only, with a test against `innerHTML`; links checked as http(s) and opened only on a click through a route that checks the token, the origin, and the card shown |

## Open Questions

See `SPEC.md` → Open Questions (wake-word spellings, SmartThings AC).
