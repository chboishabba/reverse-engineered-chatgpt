# re-gpt User Guide

`re-gpt` is an accessibility-oriented client for working with the authenticated ChatGPT web account you already control. The normal product path is API-first: exact reads, existing-thread continuation, attachment staging, new-conversation creation, and graph-structural mutations use the direct async `curl_cffi` transport and are certified from the canonical conversation graph.

The browser is retained for login/bootstrap, protocol observation, diagnostics, and a safe **pre-commit-only** fallback for ordinary continuation. It is not the normal writer.

## Architecture

```text
CLI / interactive CLI / MCP / Python
                 |
                 v
          MutationService
                 |
       +---------+----------+
       |                    |
 direct async API       DOM/browser
   (primary)            (fallback/oracle)
       |                    |
       +---------+----------+
                 v
        exact GraphBroker read
                 v
       canonical SQLite receipt
```

The central safety rule is exactly-once mutation handling: after a direct conversation POST may have been dispatched, an uncertain outcome is **reconciled by reading**. It is never automatically resent or converted into a DOM write.

## Install

For a source checkout:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

See [source-install.md](source-install.md) for offline/restricted-network notes.

## Authentication

The client uses the authenticated ChatGPT web session, not an OpenAI API key. Existing token/config sources remain supported. Browser session capture is available when a browser-authenticated bootstrap is needed; session snapshot persistence is explicit operational state and is not included in shareable receipts.

The browser should normally be opened only when authentication/bootstrap requires it. Once usable session state exists, reads and mutations should proceed without loading the conversation DOM.

## Existing conversation: text

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Reply with only ACK' \
  --follow
```

The default is `--write-transport async` with DOM fallback disabled. The
`--no-dom-fallback` flag is therefore redundant for normal automation but can
be retained in scripts as an explicit safety assertion. `--dom-fallback` is
manual diagnostic opt-in only.

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Reply with only ACK' \
  --no-dom-fallback \
  --follow
```

## Existing conversation: attachments

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Read the attached file and reply with only ACK' \
  --attach ./probe.txt \
  --follow
```

The direct attachment path is:

```text
POST /backend-api/files
  -> signed blob PUT
  -> POST /backend-api/files/process_upload_stream
  -> POST /backend-api/f/conversation
  -> exact graph reconciliation
```

Multiple `--attach` flags are supported for continuation. Canonical success requires the committed user node and attachment identity to appear in the exact conversation graph; HTTP upload success alone is not enough.

## Create a new conversation

```bash
python -m re_gpt.entrypoint \
  --new \
  --prompt 'Reply with only NEW-ACK' \
  --follow
```

A new thread starts without a conversation UUID. The direct response supplies the assigned UUID; `MutationService` then exact-reads that new graph, persists it, and returns the canonical `conversation_id`, user node and assistant node.

New-conversation creation currently starts with text. Attach files on the next continuation if required.

## Branch from an exact node

Branching is graph-addressed, not transcript-position based:

```bash
python -m re_gpt.entrypoint \
  --branch <CONVERSATION_ID> \
  --parent-node <NODE_ID> \
  --prompt 'Alternative branch prompt' \
  --follow
```

The supplied node must exist in the exact preflight graph. The new user node is reconciled against that exact parent.

Attachments are supported on `--branch`.

## Regenerate an assistant response

Regenerate is a `variant` mutation targeted at the exact user node whose assistant response should receive another variant:

```bash
python -m re_gpt.entrypoint \
  --regenerate <CONVERSATION_ID> \
  --user-node <USER_NODE_ID> \
  --follow
```

No replacement user message is sent. Success is a new assistant descendant/variant under the requested user turn.

## Edit and branch

Editing a user turn is represented structurally as a new user sibling under the original user's parent:

```bash
python -m re_gpt.entrypoint \
  --edit-branch <CONVERSATION_ID> \
  --user-node <USER_NODE_ID> \
  --prompt 'Replacement user text' \
  --follow
```

The service preflights the original user node, obtains its parent, and sends the replacement as a new `next` branch. Attachments are supported.

## Understanding mutation states

Every mutation receives a durable operation ID and state in SQLite.

- `complete` — canonical user/assistant result has been observed; attachments are bound when expected.
- `committed` — the user mutation is canonical but the final assistant result has not yet been observed.
- `committed_pending` — committed, but assistant completion or expected attachment reconciliation is still pending.
- `commit_unknown` — the write may have happened but canonical proof is not currently available. **Do not resend automatically.** Reconcile/read instead.
- `failed_precommit` — the direct attempt was explicitly rejected before a conversation POST could commit. Ordinary continuation may use the configured DOM fallback here.
- `attachment_failed_precommit` — attachment staging failed before conversation POST.

## Python API

```python
from re_gpt.mutation_service import MutationService

service = MutationService()

new = service.new_conversation("Reply with only ACK", follow=True)
conversation_id = new["conversation_id"]

continued = service.continue_thread(
    conversation_id,
    "Reply with only SECOND",
    write_transport="async",
    dom_fallback=False,
    follow=True,
)

branched = service.branch_from_node(
    conversation_id,
    parent_node_id="<canonical-node-id>",
    prompt="Alternative branch",
    follow=True,
)

regenerated = service.regenerate(
    conversation_id,
    user_node_id="<canonical-user-node-id>",
    follow=True,
)

edited = service.edit_and_branch(
    conversation_id,
    user_node_id="<canonical-user-node-id>",
    prompt="Replacement text",
    follow=True,
)
```

Legacy `SyncConversation.chat()` remains source-compatible but is installed as a compatibility adapter over `MutationService`; it is no longer a separate mutation implementation.

## Interactive CLI

The existing interactive experience remains available:

```bash
python -m re_gpt.cli
```

Conversation listing, selection, viewing and download behavior remain legacy-compatible. The actual `SyncConversation.chat()` mutation call is routed through `MutationService`, so existing-thread messages are API-first with safe fallback, and an interactive new conversation uses direct new-thread creation.

Because `MutationService` persists the canonical graph itself, the compatibility layer suppresses the immediately following optimistic `append_message` calls from the old interactive loop when they duplicate those just-persisted canonical messages.

## MCP

Run the native MCP server:

```bash
python -m re_gpt.mcp_server
```

Mutation tools:

- `chatgpt_continue(conversation_id, prompt, attachments?, follow?)`
- `chatgpt_new(prompt, follow?)`
- `chatgpt_branch(conversation_id, parent_node_id, prompt, attachments?, follow?)`
- `chatgpt_regenerate(conversation_id, user_node_id, follow?)`
- `chatgpt_edit_branch(conversation_id, user_node_id, prompt, attachments?, follow?)`
- `chatgpt_operation_status(operation_id)`
- `chatgpt_reconcile(...)` for existing tail-continuation receipts

Read tools include `chatgpt_view`, `chatgpt_list`, and provisional `chatgpt_live_status`.

## Exact reads and persistence

`GraphBroker` tries validated exact-graph adapters in this order unless explicitly quarantined:

```text
async -> sync -> browser
```

The first valid exact graph wins. Accepted graphs flow through the same `ConversationStorage` / `AttachmentArchive` persistence path. Browser-rendered text is diagnostic evidence, not canonical mutation truth.

## Accessibility browser and protocol recorder

The browser tooling remains important, but its role has changed:

- login/session bootstrap;
- protocol capture for newly discovered product operations;
- accessibility-compatible fallback after an explicit pre-commit continuation rejection;
- diagnostics when exact direct reads fail;
- acceptance/oracle work during protocol drift.

`BrowserProtocolRecorder` emits redacted structural observations rather than raw credentials or transcript bodies. See [accessibility-browser.md](accessibility-browser.md) and [accessibility-capture.md](accessibility-capture.md).

## When an operation is uncertain

If a command reports `commit_unknown` or says the mutation is uncertain:

1. do **not** repeat the prompt automatically;
2. keep the operation ID;
3. exact-read/reconcile the conversation;
4. inspect the canonical graph/SQLite receipt;
5. only initiate a genuinely new mutation after you know whether the first one committed.

This rule is especially important after network resets, timeouts, 5xx responses, or HTTP 200 responses that contain no usable SSE data after POST dispatch.

## Current product boundary

API-first functionality now covers:

- exact existing-thread reads;
- existing-thread text continuation;
- direct attachment staging + continuation;
- new-conversation creation;
- branch from exact node;
- regenerate from exact user node;
- edit-and-branch from exact user node;
- canonical graph reconciliation and SQLite persistence;
- CLI, interactive compatibility, MCP and Python mutation surfaces.

Browser/DOM remains intentionally available as infrastructure rather than the primary product transport.
