# PostgreSQL transactional rejuvenation — execution roadmap

## Authority

Agda golden: `chboishabba/dashi_agda:DASHI/Interop/SemanticThreadRejuvenationExact.agda`.
Lean mirror: `chboishabba/dashi_lean4:AgdaMirror/SemanticThreadRejuvenation.lean`.
Python candidate extractor: `re_gpt/semantic_rejuvenation.py`.
Python PostgreSQL job coordinator: `re_gpt/postgres_rejuvenation.py`.

The new coordinator uses PostgreSQL only. It does **not** replace the legacy SQLite-based `revive.py` entrypoint yet. Do not resume AO automatically.

## Phases and acceptance

- [x] R0a: inspect legacy SQLite revival and locate native CFFI roster work (PR #56).
- [x] R2a: deterministic SVO candidate extraction and provenance-aware capsule.
- [x] R3a: durable PostgreSQL job state with idempotent prepare, single claim and conservative reconciliation.
- [ ] R0b: inspect actual TrueNAS table DDL and FK keys for `corpus.chat_archive_message`, `pnf.statement_candidate_factor`, `pnf.graph`, `formal.proof_gap`, `ops.mutation_operation`, `ops.conversation_lineage`.
- [ ] R1: backfill and reconcile exact source graph coordinates; preserve legacy provenance.
- [ ] R2b: in one PostgreSQL transaction register source coordinates, factors, graph links, proof gaps, world-relative capsule and lineage intent. Do not guess existing schema columns.
- [ ] R2c: bounded adjacent-world query with explicit evidence/attribution boundaries.
- [ ] R3b: connect authenticated async curl_cffi dispatcher and reconcile remote send by exact conversation/message identifiers.
- [ ] R3c: verify no duplicate logical child on crash after send before DB completion; unknown outcome must stay blocked until reconciliation.
- [ ] R4: run isolated PostgreSQL tests, inject real disconnects and concurrency faults; kernel-check Agda and Lean; verify ABI fixtures.
- [ ] R5: switch all rejuvenation and AO query entrypoints to PostgreSQL, audit imports, canary AO operations.
- [ ] R6: verify backfill, backup and restore, then retire SQLite and .slrw.

## Semantics

The PostgreSQL job table is an outbox-like control ledger, not a substitute for the requested existing world-model tables. External sends cannot be committed atomically with PostgreSQL. A `sending` state after a crash is ambiguous; it must not be resent automatically. The caller's send adapter is currently synchronous and not yet wired to curl_cffi.

Do not treat source-written code, tests, semantic similarity, PR descriptions, or theorems mentioned in a thread as kernel-verified. Preserve original mathematical author and dated roots, repository proof ownership, executed checks and kernel receipts separately. Preserve Ibrahim Monster 3B attribution and never infer same-object representations from fingerprints.

## Tests

```bash
PYTHONPATH=. python -m pytest tests/test_semantic_rejuvenation.py -q
# Use an isolated throwaway PostgreSQL database only:
REJUVENATION_TEST_PG_DSN='postgresql://.../rejuvenation_test' PYTHONPATH=. python -m pytest tests/test_postgres_rejuvenation_integration.py -q
```

The integration tests are opt-in. No execution or production-schema verification is claimed by this document.


## 2026-10-29 semantic spine implementation tranche

- [x] Added `re_gpt.postgres_semantic_registration.register_thread_factors_and_messages`: parameterized inserts into verified `corpus.chat_archive_message`, `pnf.statement_candidate_factor`, and `pnf.candidate_semantic_product`. Canonical node ID required; no authority flags promoted.
- [x] Added `re_gpt.postgres_pnf_stream.stream_postgres_pnf_corpus`: read-only server-side cursor over canonical PNF factor table, without `.slrw`.
- [x] Added mocked SQL mapping, rollback and fail-closed coordinate tests.
- [ ] Execute those tests and verify actual DB constraints/foreign keys, conflicts and row counts.
- [ ] Integrate streamer into `itir_mcp` consumer and remove its binary-file dependence.
- [ ] Extend one transaction to `pnf.graph`, `formal.proof_gap`, `operational.rejuvenation_job` and lineage, using verified schemas. The new method currently covers only the three verified registration tables.
- [ ] Validate the exact-node backfill and the interpretation of `document_ref`, `source_revision_ref`, `full_message_span_ref`, and `dependency_ref` against existing ITIR canonical conventions. Current generated values are deterministic provisional references, not canonical equivalence proofs.
- [ ] Integrate async curl_cffi send/reconcile, verify AO 20-worker scheduling and the golden Agda/Lean proof boundary.
- [ ] Replace SQLite-based `revive.py` runtime entrypoint before claiming PostgreSQL-only rejuvenation.

### Deterministic commands

```bash
PYTHONPATH=. python -m pytest tests/test_postgres_semantic_registration.py -v
PYTHONPATH=. python -m pytest tests/test_semantic_rejuvenation.py -v
# Only against a disposable test PostgreSQL database:
REJUVENATION_TEST_PG_DSN='postgresql://.../rejuvenation_test' PYTHONPATH=. python -m pytest tests/test_postgres_rejuvenation_integration.py -v
```

### Golden formalism alignment

`DASHI.Interop.SemanticThreadRejuvenationExact` remains the protocol owner; `AgdaMirror.SemanticThreadRejuvenation` is secondary. The runtime now implements candidate-only registration but has not discharged all golden stage transitions. PR #1416 supplies spectral/Pareto quotient machinery; PR #1417 supplies the capacity-bounded conductor. Neither scheduling nor similarity promotes proof authority. Ibrahim attribution and Monster 3B ownership remain unchanged.

Verification: source committed; no fresh PostgreSQL, pytest, Agda or Lean execution receipt claimed.


## Topic-centric PostgreSQL conductor tranche (2026-10-29)

- [x] `re_gpt.postgres_world_rejuvenation.load_postgres_processing_queue` queries active candidate thread IDs from `corpus.chat_archive_message` (no SQLite).
- [x] `build_world_enhanced_capsule` uses thread SVO extraction, required externally supplied spectral coordinates, PostgreSQL candidate lemma adjacency and supplied topic goals. Missing manifold coordinates fail closed.
- [x] `allocate_topic_workers` mirrors golden PR #1417 topic-shape width fixtures, W<=20, and eligibility gating.
- [x] Mock unit tests in `tests/test_postgres_world_rejuvenation.py`.
- [ ] Confirm the actual `docs/fleet/fleet_hierarchical_pareto_summary.json` shape. It was not available at the inspected ITIR default branch; current adapter accepts a `conversations` mapping or list with explicit coordinates.
- [ ] Read `operational.rejuvenation_job` actual columns/state contract and incorporate its eligible/pending job selection. Current queue only reads active corpus candidates.
- [ ] Wire into `itir_mcp.fleet_round_runner.execute_pareto_conductor` on the appropriate ITIR feature branch. PR #53 already owns dynamic conductor; avoid copying that scheduler.
- [ ] Derive topic deliverables from verified StatiBaker/Kanban sources instead of requiring caller-supplied `active_goals`.
- [ ] Confirm spectral coordinate names and actual source of lambda_1/2/3; do not infer them from candidate strings.
- [ ] Add topic-specific dispatch receipts, noTypedMeet, worker lane identity and remote reconciliation; this module currently only computes candidate allocations.
- [ ] Run pytest, PG integration and Agda/Lean kernel parity checks. Do not activate unattended AO yet.

### Verification

```bash
PYTHONPATH=. python -m pytest tests/test_postgres_world_rejuvenation.py -v
PYTHONPATH=. python -m pytest tests/test_postgres_semantic_registration.py -v
PYTHONPATH=. python -m pytest tests/test_semantic_rejuvenation.py -v
```

Source-written only, no new executed test receipt. PostgreSQL is the only database used by the new module; the legacy runner is not yet retired. Agda remains golden, Lean secondary. Original authors and Ibrahim Monster 3B attribution remain unchanged.


## Coalescent manifold rejuvenation (2026-10-29)

- [x] Proposed coalescence clusters from exact PostgreSQL dependency_ref→message_ref join and persisted candidate lemmas, using deterministic complete-link overlap; requires identical topic/sub-eigen and at least one shared explicit deliverable.
- [x] Multi-parent world capsule retains separate parent provenance, supplied spectral coordinates and adjacent PostgreSQL candidates; no generic TOE prompts.
- [x] Source-written deterministic tests in `tests/test_coalescent_rejuvenation.py`.
- [ ] Verify `dependency_ref` is in fact an exact `message_ref` in the production dataset; otherwise use the existing ITIR graph evidence route. No synthetic identity promotion.
- [ ] Inspect `operational.rejuvenation_job` and `operational.conversation_lineage` actual DDL before multi-parent writes. Do not guess keys or retire parents prematurely.
- [ ] Implement transactional multi-parent lineage and atomic parent queue transitions once verified; recovery must preserve the ambiguous-send reconciliation barrier.
- [ ] Integrate with ITIR PR #53 `execute_pareto_conductor`, enforce per-worker lane identity/noTypedMeet and a 20-capacity cap.
- [ ] Bind actual manifold JSON layout and StatiBaker/Kanban deliverable evidence, run PG integration and pytest. Current proposals are not verified completion.
- [ ] Add Agda golden coalescence preservation lemmas, then Lean mirror and TOE import; kernel receipts required before calling them verified.
- [ ] Retire the legacy SQLite runner and all .slrw dependencies only after a validated replacement, never by blind deletion.

```bash
PYTHONPATH=. python -m pytest tests/test_coalescent_rejuvenation.py -v
PYTHONPATH=. python -m pytest tests/test_postgres_world_rejuvenation.py -v
```

Formal boundary: coalescence does not prove statement identity, theorem equivalence, or semantic authority. Attribution preserves original mathematical authors, repository PR ownership, independent runtime execution and kernel verification. Preserve Ibrahim Monster 3B ownership. No ESD changes.


## Automatic formal evidence tranche (2026-10-29)

- [x] Bounded PostgreSQL candidate-factor retrieval for formal-file linked topics.
- [x] Safe repo-root constrained Agda file lookup; deterministic fixed-string ripgrep search and timeout-bounded `dashi-agda-preflight --json` invocation.
- [x] Inject formal evidence automatically into single-thread capsules when `formal_repo_root` is supplied, and propagate to coalesced parents.
- [x] Source-written mock tests `tests/test_automated_formal_capsule.py`.
- [ ] **Critical**: replace surface-token gap diagnostic with `ExactGapComparator`'s exact (role,lemma,dependency) multiset logic refactored to read PostgreSQL rather than `.slrw`. Existing comparator defaults to a binary `.slrw` file and MUST NOT be called in that mode.
- [ ] Invoke actual tree-sitter AST symbol index and `itir.formal.inspect` / `itir.formal.certify` / `itir.ao.gap_check` through verified adapters; current ripgrep is source search, not AST proof search.
- [ ] Obtain verified `kernel_verified` schema/attestation source and join exact topic evidence; current implementation explicitly does not invent these receipts.
- [ ] Configure `formal_repo_root` in the conductor entrypoint; without it automatic preflight is not activated.
- [ ] Run tests and real preflight with available local tools; no execution receipt from this session.
- [ ] Add Agda golden evidence-kind and nonpromotion lemmas, then Lean mirror and TOE imports; current changes are Python-only.

```bash
PYTHONPATH=. python -m pytest tests/test_automated_formal_capsule.py -v
PYTHONPATH=. python -m pytest tests/test_postgres_world_rejuvenation.py tests/test_coalescent_rejuvenation.py -v
```

Attribution: search hits, lexical gaps, preflight output, and PostgreSQL candidate factors are NOT kernel-verified proofs. Preserve dated mathematical sources, independent repository ownership and Ibrahim Monster 3B boundaries. No ESD changes.

## Bounded formal slicing tranche

- [x] Source-written bounded lexical fallback in `re_gpt/formal_slicer.py`, including file/line receipts and explicit non-standalone status.
- [x] Capsule embedding when topic supplies `formal_symbols` and `formal_files`.
- [x] Deterministic tests in `tests/test_formal_slicer.py` (not yet executed).
- [ ] Bind verified agda_preflight AST index and related-symbol APIs. Repository paths inspected did not expose the requested API.
- [ ] Prove dependency closure including imports, constructors, fixities and scoped names; kernel-check generated standalone modules.
- [ ] Add Agda golden formal model, Lean mirror, and TOE imports.
- [ ] Verify exact similarity categories and preserve author attribution, Ibrahim ownership, and ESD import-only boundaries.

Run: `PYTHONPATH=. python -m pytest tests/test_formal_slicer.py tests/test_automated_formal_capsule.py -v`

## Round 1/10 — Topic 1: collapse(auto,last), expand(now) (2026-10-29)

### Docs → TODOs → Code → Changelog alignment

**Observed**: `postgres_world_rejuvenation.py` accepts a supplied manifold and can attach formal evidence when `formal_repo_root` is supplied. `automatic_formal_evidence.py` uses candidate factors, lexical token gaps, fixed-string search and optional bounded source slices; it does not demonstrate native commit/PR lineage or kernel verification. `DASHI/Interop/SemanticThreadRejuvenationExact.agda` is the golden single-parent lineage/nonpromotion contract; `AgdaMirror/SemanticThreadRejuvenation.lean` is secondary. Neither is a runtime correctness proof.

**Conductor**: Topic 1 has 1 allocated worker and reported sizing benefit 0.630; this is an input receipt, not a recomputed result. ITIR PR #53 defines sequential width 1, frontier-blocker width by distinct blockers, parallel width by sub-eigen dimension, capacity bounded and noTypedMeet gated. PR #52 offers PostgreSQL with an SLRW fallback; the latter is forbidden for this rejuvenation path. Supplied merge commits `b3ce2c5` and `e3f0b7c` require branch reachability verification; PR search still reports #52/#53 open, so do not infer merged state from abbreviated commit messages alone.

**Lineage evidence supplied, not independently verified**: `5a98d42`, `deab692`, `d4607b7`. Candidate lexical PR numbers and commit hashes must be resolved to exact repository, SHA, file/line, PR head/base, author, timestamp and verified receipt before contributing to completed goals. Duplicate historical PR references do not increase proof count.

### TODOs — next convergence gate

- [ ] Locate the actual source branch of the reported `deab692` Git/PR lineage injection; compare with this branch's implementation, then reuse or cherry-pick without duplicate logic.
- [ ] Add typed lineage inputs to world and coalesced capsules; preserve parent IDs, source revision and PR/commit provenance separately from kernel-verified proof status.
- [ ] Verify Topic 1's `collapse(property:auto,last)` and `expand(temporal:now)` meanings against actual topic data before transforming anything. No semantic equivalence is implied by labels.
- [ ] Reconcile `pnf_work_packet_v1.md` lexical PR candidate receipts with the PostgreSQL-only policy; do not adopt its SQLite canonical store.
- [ ] Confirm Round193 four consensus symbols and two blockers by reading exact declarations and dependent proofs before updating completed-goal counts.
- [ ] Golden Agda: model bounded provenance, source/PR/commit receipts, explicit nonpromotion and multi-parent lineage with `{-# OPTIONS --safe #-}`; zero postulates, unsolved metas, TERMINATING pragmas. Mirror checked statements in Lean after Agda kernel success; attach to TOE imports without ESD edits.
- [ ] Verify PostgreSQL schema joins and deterministic integration tests, then run pytest, Agda and Lean; do not claim CI/kernel success without execution.
- [ ] Update root ROADMAP, planning and changelog in their actual owning repositories after locating canonical paths; root ROADMAP.md was not present on this branch.

### Changelog — source-written documentation only

- 2026-10-29: recorded Round 1/10 topic allocation, conductor/PG spine policy conflict, lineage source reconciliation and remaining Agda/Lean/code verification gates. No runtime, schema, kernel or test change is claimed by this entry.

## Round 2/10 — Topic 2: Explain torque better (2026-10-29)

### Docs — input receipts and scope

- Topic ID 2, lead `Explain torque better`, one allocated worker, reported sizing benefit 0.776. Allocation is supplied, not recomputed.
- User-reported shared AST invariant: `round191CompleteProductionEnergyLedgerClosed`. Treat as a lookup target until exact Agda owner, declaration, import closure and kernel receipt are verified. A production energy ledger is not automatically a torque theorem.
- User-reported universal blockers: `round193ClayPromotion` and `round193PackageAClosed`. These may be global Round193 obligations, but they are not automatically typed prerequisites of the torque topic. Require explicit dependency edges and `noTypedMeet` before assigning either blocker to this worker.
- Supplied PR/commit context: PRs #49–#53 and short SHAs `b3ce2c5`, `e3f0b7c`, `5a98d42`, `deab692`, `d4607b7`. Do not infer merge status or proof discharge from descriptions or abbreviated IDs.

### TODOs — dependency-aware convergence

- [ ] Search golden Agda and Lean for torque, angular momentum, force, lever arm, work, and energy relations before constructing new lemmas. Reuse or unify existing exact owners; preserve mathematical authorship.
- [ ] Resolve `round191CompleteProductionEnergyLedgerClosed` to an exact qualified Agda declaration and verify whether its typed signature can be reused for a torque explanation; if unrelated, keep it as adjacent context only.
- [ ] Resolve the two Round193 blockers to exact signatures, dependencies and proof statuses; do not spend Topic 2's sole lane on an untyped global blocker.
- [ ] Produce a bounded topic-specific formal slice and a clear explanation linking torque to moment arm and angular acceleration only after checking the selected model's assumptions and units.
- [ ] Encode source/proof/commit lineage and nonpromotion in safe golden Agda (zero postulates, unsolved metas or TERMINATING pragmas); mirror verified Agda statements in Lean and attach to TOE imports without ESD edits.
- [ ] Reconcile actual lineage injection on the reported commit `deab692`, plus PostgreSQL-only capsule integration, against this branch's code before claiming runtime parity.
- [ ] Run deterministic tests, preflight, Agda and Lean verification when local checkout and tools are available; record exact receipts.

### Code status and changelog

- No torque-specific implementation, Agda theorem, Lean mirror or runtime behavior was added in this documentation tranche.
- 2026-10-29: documented Round 2's lead, allocation, consensus symbol, blockers and typed-gating requirements; recorded that source-reported consensus is not a kernel verification receipt.

## Round 3/10 — Topic 3: Implement Max Cut Changes (2026-10-29)

### Docs / scope

- Lead Topic 3, one worker, reported sizing benefit 0.741; these are supplied conductor inputs, not independently recomputed.
- AST triangulation reports zero shared invariants, zero universal blockers and no frontier targets. **Absence of returned candidates is not evidence that the repository has no matching proofs.** Preserve discovery uncertainty.
- The title `Implement Max Cut Changes` is ambiguous across graph optimization, geometric max-cut, proof-governance max-cut and repository-specific convergence. Do not select an owner by title alone. Resolve from source conversation IDs, PNF typed factors, formal files and exact PR/commit lineage before editing mathematics.
- Latest GitHub PR search contains several distinct max-cut lanes, including R2P2/369 Fourier symmetry, Alexander horned C3 topology and foundational monodromy. These are candidates for reuse-first inspection, not interchangeable theorems.
- Preserve PostgreSQL-only world/gap search, exact source provenance and original author/PR proof ownership. No ESD surface edits; imports only.

### TODO / implementation gates

- [ ] Retrieve Topic 3's canonical conversation/PNF source coordinates, target files, named obligations and typed scope; disambiguate which max-cut construction is intended.
- [ ] Inspect relevant golden Agda declarations and their import graph; run related_symbols/discovery and AST dedup before constructing or extending lemmas.
- [ ] Check `dashi_lean4` for existing mirror and only mirror verified golden statements; enforce `{-# OPTIONS --safe #-}`, zero postulates, unsolved metas and TERMINATING pragmas.
- [ ] Reconcile supplied Git short hashes `b3ce2c5`, `e3f0b7c`, `5a98d42`, `deab692`, `d4607b7` against exact commit/PR provenance. Do not count Git or similarity as kernel evidence.
- [ ] If a typed max-cut obligation is established, implement in existing Agda owner first, then Lean, Python integration, focused tests and TOE import roots. Keep docs and changelog in step with actual changes.
- [ ] Verify PR #53 one-worker allocation against typed `noTypedMeet` and cost law; do not fabricate blocker lanes when the frontier is empty.
- [ ] Execute local tests and kernel checks before marking any implementation or theorem discharged.

### Code / changelog receipt

- 2026-10-29: Round 3 source and plan reconciled. No code, Agda, Lean, theorem, test execution or kernel verification is claimed in this documentation-only tranche.

## Round 4/10 — Topic 4: SensibLaw Agda Test Case (2026-10-29)

### Docs — topic and evidence boundaries

- Lead topic 4, two allocated workers, reported sizing benefit 1.536; supplied scheduler receipt, not recomputed.
- The round supplies no AST consensus symbols, explicit blockers, target case, source judgment, or expected legal result. Do not invent a SensibLaw test case or infer legal validity from a generic proof contract.
- Existing golden `DASHI/Interop/SemanticThreadRejuvenationExact.agda` and Lean mirror are about provenance/lineage, not SensibLaw legal reasoning. Reuse only their typed nonpromotion and PostgreSQL-only authority boundaries if relevant.
- Agda PR search found legal-compliance/safe-just governance PR #1430 and related #1424, and PNF exact-work admission PR #1427. They are research candidates; neither proves a particular SensibLaw case.
- Two workers require explicit independent lane IDs and `noTypedMeet` evidence under ITIR PR #53. Never duplicate the same case/proof lane merely to fill capacity.

### TODOs — Docs → TODOs → Code → Changelog

- [ ] Retrieve Topic 4's exact SensibLaw case fixture, source judgment, legal issue, jurisdiction, expected outcome and evidence provenance from the canonical PostgreSQL corpus; abstain on missing fields.
- [ ] Search actual golden Agda SensibLaw/case comparison/legal-governance owners, their signatures, dependencies and TOE imports; deduplicate before adding any new declaration.
- [ ] Partition two workers into independent typed lanes only after `noTypedMeet` (e.g., source/citation provenance versus substantive rule applicability, if those are genuinely distinct in the source case).
- [ ] Formalize case-specific assumptions and testable outcome in safe Agda with no postulates, holes or termination pragmas; never infer judicial correctness from type checking alone.
- [ ] Mirror only the validated golden Agda contract in Lean; align Python SensibLaw fixture, PostgreSQL source refs and formal gap receipts, with no ESD edits.
- [ ] Reconcile Git/PR lineage commits against exact repository SHAs, author, PR status and kernel/test receipts; do not treat PR descriptions as verified proof.
- [ ] Run focused tests and kernel checks and record executed receipts before marking any case test complete.

### Code / changelog receipt

- 2026-10-29: Round 4 planning synchronized, including two-worker typed-independence gate and missing-case abstention. No SensibLaw test fixture, Agda proof, Lean theorem, Python code, executed test or kernel check was produced in this documentation-only tranche.
