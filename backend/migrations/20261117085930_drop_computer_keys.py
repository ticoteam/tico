"""Computer keys go: a model key is a subscription in Settings > Models and nothing else (backend/model_subscriptions.py).

20261117061243_computer_keys_to_subscriptions moved every key a computer used into subscriptions. This drops what was
left of the Settings > Computers key store, and it runs whether or not that move ran first. It never deletes what may
be a computer's only working login: a key job a runner may already have acted on is left alone, and so are its keys.

1. A key still in use in `computer_keys` moves as that migration moves it (it is run again here: a key it moved
   already is skipped).
2. A computer login switched to a subscription (`model_login_key_off`, set once the runner said the switch was done)
   takes no key since. The key it is given from Settings > Models, if any, is taken off it, so it keeps signing in
   with its own subscription and nobody is billed differently. A person's plans given to it stay. A switch still
   running is not one of these: the runner may still be on the key, or have gone back to it.
3. A key job (`clear_key`, `use_key`, `to_key` and `to_subscription`) the runner has not taken yet (`requested`) is
   canceled, and the key staged for it is deleted from the vault unless a subscription points at it or someone other
   than the computers holds a grant to it.
4. One the runner has taken (`starting`, `waiting` or `finishing`) keeps running, and the server takes its reports as
   they come (backend/model_login.py LEGACY_VERDICTS). Its keys stay: the key it replaces stays given, and a key
   staged for Switch to API key (`to_key`), which the runner may have put in place and signed the subscription out
   for already, moves as a key in use does and is given to the computer, so the key fetch keeps handing it out (a
   runner still on its subscription keeps its own key-off mark and takes no key). Any other staged key stays in the
   vault, given to nobody: someone can delete it in Settings later.
5. `computer_keys` (with its index) and `model_login_key_off` are dropped.
"""

import importlib
from datetime import datetime, timezone

KEY_KINDS = ("clear_key", "use_key", "to_key", "to_subscription")
TAKEN = "state IN ('starting','waiting','finishing')"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _kinds():
    return f"kind IN ({','.join('?' * len(KEY_KINDS))})"


def _drop_unsent(conn, tables):
    """Delete each key staged for a job the runner never took that nothing else holds (as
    model_subscriptions.drop_credential does)."""
    if "model_logins" not in tables:
        return
    for (cid,) in conn.execute(
        "SELECT k.credential_id FROM computer_keys k JOIN model_logins l ON l.id=k.login_id "
        f"WHERE k.state='staged' AND l.state='requested' AND l.{_kinds()}",
        KEY_KINDS,
    ).fetchall():
        if (
            "model_subscriptions" in tables
            and conn.execute("SELECT 1 FROM model_subscriptions WHERE credential_id=?", (cid,)).fetchone()
        ):
            continue
        if conn.execute(
            "SELECT 1 FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL "
            "AND subject<>'computers' AND subject NOT LIKE 'computer:%'",
            (cid,),
        ).fetchone():
            continue
        conn.execute("DELETE FROM computer_keys WHERE credential_id=?", (cid,))
        conn.execute("DELETE FROM credential_grants WHERE credential_id=?", (cid,))
        conn.execute("DELETE FROM credentials WHERE id=?", (cid,))


def up(conn, now=None):
    now = now or _now()
    move = importlib.import_module("backend.migrations.20261117061243_computer_keys_to_subscriptions")
    # 1. Whatever the move left to do (it also adds `model_subscriptions.credential_id` on a file that lacks it).
    move.up(conn, now)
    tables = _tables(conn)
    # 2. Switches to a subscription that ended.
    if {"model_login_key_off", "computer_subscriptions", "model_subscriptions"} <= tables:
        for runner_id, runtime in conn.execute(
            "SELECT runner_id,runtime FROM model_login_key_off WHERE profile='' ORDER BY runner_id,runtime"
        ).fetchall():
            conn.execute(
                "DELETE FROM computer_subscriptions WHERE runner_id=? AND runtime=? AND person='' AND subscription_id "
                "IN (SELECT id FROM model_subscriptions WHERE credential_id IS NOT NULL)",
                (runner_id, runtime),
            )
    if {"computer_keys", "model_logins"} <= tables:
        # 4. A Switch to API key the runner took: its key is in use, or may be.
        promoted = conn.execute(
            "UPDATE computer_keys SET state='active' WHERE state='staged' AND login_id IN "
            f"(SELECT id FROM model_logins WHERE kind='to_key' AND {TAKEN})"
        ).rowcount
        if promoted:
            move.up(conn, now)
        # 3. The jobs the runner never took, and their keys.
        _drop_unsent(conn, tables)
    if "model_logins" in tables:
        conn.execute(
            "UPDATE model_logins SET state='canceled', url='', user_code='', pending_code=NULL, updated_at=? "
            f"WHERE {_kinds()} AND state='requested'",
            (now, *KEY_KINDS),
        )
    # 5.
    conn.execute("DROP INDEX IF EXISTS computer_keys_by_runner")
    conn.execute("DROP TABLE IF EXISTS computer_keys;")
    conn.execute("DROP TABLE IF EXISTS model_login_key_off;")
