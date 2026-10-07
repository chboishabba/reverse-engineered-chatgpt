# Browser-backed writes

`re-gpt` keeps the existing direct retrieval/archive path for reads, but can now
mutate an existing ChatGPT conversation through an authenticated Chrome profile.
The browser owns authentication and request-affinity state.

## Transport order

For every mutation the CLI attempts:

1. same-origin browser `fetch` (`api` transport), then
2. the rendered ChatGPT composer (`dom` transport) if the first write fails.

The legacy copied-cookie Python POST path is not in this production fallback
chain.

After a successful mutation, `re-gpt` resolves the real conversation id and
re-pulls the conversation through `SyncChatGPT`, persisting it with the normal
`ConversationStorage` archive machinery. The browser response is therefore an
execution result, not the canonical history record.

## Usage

Install the browser extra and seed the persistent profile as described in the
main README, then submit to an existing conversation:

```bash
re-gpt --send 6a33ae58-cb84-83ec-b187-ddab3179ccbb \
  --prompt 'Complete all per max-cut.' \
  --headed
```

Aliases understood by `chatgpt_dom_relay.py` also work:

```bash
printf '%s\n' 'Status update?' | re-gpt --send ym --headed
```

A prompt can also be supplied from a file:

```bash
re-gpt --send ym --prompt-file /tmp/task.txt --headed
```

Use `--browser-profile PATH` to override the relay profile and `--write-timeout
SECONDS` to override the default 900-second mutation timeout.

The session token (`--key` or the normal re-gpt token configuration) is used
only for the post-write reconciliation pull; the write itself is authenticated
inside the browser.

## Exit status

- `0`: mutation succeeded and the canonical archive was reconciled.
- `1`: the mutation failed before ChatGPT accepted it.
- `2`: ChatGPT accepted the mutation, but the canonical archive reconciliation
  failed. The command does **not** resend the prompt in this state; fix retrieval
  authentication and pull the conversation again.
