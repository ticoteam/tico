#!/usr/bin/env bash
# The fast local gate: the checks CI's static job runs, on what you changed, one line per check with its time, and a
# non-zero exit if one fails. The checks run in parallel; each runs only when its inputs changed.
#   scripts/check.sh          what changed since the merge-base with origin/main: committed, uncommitted and new files
#   scripts/check.sh --all    every tracked file, every check (what CI's static job runs on every pull request)
# The baselines (ruff, tsc, Biome, the API routes, UI strict, the pytest ignores) are compared with origin/main, as CI
# compares a pull request with its base, so `git fetch origin main` first; the pre-push hook does. `git config
# core.hooksPath .githooks` runs this, and the branch conflict check, on every push. CONTRIBUTING.md lists where this
# differs from CI.
# Checks: lint.sh (ruff baseline and ruff format), test collection (pytest --collect-only), shell_lint.sh (shellcheck),
# actionlint (workflows), migration order,
# check_ui_types.sh (generated API types, tsc), check_ui_strict.py (every UI script TypeScript, strict ratchet),
# check_node_types.py (the .cts/.mts scripts under scripts/ and recorder/, and the Recorder window's .ts, type-check
# strictly), the
# OpenAPI document (backend.openapi_v2 --check), the status value table (build_status_docs.py --check),
# check_ui_lint.sh (Biome), check_ui_routes.sh (API paths),
# check_wire_contract.py (runner wire contract), check_types.sh (basedpyright on the backend against its baseline), and
# cargo fmt for the Rust crates scripts/rust_checks.sh formats (the merge queue runs that one; the static job does not).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
# The .venv scripts/dev_setup.sh makes comes first, so the checks find its Python packages and uv with no venv
# activated (the pre-push hook, a bot's shell); the cargo rustup installs in your home is found too.
if [ -x .venv/bin/python3 ]; then PATH="$PWD/.venv/bin:$PATH"; fi
PATH="$PATH:${CARGO_HOME:-$HOME/.cargo}/bin"
export PATH

ALL=0
case ${1:-} in
  "") ;;
  --all) ALL=1 ;;
  -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
  *) echo "usage: scripts/check.sh [--all]" >&2; exit 2 ;;
esac

now() { python3 -c 'import time; print(time.time())'; }
elapsed() { awk -v s="$1" -v e="$2" 'BEGIN { printf "%.1fs", e - s }'; }
T0=$(now)

# The ratchet base: origin/main, which is what CI compares a pull request with. Local main only when there is no
# origin/main at all.
ratchet=""
for ref in origin/main main; do
  if git rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then ratchet=$ref; break; fi
done
RATCHET_ARGS=()
if [ -n "$ratchet" ]; then RATCHET_ARGS=(--base "$ratchet"); fi
# The merge-base is where this work starts: the changed files are those since it.
base=""
if [ -n "$ratchet" ]; then base=$(git merge-base "$ratchet" HEAD 2>/dev/null) || base=""; fi

# TOUCHED is every path that changed, deleted ones and both sides of a rename included: a deleted module can break a
# check that imports it. CHANGED is the ones that still exist, for the checks that read files.
if [ "$ALL" = 1 ]; then
  listing=$(git ls-files)
  scope="every tracked file"
elif [ -n "$base" ]; then
  listing=$(git diff --no-renames --name-only "$base" --; git ls-files --others --exclude-standard)
  scope="changed since ${base:0:9} (committed, uncommitted and new)"
else
  listing=$(git diff --no-renames --name-only HEAD --; git ls-files --others --exclude-standard)
  scope="uncommitted only (no origin/main to compare with; run git fetch)"
fi
TOUCHED=$(printf '%s\n' "$listing" | sort -u)
CHANGED=$(printf '%s\n' "$TOUCHED" | while IFS= read -r path; do
  if [ -n "$path" ] && [ -f "$path" ]; then printf '%s\n' "$path"; fi
done)
echo "check: $(grep -c . <<< "$CHANGED" || true) file(s), $scope; baselines against ${ratchet:-nothing}"

# Here-strings, not pipes into grep -q: under pipefail an early exit from grep would fail a long list.
touched() { [ "$ALL" = 1 ] || grep -qE "$1" <<< "$TOUCHED"; }

# Each check is started in the background and its line is printed in the order the checks are listed, so the output
# reads the same as a sequential run.
JOB_DIR=$(mktemp -d "${TMPDIR:-/tmp}/tico-check.XXXXXX")
trap 'rm -rf "$JOB_DIR"' EXIT
ENTRIES=()  # "S|name|reason" for a skipped check, "J|n" for job n
UI_TYPES_JOB=""  # the ui types check's job number, once it is started
NAMES=()
PIDS=()

skip() {  # NAME REASON
  ENTRIES+=("S|$1|$2")
}

job() {  # NAME COMMAND...: starts it in the background; its result is printed once it has finished
  local n=${#NAMES[@]}
  NAMES+=("$1")
  shift
  (
    start=$(now)
    "$@" > "$JOB_DIR/$n.out" 2>&1
    status=$?
    printf '%s %s %s\n' "$status" "$start" "$(now)" > "$JOB_DIR/$n.status"
  ) &
  PIDS+=("$!")
  ENTRIES+=("J|$n")
}

# shellcheck disable=SC2329  # job calls it by name
after() {  # JOB COMMAND...: waits for job number JOB to finish, then runs the command (it reads what that job writes)
  local n=$1
  shift
  while [ ! -f "$JOB_DIR/$n.status" ]; do sleep 0.2; done
  "$@"
}

# The Rust crates rust_checks.sh formats, and its toolchain. A crate listed as pending reports but does not fail.
RUST_CRATES=$(sed -n 's/^CRATES="\(.*\)"$/\1/p' scripts/rust_checks.sh)
# shellcheck disable=SC2016  # the sed pattern matches the literal ${RUST_TOOLCHAIN:-...} in rust_checks.sh
RUST_TOOLCHAIN=$(sed -n 's/^TOOLCHAIN="${RUST_TOOLCHAIN:-\(.*\)}"$/\1/p' scripts/rust_checks.sh)
RUST_TOOLCHAIN=${RUST_TOOLCHAIN:-1.92.0}
# shellcheck disable=SC2329  # job calls it by name
rust_fmt() {  # CRATE: cargo fmt --check as rust_checks.sh runs it
  local crate=$1 pending=0 status=0 out
  if ! command -v cargo >/dev/null 2>&1; then echo "cargo is not installed: run scripts/dev_setup.sh --rust"; return 1; fi
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

# 1. Python lint, as lint.sh runs it in CI: the ruff baseline (counts only go down, against origin/main), the pytest
# ignore count, and ruff format over the whole repository. The ruff settings and the pytest config are inputs too.
# lint.sh also runs pr_checks.py smoke-check, which reads the smoke set and the browser runner's core scripts.
if touched '\.pyi?$|^ruff\.toml$|^baselines/ruff/|^pytest\.ini$|^scripts/(lint|lint_baseline|pytest_warning_ignores|baseline_files)\.(sh|py)$|^scripts/queue-smoke\.json$|^scripts/ui-tests\.cts$|^ui/tests/.*\.cts$'; then
  job "lint (ruff baseline, format)" env LINT_BASE_REF="$ratchet" bash scripts/lint.sh
else
  skip "lint (ruff baseline, format)" "no Python, ruff or pytest config changed"
fi

# 1a. No Python file imports a test module (scripts/check_test_imports.py): shared names live in the tests' helpers/.
if touched '\.py$|^scripts/test-imports-allowed\.txt$'; then
  job "test imports (no test module imported)" python3 scripts/check_test_imports.py
else
  skip "test imports (no test module imported)" "no Python file changed"
fi

# 1b. Every test file collects. A test that imports a module that is gone, or a name it lost, fails here instead of in
# CI's test job. Removing a module breaks the tests that import it, not the files that changed, so every test file is
# collected: about 5 s in one process, nothing run. Warnings stay warnings here (-p no:warnings); the test run itself
# makes them errors.
# shellcheck disable=SC2329  # job calls it by name
collect_tests() {
  local out status=0
  out=$(python3 -m pytest --collect-only -qq -n 0 -p no:cacheprovider -p no:warnings \
    backend/tests setup/tests scripts/tests 2>&1) || status=$?
  if [ "$status" -ne 0 ]; then
    grep -E '^_+ ERROR collecting |^E   |^(ERROR|INTERNALERROR) ' <<< "$out" || printf '%s\n' "$out" | tail -n 40
  fi
  return "$status"
}
if touched '\.py$|^pytest\.ini$'; then
  job "tests collect (pytest --collect-only)" collect_tests
else
  skip "tests collect (pytest --collect-only)" "no Python file or pytest config changed"
fi

# 2. Migration names and timestamps against origin/main's history, as CI checks a pull request. Only additions matter.
if touched '^backend/migrations/|^scripts/check_migration_order\.py$'; then
  if [ -n "$ratchet" ]; then
    job "migration order" python3 scripts/check_migration_order.py --base "$ratchet" --head HEAD --base-name main
  else
    skip "migration order" "no origin/main to compare with"
  fi
else
  skip "migration order" "no migration changed"
fi

# 3. Shell scripts: shellcheck on the changed ones, leaving out vendored code as CI does. Every tracked one when the
# checker or its pinned tool changed.
shell_files=()
if [ "$ALL" != 1 ]; then
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case $path in */vendor/* | vendor/*) continue ;; esac
    case $path in
      *.sh) shell_files+=("$path") ;;
      *) if head -n 1 -- "$path" | grep -qE '^#!/.*[/ ](ba)?sh( |$)'; then shell_files+=("$path"); fi ;;
    esac
  done <<< "$CHANGED"
fi
if [ "$ALL" = 1 ] || touched '^scripts/(shell_lint|pinned_tool)\.sh$'; then
  job "shellcheck (all tracked)" bash scripts/shell_lint.sh
elif [ "${#shell_files[@]}" -gt 0 ]; then
  job "shellcheck (${#shell_files[@]} changed)" bash scripts/shell_lint.sh ${shell_files[@]+"${shell_files[@]}"}
else
  skip "shellcheck" "no shell script changed"
fi

# 4. Workflows: actionlint over every workflow, as CI runs it, when anything under .github changed (a workflow can call
# another one, and the lint config lives there).
if [ "$ALL" = 1 ] || touched '^\.github/|^scripts/pinned_tool\.sh$'; then
  job "actionlint (all workflows)" bash scripts/pinned_tool.sh actionlint
else
  skip "actionlint" "no workflow changed"
fi

# 5. Rust formatting for the crates rust_checks.sh checks.
rust_any=0
for crate in $RUST_CRATES; do
  if [ "$ALL" = 1 ] || touched "^$crate/"; then
    rust_any=1
    job "rust fmt $crate" rust_fmt "$crate"
  fi
done
if [ "$rust_any" = 0 ]; then skip "rust fmt" "no Rust crate changed"; fi

# 6. The UI's generated API types (the check imports the backend) and tsc over the UI, as CI runs check_ui_types.sh.
if touched '\.pyi?$|^ui/|^scripts/(gen_ui_types\.py|check_ui_types\.sh|ui_baseline\.py)$'; then
  UI_TYPES_JOB=${#NAMES[@]}
  job "ui types (tsc, API types)" bash scripts/check_ui_types.sh ${RATCHET_ARGS[@]+"${RATCHET_ARGS[@]}"}
else
  skip "ui types (tsc, API types)" "no UI, Python or API schema change"
fi

# 6b. The API spec is generated from the app's routes and never committed: it must generate, and the same bytes twice.
#     The API guide's operation table (docs/api.md) follows the same tags.
if touched '^backend/.*\.pyi?$|^docs/api\.md$|^scripts/build_api_docs\.py$'; then
  job "openapi spec (generates the same bytes twice)" python3 -m backend.openapi_v2 --check
  job "API guide tags (docs/api.md)" python3 scripts/build_api_docs.py --check
else
  skip "openapi spec (generates the same bytes twice)" "no backend Python or API guide changed"
fi

# 6b'. The value table in docs/status-and-state.md is generated from backend/status_values.py.
if touched '^backend/status_values\.py$|^docs/status-and-state\.md$|^scripts/build_status_docs\.py$'; then
  job "status value lists (docs/status-and-state.md)" python3 scripts/build_status_docs.py --check
else
  skip "status value lists (docs/status-and-state.md)" "no status list or its page changed"
fi

# 6c. Every UI script is TypeScript, and every .ts file type-checks strictly (scripts/check_ui_strict.py).
# Its tsc reads ui/types/api.d.ts, which the ui types check writes from the backend's routes (so a Python change can
# break a .ts file), and it starts once that check has finished.
# ui/tests/tsconfig.json extends tsconfig.node.json, and rule 10 reads its options through check_node_types.py.
if touched '\.pyi?$|^ui/|^tsconfig\.node\.json$|^scripts/(check_ui_strict|ui_baseline|gen_ui_types|check_node_types)\.py$'; then
  strict=(python3 scripts/check_ui_strict.py ${RATCHET_ARGS[@]+"${RATCHET_ARGS[@]}"})
  if [ -n "$UI_TYPES_JOB" ]; then strict=(after "$UI_TYPES_JOB" "${strict[@]}"); fi
  job "ui strict (TypeScript, strict ratchet)" "${strict[@]}"
else
  skip "ui strict (TypeScript, strict ratchet)" "no UI file changed"
fi

# 6c'. The TypeScript outside ui/ (scripts/ and recorder/ .cts, .mts and .d.ts files Node runs, and the Recorder
# window's .ts files under recorder/ui) type-checks strictly, with no baseline (scripts/check_node_types.py). Any TypeScript file outside ui/ is an input: it must sit in
# a project. The base config and the npm packages whose types the scripts read are inputs too.
if touched '^([^u]|u[^i]|ui[^/])[^[:space:]]*\.([cm]?ts|tsx)$|(^|/)tsconfig(\.node)?\.json$|^package(-lock)?\.json$|^scripts/check_node_types\.py$'; then
  job "node types (scripts/, recorder/ TypeScript)" python3 scripts/check_node_types.py
else
  skip "node types (scripts/, recorder/ TypeScript)" "no TypeScript outside ui/ changed"
fi

# 6d. A check that picks UI source by a .js suffix alone stops covering each converted .ts file. Text only, a second.
if touched '\.(py|sh|cjs|js|ts)$'; then
  job "ui source suffixes" python3 scripts/check_ui_source_suffixes.py
else
  skip "ui source suffixes" "no script or source changed"
fi

# 7. Biome lint of the UI files, counted against ui/lint-baseline.json, as CI runs it.
if touched '^ui/|^biome\.json$|^package(-lock)?\.json$|^scripts/(check_ui_lint\.sh|ui_baseline\.py)$'; then
  job "ui lint (Biome)" bash scripts/check_ui_lint.sh ${RATCHET_ARGS[@]+"${RATCHET_ARGS[@]}"}
else
  skip "ui lint (Biome)" "no UI or lint config changed"
fi

# 8. Every API path the UI calls is a route the backend serves. The app is imported, not run.
if touched '\.pyi?$|^ui/|^scripts/(check_ui_routes\.(py|sh)|ui_baseline\.py)$'; then
  job "ui routes (API paths)" bash scripts/check_ui_routes.sh ${RATCHET_ARGS[@]+"${RATCHET_ARGS[@]}"}
else
  skip "ui routes (API paths)" "no UI or Python change"
fi

# 9. The runner wire contract. It reads the contract and backend/models.py and takes a fraction of a second, so it runs
# on every change rather than tracking its inputs.
job "runner wire contract" python3 scripts/check_wire_contract.py

# 10. Python types: basedpyright on pyrightconfig.json's include list, against its baseline and the strict-module
# ratchet. Pyright follows imports, so a change to any Python file, or to the packages the venv installs, can change it.
if touched '\.pyi?$|^pyrightconfig\.json$|^scripts/(check_types\.sh|check_pyright_ratchet\.py|pyright_baseline\.py|baseline_files\.py|pyright-legacy-modules\.txt)$|^baselines/pyright/|^backend/requirements[^/]*\.(txt|in)$'; then
  job "types (basedpyright baseline)" env PYRIGHT_BASE_REF="$ratchet" bash scripts/check_types.sh
else
  skip "types (basedpyright baseline)" "no Python or type config changed"
fi

# Wait for each check in order and print its line; a failure shows its output.
passed=0 failed=0 skipped=0
for entry in ${ENTRIES[@]+"${ENTRIES[@]}"}; do
  kind=${entry%%|*}
  rest=${entry#*|}
  if [ "$kind" = S ]; then
    skipped=$((skipped + 1))
    printf '  SKIP  %-38s %s\n' "${rest%%|*}" "${rest#*|}"
    continue
  fi
  n=$rest
  wait "${PIDS[$n]}" 2>/dev/null || true
  status=1 start=0 end=0
  if [ -f "$JOB_DIR/$n.status" ]; then read -r status start end < "$JOB_DIR/$n.status"; fi
  word=PASS
  if [ "$status" -eq 0 ]; then passed=$((passed + 1)); else word=FAIL; failed=$((failed + 1)); fi
  printf '  %-4s  %-38s %7s\n' "$word" "${NAMES[$n]}" "$(elapsed "$start" "$end")"
  if [ "$status" -ne 0 ] && [ -s "$JOB_DIR/$n.out" ]; then sed 's/^/        /' "$JOB_DIR/$n.out"; fi
done

echo "check: $passed passed, $failed failed, $skipped skipped in $(elapsed "$T0" "$(now)")"
if [ "$(git config --get core.hooksPath 2>/dev/null || true)" != .githooks ]; then
  echo "hint: the pre-push hook is off in this clone. Turn it on once: git config core.hooksPath .githooks"
fi
if [ "$failed" -gt 0 ]; then exit 1; fi
exit 0
