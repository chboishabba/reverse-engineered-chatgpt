# API-First Transport Hardening Plan

**Goal:** Finish PR #41 without adding features: preserve exactly-once semantics, make async wrappers event-loop safe, tighten snapshot/redaction/DOM correctness, then require a fresh full suite plus one paced canonical async-write receipt before merge.

**Authority:** `docs/superpowers/specs/2026-10-17-api-first-transport-design.md`

## Hardening tranche

- [x] Classify all failures after async conversation POST dispatch as ambiguous, including 5xx and cleanup/context-manager failures.
- [x] Keep explicit post rejection limited to 4xx responses.
- [x] Add a shared running-event-loop-aware sync bridge for async graph reads and async mutation wrappers.
- [x] Make ordinary mutation/session recording observation-only; snapshot persistence is explicit opt-in.
- [x] Create persisted snapshot secrets through exclusive `0600` temp files before atomic replace.
- [x] Remove raw diagnostic-field merge-back after browser redaction.
- [x] Match complete attachment filenames during leftover-chip cleanup.
- [x] Count duplicate rendered user turns once and select the newest matching turn across plain and attachment-prefixed shapes.
- [x] Require labeled HTTP/status context before interpreting a three-digit exception substring as an HTTP status.
- [x] Stop browser recorder listeners before awaiting outstanding capture tasks.
- [x] Update phase docs and PR description from DOM-first to demonstrated API-first text continuation.

## Verification gate

The earlier `89637e4` baseline supplied live evidence and a green local suite, but it does not verify the later hardening commits.

Before merge:

- [ ] Run the full pytest suite on the current PR head and record the exact pass/skip/fail totals.
- [ ] After an appropriate request cooldown, run exactly one fresh async text-write acceptance against a sacrificial thread and require canonical `complete` user/assistant graph receipts.
- [ ] Confirm no new unresolved PR review threads after the fresh review pass.
- [ ] Merge only after all three conditions above are satisfied.

No attachment API work, structural graph operations, or interactive migration belongs in PR #41 before this gate is complete.
