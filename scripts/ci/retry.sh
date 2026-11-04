#!/usr/bin/env bash
# Retry a download that can fail on a transient error. GitHub's artifact storage answers HTTP 503 ("Egress is over the
# account limit") while it is throttled, and one 503 used to fail a whole release.
#
#   bash scripts/ci/retry.sh [--clear DIR]... [--permanent REGEX] [--max-attempts N] -- COMMAND [ARG]...
#
# Up to 8 attempts, waiting 30s, 60s, 90s, then 120s between them (about 11 minutes at most). Each --clear DIR is
# emptied before every attempt, since gh run download refuses to overwrite files a failed attempt left behind. Each
# failed attempt prints a ::warning:: with its number and error; the last one prints an ::error:: and exits with its status.
# --permanent stops immediately when stderr matches the case-insensitive extended regex, preserving the exit status.
# --max-attempts bounds optional downloads (3 attempts wait only 30s + 60s). Command stderr stays visible on every attempt.
set -uo pipefail

max_attempts=8
permanent=''
clear_dirs=()
usage() {
  echo "::error::retry.sh: $* (usage: retry.sh [--clear DIR]... [--permanent REGEX] [--max-attempts N] -- COMMAND...)" >&2
  exit 2
}
while [ $# -gt 0 ]; do
  case "$1" in
    --clear|--permanent|--max-attempts)
      [ $# -ge 2 ] || usage "missing value for $1"
      case "$1" in
        --clear) clear_dirs+=("$2") ;;
        --permanent) permanent="$2" ;;
        --max-attempts) max_attempts="$2" ;;
      esac
      shift 2 ;;
    --) shift; break ;;
    *) usage "unexpected argument '$1'" ;;
  esac
done
[ $# -gt 0 ] || usage 'no command given'
[[ "$max_attempts" =~ ^[1-9][0-9]*$ ]] || usage '--max-attempts must be a positive integer'
if [ -n "$permanent" ]; then
  grep -Ei -- "$permanent" /dev/null >/dev/null
  [ $? -ne 2 ] || usage 'invalid --permanent regex'
fi

err="$(mktemp)"
trap 'rm -f "$err"' EXIT
for attempt in $(seq 1 "$max_attempts"); do
  for dir in ${clear_dirs[@]+"${clear_dirs[@]}"}; do rm -rf "$dir"; done
  "$@" 2>"$err"
  status=$?
  cat "$err" >&2
  if [ "$status" -eq 0 ]; then exit 0; fi
  detail="$(tail -n 3 "$err" | tr '\n' ' ')"
  if [ -n "$permanent" ] && grep -Eiq -- "$permanent" "$err"; then
    echo "::error::attempt $attempt/$max_attempts failed permanently (exit $status): $detail"
    exit "$status"
  fi
  if [ "$attempt" -eq "$max_attempts" ]; then
    echo "::error::attempt $attempt/$max_attempts failed (exit $status): $detail"
    exit "$status"
  fi
  wait_s=$((attempt * 30))
  [ "$wait_s" -gt 120 ] && wait_s=120
  echo "::warning::attempt $attempt/$max_attempts failed (exit $status): $detail; retrying in ${wait_s}s"
  sleep "$wait_s"
done
