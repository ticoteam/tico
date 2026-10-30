#!/usr/bin/env bash
# The browser scenarios, on demand: a maintainer runs it on a machine with Docker. It is not part of CI.
#
#   scripts/browser-scenarios.sh          run the scenarios, print a pass/fail table
#   scripts/browser-scenarios.sh --keep   leave the stack running afterwards
#
# It starts the same stack as scripts/browser-journey.sh (scripts/browser-lib.sh), then pushes one bot's browser harder:
# an SMS code from a phone rental, a forward proxy with the runner's time zone and language, a browser fingerprint, a
# second bot on the same computer, a killed Chrome, Playwright and chrome-devtools-mcp on the endpoint, a captcha a person
# holds through the computer screen, two runs of one bot at once, and the leak check across all of it. Every step is
# scripted, so no model runs. The exit code is the number of failed steps.
#
# The steps that drive Chrome fail until the bot's Chrome is on main; a step that needs Chrome says so in its NEEDS column.
# Scenario 1 needs the phone rental on main; scenario 7 needs the screen and the reconnect card.

set -u -o pipefail

cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
. "$REPO/scripts/browser-lib.sh"
browser_init

BOT="${TICO_SCENARIO_BOT:-scenario-bot}"
BOT_NAME="Scenario bot"
PEER_BOT="scenario-peer"
PEER_NAME="Scenario peer"
PROXY_URL=http://proxy:3128
ZONE=Europe/Berlin
LOCALE=de-DE
# The runner's Chrome goes through the proxy and reports the time zone and language above.
RUNNER_ENV=(-e "TICO_BROWSER_PROXY=$PROXY_URL" -e "TICO_BROWSER_TZ=$ZONE" -e "TICO_BROWSER_LANG=$LOCALE")
EXTRA_COMPOSE="$DIR/compose.scenarios.yaml"
cat > "$EXTRA_COMPOSE" <<EOF
services:
  proxy:
    image: $SERVER_IMAGE:$TAG
    pull_policy: never
    entrypoint: ["python", "-I"]
    command: ["/proxy/server.py"]
    depends_on: [server]
    volumes:
      - "$REPO/e2e/proxy:/proxy:ro"
EOF

step_proxy_up() {
  dc up -d proxy >/dev/null 2>&1 || { BLOCKED="the proxy did not start"; say "$BLOCKED"; return 1; }
  retry 60 dc exec -T proxy python -I -c 'import socket; socket.create_connection(("login.practice.test", 8080), timeout=3)'
  verdict "proxy: started, and it reaches the practice site" $?
}

# The PID of the bot's Chrome: the profile's lock names it as host-PID, and the process must be running with this profile.
# Diagnostics, read only: the bot's login, its grants, and every event about them, as the server's database has them.
grant_trail() {  # a label: says what the server recorded about the login's grants at this point
  say "grants ($1):"
  dc exec -T server python -I - "$LABEL" <<'PY' 2>&1 | sed 's/^/    /' || say "grants: the database could not be read"
import sqlite3, sys
c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True)
c.row_factory = sqlite3.Row
creds = c.execute("SELECT id, name, revision, updated FROM credentials WHERE name=? ORDER BY created", (sys.argv[1],)).fetchall()
if not creds:
    print("no credential named " + sys.argv[1])
for cr in creds:
    print(f"credential {cr['id']} revision={cr['revision']} updated={cr['updated']}")
    grants = c.execute("SELECT id, subject, granted_by, created, revoked, revoked_by FROM credential_grants "
                       "WHERE credential_id=? ORDER BY created", (cr["id"],)).fetchall()
    for g in grants:
        print(f"  grant {g['id']} subject={g['subject']} granted_by={g['granted_by']} created={g['created']} "
              f"revoked={g['revoked']} revoked_by={g['revoked_by']}")
    ids = [cr["id"]] + [g["id"] for g in grants]
    marks = ",".join("?" * len(ids))
    for e in c.execute(f"SELECT ts, actor, action, target, detail_json FROM events WHERE target IN ({marks}) ORDER BY ts", ids):
        print(f"  event {e['ts']} {e['action']} actor={e['actor']} target={e['target'][:14]} {(e['detail_json'] or '')[:160]}")
PY
}

# Scenario 1: the owner rents a phone (TICO approves the quote, the owner accepts it), and the rental is an SMS inbox.
sms_inbox() { japi GET /api/v2/inbound/inboxes | pick 'sys.exit(0 if any(i.get("kind") == "sms" for i in d["inboxes"]) else 1)'; }

sc_phone() {
  local request rid price
  request="$(send POST /api/v2/inbound/phone-requests '{"name": "Practice phone"}')" || { say "the phone request was refused"; return 1; }
  rid="$(printf '%s' "$request" | pick 'd["phone_request"]["request_id"]')" || return 1
  # TICO's approval of the quote is a test hook on the stub: the stub stands in for TICO, and the owner's accept follows.
  dc exec -T practice python -I -c "import json, sys, urllib.request as r; r.urlopen(r.Request('http://server:8791/_approve', data=json.dumps({'request_id': sys.argv[1]}).encode(), headers={'Content-Type': 'application/json'}, method='POST'), timeout=5).read()" "$rid" >/dev/null 2>&1 ||
    { say "TICO's approval of the quote did not take"; return 1; }
  price="$(japi GET /api/v2/inbound/phone-quote | pick 'd["price_micros"]')" || return 1
  send POST "/api/v2/inbound/phone-requests/$rid/accept" "{\"price_micros\": $price}" >/dev/null || { say "the owner's accept was refused"; return 1; }
  retry 180 sms_inbox || { say "no SMS inbox appeared for the rental within 180 seconds"; return 1; }
  verdict "phone: approved and accepted, and the rental is listed as an SMS inbox" 0 || return 1
  browser_phase phone
}

sc_proxy() {
  browser_phase proxy "EXPECT_TZ=$ZONE" "EXPECT_LANG=$LOCALE" || return 1
  dc logs --no-log-prefix proxy 2>/dev/null | grep -Eq '^(GET|CONNECT) login\.practice\.test '
  local rc=$?
  # The proxy's lines about the practice site, when the check fails: the page loaded, so these say how it was reached.
  [ "$rc" = 0 ] || { say "proxy log, last lines (hosts only):"; dc logs --no-log-prefix proxy 2>/dev/null | tail -n 12 | sed 's/^/    /'; }
  verdict "proxy: the proxy logged the practice site's host" "$rc"
}

sc_fingerprint() { browser_phase fingerprint; }

# Scenario 4: a second bot on the same computer. The first bot reads the second one's port and profile from the second
# bot's result, then tries that endpoint, and tries the second bot's login.
sc_peers() {
  grant_trail "before S4"
  make_bot "$PEER_BOT" "$PEER_NAME" || return 1
  PHASE_BOT="$PEER_BOT" browser_phase status || return 1
  PHASE_BOT="$PEER_BOT" bot_phase peer-setup || return 1
  local port profile
  port="$(python3 "$J" field "$LAST_PHASE" port)" || return 1
  profile="$(python3 "$J" field "$LAST_PHASE" profile)" || return 1
  [ -n "$port" ] || { say "the peer reported no port"; return 1; }
  browser_phase peer-probe "PEER_PORT=$port" "PEER_PROFILE=$profile"
}

# The computer's log: the container's output and the log files the runner writes under its home.
# Diagnostics, read only: the website logins the runner's list holds for the bot (runner-login-credentials filters the same
# way): names and ids, never the values.
login_names() {
  say "logins granted to $BOT (names and ids as the database has them):"
  dc exec -T server python -I - "bot:$BOT" <<'PY' 2>&1 | sed 's/^/    /' || say "logins: the database could not be read"
import sqlite3, sys
c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True)
rows = c.execute("SELECT c.id, c.name FROM credentials c WHERE c.kind='login' AND c.ciphertext IS NOT NULL AND EXISTS "
                 "(SELECT 1 FROM credential_grants g WHERE g.credential_id=c.id AND g.subject=? AND g.revoked IS NULL) "
                 "ORDER BY c.id", (sys.argv[1],)).fetchall()
for r in rows:
    print(f"login {r[0]} name={r[1]!r}")
if not rows:
    print("no active login grant for this bot")
PY
}

# Epoch seconds of the last sign-in the practice site completed before the cut-off (epoch seconds, $1). A sign-in is a POST
# to /login or /login/2fa that got a 303 and is followed by a GET /me that got a 200; the log lines carry Docker's timestamps.
last_sign_in() {
  dc logs -t practice 2>&1 | python3 -c '
import calendar, re, sys, time
cut, pending, session = float(sys.argv[1]), None, None
for line in sys.stdin:
    m = re.search(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z (POST|GET) (\S+) (\d{3})", line)
    if not m:
        continue
    at = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) + float("0." + (m.group(2) or "0")[:9])
    if at > cut:
        break
    if m.group(3) == "POST" and m.group(4) in ("/login", "/login/2fa") and m.group(5) == "303":
        pending = at
    elif m.group(3) == "GET" and m.group(4) == "/me" and m.group(5) == "200" and pending is not None:
        session, pending = pending, None
if session is not None:
    print(f"{session:.3f}")
' "$1"
}

# Scenario 5: Chrome is killed by its exact PID; the runner starts it again with the same profile.
# The cookies the profile's database holds, in all of them: Chrome writes cookies on a timer, so a kill soon after a sign-in
# can find none on disk. Read plainly, not immutable, so a commit in progress is seen.

sc_crash() {  # the round, for the log
  local pid killed signed gap rows waited=0
  say "profile before the kill, round ${1:-1}:"
  profile_facts
  # The round waits up to 90 seconds for the first cookie on disk, then kills; the wait is part of the log.
  while :; do
    rows="$(cookie_rows)"
    [ "${rows:-0}" -gt 0 ] 2>/dev/null && break
    [ "$waited" -ge 90 ] && break
    sleep 3
    waited=$((waited + 3))
  done
  say "round ${1:-1}: cookies on disk before the kill: ${rows:-0}, after ${waited}s of waiting"
  pid="$(chrome_pid)" || { say "no Chrome for $BOT is running under its profile lock"; return 1; }
  killed="$(python3 -c 'import time; print(f"{time.time():.3f}")')"
  docker exec "$RUNNER" sh -c "kill -KILL $pid" || { say "Chrome $pid could not be killed"; return 1; }
  sleep 2
  if docker exec "$RUNNER" test -d "/proc/$pid"; then say "Chrome $pid is still running after KILL"; return 1; fi
  signed="$(last_sign_in "$killed")"
  if [ -n "$signed" ]; then
    gap="$(python3 -c 'import sys; print(f"{float(sys.argv[1]) - float(sys.argv[2]):.1f}")' "$killed" "$signed")"
  fi
  say "round ${1:-1}: SIGKILL to pid $pid at $killed; last sign-in the practice site completed at ${signed:-none}; seconds from sign-in to kill: ${gap:-n/a}"
  verdict "crash: Chrome (pid $pid, from the profile lock) was killed" 0
}

# Three kills in one run. A round that fails is recorded, and the next round still runs, so the pattern shows.
sc_after_crash() {
  local round failed=0
  grant_trail "before S5"
  for round in 1 2 3; do
    say "crash round $round of 3"
    sc_crash "$round" || return 1
    browser_phase after-crash || {
      failed=1
      say "profile after the restart, round $round:"
      profile_facts
      say "runner log about the browser, round $round:"
      docker logs "$RUNNER" 2>&1 | grep -iE 'browser|chrome|profile|session|restore|crash' | tail -n 40 | sed 's/^/    /'
    }
  done
  return "$failed"
}

# Scenario 6: the image has no Playwright, so the repository's pinned playwright-core goes in under /opt/e2e.
step_playwright_copy() {
  docker exec "$RUNNER" mkdir -p /opt/e2e/node_modules &&
    docker cp "$REPO/node_modules/playwright-core" "$RUNNER:/opt/e2e/node_modules/"
  verdict "playwright-core: the repository's pinned copy is in the runner" $?
}

sc_attach() {
  step_playwright_copy || return 1
  browser_phase attach
}

# Scenario 7: the bot signs in and asks for a person at the check page. The person holds the bot's screen (control mode,
# 390x844), the person marks it done, and the next turn reads the check as passed.
challenge_log() {  # the practice site's lines for the check: each request to the check and each pass attempt's held_ms
  dc logs --no-log-prefix practice 2>&1 | grep -E '/challenge|^challenge ' | tail -n 30 | sed 's/^/    /'
}

sc_captcha() {
  # The check's card waits on a person, so the phase runs on a task of the bot's, as the journey's needs-you does.
  grant_trail "before S7"
  login_names
  PHASE_ON=task browser_phase challenge || { grant_trail "after S7's sign-in failed"; login_names; return 1; }
  local rid card id task key out="$WORK/captcha-person.json" person
  rid="$(runner_id)" || return 1
  # Control mode: the person's Control button exists only then, and the server grants control only in this mode.
  send POST "/api/v2/runners/$rid/screen" '{"mode": "control"}' >/dev/null || { say "the screen could not be turned on"; return 1; }
  node "$REPO/e2e/browser/captcha-person.mjs" "http://127.0.0.1:$TICO_PORT" "$rid" > "$out" 2>"$WORK/captcha.err"
  person=$?
  send POST "/api/v2/runners/$rid/screen" '{"mode": "off"}' >/dev/null
  python3 "$J" report "$out"
  verdict "captcha: the person held the bot's screen for the check" "$person" || { challenge_log; return 1; }
  card="$(python3 "$J" reconnect-id "$HOST_NAME")" || return 1
  [ -n "$card" ] || { say "no open reconnect for $HOST_NAME"; return 1; }
  id="$(printf '%s' "$card" | pick 'd["id"] or "-"')"
  task="$(printf '%s' "$card" | pick 'd["task"]')"
  key="$(printf '%s' "$card" | pick 'd["key"]')"
  send POST "/api/v2/reconnects/$id/done" "{\"task\": \"$task\", \"key\": \"$key\"}" >/dev/null || { say "Done did not close the check"; return 1; }
  browser_phase challenge-check
  local rc=$?
  say "the practice site's check lines after the person's hold and the next turn:"
  challenge_log
  return $rc
}

# Scenario 8: two runs of the same bot as two tasks, which the bot takes at once (its chat would queue them).
# A task's turn works in its own worktree only when the bot's checkout has an upstream (runner-rs/src/sessions.rs
# NO_UPSTREAM); without one both turns share the checkout and run one after the other. The upstream is a bare repository
# in the runner's folder, set as origin the way a bot's first push sets it: the runner takes a checkout whose branch
# already tracks an upstream as current and leaves its origin alone (runner-rs/src/execute.rs, the upstream check).
bot_upstream() {
  local dir
  dir="$(docker exec "$RUNNER" sh -c "find /home/runner -name AGENT.md -path '*$BOT*' 2>/dev/null | head -n 1")" || true
  [ -n "$dir" ] || { say "no checkout of $BOT on the computer"; return 1; }
  dir="$(dirname "$dir")"
  docker exec -i "$RUNNER" sh -s -- "$BOT" "$dir" <<'SH'
set -e
bot=$1 dir=$2 bare=/home/runner/upstream/$1.git
mkdir -p /home/runner/upstream
[ -d "$bare" ] || git init -q --bare "$bare"
cd "$dir"
git rev-parse --verify -q HEAD >/dev/null || git -c user.name=Tico -c user.email=scenarios@tico.invalid commit -q --allow-empty -m "scenario checkout"
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$bare"
git push -q -u origin HEAD
git rev-parse --abbrev-ref '@{u}'
SH
}

task_start() {  # the phase's name: creates one task for the bot that runs the phase, and prints the run's nonce
  local name=$1 nonce text body
  nonce="$name-$RANDOM$RANDOM"
  text="journey-run $nonce $name PRACTICE=$PRACTICE HOST=$HOST_NAME OTHER=$OTHER CREDENTIAL=$CREDENTIAL LABEL=$LABEL"
  # The title carries the nonce: the server refuses a second open task with the same title for one bot.
  body="$(python3 -c 'import json,sys; print(json.dumps({"title": sys.argv[1], "body": sys.argv[2], "owner": sys.argv[3]}))' "Scenario $nonce" "$text" "bot:$BOT")"
  send POST /api/v2/tasks "$body" >/dev/null || return 1
  printf '%s\n' "$nonce"
}

task_result() {  # a run's nonce: waits for the bot's result, copies it to the work folder, and prints its path
  local nonce=$1 out="$WORK/phase-$1.json"
  retry "$PHASE_WAIT" docker exec "$RUNNER" test -s "$RESULTS_DIR/$nonce.json" || return 1
  docker exec "$RUNNER" cat "$RESULTS_DIR/$nonce.json" > "$out" || return 1
  printf '%s\n' "$out"
}

# Why two runs of one bot did not overlap: the bot's and the computer's turn limits, the server's attempts for the bot since
# the run started, and the computer's log about turns and claims.
concurrent_diagnose() {  # the time the first task was created (UTC, to the second)
  say "turn limits: the bot's and the computer's"
  japi GET "/api/v2/bots/$BOT" | python3 -c 'import json,re,sys
print("    bot:", re.findall(r"\"[^\"]*turn[^\"]*\": ?[^,}\]]*", sys.stdin.read())[:6])' 2>&1
  japi GET /api/v2/operations | python3 -c 'import json,re,sys
m = json.load(sys.stdin)["machines"][0]
print("    computer:", re.findall(r"\"[^\"]*(?:turn|capacity)[^\"]*\": ?[^,}\]]*", json.dumps(m))[:8])' 2>&1
  say "attempts of $BOT since $1 (server): the lane each run was given, and the job and thread it ran"
  local rows ids
  rows="$(dc exec -T server python -I - "$1" "$BOT" <<'PY' 2>&1
import sqlite3, sys
c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True)
for r in c.execute("SELECT id, runner_id, lane, state, created, started, finished, job_id, thread_id FROM attempts "
                   "WHERE bot=? AND created>=? ORDER BY created", (sys.argv[2], sys.argv[1])):
    print(f"attempt {r[0]} lane={r[2]} runner={r[1][:12]} state={r[3]} created={r[4]} started={r[5]} finished={r[6]} "
          f"job={r[7]} thread={r[8]}")
PY
)"
  printf '%s\n' "$rows" | sed 's/^/    /'
  # Every id the rows name, as one pattern for the computer's log: the attempts', their jobs' and their threads'.
  ids="$(printf '%s\n' "$rows" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | sort -u | paste -sd'|' -)"
  say "computer log lines that name those ids, or a lane or a claim"
  runner_log | grep -E "${ids:-^$}|lane|claim" | tail -n 60 | sed 's/^/    /'
}

sc_concurrent() {
  local first second one two since
  since="$(date -u +%Y-%m-%dT%H:%M:%S)"
  local upstream
  upstream="$(bot_upstream)" || { say "the bot's checkout could not get an upstream"; return 1; }
  say "the bot's checkout tracks $upstream"
  first="$(task_start concurrent)" || { say "the first task was not created"; return 1; }
  second="$(task_start concurrent)" || { say "the second task was not created"; return 1; }
  one="$(task_result "$first")" || { say "the first run did not finish in $PHASE_WAIT seconds"; return 1; }
  two="$(task_result "$second")" || { say "the second run did not finish in $PHASE_WAIT seconds"; return 1; }
  LAST_PHASE="$one"
  python3 - "$one" "$two" <<'PY'
import json, sys
one, two = (json.load(open(path)) for path in sys.argv[1:3])
a, b = one.get("data", {}), two.get("data", {})
checks = [
    ("both runs passed", one.get("ok") is True and two.get("ok") is True),
    ("both runs used the same Chrome profile", bool(a.get("profile")) and a.get("profile") == b.get("profile")),
    ("the two runs overlapped in time", a.get("start", 0) < b.get("end", 0) and b.get("start", 0) < a.get("end", 0)),
]
print(f"  holds (epoch seconds): first {a.get('start')}-{a.get('end')}, second {b.get('start')}-{b.get('end')}")
for name, ok in checks:
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
PY
  local rc=$?
  [ "$rc" = 0 ] || concurrent_diagnose "$since"
  return "$rc"
}

# Scenario 9: the leak check, over everything the scenarios wrote down.
sc_leaks() {
  dc exec -T server python -I -c 'import sqlite3; c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True); print("\n".join(c.iterdump()))' \
    > "$WORK/db.sql" 2>/dev/null
  dc logs --no-log-prefix server inbox > "$WORK/server.log" 2>&1
  runner_log > "$WORK/runner.log"
  dc logs --no-log-prefix practice > "$WORK/practice.log" 2>&1
  dc logs --no-log-prefix proxy > "$WORK/proxy.log" 2>&1
  cat "$WORK"/phase-*.json > "$WORK/phase-results.txt" 2>/dev/null
  docker exec "$RUNNER" sh -c "cat $RESULTS_DIR/*.json 2>/dev/null" > "$WORK/runner-results.txt"
  dc exec -T practice python -I -c 'import urllib.request; print(urllib.request.urlopen("http://server:8791/_deliveries", timeout=5).read().decode())' \
    > "$WORK/mail.json" 2>/dev/null
  # An empty source would pass trivially, so every source must have been read.
  [ -s "$WORK/db.sql" ] && [ -s "$WORK/server.log" ] && [ -s "$WORK/practice.log" ] && [ -s "$WORK/proxy.log" ] &&
    [ -s "$WORK/mail.json" ] && [ -s "$WORK/runner-results.txt" ]
  verdict "leak check read every source" $? || return 1
  python3 "$J" needles --password-file "$WORK/person-password" --password-file "$WORK/bot-password" \
    --start "$START_EPOCH" --mail "$WORK/mail.json" --out "$WORK/needles.json" >/dev/null || return 1
  python3 "$J" leakscan --needles "$WORK/needles.json" --source "db=$WORK/db.sql" --source "events=$WORK/server.log" \
    --source "runner=$WORK/runner.log" --source "practice=$WORK/practice.log" --source "proxy=$WORK/proxy.log" \
    --source "results=$WORK/runner-results.txt" --source "phases=$WORK/phase-results.txt" --source "run=$WORK/run.log"
  verdict "leak check: no password or texted code in any source" $?
}

printf 'scenarios: run %s, Docker names end in -%s\n' "$RUN" "$RUN"
run_step "install: images built from this checkout, server healthy, inbox and practice up" main step_install
run_step "practice site and inbox stub reach each other" main step_reach
run_step "proxy: a forward proxy that carries only the practice site's names" main step_proxy_up
run_step "runner enrolled, online, OpenAI enabled, Chrome through the proxy in the runner's zone and language" main step_enroll
run_step "bot created, on the computer, active, with the chrome tool" "runner: chrome tool" step_bot
run_step "bot: Chrome runs for the bot and answers its status" "runner: chrome, hub browser" step_chrome
run_step "bot asks for a login card; person saves it" "creds: login card" step_card_first
run_step "bot signs up, code read from its own inbox, typed with hub browser fill" "chrome, creds, fill" step_signup_bot
run_step "S1 sms: the rental is an SMS inbox; the bot types the number and reads the texted code" "phone rental, inbound sync, hub inbound wait" sc_phone
run_step "S2 proxy: the page is reached through the proxy; time zone and language match the runner" "chrome, proxy" sc_proxy
run_step "S3 fingerprint: no HeadlessChrome; plugins, languages, webdriver false, a window size" "chrome" sc_fingerprint
run_step "S4 two bots: the peer endpoint and login are refused; separate profiles" "chrome, two bots" sc_peers
run_step "S5 crash: Chrome killed by its PID; the same profile is back and still signed in" "chrome, creds" sc_after_crash
run_step "S6 Playwright over CDP and chrome-devtools-mcp list_pages over stdio" "chrome, playwright-core" sc_attach
run_step "S7 captcha: the person holds the bot's screen at 390x844; Done; the next turn sees it passed" "chrome, screen, reconnects" sc_captcha
run_step "S8 two runs of one bot at once share its Chrome" "chrome, tasks" sc_concurrent
run_step "S9 leak check across every scenario" "main" sc_leaks

browser_table scenarios
exit $?
