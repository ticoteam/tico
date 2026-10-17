# Sourced by docker/smoke.sh. `compose_up COMPOSE [FLAGS] up ARGS...` runs that command, where COMPOSE runs docker
# compose for one project. Docker sometimes fails to create a fresh network at once: "Network tico-rc-smoke_edge
# Error" after 1 s failed the v0.3.53 candidate's Release validation on 2026-10-17, and the next run passed. Only that
# failure is retried, twice, after a `COMPOSE down` (which keeps volumes). A range clash ("Pool overlaps") is a real
# misconfiguration and, like every other failure, fails at once.
compose_up() {
  local compose="$1" attempt output
  for attempt in 1 2 3; do
    if output="$("$@" 2>&1)"; then
      [ -z "$output" ] || printf '%s\n' "$output"
      return 0
    fi
    printf '%s\n' "$output" >&2
    if [ "$attempt" = 3 ] || grep -qi 'overlaps' <<<"$output" \
        || ! grep -Eqi '(^|[[:space:]])network [^[:space:]]+[[:space:]]+error|failed to create network' <<<"$output"; then
      return 1
    fi
    printf 'compose_up: Docker could not create a network (attempt %s of 3); retrying\n' "$attempt" >&2
    "$compose" down >/dev/null 2>&1 || true
    sleep "$((attempt * ${TICO_COMPOSE_RETRY_SECONDS:-2}))"
  done
}
