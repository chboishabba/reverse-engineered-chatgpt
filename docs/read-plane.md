# Canonical conversation read plane

For an exact ChatGPT conversation ID or conversation URL, canonical truth is the validated conversation graph plus the shared SQLite archive. No transport is canonical by identity.

## Adapter-independent exact graph broker

Mutation reconciliation uses:

```python
from re_gpt.graph_broker import fetch_exact_graph

result = fetch_exact_graph(conversation_id)
```

The current acquisition order is:

```text
async curl_cffi exact GET
  -> accepted: validate graph -> shared persistence/receipt path
  -> rejected/fails: one bounded attempt only
sync curl_cffi exact GET
  -> accepted: same validation/persistence path
  -> rejected/fails: one bounded attempt only
browser same-origin exact GET
  -> accepted: same validation/persistence path
  -> otherwise: commit_unknown / acquisition failure
```

This ordering reflects live evidence from the API-first tranche: async exact reads succeeded on short, medium and long conversations while other request contexts could receive 429s. The broker contract, rather than the adapter name, is stable.

Each successful result carries the validated graph, adapter used, timing, attempted adapters, rejected adapters, and redacted observations. Adapter failures never create a second archive format.

A 429 / `Too Many Requests` is treated as rejection of that request context for the current operation. It does **not** establish account-wide throttling or conversation absence. The rejected adapter is not immediately retried in the same operation, and no post-submission read failure can trigger mutation resubmission.

## Existing-thread API-first phase

Text continuation now has a demonstrated direct async/CFFI transport path in addition to the DOM fallback. Promotion remains receipt-driven:

```text
one async HTTP mutation attempt
  -> submission evidence
  -> exact graph broker
  -> canonical user node
  -> optional final assistant descendant
  -> SQLite mutation receipt
```

Post-dispatch transport failures, HTTP 5xx responses, missing SSE data, and cleanup failures are ambiguous outcomes. They must never be reported as safe precommit failures and must never cause automatic resend or DOM fallback. Explicit 4xx rejection remains distinguishable from an ambiguous dispatched POST.

The synchronous wrapper used by MCP/library entrypoints is running-event-loop aware: async work moves to a worker thread when a caller already owns an event loop rather than nesting `asyncio.run()` on that thread.

## Session snapshot boundary

Authenticated browser-session snapshots are explicit operational state, not an automatic side effect of ordinary DOM mutation or diagnostics.

- ordinary mutation/diagnostic recording does not persist a snapshot;
- explicit session capture may persist one when requested;
- persisted snapshot files are created private (`0600`) from the first write and atomically replace the prior snapshot;
- diagnostic receipts contain structural metadata only and never merge unknown raw fields back into sanitized observations.

## Supported single-conversation operations

The `re-gpt` entrypoint retains exact-ID semantics for:

- `re-gpt --view <UUID>`;
- `re-gpt --download <UUID>`;
- interactive resume through legacy `SyncConversation.fetch_chat()` callers;
- post-write reconciliation through the graph broker;
- direct library callers that still invoke `SyncConversation.fetch_chat()`.

`SyncConversation.fetch_chat()` remains a compatibility surface. Exact-ID failures must not expand into remote catalog pagination.

## Differential diagnostics

Use:

```bash
python -m re_gpt.read_diagnostics <conversation-uuid>
```

to compare exact-read transports. Output is redacted to structural request/response metadata such as adapter, method/path, header and cookie names, status, timing, and response class. Cookie values, bearer tokens, device identifiers, raw response bodies, and arbitrary unknown diagnostic fields are not emitted.

## Structurer pulls

The structurer may select an explicit engine for experiments, but the mutation architecture should consume the broker rather than encode an adapter as canonical. Exact conversation identity must be validated before ingestion.

## Merge/release criterion for this tranche

The proven baseline for PR #41 established async exact reads across short/medium/long threads, canonical async text-write receipts, DOM fallback writes, canonical attachment receipts, and a green local suite. Subsequent hardening changes specifically address exactly-once ambiguity, MCP event-loop compatibility, snapshot safety, diagnostic redaction, attachment cleanup, DOM duplicate-turn selection, strict status parsing, and recorder shutdown races.

The hardening head still requires a fresh full test run and one paced async-write canonical receipt before merge/release is claimed. Do not substitute the earlier baseline receipt for verification of later hardening commits.
