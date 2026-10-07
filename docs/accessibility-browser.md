# Accessibility-first browser relay

`re-gpt-browser` is the displayless browser-owned write path for environments
where the normal ChatGPT page is impractical or impossible to operate visually.
It keeps ChatGPT authentication and mutation state inside the browser and emits
the assistant reply as terminal text.

## Default: displayless Ozone Chrome

On Linux the default mode is:

```text
--browser-mode displayless
```

This launches normal installed Chrome with Playwright `headless=False`, but asks
Chromium to use its Ozone headless **display backend**:

```text
--ozone-platform=headless
--ozone-override-screen-size=1440,960
```

That means the browser does not require X11, Wayland, a virtual framebuffer, or
a physical monitor.  It is intentionally different from Chrome's `--headless`
mode.  The relay does not add flags intended to conceal automation signals.

Example:

```bash
printf '%s\n' 'Reply exactly ACK.' | \
  re-gpt-browser 6a33ae58-cb84-83ec-b187-ddab3179ccbb \
  --profile-dir ~/.codex/chrome-mcp-profile
```

The default mutation transport is `auto`: browser same-origin API submission is
tried first, then the real ChatGPT composer DOM is used as a compatibility
fallback.

## Other browser modes

Modern Playwright unified Chromium headless remains available explicitly:

```bash
re-gpt-browser <conversation> --browser-mode headless --prompt 'hello'
```

This mode uses Playwright `channel="chromium"`.  Install that browser build if
needed:

```bash
playwright install --no-shell chromium
```

A normal Chrome window remains available for compatibility/debugging:

```bash
re-gpt-browser <conversation> --browser-mode headed --prompt 'hello'
```

The legacy `--headed` flag is retained as an alias for that mode.

## Transport selection

```text
--transport auto   API first, DOM fallback (default)
--transport api    same-origin browser fetch only
--transport dom    composer DOM only
```

Use `--json` when a caller needs structured fields such as the resolved thread
ID, selected browser mode, transport, or fallback transport.

## Profile ownership

A persistent browser profile must not be open concurrently in another Chrome
process.  If you reuse `~/.codex/chrome-mcp-profile`, stop the process currently
owning that profile before launching the relay.  Alternatively, use a dedicated
profile directory for `re-gpt-browser`.

This browser path is independent from the legacy copied-session-token read path.
Conversation reads and archive reconciliation can continue to use the existing
`re_gpt`/SQLite machinery.
