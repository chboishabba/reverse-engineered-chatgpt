"""Adapter for tritao/chatgpt-web's *existing* run_tui callbacks.

Install upstream separately and supply --upstream-tui /path/to/chatgpt_tui.py.
No upstream code is copied. No extra database, daemon or browser sender.
Attribution: https://github.com/tritao/chatgpt-web (original TUI authors).
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

from .pg_conversation_read import list_synced_roster, read_pg_conversation
from .pg_hydration import roster_page, hydrate_one, hydrate_pending
from .terminal_activity import activity_from_progress
from .tui_state_server import TuiStateServer
from .mutation_service import MutationService
from .storage import extract_ordered_messages


def project_chat(conversation_id: str, graph: dict[str, Any]) -> dict[str, Any]:
    """Adapt canonical graph read to upstream display format, retaining source."""
    messages = []
    for index, msg in enumerate(extract_ordered_messages(graph)):
        role = str(msg.get("author") or "")
        if role not in ("user", "assistant"):
            continue
        source = str(msg.get("content") or "")
        if source:
            messages.append({"id": f"view-{index}", "role": role, "text": source,
                             "author": {"role": role}, "content_type": "text",
                             "content": {"parts": [source]}})
    return {"conversation_id": conversation_id, "title": graph.get("title") or "ChatGPT",
            "default_model_slug": graph.get("default_model_slug"),
            "messages": messages}


class ReGptTuiCallbacks:
    """No authority beyond existing re-gpt read and MutationService writes."""

    def __init__(self, *, model: str | None = None) -> None:
        self.service = MutationService(model=model)
        self._roster_refresh_started = False

    def list_conversations(self) -> list[dict[str, Any]]:
        # Remote catalog is a *non-authoritative* temporary picker source.
        # Never translate malformed/empty pages into a misleading search miss.
        # PG always answers immediately; network discovery never blocks the picker.
        if not self._roster_refresh_started:
            from threading import Thread
            self._roster_refresh_started = True
            def refresh():
                try:
                    roster_page(pages=3)
                    hydrate_pending(limit=5)
                except Exception:
                    pass  # Diagnostic and retry belong in persisted PG roster state.
                finally:
                    self._roster_refresh_started = False
            Thread(target=refresh, daemon=True, name="re-gpt-pg-roster-refresh").start()
        return list_synced_roster(limit=500)

    def load_conversation(self, conversation_id: str) -> dict[str, Any]:
        # No browser fallback; graph broker validates canonical read.
        try:
            return read_pg_conversation(conversation_id)
        except KeyError:
            # A selected roster-only thread gets priority over the background queue.
            hydrate_one(conversation_id)
            return read_pg_conversation(conversation_id)

    def send_prompt(self, conversation_id, prompt, attachments, on_event, register_connection):
        # Do not reuse tritao's HTTP sender; no automatic retries or DOM.
        if attachments:
            raise NotImplementedError(
                "Clipboard attachment descriptors need explicit re-gpt staging adapter")
        if conversation_id:
            def progress(event):
                channel = str(event.get("channel") or "final")
                if (event.get("kind") == "token_delta" and channel == "final"
                        and event.get("recipient") in (None, "", "all") and event.get("delta")):
                    on_event({"type": "delta", "text": str(event["delta"])})
                activity = activity_from_progress(event)
                if activity is not None:
                    on_event({"type": "activity", "activity": activity})
            result = self.service.continue_thread(
                conversation_id, prompt, write_transport="async", dom_fallback=False,
                follow=True, follow_timeout=300, progress_callback=progress)
        else:
            result = self.service.new_conversation(prompt, follow=True, follow_timeout=300)
        state = result.get("state")
        if state != "complete":
            raise RuntimeError(
                f"Operation {result.get('operation_id')} state={state}; "
                "do not resend. Reconcile using re-gpt operation status.")
        resolved_id = result.get("conversation_id") or conversation_id
        # Commit is already proven. Rehydrate the selected conversation into PG
        # before projecting it; a failed read must never repeat the POST.
        try:
            hydrate_one(resolved_id)
        except Exception:
            pass  # Keep already committed answer visible; receipt remains in re-gpt.
        try:
            rendered = self.load_conversation(resolved_id)
        except Exception:
            rendered = {"conversation_id": resolved_id, "title": "ChatGPT", "messages": []}
        assistant = result.get("assistant_text")
        if assistant:
            on_event({"type": "replace", "text": str(assistant)})
        return {"conversation_id": resolved_id, "conversation": rendered, "previous_ids": []}


def collect_remote_catalog(chatgpt, *, page_size: int = 100, max_pages: int = 10):
    """Bounded remote catalog fallback until the sole PG history API is wired.

    Reject missing/invalid response shapes and duplicate-page loops. A genuine
    empty first page is an explicit diagnostic, not 'no matching conversations'.
    """
    if not (1 <= page_size <= 100 and 1 <= max_pages <= 100):
        raise ValueError("invalid catalog bounds")
    collected = []
    seen = set()
    for page_index in range(max_pages):
        payload = chatgpt.list_conversations_page(offset=page_index * page_size, limit=page_size)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("ChatGPT catalog returned an unexpected response shape")
        raw = payload["items"]
        if page_index == 0 and not raw:
            raise RuntimeError(
                "ChatGPT remote catalog returned zero conversations; "
                "canonical ITIR PostgreSQL history has not yet been connected to the picker"
            )
        new = 0
        for item in raw:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                continue
            if identifier not in seen:
                collected.append(item)
                seen.add(identifier)
                new += 1
        if not raw or len(raw) < page_size or new == 0:
            break
    if not collected:
        raise RuntimeError("ChatGPT catalog returned no usable conversation IDs")
    return collected


def load_upstream_tui(filename: Path, *, control_socket: Path | None = None):
    if not filename.is_file():
        raise FileNotFoundError(filename)
    spec = importlib.util.spec_from_file_location("chatgpt_upstream_terminal_ui", filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load upstream terminal UI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "run_tui", None)):
        raise RuntimeError("Upstream chatgpt_tui.py has no run_tui")
    # Preserve upstream prompt, scrollback, Markdown and keyboard behaviour.
    # Extend only event handling, because upstream ignores activity events.
    class ActivityChatTui(module.ChatTui):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.activity_log = []

        def handle_command(self, command):
            if command.strip() == "/activity":
                import json
                with self.lock:
                    selected = list(self.activity_log[-30:])
                if not selected:
                    self.append_system("No activity events received in this session.")
                else:
                    self.append_system("Observed activity (latest 30):\n" +
                        "\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in selected))
                return
            return super().handle_command(command)

        def open_resume(self):
            """Load picker data off-loop, but mutate prompt_toolkit state on-loop.

            Upstream's loader thread assigns ``resume_filtered`` while the
            renderer may be calculating wrapped cursor positions.  That races
            scrolling keys (notably a held PageDown) and can produce an index
            error inside prompt_toolkit.
            """
            if self.busy or self.loading_conversation or self.resume_visible:
                return
            self.resume_visible = True
            self.resume_loading = True
            self.resume_items = []
            self.resume_filtered = []
            self.resume_index = 0
            self.resume_search.text = ""
            self.status = "Loading recent conversations…"
            self.app.layout.focus(self.resume_search)
            self.app.invalidate()

            def apply_items(items=None, error=None):
                if not self.resume_visible:
                    return
                self.resume_loading = False
                if error is None:
                    self.resume_items = items
                    self.filter_resume()
                    self.status = f"{len(items)} recent conversations"
                else:
                    self.status = f"Conversation list failed: {error}"
                self.app.invalidate()

            def load_items():
                try:
                    items = self.list_conversations()
                except Exception as exc:
                    self.event_loop.call_soon_threadsafe(apply_items, None, exc)
                else:
                    self.event_loop.call_soon_threadsafe(apply_items, items, None)

            from threading import Thread
            Thread(target=load_items, daemon=True, name="re-gpt-resume-loader").start()

        def run_send(self, prompt, attachments):
            original = self.send_prompt

            def with_activity(conversation_id, text, files, on_event, register):
                def dispatch(event):
                    if event.get("type") == "activity":
                        activity = event.get("activity") or {}
                        with self.lock:
                            self.activity_log.append(activity)
                            if len(self.activity_log) > 500:
                                self.activity_log = self.activity_log[-500:]
                        self.status = str(activity.get("summary") or "Tool activity")[:160]
                        self.app.invalidate()
                    else:
                        on_event(event)
                return original(conversation_id, text, files, dispatch, register)

            self.send_prompt = with_activity
            try:
                super().run_send(prompt, attachments)
            finally:
                self.send_prompt = original

    def launch(*args, **kwargs):
        tui = ActivityChatTui(*args, **kwargs)
        if control_socket is None:
            tui.run()
        else:
            server = TuiStateServer(tui, control_socket)
            server.start()
            try:
                tui.run()
            finally:
                server.close()
    return launch


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run upstream TUI on re-gpt transport")
    parser.add_argument("--upstream-tui", type=Path, required=True)
    parser.add_argument("--conversation-id")
    parser.add_argument("--model")
    parser.add_argument("--control-socket", type=Path, help="Opt-in local TUI state/control Unix socket")
    args = parser.parse_args(argv)
    callbacks = ReGptTuiCallbacks(model=args.model)
    run_tui = load_upstream_tui(args.upstream_tui, control_socket=args.control_socket)
    run_tui(callbacks.list_conversations, callbacks.load_conversation,
            callbacks.send_prompt, conversation_id=args.conversation_id,
            model=args.model, upload_image=None, rename_conversation=None,
            set_active_conversation=None)


if __name__ == "__main__":
    main()
