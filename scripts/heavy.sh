#!/bin/bash
# Run a heavy local command (a full test suite, the browser tests, a release validation, a cargo build) in one of a
# few box-wide slots, at low priority, so the bots on one computer don't all run them at once.
#
#   scripts/heavy.sh <command> [args...]
#
# A slot is a lock file in TICO_HEAVY_LOCK_DIR (default ~/.cache/tico-heavy: the bots' sandboxes on one box share
# the home folder, not /tmp, which is each sandbox's own), taken with flock by this script, so it is let go however
# the job ends: exit, kill or timeout. TICO_HEAVY_SLOTS (default 2) is how many run at once. With no slot free, the
# command waits for one and says so once: "waiting for heavy-job slot (n holders)". Start a wrapped job detached
# when a turn must not wait. The command runs under nice -n 10 and keeps its exit code. With no flock (a Mac), it
# runs unlocked with a warning. On hosted CI (CI set), or inside a job that already holds a slot (TICO_HEAVY_HELD),
# it runs as it is.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/heavy.sh <command> [args...]" >&2
  exit 2
fi
if [ -n "${CI:-}" ] || [ -n "${TICO_HEAVY_HELD:-}" ]; then
  exec "$@"
fi
export TICO_HEAVY_HELD=1

if ! command -v flock >/dev/null 2>&1; then
  echo "heavy.sh: flock not found; running without a slot" >&2
  exec nice -n 10 "$@"
fi

slots=${TICO_HEAVY_SLOTS:-2}
case $slots in '' | *[!0-9]* | 0) slots=2 ;; esac
dir=${TICO_HEAVY_LOCK_DIR:-${HOME:-/tmp}/.cache/tico-heavy}
mkdir -p "$dir" 2>/dev/null

# This shell holds the slot (fd 9) and the command does not inherit it, so the slot goes when this shell ends,
# however it ends, even if the command leaves processes behind. A stop is passed on to the command.
run() {
  nice -n 10 "$@" 9>&- &
  child=$!
  trap 'kill -TERM "$child" 2>/dev/null' TERM INT HUP
  while :; do
    wait "$child"
    code=$?
    kill -0 "$child" 2>/dev/null || break
  done
  exit "$code"
}

said=""
while :; do
  holders=0
  i=0
  while [ "$i" -lt "$slots" ]; do
    if { exec 9>>"$dir/slot.$i"; } 2>/dev/null; then
      if flock -n 9; then
        run "$@"
      fi
      exec 9>&-
    fi
    holders=$((holders + 1))
    i=$((i + 1))
  done
  if [ -z "$said" ]; then
    echo "waiting for heavy-job slot ($holders holders)" >&2
    said=1
  fi
  sleep 2
done
