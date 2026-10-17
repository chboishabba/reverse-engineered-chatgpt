# TODO

## Completed product milestones

- [x] Exact conversation reads through the adapter-independent graph broker.
- [x] API-first existing-thread text continuation.
- [x] Exactly-once mutation state and canonical graph reconciliation.
- [x] Direct attachment staging (`files -> blob PUT -> process_upload_stream -> conversation`).
- [x] API-first MCP continuation with safe pre-commit DOM fallback.
- [x] Redaction-first browser protocol capture and accessibility experiment receipts.
- [x] Direct new-conversation creation with assigned conversation ID capture.
- [x] Direct branch-from-exact-node mutation.
- [x] Direct regenerate/variant mutation from an exact user node.
- [x] Direct edit-and-branch from an exact user node.
- [x] Route the supported command-line mutation surface through `MutationService`.
- [x] Route legacy `SyncConversation.chat()` / interactive mutations through `MutationService` compatibility.
- [x] Expose new/branch/regenerate/edit-and-branch through MCP.
- [x] Add one-command sacrificial graph-mutation acceptance runner.
- [x] Refresh README, user guide, read/mutation/browser docs and interface contract.

## Hardening / resilience

- [x] Run the full local pytest suite on the graph-mutation branch and record the exact pass/skip receipt (221 passed, 1 skipped).
- [x] Run `scripts/accept_graph_mutations_v1.py` against a sacrificial account thread and retain canonical complete receipts for all four graph operations.
- [ ] Expand the attachment acceptance matrix: multiple files, duplicate basenames, larger files and non-text MIME types.
- [ ] Add explicit session-bootstrap / refresh lifecycle commands and expiry diagnostics.
- [ ] Add protocol-drift detection against redacted browser-oracle captures.
- [ ] Add adapter health/backoff and empirical latency telemetry without weakening exactly-once semantics.
- [ ] Improve arbitrary uploaded-file archive download/provenance beyond current canonical attachment binding.
- [ ] Remove/deprecate obsolete low-level mutation implementations once downstream compatibility consumers no longer call them directly.

## Existing archive/context helpers

- [x] Automation-friendly `--list`, `--inspect`, `--view`, and `--download` flows.
- [x] `scripts/context_sync.sh` wrapper and catalog delta helper.
- [x] Cached/remote timestamps in `--inspect`.
