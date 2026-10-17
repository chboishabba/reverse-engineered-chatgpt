# Accessibility Capture and Differential Harness Design

Date: 2026-10-17
Branch: `maxcut/accessibility-capture-harness-20261017`
Base: `main@66e7f0210a2dc02aa6387aa2be95e7d4840e1402`

## Intent

Build a reusable accessibility-focused research harness that lets a human operator run bounded experiments against their own authenticated ChatGPT session and return redaction-first evidence to the repository. The immediate target is direct attachment support; the same harness should later support new-conversation, regenerate, branch, and edit-and-branch discovery without creating a new one-off workflow for each operation.

The browser is an actuator and protocol oracle. The canonical conversation graph is the semantic authority. Normal product behavior must not change merely because the harness exists.

## Success criteria

A single local command should be able to:

1. capture a canonical graph snapshot before a bounded browser action;
2. perform one known-good browser operation using the existing accessibility relay/mutation machinery;
3. record ordered, redacted `ProtocolObservation` entries during the action;
4. acquire a canonical graph snapshot after the action through `GraphBroker`;
5. compute a redaction-safe structural graph delta;
6. correlate local attachment metadata with canonical remote attachment IDs where applicable;
7. emit one self-contained JSON receipt that can be attached to an issue/PR or pasted back for implementation work;
8. never auto-repeat a mutation after dispatch ambiguity.

For direct attachment experiments, success additionally requires a local file SHA-256, protocol-stage evidence, a committed user node, and a canonical attachment binding on that node.

## Existing machinery to reuse

### Browser actuator

Reuse `AccessibilityBrowserRelay`, `browser_mutation`, `mutation_browser`, and `stage_attachments`. The DOM path is already a known-good accessibility-compatible way to cause actions before their direct API semantics are understood.

### Protocol oracle

Reuse `BrowserProtocolRecorder` and `ProtocolObservation`. The recorder already emits ordered redacted metadata including normalized route, method, response class, header/cookie names, body keys/schema, sentinel/route/device presence flags, SSE shape, sequence index, elapsed time, and inferred bootstrap stage.

The harness must not persist raw authorization values, cookie values, sentinel values, request bodies, response bodies, transcript contents, or local file bytes in the receipt.

### Canonical semantic oracle

Reuse `fetch_exact_graph()` / `GraphBroker` for both pre- and post-action exact graph acquisition. Browser DOM state is not semantic truth.

### Mutation and attachment receipts

Reuse `MutationService`, `MutationOperationStore`, `LocalAttachment`, `match_expected_attachments`, and `AttachmentArchive` where a mutation operation is involved. The harness must preserve the existing exactly-once state machine rather than inventing a second commit classifier.

## Architecture

```text
human operator
    |
    v
accessibility experiment CLI
    |
    +--> exact graph before --------------+
    |                                      |
    +--> bounded browser actuator          |
    |       |                              |
    |       +--> BrowserProtocolRecorder   |
    |                                      |
    +--> exact graph after ----------------+
    |                                      |
    v                                      v
protocol trace                      graph structural delta
    |                                      |
    +------------------+-------------------+
                       v
              experiment receipt JSON
                       |
                       +--> local attachment fingerprint
                       +--> canonical file/user/assistant IDs
                       +--> transport/read metadata
                       +--> mutation state
```

The discovery loop for an unsupported operation is:

```text
known-good browser action
    -> redacted protocol trace
    -> graph before/after delta
    -> infer minimum direct contract
    -> opt-in direct probe
    -> canonical reconciliation
    -> compare semantic outcome
```

The protocol trace is evidence about how an operation was performed. The graph delta is evidence about what the operation means.

## New subsystem: experiment receipt

Add a small value-oriented module, tentatively `re_gpt/experiment_receipt.py`, with data structures/functions for:

- stable graph projection;
- graph snapshot digest;
- structural graph diff;
- protocol-trace normalization;
- experiment receipt assembly;
- JSON serialization.

The receipt should contain only structural/canonical information needed for differential analysis.

Suggested top-level schema:

```json
{
  "schema_version": 1,
  "operation": "attachment_continue",
  "actuator": "browser_dom",
  "conversation_id": "...",
  "started_at": 0,
  "completed_at": 0,
  "local_inputs": {
    "attachments": [
      {
        "filename": "...",
        "mime_type": "...",
        "size": 0,
        "sha256": "..."
      }
    ]
  },
  "protocol_observations": [],
  "graph_before": {
    "digest": "...",
    "node_count": 0,
    "current_node": "..."
  },
  "graph_after": {
    "digest": "...",
    "node_count": 0,
    "current_node": "..."
  },
  "graph_delta": {
    "added_nodes": [],
    "removed_nodes": [],
    "changed_nodes": [],
    "added_edges": [],
    "removed_edges": [],
    "current_node_before": "...",
    "current_node_after": "..."
  },
  "canonical_receipt": {
    "state": "complete",
    "user_node_id": "...",
    "assistant_node_id": "...",
    "attachment_ids": []
  },
  "read_receipts": {
    "before": {},
    "after": {}
  }
}
```

Transcript text should not be copied into the receipt. Node diffs should compare structural fields and content metadata, not raw message parts.

## Graph projection and delta

The graph projection should intentionally be smaller than the raw ChatGPT graph. For each node it should retain only fields useful for semantic analysis:

- node ID;
- parent ID;
- ordered child IDs;
- author role;
- content type;
- message status/end-turn flags;
- message create/update timestamps where available;
- selected non-secret structural metadata keys;
- canonical attachment summaries: file ID, filename, MIME type, size, pointer presence/type;
- whether assistant/user/tool/system authored;
- current-node membership.

Do not retain transcript text, tool payload contents, hidden chain-of-thought text, token values, URLs carrying credentials, or arbitrary metadata blobs.

The first graph-delta implementation should report:

- added/removed node IDs;
- changed projected node IDs and changed field names;
- added/removed parent-child edges;
- current-node change;
- added canonical attachment identities.

This is sufficient for attachment, regenerate, branch, and edit-and-branch research without building a generic deep-diff engine.

## New CLI: bounded capture runner

Add `scripts/capture_accessibility_operation.py` as the human-facing experiment tool.

Initial supported operation:

```text
attachment-continue
```

Required inputs:

- existing conversation selector/UUID;
- prompt or prompt file;
- one or more attachment paths;
- browser profile/CDP options already supported by the repo;
- explicit output path or stdout JSON.

Flow:

1. resolve conversation ID;
2. compute `LocalAttachment` fingerprints;
3. exact-read and project graph before;
4. create one durable `MutationOperationStore` operation;
5. run exactly one browser attachment continuation under `BrowserProtocolRecorder`;
6. reconcile through `MutationService`/`GraphBroker` until a canonical receipt or bounded unknown state is reached;
7. exact-read/project graph after;
8. build graph delta;
9. assemble and emit receipt.

If the browser mutation outcome is ambiguous, the tool must not resend. It should move directly to reconciliation and record the resulting canonical/unknown state.

The tool should support a `--capture-only`/`--no-direct-probe` style mode by default; no direct HTTP attachment synthesis belongs in this first tranche.

## Protocol-stage extraction

Add helpers that summarize `ProtocolObservation` sequences into operation stages without inventing endpoint semantics. For the attachment experiment, group observations by existing `bootstrap_stage` plus normalized route/method.

A receipt may report evidence such as:

```text
auth_session
attachment_staging
sentinel_requirements
sentinel_finalize
conversation_post
conversation_get
```

If a route cannot be confidently classified, retain its normalized route/method in an `unclassified` group rather than guessing.

The recorder remains value-redacted. The purpose of the first run is to identify the supported file lifecycle shape and returned/reference identifier locations indirectly through structural metadata and the canonical graph, not to dump bearer/session/anti-abuse values.

## Direct attachment probe boundary

The capture harness is designed to produce the evidence needed for a later `AsyncAttachmentProbe`, but the first PR should not synthesize a direct upload protocol before a browser receipt exists.

Once the operator supplies a capture receipt, the follow-up implementation can add an opt-in one-attempt direct attachment probe that:

1. performs only the minimum file lifecycle demonstrated by the capture;
2. uses ordinary authenticated session state already supported by the direct client;
3. never auto-retries an upload or conversation POST after dispatch ambiguity;
4. feeds the resulting message through the existing `MutationService` reconciliation path;
5. treats success as canonical user-node + canonical attachment binding, not merely HTTP success.

## Reuse for graph operations

The same capture runner should later accept:

- `regenerate`;
- `branch`;
- `edit-branch`;
- `new-conversation`.

Each operation supplies only an actuator callback and operation-specific local inputs. The common harness still owns graph-before, recorder, graph-after, projection, delta, and receipt.

This avoids creating separate protocol-capture programs with inconsistent evidence schemas.

## Accessibility boundary

This work exists to make an otherwise browser-bound interface programmatically accessible to the operator. The harness should reduce reliance on visual DOM interaction by discovering stable programmatic paths and preserving an accessibility-compatible DOM fallback.

The design does not require background automation, bulk account operation, credential harvesting, or unbounded mutation loops. Experiments are explicit, local, bounded, and initiated by the authenticated operator.

## Privacy and credential handling

- `ProtocolObservation` remains the only protocol evidence serialized by default.
- Session snapshot persistence stays explicit opt-in and outside normal experiment receipts.
- Receipt JSON must be safe to inspect and share without token/cookie/sentinel values.
- Local attachment paths should not be serialized by default; serialize filename/MIME/size/SHA-256 only.
- Raw graph JSON should not be embedded in receipts; optionally allow separate local raw snapshots behind an explicit debug flag, with a warning that they may contain transcript/private content.
- No capture mode should silently persist authenticated browser state.

## Testing strategy

Unit tests should be written before implementation for:

1. graph projection excludes message text and arbitrary metadata values;
2. graph delta identifies added nodes/edges/current-node changes;
3. attachment delta reports canonical added file IDs;
4. protocol-stage summary preserves order and unclassified observations;
5. receipt serialization excludes supplied secret markers and local absolute paths;
6. ambiguous mutation paths never invoke the actuator twice;
7. capture runner integrates mocked graph-before/browser-actuator/graph-after into one stable receipt.

Existing mutation, recorder, graph-broker, and attachment tests remain regression coverage.

## Initial implementation cut

The first implementation PR should contain only:

- `re_gpt/experiment_receipt.py`;
- tests for projection/delta/receipt redaction;
- `scripts/capture_accessibility_operation.py` with `attachment-continue`;
- a thin operation adapter around existing browser attachment mutation;
- documentation for the exact command the operator should run and the JSON receipt to return.

It should not yet add direct attachment HTTP writes. The browser capture receipt produced by this tool is the input to that next max-cut.

## Exit criterion for this phase

This phase is complete when the operator can run one command against a sacrificial existing thread with a uniquely named small file and obtain a redaction-safe receipt that proves:

```text
local file fingerprint
  -> ordered browser protocol stages
  -> one browser mutation attempt
  -> committed canonical user node
  -> canonical ChatGPT attachment ID
  -> assistant descendant (when present)
  -> structural graph delta
```

That receipt is then sufficient to implement the direct attachment probe without another ad-hoc browser-debugging cycle.
