# API-First Transport Implementation Plan

**Goal:** Prefer the existing asynchronous exact-read client while preserving a shared validated-graph persistence path.

**Spec:** `docs/superpowers/specs/2026-10-17-api-first-transport-design.md`

## Tasks

1. Add an adapter-independent exact graph broker with async, sync, and browser readers.
2. Make `MutationService` use the broker and expose which reader succeeded.
3. Preserve existing exactly-once mutation behavior and canonical receipt monotonicity.
4. Add non-sensitive operational diagnostics that exclude raw request/response values.
5. Update MCP/docs and acceptance preflight for the new read architecture.
6. Verify with the strongest available test receipt before merge.
