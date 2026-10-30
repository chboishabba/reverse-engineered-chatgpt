# Python TUI server-owned multi-chat implementation cut (2026-10-30)

## Owner correction
No Firefox-specific change watcher is necessary. The older `conversation_sync.ConversationRevisionWatcher` was transport polling and compared a `revision` field absent from the current `hydrate_one` receipt. The TUI adapter now uses `PGConversationSubscriber` to read *PostgreSQL source revisions and immutable operation events* without identifying a browser origin, DOM scraping, or triggering hydration on every viewing interval.

## Implemented source surfaces
- `pg_operation_events.py`: immutable PG event journal in `ops.chat_operation_event`, per-conversation replay after event cursor, exact job ID and sequence, conflicting duplicate rejection.
- `pg_turn_worker.py`: PG queue in `ops.chat_conversation_job`. A worker independent of the TUI executes re-gpt MutationService exactly once and appends progress/tool events. Multiple processes/threads may serve different conversations; transactions guard active per-conversation claims. Unknown-commit jobs remain held and never automatically resend.
- `pg_conversation_subscriptions.py`: PG-only change/event observation; no remote/browser polling for display sync.
- `upstream_tui_adapter.py --server-jobs`: existing threads submit asynchronous PG work and regain the terminal promptly. Switching conversation does not own/cancel server operations. Partial buffers are keyed by conversation + job, and refresh occurs on PG source advances.
- `terminal_activity.py`: when source contains actual tool or reasoning-summary bodies, use those instead of generic placeholder status. PG journal retains original observed argument/result contents; the presentation layer retains a redaction policy for known secret names.
- `terminal_rich_display.py` + `terminal_math_layout.py`: source-preserving Unicode display fallback for common LaTeX, nested fractions, roots, scripts, matrix environments, MDX links and citation placeholders; not a full TeX engine.
- `workspace_routing.py`, `workspace_auto_policy.py`, `workspace_projection.py`: explicit @chatgpt/@local/@both routing and per-conversation /auto off/local_first/parallel/local_after. Local output is PG workspace evidence, never inserted as a remote ChatGPT graph node. @ao remains fail-closed until typed lane/worker obligations are supplied, rather than manufacturing AO authority.
- `ITIR MCP`: `itir.chatgpt.operation_events`, `queue_turn`, `job_status`, `cancel_queued`. Requires no running TUI.

## Operator flow
Install the re-gpt/ITIR versions from the coordinated PRs and configure the existing `ITIR_PG_DSN`, CFFI owned session, and existing PG source-store schema. In an independently supervised server process:

```sh
python -m re_gpt.pg_turn_worker --workers 2
```

Run the existing upstream terminal separately:

```sh
python -m re_gpt.upstream_tui_adapter \
  --upstream-tui /path/to/chatgpt-web/chatgpt_tui.py \
  --server-jobs --sync-interval 2
```

For an opt-in local agent, configure `ITIR_LOCAL_AGENT_ARGV` as a JSON argv array naming an installed executable that accepts task context on stdin. No shell interpolation is used. The local agent receives the bounded source-labelled workspace context. `/auto off|local_first|parallel|local_after` changes one conversation's policy; actor-generated events never recursively trigger auto replies.

## Critical residuals before production claims
1. Execute real two-conversation (A/B), kill/reconnect, source revision, duplicate-cursor and PostgreSQL transaction acceptance. Unit tests in this cut are **source-written**, not executed here.
2. Integrate the existing MutationOperationStore's operational receipts into the canonical PG service and reconcile abandoned `running`/`commit_unknown` jobs explicitly; never infer safe retry.
3. New-thread operations still use the legacy synchronous path. `--server-jobs` currently applies to existing threads; no full new-conversation async spool.
4. `@ao` is deliberately refused until a validated typed worker/lane contract exists. Local-first is FIFO in a single worker; full distributed dependency gating and atomic multi-provider fan-out require acceptance.
5. Unicode terminal math is not full matrix/TeX *typesetting* in the graphics sense. Integrate a vetted math layout engine and terminal graphics fallback with streamed delimiters and citation/asset metadata.
6. Existing source `chat_archive_message` exact active-branch ordering and append/versioned revisions need real schema acceptance. No ESD modifications and no source-theorem promotion.
7. The queue stores prompts and full observed tool payloads: apply account-scoped PG roles, encryption at rest, retention and per-agent authorisation before multi-user deployment.
8. A TUI revision subscriber uses bounded PG polling; event-indexed LISTEN/NOTIFY or server SSE is preferable for scale.

## Prior art
Upstream tritao/chatgpt-web remains separately installed; no vendoring. Herdr is the Neovim-friendly workspace reference, Dux a distinct agent-office TUI, dex the Rust daemon/delegate precedent. ITIR PG/SLR is canonical source authority and semantic read owner; AO owns Pareto/max-cut typed worker scheduling. Golden DASHI Agda contracts take precedence and protected ESD surfaces were not modified.

No live PG, CFFI account, TUI terminal or formal kernel verification has been performed in this GitHub-only execution. Do not merge as production-ready without acceptance.
