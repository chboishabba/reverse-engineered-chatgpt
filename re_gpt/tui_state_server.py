"""Local, non-browser control plane over the already-running TUI state.

Runs only when --control-socket is given. The socket directory is owner-only.
Requests never access the shell, transport or transcript bodies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import struct
import threading


class TuiStateServer:
    def __init__(self, tui, socket_path: Path):
        self.tui = tui
        self.path = Path(socket_path).expanduser()
        self.stop_event = threading.Event()
        self.worker = None
        self.socket = None

    def start(self):
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise PermissionError("TUI control socket directory must be owner-only")
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError("Refusing to replace an existing TUI control socket")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        sock.listen(4)
        sock.settimeout(0.3)
        self.socket = sock
        self.worker = threading.Thread(target=self._serve, daemon=True)
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.socket:
            self.socket.close()
        if self.worker:
            self.worker.join(timeout=1)
        if self.path.is_socket():
            self.path.unlink()

    def _serve(self):
        while not self.stop_event.is_set():
            try:
                conn, _ = self.socket.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                conn.settimeout(3)
                try:
                    if hasattr(socket, "SO_PEERCRED"):
                        pid, uid, gid = struct.unpack(
                            "3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if uid != os.getuid():
                            raise PermissionError("different user")
                    data = conn.recv(4097)
                    if len(data) > 4096:
                        raise ValueError("request too large")
                    request = json.loads(data.decode("utf-8"))
                    response = self.handle(request)
                except Exception as exc:
                    response = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:120]}
                try:
                    conn.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8"))
                except OSError:
                    pass

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError("request must be object")
        action = request.get("action", "state")
        if action not in {"state", "messages", "latest_response", "open_picker", "set_query", "select", "set_composer", "submit_composer"}:
            raise ValueError("unsupported TUI action")
        if action == "messages":
            start = request.get("start", 0)
            limit = request.get("limit", 20)
            if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 30:
                raise ValueError("invalid message window")
        elif action == "set_composer":
            text = request.get("text")
            if not isinstance(text, str) or len(text) > 16000:
                raise ValueError("invalid composer text")
        if action == "set_query":
            query = request.get("query")
            if not isinstance(query, str) or len(query) > 200:
                raise ValueError("query must be at most 200 characters")
        elif action == "select":
            index = request.get("index")
            if type(index) is not int or index < 0:
                raise ValueError("invalid picker selection")
        else:
            query = None
            index = None

        if action not in {"state", "messages", "latest_response"}:
            loop = getattr(self.tui, "event_loop", None)
            if loop is None or not loop.is_running():
                return {"ok": False, "error": "TUI event loop not running"}
            def dispatch():
                try:
                    if action == "open_picker":
                        self.tui.open_resume()
                    elif action == "set_query" and self.tui.resume_visible:
                        self.tui.resume_search.text = query
                        self.tui.filter_resume()
                    elif action == "set_composer":
                        self.tui.input.text = text
                    elif action == "submit_composer":
                        self.tui.submit_prompt()
                    elif action == "select" and self.tui.resume_visible:
                        if index < len(self.tui.resume_filtered):
                            self.tui.resume_index = index
                            self.tui.select_resume()
                except Exception:
                    self.tui.status = "TUI control action failed"
                    self.tui.app.invalidate()
            loop.call_soon_threadsafe(dispatch)
            return {"ok": True, "queued": action}
        t = self.tui
        if action in {"messages", "latest_response"}:
            with t.lock:
                messages = [dict(m) for m in t.messages]
                cid = t.conversation_id
            if action == "latest_response":
                return {"ok": True, "conversation_id": cid, "message": next((m for m in reversed(messages) if m.get("role") == "assistant"), None)}
            selected = messages[start:start + limit]
            return {"ok": True, "conversation_id": cid, "total": len(messages), "start": start, "messages": selected, "has_more": start + len(selected) < len(messages)}
        with t.lock:
            matches = [
                {"id": str(item.get("id") or "")[:100], "title": str(item.get("title") or "")[:160]}
                for item in t.resume_filtered[:50] if isinstance(item, dict)
            ]
            return {"ok": True, "state": {
                "conversation_id": t.conversation_id,
                "title": str(t.title)[:160],
                "status": str(t.status)[:180],
                "busy": bool(t.busy),
                "composer_text": t.input.text,
                "message_count": len(t.messages),
                "picker_visible": bool(t.resume_visible),
                "picker_loading": bool(t.resume_loading),
                "picker_query": t.resume_search.text[:200],
                "picker_total": len(t.resume_items),
                "match_count": len(t.resume_filtered),
                "selected_index": t.resume_index,
                "matches": matches,
                "truncated": len(t.resume_filtered) > 50,
            }}
