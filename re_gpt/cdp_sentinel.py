"""Direct CDP WebSocket Sentinel Token Harvester for re_gpt.

Connects to a running Chromium instance on CDP port 9222 (used for accessibility
and debugging) over raw WebSocket, executing the in-page Sentinel attestation
(prepare -> proof-of-work -> Turnstile VM -> finalize) to obtain fresh
authenticated sentinel mutation tokens in <1 second without DOM automation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://127.0.0.1:9222"

SENTINEL_INPAGE_JS = """
(async () => {
    const sessRes = await fetch('/api/auth/session');
    const sess = await sessRes.json();
    const accountId = sess?.account?.id || '';

    function s(e, t) {
        let n = '';
        for (let a = 0; a < e.length; a++) n += String.fromCharCode(e.charCodeAt(a) ^ t.charCodeAt(a % t.length));
        return n;
    }
    function h(e) { return e[Math.floor(Math.random() * e.length)]; }
    function _(e) {
        let t = JSON.stringify(e);
        return btoa(String.fromCharCode(...new TextEncoder().encode(t)));
    }
    function f() {
        let e = performance.memory;
        return [
            (screen?.width || 1920) + (screen?.height || 1080),
            String(new Date()),
            e?.jsHeapSizeLimit ?? null,
            Math.random(),
            navigator.userAgent,
            h(Array.from(document.scripts).map(e => e?.src).filter(Boolean)),
            (Array.from(document.scripts || []).map(e => e?.src?.match('c/[^/]*/_')).filter(e => e?.length)[0] ?? [])[0] ?? document.documentElement.getAttribute('data-build') ?? 'prod',
            navigator.language,
            navigator.languages?.join(','),
            Math.random(),
            (() => {
                let e = h(Object.keys(Object.getPrototypeOf(navigator)));
                try { let t = navigator[e]; return e + '−' + String(t); } catch { return e; }
            })(),
            h(Object.keys(document)),
            h(Object.keys(window)),
            performance.now(),
            0,
            [...new URLSearchParams(window.location.search).keys()].join(','),
            navigator.hardwareConcurrency || 8,
            performance.timeOrigin,
            Number('ai' in window),
            Number('createPRNG' in window),
            Number('cache' in window),
            Number('data' in window),
            Number('solana' in window),
            Number('dump' in window),
            Number('InstallTrigger' in window)
        ];
    }
    function g() {
        let e = performance.now();
        try {
            let t = f();
            t[3] = 1;
            t[9] = performance.now() - e;
            return 'gAAAAAC' + _(t);
        } catch (err) {
            return 'wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D' + _(String(err));
        }
    }
    function solvePow(seed, difficulty) {
        let n = performance.now();
        try {
            let a = f();
            for (let r = 0; r < 5e5; r++) {
                a[3] = r;
                a[9] = Math.round(performance.now() - n);
                let i = _(a);
                let hash = ((e) => {
                    let t = 0x811c9dc5;
                    for (let n = 0; n < e.length; n++) {
                        t ^= e.charCodeAt(n);
                        t = Math.imul(t, 0x1000193) >>> 0;
                    }
                    t ^= t >>> 16;
                    t = Math.imul(t, 0x85ebca6b) >>> 0;
                    t ^= t >>> 13;
                    t = Math.imul(t, 0xc2b2ae35) >>> 0;
                    return ((t ^= t >>> 16) >>> 0).toString(16).padStart(8, '0');
                })(seed + i);
                if (hash.substring(0, difficulty.length) <= difficulty) {
                    return 'gAAAAAB' + i + '~S';
                }
            }
        } catch (err) {
            return 'wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D' + _(String(err));
        }
        return 'wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D' + _('e');
    }

    let r = new Map(), i = 0;
    async function l() {
        for (; r.get(9).length > 0;) {
            let [e, ...t] = r.get(9).shift() ?? [], n = r.get(e), a = n?.(...t);
            a && 'function' == typeof a.then && await Promise.resolve(a), i++;
        }
    }
    function solveDx(dx, requirementsKey) {
        return new Promise((resolve, reject) => {
            r.clear();
            r.set(0, t => solveDx(t, String(r.get(16))));
            r.set(1, (e, t) => r.set(e, s(String(r.get(e)), String(r.get(t)))));
            r.set(2, (e, t) => r.set(e, t));
            r.set(5, (e, t) => { let n = r.get(e); Array.isArray(n) ? n.push(r.get(t)) : r.set(e, n + r.get(t)); });
            r.set(27, (e, t) => { let n = r.get(e); Array.isArray(n) ? n.splice(n.indexOf(r.get(t)), 1) : r.set(e, n - r.get(t)); });
            r.set(29, (e, t, n) => r.set(e, Number(r.get(t)) < Number(r.get(n))));
            r.set(33, (e, t, n) => r.set(e, Number(r.get(t)) * Number(r.get(n))));
            r.set(35, (e, t, n) => { let a = Number(r.get(n)); r.set(e, 0 === a ? 0 : Number(r.get(t)) / a); });
            r.set(6, (e, t, n) => { let a = r.get(t); r.set(e, a[String(r.get(n))]); });
            r.set(7, function(e, ...args) { return r.get(e)(...args.map(x => r.get(x))); });
            r.set(17, function(e, t, ...args) {
                try {
                    let n = r.get(t)(...args.map(x => r.get(x)));
                    if (n && typeof n.then === 'function') return n.then(res => r.set(e, res)).catch(err => r.set(e, String(err)));
                    r.set(e, n);
                } catch (err) { r.set(e, String(err)); }
            });
            r.set(13, function(e, t, ...args) { try { r.get(t)(...args); } catch(err) { r.set(e, String(err)); } });
            r.set(8, (e, t) => r.set(e, r.get(t)));
            r.set(10, window);
            r.set(11, (e, t) => r.set(e, (Array.from(document.scripts || []).map(e => e?.src?.match(String(r.get(t)))).filter(e => e?.length)[0] ?? [])[0] ?? null));
            r.set(12, e => r.set(e, r));
            r.set(14, (e, t) => r.set(e, JSON.parse(String(r.get(t)))));
            r.set(15, (e, t) => r.set(e, JSON.stringify(r.get(t))));
            r.set(18, e => r.set(e, atob(String(r.get(e)))));
            r.set(19, e => r.set(e, btoa(String(r.get(e)))));
            r.set(20, function(e, t, n, ...args) { return r.get(e) === r.get(t) ? r.get(n)(...args) : null; });
            r.set(21, function(e, t, n, a, ...args) { return Math.abs(Number(r.get(e)) - Number(r.get(t))) > Number(r.get(n)) ? r.get(a)(...args) : null; });
            r.set(23, function(e, t, ...args) { return void 0 !== r.get(e) ? r.get(t)(...args) : null; });
            r.set(24, (e, t, n) => { let a = r.get(t), i = a[String(r.get(n))]; r.set(e, i.bind(a)); });
            r.set(34, (e, t) => Promise.resolve(r.get(t)).then(res => r.set(e, res)));
            r.set(22, (e, t) => { let n = [...r.get(9)]; r.set(9, [...t]); return l().catch(err => r.set(e, String(err))).finally(() => r.set(9, n)); });
            r.set(28, () => void 0); r.set(26, () => void 0); r.set(25, () => void 0);
            i = 0; r.set(16, requirementsKey);
            let done = !1;
            setTimeout(() => { if (!done) { done = !0; resolve(String(i)); } }, 500);
            r.set(3, e => { if (!done) { done = !0; resolve(btoa(String(e))); } });
            r.set(4, e => { if (!done) { done = !0; reject(btoa(String(e))); } });
            r.set(30, (e, t, n, a) => {
                let i = Array.isArray(a), o = i ? n : [], s = (i ? a : n) ?? [];
                r.set(e, function(...args) {
                    if (done) return;
                    let d = [...r.get(9)];
                    if (i) for (let k = 0; k < o.length; k++) r.set(o[k], args[k]);
                    return r.set(9, [...s]), l().then(() => r.get(t)).catch(err => String(err)).finally(() => r.set(9, d));
                });
            });
            try {
                r.set(9, JSON.parse(s(atob(dx), String(r.get(16)))));
                l().catch(err => resolve(btoa(i + ': ' + String(err))));
            } catch(err) {
                resolve(btoa(i + ': ' + String(err)));
            }
        });
    }

    const reqKey = g();
    const prepHeaders = {'content-type': 'application/json'};
    if (accountId) prepHeaders['chatgpt-account-id'] = accountId;

    const prepRes = await fetch('/backend-api/sentinel/chat-requirements/prepare', {
        method: 'POST',
        headers: prepHeaders,
        body: JSON.stringify({p: reqKey})
    });
    const prepData = await prepRes.json();
    if (!prepRes.ok) return {error: 'prepare_failed', status: prepRes.status, prepData};

    let proofToken = null;
    if (prepData.proofofwork?.required) {
        proofToken = solvePow(prepData.proofofwork.seed, prepData.proofofwork.difficulty);
    }
    let turnstileToken = null;
    if (prepData.turnstile?.required && prepData.turnstile.dx) {
        turnstileToken = await solveDx(prepData.turnstile.dx, reqKey);
    }

    const finHeaders = {'content-type': 'application/json'};
    if (accountId) finHeaders['chatgpt-account-id'] = accountId;

    const finRes = await fetch('/backend-api/sentinel/chat-requirements/finalize', {
        method: 'POST',
        headers: finHeaders,
        body: JSON.stringify({
            prepare_token: prepData.prepare_token,
            proofofwork: proofToken || undefined,
            turnstile: turnstileToken || undefined
        })
    });
    const finData = await finRes.json();
    if (!finRes.ok || !finData.token) return {error: 'finalize_failed', status: finRes.status, finData};

    return {
        chat_requirements_token: String(finData.token || ''),
        proof_token: proofToken ? String(proofToken) : '',
        turnstile_token: turnstileToken ? String(turnstileToken) : '',
        prepare_token: String(prepData.prepare_token || ''),
        expire_after: Number(finData.expire_after || 540),
        expire_at: Number(finData.expire_at || (Date.now() / 1000 + 540)),
        persona: String(finData.persona || 'chatgpt-paid'),
        captured_at: Date.now() / 1000,
        consumed: false,
    };
})()
"""


def find_chatgpt_tab_ws_url(cdp_url: str = DEFAULT_CDP_URL, timeout: float = 2.0) -> str | None:
    """Find the WebSocket URL for an active ChatGPT tab via CDP HTTP JSON API."""
    endpoint = cdp_url.rstrip("/")
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = f"http://{endpoint}"
    url = f"{endpoint}/json"
    try:
        req = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(req.read().decode("utf-8"))
        for target in data:
            target_url = str(target.get("url") or "")
            target_type = str(target.get("type") or "")
            if "chatgpt.com" in target_url and target_type == "page":
                ws_url = target.get("webSocketDebuggerUrl")
                if ws_url:
                    return str(ws_url)
    except Exception as exc:
        logger.debug("Failed to query CDP tabs at %s: %s", url, exc)
    return None


async def mint_sentinel_tokens_via_cdp(
    cdp_url: str = DEFAULT_CDP_URL,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Mint fresh Sentinel tokens using in-browser execution over raw WebSocket CDP."""
    ws_url = find_chatgpt_tab_ws_url(cdp_url=cdp_url, timeout=min(2.0, timeout))
    if not ws_url:
        logger.debug("No active ChatGPT tab found on CDP %s", cdp_url)
        return None

    try:
        import websockets
    except ImportError:
        logger.warning("websockets library not installed; cannot connect to CDP WebSocket")
        return None

    try:
        async with websockets.connect(ws_url, max_size=None, close_timeout=2.0) as ws:
            req_id = int(time.time() * 1000) % 1000000
            msg = {
                "id": req_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": SENTINEL_INPAGE_JS,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            }
            await ws.send(json.dumps(msg))
            start_wait = time.time()
            while time.time() - start_wait < timeout:
                try:
                    raw_reply = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    reply = json.loads(raw_reply)
                    if reply.get("id") == req_id:
                        res = reply.get("result", {}).get("result", {}).get("value")
                        if isinstance(res, dict) and res.get("chat_requirements_token"):
                            return res
                        if isinstance(res, dict) and res.get("error"):
                            logger.warning("In-page sentinel minting returned error: %s", res)
                            return None
                except asyncio.TimeoutError:
                    break
    except Exception as exc:
        logger.warning("CDP sentinel minting error: %s", exc)
    return None


def mint_sentinel_tokens_sync(
    cdp_url: str = DEFAULT_CDP_URL,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Synchronous entrypoint for minting fresh Sentinel tokens via CDP."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, mint_sentinel_tokens_via_cdp(cdp_url, timeout)).result()
        return loop.run_until_complete(mint_sentinel_tokens_via_cdp(cdp_url, timeout))
    except Exception:
        return asyncio.run(mint_sentinel_tokens_via_cdp(cdp_url, timeout))


async def capture_snapshot_via_cdp(
    cdp_url: str = DEFAULT_CDP_URL,
    profile_dir: str | None = None,
    timeout: float = 5.0,
) -> Any | None:
    """Fast in-browser capture of complete authenticated snapshot via raw CDP WebSocket."""
    from .browser_session_replay import BrowserSessionSnapshot

    ws_url = find_chatgpt_tab_ws_url(cdp_url=cdp_url, timeout=min(2.0, timeout))
    if not ws_url:
        return None

    try:
        import websockets
    except ImportError:
        return None

    try:
        async with websockets.connect(ws_url, max_size=None, close_timeout=2.0) as ws:
            # 1. Network cookies
            await ws.send(json.dumps({
                "id": 1,
                "method": "Network.getCookies",
                "params": {"urls": ["https://chatgpt.com"]},
            }))
            cookie_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            raw_cookies = cookie_msg.get("result", {}).get("cookies", [])
            cookie_list = [dict(c) for c in raw_cookies if isinstance(c, dict)]
            cookie_lookup = {str(c.get("name") or ""): str(c.get("value") or "") for c in cookie_list}

            # 2. In-page evaluate for session & sentinel
            req_id = 2
            await ws.send(json.dumps({
                "id": req_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": SENTINEL_INPAGE_JS,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            }))
            eval_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            sentinel_data = eval_msg.get("result", {}).get("result", {}).get("value", {})
            if not isinstance(sentinel_data, dict):
                sentinel_data = {}

            # 3. Environment & auth info
            env_expr = """
            (async () => {
                let session = {};
                try {
                    const resp = await fetch("/api/auth/session");
                    if (resp.ok) session = await resp.json();
                } catch (_) {}
                return {
                    accessToken: session.accessToken || null,
                    accountId: (session.account && session.account.id) || null,
                    userAgent: navigator.userAgent || null,
                    language: navigator.language || "en-US",
                    languages: Array.isArray(navigator.languages) ? navigator.languages : ["en-US"],
                    platform: navigator.platform || "",
                    hardwareConcurrency: navigator.hardwareConcurrency || null,
                    deviceMemory: navigator.deviceMemory || null,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
                    timezone_offset_min: new Date().getTimezoneOffset(),
                    innerWidth: window.innerWidth || 1440,
                    innerHeight: window.innerHeight || 900,
                    devicePixelRatio: window.devicePixelRatio || 1,
                    isDarkMode: Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches),
                };
            })()
            """
            await ws.send(json.dumps({
                "id": 3,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": env_expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            }))
            env_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            env_data = env_msg.get("result", {}).get("result", {}).get("value", {})
            if not isinstance(env_data, dict):
                env_data = {}

            access_token = str(env_data.get("accessToken") or "").strip() or None
            account_id = str(env_data.get("accountId") or cookie_lookup.get("_account") or "").strip() or None
            device_id = str(cookie_lookup.get("oai-did") or "").strip() or None
            user_agent = str(env_data.get("userAgent") or "").strip() or None

            fingerprint = {
                "userAgent": user_agent,
                "language": env_data.get("language"),
                "languages": env_data.get("languages"),
                "platform": env_data.get("platform"),
                "hardwareConcurrency": env_data.get("hardwareConcurrency"),
                "deviceMemory": env_data.get("deviceMemory"),
                "timezone": env_data.get("timezone") or "UTC",
                "timezone_offset_min": int(env_data.get("timezone_offset_min") or 0),
                "innerWidth": env_data.get("innerWidth") or 1440,
                "innerHeight": env_data.get("innerHeight") or 900,
                "devicePixelRatio": env_data.get("devicePixelRatio") or 1,
                "isDarkMode": bool(env_data.get("isDarkMode")),
                "client_contextual_info": {
                    "app_name": "chatgpt.com",
                    "app_surface": "codex_browser",
                    "has_web_push_capabilities": True,
                    "web_push_notification_permission": "default",
                },
            }

            headers = {
                "user-agent": user_agent or "",
                "accept": "*/*",
                "accept-language": env_data.get("language") or "en-US",
                "content-type": "application/json",
                "oai-device-id": device_id or "",
                "sec-ch-ua-platform": f'"{env_data.get("platform", "Linux")}"',
            }
            if account_id:
                headers["chatgpt-account-id"] = account_id

            return BrowserSessionSnapshot(
                captured_at=time.time(),
                profile_dir=profile_dir,
                access_token=access_token,
                account_id=account_id,
                device_id=device_id,
                user_agent=user_agent,
                cookies=cookie_list,
                headers=headers,
                fingerprint=fingerprint,
                sentinel=sentinel_data,
            )
    except Exception as exc:
        logger.warning("Fast CDP snapshot capture failed: %s", exc)
        return None


INPAGE_DISPATCH_JS = """
async ({conversationId, prompt, messageId}) => {
    const sessRes = await fetch('/api/auth/session');
    const sess = await sessRes.json();
    const accountId = sess?.account?.id || '';
    const accessToken = sess?.accessToken || '';

    const authHeaders = {
        'Authorization': 'Bearer ' + accessToken,
        'chatgpt-account-id': accountId
    };

    const convRes = await fetch('/backend-api/conversation/' + conversationId, {
        headers: authHeaders
    });
    if (!convRes.ok) {
        return {ok: false, stage: 'conversation-get', status: convRes.status, body: await convRes.text()};
    }
    const convData = await convRes.json();
    const currentNode = convData.current_node;
    const modelSlug = convData.default_model_slug || 'gpt-4';

    function s(e, t) {
        let n = '';
        for (let a = 0; a < e.length; a++) n += String.fromCharCode(e.charCodeAt(a) ^ t.charCodeAt(a % t.length));
        return n;
    }
    function h(e) { return e[Math.floor(Math.random() * e.length)]; }
    function _(e) {
        let t = JSON.stringify(e);
        return btoa(String.fromCharCode(...new TextEncoder().encode(t)));
    }
    function f() {
        let e = performance.memory;
        return [
            (screen?.width || 1920) + (screen?.height || 1080),
            String(new Date()),
            e?.jsHeapSizeLimit ?? null,
            Math.random(),
            navigator.userAgent,
            h(Array.from(document.scripts).map(e => e?.src).filter(Boolean)),
            (Array.from(document.scripts || []).map(e => e?.src?.match('c/[^/]*/_')).filter(e => e?.length)[0] ?? [])[0] ?? document.documentElement.getAttribute('data-build') ?? 'prod',
            navigator.language,
            navigator.languages?.join(','),
            Math.random(),
            (() => {
                let e = h(Object.keys(Object.getPrototypeOf(navigator)));
                try { let t = navigator[e]; return e + '−' + String(t); } catch { return e; }
            })(),
            h(Object.keys(document)),
            h(Object.keys(window)),
            performance.now(),
            0,
            [...new URLSearchParams(window.location.search).keys()].join(','),
            navigator.hardwareConcurrency || 8,
            performance.timeOrigin,
            Number('ai' in window),
            Number('createPRNG' in window),
            Number('cache' in window),
            Number('data' in window),
            Number('solana' in window),
            Number('dump' in window),
            Number('InstallTrigger' in window)
        ];
    }
    function g() {
        let e = performance.now();
        try {
            let t = f();
            t[3] = 1;
            t[9] = performance.now() - e;
            return 'gAAAAAC' + _(t);
        } catch (err) {
            return 'wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D' + _(String(err));
        }
    }
    function solvePow(seed, difficulty) {
        let n = performance.now();
        try {
            let a = f();
            for (let r = 0; r < 5e5; r++) {
                a[3] = r;
                a[9] = Math.round(performance.now() - n);
                let i = _(a);
                let hash = ((e) => {
                    let t = 0x811c9dc5;
                    for (let n = 0; n < e.length; n++) {
                        t ^= e.charCodeAt(n);
                        t = Math.imul(t, 0x1000193) >>> 0;
                    }
                    t ^= t >>> 16;
                    t = Math.imul(t, 0x85ebca6b) >>> 0;
                    t ^= t >>> 13;
                    t = Math.imul(t, 0xc2b2ae35) >>> 0;
                    return ((t ^= t >>> 16) >>> 0).toString(16).padStart(8, '0');
                })(seed + i);
                if (hash.substring(0, difficulty.length) <= difficulty) {
                    return 'gAAAAAB' + i + '~S';
                }
            }
        } catch (err) {
            return 'wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D' + _(String(err));
        }
        return 'wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D' + _('e');
    }

    let r = new Map(), i = 0;
    async function l() {
        for (; r.get(9).length > 0;) {
            let [e, ...t] = r.get(9).shift() ?? [], n = r.get(e), a = n?.(...t);
            a && 'function' == typeof a.then && await Promise.resolve(a), i++;
        }
    }
    function solveDx(dx, requirementsKey) {
        return new Promise((resolve, reject) => {
            r.clear();
            r.set(0, t => solveDx(t, String(r.get(16))));
            r.set(1, (e, t) => r.set(e, s(String(r.get(e)), String(r.get(t)))));
            r.set(2, (e, t) => r.set(e, t));
            r.set(5, (e, t) => { let n = r.get(e); Array.isArray(n) ? n.push(r.get(t)) : r.set(e, n + r.get(t)); });
            r.set(27, (e, t) => { let n = r.get(e); Array.isArray(n) ? n.splice(n.indexOf(r.get(t)), 1) : r.set(e, n - r.get(t)); });
            r.set(29, (e, t, n) => r.set(e, Number(r.get(t)) < Number(r.get(n))));
            r.set(33, (e, t, n) => r.set(e, Number(r.get(t)) * Number(r.get(n))));
            r.set(35, (e, t, n) => { let a = Number(r.get(n)); r.set(e, 0 === a ? 0 : Number(r.get(t)) / a); });
            r.set(6, (e, t, n) => { let a = r.get(t); r.set(e, a[String(r.get(n))]); });
            r.set(7, function(e, ...args) { return r.get(e)(...args.map(x => r.get(x))); });
            r.set(17, function(e, t, ...args) {
                try {
                    let n = r.get(t)(...args.map(x => r.get(x)));
                    if (n && typeof n.then === 'function') return n.then(res => r.set(e, res)).catch(err => r.set(e, String(err)));
                    r.set(e, n);
                } catch (err) { r.set(e, String(err)); }
            });
            r.set(13, function(e, t, ...args) { try { r.get(t)(...args); } catch(err) { r.set(e, String(err)); } });
            r.set(8, (e, t) => r.set(e, r.get(t)));
            r.set(10, window);
            r.set(11, (e, t) => r.set(e, (Array.from(document.scripts || []).map(e => e?.src?.match(String(r.get(t)))).filter(e => e?.length)[0] ?? [])[0] ?? null));
            r.set(12, e => r.set(e, r));
            r.set(14, (e, t) => r.set(e, JSON.parse(String(r.get(t)))));
            r.set(15, (e, t) => r.set(e, JSON.stringify(r.get(t))));
            r.set(18, e => r.set(e, atob(String(r.get(e)))));
            r.set(19, e => r.set(e, btoa(String(r.get(e)))));
            r.set(20, function(e, t, n, ...args) { return r.get(e) === r.get(t) ? r.get(n)(...args) : null; });
            r.set(21, function(e, t, n, a, ...args) { return Math.abs(Number(r.get(e)) - Number(r.get(t))) > Number(r.get(n)) ? r.get(a)(...args) : null; });
            r.set(23, function(e, t, ...args) { return void 0 !== r.get(e) ? r.get(t)(...args) : null; });
            r.set(24, (e, t, n) => { let a = r.get(t), i = a[String(r.get(n))]; r.set(e, i.bind(a)); });
            r.set(34, (e, t) => Promise.resolve(r.get(t)).then(res => r.set(e, res)));
            r.set(22, (e, t) => { let n = [...r.get(9)]; r.set(9, [...t]); return l().catch(err => r.set(e, String(err))).finally(() => r.set(9, n)); });
            r.set(28, () => void 0); r.set(26, () => void 0); r.set(25, () => void 0);
            i = 0; r.set(16, requirementsKey);
            let done = !1;
            setTimeout(() => { if (!done) { done = !0; resolve(String(i)); } }, 500);
            r.set(3, e => { if (!done) { done = !0; resolve(btoa(String(e))); } });
            r.set(4, e => { if (!done) { done = !0; reject(btoa(String(e))); } });
            r.set(30, (e, t, n, a) => {
                let i = Array.isArray(a), o = i ? n : [], s = (i ? a : n) ?? [];
                r.set(e, function(...args) {
                    if (done) return;
                    let d = [...r.get(9)];
                    if (i) for (let k = 0; k < o.length; k++) r.set(o[k], args[k]);
                    return r.set(9, [...s]), l().then(() => r.get(t)).catch(err => String(err)).finally(() => r.set(9, d));
                });
            });
            try {
                r.set(9, JSON.parse(s(atob(dx), String(r.get(16)))));
                l().catch(err => resolve(btoa(i + ': ' + String(err))));
            } catch(err) {
                resolve(btoa(i + ': ' + String(err)));
            }
        });
    }

    const reqKey = g();
    const prepRes = await fetch('/backend-api/sentinel/chat-requirements/prepare', {
        method: 'POST',
        headers: {'content-type': 'application/json', ...authHeaders},
        body: JSON.stringify({p: reqKey})
    });
    if (!prepRes.ok) {
        return {ok: false, stage: 'sentinel-prepare', status: prepRes.status, body: await prepRes.text()};
    }
    const prepData = await prepRes.json();
    let proofToken = null;
    if (prepData.proofofwork?.required) {
        proofToken = solvePow(prepData.proofofwork.seed, prepData.proofofwork.difficulty);
    }
    let turnstileToken = null;
    if (prepData.turnstile?.required && prepData.turnstile.dx) {
        turnstileToken = await solveDx(prepData.turnstile.dx, reqKey);
    }

    const finRes = await fetch('/backend-api/sentinel/chat-requirements/finalize', {
        method: 'POST',
        headers: {'content-type': 'application/json', ...authHeaders},
        body: JSON.stringify({
            prepare_token: prepData.prepare_token,
            proofofwork: proofToken,
            turnstile: turnstileToken
        })
    });
    if (!finRes.ok) {
        return {ok: false, stage: 'sentinel-finalize', status: finRes.status, body: await finRes.text()};
    }
    const finData = await finRes.json();

    // Prepare conversation turn
    const traceId = crypto.randomUUID();
    const convoPrepPayload = {
        action: 'next',
        conversation_id: conversationId,
        parent_message_id: currentNode,
        model: modelSlug,
        client_contextual_info: {
            app_name: 'chatgpt.com',
            app_surface: 'codex_browser',
            has_web_push_capabilities: true,
            web_push_notification_permission: 'default'
        },
        client_prepare_state: 'sent',
        supported_encodings: ['v1']
    };

    const fPrepRes = await fetch('/backend-api/f/conversation/prepare', {
        method: 'POST',
        headers: {
            'content-type': 'application/json',
            ...authHeaders,
            'x-oai-turn-trace-id': traceId,
            'x-oai-mcp-form-version': '2'
        },
        body: JSON.stringify(convoPrepPayload)
    });
    if (!fPrepRes.ok) {
        return {ok: false, stage: 'conversation-prepare', status: fPrepRes.status, body: await fPrepRes.text()};
    }
    const fPrepData = await fPrepRes.json();
    const conduitToken = fPrepData.conduit_token || '';

    // Commit stream
    const fConvoPayload = {
        action: 'next',
        messages: [
            {
                id: messageId,
                author: {role: 'user'},
                content: {content_type: 'text', parts: [prompt]},
                metadata: {}
            }
        ],
        conversation_id: conversationId,
        parent_message_id: currentNode,
        model: modelSlug,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
        timezone_offset_min: new Date().getTimezoneOffset(),
        client_contextual_info: {
            app_name: 'chatgpt.com',
            app_surface: 'codex_browser',
            has_web_push_capabilities: true,
            web_push_notification_permission: 'default'
        },
        client_prepare_state: 'sent',
        supported_encodings: ['v1']
    };

    const fConvoHeaders = {
        'content-type': 'application/json',
        ...authHeaders,
        'x-conduit-token': conduitToken,
        'x-oai-turn-trace-id': traceId,
        'x-oai-mcp-form-version': '2',
        'openai-sentinel-chat-requirements-token': finData.token,
        'openai-sentinel-proof-token': proofToken,
        'openai-sentinel-turnstile-token': turnstileToken
    };

    const fConvoRes = await fetch('/backend-api/f/conversation', {
        method: 'POST',
        headers: fConvoHeaders,
        body: JSON.stringify(fConvoPayload)
    });

    const reader = fConvoRes.body.getReader();
    const dec = new TextDecoder();
    let accumulated = '';
    while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        accumulated += dec.decode(value);
    }

    return {
        ok: fConvoRes.ok,
        status: fConvoRes.status,
        body: accumulated,
        conversation_id: conversationId,
        user_message_id: messageId,
        parent_message_id: currentNode,
        model: modelSlug,
    };
}
"""


async def send_inpage_api_prompt(
    conversation_id: str,
    prompt: str,
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Execute authenticated in-page API prompt mutation over direct CDP WebSocket in <1s."""
    import uuid
    ws_url = find_chatgpt_tab_ws_url(cdp_url=cdp_url, timeout=2.0)
    if not ws_url:
        raise RuntimeError(f"No active ChatGPT tab found on CDP {cdp_url}")

    import websockets

    message_id = str(uuid.uuid4())
    call_expr = f"({INPAGE_DISPATCH_JS})({json.dumps({'conversationId': conversation_id, 'prompt': prompt, 'messageId': message_id})})"

    async with websockets.connect(ws_url, max_size=None, close_timeout=2.0) as ws:
        req_id = int(time.time() * 1000) % 1000000
        await ws.send(json.dumps({
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": call_expr,
                "returnByValue": True,
                "awaitPromise": True,
            },
        }))
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            raw_reply = await asyncio.wait_for(ws.recv(), timeout=timeout)
            reply = json.loads(raw_reply)
            if reply.get("id") == req_id:
                res = reply.get("result", {}).get("result", {}).get("value")
                if isinstance(res, dict):
                    if not res.get("ok"):
                        raise RuntimeError(
                            f"In-page API mutation failed: stage={res.get('stage')} status={res.get('status')} body={res.get('body', '')[:500]}"
                        )
                    return res
                break
    raise RuntimeError("In-page API mutation timed out waiting for result")


async def strip_tab_dom_via_cdp(
    cdp_url: str = DEFAULT_CDP_URL,
    timeout: float = 5.0,
) -> bool:
    """Convert an open ChatGPT tab into a ~35MB shell tab by blocking UI assets and wiping DOM."""
    ws_url = find_chatgpt_tab_ws_url(cdp_url=cdp_url, timeout=min(2.0, timeout))
    if not ws_url:
        return False
    try:
        import websockets

        async with websockets.connect(ws_url, max_size=None, close_timeout=2.0) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
            await ws.send(json.dumps({
                "id": 2,
                "method": "Network.setBlockedURLs",
                "params": {"urls": ["*.css", "*.woff2", "*.png", "*.svg", "*.jpg", "*.webp"]},
            }))
            await ws.send(json.dumps({
                "id": 3,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "if (document && document.body) { document.body.innerHTML = ''; } true;",
                    "returnByValue": True,
                },
            }))
            await asyncio.wait_for(ws.recv(), timeout=timeout)
            return True
    except Exception as exc:
        logger.debug("Failed to strip tab DOM via CDP: %s", exc)
        return False



