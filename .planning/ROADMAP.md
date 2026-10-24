# Roadmap

## Phase 01 — Remote conversation sync
- Objective: fetch, filter, and display the authoritative JSON from ChatGPT endpoints so the CLI can recover any conversation (e.g., Fish Spine Symbolism) without previous exports.
- Domain: CLI tooling + automation scripts.

## Post-Milestone-E — terminal SSE timeout reconciliation (merged PR #51)
- Direct async conversation POST decodes SSE incrementally and preserves redacted terminal evidence on timeout.
- Terminal+timeout selects exactly one direct canonical-graph reconciliation; only graph proof permits complete. Preterminal timeout is held without read or resend.
- Merged to main in PR #51 (4d20dda); caller-owned focused tests and lint have passed.
- A controlled live terminal-timeout fault-injection receipt and a formal proof remain open; neither is claimed by the merge.
