# Project Context

This file captures the current working context for this repository.

## Current request

- "Ensure docs are correct and up to date."

## Recent progress (2026-01-17 11:24 UTC)

- Confirmed the authenticated https://chatgpt.com/ tab is stable; only preload/extension warnings appear in the console and backend/API requests are returning 200s.
- Captured the live tab's key `localStorage` entries (for example: `statsig.session_id.1792610830`, `client-correlated-secret`, `oai/apps/debugSettings`, and the conversation-history cache keys) to guide MCP/CDP reattachment without spinning up Playwright.

## Test status

- `./scripts/run_tests.sh` should pass with the inline HTML fixture in the asset download tests.
- The repository does not require a `gpt-page-source-raw.txt` fixture file.
