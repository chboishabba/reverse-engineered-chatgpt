# Accessibility Capture Runbook

The accessibility capture harness is the protocol-oracle path used when a browser operation needs to be measured structurally before it is promoted to the direct API client. It wraps one bounded known-good browser mutation with redacted protocol observations plus canonical graph-before/after receipts.

Direct attachment staging has already graduated from this discovery flow into the normal async product path. Keep this tool for regression evidence, protocol drift, and future operations whose current wire contract is not yet established.

## Example: attachment regression capture

Use a small, uniquely named, non-sensitive file in a sacrificial existing conversation:

```bash
python scripts/capture_accessibility_operation.py attachment-continue \
  <conversation-uuid-or-url> \
  --prompt 'ACCESSIBILITY-ATTACHMENT-PROBE-<unique>' \
  --attach ./probe.txt \
  --output ./attachment-capture.json
```

This command deliberately uses the browser actuator because its purpose is to compare the browser's known-good behavior with the direct protocol implementation. For normal attachment sends, use the API-first `MutationService` / CLI / MCP path instead.

## One-shot behavior

The browser mutation actuator is invoked at most once per capture. If the outcome becomes uncertain, the harness switches to read-only reconciliation and never triggers the mutation again.

Do not manually repeat an uncertain probe until you have checked the generated receipt and exact conversation graph.

## Receipt contents

The shareable JSON contains structural evidence only:

- operation/actuator metadata;
- attachment filename, MIME type, size and SHA-256;
- ordered redacted protocol stages;
- structural graph digests and before/after delta;
- canonical conversation/user/assistant/file identifiers;
- exact-read adapter summaries;
- error stage/class where applicable.

It deliberately excludes:

- message/transcript text;
- arbitrary metadata values;
- local absolute attachment paths;
- local file bytes;
- raw bearer/cookie/sentinel values;
- signed upload URLs;
- raw authenticated browser state.

## Direct attachment sequence now supported

The capture evidence was used to promote the normal attachment pipeline to:

```text
POST /backend-api/files
-> signed blob PUT
-> POST /backend-api/files/process_upload_stream
-> POST /backend-api/f/conversation
-> exact graph reconciliation
```

Protocol observations normalize away signed upload URLs/query strings. Canonical success requires the expected file to appear on the committed user node.

## Future discovery loop

For a not-yet-understood operation:

```text
known-good browser action
-> BrowserProtocolRecorder
-> exact graph before/after delta
-> infer minimum direct contract
-> one-attempt direct implementation
-> canonical graph reconciliation
-> semantic comparison
```

The browser trace explains **how** the web application performed the action. The graph delta explains **what the action means**. The graph remains the semantic authority.
