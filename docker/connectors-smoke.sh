#!/usr/bin/env bash
# Proves the mail and calendar `connectors` job runs in a runner container: it stays off without the Google
# service-account key, starts when the key appears in the runner's secrets folder (building its venv into the
# volume), syncs a mailbox and refreshes a calendar against a fake Google (docker/fake-google.py, the key's
# token_uri and TICO_GOOGLE_API_ENDPOINT point at it), reports to the hub, and stops when the key is removed.
# No real Google account is involved. Own network and volumes, no host port. Needs PyPI once, for the venv.
#   TICO_IMAGE=tico TICO_TAG=local TICO_RUNNER_IMAGE=tico-runner docker/connectors-smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
server_image="${TICO_IMAGE:-tico}:${TICO_TAG:-local}"
runner_image="${TICO_RUNNER_IMAGE:-tico-runner}:${TICO_TAG:-local}"
net=tico-connectors server=connectors-server runner=connectors-runner
key="$(mktemp)"

cleanup() {
  status=$?
  [ "$status" = 0 ] || { docker logs --tail 40 "$server" >&2 || true; docker logs --tail 60 "$runner" >&2 || true; }
  docker rm -f "$runner" "$server" >/dev/null 2>&1 || true
  docker volume rm "$net-data" "$net-runner" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  rm -f "$key"
  exit "$status"
}
trap cleanup EXIT

step() { printf '==> %s\n' "$*"; }
fail() { printf 'connectors-smoke: %s\n' "$*" >&2; exit 1; }
retry() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ "$SECONDS" -lt "$end" ] || return 1; sleep 2; done; }

api() { docker exec "$server" sh -c 'curl -fsS -H "Authorization: Bearer $(cat /data/local-owner.token)" -H "Content-Type: application/json" "$@"' sh "$@"; }
url=http://127.0.0.1:8765/api/v2
healthy() { docker exec "$server" curl -fsS --max-time 3 http://127.0.0.1:8765/healthz; }
runner_id() { docker exec "$server" python -c 'import sqlite3
print(sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute("SELECT id FROM runners WHERE revoked_at IS NULL").fetchone()[0])'; }
hub_scalar() { docker exec "$server" python -c 'import sqlite3, sys
row = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute(sys.argv[1]).fetchone()
sys.exit(0 if row and row[0] else 1)' "$1"; }
job_running() { docker exec "$runner" pgrep -f 'python -m runner .* connectors$'; }
job_stopped() { ! job_running; }
google_saw() { docker exec "$runner" grep -q "$1" /tmp/google.log; }
calendar_published() { hub_scalar "SELECT 1 FROM connector_snapshots WHERE kind='calendar'"; }
mail_published() { hub_scalar "SELECT message_count FROM mail_mailboxes WHERE address='owner@example.com'"; }

step "server and runner up, with a fake Google inside the runner"
docker network create "$net" >/dev/null
docker run -d --name "$server" --network "$net" --network-alias server -v "$net-data:/data" \
  -e TICO_COMPANY_NAME="Smoke Test" -e TICO_OWNER_EMAIL=owner@example.com -e TICO_AUTH_PROXY=none \
  "$server_image" server >/dev/null
retry 180 healthy || fail "the server did not become healthy"
code="$(api -X POST -H "Idempotency-Key: conn-$RANDOM$RANDOM$SECONDS" -d '{"operator": "owner"}' "$url/enrollments" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
docker run -d --name "$runner" --network "$net" -v "$net-runner:/home/runner" -e TICO_SIDE_JOBS_POLL=5 \
  -e TICO_MAIL_SYNC_SECONDS=5 -e TICO_GOOGLE_API_ENDPOINT=http://127.0.0.1:9099 \
  "$runner_image" join --url http://server:8765 --code "$code" --label "Connectors runner" >/dev/null
retry 120 runner_id || fail "the runner did not enroll"
docker cp docker/fake-google.py "$runner:/tmp/fake-google.py"
docker exec -d "$runner" python /tmp/fake-google.py 9099 /tmp/google.log
job_stopped || fail "the connectors job runs with no Google key"
sleep 12
docker logs "$runner" 2>&1 | grep -q 'started connectors' && fail "the connectors job started without a key"

step "the key appearing in the runner's secrets folder starts the job"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null | python3 -c '
import json, sys
print(json.dumps({"type": "service_account", "project_id": "fixture", "private_key_id": "fixture0",
    "private_key": sys.stdin.read(), "client_email": "fixture@fixture.iam.gserviceaccount.com",
    "client_id": "1", "token_uri": "http://127.0.0.1:9099/token"}))' > "$key"
docker exec -i "$runner" sh -c 'umask 077; tee /home/runner/workspace/secrets/google-sa.json >/dev/null' < "$key"
retry 60 job_running || fail "the connectors job did not start"
retry 300 google_saw '/gmail/' || fail "the job never asked Gmail (see the log for the venv build)"
retry 60 google_saw 'events' || fail "the job never asked Calendar"
retry 60 calendar_published || fail "the calendar snapshot did not reach the hub"
retry 60 mail_published || fail "the mailbox did not reach the hub"
docker exec "$runner" test -x /home/runner/tools/mail-venv/bin/python || fail "the venv is not in the volume"

step "removing the key stops the job, and the runner stays up"
docker exec "$runner" rm /home/runner/workspace/secrets/google-sa.json
retry 60 job_stopped || fail "the connectors job kept running after the key was removed"
docker exec "$runner" pgrep -f '(python -m runner|tico-runner) .* run$' >/dev/null || fail "the runner itself stopped"
echo "connectors-smoke: ok"
