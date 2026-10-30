"""Offline contract checks for programmatic TUI state."""
from types import SimpleNamespace
from threading import RLock
import socket

import pytest

from re_gpt.tui_state_server import TuiStateServer


class Dummy:
    def __init__(self):
        self.lock = RLock()
        self.conversation_id = "cid"
        self.title = "Known thread"
        self.status = "Ready"
        self.busy = False
        self.resume_visible = True
        self.resume_loading = False
        self.resume_search = SimpleNamespace(text="")
        self.resume_items = [{"id": "a", "title": "Alpha"}]
        self.resume_filtered = self.resume_items[:]
        self.resume_index = 0


def test_reads_real_state_without_transcript():
    state = TuiStateServer(Dummy(), "/tmp/no-socket-created").handle({"action": "state"})
    assert state["ok"]
    assert state["state"]["match_count"] == 1
    assert state["state"]["matches"][0]["title"] == "Alpha"
    assert "messages" not in state["state"]


def test_unsupported_action_rejected():
    import pytest
    with pytest.raises(ValueError):
        TuiStateServer(Dummy(), "/tmp/not-used").handle({"action": "send_prompt", "prompt": "hi"})


def test_selection_requires_nonnegative_index():
    with pytest.raises(ValueError):
        TuiStateServer(Dummy(), "/tmp/not-used").handle({"action": "select", "index": -1})


def test_replace_stale_socket_requires_explicit_opt_in(tmp_path):
    path = tmp_path / "control.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()

    with pytest.raises(FileExistsError):
        TuiStateServer(Dummy(), path).start()

    server = TuiStateServer(Dummy(), path, replace_stale=True)
    server.start()
    assert path.is_socket()
    server.close()


def test_replace_stale_socket_refuses_live_socket(tmp_path):
    path = tmp_path / "control.sock"
    first = TuiStateServer(Dummy(), path)
    first.start()
    with pytest.raises(FileExistsError, match="live"):
        TuiStateServer(Dummy(), path, replace_stale=True).start()
    first.close()
