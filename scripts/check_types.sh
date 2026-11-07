#!/usr/bin/env bash
# Type-check every backend module with basedpyright (pinned) against its baseline, baselines/pyright (one file per module;
# basedpyright reads the .basedpyright/baseline.json generated from them), then the ratchet in
# scripts/check_pyright_ratchet.py. The baseline and the legacy module list only shrink, and a module added since
# the legacy list was written must be strict (migrations excepted). Takes about 30 seconds on a Mac; CI runs it in
# the static job.
#   scripts/check_types.sh                                 check: fails on a new error and on a non-strict new
#                                                          module; a baseline entry that no longer fires warns
#   scripts/check_types.sh --writebaseline                 rewrite the baseline after fixes (review the diff)
#   PYRIGHT_BASE_REF=origin/main scripts/check_types.sh    also fail when the baseline or the legacy list grew
#   scripts/check_types.sh path/to/file.py                 extra arguments go to basedpyright (a file to check alone)
# Local setup, so imports resolve as they do in CI (Python 3.15, the same pins). One venv serves every worktree: make it
# in the main checkout (the folder that holds the shared .git), seconds with uv:
#   uv venv --python 3.15 .venv-pyright && uv pip install --python .venv-pyright/bin/python -r backend/requirements-dev.txt
# It looks for .venv-pyright in this worktree, then in the main checkout, then uses the python3 on PATH (CI installs
# backend/requirements-dev.txt there; on a Mac without the venv, imports such as livekit and slack_sdk do not resolve).
set -euo pipefail
cd "$(dirname "$0")/.."
BASEDPYRIGHT="basedpyright@1.40.2"    # released 2026-10-04; bump only after the release is two weeks old
main_checkout="$(cd "$(git rev-parse --git-common-dir 2>/dev/null || echo .git)/.." 2>/dev/null && pwd || true)"
if [ -x .venv-pyright/bin/python ]; then
  PYTHON_BIN="$PWD/.venv-pyright/bin/python"
elif [ -n "$main_checkout" ] && [ -x "$main_checkout/.venv-pyright/bin/python" ]; then
  PYTHON_BIN="$main_checkout/.venv-pyright/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
  if [ -z "${CI:-}" ]; then
    echo "check_types: no .venv-pyright here or in ${main_checkout:-the main checkout}; using $PYTHON_BIN (see the setup line at the top of this script)" >&2
  fi
fi
case " $* " in
  *" --writebaseline "*)
    # basedpyright writes .basedpyright/baseline.json; its entries are split into baselines/pyright, one file per module.
    status=0
    uvx "$BASEDPYRIGHT" --pythonpath "$PYTHON_BIN" "$@" || status=$?
    if [ -f .basedpyright/baseline.json ]; then
      "$PYTHON_BIN" scripts/pyright_baseline.py --split
    fi
    exit "$status"
    ;;
esac
# lock: CI's default, made explicit so a laptop run is the same. Warnings count too; the baseline holds them as well.
# A new error fails. A baseline entry that did not fire only warns: basedpyright's inference of unannotated code is not
# the same on every run (Nov 7: "went down by 3" failed a merge group whose only change was under scripts/), so failing
# on it ejects good PRs at random. The ratchet below still fails when the baseline grows; --writebaseline drops gone
# entries.
# The pieces in baselines/pyright are the source; basedpyright reads the single file generated from them (git-ignored).
"$PYTHON_BIN" scripts/pyright_baseline.py --generate
out="$(mktemp)"
set +e
uvx "$BASEDPYRIGHT" --pythonpath "$PYTHON_BIN" --baselinemode=lock --warnings "$@" 2>&1 | tee "$out"
status="${PIPESTATUS[0]}"
set -e
if [ "$status" -ne 0 ]; then
  # Exit 3 with "0 errors, 0 warnings" and "went down by N" is only the baseline shrinking; anything else is real.
  if [ "$status" -eq 3 ] && grep -q 'went down by' "$out" && grep -q '^0 errors, 0 warnings' "$out"; then
    n="$(grep -o 'went down by [0-9]*' "$out" | head -1)"
    echo "::warning::basedpyright: baseline entries did not fire on this run ($n); not a failure. Drop them with scripts/check_types.sh --writebaseline when they stay gone."
  else
    rm -f "$out"
    exit "$status"
  fi
fi
rm -f "$out"
if [ -n "${PYRIGHT_BASE_REF:-}" ]; then
  exec "$PYTHON_BIN" scripts/check_pyright_ratchet.py --base "$PYRIGHT_BASE_REF"
fi
exec "$PYTHON_BIN" scripts/check_pyright_ratchet.py
