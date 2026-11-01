#!/usr/bin/env bash
# The install-to-rollback journey, on demand: a maintainer runs this before a deploy, on a laptop with Docker.
# It is not part of CI. It takes about ten minutes and prints a pass/fail table.
#
#   scripts/journey-test.sh                     # build this checkout as the candidate (v9.9.9), start from the newest release
#   scripts/journey-test.sh --tag v0.3.0        # a published candidate release (its images and bundle must exist)
#   scripts/journey-test.sh --tag v0.3.0 --previous v0.2.5
#   scripts/journey-test.sh --keep              # leave the stack running afterwards
#   scripts/journey-test.sh --release           # the release gate's run (python scripts/release_checks.py --release)
#
# Each run's containers, volume and compose project end in its own run id, and cleanup removes only those. A run
# killed before its cleanup leaves them behind: `docker ps -a --filter name=journey` lists its containers and
# `docker volume ls --filter name=journey-runner` its volume.
#
# --release keeps the stages nothing else covers and drops the ones docker/smoke.sh already proves (server restart)
# or that run on demand (replica restore, runner restart, backup). With TICO_JOURNEY_IMAGES_READY=<file> the candidate
# images are built by the caller: the upgrade waits for that file to say "ok" instead of building them.
# TICO_JOURNEY_CANDIDATE=vX.Y.Z names a local candidate built (or pulled and tagged) as that version instead of v9.9.9.
#
# What it does, in one throwaway install directory and Docker project (tico-journey-<run id>), auth none:
#   1 install     the PREVIOUS release: its checksummed bundle unpacked, a .env, `docker compose up -d`
#   2 seed        data shaped like a real team's: older releases' rows (scripts/make_upgrade_fixture.py), a computer
#                 enrolled with a one-time code (the previous runner, started as docker/runner.compose.yaml starts it),
#                 a team HUB_ variable granted to BotOps, and one BotOps turn answered by a fake `codex`
#                 (scripts/journey-fake-codex.py) that opens a session and adds a task worktree with `hub`
#   3 restart     the server restarts; data, environment id and the runner survive
#   4 upgrade     "Update now" (POST /api/v2/system/update) moves the server to the candidate; the previous updater
#                 applies the candidate's bundle and then replaces itself with the candidate's updater
#   5 old runner  the previous release's runner, still running, against the candidate server: a turn in the same
#                 session and a second worktree (v0.3.11, v0.3.36 and v0.3.37 broke exactly there)
#   6 new runner  the computer moves to the candidate runner on the same volume: its turn still has the HUB_ variable
#                 (v0.2.31 dropped them), continues the same session, the seeded worktree is still there and the
#                 older releases' rows are intact; with the harness's saved sessions gone, the next turn still answers
#                 on a fresh thread (v0.3.32 failed every turn after a move)
#   7 rollback    an update to an image that migrates the database (with the real entrypoint's Litestream replicating
#                 it) and never turns healthy is rolled back, and the pre-update snapshot brings the database back
#   8 replica     after that rollback the data volume is wiped and the server restores from its Litestream replica: the
#                 database opens, the pre-migration rows are there and the migration is not
#   9 restart     the runner is restarted with `docker restart` after a turn handed its secrets folder to the bot user
#                 and must answer a second turn (v0.2.6 and v0.2.7 crash-looped on exactly that restart)
#  10 second hop  an update to a third build (the candidate re-released as v9.9.11): the updater the first hop made
#                 must still pull its image and refresh the bundle, then replace itself again (v0.2.9's replaced
#                 updater kept the helper's settings and stopped refreshing)
#  11 backup      docker/backup-test.sh on the candidate: MinIO and a file replica, wipe the volume, restore
#
# scripts/install.sh is Linux-and-root only (it installs Docker and writes /opt/tico), so this does what it does
# after the preflight, the same way: download the bundle and SHA256SUMS, check the checksum, unpack, write .env.
# Server and runner images are used from the local Docker cache (`docker pull` for a published tag; the server
# service pulls only what is missing). The updater replaces itself only with an image it can pull, so updater images
# go through a throwaway registry on 127.0.0.1, and the bundles the updater downloads come from a throwaway release
# server on the install's network (the candidate's is built from this checkout for the local build).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

CAND="" PREV="" KEEP=0 LOCAL=1 FAST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) CAND="$2"; LOCAL=0; shift 2 ;;
    --previous) PREV="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --release) FAST=1; shift ;;
    -h|--help) sed -n 2,50p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ "$LOCAL" = 1 ] && CAND="${TICO_JOURNEY_CANDIDATE:-v9.9.9}"
BROKEN=v9.9.10   # newer than the candidate: the updater refuses a downgrade
THIRD=v9.9.11    # the second updater hop's target: the candidate again, as another release
RELEASES="${TICO_JOURNEY_RELEASES_URL:-https://github.com/ticoteam/tico/releases}"
SERVER_IMAGE=ghcr.io/ticoteam/tico RUNNER_IMAGE=ghcr.io/ticoteam/tico-runner UPDATER_IMAGE=ghcr.io/ticoteam/tico-updater
# Every Docker name carries this run's id, so cleanup removes only what this run made, never a same-named
# container or volume someone already had.
RUN="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
PROJECT=tico-journey-$RUN NET=tico-journey-${RUN}_default RUNNER=journey-runner-$RUN RUNNER_VOLUME=journey-runner-$RUN
REGISTRY_IMAGE=public.ecr.aws/docker/library/registry:2.8.3
REGISTRY=127.0.0.1:${TICO_JOURNEY_REGISTRY_PORT:-5052} REGISTRY_NAME=journey-registry-$RUN RELEASE_SERVER=journey-releases-$RUN
INSTALL_UPDATER="$REGISTRY/ticoteam/tico-updater"   # the install's updater image: what it pulls when it replaces itself
SWAP_HELPER=tico-updater-swap-$PROJECT              # docker/updater.py names the helper after the compose project
BUCKET=acme-journey-bucket                          # the team's own HUB_BUCKET value, fake

for tool in docker curl python3; do command -v "$tool" >/dev/null || { echo "journey: $tool is required" >&2; exit 2; }; done
docker info >/dev/null 2>&1 || { echo "journey: the Docker daemon is not running" >&2; exit 2; }
if [ -z "$PREV" ]; then
  PREV="$(git tag -l 'v[0-9]*' --sort=-v:refname | grep -v -- - | grep -vx "$CAND" | head -n 1)"
  [ -n "$PREV" ] || { echo "journey: no previous release tag found; pass --previous" >&2; exit 2; }
fi

WORK="$(mktemp -d)"
DIR="$WORK/install"
mkdir -p "$DIR" "$WORK/releases/download"
RESULTS=() STATE_ID="" FAILED=0 SEED_THREAD="" SEED_TASK=""

dc() { docker compose -p "$PROJECT" --project-directory "$DIR" "$@"; }
say() { printf '  %s\n' "$*"; }
retry() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ "$SECONDS" -lt "$end" ] || return 1; sleep 0.5; done; }
api() {  # curl arguments; the owner token never leaves the server container
  dc exec -T server sh -c 'curl -sS --fail-with-body -H "Authorization: Bearer $(cat /data/local-owner.token)" -H "Content-Type: application/json" "$@"' sh "$@"
}
post() {
  local path=$1 body=$2 response
  if response="$(api --fail-with-body -X POST -H "Idempotency-Key: j-$RANDOM$RANDOM$SECONDS" -d "$body" "http://127.0.0.1:8765/api/v2/$path")"; then
    printf '%s\n' "$response"
  else
    printf 'journey: POST %s failed: %s\n' "$path" "$response" >&2
    return 1
  fi
}
get() { api "http://127.0.0.1:8765/api/v2/$1"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
healthy() { dc exec -T server curl -fsS --max-time 3 http://127.0.0.1:8765/healthz; }
environment_id() { healthy | json 'd["environment_id"]'; }
service_tag() { dc ps -q "$1" | xargs docker inspect --format '{{.Config.Image}}' | sed 's/.*://'; }
server_tag() { service_tag server; }
sql() {  # a read-only query in the server's database; prints the first column of each row
  dc exec -T server python -c 'import sqlite3,sys
for row in sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True).execute(sys.argv[1]): print(row[0])' "$1"
}

cleanup() {
  [ "$KEEP" = 1 ] && { echo "journey: kept the stack (docker compose -p $PROJECT --project-directory $DIR ...), $RUNNER and volume $RUNNER_VOLUME"; return; }
  docker rm -f "$RUNNER" "$SWAP_HELPER" "$RELEASE_SERVER" "$REGISTRY_NAME" >/dev/null 2>&1
  dc down -v >/dev/null 2>&1
  docker volume rm "$RUNNER_VOLUME" >/dev/null 2>&1
  # The updater writes the install's bundle as the image's user; hand it back so the directory can go.
  docker run --rm --user 0 --entrypoint chown -v "$WORK:/w" "$SERVER_IMAGE:$PREV" -R "$(id -u):$(id -g)" /w >/dev/null 2>&1
  for v in $([ "$LOCAL" = 1 ] && echo "$CAND") "$BROKEN" "$THIRD"; do docker rmi "$SERVER_IMAGE:$v" "$RUNNER_IMAGE:$v" "$UPDATER_IMAGE:$v" >/dev/null 2>&1; done
  for v in "$PREV" "$CAND" "$THIRD"; do docker rmi "$INSTALL_UPDATER:$v" >/dev/null 2>&1; done
  rm -rf "$WORK"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------------------------- helpers

bucket_sha() { printf '%s' "$BUCKET" | sha256_of /dev/stdin | cut -c1-12; }   # what the fake harness reports of it
sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }

start_runner() {  # image tag, then the join arguments: as docker/runner.compose.yaml starts it (root, five capabilities)
  local tag=$1; shift
  docker rm -f "$RUNNER" >/dev/null 2>&1
  docker run -d --name "$RUNNER" --restart unless-stopped --network "$NET" -e OPENAI_API_KEY=journey-fake -v "$RUNNER_VOLUME:/home/runner" \
    --user 0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add KILL --cap-add SETGID --cap-add SETUID \
    --security-opt no-new-privileges:true "$RUNNER_IMAGE:$tag" join --url http://server:8765 "$@" --label "Journey runner" >/dev/null || return 1
  # The fake harness: a `codex` on the runner's PATH that speaks the app-server protocol. The runner finds it, the
  # OPENAI_API_KEY given above satisfies its sign-in check, and no model or network is involved. The mode is set
  # before the copy: root here has no FOWNER, so it cannot chmod a file that keeps the host user as owner.
  install -m 755 scripts/journey-fake-codex.py "$WORK/codex" && docker cp "$WORK/codex" "$RUNNER:/usr/local/bin/codex" &&
    docker exec "$RUNNER" sh -c 'mkdir -p /home/runner/journey-threads && chmod 777 /home/runner/journey-threads'
}
runner_online() { get getting-started | json 'sys.exit(0 if any(i["id"]=="computer" and i["done"] for i in d["items"]) else 1)'; }
runner_id() { get operations | json 'd["machines"][0]["id"]'; }
last_seen() { sql "SELECT coalesce(max(last_seen_at),'') FROM runners WHERE revoked_at IS NULL"; }
seen_since() { [[ "$(last_seen)" > "$1" ]]; }
botops_ready() { get operations | json 'sys.exit(0 if d["machines"][0]["readiness"]["bots"]["botops"]["ready"] else 1)'; }

reply_to() {  # conversation nonce: the fake harness's answer to that ask, into $WORK/reply
  get "conversations/$1/messages" | python3 -c '
import json, sys
nonce, out = sys.argv[1], sys.argv[2]
found = [m["body"] for m in json.load(sys.stdin)["messages"] if m["body"].startswith("journey-reply: " + nonce + " ")]
if not found:
    sys.exit(1)
open(out, "w").write(found[-1])' "$2" "$WORK/reply"
}
ask() {  # name, then what to ask (env=NAME, worktree=TASK): one turn with BotOps in the owner's chat with it
  local nonce="$1-$RANDOM$RANDOM" cid
  shift
  cid="$(post chat/botops "{\"text\": \"journey-ask $nonce $*\"}" | json 'd["conversation"]["id"]')" || return 1
  echo "$cid" > "$WORK/conversation"
  rm -f "$WORK/reply"
  retry 120 reply_to "$cid" "$nonce" || { say "no reply from the fake harness to $nonce"; return 1; }
}
fact() { tr ' ' '\n' < "$WORK/reply" | sed -n "s/^$1=//p" | head -n 1; }   # one fact of the last reply
replied() { get "conversations/$(cat "$WORK/conversation")/messages" | json 'sys.exit(0 if any(m["body"].startswith("journey-reply:") for m in d["messages"]) else 1)'; }
conversation_intact() { replied && [ "$(environment_id)" = "$STATE_ID" ]; }
fixture_intact() {  # the rows scripts/make_upgrade_fixture.py added, as many as it added
  [ "$(sql "SELECT (SELECT count(*) FROM bots WHERE slug IN ('coo','cmo','seo')) || ' ' || (SELECT count(*) FROM tasks WHERE id IN ('t1','t2','t3')) || ' ' || (SELECT count(*) FROM messages WHERE id IN ('m1','m2'))")" = "3 3 2" ]
}
worktree_state() {  # task: the state of its worktree link and whether its checkout is on the computer
  local path
  get "tasks/$1/links" | json '[l for l in (d.get("links", d) if isinstance(d, dict) else d) if l["kind"] == "worktree"][0]["state"]'
  path="$(get "tasks/$1/links" | json '[l for l in (d.get("links", d) if isinstance(d, dict) else d) if l["kind"] == "worktree"][0]["path"]')"
  docker exec "$RUNNER" test -e "/home/runner/workspace/$path/.git" && echo on-disk || echo not-on-disk
}
new_task() {  # title: a task BotOps is working on, so a worktree may start for it; prints its id
  local task version
  task="$(post tasks "{\"owner\": \"bot:botops\", \"title\": \"$1\", \"body\": \"A worktree for the journey.\", \"next_run\": true}")" || return 1
  version="$(printf '%s' "$task" | json 'd.get("task", d)["version"]')"
  task="$(printf '%s' "$task" | json 'd.get("task", d)["id"]')"
  post "tasks/$task" "{\"version\": $version, \"status\": \"doing\", \"quiet\": true}" >/dev/null || return 1
  echo "$task"
}

# The updater's side of an update: images it pulls and bundles it downloads, all local.
release_bundle() {  # version: its bundle and SHA256SUMS where the release server serves them
  local into="$WORK/releases/download/$1"
  [ -s "$into/SHA256SUMS" ] && return 0
  mkdir -p "$into"
  if [ "$1" = "$CAND" ] && [ "$LOCAL" = 0 ]; then
    curl -fsSL -o "$into/SHA256SUMS" "$RELEASES/download/$1/SHA256SUMS" && curl -fsSL -o "$into/tico-bundle-$1.tar.gz" "$RELEASES/download/$1/tico-bundle-$1.tar.gz" || return 1
  else
    python3 scripts/build_install_bundle.py --version "$1" --output "$into" >/dev/null || return 1
  fi
  chmod -R a+rX "$WORK/releases"
}
publish_updater() {  # source image, version: the updater image the install pulls for that version, in the registry only
  docker tag "$1" "$INSTALL_UPDATER:$2" && docker push -q "$INSTALL_UPDATER:$2" >/dev/null && docker rmi "$INSTALL_UPDATER:$2" >/dev/null
}
bundle_version() { tr -d '\n' < "$DIR/.bundle-version"; }
updater_env() { dc ps -q updater | xargs docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}'; }
swapped() {  # tag: the helper finished and the updater container runs that tag
  [ "$(docker inspect --format '{{.State.Status}} {{.State.ExitCode}}' "$SWAP_HELPER" 2>/dev/null)" = "exited 0" ] && [ "$(service_tag updater)" = "$1" ]
}
updater_replaced() {  # tag: after a healthy update, the updater replaced itself with that tag and still refreshes
  retry 120 swapped "$1" || { say "the updater did not replace itself with $1: $(get system/update | json 'd["message"]'); helper: $(docker logs --tail 3 "$SWAP_HELPER" 2>&1 | tr '\n' ' ')"; return 1; }
  # The helper runs with pulls and bundles off; the updater it starts must not keep them, or the next update neither
  # pulls its image nor refreshes the compose files.
  if updater_env | grep -E '^TICO_UPDATER_(PULL|BUNDLE)=never$' >/dev/null; then
    say "the replaced updater kept the helper's settings: $(updater_env | grep -E '^TICO_UPDATER_(PULL|BUNDLE)=' | tr '\n' ' ')"; return 1
  fi
}
run_update() {  # version -> waits for a final state; prints it
  local state end=$((SECONDS + 400))
  post system/update "{\"version\": \"$1\"}" >/dev/null || { echo request_failed; return; }
  while [ "$SECONDS" -lt "$end" ]; do
    state="$(get system/update 2>/dev/null | json 'd["state"]' 2>/dev/null || true)"
    case "$state" in healthy|rolled_back|failed) echo "$state"; return ;; esac
    sleep 1
  done
  echo timeout
}

# ---------------------------------------------------------------------------------------------- steps

step_install() {
  local bundle="tico-bundle-$PREV.tar.gz" want got
  curl -fsSL -o "$WORK/SHA256SUMS" "$RELEASES/download/$PREV/SHA256SUMS" && curl -fsSL -o "$WORK/$bundle" "$RELEASES/download/$PREV/$bundle" \
    || { say "could not download the $PREV bundle"; return 1; }
  want="$(awk -v f="$bundle" '{n=$2; sub(/^\*/,"",n)} n==f {print $1; exit}' "$WORK/SHA256SUMS")"
  got="$(sha256_of "$WORK/$bundle")"
  [ -n "$want" ] && [ "$got" = "$want" ] || { say "checksum mismatch for $bundle"; return 1; }
  tar -xzf "$WORK/$bundle" -C "$DIR" && printf '%s\n' "$PREV" > "$DIR/.bundle-version"
  cat > "$DIR/.env" <<EOF
TICO_COMPANY_NAME=Journey
TICO_OWNER_EMAIL=owner@example.com
TICO_AUTH_PROXY=none
COMPOSE_PROFILES=updater
TICO_UPDATER_URL=http://updater:8080
TICO_UPDATER_IMAGE=$INSTALL_UPDATER
TICO_TAG=$PREV
EOF
  docker/edge-env.sh "${TICO_JOURNEY_EDGE:-172.30.252}" >> "$DIR/.env" || return 1
  # The edge range (TICO_JOURNEY_EDGE) is the journey's own, so docker/smoke.sh and a real install can be up beside it.
  # Kept out of the bundle's files, so the updater never replaces it: a private project, no host port, quick health
  # limit, the local release server, and server images from the local cache (a local build is in no registry).
  cat > "$DIR/compose.override.yaml" <<EOF
name: $PROJECT
services:
  server:
    ports: !override []
    pull_policy: missing
  updater:
    environment:
      TICO_HEALTH_SECONDS: "$([ "$FAST" = 1 ] && echo 30 || echo 60)"
      TICO_UPDATER_POLL: "0.5"
      TICO_RELEASES_URL: http://$RELEASE_SERVER:8000
EOF
  say "pulling $PREV images"
  local image pids=() ok=1
  for image in "$SERVER_IMAGE" "$UPDATER_IMAGE" "$RUNNER_IMAGE"; do   # a published tag does not change: a cached copy is enough
    { docker image inspect "$image:$PREV" >/dev/null 2>&1 || docker pull -q "$image:$PREV" >/dev/null; } & pids+=($!)
  done
  { docker image inspect "$REGISTRY_IMAGE" >/dev/null 2>&1 || docker pull -q "$REGISTRY_IMAGE" >/dev/null; } & pids+=($!)
  for pid in "${pids[@]}"; do wait "$pid" || ok=0; done
  [ "$ok" = 1 ] || { say "the $PREV images are not available"; return 1; }
  docker rm -f "$REGISTRY_NAME" "$RELEASE_SERVER" >/dev/null 2>&1
  docker run -d --name "$REGISTRY_NAME" -p "$REGISTRY:5000" "$REGISTRY_IMAGE" >/dev/null || { say "the registry did not start"; return 1; }
  docker tag "$UPDATER_IMAGE:$PREV" "$INSTALL_UPDATER:$PREV" || return 1
  dc up -d >/dev/null 2>&1 || { say "docker compose up failed"; return 1; }
  docker run -d --name "$RELEASE_SERVER" --network "$NET" -v "$WORK/releases:/srv:ro" --entrypoint python "$SERVER_IMAGE:$PREV" \
    -m http.server -d /srv 8000 >/dev/null || { say "the release server did not start"; return 1; }
  retry 600 healthy || { say "the server did not become healthy"; return 1; }   # a loaded Mac can take minutes
  STATE_ID="$(environment_id)"
  [ -n "$STATE_ID" ] && [ "$(server_tag)" = "$PREV" ]
}

step_seed() {
  local operator code rid revision assignment assigned generation credential
  # Rows the way older releases wrote them, added to the running install's database.
  dc exec -T server python - --into /data/hub.sqlite < scripts/make_upgrade_fixture.py || { say "could not add the fixture rows"; return 1; }
  operator="$(get me | json 'd["actor"].split(":",1)[1]')"
  code="$(post enrollments "{\"operator\": \"$operator\"}" | json 'd["code"]')" || return 1
  start_runner "$PREV" --code "$code" || return 1
  retry 120 runner_online || { say "the runner did not come online"; return 1; }
  revision="$(get providers | json 'd["revision"]')"
  api -X PUT -H "Idempotency-Key: j-$RANDOM$SECONDS" -d "{\"enabled\": [\"openai\"], \"runtime\": \"\", \"model\": \"\", \"expected_revision\": $revision}" \
    http://127.0.0.1:8765/api/v2/providers >/dev/null || { say "could not enable OpenAI"; return 1; }
  rid="$(runner_id)"
  # Enrollment may already place the starter bot. Preserve that assignment.
  assignment="$(get bots/botops)" || return 1
  assigned="$(printf '%s' "$assignment" | json '(d.get("assignment") or {}).get("runner_id", "")')"
  generation="$(printf '%s' "$assignment" | json '(d.get("assignment") or {}).get("generation", 0)')"
  if [ "$assigned" != "$rid" ]; then
    post bots/botops/assignment "{\"runner_id\": \"$rid\", \"expected_generation\": $generation}" >/dev/null || return 1
  fi
  # The runner builds starter repositories asynchronously after enrollment.
  retry 180 botops_ready || { say "BotOps repository never became ready on the runner"; return 1; }
  revision="$(get bots | json '[b for b in d if b["slug"] == "botops"][0]["revision"]')" || return 1
  post bots/botops/definition "{\"status\": \"active\", \"expected_revision\": $revision}" >/dev/null || return 1
  retry 180 botops_ready || { say "BotOps never became ready on the runner"; return 1; }
  # A team's own HUB_ variable, stored as a credential and granted to the bot.
  credential="$(post credentials "{\"name\": \"Acme bucket\", \"kind\": \"api_key\", \"env\": \"HUB_BUCKET\", \"secret\": \"$BUCKET\"}" | json 'd.get("credential", d)["id"]')" || return 1
  post "credentials/$credential/grants" '{"subject": "bot:botops"}' >/dev/null || return 1
  SEED_TASK="$(new_task "Acme launch checklist")" || return 1
  ask seed env=HUB_BUCKET worktree="$SEED_TASK" || return 1
  SEED_THREAD="$(fact thread)"
  [ "$(fact HUB_BUCKET)" = "$(bucket_sha)" ] || { say "the seed turn did not get HUB_BUCKET: $(cat "$WORK/reply")"; return 1; }
  [ "$(fact worktree)" = ok ] || { say "the seed turn could not add a worktree: $(cat "$WORK/reply")"; return 1; }
  [ "$(worktree_state "$SEED_TASK" | tr '\n' ' ')" = "present on-disk " ] || { say "the seeded worktree is $(worktree_state "$SEED_TASK" | tr '\n' ' ')"; return 1; }
}

step_restart() {
  local before
  before="$(last_seen)"
  dc restart server >/dev/null 2>&1 || return 1
  retry 120 healthy || { say "the server did not come back"; return 1; }
  conversation_intact || { say "the conversation or environment id changed"; return 1; }
  retry 120 seen_since "$before" || { say "the runner did not reconnect"; return 1; }
}

step_upgrade() {
  local state
  if [ -n "${TICO_JOURNEY_IMAGES_READY:-}" ]; then
    say "waiting for the candidate images"
    until [ -s "$TICO_JOURNEY_IMAGES_READY" ]; do sleep 0.5; done
    [ "$(cat "$TICO_JOURNEY_IMAGES_READY")" = ok ] || { say "the candidate images did not build"; return 1; }
  elif [ "$LOCAL" = 1 ]; then
    say "building the candidate images from this checkout"
    for target in "server:$SERVER_IMAGE" "runner:$RUNNER_IMAGE" "updater:$UPDATER_IMAGE"; do
      docker build -q --target "${target%%:*}" --build-arg "TICO_VERSION=$CAND" \
        --build-arg "TICO_COMMIT=$(git rev-parse HEAD)" \
        --build-arg "TICO_REPOSITORY=${TICO_JOURNEY_REPOSITORY:-ticoteam/tico}" \
        -t "${target#*:}:$CAND" . >/dev/null || { say "build failed"; return 1; }
    done
  else
    say "pulling the $CAND images"
    for image in "$SERVER_IMAGE" "$UPDATER_IMAGE" "$RUNNER_IMAGE"; do docker pull -q "$image:$CAND" >/dev/null || { say "$image:$CAND is not published"; return 1; }; done
  fi
  release_bundle "$CAND" || { say "no $CAND bundle"; return 1; }
  publish_updater "$UPDATER_IMAGE:$CAND" "$CAND" || { say "could not put the $CAND updater in the registry"; return 1; }
  state="$(run_update "${CAND#v}")"
  [ "$state" = healthy ] || { say "the update ended in state $state: $(get system/update | json 'd["message"]')"; return 1; }
  retry 60 healthy && [ "$(server_tag)" = "$CAND" ] || { say "the server is not on $CAND"; return 1; }
  conversation_intact || { say "data did not survive the update"; return 1; }
  grep -q "^TICO_TAG=$CAND\$" "$DIR/.env" || { say ".env does not record $CAND"; return 1; }
  [ "$(bundle_version)" = "$CAND" ] || { say "the install's bundle is $(bundle_version), not $CAND"; return 1; }
  updater_replaced "$CAND"   # the first hop: the previous updater hands over to the candidate's
}

step_old_runner() {
  local task
  # The update moved the server only; the computer still runs the previous release's runner, and its `hub`.
  [ "$(docker inspect --format '{{.Config.Image}}' "$RUNNER" | sed 's/.*://')" = "$PREV" ] || { say "the runner is not on $PREV"; return 1; }
  task="$(new_task "Acme pricing page")" || return 1
  ask old worktree="$task" || return 1
  [ "$(fact thread)" = "$SEED_THREAD" ] || { say "the $PREV runner did not continue the seeded session: $(cat "$WORK/reply")"; return 1; }
  [ "$(fact worktree)" = ok ] || { say "the $PREV runner could not add a worktree: $(cat "$WORK/reply")"; return 1; }
  [ "$(worktree_state "$task" | tr '\n' ' ')" = "present on-disk " ] || { say "the $PREV runner's new worktree is $(worktree_state "$task" | tr '\n' ' ')"; return 1; }
}

step_new_runner() {
  local before state
  # The computer updates: the candidate runner on the same volume, already enrolled, so no code is needed.
  before="$(last_seen)"
  start_runner "$CAND" --code "" || return 1
  retry 120 seen_since "$before" || { say "the candidate runner did not connect"; return 1; }
  retry 120 botops_ready || { say "BotOps is not ready on the candidate runner"; return 1; }
  ask new env=HUB_BUCKET || return 1
  [ "$(fact HUB_BUCKET)" = "$(bucket_sha)" ] || { say "the team's HUB_BUCKET did not reach the turn after the upgrade: $(cat "$WORK/reply")"; return 1; }
  [ "$(fact thread)" = "$SEED_THREAD" ] || { say "the session from before the upgrade did not continue: $(cat "$WORK/reply")"; return 1; }
  state="$(worktree_state "$SEED_TASK" | tr '\n' ' ')"
  [ "$state" = "present on-disk " ] || { say "the worktree from before the upgrade is $state"; return 1; }
  fixture_intact || { say "the older releases' rows did not survive the upgrade"; return 1; }
  # A computer that lost the harness's own sessions (a move, a new HOME, cleaned files) still answers, on a fresh
  # thread, instead of failing every turn on the one it remembers.
  docker exec "$RUNNER" find /home/runner/journey-threads -maxdepth 1 -type f -name 'journey-*' -delete || return 1
  ask moved || { say "the bot stopped answering once its saved session was gone"; return 1; }
  [ "$(fact resumed)" = no ] && [ "$(fact thread)" != "$SEED_THREAD" ] || { say "the turn after the session was lost: $(cat "$WORK/reply")"; return 1; }
}

step_rollback() {
  local state snapshot
  # A "release" that migrates the database and then never serves: the case the snapshot exists for. The updater
  # doing it is the candidate's own, which replaced itself after the upgrade.
  # Through the server's real entrypoint (`prepare`: seed, identity, Litestream's configuration), so Litestream follows the
  # database and replicates the migration before the rollback puts the snapshot back. It never serves.
  mkdir -p "$WORK/broken"
  cat > "$WORK/broken/tico-broken" <<'EOF'
#!/bin/sh
set -e
tico-entrypoint prepare
python -c 'import sqlite3; c = sqlite3.connect("/data/hub.sqlite"); c.execute("CREATE TABLE journey_migrated(x)"); c.commit()'
exec litestream replicate -config /tmp/litestream.yml -exec "sleep 3600"
EOF
  printf 'FROM %s:%s\nCOPY --chmod=755 tico-broken /usr/local/bin/tico-broken\nENTRYPOINT ["tico-broken"]\n' "$SERVER_IMAGE" "$CAND" > "$WORK/broken/Dockerfile"
  docker build -q -t "$SERVER_IMAGE:$BROKEN" "$WORK/broken" >/dev/null || return 1
  release_bundle "$BROKEN" || return 1
  state="$(run_update "${BROKEN#v}")"
  [ "$state" = rolled_back ] || { say "expected rolled_back, got $state"; return 1; }
  snapshot="$(get system/update | json 'd.get("snapshot","") + " " + str(d.get("restored"))')"
  [ "${snapshot##* }" = True ] || { say "the snapshot was not restored ($snapshot)"; return 1; }
  retry 90 healthy && [ "$(server_tag)" = "$CAND" ] || { say "the server is not back on $CAND"; return 1; }
  [ -z "$(sql "SELECT name FROM sqlite_master WHERE name='journey_migrated'")" ] || { say "the migration is still in the database"; return 1; }
  conversation_intact || { say "data did not survive the rollback"; return 1; }
  dc exec -T server sh -c 'ls /data/snapshots/pre-update-*.sqlite >/dev/null' || { say "no snapshot file in /data/snapshots"; return 1; }
  [ "$(bundle_version)" = "$CAND" ] || { say "the rollback left the $(bundle_version) bundle"; return 1; }
}

step_replica() {
  local data="${PROJECT}_tico-data"
  # The rolled-back server has been replicating for a while; then the volume is lost and the same image restores from
  # the replica. The migrated copy Litestream saw before the rollback must not be the newest thing in it.
  sleep 20
  dc stop server >/dev/null 2>&1 || return 1
  docker run --rm --user 0 --entrypoint sh -v "$data:/data" "$SERVER_IMAGE:$CAND" -c 'find /data -mindepth 1 -delete' || return 1
  dc up -d server >/dev/null 2>&1 || return 1
  retry 180 healthy || { say "the server did not start from the replica"; return 1; }
  [ "$(sql 'PRAGMA integrity_check')" = ok ] || { say "the restored database fails its integrity check"; return 1; }
  [ -z "$(sql "SELECT name FROM sqlite_master WHERE name='journey_migrated'")" ] || { say "the restore brought back the migrated database"; return 1; }
  conversation_intact || { say "the pre-migration rows are missing after the restore"; return 1; }
}

step_runner_restart() {
  local before
  # The candidate runner has taken a turn, which handed its secrets folder to the bot user.
  before="$(last_seen)"
  docker restart "$RUNNER" >/dev/null || return 1
  retry 120 seen_since "$before" || { say "the runner did not come back after docker restart: $(docker logs --tail 5 "$RUNNER" 2>&1 | tr '\n' ' ')"; return 1; }
  retry 120 botops_ready || { say "BotOps is not ready after the restart"; return 1; }
  ask restarted || { say "no reply to the turn after the restart"; return 1; }
}

step_second_hop() {
  local state
  # A third build: the candidate, released again under another version, so it reports that release. Its updater
  # image exists only in the registry, so the update proves the updater the first hop made still pulls.
  printf 'FROM %s:%s\nENV TICO_VERSION=%s TICO_RELEASE=%s\n' "$SERVER_IMAGE" "$CAND" "$THIRD" "$THIRD" \
    | docker build -q -t "$SERVER_IMAGE:$THIRD" - >/dev/null || return 1
  # Its updater gets a layer of its own: a release's updater is always a new image, and the swap tells the new
  # updater from the old by image id.
  printf 'FROM %s:%s\nLABEL org.opencontainers.image.version=%s\n' "$UPDATER_IMAGE" "$CAND" "$THIRD" \
    | docker build -q -t "$UPDATER_IMAGE:$THIRD" - >/dev/null || return 1
  publish_updater "$UPDATER_IMAGE:$THIRD" "$THIRD" || { say "could not put the $THIRD updater in the registry"; return 1; }
  release_bundle "$THIRD" || return 1
  state="$(run_update "${THIRD#v}")"
  [ "$state" = healthy ] || { say "the update to $THIRD ended in state $state: $(get system/update | json 'd["message"]')"; return 1; }
  retry 60 healthy && [ "$(server_tag)" = "$THIRD" ] || { say "the server is not on $THIRD"; return 1; }
  [ "$(bundle_version)" = "$THIRD" ] || { say "the replaced updater did not refresh the bundle: it is $(bundle_version), not $THIRD"; return 1; }
  conversation_intact || { say "data did not survive the second update"; return 1; }
  updater_replaced "$THIRD"
}

step_backup() {
  [ -x docker/backup-test.sh ] || return 1
  # The upgrade may have failed before building; do not misreport a missing image as MinIO failure.
  if ! docker image inspect "$SERVER_IMAGE:$CAND" >/dev/null 2>&1; then
    if [ "$LOCAL" = 1 ]; then
      docker build -q --target server --build-arg "TICO_VERSION=$CAND" \
        --build-arg "TICO_COMMIT=$(git rev-parse HEAD)" \
        --build-arg "TICO_REPOSITORY=${TICO_JOURNEY_REPOSITORY:-ticoteam/tico}" \
        -t "$SERVER_IMAGE:$CAND" . >/dev/null || return 1
    else
      docker pull -q "$SERVER_IMAGE:$CAND" >/dev/null || return 1
    fi
  fi
  # Its own project, network and MinIO.
  TICO_IMAGE="$SERVER_IMAGE" TICO_TAG="$CAND" docker/backup-test.sh > "$WORK/backup.log" 2>&1 || { tail -n 15 "$WORK/backup.log" | sed 's/^/    /'; return 1; }
}

# ---------------------------------------------------------------------------------------------- run

run_step() {  # label function; after a failure the rest are skipped
  local label=$1 fn=$2 start=$SECONDS
  printf '==> %s\n' "$label"
  if [ "$FAILED" = 1 ]; then RESULTS+=("$label|SKIP|0"); return; fi
  if "$fn"; then RESULTS+=("$label|PASS|$((SECONDS - start))")
  else RESULTS+=("$label|FAIL|$((SECONDS - start))"); FAILED=1; failure_logs; fi
}
failure_logs() {  # what the containers said last: the release gate's log is all there is once the stack is gone
  local name
  for name in server updater; do
    printf '  --- %s\n' "$name"; dc logs --no-log-prefix --tail 15 "$name" 2>&1 | sed 's/^/    /'
  done
  printf '  --- runner\n'; docker logs --tail 15 "$RUNNER" 2>&1 | sed 's/^/    /'
  docker exec "$RUNNER" cat /tmp/journey-fake-codex.log 2>/dev/null | tail -n 20 | sed 's/^/    fake codex: /'
}

printf 'journey: previous %s -> candidate %s (%s), Docker names end in -%s\n' "$PREV" "$CAND" "$([ "$LOCAL" = 1 ] && echo "built from this checkout" || echo "published release")" "$RUN"
run_step "install $PREV (bundle, auth none)" step_install
run_step "seed: fixture rows, runner, HUB_ variable, session, worktree" step_seed
[ "$FAST" = 1 ] || run_step "restart the server" step_restart
run_step "upgrade $PREV -> $CAND (updater replaces itself)" step_upgrade
run_step "$PREV runner on the $CAND server: turn and worktree" step_old_runner
run_step "$CAND runner keeps the seeded data" step_new_runner
run_step "roll back a bad update (snapshot restored)" step_rollback
if [ "$FAST" = 0 ]; then
  run_step "restore from the replica after that rollback" step_replica
  run_step "runner restarts after a turn took its secrets" step_runner_restart
fi
run_step "second updater hop $CAND -> $THIRD" step_second_hop
if [ "$FAST" = 0 ]; then
  FAILED=0   # the backup rehearsal builds its own stack; it does not depend on the steps above
  run_step "backup, wipe and restore (MinIO + file replica)" step_backup
fi

printf '\n%-60s %-6s %s\n' STEP RESULT SECONDS
status=0
for row in "${RESULTS[@]}"; do
  IFS='|' read -r label result secs <<<"$row"
  printf '%-60s %-6s %s\n' "$label" "$result" "$secs"
  [ "$result" = FAIL ] && status=1
done
exit "$status"
