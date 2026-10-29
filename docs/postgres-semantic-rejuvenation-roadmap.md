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
