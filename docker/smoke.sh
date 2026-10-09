#!/usr/bin/env bash
# Starts the server stack in local test mode, joins a runner container with a one-time code, and checks
# what a real install depends on: the server is healthy, the runner comes online, it reconnects after the
# server restarts, and starting the runner again on the same volume does not enroll it twice. It also
# checks the runner image ships no model CLI: the runner installs Codex into its volume once OpenAI is
# an enabled provider, and keeps it across a new container. Last it runs a runner box
# (docker/runner.compose.yaml with its updater sidecar) through an update from one local tag to the
# release its server runs, and through one that does not turn healthy and is rolled back.
#   TICO_IMAGE=tico TICO_TAG=local TICO_RUNNER_IMAGE=tico-runner TICO_UPDATER_IMAGE=tico-updater docker/smoke.sh
# TICO_SMOKE_PROJECT and TICO_SMOKE_PORT (defaults tico-smoke, 8765) name its Docker objects and host port, so a second
# copy can run beside one already running.
set -euo pipefail
cd "$(dirname "$0")/.."
export TICO_IMAGE="${TICO_IMAGE:-tico}" TICO_TAG="${TICO_TAG:-local}"
runner_image="${TICO_RUNNER_IMAGE:-tico-runner}:$TICO_TAG"
updater_image="${TICO_UPDATER_IMAGE:-tico-updater}:${TICO_UPDATER_TAG:-$TICO_TAG}"
box=""      # the runner box's project directory, once the update step makes it
project="${TICO_SMOKE_PROJECT:-tico-smoke}" port="${TICO_SMOKE_PORT:-8765}"
if [ "$project" = tico-smoke ]; then runner=smoke-runner runner_volume=tico-smoke-runner box_project=tico-smoke-runner
else runner="$project-runner" runner_volume="$project-runner-home" box_project="$project-box"; fi
base="http://127.0.0.1:$port"

env_file="$(mktemp)"
cat > "$env_file" <<'EOF'
TICO_COMPANY_NAME=Smoke Test
TICO_OWNER_EMAIL=owner@example.com
TICO_AUTH_PROXY=none
EOF
echo "TICO_PORT=$port" >> "$env_file"
dc() { docker compose -p "$project" --env-file "$env_file" "$@"; }
cleanup() {
  status=$?
  [ "$status" = 0 ] || { dc logs --tail 60 >&2 || true; docker logs --tail 40 "$runner" >&2 || true; }
  [ "$status" = 0 ] || [ -z "$box" ] || { rc logs --tail 40 >&2; dc exec -T server python -c 'import sqlite3
for row in sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute("SELECT r.label, v.* FROM runner_versions v JOIN runners r ON r.id=v.runner_id"): print(tuple(row))' >&2; } || true
  [ -z "$box" ] || { rc down -v >/dev/null 2>&1 || true; rm -r "$box" 2>/dev/null || true; }
  for tag in v9.0.1 v9.0.2 v9.0.3; do
    docker rmi "${TICO_IMAGE:-tico}:$tag" "${TICO_RUNNER_IMAGE:-tico-runner}:$tag" >/dev/null 2>&1 || true
  done
  docker rm -f "$runner" >/dev/null 2>&1 || true
  docker volume rm "$runner_volume" >/dev/null 2>&1 || true
  dc down -v >/dev/null 2>&1 || true
  rm -f "$env_file"
  exit "$status"
}
trap cleanup EXIT

# The runner box: its own project, on the server's network, from a copy of the real compose file.
rc() { env -u TICO_TAG docker compose --project-directory "$box" -f "$box/runner.compose.yaml" "$@"; }
runner_row() {  # column
  dc exec -T server python -c 'import sqlite3,sys
row = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute(
    "SELECT v." + sys.argv[1] + " FROM runner_versions v JOIN runners r ON r.id=v.runner_id "
    "WHERE r.label=? AND r.revoked_at IS NULL", (sys.argv[2],)).fetchone()
print(row[0] if row else "")' "$1" "Smoke box"
}
runner_is() { [ "$(runner_row "$1")" = "$2" ]; }
runs_tag() { rc ps -q runner | xargs docker inspect --format '{{.Config.Image}}' | grep -q ":$1\$"; }
derive() {  # base new-tag extra-Dockerfile-lines...
  local base="$1" tag="$2"; shift 2
  { echo "FROM $base"; for line in "$@"; do echo "$line"; done; } | docker build -q -t "$tag" - >/dev/null
}

step() { printf '==> %s\n' "$*"; }
fail() { printf 'smoke: %s\n' "$*" >&2; exit 1; }
retry() {  # seconds command...
  local end=$((SECONDS + $1)); shift
  until "$@" >/dev/null 2>&1; do [ "$SECONDS" -lt "$end" ] || return 1; sleep 0.5; done
}

api() { curl -fsS -H "Authorization: Bearer $(dc exec -T server cat /data/local-owner.token)" "$@"; }
healthy() { curl -fsS --max-time 3 $base/healthz; }
environment_id() { healthy | python3 -c 'import json,sys; print(json.load(sys.stdin)["environment_id"])'; }
runners() {
  dc exec -T server python -c 'import sqlite3
print(sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute(
    "SELECT count(*) FROM runners WHERE revoked_at IS NULL").fetchone()[0])'
}
last_seen() {
  dc exec -T server python -c 'import sqlite3
print(sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute(
    "SELECT coalesce(max(last_seen), \"\") FROM runners WHERE revoked_at IS NULL").fetchone()[0])'
}
reconnected() { [[ "$(last_seen)" > "$before" ]]; }
online() {
  api $base/api/v2/setup/getting-started | python3 -c '
import json, sys
items = json.load(sys.stdin)["items"]
sys.exit(0 if any(i["id"] == "computer" and i["done"] for i in items) else 1)'
}
harness() {  # id field: what the runner reported for one harness, from the server's point of view
  api $base/api/v2/operations | python3 -c '
import json, sys
machine = json.load(sys.stdin)["machines"][0]
print(machine["readiness"].get("harnesses", {}).get(sys.argv[1], {}).get(sys.argv[2], "unreported"))' "$1" "$2"
}
codex_installed() { [ "$(harness codex installed)" = True ]; }
codex_absent() { [ "$(harness codex installed)" = False ]; }
enable_openai() {
  local revision
  revision="$(api $base/api/v2/providers | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"])')"
  api -X PUT -H 'Content-Type: application/json' -H "Idempotency-Key: smoke-$RANDOM$RANDOM$SECONDS" \
    -d "{\"enabled\": [\"openai\"], \"runtime\": \"\", \"model\": \"\", \"expected_revision\": $revision}" \
    $base/api/v2/providers >/dev/null
}
join_runner() {
  local code
  code="$(api -X POST -H 'Content-Type: application/json' -H "Idempotency-Key: smoke-$RANDOM$RANDOM$SECONDS" -d '{"operator": "owner"}' $base/api/v2/enrollments \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
  docker rm -f "$runner" >/dev/null 2>&1 || true
  # As docker/runner.compose.yaml starts it: root with five capabilities, so bot code runs as another user.
  docker run -d --name "$runner" --restart unless-stopped --network "${project}_default" -v "$runner_volume:/home/runner" \
    --user 0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add KILL --cap-add SETGID --cap-add SETUID \
    --security-opt no-new-privileges:true "$runner_image" join --url http://server:8765 --code "$code" --label "Smoke runner" >/dev/null
}

step "server up"
dc --profile none up -d
retry 180 healthy || fail "the server did not become healthy"
id="$(environment_id)"
[ -n "$id" ] || fail "the server reports no environment id"

step "the tunnel's route can be written for the cloudflared container"
# The server user owns the tico-tunnel volume, as it does in a real install, and cloudflared (another user) can read the file.
dc exec -T -e TICO_DOMAIN=smoke.example.test server tico-entrypoint tunnel-config || fail "the server cannot write the tunnel's route"
dc exec -T server cat /tunnel/cloudflared.yml | grep -q 'hostname: smoke.example.test' || fail "the tunnel's route names the wrong host"
[ "$(dc exec -T server stat -c %a /tunnel/cloudflared.yml)" = 644 ] || fail "the tunnel's route is not readable by cloudflared"

step "a malformed expected AWS account refuses to start, without calling AWS"
out="$(dc exec -T -e TICO_EXPECTED_AWS_ACCOUNT=12345 server tico-entrypoint aws-identity 2>&1)" && fail "a malformed TICO_EXPECTED_AWS_ACCOUNT was accepted"
grep -q '12-digit' <<<"$out" || fail "a malformed TICO_EXPECTED_AWS_ACCOUNT gave no clear error: $out"

step "runner joins with a one-time code"
join_runner
retry 120 online || fail "the runner did not enroll and come online"
[ "$(runners)" = 1 ] || fail "expected one runner"

step "bot code cannot read the runner's registration"
[ "$(docker exec "$runner" stat -c '%U:%a' /home/runner/runner.json)" = "ticorun:600" ] || fail "runner.json is not the runner's alone"
! docker exec -u bot "$runner" cat /home/runner/runner.json >/dev/null 2>&1 || fail "the bot user can read runner.json"
docker exec -u bot "$runner" sh -c 'git config --global user.name && test -w /home/runner/workspace' >/dev/null || fail "the bot user has no working home"
# Legacy secrets belong to the supervisor; only the workspace is shared with bot code.
[ "$(docker exec "$runner" stat -c '%U:%a' /home/runner/workspace/secrets)" = "ticorun:700" ] || fail "legacy secrets are not private to the supervisor"
! docker exec -u bot "$runner" test -r /home/runner/workspace/secrets || fail "the bot user can read legacy secrets"
! docker exec -u bot "$runner" test -w /home/runner/workspace/secrets || fail "the bot user can write legacy secrets"

step "the image holds no model CLI, and the runner reports every harness as not installed"
for cli in codex claude gemini grok pi; do
  ! docker exec "$runner" sh -c "command -v $cli" >/dev/null 2>&1 || fail "$cli is baked into the runner image"
done
retry 60 codex_absent || fail "the runner did not report its harnesses"
docker exec "$runner" test ! -e /home/runner/tools/bin/codex || fail "a harness was installed with no provider enabled"

step "enabling OpenAI makes the runner install Codex into its volume"
enable_openai
retry 300 codex_installed || fail "the runner did not install Codex"
docker exec "$runner" codex --version | grep -Eq '[0-9]+\.[0-9]+\.[0-9]+' || fail "the installed Codex does not answer --version"
[ "$(harness codex managed)" = True ] || fail "the runner does not report Codex as its own install"
docker exec "$runner" test -L /home/runner/tools/bin/codex || fail "Codex is not in the tools directory of the volume"

step "server restart"
before="$(last_seen)"
dc restart server
retry 120 healthy || fail "the server did not come back"
retry 120 reconnected || fail "the runner did not reconnect after the server restarted"

step "the same volume does not enroll twice"
join_runner   # a new code, and a fresh container on the same volume
retry 120 online || fail "the runner is not online after starting again"
[ "$(runners)" = 1 ] || fail "the runner enrolled a second time"
docker exec "$runner" codex --version >/dev/null || fail "the installed Codex did not survive a new container"

step "down and up keeps the data"
docker rm -f "$runner" >/dev/null
dc down
dc --profile none up -d
retry 180 healthy || fail "the server did not come back after down and up"
[ "$(environment_id)" = "$id" ] || fail "the environment id changed"
[ "$(runners)" = 1 ] || fail "the database was not kept"
step "a runner box updates to the server's release"
# Releases 9.0.x exist only as local tags: the same images with the version they report. 9.0.3 never turns healthy.
for v in 9.0.1 9.0.2 9.0.3; do
  derive "$runner_image" "${TICO_RUNNER_IMAGE:-tico-runner}:v$v" "ENV TICO_VERSION=v$v"
  derive "${TICO_IMAGE}:$TICO_TAG" "${TICO_IMAGE}:v$v" "ENV TICO_VERSION=v$v"
done
derive "${TICO_RUNNER_IMAGE:-tico-runner}:v9.0.3" "${TICO_RUNNER_IMAGE:-tico-runner}:v9.0.3" \
  "HEALTHCHECK --interval=2s --timeout=2s --start-period=0s --retries=1 CMD false"
TICO_TAG=v9.0.2 dc --profile none up -d --no-deps server
retry 180 healthy || fail "the server did not come back on v9.0.2"
box="$(mktemp -d "$PWD/.smoke-runner.XXXXXX")"
awk -v name="$box_project" '/^name: tico-runner$/ {print "name: " name; next} {print} /^  (runner|updater):$/ {print "    networks: [default, smoke]"}' \
  docker/runner.compose.yaml > "$box/runner.compose.yaml"
printf 'networks:\n  smoke:\n    external: true\n    name: %s_default\n' "$project" >> "$box/runner.compose.yaml"
code="$(api -X POST -H 'Content-Type: application/json' -H "Idempotency-Key: smoke-$RANDOM$RANDOM$SECONDS" -d '{"operator": "owner"}' $base/api/v2/enrollments \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
cat > "$box/.env" <<EOF
TICO_URL=http://server:8765
TICO_CODE=$code
TICO_RUNNER_LABEL=Smoke box
TICO_RUNNER_IMAGE=${TICO_RUNNER_IMAGE:-tico-runner}
TICO_UPDATER_IMAGE=${updater_image%:*}
TICO_UPDATER_TAG=${updater_image##*:}
TICO_TAG=v9.0.1
TICO_UPDATER_PULL=never
TICO_UPDATER_BUNDLE=never
TICO_HEALTH_SECONDS=60
TICO_UPDATER_POLL=0.5
EOF
rc up -d
retry 300 runner_is release 9.0.2 || fail "the runner did not update to the server's release 9.0.2"
runs_tag v9.0.2 || fail "the runner container is not on the v9.0.2 image"
retry 120 grep -q '^TICO_TAG=v9.0.2$' "$box/.env" || fail "the update did not stay in .env"   # written once it is healthy

step "a release that does not turn healthy is rolled back"
TICO_TAG=v9.0.3 dc --profile none up -d --no-deps server
retry 180 healthy || fail "the server did not come back on v9.0.3"
retry 300 runner_is update_state rolled_back || fail "the failed runner update was not reported as rolled back"
runner_is release 9.0.2 || fail "the runner is not back on 9.0.2"
runs_tag v9.0.2 || fail "the runner container is not back on the v9.0.2 image"
[ -n "$(runner_row update_error)" ] || fail "the rollback carries no reason"
echo "smoke: ok"
