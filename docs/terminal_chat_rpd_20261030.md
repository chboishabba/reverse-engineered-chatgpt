# Terminal-native ChatGPT RPD — 2026-10-30

## Steelman
Create a low-DOM terminal presentation of authenticated ChatGPT conversations, **not** a new coding agent, history store, semantic engine or model router. Consume existing re-gpt curl_cffi and graph/MCP read/write contracts. The terminal is a non-authoritative view.

## Reuse evidence (inspected)
- re-gpt: README, docs/interfaces.md, re_gpt/cli.py, re_gpt/mutation_service.py; recent PR #54 bounded SSE tool-stage metadata, #56 authenticated async fleet session.
- ITIR: docs/architecture/SINGLE_CANONICAL_STORE_AND_PG_SPINE_ROADMAP.md (2026-10-29 PG-only destination; supersedes legacy SQLite); docs/planning/chat_archive_db_exporter_roadmap_20260516.md (LaTeX-source retention, KaTeX, bounded exports); ao_operator_workflow_v1.md; recent PRs #53–56.
- SLR: docs/roadmap/semantic_reader_runtime_20260916.md, PR #74 cockpit contract and #75 native fleet read model. Reader ABI is typed optional semantic projection only.
- SensibLaw: docs/planning/suite_story_pnf_fanout_boundary_20260606.md. Candidate evidence is not authority.
- Agda golden precedent: dashi_agda/DASHI/Interop/ITIRRibbonProjectionAuthorityBridgeExact.agda: readonly presentation, no canonical mutation. SLR DASHI/Interop/SLRLegalRuntimeCapstoneExact.agda separately names read-only workbench, explicitly source-written; neither proves a new TUI implementation.
- External comparison: tritao/chatgpt-web for UX; junosuarez/termtex and gakonst/ratatex for terminal mathematical display. No external code copied into this slice. Preserve respective authors and dated source roots if reused.

## RPD priority / discriminators
1. P0: Source-exact Markdown/TeX projection. Parser emits immutable segments and source coordinates, not rendered replacement strings; incomplete streamed TeX remains source.
2. P0: Do not duplicate conversation graph, archive, query or receipt state. All mutation writes go through MutationService. commit_unknown never triggers automatic resend.
3. P0: PostgreSQL is the target sole persistent store. Existing re-gpt SQLite operation persistence remains a **migration gap**, not a proposed permanent TUI store.
4. P1: Test UI alternatives against canonical graph branch navigation, streamed partial equations, text-copy fidelity, 2,000-turn bounded viewing, and graphics-unavailable fallback.
5. P1: Integrate existing KaTeX semantics where possible with negotiated Kitty graphics and Unicode fallback. Validate matrices, align/cases, fences and nested delimiters.
6. P2: Feature matrix for authenticated ChatGPT model/thinking choices, attachments, tool metadata, artifact links, widgets and media. Absent capabilities must be reported unsupported, not fabricated.
7. P2: Optional SLR evidence/Agda/Lean proof drilldown and AO task receipts; render does not promote candidates or prove theorems.

## Obligations (proposals, not claimed Agda kernel proofs)
- concat(projection segment sources) == original source, with exact nonoverlapping offsets.
- display-only action cannot mutate canonical source, receipts, or semantic authority.
- an uncertain write is reconciled, never resubmitted.
- rendered != admitted != kernel-verified; formal receipts retain independent provenance.
- rendering cost scales with visible source and uncached visible mathematics.
- provider/transport failure must not silently route to DOM in ordinary headless usage.

## Max-cut / Pareto / QID
The highest-alpha cut lies between the existing canonical re-gpt read/operation boundary and a terminal renderer. Reuse upstream persistence and downstream formal read models. Test bounded projections and interruption states before choosing a full TUI toolkit. Avoid CI claims: no runtime or Agda/Lean verification established by this document.
## Source-written continuation (2026-10-30)
Added `re_gpt/terminal_view.py`, a pipe-friendly read-only projection of stdin to original source, source-indexed segment JSONL, or math-source lines. It is intentionally not a second chat transport, TUI framework, or PG store. The scanner was corrected to consider `\\(`/`\\[` openers and to bound fence-prefix scanning. Local CLI execution was unavailable because the checkout cannot be materialized from GitHub in this runtime; these remain source-written changes. Follow-up: execute tests against an actual checkout, add capability-negotiated formula renderer, and wire canonical re-gpt streaming/read UI after production SLR read models are checked. No CI/kernel claims.

## Upstream TUI reuse: implementation tranche (2026-10-30)

The upstream `tritao/chatgpt-web/chatgpt_tui.py` exports `run_tui(list_conversations, load_conversation, send_prompt, ...)` with callable injection. The implementation uses this **existing** callback seam in `re_gpt/upstream_tui_adapter.py`: it loads the unmodified module from an explicitly supplied checkout and delegates network reads to `GraphBroker` and mutations to `MutationService`. There is no need to copy upstream TUI, Chrome signing service, browser automation, daemon or history cache. Original author attribution: tritao/chatgpt-web, https://github.com/tritao/chatgpt-web, inspected 2026-10-30.

Example after separately obtaining upstream code and installing its prompt-toolkit/Rich dependencies:

```sh
python -m re_gpt.upstream_tui_adapter --upstream-tui /path/to/chatgpt-web/chatgpt_tui.py --conversation-id UUID
```

The current upstream TUI has no incorporated local math graphics; `terminal_projection` remains a separate source-only stage pending graphics negotiation. Existing docs and status are **not** claims of full ChatGPT feature parity. CLI image descriptors, rename and canonical PG history query remain explicit follow-up adapters. `SyncChatGPT.list_conversations_page` currently provides a bounded remote picker, not a substitute for ITIR PostgreSQL.

Critical safety: continuation uses `write_transport=async, dom_fallback=False` and emits `commit_unknown` as a hold, never a resend; no credential material enters UI callbacks. Current adapter has **source-written/offline-test-only** status. GitHub tool writes were available but local checkout/test execution was blocked by network DNS. No CI or Agda/Lean kernel verification claimed. Next discriminator: execute isolated mock callback tests, then test actual re-gpt session with one sacrificial thread and review operation receipt. Upstream repository tree had no LICENSE file; dynamic loading from a separately installed checkout avoids embedding its code, and any later distribution must establish reuse permissions.

## SSE activity / full tool payload continuation (2026-10-30)

User requirement supersedes the metadata-only limitation in re-gpt PR #54:
**retain and show available tool request bodies, arguments and results**, not only
their labels. Current source-written implementation adds `terminal_activity.py`,
emits observed tool-call/result `body` separately from SSE `token_delta`, and
introduces `/activity` in the reused upstream TUI subclass to inspect in-memory
redacted activity records. Its normal status shows the current activity summary;
ordinary final-channel response streaming remains separate.

Distinguish precisely:
- Observed tool body from ChatGPT SSE: retained in the TUI activity log.
- Structured `arguments` or `result`: retained if present in progress events;
  currently not guaranteed by the upstream SSE decoder for every tool family.
- Reasoning status or model-exposed summary: may be displayed where provided.
- Hidden/private chain-of-thought: not available and not reverse engineered or
  fabricated. A `thinking` event must not be misrepresented as its text.
- Known sensitive key names are recursively redacted before display. This is a
  first conservative policy, **not** a comprehensive sensitive-data scrubber.
- Activity log is in-memory and bounded to 500 entries; no new persistent
  database was added. Durable retention belongs on the ITIR sole PostgreSQL
  spine, with authorization, encryption/redaction and retention controls.

Residuals before production use: validate actual raw SSE tool envelopes;
replace heuristic parsing with typed tool schema adapters; support safe
export of tool evidence to PostgreSQL; test clipboard/scrollback security and
long/multimessage streams; close upstream LaTeX typesetting and rich citation
markup rendering. These changes are source-written, not locally executed.
