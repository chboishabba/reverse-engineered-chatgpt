# reverse-engineered-chatgpt Repository Guidelines

## External LLM / ChatGPT Guidelines (curl_cffi & Accessibility Debug Policy)
- **Accessibility & Debug Protocol (CDP & Browser Inspection)**:
  - This is fundamentally an **accessibility project for vision-impaired people**, extracting rich semantic content from browser silos for assistive technology (e.g. braille readers, offline speech synthesizers, screen readers).
  - Connecting via Chrome DevTools Protocol (CDP) on port 9222 or inspecting the browser session is **permitted strictly for debugging, diagnostic tracing, and accessibility extraction**.
  - Do not use DOM automation or CDP hijacking for routine background spam; reserve it for debugging session anomalies, token state, and accessibility verification.
- **Headless curl_cffi is the Preferred Programmatic Method**: For ordinary turns, it is **significantly faster, requires no browser overhead, and is vastly more reliable** than browser/DOM automation.
- **Headless CLI Usage**:
  ```bash
  python -m re_gpt.entrypoint \
    --send <CONVERSATION_ID> \
    --prompt "<text>" \
    --write-transport async \
    --no-dom-fallback \
    --write-timeout 300 \
    --follow
  ```
  *(Use `--prompt-file <path>` for multiline prompts. Keep `--write-timeout 300` for reasoning models).*
- **Native MCP Server**:
  Use `python -m re_gpt.mcp_server` (`chatgpt_continue`, `chatgpt_hydrate`, etc.) with `write_transport="async"` and `dom_fallback=False`.

## Implementation Delegation Protocol (Actor-Critic / Supervisor-Discriminator)
- **The MCP Worker is the Generative Actor**: All major implementation, deep mathematical/algorithmic reasoning, exploratory theorem proving, and wide-reaching search must be deferred to the governed MCP worker (`itir.chatgpt.dispatch_worker` or `re_gpt.mcp_server`). Instruct the remote worker to implement changes via its GitHub connector directly in the repository (creating a branch/PR).
- **The Local Worker is the Cooperative Discriminator & Commons Coordinator**: The local agent must not attempt heavy speculative coding locally. It acts as the analytical judge, security firewall, and local referee. Fetch the branch locally (`git fetch origin`), run deterministic tests (`pytest`, `ruff`, `cargo test`, `agda`), and verify conformance against ISO, NIST AI RMF, and ZKP framing (O/R/C/S/L/P/G/F). Never merge unverified code.
- **Evidence-Based Restorative Feedback**: If local verification fails, the local discriminator never provides vague conversational complaints. It returns a minimal, token-distilled critique package with exact failing test traces, deterministic compilation logs, or schema residuals, allowing the MCP worker to execute targetted course correction.
- **Context-Insulated Protocol (State over Chat History)**: Mitigate the Tragedy of the Context Commons. Do not pass sprawling conversational histories or massive raw tool dumps across turns. Distill shared progress into lean state commons (active status maps, verified facts, open gap lists) and dispatch only targeted state fingerprints and event triggers.
- **All Graph Operations Use Headless curl_cffi**: All catalog queries (`get_conversations`, `list_conversations_page`) and thread message hydrations (`fetch_conversation`, `itir.chatgpt.hydrate`) execute purely over headless `curl_cffi` without browser overhead or DOM rendering. Browser same-origin retrieval is an isolated diagnostic fallback only when Turnstile challenges or backend 429 cooldowns strictly require it.
