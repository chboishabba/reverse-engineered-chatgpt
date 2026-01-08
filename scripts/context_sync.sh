#!/usr/bin/env bash
set -euo pipefail

# Lightweight workflow for the automation-friendly CLI helpers described in
# README.md so we can rerun a single script instead of re-typing the commands.
#
# You can edit the environment variables below instead of changing the body when
# you want to tweak the inspected conversation, line range, or grep target.

CONVERSATION_TARGET="${CONVERSATION_TARGET:-}"
VIEW_ADDITIONAL="${VIEW_ADDITIONAL:-since last update}"
LINE_RANGE="${LINE_RANGE:-}"
RG_PATTERN="${RG_PATTERN:-}"
EXPORT_DIR="chat_exports"
# Recent CLI runs tend to hang, so cap each helper at 5 seconds by default.
COMMAND_TIMEOUT="${COMMAND_TIMEOUT:-5s}"

function usage() {
  cat <<'EOF'
Usage: context_sync.sh [conversation-id-or-title]

Environment variables you can adjust:
  CONVERSATION_TARGET  - Override the conversation ID/title without passing an arg
  VIEW_ADDITIONAL      - Suffix added to the `--view` arguments (default: "since last update")
  LINE_RANGE          - Optional "lines START-END" appended to the `--view` arguments
  RG_PATTERN          - Optional pattern to search exported JSON after --download

The script runs the automation commands in order: --list, --inspect, --download, --view.
EOF
}

if [[ "${1:-}" =~ ^-h|--help$ ]]; then
  usage
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  CONVERSATION_TARGET="$1"
fi

if [[ -z "$CONVERSATION_TARGET" ]]; then
  echo "Set CONVERSATION_TARGET or pass a conversation selector argument." >&2
  usage
  exit 1
fi

echo "1. Listing available conversations"
timeout "$COMMAND_TIMEOUT" python -m re_gpt.cli --list

echo
echo "2. Inspecting metadata for '$CONVERSATION_TARGET'"
timeout "$COMMAND_TIMEOUT" python -m re_gpt.cli --inspect "$CONVERSATION_TARGET"

if [[ ! -d "$EXPORT_DIR" ]]; then
  mkdir -p "$EXPORT_DIR"
fi

echo
echo "3. Downloading/persisting '$CONVERSATION_TARGET'"
timeout "$COMMAND_TIMEOUT" python -m re_gpt.cli --download "$CONVERSATION_TARGET"

VIEW_ARGUMENT="$CONVERSATION_TARGET $VIEW_ADDITIONAL"
if [[ -n "$LINE_RANGE" ]]; then
  VIEW_ARGUMENT="$VIEW_ARGUMENT lines $LINE_RANGE"
fi

echo
echo "4. Viewing recent messages ($VIEW_ARGUMENT)"
timeout "$COMMAND_TIMEOUT" python -m re_gpt.cli --view "$VIEW_ARGUMENT"

if [[ -n "$RG_PATTERN" ]]; then
  echo
  echo "5. Searching exported JSON for '$RG_PATTERN'"
  rg --no-heading --line-number -- "$RG_PATTERN" "$EXPORT_DIR"
fi

echo
echo "Done. Adjust the variables at the top or rerun with a different argument as needed."
