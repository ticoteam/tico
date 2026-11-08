#!/usr/bin/env bash
# A merge queue group's whole-tree checks on its merge commit, which is what lands on main. Two pull requests can each
# pass their own static checks and still break one of these together (a migration timestamp, a route one removes and the
# other calls, a type one changes and the other uses), so the group runs them; the per-file checks (lint, shellcheck,
# actionlint) cannot break that way and run on the pull request only. The plan job starts this beside its test selection,
# so a group needs no static checks VM (.github/workflows/pr-checks.yml). Every check runs; any failure fails the script.
#   PATH="$RUNNER_TEMP/venv/bin:$PATH" bash scripts/ci/merge_commit_checks.sh <base sha> <merge commit sha>
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
base=$1
head=$2
status=0
check() {
  echo "== $*"
  if ! "$@"; then
    echo "::error::merge commit check failed: $*"
    status=1
  fi
}
# The pinned uv the static checks job installs: basedpyright runs through uvx.
command -v uvx >/dev/null || python3 -m pip install --disable-pip-version-check --quiet uv==0.13.0
export PYRIGHT_BASE_REF="$base"
check python3 scripts/check_migration_order.py --base "$base" --head "$head" --base-name main
check python3 -m backend.openapi_v2 --check
check python3 scripts/check_wire_contract.py
check bash scripts/check_types.sh
check bash scripts/check_ui_types.sh --base "$base"
check python3 scripts/check_ui_strict.py --base "$base"
check bash scripts/check_ui_routes.sh --base "$base"
check python3 scripts/check_ui_source_suffixes.py
exit "$status"
