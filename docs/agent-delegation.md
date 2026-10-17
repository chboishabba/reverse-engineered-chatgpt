# Agent Delegation and Implementation Guide

This guide is for AI agents (coding assistants, local subagents, orchestration harnesses) using `re-gpt` to pair program with or delegate implementation tasks to remote ChatGPT reasoning models.

---

## 1. The Pair-Programming Architecture

When delegating implementation to ChatGPT through `re-gpt`, follow this proven division of labor:

```text
+--------------------------------------------------------------+
|                   Remote ChatGPT Agent                       |
|  - Deep reasoning & architectural planning (e.g. gpt-6)     |
|  - Multi-file refactoring & test design                      |
|  - Commits & PR creation via GitHub connector                |
+------------------------------+-------------------------------+
                               |
                   re-gpt (API-first transport)
                               |
+------------------------------v-------------------------------+
|                      Local Agent                             |
|  - Test suite execution & coverage verification (pytest)     |
|  - Live environment & acceptance runner execution            |
|  - Runtime quirk resolution & local bug fixes                |
|  - Code review debt clearance & PR merge                     |
+--------------------------------------------------------------+
```

### Why Defer to ChatGPT for Reasoning
- **Deep Deliberation**: High-reasoning models (`gpt-6-thinking`, `o1`, `o3`) excel at exploring multi-step failure modes, designing clean decoupled contracts (such as data-in/world-model-out interfaces), and generating thorough test fixtures.
- **Context Preservation**: Delegating code generation keeps your local agent's context window lean and focused on verification receipts, git state, and test output.
- **GitHub Connector Automation**: When connected to GitHub, ChatGPT creates branches, writes commits atomically, and opens pull requests without clogging conversational transcripts with raw files.

---

## 2. Dispatching Tasks to the Remote Worker

### The Dispatch Prompt Pattern
When prompting the remote agent for an implementation tranche:

1. **Specify Scope & Branch**: Define the milestone name, exact scope boundaries, and target branch name (e.g. `maxcut/<feature>-<date>`).
2. **Explicit GitHub Connector Instruction**: Tell the agent to commit directly to GitHub via its connector and open a PR:
   > *"Implement directly on GitHub using your connector on branch `maxcut/...` and open a PR. Do not dump raw code in chat."*
3. **Clear Acceptance Gates**: List the specific tests, invariants, and acceptance scripts the local agent will run to certify the PR.
4. **Division of Labor Reminder**: Clarify that execution and verification are owned locally; they own implementation and PR readiness.

### Invoking via Python (`re_gpt`)

```python
from re_gpt import continue_thread

# 1-liner to dispatch, stream live tokens to stdout, and save clean markdown reply to a file:
res = continue_thread(
    "6ad2d380-e204-83ec-b132-05289418989a",
    prompt,
    follow=True,
    stream=True,
    output="response.md",
)
```

### Invoking via CLI (`re-gpt`)

```bash
# 1-liner: read prompt, stream tokens live, and save markdown reply to a file:
re-gpt --send <THREAD_ID> \
       --stream \
       --follow \
       --output response.md <<'PROMPT'
Implement Milestone X on branch maxcut/... using your GitHub connector and open PR.
PROMPT

# Or via prompt file:
re-gpt --send <THREAD_ID> \
       --prompt-file prompt.txt \
       --stream \
       --output response.md
```

---

## 3. Operational Rules for Agents

### Rule 1: Never Poll Aggressively
- High-reasoning models think and invoke tools for 2 to 5 minutes per turn.
- Querying `GET /backend-api/conversation/<id>` every few seconds will trigger OpenAI's **HTTP 429 (Rate Limit)**.
- If you need to monitor progress, either:
  - Check the remote git branch directly (`git fetch origin && git log origin/<branch>`), or
  - Allow adequate delay between graph checks (at least 15–30 seconds) rather than polling in a tight loop.

### Rule 2: Keep the Exactly-Once Boundary Sacred
- Once a mutation request (`POST /backend-api/conversation`) is dispatched, **never resend it** if an error occurs.
- Reconcile status by reading from the canonical graph via `service.reconcile(operation_id)`.
- If an operation outcome is ambiguous, it is certified by reading, not by re-POSTing.

### Rule 3: Pay Review Debt Before Merge
- When the remote agent opens a PR, fetch the branch and run the local test suite:
  ```bash
  git fetch origin <branch>
  git checkout <branch>
  pytest -q
  ```
- Run any live sacrificial acceptance runners (e.g. `scripts/accept_*`).
- Review automated comments (e.g. CodeRabbit, CI checks). Apply any needed local fixes, commit, push, and only then merge the PR:
  ```bash
  gh pr merge <PR_NUMBER> --repo <REPO> --merge
  ```

### Rule 4: Handle Profile Directory Locks
- Chromium enforces a single process per `user-data-dir` (`SingletonLock`).
- If you run multiple operations concurrently against the same profile, browser fallback will fail with `ProcessSingleton File exists (17)`.
- Use `clear_stale_profile_locks(profile_dir)` to safely unlink dangling locks from terminated processes before launching browser observers.
