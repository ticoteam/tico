"""The Company page is gone, and with it the rules' "Outside editions" Who: no edition reads a rule any more. A rule
whose only Who was Outside editions never named a teammate, so Tico retires it (an empty Who must not read as
Everyone): its history gains that version, and an OK still waiting on it is withdrawn the normal way, its task moving
on if that was all it waited for (backend/org_rules.py `retire_for_tico`). Beside teammates the flag is dropped and the
rule applies to them as before. Earlier history versions keep the old Who as it was."""

import json

from backend import org_rules

WHY = "The Company page is gone, and this rule's only Who was Outside editions"
FLAGGED = "json_extract(who, '$.outside_editions')"
NO_TEAMMATE = (
    "NOT coalesce(json_extract(who, '$.everyone'), 0) AND NOT coalesce(json_extract(who, '$.all_bots'), 0) "
    "AND coalesce(json_array_length(who, '$.groups'), 0) = 0 AND coalesce(json_array_length(who, '$.bots'), 0) = 0 "
    "AND coalesce(json_array_length(who, '$.people'), 0) = 0"
)


def up(conn):
    alone = conn.execute(
        f"SELECT id, who FROM org_rules WHERE {FLAGGED} AND status != 'retired' AND {NO_TEAMMATE}"
    ).fetchall()
    for rule_id, who in alone:
        scope = json.loads(who)
        scope.pop("outside_editions", None)
        org_rules.retire_for_tico(conn, rule_id, WHY, who=scope)
    conn.execute(f"UPDATE org_rules SET who=json_remove(who, '$.outside_editions') WHERE {FLAGGED} IS NOT NULL")
