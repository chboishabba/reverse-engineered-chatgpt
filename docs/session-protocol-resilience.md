# Session lifecycle and protocol-drift resilience

Milestone D1 makes session health and protocol compatibility explicit first-class subsystems rather than implicit transport side effects.

## SessionLifecycle

`SessionLifecycle` reads the persisted browser-session snapshot and reports only redacted structural state:

- overall state: `missing`, `partial`, `ready`, or `stale`;
- snapshot age;
- access-token presence and JWT expiry when locally parseable;
- session-cookie presence and expiry when the browser supplied it;
- sentinel presence/freshness;
- cookie/header **names** only;
- account/device presence booleans and timezone.

Opaque tokens are never guessed stale. They report `unknown_expiry`. A mutation is rejected before dispatch only when persisted expiry metadata proves the saved access token or session cookie stale.

### CLI

```bash
re-gpt-session status
re-gpt-session doctor
re-gpt-session bootstrap --profile-dir ~/.codex/chrome-mcp-profile
re-gpt-session refresh --profile-dir ~/.codex/chrome-mcp-profile
```

`bootstrap` and `refresh` use the lightweight authenticated ChatGPT root context. They do not open a conversation DOM.

### MCP

- `chatgpt_session_status`
- `chatgpt_session_bootstrap`
- `chatgpt_session_refresh`
- `chatgpt_session_doctor`

### Pre-commit boundary

The MutationService compatibility installer runs the stale-session guard before any supported mutation transport is invoked. Missing snapshots and opaque expiry continue normally; provably stale saved credentials raise `StaleSessionError` as a safe pre-commit failure.

## ProtocolDrift

`ProtocolDrift` builds value-free fingerprints from existing redacted `ProtocolObservation` records. A fingerprint contains:

- normalized method/path/bootstrap-stage route triples;
- union of request header names;
- top-level body keys;
- route-scoped value-free body-schema signatures;
- SSE event-key signatures;
- observed attachment lifecycle stages.

It never includes request values, bearer/cookie/sentinel values, transcript text, raw response bodies, or signed upload URLs.

Classification:

- `compatible`: no structural delta;
- `drift_suspected`: additive/non-breaking structural change such as an extra header, route, body key, or SSE signature;
- `incompatible`: a baseline route, body key/schema, or attachment stage disappeared.

### CLI

```bash
re-gpt-protocol fingerprint browser-observations.json
re-gpt-protocol check baseline.json candidate.json
```

`check` exits 0 for compatible, 1 for `drift_suspected`, and 2 for incompatible.

### MCP

- `chatgpt_protocol_fingerprint`
- `chatgpt_protocol_compare`

## Acceptance

Session lifecycle:

```bash
python scripts/accept_session_lifecycle_v1.py \
  --profile-dir ~/.codex/chrome-mcp-profile \
  --browser-mode displayless
```

Protocol drift, using two redacted observation receipts for the same bounded operation:

```bash
python scripts/accept_protocol_drift_v1.py baseline.json candidate.json
```

The protocol acceptance runner also checks that exact self-comparison is `compatible`, a synthetic additive change is `drift_suspected`, and a synthetic contract loss is `incompatible`.
