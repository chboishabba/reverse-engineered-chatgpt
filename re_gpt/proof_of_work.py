"""Pure Python solver for OpenAI Sentinel Proof-of-Work challenge.

Implements the FNV-1a (32-bit) with MurmurHash3/SplitMix finalizer hash loop
and telemetry payload generation matching OpenAI frontend attestation.
"""

from __future__ import annotations

import base64
import json
import random
import shutil
import subprocess
import time
from typing import Any


def xor_strings(data: str, key: str) -> str:
    """XOR two strings character-by-character cycling over key."""
    if not key:
        return data
    klen = len(key)
    return "".join(chr(ord(c) ^ ord(key[i % klen])) for i, c in enumerate(data))


def build_requirements_token(user_agent: str | None = None) -> str:
    """Build the initial gAAAAAC telemetry token for /sentinel/chat-requirements/prepare."""
    t0 = time.perf_counter()
    telemetry = generate_telemetry_array(user_agent=user_agent)
    telemetry[3] = 1
    telemetry[9] = round((time.perf_counter() - t0) * 1000, 3)
    raw_json = json.dumps(telemetry, separators=(",", ":"))
    encoded = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
    return f"gAAAAAC{encoded}"


def fnv_murmur3(s: str) -> str:
    """Compute OpenAI Sentinel 32-bit FNV-1a + MurmurHash3 finalizer hash."""
    t = 0x811C9DC5
    for ch in s:
        t ^= ord(ch)
        t = (t * 0x1000193) & 0xFFFFFFFF
    t ^= (t >> 16)
    t = (t * 0x85EBCA6B) & 0xFFFFFFFF
    t ^= (t >> 13)
    t = (t * 0xC2B2AE35) & 0xFFFFFFFF
    t ^= (t >> 16)
    t &= 0xFFFFFFFF
    return f"{t:08x}"


def generate_telemetry_array(user_agent: str | None = None) -> list[Any]:
    """Generate baseline 25-element telemetry vector matching frontend runtime."""
    ua = user_agent or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    return [
        3000,  # 0: screen.width + screen.height (e.g. 1920 + 1080)
        time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
        2147483648,  # 2: heap size limit
        random.random(),  # 3: iteration counter r
        ua,  # 4: user agent
        "https://chatgpt.com/cdn/assets/manifest-67419903.js",  # 5: script src
        "c/prod/_",  # 6: data build
        "en-US",  # 7: language
        "en-US,en",  # 8: languages
        random.random(),  # 9: elapsed ms
        "webdriver−false",  # 10: navigator prop
        "location,cookie",  # 11: doc keys
        "window,document",  # 12: win keys
        time.time() * 1000.0,  # 13: perf now
        0,  # 14
        "",  # 15: search params
        8,  # 16: hardware concurrency
        time.time() * 1000.0,  # 17: time origin
        0,  # 18: ai in window
        0,  # 19: createPRNG
        0,  # 20: cache
        0,  # 21: data
        0,  # 22: solana
        0,  # 23: dump
        0,  # 24: InstallTrigger
    ]


def solve_proof_of_work(
    seed: str,
    difficulty: str,
    *,
    user_agent: str | None = None,
    max_iterations: int = 500000,
) -> str:
    """Solve the proof-of-work challenge and return the gAAAAAB formatted token."""
    t0 = time.perf_counter()
    telemetry = generate_telemetry_array(user_agent=user_agent)
    diff_len = len(difficulty)

    for r in range(max_iterations):
        telemetry[3] = r
        telemetry[9] = round((time.perf_counter() - t0) * 1000)
        raw_json = json.dumps(telemetry, separators=(",", ":"))
        encoded = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
        candidate = f"{seed}{encoded}"
        hash_hex = fnv_murmur3(candidate)
        if hash_hex[:diff_len] <= difficulty:
            return f"gAAAAAB{encoded}~S"

    err_b64 = base64.b64encode(b'"e"').decode("ascii")
    return f"wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D{err_b64}"


_HEADLESS_DX_VM_JS = """
const [dx, reqKey, ua] = process.argv.slice(1);
function s(e, t) {
    let n = '';
    for (let a = 0; a < e.length; a++) n += String.fromCharCode(e.charCodeAt(a) ^ t.charCodeAt(a % t.length));
    return n;
}
const mockWindow = {
    Reflect, Object, Math, JSON, Array, String, Number, Boolean,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    performance: { now: () => 124.5, timeOrigin: Date.now() - 10000, memory: { jsHeapSizeLimit: 2147483648 } },
    document: {
        body: { innerHTML: '', appendChild: () => {}, removeChild: () => {}, clientWidth: 1920, clientHeight: 1080 },
        documentElement: { getAttribute: () => 'prod' },
        scripts: [{ src: 'https://chatgpt.com/cdn/assets/manifest-67419903.js' }],
        createElement: () => ({ style: {}, setAttribute: () => {}, getContext: () => ({}) }),
        location: { search: '', href: 'https://chatgpt.com/' }
    },
    history: { length: 2, state: null },
    navigator: {
        userAgent: ua,
        language: 'en-US',
        languages: ['en-US', 'en'],
        hardwareConcurrency: 8,
        webdriver: false,
        platform: 'Linux x86_64'
    },
    screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24 },
    location: { search: '', href: 'https://chatgpt.com/', hostname: 'chatgpt.com' },
    innerWidth: 1440,
    innerHeight: 900,
    devicePixelRatio: 1
};
mockWindow.window = mockWindow;
mockWindow.self = mockWindow;
mockWindow.top = mockWindow;

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
        r.set(6, (e, t, n) => { let a = r.get(t); r.set(e, a?.[String(r.get(n))]); });
        r.set(7, function(e, ...args) { return r.get(e)?.(...args.map(x => r.get(x))); });
        r.set(17, function(e, t, ...args) {
            try {
                let n = r.get(t)?.(...args.map(x => r.get(x)));
                if (n && typeof n.then === 'function') return n.then(res => r.set(e, res)).catch(err => r.set(e, String(err)));
                r.set(e, n);
            } catch (err) { r.set(e, String(err)); }
        });
        r.set(13, function(e, t, ...args) { try { r.get(t)?.(...args); } catch(err) { r.set(e, String(err)); } });
        r.set(8, (e, t) => r.set(e, r.get(t)));
        r.set(10, mockWindow);
        r.set(11, (e, t) => r.set(e, (Array.from(mockWindow.document.scripts || []).map(e => e?.src?.match(String(r.get(t)))).filter(e => e?.length)[0] ?? [])[0] ?? null));
        r.set(12, e => r.set(e, r));
        r.set(14, (e, t) => r.set(e, JSON.parse(String(r.get(t)))));
        r.set(15, (e, t) => r.set(e, JSON.stringify(r.get(t))));
        r.set(18, e => r.set(e, atob(String(r.get(e)))));
        r.set(19, e => r.set(e, btoa(String(r.get(e)))));
        r.set(20, function(e, t, n, ...args) { return r.get(e) === r.get(t) ? r.get(n)?.(...args) : null; });
        r.set(21, function(e, t, n, a, ...args) { return Math.abs(Number(r.get(e)) - Number(r.get(t))) > Number(r.get(n)) ? r.get(a)?.(...args) : null; });
        r.set(23, function(e, t, ...args) { return void 0 !== r.get(e) ? r.get(t)?.(...args) : null; });
        r.set(24, (e, t, n) => { let a = r.get(t), fn = a?.[String(r.get(n))]; r.set(e, fn ? fn.bind(a) : () => {}); });
        r.set(34, (e, t) => Promise.resolve(r.get(t)).then(res => r.set(e, res)));
        r.set(22, (e, t) => { let n = [...r.get(9)]; r.set(9, [...t]); return l().catch(err => r.set(e, String(err))).finally(() => r.set(9, n)); });
        r.set(28, () => void 0); r.set(26, () => void 0); r.set(25, () => void 0);
        i = 0; r.set(16, requirementsKey);
        let done = !1;
        setTimeout(() => { if (!done) { done = !0; resolve(String(i)); } }, 500);
        r.set(3, e => { if (!done) { done = !0; resolve(btoa(String(e))); } });
        r.set(4, e => { if (!done) { done = !0; reject(btoa(String(e))); } });
        r.set(30, (e, t, n, a) => {
            let isArr = Array.isArray(a), o = isArr ? n : [], sArr = (isArr ? a : n) ?? [];
            r.set(e, function(...args) {
                if (done) return;
                let d = [...r.get(9)];
                if (isArr) for (let k = 0; k < o.length; k++) r.set(o[k], args[k]);
                return r.set(9, [...sArr]), l().then(() => r.get(t)).catch(err => String(err)).finally(() => r.set(9, d));
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
solveDx(dx, reqKey).then(tok => console.log(JSON.stringify({tok, steps: i})));
"""


def solve_turnstile_dx(
    dx: str,
    requirements_key: str,
    *,
    user_agent: str | None = None,
    timeout: float = 5.0,
) -> str:
    """Evaluate OpenAI's Sentinel bytecode VM headlessly without a browser."""
    ua = user_agent or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    node_bin = shutil.which("node") or "node"
    try:
        completed = subprocess.run(
            [node_bin, "-e", _HEADLESS_DX_VM_JS, dx, requirements_key, ua],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            payload = json.loads(completed.stdout.strip())
            tok = str(payload.get("tok") or "").strip()
            if tok:
                return tok
    except Exception:
        pass
    return base64.b64encode(b"0").decode("ascii")


async def mint_sentinel_tokens_cffi(
    session: Any,
    headers: dict[str, str],
    *,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Mint Sentinel tokens directly on the active curl_cffi session (same TLS socket)."""
    req_key = build_requirements_token(user_agent=user_agent)
    prep_headers = dict(headers)
    prep_headers["Content-Type"] = "application/json"

    prep_resp = await session.post(
        url="https://chatgpt.com/backend-api/sentinel/chat-requirements/prepare",
        headers=prep_headers,
        json={"p": req_key},
    )
    prep_data = prep_resp.json()
    if not isinstance(prep_data, dict) or not prep_data.get("prepare_token"):
        raise RuntimeError(f"Sentinel prepare failed: status={getattr(prep_resp, 'status_code', None)}")

    proof_token: str | None = None
    pow_spec = prep_data.get("proofofwork")
    if isinstance(pow_spec, dict) and pow_spec.get("required"):
        proof_token = solve_proof_of_work(
            str(pow_spec.get("seed") or ""),
            str(pow_spec.get("difficulty") or ""),
            user_agent=user_agent,
        )

    turnstile_token: str | None = None
    ts_spec = prep_data.get("turnstile")
    if isinstance(ts_spec, dict) and ts_spec.get("required") and ts_spec.get("dx"):
        turnstile_token = solve_turnstile_dx(
            str(ts_spec["dx"]),
            req_key,
            user_agent=user_agent,
        )

    fin_body: dict[str, Any] = {"prepare_token": prep_data["prepare_token"]}
    if proof_token:
        fin_body["proofofwork"] = proof_token
    if turnstile_token:
        fin_body["turnstile"] = turnstile_token

    fin_resp = await session.post(
        url="https://chatgpt.com/backend-api/sentinel/chat-requirements/finalize",
        headers=prep_headers,
        json=fin_body,
    )
    fin_data = fin_resp.json()
    if not isinstance(fin_data, dict) or not fin_data.get("token"):
        raise RuntimeError(f"Sentinel finalize failed: status={getattr(fin_resp, 'status_code', None)}")

    return {
        "chat_requirements_token": str(fin_data["token"]),
        "proof_token": proof_token,
        "turnstile_token": turnstile_token,
        "prepare_token": str(prep_data["prepare_token"]),
        "expire_after": fin_data.get("expire_after"),
        "expire_at": fin_data.get("expire_at"),
        "persona": fin_data.get("persona") or prep_data.get("persona"),
        "captured_at": time.time(),
        "consumed": False,
    }


async def prepare_conduit_cffi(
    session: Any,
    headers: dict[str, str],
    *,
    conversation_id: str | None,
    parent_message_id: str | None,
    model: str,
    action: str = "next",
    trace_id: str,
) -> str:
    """Call /backend-api/f/conversation/prepare on the active curl_cffi session to obtain x-conduit-token."""
    prep_headers = dict(headers)
    prep_headers["Content-Type"] = "application/json"
    prep_headers["x-oai-turn-trace-id"] = trace_id
    prep_headers["x-oai-mcp-form-version"] = "2"
    if conversation_id:
        prep_headers["Referer"] = f"https://chatgpt.com/c/{conversation_id}"

    payload: dict[str, Any] = {
        "action": action,
        "parent_message_id": parent_message_id,
        "model": model,
        "client_contextual_info": {
            "app_name": "chatgpt.com",
            "app_surface": "codex_browser",
            "has_web_push_capabilities": True,
            "web_push_notification_permission": "default",
        },
        "client_prepare_state": "sent",
        "supported_encodings": ["v1"],
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    resp = await session.post(
        url="https://chatgpt.com/backend-api/f/conversation/prepare",
        headers=prep_headers,
        json=payload,
    )
    data = resp.json()
    if isinstance(data, dict) and data.get("conduit_token"):
        return str(data["conduit_token"])
    return ""

