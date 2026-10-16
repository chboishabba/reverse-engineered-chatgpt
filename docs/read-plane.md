# Canonical conversation read plane

For an exact ChatGPT conversation ID or conversation URL, the supported read
path is:

```python
chat = chatgpt.fetch_conversation(conversation_id)
```

where `chatgpt` is `SyncChatGPT`.

This is the validated synchronous fetch path that has successfully retrieved a
complete conversation graph for archival. Exact-ID reads must not expand a
failed fetch into remote catalog pagination.

## Supported single-conversation operations

The `re-gpt` entrypoint installs the same invariant for:

- `re-gpt --view <UUID>`;
- `re-gpt --download <UUID>`;
- interactive resume through legacy `SyncConversation.fetch_chat()` callers;
- post-write reconciliation;
- direct library callers that still invoke `SyncConversation.fetch_chat()`.

`SyncConversation.fetch_chat()` is compatibility surface only. At package
import it is rebound to a delegate that calls the parent
`SyncChatGPT.fetch_conversation()` and then populates legacy conversation state.
It no longer owns a second unchecked HTTP implementation in supported use.

For exact UUID/URL selectors, a fetch error is terminal for that operation and
is reported as-is. Catalog/title matching remains available for title and alias
selectors.

## Structurer pulls

Until the async session/request behavior is understood and shown equivalent,
use the synchronous engine for targeted archival pulls:

```bash
python scripts/pull_to_structurer.py \
  --mode pull \
  --engine sync \
  --ids 6acc564d-0314-83ec-8036-1f4d9868c5f3
```

The synchronous structurer engine already calls
`SyncChatGPT.fetch_conversation()` directly and verifies payload conversation
identity before ingestion.

The async engine remains available for explicit bulk/benchmark experiments:

```bash
python scripts/pull_to_structurer.py --mode bench ...
python scripts/pull_to_structurer.py --engine async ...
```

Do not infer account-wide throttling from an async or legacy-path HTTP response
when the validated synchronous path has not been tested for that same operation.
The transports have demonstrated different behavior in practice even when the
nominal backend URL is the same.
