# Direct TUI application-state bridge (2026-10-30)

## Purpose
An agent must be able to debug a running terminal ChatGPT application **through its state**, not by scraping a pane, opening Chrome, or pretending terminal output is an application model.

This implementation reuses tritao/chatgpt-web's Python `ChatTui` fields and methods via the existing subclass adapter, and re-gpt's existing transport. No upstream code is copied.

## Launch
After obtaining upstream `tritao/chatgpt-web` separately, launch:

```sh
mkdir -p "$XDG_RUNTIME_DIR/re-gpt-tui"
chmod 700 "$XDG_RUNTIME_DIR/re-gpt-tui"
python -m re_gpt.upstream_tui_adapter \
  --upstream-tui /path/to/chatgpt-web/chatgpt_tui.py \
  --control-socket "$XDG_RUNTIME_DIR/re-gpt-tui/control.sock"
```

`TuiStateServer` creates an owner-only Unix socket and refuses existing paths. It verifies Unix peer UID where supported. It never starts a browser, tmux or a second ChatGPT transport. Only launch the socket when the operator deliberately enables it.

The action protocol is JSON:
- `{"action":"state"}`: read current conversation ID, title, status, busy state, picker loading/visibility/query, counts, selection and bounded result titles/IDs (no transcript bodies).
- `{"action":"open_picker"}`: queue upstream `open_resume()`.
- `{"action":"set_query","query":"..."}`: queue upstream resume-search text update and `filter_resume()`.
- `{"action":"select","index":0}`: queue selecting the currently filtered item.

Actions queue on the original `asyncio`/prompt_toolkit UI event loop. The caller must request `state` again to observe their effect. No generic keyboard injection, prompt sending, attachment upload, arbitrary method invocation or shell command execution is present.

## Authorization and boundaries
Socket filesystem mode 0600, directory mode 0700 and same-UID peer check protect local access, but same-UID processes with access to the configured path can operate the picker. ITIR MCP must independently gate controls using `ITIR_TUI_ALLOW_CONTROL=1`. Do not expose this socket to untrusted local processes. Session-title and ID information may be sensitive: avoid logging/exporting responses unnecessarily.

Read-only projection precedent: `dashi_agda/DASHI/Interop/ITIRRibbonProjectionAuthorityBridgeExact.agda`. Golden Agda contract is not a claim that this Python code has been kernel-verified. Production semantic data remains SLR; persistent history remains ITIR PostgreSQL; SensibLaw is deprecated reference. Upstream ChatTui implementation remains owned by tritao.

## Discriminators
1. Blank picker with zero search: state reports `picker_total=0`, `match_count=0`, and `status` separately.
2. A nonblank query that filters all results reports `picker_total>0`, `match_count=0`.
3. Inspect and queue open/query/select with no tmux, DOM, or browser.
4. Bounded result fields contain no transcript bodies.
5. Explicit control gate refuses writes from read-only MCP clients.
6. Socket permission and same-UID checks fail closed.
7. Live state read must never cause a ChatGPT mutation. Picker selection requests a read of the selected conversation only.

Source-written with offline tests; no live-process acceptance run or Agda/Lean kernel verification asserted.
