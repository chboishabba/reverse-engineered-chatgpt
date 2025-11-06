#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${REPO_ROOT}/.venv"

echo "[run_tests] Ensuring virtual environment at ${VENV_PATH}"
if [ ! -d "${VENV_PATH}" ]; then
  echo "[run_tests] Creating virtual environment with python3 -m venv"
  python3 -m venv "${VENV_PATH}"
else
  echo "[run_tests] Virtual environment already exists"
fi

# shellcheck disable=SC1091
source "${VENV_PATH}/bin/activate"

echo "[run_tests] Upgrading pip"
pip install --upgrade pip

echo "[run_tests] Installing project in editable mode (browser extras)"
pip install -e "${REPO_ROOT}[browser]"

if command -v playwright >/dev/null 2>&1; then
  echo "[run_tests] Ensuring Playwright Firefox browser is installed"
  playwright install firefox
else
  echo "[run_tests] Playwright CLI not found after installation" >&2
  exit 1
fi

echo "[run_tests] Installing test dependencies"
pip install pytest pytest-mock


echo "[run_tests] Running tests"
exec python -m unittest discover -s "${REPO_ROOT}/tests"
