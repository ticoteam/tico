#!/usr/bin/env bash
# Turn a local employee folder into a private GitHub repo (without the GitHub App; with it, BotOps uses `tico bot repo-create`).
# Usage: scripts/publish-employee.sh [--owner <org>] [--workspace <dir>] <slug> [<slug>...]
#   --owner      GitHub organization or user that owns the repositories (or TICO_GITHUB_OWNER)
#   --workspace  where the bot-<slug> (older: emp-<slug>) folders are (default: the folder holding this checkout)
# Idempotent: skips git init / repo create when already done.
set -euo pipefail
OWNER="${TICO_GITHUB_OWNER:-}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SLUGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --owner) OWNER="${2:-}"; shift 2 ;;
    --owner=*) OWNER="${1#*=}"; shift ;;
    --workspace) ROOT="$(cd "${2:-}" && pwd)"; shift 2 ;;
    --workspace=*) ROOT="$(cd "${1#*=}" && pwd)"; shift ;;
    *) SLUGS+=("$1"); shift ;;
  esac
done
[ "${#SLUGS[@]}" -gt 0 ] || { sed -n '2,6p' "$0" >&2; exit 2; }
[ -n "$OWNER" ] || { echo "no GitHub owner: pass --owner <org> or set TICO_GITHUB_OWNER" >&2; exit 2; }
for slug in "${SLUGS[@]}"; do
  d="$ROOT/bot-$slug"; [ -d "$d" ] || d="$ROOT/emp-$slug"     # new bots are bot-<slug>; older ones keep emp-<slug>
  name="$(basename "$d")"
  [ -d "$d" ] || { echo "no folder $d"; continue; }
  cd "$d"
  [ -d .git ] || git init -q -b main
  git add -A
  git diff --cached --quiet || git commit -q -m "Initialize employee $slug from the hub template"
  if ! git remote get-url origin >/dev/null 2>&1; then
    if gh repo view "$OWNER/$name" >/dev/null 2>&1; then
      git remote add origin "https://github.com/$OWNER/$name.git"; git push -q -u origin main
    else
      gh repo create "$OWNER/$name" --private --source . --remote origin --push >/dev/null
    fi
  else
    git push -q
  fi
  echo "ok  $name"
done
