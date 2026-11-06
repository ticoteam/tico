#!/usr/bin/env bash
# Early conflict warning: fails when a commit would not merge into origin/main, so a push does not spend CI and
# merge-queue time on a branch GitHub cannot merge. The pre-push hook runs it after scripts/check.sh. It reads only what
# the last fetch brought in (the hook fetches origin main first) and does not touch the working tree.
# Needs git 2.40 or later (merge-tree --write-tree --name-only). An older git, or a missing origin/main, is reported as
# skipped and does not fail.
#   scripts/check_conflicts.sh [--base origin/main] [--head HEAD]
# Exit 0: the head merges cleanly, or the check could not run. Exit 1: the files that conflict are listed, with the fix.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

base=origin/main
head=HEAD
while [ $# -gt 0 ]; do
  case $1 in
    --base) base=${2:?usage: check_conflicts.sh --base REF}; shift 2 ;;
    --head) head=${2:?usage: check_conflicts.sh --head REF}; shift 2 ;;
    -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "usage: scripts/check_conflicts.sh [--base REF] [--head REF]" >&2; exit 2 ;;
  esac
done

if ! git rev-parse --verify --quiet "$base^{commit}" >/dev/null; then
  echo "conflict check skipped: $base is not in this clone (git fetch origin main)"
  exit 0
fi
if ! git rev-parse --verify --quiet "$head^{commit}" >/dev/null; then
  echo "conflict check: $head is not a commit" >&2
  exit 2
fi

status=0
out=$(git merge-tree --write-tree --name-only --no-messages "$base" "$head" 2>&1) || status=$?
case $status in
  0) exit 0 ;;
  1)
    # The first line is the merge's tree; the rest are the conflicting paths, one per line.
    files=$(printf '%s\n' "$out" | tail -n +2 | sed '/^$/d')
    if [ -z "$files" ]; then
      echo "conflict check skipped: git could not merge $head with $base: $out"
      exit 0
    fi
    count=$(printf '%s\n' "$files" | wc -l | tr -d ' ')
    echo "conflict: $(git rev-parse --short "$head") conflicts with $base in $count file(s):" >&2
    printf '%s\n' "$files" | sed 's/^/  /' >&2
    echo "fix: git rebase $base, resolve the files above, git rebase --continue, then push again (git push --no-verify skips this)" >&2
    exit 1 ;;
  *)
    echo "conflict check skipped: git merge-tree failed (git 2.40 or later is needed): $out"
    exit 0 ;;
esac
