"""Helpers that other tests share, moved out of test_automation_mail_actions.py so they import no test file."""

import json
from datetime import datetime, timedelta, timezone

import time_machine

from backend.store import H
from backend.tests.helpers.api import get, post


BOX = "ana@acme.example"


def automate(api, *steps, **extra):
    return post(
        api,
        "automations",
        {
            "name": "Invoices",
            "owner": "person:ana",
            "trigger": {"type": "message", "inbox": BOX},
            "steps": list(steps),
            **extra,
        },
    )["id"]


def owned_by_ana(c, *ids):
    """Automation rows for actions a test queues by hand: the claim checks each action's owner holds the mailbox."""
    now = H.now()
    for aid in ids:
        c.execute(
            "INSERT OR IGNORE INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,"
            "created_by_id,updated_at) VALUES(?,?,?,'manual',0,?,1,?,?,?)",
            (aid, aid, "person:ana", json.dumps({"owner": "person:ana", "steps": []}), now, "person:ana", now),
        )


def last_run(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"][0]


def claim(api, token, expected=200):
    return post(api, "automations/mail-actions/claim", {"mailbox": BOX}, token, expected=expected)


def later(minutes):
    return time_machine.travel(datetime.now(timezone.utc) + timedelta(minutes=minutes), tick=False)
