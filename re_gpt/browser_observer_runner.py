"""Browser-owned exact-thread reader and provisional DOM status probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from chatgpt_dom_relay import DEFAULT_PROFILE_DIR, build_conversation_url, extract_thread_id

from .accessibility_browser import AccessibilityBrowserRelay, BROWSER_MODES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Browser-owned ChatGPT read/status observer")
    parser.add_argument("conversation_id")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--read", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--browser-mode", choices=BROWSER_MODES, default="displayless")
    parser.add_argument("--cdp-endpoint")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--idle-ms", type=int, default=2500)
    parser.add_argument("--json", action="store_true")
    return parser


async def _same_origin_read(relay: AccessibilityBrowserRelay, conversation_id: str) -> dict[str, Any]:
    await relay.open_api_context()
    result = await relay.page.evaluate(
        """async (conversationId) => {
            const sessionResponse = await fetch('/api/auth/session', {method: 'GET'});
            const sessionText = await sessionResponse.text();
            if (!sessionResponse.ok) {
                return {ok: false, stage: 'auth-session', status: sessionResponse.status,
                  body: sessionText.slice(0, 1000)};
            }
            let session;
            try { session = JSON.parse(sessionText); }
            catch (_) { return {ok: false, stage: 'auth-session-json', status: sessionResponse.status,
              body: sessionText.slice(0, 1000)}; }
            const accessToken = session.accessToken || '';
            if (!accessToken) {
                return {ok: false, stage: 'auth-session', status: 401,
                  body: 'No access token in /api/auth/session response.'};
            }
            const headers = {Authorization: `Bearer ${accessToken}`, Accept: 'application/json'};
            const response = await fetch(`/backend-api/conversation/${conversationId}`, {
              method: 'GET', headers,
            });
            const body = await response.text();
            let chat = null;
            if (response.ok) {
                try { chat = JSON.parse(body); }
                catch (_) { return {ok: false, stage: 'conversation-json', status: response.status,
                  body: body.slice(0, 1000)}; }
            }
            return {
              ok: response.ok,
              stage: 'conversation-get',
              status: response.status,
              body: response.ok ? '' : body.slice(0, 1000),
              chat,
              diagnostic: {
                adapter: 'browser',
                method: 'GET',
                route: '/backend-api/conversation/<id>',
                header_names: Object.keys(headers).sort(),
                auth_header_present: Boolean(headers.Authorization),
                status: response.status,
                response_class: response.ok ? 'ok' : 'http_error',
              },
            };
        }""",
        conversation_id,
    )
    if not result.get("ok"):
        raise RuntimeError(
            f"browser {result.get('stage')} failed with HTTP {result.get('status')}: "
            f"{result.get('body', '')}"
        )
    diagnostic = result.get("diagnostic") if isinstance(result.get("diagnostic"), dict) else {}
    try:
        cookies = await relay.page.context.cookies("https://chatgpt.com")
    except Exception:
        cookies = []
    diagnostic["cookie_names"] = sorted({str(cookie.get("name") or "") for cookie in cookies if cookie.get("name")})
    diagnostic["cookie_domains"] = sorted({str(cookie.get("domain") or "") for cookie in cookies if cookie.get("domain")})
    return {"chat": result.get("chat"), "diagnostic": diagnostic}


async def _dom_status(relay: AccessibilityBrowserRelay, conversation_id: str) -> dict[str, Any]:
    await relay.page.goto(build_conversation_url(conversation_id), wait_until="commit", timeout=relay.timeout_ms)
    await relay.page.wait_for_timeout(750)
    observation = await relay.page.evaluate(
        """(conversationId) => {
            const text = (document.querySelector('main')?.innerText || document.body?.innerText || '');
            const visible = (el) => {
              if (!el) return false;
              const style = window.getComputedStyle(el);
              const rect = el.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width >= 0 && rect.height >= 0;
            };
            const anyVisible = (selector) => Array.from(document.querySelectorAll(selector)).some(visible);
            const region = document.querySelector('div[role="region"][aria-label="Conversation"]');
            const copyOrEdit = anyVisible('button[aria-label="Copy message"], button[aria-label="Edit message"]');
            const stopVisible = anyVisible('button[data-testid="stop-button"], button[aria-label*="Stop"]');
            const tertiary = Array.from(document.querySelectorAll('[data-markdown-text-tone="tertiary"]'))
              .filter(visible)
              .map((node) => (node.innerText || '').trim())
              .filter(Boolean);
            const lowerTertiary = tertiary.join('\n').toLowerCase();
            const assistantVisible = text.includes('ChatGPT said:') || anyVisible('button[aria-label="Copy message"]');
            return {
              conversation_id: conversationId,
              url: window.location.href,
              loaded: Boolean(region) || copyOrEdit,
              generating: stopVisible,
              thinking: lowerTertiary.includes('thinking'),
              tool_activity: tertiary.length > 0 && !lowerTertiary.includes('connection interrupted'),
              connection_interrupted: text.includes('Connection interrupted') || text.includes('Waiting for the complete answer'),
              conversation_load_error: text.includes('Could not load this ChatGPT conversation'),
              history_load_error: text.includes('Unable to load history'),
              assistant_text_visible: assistantVisible,
              retry_visible: /(^|\n)Retry($|\n)/.test(text),
              activity_text: tertiary.slice(-8),
            };
        }""",
        conversation_id,
    )
    return {"observation": observation}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    conversation_id = extract_thread_id(args.conversation_id)
    if not conversation_id:
        raise ValueError("conversation_id must contain a valid UUID")
    async with AccessibilityBrowserRelay(
        profile_dir=args.profile_dir,
        browser_mode=args.browser_mode,
        cdp_endpoint=args.cdp_endpoint,
        timeout_ms=max(1, args.timeout) * 1000,
        idle_ms=args.idle_ms,
    ) as relay:
        if args.read:
            return await _same_origin_read(relay, conversation_id)
        return await _dom_status(relay, conversation_id)


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
