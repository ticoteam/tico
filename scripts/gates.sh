#!/usr/bin/env bash
# The shared verification checks: every scripts/gates/*.py, then scripts/rules/*.{yml,yaml} and their fixtures.
#   scripts/gates.sh             compare with origin/main (main when there is no origin/main)
#   scripts/gates.sh --base REF  resolve REF to a commit SHA and pass --base <sha> to every gate
# One timed line per gate, in check.sh's style; a failure shows its output and fails the run. No gates or rules prints
# "gates: none". scripts/gates/README.md describes the script and rule contracts.
# ast-grep is pinned through uvx, like ruff. CI fetches it and fails if unavailable. Locally only the cached tool runs;
# if it is absent, rules are skipped with a warning. Install it with: uvx --from ast-grep-cli==0.51.1 ast-grep --version
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
AST_GREP=(uvx --from ast-grep-cli==0.51.1 ast-grep)
if [ -z "${CI:-}" ]; then AST_GREP=(uvx --offline --from ast-grep-cli==0.51.1 ast-grep); fi

ref=""
case $# in
  0) ;;
  1) if [ "$1" = -h ] || [ "$1" = --help ]; then
       awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0
     fi
     echo "usage: scripts/gates.sh [--base REF]" >&2; exit 2 ;;
  2) if [ "$1" = --base ] && [ -n "$2" ]; then ref=$2; else
       echo "usage: scripts/gates.sh [--base REF]" >&2; exit 2
     fi ;;
  *) echo "usage: scripts/gates.sh [--base REF]" >&2; exit 2 ;;
esac

shopt -s nullglob
gates=(scripts/gates/*.py)
rules=(scripts/rules/*.yml scripts/rules/*.yaml)
if [ "${#gates[@]}" -eq 0 ] && [ "${#rules[@]}" -eq 0 ]; then
  echo "gates: none"
  exit 0
fi

now() { python3 -c 'import time; print(time.time())'; }
elapsed() { awk -v s="$1" -v e="$2" 'BEGIN { printf "%.1fs", e - s }'; }
T0=$(now)
passed=0 failed=0 skipped=0
JOB_DIR=$(mktemp -d "${TMPDIR:-/tmp}/tico-gates.XXXXXX") || exit 2
OUT="$JOB_DIR/output"
CONFIG=""
trap 'rm -rf "$JOB_DIR"; if [ -n "$CONFIG" ]; then rm -f "$CONFIG"; fi' EXIT

run() {  # NAME COMMAND...
  local name=$1 start status=0 word=PASS
  shift
  start=$(now)
  "$@" > "$OUT" 2>&1 || status=$?
  if [ "$status" -eq 0 ]; then passed=$((passed + 1)); else word=FAIL; failed=$((failed + 1)); fi
  printf '  %-4s  %-38s %7s\n' "$word" "$name" "$(elapsed "$start" "$(now)")"
  if [ "$status" -ne 0 ] && [ -s "$OUT" ]; then sed 's/^/        /' "$OUT"; fi
}

if [ "${#gates[@]}" -gt 0 ]; then
  if [ -z "$ref" ]; then
    for candidate in origin/main main; do
      if git rev-parse --verify --quiet "$candidate^{commit}" >/dev/null; then ref=$candidate; break; fi
    done
  fi
  base=$(git rev-parse --verify --quiet "${ref:-origin/main}^{commit}") || {
    echo "gates: no base commit (${ref:-origin/main}); run git fetch origin main or pass --base" >&2
    exit 2
  }
  export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
  for gate in "${gates[@]}"; do
    run "$(basename "$gate" .py)" python3 "$gate" --base "$base"
  done
fi

# shellcheck disable=SC2329  # run calls it by name
fixtures() {  # RULE: stdin ignores path filters, so fixtures exercise the rule itself, wherever it applies in the repo.
  local rule=$1 id fixture status result=0
  id=$(basename "${rule%.*}")
  local passes=(scripts/rules/fixtures/"$id"/pass.*)
  local fails=(scripts/rules/fixtures/"$id"/fail.*)
  if [ "${#passes[@]}" -eq 0 ] || [ "${#fails[@]}" -eq 0 ]; then
    echo "$rule needs fixtures/$id/pass.* and fixtures/$id/fail.*"
    return 1
  fi
  for fixture in "${passes[@]}" "${fails[@]}"; do
    status=0
    "${AST_GREP[@]}" scan --rule "$rule" --stdin --error --json=compact < "$fixture" > "$JOB_DIR/matches.json" || status=$?
    # Diagnostic matches and command errors can both exit non-zero. Require valid match JSON as well as the status.
    if [ "$status" -gt 1 ] || ! python3 - "$fixture" "$JOB_DIR/matches.json" "$status" <<'PY'
import json
from pathlib import Path
import sys

fixture, output, status = sys.argv[1:]
try:
    matches = json.loads(Path(output).read_text())
except (ValueError, OSError):
    sys.exit(1)
expect_match = Path(fixture).name.startswith("fail.")
sys.exit(0 if isinstance(matches, list) and bool(matches) == expect_match and int(status) == int(expect_match) else 1)
PY
    then
      echo "fixture failed: $fixture (exit $status)"
      cat "$JOB_DIR/matches.json"
      result=1
    fi
  done
  return "$result"
}

if [ "${#rules[@]}" -gt 0 ]; then
  if ! "${AST_GREP[@]}" --version > "$OUT" 2>&1; then
    if [ -n "${CI:-}" ]; then
      echo "gates: ERROR: pinned ast-grep unavailable; CI requires all rules" >&2
      cat "$OUT" >&2
      failed=$((failed + 1))
    else
      echo "gates: WARNING: SKIPPING RULES: pinned ast-grep unavailable locally; cache it with uvx --from ast-grep-cli==0.51.1 ast-grep --version"
      skipped=$((skipped + 1))
    fi
  else
    for rule in "${rules[@]}"; do run "fixtures $(basename "$rule")" fixtures "$rule"; done
    # A config at the repository root makes each rule's files/ignores globs relative to that root. Copy only rules,
    # since YAML fixtures must not be discovered as rules. The config and copies disappear on exit.
    CONFIG=$(mktemp "$PWD/.tico-gates.XXXXXX") || exit 2
    mkdir "$JOB_DIR/rules"
    cp "${rules[@]}" "$JOB_DIR/rules/"
    python3 -c 'import json, sys; print(json.dumps({"ruleDirs": [sys.argv[1]]}))' "$JOB_DIR/rules" > "$CONFIG"
    run "ast-grep scan (${#rules[@]} rules)" "${AST_GREP[@]}" scan --config "$CONFIG" --error --threads 1 \
      --no-ignore hidden --globs '!.git/**' --globs '!scripts/rules/fixtures/**' .
  fi
fi

echo "gates: $passed passed, $failed failed, $skipped skipped in $(elapsed "$T0" "$(now)")"
if [ "$failed" -gt 0 ]; then exit 1; fi
exit 0
