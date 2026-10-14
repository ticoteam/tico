#!/usr/bin/env bash
# Entry point of the runner image.
#   join --url https://tico.example.com --code <one-time code> --label "Build box"
#   run
# `join` enrolls this runner the first time and then runs it; every later start of the same volume
# skips the enrollment, so the same command can stay as the container's restart command.
set -euo pipefail

CONFIG="$HOME/runner.json"
# Two users (SECURITY.md, runner/isolation.py). The supervisor is `ticorun` (10002), as in every earlier
# image, and owns runner.json, its state and the tools directory. Every process that runs bot code is `bot`
# (10003), in the same group so the workspace and the model logins work for both, and so an older image
# can still run on a migrated volume (an update that is rolled back). Started as root with the
# capabilities in docker/runner.compose.yaml, this script prepares the volume and runs itself again as
# ticorun holding them as ambient capabilities, which is how the supervisor drops bot code to `bot`.
SUPERVISOR_UID=10002 SUPERVISOR_GID=10002 BOT_UID=10003
LAYOUT="$HOME/.tico-two-user-layout"
CAPS=+chown,+dac_override,+kill,+setgid,+setuid

die() { printf 'tico-runner: error: %s\n' "$*" >&2; exit 1; }
log() { printf 'tico-runner: %s\n' "$*"; }

as_supervisor() { setpriv --reuid="$SUPERVISOR_UID" --regid="$SUPERVISOR_GID" --clear-groups "$@"; }
as_bot() { setpriv --reuid="$BOT_UID" --regid="$SUPERVISOR_GID" --clear-groups --inh-caps=-all --ambient-caps=-all "$@"; }

have_caps() {  # CHOWN DAC_OVERRIDE KILL SETGID SETUID: what the supervisor keeps (docker/runner.compose.yaml)
  local mask bit
  mask=$((16#$(awk '/^CapEff:/ {print $2}' /proc/self/status)))
  for bit in 0 1 5 6 7; do [ $(( (mask >> bit) & 1 )) -eq 1 ] || return 1; done
}

# What bot code works in is shared by group, so an older image can still use it; what is the
# supervisor's is 0600/0700 for ticorun alone, and `bot` (same group, no bits for it) cannot open it.
separate_users() {
  have_caps || die "started as root without the capabilities the runner needs: use the current runner.compose.yaml (user: \"0\", cap_add CHOWN DAC_OVERRIDE KILL SETGID SETUID)"
  if [ ! -e "$LAYOUT" ]; then
    log "moving this volume to the two-user layout (one time): the runner's login stays out of the bots' reach"
    # Everything at the top of the home directory that is not the supervisor's is the bot user's, so it can
    # replace it (the directory is sticky), and group-writable, so an older image can still use it. The
    # workspace stays with its old owner: git refuses a checkout another user owns, and an older image
    # runs git there; `bot` works in it through the group.
    as_supervisor find "$HOME" -mindepth 1 -maxdepth 1 ! -type l ! -name 'state-*' ! -name tools ! -name 'runner.json*' ! -name .ssh \
      ! -name '.enroll.*' ! -name '.tico-two-user-layout' -exec chmod -R g+rwX {} +
    find "$HOME" -mindepth 1 -maxdepth 1 ! -type l ! -name workspace ! -name 'state-*' ! -name tools ! -name 'runner.json*' \
      ! -name '.enroll.*' ! -name '.tico-two-user-layout' -exec chown -R -h "$BOT_UID:$SUPERVISOR_GID" {} +
    : > "$LAYOUT"; chown "$SUPERVISOR_UID:$SUPERVISOR_GID" "$LAYOUT"
  fi
  # The home directory is sticky: a turn may create files here but cannot delete or replace the supervisor's.
  chown "$SUPERVISOR_UID:$SUPERVISOR_GID" "$HOME" && as_supervisor chmod 1770 "$HOME"
  local file
  for file in "$HOME"/runner.json "$HOME"/runner.json.*; do
    [ -f "$file" ] && [ ! -L "$file" ] && chown "$SUPERVISOR_UID:$SUPERVISOR_GID" "$file" && as_supervisor chmod 0600 "$file"
  done
  # The workspace and model logins are shared; legacy secrets are the supervisor's.
  as_supervisor mkdir -p "$HOME/workspace/secrets"
  as_supervisor chmod 0770 "$HOME/workspace"
  chown "$SUPERVISOR_UID:$SUPERVISOR_GID" "$HOME/workspace/secrets" && as_supervisor chmod 0700 "$HOME/workspace/secrets"
  # The mail tool's folder, <workspace>/runtime/mail: a bot's first `mail.sh` builds its venv there, and the
  # supervisor's connectors job keeps mail.db there. The job made these folders as ticorun (0755) on a new
  # computer, so the bot user could not create the venv ("permission denied", run blocked). They are the bot
  # user's, setgid and group-writable, so both users can write in them. Only these two folders change hands:
  # the state directory, runner.json and the mail key stay 0600/0700 for ticorun.
  local mail_runtime="$HOME/workspace/runtime"
  if [ ! -L "$mail_runtime" ] && [ ! -L "$mail_runtime/mail" ]; then
    mkdir -p "$mail_runtime/mail" && chown "$BOT_UID:$SUPERVISOR_GID" "$mail_runtime" "$mail_runtime/mail" \
      && as_bot chmod 2770 "$mail_runtime" "$mail_runtime/mail"
    # The files in it too: the job wrote mail.db and audit.jsonl as ticorun with umask 022 (0644), so a bot's
    # `mail.sh search` failed with "attempt to write a readonly database". Both users need to write them
    # (mail.db and its -wal/-shm/-journal, audit.jsonl). Root has no CAP_FOWNER here, so the mode is set by
    # whichever of the two owns the file. Nothing outside this folder changes.
    local mail_file
    for mail_file in "$mail_runtime"/mail/mail.db "$mail_runtime"/mail/mail.db-* "$mail_runtime"/mail/audit.jsonl; do
      if [ -f "$mail_file" ] && [ ! -L "$mail_file" ]; then
        as_supervisor chmod g+rw "$mail_file" 2>/dev/null || as_bot chmod g+rw "$mail_file" 2>/dev/null || true
      fi
    done
  fi
  # The Codex login lives in the bot user's setgid, group-writable home, so `codex login` as `bot` works and the
  # supervisor can still read the 0600 files Codex leaves there (`codex login status`, the model list).
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  if [ ! -L "$codex_home" ]; then
    # Root has no CAP_FOWNER here, so the modes are set as the owner, after the chown.
    mkdir -p "$codex_home" && chown -R -h "$BOT_UID:$SUPERVISOR_GID" "$codex_home" \
      && as_bot chmod -R g+rwX "$codex_home" && as_bot chmod 2770 "$codex_home"
  fi
  mkdir -p /run/tico-runner && chown "$SUPERVISOR_UID:$SUPERVISOR_GID" /run/tico-runner && as_supervisor chmod 0755 /run/tico-runner
  export TICO_RUNNER_BOT_UID="$BOT_UID" TICO_RUNNER_BOT_GID="$SUPERVISOR_GID"
  exec setpriv --reuid="$SUPERVISOR_UID" --regid="$SUPERVISOR_GID" --clear-groups --inh-caps="$CAPS" --ambient-caps="$CAPS" \
    /usr/local/bin/tico-runner-entrypoint "$@"
}

ISOLATED=0
if [ "$(id -u)" = 0 ]; then
  separate_users "$@"
elif [ -n "${TICO_RUNNER_BOT_UID:-}" ]; then
  ISOLATED=1                # the second pass: ticorun with ambient capabilities
elif [ -e "$LAYOUT" ]; then
  log "warning: this volume has the two-user layout but the container is not starting as root, so bot code runs as the runner's own user (start it with the current runner.compose.yaml to separate them)"
fi

# ok, rejected (the server does not know this token) or unreachable (it may just be restarting)
registration() {
  python - "$CONFIG" <<'PY'
import json, sys
from clients.tico import APIError, Client
config = json.load(open(sys.argv[1]))
try:
    Client(config["url"], config["token"]).get("runners/assignments")
except APIError as exc:
    print("rejected" if exc.status in (401, 403) else "unreachable")
except OSError:
    print("unreachable")
else:
    print("ok")
PY
}

enroll() {  # url code label
  local url="$1" code="$2" label="$3" environment code_file
  environment="$(curl -fsS --max-time 10 "$url/healthz" 2>/dev/null | jq -r '.environment_id // empty' 2>/dev/null || true)"
  code_file="$(mktemp "$HOME/.enroll.XXXXXX")"
  jq -n --arg code "$code" '{code: $code}' > "$code_file"
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if python -m runner --config "$CONFIG" enroll --url "$url" --code-file "$code_file" --label "$label" \
         --projects "$HOME/workspace" ${environment:+--environment "$environment"} >/dev/null; then
      # A container never has a checkout to pull: updating means a new image.
      python - "$CONFIG" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
config["self_update"] = False
json.dump(config, open(sys.argv[1], "w"), indent=2)
PY
      rm -f "$code_file"   # single-use, but still not left lying around
      log "enrolled as $label"
      return 0
    fi
    # A code that was already used or has expired will not work on a retry; the server may just be starting.
    if [ -f "$CONFIG" ]; then rm -f "$code_file"; return 0; fi
    sleep $((attempt * 5))
  done
  rm -f "$code_file"
  die "could not enroll: check the URL, and that the code is unused and under 15 minutes old"
}

url="" code="" label="${TICO_RUNNER_LABEL:-$(uname -n)}"
mode="${1:-run}"
[ "$#" -eq 0 ] || shift
case "$mode" in
  join|run) ;;
  *) exec "$mode" "$@" ;;
esac
while [ "$#" -gt 0 ]; do
  case "$1" in
    --url) url="${2-}"; shift 2 ;;
    --code) code="${2-}"; shift 2 ;;
    --label) label="${2:?--label needs a value}"; shift 2 ;;
    *) die "unknown option $1 (join takes --url, --code and --label)" ;;
  esac
done

if [ -f "$CONFIG" ]; then
  case "$(registration)" in
    ok) log "already enrolled" ;;
    unreachable) log "the server is not answering; the runner will keep trying" ;;
    rejected)
      [ -n "$url" ] && [ -n "$code" ] || die "the server no longer knows this runner: join again with a new code"
      log "the server no longer knows this runner; enrolling again"
      mv "$CONFIG" "$CONFIG.stale"
      enroll "$url" "$code" "$label" ;;
  esac
else
  [ -n "$url" ] && [ -n "$code" ] || die "this runner is not enrolled: run it with  join --url <https://your-tico> --code <code> --label <name>"
  enroll "$url" "$code" "$label"
fi

as_user() { if [ "$ISOLATED" = 1 ]; then as_bot "$@"; else "$@"; fi; }
as_user git config --global user.name >/dev/null 2>&1 || as_user git config --global user.name "Tico runner"
as_user git config --global user.email >/dev/null 2>&1 || as_user git config --global user.email "tico-runner@$(uname -n)"
# The mail and calendar connectors have no checkout-sibling layout here: point them at the volume. The venv
# is built into the volume on first use (scripts/mail.sh), so the image stays slim and it survives restarts.
export TICO_PROJECTS_DIR="${TICO_PROJECTS_DIR:-$HOME/workspace}"
export TICO_MAIL_VENV="${TICO_MAIL_VENV:-${TICO_TOOLS_DIR:-$HOME/tools}/mail-venv}"
export TICO_SUPERVISED=1 TICO_RUNNER_SELF_UPDATE=0
# The runner also supervises the meeting-importer and Close jobs the hub assigns here (runner/sidejobs.py).
export TICO_SIDE_JOBS="${TICO_SIDE_JOBS:-1}"
# TICO_RUNNER_ENGINE=rust (or "engine": "rust" in runner.json) makes `python -m runner run` hand the process
# to this image's Rust runner, $TICO_RUNNER_BIN (runner/engine.py); a computer it cannot run yet comes back
# to the Python runner instead of stopping. The Python runner stays the default.
exec python -m runner --config "$CONFIG" run
