# API-First Transport Architecture

## Intent

Make `curl_cffi` HTTP the preferred steady-state transport for ordinary exact graph reads and text continuation, while retaining browser/CDP as an authentication/bootstrap surface, diagnostic oracle, acceptance surface, attachment fallback, and emergency mutation fallback. DOM is not the final product architecture.

## Invariants

- Canonical truth remains the validated conversation graph plus SQLite persistence/receipts.
- No adapter is canonical by identity; adapters are acquisition or mutation mechanisms.
- Request-context rejection does not imply account-wide throttling or conversation absence.
- Once send evidence exists, never auto-resubmit because reconciliation failed.
- Once canonical `committed` or `complete` exists, later read rejection cannot demote it.
- Protocol observations are redaction-first: never persist raw response bodies or secret values in diagnostics.
- Authenticated session snapshot persistence is explicit opt-in rather than a side effect of ordinary mutation.

## Target architecture

```text
CLI / MCP / Python
       |
MutationService
       |
text transport
  |-- async curl_cffi  (demonstrated direct path; promotion receipt-gated)
  `-- browser DOM      (fallback / attachments)
       |
exact graph broker
  |-- async curl_cffi
  |-- sync curl_cffi
  `-- browser exact read
       |
validated graph
       |
ConversationStorage + attachment archive + mutation receipts
```

## Exact graph acquisition broker

Expose one adapter-independent contract:

```python
fetch_exact_graph(conversation_id, ...) -> GraphAcquisitionResult
```

`GraphAcquisitionResult` carries the graph, adapter used, timing, redacted observations, attempted adapters, and rejected adapters. Current priority is `async -> sync -> browser`. Every accepted graph goes through one validator and persistence path.

Each adapter receives at most one attempt per broker call. A request-context rejection quarantines only that adapter for the active operation. The caller never falls back to catalog traversal for an exact ID.

Synchronous MCP/library entrypoints use one running-event-loop-aware bridge when invoking async transports.

## Exactly-once async mutation semantics

Direct async text mutation is a one-attempt operation.

Pre-dispatch preparation errors may be reported as precommit failures. Once the conversation POST is dispatched, every uncertain outcome is unsafe to resend:

- 4xx: explicit rejection;
- 5xx: ambiguous post-dispatch outcome;
- timeout/reset after dispatch: ambiguous;
- 200 without usable SSE evidence: ambiguous;
- observation/cleanup/context-manager failure after dispatch: ambiguous.

An ambiguous async write never causes an automatic second HTTP POST or DOM fallback. Recovery is graph reconciliation only.

Promotion of direct async text continuation requires canonical user/assistant graph receipts rather than transport success alone.

## Diagnostics

Browser and CFFI diagnostics share a redaction-first structural observation shape. Useful fields include transport, operation, sequence, method/path, status, response class, header names, cookie names/domains, timing, body key/schema shape, and SSE event shape. Unknown browser diagnostic fields are not merged into sanitized receipts.

The product boundary is ordinary authenticated session state plus supported request metadata. Session snapshots are explicit local operational artifacts rather than an automatic mutation side effect.

## Session snapshot safety

If an operator explicitly captures an authenticated browser session snapshot:

- write it through a unique exclusive temporary file created with mode `0600`;
- atomically replace the destination only after a completed write;
- never enable persistence merely because a profile directory was provided;
- ordinary mutation and read/status protocol recording remain observation-only.

## Browser fallback and attachments

Browser mutation remains available and continues to own attachment sends until a direct attachment API path earns canonical receipts.

Attachment staging must clean only complete-filename matches, dismiss blocking dialogs, send exactly once, and bind the resulting local file to the canonical graph file ID/pointer on the committed user node.

Rendered DOM follow logic selects the newest matching user turn across both plain text and attachment-prefixed shapes.

## Release gate for PR #41

The pre-hardening `89637e4` baseline demonstrated:

- async exact reads across short/medium/long conversations;
- two complete canonical async/CFFI text mutation receipts;
- DOM medium/long completion;
- canonical DOM attachment completion;
- local `pytest`: 173 passed, 1 skipped.

Review hardening after that baseline addresses exactly-once ambiguity, event-loop compatibility, session persistence safety, diagnostic redaction, attachment matching, duplicate DOM turn selection, status parsing, and recorder shutdown races.

The later hardening head must receive a fresh full pytest receipt and one fresh paced canonical async-write receipt before merge/release is claimed. No new feature lane belongs in PR #41 before that gate is paid.

## Post-merge roadmap

After PR #41 is verified and merged:

1. promote direct async text continuation toward the preferred normal transport while retaining browser fallback;
2. add direct attachment support only after the normal upload/message-reference sequence is demonstrated and canonically verified;
3. make normal text MCP operation no-DOM;
4. implement new conversation, branch, regenerate, and edit-and-branch with direct semantics where proven and DOM fallback otherwise;
5. migrate the interactive CLI onto the same `MutationService` path.
