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
