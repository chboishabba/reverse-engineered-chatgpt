# TODO

## Completed product milestones

- [x] Exact conversation reads through the adapter-independent graph broker.
- [x] API-first existing-thread text continuation.
- [x] Exactly-once mutation state and canonical graph reconciliation.
- [x] Direct attachment staging (`files -> blob PUT -> process_upload_stream -> conversation`).
- [x] API-first MCP continuation with direct async/no-DOM defaults; DOM fallback is explicit manual diagnostic opt-in.
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
- [ ] Expand the attachment acceptance matrix: multiple files, duplicate basenames, larger files and non-text MIME types (implemented in PR #46; live attachment receipt still pending here).
- [x] Add explicit session-bootstrap / refresh lifecycle commands and expiry diagnostics (PR #45 merged).
- [x] Add protocol-drift detection against redacted browser-oracle captures (PR #45 merged).
- [ ] Add adapter health/backoff and empirical latency telemetry without weakening exactly-once semantics (implemented and locally verified in PR #46; pending merge).
- [x] Implement stream-first `follow=True`: consume active conversation SSE/DOM completion and perform one terminal canonical reconciliation with zero full-graph follow polls (Milestone E branch).
- [x] Add a cross-process SQLite `/backend-api/` pacing queue with 1.5s default exact-read spacing and exponential global 429 backoff (Milestone E branch).
- [x] Add canonical per-profile browser launch leases so same-profile operations serialize without blocking distinct profile directories (Milestone E branch).
- [ ] Run `scripts/accept_stream_follow_v1.py` and retain the real-time progress + zero-poll terminal reconciliation receipt before merging Milestone E.
- [ ] Improve arbitrary uploaded-file archive download/provenance beyond current canonical attachment binding.
- [ ] Remove/deprecate obsolete low-level mutation implementations once downstream compatibility consumers no longer call them directly.
- [x] Keep MCP worker responses small: drop transport telemetry (`read_observations`, `write_observations`, timestamps, node IDs) from `chatgpt_continue` / `itir.chatgpt.dispatch_worker`. Keep it in the durable SQLite receipt only.
- [x] Add worker prompt presets (`@max_cut`, `@grug_ste100`, `@zkp_qa`, `@introspective_viz`, `@referee_pass`). Callers can send short replies instead of full prompts.
- [ ] Roadmap (not yet priority): staged outbound prompts. The caller writes the prompt to the SQLite archive first, as a pending row. The service then reads the remote thread head. It sends only if the remote head is not newer than the local head. This removes temporary prompt/response files and gives a send-once guard before transport.
- [ ] Roadmap: return large worker replies by reference (operation ID + message index), not inline, when the host MCP client has an output size cap. Read the full text through `itir.archive.thread_view`.

## Existing archive/context helpers

- [x] Automation-friendly `--list`, `--inspect`, `--view`, and `--download` flows.
- [x] `scripts/context_sync.sh` wrapper and catalog delta helper.
- [x] Cached/remote timestamps in `--inspect`.

## Terminal-timeout direct-async hardening (merged; verification recorded)
- [x] Merged PR #51 to main (4d20dda) after caller-owned focused verification: 36 timeout/reconciliation tests, Ruff, and a 24-test ITIR frontier/handoff/PNF packet passed.
- [x] Independently reviewed fail-closed canonical identity matching, exactly-once state transitions, no-resend behavior, and the direct-only terminal-timeout reconciliation path.
- [ ] Run a controlled live terminal-timeout fault-injection acceptance. A normal governed smoke continuation completed, but it did not exercise that timeout branch.
- [ ] Triage the existing full-suite auth-bootstrap baseline separately: 297 passed, 1 skipped, and 5 unrelated failures remain in unchanged InvalidSessionToken handling.
- [ ] Formal gap: executable Python tests do not establish a kernel-verified refinement of POSTCount <= 1, Complete => CanonicalGraphProves, or post-timeout graph reads <= 1.
