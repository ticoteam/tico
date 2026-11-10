"""Web Push subscriptions (backend/web_push.py, docs/push.md): one row per browser a person turned push on in.

- push_subscriptions: `actor` is the person (`human:<id>`); `endpoint` is the push service's URL for that browser and is
  unique, so a browser signed in to by someone else moves to them. `p256dh` and `auth` are the browser's keys the message
  is encrypted for. `last_ok_at` is the latest success; `failed_at` is when the current run of failures began and
  `failures` how many it has (both cleared by a success). `gone_at` is set when the push service answers 404 or 410, or
  the failures go on too long, after which the row is never sent to again.

Nothing is backfilled: a browser subscribes when its person turns browser notifications on.
"""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS push_subscriptions ("
        "id TEXT PRIMARY KEY, actor TEXT NOT NULL, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, "
        "auth TEXT NOT NULL, created_at TEXT NOT NULL, last_ok_at TEXT, failed_at TEXT, failures INTEGER NOT NULL DEFAULT 0, "
        "gone_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS push_subscriptions_actor ON push_subscriptions(actor, created_at)")
