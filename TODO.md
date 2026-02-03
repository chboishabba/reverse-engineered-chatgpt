# TODO

- [x] Update asset download tests to use an inline HTML fixture instead of `gpt-page-source-raw.txt`.
- [x] Document test requirements in `README.md`.
- [x] Keep fixture content minimal and synthetic when used in tests.
- [x] Record the automation-friendly CLI helpers (`--list`, `--inspect`, `--view`, `--download`) that support scripted `rg` workflows.
- [x] Add automation commands (`--view`, `--inspect`, `--download`) that emit conversation dumps, metadata, and exports without entering the interactive session.
- [x] Provide a reusable `scripts/context_sync.sh` wrapper so the automation flow can be rerun without typing every command.
- [x] Explain in the docs how `scripts/context_sync.sh` is wired to the CLI helpers (`run_noninteractive_view`, `run_inspect_command`, `handle_download_command`) so future edits know where to hook.
- [x] Capture the ROCm-friendly Codex reinstall steps (tarball Node, no apt node) in `docs/codex-docker-reinstall.md`.
- [ ] Implement an MCP-backed session in `re_gpt/sync_chatgpt.py` that attaches to the authenticated `chatgpt.com` tab, reads cookies/localStorage, and drives CDP commands while keeping the Playwright/Firefox flow as a fallback.
