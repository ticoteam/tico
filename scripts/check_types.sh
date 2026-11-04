#!/usr/bin/env bash
# Type-check the boundary modules listed in pyrightconfig.json (Phase 5: pyright basic, growing include list).
# Pyright is pinned; it resolves imports against .venv-pyright when that exists (local), else the python3 on PATH
# (CI installs backend/requirements-dev.txt there). Extra arguments go to pyright, e.g. a file to check alone.
#   scripts/check_types.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PYRIGHT="pyright@1.1.414"    # released 2026-09-09; bump only after the release is two weeks old
if [ -x .venv-pyright/bin/python ]; then
  PYTHON_BIN="$PWD/.venv-pyright/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi
exec uvx "$PYRIGHT" --pythonpath "$PYTHON_BIN" "$@"
