# State

- current_phase: 01-remote-fetch
- current_focus: MCP/CDP reattachment to an authenticated `chatgpt.com` tab (keep Playwright/Firefox as fallback)
- blockers:
  - Need an MCP-backed session in `re_gpt/sync_chatgpt.py` that can locate the live tab, read cookies/localStorage, and drive commands through CDP.
  - Remote vs storage toggles must stay wired for fallback paths.
- deferred_issues:
  - None
