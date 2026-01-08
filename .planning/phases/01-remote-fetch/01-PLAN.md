---
phase: 01-remote-fetch
type: execute
domain: cli
---

<objective>
Overview: empower the CLI and automation script to pull authoritative JSON from ChatGPT’s conversation endpoint, even when no SQLite messages exist, so `Fish Spine Symbolism` (and any future chat) can be displayed programmatically.
Purpose: replace the current “metadata-only” deadlock with a remote-fetch primitive that understands `since` markers and feeds the same filters/line helpers we already built.
Output: an updated `SyncChatGPT` fetch API plus CLI/script wiring (with `--remote`/`--store` flags) and supporting tests, documented in a follow-up summary.
</objective>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@re_gpt/sync_chatgpt.py
@re_gpt/cli.py
@scripts/view_conversation.py
@re_gpt/storage.py
@tests/test_cli.py
</context>

<tasks>
<task type="auto">
  <name>Task 1: Add a remote conversation fetch primitive</name>
  <files>re_gpt/sync_chatgpt.py</files>
  <action>Expose a method such as `SyncChatGPT.fetch_conversation(conversation_id, *, since_message_id=None, since_time=None)` that issues an authenticated GET to the internal `conversation/<id>` endpoint and returns the raw mapping. Honor the existing session token, propagate Cloudflare cookies, and allow callers to request deltas via query params or headers so the delta logic (since-message-id/timestamp) lives in one place. Keep the response shape consistent with `extract_ordered_messages` so downstream callers can reuse the same filters. Handle HTTP errors by raising `UnexpectedResponseError` with the server payload.</action>
  <verify>`python scripts/view_conversation.py --cID 695f8c92-f528-8320-9672-e77dc5b00f5c --remote --nostore` prints at least one assistant line and exits 0.</verify>
  <done>Remote fetch returns the conversation mapping even when SQLite and exports are empty, and callers can request only the delta by passing `since_message_id` or `since_time`.</done>
</task>

<task type="auto">
  <name>Task 2: Wire the CLI/script to `--remote` mode</name>
  <files>re_gpt/cli.py, scripts/view_conversation.py</files>
  <action>Add a `--remote` flag that forces a server fetch regardless of local cache and respects the `--store`/`--nostore` matrix (default persists, `--nostore` keeps using `NullConversationStorage`). When `--remote` is provided, call the helper from Task 1 to get the latest conversation, feed it through the existing line filters, and optionally persist/download assets only if `--store` is also specified. Document this behavior near the help text and ensure the interactive CLI prints a reminder when it skipped storage.</action>
  <verify>`python scripts/view_conversation.py --cID 695f8c92-f528-8320-9672-e77dc5b00f5c --remote --lines 1-5` returns the filtered intro lines without touching `chat_exports/` or `.chatgpt_history.sqlite3`.</verify>
  <done>`re_gpt.cli` and `scripts/view_conversation.py` honor `--remote`, `--store`, and `--nostore` combinations, and users can fetch Fish Spine Symbolism entirely from the API.</done>
</task>

<task type="auto">
  <name>Task 3: Cover remote flow with automated tests</name>
  <files>tests/test_cli.py, tests/test_sync_chatgpt.py</files>
  <action>Create targeted unit tests that mock the new `fetch_conversation` helper (and the CLI runnable entry points) to ensure `--remote` toggles the fetch path, respects storage/no-storage modes, and still filters line ranges/since markers. Reuse `MagicMock` to assert the helper receives `conversation_id` from titles or IDs and that the CLI prints the expected notice when storage is disabled.</action>
  <verify>`python -m pytest tests/test_cli.py tests/test_sync_chatgpt.py` passes.</verify>
  <done>Remote flag behavior is asserted, so regressions in the new data path are caught before release.</done>
</task>
</tasks>

<verification>
- Run the new unit tests (`python -m pytest tests/test_cli.py tests/test_sync_chatgpt.py`).
- Execute `scripts/view_conversation.py --cID 695f8c92-f528-8320-9672-e77dc5b00f5c --remote --nostore` with a real session token to confirm the CLI prints the assistant reply and acknowledges storage was skipped.
- Confirm `chat_exports/` and `.chatgpt_history.sqlite3` remain unchanged when `--nostore` is used.
</verification>

<success_criteria>
- The CLI can fetch any conversation directly from the ChatGPT JSON API, regardless of local persistence.
- `--remote` and `--store`/`--nostore` flags behave as described in the requirement matrix.
- Automated tests guard the new fetch flag and storage interactions so future changes stay aligned.
</success_criteria>

<output>
After execution, add `.planning/phases/01-remote-fetch/01-SUMMARY.md` summarizing the implementation status, key verification commands, and any remaining blockers.
</output>
