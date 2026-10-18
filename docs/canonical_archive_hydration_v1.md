# Canonical chat archive consolidation v1 — contract and TODO

**Single corpus authority:** the structured SQLite archive selected by explicit `--archive-db` or `ITIR_CANONICAL_CHAT_ARCHIVE`, default `~/chat_archive.sqlite`. The historical `~/.chat_archive.sqlite` is a *different path*: if it exists and differs, do not auto-merge, move, or select it. An operator must explicitly select a path and run a separately reviewed migration with source-identity/conflict receipts. Re-gpt `~/.chatgpt_history.sqlite3` is a reconstructible graph/adapter/operation cache, **never AO corpus-selection authority**.

Graph hydration preserves platform/account/thread and stable message/node identity, parent/branch, timestamp and attachment pointers. A repeated identical identity+digest is a no-op; changed digest is an explicit conflict and must not overwrite canonical text. The canonical structurer ingest owns body storage. Cache may retain compact conflict/operation receipts only.

## TODO (before implementation)
- [ ] Wire exact confirmed graph reconciliation to bounded canonical archive hydration.
- [ ] Reject unverified graph identity and destructive message overwrite.
- [ ] Expose canonical archive candidate ledger to ITIR AO, with formal receipts and abstentions.
- [ ] Preserve governance: only existing authorized ITIR worker lane may submit; no automatic resend.
- [ ] Verify with focused tests; no CI.
