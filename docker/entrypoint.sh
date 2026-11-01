#!/usr/bin/env bash
# Entry point for the server image: `server`, `slack-gateway`, `restore` or `demo`. It seeds the data volume and runs
# the backups (or, with TICO_REHEARSAL=1, none of them). The runner has its own image and entry point.
set -euo pipefail

DATA=${TICO_DATA_DIR:-/data}  # overridable so tests can run this script unprivileged
LITESTREAM_CONFIG=${TICO_LITESTREAM_CONFIG:-/tmp/litestream.yml}  # overridable so tests can read what was written

log() { printf 'tico: %s\n' "$*"; }
die() { printf 'tico: error: %s\n' "$*" >&2; exit 1; }

# TICO_REHEARSAL=1: a server on a copy of real data, to see whether a migration works. It runs the same migrations and
# initialization as any start, and otherwise does nothing on a timer and sends nothing out (docs/install.md, "Rehearse a
# migration"): no scheduler or directory sync, no backups, no release check or usage count, no support or HQ calls, no
# Slack, no updater, no telemetry. The server also reads TICO_REHEARSAL itself (backend/config.py), so this is the second lock.
rehearsal() { case "${TICO_REHEARSAL:-}" in 1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;; *) return 1 ;; esac; }

server_environment() {
  export TICO_TEAM_NAME="${TICO_TEAM_NAME:-${TICO_COMPANY_NAME:-}}"
  export TICO_COMPANY_NAME="$TICO_TEAM_NAME"  # alias for older initialization code
  [ -n "$TICO_TEAM_NAME" ] || die "set TICO_TEAM_NAME"
  [ -n "${TICO_OWNER_EMAIL:-}" ] || die "set TICO_OWNER_EMAIL"
  case "$TICO_OWNER_EMAIL" in *[!A-Za-z0-9.@_+-]*|*@*@*|'') die "TICO_OWNER_EMAIL is not a plain email address" ;; esac
  case "$TICO_OWNER_EMAIL" in *@*) ;; *) die "TICO_OWNER_EMAIL is not a plain email address" ;; esac

  export TICO_DB=$DATA/hub.sqlite TICO_REGISTRY_DIR=$DATA/registry
  [ -n "${TICO_BLOB_BUCKET:-}" ] || export TICO_BLOB_DIR=$DATA/blobs
  export TICO_APP_NAME="${TICO_APP_NAME:-$TICO_COMPANY_NAME}" TICO_ASSISTANT_NAME="${TICO_ASSISTANT_NAME:-Assistant}"
  # An empty AWS region from .env would break the AWS SDK's own default; unset is what it expects.
  [ -n "${AWS_REGION:-}" ] || unset AWS_REGION
  [ -n "${AWS_DEFAULT_REGION:-}" ] || unset AWS_DEFAULT_REGION
  # An explicit TICO_SCHEDULER=0 is kept; unset means on.
  export TICO_SCHEDULER="${TICO_SCHEDULER:-1}"
  if rehearsal; then
    export TICO_REHEARSAL=1 TICO_SCHEDULER=0 TICO_UPDATE_CHECK=off TICO_TELEMETRY=off TICO_SUPPORT=off TICO_SLACK_GATEWAY_ENABLED=0
    unset TICO_UPDATER_URL TICO_POSTHOG_KEY TICO_POSTHOG_HOST TICO_SENTRY_DSN TICO_SENTRY_SERVER_DSN
  fi

  if [ -n "${TICO_UPDATER_URL:-}" ]; then
    # /control is shared with the updater alone; the volume is what keeps everyone else from the token.
    if [ ! -s /control/updater-token ]; then
      head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > /control/updater-token
    fi
    TICO_UPDATER_TOKEN="$(cat /control/updater-token)"
    export TICO_UPDATER_TOKEN
  fi

  # No domain and no sign-in setup is the quick start: a local server on loopback, the owner signs in with a token on
  # this computer. "none" is the older spelling. A domain (a public address) always needs a sign-in.
  case "${TICO_AUTH_PROXY:-}" in
    none|'')
      [ -z "${TICO_DOMAIN:-}" ] || die "TICO_DOMAIN is set, so humans reach this server over a public address and it needs sign-in: set TICO_AUTH_PROXY (oidc or cloudflare), or unset TICO_DOMAIN to run on this computer only"
      # The server may only be reached on loopback: it trusts a local owner token.
      export TICO_PUBLIC_URL="${TICO_PUBLIC_URL:-http://127.0.0.1:${TICO_PORT:-8765}}" TICO_LOCAL_OWNER_TOKEN_FILE=$DATA/local-owner.token
      unset TICO_AUTH_PROXY
      if [ ! -s "$TICO_LOCAL_OWNER_TOKEN_FILE" ]; then
        (umask 077; head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > "$TICO_LOCAL_OWNER_TOKEN_FILE")
      fi ;;
    *)
      [ -n "${TICO_DOMAIN:-}" ] || die "set TICO_DOMAIN"
      export TICO_PUBLIC_URL="${TICO_PUBLIC_URL:-https://$TICO_DOMAIN}" ;;
  esac
}

# The Cloudflare tunnel's route. cloudflared's image has no shell and reads no environment for routes, and a tunnel that
# is run with only its token has none of its own ("No ingress rules ... cloudflared will return 503"), so the one route
# it needs is written here from TICO_DOMAIN into a volume the tunnel container reads: the domain to this server, through
# compose.yaml's hold (which keeps a request waiting while this server restarts), anything else a 404. Written on every
# start, so a changed TICO_DOMAIN follows, and by the server image itself, so an update that replaces compose.yaml never
# needs a file the old updater does not know about. It is ingress.yml; cloudflared.yml, the older name, is left as an
# older server wrote it (see compose.yaml's cloudflared). A tunnel managed in the Cloudflare dashboard keeps its own
# route: cloudflared prefers the configuration Cloudflare holds and uses this file only when the tunnel has none.
# docs/install.md, "Cloudflare Tunnel".
TUNNEL_DIR=${TICO_TUNNEL_DIR:-/tunnel}

# The host of TICO_RUNNER_URL when the tunnel should serve it too: a plain hostname other than TICO_DOMAIN.
# A loopback or IP address never reaches the tunnel, so it adds nothing.
runner_host() {
  local host=${TICO_RUNNER_URL:-}
  host=${host#*://}; host=${host%%/*}; host=${host##*@}; host=${host%%:*}
  host=$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')
  case "$host" in *[!a-z0-9.-]*|.*|*.|''|localhost) return 0 ;; esac
  case "$host" in *[!0-9.]*) ;; *) return 0 ;; esac
  [ "$host" = "$(printf '%s' "$TICO_DOMAIN" | tr '[:upper:]' '[:lower:]')" ] || printf '%s' "$host"
}

# A visual origin must not turn its cookie-free hostname into another way to reach the whole app.
# Parse it before writing YAML: only a host that passes every check reaches the file. A bad value prints one warning
# naming the reason (never the value) and the config leaves out the visuals route; the server still starts, as the
# backend does without the setting (backend/visuals.py inline_on). Outer whitespace, such as a CR from an edited .env,
# is ignored like backend/config.py does.
visual_host() {
  [ -n "${TICO_VISUAL_ORIGIN:-}" ] || return 0
  python3 - "$1" <<'PY'
import ipaddress
import os
import re
import sys
from urllib.parse import urlsplit


def refuse(reason):
    print("tico: warning: TICO_VISUAL_ORIGIN " + reason + "; the tunnel config leaves out the visuals route",
          file=sys.stderr)
    sys.exit(1)


value = os.environ["TICO_VISUAL_ORIGIN"].strip()
if not value:
    sys.exit(0)
if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
    refuse("has spaces or control characters")
try:
    origin = urlsplit(value)
    host, port = origin.hostname or "", origin.port
except ValueError:
    refuse("is not a valid URL")
if origin.scheme != "https":
    refuse("is not an https:// address")
if origin.path not in ("", "/") or origin.query or origin.fragment:
    refuse("has a path, query or fragment; give only https://<host>")
if origin.username is not None or origin.password is not None:
    refuse("has a user name or password")
if port == 0:
    refuse("has port 0")
try:
    ipaddress.ip_address(host)
except ValueError:
    pass
else:
    refuse("is an IP address, not a hostname")
if not (len(host) <= 253 and "." in host
        and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))):
    refuse("is not a plain DNS hostname")
if host == os.environ["TICO_DOMAIN"].lower():
    refuse("is the app's own host (TICO_DOMAIN); visuals need a separate site")
if host == sys.argv[1]:
    refuse("is the runner's host (TICO_RUNNER_URL); visuals need a separate site")
print(host)
PY
}

# An app alias has the whole app, unlike the runner and cookie-free visuals. Validate the complete list before
# replacing the config, so a typo cannot promote either restricted hostname or inject another YAML rule.
domain_aliases() {
  [ -n "${TICO_DOMAIN_ALIASES:-}" ] || return 0
  python3 - "$1" "$2" <<'PY'
import ipaddress
import os
import re
import sys
from urllib.parse import urlsplit


def refuse(reason):
    print("tico: error: TICO_DOMAIN_ALIASES " + reason, file=sys.stderr)
    sys.exit(1)


def plain_hostname(host):
    return bool(host and len(host) <= 253 and "." in host
                and any(ch in "abcdefghijklmnopqrstuvwxyz" for ch in host)
                and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")))


value = os.environ["TICO_DOMAIN_ALIASES"]
if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
    refuse("has control characters")
if not value.isascii():
    refuse("must use plain ASCII DNS hostnames")
if os.environ.get("TICO_VISUAL_ORIGIN", "").strip() and not sys.argv[2]:
    refuse("needs a valid separate visual origin before app aliases can be routed")
canonical = os.environ["TICO_DOMAIN"].lower()
runner_value = os.environ.get("TICO_RUNNER_URL", "")
if runner_value.strip():
    try:
        runner = urlsplit(runner_value)
        runner_name, port = runner.hostname or "", runner.port
    except ValueError:
        refuse("needs a valid runner URL before app aliases can be routed")
    if (not runner_value.isascii() or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in runner_value)
            or runner.scheme not in ("http", "https") or runner.path not in ("", "/")
            or runner.query or runner.fragment or runner.username is not None or runner.password is not None
            or port == 0 or not plain_hostname(runner_name)
            or runner_name != canonical and runner_name != sys.argv[1]):
        refuse("needs a valid runner URL before app aliases can be routed")
seen = {canonical}
aliases = []
for part in value.split(","):
    host = part.strip(" ").lower()
    if not plain_hostname(host):
        refuse("must contain comma-separated plain DNS hostnames with no empty entries")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        refuse("must not contain an IP address")
    if host == sys.argv[1] or host == sys.argv[2]:
        refuse("collides with the restricted runner or visual hostname")
    if host not in seen:
        seen.add(host)
        aliases.append(host)
print("\n".join(aliases))
PY
}

tunnel_config() {
  [ -n "${TICO_DOMAIN:-}" ] && [ -d "$TUNNEL_DIR" ] && [ -w "$TUNNEL_DIR" ] || return 0
  case "$TICO_DOMAIN" in *[!A-Za-z0-9.-]*|.*|*.|'') die "TICO_DOMAIN is not a plain hostname: $TICO_DOMAIN" ;; esac
  local runner visual aliases host origin=http://hold:8080
  runner=$(runner_host)
  visual=$(visual_host "$runner") || visual=  # visual_host printed the warning
  aliases=$(domain_aliases "$runner" "$visual") || return 1
  {
    echo "ingress:"
    echo "  - hostname: $TICO_DOMAIN"
    echo "    service: $origin"
    if [ -n "$aliases" ]; then
      while IFS= read -r host; do
        echo "  - hostname: $host"
        echo "    service: $origin"
      done <<< "$aliases"
    fi
    if [ -n "$runner" ]; then
      # A separate runner hostname has no sign-in page in front of it, so it serves only what runners and
      # external agents call: the API (which checks their tokens) and the runner download.
      echo "  - hostname: $runner"
      echo "    path: '^/(?:api/v2|download)(?:/.*)?\$'"
      echo "    service: $origin"
    fi
    if [ -n "$visual" ]; then
      echo "  - hostname: $visual"
      echo "    path: '^/visuals/'"
      echo "    service: $origin"
    fi
    echo "  - service: http_status:404"
  } > "$TUNNEL_DIR/ingress.yml.new"
  chmod 0644 "$TUNNEL_DIR/ingress.yml.new"
  mv -f "$TUNNEL_DIR/ingress.yml.new" "$TUNNEL_DIR/ingress.yml"
}

# Backups are on unless TICO_BACKUP=off: TICO_BACKUP_URL is the off-server copy; without it the copy goes to
# the tico-backups volume, which survives a deleted data volume but not a lost server.
BACKUPS=${TICO_BACKUP_DIR:-/backups}

backup_configuration() {  # writes $LITESTREAM_CONFIG; returns 1 when backups are off
  local url region sync interval_seconds unit generation
  if [ "${TICO_BACKUP:-}" = off ]; then
    TICO_BACKUP_MODE=off
  elif [ -n "${TICO_BACKUP_URL:-}" ]; then
    case "$TICO_BACKUP_URL" in s3://*) ;; *) die "TICO_BACKUP_URL must be s3://bucket/prefix" ;; esac
    TICO_BACKUP_MODE=remote
  else
    { [ -w "$BACKUPS" ] || { rehearsal && [ -r "$BACKUPS" ]; }; } || die "no readable backup location $BACKUPS: mount the tico-backups volume there, set TICO_BACKUP_URL, or set TICO_BACKUP=off"
    TICO_BACKUP_MODE=local-only
  fi
  export TICO_BACKUP_MODE TICO_BACKUP_DIR="$BACKUPS"
  [ "$TICO_BACKUP_MODE" != off ] || return 1
  # How often Litestream ships changes: the most that can be lost in a crash is about one interval. 1m by default.
  sync=${TICO_BACKUP_SYNC_INTERVAL:-1m}
  [[ $sync =~ ^([0-9]{1,4})([smh])$ ]] || die "TICO_BACKUP_SYNC_INTERVAL must be a number and s, m or h, like 30s, 5m or 1h; got '$sync'"
  case "${BASH_REMATCH[2]}" in s) unit=1 ;; m) unit=60 ;; h) unit=3600 ;; esac
  interval_seconds=$(( 10#${BASH_REMATCH[1]} * unit ))
  { [ "$interval_seconds" -ge 1 ] && [ "$interval_seconds" -le 3600 ]; } || die "TICO_BACKUP_SYNC_INTERVAL must be between 1s and 1h; got '$sync'"
  url="${TICO_BACKUP_URL:-file://$BACKUPS}"
  # The backup generation the data is in, from the cache beside the database (backend/replication.py). Reading it needs no
  # network, so a slow or unreachable bucket cannot hold up the start. No cache means the root layout, and the reset job that
  # starts Litestream reads environment.json and corrects this file before Litestream starts. A cache that cannot be read
  # stops the start: a guess could replicate over the copy the data is in.
  generation=$(python -m backend.replication cached-generation "$DATA") \
    || die "the backup generation file in $DATA cannot be read; fix it, or remove it so the next start reads environment.json"
  url="${url%/}${generation:+/$generation}"
  region="${TICO_BACKUP_REGION:-}"
  [ -n "$region" ] || [ -z "${TICO_BACKUP_ENDPOINT:-}" ] || region=us-east-1
  {
    # On a stop, Litestream's last sync of what the server wrote gets this long (its own default is 30s, which would
    # outlast Docker's stop timeout and end in a SIGKILL that skips it).
    echo "shutdown-sync-timeout: 5s"
    echo "snapshot:"
    echo "  interval: 24h"
    echo "  retention: 720h"
    echo "dbs:"
    echo "  - path: $DATA/hub.sqlite"
    echo "    monitor-interval: 1s"
    echo "    checkpoint-interval: 1m"
    echo "    busy-timeout: 5s"
    echo "    replica:"
    echo "      url: $url"
    [ -z "${TICO_BACKUP_ENDPOINT:-}" ] || echo "      endpoint: $TICO_BACKUP_ENDPOINT"
    [ -z "$region" ] || echo "      region: $region"
    echo "      sync-interval: $sync"
  } > "$LITESTREAM_CONFIG"
}

warn_backups() {
  case "$TICO_BACKUP_MODE" in
    local-only)
      log "WARNING: no TICO_BACKUP_URL. Backups go only to the tico-backups volume on this server; if the server is lost, so are they."
      log "WARNING: set TICO_BACKUP_URL (S3, R2 or any S3-compatible bucket) in .env and run: docker compose up -d" ;;
    off) log "WARNING: backups are off (TICO_BACKUP=off). One deleted volume loses everything." ;;
  esac
}

# Moves the database's -wal and -shm to BASE-wal and BASE-shm, and moves nothing while a database file is at the path. A WAL
# holds commits that may not be in the database file yet (hubdb.leave_wal_for_litestream), and SQLite applies a WAL at a
# database's path to whatever file is there. So a WAL moves with the database it belongs to, and one left without its
# database moves aside before a restore writes there. Nothing is deleted.
move_sidecars_aside() {  # BASE: the name the database has once it is moved, or its orphan's name
  local leftover
  [ ! -e "$TICO_DB" ] || return 0
  for leftover in "$TICO_DB-wal" "$TICO_DB-shm"; do
    if [ -e "$leftover" ]; then
      mv "$leftover" "$1${leftover#"$TICO_DB"}"
      log "moved $(basename "$leftover") aside as $(basename "$1")${leftover#"$TICO_DB"}"
    fi
  done
}

# An empty volume never quietly becomes a new team when a team exists (or may exist) behind it. The restore
# result is kept, not ignored; and markers outside the database (backend/replication.py) say what was here before.
# --initialize-empty (or TICO_INITIALIZE_EMPTY=1) is the explicit way to start over.
seed() {
  local restore_failed=() guard=()
  case "${TICO_INITIALIZE_EMPTY:-}" in 1|true|yes) guard+=(--initialize-empty) ;; esac
  if [ ! -s "$TICO_DB" ]; then
    move_sidecars_aside "$TICO_DB.orphan-$(date -u +%Y%m%dT%H%M%SZ)"
    if backup_configuration; then
      # The restore reads the generation the data is in, as environment.json names it now: a copy from another folder could be
      # an older one. Strict: a generation that cannot be read fails the restore, as a restore that failed does; the cache is
      # not a stand-in here.
      if python -m backend.replication sync-generation --strict "$LITESTREAM_CONFIG" "$TICO_DB"; then
        log "restoring the database from its backup, if there is one"
        litestream restore -if-db-not-exists -if-replica-exists -config "$LITESTREAM_CONFIG" "$TICO_DB" || restore_failed=(--restore-failed)
      else
        restore_failed=(--restore-failed)
      fi
    fi
    if [ -s "$TICO_DB" ]; then
      python -m backend.replication restore-blobs || log "could not restore attachments; run: docker compose run --rm server restore --force"
      # The key files that decrypt the stored secrets (credential.key, github-app.key, slack.key; docs/install.md); a
      # database without them cannot.
      python -m backend.replication restore-credential-key || log "could not restore every key file; see docs/install.md#backups-and-restore"
    else
      python -m backend.replication check-new-company "$DATA" ${restore_failed[@]+"${restore_failed[@]}"} ${guard[@]+"${guard[@]}"} \
        || die "refusing to start with an empty data volume (see above)"
    fi
  fi
  mkdir -p "$DATA/blobs" "$DATA/seed-projects"
  if [ ! -d "$TICO_REGISTRY_DIR" ]; then
    log "seeding the registry for $TICO_TEAM_NAME"
    python - "$DATA" "${TICO_OWNER_NAME:-$TICO_OWNER_EMAIL}" <<'PY'
import os, sys
from pathlib import Path
from clients import environments
environment = {"company_name": os.environ["TICO_COMPANY_NAME"], "app_name": os.environ["TICO_APP_NAME"],
               "assistant_name": os.environ["TICO_ASSISTANT_NAME"]}
environments.seed_registry(Path(sys.argv[1]), sys.argv[2], os.environ["TICO_OWNER_EMAIL"], environment)
PY
  fi
  if [ ! -s "$TICO_DB" ]; then
    log "creating the database"
    python -m backend.manage initialize "$TICO_DB" --projects "$DATA/seed-projects" >/dev/null
  fi
}

# The install's permanent id lives in the database, so a restore brings it back. TICO_ENVIRONMENT_ID sets it.
environment_identity() {
  TICO_ENVIRONMENT_ID="$(python - <<'PY'
import json, os, re, secrets, sqlite3
from backend.hubdb import leave_wal_for_litestream
db = leave_wal_for_litestream(sqlite3.connect(os.environ["TICO_DB"]))
row = db.execute("SELECT value FROM registry_metadata WHERE key='docker-environment-id'").fetchone()
value = os.environ.get("TICO_ENVIRONMENT_ID") or (json.loads(row[0]) if row else secrets.token_hex(8))
if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", value):
    raise SystemExit("TICO_ENVIRONMENT_ID is lowercase letters, digits and hyphens")
db.execute("INSERT INTO registry_metadata VALUES('docker-environment-id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
           (json.dumps(value),))
db.commit()
print(value)
PY
)"
  export TICO_ENVIRONMENT_ID
}

# TICO_EXPECTED_AWS_ACCOUNT (and TICO_EXPECTED_AWS_ROLE): refuse to start as any other AWS identity, before anything
# reads a backup, bucket, key or secret with it (backend/aws_identity.py). Unset, nothing is checked or called.
aws_identity() {
  [ -n "${TICO_EXPECTED_AWS_ACCOUNT:-}${TICO_EXPECTED_AWS_ROLE:-}" ] || return 0
  local confirmed
  confirmed="$(python -m backend.aws_identity check)" || die "refusing to start: ${confirmed:-could not confirm the AWS identity}"
  log "$confirmed"
}

caddy_config() {
  python - <<'PY'
import os
from pathlib import Path
import tempfile
from backend import caddy_assets

directory = Path(os.environ.get("TICO_CADDY_DIR", "/caddy-generated"))
if directory.is_dir() and os.access(directory, os.W_OK):
    # Directory mounts expose atomic replacements; a single-file bind could retain an older inode.
    source = Path(caddy_assets.__file__).resolve().parent.parent / "docker" / "caddy-supervisor.sh"
    descriptor, temporary = tempfile.mkstemp(prefix=".supervisor-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary, directory / "supervisor.sh")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
caddy_assets.prepare()
PY
}

prepare() {  # everything `server` does before it starts serving: also what the tests run
  server_environment
  for arg in "$@"; do
    case "$arg" in --initialize-empty) export TICO_INITIALIZE_EMPTY=1 ;; *) die "unknown option $arg" ;; esac
  done
  aws_identity
  seed
  environment_identity
  tunnel_config
  caddy_config
  backup_configuration || true
  # After the restore above, which only reads the backup: from here nothing is written to it.
  if rehearsal; then export TICO_BACKUP_MODE=rehearsal; fi
  python -m backend.replication mark-environment "$DATA" "$TICO_ENVIRONMENT_ID"
}

# Runs the server ("$@") and, with backups on, Litestream and the attachment loop beside it, and stops them in the order
# that keeps the backup whole. On SIGTERM (`docker stop`, a restart, an update) the server gets TICO_STOP_GRACE seconds
# (1 by default) to finish what it is answering, and is then killed: its own shutdown waits on background work for far
# longer than Docker's 10 s stop timeout, and a SIGKILL from Docker would take Litestream down with it, before its last
# sync. Only once the server has exited is Litestream stopped, so its last sync covers every write (and is bounded by
# shutdown-sync-timeout), and it leaves its local state current for the next start. A server or Litestream that exits
# on its own ends the container (the other is stopped as on a stop), as `litestream replicate -exec` did.
supervise() {
  local backups=$1; shift
  local grace=${TICO_STOP_GRACE:-1} stop=0 server='' litestream='' loop='' killer='' which='' rc=0 code=0
  local server_done=0 litestream_done=0 requested=0
  [[ $grace =~ ^[0-9]{1,3}(\.[0-9]{1,3})?$ ]] || die "TICO_STOP_GRACE is a number of seconds, like 1 or 2.5; got '$grace'"
  stopping() {
    [ "$stop" = 1 ] && return 0
    stop=1
    if [ -n "$server" ]; then
      kill -TERM "$server" 2>/dev/null || true
      ( sleep "$grace"; kill -KILL "$server" 2>/dev/null ) &
      killer=$!
    fi
  }
  # `wait` for a child, again after a signal (the trap) cuts it short; its exit status in rc.
  reap() { while :; do wait "$1" && rc=0 || rc=$?; [ "$rc" -gt 128 ] && kill -0 "$1" 2>/dev/null || return 0; done; }
  trap 'requested=1; stopping' TERM INT
  if [ "$backups" = 1 ]; then
    # The reset first makes the config name the backup generation that environment.json names. Then stale Litestream state
    # beside the database is cleared when the backup location is empty, and a new backup generation is started when the copy
    # is stuck. It runs here, in the job that starts Litestream, so the server does not wait for it. An unfinished switch
    # waits here, with the server running, until the backup location can be read; a stop ends the wait. exec makes the job
    # Litestream itself: $! is its pid. Exit 3 means a generation switch broke off after it began, and exit 4 that nothing
    # says which generation the data is in; Litestream does not start on either, since its config may not match.
    ( rc=0; python -m backend.replication reset-stale-litestream "$LITESTREAM_CONFIG" "$TICO_DB" || rc=$?
      case "$rc" in 3|4) exit 1 ;; esac
      exec litestream replicate -config "$LITESTREAM_CONFIG" ) &
    litestream=$!
    # The loop gets Litestream's pid: a copy that has stopped moving is healed by stopping Litestream, and the container
    # then restarts (backend/replication.py, stuck_replica_check).
    TICO_LITESTREAM_PID=$litestream python -m backend.replication loop &
    loop=$!
  fi
  "$@" &
  server=$!
  [ "$stop" = 0 ] || { stop=0; stopping; }   # a stop that came before the server did
  while [ "$stop" = 0 ]; do
    which=''
    wait -n -p which "$server" ${litestream:+"$litestream"} && rc=0 || rc=$?
    if [ "${which:-}" = "$server" ]; then server_done=1; code=$rc; break; fi
    if [ -n "${which:-}" ]; then litestream_done=1; break; fi
    if [ "$rc" = 127 ]; then server_done=1; code=1; break; fi   # nothing left to wait for
  done                                        # (no pid: the trap ran, and the loop ends)
  if [ "$server_done" = 0 ]; then
    [ "$requested" = 1 ] || log "Litestream exited; stopping the server"
    stopping
    reap "$server"
    code=$rc
  fi
  [ -z "$killer" ] || kill "$killer" 2>/dev/null || true
  if [ -n "$litestream" ]; then
    if [ "$litestream_done" = 0 ]; then
      kill -TERM "$litestream" 2>/dev/null || true
      reap "$litestream"
    elif [ "$requested" = 0 ]; then
      code=1
    fi
    kill -TERM "$loop" 2>/dev/null || true
  fi
  trap - TERM INT
  if [ "$requested" = 1 ]; then code=0; fi        # a requested stop is a clean exit, however the server ended
  exit "$code"
}

server() {
  prepare "$@"
  local serve=(python -m backend.server --host 0.0.0.0 --port 8765)
  if rehearsal; then
    log "REHEARSAL: nothing runs or leaves this server. No scheduler, directory sync, release check, usage count, support, Slack or updater."
    log "REHEARSAL: backups are off: no Litestream, no replication loop, nothing is written to ${TICO_BACKUP_URL:-a backup location}."
    exec "${serve[@]}"
  fi
  warn_backups
  if [ "$TICO_BACKUP_MODE" != off ]; then
    log "backing up the database and attachments to ${TICO_BACKUP_URL:-the tico-backups volume ($BACKUPS)}"
    supervise 1 "${serve[@]}"
  fi
  supervise 0 "${serve[@]}"
}

# The Slack gateway: the same image, its own process, so a Slack outage or restart never touches the
# server. It waits for the server to create the database and for the owner to paste tokens.
slack_gateway() {
  if rehearsal; then
    log "REHEARSAL: the Slack gateway does not run"
    exec sleep infinity
  fi
  server_environment
  aws_identity
  local waited=0
  until [ -s "$TICO_DB" ]; do
    [ "$waited" -lt 120 ] || die "the server has not created $TICO_DB; is it running?"
    sleep 2; waited=$((waited + 2))
  done
  # The server writes the install's id after creating the database; read it, never invent one here.
  TICO_ENVIRONMENT_ID="$(python - <<'PY'
import json, os, sqlite3, sys, time
for _ in range(60):
    try:
        row = sqlite3.connect("file:%s?mode=ro" % os.environ["TICO_DB"], uri=True).execute(
            "SELECT value FROM registry_metadata WHERE key='docker-environment-id'").fetchone()
    except sqlite3.Error:
        row = None
    if row:
        print(json.loads(row[0])); sys.exit(0)
    time.sleep(2)
sys.exit("the server has not recorded this install's id")
PY
)"
  export TICO_ENVIRONMENT_ID TICO_SLACK_GATEWAY_ENABLED=1 TICO_SLACK_WAIT=1
  exec python -m backend.slack_gateway
}

# Rebuilds an empty data volume from the backup (the remote one, else the tico-backups volume). The install's
# id is in the database, so it returns with it. A volume that already holds data is only replaced with --force.
restore() {
  local force=0 aside
  case "${1:-}" in --force) force=1 ;; '') ;; *) die "usage: restore [--force]" ;; esac
  aws_identity  # before the backup or the credential key is read, or a database is moved aside
  export TICO_DB=$DATA/hub.sqlite TICO_BLOB_DIR=$DATA/blobs
  if ! python -m backend.replication is-empty "$DATA"; then
    [ "$force" = 1 ] || die "the data volume is not empty; restore would replace it. Re-run with --force to keep the current database aside and restore over it"
  fi
  backup_configuration || die "backups are off (TICO_BACKUP=off); there is nothing to restore from"
  python -m backend.replication sync-generation --strict "$LITESTREAM_CONFIG" "$TICO_DB" \
    || die "could not find the backup generation the data is in (see above), so nothing was restored; if the backup location cannot be read, try again when it is reachable"
  # The current database moves aside with its WAL, so the kept copy opens with every commit it had. A WAL left without a
  # database moves aside too, or SQLite would apply it to the restored copy.
  if [ -e "$TICO_DB" ]; then
    aside="$TICO_DB.before-restore.$(date +%s)"
    log "keeping the current database as $aside"
    mv "$TICO_DB" "$aside"
    move_sidecars_aside "$aside"
  else
    move_sidecars_aside "$TICO_DB.orphan-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  log "restoring from ${TICO_BACKUP_URL:-the tico-backups volume ($BACKUPS)}"
  litestream restore -if-replica-exists -config "$LITESTREAM_CONFIG" "$TICO_DB"
  [ -s "$TICO_DB" ] || die "the backup holds no database"
  python -m backend.replication restore-blobs
  # With --force the database above was kept aside; the key files that go with it are put back too (a different one
  # already here is kept aside as <name>.before-restore.*).
  # shellcheck disable=SC2046  # unquoted on purpose: no --force passes no argument at all
  python -m backend.replication restore-credential-key $([ "$force" = 1 ] && echo --replace) \
    || log "could not restore every key file; see docs/install.md#backups-and-restore"
  python - <<'PY'
import os, sqlite3
if sqlite3.connect(os.environ["TICO_DB"]).execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise SystemExit("tico: error: the restored database fails its integrity check")
PY
  log "restored; start Tico with: docker compose up -d"
}

# The demo needs no configuration and keeps nothing: sample data in a temporary folder, on the port
# the server image already exposes. It binds all interfaces because a container must, and answers only
# requests addressed to localhost, so publish it on loopback: -p 127.0.0.1:8765:8765 (docs/demo.md).
demo() {
  shift
  exec python -m backend.demo --in-container --port 8765 "$@"
}

case "${1:-server}" in
  server) shift; server "$@" ;;
  prepare) shift; prepare "$@" ;;
  tunnel-config) tunnel_config ;;
  caddy-config) caddy_config ;;
  aws-identity) aws_identity ;;
  demo) demo "$@" ;;
  restore) shift; restore "$@" ;;
  slack-gateway) slack_gateway ;;
  *) exec "$@" ;;
esac
