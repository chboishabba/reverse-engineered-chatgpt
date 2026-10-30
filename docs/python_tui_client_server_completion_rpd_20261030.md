# Multi-chat Python TUI completion RPD — 2026-10-30

## Corrected upstream attribution
- **Herdr** (the user's preferred screenshot): workspace/split/editor/agent presentation reference. Assess actual community/licensing/API before adopting; do not mislabel images as dex.
- **Dux** (the later screenshot): separate agent-office TUI; NOT herdr.
- **dex**, sumofbytes/dex: Rust background subagents (spawn, wait, stop, steer, follow_up, resume_from), daemon event replay, explicit approvals. Reuse lifecycle ideas, not its JSONL store as conversation authority.
- **tritao/chatgpt-web**: existing Python TUI, independently checked out and dynamically loaded; licensing remains unverified for redistribution.
- Source of truth: ITIR PostgreSQL corpus and operation receipts; re-gpt direct curl_cffi transport; SLR production semantic reader; AO conductor scheduling. Optional tmux/socket are UI-debug surfaces, never prerequisites for MCP conversation access.

## Authoritative state vs presentation
1. Conversation list and selected branch: PostgreSQL read projection; roster/page hydration commits into PG independently.
2. ChatGPT mutation: re-gpt worker creates a durable immutable operation ID and preserves exact-once reconciliation. Never resend on commit_unknown.
3. Stream: worker persists every observable token_delta, tool-call argument/body/result, tool status and terminal receipt, with (conversation_id, operation_id, event_seq). Hidden model chain-of-thought is not observable and must not be invented.
4. Client: subscribe from last seen cursor, replay missed events, switch conversations without cancelling or redirecting the operation. Display buffers are keyed by operation ID, not self.messages[-1].
5. Local/remote AO participants: a separate workspace event lane, with actor/authority/provenance, not inserted as remote ChatGPT graph nodes.
6. Formatting: ITIR Svelte chat-reader capabilities and exact source projection are reference. Rich fallback newly wired in terminal_rich_display.py; full TeX typesetting, citation resolution and mixed media remain missing.

## Mandatory discriminators
- Start A, receive partial SSE, switch to B, submit B; A continues and remains correctly attributed. Return A, replay exactly once from cursor.
- Close/reopen TUI during A; worker remains alive and reconnecting client recovers PG events without resubmission.
- Concurrent A+B progress, exceptions, cancellation and commit_unknown each reconcile only their own operation.
- Firefox-originated changes: existing conversation_sync.py revision watcher detects exact graph updates; do not use DOM or alter native terminal SSE completion.
- Resume picker immediately serves PG roster records even if their graphs are not yet hydrated; selected thread has priority, background queue is bounded and durable across restarts.
- Math screenshots: inline/display \(\), \[\], aligned/cases/matrices, fractions, citation tags/Link, code fences, unicode escapes, long streams and original clipboard-source fidelity.
- Tool activity: no generic 'reasoning unavailable' substitute where provider supplied an explicit summary; show real content when actually present, never private unavailable reasoning.
- @local, @ao, @both and optional /auto must queue participant operations with caps, permissions, idempotency and loop prevention.
- Syntax highlighting and nvim: prefer Herdr as workspace shell and Neovim as editor client; reuse renderer/tokenizer rather than vendoring multiplexer.
- No ESD modifications; golden Agda > Lean mirror, and no false kernel proof or CI claim.

## Implementation sequencing
P0: server operation state/event log and durable PG subscription cursor; eliminate TUI-owned synchronous operation lifecycle.
P0: one thread A/B concurrency/replay integration test, reconnect and exact-once receipts.
P0: complete authenticated CFFI / PG hydration merge and validate real DB writes/branches.
P1: rich terminal display (first Unicode fallback landed), real LaTeX layout/citation anchors and read-only source mode.
P1: activity details and typed tool arguments/results, explicit user permissions for sensitive outputs.
P1: stable queue + actor routing with AO/dex adapter and response approval policy.
P2: Herdr/Neovim UX integration and optional full Rust frontend, only after stable server API.
P2: widget/media parity and multi-thousand-turn virtualized history.

## Implemented in this tranche
re_gpt/terminal_rich_display.py provides a source-preserving *presentation-only* Markdown/Unicode-math fallback. It is wired through the subclass render_message override, retaining upstream source and cache semantics. Offline fixtures were added, not run here. No claim of true TeX typesetting or multi-chat background execution. No user terminal sessions, SeaMeInIt, SensibLaw or ESD code changed.
