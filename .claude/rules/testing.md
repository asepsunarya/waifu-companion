---
description: Test files follow the test-driven-development skill and shared testing patterns
paths:
  - "**/*.test.*"
  - "**/*.spec.*"
  - "**/tests/**"
  - "**/__tests__/**"
---

# Writing or changing tests

- Follow the `test-driven-development` skill: write the failing test first (RED), make it pass (GREEN), then refactor.
- For a bug, use the Prove-It pattern: a test that reproduces the bug and fails before the fix.
- Test behaviour, not implementation. One concept per test. No shared mutable state between tests.
- Mock at system boundaries (network, database, clock), not between internal functions.
- Test names read like a specification: `it('rejects an empty email')`.
- Patterns, naming, and anti-patterns: `.claude/references/testing-patterns.md`.
- Never delete, skip, or weaken a failing test to get green. Fix the code or ask.
