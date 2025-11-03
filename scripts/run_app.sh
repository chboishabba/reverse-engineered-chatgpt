#!/usr/bin/env bash
# Launch the interactive CLI.  Use ``python -u`` if you need unbuffered output
# for streaming responses when embedding this script elsewhere.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
PYTHON_BIN="${PYTHON:-python}"

exec "${PYTHON_BIN}" "${ROOT_DIR}/examples/interactive_cli.py" "$@"
