"""Attachment staging and canonical graph receipts.

The browser DOM is authoritative only for pre-submit staging. Durable attachment
success is certified from the validated conversation graph after the user node
commits. Native file input is preferred; drag/drop is compatibility fallback.
"""

from __future__ import annotations

import hashlib
import mimetypes
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ATTACH_INPUT_SELECTOR = 'input[aria-label="Attach files"]'
DROP_TARGET_SELECTORS = (
    '[data-testid="composer-root"]',
    'form:has(.ProseMirror[contenteditable="true"])',
    '.ProseMirror[contenteditable="true"]',
)


@dataclass(frozen=True)
class LocalAttachment:
    path: Path
    filename: str
    mime_type: str | None
    size: int
    sha256: str

    @classmethod
    def from_path(cls, raw: Path | str) -> "LocalAttachment":
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"attachment is not a readable file: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        mime, _ = mimetypes.guess_type(path.name)
        return cls(path, path.name, mime, path.stat().st_size, digest.hexdigest())


@dataclass(frozen=True)
class AttachmentReadyReceipt:
    filename: str
    rendered: bool
    uploading: bool
    removable: bool

    @property
    def ready(self) -> bool:
        return self.rendered and not self.uploading


def _attachment_scope_script() -> str:
    return """(filename) => {
      const input = document.querySelector('input[aria-label="Attach files"]');
      const editor = document.querySelector('.ProseMirror[contenteditable="true"]');
      let scope = (input && (input.closest('form') || input.closest('[data-testid="composer-root"]'))) ||
                  (editor && (editor.closest('form') || editor.closest('[data-testid="composer-root"]'))) ||
                  document.body;
      const text = (scope.innerText || '').toLowerCase();
      const name = filename.toLowerCase();
      const rendered = text.includes(name) || Array.from(scope.querySelectorAll('[aria-label],[title]')).some(el =>
        ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).toLowerCase().includes(name));
      const uploading = Array.from(scope.querySelectorAll('[aria-busy="true"], progress, [role="progressbar"]')).some(el => {
        const label = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.innerText || '')).toLowerCase();
        return !label || label.includes(name) || label.includes('upload');
      });
      const removable = Array.from(scope.querySelectorAll('button,[role="button"]')).some(el => {
        const label = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).toLowerCase();
        return (label.includes('remove') || label.includes('delete')) && (label.includes(name) || rendered);
      });
      return {rendered, uploading, removable};
    }"""


async def _drag_drop_fallback(relay, attachments: Sequence[LocalAttachment]) -> None:
    """Stage files by dispatching a real DataTransfer from a temporary file input."""

    target = None
    for selector in DROP_TARGET_SELECTORS:
        locator = relay.page.locator(f"{selector}:visible").first
        if await locator.count():
            target = locator
            break
    if target is None:
        raise RuntimeError("attachment input absent and no active composer drop target found")

    temp_id = "re-gpt-attachment-drop-source"
    await relay.page.evaluate(
        """(id) => {
          const old = document.getElementById(id); if (old) old.remove();
          const input = document.createElement('input');
          input.id = id; input.type = 'file'; input.multiple = true;
          input.style.position = 'fixed'; input.style.left = '-10000px';
          document.body.appendChild(input);
        }""",
        temp_id,
    )
    source = relay.page.locator(f"#{temp_id}")
    try:
        await source.set_input_files([str(item.path) for item in attachments])
        handle = await target.element_handle()
        if handle is None:
            raise RuntimeError("composer drop target disappeared during attachment staging")
        await relay.page.evaluate(
            """({id, target}) => {
              const input = document.getElementById(id);
              if (!input || !target) throw new Error('drop source/target missing');
              const dt = new DataTransfer();
              for (const file of input.files) dt.items.add(file);
              for (const type of ['dragenter','dragover','drop']) {
                target.dispatchEvent(new DragEvent(type, {bubbles:true, cancelable:true, dataTransfer:dt}));
              }
            }""",
            {"id": temp_id, "target": handle},
        )
    finally:
        await relay.page.evaluate("(id) => { const el=document.getElementById(id); if (el) el.remove(); }", temp_id)


async def dismiss_blocking_dialogs(relay) -> bool:
    """Close transient modal dialogs (e.g. 'File added to chat only') that block composer clicks."""
    try:
        closed = await relay.page.evaluate(
            """() => {
              let acted = false;
              const dialogs = Array.from(document.querySelectorAll('.codex-dialog[data-state="open"], [role="dialog"][data-state="open"]'));
              for (const dialog of dialogs) {
                const closeBtn = Array.from(dialog.querySelectorAll('button,[role="button"]')).find(btn => {
                  const label = ((btn.getAttribute('aria-label') || '') + ' ' + (btn.getAttribute('title') || '') + ' ' + (btn.innerText || '')).toLowerCase();
                  return label.includes('close') || label.includes('dismiss') || label.includes('got it');
                });
                if (closeBtn) {
                  closeBtn.click();
                  acted = true;
                }
              }
              return acted;
            }"""
        )
        overlay = relay.page.locator('.codex-dialog-overlay[data-state="open"]:visible').first
        if await overlay.count():
            await relay.page.keyboard.press("Escape")
            closed = True
        if closed:
            await relay.page.wait_for_timeout(150)
        return bool(closed)
    except Exception:
        return False


async def _clear_existing_staged_chips(relay, attachments: Sequence[LocalAttachment]) -> None:
    """Remove only exact-filename leftover chips from an earlier pre-commit draft."""
    try:
        names = [item.filename.lower() for item in attachments]
        removed = await relay.page.evaluate(
            r"""(targetNames) => {
              const editor = document.querySelector('.ProseMirror[contenteditable="true"]');
              const scope = (editor && (editor.closest('form') || editor.closest('[data-testid="composer-root"]'))) || document.body;
              const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
              const exactFilename = (label, name) => {
                const pattern = new RegExp(`(?:^|\\s)${escapeRegExp(name)}(?:$|\\s)`, 'i');
                return pattern.test(label);
              };
              let count = 0;
              for (const btn of Array.from(scope.querySelectorAll('button,[role="button"]'))) {
                const label = ((btn.getAttribute('aria-label') || '') + ' ' + (btn.getAttribute('title') || '')).toLowerCase().trim();
                const isRemove = label.includes('remove') || label.includes('delete');
                if (isRemove && targetNames.some(name => exactFilename(label, name))) {
                  btn.click();
                  count += 1;
                }
              }
              return count;
            }""",
            names,
        )
        if removed:
            await relay.page.wait_for_timeout(200)
    except Exception:
        return


async def stage_attachments(relay, attachments: Sequence[LocalAttachment]) -> list[AttachmentReadyReceipt]:
    if not attachments:
        return []
    await dismiss_blocking_dialogs(relay)
    await _clear_existing_staged_chips(relay, attachments)
    locator = relay.page.locator(f"{ATTACH_INPUT_SELECTOR}:visible").first
    if not await locator.count():
        locator = relay.page.locator(ATTACH_INPUT_SELECTOR).first
    if await locator.count():
        await locator.set_input_files([str(item.path) for item in attachments])
    else:
        await _drag_drop_fallback(relay, attachments)

    deadline = time.monotonic() + relay.timeout_ms / 1000.0
    latest: list[AttachmentReadyReceipt] = []
    script = _attachment_scope_script()
    while time.monotonic() < deadline:
        await dismiss_blocking_dialogs(relay)
        latest = []
        for item in attachments:
            raw = await relay.page.evaluate(script, item.filename)
            latest.append(AttachmentReadyReceipt(
                filename=item.filename,
                rendered=bool(isinstance(raw, dict) and raw.get("rendered")),
                uploading=bool(not isinstance(raw, dict) or raw.get("uploading")),
                removable=bool(isinstance(raw, dict) and raw.get("removable")),
            ))
        if latest and all(receipt.ready for receipt in latest):
            await relay.page.wait_for_timeout(250)
            await dismiss_blocking_dialogs(relay)
            return latest
        await relay.page.wait_for_timeout(200)
    raise TimeoutError(f"timed out waiting for attachment readiness: {[asdict(r) for r in latest]}")


@dataclass(frozen=True)
class CanonicalAttachment:
    node_id: str
    file_id: str
    filename: str
    mime_type: str | None
    size: int | None
    pointer: str | None


def _first(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _candidate_mappings(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    metadata = message.get("metadata")
    if isinstance(metadata, Mapping):
        raw = metadata.get("attachments")
        if isinstance(raw, list):
            candidates.extend(item for item in raw if isinstance(item, Mapping))
    raw = message.get("attachments")
    if isinstance(raw, list):
        candidates.extend(item for item in raw if isinstance(item, Mapping))
    content = message.get("content")
    if isinstance(content, Mapping):
        raw = content.get("attachments")
        if isinstance(raw, list):
            candidates.extend(item for item in raw if isinstance(item, Mapping))
    return candidates


def canonical_attachments_for_node(chat: Mapping[str, Any], node_id: str) -> list[CanonicalAttachment]:
    mapping = chat.get("mapping")
    if not isinstance(mapping, Mapping): return []
    node = mapping.get(node_id)
    if not isinstance(node, Mapping): return []
    message = node.get("message")
    if not isinstance(message, Mapping): return []
    result: list[CanonicalAttachment] = []
    seen: set[tuple[str, str]] = set()
    for item in _candidate_mappings(message):
        file_id = str(_first(item, ("id", "file_id", "fileId")) or "").strip()
        pointer = _first(item, ("asset_pointer", "pointer", "url"))
        pointer = str(pointer).strip() if pointer else None
        if not file_id and pointer and "://" in pointer:
            file_id = pointer.split("://", 1)[1].strip("/")
        filename = str(_first(item, ("name", "filename", "file_name")) or "").strip()
        mime = _first(item, ("mime_type", "mime", "content_type"))
        mime = str(mime).strip() if mime else None
        size_raw = _first(item, ("size", "size_bytes", "bytes"))
        try: size = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError): size = None
        key = (file_id, filename)
        if key in seen or not (file_id or filename): continue
        seen.add(key)
        result.append(CanonicalAttachment(str(node_id), file_id, filename, mime, size, pointer))
    return result


def canonical_attachments_for_user_node(chat: Mapping[str, Any], user_node_id: str) -> list[CanonicalAttachment]:
    return canonical_attachments_for_node(chat, user_node_id)


def iter_canonical_attachments(chat: Mapping[str, Any]) -> list[CanonicalAttachment]:
    mapping = chat.get("mapping")
    if not isinstance(mapping, Mapping): return []
    result: list[CanonicalAttachment] = []
    for node_id in mapping:
        result.extend(canonical_attachments_for_node(chat, str(node_id)))
    return result


def match_expected_attachments(chat: Mapping[str, Any], user_node_id: str, expected: Sequence[LocalAttachment]) -> tuple[bool, list[CanonicalAttachment]]:
    actual = canonical_attachments_for_user_node(chat, user_node_id)
    matched: list[CanonicalAttachment] = []
    used: set[tuple[str, str, str | None]] = set()
    for item in expected:
        candidates = [
            entry for entry in actual
            if entry.filename == item.filename
            and (entry.file_id, entry.filename, entry.pointer) not in used
        ]
        if item.mime_type:
            candidates = [entry for entry in candidates if not entry.mime_type or entry.mime_type == item.mime_type]
        candidates = [entry for entry in candidates if bool(entry.file_id or entry.pointer)]
        if not candidates: return False, matched
        selected = candidates[-1]
        matched.append(selected)
        used.add((selected.file_id, selected.filename, selected.pointer))
    return True, matched
