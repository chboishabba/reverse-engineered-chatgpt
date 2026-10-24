# Roadmap

## Phase 01 — Remote conversation sync
- Objective: fetch, filter, and display the authoritative JSON from ChatGPT endpoints so the CLI can recover any conversation (e.g., Fish Spine Symbolism) without previous exports.
- Domain: CLI tooling + automation scripts.

## Post-Milestone-E — terminal SSE timeout reconciliation (PR candidate)
- Direct async conversation POST decodes SSE incrementally and preserves redacted terminal evidence on timeout.
- Terminal+timeout selects exactly one direct canonical-graph reconciliation; only graph proof permits complete. Preterminal timeout is held without read or resend.
- This is source-written and requires caller-owned local tests/review; no live transport acceptance or formal proof is claimed.
