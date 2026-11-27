"""The Company page is retired: its OKs (`edition:` to publish an edition, `board:` to approve a stats board) can no
longer be given or used, since nothing says who may OK them. Any still open is taken back, so it no longer waits on a
person or shows Approve. A used one stays as it was, in its task's history."""

from datetime import datetime, timezone


def up(conn):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    conn.execute(
        "UPDATE requirements SET withdrawn_at=? WHERE withdrawn_at IS NULL AND id IN (SELECT requirement_id FROM "
        "ok_grants WHERE used_at IS NULL AND (subject LIKE 'edition:%' OR subject LIKE 'board:%'))",
        (now,),
    )
