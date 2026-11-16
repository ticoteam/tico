"""Rows saved before the Release 1 cutover keep working after it: a webhook send queued before the API v2 rename, and a
rule or automation saved under the old column names (backend/migrations/renames.py reads them under the new ones)."""

import json

from backend import automations as AU
from backend import hubdb as H
from backend import rules as R
from backend.migrations import renames
from backend.tests.helpers.webhooks import make, net, post, run, sends, verify  # noqa: F401
from backend.tests.helpers.tasks_board import api  # noqa: F401

ANA, CMO = H.human_actor("ana"), H.bot_actor("cmo")


def test_a_webhook_send_queued_before_the_run_rename_is_delivered_as_a_run_and_signed(api, net):
    made = make(api, events=["custom.*"])
    post(api, f"webhooks/{made['id']}/send", {"type": "custom.order", "data": {"order": 42}})
    (row,) = sends(api)
    released = json.loads(row["body"])
    released["data"] = {"order": 42, "attempt_id": "att-1"}  # the body the released server queued
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE webhook_sends SET body=? WHERE id=?",
            (json.dumps(released, separators=(",", ":"), ensure_ascii=False), row["id"]),
        )
    assert run(api, net) == 1
    sent = net.seen[0]
    assert json.loads(sent.content)["data"] == {"order": 42, "run_id": "att-1"}
    assert verify(made["secret"], dict(sent.headers), sent.content)


def test_a_rule_condition_saved_under_the_old_owner_and_requester_names_still_matches_after_the_rename(monkeypatch):
    monkeypatch.setattr(
        renames,
        "COLUMNS",
        renames.COLUMNS + (("tasks", "owner", "owner_id", "3"), ("tasks", "requester", "requester_id", "3")),
    )
    row = {"id": "task-1", "status": "open", "owner_id": "bot:cmo", "requester_id": "human:ana", "type_id": "general"}
    ctx = R._Context(None, H.KEEPER, CMO, {}, None, row)
    assert ctx.matches({"owner": ["bot:cmo"]})
    assert ctx.matches({"requester": "human:ana"})
    assert ctx.matches({"role": "owner"})
    assert not ctx.matches({"owner": ["bot:ops"]})


def test_a_task_received_automation_still_hears_the_requester_after_the_rename(monkeypatch):
    monkeypatch.setattr(renames, "COLUMNS", renames.COLUMNS + (("tasks", "requester", "requester_id", "3"),))
    row = {"id": "task-1", "title": "Ship", "body": "b", "status": "open", "requester_id": "human:ana", "step_id": None}
    heard = AU._received_task(row, "bot:cmo")
    assert heard["requester"] == "human:ana" and heard["owner"] == "bot:cmo" and heard["title"] == "Ship"
