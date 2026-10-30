#!/usr/bin/env bash
# The browser harness, run on demand by a maintainer on a machine with Docker. It is not part of CI.
#
#   scripts/browser-harness.sh          run every harness the runner supports; print a matrix; exit with the failure count
#   scripts/browser-harness.sh --keep   leave the stack running afterwards
#
# It starts the journey's stack (scripts/browser-lib.sh): the server, a runner with the screen on, the practice login site
# and the inbox stub. The bot gets one turn per harness. Each turn runs the harness's own CLI name, which here is a stand-in
# (e2e/browser/harness/fake_cli.py): it answers the runner's version and sign-in probes, writes what the run gave it (argv,
# environment, the MCP servers its config names), and runs e2e/browser/harness/probe.mjs inside the turn. The probe starts
# each MCP server exactly as the config wrote it, drives the run's Chrome through them, and fills the practice form through
# the hub MCP tools and the hub CLI. No model runs. The bot's password and 2FA secret, and every code the secret makes, are
# scanned for in every file the runs wrote.
#
# A harness whose turn cannot run here is a failure, not a skip: nothing is proven for it. The Tico Gateway runs its turns on
# the server with no computer, so it is listed as a gap. Real CLIs (claude, codex, muse) are looked for in the runner image;
# none ships there today, so their real-CLI check is skipped and reported as such.

set -u -o pipefail

cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
. "$REPO/scripts/browser-lib.sh"
browser_init

BOT=harness-bot
BOT_NAME="Harness bot"
LABEL=Harness-login
PHASE_WAIT="${TICO_HARNESS_PHASE_WAIT:-240}"
HARNESS_ROOT=/home/runner/harness
HARNESS_RESULTS="$HARNESS_ROOT/results"
REAL_CLIS=""
SWITCH_WHY=""
TURN_WHY=""
LAST_RESULT=""
mkdir -p "$WORK/rows" "$WORK/results" "$WORK/dumps"

# The modes are set on the host before the copy: root in the runner cannot chmod a file the host user owns.
# The stand-in CLIs take the runner's harness names: each name the runner starts is the same program.
install_fakes() {
  install -m 755 "$REPO/e2e/browser/harness/fake_cli.py" "$WORK/fake_cli.py" &&
    install -m 644 "$REPO/e2e/browser/harness/probe.mjs" "$WORK/probe.mjs" &&
    docker exec "$RUNNER" mkdir -p /opt/harness "$HARNESS_RESULTS" "$HARNESS_ROOT/dumps" &&
    docker cp "$WORK/fake_cli.py" "$RUNNER:/opt/harness/fake_cli.py" &&
    docker cp "$WORK/probe.mjs" "$RUNNER:/opt/harness/probe.mjs" &&
    docker exec "$RUNNER" sh -c "chmod -R 777 $HARNESS_ROOT &&
      for name in claude codex muse gemini grok cursor-agent pi agy; do install -m 755 /opt/harness/fake_cli.py /usr/local/bin/\$name || exit 1; done"
}

# One turn of the bot with a harness-run prompt: name, phase, then KEY=value pairs for the probe. Its result lands in
# $LAST_RESULT and its dump in $WORK/dumps/<name>.json; a failure says why in $TURN_WHY.
hturn() {
  local name=$1 phase=$2 nonce text out
  shift 2
  nonce="$name-$RANDOM$RANDOM"
  out="$WORK/$name-$phase.json"
  text="harness-run $nonce $phase PRACTICE=$PRACTICE HOST=$HOST_NAME LABEL=$LABEL $*"
  python3 -c 'import json,sys; sys.stdout.write(json.dumps({"text": sys.argv[1]}))' "$text" > "$WORK/body.json"
  japi POST "/api/v2/chat/$BOT" --body-file "$WORK/body.json" >/dev/null || { TURN_WHY="the chat with $BOT did not take the message"; return 1; }
  retry "$PHASE_WAIT" docker exec "$RUNNER" test -s "$HARNESS_RESULTS/$nonce.json" ||
    { TURN_WHY="the bot wrote no result in $PHASE_WAIT seconds"; return 1; }
  docker exec "$RUNNER" cat "$HARNESS_RESULTS/$nonce.json" > "$out" || { TURN_WHY="the result could not be read"; return 1; }
  docker exec "$RUNNER" sh -c "cat $HARNESS_ROOT/dumps/*-$nonce.json" > "$WORK/dumps/$name.json" 2>/dev/null
  LAST_RESULT="$out"
}

# The bot's model and harness for its next turn; waits until the computer reports the bot ready on it.
switch_model() {  # the harness id, then its model id
  local harness=$1 model=$2 revision
  SWITCH_WHY=""
  revision="$(japi GET /api/v2/bots | pick "[b for b in d if b[\"slug\"] == \"$BOT\"][0][\"revision\"]")" ||
    { SWITCH_WHY="the bot's revision could not be read"; return 1; }
  python3 -c 'import json,sys; sys.stdout.write(json.dumps({"model": sys.argv[1], "harness": sys.argv[2], "expected_revision": int(sys.argv[3])}))' \
    "$model" "$harness" "$revision" > "$WORK/body.json"
  SWITCH_WHY="$(japi POST "/api/v2/bots/$BOT/model" --body-file "$WORK/body.json" 2>&1 >/dev/null)" ||
    { SWITCH_WHY="the computer would not run $harness: ${SWITCH_WHY##*: }"; return 1; }
  retry 240 bot_ready "$BOT" || { SWITCH_WHY="the bot never became ready on $harness"; return 1; }
}

# A harness's row: the switch that makes the bot run it, then one probe turn. A row that cannot run keeps its reason in
# rows/<name>.json; report.py counts it as a failure. The row never fails the step, so every harness gets its turn.
run_row() {  # the row's name, its harness id, its model id, and "devtools" when its config must carry the chrome server
  local name=$1 harness=$2 model=$3 expect=$4 why="" ran=0
  if ! switch_model "$harness" "$model"; then
    why=$SWITCH_WHY
  elif hturn "$name" probe; then
    cp "$LAST_RESULT" "$WORK/results/$name.json"
    ran=1
  else
    why=$TURN_WHY
  fi
  python3 - "$WORK/rows/$name.json" "$name" "$harness" "$model" "$ran" "$expect" "$why" "$REAL_CLIS" <<'PY'
import json, sys
path, name, harness, model, ran, expect, why, clis = sys.argv[1:]
exe = {"claude-code": "claude", "codex": "codex", "muse-code": "muse"}.get(name)
if exe is None:
    real = "n/a"
elif exe in clis.split():
    real = "in image, not exercised yet"
else:
    real = "skipped: %s is not in the runner image" % exe
json.dump({"name": name, "harness": harness, "model": model, "turn": bool(int(ran)), "why": why,
           "expect_devtools": expect == "devtools", "real_cli": real}, open(path, "w"))
PY
  if [ "$ran" = 1 ]; then say "$name: probe finished"; else say "$name: not run: $why"; fi
  return 0
}

step_real_clis() {  # the harness binaries the runner image ships; none of the real CLIs is exercised here yet
  REAL_CLIS="$(docker run --rm --entrypoint sh "$RUNNER_IMAGE:$TAG" -c 'for n in claude codex muse; do command -v "$n" >/dev/null && echo "$n"; done' 2>/dev/null | tr '\n' ' ')"
  say "runner image ships: ${REAL_CLIS:-none of claude, codex, muse}"
}

step_enroll_harness() {  # enrolls the runner as the journey does, then puts the stand-in CLIs on its PATH; every provider on
  local operator code revision
  operator="$(japi GET /api/v2/me | pick 'd["actor"].split(":",1)[1]')" || { BLOCKED="no owner to enroll the runner for"; return 1; }
  code="$(send POST /api/v2/enrollments "{\"operator\": \"$operator\"}" | pick 'd["code"]')" || { BLOCKED="no enrollment code"; return 1; }
  start_runner "$code" || { BLOCKED="the runner container did not start"; say "$BLOCKED"; return 1; }
  retry 180 runner_online || { BLOCKED="the runner did not come online"; say "$BLOCKED"; return 1; }
  install_fakes || { BLOCKED="the stand-in CLIs did not reach the runner"; say "$BLOCKED"; return 1; }
  revision="$(japi GET /api/v2/providers | pick 'd["revision"]')" || return 1
  send PUT /api/v2/providers '{"enabled": ["openai", "anthropic", "google", "xai", "cursor", "meta", "deepseek"], "runtime": "", "model": "", "expected_revision": '"$revision"'}' >/dev/null || return 1
  verdict "enroll: the runner is online with the stand-in CLIs, and every provider the harnesses need is on" 0
}

step_bot_harness() {
  make_bot "$BOT" "$BOT_NAME" || return 1
  verdict "bot: $BOT is active on the computer with the chrome tool" 0
}

# The practice account the bot signs in with, made as the journey makes the bot's: its own inbox gets the signup code,
# and two-step sign-in is switched on. The secret it writes is the one the login card saves.
step_practice_account() {
  local inbox inbox_id email
  inbox="$(python3 "$J" inbox-create --name "Harness bot")" || return 1
  inbox_id="$(printf '%s' "$inbox" | pick 'd["inbox_id"]')" || return 1
  email="$(printf '%s' "$inbox" | pick 'd["address"]')" || return 1
  python3 "$J" person-signup --practice "http://127.0.0.1:$PRACTICE_PORT" --inbox-id "$inbox_id" --email "$email" \
    --user "$BOT_USER" --password-file "$WORK/bot-password" || return 1
  python3 "$J" person-2fa --practice "http://127.0.0.1:$PRACTICE_PORT" --user "$BOT_USER" \
    --password-file "$WORK/bot-password" --secret-out "$WORK/bot-secret" >/dev/null || return 1
  verdict "practice: the bot's account is signed up, with two-step sign-in switched on" 0
}

# The bot opens a login card on codex; the person saves the password and the 2FA secret on it.
step_login() {
  local card
  switch_model codex gpt-6-astra || { say "$SWITCH_WHY"; return 1; }
  hturn request request || { say "$TURN_WHY"; chrome_diagnose; return 1; }
  card="$(python3 "$J" field "$LAST_RESULT" card)" || return 1
  [ -n "$card" ] || { say "the bot opened no login card"; return 1; }
  python3 "$J" save-card "$card" --username "$BOT_USER" --password-file "$WORK/bot-password" \
    --totp-file "$WORK/bot-secret" >/dev/null || { say "the person could not save the login card"; return 1; }
  verdict "creds: the person saved the bot's login with a password and a 2FA secret" 0
}

# The run's Chrome answers from inside a turn, and the hub CLI reports it running.
step_status() {
  hturn status status || { say "$TURN_WHY"; chrome_diagnose; return 1; }
  python3 "$J" report "$LAST_RESULT"
  verdict "chrome: the run's endpoint answers and Chrome is running" $?
}

rows() {  # every harness the runner supports, as the bot's harness and model; the gateway is a gap, listed last
  run_row codex codex gpt-6-astra devtools
  run_row claude-code claude-code claude-opus-5-5 devtools
  run_row muse-code muse-code muse-spark-1.3 devtools
  run_row gemini-cli gemini-cli gemini-3.8-flash none
  run_row grok grok grok-4.6 none
  run_row cursor-agent cursor-agent cursor-auto none
  run_row pi pi deepseek-v4.1-flash none
  run_row antigravity antigravity gemini-3.8-flash none
  python3 - "$WORK/rows/native.json" <<'PY'
import json, sys
json.dump({"name": "native", "harness": "native", "model": "tico (Gateway)", "turn": False, "gap": True,
           "why": "a Gateway turn runs on the server with no runner computer, so the probe cannot start there; its hub "
                  "tools come from the server's tool table (clients/hubtools.py BOTS)",
           "real_cli": "n/a"}, open(sys.argv[1], "w"))
PY
}

collect() {  # what the runs left on the computer and the server, copied into the work folder for the leak scan
  mkdir -p "$WORK/all-dumps" "$WORK/all-results"
  docker cp "$RUNNER:$HARNESS_ROOT/dumps/." "$WORK/all-dumps/" 2>/dev/null
  docker cp "$RUNNER:$HARNESS_RESULTS/." "$WORK/all-results/" 2>/dev/null
  docker exec "$RUNNER" sh -c "cat $HARNESS_ROOT/fake-cli.log 2>/dev/null" > "$WORK/fake-cli.log" 2>/dev/null
  docker logs "$RUNNER" > "$WORK/runner.log" 2>&1
  dc logs --no-log-prefix server > "$WORK/server.log" 2>&1
  dc logs --no-log-prefix practice > "$WORK/practice.log" 2>&1
  japi GET "/api/v2/conversations?bot=$BOT" > "$WORK/transcript.txt" 2>&1
  dc exec -T server python -I -c 'import sqlite3; c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True); print("\n".join(c.iterdump()))' \
    > "$WORK/db.sql" 2>/dev/null
  cat "$WORK"/all-dumps/* "$WORK"/all-results/* "$WORK/fake-cli.log" > "$WORK/runs.txt" 2>/dev/null
  [ -s "$WORK/db.sql" ] || { say "the database dump is empty: the scan covers the other files"; return 1; }
}

scan_secrets() {  # the bot's password, its 2FA secret and every code the secret made since the run began, in every file
  python3 "$J" needles --password-file "$WORK/bot-password" --secret-file "$WORK/bot-secret" --start "$START_EPOCH" \
    --out "$WORK/needles.json" >/dev/null || return 1
  local out rc=0 verdict_text
  out="$(python3 "$J" leakscan --needles "$WORK/needles.json" --source "runs=$WORK/runs.txt" --source "runner=$WORK/runner.log" \
    --source "server=$WORK/server.log" --source "practice=$WORK/practice.log" --source "db=$WORK/db.sql" \
    --source "transcript=$WORK/transcript.txt" --source "fake=$WORK/fake-cli.log" --source "run=$WORK/run.log" 2>&1)" || rc=$?
  printf '%s\n' "$out" | sed 's/^/  /'
  case $rc in
    0) verdict_text=clean ;;
    2) verdict_text="not run" ;;
    *) verdict_text=LEAKED ;;
  esac
  python3 -c 'import json,sys; json.dump({"verdict": sys.argv[1], "failed": sys.argv[2] != "0"}, open(sys.argv[3], "w"))' \
    "$verdict_text" "$rc" "$WORK/secrets.json"
  verdict "secrets: no password, 2FA secret or code in any run file, log, database row or transcript ($verdict_text)" "$rc"
}

echo "harness: run $RUN (containers end in -$RUN)"
run_step "install: images built from this checkout, server healthy, inbox and practice up" main step_install
run_step "practice site and inbox stub reach each other" main step_reach
run_step "runner image: which real CLIs it ships" main step_real_clis
run_step "enroll: runner online with the stand-in CLIs; every provider the harnesses need enabled" main step_enroll_harness
run_step "bot: active on the computer with the chrome tool" "runner online" step_bot_harness
run_step "practice: the bot's account signed up, two-step sign-in on" "bot" step_practice_account
run_step "login: the bot opens a card; the person saves a password and a 2FA secret" "account, chrome tool" step_login
run_step "status: the run's endpoint answers and Chrome runs" "login" step_status
run_step "rows: one turn per harness, each through its own CLI" main rows
run_step "collect: the runs' dumps, results and logs" main collect
run_step "secrets: scanned across every file the runs wrote" main scan_secrets

if [ -n "$BLOCKED" ]; then say "stopped: $BLOCKED"; fi
echo ""
echo "matrix (one row per harness; the last line counts the failures)"
python3 "$REPO/e2e/browser/harness/report.py" "$WORK"
MATRIX=$?
browser_table harness
SETUP=$?
TOTAL=$((SETUP + MATRIX))
[ "$TOTAL" -gt 125 ] && TOTAL=125
echo "harness: $TOTAL failure(s): $SETUP setup step(s), $MATRIX matrix check(s)"
exit "$TOTAL"
