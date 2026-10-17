# Accessibility Capture Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the redaction-first accessibility capture harness that records one bounded browser attachment continuation, graph-before/after structure, canonical attachment binding, and a share-safe JSON receipt for the operator to run locally.

**Architecture:** Add one pure receipt/diff module, then a thin capture runner that reuses the existing browser actuator, `BrowserProtocolRecorder`, `GraphBroker`, `MutationService`, and attachment canonicalization. The browser remains the actuator/oracle; canonical graph reconciliation remains semantic truth; this tranche does not synthesize direct attachment HTTP writes.

**Tech Stack:** Python 3, pytest, existing Playwright accessibility relay, existing curl_cffi-backed graph broker/mutation service, JSON receipts.

**Spec:** `docs/superpowers/specs/2026-10-17-accessibility-capture-harness-design.md`

## Global Constraints

- This work exists for accessibility: reduce visual browser dependence while preserving the accessibility-compatible DOM fallback.
- Experiments are explicit, local, bounded, and initiated by the authenticated operator.
- Never auto-repeat a mutation after submission may have been dispatched.
- Serialize only redaction-safe protocol structure; never serialize bearer/cookie/sentinel values, raw HTTP bodies, transcript contents, local file bytes, or local absolute paths.
- Session-snapshot persistence remains explicit opt-in and outside experiment receipts.
- Canonical success comes from the conversation graph, not DOM state or HTTP success alone.
- The first tranche supports `attachment-continue` capture only; no direct HTTP attachment synthesis yet.

## Review Focus

- Graph nodes with missing/null messages must still project deterministically without leaking arbitrary values; Task 1 pins this.
- Attachments represented under metadata/message/content variants must collapse to stable canonical summaries; Task 1 pins this.
- A browser mutation that raises after potential dispatch must never be invoked a second time; Task 2 pins this.
- Receipt serialization must not leak absolute paths or sentinel/token-like marker strings from mocked inputs; Task 1 pins this.
- Graph acquisition failure after the browser action must still emit a bounded `commit_unknown`/error receipt rather than resend; Task 2 pins this.

---

### Task 1: Structural graph projection, delta, and experiment receipt

**Files:**
- Create: `re_gpt/experiment_receipt.py`
- Create: `tests/test_experiment_receipt.py`

**Interfaces:**
- Consumes: raw validated conversation graph mappings and existing `ProtocolObservation.to_dict()` output.
- Produces:
  - `project_graph(chat: Mapping[str, Any]) -> dict[str, Any]`
  - `graph_snapshot(chat: Mapping[str, Any]) -> dict[str, Any]`
  - `diff_graphs(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]`
  - `summarize_protocol_stages(observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]`
  - `build_experiment_receipt(...) -> dict[str, Any]`
  - `serialize_experiment_receipt(receipt: Mapping[str, Any]) -> str`

- [ ] **Step 1: Write failing projection/redaction tests**

Add tests that assert projected nodes retain only node/edge/role/content-type/status/end-turn/timestamp/selected structural metadata-key names/canonical attachment summaries/current-node membership; transcript text and arbitrary metadata values are absent. Include nodes with missing `message` and attachment variants under metadata/message/content.

- [ ] **Step 2: Run the focused projection tests and verify failure**

Run: `pytest -q tests/test_experiment_receipt.py -k 'project or redact'`
Expected: FAIL because `re_gpt.experiment_receipt` does not exist.

- [ ] **Step 3: Implement stable projection and digest**

Implement `project_graph()` and `graph_snapshot()` using canonical attachment extraction from `re_gpt.attachments`. Hash the canonical JSON projection with SHA-256; never hash raw transcript-bearing graph JSON.

- [ ] **Step 4: Write failing graph-delta tests**

Cover added/removed nodes, changed projected fields, added/removed edges, current-node transition, and added canonical attachment identities.

- [ ] **Step 5: Implement `diff_graphs()`**

Return deterministic sorted IDs/edge pairs and changed-field names only; do not embed before/after raw values except safe canonical attachment summaries.

- [ ] **Step 6: Write failing protocol-stage and receipt-redaction tests**

Assert ordered stage summaries preserve `sequence_index`, `bootstrap_stage`, normalized method/path/status/response class, and an explicit `unclassified` stage when bootstrap classification is absent. Assert receipt JSON excludes mocked bearer/cookie/sentinel markers, transcript markers, and `/home/...` attachment paths.

- [ ] **Step 7: Implement stage summary and receipt assembly/serialization**

Top-level receipt schema version is `1`; local attachment inputs serialize filename, MIME type, size, and SHA-256 only.

- [ ] **Step 8: Run Task 1 tests**

Run: `pytest -q tests/test_experiment_receipt.py`
Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Commit message: `feat: add redacted experiment graph receipts`

---

### Task 2: One-shot accessibility attachment capture orchestrator

**Files:**
- Create: `re_gpt/accessibility_capture.py`
- Create: `tests/test_accessibility_capture.py`
- Reuse without semantic duplication: `re_gpt/mutation_browser.py`, `re_gpt/browser_protocol_recorder.py`, `re_gpt/mutation_service.py`, `re_gpt/graph_broker.py`, `re_gpt/attachments.py`

**Interfaces:**
- Consumes:
  - `LocalAttachment.from_path(path)`
  - `fetch_exact_graph(...)`
  - existing accessibility browser relay/readiness/staging/submission primitives
  - `MutationService.reconcile(...)` / canonical attachment matching behavior
  - Task 1 projection/delta/receipt helpers
- Produces:
  - `capture_attachment_continue(selector: str, prompt: str, attachments: Sequence[Path | str], *, db_path=..., profile_dir=..., browser_mode='displayless', cdp_endpoint=None, browser_timeout=60, follow=False, dependencies: CaptureDependencies | None = None) -> dict[str, Any]`
  - a small injectable dependency bundle/protocol so tests can substitute graph reads and the single browser actuator without launching a browser.

- [ ] **Step 1: Write failing happy-path orchestration test**

Mock graph-before, exactly one browser actuator call returning redacted protocol observations/submission metadata, and graph-after containing one new committed user node with a canonical attachment plus assistant child. Assert the receipt contains one local SHA-256 record, one browser mutation attempt, graph delta, canonical user/assistant IDs, canonical attachment ID, and read receipts.

- [ ] **Step 2: Write failing exactly-once/ambiguous test**

Have the actuator raise a post-dispatch ambiguity-shaped exception/result and assert call count remains `1`; reconciliation/after-read is attempted and no second actuator invocation occurs.

- [ ] **Step 3: Write failing graph-after acquisition failure test**

After a successful single browser actuation, make the post-action graph read fail. Assert the function returns a redaction-safe bounded receipt with canonical state `commit_unknown` (or explicit capture error field) and never actuates again.

- [ ] **Step 4: Run Task 2 tests and verify failure**

Run: `pytest -q tests/test_accessibility_capture.py`
Expected: FAIL because orchestrator does not exist.

- [ ] **Step 5: Implement the one-shot browser actuator adapter**

Reuse `AccessibilityBrowserRelay`, `open_ready_thread`, `stage_attachments`, `submit_prompt_once`, and `BrowserProtocolRecorder(save_session_snapshot=False)` in one bounded async action. Return only redaction-safe protocol observations and staging/submission metadata; do not serialize local absolute paths.

- [ ] **Step 6: Implement `capture_attachment_continue()`**

Sequence: resolve/fingerprint local attachments -> exact graph before -> one browser actuator -> exact graph/reconciliation after -> graph projection/delta -> canonical attachment/user/assistant binding -> receipt. If the actuator may have dispatched, never call it again regardless of subsequent failure.

- [ ] **Step 7: Run Task 2 tests**

Run: `pytest -q tests/test_accessibility_capture.py`
Expected: PASS.

- [ ] **Step 8: Run focused regressions**

Run: `pytest -q tests/test_browser_protocol_recorder.py tests/test_mutation_receipt.py tests/test_mutation_service.py tests/test_graph_broker.py tests/test_experiment_receipt.py tests/test_accessibility_capture.py`
Expected: PASS.

- [ ] **Step 9: Commit Task 2**

Commit message: `feat: add one-shot accessibility attachment capture`

---

### Task 3: Operator CLI and runbook

**Files:**
- Create: `scripts/capture_accessibility_operation.py`
- Create: `tests/test_capture_accessibility_operation.py`
- Create: `docs/accessibility-capture.md`

**Interfaces:**
- Consumes: `capture_attachment_continue()` from Task 2.
- Produces one CLI command with initial operation `attachment-continue` and share-safe JSON output.

- [ ] **Step 1: Write failing parser/output tests**

Cover: required selector + prompt source + at least one `--attach`; existing browser/profile/CDP options; `--output` writes UTF-8 JSON; default stdout JSON; no absolute attachment path appears in emitted receipt.

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `pytest -q tests/test_capture_accessibility_operation.py`
Expected: FAIL because script does not exist.

- [ ] **Step 3: Implement CLI**

Command shape:

```bash
python scripts/capture_accessibility_operation.py attachment-continue \
  <conversation-selector> \
  --prompt 'ACCESSIBILITY-ATTACHMENT-PROBE-<unique>' \
  --attach ./probe.txt \
  --output ./attachment-capture.json
```

Support `--prompt-file`, repeated `--attach`, `--profile-dir`, `--browser-mode`, `--cdp-endpoint`, `--timeout`, `--follow`, and `--db-path`. Exit nonzero only for pre-actuation validation/bootstrap failures; post-dispatch uncertainty is serialized as a receipt rather than encouraging rerun.

- [ ] **Step 4: Write runbook**

Document: use a sacrificial existing thread, use a uniquely named small non-sensitive file, run exactly once, do not manually retry an uncertain result, and return only the generated receipt. Explain that raw session snapshots and raw graph dumps are deliberately excluded.

- [ ] **Step 5: Run Task 3 tests**

Run: `pytest -q tests/test_capture_accessibility_operation.py`
Expected: PASS.

- [ ] **Step 6: Run all new/focused tests**

Run: `pytest -q tests/test_experiment_receipt.py tests/test_accessibility_capture.py tests/test_capture_accessibility_operation.py tests/test_browser_protocol_recorder.py tests/test_mutation_service.py tests/test_graph_broker.py`
Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Commit message: `feat: add accessibility capture CLI and runbook`

---

### Task 4: Branch-level review and operator handoff

**Files:**
- Modify only if review reveals a concrete defect.

**Interfaces:**
- Consumes all previous task outputs.
- Produces a branch/PR ready for the operator's local run.

- [ ] **Step 1: Self-review against the design spec**

Verify every serialized field is structurally necessary and share-safe; verify the browser actuator is reachable exactly once per invocation; verify there is no direct attachment HTTP writer in this tranche.

- [ ] **Step 2: Run the repository-focused verification command available in the execution environment**

Preferred: `pytest -q tests/test_experiment_receipt.py tests/test_accessibility_capture.py tests/test_capture_accessibility_operation.py tests/test_browser_protocol_recorder.py tests/test_mutation_receipt.py tests/test_mutation_service.py tests/test_graph_broker.py`

If the execution environment cannot run the repo, do not claim a pass; leave the exact command for the operator.

- [ ] **Step 3: Review changed diff for secret/path leakage**

Search changed code/tests/docs for accidental token values, cookie values, raw graph serialization, and local absolute-path serialization.

- [ ] **Step 4: Open/update PR**

PR body should state the accessibility purpose, one-shot safety boundary, capture-only scope, exact local command, and that the returned receipt is the input to the subsequent direct-attachment implementation max-cut.
