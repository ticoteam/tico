"""The growth rethink (backend/marketing_tasks.py, backend/strategy.py, backend/growth.py): the idea and go-to-market
tables go, once what they held has a new home. A marketing idea's notes go into its task's body and its channels into
`channel:<slug>` labels, the newest go-to-market page into the strategy draft's How we sell when that is empty,
go-to-market proposals are deleted (a leftover ask for one closes on the Strategy Bot's next sync, which finds nothing
to decide), cohorts lose their segment split, and the verdict settings go. The idea type takes the Marketing name once,
unless the team already has a type of that name.

Each step reads what is there: a file from a release that ran this as hubdb's step 74 (migrations.NEWEST_USER_VERSION)
has already lost the tables, and a note already in a body is not added twice."""
import json

from backend import hubdb as H
from backend import marketing_tasks as MT

RETIRED = ("growth_idea_channels", "growth_idea_segments", "growth_channel_segments", "growth_ideas", "growth_segments",
           "gtm_versions", "gtm_drafts")


def _json_object(text):
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _gtm_markdown(body):
    """The go-to-market page as plain markdown: its motion, buyer and playbooks, one bullet per fact."""
    lines = []
    motion = body.get("motion") or {}
    if motion.get("primary"):
        primary = str(motion["primary"]).replace("_", " ")
        second = str(motion.get("secondary") or "").replace("_", " ")
        lines.append(f"- Motion: {primary}" + (f", then {second}" if second else ""))
    buyer = body.get("buyer") or {}
    for label, key in (("Who buys", "who_buys"), ("Who uses it", "who_uses"), ("Deal size", "deal_size"),
                       ("Sales cycle", "cycle")):
        if str(buyer.get(key) or "").strip():
            lines.append(f"- {label}: {str(buyer[key]).strip()}")
    for item in buyer.get("objections") or []:
        if str(item or "").strip():
            lines.append(f"- Objection: {str(item).strip()}")
    for item in body.get("playbooks") or []:
        if str(item or "").strip():
            lines.append(f"- Playbook: {str(item).strip()}")
    return "\n".join(lines)


def up(conn):
    found = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    stamp = H.now()
    if "task_types" in found:
        conn.execute("UPDATE task_types SET name=?, updated_at=? WHERE id=? AND name IN ('Idea', 'Growth idea') "
                     "AND NOT EXISTS (SELECT 1 FROM task_types WHERE name=? AND id<>?)",
                     (MT.TYPE_NAMES[0], stamp, MT.TYPE_ID, MT.TYPE_NAMES[0], MT.TYPE_ID))
    if {"growth_ideas", "tasks"} <= found:
        for task_id, hypothesis, prediction, result, learning in conn.execute(
                "SELECT i.task_id, i.hypothesis, i.prediction, i.result, i.learning FROM growth_ideas i "
                "JOIN tasks t ON t.id=i.task_id").fetchall():
            body = conn.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()[0] or ""
            # A text already in the body is not copied again: an idea filed with only a hypothesis had it as the body.
            texts = [(label, str(text).strip()) for label, text in (
                ("Hypothesis", hypothesis), ("Prediction", prediction), ("Result", result), ("Learning", learning))
                if str(text or "").strip()]
            missing = [f"{label}: {text}" for label, text in texts if text not in body]
            if missing:
                body = "\n\n".join(part for part in (body.strip(), "\n".join(missing)) if part)
                conn.execute("UPDATE tasks SET body=? WHERE id=?", (body, task_id))
    if {"growth_idea_channels", "growth_channels", "tasks", "tags", "task_tags"} <= found:
        for task_id, slug in conn.execute(
                "SELECT i.task_id, c.slug FROM growth_idea_channels i JOIN growth_channels c ON c.id=i.channel_id "
                "JOIN tasks t ON t.id=i.task_id").fetchall():
            keys = H._labels([f"channel:{slug}"])
            if not keys:
                continue
            conn.execute("INSERT INTO tags (id, key, label, created_at, updated_at) SELECT ?,?,?,?,? WHERE NOT EXISTS "
                         "(SELECT 1 FROM tags WHERE key=?)", (f"tag-{H.new_id()}", keys[0], keys[0], stamp, stamp,
                                                             keys[0]))
            conn.execute("INSERT OR IGNORE INTO task_tags (task_id, tag_id) SELECT ?, id FROM tags WHERE key=?",
                         (task_id, keys[0]))
    if {"gtm_versions", "strategy_versions"} <= found:
        version = conn.execute("SELECT body FROM gtm_versions ORDER BY n DESC LIMIT 1").fetchone()
        draft = conn.execute("SELECT id, sections FROM strategy_versions WHERE status='draft'").fetchone()
        text = _gtm_markdown(_json_object(version[0])) if version else ""
        if draft and text:
            sections = _json_object(draft[1])
            if not str(sections.get("how") or "").strip():
                sections["how"] = text
                conn.execute("UPDATE strategy_versions SET sections=?, revision=revision+1, updated_at=?, "
                             "updated_by=? WHERE id=?", (json.dumps(sections, sort_keys=True), stamp, H.KEEPER, draft[0]))
    if "goal_proposals" in found:
        conn.execute("DELETE FROM goal_proposals WHERE kind='gtm_draft'")
    if "growth_cohorts" in found:
        if "segment_id" in {row[1] for row in conn.execute("PRAGMA table_info(growth_cohorts)")}:
            conn.execute("DELETE FROM growth_cohorts WHERE segment_id IS NOT NULL")
            conn.execute("DROP INDEX IF EXISTS growth_cohorts_month_segment")
            conn.execute("ALTER TABLE growth_cohorts DROP COLUMN segment_id")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS growth_cohorts_month "
                     "ON growth_cohorts(coalesce(channel_id, ''), month)")
    if "growth_settings" in found:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(growth_settings)")}
        for column in ("framework", "verdict_win_pct"):
            if column in columns:
                conn.execute(f"ALTER TABLE growth_settings DROP COLUMN {column}")
    for table in RETIRED:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
