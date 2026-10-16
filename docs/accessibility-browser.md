# Accessibility-first browser relay

`re-gpt-browser` is the browser-owned write path for environments where the
normal ChatGPT page is impractical or impossible to operate visually. It keeps
ChatGPT authentication and mutation state inside the browser and emits the
assistant reply as terminal text.

## Preferred when Chrome is already running: CDP attach

If an authenticated Chrome/Chromium instance is already running with a local
DevTools endpoint, the relay can attach instead of launching another process:

```bash
re-gpt --send 6a33ae58-cb84-83ec-b187-ddab3179ccbb \
  --cdp-endpoint http://127.0.0.1:9222 \
  --prompt 'Reply exactly ACK.'
```

or directly:

```bash
re-gpt-browser 6a33ae58-cb84-83ec-b187-ddab3179ccbb \
  --cdp-endpoint http://127.0.0.1:9222 \
  --prompt 'Reply exactly ACK.'
```

CDP attach is deliberately non-owning: the relay does not clear profile locks,
does not launch Chrome, and does not close the attached browser or its browser
contexts when it exits. It prefers an already-open `chatgpt.com` page and opens
a page in the existing context only when none exists.

Keep the debugging endpoint bound to loopback. Chrome must itself be configured
to accept the local DevTools client; a browser rejecting the CDP WebSocket is a
browser-launch/configuration issue, not a signal to replay cookies outside the
browser.

## Default launch mode: displayless Ozone Chrome

When no CDP endpoint is supplied, Linux defaults to:

```text
--browser-mode displayless
```

This launches normal installed Chrome with Playwright `headless=False`, but asks
Chromium to use its Ozone headless **display backend**:

```text
--ozone-platform=headless
--ozone-override-screen-size=1440,960
```

The browser therefore does not require X11, Wayland, a virtual framebuffer, or
a physical monitor. This is intentionally different from Chrome's `--headless`
mode. The relay does not add flags intended to conceal automation signals.

Example:

```bash
printf '%s\n' 'Reply exactly ACK.' | \
  re-gpt-browser 6a33ae58-cb84-83ec-b187-ddab3179ccbb \
  --profile-dir ~/.codex/chrome-mcp-profile
```

A launched persistent profile must not already be owned by another Chrome
process. If it is, use `--cdp-endpoint` against that running browser instead of
trying to launch the same profile twice.

## Other launch modes

Modern Playwright unified Chromium headless remains available explicitly:

```bash
re-gpt-browser <conversation> --browser-mode headless --prompt 'hello'
```

This mode uses Playwright `channel="chromium"`. Install that browser build if
needed:

```bash
playwright install --no-shell chromium
```

A normal Chrome window remains available for compatibility/debugging:

```bash
re-gpt-browser <conversation> --browser-mode headed --prompt 'hello'
```

The legacy `--headed` flag is retained as an alias for that mode.

## Mutation transport selection

```text
--transport auto   API first, DOM fallback (default)
--transport api    same-origin browser fetch only
--transport dom    composer DOM only
```

This ordering is the same for launched and CDP-attached browsers. Use `--json`
when a caller needs structured fields such as the resolved thread ID, selected
browser mode, transport, or fallback transport.

## Read/write split

Browser ownership is for mutation. Canonical single-conversation reads and
post-write reconciliation use the validated synchronous read path,
`SyncChatGPT.fetch_conversation(conversation_id)`, before persistence into the
SQLite archive. The legacy `SyncConversation.fetch_chat()` surface is retained
only as a compatibility wrapper around that validated parent fetcher.
