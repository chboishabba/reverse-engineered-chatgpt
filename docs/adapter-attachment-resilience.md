# Milestone D2: adapter health and attachment robustness

D2 hardens two already-supported product paths without changing mutation commit semantics.

## Read-adapter health and backoff

`GraphBroker` remains the only exact-read acquisition coordinator. Persistent adapter health is deliberately **read-plane only**:

```text
async exact read
  -> success: clear consecutive failures/cooldown, record latency
  -> 429/context rejection: exponential cooldown
  -> other failure: short exponential cooldown
sync exact read
  -> same policy
browser exact read
  -> terminal fallback; never suppressed by direct-adapter cooldown
```

No health record is allowed to schedule, retry, replay or otherwise influence a conversation mutation POST. The existing exactly-once write rule is unchanged.

Health state is stored in SQLite alongside bounded latency samples. The store records:

- success/failure/rejection counts;
- consecutive failures;
- last status/response class;
- last latency and EWMA latency;
- cooldown deadline/remaining time;
- recent empirical p50/p95 latency.

Default/direct `GraphBroker` calls use the canonical SQLite database automatically. Injected/custom adapter fetchers do **not** inherit persisted health unless a caller explicitly supplies `health_db_path`, keeping unit tests and experiments isolated.

Inspect current telemetry with:

```bash
re-gpt-adapters
```

or through MCP with `chatgpt_adapter_health`.

### Backoff policy

For direct exact-read adapters:

- request-context rejection such as HTTP 429: 30 s exponential backoff, capped at 300 s;
- other read failure: 5 s exponential backoff, capped at 60 s;
- successful read: reset consecutive-failure count and cooldown.

Each admitted adapter is still attempted at most once per broker call.

## Attachment matching hardening

Attachment staging remains the proven direct sequence:

```text
POST /backend-api/files
-> signed blob PUT
-> POST /backend-api/files/process_upload_stream
-> one conversation POST
-> exact graph reconciliation
```

D2 strengthens canonical attachment binding after commit. Each local attachment must bind to one unique remote attachment identity. Matching requires:

- exact filename;
- compatible MIME type when the graph exposes MIME;
- exact size when the graph exposes size;
- a remote file ID or canonical pointer;
- no reuse of one remote attachment for two local ordinals.

This specifically closes the duplicate-basename ambiguity while remaining tolerant when the remote graph omits optional MIME or size fields.

## Acceptance runners

### Adapter health

```bash
python scripts/accept_adapter_health_v1.py \
  <CONVERSATION_ID> \
  --profile-dir ~/.codex/chrome-mcp-profile \
  --samples 4
```

The runner performs a synthetic 429/cooldown policy check and several live exact reads, then verifies telemetry was recorded and no adapter was attempted twice in one broker call.

### Attachment matrix

```bash
python scripts/accept_attachment_matrix_v1.py \
  --profile-dir ~/.codex/chrome-mcp-profile \
  --step-delay 15
```

The runner creates one sacrificial conversation and certifies four direct-API continuation cases:

1. multiple files in one turn;
2. duplicate basenames from different local directories;
3. a larger text file (default 512 KiB);
4. a non-text PNG.

Every case must finish in canonical `complete` state and return the expected number of distinct canonical attachment IDs.

## Verification boundary

Implementation branches must not claim D2 complete until the operator has run the focused tests, full pytest suite, live adapter-health acceptance, and live attachment matrix. Runtime quirks should be fixed on the branch before merge.
