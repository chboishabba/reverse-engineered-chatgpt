#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONVO_FILE="$ROOT_DIR/__CONTEXT/convo_ids.md"
OUT_DIR="$ROOT_DIR/__CONTEXT/last_sync"
TS_UTC="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_FILE="$OUT_DIR/${TS_UTC}_context_sync.txt"

PYTHON_BIN="$ROOT_DIR/reverse-engineered-chatgpt/.venv/bin/python"
VIEW_SCRIPT="$ROOT_DIR/reverse-engineered-chatgpt/scripts/view_conversation.py"
LATEST_SCRIPT="$ROOT_DIR/reverse-engineered-chatgpt/scripts/check_latest_chat.py"

if [[ ! -f "$CONVO_FILE" ]]; then
  echo "Missing $CONVO_FILE" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing python venv at $PYTHON_BIN" >&2
  exit 1
fi

TOKEN="${CHATGPT_SESSION_TOKEN:-}"
if [[ -z "$TOKEN" && -f "$HOME/.chatgpt_session" ]]; then
  TOKEN="$(head -n1 "$HOME/.chatgpt_session")"
fi

if [[ -z "$TOKEN" ]]; then
  echo "No session token found. Set CHATGPT_SESSION_TOKEN or add ~/.chatgpt_session" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

{
  echo "# Chat Context Sync"
  echo "Generated (UTC): $TS_UTC"
  echo "Source: $CONVO_FILE"
  echo
} > "$OUT_FILE"

# Parse markdown table rows: | id | title | tail_lines | notes |
# Skip header/separator/blank/comment lines.
while IFS='|' read -r _ raw_id raw_title raw_tail raw_notes _; do
  id="$(echo "${raw_id:-}" | xargs)"
  title="$(echo "${raw_title:-}" | xargs)"
  tail_lines="$(echo "${raw_tail:-}" | xargs)"
  notes="$(echo "${raw_notes:-}" | xargs)"

  if [[ -z "$id" || "$id" == "id" ]]; then
    continue
  fi

  if [[ -z "$tail_lines" || ! "$tail_lines" =~ ^[0-9]+$ ]]; then
    tail_lines=60
  fi

  {
    echo "---"
    echo "Conversation ID: $id"
    if [[ -n "$title" ]]; then
      echo "Title: $title"
    fi
    if [[ -n "$notes" ]]; then
      echo "Notes: $notes"
    fi
    echo
  } >> "$OUT_FILE"

  "$PYTHON_BIN" "$LATEST_SCRIPT" \
    --conversation-id "$id" \
    --token "$TOKEN" \
    >> "$OUT_FILE"

  echo >> "$OUT_FILE"
  echo "Tail ($tail_lines lines, numbered):" >> "$OUT_FILE"

  "$PYTHON_BIN" "$VIEW_SCRIPT" \
    --conversation-id "$id" \
    --lines "1+" \
    --token "$TOKEN" \
    | tail -n "$tail_lines" \
    | nl -ba \
    >> "$OUT_FILE"

  echo >> "$OUT_FILE"
  echo >> "$OUT_FILE"

done < <(rg -n "^\|" "$CONVO_FILE")

printf 'Wrote %s\n' "$OUT_FILE"
