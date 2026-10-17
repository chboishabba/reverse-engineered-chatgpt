# Canonical conversation read plane

For an exact ChatGPT conversation ID or conversation URL, the primary supported read path is:

```python
chat = chatgpt.fetch_conversation(conversation_id)
```

where `chatgpt` is `SyncChatGPT`.

This is the validated synchronous fetch path that has successfully retrieved a complete conversation graph for archival. Exact-ID reads must not expand a failed fetch into remote catalog pagination.

For mutation reconciliation there is now one bounded fallback:

```text
sync exact GET
  -> accepted: validate graph -> persist
  -> direct-client rejection (for example HTTP 429 / Too Many Requests)
       -> authenticated browser same-origin exact GET
       -> validate the same graph shape -> persist through the same SQLite path
       -> if browser read also fails: commit_unknown / DirectClientRejected
```

The browser fallback is a structured conversation GET performed inside the authenticated Chrome origin. It is not a DOM transcript scrape and it does not create a second archive format. Both accepted adapters feed the same graph validation, `ConversationStorage`, attachment archive, and mutation-receipt logic.

A direct-client 429 is therefore classified as a request-context/direct-client rejection signal for this application, not as ordinary user-visible rate limiting. After such a rejection, the rejected direct context is not immediately retried in the same follow loop and no mutation is ever resent.

## Supported single-conversation operations

The `re-gpt` entrypoint installs the same invariant for:

- `re-gpt --view <UUID>`;
- `re-gpt --download <UUID>`;
- interactive resume through legacy `SyncConversation.fetch_chat()` callers;
- post-write reconciliation (with browser same-origin fallback on direct-client rejection);
- direct library callers that still invoke `SyncConversation.fetch_chat()`.

`SyncConversation.fetch_chat()` is compatibility surface only. At package import it is rebound to a delegate that calls the parent `SyncChatGPT.fetch_conversation()` and then populates legacy conversation state. It no longer owns a second unchecked HTTP implementation in supported use.

For ordinary exact UUID/URL reads outside mutation reconciliation, a fetch error remains terminal for that operation and is reported as-is. Catalog/title matching remains available for title and alias selectors.

## Differential diagnostics

When read behavior diverges, compare adapters before changing protocol assumptions:

```bash
python -m re_gpt.read_diagnostics <conversation-uuid>
```

The diagnostic probes `sync`, `async`, and authenticated browser same-origin reads and emits only redacted request shape:

```text
adapter
method
route
header names
cookie names/domains
auth-header present yes/no
device-id present yes/no
status
elapsed time
response class
```

It never emits cookie, token, authorization, or device-ID values. Async remains a second read adapter and differential reference implementation; it is not inserted into the production reconciliation chain.

## Structurer pulls

Until the async session/request behavior is understood and shown equivalent, use the synchronous engine for targeted archival pulls:

```bash
python scripts/pull_to_structurer.py \
  --mode pull \
  --engine sync \
  --ids 6acc564d-0314-83ec-8036-1f4d9868c5f3
```

The synchronous structurer engine already calls `SyncChatGPT.fetch_conversation()` directly and verifies payload conversation identity before ingestion.

The async engine remains available for explicit bulk/benchmark experiments:

```bash
python scripts/pull_to_structurer.py --mode bench ...
python scripts/pull_to_structurer.py --engine async ...
```

Do not infer account-wide throttling from an async or legacy-path HTTP response when the validated synchronous path has not been tested for that same operation. The transports have demonstrated different behavior in practice even when the nominal backend URL is the same.
