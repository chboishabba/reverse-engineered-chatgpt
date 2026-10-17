# Browser-owned mutation service

`re-gpt --send` now treats the browser as the mutation owner and the validated ChatGPT graph as the commit authority.

## Production path

```text
CLI / MCP
  -> MutationService
  -> authenticated browser profile
  -> target thread hydration
  -> real composer (never #pending-conversation-input)
  -> one DOM trigger
  -> browser submission evidence
  -> validated graph pull
  -> canonical user node receipt
  -> ConversationStorage + mutation_attempts SQLite
```

The historical handcrafted same-origin conversation POST remains diagnostic/research code in `re_gpt.accessibility_browser`. The production `re-gpt` write path does not try it before DOM submission.

## Commit semantics

Browser evidence is deliberately weaker than commit evidence.

- `submission_observed`: the real page emitted at least one signal that makes a second trigger unsafe.
- `commit_unknown`: the browser may have submitted, but a validated graph pull has not certified the new user node. Do not resend automatically.
- `committed_pending`: the validated graph contains the exact new user node, but no settled assistant child is present yet.
- `complete`: the validated graph contains the new user node and an assistant child with text.

Repeated identical prompts are classified using the mutation start time, so an old matching user node cannot certify a new send.

## Readiness

An existing thread is ready only after all of these are true:

1. the URL is the requested `/c/<uuid>` thread;
2. `/api/auth/session` exposes an access token;
3. thread history or thread metadata has hydrated;
4. `#pending-conversation-input` is not visible;
5. the real editor is visible;
6. a send control exists and is not `aria-busy`.

After prompt insertion the send control must become enabled before the single trigger is attempted.

## CLI

```bash
re-gpt --send <thread-id> --prompt 'Reply exactly ACK.'
```

The command returns as soon as the canonical user node is proven. If the assistant is still running it reports `committed_pending` and exits successfully because the user mutation is already durable and must not be resent.

Use `--follow` to keep reconciling for an assistant child up to `--follow-timeout`.

## MCP

Install the optional MCP dependency and run the native stdio server:

```bash
pip install -e '.[browser,mcp]'
re-gpt-mcp
```

Tools:

- `chatgpt_continue`
- `chatgpt_operation_status`
- `chatgpt_reconcile`
- `chatgpt_view`
- `chatgpt_list`

MCP calls `MutationService` directly; it does not shell out to the human CLI. Operation status is read from SQLite and therefore survives browser/process restarts.

## Durable state

`mutation_attempts` stores operation IDs, stage timestamps, graph node IDs, state, transport, and errors. It stores only a SHA-256 prompt digest before a graph node exists; transcript text remains in the canonical conversation/message archive.

## Remaining live acceptance work

The next execution-bound gate is empirical rather than architectural:

- repeat ACK on short, medium, and long existing threads;
- verify unauthenticated and pending-composer negative cases;
- collect stage timing distributions and tune condition budgets from those observations;
- then probe native attachment selection and the remaining new/branch/regenerate/edit UI operations one at a time, each with the same validated-graph receipt rule.
