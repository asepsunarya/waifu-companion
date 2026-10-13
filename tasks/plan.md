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

## Open Questions

See `SPEC.md` → Open Questions (wake-word spellings, SmartThings AC).
