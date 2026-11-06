#!/usr/bin/env bash
# The fast local gate: each static check that applies to the files you changed, one line per check, and a non-zero
# exit if any fails. Run it before you push. `git config core.hooksPath .githooks` runs it on every push.
#   scripts/check.sh          the files changed since the merge-base with origin/main, plus uncommitted and new files
#   scripts/check.sh --all    every tracked file, every check (CI runs these too)
# Checks: lint.sh (Python baseline), ruff format (once ruff.toml has a [format] section), shell_lint.sh (shellcheck),
# actionlint (workflows), cargo fmt (the Rust crates scripts/rust_checks.sh formats), migration order,
# check_ui_types.sh and check_types.sh (the pyright boundary modules). Each runs only when its inputs changed.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

ALL=0
case ${1:-} in
  "") ;;
  --all) ALL=1 ;;
  -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
  *) echo "usage: scripts/check.sh [--all]" >&2; exit 2 ;;
esac

now() { python3 -c 'import time; print(time.time())'; }
elapsed() { awk -v s="$1" -v e="$2" 'BEGIN { printf "%.1fs", e - s }'; }
passed=0 failed=0 skipped=0
T0=$(now)

# The base: the merge-base with origin/main (or main), so committed work is checked too.
base=""
for ref in origin/main main; do
  if git rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then
    base=$(git merge-base "$ref" HEAD 2>/dev/null) || base=""
    if [ -n "$base" ]; then break; fi
  fi
done

if [ "$ALL" = 1 ]; then
  listing=$(git ls-files)
  scope="every tracked file"
elif [ -n "$base" ]; then
  listing=$(git diff --name-only --diff-filter=d "$base" --; git ls-files --others --exclude-standard)
  scope="changed since ${base:0:9} (committed, uncommitted and new)"
else
  listing=$(git diff --name-only --diff-filter=d HEAD --; git ls-files --others --exclude-standard)
  scope="uncommitted only (no origin/main to compare with; run git fetch)"
fi
CHANGED=$(printf '%s\n' "$listing" | sort -u | while IFS= read -r path; do
  if [ -n "$path" ] && [ -f "$path" ]; then printf '%s\n' "$path"; fi
done)
echo "check: $(grep -c . <<< "$CHANGED" || true) file(s), $scope"

# Here-strings, not pipes into grep -q: under pipefail an early exit from grep would fail a long list.
changed_matches() { grep -qE "$1" <<< "$CHANGED"; }
changed_paths() { grep -E "$1" <<< "$CHANGED" || true; }

run() {  # NAME COMMAND...: a PASS or FAIL line with its time; the output is shown only for a failure
  local name=$1 start end out status word
  shift
  start=$(now)
  out=$("$@" 2>&1)
  status=$?
  end=$(now)
  if [ "$status" -eq 0 ]; then
    passed=$((passed + 1)); word=PASS
  else
    failed=$((failed + 1)); word=FAIL
  fi
  printf '  %-4s  %-38s %7s\n' "$word" "$name" "$(elapsed "$start" "$end")"
  if [ "$status" -ne 0 ] && [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/        /'; fi
}

skip() {  # NAME REASON
  skipped=$((skipped + 1))
  printf '  %-4s  %-38s %s\n' SKIP "$1" "$2"
}

# The Rust crates rust_checks.sh formats, and its toolchain. A crate listed as pending reports but does not fail.
RUST_CRATES=$(sed -n 's/^CRATES="\(.*\)"$/\1/p' scripts/rust_checks.sh)
# shellcheck disable=SC2016  # the sed pattern matches the literal ${RUST_TOOLCHAIN:-...} in rust_checks.sh
RUST_TOOLCHAIN=$(sed -n 's/^TOOLCHAIN="${RUST_TOOLCHAIN:-\(.*\)}"$/\1/p' scripts/rust_checks.sh)
RUST_TOOLCHAIN=${RUST_TOOLCHAIN:-1.92.0}
# shellcheck disable=SC2329  # run calls it by name
rust_fmt() {  # CRATE: cargo fmt --check as rust_checks.sh runs it
  local crate=$1 pending=0 status=0 out
  if grep -qx "$crate" scripts/rust-fmt-pending.txt; then pending=1; fi
  out=$(cd "$crate" && if printf '%s\n' "$(rustup toolchain list 2>/dev/null)" | grep -q "^$RUST_TOOLCHAIN"; then
          cargo +"$RUST_TOOLCHAIN" fmt --check; else cargo fmt --check; fi 2>&1) || status=$?
  if [ "$status" -ne 0 ] && ! grep -q '^Diff in' <<< "$out"; then
    printf '%s\n' "$out"
    return 1
  fi
  if [ "$pending" = 1 ]; then
    if [ "$status" -eq 0 ]; then
      echo "$crate is formatted: remove it from scripts/rust-fmt-pending.txt"
      return 1
    fi
    return 0
  fi
  if [ "$status" -ne 0 ]; then printf '%s\n' "$out"; return 1; fi
  return 0
}

# 1. Python lint: the ruff baseline, compared with the merge-base so a grown baseline fails here as it does in CI.
if [ "$ALL" = 1 ] || changed_matches '\.py$|^ruff(-baseline)?\.toml$|^scripts/lint_baseline\.py$'; then
  run "lint (ruff baseline)" env LINT_BASE_REF="$base" bash scripts/lint.sh
else
  skip "lint (ruff baseline)" "no Python or ruff config changed"
fi

# 2. Python format: only once ruff.toml has a [format] section.
py_files=()
while IFS= read -r path; do [ -n "$path" ] && py_files+=("$path"); done <<< "$(changed_paths '\.py$')"
if ! grep -q '^\[format\]' ruff.toml; then
  skip "ruff format" "ruff.toml has no [format] section"
elif [ "${#py_files[@]}" -eq 0 ]; then
  skip "ruff format" "no Python file changed"
else
  run "ruff format --check (${#py_files[@]} files)" uvx ruff@0.15.9 format --check -- ${py_files[@]+"${py_files[@]}"}
fi

# 3. Shell scripts: shellcheck on the changed ones, and on every tracked one under --all.
shell_files=()
if [ "$ALL" != 1 ]; then
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case $path in
      *.sh) shell_files+=("$path") ;;
      *) if head -n 1 -- "$path" | grep -qE '^#!/.*[/ ](ba)?sh( |$)'; then shell_files+=("$path"); fi ;;
    esac
  done <<< "$CHANGED"
fi
if [ "$ALL" = 1 ]; then
  run "shellcheck (all tracked)" bash scripts/shell_lint.sh
elif [ "${#shell_files[@]}" -gt 0 ]; then
  run "shellcheck (${#shell_files[@]} changed)" bash scripts/shell_lint.sh ${shell_files[@]+"${shell_files[@]}"}
else
  skip "shellcheck" "no shell script changed"
fi

# 4. Workflows: actionlint, with the pinned shellcheck for the run: blocks.
workflow_files=()
while IFS= read -r path; do [ -n "$path" ] && workflow_files+=("$path"); done <<< "$(changed_paths '^\.github/workflows/[^/]+\.ya?ml$')"
if [ "$ALL" = 1 ]; then
  run "actionlint (all workflows)" bash scripts/pinned_tool.sh actionlint
elif [ "${#workflow_files[@]}" -gt 0 ]; then
  run "actionlint (${#workflow_files[@]} changed)" bash scripts/pinned_tool.sh actionlint ${workflow_files[@]+"${workflow_files[@]}"}
else
  skip "actionlint" "no workflow changed"
fi

# 5. Rust formatting for the crates rust_checks.sh checks.
rust_any=0
for crate in $RUST_CRATES; do
  if [ "$ALL" = 1 ] || changed_matches "^$crate/"; then
    rust_any=1
    run "rust fmt $crate" rust_fmt "$crate"
  fi
done
if [ "$rust_any" = 0 ]; then skip "rust fmt" "no Rust crate changed"; fi

# 6. Migration names and timestamps against origin/main's history, as CI checks a pull request.
if [ "$ALL" = 1 ] || changed_matches '^backend/migrations/'; then
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    run "migration order" python3 scripts/check_migration_order.py --base origin/main --head HEAD --base-name main
  else
    skip "migration order" "no origin/main to compare with"
  fi
else
  skip "migration order" "no migration changed"
fi

# 7. The UI's generated API types and the type-checked UI files.
if [ "$ALL" = 1 ] || changed_matches '^ui/|^backend/openapi_v2\.py$|^scripts/(gen_ui_types\.py|check_ui_types\.sh)$'; then
  run "ui types (tsc, API types)" bash scripts/check_ui_types.sh
else
  skip "ui types (tsc, API types)" "no UI or API schema changed"
fi

# 7b. Every API path the UI calls is a route the backend serves (the app is imported, not run: seconds).
if [ "$ALL" = 1 ] || changed_matches '^ui/|^backend/.*\.py$|^scripts/(check_ui_routes\.(py|sh)|ui_baseline\.py)$'; then
  run "ui routes (API paths)" bash scripts/check_ui_routes.sh
else
  skip "ui routes (API paths)" "no UI or backend Python changed"
fi

# 8. Python types on the boundary modules in pyrightconfig.json's include list (files or folders).
include_regex=$(python3 -c '
import json, re
print("|".join("^" + re.escape(entry) + "(/|$)" for entry in json.load(open("pyrightconfig.json"))["include"]))')
if [ "$ALL" = 1 ] || changed_matches "$include_regex|^pyrightconfig\.json$|^scripts/check_types\.sh$"; then
  run "boundary types (pyright)" bash scripts/check_types.sh
else
  skip "boundary types (pyright)" "no typed boundary module changed"
fi

echo "check: $passed passed, $failed failed, $skipped skipped in $(elapsed "$T0" "$(now)")"
if [ "$failed" -gt 0 ]; then exit 1; fi
exit 0
