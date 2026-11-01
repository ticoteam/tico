#!/usr/bin/env bash
# Destroys the data volume of a running server and restores it, twice: from a MinIO bucket standing in for S3
# (TICO_BACKUP_URL set), and from the tico-backups volume alone (no URL). Each time it checks that a database
# row, an attachment and the environment id come back.
#   TICO_IMAGE=tico TICO_TAG=local docker/backup-test.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export TICO_IMAGE="${TICO_IMAGE:-tico}" TICO_TAG="${TICO_TAG:-local}"
net=tico-bt-net
work="$(mktemp -d)"

# No published port: 8765 may be taken on the host, and everything is reached with exec.
cat > "$work/override.yaml" <<YAML
services:
  server:
    ports: !override []
networks:
  default:
    name: $net
    external: true
YAML
write_env() {
  cat > "$work/env" <<'EOF2'
TICO_COMPANY_NAME=Backup Test
TICO_OWNER_EMAIL=owner@example.com
TICO_AUTH_PROXY=none
EOF2
  docker/edge-env.sh "${TICO_BACKUP_EDGE:-172.30.251}" >> "$work/env"   # its own edge range, apart from an install's and the gate's
  [ "$1" = remote ] || return 0
  cat >> "$work/env" <<'EOF2'
TICO_BACKUP_URL=s3://tico-test/tico
TICO_BACKUP_ENDPOINT=http://minio:9000
LITESTREAM_ACCESS_KEY_ID=minioadmin
LITESTREAM_SECRET_ACCESS_KEY=minioadmin
EOF2
}
dc() { docker compose -p tico-bt -f compose.yaml -f "$work/override.yaml" --env-file "$work/env" "$@"; }
cleanup() {
  status=$?
  [ "$status" = 0 ] || dc logs --tail 60 >&2 || true
  dc down -v >/dev/null 2>&1 || true
  docker rm -f tico-bt-minio >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT

step() { printf '==> %s\n' "$*"; }
fail() { printf 'backup-test: %s\n' "$*" >&2; exit 1; }
retry() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ "$SECONDS" -lt "$end" ] || return 1; sleep 2; done; }
healthy() { dc exec -T server curl -fsS --max-time 3 http://127.0.0.1:8765/healthz; }
environment_id() { healthy | python3 -c 'import json,sys; print(json.load(sys.stdin)["environment_id"])'; }
# shellcheck disable=SC2016  # the token is read inside the container
config() { dc exec -T server sh -c 'curl -fsS -H "Authorization: Bearer $(cat /data/local-owner.token)" http://127.0.0.1:8765/api/v2/config'; }
backup_field() { config | python3 -c "import json,sys; print(json.load(sys.stdin)['backup']['$1'])"; }

write_data() {  # prints the attachment digest
  dc exec -T server python - <<'PY'
import os, sqlite3
from pathlib import Path
from backend.blobs import Blobs
from backend.config import Settings
db = sqlite3.connect("/data/hub.sqlite")
db.execute("INSERT INTO registry_metadata VALUES('backup-test', '\"survives\"') ON CONFLICT(key) DO NOTHING")
db.commit()
print(Blobs(Settings(db_path=Path("/data/hub.sqlite"), blob_dir=Path("/data/blobs"))).put(b"attachment that must survive"))
PY
}
check_data() {  # digest
  dc exec -T server python - "$1" <<'PY'
import os, sqlite3, sys
from pathlib import Path
from backend.blobs import Blobs
from backend.config import Settings
row = sqlite3.connect("/data/hub.sqlite").execute("SELECT value FROM registry_metadata WHERE key='backup-test'").fetchone()
assert row and row[0] == '"survives"', row
blobs = Blobs(Settings(db_path=Path("/data/hub.sqlite"), blob_dir=Path("/data/blobs")))
assert blobs.get(sys.argv[1]) == b"attachment that must survive"
PY
}
replicated() {  # digest: the database and the attachment are both in the copy
  dc exec -T server python - "$1" <<'PY'
import os, sys
from backend import replication
mirror = replication.mirror_from_env()
assert mirror.newest(replication.db_levels(replication.replica_generation(mirror))), "no database segments yet"
assert any(k.endswith(sys.argv[1]) for k in mirror.keys("files/blobs")), "attachment not copied yet"
PY
  [ "$(backup_field last_replicated_at)" != None ]
}

rehearse() {  # remote|local
  local mode=$1 digest id
  write_env "$mode"
  step "$mode: server up"
  dc up -d
  retry 180 healthy || fail "the server did not become healthy"
  [ "$(backup_field mode)" = "$([ "$mode" = remote ] && echo remote || echo local-only)" ] || fail "the status reports the wrong mode"
  id="$(environment_id)"
  digest="$(write_data | tail -1)"
  step "$mode: wait for the copy"
  retry 120 replicated "$digest" || fail "the database and attachment were not copied"
  if [ "$mode" = local ]; then
    logs="$(dc logs server)"  # not piped into grep -q: pipefail would turn its early exit into a failure
    grep -q "WARNING: no TICO_BACKUP_URL" <<<"$logs" || fail "no local-only warning in the log"
  fi

  step "$mode: destroy the data volume"
  dc down
  if [ "$mode" = remote ]; then dc down -v; else docker volume rm tico-bt_tico-data >/dev/null; fi

  step "$mode: restore refuses a non-empty volume without --force"
  dc run --rm --no-deps server restore
  if dc run --rm --no-deps server restore 2>"$work/err"; then fail "restore overwrote a non-empty volume"; fi
  grep -q -- --force "$work/err" || fail "the refusal does not mention --force"
  dc run --rm --no-deps server restore --force

  step "$mode: up again"
  dc up -d
  retry 180 healthy || fail "the server did not come back"
  [ "$(environment_id)" = "$id" ] || fail "the environment id changed"
  check_data "$digest" || fail "the row or the attachment did not survive"
  dc down -v
}

write_env local
step "MinIO"
docker network create "$net" >/dev/null
# MinIO no longer publishes to Docker Hub; the legacy Bitnami repository still carries the server.
docker run -d --name tico-bt-minio --network "$net" --network-alias minio -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  "${MINIO_IMAGE:-bitnamilegacy/minio:latest}" >/dev/null
retry 60 docker run --rm --network "$net" --entrypoint python "$TICO_IMAGE:$TICO_TAG" -c '
import boto3
s3 = boto3.client("s3", endpoint_url="http://minio:9000", aws_access_key_id="minioadmin", aws_secret_access_key="minioadmin", region_name="us-east-1")
s3.create_bucket(Bucket="tico-test")' || fail "MinIO did not start"

rehearse remote
rehearse local
echo "backup-test: ok"
