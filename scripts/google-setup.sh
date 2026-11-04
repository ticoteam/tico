#!/usr/bin/env bash
# Sets up Google Workspace access for Tico, run in Google Cloud Shell with the command Tico shows. The Tico server
# serves this file at /api/v2/google/setup.sh with its own address and install id filled in. The script makes a
# service account in a project of your organization, lets it act for the Workspace mailboxes Tico reads, and sends
# its key to the server with the one-time setup code. It never prints the key.
#
# Usage: curl -fsSL <server>/api/v2/google/setup.sh | bash -s -- <setup code> [--project ID] [--organization ID]
set -euo pipefail
umask 077

TICO_BASE="__TICO_BASE__"
TICO_INSTALL="__TICO_INSTALL__"
# Key creation is retried for up to KEY_WAIT seconds, waiting RETRY_BASE x attempt between tries (tests set both).
RETRY_BASE="${TICO_RETRY_BASE:-2}"
KEY_WAIT="${TICO_KEY_WAIT:-120}"
KEY_POLICIES=(iam.disableServiceAccountKeyCreation iam.managed.disableServiceAccountKeyCreation)
CODE=""
ORG=""
PROJECT=""
SA=""
OWN_KEY=""
CALLER=""
ADDED_ROLE=0
TMP=""

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
first_line() { head -n 1 "$1" 2>/dev/null | tr -d '\r' || true; }

# Asks on the terminal and leaves the answer in ANSWER. The script arrives on stdin from curl, so answers come from
# /dev/tty. It is not run in a subshell, so a failure here stops the whole run.
ask() {
  local answer=""
  printf '%s' "$1" > /dev/tty 2>/dev/null || die "This setup needs a terminal to answer questions. Run it in Cloud Shell."
  IFS= read -r answer < /dev/tty || die "No answer was given."
  ANSWER="$answer"
}

member() {
  if [[ "$CALLER" == *.gserviceaccount.com ]]; then printf 'serviceAccount:%s' "$CALLER"; else printf 'user:%s' "$CALLER"; fi
}

# Removes the policy-admin role this run added, and nothing else, however the run ends.
cleanup() {
  local status=$?
  if [[ "$ADDED_ROLE" == 1 ]]; then
    if gcloud organizations remove-iam-policy-binding "$ORG" --member="$(member)" --role=roles/orgpolicy.policyAdmin \
      --quiet </dev/null >/dev/null 2>&1; then
      say "Removed the temporary policy-admin role"
    else
      say "Could not remove the temporary policy-admin role. Remove roles/orgpolicy.policyAdmin for $CALLER on the organization." >&2
    fi
  fi
  if [[ -n "$TMP" ]]; then rm -rf "$TMP"; fi
  exit "$status"
}

parse_args() {
  CODE="${1:-}"
  if [[ $# -gt 0 ]]; then shift; fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project)
        [[ $# -ge 2 ]] || die "--project needs a project ID."
        PROJECT="$2"
        shift 2
        ;;
      --organization)
        [[ $# -ge 2 ]] || die "--organization needs an ID."
        ORG="${2#organizations/}"
        shift 2
        ;;
      *) die "Unknown option: $1" ;;
    esac
  done
  [[ -n "$CODE" ]] || die "The setup code is missing. Copy the whole command from Tico."
  [[ "$CODE" =~ ^[A-Za-z0-9_-]+$ ]] || die "The setup code isn't valid. Copy the whole command from Tico."
  if [[ -n "$PROJECT" && ! "$PROJECT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then die "The project ID isn't valid."; fi
  if [[ -n "$ORG" && ! "$ORG" =~ ^[0-9]+$ ]]; then die "The organization ID isn't valid."; fi
}

check_config() {
  [[ "$TICO_BASE" =~ ^https?:// ]] || die "This copy of the script has no server address. Get the command again from Tico."
  [[ "$TICO_INSTALL" =~ ^[a-z0-9_-]{1,63}$ ]] || die "This copy of the script has no install id. Get the command again from Tico."
}

need_tools() {
  local tool
  for tool in gcloud curl python3; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool isn't available here. Run this in Google Cloud Shell."
  done
}

choose_org() {
  if [[ -n "$ORG" ]]; then say "Organization: $ORG"; return; fi
  local listing="" names="" line i choice
  local -a org_list=()
  if ! listing=$(gcloud organizations list --format='value(name)' </dev/null 2>"$TMP/orgs.err"); then
    die "Could not list organizations. $(first_line "$TMP/orgs.err")"
  fi
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then names="$names $line"; fi
  done <<< "$listing"
  read -r -a org_list <<< "$names"
  if (( ${#org_list[@]} == 0 )); then
    say "No organization. Internal sign-in won't be available, only External."
    return
  fi
  if (( ${#org_list[@]} == 1 )); then
    ORG="${org_list[0]}"
  else
    for ((i = 0; i < ${#org_list[@]}; i++)); do say "  $((i + 1))) ${org_list[$i]}"; done
    ask "Choose the organization for the Tico project [1-${#org_list[@]}]: "
    choice="$ANSWER"
    if [[ ! "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#org_list[@]} )); then
      die "That isn't one of the organizations listed."
    fi
    ORG="${org_list[$((choice - 1))]}"
  fi
  say "Organization: $ORG"
}

# The project labeled for this Tico install is reused, so a run that stopped halfway resumes where it was.
choose_project() {
  if [[ -n "$PROJECT" ]]; then say "Project: $PROJECT"; return; fi
  local found="" suffix=""
  if ! found=$(gcloud projects list --filter="labels.tico-setup=$TICO_INSTALL" --format='value(projectId)' --limit=1 \
    </dev/null 2>"$TMP/projects.err"); then
    die "Could not look for the Tico project. $(first_line "$TMP/projects.err")"
  fi
  if [[ -n "$found" ]]; then
    PROJECT="$found"
    say "Using project $PROJECT"
    return
  fi
  suffix=$(python3 -c 'import secrets; print(secrets.token_hex(3))')
  PROJECT="tico-mail-$suffix"
  say "Creating project $PROJECT"
  if [[ -n "$ORG" ]]; then
    gcloud projects create "$PROJECT" --labels="tico-setup=$TICO_INSTALL" --organization="$ORG" </dev/null \
      >/dev/null 2>"$TMP/create.err" || die "Could not create the project. $(first_line "$TMP/create.err")"
  else
    gcloud projects create "$PROJECT" --labels="tico-setup=$TICO_INSTALL" </dev/null \
      >/dev/null 2>"$TMP/create.err" || die "Could not create the project. $(first_line "$TMP/create.err")"
  fi
}

enable_apis() {
  gcloud services enable gmail.googleapis.com calendar-json.googleapis.com orgpolicy.googleapis.com iam.googleapis.com \
    --project="$PROJECT" </dev/null >/dev/null 2>"$TMP/apis.err" || die "Could not turn on the Google APIs. $(first_line "$TMP/apis.err")"
  say "Turned on Gmail, Calendar, Organization Policy and IAM"
}

ensure_service_account() {
  SA="tico-mail@$PROJECT.iam.gserviceaccount.com"
  if gcloud iam service-accounts describe "$SA" --project="$PROJECT" </dev/null >/dev/null 2>&1; then
    say "Using service account $SA"
    return
  fi
  gcloud iam service-accounts create tico-mail --display-name="Tico mail" --project="$PROJECT" </dev/null \
    >/dev/null 2>"$TMP/sa.err" || die "Could not create the service account. $(first_line "$TMP/sa.err")"
  say "Created service account $SA"
}

# Sets ENFORCED to yes or no when the policy in force on the project says so, in either form. When the policy can't be
# read it is unknown, and key creation decides: a refusal that names the key policy still gets the override.
enforced() {
  local constraint="$1"
  if ! gcloud org-policies describe "constraints/$constraint" --project="$PROJECT" --effective --format=json \
    </dev/null >"$TMP/policy.json" 2>"$TMP/policy.err"; then
    if grep -qi 'not found' "$TMP/policy.err"; then ENFORCED=no; else ENFORCED=unknown; fi
    return
  fi
  ENFORCED=$(python3 -I "$TMP/enforced.py" "$TMP/policy.json") || ENFORCED=unknown
}

grant_policy_admin() {
  CALLER=$(gcloud config get-value account </dev/null 2>/dev/null || true)
  [[ -n "$CALLER" ]] || die "Could not tell which account this is."
  say "Granting $CALLER the Organization Policy Administrator role on the organization, for this setup only"
  if ! gcloud organizations add-iam-policy-binding "$ORG" --member="$(member)" --role=roles/orgpolicy.policyAdmin \
    --condition=None --quiet </dev/null >/dev/null 2>"$TMP/grant.err"; then
    die "Could not grant the role. $(first_line "$TMP/grant.err")"
  fi
  ADDED_ROLE=1
}

# Writes a project-level override that allows key creation under the named constraint.
set_policy() {
  local constraint="$1" file="$TMP/override-$1.json"
  printf '{"name": "projects/%s/policies/%s", "spec": {"rules": [{"enforce": false}]}}\n' "$PROJECT" "$constraint" > "$file"
  gcloud org-policies set-policy "$file" --project="$PROJECT" </dev/null >/dev/null 2>"$TMP/set.err"
}

override_policy() {
  local constraint="$1"
  say "Allowing service account keys on the project ($constraint)"
  if set_policy "$constraint"; then return; fi
  if grep -qi 'permission' "$TMP/set.err" && [[ -n "$ORG" ]]; then
    grant_policy_admin
    if set_policy "$constraint"; then return; fi
  fi
  die "This account needs the Organization Policy Administrator role (roles/orgpolicy.policyAdmin) on the organization in Cloud IAM; Workspace super admin is a different role, so ask a Google Cloud administrator to grant it, then run this again."
}

ensure_key_policy() {
  local constraint
  for constraint in "${KEY_POLICIES[@]}"; do
    enforced "$constraint"
    if [[ "$ENFORCED" == yes ]]; then override_policy "$constraint"; fi
  done
}

key_id() {
  python3 -I "$TMP/key_id.py" "$1"
}

# True when the last key creation was refused because of the key policy, which the setup can lift.
refused_by_policy() {
  grep -qi 'disableServiceAccountKeyCreation' "$TMP/key.err" && return 0
  grep -qi 'FAILED_PRECONDITION' "$TMP/key.err" && grep -qi 'policy' "$TMP/key.err"
}

# Creates the key. A policy change can take a minute to apply, so a refusal is retried with a growing pause.
# A refusal that names the key policy overrides both key constraints, once, then retries under the same KEY_WAIT budget.
create_key() {
  local attempt=0 started=$SECONDS fixed=0 constraint
  while :; do
    attempt=$((attempt + 1))
    if gcloud iam service-accounts keys create "$TMP/key.json" --iam-account="$SA" --project="$PROJECT" </dev/null \
      >/dev/null 2>"$TMP/key.err"; then
      break
    fi
    if [[ "$fixed" == 0 ]] && refused_by_policy; then
      fixed=1
      for constraint in "${KEY_POLICIES[@]}"; do override_policy "$constraint"; done
    fi
    if (( SECONDS - started >= KEY_WAIT )); then
      die "Google did not create the key. Organization policy changes can take a few minutes; run this again then. $(first_line "$TMP/key.err")"
    fi
    say "Waiting for the key policy to apply"
    sleep $((attempt * RETRY_BASE))
  done
  OWN_KEY=$(key_id "$TMP/key.json")
  say "Created a key"
}

# Sends the key with the setup code. 409 means a newer setup code exists: this key is deleted and the run stops.
upload_key() {
  python3 -I "$TMP/wrap_key.py" "$TMP/key.json" "$TMP/upload.json"
  if ! curl -fsS -X POST -H "Authorization: Setup $CODE" -H 'Content-Type: application/json' \
    --data-binary @"$TMP/upload.json" "$TICO_BASE/api/v2/google/workspace/key" >/dev/null 2>"$TMP/curl.err"; then
    if grep -q 'error: 409' "$TMP/curl.err"; then
      gcloud iam service-accounts keys delete "$OWN_KEY" --iam-account="$SA" --project="$PROJECT" --quiet </dev/null \
        >/dev/null 2>&1 || true
      die "A newer setup started; use the newest command in Tico."
    fi
    if grep -q 'error: 401' "$TMP/curl.err"; then
      die "This setup code was used, has expired or isn't valid. Make a new command in Tico."
    fi
    if grep -q 'error: 422' "$TMP/curl.err"; then
      die "Tico couldn't read the key Google made. Run the command again to make a new one."
    fi
    if grep -q 'error: 429' "$TMP/curl.err"; then
      die "Tico is busy. Wait a minute, then run the command again."
    fi
    die "Tico didn't confirm the key. Run the command again: it reuses this project and removes the key this run leaves behind."
  fi
  say "Sent the key to Tico"
}

# Old keys go only when the server says this run's code is the newest, and only keys created before this one.
clean_up() {
  local answer="" latest="" keep="" old="" kid
  if ! answer=$(curl -fsS -X POST -H "Authorization: Setup $CODE" -H 'Content-Type: application/json' --data-binary '{}' \
    "$TICO_BASE/api/v2/google/workspace/key/keep" 2>"$TMP/keep.err"); then
    say "Could not check for old keys, so none were removed. Run the command again to clean up."
    return
  fi
  printf '%s' "$answer" > "$TMP/keep.json"
  latest=$(python3 -I "$TMP/keep_fields.py" "$TMP/keep.json" latest)
  if [[ "$latest" != yes ]]; then
    say "A newer setup has started, so this run leaves the keys alone."
    return
  fi
  keep=$(python3 -I "$TMP/keep_fields.py" "$TMP/keep.json" keep)
  if ! gcloud iam service-accounts keys list --iam-account="$SA" --project="$PROJECT" --managed-by=user --format=json \
    </dev/null >"$TMP/keys.json" 2>"$TMP/keys.err"; then
    say "Could not list the service account keys, so none were removed. Run the command again to clean up."
    return
  fi
  old=$(python3 -I "$TMP/old_keys.py" "$TMP/keys.json" "$OWN_KEY" "$keep")
  while IFS= read -r kid; do
    if [[ -n "$kid" ]]; then
      if gcloud iam service-accounts keys delete "$kid" --iam-account="$SA" --project="$PROJECT" --quiet </dev/null \
        >/dev/null 2>&1; then
        say "Removed an old key"
      else
        say "Could not remove an old key ($kid). Remove it in Cloud Console under the service account."
      fi
    fi
  done <<< "$old"
}

# The helper files are written here, not inlined, so the script stays one readable unit.
write_helpers() {
  cat > "$TMP/enforced.py" <<'PY'
import json, sys
policy = json.load(open(sys.argv[1]))
on = (policy.get("booleanPolicy") or {}).get("enforced") is True
on = on or any(rule.get("enforce") is True for rule in (policy.get("spec") or {}).get("rules") or [])
print("yes" if on else "no")
PY
  cat > "$TMP/key_id.py" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["private_key_id"])
PY
  cat > "$TMP/wrap_key.py" <<'PY'
import json, sys
with open(sys.argv[2], "w") as out:
    json.dump({"key": json.load(open(sys.argv[1]))}, out)
PY
  cat > "$TMP/keep_fields.py" <<'PY'
import json, sys
answer = json.load(open(sys.argv[1]))
if sys.argv[2] == "latest":
    print("yes" if answer.get("latest") is True else "no")
else:
    print(",".join(answer.get("keep_key_ids") or []))
PY
  cat > "$TMP/old_keys.py" <<'PY'
import datetime, json, re, sys
keys = json.load(open(sys.argv[1]))
own = sys.argv[2]
keep = set(filter(None, sys.argv[3].split(",")))
def when(text):
    return datetime.datetime.fromisoformat(re.sub(r"\.\d+", "", text).replace("Z", "+00:00"))
mine = [k for k in keys if k["name"].rsplit("/", 1)[-1] == own]
if mine:
    cut = when(mine[0]["validAfterTime"])
    for k in keys:
        kid = k["name"].rsplit("/", 1)[-1]
        if k.get("keyType") == "USER_MANAGED" and kid not in keep and kid != own and when(k["validAfterTime"]) < cut:
            print(kid)
PY
}

main() {
  parse_args "$@"
  check_config
  need_tools
  TMP=$(mktemp -d)
  trap cleanup EXIT
  write_helpers
  choose_org
  choose_project
  enable_apis
  ensure_service_account
  ensure_key_policy
  create_key
  upload_key
  clean_up
  say "Done. Go back to Tico."
}

main "$@"
