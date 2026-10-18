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
