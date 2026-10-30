#!/usr/bin/env bash
# The Python lint gate: pinned ruff against ruff-baseline.toml (see scripts/lint_baseline.py). Takes seconds.
# CI sets LINT_BASE_REF (for example origin/main) on pull requests so a grown baseline or suppression set fails too.
set -euo pipefail
cd "$(dirname "$0")/.."
args=(--check)
if [ -n "${LINT_BASE_REF:-}" ]; then
  args+=(--base "$LINT_BASE_REF")
fi
exec python3 scripts/lint_baseline.py "${args[@]}"
