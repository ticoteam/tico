"""Pipeline review mode (backend/pipeline_review.py): who sees a ▶ item, claims, Approve with its version check and
advance, the 10-second undo, send-back, Message manager and Mark met. The concurrency cases use real connections to one
file: the database decides, not a Python lock."""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import access
from backend import hubdb as H
from backend import pipeline_review as R
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import Problem, Store

ANA, BEN, CHRIS = "human:ana", "human:ben", "human:chris"
FIXTURES = Path(__file__).parent / "fixtures"
STEPS = [
    {"name": "Making", "status": "doing"},
    {"name": "Review", "status": "doing"},
    {"name": "Shipped", "status": "done"},
]


class World:
    """A Design pipeline: Making, Review (a Mockup attachment and a Sign-off approval, both ana's) and Shipped."""

    def __init__(self, path, reviewed_by="person", conn=None):
        self.path = path
        if conn is None:
            Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False)
            conn = H.connect(path)
        self.c = conn
        H.sync_registry(
            self.c,
            {"cmo": {"name": "cmo", "runtime": "fake", "status": "active"}},
            {
                "people": [
                    {"id": "ana", "email": "ana@acme.example"},
                    {"id": "ben", "email": "ben@acme.example"},
                    {"id": "chris", "email": "chris@acme.example"},
                ]
            },
        )
        c = self.c
        self.pipeline = H.type_create(c, ANA, "Design", STEPS, mover=True)["id"]
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, self.pipeline))
        self.making, self.review, self.shipped = (self.step(n) for n in ("Making", "Review", "Shipped"))
        self.mockup = self.requirement("Mockup", "attachment", reviewed_by)
        self.signoff = self.requirement("Sign-off", "approval", "person")
        self.task = self.ticket("Logo")

    def step(self, name):
        return self.c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (self.pipeline, name)).fetchone()[
            0
        ]

    def requirement(self, name, kind, reviewed_by, reviewer=ANA, stage=None, **extra):
        rid = f"r-{name.lower().replace(' ', '-')}"
        values = {
            "id": rid,
            "pipeline_id": self.pipeline,
            "pipeline_stage_id": stage or self.review,
            "name": name,
            "kind": kind,
            "reviewed_by": reviewed_by,
            "reviewer_id": reviewer,
            "created_at": H.now(),
            "updated_at": H.now(),
            **extra,
        }
        self.c.execute(
            f"INSERT INTO stage_requirements ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )
        return rid

    def ticket(self, title, step="Review", owner="bot:cmo"):
        task = H.task_create(self.c, ANA, title, "", owner, type=self.pipeline, step=step, lint=False)
        return task["id"]

    def attach(self, task=None, requirement=None, digest="d1"):
        values = (
            f"a-{digest}-{time.monotonic_ns()}",
            task or self.task,
            requirement or self.mockup,
            "link",
            "https://example.test/mockup",
            digest,
            self.revision(task),
            H.now(),
        )
        self.c.execute(
            "INSERT INTO requirement_attachments (id, task_id, stage_requirement_id, source, url, digest, work_version, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            values,
        )

    def revision(self, task=None):
        return self.c.execute("SELECT work_revision FROM tasks WHERE id=?", (task or self.task,)).fetchone()[0]

    def push(self, task=None):
        self.c.execute("UPDATE tasks SET work_revision=work_revision+1 WHERE id=?", (task or self.task,))

    def step_of(self, task=None):
        return self.c.execute("SELECT step_id FROM tasks WHERE id=?", (task or self.task,)).fetchone()[0]

    def visit(self, task=None, at=None):
        self.c.execute(
            "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"v-{time.monotonic_ns()}", task or self.task, self.pipeline, self.review, "initial", at or H.now()),
        )

    def decide(self, requirement, verdict, judge="llm", task=None, **extra):
        values = {
            "id": f"d-{time.monotonic_ns()}",
            "task_id": task or self.task,
            "pipeline_stage_id": self.review,
            "stage_requirement_id": requirement,
            "work_version": self.revision(task),
            "judge": judge,
            "verdict": verdict,
            "created_at": H.now(),
            **extra,
        }
        self.c.execute(
            f"INSERT INTO stage_decisions ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )

    def view(self, requirement=None, task=None):
        """What ana's queue shows for the item: the versions she would send back with Approve."""
        item = next(
            i
            for i in R.queue_for(self.c, ANA)
            if i["task_id"] == (task or self.task) and i["requirement_id"] == (requirement or self.mockup)
        )
        return {"work_version": item["work_version"], "attachment_digest": item["attachment_digest"]}

    def approve(self, requirement=None, actor=ANA, task=None, **kw):
        seen = {} if kw.get("seen") is False else self.view(requirement, task)
        kw.pop("seen", None)
        return R.approve(self.c, actor, task or self.task, requirement or self.mockup, **{**seen, **kw})


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path / "hub.sqlite")
    w.attach()
    yield w
    w.c.close()


def refused(code, fn, *args, **kw):
    with pytest.raises(Problem) as caught:
        fn(*args, **kw)
    assert caught.value.code == code, (caught.value.code, caught.value.detail)
    return caught.value


def test_only_the_reviewer_the_route_and_the_asked_backup_see_an_item(world):
    w = world
    assert {i["requirement_id"] for i in R.queue_for(w.c, ANA)} == {w.mockup, w.signoff}
    assert R.queue_for(w.c, BEN) == [] and R.queue_for(w.c, "bot:cmo") == []
    # A backup sees it only after the manager asked them.
    w.c.execute("UPDATE stage_requirements SET backup_reviewer_id=? WHERE id=?", (BEN, w.mockup))
    assert R.queue_for(w.c, BEN) == []
    refused("not_yours", R.claim, w.c, BEN, w.task, w.mockup)
    w.visit()
    visit = w.c.execute("SELECT id FROM stage_visits").fetchone()[0]
    w.c.execute(
        "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, stage_visit_id, "
        "stage_requirement_id, action, created_at) VALUES ('pa1',?,'run1',?,?,?,'ask_backup',?)",
        (w.pipeline, w.task, visit, w.mockup, H.now()),
    )
    assert [i["why"] for i in R.queue_for(w.c, BEN)] == ["backup"]
    # A person who may edit the pipeline still does not see a review that is not theirs.
    assert R.queue_for(w.c, CHRIS) == []


def test_the_queue_lists_late_first_then_oldest_and_narrows_to_a_stage(world):
    w = world
    w.c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (w.review,))
    now = H.now()
    fresh = w.ticket("Fresh")
    old = w.ticket("Old, within limit")
    late = w.ticket("Late")
    for task in (fresh, old, late):
        w.attach(task, w.mockup, "x")
    w.visit(fresh, now)
    w.visit(old, H.shift(now, minutes=-30))
    w.visit(late, H.shift(now, minutes=-90))
    items = [i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup and i["task_id"] != w.task]
    assert [i["task_id"] for i in items] == [late, old, fresh]
    assert [i["late"] for i in items] == [True, False, False]
    assert R.queue_for(w.c, ANA, w.pipeline, w.making) == []
    assert {i["stage_id"] for i in R.queue_for(w.c, ANA, w.pipeline, w.review)} == {w.review}
    assert R.queue_for(w.c, ANA, "other-pipeline") == []


def test_a_second_reviewer_cannot_claim_or_approve_while_the_claim_is_held(world, monkeypatch):
    w = world
    w.c.execute("UPDATE stage_requirements SET backup_reviewer_id=? WHERE id=?", (BEN, w.mockup))
    w.visit()
    visit = w.c.execute("SELECT id FROM stage_visits").fetchone()[0]
    w.c.execute(
        "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, stage_visit_id, "
        "stage_requirement_id, action, created_at) VALUES ('pa1',?,'run1',?,?,?,'ask_backup',?)",
        (w.pipeline, w.task, visit, w.mockup, H.now()),
    )
    claim = R.claim(w.c, ANA, w.task, w.mockup)
    assert claim["claimed_by"] == ANA
    assert R.claim(w.c, ANA, w.task, w.mockup)["claimed_by"] == ANA  # her own claim is refreshed, not doubled
    error = refused("claimed", R.claim, w.c, BEN, w.task, w.mockup)
    assert error.extra["claimed_by"] == ANA
    seen = w.view()
    refused("claimed", R.approve, w.c, BEN, w.task, w.mockup, **seen)
    refused("claimed", R.send_back, w.c, BEN, w.task, w.mockup, "No")
    mine = next(i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup)
    theirs = next(i for i in R.queue_for(w.c, BEN) if i["requirement_id"] == w.mockup)
    assert mine["claimed_by_me"] and mine["claimed_by"] is None
    assert theirs["claimed_by"] == ANA and not theirs["claimed_by_me"]
    assert w.c.execute("SELECT count(*) FROM review_claims WHERE released_at IS NULL").fetchone()[0] == 1
    # An expired claim counts as released.
    later = H.shift(H.now(), minutes=R.CLAIM_MINUTES + 1)
    monkeypatch.setattr(R, "_now", lambda: later)
    assert R.claim(w.c, BEN, w.task, w.mockup)["claimed_by"] == BEN
    rows = w.c.execute("SELECT claimed_by, released_at FROM review_claims ORDER BY claimed_at").fetchall()
    assert [(r[0], r[1] is None) for r in rows] == [(ANA, False), (BEN, True)]


def test_approve_is_refused_after_a_push_an_attachment_swap_or_a_rule_change(world):
    w = world
    seen = w.view()
    w.push()
    error = refused("stale", R.approve, w.c, ANA, w.task, w.mockup, **seen)
    assert error.extra["reason"] == "work"
    seen = w.view()
    w.attach(digest="d2")  # the attachment was swapped under her
    assert refused("stale", R.approve, w.c, ANA, w.task, w.mockup, **seen).extra["reason"] == "attachment"
    seen = w.view()
    w.c.execute("UPDATE stage_requirements SET version=2 WHERE id=?", (w.mockup,))
    error = refused("stale", R.approve, w.c, ANA, w.task, w.mockup, **seen, requirement_version=1)
    assert error.extra["reason"] == "requirement"
    assert w.c.execute("SELECT count(*) FROM stage_decisions").fetchone()[0] == 0
    assert w.step_of() == w.review


def test_a_bot_never_approves_and_a_requirement_nobody_reviews_by_person_is_not_a_review(world):
    w = world
    seen = w.view()
    refused("not_person", R.approve, w.c, "bot:cmo", w.task, w.mockup, **seen)
    refused("not_person", R.claim, w.c, "bot:cmo", w.task, w.mockup)
    refused("not_person", R.mark_met, w.c, "bot:cmo", w.task, w.mockup, "ok")
    botted = w.requirement("Lint", "run", "bot", stage=w.review)
    refused("not_person_reviewed", R.approve, w.c, ANA, w.task, botted, work_version=0)
    # The table itself will not let an approval be reviewed by a bot.
    with pytest.raises(Exception, match="CHECK"):
        w.requirement("Bot sign-off", "approval", "bot")
    assert w.c.execute("SELECT count(*) FROM stage_decisions").fetchone()[0] == 0


def test_the_last_requirement_moves_the_ticket_on_and_an_earlier_one_does_not(world):
    w = world
    first = w.approve(w.mockup)
    assert first["advanced"] is False and w.step_of() == w.review
    second = w.approve(w.signoff, note="Looks right")
    assert second["advanced"] is True and w.step_of() == w.shipped
    decision = dict(w.c.execute("SELECT * FROM stage_decisions WHERE id=?", (second["decision_id"],)).fetchone())
    assert decision["judge"] == "person" and decision["verdict"] == "met" and decision["judge_actor"] == ANA
    assert decision["reason"] == "Looks right" and decision["requirement_version"] == 1
    assert '"advanced"' in decision["outcome"]
    # Nothing is left to approve at the stage the ticket has left.
    refused("moved", R.approve, w.c, ANA, w.task, w.signoff, work_version=0)


def test_a_met_requirement_goes_stale_when_the_work_changes_and_is_approved_again(world):
    w = world
    seen = w.view()
    R.approve(w.c, ANA, w.task, w.mockup, **seen)
    mockup = dict(w.c.execute("SELECT * FROM stage_requirements WHERE id=?", (w.mockup,)).fetchone())
    assert R.requirement_state(w.c, H.task(w.c, w.task), mockup) == "met"
    refused("already_met", R.approve, w.c, ANA, w.task, w.mockup, **seen)
    w.push()
    assert w.view()["work_version"] == 1  # back in the queue at the new version
    assert w.approve(w.mockup)["verdict"] == "met"


def test_undo_inside_ten_seconds_reverts_the_decision_and_the_move(world, monkeypatch):
    w = world
    w.approve(w.mockup)
    w.visit()  # (the engine would have kept the open visit)
    done = w.approve(w.signoff)
    assert w.step_of() == w.shipped
    refused("not_found", R.undo, w.c, BEN, done["decision_id"])  # only the approver
    assert R.undo(w.c, ANA, done["decision_id"]) == {
        "decision_id": done["decision_id"],
        "undone": True,
        "moved_back": True,
    }
    assert w.step_of() == w.review
    row = w.c.execute("SELECT undone_at, undone_by FROM stage_decisions WHERE id=?", (done["decision_id"],)).fetchone()
    assert row[0] and row[1] == ANA
    refused("already_undone", R.undo, w.c, ANA, done["decision_id"])
    # The undone approval no longer counts, so it can be approved again.
    assert w.approve(w.signoff)["advanced"] is True


def test_undo_is_refused_after_ten_seconds_and_after_the_ticket_moved_on(world, monkeypatch):
    w = world
    w.approve(w.mockup)
    late = w.approve(w.signoff)
    soon = H.shift(H.now(), seconds=R.UNDO_SECONDS + 1)
    with monkeypatch.context() as patched:
        patched.setattr(R, "_now", lambda: soon)
        refused("too_late", R.undo, w.c, ANA, late["decision_id"])
    # Inside the window, but the ticket was already moved on by someone else.
    H.task_update(w.c, ANA, w.task, step="Making")
    refused("moved_on", R.undo, w.c, ANA, late["decision_id"])
    assert w.step_of() == w.making


def test_send_back_leaves_the_queue_comments_and_returns_to_the_sender_on_the_next_attachment(tmp_path):
    w = World(tmp_path / "hub.sqlite", reviewed_by="bot_then_person")
    try:
        w.attach()
        # The bot has not passed it up: the person does not see it yet.
        assert not [i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup]
        w.decide(w.mockup, "unsure")
        seen = w.view()
        refused("note_required", R.send_back, w.c, ANA, w.task, w.mockup, "  ")
        sent = R.send_back(w.c, ANA, w.task, w.mockup, "The logo is too small")
        assert sent["verdict"] == "sent_back"
        assert not [i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup]
        texts = [m["body"] for m in H.task_comments(w.c, w.task) if "body" in m]
        assert any("The logo is too small" in t for t in texts)
        # New work arrives: it returns to her at once, with no bot pass in between.
        time.sleep(0.01)
        w.attach(digest="d2")
        back = [i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup]
        assert len(back) == 1 and back[0]["attachment_digest"] == "d2"
        assert w.c.execute("SELECT count(*) FROM stage_decisions WHERE judge IN ('llm','bot')").fetchone()[0] == 1
        assert seen["attachment_digest"] == "d1"
        # A push alone also brings it back; a send-back never leaves the work stuck.
    finally:
        w.c.close()


def test_a_send_back_is_held_off_until_something_new_arrives(world):
    w = world
    R.send_back(w.c, ANA, w.task, w.mockup, "Redo it")
    assert not [i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup]
    w.push()
    assert len([i for i in R.queue_for(w.c, ANA) if i["requirement_id"] == w.mockup]) == 1


def test_message_manager_writes_to_the_manager_with_the_context(world):
    w = world
    refused("no_manager", R.message_manager, w.c, ANA, w.task, w.mockup, "Can this wait?")
    w.c.execute("UPDATE task_types SET manager='cmo' WHERE id=?", (w.pipeline,))
    sent = R.message_manager(w.c, ANA, w.task, w.mockup, "Can this wait?")
    assert sent["to"] == "bot:cmo"
    bodies = [m["body"] for m in H.task_comments(w.c, w.task) if "body" in m]
    assert any("Review of Mockup at Review" in b and "Can this wait?" in b for b in bodies)
    refused("not_yours", R.message_manager, w.c, BEN, w.task, w.mockup, "Hi")


def test_only_a_rule_editor_marks_met_and_it_goes_stale_with_new_work(world, monkeypatch):
    w = world
    monkeypatch.setattr(access, "owner_and_admin_emails", lambda conn: ["chris@acme.example"])
    refused("forbidden", R.mark_met, w.c, BEN, w.task, w.mockup, "It is fine")
    refused("why_required", R.mark_met, w.c, ANA, w.task, w.mockup, "  ")
    refused("not_found", R.mark_met, w.c, ANA, w.task, "no-such-requirement", "ok")
    done = R.mark_met(w.c, CHRIS, w.task, w.mockup, "The client approved it by phone")
    row = dict(w.c.execute("SELECT * FROM stage_decisions WHERE id=?", (done["decision_id"],)).fetchone())
    assert row["verdict"] == "override" and row["judge"] == "person" and row["judge_actor"] == CHRIS
    assert row["work_version"] == w.revision() and row["reason"] == "The client approved it by phone"
    assert w.step_of() == w.review  # marking met does not move the ticket
    task = H.task(w.c, w.task)
    mockup = dict(w.c.execute("SELECT * FROM stage_requirements WHERE id=?", (w.mockup,)).fetchone())
    assert R.requirement_state(w.c, task, mockup) == "met"
    refused("already_met", R.mark_met, w.c, ANA, w.task, w.mockup, "again")
    w.push()
    assert R.requirement_state(w.c, H.task(w.c, w.task), mockup) == "waiting"


def test_two_reviewers_racing_to_approve_give_exactly_one_decision(world):
    w = world
    w.c.execute("UPDATE stage_requirements SET backup_reviewer_id=? WHERE id=?", (BEN, w.mockup))
    w.visit()
    visit = w.c.execute("SELECT id FROM stage_visits").fetchone()[0]
    w.c.execute(
        "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, stage_visit_id, "
        "stage_requirement_id, action, created_at) VALUES ('pa1',?,'run1',?,?,?,'ask_backup',?)",
        (w.pipeline, w.task, visit, w.mockup, H.now()),
    )
    seen = w.view()
    mine = H.connect(w.path)
    outcomes: dict[str, object] = {}

    ready, go = threading.Event(), threading.Event()

    def second():
        theirs = H.connect(w.path)
        ready.set()
        go.wait(10)
        try:
            outcomes["ben"] = R.approve(theirs, BEN, w.task, w.mockup, **seen)
        except Problem as problem:
            outcomes["ben"] = problem.code
        finally:
            theirs.close()

    # Ana is inside her write when Ben's approve starts: his waits for the write lock, then sees what she did.
    runner = threading.Thread(target=second)
    runner.start()
    assert ready.wait(20)
    mine.execute("BEGIN IMMEDIATE")
    go.set()
    time.sleep(0.3)
    outcomes["ana"] = R.approve(mine, ANA, w.task, w.mockup, **seen)
    mine.execute("COMMIT")
    runner.join(10)
    assert not runner.is_alive()
    assert isinstance(outcomes["ana"], dict) and outcomes["ben"] == "already_met"
    assert w.c.execute("SELECT count(*) FROM stage_decisions WHERE verdict='met'").fetchone()[0] == 1
    mine.close()


def test_an_approve_racing_a_push_is_refused_when_the_push_commits_first(world):
    w = world
    seen = w.view()
    pusher = H.connect(w.path)
    result: dict[str, object] = {}

    ready, go = threading.Event(), threading.Event()

    def approve():
        reviewer = H.connect(w.path)
        ready.set()
        go.wait(10)
        try:
            result["approve"] = R.approve(reviewer, ANA, w.task, w.mockup, **seen)
        except Problem as problem:
            result["approve"] = problem.code
        finally:
            reviewer.close()

    runner = threading.Thread(target=approve)
    runner.start()
    assert ready.wait(20)
    pusher.execute("BEGIN IMMEDIATE")
    pusher.execute("UPDATE tasks SET work_revision=work_revision+1 WHERE id=?", (w.task,))
    go.set()
    time.sleep(0.3)
    pusher.execute("COMMIT")
    runner.join(10)
    assert result["approve"] == "stale"
    assert w.c.execute("SELECT count(*) FROM stage_decisions").fetchone()[0] == 0
    pusher.close()


def test_the_routes_serve_the_queue_and_approve_and_undo_for_a_person_only(tmp_path):
    ids = {
        "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
        "ben-test": Identity("human:ben", "human", "ben@acme.example"),
    }
    app = create_app(Settings(db_path=tmp_path / "hub.db", company_name="Acme", test_identities=ids))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            w = World(tmp_path / "hub.db", conn=c)
            w.attach()
            w.approve(w.mockup)  # only the sign-off is left
        ana, ben = (
            {"Authorization": "Bearer " + name, "Idempotency-Key": f"k-{name}"} for name in ("ana-test", "ben-test")
        )
        queue = client.get("/api/v2/pipelines/review/queue", headers=ana)
        assert queue.status_code == 200, queue.text
        (item,) = queue.json()["items"]
        assert item["requirement_id"] == w.signoff and item["pipeline"] == "Design" and item["stage"] == "Review"
        assert client.get(f"/api/v2/pipelines/{w.pipeline}/stages/{w.review}/review/queue", headers=ana).json()["items"]
        assert client.get("/api/v2/pipelines/review/queue", headers=ben).json()["items"] == []
        path = f"/api/v2/pipelines/tasks/{w.task}/requirements/{w.signoff}"
        body = {"work_version": item["work_version"], "attachment_digest": item["attachment_digest"]}
        refused_by_ben = client.post(path + "/approve", json=body, headers=ben)
        assert refused_by_ben.status_code == 422 and refused_by_ben.json()["error"]["code"] == "not_yours"
        stale = client.post(
            path + "/approve", json={**body, "work_version": 9}, headers={**ana, "Idempotency-Key": "k2"}
        )
        assert stale.status_code == 422 and stale.json()["error"]["code"] == "stale"
        done = client.post(path + "/approve", json=body, headers={**ana, "Idempotency-Key": "k3"})
        assert done.status_code == 200 and done.json()["advanced"] is True
        again = client.post(path + "/approve", json=body, headers={**ana, "Idempotency-Key": "k3"})
        assert again.json() == done.json()  # a retry with the same key is the same answer, not a second decision
        undone = client.post(
            f"/api/v2/pipelines/decisions/{done.json()['decision_id']}/undo",
            json={},
            headers={**ana, "Idempotency-Key": "k4"},
        )
        assert undone.status_code == 200 and undone.json()["moved_back"] is True
        with app.state.store.read() as c:
            assert c.execute("SELECT step_id FROM tasks WHERE id=?", (w.task,)).fetchone()[0] == w.review
            assert c.execute("SELECT count(*) FROM stage_decisions WHERE undone_at IS NOT NULL").fetchone()[0] == 1


def test_the_engine_calls_approve_and_mark_met_with_no_versions_and_catches_hubdb_refused(world, monkeypatch):
    w = world
    monkeypatch.setattr(access, "owner_and_admin_emails", lambda conn: [])
    assert R.approve(w.c, ANA, w.task, w.mockup)["verdict"] == "met"
    with pytest.raises(H.Refused) as caught:
        R.approve(w.c, BEN, w.task, w.signoff)
    assert caught.value.rule == "not_yours"
    with pytest.raises(H.Refused):
        R.mark_met(w.c, BEN, w.task, w.signoff, "note")
    assert R.mark_met(w.c, ANA, w.task, w.signoff, "note")["verdict"] == "override"
