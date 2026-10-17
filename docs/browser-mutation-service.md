# Browser-owned mutation service

`re-gpt --send` treats the browser as the mutation owner and a validated ChatGPT conversation graph as the commit authority.

## Production path

```text
CLI / MCP
  -> MutationService
  -> authenticated browser profile
  -> target thread hydration
  -> real ProseMirror composer
  -> optional native file-input staging
  -> one DOM trigger
  -> browser submission evidence
  -> reconciliation read
       -> sync exact GET
       -> on direct-client rejection only: authenticated browser same-origin exact GET
  -> shared graph validation
  -> canonical user-node + attachment receipt
  -> ConversationStorage / shared SQLite
```

The historical handcrafted same-origin conversation POST remains diagnostic/research code in `re_gpt.accessibility_browser`. The production write path never tries it first. The browser fallback described above is read-only and retrieves structured conversation JSON; it is not a second mutation transport and not a rendered-DOM transcript scrape.

## Existing-thread readiness

A thread is mutation-ready only when:

1. the URL is the requested `/c/<uuid>`;
2. `/api/auth/session` exposes an access token;
3. the existing thread has hydrated;
4. `#pending-conversation-input` is absent;
5. a real `.ProseMirror[contenteditable="true"]` editor is visible.

The empty composer does not have to expose a Send control before text exists: current ChatGPT UI may expose Start Voice until insertion. Send discovery and enabled-state validation remain owned by the post-insertion submission path.

The pending textarea is a loading-state control and is deliberately excluded from production editor selectors. Modern rendered-history evidence includes the conversation region and message controls such as `Copy message` / `Edit message` in addition to older message-author attributes.

## Mutation states

Browser evidence is weaker than canonical commit evidence.

- `failed_precommit`: the DOM mutation was never observed; retry is safe after fixing the cause.
- `attachment_failed_precommit`: native file staging failed before any message submission evidence; retry is safe.
- `submission_observed`: the browser emitted evidence that makes a second trigger unsafe.
- `commit_unknown`: reconciliation did not prove the user node. **Do not resend.** Reconcile later.
- `committed`: a validated graph contains the exact new user node.
- `committed_pending`: the user node is canonical but the assistant and/or canonical attachment metadata has not settled yet.
- `complete`: the canonical graph contains the user node, all expected attachments (if any), and an assistant child with text.

A direct-client 429 / `Too Many Requests` after DOM submission is recorded as `DirectClientRejected`, not `SyncRateLimited`. The service stops retrying that direct context immediately. It may perform one authenticated browser same-origin exact-thread GET; if that also fails, the durable result remains recoverable `commit_unknown`. No reconciliation failure can trigger a second browser submission.

## Browser read fallback

The fallback executes inside the authenticated Chrome origin:

```text
GET /api/auth/session
  -> access token
GET /backend-api/conversation/<id>
  Authorization: Bearer ...
  Accept: application/json
```

No `Content-Type` header is synthesized for the bodyless GET. The response must be a JSON object with a non-empty `mapping`; only then does it enter the same persistence path used by the sync reader.

The fallback can use the same launch-owned profile or the same CDP-attached browser context as mutation. CDP remains non-owning: re-gpt disconnects Playwright rather than closing the user's browser.

## Differential read diagnostics

Use:

```bash
python -m re_gpt.read_diagnostics <conversation-uuid>
```

to compare `sync`, `async`, and browser same-origin exact-thread reads. The output is intentionally redacted and contains only adapter, method, route, header names, cookie names/domains, auth/device-header presence, status, elapsed time, and response class. Cookie, bearer-token and device-ID values are never emitted.

Async remains useful as a differential reference implementation; it is not inserted between sync and browser in the production reconciliation chain.

## Live DOM status

`re_gpt.browser_observer` also exposes provisional rendered-state observation. The classifier distinguishes states such as:

```text
hydrating
ready
thinking
tool_activity
streaming
connection_interrupted
conversation_load_error
history_load_error
settled
```

Evidence includes the current conversation region, message controls, stop/generating controls, tertiary thinking/tool rows, and exact transient error text such as `Connection interrupted`, `Waiting for the complete answer`, `Could not load this ChatGPT conversation`, and `Unable to load history`.

This status is **observation, not archive truth**. A transient `Could not load...` browser render cannot negate a canonical graph receipt, and a visible assistant turn cannot by itself promote a mutation to committed.

## Attachments

The preferred path is the browser-native input:

```text
input[aria-label="Attach files"]
```

`--attach` is repeatable and files are selected in one `set_input_files(...)` operation when possible:

```bash
re-gpt --send <thread-id> \
  --attach spec.md \
  --attach data.csv \
  --prompt 'Implement the attached specification using the supplied data.'
```

Pre-submit readiness is DOM-based, not endpoint-based. For each expected filename the active composer must show a filename-associated representation and no active upload/progress state. A remove/delete control is recorded as supporting evidence but is not required when filename rendering and upload completion are clear.

After Send, DOM staging is no longer sufficient. Canonical success requires the new user graph node to contain every expected filename, a compatible MIME type when known, and a file ID or asset pointer. Duplicate basenames must bind to distinct canonical attachment records rather than satisfying multiple expected files with one remote object.

The shared SQLite DB stores:

- `mutation_attachments`: local source path, filename, MIME, size, SHA-256, canonical file ID/pointer/node ID for the mutation operation;
- `conversation_attachments`: graph-wide canonical conversation/message-node attachment metadata and optional local path/hash binding;
- transcript bodies remain only in the canonical conversation/message archive.

The existing asset hydrator continues to download canonical file pointers as binary assets; no internal upload endpoint is synthesized.

## CLI

```bash
re-gpt --send <thread-id> --prompt 'Reply exactly ACK.'
```

By default the browser process returns after submission evidence and `MutationService` performs canonical reconciliation outside the browser lifecycle. Once the canonical user node exists, `committed`/`committed_pending` is success and the message must never be resent.

Use `--follow` to keep reconciling for an assistant child up to `--follow-timeout`. A direct-client rejection stops that direct follow context rather than repeatedly hammering it.

## MCP

```bash
pip install -e '.[browser,mcp]'
re-gpt-mcp
```

Tools:

- `chatgpt_continue(conversation_id, prompt, attachments=[...])`
- `chatgpt_operation_status`
- `chatgpt_reconcile` — sync first, browser read fallback on direct rejection, never resubmits
- `chatgpt_live_status` — provisional browser DOM state only
- `chatgpt_view`
- `chatgpt_list`

MCP calls `MutationService` directly rather than shelling out to the human CLI.

## Legacy direct POST

`tests/test_send_message.py` is now an opt-in diagnostic and is skipped unless:

```bash
REGPT_LEGACY_DIRECT_POST=1 pytest -m legacy_direct_post
```

A 429 in that probe is useful direct-client/CF rejection evidence only; it is not a production DOM failure.

## Remaining live acceptance gate

The structural existing-thread + attachment + browser-read-fallback tranche is implemented in code. The remaining release gate is live execution against an owned authenticated profile:

- repeat text-only ACK on short, medium, and ~700-node threads;
- verify unauthenticated, wrong-profile, pending-composer and busy-composer negatives;
- submit one disposable text/Markdown attachment and verify its canonical file ID/node receipt;
- submit multiple attachments;
- exercise direct-client 429 after browser submission and verify browser same-origin recovery, or durable `DirectClientRejected`/`commit_unknown` if both readers fail;
- exercise `chatgpt_live_status` during thinking/tool activity, streaming, connection interruption, transient load error, and settled states;
- populate hydration, attachment-ready, submit->commit and assistant-completion timing distributions.

Only after this matrix should existing-thread + MCP v1 be called release-complete and graph mutations (new thread, branch, regenerate, edit-and-branch) move onto the critical path.
