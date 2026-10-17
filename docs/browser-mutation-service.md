# Browser-owned and API-first mutation service

`re-gpt --send` treats a validated ChatGPT conversation graph as the commit authority. The steady-state architectural target is API-first (`curl_cffi` HTTP for exact reads and eventually writes), with browser/CDP as the protocol oracle, auth/bootstrap recovery, and fallback, and DOM mutation as the safe production writer until HTTP writes earn repeated canonical graph receipts.

## Production and probe paths

```text
CLI / MCP
  -> MutationService
  -> write transport
       -> default production: authenticated browser DOM composer + native file input
       -> opt-in probe (--write-transport async): single-attempt AsyncChatGPT HTTP POST
  -> submission evidence + redacted ProtocolObservation trace
  -> reconciliation read via exact-graph broker (async -> sync -> browser)
  -> shared graph validation
  -> canonical user-node + attachment receipt
  -> ConversationStorage / shared SQLite
```

The historical handcrafted same-origin conversation POST remains diagnostic/research code in `re_gpt.accessibility_browser`. The browser read fallback retrieves structured conversation JSON (`fetch('/backend-api/conversation/<id>')`); it is not a second mutation transport and not a rendered-DOM transcript scrape.

## Existing-thread readiness

A thread is mutation-ready only when:

1. the URL is the requested `/c/<uuid>`;
2. `/api/auth/session` exposes an access token;
3. the existing thread has hydrated;
4. `#pending-conversation-input` is absent;
5. a real `.ProseMirror[contenteditable="true"]` editor is visible.

The empty composer does not have to expose a Send control before text exists: current ChatGPT UI may expose Start Voice until insertion. Send discovery and enabled-state validation remain owned by the post-insertion submission path.

The pending textarea is a loading-state control and is deliberately excluded from production editor selectors. Modern rendered-history evidence includes the conversation region and message controls such as `Copy message` / `Edit message` in addition to older message-author attributes.

## Mutation states

Browser or HTTP submission evidence is weaker than canonical commit evidence.

- `failed_precommit`: the mutation was never dispatched or was explicitly rejected before commit; retry is safe after fixing the cause.
- `attachment_failed_precommit`: native file staging failed before any message submission evidence; retry is safe.
- `submission_observed`: the transport emitted evidence that makes a second trigger unsafe.
- `commit_unknown`: reconciliation or an ambiguous HTTP POST did not prove the user node. **Do not resend.** Reconcile later.
- `committed`: a validated graph contains the exact new user node.
- `committed_pending`: the user node is canonical but the assistant and/or canonical attachment metadata has not settled yet.
- `complete`: the canonical graph contains the user node, all expected attachments (if any), and an assistant child with text.

A 429 / `Too Many Requests` on any read adapter is recorded as a request-context rejection for that adapter (`RequestContextRejected`). The broker quarantines that adapter for the remainder of the active operation and advances through `async -> sync -> browser`. If all adapters fail, the durable result remains recoverable `commit_unknown`. No reconciliation failure or ambiguous HTTP write ever triggers a second submission or DOM fallback.

## Exact-graph broker and browser read fallback

`re_gpt.graph_broker.fetch_exact_graph` tries `async -> sync -> browser`. The browser fallback executes inside the authenticated Chrome origin:

```text
GET /api/auth/session
  -> access token
GET /backend-api/conversation/<id>
  Authorization: Bearer ...
  Accept: application/json
```

No `Content-Type` header is synthesized for the bodyless GET. The response must be a JSON object with a non-empty `mapping`; only then does it enter the same persistence path used by all readers.

The fallback can use the same launch-owned profile or the same CDP-attached browser context as mutation. CDP remains non-owning: re-gpt disconnects Playwright rather than closing the user's browser.

## Redacted protocol observations and browser session/fingerprint replay

Both CFFI and browser/CDP paths emit `re_gpt.protocol_observation.ProtocolObservation` records containing only transport, operation, sequence index, method, normalized path, status, response class, header names, cookie names/domains, auth/device/route/sentinel presence booleans, sanitized JSON payload key/schema trees, and SSE event counts. Secret values and transcript/response bodies are never emitted in diagnostic traces.

For offline session analysis and CFFI reuse without loading thread DOM, `re_gpt.browser_session_replay` records and replays a local `BrowserSessionSnapshot` (`<profile_dir>/.re_gpt_session_snapshot.json`, `0600` permissions) capturing:

- full browser cookie jar (`__Secure-next-auth.session-token`, `oai-did`, `oai-sc`, `__cf_bm`, `_cfuvid`, etc.);
- bearer `accessToken` and `chatgpt-account-id` from `/api/auth/session`;
- Client-Hints (`sec-ch-ua*`), `User-Agent`, `Accept-Language`, and contextual navigator/screen/timezone fingerprint;
- single-use pre-minted sentinel token trio (`openai-sentinel-chat-requirements-token`, `openai-sentinel-proof-token`, `openai-sentinel-turnstile-token`) from `/backend-api/sentinel/chat-requirements/finalize`.

`AsyncChatGPT`, `SyncChatGPT`, `fetch_exact_graph`, and `submit_async_prompt_once` automatically replay the cached `BrowserSessionSnapshot` when available, refreshing single-use sentinel tokens via a lightweight root-page bootstrap (`https://chatgpt.com/`, no thread DOM) when needed for `POST /backend-api/f/conversation`.

Use:

```bash
python -m re_gpt.read_diagnostics <conversation-uuid>
python -m re_gpt.browser_protocol_recorder <conversation-uuid> --read
python -m re_gpt.browser_protocol_recorder <conversation-uuid> --session --profile-dir ./chatgpt_browser_profile
python -m re_gpt.async_mutation_probe <conversation-uuid> --prompt 'Reply exactly ACK.' --sacrificial --profile-dir ./chatgpt_browser_profile
```

to compare `sync`, `async`, and browser same-origin exact-thread reads, record bounded browser request sequences, capture/refresh the local session snapshot, or test one-attempt async HTTP writes against a sacrificial thread.

## Live DOM status

`re_gpt.browser_observer` also exposes provisional rendered-state observation. The classifier distinguishes states such as:

```text
hydrating
ready
thinking
tool_activity
streaming
connection_interrupted
conversation_load_error
history_load_error
settled
```

Evidence includes the current conversation region, message controls, stop/generating controls, tertiary thinking/tool rows, and exact transient error text such as `Connection interrupted`, `Waiting for the complete answer`, `Could not load this ChatGPT conversation`, and `Unable to load history`.

This status is **observation, not archive truth**. A transient `Could not load...` browser render cannot negate a canonical graph receipt, and a visible assistant turn cannot by itself promote a mutation to committed.

## Attachments

The preferred path is the browser-native input:

```text
input[aria-label="Attach files"]
```

`--attach` is repeatable and files are selected in one `set_input_files(...)` operation when possible:

```bash
re-gpt --send <thread-id> \
  --attach spec.md \
  --attach data.csv \
  --prompt 'Implement the attached specification using the supplied data.'
```

Pre-submit readiness is DOM-based, not endpoint-based. For each expected filename the active composer must show a filename-associated representation and no active upload/progress state. A remove/delete control is recorded as supporting evidence but is not required when filename rendering and upload completion are clear.

After Send, DOM staging is no longer sufficient. Canonical success requires the new user graph node to contain every expected filename, a compatible MIME type when known, and a file ID or asset pointer. Duplicate basenames must bind to distinct canonical attachment records rather than satisfying multiple expected files with one remote object.

The shared SQLite DB stores:

- `mutation_attachments`: local source path, filename, MIME, size, SHA-256, canonical file ID/pointer/node ID for the mutation operation;
- `conversation_attachments`: graph-wide canonical conversation/message-node attachment metadata and optional local path/hash binding;
- transcript bodies remain only in the canonical conversation/message archive.

The existing asset hydrator continues to download canonical file pointers as binary assets; no internal upload endpoint is synthesized.

## CLI

```bash
re-gpt --send <thread-id> --prompt 'Reply exactly ACK.'
```

By default the browser process returns after submission evidence and `MutationService` performs canonical reconciliation outside the browser lifecycle. Once the canonical user node exists, `committed`/`committed_pending` is success and the message must never be resent.

Use `--follow` to keep reconciling for an assistant child up to `--follow-timeout`. A direct-client rejection stops that direct follow context rather than repeatedly hammering it.

## MCP

```bash
pip install -e '.[browser,mcp]'
re-gpt-mcp
```

Tools:

- `chatgpt_continue(conversation_id, prompt, attachments=[...])`
- `chatgpt_operation_status`
- `chatgpt_reconcile` — sync first, browser read fallback on direct rejection, never resubmits
- `chatgpt_live_status` — provisional browser DOM state only
- `chatgpt_view`
- `chatgpt_list`

MCP calls `MutationService` directly rather than shelling out to the human CLI.

## Legacy direct POST

`tests/test_send_message.py` is now an opt-in diagnostic and is skipped unless:

```bash
REGPT_LEGACY_DIRECT_POST=1 pytest -m legacy_direct_post
```

A 429 in that probe is useful direct-client/CF rejection evidence only; it is not a production DOM failure.

## Remaining live acceptance gate

The structural existing-thread + attachment + browser-read-fallback tranche is implemented in code. The remaining release gate is live execution against an owned authenticated profile:

- repeat text-only ACK on short, medium, and ~700-node threads;
- verify unauthenticated, wrong-profile, pending-composer and busy-composer negatives;
- submit one disposable text/Markdown attachment and verify its canonical file ID/node receipt;
- submit multiple attachments;
- exercise direct-client 429 after browser submission and verify browser same-origin recovery, or durable `DirectClientRejected`/`commit_unknown` if both readers fail;
- exercise `chatgpt_live_status` during thinking/tool activity, streaming, connection interruption, transient load error, and settled states;
- populate hydration, attachment-ready, submit->commit and assistant-completion timing distributions.

Only after this matrix should existing-thread + MCP v1 be called release-complete and graph mutations (new thread, branch, regenerate, edit-and-branch) move onto the critical path.
