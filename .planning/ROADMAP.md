# Roadmap

## Phase 01 — Remote conversation sync
- Objective: fetch, filter, and display the authoritative JSON from ChatGPT endpoints so the CLI can recover any conversation (e.g., Fish Spine Symbolism) without previous exports.
- Domain: CLI tooling + automation scripts.

## Post-Milestone-E — terminal SSE timeout reconciliation (merged PR #51)
- Direct async conversation POST decodes SSE incrementally and preserves redacted terminal evidence on timeout.
- Terminal+timeout selects exactly one direct canonical-graph reconciliation; only graph proof permits complete. Preterminal timeout is held without read or resend.
- Merged to main in PR #51 (4d20dda); caller-owned focused tests and lint have passed.
- A controlled live terminal-timeout fault-injection receipt and a formal proof remain open; neither is claimed by the merge.

## Python TUI client/server completion (2026-10-30)
Canonical plan: [docs/python_tui_client_server_completion_rpd_20261030.md](../docs/python_tui_client_server_completion_rpd_20261030.md).

- [x] Reuse tritao TUI frontend; reuse PG first conversation reads and `conversation_sync` transport revision watcher.
- [x] Wire presentation-only Unicode math, MDX Link and citation fallback (not complete typesetting).
- [ ] Server-owned independent A/B operation jobs, PG event journal, replay cursors and event subscriptions.
- [ ] Switch A→B while A runs without cancellation, reparenting, duplicate messages or resubmission; crash/reconnect acceptance.
- [ ] Integrate full TeX typesetting, citation resolution and streamed tool body display using ITIR Svelte reader as semantic reference.
- [ ] CFFI-only renewal and PG hydration merge/acceptance, with explicit queue/branch validation.
- [ ] AO @local/@both/@ao orchestration and optional auto-response with capacity and authority controls.
- [ ] Verify Herdr and dex actual APIs/licensing; keep Dux separate and do not misattribute screenshots.
