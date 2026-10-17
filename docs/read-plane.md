# Canonical conversation read plane

For an exact ChatGPT conversation ID, canonical local truth is a validated conversation graph plus the shared SQLite archive. No transport is canonical merely because of its adapter name.

## GraphBroker

Mutation reconciliation and direct exact reads use:

```python
from re_gpt.graph_broker import fetch_exact_graph

result = fetch_exact_graph(conversation_id)
```

Default acquisition order:

```text
async curl_cffi exact GET
  -> validate graph
sync curl_cffi exact GET
  -> validate graph
browser same-origin exact GET
  -> validate graph
```

Each adapter is attempted at most once per broker call. The first valid graph wins and flows through the same persistence/receipt path.

A successful broker result includes:

- validated graph;
- adapter used;
- timing;
- attempted adapters;
- rejected adapters;
- redaction-safe protocol observations.

## Request-context rejection

A 429 / `Too Many Requests` is rejection of that concrete request context for the current broker call. It is not treated as proof of conversation absence or account-wide throttling.

A rejected adapter is skipped for the remainder of the current reconciliation loop where appropriate. Read failures after mutation dispatch never cause mutation resubmission.

## Mutation reconciliation

Every supported mutation converges on an exact graph read:

```text
one mutation dispatch
-> exact GraphBroker read
-> operation-specific graph identity check
-> ConversationStorage / AttachmentArchive
-> durable MutationOperationStore state
```

Operation-specific checks include:

- tail continuation — exact prompt/time receipt;
- attachments — expected local file bound to canonical graph attachment;
- new conversation — assigned server conversation ID plus new user/assistant turn;
- branch — new user node beneath exact requested parent;
- regenerate — new assistant variant beneath exact requested user node;
- edit-and-branch — replacement user node beneath the original user's canonical parent.

Reasoning/tool intermediate nodes are traversed when locating the visible assistant descendant.

## Exactly-once interaction with reads

After conversation POST dispatch, failures such as timeout/reset, HTTP 5xx, or HTTP 200 without usable SSE data are ambiguous. The system moves to read/reconciliation only.

An exact-read failure may leave the operation `commit_unknown`, but it must never trigger automatic resend or a DOM fallback mutation.

Explicit pre-commit rejection remains distinguishable and is the only direct-write condition under which ordinary continuation may use configured DOM fallback.

## Session snapshot boundary

Authenticated browser-session snapshots are explicit local operational state:

- ordinary reads/mutations do not persist a new snapshot merely because a profile is configured;
- explicit bootstrap/capture may persist one;
- persisted snapshots are written through private (`0600`) temporary files and atomically replaced;
- redacted diagnostics do not serialize raw cookie/bearer/sentinel values.

## Compatibility reads

`SyncConversation.fetch_chat()` remains a compatibility surface over validated exact-ID fetching. Exact conversation lookup failures do not silently expand into remote catalog search/pagination.

Legacy list/view/download catalog helpers remain available for archive workflows, but mutation reconciliation always uses exact conversation identity.

## Differential diagnostics

Use:

```bash
python -m re_gpt.read_diagnostics <conversation-uuid>
```

The diagnostic output describes structural request/response facts such as adapter, method/path, header/cookie names, status, timing and response class. It excludes secret values and raw transcript bodies.

## Persistence

Accepted graphs are persisted through `ConversationStorage`. Canonical attachments are additionally persisted through `AttachmentArchive`. JSON export is optional; SQLite is the normal local archive.
