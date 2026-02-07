# Docs

## Current status (2026-01-17 11:24 UTC)
- Authenticated `https://chatgpt.com/` tab verified healthy: console only shows preload/extension warnings and recent backend/API requests returned 200s.
- Captured key `localStorage` entries from the live tab (`statsig.session_id.1792610830`, `client-correlated-secret`, `oai/apps/debugSettings`, conversation-history cache keys) to guide MCP/CDP reattachment without spinning up Playwright.

## Next steps
- Implement an MCP-backed session in `re_gpt/sync_chatgpt.py` that locates the live tab, reads cookies/localStorage, and issues CDP commands while keeping the current Playwright/Firefox path as fallback.

## Logs
- Context-sync notes for “Reinstalling Codex in Docker”: see `docs/chat-context-sync-log.md`.

## Setup Guides
- Source install and CLI setup (editable install, offline fallback, token config):
  `docs/source-install.md`.
