# The browser scripts' shared setup, sourced by scripts/browser-journey.sh and scripts/browser-scenarios.sh. It is never
# run on its own. The caller sets REPO (the checkout) and KEEP (1 leaves the stack up), then calls browser_init.
#
# One throwaway Docker project (tico-browser-<run id>) holds the server built from this checkout with auth none, the
# practice login site (login.practice.test and other.practice.test), a fake Inbox in the server's network namespace
# (the server takes its inbound service on loopback only), and a runner enrolled with TICO_SCREEN=1. A bot on that
# computer has Chrome, and its steps run inside its own turns through e2e/browser/fake-codex.py, so no model runs.

browser_init() {
  for tool in docker curl python3 node openssl git; do
    command -v "$tool" >/dev/null || { echo "browser: $tool is required" >&2; exit 2; }
  done
  docker info >/dev/null 2>&1 || { echo "browser: the Docker daemon is not running" >&2; exit 2; }

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
  # Extra compose files and runner variables a script adds (the scenarios' proxy and their Chrome settings); empty
  # for the journey.
  EXTRA_COMPOSE="${EXTRA_COMPOSE:-}"
  RUNNER_ENV=()

  WORK="$(mktemp -d)"
  DIR="$WORK/project"
  mkdir -p "$DIR"
  # The run's output is kept for the leak check, so it is written to the work folder as it goes.
  exec > >(tee "$WORK/run.log") 2>&1
  export TICO_PORT TOKEN_FILE="$WORK/owner.token"
  python3 "$J" password "$WORK/person-password" && python3 "$J" password "$WORK/bot-password" || exit 2
  trap browser_cleanup EXIT
}

browser_cleanup() {
  if [ "${KEEP:-0}" = 1 ]; then
    echo "browser: kept the stack (docker compose -p $PROJECT --project-directory $REPO ...), $RUNNER and volume $RUNNER_VOLUME"
    return
  fi
  docker rm -f "$RUNNER" >/dev/null 2>&1
  dc down -v >/dev/null 2>&1
  docker volume rm "$RUNNER_VOLUME" >/dev/null 2>&1
  docker rmi "$SERVER_IMAGE:$TAG" "$RUNNER_IMAGE:$TAG" >/dev/null 2>&1
  # The folder holds the passwords, the setup keys and the owner token; it goes with the run.
  rm -rf "$WORK"
}

dc() {
  if [ -n "$EXTRA_COMPOSE" ]; then
    docker compose -p "$PROJECT" --project-directory "$REPO" --env-file "$DIR/.env" \
      -f "$REPO/compose.yaml" -f "$DIR/compose.override.yaml" -f "$EXTRA_COMPOSE" "$@"
  else
    docker compose -p "$PROJECT" --project-directory "$REPO" --env-file "$DIR/.env" \
      -f "$REPO/compose.yaml" -f "$DIR/compose.override.yaml" "$@"
  fi
}
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
bot_ready() {  # the bot's slug
  japi GET /api/v2/operations | BOT="$1" python3 -c 'import json,os,sys; d=json.load(sys.stdin); sys.exit(0 if d["machines"][0]["readiness"]["bots"].get(os.environ["BOT"], {}).get("ready") else 1)'
}

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

browser_table() {  # the script's name for the summary line; the exit code is the number of failed steps, capped at 125
  printf '\n%-80s %-40s %-6s %s\n' STEP NEEDS RESULT SECONDS
  for row in "${RESULTS[@]}"; do
    IFS='|' read -r label needs result secs <<<"$row"
    printf '%-80s %-40s %-6s %s\n' "$label" "$needs" "$result" "$secs"
  done
  echo "$1: $(( ${#RESULTS[@]} - FAILS )) of ${#RESULTS[@]} steps passed"
  [ "$FAILS" -gt 125 ] && FAILS=125
  return "$FAILS"
}

start_runner() {  # the enrollment code: the runner as docker/runner.compose.yaml starts it, with the screen on
  docker rm -f "$RUNNER" >/dev/null 2>&1
  docker run -d --name "$RUNNER" --restart unless-stopped --network "$NET" -e OPENAI_API_KEY=journey-fake \
    -e TICO_SCREEN=1 ${RUNNER_ENV[@]+"${RUNNER_ENV[@]}"} -v "$RUNNER_VOLUME:/home/runner" --user 0 --cap-drop ALL \
    --cap-add CHOWN --cap-add DAC_OVERRIDE \
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

attach_chrome_tool() {  # the bot's slug: the bot opts in to Chrome in its bot.yaml in the checkout on the computer
  # A starter's bot.yaml already says `tools: []`; the entry goes into that list rather than a second `tools:` key.
  docker exec -i -e BOT="$1" "$RUNNER" sh -s <<'SH'
f=$(find /home/runner -path "*/bot-$BOT/bot.yaml" -not -path '*/node_modules/*' -not -path '*/worktrees/*' 2>/dev/null | head -n 1)
[ -n "$f" ] || { echo "no bot.yaml for $BOT" >&2; exit 1; }
if ! grep -q '^  - service: chrome$' "$f"; then
  if grep -q '^tools: *\[\] *\(#.*\)\{0,1\}$' "$f"; then sed -i 's/^tools: *\[\].*$/tools:\n  - service: chrome/' "$f"
  elif grep -q '^tools: *$' "$f"; then sed -i '/^tools: *$/a\  - service: chrome' "$f"
  elif grep -q '^tools:' "$f"; then echo "bot.yaml's tools are not a block list: $(grep '^tools:' "$f")" >&2; exit 1
  else printf '\ntools:\n  - service: chrome\n' >> "$f"; fi
fi
echo "chrome tool: $f"
grep -q '^  - service: chrome$' "$f"
SH
}

profile_facts() {  # the bot's Chrome profile as the restart left it: its exit and restore prefs, its cookies' names
  docker exec -i -e BOT="$BOT" "$RUNNER" python3 -I - <<'PY' 2>&1 | sed 's/^/    /'
import glob, os, re, sqlite3
p = os.path.join("/home/runner/browsers", os.environ["BOT"], "profile", "Default")
try:
    text = open(os.path.join(p, "Preferences")).read()
    print("prefs:", " ".join(re.findall(r'"(?:restore_on_startup|exit_type|exited_cleanly)":(?:"[A-Za-z]*"|[0-9a-z]+)', text)) or "none of them")
except OSError as e:
    print("prefs:", e)
print("files:", " ".join(sorted(os.listdir(p))) if os.path.isdir(p) else "no Default folder")
for db in glob.glob(os.path.join(p, "**", "Cookies"), recursive=True):
    try:
        c = sqlite3.connect("file:%s?mode=ro&immutable=1" % db, uri=True)
        print("cookies:", db[len(p):], c.execute("SELECT group_concat(host_key || ' ' || name || ' persistent=' || is_persistent, '; ') FROM cookies").fetchone()[0])
    except sqlite3.Error as e:
        print("cookies:", db[len(p):], e)
PY
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
  local name=$1 nonce out text bot=${PHASE_BOT:-$BOT}
  shift
  nonce="$name-$RANDOM$RANDOM"
  out="$WORK/phase-$nonce.json"
  text="journey-run $nonce $name PRACTICE=$PRACTICE HOST=$HOST_NAME OTHER=$OTHER CREDENTIAL=$CREDENTIAL LABEL=$LABEL $*"
  # PHASE_ON=task runs the phase as a turn on a new task of the bot's (what waits on a person needs a task); else chat.
  if [ "${PHASE_ON:-chat}" = task ]; then
    BOT="$bot" python3 -c 'import json,os,sys; sys.stdout.write(json.dumps({"owner": "bot:" + os.environ["BOT"],
        "title": "Practice site sign-in", "body": sys.argv[1]}))' "$text" > "$WORK/body.json"
    japi POST /api/v2/tasks --body-file "$WORK/body.json" >/dev/null || { say "$bot did not take the task"; return 1; }
  else
    python3 -c 'import json,sys; sys.stdout.write(json.dumps({"text": sys.argv[1]}))' "$text" > "$WORK/body.json"
    japi POST "/api/v2/chat/$bot" --body-file "$WORK/body.json" >/dev/null || { say "the chat with $bot did not take the message"; return 1; }
  fi
  retry "$PHASE_WAIT" docker exec "$RUNNER" test -s "$RESULTS_DIR/$nonce.json" ||
    { say "the bot did not finish $name in $PHASE_WAIT seconds"; return 1; }
  docker exec "$RUNNER" cat "$RESULTS_DIR/$nonce.json" > "$out" || return 1
  LAST_PHASE="$out"
  python3 "$J" report "$out"
}

task_phase() {  # the phase's name, the bot's slug, then KEY=value pairs: a task for the bot, run in its own turn
  local name=$1 bot=$2 nonce out text
  shift 2
  nonce="$name-$RANDOM$RANDOM"
  out="$WORK/phase-$nonce.json"
  text="journey-run $nonce $name PRACTICE=$PRACTICE HOST=$HOST_NAME OTHER=$OTHER CREDENTIAL=$CREDENTIAL LABEL=$LABEL $*"
  python3 -c 'import json,sys; sys.stdout.write(json.dumps({"title": sys.argv[1], "body": sys.argv[2], "owner": sys.argv[3]}))' \
    "Browser scenario $name" "$text" "bot:$bot" > "$WORK/body.json"
  send POST /api/v2/tasks "$(cat "$WORK/body.json")" >/dev/null || { say "the task for $bot was not created"; return 1; }
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

# Makes a bot from the starter template, active on the computer with the chrome tool. The bot's slug and name.
make_bot() {
  local slug=$1 name=$2 rid assignment assigned generation revision
  rid="$(runner_id)" || return 1
  if ! japi GET "/api/v2/bots/$slug" >/dev/null 2>&1; then
    send POST /api/v2/bots/register "{\"slug\": \"$slug\", \"display_name\": \"$name\", \"template\": \"$BOT_TEMPLATE\", \"build\": true}" >/dev/null || return 1
  fi
  assignment="$(japi GET "/api/v2/bots/$slug")" || return 1
  assigned="$(printf '%s' "$assignment" | pick '(d.get("assignment") or {}).get("runner_id", "")')"
  generation="$(printf '%s' "$assignment" | pick '(d.get("assignment") or {}).get("generation", 0)')"
  if [ "$assigned" != "$rid" ]; then
    send POST "/api/v2/bots/$slug/assignment" "{\"runner_id\": \"$rid\", \"expected_generation\": $generation}" >/dev/null || return 1
  fi
  revision="$(japi GET /api/v2/bots | pick "[b for b in d if b[\"slug\"] == \"$slug\"][0][\"revision\"]")" || return 1
  send POST "/api/v2/bots/$slug/definition" "{\"status\": \"active\", \"expected_revision\": $revision}" >/dev/null || return 1
  retry 300 bot_ready "$slug" || { say "the bot $slug never became ready on the computer"; return 1; }
  # The owner marks onboarding complete, as the UI does for a new bot; until then the bot takes no tasks.
  send POST "/api/v2/bots/$slug/onboarded" '{}' >/dev/null || { say "onboarding of $slug was not marked complete"; return 1; }
  attach_chrome_tool "$slug" || { say "the chrome tool could not be added to $slug"; return 1; }
}

step_bot() {
  make_bot "$BOT" "$BOT_NAME" || return 1
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
