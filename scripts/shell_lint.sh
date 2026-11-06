#!/usr/bin/env bash
# Runs the pinned shellcheck (scripts/pinned_tool.sh) on the repository's shell scripts: every tracked *.sh and every other
# tracked file whose first line is an sh or bash shebang. Vendored code is left out. With paths, it checks the shell
# scripts among them (scripts/check.sh passes the changed files).
#   scripts/shell_lint.sh          all of them (CI)
#   scripts/shell_lint.sh PATH...  the shell scripts among PATH...
set -euo pipefail
cd "$(dirname "$0")/.."

is_shell() {  # PATH: a file, and a shell script by name or by its first line
  [ -f "$1" ] || return 1
  case $1 in *.sh) return 0 ;; esac
  head -n 1 -- "$1" | grep -qE '^#!/.*[/ ](ba)?sh( |$)'
}

candidates=()
if [ "$#" -gt 0 ]; then
  candidates=("$@")
else
  while IFS= read -r path; do candidates+=("$path"); done < <({
    git ls-files -- '*.sh' ':(exclude)**/vendor/**'
    git grep -n -I -E '^#!/.*[/ ](ba)?sh( |$)' -- . ':(exclude)**/vendor/**' | awk -F: '$2 == 1 { print $1 }'
  } | sort -u)
fi

shells=()
for path in ${candidates[@]+"${candidates[@]}"}; do
  if is_shell "$path"; then shells+=("$path"); fi
done
if [ "${#shells[@]}" -eq 0 ]; then
  echo "shellcheck: no shell scripts to check"
  exit 0
fi
bash scripts/pinned_tool.sh shellcheck "${shells[@]}"
echo "shellcheck: ok (${#shells[@]} files)"
