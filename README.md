# reverse-engineered-chatgpt

Accessibility-oriented, API-first access to the authenticated ChatGPT web account you already control.

The project now treats the browser as **fallback/oracle/bootstrap infrastructure**, not the normal conversation writer. Existing-thread reads, text continuation, attachment staging, new-conversation creation, and graph-structural mutations run through the direct async `curl_cffi` transport and are certified from the canonical conversation graph before being persisted to SQLite.

> This project targets the ChatGPT web application protocol. It is not the official OpenAI API and does not use an OpenAI API key. Web protocol details can change; the repository therefore keeps browser-based protocol capture and accessibility fallback tooling alongside the direct client.

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
     primary          fallback / login / oracle
       |                    |
       +---------+----------+
                 v
            GraphBroker
       async -> sync -> browser
                 v
        validated exact graph
                 v
       SQLite canonical receipts
```

The exactly-once rule is fundamental: once a conversation POST may have been dispatched, an uncertain outcome is **read/reconciled, never automatically resent**.

## What works

The API-first product surface includes:

- validated exact conversation reads;
- existing-thread text continuation;
- direct attachment upload + message send;
- new-conversation creation with server-assigned conversation ID capture;
- branch from an exact canonical graph node;
- regenerate from an exact user node;
- edit-and-branch from an exact user node;
- canonical user/assistant/attachment reconciliation;
- durable operation state and conversation/message/attachment persistence in SQLite;
- native MCP tools;
- API-first command-line mutations;
- legacy interactive `SyncConversation.chat()` compatibility routed through `MutationService`;
- browser login/bootstrap, protocol recording, diagnostics and safe pre-commit continuation fallback.

## Install

### Source checkout

```bash
git clone https://github.com/chboishabba/reverse-engineered-chatgpt.git
cd reverse-engineered-chatgpt
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

For source/offline details, see [`docs/source-install.md`](docs/source-install.md).

### Tests

```bash
pytest -q
```

or use the repository helper:

```bash
./scripts/run_tests.sh
```

## Authentication

The client uses authenticated ChatGPT web-session state. Existing configuration/session-token sources remain supported, and a browser-authenticated profile can be used for login/bootstrap when necessary.

Normal reads and writes do **not** need to load the conversation DOM once usable authenticated session state exists.

Browser session snapshots are local operational state. Shareable protocol/experiment receipts are deliberately redacted and do not include raw bearer tokens, cookie values, sentinel values, signed blob URLs, transcript bodies, or local attachment paths.

## Quick start

### Continue an existing conversation

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Reply with only ACK' \
  --follow
```

The public CLI defaults to direct async writes and allows DOM fallback only after an explicit **pre-commit** rejection. To require no DOM fallback:

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Reply with only ACK' \
  --no-dom-fallback \
  --follow
```

### Continue with attachments

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Read the attachment and reply with only ACK' \
  --attach ./probe.txt \
  --follow
```

The direct attachment sequence is:

```text
POST /backend-api/files
 -> signed blob PUT
 -> POST /backend-api/files/process_upload_stream
 -> POST /backend-api/f/conversation
 -> exact graph reconciliation
```

Attachment success is not certified merely because upload HTTP calls succeeded; the expected attachment must bind to the committed canonical user node.

### Create a new conversation

```bash
python -m re_gpt.entrypoint \
  --new \
  --prompt 'Reply with only NEW-ACK' \
  --follow
```

The assigned `conversation_id` is captured from the direct response and then validated by an exact graph read before the result is considered canonical.

### Branch from an exact node

```bash
python -m re_gpt.entrypoint \
  --branch <CONVERSATION_ID> \
  --parent-node <NODE_ID> \
  --prompt 'Alternative branch prompt' \
  --follow
```

### Regenerate

```bash
python -m re_gpt.entrypoint \
  --regenerate <CONVERSATION_ID> \
  --user-node <USER_NODE_ID> \
  --follow
```

Regenerate uses the conversation protocol's `variant` action and targets the exact canonical user node. It does not synthesize a duplicate user message.

### Edit and branch

```bash
python -m re_gpt.entrypoint \
  --edit-branch <CONVERSATION_ID> \
  --user-node <USER_NODE_ID> \
  --prompt 'Replacement text' \
  --follow
```

The service preflights the original user node, obtains its canonical parent, and creates the replacement as a sibling branch.

## Python API

Use `MutationService` for supported writes:

```python
from re_gpt.mutation_service import MutationService

service = MutationService()

created = service.new_conversation("Reply with only ACK", follow=True)
conversation_id = created["conversation_id"]

continued = service.continue_thread(
    conversation_id,
    "Reply with only SECOND",
    write_transport="async",
    dom_fallback=True,
    follow=True,
)

branched = service.branch_from_node(
    conversation_id,
    parent_node_id="<NODE_ID>",
    prompt="Alternative branch",
    follow=True,
)

regenerated = service.regenerate(
    conversation_id,
    user_node_id="<USER_NODE_ID>",
    follow=True,
)

edited = service.edit_and_branch(
    conversation_id,
    user_node_id="<USER_NODE_ID>",
    prompt="Replacement text",
    follow=True,
)
```

Legacy `SyncConversation.chat()` remains available for compatibility, but it is now an adapter over `MutationService`, not a separate mutation implementation.

## Interactive CLI

```bash
python -m re_gpt.cli
```

The existing conversation browser/view/download experience is retained. Its actual chat mutation path is installed through the same API-first `MutationService` compatibility layer used elsewhere.

## MCP

```bash
python -m re_gpt.mcp_server
```

Mutation tools:

- `chatgpt_continue`
- `chatgpt_new`
- `chatgpt_branch`
- `chatgpt_regenerate`
- `chatgpt_edit_branch`
- `chatgpt_operation_status`
- `chatgpt_reconcile` for explicit existing-tail reconciliation

Read/diagnostic tools include `chatgpt_view`, `chatgpt_list`, and `chatgpt_live_status`.

## Mutation states

`MutationService` writes durable operation records. Important states are:

| State | Meaning |
| --- | --- |
| `complete` | Canonical committed turn and assistant result are observed. |
| `committed` | User mutation is canonical; assistant completion is not yet proven. |
| `committed_pending` | Committed, with assistant/attachment reconciliation still pending. |
| `commit_unknown` | The write may have committed but proof is unavailable. **Do not resend automatically.** |
| `failed_precommit` | Explicit rejection before a conversation POST could commit. |
| `attachment_failed_precommit` | Attachment staging failed before conversation POST. |

## Exact reads

`GraphBroker` tries exact graph acquisition adapters in this order unless an adapter is explicitly skipped:

```text
async -> sync -> browser
```

The first validated graph wins. Browser-rendered state is useful diagnostic evidence, but it is not the canonical mutation receipt.

## Browser and accessibility tooling

The browser remains first-class infrastructure for cases where graphical web state is genuinely required:

- login/session bootstrap;
- protocol discovery and drift diagnosis;
- redaction-first request/response structure capture;
- accessibility-compatible continuation fallback after an explicit pre-commit direct rejection;
- same-origin/browser exact-read fallback;
- acceptance testing.

The steady-state architecture is deliberately not “open ChatGPT and scrape/drive the composer for every call.”

See:

- [`docs/user-guide.md`](docs/user-guide.md) — full user guide and examples;
- [`docs/read-plane.md`](docs/read-plane.md) — exact-read architecture;
- [`docs/browser-mutation-service.md`](docs/browser-mutation-service.md) — mutation/exactly-once architecture;
- [`docs/accessibility-browser.md`](docs/accessibility-browser.md) — browser transport and accessibility role;
- [`docs/accessibility-capture.md`](docs/accessibility-capture.md) — redaction-first protocol capture;
- [`docs/interfaces.md`](docs/interfaces.md) — external interface contract.

## Archive / automation helpers

Existing list/view/download/archive helpers remain available through the legacy CLI and scripts. The default conversation persistence path is SQLite; JSON exports remain opt-in.

Examples:

```bash
python -m re_gpt.cli --list
python -m re_gpt.cli --inspect <CONVERSATION_ID>
python -m re_gpt.cli --view '<CONVERSATION_ID> since last update'
python -m re_gpt.cli --download <CONVERSATION_ID>
```

## Development boundaries

When adding another mutation operation:

1. observe the operation with the redaction-first browser protocol tooling if its current wire semantics are not already established;
2. encode graph coordinates explicitly rather than relying on transcript position or rendered DOM order;
3. perform at most one mutation dispatch;
4. classify 4xx pre-commit rejection separately from post-dispatch ambiguity;
5. reconcile from the exact graph;
6. persist through `MutationService` / `ConversationStorage` / `AttachmentArchive`;
7. expose the operation consistently through Python, CLI and MCP.

## Roadmap

The core transport-discovery milestone is complete. Current work is primarily hardening rather than making basic programmatic use possible:

- broader live acceptance matrix for graph mutations and attachment combinations;
- session expiry/refresh lifecycle and an explicit bootstrap command;
- protocol drift detection;
- adapter health/backoff and latency measurements;
- broader attachment types/sizes and archive provenance;
- continued reduction of legacy direct-client mutation code once compatibility consumers have migrated.

## License

MIT. See [`LICENSE`](LICENSE).
