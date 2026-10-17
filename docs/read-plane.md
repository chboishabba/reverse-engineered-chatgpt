# Canonical conversation read plane

The canonical object is the **validated conversation graph**, not any particular HTTP client. Exact-ID reads must never expand a failed exact fetch into catalog pagination.

## Mutation reconciliation broker

`MutationService` now acquires exact graphs through one adapter-independent broker:

```text
async exact read
  -> valid graph: persist/certify
  -> rejected or failed: try next adapter
sync exact read
  -> valid graph: persist/certify
  -> rejected or failed: try next adapter
browser exact read
  -> valid graph: persist/certify
  -> otherwise: commit_unknown
```

The current order is async -> sync -> browser because live acceptance evidence showed the medium thread was readable through `AsyncChatGPT` while the other read contexts were rejected. This ordering is an acquisition policy, not a claim that async is intrinsically more canonical.

Every accepted graph passes through the same validator, ordered-message extraction, `ConversationStorage`, attachment archive, and mutation-receipt classifier. Results expose:

```text
read_adapter
attempted_read_adapters
rejected_read_adapters
read_diagnostic
```

A rejected adapter is not retried inside the same active mutation/follow operation. An explicit later reconcile is a fresh acquisition operation and may try the normal order again.

## Exactly-once boundary

Read failure never causes a second mutation submission. Once browser submission evidence exists, recovery is observation and reconciliation only. Likewise, once a canonical `committed`, `committed_pending`, or `complete` receipt exists, a later read failure cannot demote that receipt.

## Supported single-conversation operations

The `re-gpt` entrypoint continues to support:

- `re-gpt --view <UUID>`;
- `re-gpt --download <UUID>`;
- interactive resume through legacy `SyncConversation.fetch_chat()` callers;
- post-write reconciliation through the broker;
- direct library callers that still invoke `SyncConversation.fetch_chat()`.

`SyncConversation.fetch_chat()` remains a compatibility surface for ordinary legacy reads. Mutation reconciliation no longer treats it as the canonical acquisition implementation.

## Differential diagnostics

When read behavior diverges, compare adapters without changing canonical semantics:

```bash
python -m re_gpt.read_diagnostics <conversation-uuid>
```

Diagnostics are operational evidence only. They must not persist credential values or raw response bodies. A transport failure says only that the concrete read attempt failed; it does not negate a graph previously acquired through another adapter.

## Structurer pulls

Structurer tooling may still select a concrete engine explicitly for controlled archival/benchmark work:

```bash
python scripts/pull_to_structurer.py --mode pull --engine sync --ids <uuid>
python scripts/pull_to_structurer.py --mode pull --engine async --ids <uuid>
python scripts/pull_to_structurer.py --mode bench ...
```

That engine choice is separate from the mutation reconciliation broker.
