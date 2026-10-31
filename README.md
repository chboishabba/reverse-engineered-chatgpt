# reverse-engineered-chatgpt

Accessibility-oriented, API-first access to the authenticated ChatGPT web account you already control.

The project treats the browser as **fallback/oracle/bootstrap infrastructure**, not the normal conversation writer. Existing-thread reads, text continuation, attachment staging, new-conversation creation, and graph-structural mutations run through the direct async `curl_cffi` transport and are certified from the canonical conversation graph before being persisted to SQLite.

> [!IMPORTANT]
> **CRITICAL FOR AGENTS & AUTOMATION**: The direct headless async `curl_cffi` transport is the **sole preferred method** for programmatic and agentic interactions. It is **significantly faster and vastly more reliable** than browser or DOM automation. Agents must **NEVER** attach to or hijack the user's active desktop browser (no CDP on port 9222, no puppeteer/playwright against user sessions) and must always pass `--no-dom-fallback` to guarantee pure headless execution.

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

SessionLifecycle ------> pre-commit freshness gate
ProtocolDrift ---------> redacted structural compatibility
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
- explicit session status/bootstrap/refresh/doctor diagnostics;
- pre-commit rejection of persisted credentials whose expiry is locally proven stale;
- redacted structural protocol fingerprints and drift classification;
- browser login/bootstrap, protocol recording, diagnostics and safe pre-commit continuation fallback.

## Install

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

or:

```bash
./scripts/run_tests.sh
```

## Authentication and session lifecycle

The client uses authenticated ChatGPT web-session state. Existing configuration/session-token sources remain supported, and a browser-authenticated profile can be used for login/bootstrap when necessary.

Normal reads and writes do **not** need to load the conversation DOM once usable authenticated session state exists.

Browser session snapshots are local operational state. Shareable protocol/experiment receipts are deliberately redacted and do not include raw bearer tokens, cookie values, sentinel values, signed blob URLs, transcript bodies, or local attachment paths.

Milestone D1 adds explicit lifecycle commands:

```bash
re-gpt-session status
re-gpt-session doctor
re-gpt-session bootstrap --profile-dir ~/.codex/chrome-mcp-profile
re-gpt-session refresh --profile-dir ~/.codex/chrome-mcp-profile
```

Opaque token expiry is reported as `unknown_expiry`, not guessed stale. A mutation is blocked before dispatch only when saved access-token/session-cookie expiry is locally provable as stale.

## Quick start (Headless curl_cffi - Sole Preferred Method)

The direct async `curl_cffi` transport is **sole preferred**: it is significantly faster, lower-overhead, and more reliable than browser/DOM automation. Automated agents should **always** execute with `--no-dom-fallback --write-transport async`.

### Continue an existing conversation (Sole Preferred Method)

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --prompt 'Reply with only ACK' \
  --write-transport async \
  --no-dom-fallback \
  --write-timeout 300 \
  --follow
```
*(Use `--prompt-file <path>` for multiline prompt text. `--write-timeout 300` gives reasoning models sufficient deliberation time before initial chunks).*

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

### Edit and branch

```bash
python -m re_gpt.entrypoint \
  --edit-branch <CONVERSATION_ID> \
  --user-node <USER_NODE_ID> \
  --prompt 'Replacement text' \
  --follow
```

The service preflights the original user node, obtains its canonical parent, and creates the replacement as a sibling branch. Root turns are rejected explicitly because the live API does not accept `action="next"` with `parent_message_id="client-created-root"` on an existing conversation.

## Protocol drift

Build or compare fingerprints from redacted `ProtocolObservation` receipts:

```bash
re-gpt-protocol fingerprint browser-observations.json
re-gpt-protocol check baseline.json candidate.json
```

Fingerprints include only normalized route/stage structure, header names, body keys/value-free body schemas, SSE event-key signatures and attachment lifecycle stages.

Classification:

- `compatible`: no structural difference;
- `drift_suspected`: additive/non-breaking structure appeared;
- `incompatible`: a contract-bearing route/body/schema/auth/SSE/attachment element disappeared.

See [`docs/session-protocol-resilience.md`](docs/session-protocol-resilience.md).

## Python API

Use `MutationService` for supported writes:

```python
from re_gpt.mutation_service import MutationService

service = MutationService()
created = service.new_conversation("Reply with only ACK", follow=True)
conversation_id = created["conversation_id"]
continued = service.continue_thread(conversation_id, "Reply with only SECOND", write_transport="async", dom_fallback=False, follow=True)
branched = service.branch_from_node(conversation_id, parent_node_id="<NODE_ID>", prompt="Alternative branch", follow=True)
regenerated = service.regenerate(conversation_id, user_node_id="<USER_NODE_ID>", follow=True)
edited = service.edit_and_branch(conversation_id, user_node_id="<USER_NODE_ID>", prompt="Replacement text", follow=True)
```

Session and drift APIs:

```python
from re_gpt.session_lifecycle import SessionLifecycle
from re_gpt.protocol_drift import build_protocol_fingerprint, compare_protocol_fingerprints

status = SessionLifecycle().status()
```

Legacy `SyncConversation.chat()` remains available for compatibility, but it is an adapter over `MutationService`, not a separate mutation implementation.

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
- `chatgpt_revive_multi` (explicit 2–10 canonical parent IDs + caller-owned candidate handoff attachment)
- `chatgpt_operation_status`
- `chatgpt_reconcile`

Session/protocol tools:

- `chatgpt_session_status`
- `chatgpt_session_bootstrap` (explicit browser capture)
- `chatgpt_session_refresh` (CFFI-only renewal)
- `chatgpt_session_refresh_cffi` (CFFI-only renewal alias)
- `chatgpt_session_doctor`
- `chatgpt_protocol_fingerprint`
- `chatgpt_protocol_compare`

Read/diagnostic tools include `chatgpt_view`, `chatgpt_list`, and `chatgpt_live_status`.

The multi-parent revival adapter is deliberately separate from the legacy one-parent `revive_thread` transcript-export path. It accepts an explicit handoff artifact, canonical parent IDs and a stable caller-supplied request ID from ITIR, creates one fresh conversation, attaches the artifact once through `MutationService`, and records ordered lineage edges plus the attachment receipt in operational receipt storage. The request ID is claimed before either write; repeating it returns the existing receipt and never dispatches again. It does not select or read corpus transcripts. `commit_unknown` on creation prevents attachment; an uncertain attachment is never automatically resent. The lineage store is adapter evidence, not canonical archive authority.

## Mutation states

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

The browser remains first-class infrastructure for:

- interactive login/session bootstrap (`chatgpt_session_bootstrap`, `cdp_capture`);
- protocol discovery and drift diagnosis;
- redaction-first request/response structure capture;
- accessibility-compatible continuation fallback after an explicit pre-commit direct rejection;
- same-origin/browser exact-read fallback;
- acceptance testing.

Reauthentication of an already-owned session is CFFI-only (`chatgpt_session_refresh`,
`chatgpt_session_refresh_cffi`, and precommit `auto_refresh`); it never opens a
browser or CDP connection.

The steady-state architecture is deliberately not “open ChatGPT and scrape/drive the composer for every call.”

See:

- [`docs/user-guide.md`](docs/user-guide.md)
- [`docs/read-plane.md`](docs/read-plane.md)
- [`docs/browser-mutation-service.md`](docs/browser-mutation-service.md)
- [`docs/session-protocol-resilience.md`](docs/session-protocol-resilience.md)
- [`docs/adapter-attachment-resilience.md`](docs/adapter-attachment-resilience.md)
- [`docs/agent-delegation.md`](docs/agent-delegation.md)
- [`docs/accessibility-browser.md`](docs/accessibility-browser.md)
- [`docs/accessibility-capture.md`](docs/accessibility-capture.md)
- [`docs/interfaces.md`](docs/interfaces.md)

## Archive / automation helpers

Existing list/view/download/archive helpers remain available through the legacy CLI and scripts. The default conversation persistence path is SQLite; JSON exports remain opt-in.

```bash
python -m re_gpt.cli --list
python -m re_gpt.cli --inspect <CONVERSATION_ID>
python -m re_gpt.cli --view '<CONVERSATION_ID> since last update'
python -m re_gpt.cli --download <CONVERSATION_ID>
```

## Development boundaries

When adding another mutation operation:

1. observe its wire semantics with redaction-first browser protocol tooling when needed;
2. encode graph coordinates explicitly rather than relying on transcript position or rendered DOM order;
3. perform at most one mutation dispatch;
4. classify 4xx pre-commit rejection separately from post-dispatch ambiguity;
5. reconcile from the exact graph;
6. persist through `MutationService` / `ConversationStorage` / `AttachmentArchive`;
7. expose the operation consistently through Python, CLI and MCP.

## Acceptance

Graph mutation acceptance remains:

```bash
python scripts/accept_graph_mutations_v1.py
```

D1 acceptance:

```bash
python scripts/accept_session_lifecycle_v1.py --profile-dir ~/.codex/chrome-mcp-profile
python scripts/accept_protocol_drift_v1.py baseline.json candidate.json
```

## Roadmap

Milestones A-C are complete. D1 implements session lifecycle and protocol-drift resilience. Remaining work is primarily:

- broader attachment types/sizes and duplicate-name acceptance;
- adapter health/backoff and latency measurements;
- arbitrary uploaded-file archive provenance/download;
- removal/deprecation of obsolete direct mutation internals once downstream consumers are migrated.

## License

MIT. See [`LICENSE`](LICENSE).
