# Accessibility Capture Runbook

This local harness reduces reliance on visual browser interaction by wrapping the repository's existing browser mutation path with structural before/after receipts.

## Run

Use a small, uniquely named, non-sensitive file in an existing test conversation.

```bash
python scripts/capture_accessibility_operation.py attachment-continue \
  <conversation-uuid-or-url> \
  --prompt 'ACCESSIBILITY-ATTACHMENT-PROBE-<unique>' \
  --attach ./probe.txt \
  --output ./attachment-capture.json
```

You may also use `--prompt-file`, repeat `--attach`, and pass the existing browser/profile/CDP, timeout, follow, and database options.

## One-shot behavior

The browser mutation actuator is invoked at most once by each command invocation. If the result becomes uncertain after that invocation, the harness performs read-only reconciliation instead of triggering the mutation again.

Do not manually repeat an uncertain probe until you have inspected the conversation and the generated receipt.

## Receipt contents

The generated JSON contains only share-safe structural evidence: operation metadata, attachment filename/MIME/size/SHA-256, ordered redacted protocol stages, structural graph digests and deltas, canonical node/file identifiers, read-adapter summaries, and error stage/class where applicable.

It deliberately omits message text, arbitrary metadata values, local absolute attachment paths, local file bytes, and raw authenticated state.

Return `attachment-capture.json` for analysis.
