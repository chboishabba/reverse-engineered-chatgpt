# reverse-engineered-chatgpt Interface Contract (Intended)

## Intersections
- Live conversation access layer used by `chat-context-sync` workflows.
- Supplies raw exports and conversation metadata to `chat-export-structurer/`.
- Supports context refresh for suite planning and audit traces.

## Interaction Model
1. Authenticate using a session token.
2. List/view/download conversation data via CLI and Python API.
3. Persist canonical conversation records to `chat-export-structurer/my_archive.sqlite` via direct ingest.
4. Expose metadata for context synchronization tasks.

## Exchange Channels
### Channel A: Auth Ingress
- Input: `CHATGPT_SESSION_TOKEN` or configured token source.
- Output: authenticated session or explicit auth failure.

### Channel B: Conversation Query Ingress
- Input: list/view/download commands with id/title filters.
- Output: conversation metadata and message payloads.

### Channel C: Export Egress
- Output: normalized conversation/message records for canonical archive ingest.
- Consumer: `chat-export-structurer/` and local context pipelines.
- Notes:
  - JSON conversation exports are opt-in (`re_gpt.cli --export-json`).
  - Default workflow is direct DB ingest (no intermediate JSON files).

### Channel D: Sync Metadata Egress
- Output: conversation id, title, timestamps, and latest-message excerpts.
- Consumer: `__CONTEXT/` synchronization and planning references.
