# Mutation service: API-first text, browser fallback

`MutationService` owns exactly-once mutation operations and canonical graph receipts. A transport can submit or observe work; only the validated conversation graph plus SQLite persistence certifies commit.

## Current phase boundary

PR #41 established a demonstrated direct async/CFFI text continuation path while retaining the browser path for fallback, attachments, diagnostics, and acceptance testing.

```text
CLI / MCP / Python
       |
MutationService
       |
text continuation transport
  |-- async CFFI one-attempt path (demonstrated, promotion gated)
  `-- browser/DOM fallback
       |
exact graph broker
  |-- async
  |-- sync
  `-- browser exact read
       |
validated graph
       |
ConversationStorage + attachment archive + mutation receipts
```

DOM is no longer the conceptual target for ordinary text continuation. It remains valuable as an independent fallback and for capabilities that have not yet been promoted to a direct supported API path.

## Exactly-once boundary

Preparation before a trigger is retryable. Any evidence that the conversation POST or browser Send was dispatched is a one-way door.

After dispatch, recovery is limited to observation and reconciliation. Never resend automatically.

For async HTTP writes:

- explicit 4xx response: explicit rejection;
- 5xx response: ambiguous post-dispatch outcome;
- timeout/reset after dispatch: ambiguous;
- HTTP 200 without SSE data: ambiguous;
- cleanup/context-manager failure after dispatch: ambiguous.

An ambiguous async outcome must not silently fall back to a DOM send, because the remote user turn may already exist.

For browser writes, submission evidence likewise makes a second trigger unsafe even if graph acquisition later fails.

## Canonical states

- `failed_precommit`: mutation was not dispatched; retry is safe after correcting the cause.
- `attachment_failed_precommit`: attachment preparation failed before submission evidence.
- `submission_observed`: dispatch evidence exists; never auto-resend.
- `commit_unknown`: graph acquisition cannot yet certify the new user node; never auto-resend.
- `committed`: validated graph contains the exact new user node.
- `committed_pending`: canonical user node exists but final assistant/attachment metadata is still pending.
- `complete`: canonical graph contains the user node, required attachment receipts, and the final assistant descendant.

Once `committed`/`committed_pending`/`complete` is established, later read rejection cannot demote that canonical fact.

## Graph acquisition

Reconciliation is adapter-independent. Current order is async -> sync -> browser. Each adapter is attempted at most once per broker call; request-context rejection is scoped to the current operation rather than generalized into account-wide throttling.

Reasoning/tool intermediate nodes are traversed from the canonical user node to the final visible assistant descendant before declaring completion.

## Browser/DOM fallback

The browser path still provides:

- authenticated UI fallback for text continuation;
- native attachment staging;
- transient DOM live status;
- protocol/diagnostic oracle;
- acceptance testing;
- explicit session bootstrap/capture when requested.

Normal mutation runs are observation-only with respect to session snapshots. Merely using a profile does not persist bearer tokens/cookies/session state.

Rendered duplicate prompts are matched by newest user turn across both plain-text and attachment-prefixed shapes. Existing attachment cleanup requires the complete filename rather than substring matching.

## Attachments

The current production attachment send remains browser-native:

```text
real composer
-> native file input
-> attachment chip/readiness
-> prompt
-> Send once
-> exact graph broker
-> canonical user node
-> canonical file ID/pointer binding
-> SQLite provenance
```

Blocking attachment dialogs are dismissed before composer interaction. Pre-existing staged chips are removed only when their full filename matches the new operation's expected attachment.

Direct attachment API support is a later phase; this tranche does not invent upload endpoints.

## Protocol observations and session state

Diagnostics are redaction-first. They may retain structural facts such as method/path, header names, cookie names/domains, status, sequence, and timing, but not secret values or response/transcript bodies. Unknown browser diagnostic fields are not copied back into sanitized receipts.

Browser protocol recorders detach listeners before awaiting final capture tasks, eliminating the shutdown race where a late response could mutate snapshot state after finalization began.

Explicit saved session snapshots are atomically written through exclusive `0600` temporary files and replace the destination only after a completed write. Session snapshot persistence is explicit opt-in; ordinary mutation and read/status recording do not enable it merely by having a profile directory.

## MCP

MCP continues to call `MutationService` directly. Sync entrypoints use a shared running-loop-aware bridge for async work, avoiding nested `asyncio.run()` failures under FastMCP/event-loop callers.

## Acceptance boundary

The pre-hardening live baseline demonstrated:

- async exact reads on short, medium, and long threads;
- two canonical complete async/CFFI text-write receipts;
- canonical medium and long DOM fallback receipts;
- canonical DOM attachment completion with a ChatGPT file ID;
- a local full suite of 173 passed, 1 skipped.

The review-hardening commits after that baseline require their own fresh verification before merge: full pytest plus one paced async-write canonical receipt. No additional feature work belongs in PR #41 before that gate is paid.

## Next phase after merge

Once the hardening head receives a fresh green suite and one canonical async-write acceptance receipt, merge PR #41 and move the roadmap to API-first product work:

1. promote direct async text continuation toward the preferred transport while retaining DOM fallback;
2. add direct attachment support only when the supported upload/message-reference sequence is demonstrated;
3. make normal text MCP operation no-DOM (`MutationService -> async write -> async graph read -> SQLite`);
4. implement new conversation, branch, regenerate, and edit-and-branch through direct semantics where proven, DOM otherwise;
5. migrate the interactive CLI onto the same `MutationService` path.
