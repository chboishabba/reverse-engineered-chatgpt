# API-First Transport Architecture

## Intent

Prefer the existing asynchronous curl_cffi reader for exact conversation graph acquisition when it is healthy. Keep sync and browser readers as fallback acquisition mechanisms. Keep the existing browser DOM writer unchanged in this cut.

## Invariants

- Canonical truth is the validated conversation graph plus SQLite receipts.
- Read adapters are interchangeable acquisition mechanisms.
- A rejected read adapter does not imply the conversation is unavailable.
- After submission evidence exists, reconciliation failure never causes an automatic resend.
- A later read failure cannot demote an already committed or complete operation.
- Diagnostics store only non-sensitive operational metadata and never raw response bodies or credential values.

## Architecture

```text
CLI / MCP / Python
       |
MutationService
       |
exact-read broker
  |-- async curl_cffi
  |-- sync curl_cffi
  `-- browser exact read
       |
validated graph
       |
ConversationStorage + attachment archive + mutation receipts

writes: existing exactly-once DOM path remains unchanged
```

## Exact graph acquisition broker

Expose one adapter-independent contract:

```python
fetch_exact_graph(conversation_id, ...) -> GraphAcquisitionResult
```

The result carries the graph, adapter used, timing, attempted adapters, rejected adapters, and redacted operational diagnostics. The current order is `async -> sync -> browser` based on the supplied live evidence that async exact reading succeeds on the medium thread.

An adapter is attempted at most once in one broker acquisition call. Rejected adapters can be carried forward by `MutationService` and skipped for the rest of the active mutation/follow operation. Every accepted graph flows through the same validation and persistence path.

## Operational diagnostics

A shared observation shape may record adapter, operation, sequence, timing, method, normalized path, response status/class, and body key/type schema for client-owned generated JSON. It must not persist raw bodies, transcript text, credential values, cookie values, or other private session data.

## Mutation and attachments

The browser DOM writer remains the production writer in this cut. Native browser attachment staging and canonical attachment receipts remain unchanged. No new direct HTTP writer or upload protocol is introduced.

## MCP/CLI impact

MCP and CLI continue to call `MutationService` and inherit the broker. Results standardize `read_adapter`, `attempted_read_adapters`, and `rejected_read_adapters`. No duplicate archive is introduced.

## Release gate

- async-first broker tests cover success, fallback, rejection quarantine, invalid graphs, and all-adapter failure;
- `MutationService` persists whichever accepted graph the broker returns;
- canonical receipts survive later read failures;
- operational diagnostics remain redacted;
- existing DOM mutation and attachment behavior remains available.
