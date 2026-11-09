#!/usr/bin/env bash
# Move a branch across the one-time ruff format commit (named in .git-blame-ignore-revs) with few conflicts.
#
#   scripts/format_rebase.sh [FORMAT_COMMIT]
#
# Run it in the branch's own worktree, on the branch, with no uncommitted changes. FORMAT_COMMIT defaults to the
# commit named in .git-blame-ignore-revs. The script:
#   1. rebases the branch onto the format commit's parent (a conflict stops here, as any rebase does);
#   2. runs `ruff format` on the .py files the branch changes and commits that as one commit;
#   3. rebases onto the format commit with `-X theirs`: where the formatting and the branch's own lines clash,
#      the branch's lines win, and the commit from step 2 then formats them;
#   4. runs `ruff format` on those files once more and commits anything left over.
# It never pushes. When it finishes, review `git log` and push with `git push --force-with-lease`.
# A conflict it cannot settle stops it: resolve it, `git rebase --continue`, then run the script again. A branch that
# already contains the format commit only gets step 4. Files excluded from formatting (ruff.toml [format]) are skipped.
set -euo pipefail

RUFF=(uvx ruff@0.15.9)
FORMAT_MESSAGE="Format this branch's Python files with ruff format"

die() { echo "format_rebase: $*" >&2; exit 2; }

target=${1:-}
if [ -z "$target" ]; then
  target=$(awk '!/^#/ && NF { print $1; exit }' .git-blame-ignore-revs 2>/dev/null || true)
fi
[ -n "$target" ] || die "name the format commit (.git-blame-ignore-revs lists none)"
FORMAT=$(git rev-parse --verify --quiet "$target^{commit}") || die "$target is not a commit in this repository"
PARENT=$(git rev-parse --verify --quiet "$FORMAT^") || die "$target has no parent to rebase onto"
BRANCH=$(git symbolic-ref --quiet --short HEAD) || die "check out the branch first (HEAD is detached)"
[ "$BRANCH" != main ] || die "refusing to run on main"
if ! git diff --quiet || ! git diff --cached --quiet; then
  die "commit or discard your changes first"
fi

# Formats the .py files this branch changes against $1 (a commit), then commits them with message $2 if any changed.
format_changed() {
  local base=$1 message=$2 files=() file
  while IFS= read -r -d '' file; do files+=("$file"); done < <(git diff -z --name-only --diff-filter=ACMR "$base" -- '*.py')
  if [ "${#files[@]}" -eq 0 ]; then
    echo "format_rebase: no Python files to format"
    return 0
  fi
  "${RUFF[@]}" format --no-cache --force-exclude -- "${files[@]}"
  git add -- "${files[@]}"
  if git diff --cached --quiet; then
    echo "format_rebase: ${#files[@]} Python file(s) already formatted"
    return 0
  fi
  git commit -q -m "$message"
  echo "format_rebase: committed formatting for ${#files[@]} Python file(s): $message"
}

if git merge-base --is-ancestor "$FORMAT" HEAD; then
  echo "format_rebase: $BRANCH already contains $(git rev-parse --short "$FORMAT"); checking its formatting"
  format_changed "$FORMAT" "Format Python files changed on this branch with ruff format"
  echo "format_rebase: done. Review git log, then push with git push --force-with-lease"
  exit 0
fi

echo "format_rebase: step 1, rebase $BRANCH onto $(git rev-parse --short "$PARENT")"
if ! git rebase "$PARENT"; then
  die "conflict while rebasing onto $PARENT: resolve it, run git rebase --continue, then run this script again"
fi

echo "format_rebase: step 2, format the Python files this branch changes"
format_changed "$PARENT" "$FORMAT_MESSAGE"

echo "format_rebase: step 3, rebase onto $(git rev-parse --short "$FORMAT") with -X theirs"
if ! git rebase -X theirs "$FORMAT"; then
  die "conflict that -X theirs could not settle: resolve it, run git rebase --continue, then run this script again"
fi

echo "format_rebase: step 4, format again after the rebase"
format_changed "$FORMAT" "Format Python files changed on this branch with ruff format"
echo "format_rebase: done. Review git log, then push with git push --force-with-lease"
