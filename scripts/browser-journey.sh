#!/usr/bin/env bash
# The browser-login journey, on demand: a maintainer runs it on a machine with Docker. It is not part of CI.
#
#   scripts/browser-journey.sh          build this checkout, run the scripted steps, print a pass/fail table
#   scripts/browser-journey.sh --keep   leave the stack running afterwards
#
# One throwaway Docker project (tico-browser-<run id>) holds the server built from this checkout with auth none, the
# practice login site (login.practice.test and other.practice.test), a fake Inbox in the server's network namespace
# (the server takes its inbound service on loopback only), and a runner enrolled with TICO_SCREEN=1. The bot's steps run
# inside its own turns through e2e/browser/fake-codex.py, so no model runs. A secret reaches a page only through
# `hub browser fill`; the leak check at the end looks for the secrets everywhere they could have been written.
#
# The NEEDS column names what a step relies on that is not on main yet (the bot's Chrome, login cards, site reconnects,
# the hub browser commands). Those steps fail until that work lands. The other steps fail only when what they check is
# broken. The exit code is the number of failed steps. Cleanup removes only this run's containers, volume, images and
# folder.

set -u -o pipefail

cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

for tool in docker curl python3 node openssl git; do
  command -v "$tool" >/dev/null || { echo "journey: $tool is required" >&2; exit 2; }
done
docker info >/dev/null 2>&1 || { echo "journey: the Docker daemon is not running" >&2; exit 2; }

RUN="$(openssl rand -hex 4)"
TAG="browser-$RUN"
PROJECT="tico-browser-$RUN"
SERVER_IMAGE=tico-browser-server
RUNNER_IMAGE=tico-browser-runner
NET="${PROJECT}_default"
RUNNER="browser-runner-$RUN"
RUNNER_VOLUME="browser-runner-$RUN"
TICO_PORT="${TICO_JOURNEY_PORT:-18765}"
PRACTICE_PORT="${TICO_JOURNEY_PRACTICE_PORT:-18080}"
EDGE="${TICO_JOURNEY_EDGE:-172.30.250}"
PHASE_WAIT="${TICO_JOURNEY_PHASE_WAIT:-300}"
HOST_NAME=login.practice.test
PRACTICE=http://login.practice.test:8080
OTHER=http://other.practice.test:8080
CREDENTIAL=PRACTICE_LOGIN
LABEL=Practice-login
# An ordinary bot from a starter template, added the way the UI adds one from Templates: the computer builds a starter's
# repository itself, so no BotOps turn is needed. TICO_JOURNEY_BOT names another bot to use instead.
BOT="${TICO_JOURNEY_BOT:-browser-bot}"
BOT_NAME="Browser bot"
BOT_TEMPLATE=learning
RESULTS_DIR=/home/runner/journey-threads/results
J="$REPO/e2e/browser/journey.py"
RESULTS=()
FAILS=0
BLOCKED=""
CHROME=0
LAST_PHASE=""
START_EPOCH="$(date +%s)"
INBOUND_KEY="$(openssl rand -hex 24)"
PRACTICE_ADMIN_TOKEN="$(openssl rand -hex 16)"
PERSON_USER="person_$(openssl rand -hex 3)"
BOT_USER="bot_$(openssl rand -hex 3)"

WORK="$(mktemp -d)"
DIR="$WORK/project"
mkdir -p "$DIR"
# The run's output is kept for the leak check, so it is written to the work folder as it goes.
exec > >(tee "$WORK/run.log") 2>&1
export TICO_PORT TOKEN_FILE="$WORK/owner.token"
python3 "$J" password "$WORK/person-password" && python3 "$J" password "$WORK/bot-password" || exit 2

cleanup() {
  if [ "$KEEP" = 1 ]; then
    echo "journey: kept the stack (docker compose -p $PROJECT --project-directory $REPO ...), $RUNNER and volume $RUNNER_VOLUME"
    return
  fi
  docker rm -f "$RUNNER" >/dev/null 2>&1
  dc down -v >/dev/null 2>&1
  docker volume rm "$RUNNER_VOLUME" >/dev/null 2>&1
  docker rmi "$SERVER_IMAGE:$TAG" "$RUNNER_IMAGE:$TAG" >/dev/null 2>&1
  # The folder holds the passwords, the setup keys and the owner token; it goes with the run.
  rm -rf "$WORK"
}
trap cleanup EXIT

dc() { docker compose -p "$PROJECT" --project-directory "$REPO" --env-file "$DIR/.env" \
  -f "$REPO/compose.yaml" -f "$DIR/compose.override.yaml" "$@"; }
say() { printf '  %s\n' "$*"; }
verdict() { if [ "$2" = 0 ]; then say "PASS $1"; else say "FAIL $1"; return 1; fi; }
retry() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ "$SECONDS" -lt "$end" ] || return 1; sleep 1; done; }
japi() { python3 "$J" api "$@"; }
send() { printf '%s' "$3" > "$WORK/body.json"; japi "$1" "$2" --body-file "$WORK/body.json"; }
pick() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
padmin() { curl -fsS -X "$1" -H "Authorization: Bearer $PRACTICE_ADMIN_TOKEN" "http://127.0.0.1:$PRACTICE_PORT$2"; }
healthy() { dc exec -T server curl -fsS --max-time 3 http://127.0.0.1:8765/healthz; }
runner_online() { japi GET /api/v2/getting-started | pick 'sys.exit(0 if any(i["id"]=="computer" and i["done"] for i in d["items"]) else 1)'; }
runner_id() { japi GET /api/v2/operations | pick 'd["machines"][0]["id"]'; }
bot_ready() { japi GET /api/v2/operations | BOT="$BOT" python3 -c 'import json,os,sys; d=json.load(sys.stdin); sys.exit(0 if d["machines"][0]["readiness"]["bots"].get(os.environ["BOT"], {}).get("ready") else 1)'; }

run_step() {  # the label, what it needs beyond main, the step's function
  local label=$1 needs=$2 fn=$3 start=$SECONDS
  printf '==> %s\n' "$label"
  if [ -n "$BLOCKED" ]; then
    say "not run: $BLOCKED"
    RESULTS+=("$label|$needs|FAIL|0")
    FAILS=$((FAILS + 1))
    return
  fi
  if "$fn"; then
    RESULTS+=("$label|$needs|PASS|$((SECONDS - start))")
  else
    RESULTS+=("$label|$needs|FAIL|$((SECONDS - start))")
    FAILS=$((FAILS + 1))
  fi
}

start_runner() {  # the enrollment code: the runner as docker/runner.compose.yaml starts it, with the screen on
  docker rm -f "$RUNNER" >/dev/null 2>&1
  docker run -d --name "$RUNNER" --restart unless-stopped --network "$NET" -e OPENAI_API_KEY=journey-fake \
    -e TICO_SCREEN=1 -v "$RUNNER_VOLUME:/home/runner" --user 0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE \
    --cap-add KILL --cap-add SETGID --cap-add SETUID --security-opt no-new-privileges:true \
    "$RUNNER_IMAGE:$TAG" join --url http://server:8765 --code "$1" --label "Browser runner" >/dev/null || return 1
  # The harness is the codex on the runner's PATH; it runs the bot's steps as the bot's own turn and answers the
  # journey's other prompts itself. The file modes are set before the copy, since root in the runner cannot chmod a
  # file the host user owns.
  install -m 755 "$REPO/e2e/browser/fake-codex.py" "$WORK/codex" &&
    install -m 755 "$REPO/scripts/journey-fake-codex.py" "$WORK/journey-fake-codex.py" &&
    install -m 755 "$REPO/e2e/browser/bot-steps.mjs" "$WORK/bot-steps.mjs" &&
    docker exec "$RUNNER" mkdir -p /opt/e2e "$RESULTS_DIR" &&
    docker cp "$WORK/codex" "$RUNNER:/usr/local/bin/codex" &&
    docker cp "$WORK/journey-fake-codex.py" "$RUNNER:/opt/e2e/journey-fake-codex.py" &&
    docker cp "$WORK/bot-steps.mjs" "$RUNNER:/usr/local/bin/bot-steps.mjs" &&
    docker exec "$RUNNER" chmod 777 /home/runner/journey-threads "$RESULTS_DIR"
}

attach_chrome_tool() {  # the bot opts in to Chrome in its bot.yaml in the checkout on the computer
  # A starter's bot.yaml already says `tools: []`; the entry goes into that list rather than a second `tools:` key.
  docker exec -i -e BOT="$BOT" "$RUNNER" sh -s <<'SH'
f=$(find /home/runner -path "*/bot-$BOT/bot.yaml" -not -path '*/node_modules/*' -not -path '*/worktrees/*' 2>/dev/null | head -n 1)
[ -n "$f" ] || { echo "no bot.yaml for $BOT" >&2; exit 1; }
if ! grep -q '^  - service: chrome$' "$f"; then
  if grep -q '^tools: *\[\] *\(#.*\)\{0,1\}$' "$f"; then sed -i 's/^tools: *\[\].*$/tools:\n  - service: chrome/' "$f"
  elif grep -q '^tools: *$' "$f"; then sed -i '/^tools: *$/a\  - service: chrome' "$f"
  elif grep -q '^tools:' "$f"; then echo "bot.yaml's tools are not a block list: $(grep '^tools:' "$f")" >&2; exit 1
  else printf '\ntools:\n  - service: chrome\n' >> "$f"; fi
fi
echo "journey: $f"
grep -A2 '^tools:' "$f"
grep -q '^  - service: chrome$' "$f"
SH
}

chrome_diagnose() {  # what the computer says about the bot's Chrome when it did not answer (no values: none exist yet)
  say "diagnosis: runner log lines about the browser"
  docker logs "$RUNNER" 2>&1 | grep -iE 'browser|chrome|xvfb|display|devtools' | tail -n 25 | sed 's/^/    /'
  say "diagnosis: the chrome tool as the computer reports it, and browser_v1"
  japi GET "/api/v2/bots/$BOT/tools" | python3 -c 'import json,sys
d=json.load(sys.stdin)
for t in d.get("tools", []):
    if str(t.get("service","")).lower()=="chrome": print("    tool:", json.dumps({k: t.get(k) for k in ("status","detail","problem","pending")}))' 2>&1
  japi GET /api/v2/operations | python3 -c 'import json,sys
m=json.load(sys.stdin)["machines"][0]
caps=m.get("capabilities") or (m.get("readiness") or {}).get("capabilities") or []
print("    browser_v1 advertised:", "browser_v1" in json.dumps(caps) or "browser_v1" in json.dumps(m))' 2>&1
}

bot_phase() {  # the phase's name, then extra KEY=value pairs; one line per check, nonzero when any check fails
  local name=$1 nonce out text
  shift
  nonce="$name-$RANDOM$RANDOM"
  out="$WORK/phase-$nonce.json"
  text="journey-run $nonce $name PRACTICE=$PRACTICE HOST=$HOST_NAME OTHER=$OTHER CREDENTIAL=$CREDENTIAL LABEL=$LABEL $*"
  python3 -c 'import json,sys; sys.stdout.write(json.dumps({"text": sys.argv[1]}))' "$text" > "$WORK/body.json"
  japi POST "/api/v2/chat/$BOT" --body-file "$WORK/body.json" >/dev/null || { say "the chat with $BOT did not take the message"; return 1; }
  retry "$PHASE_WAIT" docker exec "$RUNNER" test -s "$RESULTS_DIR/$nonce.json" ||
    { say "the bot did not finish $name in $PHASE_WAIT seconds"; return 1; }
  docker exec "$RUNNER" cat "$RESULTS_DIR/$nonce.json" > "$out" || return 1
  LAST_PHASE="$out"
  python3 "$J" report "$out"
}

browser_phase() {  # a phase that drives Chrome: it runs only once the bot's Chrome answered its status
  if [ "$CHROME" != 1 ]; then say "not run: the bot's Chrome did not answer"; return 1; fi
  bot_phase "$@"
}

expire_sessions() {  # ends every practice session; the admin view must show none left afterwards
  local ended left
  ended="$(padmin POST /admin/expire | pick 'd["expired"]')" || return 1
  left="$(padmin GET /admin/state | pick 'd["sessions"]')" || return 1
  [ "${ended:-0}" -ge 1 ] 2>/dev/null
  verdict "practice: /admin/expire ended $ended session(s)" $? || return 1
  [ "$left" = 0 ]
  verdict "practice: no session is left" $?
}

step_install() {
  local pid1 pid2
  say "building the server and the runner from this checkout (the runner compiles Rust, the slow one)"
  docker build -q --target server --build-arg TICO_VERSION=v9.9.9 --build-arg "TICO_COMMIT=$(git rev-parse HEAD)" \
    --build-arg TICO_REPOSITORY=ticoteam/tico -t "$SERVER_IMAGE:$TAG" "$REPO" >"$WORK/build-server.log" 2>&1 & pid1=$!
  docker build -q --target runner --build-arg TICO_VERSION=v9.9.9 --build-arg "TICO_COMMIT=$(git rev-parse HEAD)" \
    --build-arg TICO_REPOSITORY=ticoteam/tico -t "$RUNNER_IMAGE:$TAG" "$REPO" >"$WORK/build-runner.log" 2>&1 & pid2=$!
  if ! wait "$pid1"; then BLOCKED="the server image did not build"; say "$BLOCKED"; tail -n 5 "$WORK/build-server.log"; return 1; fi
  if ! wait "$pid2"; then BLOCKED="the runner image did not build"; say "$BLOCKED"; tail -n 5 "$WORK/build-runner.log"; return 1; fi

  cat > "$DIR/.env" <<EOF
TICO_COMPANY_NAME=Browser journey
TICO_OWNER_EMAIL=owner@example.com
TICO_AUTH_PROXY=none
TICO_IMAGE=$SERVER_IMAGE
TICO_TAG=$TAG
TICO_PORT=$TICO_PORT
TICO_INBOUND_URL=http://127.0.0.1:8791
TICO_INBOUND_KEY=$INBOUND_KEY
EOF
  bash "$REPO/docker/edge-env.sh" "$EDGE" >> "$DIR/.env" || { BLOCKED="the edge range is not set"; return 1; }
  # The inbox runs in the server's network namespace, because the server only takes its inbound service on loopback.
  # The practice site has the two hostnames its signup and the refused fill use.
  cat > "$DIR/compose.override.yaml" <<EOF
name: $PROJECT
services:
  server:
    ports: !override ["127.0.0.1:$TICO_PORT:8765"]
    pull_policy: missing
  inbox:
    image: $SERVER_IMAGE:$TAG
    pull_policy: never
    network_mode: service:server
    depends_on: [server]
    entrypoint: ["python", "-I"]
    command: ["/stub/server.py"]
    environment:
      STUB_KEY: "$INBOUND_KEY"
      STUB_PORT: "8791"
      STUB_DOMAIN: inbound.tico.test
    volumes:
      - "$REPO/e2e/inbox:/stub:ro"
  practice:
    image: $SERVER_IMAGE:$TAG
    pull_policy: never
    entrypoint: ["python", "-I"]
    command: ["/practice/server.py"]
    depends_on: [server]
    environment:
      PRACTICE_MAIL_URL: http://server:8791
      PRACTICE_ADMIN_TOKEN: "$PRACTICE_ADMIN_TOKEN"
      PRACTICE_PORT: "8080"
    ports: ["127.0.0.1:$PRACTICE_PORT:8080"]
    networks:
      default:
        aliases: [login.practice.test, other.practice.test]
    volumes:
      - "$REPO/e2e/login-practice:/practice:ro"
EOF
  dc up -d server >/dev/null 2>&1 || { BLOCKED="docker compose could not start the server"; say "$BLOCKED"; return 1; }
  retry 300 healthy || { BLOCKED="the server did not become healthy"; say "$BLOCKED"; return 1; }
  dc exec -T server cat /data/local-owner.token > "$WORK/owner.token" && chmod 600 "$WORK/owner.token" ||
    { BLOCKED="the owner token could not be read"; return 1; }
  dc up -d inbox practice >/dev/null 2>&1 || { BLOCKED="the inbox or the practice site did not start"; return 1; }
  verdict "install: the server is healthy with auth none" 0
}

step_reach() {
  local ok=0
  retry 60 curl -fsS -o /dev/null "http://127.0.0.1:$PRACTICE_PORT/"
  verdict "practice login site answers on the host" $? || ok=1
  dc exec -T server python -I -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8791/_state", timeout=5).read()' >/dev/null 2>&1
  verdict "inbox stub answers in the server's network" $? || ok=1
  dc exec -T practice python -I -c 'import urllib.request; urllib.request.urlopen("http://server:8791/_state", timeout=5).read()' >/dev/null 2>&1
  verdict "practice site reaches the inbox stub" $? || ok=1
  return $ok
}

step_enroll() {
  local operator code revision
  operator="$(japi GET /api/v2/me | pick 'd["actor"].split(":",1)[1]')" || { BLOCKED="no owner to enroll the runner for"; return 1; }
  code="$(send POST /api/v2/enrollments "{\"operator\": \"$operator\"}" | pick 'd["code"]')" || { BLOCKED="no enrollment code"; return 1; }
  start_runner "$code" || { BLOCKED="the runner container did not start"; say "$BLOCKED"; return 1; }
  retry 180 runner_online || { BLOCKED="the runner did not come online"; say "$BLOCKED"; return 1; }
  revision="$(japi GET /api/v2/providers | pick 'd["revision"]')" || return 1
  send PUT /api/v2/providers "{\"enabled\": [\"openai\"], \"runtime\": \"\", \"model\": \"\", \"expected_revision\": $revision}" >/dev/null || return 1
  verdict "enroll: the runner is online and OpenAI is enabled" 0
}

step_person_signup() {
  local inbox inbox_id email
  inbox="$(python3 "$J" inbox-create --name "Practice person")" || return 1
  inbox_id="$(printf '%s' "$inbox" | pick 'd["inbox_id"]')" || return 1
  email="$(printf '%s' "$inbox" | pick 'd["address"]')" || return 1
  python3 "$J" person-signup --practice "http://127.0.0.1:$PRACTICE_PORT" --inbox-id "$inbox_id" --email "$email" \
    --user "$PERSON_USER" --password-file "$WORK/person-password"
}

step_person_2fa() {
  python3 "$J" person-2fa --practice "http://127.0.0.1:$PRACTICE_PORT" --user "$PERSON_USER" \
    --password-file "$WORK/person-password" --secret-out "$WORK/person-secret"
}

step_expire_person() { expire_sessions; }

step_bot() {
  local rid assignment assigned generation revision
  rid="$(runner_id)" || return 1
  if ! japi GET "/api/v2/bots/$BOT" >/dev/null 2>&1; then
    send POST /api/v2/bots/register "{\"slug\": \"$BOT\", \"display_name\": \"$BOT_NAME\", \"template\": \"$BOT_TEMPLATE\", \"build\": true}" >/dev/null || return 1
  fi
  assignment="$(japi GET "/api/v2/bots/$BOT")" || return 1
  assigned="$(printf '%s' "$assignment" | pick '(d.get("assignment") or {}).get("runner_id", "")')"
  generation="$(printf '%s' "$assignment" | pick '(d.get("assignment") or {}).get("generation", 0)')"
  if [ "$assigned" != "$rid" ]; then
    send POST "/api/v2/bots/$BOT/assignment" "{\"runner_id\": \"$rid\", \"expected_generation\": $generation}" >/dev/null || return 1
  fi
  revision="$(japi GET /api/v2/bots | pick "[b for b in d if b[\"slug\"] == \"$BOT\"][0][\"revision\"]")" || return 1
  send POST "/api/v2/bots/$BOT/definition" "{\"status\": \"active\", \"expected_revision\": $revision}" >/dev/null || return 1
  retry 300 bot_ready || { say "the bot never became ready on the computer"; return 1; }
  attach_chrome_tool || { say "the chrome tool could not be added to the bot"; return 1; }
  verdict "bot: $BOT is active on the computer with the chrome tool" 0
}

step_chrome() {
  bot_phase status || { chrome_diagnose; return 1; }
  CHROME=1
}

step_card_first() {
  bot_phase request-card || return 1
  local card
  card="$(python3 "$J" field "$LAST_PHASE" card)" || return 1
  [ -n "$card" ] || { say "the bot opened no login card"; return 1; }
  python3 "$J" save-card "$card" --username "$BOT_USER" --password-file "$WORK/bot-password" >/dev/null ||
    { say "the person could not save the login card"; return 1; }
  verdict "creds: the person saved the bot's login card (username and password)" 0
}

step_signup_bot() { browser_phase signup; }

step_card_second() {
  local card
  python3 "$J" setup-key --practice "http://127.0.0.1:$PRACTICE_PORT" --user "$BOT_USER" \
    --password-file "$WORK/bot-password" --out "$WORK/bot-secret" || return 1
  bot_phase request-card || return 1
  card="$(python3 "$J" field "$LAST_PHASE" card)" || return 1
  [ -n "$card" ] || { say "the bot opened no second login card"; return 1; }
  python3 "$J" save-card "$card" --username "$BOT_USER" --password-file "$WORK/bot-password" \
    --totp-file "$WORK/bot-secret" >/dev/null || { say "the person could not save the card with the second step"; return 1; }
  verdict "creds: the person saved the card with the second step" 0
}

step_enable() { browser_phase enable-2fa; }

step_login2fa() { browser_phase login-2fa; }

step_restart() {
  docker restart "$RUNNER" >/dev/null || return 1
  retry 180 runner_online || { say "the runner did not come back after the restart"; return 1; }
  browser_phase restart-check
}

step_expire_bot() { expire_sessions; }

step_relogin() { browser_phase relogin; }

step_needs_you() {
  local card id task key
  bot_phase needs-you || return 1
  card="$(python3 "$J" reconnect-id "$HOST_NAME")" || return 1
  [ -n "$card" ] || { say "no open site reconnect for $HOST_NAME in Needs you"; return 1; }
  id="$(printf '%s' "$card" | pick 'd["id"] or "-"')"
  task="$(printf '%s' "$card" | pick 'd["task"]')"
  key="$(printf '%s' "$card" | pick 'd["key"]')"
  # Done finds the card by its id, or by its task and key when the id is not on the card: "-" asks for the latter.
  send POST "/api/v2/reconnects/$id/done" "{\"task\": \"$task\", \"key\": \"$key\"}" >/dev/null || { say "Done did not close the reconnect"; return 1; }
  [ -z "$(python3 "$J" reconnect-id "$HOST_NAME")" ]
  verdict "needs you: Done closed the reconnect" $?
}

step_import() {
  local first again first_ok=0
  python3 "$J" import-file "$WORK/import.json" >/dev/null || return 1
  first="$(python3 "$J" import-logins "$WORK/import.json")" || return 1
  printf '%s' "$first" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["imported"] == 2 and d["errors"] == 0 else 1)' || first_ok=1
  again="$(python3 "$J" import-logins "$WORK/import.json")" || return 1
  printf '%s' "$again" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["imported"] == 0 and d["skipped"] == 2 else 1)'
  local again_ok=$?
  verdict "import: the owner's export adds two logins; importing it again adds none" $(( first_ok || again_ok ))
}

step_code_refused() {
  local status
  status="$(python3 "$J" code-refused "Journey import two")" || return 1
  printf '%s' "$status" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["status"] == 403 else 1)'
  verdict "code: a personal API token cannot read a 2FA code (a person opens it signed in)" $?
}

step_screen() {
  local rid out="$WORK/screen.json"
  rid="$(runner_id)" || return 1
  send POST "/api/v2/runners/$rid/screen" '{"mode": "view"}' >/dev/null || { say "the screen could not be turned on"; return 1; }
  node "$REPO/e2e/browser/screen-check.mjs" "http://127.0.0.1:$TICO_PORT" "$rid" "$WORK/screen-390x844.png" \
    > "$out" 2>"$WORK/screen.err"
  local rc=$?
  send POST "/api/v2/runners/$rid/screen" '{"mode": "off"}' >/dev/null
  python3 "$J" report "$out" || return 1
  return "$rc"
}

step_beacon() {
  local beacon
  beacon="$(padmin GET /admin/state | pick 'd["beacon"]')" || return 1
  [ "$beacon" = "false" ]
  verdict "the practice page script saw navigator.webdriver false" $?
}

step_leaks() {
  dc exec -T server python -I -c 'import sqlite3; c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True); print("\n".join(c.iterdump()))' \
    > "$WORK/db.sql" 2>/dev/null
  dc logs --no-log-prefix server inbox > "$WORK/server.log" 2>&1
  { docker logs "$RUNNER" 2>&1; docker exec "$RUNNER" sh -c 'find /home/runner -name "*.log" -type f -exec cat {} + 2>/dev/null'; } > "$WORK/runner.log"
  dc logs --no-log-prefix practice > "$WORK/practice.log" 2>&1
  cat "$WORK"/phase-*.json > "$WORK/phase-results.txt" 2>/dev/null
  docker exec "$RUNNER" sh -c "cat $RESULTS_DIR/*.json 2>/dev/null" > "$WORK/runner-results.txt"
  dc exec -T practice python -I -c 'import urllib.request; print(urllib.request.urlopen("http://server:8791/_deliveries", timeout=5).read().decode())' \
    > "$WORK/mail.json" 2>/dev/null
  # The leak check only means something when every source was read: an empty source would pass trivially.
  [ -s "$WORK/db.sql" ] && [ -s "$WORK/server.log" ] && [ -s "$WORK/practice.log" ] && [ -s "$WORK/mail.json" ] &&
    [ -s "$WORK/import.json" ]
  verdict "leak check read every source" $? || return 1
  python3 "$J" needles --secret-file "$WORK/person-secret" --secret-file "$WORK/bot-secret" \
    --password-file "$WORK/person-password" --password-file "$WORK/bot-password" --start "$START_EPOCH" \
    --mail "$WORK/mail.json" --import-file "$WORK/import.json" --out "$WORK/needles.json" >/dev/null || return 1
  python3 "$J" leakscan --needles "$WORK/needles.json" --source "db=$WORK/db.sql" --source "events=$WORK/server.log" \
    --source "runner=$WORK/runner.log" --source "practice=$WORK/practice.log" \
    --source "results=$WORK/runner-results.txt" --source "phases=$WORK/phase-results.txt" --source "run=$WORK/run.log" \
    --source "import=$WORK/import.json"
  verdict "leak check: no password, setup key or code in any source" $?
}

printf 'journey: run %s, Docker names end in -%s\n' "$RUN" "$RUN"
run_step "install: images built from this checkout, server healthy, inbox and practice up" main step_install
run_step "practice site and inbox stub reach each other" main step_reach
run_step "runner enrolled, online, OpenAI enabled" main step_enroll
run_step "person signs up on the practice site with a code from its inbox" main step_person_signup
run_step "person turns two-step sign-in on; a wrong code is refused" main step_person_2fa
run_step "practice: /admin/expire ends the person's session" main step_expire_person
run_step "bot created, on the computer, active, with the chrome tool" "runner: chrome tool" step_bot
run_step "bot: Chrome runs for the bot and answers its status" "runner: chrome, hub browser" step_chrome
run_step "bot asks for a login card; person saves it" "creds: login card" step_card_first
run_step "bot signs up, code read from its own inbox, typed with hub browser fill" "chrome, creds, fill" step_signup_bot
run_step "person reads the setup key; bot asks again; person saves the card with the second step" "chrome, creds" step_card_second
run_step "bot turns two-step sign-in on from the saved key" "chrome, creds" step_enable
run_step "bot signs in with the second step" "chrome, creds" step_login2fa
run_step "runner restart keeps the bot's login" "chrome, creds, runner restart" step_restart
run_step "practice: /admin/expire ends the bot's sessions" main step_expire_bot
run_step "bot signs in again with no person" "chrome, creds" step_relogin
run_step "bot raises a site reconnect; Done closes it" "creds: reconnects, hub browser needs-you" step_needs_you
run_step "owner imports a password manager export; importing it again adds none" "creds: import" step_import
run_step "a 2FA code is refused to a personal API token" "creds: code" step_code_refused
run_step "computer screen opens at 390x844 and fits the width" "main, screen on computer" step_screen
run_step "practice page script saw navigator.webdriver false" "chrome" step_beacon
run_step "leak check: database, events, runner logs, run output, practice log" "main" step_leaks

printf '\n%-80s %-40s %-6s %s\n' STEP NEEDS RESULT SECONDS
for row in "${RESULTS[@]}"; do
  IFS='|' read -r label needs result secs <<<"$row"
  printf '%-80s %-40s %-6s %s\n' "$label" "$needs" "$result" "$secs"
done
echo "journey: $((${#RESULTS[@]} - FAILS)) of ${#RESULTS[@]} steps passed"
[ "$FAILS" -gt 125 ] && FAILS=125
exit "$FAILS"
