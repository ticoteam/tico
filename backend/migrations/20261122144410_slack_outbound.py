"""Copy Slack posts into the outbound queue while an older gateway can still finish its writes."""

import json

from backend import hubdb as H

STATES = {
    "ready": "queued",
    "retry": "queued",
    "rate_limited": "queued",
    "sending": "unknown",
    "uncertain": "unknown",
    "sent": "done",
    "canceled": "canceled",
    "failed": "failed",
}
HELD = {"ready", "retry", "rate_limited", "sending"}


def _copy(conn, post, hold_until, *, trashed=False):
    state = STATES[post["state"]]
    if trashed and state == "queued":
        state = "canceled"
    mid = post["message_id"]
    metadata = {"thread_ts": post["thread_ts"], "recipient_actor": post.get("recipient_actor")}
    if not post["text"]:
        metadata["redacted"] = True
    if state == "done" and post["text"]:
        metadata["_tico"] = {"sent_text": post["text"]}
    if not trashed and (post["state"] in HELD or state == "unknown"):
        metadata["_tico"] = {"slack_legacy": f"{post['state']}:{post['updated_at']}"}
    base = f"slack:post:{mid}"
    key, suffix = base, 0
    # Before cutover callers could use any key. Keep their keys and every Slack message id.
    while conn.execute("SELECT 1 FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone():
        suffix += 1
        key = f"{base}:{suffix}"
    conn.execute(
        "INSERT INTO outbound_actions(id,channel,audience,operation,destination,occurrence_key,actor,payload,"
        "metadata,state,attempts,send_after,next_attempt_at,receipt,last_error,created_at,updated_at,task_id) "
        "VALUES(?,'slack','external','post',?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
        (
            mid,
            post["channel"],
            key,
            f"bot:{post['bot']}" if post["bot"] else "keeper",
            json.dumps({"text": post["text"]}) if state in ("queued", "unknown") and not trashed else "",
            json.dumps(metadata),
            state,
            int(post.get("attempts") or 0),
            hold_until if post["state"] in HELD and not trashed else post["created_at"],
            post.get("next_attempt_at") or post["created_at"],
            json.dumps({"ts": post["slack_ts"]}) if post.get("slack_ts") else "{}",
            post.get("error") or ("ratelimited" if post["state"] == "rate_limited" else ""),
            post["created_at"],
            post["updated_at"],
            post.get("task_id"),
        ),
    )


def _keep_legacy(conn):
    # Deleting a Tico message must neither fail nor remove a receipt an old gateway can still finish.
    conn.execute(
        "CREATE TABLE slack_posts_kept (message_id TEXT PRIMARY KEY, channel TEXT NOT NULL, "
        "thread_ts TEXT NOT NULL, bot TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready', "
        "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, slack_ts TEXT, error TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, recipient_actor TEXT)"
    )
    conn.execute("INSERT INTO slack_posts_kept SELECT * FROM slack_posts")
    conn.execute("ALTER TABLE slack_posts RENAME TO slack_posts_with_message_fk")
    conn.execute("ALTER TABLE slack_posts_kept RENAME TO slack_posts")
    conn.execute("DROP TABLE slack_posts_with_message_fk")
    conn.execute("CREATE INDEX slack_posts_state ON slack_posts(state, next_attempt_at)")


def up(conn):
    hold_until = H.shift(H.now(), minutes=2)
    posts = conn.execute(
        "SELECT p.*, m.refs, c.task_id FROM slack_posts p LEFT JOIN messages m ON m.id=p.message_id "
        "LEFT JOIN conversations c ON c.id=m.conversation_id"
    )
    columns = [col[0] for col in posts.description]
    for row in posts:
        post = dict(zip(columns, row, strict=True))
        try:
            refs = json.loads(post.pop("refs") or "{}")
        except ValueError:
            refs = {}
        if isinstance(refs, dict):
            for key in ("task", "task_id"):
                value = refs.get(key)
                if isinstance(value, list):
                    value = next((v for v in value if isinstance(v, str) and v.strip()), None)
                if isinstance(value, str) and value.strip():
                    post["task_id"] = value.strip()
                    break
        _copy(conn, post, hold_until)
    _keep_legacy(conn)
    # Both gateways need a dedupe marker when a snapshot puts a deleted message back.
    for task_id, raw in conn.execute(
        "SELECT task_id,rows FROM task_trash WHERE rows LIKE '%\"slack_posts\"%'"
    ).fetchall():
        snapshot = json.loads(raw)
        posts = snapshot.get("rows", {}).pop("slack_posts", None)
        if posts is None:
            continue
        for post in posts:
            for before, after in (
                ("created", "created_at"),
                ("updated", "updated_at"),
                ("next_attempt", "next_attempt_at"),
            ):
                if before in post:
                    post.setdefault(after, post[before])
            _copy(conn, {**post, "task_id": task_id}, hold_until, trashed=True)
            conn.execute(
                "INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,error,created_at,updated_at,"
                "recipient_actor) VALUES(?,?,?,?,'','canceled','outbound',?,?,?) ON CONFLICT(message_id) DO NOTHING",
                (
                    post["message_id"],
                    post["channel"],
                    post["thread_ts"],
                    post["bot"],
                    post["created_at"],
                    post["updated_at"],
                    post.get("recipient_actor"),
                ),
            )
        conn.execute("UPDATE task_trash SET rows=? WHERE task_id=?", (json.dumps(snapshot), task_id))
    conn.execute(
        "CREATE INDEX outbound_slack_legacy ON outbound_actions(id) "
        "WHERE channel='slack' AND json_extract(metadata,'$._tico.slack_legacy') IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX outbound_slack_destination ON outbound_actions(destination, created_at) WHERE channel='slack'"
    )
    conn.execute(
        "CREATE INDEX outbound_slack_task ON outbound_actions(task_id) WHERE channel='slack' AND task_id IS NOT NULL"
    )
