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
- [ ] Task 2: Device registry, mock device, IR code store, IR device
- [ ] Task 3: Tuya light, plug, and IR blaster
- [ ] Task 4: Broadlink IR blaster
- [ ] Task 5: PC power device
- [ ] Task 6: Registry factory from config (incl. dry-run)

### Checkpoint: Devices
- [ ] Full suite and ruff clean; `build_registry(load_config("config.example.yaml"), dry_run=True)` lists all example devices

### Phase 3: Voice
- [ ] Task 7: Audio I/O and energy segmenter
- [ ] Task 8: Speaker verification
- [ ] Task 9: Whisper speech-to-text
- [ ] Task 10: Bilingual text-to-speech router

### Checkpoint: Voice
- [ ] Full suite and ruff clean

### Phase 4: Brain
- [ ] Task 11: Tool specs, persona prompt, session clock
- [ ] Task 12: Claude backend
- [ ] Task 13: Ollama backend and brain factory

### Checkpoint: Brain
- [ ] Full suite and ruff clean; fake-client tests show a two-device request producing two tool calls and one reply

### Phase 5: App
- [ ] Task 14: Wake gate and Assistant loop
- [ ] Task 15: CLI and `python -m waifu`
- [ ] Task 16: README setup guide and hardware checklist

### Checkpoint: Complete
- [ ] All acceptance criteria met, full suite and ruff clean
- [ ] `waifu chat --dry-run` and `waifu devices --dry-run` run against the example config
- [ ] Review with human before real-hardware testing

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| CUDA DLLs for ctranslate2 not found on Windows | STT fails to load | Register `nvidia-*`/`torch/lib` DLL dirs before loading; `waifu check` reports it |
| Disk space (24 GB free) | Voice extra + models ≈ 7–8 GB | Install voice extra only after the owner agrees; models go to `models/` |
| Speaker verification on very short clips ("Cyrene!") | False rejects | Threshold is configurable; `speaker-test` shows live scores; `--no-speaker-check` fallback |
| Tuya local keys and IR learning need the owner's hardware | Can't verify in CI | Fakes in tests; manual checklist in README |
| LLM claims an action without a tool call | Wrong spoken confirmation | Persona forbids it; replies are spoken only after tool results return |

## Open Questions

See `SPEC.md` → Open Questions (name, SmartThings AC, anime voice clip).
