# PostgreSQL-first terminal conversation read (2026-10-30)

## Decision
TUI history and resume load use ITIR's canonical PostgreSQL corpus rather than the network roster. re-gpt owns ChatGPT transport; SLR owns production semantic read models; golden DASHI Agda owns projection/nonpromotion contracts; SensibLaw is deprecated reference. Upstream tritao/chatgpt-web supplies the TUI experience and was not copied. No TUI, tmux or socket is required for an MCP agent to use repository-native account services.

## Reused in-repo schema
Confirmed against ITIR-suite/scripts/migrate_sqlite_to_postgres_corpus.py: `corpus.chat_archive_message` contains `conversation_ref`, `node_ref`, `parent_node_ref`, `role_ref`, `thread_title`, `message_time_ref`, `body_storage_ref`, provenance refs and `identity_verified`. Existing SLR crate `sl-pg-source-store` persists chat archive message fixtures. This adapter reads that existing table; it does not create another database, semantic authority or migration.

## Implemented
- `re_gpt/pg_conversation_read.py` list/search and bounded thread read using bound SQL values. Fail closed if PG is unavailable, instead of falling back to SQLite or silently pretending a network error means zero messages.
- `upstream_tui_adapter.py` now uses PG for the resume picker and selected thread load.
- Regression fixtures for SQL bindings and exact stored source bodies.

## Operator requirements
Set `ITIR_PG_DSN` to an authorised PostgreSQL DSN, and install `psycopg` in the re-gpt environment. No hardcoded password. The server must contain hydrated chat messages: missing records cannot be invented. The first picker load is bounded to 500 conversations; full pagination and server-side search integration in upstream's interactive picker are the next discriminator.

## Explicit remaining obligations
1. Verify actual deployed database schema and permissions; this code was source-written without a live PostgreSQL connection.
2. Wire a reusable canonical-history MCP read tool using the same shared projection, independent of the TUI; existing `itir.chatgpt.*` worker endpoints are separate.
3. Implement governed incremental network-to-PG hydration as explicit requested action. Use SLR source-store ingestion semantics and operation receipts. Do not write to deprecated SQLite or fetch on every read.
4. Verify branch-aware message reconstruction and source revisions; timestamp ordering alone is not a proof of active-path order.
5. Add workspace event queue, author and routing policy for @local/@both and auto-response with idempotency and turn budgets. Do not promote local-agent proposals into ChatGPT remote graph.
6. Complete LaTeX rendering and structured tool/reasoning-summary activity projection using existing upstream/ITIR facilities. No private hidden reasoning reconstruction.
7. TUI state socket remains optional for debugging a *running* UI; it is not a prerequisite for account access or programmatic search.

No local tests, CI or Agda/Lean kernel verification were executed in this GitHub-only implementation environment.

## Hydration workflow continuation (2026-10-30)

Implementation in `re_gpt/pg_hydration.py` adds an explicit PG-owned roster queue at `ops.chat_hydration_roster`, a bounded `roster_page` operation, `hydrate_one` (selected exact graph, five-level content-addressed PG ingest), and `hydrate_pending` (bounded worker pass). It reuses the existing ITIR corpus table identity and the `fetch_exact_graph` direct read broker. Nothing writes a second SQLite history store. The roster queue keeps `queued`, `hydrated` or `error` dispositions and attempt counts. The TUI initiates a best-effort background roster refresh once per picker load, serves already committed PG rows immediately, and fetches an unhydrated selected thread into PG before display. The response path hydrates after a proven complete mutation, without resubmitting that mutation.

The combined picker is backed by roster metadata and hydrated corpus records. **Its first returned list is a snapshot:** background discoveries appear the next time the picker is opened; reactive updates are still a residual. Durable worker jobs require a separately scheduled process/MCP invocation; a daemon thread is not a cross-restart background service. Queue failures use `error` and currently require an explicit retry policy to requeue; concurrency leasing, staleness TTL, revision-safe upsert and active-branch path identity remain residuals. The source-only Python importer is not yet verified against the deployed PG constraints nor checked for full parity with SLR's production Rust source-store writer; migrate ingestion to the SLR production adapter after checking exact schema.

MCP adds `itir.chatgpt.pg_roster_sync`, `itir.chatgpt.pg_hydrate_selected`, and `itir.chatgpt.pg_hydrate_pending` (mutating PG but never ChatGPT threads). The earlier `pg_search` and `pg_read` remain read-only. No agent requires a TUI, terminal or socket.

These additions are **source-written**, with new offline fixtures but no live database acceptance test for this tranche. The previous 35-conversation live query and 7-message read validate an earlier projection, not the new ingestion writer.
