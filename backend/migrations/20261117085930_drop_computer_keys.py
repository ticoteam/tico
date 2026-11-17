"""Computer keys go: a model key is a subscription in Settings > Models and nothing else (backend/model_subscriptions.py).

20261117061243_computer_keys_to_subscriptions moved every key a computer used into subscriptions. This drops what was
left of the Settings > Computers key store, and it runs whether or not that move ran first:

1. A key still in use in `computer_keys` moves as that migration moves it (it is run again here: a key it moved
   already is skipped).
2. The key jobs are gone (`clear_key`, `use_key`, `to_key` and `to_subscription`): one still running is canceled. A
   computer told to clear its key is still told so at its next key fetch (`model_login_cleared.clear_key` stays).
3. A key staged for a check that never ended is deleted from the vault, unless a subscription points at it or someone
   other than the computers holds a grant to it. Any other key left in `computer_keys` stays in the vault.
4. A computer login switched to a subscription (`model_login_key_off`, or a switch already putting the subscription in
   place) took no key since. The key it is given from Settings > Models, if any, is taken off it, so it keeps
   signing in with its own subscription and nobody is billed differently. A person's plans given to it stay.
5. `computer_keys` (with its index) and `model_login_key_off` are dropped.
"""

import importlib
from datetime import datetime, timezone

KEY_KINDS = ("clear_key", "use_key", "to_key", "to_subscription")
ACTIVE = "state IN ('requested','starting','waiting','finishing')"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _drop_staged(conn, tables):
    """Delete each staged key's vault value that nothing else holds (as model_subscriptions.drop_credential does)."""
    for (cid,) in conn.execute("SELECT credential_id FROM computer_keys WHERE state='staged'").fetchall():
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


def _keys_off(conn, tables):
    """(runner_id, runtime) of every computer login switched to a subscription: marked, or putting it in place now."""
    found = set()
    if "model_login_key_off" in tables:
        found |= {
            (r[0], r[1]) for r in conn.execute("SELECT runner_id,runtime FROM model_login_key_off WHERE profile=''")
        }
    if "model_logins" in tables:
        found |= {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT runner_id,runtime FROM model_logins WHERE kind='to_subscription' AND profile='' "
                "AND state='finishing'"
            )
        }
    return found


def up(conn, now=None):
    now = now or _now()
    # 1. Whatever the move left to do (it also adds `model_subscriptions.credential_id` on a file that lacks it).
    importlib.import_module("backend.migrations.20261117061243_computer_keys_to_subscriptions").up(conn, now)
    tables = _tables(conn)
    # 4. Read before 2 cancels the switches still putting a subscription in place.
    off = _keys_off(conn, tables)
    if off and {"computer_subscriptions", "model_subscriptions"} <= tables:
        for runner_id, runtime in sorted(off):
            conn.execute(
                "DELETE FROM computer_subscriptions WHERE runner_id=? AND runtime=? AND person='' AND subscription_id "
                "IN (SELECT id FROM model_subscriptions WHERE credential_id IS NOT NULL)",
                (runner_id, runtime),
            )
    # 2. The key jobs still running.
    if "model_logins" in tables:
        conn.execute(
            "UPDATE model_logins SET state='canceled', url='', user_code='', pending_code=NULL, updated_at=? "
            f"WHERE kind IN ({','.join('?' * len(KEY_KINDS))}) AND {ACTIVE}",
            (now, *KEY_KINDS),
        )
    # 3. Staged keys.
    if "computer_keys" in tables:
        _drop_staged(conn, tables)
    # 5.
    conn.execute("DROP INDEX IF EXISTS computer_keys_by_runner")
    conn.execute("DROP TABLE IF EXISTS computer_keys;")
    conn.execute("DROP TABLE IF EXISTS model_login_key_off;")
