# Stream-first follow and global backend queue

Milestone E removes full-graph polling from the normal `follow=True` path and introduces one cross-process pacing clock for ChatGPT backend requests.

## Stream-first follow

The direct async mutation transport already keeps the conversation POST open until the SSE response closes. Milestone E consumes that live stream incrementally rather than waiting silently and then polling the conversation graph every two seconds.

`MutationService.continue_thread(..., follow=True)` now has this contract:

```text
one mutation POST
  -> consume active SSE stream
  -> emit progress callbacks while chunks arrive
  -> observe terminal [DONE] / finished_successfully boundary
  -> exactly one canonical graph reconciliation
  -> persist canonical SQLite receipts
```

It does **not** enter the former repeated `GET /backend-api/conversation/<id>` follow loop. Results report:

- `follow_mode = "stream_terminal_single_reconcile"`
- `terminal_reconcile_count = 1`
- `reconcile_poll_count = 0`
- `stream_terminal_observed`
- `stream_terminal_reason`
- `stream_event_counts`

A caller can receive live progress with a callback:

```python
service.continue_thread(
    conversation_id,
    prompt,
    write_transport="async",
    follow=True,
    progress_callback=lambda event: print(event["kind"]),
)
```

Progress event kinds currently include `thinking`, `tool_invocation`, `token_delta`, and `terminal`. Token delta text is delivered only to the caller callback/result context; the acceptance receipt records lengths and event metadata rather than transcript text.

Graph mutations that request `follow=True` also suppress the service polling loop and use one post-stream reconciliation.

If an explicit pre-commit async rejection legitimately selects DOM fallback, the browser-owned mutation is still allowed to perform its own terminal follow. The service does not add a graph-polling loop afterward.

## Global backend queue

`GlobalBackendRateLimiter` stores one queue clock in `DEFAULT_DB_PATH` using SQLite `BEGIN IMMEDIATE`, so independent processes reserve backend dispatch slots atomically.

Known `/backend-api/` requests are paced through the installed async/sync HTTP-session wrappers. Exact conversation reads use a wider minimum interval than other backend operations.

Defaults:

```text
backend request minimum interval: 0.25 s
exact-read minimum interval:      1.50 s
429 base backoff:                30 s
429 maximum backoff:            300 s
```

Environment overrides:

- `RE_GPT_BACKEND_MIN_INTERVAL`
- `RE_GPT_EXACT_READ_MIN_INTERVAL`
- `RE_GPT_429_BASE_BACKOFF`
- `RE_GPT_429_MAX_BACKOFF`

A 429 advances the one global `next_allowed_at` clock using exponential backoff. Later processes wait on that same clock before dispatch. This prevents a rejected direct read from immediately cascading through sync/browser fallbacks or producing a browser-spawn storm.

The current request is never replayed merely because the queue observed a 429. Mutation POSTs retain the exactly-once boundary: ambiguous writes are never retried, while an explicit 429 remains a proved pre-commit rejection. The queue controls subsequent dispatch timing.

Browser exact-read fallback is also gated by the global exact-read queue before a browser is launched.

## Profile isolation

Browser launch serialization is **per canonical profile path**. A small atomic lease lives inside each profile directory:

```text
<profile>/.re_gpt_profile_lease
```

Two operations targeting the same profile serialize before Chromium launch. Operations using distinct profile directories have distinct leases and can proceed independently. Profile paths are canonicalized with `expanduser().resolve(strict=False)` before leasing, so alternate relative spellings of one profile do not create separate lock namespaces.

Stale leases are reclaimed only when the recorded owner PID is gone. Fresh unparseable lease metadata receives a grace window to avoid treating an in-progress lease creation as stale.

The existing Chromium `SingletonLock` cleanup remains scoped to the same profile directory and runs only after the re-gpt profile lease has been acquired.

## Acceptance

Run the focused unit/regression tranche, then the full suite. Live stream acceptance:

```bash
python scripts/accept_stream_follow_v1.py \
  <CONVERSATION_ID> \
  --profile-dir ~/.codex/chrome-mcp-profile \
  --output ./stream-follow-acceptance.json
```

The runner requires:

1. at least one real-time non-terminal progress callback;
2. a terminal callback/stream boundary;
3. `reconcile_poll_count == 0`;
4. `terminal_reconcile_count == 1`;
5. canonical final state `complete`.

The receipt also includes a snapshot of the SQLite backend queue without authentication/session secrets.
