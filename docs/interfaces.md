# reverse-engineered-chatgpt Interface Contract

## Product boundary

`MutationService` and `GraphBroker` are the shared mutation/read boundary for CLI, interactive compatibility, MCP and Python callers. Normal operation is API-first; browser/DOM is infrastructure for login/bootstrap, diagnostics, protocol observation and explicit pre-commit continuation fallback.

## Authentication ingress

Input is authenticated ChatGPT web-session state from the existing configured token/session sources and, when necessary, an owned browser profile/session bootstrap.

Output is either a usable authenticated client context or an explicit auth/bootstrap failure. Shareable receipts never contain raw token/cookie/sentinel values.

## Exact read ingress

Inputs:

- conversation ID;
- optional adapter/profile/CDP controls.

Acquisition order defaults to:

```text
async -> sync -> browser
```

Output is a validated exact conversation graph plus redacted adapter diagnostics. The first validated graph wins.

## Mutation ingress

Supported canonical operations:

- continue existing conversation;
- continue with one or more attachments;
- create new conversation;
- branch from exact `parent_node_id`;
- regenerate from exact `user_node_id`;
- edit-and-branch from exact `user_node_id`.

Every operation returns a durable operation ID/state and, when proven, canonical conversation/user/assistant/attachment identifiers.

After conversation POST dispatch ambiguity, callers must not resend automatically.

## Attachment ingress

Existing-thread continuation/branch/edit may provide local attachment paths. The direct transport fingerprints/stages the local file, uploads it, processes it, references the resulting file metadata in the conversation request, and later binds the local fingerprint to the canonical graph attachment ID.

Raw local paths and signed upload URLs are not shareable protocol evidence.

## Persistence egress

Validated graphs flow into:

- `ConversationStorage` for conversation/message persistence;
- `AttachmentArchive` for canonical file provenance;
- `MutationOperationStore` for exactly-once mutation state.

JSON exports are optional; SQLite is the canonical local persistence path.

## CLI surfaces

### Mutation CLI

`python -m re_gpt.entrypoint` exposes `--send`, `--new`, `--branch`, `--regenerate`, and `--edit-branch`.

### Interactive compatibility

`python -m re_gpt.cli` retains the legacy interactive browser/catalog UX, while `SyncConversation.chat()` is routed through `MutationService`.

### Archive/query CLI

Legacy list/view/download/inspect helpers remain supported for context/archive workflows.

## MCP surface

Mutation tools:

- `chatgpt_continue`
- `chatgpt_new`
- `chatgpt_branch`
- `chatgpt_regenerate`
- `chatgpt_edit_branch`
- `chatgpt_operation_status`
- `chatgpt_reconcile`

Read/diagnostic tools include `chatgpt_view`, `chatgpt_list`, and `chatgpt_live_status`.

## Browser/accessibility channel

The accessibility browser can launch or attach over local CDP. It provides:

- login/bootstrap;
- DOM continuation fallback only after explicit pre-commit direct rejection;
- same-origin exact-read fallback;
- bounded protocol observation;
- acceptance testing and diagnostics.

The browser is not canonical truth for mutation completion; the exact conversation graph is.

## Archive / suite integration

Conversation metadata and persisted messages remain consumable by chat-context-sync / archive tooling. The contract continues to expose stable conversation IDs, titles, timestamps, canonical messages and attachment provenance without requiring intermediate JSON exports.
