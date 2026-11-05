"""Runner receipts keep the initiating person's rights after a lost response and a finished lease."""
import importlib

import pytest

from backend import bot_settings as BS, store as S
from backend.tests.test_api import api, as_member, assign, headers, post, ready, runner  # noqa: F401
from backend.tests.test_files import turn
from backend.tests.test_make_page import start
from backend.tests.test_meeting_task_links import task


ATTEMPT_RECEIPTS = ("claim", "start-place", "started", "thread", "events", "renew", "capture", "diagnostics",
                    "inputs", "ack", "background-done", "complete")


@pytest.mark.parametrize("endpoint", ATTEMPT_RECEIPTS)
def test_private_make_page_receipts_replay_after_finish_and_recheck_person_and_computer(api, monkeypatch, endpoint):
    as_member(api, "ben@acme.example")
    t = task(api, private=True)
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"], capabilities=["make_page"])
    with api.app.state.store.transaction() as c:
        BS.update(c, "coo", {"capture_stream": "on"})
    message = post(api, f"tasks/{t['id']}/make-page", {"text": "Make the plan"}, "ben-test")["message"]
    claim_key = "receipt-claim"
    claimed = post(api, "jobs/claim", {"next_run": True}, machine["token"], claim_key)
    aid = claimed["attempt"]["id"]
    path, body, key = "jobs/claim", {"next_run": True}, claim_key
    first = claimed
    if endpoint != "claim":
        suffix = endpoint
        body = {
            "start-place": {}, "started": {"thread_id": "receipt-thread"},
            "thread": {"thread_id": "receipt-thread"},
            "events": {"events": [{"seq": 1, "kind": "status", "payload": {"text": "Making the plan"}}]},
            "renew": {}, "capture": {"provider": "test", "frames": "{}\n"},
            "diagnostics": {"tail": "Progress saved"}, "inputs": {}, "ack": {},
            "background-done": {"commands": [{"description": "Write the plan", "exit": 0}]},
            "complete": {"outcome": "completed", "last_seq": 0, "text": "The plan is ready."},
        }[endpoint]
        if endpoint not in ("start-place", "started"):
            post(api, f"attempts/{aid}/started", {"thread_id": "receipt-thread"}, machine["token"])
        if endpoint == "ack":
            # Make page does not take later chat inputs; retain an already assigned input's legacy receipt too.
            with api.app.state.store.transaction() as c:
                c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, message["id"]))
            suffix = f"inputs/{message['id']}/ack"
        path, key = f"attempts/{aid}/{suffix}", "lost-" + endpoint
        with monkeypatch.context() as patch:
            # Polls no longer save receipts. Exercise receipts kept by older servers through the real routes.
            patch.setattr(S, "is_poll", lambda operation, result: False)
            patch.setattr(api.app.state.execution, "idle_inputs", lambda *args: None)
            real_renew = api.app.state.execution.renew
            patch.setattr(api.app.state.execution, "renew", lambda *args, **kw:
                          None if kw.get("if_fresh") else real_renew(*args, **kw))
            first = post(api, path, body, machine["token"], key)
        with api.app.state.store.read() as c:
            assert c.execute("SELECT 1 FROM idempotency WHERE actor=? AND operation=? AND key=? AND binding IS NULL",
                             ("runner:" + machine["runner_id"], "/api/v2/" + path, key)).fetchone()
    if endpoint != "complete":
        if endpoint in ("claim", "start-place"):
            post(api, f"attempts/{aid}/started", {"thread_id": "receipt-thread"}, machine["token"])
        post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": int(endpoint == "events")},
             machine["token"])
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state,acting_person FROM attempts WHERE id=?", (aid,)).fetchone()
        assert tuple(row) == ("completed", "human:ben")
    # The original response was lost: the finished attempt must recover the identical receipt, not a historical result.
    assert post(api, path, body, machine["token"], key) == first
    post(api, path, body, "cara-test", key, expected=403)
    outsider = runner(api, operator="cara")
    if endpoint == "claim":
        assert post(api, path, body, outsider["token"], key)["attempt"] is None
    else:
        post(api, path, body, outsider["token"], key, expected=403)
    post(api, "access/people/ben", {"sign_in": False})
    post(api, path, body, machine["token"], key, expected=403)
    post(api, "access/people/ben", {"sign_in": True})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='human:cara',owner_id='human:cara' WHERE id=?", (t["id"],))
    post(api, path, body, machine["token"], key, expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (t["id"],))
    assert post(api, path, body, machine["token"], key) == first
    # Even on the same computer a new assignment generation cannot recover the former generation's receipts.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE assignments SET generation=generation+1 WHERE bot='coo'")
    post(api, path, body, machine["token"], key, expected=403)


def test_finished_public_make_page_claim_still_returns_200_with_the_original_attempt(api):
    t = task(api, private=False)
    machine, attempt, message = start(api, t["id"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    replay = post(api, "jobs/claim", {"next_run": True}, machine["token"], "claim:" + message["id"])["attempt"]
    assert (replay["id"], replay["token"]) == (attempt["id"], attempt["token"])
    # Replay of a receipt grants no new tool lease.
    assert api.get("/api/v2/me", headers=headers(replay["token"])).status_code == 409


@pytest.mark.parametrize("endpoint", ["uploads", "imports"])
def test_runner_file_receipts_without_an_attempt_in_the_response_check_the_request_attempt(api, endpoint):
    machine, attempt = turn(api)
    body = {"bot": "ops", "attempt": attempt["id"], "name": "plan.md", "text": "The plan"}
    if endpoint == "imports":
        body.update(source="s3://acme-example/plan.md", etag="v1")
    path, key = "files/" + endpoint, "lost-file"
    first = post(api, path, body, machine["token"], key)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "file-receipt"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    assert post(api, path, body, machine["token"], key) == first
    outsider = runner(api, operator="cara")
    post(api, path, body, outsider["token"], key, expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE assignments SET generation=generation+1 WHERE bot='ops'")
    post(api, path, body, machine["token"], key, expected=403)


def test_acting_person_migration_preserves_finished_attempts_and_backfills_the_request_person(api):
    t = task(api, private=True)
    machine, attempt, _ = start(api, t["id"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    migration = importlib.import_module("backend.migrations.20261105215933_attempt_acting_person")
    with api.app.state.store.transaction() as c:
        c.execute("ALTER TABLE attempts DROP COLUMN acting_person")
        migration.up(c)
        assert tuple(c.execute("SELECT state,acting_person FROM attempts WHERE id=?", (attempt["id"],)).fetchone()) == (
            "completed", "human:ben")
