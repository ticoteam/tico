"""Helpers that other tests share, moved out of test_triggers.py so they import no test file."""

import json
from urllib.parse import urlsplit
from backend import hubdb as H, tico_notices
from backend.tests.helpers.api import post


def hook(api, url, body=b'{"order": 42}', headers=None):
    return api.post(urlsplit(url).path, content=body, headers={"content-type": "application/json", **(headers or {})})


def listener(api, trigger_id, bot="ops", text="Ship it.", name="New order"):
    """An automation on the webhook whose Bot turn hands `bot` the call, the way a routine on `webhook.<id>` did."""
    return post(
        api,
        "automations",
        {
            "name": name,
            "owner": "person:ana",
            "trigger": {"type": "webhook", "id": trigger_id},
            "steps": [{"id": "work", "type": "bot_turn", "bot": bot, "prompt": text + " {{webhook.content}}"}],
        },
    )["id"]


def rule_row(
    api, params, event="webhook.received", by="human:ana", scope_kind="team", scope_id="", when=None, then="action"
):
    """A row in the rules table, as the rules engine reads it (a kept row): what the rules editor wrote."""
    with api.app.state.store.transaction() as c:
        position = (
            c.execute(
                "SELECT max(position) FROM rules WHERE scope_kind=? AND scope_id=? AND position<?",
                (scope_kind, scope_id, tico_notices.RULE_POSITION),
            ).fetchone()[0]
            or 0
        ) + 1
        row = {
            "id": "rule-" + H.new_id(),
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "event": event,
            "condition": json.dumps(when or {}, sort_keys=True),
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


def turns(api, name="New order"):
    """The tasks an automation's Bot turns opened: each is titled with its automation's name."""
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE title=? ORDER BY created_at, id", (name,))]


def sig(key, body):
    import hashlib
    import hmac

    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
