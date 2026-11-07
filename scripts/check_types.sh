#!/usr/bin/env bash
# Type-check every backend module with basedpyright (pinned) against .basedpyright/baseline.json, then the ratchet in
# scripts/check_pyright_ratchet.py. The baseline and the legacy module list only shrink, and a module added since
# the legacy list was written must be strict (migrations excepted). Takes about 30 seconds on a Mac; CI runs it in
# the static job.
#   scripts/check_types.sh                                 check: fails on a new error, on a baseline entry that
#                                                          no longer fires, and on a non-strict new module
#   scripts/check_types.sh --writebaseline                 rewrite the baseline after fixes (review the diff)
#   PYRIGHT_BASE_REF=origin/main scripts/check_types.sh    also fail when the baseline or the legacy list grew
#   scripts/check_types.sh path/to/file.py                 extra arguments go to basedpyright (a file to check alone)
# Local setup, so imports resolve as they do in CI (Python 3.15, the same pins):
#   python3.15 -m venv .venv-pyright && .venv-pyright/bin/python -m pip install -r backend/requirements-dev.txt
# Without .venv-pyright it uses the python3 on PATH (CI installs backend/requirements-dev.txt there).
set -euo pipefail
cd "$(dirname "$0")/.."
BASEDPYRIGHT="basedpyright@1.40.2"    # released 2026-10-04; bump only after the release is two weeks old
if [ -x .venv-pyright/bin/python ]; then
  PYTHON_BIN="$PWD/.venv-pyright/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi
case " $* " in
  *" --writebaseline "*)
    # basedpyright writes the baseline; then it is put in the layout scripts/baseline_merge.py merges (the ratchet checks it).
    status=0
    uvx "$BASEDPYRIGHT" --pythonpath "$PYTHON_BIN" "$@" || status=$?
    if [ -f .basedpyright/baseline.json ]; then
      "$PYTHON_BIN" scripts/baseline_merge.py normalize .basedpyright/baseline.json
    fi
    exit "$status"
    ;;
esac
# lock: CI's default, made explicit so a laptop run is the same. Warnings count too; the baseline holds them as well.
uvx "$BASEDPYRIGHT" --pythonpath "$PYTHON_BIN" --baselinemode=lock --warnings "$@"
if [ -n "${PYRIGHT_BASE_REF:-}" ]; then
  exec "$PYTHON_BIN" scripts/check_pyright_ratchet.py --base "$PYRIGHT_BASE_REF"
fi
exec "$PYTHON_BIN" scripts/check_pyright_ratchet.py
