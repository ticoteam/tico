#!/usr/bin/env bash
# Proves a runner container runs the side jobs the hub assigns to it: the Zoom importer starts when
# the owner enables it for this computer (no Zoom key is needed: the importer starting is what shows,
# by reporting missing_credentials), and stops again when it is switched off. Own network and volumes, no
# host port, so it can run beside docker/smoke.sh.
#   TICO_IMAGE=tico TICO_TAG=local TICO_RUNNER_IMAGE=tico-runner docker/side-jobs-smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
server_image="${TICO_IMAGE:-tico}:${TICO_TAG:-local}"
runner_image="${TICO_RUNNER_IMAGE:-tico-runner}:${TICO_TAG:-local}"
net="${TICO_SIDEJOBS_NAME:-tico-sidejobs}" server=sidejobs-server runner=sidejobs-runner
[ "$net" = tico-sidejobs ] || { server="$net-server" runner="$net-runner"; }   # a second copy beside a running one

cleanup() {
  status=$?
  [ "$status" = 0 ] || { docker logs --tail 40 "$server" >&2 || true; docker logs --tail 60 "$runner" >&2 || true; }
  docker rm -f "$runner" "$server" >/dev/null 2>&1 || true
  docker volume rm "$net-data" "$net-runner" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

step() { printf '==> %s\n' "$*"; }
fail() { printf 'side-jobs-smoke: %s\n' "$*" >&2; exit 1; }
retry() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ "$SECONDS" -lt "$end" ] || return 1; sleep 0.5; done; }

# The server image has curl; the API is only reachable from inside the network.
api() {  # curl arguments after the URL are passed through
  docker exec "$server" sh -c 'curl -fsS -H "Authorization: Bearer $(cat /data/local-owner.token)" -H "Content-Type: application/json" "$@"' sh "$@"
}
url=http://127.0.0.1:8765/api/v2
healthy() { docker exec "$server" curl -fsS --max-time 3 http://127.0.0.1:8765/healthz; }
runner_id() { docker exec "$server" python -c 'import sqlite3
print(sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute("SELECT id FROM runners WHERE revoked_at IS NULL").fetchone()[0])'; }
importer_running() { docker exec "$runner" pgrep -f 'python -m runner .* importers$'; }
importer_stopped() { ! importer_running; }
reported() {
  api "$url/meeting-importers" | python3 -c '
import json, sys
row = next(r for r in json.load(sys.stdin)["importers"] if r["source"] == "zoom")
sys.exit(0 if row["error_code"] == "missing_credentials" and row["runner_label"] == "Side jobs runner" else 1)'
}

step "server and runner up"
docker network create "$net" >/dev/null
docker run -d --name "$server" --network "$net" --network-alias server -v "$net-data:/data" \
  -e TICO_COMPANY_NAME="Smoke Test" -e TICO_OWNER_EMAIL=owner@example.com -e TICO_AUTH_PROXY=none \
  "$server_image" server >/dev/null
retry 180 healthy || fail "the server did not become healthy"
code="$(api -X POST -H "Idempotency-Key: side-$RANDOM$RANDOM$SECONDS" -d '{"operator": "owner"}' "$url/enrollments" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
docker run -d --name "$runner" --network "$net" -v "$net-runner:/home/runner" -e TICO_SIDE_JOBS_POLL=1 \
  "$runner_image" join --url http://server:8765 --code "$code" --label "Side jobs runner" >/dev/null
retry 120 runner_id || fail "the runner did not enroll"
id="$(runner_id)"
importer_stopped || fail "the importers job runs with nothing assigned"

step "assigning Zoom to this computer starts the importers job"
api -X POST -H "Idempotency-Key: side-$RANDOM$RANDOM$SECONDS" \
  -d "{\"enabled\": true, \"runner_id\": \"$id\"}" "$url/meeting-importers/zoom" >/dev/null
retry 60 importer_running || fail "the importers job did not start"
retry 90 reported || fail "the importer did not report its status to the hub"

step "switching it off stops the job, and the runner stays up"
api -X POST -H "Idempotency-Key: side-$RANDOM$RANDOM$SECONDS" \
  -d '{"enabled": false, "runner_id": ""}' "$url/meeting-importers/zoom" >/dev/null
retry 60 importer_stopped || fail "the importers job kept running after it was unassigned"
docker exec "$runner" pgrep -f 'python -m runner .* run$' >/dev/null || fail "the runner itself stopped"
docker logs "$runner" 2>&1 | grep -q 'no longer assigned here' || fail "the runner did not log stopping the job"
echo "side-jobs-smoke: ok"
