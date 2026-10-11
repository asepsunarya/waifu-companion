# Project: waifu-companion

<!--
  Template dari claude-code-starter (berbasis addyosmani/agent-skills).
  Jaga file ini tetap pendek (di bawah ~150 baris).
-->

Rules file for AI coding agents working in this repository. It is loaded into
every session, so keep it short and current. Long workflows live in
`.claude/skills/`; shared checklists live in `.claude/references/`.
If `.claude/skills/` does not exist in this repository, the kit is installed
at user level: read every `.claude/...` path in this file and in
`.claude/rules/` as `~/.claude/...`.

A voice-controlled anime-style AI companion that runs on the owner's Windows PC
(RTX 5060 Ti 16 GB) and controls lights, AC, projector, TV, and the PC itself.
Read `SPEC.md` before changing behaviour; tasks live in `tasks/`.

## Tech Stack

- Python 3.14 (3.11+ supported), src layout, package `waifu`, Windows 11 first
- LLM: `anthropic` 1.x (Claude, model `claude-opus-5-5`) or `ollama` (local `qwen3:8b`)
- Voice: `faster-whisper` (STT), `speechbrain` ECAPA (speaker verification), `edge-tts` / GPT-SoVITS HTTP (TTS), `sounddevice` + `soundfile`
- Devices: `tinytuya` (Tuya Wi-Fi lights, plugs, IR), `broadlink` (IR blaster), `wakeonlan`
- Config: `pydantic` v2 models over `config.yaml`
- Tooling: `pytest`, `ruff` (lint + format)

## Commands

All commands run from the repository root with the project venv.

| Task            | Command                                                     |
|-----------------|-------------------------------------------------------------|
| Install (core)  | `.venv\Scripts\python -m pip install -e ".[dev]"`           |
| Install (voice) | see README: torch from the cu128 index, then `-e ".[voice]"` |
| Run             | `.venv\Scripts\waifu run` (`chat` for text mode, `--dry-run` for fake devices) |
| Test (all)      | `.venv\Scripts\python -m pytest`                            |
| Test (one file) | `.venv\Scripts\python -m pytest tests/test_config.py`       |
| Lint / format   | `.venv\Scripts\python -m ruff check src tests` / `... ruff format src tests` |
| Type check      | none configured; keep full type hints on public functions   |

Run the relevant checks before claiming a task is done. "Seems right" is not evidence.

## Project Structure

```
src/waifu/
  config.py      → pydantic config models, load_config()
  cli.py, app.py → entry points and the listen → think → speak loop
  gate.py        → wake word and follow-up window logic
  devices/       → Device contract (base.py), registry, tuya, broadlink, pc, mock
  brain/         → Brain contract (base.py), tools, persona, claude, ollama backends
  voice/         → audio I/O, segmenter, speaker verification, STT, TTS
config/          → persona.md (committed); config.yaml lives at the root (gitignored)
data/, models/   → voiceprint, learned IR codes, downloaded models (gitignored)
tests/           → pytest, one test_<module>.py per module
tasks/           → tasks/plan.md and tasks/todo.md written by /plan
```

## Code Conventions

- Contracts live in `devices/base.py`, `brain/base.py`, `voice/base.py`; implement them, do not widen them silently.
- Heavy or hardware libraries (`torch`, `speechbrain`, `faster_whisper`, `sounddevice` streams, vendor SDK connections) are imported lazily inside the class that needs them, so the test suite and `chat --dry-run` run without a GPU, mic, or network.
- Every collaborator is injected through the constructor (client, clock, model loader); tests pass fakes. Mock at the vendor-library boundary, never between our own modules.
- Device failures return `DeviceResult(ok=False, ...)` via the registry; the brain turns them into honest spoken replies. Never raise out of `Brain.respond`.
- User-facing strings (spoken replies, CLI output) are Indonesian; code, comments, and logs are English.
- Use `logging.getLogger(__name__)`; no `print` outside `cli.py`.
- Code reveals intent through names and structure. No dead code, no debug output, no commented-out blocks.

## Boundaries

**Always**
- Read the file (and its tests) before editing it; copy an existing pattern instead of inventing one.
- Verify a third-party API against the installed package source in `.venv/Lib/site-packages` before calling it.
- Run tests and ruff before committing.
- Keep changes scoped to the task. Touch only what you were asked to touch.

**Ask first**
- New dependencies (state the size and the reason); anything that downloads multi-GB models
- Changing a contract in a `base.py` file
- Anything that powers off, restarts, or sleeps the real PC outside an explicit user command

**Never**
- Commit secrets, `config.yaml`, `.env*`, `data/` (voiceprint is biometric), or `models/`
- Silence failing tests, lower thresholds, or add suppressions to get green
- Force-push to shared branches
- Invent requirements the spec does not cover: stop and ask

## How to Work

These behaviours apply at all times (see the `using-agent-skills` skill):

1. **Surface assumptions** before non-trivial work: list them and ask for correction.
2. **Manage confusion.** When the spec and the code disagree, stop and ask. Do not guess.
3. **Push back** when an approach has a concrete downside; propose an alternative, then accept the decision.
4. **Prefer the boring solution.** If 100 lines would do, do not write 1000.
5. **Scope discipline.** No drive-by refactors; never remove things you do not understand.
6. **Verify, do not assume.** Every task ends with evidence: test output, build result, runtime check.

For multi-step work, emit a short plan first and proceed unless redirected.

## Workflow

Skills in `.claude/skills/` activate automatically from their descriptions.
The slash commands are the entry points:

| Phase       | Command                      | Skill(s) and artifacts                                              |
|-------------|------------------------------|---------------------------------------------------------------------|
| Define      | `/spec`                      | spec-driven-development → `SPEC.md`                                 |
| Quality bar | `/constraints`               | constraint-driven-development → `CONSTRAINTS.md`                    |
| Plan        | `/plan`                      | planning-and-task-breakdown → `tasks/plan.md`, `tasks/todo.md`      |
| Build       | `/build` or `/build auto`    | incremental-implementation + test-driven-development                |
| Verify      | `/test`                      | test-driven-development (Prove-It pattern for bugs)                 |
| Review      | `/review`, `/code-simplify`  | code-review-and-quality, code-simplification                        |
| Ship        | `/ship`                      | shipping-and-launch (fans out to code-reviewer, security-auditor, test-engineer) |

Typical paths:
- New feature: `/spec` → `/plan` → `/build` (repeat per task) → `/review` → `/ship`
- Bug fix: debugging-and-error-recovery → `/test` (failing test first) → `/review`
- Unclear request: interview-me or idea-refine before `/spec`

Specialist subagents live in `.claude/agents/`. They never call each other;
the user or a slash command does the orchestration.

If `CONSTRAINTS.md` exists, read it before writing code and never weaken it to make a change pass.

## Definition of Done

A task is done only when its acceptance criteria are met **and** the standing
bar in `.claude/references/definition-of-done.md` holds: verified at runtime,
covered by a test that fails without the change and passes with it, no
regressions, lint clean, scoped to the task, and documented where
public behaviour changes.

## Git

- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`. The body explains *why*.
- One logical change per commit, ~100 lines target; split anything over ~1000.
- Keep refactors separate from behaviour changes.
- Commit only when the user asks, or when a command defines the commit step (`/build` commits once per task; `/ship` never commits).
- Branch names: `feature/<desc>`, `fix/<desc>`, `chore/<desc>`.

## Patterns

Inject the vendor boundary, keep the logic pure, and test with a fake:

```python
class PcDevice(Device):
    kind = "pc"

    def __init__(self, device_id, name, conn: PcConnection, *, run=subprocess.run, send_wol=send_magic_packet):
        super().__init__(device_id, name)
        self._conn, self._run, self._send_wol = conn, run, send_wol

def test_shutdown_schedules_a_delayed_windows_shutdown():
    calls = []
    pc = PcDevice("komputer", "Komputer", PcConnection(shutdown_delay_s=30), run=lambda cmd, **kw: calls.append(cmd))
    assert pc.execute("shutdown").ok
    assert calls == [["shutdown", "/s", "/t", "30"]]
```
