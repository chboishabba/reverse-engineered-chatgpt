# Accessibility browser: bootstrap, fallback and protocol oracle

The browser relay exists so authenticated ChatGPT remains operable without requiring visual interaction. It is still a first-class accessibility tool, but it is no longer the ordinary product writer: direct async API reads/writes are primary, and canonical completion comes from the exact conversation graph.

## Browser roles

Use the browser for:

- login/session bootstrap;
- attaching to an already authenticated browser over local CDP;
- accessibility-compatible continuation fallback after an explicit direct **pre-commit** rejection;
- same-origin/browser exact-read fallback;
- redaction-first protocol capture;
- protocol drift diagnosis and acceptance testing.

Do not use browser/DOM success alone as proof that a mutation committed.

## CDP attach

If an authenticated Chrome/Chromium instance is already running with a local DevTools endpoint, attach instead of launching another browser:

```bash
python -m re_gpt.entrypoint \
  --send <CONVERSATION_ID> \
  --cdp-endpoint http://127.0.0.1:9222 \
  --prompt 'Reply exactly ACK.'
```

CDP attach is non-owning: the relay does not close the attached browser or its existing contexts. Keep the DevTools endpoint bound to loopback.

## Launch mode

When no CDP endpoint is supplied, Linux browser tooling defaults to displayless Ozone Chrome:

```text
--browser-mode displayless
```

This runs normal installed Chrome with a non-visual Ozone display backend rather than requiring X11/Wayland or a physical monitor. The relay does not add flags whose purpose is concealing automation signals.

Other explicit modes remain available:

```text
--browser-mode headless
--browser-mode headed
```

A persistent profile cannot be opened simultaneously by another Chrome process. If Chrome already owns the profile, use CDP attach rather than launching the same profile twice.

## Normal mutation routing

The public continuation surface is:

```text
MutationService
  -> direct async mutation (primary)
       -> success/ambiguous: reconcile only
       -> explicit pre-commit rejection: optional DOM fallback
  -> exact GraphBroker read
  -> SQLite canonical receipt
```

Graph mutations (`new`, `branch`, `regenerate`, `edit-and-branch`) use the direct graph-mutation path and exact graph reconciliation rather than DOM interaction.

## Direct browser runner

The lower-level browser runner remains useful for diagnostics/acceptance:

```bash
python -m re_gpt.mutation_browser \
  <CONVERSATION_ID> \
  --prompt 'Reply exactly ACK.' \
  --browser-mode displayless \
  --json
```

Attachments may be staged through the browser-native file input on this fallback/oracle path.

## Protocol recorder

`BrowserProtocolRecorder` records bounded same-origin ChatGPT request/response structure while a known-good browser action runs. It preserves normalized method/path/status, payload schema shape, sequencing and timing, but shareable observations omit secret values and transcript bodies.

That recorder was used to graduate direct attachment support and remains the preferred way to investigate protocol drift or a not-yet-supported web operation.

See [accessibility-capture.md](accessibility-capture.md).

## Read/write split

Canonical reads are adapter-independent through `GraphBroker`:

```text
async -> sync -> browser
```

The browser is simply one exact-read adapter. Rendered DOM status is provisional diagnostic evidence.

## Exactly-once rule

A DOM fallback may happen only while the direct attempt is known to have failed before conversation POST commit. If the direct POST timed out, reset, returned a 5xx, or otherwise became ambiguous after dispatch, the browser must **not** submit the prompt again. Read/reconcile the operation instead.
