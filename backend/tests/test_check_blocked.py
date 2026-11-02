"""A blocked Review or QA check holds its ticket, and one known blocker gets one ask (Dev 553)."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import hubdb as H
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode

BOTS = ("cpo", "cmo", "qa", "devops-engineer")
REFUSAL = {"repo": "acme/app", "status": 403, "endpoint": "workflow_dispatch"}


@pytest.fixture
def api(tmp_path):
    identities = {"ana-test": Identity("human:ana", "owner", "ana@acme.example")}
    identities.update({slug + "-test": Identity("bot:" + slug, "bot", agent="hermes") for slug in BOTS})
    app = create_app(Settings(db_path=tmp_path / "hub.db", release_commit="", release_repo="acme/app",
                              test_identities=identities))
    people = [{"id": "ana", "email": "ana@acme.example", "team": "leadership", "primary_for": ["*"]}]
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS}
            H.sync_registry(c, bots, {"people": people})
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": people}),))
            for slug, config in bots.items():
                c.execute("INSERT INTO bot_config(bot,config,team,owner_id) VALUES(?,?,?,?)",
                          (slug, encode(config), "product", "ana"))
            c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
            for slug in BOTS:
                # Each bot connects as an external agent: its own standing credential, no runner lease.
                c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                          (slug, "hermes", "hash-" + slug, "2026-01-01T00:00:00Z", "human:ana"))
        yield client


def create(api, who, body, expected=200):
    r = api.post("/api/v2/tasks", json=body, headers={"Authorization": f"Bearer {who}-test",
                                                     "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == expected, r.text
    return r.json()


def to(c, actor, task_id):
    """Messages to `actor` about the task."""
    return H._rows(c.execute("SELECT body,refs FROM messages WHERE recipient_id=? AND json_extract(refs,'$.task')=?",
                             (actor, task_id)))


def in_qa_ticket(c, check_title="QA: PR #412"):
    typ = H.type_create(c, H.KEEPER, "Dev tickets", [
        {"name": "In Progress", "status": "doing"}, {"name": "In QA", "status": "review"},
        {"name": "Done", "status": "done"}])
    c.execute("UPDATE task_types SET manager='cmo' WHERE id=?", (typ["id"],))
    ticket = H.task_create(c, "human:ana", "Show the run log", "Build it.", "bot:cpo", type=typ["id"], step="In QA")
    check = H.task_create(c, "bot:cpo", check_title, "Check the candidate.", "bot:qa", parent_id=ticket["id"])
    return H.task(c, ticket["id"]), check


def check_notices(c, ticket_id):
    return [m for m in to(c, "bot:cpo", ticket_id) if (H._json(m["refs"], {}) or {}).get("news") == "check_blocked"]


def test_waiting_qa_check_blocks_its_ticket_and_resumes_it(api):
    with api.app.state.store.transaction() as c:
        ticket, check = in_qa_ticket(c)
        in_qa = ticket["step_id"]
        assert ticket["status"] == "review"
        H.task_update(c, "bot:qa", check["id"], status="waiting", waiting_on="human:ana",
                      note="GitHub refused the remote test run")
        row = H.task(c, ticket["id"])
        assert row["status"] == "waiting" and row["blocked_kind"] == "check"
        assert row["blocked_reason"] == "QA blocked: GitHub refused the remote test run, waiting on Ana"
        assert row["step_id"] == in_qa, "a hold keeps the ticket at its check step"
        assert len(check_notices(c, ticket["id"])) == 1
        assert not [m for m in to(c, "bot:cmo", ticket["id"]) if "QA blocked" in m["body"]], "not Head of Engineering"
        from backend import task_blocked
        task_blocked.sweep(c, H.now())
        H.task_update(c, "bot:qa", check["id"], note="Still refused")
        assert len(check_notices(c, ticket["id"])) == 1, "one notice per blocked episode"
        card = H._one(c, "SELECT id FROM tasks WHERE owner_id='human:ana' AND status='open'")
        H.task_update(c, "human:ana", card["id"], status="done")
        assert H.task(c, check["id"])["status"] != "waiting"
        row = H.task(c, ticket["id"])
        assert row["step_id"] == in_qa and row["status"] == "review" and row["blocked_since"] is None


def test_waiting_post_release_qa_check_is_shown_as_blocked_on_its_ticket(api):
    with api.app.state.store.transaction() as c:
        ticket, check = in_qa_ticket(c, "Post Release QA: PR #412")
        H.task_update(c, "bot:qa", check["id"], status="waiting", waiting_on="human:ana",
                      note="Release runner is unavailable")
        row = H.task(c, ticket["id"])
        assert row["status"] == "waiting"
        assert row["blocked_reason"] == "Post Release QA blocked: Release runner is unavailable, waiting on Ana"


def test_check_its_bot_resumes_returns_the_ticket_and_a_new_hold_notifies_again(api):
    with api.app.state.store.transaction() as c:
        ticket, check = in_qa_ticket(c)
        H.task_update(c, "bot:qa", check["id"], status="waiting", note="Runner pool is down")
        assert H.task(c, ticket["id"])["blocked_reason"] == "QA blocked: Runner pool is down"
        H.task_update(c, "bot:qa", check["id"], status="doing")
        assert H.task(c, ticket["id"])["status"] == "review"
        H.task_update(c, "bot:qa", check["id"], status="waiting", note="Runner pool is down again")
        assert H.task(c, ticket["id"])["status"] == "waiting"
        assert len(check_notices(c, ticket["id"])) == 2, "each new hold is one notice"


def test_sweep_finds_a_check_blocked_before_this_change(api):
    """A check already Waiting (no status change to catch) still blocks its ticket at the next sweep."""
    from backend import task_blocked
    with api.app.state.store.transaction() as c:
        ticket, check = in_qa_ticket(c)
        c.execute("UPDATE tasks SET status='waiting',waiting_on='human:ana',blocked_reason='GitHub refused the run' "
                  "WHERE id=?", (check["id"],))
        assert H.task(c, ticket["id"])["status"] == "review"
        task_blocked.sweep(c, H.now())
        row = H.task(c, ticket["id"])
        assert row["status"] == "waiting" and row["blocked_reason"].startswith("QA blocked: GitHub refused the run")
        assert len(check_notices(c, ticket["id"])) == 1


def test_check_blocked_by_an_open_task_blocks_its_ticket(api):
    from backend import task_relations as TR
    with api.app.state.store.transaction() as c:
        ticket, check = in_qa_ticket(c)
        grant = H.task_create(c, "bot:qa", "Grant Actions write", "Only a person can.", "human:ana")
        TR.add_blocker(c, "bot:qa", check["id"], grant["id"])
        row = H.task(c, ticket["id"])
        assert row["status"] == "waiting" and row["blocked_reason"] == "QA blocked: it waits on Grant Actions write, waiting on Ana"
        H.task_update(c, "human:ana", grant["id"], status="done")
        row = H.task(c, ticket["id"])
        assert row["status"] == "review" and row["blocked_since"] is None


def test_a_check_that_merely_runs_keeps_the_ticket_at_its_step(api):
    from backend import task_blocked
    with api.app.state.store.transaction() as c:
        ticket, check = in_qa_ticket(c)
        H.task_update(c, "bot:qa", check["id"], status="doing")
        task_blocked.sweep(c, H.now())
        assert H.task(c, ticket["id"])["status"] == "review"
        assert not check_notices(c, ticket["id"])


def test_same_blocker_joins_the_open_ask_and_both_wake(api):
    first = create(api, "cpo", {"owner": "human:ana", "title": "Grant Actions write", "body": "403 on dispatch.",
                                "blocker": REFUSAL})["task"]
    reply = create(api, "qa", {"owner": "human:ana", "title": "Remote test run refused", "body": "403.",
                               "blocker": {**REFUSAL, "repo": "Acme/App", "endpoint": " workflow_dispatch "}})
    second = reply["task"]
    assert reply["joined"]["task"] == first["id"]
    assert first["id"] in reply["warning"] and "--kind blocked_by --remove" in reply["warning"]
    with api.app.state.store.transaction() as c:
        assert second["owner_id"] == "bot:qa", "no second ask reaches the person"
        assert H.TR.blocker_ids(c, second["id"]) == [first["id"]]
        assert not [m for m in to(c, "human:ana", second["id"]) if not (H._json(m["refs"], {}) or {}).get("quiet")]
        assert not c.execute("SELECT 1 FROM tasks WHERE owner_id='human:ana' AND id<>?", (first["id"],)).fetchone()
        assert not c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                             "WHERE json_extract(m.refs,'$.task')=?", (second["id"],)).fetchone(), "quiet until the ask is done"
        H.task_update(c, "human:ana", first["id"], status="done")
        assert any(m["body"].startswith("Unblocked: Remote test run refused") for m in to(c, "bot:qa", second["id"]))
        assert any("Grant Actions write" in m["body"] for m in to(c, "bot:cpo", first["id"]))
        assert not H.TR.blocker_ids(c, second["id"])
        assert H.task(c, second["id"])["owner_id"] == "bot:qa", "finishing the ask does not hand the task on"


def test_different_endpoint_is_a_separate_ask(api):
    first = create(api, "cpo", {"owner": "human:ana", "title": "Grant Actions write", "body": "403.", "blocker": REFUSAL})
    other = create(api, "qa", {"owner": "human:ana", "title": "Contents write refused", "body": "403.",
                               "blocker": {**REFUSAL, "endpoint": "contents"}})
    assert "joined" not in other and other["task"]["owner_id"] == "human:ana"
    status = create(api, "qa", {"owner": "human:ana", "title": "Dispatch not found", "body": "404.",
                                "blocker": {**REFUSAL, "status": 404}})
    assert "joined" not in status and status["task"]["owner_id"] == "human:ana"
    repo = create(api, "qa", {"owner": "human:ana", "title": "Other repo refused", "body": "403.",
                              "blocker": {**REFUSAL, "repo": "acme/site"}})
    assert "joined" not in repo and repo["task"]["owner_id"] == "human:ana"
    with api.app.state.store.transaction() as c:
        assert not H.TR.blocker_ids(c, other["task"]["id"]) and first["task"]["status"] != "done"


@pytest.mark.parametrize("endpoint", ["/hooks/Deploy", "/hooks/deploy/"])
def test_case_or_slash_different_endpoint_is_a_separate_ask(api, endpoint):
    refusal = {**REFUSAL, "endpoint": "/hooks/deploy"}
    first = create(api, "cpo", {"owner": "human:ana", "title": "Deploy endpoint refused", "body": "403.",
                                "blocker": refusal})["task"]
    other = create(api, "qa", {"owner": "human:ana", "title": "Other endpoint refused", "body": "403.",
                               "blocker": {**refusal, "endpoint": endpoint}})
    assert "joined" not in other and other["task"]["owner_id"] == "human:ana"
    same = create(api, "cmo", {"owner": "human:ana", "title": "Deploy refused again", "body": "403.",
                              "blocker": {**refusal, "endpoint": " /hooks/deploy "}})
    assert same["joined"]["task"] == first["id"], "an identical path still joins its open ask"
    with api.app.state.store.transaction() as c:
        assert not H.TR.blocker_ids(c, other["task"]["id"])
        assert any(not (H._json(m["refs"], {}) or {}).get("quiet")
                   for m in to(c, "human:ana", other["task"]["id"])), "a distinct endpoint reaches the person"
        assert H.task(c, first["id"])["owner_id"] == "human:ana"


def test_unlinking_a_joined_task_asks_separately(api):
    first = create(api, "cpo", {"owner": "human:ana", "title": "Grant Actions write", "body": "403.", "blocker": REFUSAL})["task"]
    second = create(api, "qa", {"owner": "human:ana", "title": "Remote run refused", "body": "403.", "blocker": REFUSAL})["task"]
    with api.app.state.store.transaction() as c:
        H.TR.relate(c, "bot:qa", second["id"], first["id"], "blocked_by", remove=True)
        row = H.task(c, second["id"])
        assert row["owner_id"] == "human:ana" and not H.TR.blocker_ids(c, second["id"])


def test_private_blockers_never_join(api):
    first = create(api, "cpo", {"owner": "human:ana", "title": "Grant Actions write", "body": "403.", "blocker": REFUSAL,
                                "private": True})["task"]
    second = create(api, "qa", {"owner": "human:ana", "title": "Remote run refused", "body": "403.", "blocker": REFUSAL})
    assert "joined" not in second and second["task"]["owner_id"] == "human:ana"
    third = create(api, "cmo", {"owner": "human:ana", "title": "Remote run refused again", "body": "403.",
                                "blocker": REFUSAL, "private": True})
    assert "joined" not in third and third["task"]["owner_id"] == "human:ana"
    assert first["id"] != second["task"]["id"]


def test_devops_person_ask_holds_its_own_task_and_later_asks_join_it(api):
    devops_task = create(api, "qa", {"owner": "bot:devops-engineer", "title": "Remote run refused", "body": "403.",
                                     "blocker": REFUSAL})["task"]
    assert devops_task["owner_id"] == "bot:devops-engineer"
    again = create(api, "cpo", {"owner": "bot:devops-engineer", "title": "Dispatch refused", "body": "403.",
                                "blocker": REFUSAL})
    assert again["joined"]["task"] == devops_task["id"], "a second ask to DevOps joins its open task"
    person = create(api, "devops-engineer", {"owner": "human:ana", "title": "Grant Actions write", "body": "Only you can.",
                                             "blocker": REFUSAL})
    assert "joined" not in person and person["task"]["owner_id"] == "human:ana"
    assert devops_task["id"] in person["warning"]
    later = create(api, "cmo", {"owner": "bot:devops-engineer", "title": "Dispatch refused again", "body": "403.",
                                "blocker": REFUSAL})
    assert later["joined"]["task"] == person["task"]["id"], "the person ask is preferred"
    with api.app.state.store.transaction() as c:
        assert H.TR.blocker_ids(c, devops_task["id"]) == [person["task"]["id"]]
        assert H.task(c, devops_task["id"])["owner_id"] == "bot:devops-engineer"


def test_private_devops_person_ask_does_not_hold_public_devops_work(api):
    devops_task = create(api, "qa", {"owner": "bot:devops-engineer", "title": "Remote run refused", "body": "403.",
                                     "blocker": REFUSAL})["task"]
    person = create(api, "devops-engineer", {"owner": "human:ana", "title": "Private permission ask",
                                            "body": "Only you can.", "blocker": REFUSAL, "private": True})
    assert "joined" not in person and person["task"]["private"]
    with api.app.state.store.transaction() as c:
        assert H.task(c, person["task"]["id"])["owner_id"] == "human:ana"
        assert not H.TR.blocker_ids(c, devops_task["id"])
        assert not H.TR.blocker_ids(c, person["task"]["id"])
        assert not c.execute("SELECT 1 FROM events WHERE action='blocker.escalated' AND target=?",
                             (person["task"]["id"],)).fetchone()


def test_a_person_filing_is_never_joined_and_bad_blockers_are_refused(api):
    create(api, "cpo", {"owner": "human:ana", "title": "Grant Actions write", "body": "403.", "blocker": REFUSAL})
    r = api.post("/api/v2/tasks", headers={"Authorization": "Bearer ana-test", "Idempotency-Key": str(uuid.uuid4())},
                 json={"owner": "human:ana", "title": "Grant it today", "body": "403.", "blocker": REFUSAL})
    assert r.status_code == 200 and "joined" not in r.json()
    for bad in ({**REFUSAL, "repo": "acme"}, {**REFUSAL, "status": "403"}, {**REFUSAL, "endpoint": " / "}):
        create(api, "qa", {"owner": "human:ana", "title": "Refused", "body": "x", "blocker": bad}, expected=422)
