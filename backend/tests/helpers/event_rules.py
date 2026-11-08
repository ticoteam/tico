"""Helpers that other tests share, moved out of test_event_rules.py so they import no test file."""

import json
from backend import hubdb as H
from backend import rules as R


CALL = {
    "trigger": "shop",
    "trigger_name": "Acme shop",
    "type": "order.created",
    "delivery": "d1",
    "payload": {"order": 42, "customer": {"name": "Buyer"}},
    "via": "direct",
}


def rule(
    api, by="human:ben", scope_kind="team", scope_id="", event="webhook.received", then="wake", when=None, params=None
):
    """A row in the rules table, as the engine reads it."""
    row = {
        "id": "rule_" + H.new_id()[:8],
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "event": event,
        "condition": json.dumps(when or {}),
        "then_outcome": then,
        "params": json.dumps(params or {}),
        "created_by": by,
        "created_at": H.now(),
        "updated_at": H.now(),
    }
    with api.app.state.store.transaction() as c:
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return {"rule": {**row, "params": params or {}}}


def judge(api, ctx=None, **more):
    with api.app.state.store.transaction() as c:
        return R.judge_event(c, "webhook.received", {**CALL, **(ctx or {}), **more})
