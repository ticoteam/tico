"""The owner changes their own sign-in email in place: the same person, every copy at once, audited."""

import json

import pytest

from backend import access as Access
from backend import manage
from backend.store import H
from backend.tests.test_onboarding import OWNER_EMAIL, environment, signed_in  # noqa: F401
from backend.tests.test_people_access import HeaderProxy, call, person_headers, view

NEW = "morgan@new.example"


def change(api, email, headers=None, expected=200, again=None, revision=None):
    body = {"email": email, "confirm_email": email if again is None else again, "confirm": True,
            "expected_revision": view(api)["owner"]["revision"] if revision is None else revision}
    return call(api, "POST", "/owner/email", body, headers, expected)


def state(api):
    """Every place the owner's address lives, plus what the change must not touch."""
    with api.app.state.store.read() as c:
        return {"humans": {r["id"]: r["email"] for r in c.execute("SELECT id,email FROM humans")},
                "roster": {p["id"]: p["email"] for p in Access._load_json(c, "people")["people"]},
                "owner": Access._load_json(c, Access.OWNER), "access": Access._load_json(c, Access.ACCESS),
                "events": [dict(r) for r in c.execute(
                    "SELECT ts,actor,target,detail_json FROM events WHERE action='owner.email_changed'")],
                "joined": c.execute("SELECT count(*) FROM events WHERE action='person.joined'").fetchone()[0]}


def owner_on_the_lists(api):
    """Put the owner's address on the allow list and the Admins too, so the test sees both move."""
    with api.app.state.store.transaction() as c:
        stored = Access._load_json(c, Access.ACCESS)
        Access._store(c, Access.ACCESS, {**stored, "allowed": [OWNER_EMAIL, "sam@partner.example"],
                                         "allowed_domains": ["acme.example"],
                                         "admins": ["riley@acme.example", OWNER_EMAIL]})


def test_the_owner_changes_their_email_in_place_and_every_copy_moves_at_once(environment):
    api = environment()
    owner_on_the_lists(api)
    before = state(api)
    result = change(api, " Morgan@New.example ")
    after = state(api)
    assert result == {"owner": NEW, "revision": before["owner"]["revision"] + 1, "person": "morgan"}
    assert after["humans"] == {**before["humans"], "morgan": NEW}             # same id, no second human
    assert after["roster"] == {**before["roster"], "morgan": NEW}
    assert after["owner"]["email"] == NEW and after["owner"]["revision"] == before["owner"]["revision"] + 1
    assert after["owner"]["history"][-1]["email"] == OWNER_EMAIL and after["owner"]["retired"] == [OWNER_EMAIL]
    assert after["access"]["allowed"] == [NEW, "sam@partner.example"]
    assert after["access"]["admins"] == after["access"]["bot_admins"] == ["riley@acme.example", NEW]
    assert after["access"]["revision"] == before["access"]["revision"] + 1
    [event] = after["events"]
    assert event["actor"] == "human:morgan" and event["target"] == "morgan" and event["ts"]
    assert json.loads(event["detail_json"]) == {"from": OWNER_EMAIL, "to": NEW, "revision": result["revision"],
                                                "via": "app"}
    assert view(api)["owner"]["email"] == NEW and api.app.state.auth.owner_email == NEW


def test_the_new_address_signs_in_as_the_same_owner_and_the_old_one_joins_no_one(environment):
    api = environment()
    owner_on_the_lists(api)                                    # acme.example is an allowed domain
    task = api.post("/api/v2/tasks", json={"title": "Morgan's task", "body": "Kept", "owner": "human:morgan"}, headers=signed_in())
    assert task.status_code == 200, task.text
    api.app.state.auth.proxy = HeaderProxy()
    old, new = {"x-test-email": OWNER_EMAIL}, {"x-test-email": NEW}
    assert api.get("/api/v2/me", headers=new).status_code == 403            # not yet: not on the roster
    assert api.get("/api/v2/me", headers=old).json()["role"] == "owner"     # still works until the change
    change(api, NEW)
    me = api.get("/api/v2/me", headers=new).json()
    assert me["actor"] == "human:morgan" and me["role"] == "owner"
    tasks = api.get("/api/v2/tasks", headers=new).json()
    assert task.json()["task"]["id"] in json.dumps(tasks)
    # The old address is at an allowed domain, but joining it would split the owner in two.
    refused = api.get("/api/v2/me", headers=old)
    assert refused.status_code == 403 and "no longer signs in" in refused.text
    after = state(api)
    assert after["joined"] == 0 and len(after["humans"]) == 3
    # The owner can still put the old address on the roster on purpose, as someone else.
    call(api, "POST", "/people", {"name": "Morgan (old)", "email": OWNER_EMAIL}, headers=signed_in())
    assert api.get("/api/v2/me", headers=old).json()["role"] == "human"


def test_an_address_that_belongs_to_someone_else_is_refused_and_nothing_changes(environment):
    api = environment()
    before = state(api)
    r = change(api, "Riley@acme.example", expected=409)
    assert "already belongs to someone else" in r["error"]["detail"]
    assert state(api) == before


def test_a_failure_part_way_through_changes_nothing(environment, monkeypatch):
    api = environment()
    owner_on_the_lists(api)
    before = state(api)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(Access.H, "event", broken)        # after the roster, humans, owner and lists are written
    with pytest.raises(RuntimeError):
        change(api, NEW)
    monkeypatch.undo()
    assert state(api) == before
    assert api.app.state.auth.owner_email == OWNER_EMAIL


def test_only_the_signed_in_owner_changes_it_and_only_typed_twice(environment):
    api = environment()
    riley, quinn, morgan_token = person_headers(api, "riley"), person_headers(api, "quinn"), person_headers(api, "morgan")
    before = state(api)
    revision = view(api)["owner"]["revision"]
    for who in (riley, quinn):                                             # an admin, a member
        assert "Only the owner" in change(api, NEW, who, expected=403, revision=revision)["error"]["detail"]
    assert "signed-in browser" in change(api, NEW, morgan_token, expected=403, revision=revision)["error"]["detail"]
    assert "do not match" in change(api, NEW, again="morgan@new.exampel", expected=422)["error"]["detail"]
    change(api, "not-an-address", expected=422)
    change(api, OWNER_EMAIL, expected=409)                                 # already the owner's
    change(api, NEW, revision=revision + 5, expected=409)                  # stale page
    assert state(api) == before


def test_changing_it_back_lets_the_old_address_in_again_as_the_same_person(environment):
    api = environment()
    api.app.state.auth.proxy = HeaderProxy()
    change(api, NEW)
    back = change(api, OWNER_EMAIL)
    assert back["owner"] == OWNER_EMAIL
    me = api.get("/api/v2/me", headers={"x-test-email": OWNER_EMAIL}).json()
    assert me["actor"] == "human:morgan" and me["role"] == "owner"
    assert api.get("/api/v2/me", headers={"x-test-email": NEW}).status_code == 403
    after = state(api)
    assert after["owner"]["retired"] == [NEW] and [h["email"] for h in after["owner"]["history"]][-2:] == [OWNER_EMAIL, NEW]
    assert len(after["events"]) == 2 and after["joined"] == 0


def test_a_transfer_keeps_the_owner_history_and_the_retired_addresses(environment):
    api = environment()
    change(api, NEW)
    call(api, "POST", "/owner", {"person": "riley", "confirm": True, "expected_revision": view(api)["owner"]["revision"]})
    owner = state(api)["owner"]
    assert owner["email"] == "riley@acme.example" and owner["retired"] == [OWNER_EMAIL]
    assert [h["email"] for h in owner["history"]][-2:] == [OWNER_EMAIL, NEW]


def test_the_host_command_shows_the_change_then_applies_it_without_a_sign_in(environment, monkeypatch, capsys):
    api = environment()
    settings = api.app.state.store.settings
    monkeypatch.setenv("TICO_REGISTRY_DIR", str(settings.registry_dir))
    before = state(api)
    manage.main(["owner-email", str(settings.db_path), "--to", NEW])
    shown = json.loads(capsys.readouterr().out)
    assert shown["applied"] is False and shown["owner"] == OWNER_EMAIL and shown["to"] == NEW
    assert state(api) == before
    with pytest.raises(SystemExit, match="already belongs to someone else"):
        manage.main(["owner-email", str(settings.db_path), "--to", "quinn@acme.example", "--apply"])
    assert state(api) == before
    manage.main(["owner-email", str(settings.db_path), "--to", NEW, "--apply"])
    assert json.loads(capsys.readouterr().out)["applied"] is True
    after = state(api)
    assert after["humans"]["morgan"] == NEW and after["owner"]["email"] == NEW
    assert json.loads(after["events"][0]["detail_json"])["via"] == "host" and after["events"][0]["actor"] == "host"
    # The running server adopts it on its next request, with no restart.
    assert view(api)["owner"]["email"] == NEW


def test_meetings_and_a_granola_link_filed_under_the_old_address_follow_the_owner(environment):
    api = environment()
    with api.app.state.store.transaction() as c:
        for mid, owner, review in (("m-mine", "Morgan@acme.example", "pending"), ("m-riley", "riley@acme.example", "live")):
            c.execute("INSERT INTO meetings(id,title,owner,metadata_json,created,updated,review_state) VALUES(?,?,?,?,?,?,?)",
                      (mid, "Sync", owner, json.dumps({"owner": owner, "title": "Sync"}), H.now(), H.now(), review))
        c.execute("INSERT INTO meeting_deliveries(meeting_id,requested_by,sender_name,destination,instructions,requested_at) "
                  "VALUES('m-mine',?,'Morgan','bot:support','',?)", (OWNER_EMAIL, H.now()))
        c.execute("INSERT INTO granola_connections VALUES('human:morgan','granola:x',?,X'00',X'00','{}')", (OWNER_EMAIL,))
    change(api, NEW)
    with api.app.state.store.read() as c:
        rows = {r["id"]: (r["owner"], json.loads(r["metadata_json"])["owner"])
                for r in c.execute("SELECT id,owner,metadata_json FROM meetings")}
        assert rows == {"m-mine": (NEW, NEW), "m-riley": ("riley@acme.example", "riley@acme.example")}
        assert c.execute("SELECT requested_by FROM meeting_deliveries").fetchone()[0] == NEW
        assert c.execute("SELECT email FROM granola_connections").fetchone()[0] == NEW


def test_a_directory_sync_never_adds_the_owner_s_old_address_back_as_a_second_person():
    from backend import directory as D
    roster = {"people": [{"id": "morgan", "name": "Morgan", "email": NEW, "title": "", "hidden": False,
                          "directory": "", "directory_left": False, "external_id": "", "reports_to": ""}]}
    record = {"email": OWNER_EMAIL, "name": "Morgan", "title": "", "active": True}
    assert D.plan(roster, [record], "google", NEW, True)["adds"]
    found = D.plan(roster, [record], "google", NEW, True, retired=[OWNER_EMAIL])
    assert not found["adds"] and found["skipped"][0]["email"] == OWNER_EMAIL
