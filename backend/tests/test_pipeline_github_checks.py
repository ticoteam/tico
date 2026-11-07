"""The pipeline check run on a pull request's commit (backend/pipeline_github_checks.py): recorded in the engine's
transaction, posted once per state, retried after a failure, and withheld until Checks: write is accepted."""

import httpx
import pytest

from backend import github_app as G
from backend import hubdb as H
from backend import pipeline_github_checks as C
from backend.tests.test_github_app import api, auth, connect, gh  # noqa: F401
from backend import pipeline_review as R
from backend.tests.test_pipeline_review import ANA, World

SHA = "a" * 40


class Checks:
    """The fake GitHub plus a check-runs endpoint that can fail."""

    def __init__(self, gh):
        self.gh, self.posts, self.fail = gh, [], 0

    def __call__(self, request):
        if "/check-runs" in request.url.path:
            if self.fail:
                self.fail -= 1
                return httpx.Response(500, json={"message": "boom"})
            body = __import__("json").loads(request.content)
            self.posts.append((request.method, request.url.path, body))
            return httpx.Response(201, json={"id": 4242})
        return self.gh(request)


@pytest.fixture
def setup(api, gh, monkeypatch, tmp_path):
    fake = Checks(gh)
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(fake))
    connect(api, administration="true")
    store = api.app_state.store
    with store.transaction() as c:
        w = World(tmp_path / "unused", conn=c)
        c.execute(
            "INSERT INTO task_links (id, task_id, kind, url, repo, number, detail, created_at) VALUES "
            "('l1',?,'pr','https://github.com/Acme/app/pull/1','Acme/app',1,?,?)",
            (w.task, '{"head_sha": "%s"}' % SHA, H.now()),
        )
    return api.app_state, fake, gh, w


def posted(state):
    return state[1].posts


def test_a_check_is_withheld_until_checks_write_is_accepted_then_posted_once_per_state(setup):
    app, fake, gh, w = setup
    gh.permissions = {"checks": "read", "metadata": "read"}
    with app.store.transaction() as c:
        C.publish(c, w.task, w.mockup, "waiting", SHA.upper())
        C.publish(c, w.task, w.mockup, "waiting", SHA)  # the same state again is the same row
        C.publish(c, w.task, w.mockup, "met", "b" * 40)  # no pull request carries this commit
        assert c.execute("SELECT count(*) FROM pipeline_check_runs").fetchone()[0] == 1
    assert C.flush(app.store, app.github_app) == {"posted": 0, "failed": 0, "withheld": True}
    assert fake.posts == []
    gh.permissions = {"checks": "write", "metadata": "read"}
    app.github_app.live.clear()
    assert C.flush(app.store, app.github_app) == {"posted": 1, "failed": 0, "withheld": False}
    method, path, body = fake.posts[0]
    assert (method, path) == ("POST", "/repos/Acme/app/check-runs")
    assert body["name"] == "Tico: Mockup" and body["head_sha"] == SHA and body["status"] == "in_progress"
    # Nothing to say twice.
    assert C.flush(app.store, app.github_app)["posted"] == 0
    # A new state updates the same check run.
    with app.store.transaction() as c:
        C.publish(c, w.task, w.mockup, "met", SHA)
    assert C.flush(app.store, app.github_app)["posted"] == 1
    method, path, body = fake.posts[1]
    assert (method, path) == ("PATCH", "/repos/Acme/app/check-runs/4242")
    assert body["conclusion"] == "success" and body["status"] == "completed"


def test_a_failed_post_is_retried_after_its_delay_and_not_in_a_tight_loop(setup, monkeypatch):
    app, fake, gh, w = setup
    gh.permissions = {"checks": "write", "metadata": "read"}
    with app.store.transaction() as c:
        C.publish(c, w.task, w.mockup, "failed", SHA)
    fake.fail = 1
    assert C.flush(app.store, app.github_app) == {"posted": 0, "failed": 1, "withheld": False}
    with app.store.read() as c:
        row = dict(c.execute("SELECT attempts, last_error FROM pipeline_check_runs").fetchone())
    assert row["attempts"] == 1 and "500" in row["last_error"]
    assert C.flush(app.store, app.github_app) == {"posted": 0, "failed": 0, "withheld": False}  # not yet due
    later = H.shift(H.now(), minutes=10)
    monkeypatch.setattr(C, "_now", lambda: later)
    assert C.flush(app.store, app.github_app)["posted"] == 1
    assert fake.posts[0][2]["conclusion"] == "failure"


def test_the_scheduler_pass_records_every_requirement_of_the_current_stage_at_the_pull_request_head(setup):
    app, fake, gh, w = setup
    gh.permissions = {"checks": "write", "metadata": "read"}
    assert C.publish_open(app.store) == 2  # the Mockup and the Sign-off, at the one head the ticket has
    with app.store.read() as c:
        rows = {r[0]: r[1] for r in c.execute("SELECT stage_requirement_id, state FROM pipeline_check_runs")}
    assert rows == {w.mockup: "waiting", w.signoff: "waiting"}


def test_a_state_change_while_a_post_holds_the_lease_does_not_let_a_second_flush_post_the_check(setup):
    app, fake, gh, w = setup
    gh.permissions = {"checks": "write", "metadata": "read"}
    with app.store.transaction() as c:
        C.publish(c, w.task, w.mockup, "waiting", SHA)
        # A flush is posting this row now: its lease runs for a while.
        c.execute(
            "UPDATE pipeline_check_runs SET lease_until=?, lease_token='in-flight' WHERE stage_requirement_id=?",
            (H.shift(H.now(), minutes=5), w.mockup),
        )
    with app.store.transaction() as c:
        C.publish(c, w.task, w.mockup, "met", SHA)  # a decision lands while the first post is in flight
    assert C.flush(app.store, app.github_app) == {"posted": 0, "failed": 0, "withheld": False}
    assert fake.posts == []
    with app.store.transaction() as c:
        c.execute("UPDATE pipeline_check_runs SET lease_until=NULL, lease_token=NULL")
    assert C.flush(app.store, app.github_app)["posted"] == 1
    assert len(fake.posts) == 1 and fake.posts[0][2]["conclusion"] == "success"


def states(app):
    with app.store.read() as c:
        return {r[0]: r[1] for r in c.execute("SELECT stage_requirement_id, state FROM pipeline_check_runs")}


def test_an_advancing_approval_leaves_its_check_passed_and_its_undo_puts_it_back(setup):
    app, fake, gh, w = setup
    gh.permissions = {"checks": "write", "metadata": "read"}
    with app.store.transaction() as c:
        w.c = c
        w.attach()
        w.approve(w.mockup)
        decision = w.approve(
            w.signoff
        )  # the last requirement of the stage: the ticket moves on, nothing is published later
        assert decision["advanced"] is True and w.step_of() == w.shipped
    assert states(app)[w.signoff] == "met"
    assert states(app)[w.mockup] == "met"
    assert C.flush(app.store, app.github_app)["posted"] == 2
    assert {b["external_id"]: b["conclusion"] for _, _, b in fake.posts}[w.signoff] == "success"
    with app.store.transaction() as c:
        w.c = c
        R.undo(c, ANA, decision["decision_id"])
    assert states(app)[w.signoff] == "waiting"


def test_the_scheduler_reaches_every_ticket_in_two_passes_and_resumes_from_its_cursor(setup):
    app, fake, gh, w = setup
    gh.permissions = {"checks": "write", "metadata": "read"}
    with app.store.transaction() as c:
        c.execute("DELETE FROM pipeline_check_cursor")
    with app.store.transaction() as c:
        w.c = c
        for i in range(249):
            task = w.ticket(f"Ticket {i}")
            c.execute(
                "INSERT INTO task_links (id, task_id, kind, url, repo, number, detail, created_at) VALUES "
                "(?,?,'pr',?,'Acme/app',?,?,?)",
                (
                    f"lx{i}",
                    task,
                    f"https://github.com/Acme/app/pull/{i + 2}",
                    i + 2,
                    '{"head_sha": "%040x"}' % (i + 2),  # each pull request has its own head commit
                    H.now(),
                ),
            )

    def reached():
        with app.store.read() as c:
            return c.execute("SELECT count(DISTINCT task_id) FROM pipeline_check_runs").fetchone()[0]

    C.publish_open(app.store)
    assert reached() == 200  # the first batch: tickets in id order, up to the batch size
    with app.store.read() as c:  # the cursor is stored: a restart resumes after the last ticket recorded
        last = c.execute("SELECT max(task_id) FROM pipeline_check_runs").fetchone()[0]
        assert c.execute("SELECT task_id FROM pipeline_check_cursor").fetchone()[0] == last
    C.publish_open(app.store)
    assert reached() == 250  # the rest on the second pass, and the cursor wraps for the one after
    with app.store.read() as c:
        assert c.execute("SELECT task_id FROM pipeline_check_cursor").fetchone()[0] == ""  # wrapped after a short pass
