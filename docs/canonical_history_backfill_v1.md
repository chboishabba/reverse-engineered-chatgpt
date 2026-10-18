# Canonical history → structurer backfill v1

## Contract and TODO (before code)

**Authority:** `~/chat_archive.sqlite` is the only raw-message corpus for AO. `~/.chatgpt_history.sqlite3` is a read-only historical source for this one-way migration, then a replaceable transport/operation cache. Do not read the cache for AO selection. `~/.chat_archive.sqlite` is a separate legacy path: never silently merge or select it. An explicit `--archive-db` overrides the canonical default. Require `--account` or `ITIR_CHATGPT_ACCOUNT`; no account inference.

The re-gpt legacy `messages` table has `conversation_id,message_index,author,content,create_time,message_key`, but not universal parent/node/attachment columns. Preserve available stable `message_key`; explicitly label index-derived IDs as **legacy synthetic** with unknown parent/branch and attachment provenance. Never fabricate verified graph coordinates. If graph data is available via producer-owned structures, use it instead in a separately verified migration. Identity `chatgpt|account|thread|source_message_id|node_id`; content SHA-256 is authoritative. Replays are no-ops, changed digests produce conflict receipts, and canonical-existing records must not be overwritten. Receipts live only in the canonical archive.

TODO:
- [ ] Implement bounded, resumable read-only cache scan with canonical-only receipts.
- [ ] Detect existing canonical message identities before calling the structurer producer.
- [ ] Test replay, changed digest, preexisting canonical body, account guard and batch resume.
- [ ] Verify against real structurer schema and run focused local tests.
- [ ] Do not touch 42 `async_starting` or 4 `commit_unknown` dead-letter operations; never resend or reconcile them.

## Delivered — initial implementation

`re_gpt.canonical_history_backfill` is an offline, one-way, batch-size-limited importer. It opens history read-only, calls only the existing structurer `ingest_parsed_messages` producer for raw body writes, and persists identity/digest/conflict receipts in the canonical archive. Account is mandatory; changed digests are not overwritten. A second run replays existing receipts. Existing canonical message identities are checked before ingest; the producer result is verified against canonical rows before receipt acknowledgement. The legacy flat cache has no verified parent/branch/attachment columns: these remain explicitly unknown, not invented.

**Schema gate:** this implementation deliberately fails closed if the live canonical `messages` table does not expose `platform`, `account_id`, `source_thread_id`, `source_message_id`, `node_id`, and `content`. Verify these columns against the live structurer schema before running a real migration. The existing ingest may store canonical content under a different column; this is a known integration risk. A future producer-owned lookup adapter should replace this direct schema check if needed.

**Local commands** (do not run on production before fixture validation):

```bash
python -m unittest discover -s tests -p 'test_canonical_history_backfill*.py' -v
ruff check re_gpt/canonical_history_backfill.py tests/test_canonical_history_backfill.py
python -m re_gpt.canonical_history_backfill --history-db ~/.chatgpt_history.sqlite3 --archive-db ~/chat_archive.sqlite --account YOUR_EXPLICIT_ACCOUNT --batch-size 250
```

**Outstanding:** live structurer schema compatibility, full legacy graph provenance recovery where possible, production-scale resume and concurrent-writer gate. This tranche does not touch 42 `async_starting` and 4 `commit_unknown` dead-letter operations, does not resend/reconcile, and does not implement PNF.
