"""The Company page is gone, and with it the rules' "Outside editions" Who: no edition reads a rule any more. A rule
whose only Who was Outside editions never named a teammate, so it is retired by Tico (an empty Who must not read as
Everyone), and its OK, if one still waits, is taken back. Beside teammates the flag is dropped and the rule applies to
them as before. Rule history keeps the old Who as it was."""

from datetime import datetime, timezone

TICO = "keeper"  # backend/hubdb_core.py TICO_ACTOR
FLAGGED = "json_extract(who, '$.outside_editions')"
NO_TEAMMATE = (
    "NOT coalesce(json_extract(who, '$.everyone'), 0) AND NOT coalesce(json_extract(who, '$.all_bots'), 0) "
    "AND coalesce(json_array_length(who, '$.groups'), 0) = 0 AND coalesce(json_array_length(who, '$.bots'), 0) = 0 "
    "AND coalesce(json_array_length(who, '$.people'), 0) = 0"
)


def up(conn):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    alone = [
        rule_id
        for (rule_id,) in conn.execute(
            f"SELECT id FROM org_rules WHERE {FLAGGED} AND status != 'retired' AND {NO_TEAMMATE}"
        ).fetchall()
    ]
    for rule_id in alone:
        conn.execute(
            "UPDATE org_rules SET status='retired', decided_by=?, decided_at=?, version=version+1, updated_at=? "
            "WHERE id=?",
            (TICO, now, now, rule_id),
        )
        conn.execute(
            "UPDATE requirements SET withdrawn_at=? WHERE withdrawn_at IS NULL AND id IN (SELECT requirement_id FROM "
            "ok_grants WHERE used_at IS NULL AND subject=?)",
            (now, "rule:" + rule_id),
        )
    conn.execute(f"UPDATE org_rules SET who=json_remove(who, '$.outside_editions') WHERE {FLAGGED} IS NOT NULL")
