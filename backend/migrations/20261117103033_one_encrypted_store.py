"""Every Tico secret in one encrypted store: what an older server sealed outside the vault's `credentials` table moves
into service rows there (backend/credentials.py SERVICE, `keep_service`).

1. A Settings > Models subscription's value, sealed in its own row (`model_subscriptions.sealed`, context
   `model_subscription:<id>`), becomes the service row `model-subscription-<id>`. The subscription points at it
   (`credential_id`) and `sealed` is emptied. One that already points at a vault credential (a key moved by
   20261117061243_computer_keys_to_subscriptions) is left as it is.
2. The Tico AI Gateway key and the install's secret, sealed in the `registry_metadata` record `gateway-connection`,
   become the service rows `gateway-key` and `gateway-install-secret`; the record keeps a reference (`vault:<tag>`).
3. The Tico company server key, sealed in the record `company-server`, becomes the service row `company-server-key`.

Each was sealed with the vault's own data key, so nothing else is needed to open it. One that does not open here (KMS
unreachable, the key file missing) is left where it is and logged by name, never by value, and the start goes on. Its
owner still opens it there, and every later start moves what is left (`settle`, from backend/app.py). Running this again
changes nothing: a moved value is no longer where this looks.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from backend.credentials import KEPT, KEY_FILE, CredentialCipher, keep_service
from backend.store import Problem

SUBSCRIPTION = "model-subscription-"
# registry_metadata key -> {record field: (service row, its name, the context it was sealed under, source)}
RECORDS = {
    "gateway-connection": {
        "key": ("gateway-key", "Tico AI Gateway key", "gateway-connection:key", "gateway"),
        "install_secret": (
            "gateway-install-secret",
            "Tico AI Gateway install secret",
            "gateway-connection:install",
            "gateway",
        ),
    },
    "company-server": {"key": ("company-server-key", "Tico company server key", "company-server:key", "company")},
}
SEALED_SUBSCRIPTIONS = (
    "SELECT id,sealed,added_by,created_at FROM model_subscriptions WHERE credential_id IS NULL AND sealed<>''"
)
log = logging.getLogger("tico.migrations.one_store")


def up(conn, kms=None):
    # The vault's cipher reads its key row by column name, whatever rows this connection makes.
    factory, conn.row_factory = conn.row_factory, sqlite3.Row
    try:
        folder = Path(next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")).parent
        kms_key = os.environ.get("TICO_CREDENTIAL_KMS_KEY", "").strip()
        move(conn, CredentialCipher(kms_key, kms, key_file=folder / KEY_FILE))
    finally:
        conn.row_factory = factory


def _subscriptions(conn):
    """Whether `model_subscriptions` is here with the column it moves to."""
    return "credential_id" in {r[1] for r in conn.execute("PRAGMA table_info(model_subscriptions)")}


def _record(conn, key):
    row = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        record = json.loads(row[0]) if row else None
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _sealed(record, field):
    stored = record.get(field)
    return isinstance(stored, str) and bool(stored) and not stored.startswith(KEPT)


def left(conn):
    """Whether anything is still sealed outside the vault."""
    if _subscriptions(conn) and conn.execute(SEALED_SUBSCRIPTIONS + " LIMIT 1").fetchone():
        return True
    return any(
        record and any(_sealed(record, field) for field in fields)
        for record, fields in ((_record(conn, key), fields) for key, fields in RECORDS.items())
    )


def _open(conn, cipher, context, stored, name):
    """The value, or None (logged) when the vault's key is out of reach or does not open it."""
    try:
        return cipher.unseal(conn, context, stored)
    except Problem as exc:
        log.warning("secret not moved into the vault: category=%s id=%s detail=%s", exc.code, name, exc.detail)
        return None


def move(conn, cipher):
    """Move every value still sealed outside the vault, each on its own. Returns how many moved."""
    moved = 0
    if _subscriptions(conn):
        for sub in conn.execute(SEALED_SUBSCRIPTIONS).fetchall():
            value = _open(
                conn, cipher, "model_subscription:" + sub["id"], sub["sealed"], "model_subscriptions:" + sub["id"]
            )
            if value is None:
                continue
            cid = SUBSCRIPTION + sub["id"]
            keep_service(
                conn,
                cipher,
                cid,
                "Model subscription",
                value,
                sub["added_by"],
                "model_subscriptions",
                sub["created_at"],
            )
            conn.execute("UPDATE model_subscriptions SET credential_id=?,sealed='' WHERE id=?", (cid, sub["id"]))
            moved += 1
    for key, fields in RECORDS.items():
        record = _record(conn, key)
        if not record:
            continue
        before = moved
        for field, (cid, name, context, source) in fields.items():
            if not _sealed(record, field):
                continue
            value = _open(conn, cipher, context, record[field], f"{key}:{field}")
            if value is not None:
                record[field] = keep_service(conn, cipher, cid, name, value, "tico", source)
                moved += 1
        if moved > before:
            conn.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(record, sort_keys=True), key))
    return moved


def settle(store, cipher):
    """At each start, after the vault's key is warm: move what an earlier start could not. A read when nothing is left."""
    try:
        with store.read() as c:
            if not left(c):
                return 0
        with store.transaction() as c:
            moved = move(c, cipher)
    except (Problem, sqlite3.Error, OSError, ValueError) as exc:  # a busy database or a vault out of reach: next start
        log.warning("secrets not moved into the vault: category=unavailable detail=%s", type(exc).__name__)
        return 0
    if moved:
        log.info("Moved %d secrets into the vault", moved)
    return moved
