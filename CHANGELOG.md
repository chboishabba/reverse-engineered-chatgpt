# Changelog

## Unreleased

- Documented the automation-friendly CLI helpers and how they fit into scripting workflows.
- Added metadata inspection (`--inspect`), read-only viewing (`--view`), and export (`--download`) modes to the interactive CLI so tools can request conversation lists, query recorded history, and rest easily accessible.
- Added `scripts/context_sync.sh`, a customizable wrapper that runs the automation helpers in sequence and keeps an `rg` pass handy for exported chats.
- Documented how `scripts/context_sync.sh` maps to `run_noninteractive_view`, `run_inspect_command`, and `handle_download_command`, guiding future hook-ups.
