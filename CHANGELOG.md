# Changelog

## Unreleased

- Added chunked session-token file support:
  - `get_session_token()` can now read `~/.chatgpt_session_new` and stitch
    multiple raw lines into one token before auth bootstrap
  - documented the chunked-token workflow in the README and repo context
- Hardened auth bootstrap for authenticated live fetches:
  - preserve non-empty `__Secure-next-auth.session-token` values when frontend
    responses hand back an empty replacement cookie
  - retry warning-banner-only `api/auth/session` responses with frontend-cookie
    hydration
  - add `client-bootstrap` access-token fallback plus an async sync-bootstrap
    escape hatch
  - add focused regression coverage in
    `tests/test_list_all_conversations.py`
- Added `scripts/pull_to_structurer.py` for high-throughput live fetch + direct
  ingest into `~/.chat_archive.sqlite`, including sync/async benchmarking
  and rate-limited async pulls.
- Added async parity method `AsyncChatGPT.fetch_conversation(...)` and fetch-path
  debug logging (`RE_GPT_DEBUG_FETCH=1`).
- Hardened asset URL extraction from conversation page HTML (escaped/encoded
  backend URLs) and added asset-path debug logging (`RE_GPT_DEBUG_ASSETS=1`).
- Made JSON export opt-in in CLI/storage (`--export-json`); default `--download`
  behavior now persists SQLite cache without writing JSON files.
- Added `docs/source-install.md` with generic local setup guidance:
  editable install, restricted-network/offline fallback (`--no-build-isolation`),
  token wiring for non-interactive CLI commands, and requirements-file
  integration in parent projects.
- Documented the automation-friendly CLI helpers and how they fit into scripting workflows.
- Added metadata inspection (`--inspect`), read-only viewing (`--view`), and export (`--download`) modes to the interactive CLI so tools can request conversation lists, query recorded history, and rest easily accessible.
- Added `scripts/context_sync.sh`, a customizable wrapper that runs the automation helpers in sequence and keeps an `rg` pass handy for exported chats.
- Documented how `scripts/context_sync.sh` maps to `run_noninteractive_view`, `run_inspect_command`, and `handle_download_command`, guiding future hook-ups.
- Added `scripts/list_sync_candidates.py` to compare live conversation metadata against an archive SQLite DB and emit only missing/stale conversation IDs (or table/TSV/JSON output) for targeted `--download` workflows.
- Captured the current authenticated tab status and localStorage keys, added timestamped progress notes to the docs, and recorded the MCP/CDP session reattachment work as a TODO for the next iteration.
- Removed the temporary ROCm Codex reinstall doc and replaced it with a context-sync troubleshooting log for “Reinstalling Codex in Docker” (`docs/chat-context-sync-log.md`).
- Fixed `--inspect` so it reports cached metadata and remote update timestamps again (restoring `run_inspect_command`).
