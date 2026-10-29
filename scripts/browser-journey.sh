#!/usr/bin/env bash
# The browser-login journey, on demand: a maintainer runs it on a machine with Docker. It is not part of CI.
#
#   scripts/browser-journey.sh          build this checkout, run the scripted steps, print a pass/fail table
#   scripts/browser-journey.sh --keep   leave the stack running afterwards
#
# The stack and the setup steps live in scripts/browser-lib.sh (the scenarios use them too): one throwaway Docker project
# (tico-browser-<run id>) with the server built from this checkout with auth none, the practice login site, a fake Inbox
# and a runner enrolled with TICO_SCREEN=1. The bot's steps run inside its own turns through e2e/browser/fake-codex.py, so
# no model runs. A secret reaches a page only through `hub browser fill`; the leak check at the end looks for the secrets
# everywhere they could have been written.
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
. "$REPO/scripts/browser-lib.sh"
browser_init

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
  PHASE_ON=task bot_phase needs-you || return 1
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

browser_table journey
exit $?
