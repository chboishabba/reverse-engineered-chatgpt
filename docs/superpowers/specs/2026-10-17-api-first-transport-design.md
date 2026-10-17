# API-First Transport Architecture

## Intent

Make curl_cffi HTTP the preferred steady-state transport for exact graph reads and eventually writes, while retaining browser/CDP as protocol oracle, auth/bootstrap recovery, acceptance surface, and emergency fallback. DOM mutation becomes last resort rather than the final architecture.

## Invariants

- Canonical truth remains the validated conversation graph plus SQLite persistence/receipts.
- No adapter is canonical by identity; adapters are acquisition mechanisms.
- A 429/Too Many Requests is a request-context/bot-detection rejection for that adapter, not account-wide throttling or conversation absence.
- Once send evidence exists, never auto-resubmit because reconciliation failed.
- Once canonical `committed` or `complete` exists, later read rejection cannot demote it.
- Protocol observations are redaction-first: never persist cookie/token/device-ID/opaque anti-abuse secret values or raw response bodies.

## Target architecture

```text
CLI / MCP / Python
       |
MutationService
       |
transport broker
  |-- async curl_cffi  (preferred while healthy)
  |-- sync curl_cffi   (sibling/diagnostic/fallback)
  |-- browser fetch    (oracle/fallback/bootstrap)
  `-- DOM mutation     (last-resort writer)
       |
validated graph
       |
ConversationStorage + attachment archive + mutation receipts
```

## Exact graph acquisition broker

Expose one adapter-independent contract:

```python
fetch_exact_graph(conversation_id, ...) -> GraphAcquisitionResult
```

`GraphAcquisitionResult` carries the graph, adapter used, timing, redacted observation, attempted adapters, and rejected adapters. Current priority is `async -> sync -> browser` because the 762-node medium-thread read succeeded through `AsyncChatGPT` while sync and browser same-origin contexts returned the known 429 rejection.

Each adapter receives at most one attempt per acquisition operation after a request-context rejection. Rejected adapters are skipped for the rest of that operation. Every accepted graph goes through the same graph validator and persistence path.

## Protocol observation

Introduce a shared `ProtocolObservation` schema for browser and CFFI traces. It records only:

- transport/adapter and operation;
- sequence index and timing;
- method and normalized path;
- status / response class;
- header names;
- cookie names and domains;
- booleans for Authorization, device-id, route/affinity and sentinel headers;
- bootstrap stage;
- sanitized JSON body key/schema information;
- SSE/event shape metadata without transcript or secret values.

The browser recorder uses Playwright/CDP request/response events around a bounded native action. It does not packet-sniff or MITM TLS.

## Mutation progression

The existing DOM writer remains the safe production mutation owner until an HTTP writer proves canonical success. In parallel, add an opt-in async-CFFI mutation probe that reuses the existing `AsyncConversation` payload/sentinel machinery and is allowed only on explicit sacrificial calls. Promotion requires:

1. send returns accepted transport evidence;
2. broker subsequently reads a validated graph;
3. exact new user node is present;
4. optional assistant child is present for completion;
5. no duplicate prompt is created.

After this receipt is demonstrated repeatedly, the transport selection seam can promote async HTTP to primary while retaining browser/DOM fallback.

## Attachments

For this max-cut, capture browser upload/staging request metadata and canonical graph attachment receipts under the same observation schema. Do not guess or synthesize upload endpoints. HTTP attachment promotion remains gated on an observed, reproducible sequence plus canonical file-ID/pointer binding.

## MCP/CLI impact

MCP and CLI continue to call `MutationService`; they inherit broker behavior. Results standardize `read_adapter`, attempted/rejected adapters, and redacted observations. No duplicate archive/event store is introduced.

## Release gate

This cut is complete when:

- async-first broker unit tests pass, including adapter rejection quarantine and monotonic receipts;
- protocol observation redaction tests prove values/bodies are absent;
- browser recorder can emit sanitized traces from Playwright request events;
- an opt-in async HTTP mutation probe exists behind explicit flags and canonical graph receipt verification;
- existing DOM behavior remains available as fallback;
- live promotion to primary HTTP writes is not claimed without sacrificial-thread receipts.
