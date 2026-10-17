# Browser-owned mutation service

`re-gpt --send` treats the browser as the mutation owner and the validated ChatGPT graph as the commit authority.

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
  -> validated graph pull
  -> canonical user-node + attachment receipt
  -> ConversationStorage / shared SQLite
```

The historical handcrafted same-origin conversation POST remains diagnostic/research code in `re_gpt.accessibility_browser`. The production write path never tries it first.

## Existing-thread readiness

A thread is mutation-ready only when:

1. the URL is the requested `/c/<uuid>`;
2. `/api/auth/session` exposes an access token;
3. the existing thread has hydrated;
4. `#pending-conversation-input` is absent;
5. a real `.ProseMirror[contenteditable="true"]` editor is visible;
6. the send control exists and is not `aria-busy`.

The pending textarea is a loading-state control and is deliberately excluded from production editor selectors.

## Mutation states

Browser evidence is weaker than canonical commit evidence.

- `failed_precommit`: the DOM mutation was never observed; retry is safe after fixing the cause.
- `attachment_failed_precommit`: native file staging failed before any message submission evidence; retry is safe.
- `submission_observed`: the browser emitted evidence that makes a second trigger unsafe.
- `commit_unknown`: reconciliation did not prove the user node. This includes sync 429/CF failures. **Do not resend.** Reconcile later.
- `committed`: the validated graph contains the exact new user node.
- `committed_pending`: the user node is canonical but the assistant and/or canonical attachment metadata has not settled yet.
- `complete`: the canonical graph contains the user node, all expected attachments (if any), and an assistant child with text.

A sync 429 is a read-plane failure, not evidence that the browser mutation failed. `commit_unknown` is recoverable and intentionally has no completion timestamp.

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

After Send, DOM staging is no longer sufficient. Canonical success requires the new user graph node to contain every expected filename, a compatible MIME type when known, and a file ID or asset pointer.

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

Use `--follow` to keep reconciling for an assistant child up to `--follow-timeout`.

## MCP

```bash
pip install -e '.[browser,mcp]'
re-gpt-mcp
```

Tools:

- `chatgpt_continue(conversation_id, prompt, attachments=[...])`
- `chatgpt_operation_status`
- `chatgpt_reconcile`
- `chatgpt_view`
- `chatgpt_list`

MCP calls `MutationService` directly rather than shelling out to the human CLI.

## Legacy direct POST

`tests/test_send_message.py` is now an opt-in diagnostic and is skipped unless:

```bash
REGPT_LEGACY_DIRECT_POST=1 pytest -m legacy_direct_post
```

A 429 in that probe is useful CF/bot-detection evidence only; it is not a production DOM failure.

## Remaining live acceptance gate

The structural existing-thread + attachment tranche is implemented. The remaining gate is live execution against the owned authenticated profile:

- repeat text-only ACK on short, medium, and ~700-node threads;
- verify unauthenticated, wrong-profile, pending-composer and busy-composer negatives;
- submit one disposable text/Markdown attachment and verify its canonical file ID/node receipt;
- submit multiple attachments;
- exercise sync 429 after browser submission and verify durable `commit_unknown` without a second browser trigger;
- populate hydration, attachment-ready, submit->commit and assistant-completion timing distributions.

Only after this matrix should graph mutations (new thread, branch, regenerate, edit-and-branch) be promoted through the same receipt boundary.
