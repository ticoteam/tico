"""Helpers that other tests share, moved out of test_task_trigger_rules.py so they import no test file."""

import json
from backend import hubdb as H


def rule(
    api,
    event="task.created",
    action="note",
    params=None,
    *,
    scope_kind="team",
    scope_id="",
    when=None,
    by="human:ana",
    then="action",
):
    """A rule row as `by` would have saved it: actors named in full, it acts as its maker, last in its scope."""
    params = {"action": action, **(params or {"text": "A note"})} if then == "action" else (params or {})
    with api.app.state.store.transaction() as c:
        when = {k: H.resolve_actor(c, v) if k in ("owner", "requester") else v for k, v in (when or {}).items()}
        position = (
            c.execute(
                "SELECT max(position) FROM rules WHERE scope_kind=? AND scope_id=?", (scope_kind, scope_id)
            ).fetchone()[0]
            or 0
        ) + 1
        row = {
            "id": "rule-" + H.new_id(),
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "event": event,
            "condition": json.dumps(when, sort_keys=True),
            "then_outcome": then,
            "params": json.dumps(params, sort_keys=True),
            "enabled": 1,
            "position": position,
            "note": "",
            "created_by": by,
            "action_by": by,
            "created_at": H.now(),
            "updated_at": H.now(),
            "revision": 1,
        }
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return row
