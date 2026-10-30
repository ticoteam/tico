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
chrome_pid() {
  local lock pid
  lock="$(docker exec "$RUNNER" readlink "/home/runner/browsers/$BOT/profile/SingletonLock" 2>/dev/null)" || return 1
  pid="${lock##*-}"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  docker exec "$RUNNER" sh -c "tr '\\0' ' ' < /proc/$pid/cmdline" | grep -q -- "--user-data-dir=/home/runner/browsers/$BOT/profile" || return 1
  printf '%s\n' "$pid"
}

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

# Diagnostics, read only: the cookies the bot's Chrome profile holds on disk for the practice host (names and expiry).
profile_cookies() {  # a label
  say "profile cookies ($1):"
  docker exec -i "$RUNNER" python3 -I - "/home/runner/browsers/$BOT/profile" <<'PY' 2>&1 | sed 's/^/    /' || say "profile cookies: could not be read"
import os, shutil, sqlite3, sys, tempfile
src = os.path.join(sys.argv[1], "Default", "Cookies")
if not os.path.exists(src):
    print("no Cookies file at " + src)
    sys.exit(0)
tmp = tempfile.mkdtemp()
shutil.copy(src, os.path.join(tmp, "Cookies"))
for extra in ("-journal", "-wal"):
    if os.path.exists(src + extra):
        shutil.copy(src + extra, os.path.join(tmp, "Cookies" + extra))
c = sqlite3.connect(os.path.join(tmp, "Cookies"))
rows = c.execute("SELECT host_key, name, expires_utc, is_persistent FROM cookies WHERE host_key LIKE '%practice.test%'").fetchall()
print(f"{len(rows)} cookie(s) for the practice host on disk")
for host, name, expires, persistent in rows:
    print(f"{host} {name} expires_utc={expires} persistent={persistent}")
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
  verdict "proxy: the proxy logged the practice site's host" $?
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

# Scenario 5: Chrome is killed by its exact PID; the runner starts it again with the same profile.
sc_crash() {  # the round, for the log
  local pid
  profile_cookies "before the kill, round ${1:-1}"
  pid="$(chrome_pid)" || { say "no Chrome for $BOT is running under its profile lock"; return 1; }
  docker exec "$RUNNER" sh -c "kill -KILL $pid" || { say "Chrome $pid could not be killed"; return 1; }
  sleep 2
  if docker exec "$RUNNER" test -d "/proc/$pid"; then say "Chrome $pid is still running after KILL"; return 1; fi
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
      profile_cookies "after the restart, round $round"
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
sc_captcha() {
  # The check's card waits on a person, so the phase runs on a task of the bot's, as the journey's needs-you does.
  grant_trail "before S7"
  PHASE_ON=task browser_phase challenge || { grant_trail "after S7's sign-in failed"; return 1; }
  local rid card id task key out="$WORK/captcha-person.json" person
  rid="$(runner_id)" || return 1
  send POST "/api/v2/runners/$rid/screen" '{"mode": "view"}' >/dev/null || { say "the screen could not be turned on"; return 1; }
  node "$REPO/e2e/browser/captcha-person.mjs" "http://127.0.0.1:$TICO_PORT" "$rid" > "$out" 2>"$WORK/captcha.err"
  person=$?
  send POST "/api/v2/runners/$rid/screen" '{"mode": "off"}' >/dev/null
  python3 "$J" report "$out"
  verdict "captcha: the person held the bot's screen for the check" "$person" || return 1
  card="$(python3 "$J" reconnect-id "$HOST_NAME")" || return 1
  [ -n "$card" ] || { say "no open reconnect for $HOST_NAME"; return 1; }
  id="$(printf '%s' "$card" | pick 'd["id"] or "-"')"
  task="$(printf '%s' "$card" | pick 'd["task"]')"
  key="$(printf '%s' "$card" | pick 'd["key"]')"
  send POST "/api/v2/reconnects/$id/done" "{\"task\": \"$task\", \"key\": \"$key\"}" >/dev/null || { say "Done did not close the check"; return 1; }
  browser_phase challenge-check
}

# Scenario 8: two runs of the same bot as two tasks, which the bot takes at once (its chat would queue them).
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
  say "attempts of $BOT since $1 (server)"
  dc exec -T server python -I - "$1" "$BOT" <<'PY' 2>&1 | sed 's/^/    /'
import sqlite3, sys
c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True)
for r in c.execute("SELECT id, runner_id, state, created, started, finished FROM attempts WHERE bot=? AND created>=? ORDER BY created",
                   (sys.argv[2], sys.argv[1])):
    print(f"attempt {r[0][:12]} runner={r[1][:12]} state={r[2]} created={r[3]} started={r[4]} finished={r[5]}")
PY
  say "computer log about turns and claims"
  docker logs "$RUNNER" 2>&1 | grep -iE 'claim|lane|turn|attempt|busy' | tail -n 40 | sed 's/^/    /'
}

sc_concurrent() {
  local first second one two since
  since="$(date -u +%Y-%m-%dT%H:%M:%S)"
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
  { docker logs "$RUNNER" 2>&1; docker exec "$RUNNER" sh -c 'find /home/runner -name "*.log" -type f -exec cat {} + 2>/dev/null'; } > "$WORK/runner.log"
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
