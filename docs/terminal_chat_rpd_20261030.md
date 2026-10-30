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
