# Owned-session CFFI renewal and PG hydration authentication gate (2026-10-30)

## Reuse-first findings
- Existing `SessionLifecycle` manages redacted status/doctor, token expiry, optional precommit bootstrap and displayless browser refresh.
- re-gpt MCP `chatgpt_session_refresh` and ITIR `itir.chatgpt.session_refresh` are **browser-replay** paths.
- ITIR `itir.chatgpt.cdp_capture` is a separate explicit CDP capture path.
- Still-open re-gpt PR #56 unifies authenticated roster/thread GETs using the same curl_cffi session, but its operational storage remains SQLite.
- Still-open re-gpt PR #60 and ITIR #61 add canonical PostgreSQL roster/hydration, pending live write/auth acceptance.

## This cut
Added `SessionLifecycle.refresh_cffi`, `chatgpt_session_refresh_cffi` and corresponding ITIR MCP tool. They renew the access token by presenting the **existing owned session cookie** through the existing AsyncChatGPT curl_cffi authentication flow. No Chromium, CDP, browser DOM, token export or fallback is requested. The snapshot is atomically updated only after renewal succeeds. Failure leaves previous material unchanged, and a missing cookie fails before contacting the network.

## Explicit limitations
This is **not** a magic login mechanism. If the session cookie has expired, been revoked, is incomplete/chunked, or account policy requires interactive verification, the agent must report that and stop. Do not fake accessToken, mint cookies, evade challenges, or automatically repeat rejected logins. No periodic refresh loop is added: a future scheduled action may renew ahead of known expiry with jitter, bounded attempts, and operator-visible receipts, but should never attempt infinite reauthentication or silently trigger CDP. Credential material remains in the owned snapshot, not PG chat corpus or tool output.

## Integration order
1. Run `session_status` / doctor. If a valid owned session cookie is available, explicitly invoke CFFI renewal.
2. Incorporate PR #56's common authenticated CFFI roster/client path into PR #60's PG-only storage.
3. Run one-page roster → PG identity commit → selected exact graph → PG message commit; validate against real DB before enabling queue.
4. Once accepted, activate explicit bounded pending hydration, then optional scheduled refresh.

Kernel proofs were not run. Source-written tests do not establish live authentication success.
