"""An update records the writing rules it was checked by, and a delivery turn has its own columns.

`updates.format_version` is 1 for every update posted before this change, which the one-to-five bullet linter checked,
and 2 for the three-bullet shape (`updates.lint`). Only version 2 is linted again: `purge_rejected` never deletes a version 1
update, since it was never held to the new rules.

Three columns hold what a turn reports back: `learnings_saved` (how many learnings the bot kept), `suggestions_seen` (the
suggestions it acted on, a JSON list) and `delivery_batch_id` (the delivery the post answered). `update_queue.delivery_only`
marks a request that only delivers learnings or suggestions: a completed turn that posts nothing ends
`delivered_without_update` and is not asked again.

On a new file the update tables do not exist yet, so this skips them: updates.SCHEMA makes the same columns. The
default of 1 is the same in both places, so a row written without naming a version reads as the old shape. Running it
again changes nothing.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "updates" in tables:
        have = _columns(conn, "updates")
        if "format_version" not in have:
            conn.execute("ALTER TABLE updates ADD COLUMN format_version INTEGER NOT NULL DEFAULT 1")
        for name, declaration in (("learnings_saved", "INTEGER"), ("suggestions_seen", "TEXT"),
                                  ("delivery_batch_id", "TEXT")):
            if name not in have:
                conn.execute(f"ALTER TABLE updates ADD COLUMN {name} {declaration}")
    if "update_queue" in tables and "delivery_only" not in _columns(conn, "update_queue"):
        conn.execute("ALTER TABLE update_queue ADD COLUMN delivery_only INTEGER NOT NULL DEFAULT 0")
