#!/usr/bin/env bash
# The login shapes a bot meets, on demand: a maintainer runs it on a machine with Docker. It is not part of CI.
#
#   scripts/browser-sites.sh          run the login shapes, print a pass/fail table
#   scripts/browser-sites.sh --keep   leave the stack running afterwards
#
# It starts the stack the other browser scripts start (scripts/browser-lib.sh), with two more hostnames on the practice
# site for single sign-on, and runs one bot through each shape: a two-page login, React-style controlled inputs, a login
# inside an iframe and inside an open shadow root, single sign-on across two hostnames, a popup window, a magic link from the
# bot's inbox, an 8-digit SHA256 code on a 60-second period, passwords with unicode and 128 characters, a password changed
# through the card while Chrome runs, a revoked grant, a cookie banner, a file upload and a download, and a chrome tool taken
# out of bot.yaml. Every step is scripted, so no model runs. The exit code is the number of failed steps.
#
# Passwords and 2FA keys are written to files in the work folder and read from there, so the leak check can look for them.

set -u -o pipefail

cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
. "$REPO/scripts/browser-lib.sh"
browser_init
export PRACTICE_PORT PRACTICE_ADMIN_TOKEN

BOT="${TICO_SITES_BOT:-sites-bot}"
BOT_NAME="Sites bot"
SETUP="$REPO/e2e/browser/sites_setup.py"
CARD_LOGIN=Sites-login
CARD_SSO=Sites-sso
CARD_SSO_APP=Sites-sso-app
CARD_TOTP=Sites-totp
CARD_UNICODE=Sites-unicode
CARD_LONG=Sites-long
CARD_ROTATE=Sites-rotate
USER_SITES=sites-user
USER_TOTP=sites-totp
USER_UNICODE=sites-unicode
USER_LONG=sites-long
USER_ROTATE=sites-rotate

secret_file() {  # the file's name in the work folder, then its kind: plain, unicode, long or base32
  python3 "$SETUP" secret "$WORK/$1" "$2"
}

account() {  # the practice account's username, then its password file's name
  python3 "$SETUP" admin-user "$1" "$WORK/$2"
}

card() {  # the card's name, its username, its password file's name, then the sites it covers; TOTP_SECRET_FILE adds a 2FA key
  local name=$1 user=$2 pass=$3 cid
  shift 3
  local args=("$WORK/card-$name.json" "$name" "$user" "$WORK/$pass")
  if [ -n "${TOTP_SECRET_FILE:-}" ]; then args+=(--totp-secret "$WORK/$TOTP_SECRET_FILE"); fi
  python3 "$SETUP" item "${args[@]}" "$@" >/dev/null || return 1
  python3 "$J" import-logins "$WORK/card-$name.json" > "$WORK/import-$name.json" || return 1
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["imported"] == 1 and d["errors"] == 0 else 1)' \
    "$WORK/import-$name.json" || { say "the card $name did not import"; return 1; }
  japi GET /api/v2/credentials > "$WORK/cards.json" || return 1
  cid="$(python3 "$SETUP" card-id "$name" < "$WORK/cards.json")" || { say "the card $name is not listed"; return 1; }
  send POST "/api/v2/credentials/$cid/grants" "{\"subject\": \"bot:$BOT\"}" >/dev/null ||
    { say "the card $name was not granted to $BOT"; return 1; }
}

sv_setup() {  # the practice accounts, the login cards, each granted to the bot, and the two hostnames resolving on the computer
  docker exec "$RUNNER" python3 -c 'import socket; socket.gethostbyname("app.practice.test"); socket.gethostbyname("accounts.practice.test")' >/dev/null 2>&1
  verdict "the computer resolves app.practice.test and accounts.practice.test" $? || return 1
  secret_file pw-sites plain && secret_file pw-totp plain && secret_file pw-unicode unicode && secret_file pw-long long &&
    secret_file pw-rotate plain && secret_file totp.secret base32 || return 1
  account "$USER_SITES" pw-sites || return 1
  python3 "$SETUP" admin-totp "$USER_TOTP" "$WORK/pw-totp" "$WORK/totp.secret" || return 1
  account "$USER_UNICODE" pw-unicode || return 1
  account "$USER_LONG" pw-long || return 1
  account "$USER_ROTATE" pw-rotate || return 1
  card "$CARD_LOGIN" "$USER_SITES" pw-sites login.practice.test || return 1
  card "$CARD_SSO" "$USER_SITES" pw-sites practice.test || return 1
  card "$CARD_SSO_APP" "$USER_SITES" pw-sites app.practice.test || return 1
  TOTP_SECRET_FILE=totp.secret card "$CARD_TOTP" "$USER_TOTP" pw-totp login.practice.test || return 1
  card "$CARD_UNICODE" "$USER_UNICODE" pw-unicode login.practice.test || return 1
  card "$CARD_LONG" "$USER_LONG" pw-long login.practice.test || return 1
  card "$CARD_ROTATE" "$USER_ROTATE" pw-rotate login.practice.test || return 1
}

sv_shape() {  # one login shape: the shape's name, then the KEY=value pairs its phase needs (the cards)
  local name=$1
  shift
  browser_phase variant "VARIANT=$name" "$@"
}

sv_two_page() { sv_shape two-page "CRED=$CARD_LOGIN"; }
sv_react() { sv_shape react "CRED=$CARD_LOGIN"; }
sv_frame() { sv_shape frame "CRED=$CARD_LOGIN"; }
sv_shadow() { sv_shape shadow "CRED=$CARD_LOGIN"; }
sv_sso() { sv_shape sso "CRED=$CARD_SSO" "CRED_OTHER=$CARD_SSO_APP"; }
sv_popup() { sv_shape popup "CRED=$CARD_LOGIN"; }
sv_magic() { sv_shape magic; }
sv_totp() { sv_shape totp "CRED=$CARD_TOTP"; }
sv_unicode() { sv_shape unicode "CRED=$CARD_UNICODE" "CRED_OTHER=$CARD_LONG"; }
sv_consent() { sv_shape consent "CRED=$CARD_LOGIN"; }

sv_files() {  # the download must be in this bot's Downloads folder, and nowhere else on the computer
  browser_phase variant VARIANT=files || return 1
  local found
  found="$(docker exec "$RUNNER" sh -c 'find /home/runner -name practice-report.txt -type f 2>/dev/null')"
  say "copies of the download on the computer:"
  printf '%s\n' "$found" | sed 's/^/    /'
  [ "$(printf '%s\n' "$found" | grep -c .)" = 1 ] && printf '%s\n' "$found" | grep -q "/browsers/$BOT/Downloads/practice-report.txt\$"
  verdict "download: exactly one copy, in $BOT's Downloads folder" $?
}

sv_rotate() {  # the card's password changes while Chrome runs: the bot signs in with the first, then with the card's new one
  sv_shape rotate "CRED=$CARD_ROTATE" || return 1
  secret_file pw-rotate-2 plain || return 1
  python3 "$SETUP" admin-password "$USER_ROTATE" "$WORK/pw-rotate-2" || { say "the practice password was not changed"; return 1; }
  python3 "$SETUP" card-body "$WORK/card-rotate-2.json" "$CARD_ROTATE" "$USER_ROTATE" "$WORK/pw-rotate-2" || return 1
  local cid
  japi GET /api/v2/credentials > "$WORK/cards.json" || return 1
  cid="$(python3 "$SETUP" card-id "$CARD_ROTATE" < "$WORK/cards.json")" || return 1
  send POST "/api/v2/credentials/$cid" "$(cat "$WORK/card-rotate-2.json")" >/dev/null || { say "the card's password was not changed"; return 1; }
  expire_sessions || return 1
  sv_shape rotate "CRED=$CARD_ROTATE"
}

sv_revoked() {  # the grant the card gave the bot is revoked: the next fill is refused, with no stale copy used
  local pair cid gid
  japi GET /api/v2/credentials > "$WORK/cards.json" || return 1
  pair="$(python3 "$SETUP" bot-grant "$CARD_ROTATE" "$BOT" < "$WORK/cards.json")" || { say "$BOT has no grant of $CARD_ROTATE to revoke"; return 1; }
  read -r cid gid <<<"$pair"
  send POST "/api/v2/credentials/$cid/grants/$gid/revoke" '{}' >/dev/null || { say "the grant was not revoked"; return 1; }
  sv_shape refused "CRED=$CARD_ROTATE"
}

chrome_gone() { ! chrome_pid >/dev/null; }

sv_removed() {  # the chrome tool leaves bot.yaml: Chrome stops, the profile stays, and it runs again when the tool is back
  chrome_pid >/dev/null || { say "Chrome is not running before the tool is removed"; return 1; }
  docker exec -i -e BOT="$BOT" "$RUNNER" sh -s <<'SH' || { say "bot.yaml could not be edited"; return 1; }
f=$(find /home/runner -path "*/bot-$BOT/bot.yaml" -not -path '*/node_modules/*' -not -path '*/worktrees/*' 2>/dev/null | head -n 1)
[ -n "$f" ] || exit 1
sed -i '/^  - service: chrome$/d; s/^tools:$/tools: []/' "$f"
! grep -q '^  - service: chrome$' "$f"
SH
  retry 120 chrome_gone
  verdict "Chrome stops once the chrome tool is out of bot.yaml" $? || return 1
  docker exec "$RUNNER" test -d "/home/runner/browsers/$BOT/profile"
  verdict "the profile is kept on the computer" $? || return 1
  [ "$(cookie_rows)" -gt 0 ] 2>/dev/null
  verdict "the profile still holds its cookies" $? || return 1
  attach_chrome_tool "$BOT" >/dev/null || { say "the chrome tool was not put back"; return 1; }
  retry 180 chrome_pid
  verdict "Chrome runs again once the tool is back" $? || return 1
  browser_phase status
}

# The leak check over the login shapes: every source the run wrote, and every password and 2FA key the shapes made.
sv_leaks() {
  dc exec -T server python -I -c 'import sqlite3; c = sqlite3.connect("file:/data/hub.sqlite?mode=ro", uri=True); print("\n".join(c.iterdump()))' \
    > "$WORK/db.sql" 2>/dev/null
  dc logs --no-log-prefix server inbox > "$WORK/server.log" 2>&1
  runner_log > "$WORK/runner.log"
  dc logs --no-log-prefix practice > "$WORK/practice.log" 2>&1
  cat "$WORK"/phase-*.json > "$WORK/phase-results.txt" 2>/dev/null
  docker exec "$RUNNER" sh -c "cat $RESULTS_DIR/*.json 2>/dev/null" > "$WORK/runner-results.txt"
  dc exec -T practice python -I -c 'import urllib.request; print(urllib.request.urlopen("http://server:8791/_deliveries", timeout=5).read().decode())' \
    > "$WORK/mail.json" 2>/dev/null
  [ -s "$WORK/db.sql" ] && [ -s "$WORK/server.log" ] && [ -s "$WORK/practice.log" ] && [ -s "$WORK/mail.json" ] &&
    [ -s "$WORK/runner-results.txt" ]
  verdict "leak check read every source" $? || return 1
  local files=()
  for f in "$WORK"/pw-* "$WORK/totp.secret"; do files+=(--password-file "$f"); done
  python3 "$J" needles "${files[@]}" --password-file "$WORK/person-password" --password-file "$WORK/bot-password" \
    --start "$START_EPOCH" --mail "$WORK/mail.json" --out "$WORK/needles.json" >/dev/null || return 1
  python3 "$J" leakscan --needles "$WORK/needles.json" --source "db=$WORK/db.sql" --source "events=$WORK/server.log" \
    --source "runner=$WORK/runner.log" --source "practice=$WORK/practice.log" \
    --source "results=$WORK/runner-results.txt" --source "phases=$WORK/phase-results.txt" --source "run=$WORK/run.log"
  verdict "leak check: no password or 2FA key in any source" $?
}

printf 'sites: run %s, Docker names end in -%s\n' "$RUN" "$RUN"
run_step "install: images built from this checkout, server healthy, inbox and practice up" main step_install
run_step "practice site and inbox stub reach each other" main step_reach
run_step "runner enrolled, online, OpenAI enabled, Chrome in the runner's zone" main step_enroll
run_step "bot created, on the computer, active, with the chrome tool" "runner: chrome tool" step_bot
run_step "bot: Chrome runs for the bot and answers its status" "runner: chrome, hub browser" step_chrome
run_step "accounts and login cards made, each granted to the bot" "practice site, cards" sv_setup
run_step "a: two-page login, the username page, then the password page on its own URL" "chrome, cards, fill" sv_two_page
run_step "b: React-style controlled inputs, the fill reaches the page's state" "chrome, cards, fill" sv_react
run_step "c: login in a same-site iframe" "chrome, cards, fill" sv_frame
run_step "c: login in an open shadow root" "chrome, cards, fill" sv_shadow
run_step "d: single sign-on app.practice.test to accounts.practice.test and back; app-only login refused (site_mismatch)" "chrome, cards, fill" sv_sso
run_step "e: login in a popup window, filled by its target id" "chrome, cards, fill" sv_popup
run_step "f: magic link, the email read from the bot's inbox, the link opens signed in" "chrome, inbound" sv_magic
run_step "g: 8-digit SHA256 60-second code; code asked 1.5 s early accepted; reused code refused; next period accepted" "chrome, cards, totp" sv_totp
run_step "h: password with unicode, quotes, backslash, emoji and 128 characters typed exactly" "chrome, cards, fill" sv_unicode
run_step "i: password changed through the card while Chrome runs; the next fill uses the new one" "chrome, cards, fill" sv_rotate
run_step "i: grant revoked; the next fill is refused (not_granted)" "chrome, cards, fill" sv_revoked
run_step "j: cookie banner covers the form; the bot dismisses it with a click, then fills" "chrome, cards, fill" sv_consent
run_step "k: file upload through CDP; download lands in the bot's own Downloads folder" "chrome, files" sv_files
run_step "l: chrome tool taken out of bot.yaml: Chrome stops, profile kept, runs again when the tool is back" "chrome, computer" sv_removed
run_step "leak check across the login shapes" "main" sv_leaks

browser_table sites
exit $?
