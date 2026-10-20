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
# when a turn must not wait. The command runs under nice -n 10, in its own process group, and keeps its exit code.
# A TERM, INT or HUP stops that whole group (TERM, then KILL after 10 s) before the slot goes, so a suite's workers
# never outlive their slot. With no flock (a Mac), or a lock folder flock cannot lock, it runs unlocked with a
# warning. On hosted CI (CI set), or inside a job that already holds a slot (TICO_HEAVY_HELD), it runs as it is.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/heavy.sh <command> [args...]" >&2
  exit 2
fi
if [ -n "${CI:-}" ] || [ -n "${TICO_HEAVY_HELD:-}" ]; then
  exec "$@"
fi
export TICO_HEAVY_HELD=1

# The budget never stops work: without a working lock the command runs anyway, niced, and the log says why.
unlocked() {
  echo "heavy.sh: $1; running without a slot" >&2
  shift
  exec nice -n 10 "$@"
}

command -v flock >/dev/null 2>&1 || unlocked "flock not found" "$@"

slots=${TICO_HEAVY_SLOTS:-2}
case $slots in '' | *[!0-9]* | 0) slots=2 ;; esac
dir=${TICO_HEAVY_LOCK_DIR:-${HOME:-/tmp}/.cache/tico-heavy}
mkdir -p "$dir" 2>/dev/null || unlocked "cannot create the lock folder $dir (set TICO_HEAVY_LOCK_DIR)" "$@"

# This shell holds the slot (fd 9) and the command does not inherit it, so the slot goes when this shell ends,
# however it ends. Job control puts the command in its own process group, which a stop ends as a whole: the
# coordinator and the workers it started, never the caller's group.
run() {
  set -m
  nice -n 10 "$@" 9>&- &
  child=$!
  set +m
  trap stop TERM INT HUP
  wait "$child"
  exit $?
}

stop() {
  trap '' TERM INT HUP
  kill -TERM -- "-$child" 2>/dev/null
  n=0
  while kill -0 -- "-$child" 2>/dev/null; do
    n=$((n + 1))
    [ "$n" -eq 100 ] && kill -KILL -- "-$child" 2>/dev/null
    sleep 0.1
  done
  wait "$child"
  exit $?
}

said=""
while :; do
  holders=0
  i=0
  while [ "$i" -lt "$slots" ]; do
    { exec 9>>"$dir/slot.$i"; } 2>/dev/null || unlocked "cannot open $dir/slot.$i (set TICO_HEAVY_LOCK_DIR)" "$@"
    flock -n 9
    case $? in
      0) run "$@" ;;
      1) holders=$((holders + 1)) ;;   # another job holds this slot
      *)
        exec 9>&-
        unlocked "flock cannot lock $dir/slot.$i (set TICO_HEAVY_LOCK_DIR to a local folder)" "$@"
        ;;
    esac
    exec 9>&-
    i=$((i + 1))
  done
  if [ -z "$said" ]; then
    echo "waiting for heavy-job slot ($holders holders)" >&2
    said=1
  fi
  sleep 2
done
