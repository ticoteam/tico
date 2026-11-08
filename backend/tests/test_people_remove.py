"""Removing a left person who has no history (one added by mistake) for good; anyone with history stays as left."""

import json
import uuid

from backend import invites
from backend.store import H, digest
from backend.tests.test_onboarding import environment, signed_in  # noqa: F401
from backend.tests.test_people_access import call, person_headers, view


def added_and_left(api, email="e2e-tester@acme.example"):
    pid = call(api, "POST", "/people", {"name": "E2E Tester", "email": email})["person"]
    assert api.post(f"/api/v2/people/{pid}", json={"left": True}, headers=signed_in()).status_code == 200
    return pid


def remove(api, pid, headers=None, expected=200):
    r = api.delete(f"/api/v2/humans/{pid}", headers=headers or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


def removable(api, pid):
    return next(p for p in view(api)["people"] if p["id"] == pid)["removable"]


def test_a_left_person_with_no_history_is_removed_with_what_goes_with_them(environment):
    api = environment()
    pid = added_and_left(api)
    with api.app.state.store.transaction() as c:
        invites.record(c, "human:morgan", pid)                    # an invite sent, never answered
        c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created_at,created_by) VALUES(?,?,?,?,?,?)",
                  (uuid.uuid4().hex, pid, "never used", digest("x" + pid), H.now(), "test"))
    before = view(api)
    assert removable(api, pid) and pid in before["invites"] and "e2e-tester@acme.example" in before["allowed"]
    assert remove(api, pid) == {"person": pid, "removed": True}
    after = view(api)
    assert pid not in {p["id"] for p in after["people"]} and pid not in after["invites"]
    assert "e2e-tester@acme.example" not in after["allowed"] and after["revision"] == before["revision"] + 1
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM humans WHERE id=?", (pid,)).fetchone()
        assert not c.execute("SELECT 1 FROM human_tokens WHERE human=?", (pid,)).fetchone()
        event = c.execute("SELECT actor,target,detail FROM events WHERE action='person.removed'").fetchone()
    assert event["actor"] == "human:morgan" and event["target"] == pid
    assert json.loads(event["detail"]) == {"name": "E2E Tester", "email": "e2e-tester@acme.example"}
    remove(api, pid, expected=404)
    # The same address can be added again as a new person.
    assert call(api, "POST", "/people", {"email": "e2e-tester@acme.example"})["person"] == pid


def test_a_person_with_history_stays_as_left(environment):
    api = environment()
    # A token they used: the People tab does not offer Remove, and the API refuses.
    used = added_and_left(api, "used@acme.example")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created_at,created_by,last_used_at) VALUES(?,?,?,?,?,?,?)",
                  (uuid.uuid4().hex, used, "used", digest("y" + used), H.now(), "test", H.now()))
    assert not removable(api, used)
    assert remove(api, used, expected=409)["error"]["detail"] == "Has history; stays as left"
    # A message they wrote: only the full read on the click finds it.
    wrote = added_and_left(api, "wrote@acme.example")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO conversations(id,kind,subject,participants,created_at) VALUES('cv1','dm','',?,?)",
                  (json.dumps(["human:" + wrote, "bot:coo"]), H.now()))
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
                  "VALUES('m1','cv1',?,'bot:coo','message','hi',?)", ("human:" + wrote, H.now()))
    assert removable(api, wrote)
    remove(api, wrote, expected=409)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM humans WHERE id IN (?,?)", (used, wrote)).fetchone()[0] == 2


def test_only_the_owner_removes_and_only_a_left_person(environment):
    api = environment()
    pid = call(api, "POST", "/people", {"email": "typo@acme.example"})["person"]
    assert not next(p for p in view(api)["people"] if p["id"] == pid)["removable"]
    assert remove(api, pid, expected=409)["error"]["detail"] == "Mark them as left first"
    assert api.post(f"/api/v2/people/{pid}", json={"left": True}, headers=signed_in()).status_code == 200
    remove(api, pid, person_headers(api, "riley"), expected=403)        # an admin
    remove(api, pid, person_headers(api, "quinn"), expected=403)        # a member
    remove(api, pid)
