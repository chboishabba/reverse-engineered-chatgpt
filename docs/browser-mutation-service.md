# Mutation service: API-first graph writes, browser fallback

`MutationService` is the single supported mutation controller. Transports may submit work, but only a validated exact conversation graph plus SQLite persistence certifies the mutation.

## Current architecture

```text
CLI / interactive CLI / MCP / Python
                 |
                 v
          MutationService
                 |
       +---------+----------+
       |                    |
 async curl_cffi API     browser / DOM
     primary         continuation fallback,
                     bootstrap, oracle, diagnostics
       |                    |
       +---------+----------+
                 v
            GraphBroker
       async -> sync -> browser
                 v
        validated exact graph
                 v
ConversationStorage + AttachmentArchive + durable mutation receipt
```

Direct async support now includes:

- existing-thread continuation;
- attachment staging + continuation;
- new-conversation creation;
- branch from an exact graph node;
- regenerate from an exact user node;
- edit-and-branch from an exact user node.

The browser is no longer required for the ordinary steady-state mutation path.

## Exactly-once boundary

Preparation before mutation dispatch may fail safely. Once the conversation POST may have been dispatched, the operation crosses a one-way boundary: recovery is read/reconciliation only.

For direct writes:

- explicit pre-dispatch/4xx rejection -> `failed_precommit` (or `attachment_failed_precommit`);
- timeout/reset after dispatch -> ambiguous;
- 5xx after dispatch -> ambiguous;
- HTTP 200 without usable SSE data -> ambiguous;
- cleanup/context failure after dispatch -> ambiguous.

An ambiguous direct outcome becomes `commit_unknown`; it must not silently fall back to DOM or resend the mutation.

Ordinary tail continuation may use DOM only after an explicit direct **pre-commit** rejection when fallback is enabled.

## Canonical states

- `failed_precommit` — no conversation commit was dispatched/proven; the cause may be corrected before a genuinely new attempt.
- `attachment_failed_precommit` — file staging failed before conversation POST.
- `submission_observed` — transport dispatch succeeded enough that resending is unsafe.
- `commit_unknown` — canonical graph proof is unavailable; never auto-resend.
- `committed` — exact graph contains the intended user mutation.
- `committed_pending` — commit is canonical, but assistant completion or attachment binding is not complete yet.
- `complete` — canonical graph contains the required user/assistant result and expected attachment binding.

## Existing-thread continuation

The product default is direct async continuation. Attachment continuation stages files through the supported observed sequence:

```text
POST /backend-api/files
-> signed blob PUT
-> POST /backend-api/files/process_upload_stream
-> POST /backend-api/f/conversation
-> GraphBroker exact read
-> canonical user/assistant/file binding
```

Signed blob URLs are not persisted in protocol observations.

## New conversation

A new conversation starts without a conversation ID. The direct `next` request omits `conversation_id` and uses the client root parent. SSE identity provides the assigned server conversation ID; the service then exact-reads and persists that graph before returning a canonical receipt.

If the POST succeeds but no conversation ID can be recovered, the operation is ambiguous and is not retried.

## Branch

`branch_from_node(conversation_id, parent_node_id, prompt)` performs a direct `next` action from the explicitly supplied canonical parent node. `MutationService` exact-reads the graph first and refuses a target node that does not exist.

The resulting user node is accepted only when it reconciles beneath that parent. This avoids transcript-position or rendered-DOM heuristics.

## Regenerate

`regenerate(conversation_id, user_node_id)` uses the conversation protocol `variant` action. The target is an exact canonical **user** node; no replacement user message is sent.

Completion requires a new assistant variant/descendant for that user turn. When SSE supplies an assistant message ID it is used as the strongest immediate identity; graph-time reconciliation remains the durable fallback.

## Edit and branch

`edit_and_branch(conversation_id, user_node_id, replacement)` first exact-reads the original user node and obtains its canonical parent. The replacement is then sent as a new `next` user message from that parent, creating a sibling branch rather than mutating historical SQLite text in place.

## Graph reconciliation

`GraphBroker` currently tries:

```text
async -> sync -> browser
```

Each adapter is an acquisition mechanism only. The first validated graph wins. Reasoning/tool intermediate nodes are traversed from a canonical user node to the visible assistant descendant before declaring completion.

Graph mutations additionally constrain reconciliation by their graph coordinate:

- branch/edit: expected parent node;
- regenerate: exact target user node;
- new conversation: assigned conversation ID plus new user turn.

## Legacy compatibility

`SyncConversation.chat()` is installed as a compatibility adapter over `MutationService`. The interactive CLI therefore no longer owns a separate direct `SyncConversation.send_message()` mutation path in normal package use.

Because `MutationService` persists the exact graph itself, the compatibility adapter suppresses the immediately following legacy optimistic `ConversationStorage.append_message()` calls when they duplicate the canonical rows just persisted.

## MCP and CLI

MCP mutation tools and command-line graph operations call `MutationService` directly. The supported mutation surface is documented in [user-guide.md](user-guide.md).

The top-level continuation CLI defaults to:

```text
write_transport=async
dom_fallback=true
```

DOM fallback can be disabled explicitly.

## Browser role

The browser remains useful and intentional for:

- login/session bootstrap;
- redaction-first protocol observation;
- accessibility-compatible pre-commit continuation fallback;
- browser exact-read fallback;
- diagnostics and protocol-drift investigation;
- acceptance testing.

Using a browser profile does not itself make browser/DOM the primary mutation transport.

## Privacy / observations

Shareable protocol observations preserve structural evidence such as normalized path, method, status, payload key/schema shape, stage, sequence and timing. They must not retain bearer/cookie/sentinel values, signed upload URLs, arbitrary raw bodies, transcript contents, or local absolute attachment paths.
