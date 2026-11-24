"""Each stage's expected time becomes its Late stage rule (backend/stage_rules.py `late_rule`).

Late was its own code path in the never-stuck check, driven by `stages.expected_minutes`. It is now an ordinary timer
rule, shown and edited in the stage's Rules panel: N h in the stage on the bot's turn tells the owner bot. Every live
work stage of a live pipeline with an expected time gets one, `sr_late_<stage id>`, written as Tico, with the words
`late_rule` writes. A stage that already has one keeps it, so running this again changes nothing.

The old columns (`stages.expected_minutes`, `expected_bots_only`, `visits.late_told_at`, `late_person_at`) stay for one
release, so a revert within it loses nothing; nothing reads or writes them any more, and a later migration drops them.
"""

import json
from datetime import datetime, timezone

TICO = "keeper"  # Tico itself (hubdb_core.TICO_ACTOR)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def up(conn):
    if not {"stages", "pipelines", "stage_rules"} <= _tables(conn):
        return
    ts = _now()
    for stage_id, name, pipeline_id, minutes in conn.execute(
        "SELECT s.id, s.name, s.pipeline_id, s.expected_minutes FROM stages s JOIN pipelines p ON p.id = s.pipeline_id "
        "WHERE s.kind = 'work' AND s.retired_at IS NULL AND p.retired_at IS NULL AND s.expected_minutes > 0 "
        "ORDER BY s.pipeline_id, s.position, s.id"
    ).fetchall():
        hours = round(int(minutes) / 60, 2)
        conn.execute(
            "INSERT OR IGNORE INTO stage_rules (id, name, pipeline_id, stages, event, timer, cond, jev, then_do, "
            "then_params, reason, status, builtin, created_by, created_at, updated_at) "
            "VALUES (?, 'Late', ?, ?, 'timer', ?, ?, '', 'tell_owner', '{}', ?, 'active', 'late', ?, ?, ?)",
            (
                f"sr_late_{stage_id}",
                pipeline_id,
                json.dumps([stage_id]),
                json.dumps({"kind": "in_stage", "hours": hours}),
                json.dumps({"owner_kind": "bot", "turn": "bot"}),
                f"{hours:g} h in {name} on the bot's turn",
                TICO,
                ts,
                ts,
            ),
        )
