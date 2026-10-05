#!/usr/bin/env bash
# Entry point for the server image: `server`, `slack-gateway`, `restore` or `demo`. It seeds the data volume and runs
# the backups (or, with TICO_REHEARSAL=1, none of them). The runner has its own image and entry point.
set -euo pipefail

DATA=${TICO_DATA_DIR:-/data}  # overridable so tests can run this script unprivileged
LITESTREAM_CONFIG=/tmp/litestream.yml

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
# it needs is written here from TICO_DOMAIN into a volume the tunnel container reads: the domain to this server, anything
# else a 404. Written on every start, so a changed TICO_DOMAIN follows, and by the server image itself, so an update that
# replaces compose.yaml never needs a file the old updater does not know about. A tunnel managed in the Cloudflare
# dashboard keeps its own route: cloudflared prefers the configuration Cloudflare holds and uses this file only when the
# tunnel has none. docs/install.md, "Cloudflare Tunnel".
TUNNEL_DIR=${TICO_TUNNEL_DIR:-/tunnel}

tunnel_config() {
  [ -n "${TICO_DOMAIN:-}" ] && [ -d "$TUNNEL_DIR" ] && [ -w "$TUNNEL_DIR" ] || return 0
  case "$TICO_DOMAIN" in *[!A-Za-z0-9.-]*|.*|*.|'') die "TICO_DOMAIN is not a plain hostname: $TICO_DOMAIN" ;; esac
  {
    echo "ingress:"
    echo "  - hostname: $TICO_DOMAIN"
    echo "    service: http://server:8765"
    echo "  - service: http_status:404"
  } > "$TUNNEL_DIR/cloudflared.yml.new"
  chmod 0644 "$TUNNEL_DIR/cloudflared.yml.new"
  mv -f "$TUNNEL_DIR/cloudflared.yml.new" "$TUNNEL_DIR/cloudflared.yml"
}

# Backups are on unless TICO_BACKUP=off: TICO_BACKUP_URL is the off-server copy; without it the copy goes to
# the tico-backups volume, which survives a deleted data volume but not a lost server.
BACKUPS=${TICO_BACKUP_DIR:-/backups}

backup_configuration() {  # writes $LITESTREAM_CONFIG; returns 1 when backups are off
  local url region
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
  url="${TICO_BACKUP_URL:-file://$BACKUPS}"
  region="${TICO_BACKUP_REGION:-}"
  [ -n "$region" ] || [ -z "${TICO_BACKUP_ENDPOINT:-}" ] || region=us-east-1
  {
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
    echo "      sync-interval: 10s"
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

# An empty volume never quietly becomes a new team when a team exists (or may exist) behind it. The restore
# result is kept, not ignored; and markers outside the database (backend/replication.py) say what was here before.
# --initialize-empty (or TICO_INITIALIZE_EMPTY=1) is the explicit way to start over.
seed() {
  local restore_failed=() guard=()
  case "${TICO_INITIALIZE_EMPTY:-}" in 1|true|yes) guard+=(--initialize-empty) ;; esac
  if [ ! -s "$TICO_DB" ]; then
    if backup_configuration; then
      log "restoring the database from its backup, if there is one"
      litestream restore -if-db-not-exists -if-replica-exists -config "$LITESTREAM_CONFIG" "$TICO_DB" || restore_failed=(--restore-failed)
    fi
    if [ -s "$TICO_DB" ]; then
      python -m backend.replication restore-blobs || log "could not restore attachments; run: docker compose run --rm server restore --force"
      # The key that decrypts the stored credentials (docs/credential-vault.md); a database without it cannot.
      python -m backend.replication restore-credential-key || log "could not restore the credential key; see docs/credential-vault.md"
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
db = sqlite3.connect(os.environ["TICO_DB"])
row = db.execute("SELECT value_json FROM registry_metadata WHERE key='docker-environment-id'").fetchone()
value = os.environ.get("TICO_ENVIRONMENT_ID") or (json.loads(row[0]) if row else secrets.token_hex(8))
if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", value):
    raise SystemExit("TICO_ENVIRONMENT_ID is lowercase letters, digits and hyphens")
db.execute("INSERT INTO registry_metadata VALUES('docker-environment-id', ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
           (json.dumps(value),))
db.commit()
print(value)
PY
)"
  export TICO_ENVIRONMENT_ID
}

prepare() {  # everything `server` does before it starts serving: also what the tests run
  server_environment
  for arg in "$@"; do
    case "$arg" in --initialize-empty) export TICO_INITIALIZE_EMPTY=1 ;; *) die "unknown option $arg" ;; esac
  done
  seed
  environment_identity
  tunnel_config
  backup_configuration || true
  # After the restore above, which only reads the backup: from here nothing is written to it.
  if rehearsal; then export TICO_BACKUP_MODE=rehearsal; fi
  python -m backend.replication mark-environment "$DATA" "$TICO_ENVIRONMENT_ID"
}

server() {
  prepare "$@"
  local serve=(uvicorn backend.app:create_app --factory --host 0.0.0.0 --port 8765 --workers 1 --no-access-log
               --timeout-graceful-shutdown 2)
  if rehearsal; then
    log "REHEARSAL: nothing runs or leaves this server. No scheduler, directory sync, release check, usage count, support, Slack or updater."
    log "REHEARSAL: backups are off: no Litestream, no replication loop, nothing is written to ${TICO_BACKUP_URL:-a backup location}."
    exec "${serve[@]}"
  fi
  warn_backups
  if [ "$TICO_BACKUP_MODE" != off ]; then
    log "backing up the database and attachments to ${TICO_BACKUP_URL:-the tico-backups volume ($BACKUPS)}"
    python -m backend.replication loop &
    # litestream supervises the server: it flushes the last changes on a stop, and if the server exits so does it.
    exec litestream replicate -config "$LITESTREAM_CONFIG" -exec "${serve[*]}"
  fi
  exec "${serve[@]}"
}

# The Slack gateway: the same image, its own process, so a Slack outage or restart never touches the
# server. It waits for the server to create the database and for the owner to paste tokens.
slack_gateway() {
  if rehearsal; then
    log "REHEARSAL: the Slack gateway does not run"
    exec sleep infinity
  fi
  server_environment
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
            "SELECT value_json FROM registry_metadata WHERE key='docker-environment-id'").fetchone()
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
  local force=0
  case "${1:-}" in --force) force=1 ;; '') ;; *) die "usage: restore [--force]" ;; esac
  export TICO_DB=$DATA/hub.sqlite TICO_BLOB_DIR=$DATA/blobs
  if ! python -m backend.replication is-empty "$DATA"; then
    [ "$force" = 1 ] || die "the data volume is not empty; restore would replace it. Re-run with --force to keep the current database aside and restore over it"
  fi
  backup_configuration || die "backups are off (TICO_BACKUP=off); there is nothing to restore from"
  if [ -e "$TICO_DB" ]; then
    local aside
    aside="$TICO_DB.before-restore.$(date +%s)"
    log "keeping the current database as $aside"
    mv "$TICO_DB" "$aside"
    rm -f "$TICO_DB-wal" "$TICO_DB-shm"
  fi
  log "restoring from ${TICO_BACKUP_URL:-the tico-backups volume ($BACKUPS)}"
  litestream restore -if-replica-exists -config "$LITESTREAM_CONFIG" "$TICO_DB"
  [ -s "$TICO_DB" ] || die "the backup holds no database"
  python -m backend.replication restore-blobs
  # With --force the database above was kept aside; the key that goes with it is put back too (a different one already
  # here is kept aside as credential.key.before-restore.*).
  python -m backend.replication restore-credential-key $([ "$force" = 1 ] && echo --replace) \
    || log "could not restore the credential key; see docs/credential-vault.md"
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
  demo) demo "$@" ;;
  restore) shift; restore "$@" ;;
  slack-gateway) slack_gateway ;;
  *) exec "$@" ;;
esac
