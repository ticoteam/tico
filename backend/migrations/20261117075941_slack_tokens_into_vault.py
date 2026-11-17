"""The Slack tokens an older server sealed in its own `slack_credentials` table become two vault rows.

Before this, Tools > Slack sealed both tokens in one row with `slack.key` beside the database, or with the vault's data
key under TICO_CREDENTIAL_KMS_KEY (and a row saved before KMS was set still opened with `slack.key`). Now they are
`credentials` rows of kind `service` (backend/slack_app.py), sealed with the vault's data key like every credential, and
the vault's backup covers them. The row is opened the way that server opened it (`credentials.open_or_reseal`), so this
file is the only reader of `slack.key` left: a restored backup that still has the table needs its `slack.key` beside it.

When the row does not open here (KMS unreachable, or slack.key missing), nothing is changed and the start goes on: the
table is left, the reason is logged, and the gateway moves a row the vault's key opens on its next read
(backend/slack_app.py `load`). Otherwise the table is dropped.

Running this again changes nothing: the table is gone.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from cryptography.exceptions import InvalidTag

from backend.credentials import KEY_FILE, CredentialCipher, open_or_reseal
from backend.store import Problem

AAD = b"tico-slack-app:v1"
# (credential id, name, token field, column of the id pinned from it)
TOKENS = (
    ("slack-bot-token", "Slack bot token", "bot_token", "team_id"),
    ("slack-app-token", "Slack app-level token", "app_token", "app_id"),
)
log = logging.getLogger("tico.migrations.slack_tokens")


def up(conn, kms=None):
    # The vault's cipher reads its key row by column name, whatever rows this connection makes.
    factory, conn.row_factory = conn.row_factory, sqlite3.Row
    try:
        _move(conn, kms)
    finally:
        conn.row_factory = factory


def _move(conn, kms):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='slack_credentials'").fetchone():
        return
    row = conn.execute("SELECT * FROM slack_credentials WHERE id='app'").fetchone()
    if row:
        folder = Path(next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")).parent
        kms_key = os.environ.get("TICO_CREDENTIAL_KMS_KEY", "").strip()
        cipher = CredentialCipher(kms_key, kms, key_file=folder / KEY_FILE)
        slack_key = folder / "slack.key"
        try:
            # The key that server opened them with: the vault's under KMS (slack.key for a row from before KMS), else
            # slack.key alone.
            key = cipher.key(conn) if kms_key else slack_key.read_bytes()
            legacy = slack_key if kms_key else None
            plain = open_or_reseal(
                conn, key, legacy, row["nonce"], row["ciphertext"], AAD, "slack_credentials", "id", "app", log
            )
            tokens = json.loads(plain)
            sealed = [
                (cid, name, row[pin], *cipher.encrypt(conn, cid, tokens[field])) for cid, name, field, pin in TOKENS
            ]
        except (Problem, InvalidTag, OSError, ValueError, KeyError, TypeError) as exc:
            # The vault's key out of reach (Problem), or a key that does not open the row: a key file missing or wrong.
            log.warning(
                "slack tokens not moved into the vault: category=%s id=slack_credentials:app detail=%s",
                "vault_unavailable" if isinstance(exc, Problem) else "cannot_open",
                type(exc).__name__,
            )
            return
        for cid, name, pinned, ciphertext, nonce in sealed:
            conn.execute(
                "INSERT INTO credentials(id,name,username,kind,ciphertext,nonce,source,created_at,updated_at,updated_by,"
                "created_by) VALUES(?,?,?,'service',?,?,'slack',?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (cid, name, pinned, ciphertext, nonce, row["created_at"], row["created_at"], *[row["created_by"]] * 2),
            )
    conn.execute("DROP TABLE IF EXISTS slack_credentials;")
