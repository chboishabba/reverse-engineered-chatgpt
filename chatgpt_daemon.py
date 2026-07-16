"""Headless ChatGPT API daemon using native browser profile.
Launches persistent Chromium from ./chatgpt_browser_profile/, wakes the
network layer briefly on chatgpt.com, then parks on about:blank for
zero-UI-overhead API calls. Streams SSE responses live.
"""
import asyncio, json, os, uuid, time
from playwright.async_api import async_playwright

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "chatgpt_browser_profile")


class ChatGPTDaemon:
    """Thin daemon wrapping a headless Chromium context for ChatGPT API calls."""

    def __init__(self, profile_dir: str = PROFILE_DIR):
        self.profile_dir = profile_dir
        self._playwright = None
        self._context = None
        self._page = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 720},
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        # Wake network layer — navigates to commit only, not full load
        await self._page.goto("https://chatgpt.com", wait_until="commit", timeout=30000)
        # Park on about:blank to avoid any UI memory or lag
        await self._page.goto("about:blank", wait_until="commit")

        cookies = await self._context.cookies(urls=["https://chatgpt.com"])
        has_session = any("session" in c["name"] for c in cookies)
        if not has_session:
            raise RuntimeError(
                "No session cookie found. Run setup_browser_profile.py first "
                "and log in manually."
            )
        print(f"[daemon] Ready — {sum(1 for c in cookies if 'session' in c['name'])} session cookies loaded")

    async def _get_sentinel_token(self) -> str:
        return await self._page.evaluate("""async () => {
            const r = await fetch('https://chatgpt.com/backend-api/sentinel/chat-requirements', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
            });
            const d = await r.json();
            return d.token || '';
        }""")

    async def _get_conversation_parent(self, convo_id: str) -> str:
        return await self._page.evaluate(f"""async () => {{
            const r = await fetch('https://chatgpt.com/backend-api/conversation/{convo_id}', {{
                headers: {{'Accept': 'application/json'}}
            }});
            const d = await r.json();
            const m = d.mapping || {{}};
            let cur = d.current_node;
            while (cur && m[cur]) {{
                const kids = m[cur].children || [];
                cur = kids.length ? kids[kids.length - 1] : null;
            }}
            return JSON.stringify({{parent: cur, title: d.title || ''}});
        }}""")

    async def send_message(
        self,
        prompt: str,
        conversation_id: str | None = None,
        model: str = "auto",
        parent_message_id: str | None = None,
    ) -> list[dict]:
        """Send a message and stream the SSE response, yielding content chunks."""

        # Resolve parent message
        parent_id = parent_message_id
        if conversation_id and parent_id is None:
            conv_info = json.loads(await self._get_conversation_parent(conversation_id))
            parent_id = conv_info.get("parent") or "client-created-root"
        if parent_id is None:
            parent_id = "client-created-root"

        # Get sentinel token
        rt = await self._get_sentinel_token()

        message_id = str(uuid.uuid4())

        result = await self._page.evaluate("""async (args) => {
            const [prompt, convoId, parentId, model, rt, msgId] = args;
            const payload = {
                action: 'next',
                conversation_id: convoId || undefined,
                messages: [{
                    id: msgId,
                    author: {role: 'user'},
                    content: {content_type: 'text', parts: [prompt]},
                    create_time: Date.now() / 1000,
                    metadata: {},
                }],
                model: model,
                parent_message_id: parentId,
                conversation_mode: {kind: 'primary_assistant'},
                supported_encodings: ['v1'],
                supports_buffering: false,
                timezone_offset_min: new Date().getTimezoneOffset(),
            };
            // Strip undefined keys
            const cleaned = {};
            for (const [k, v] of Object.entries(payload)) { if (v !== undefined) cleaned[k] = v; }

            const r = await fetch('https://chatgpt.com/backend-api/conversation', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream',
                    'openai-sentinel-chat-requirements-token': rt,
                },
                body: JSON.stringify(cleaned),
            });
            if (!r.ok) return JSON.stringify({error: true, status: r.status, body: await r.text()});

            const reader = r.body.getReader();
            const decoder = new TextDecoder();
            let full = '';
            while (true) {
                const {done, value} = await reader.read();
                if (done) break;
                full += decoder.decode(value, {stream: true});
            }
            return JSON.stringify({error: false, data: full});
        }""", [prompt, conversation_id, parent_id, model, rt, message_id])

        resp = json.loads(result)
        if resp.get("error"):
            raise RuntimeError(f"API error {resp['status']}: {resp['body']}")

        return self._parse_sse(resp["data"])

    def _parse_sse(self, raw: str) -> list[dict]:
        """Parse SSE stream into a list of message events."""
        events = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
        return events

    async def close(self):
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()


async def main():
    """Example: send a message and print the assistant response."""
    daemon = ChatGPTDaemon()
    try:
        await daemon.start()
        convo_id = "6a4dda85-fe34-83ec-aee0-5987219cbc82"

        print(f"\nSending message to {convo_id}...")
        events = await daemon.send_message(
            prompt="Reply OK if you receive this test ping from Aristotle.",
            conversation_id=convo_id,
        )
        print(f"Received {len(events)} SSE events")

        # Print final assistant message
        for ev in events:
            msg = ev.get("message", {})
            role = msg.get("author", {}).get("role", "")
            if role == "assistant":
                parts = msg.get("content", {}).get("parts", [])
                if parts:
                    print(f"\nAssistant: {parts[0][:500]}")

        # Also print the conversation title if it changed
        for ev in events:
            if ev.get("conversation_id"):
                print(f"Conversation ID: {ev['conversation_id']}")
    finally:
        await daemon.close()


if __name__ == "__main__":
    asyncio.run(main())
