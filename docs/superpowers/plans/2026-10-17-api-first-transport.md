# API-First Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exact graph acquisition adapter-independent and async-CFFI-first, add redacted browser/CFFI protocol observations, and add an opt-in async HTTP write probe whose success is certified only by the canonical graph.

**Architecture:** `MutationService` delegates graph acquisition to a focused broker. All adapters return one validated graph result shape and share persistence/receipt logic. Browser/CDP and CFFI emit the same redacted protocol-observation schema; DOM remains the production mutation fallback until an opt-in async write probe proves canonical success.

**Tech Stack:** Python, curl_cffi, asyncio, Playwright/CDP, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-10-17-api-first-transport-design.md`

## Global Constraints

- Canonical truth is the validated conversation graph plus SQLite receipts, never an adapter identity.
- Treat 429/Too Many Requests as request-context rejection for that adapter.
- Never auto-resubmit after submission evidence.
- Never demote a canonical committed/complete operation because a later read failed.
- Never persist cookie/token/device-ID/opaque anti-abuse secret values or raw HTTP response bodies in protocol observations.
- Do not synthesize attachment upload endpoints in this cut.

## Review Focus

- Async adapter initialization/bootstrap failure must fall through without aborting the broker.
- One adapter 429 must quarantine only that adapter for the current broker operation.
- A valid graph from a later adapter must use the identical persistence/receipt path.
- Observation serialization must not contain secret values or transcript/response bodies.
- The async write probe must never retry or silently fall back after an ambiguous POST outcome.

---

### Task 1: Adapter-independent exact graph broker

**Files:**
- Create: `re_gpt/graph_broker.py`
- Create: `tests/test_graph_broker.py`
- Modify: `re_gpt/mutation_service.py`

**Interfaces:**
- Produces: `GraphAcquisitionResult`, `RequestContextRejected`, `fetch_exact_graph(...)`.
- Consumes: `AsyncChatGPT.fetch_conversation`, `SyncChatGPT.fetch_conversation`, `fetch_conversation_same_origin`.

- [ ] **Step 1: Write failing tests** for async-first success, async-429→sync success, async/sync-429→browser success, per-adapter single-attempt quarantine, non-rejection failures, and all-adapters-rejected diagnostics.
- [ ] **Step 2: Run focused tests** and confirm the broker does not yet exist.
- [ ] **Step 3: Implement the broker** with one asyncio boundary, shared graph validation, adapter attempt/rejection receipts, and no persistence side effects.
- [ ] **Step 4: Wire `MutationService`** to persist a returned broker graph through its existing single persistence path and expose adapter/attempt/rejection metadata.
- [ ] **Step 5: Run broker + mutation-service tests** and verify monotonic receipt behavior remains intact.

### Task 2: Redaction-first protocol observation model

**Files:**
- Create: `re_gpt/protocol_observation.py`
- Create: `tests/test_protocol_observation.py`
- Modify: `re_gpt/read_diagnostics.py`

**Interfaces:**
- Produces: `ProtocolObservation`, `sanitize_headers`, `sanitize_cookies`, `sanitize_json_schema`, `observation_from_exception`.
- Consumes: request metadata only; never raw secret values in the serialized result.

- [ ] **Step 1: Write failing redaction tests** with sentinel token, Authorization value, device-ID value, cookie values, transcript text, and nested payload values.
- [ ] **Step 2: Run tests** and confirm absence of the shared schema.
- [ ] **Step 3: Implement schema and sanitizers** preserving names/booleans/key structure while discarding values.
- [ ] **Step 4: Refactor read diagnostics** to emit this schema for async/sync/browser exact-read probes.
- [ ] **Step 5: Run diagnostics tests** and assert serialized receipts contain none of the injected secret strings.

### Task 3: Browser/CDP protocol recorder

**Files:**
- Create: `re_gpt/browser_protocol_recorder.py`
- Create: `tests/test_browser_protocol_recorder.py`
- Modify: `re_gpt/browser_observer.py` or existing browser relay only at the smallest integration seam.

**Interfaces:**
- Produces: bounded recorder context/listener returning ordered `ProtocolObservation` records.
- Consumes: Playwright request/response events for ChatGPT same-origin traffic.

- [ ] **Step 1: Write failing pure-event tests** using fake request/response objects for GET, JSON POST, SSE POST, sentinel header presence, cookies, and non-ChatGPT filtering.
- [ ] **Step 2: Run focused tests** and confirm recorder absence.
- [ ] **Step 3: Implement recorder** with normalized paths, sequence/timing, sanitized payload schema, response status/class, and explicit start/stop boundaries.
- [ ] **Step 4: Add a CLI/debug entrypoint** that records one bounded browser action without persisting secrets.
- [ ] **Step 5: Run recorder/redaction tests** and inspect one serialized fixture for secret absence.

### Task 4: Opt-in async-CFFI mutation probe

**Files:**
- Create: `re_gpt/async_mutation_probe.py`
- Create: `tests/test_async_mutation_probe.py`
- Modify: `re_gpt/async_chatgpt.py` only where status/ambiguity evidence is currently swallowed.

**Interfaces:**
- Produces: one-attempt async HTTP `continue` probe returning submission metadata and protocol observations; never auto-retries.
- Consumes: existing `AsyncConversation.build_message_payload`, chat-requirements token generation, and curl_cffi session.

- [ ] **Step 1: Write failing tests** for accepted SSE, explicit HTTP rejection, timeout/transport ambiguity, no retry, and canonical follow-up verification callback.
- [ ] **Step 2: Run focused tests** and confirm current async writer cannot surface the required one-attempt status contract.
- [ ] **Step 3: Implement a one-attempt probe** that preserves response status/headers/event-shape metadata without storing raw bodies and classifies ambiguous outcome separately from explicit rejection.
- [ ] **Step 4: Add explicit sacrificial-thread CLI flags**; no default production promotion and no attachment HTTP guessing.
- [ ] **Step 5: Run probe tests** and verify ambiguous outcomes never invoke DOM fallback or a second POST.

### Task 5: Mutation-service promotion seam and documentation

**Files:**
- Modify: `re_gpt/mutation_service.py`
- Modify: `re_gpt/mcp_server.py`
- Modify: `docs/read-plane.md`
- Modify: `docs/browser-mutation-service.md`
- Modify: `scripts/accept_existing_thread_v1.py`
- Test: `tests/test_mutation_service.py`, MCP tests if present.

**Interfaces:**
- Produces: stable result fields `read_adapter`, `attempted_read_adapters`, `rejected_read_adapters`, `read_observations`; optional explicit HTTP mutation-probe mode.
- Consumes: Tasks 1–4.

- [ ] **Step 1: Write failing integration tests** for async-first reconciliation, all-adapter rejection without resend, prior-complete preservation, and result metadata.
- [ ] **Step 2: Implement broker-backed reconcile path** and keep DOM as production writer by default.
- [ ] **Step 3: Extend acceptance runner** with read-only broker preflight and opt-in async mutation probe mode; never resend previously proven nonce.
- [ ] **Step 4: Update docs** to state API-first target, browser oracle/fallback role, DOM-last policy, and promotion gate.
- [ ] **Step 5: Run the full test suite** plus static/import smoke checks available in the environment.
- [ ] **Step 6: Inspect the complete branch diff**, open a PR, and leave live HTTP-write promotion explicitly gated on sacrificial-thread canonical receipts.
