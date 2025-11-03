#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${REPO_ROOT}/.venv"

echo "[run_app] Ensuring virtual environment at ${VENV_PATH}"
if [ ! -d "${VENV_PATH}" ]; then
  echo "[run_app] Creating virtual environment with python3 -m venv"
  python3 -m venv "${VENV_PATH}"
else
  echo "[run_app] Virtual environment already exists"
fi

# shellcheck disable=SC1091
source "${VENV_PATH}/bin/activate"

echo "[run_app] Upgrading pip"
pip install --upgrade pip

echo "[run_app] Installing project in editable mode"
pip install -e "${REPO_ROOT}"

echo "[run_app] Launching interactive CLI"
exec python -m re_gpt.cli "$@"
