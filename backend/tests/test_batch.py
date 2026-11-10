"""A batch (backend/batch.py): Needs you, frozen, walked one item at a time, applied on commit with the person's own
identity through the task calls. OK and Send back on a bot's ask, an answer to its question, Done or Close on a task
it filed for the person. An OK bound to what it lets go is never given on a walk. On a store with no app."""

import pytest

from backend import batch as B
from backend import task_asks
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.helpers import engine as X

ANA = Identity("human:ana", "human")
FINANCE = "bot:finance"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "finance")
        yield conn


def _work(c, title="Run the LinkedIn ads"):
    return H.task_create(c, "human:ana", title, "Q4 campaign.", FINANCE, lint=False)


def _sent(log):
    def send(c, who, text, refs, to):
        log.append((to, text, refs))
        return {"id": H.new_id()}

    return send


def _start(c):
    return B.start(c, ANA.actor, {}, reader=ANA.actor)


def test_a_batch_holds_what_bots_ask_and_commit_applies_it_through_the_task_calls(c):
    ads = _work(c)
    ok = H.task_ask(
        c, FINANCE, ads["id"], "human:ana", "Spend the Q4 ads budget?", kind="ok", seen_version=ads["version"]
    )
    other = _work(c, "Pick the launch day")
    question = H.task_ask(
        c,
        FINANCE,
        other["id"],
        "human:ana",
        "Monday or Friday?",
        choices=["Monday", "Friday"],
        seen_version=other["version"],
    )
    chore = H.task_create(c, FINANCE, "Sign the lease", "Sign it today.", "human:ana", lint=False)
    batch = _start(c)
    keys = [B._items(c.execute("SELECT * FROM batches WHERE id=?", (batch["id"],)).fetchone())]
    assert batch["total"] == 3 and batch["item"]["key"] == "ask:" + ok["requirement_id"]
    assert {i["key"] for i in keys[0]} == {
        "ask:" + ok["requirement_id"],
        "ask:" + question["requirement_id"],
        "task:" + chore["id"],
    }
    B.respond(c, ANA.actor, batch["id"], "decide", "Go ahead.", n=1, decision="ok")
    B.respond(c, ANA.actor, batch["id"], "decide", "Friday", n=2, decision="answer")
    B.respond(c, ANA.actor, batch["id"], "decide", "", n=3, decision="done")
    assert H.task(c, chore["id"])["status"] == "open"  # nothing is applied while the person talks
    log = []
    out = B.commit(c, ANA, batch["id"], _sent(log))
    assert out["errors"] == []
    view = H.task_view(c, ads["id"], "human:ana")
    assert next(r for r in view["to_leave"] if r["requirement_id"] == ok["requirement_id"])["met"]
    asked = H.task_view(c, other["id"], "human:ana")["to_leave"]
    assert next(r for r in asked if r["requirement_id"] == question["requirement_id"])["answer"]["value"] == "Friday"
    assert H.task(c, chore["id"])["status"] == "done"
    assert [to for to, _, _ in log] == [FINANCE]


def test_send_back_needs_notes_and_an_ok_or_a_close_only_fits_its_kind(c):
    ads = _work(c)
    ok = H.task_ask(
        c, FINANCE, ads["id"], "human:ana", "Spend the Q4 ads budget?", kind="ok", seen_version=ads["version"]
    )
    batch = _start(c)
    with pytest.raises(Problem, match="what to change"):
        B.respond(c, ANA.actor, batch["id"], "decide", "", decision="send_back")
    with pytest.raises(Problem, match="ok or send_back"):
        B.respond(c, ANA.actor, batch["id"], "decide", "", decision="close")
    B.respond(c, ANA.actor, batch["id"], "decide", "Halve it.", decision="send_back")
    B.commit(c, ANA, batch["id"], _sent([]))
    found = next(
        r for r in H.task_view(c, ads["id"], "human:ana")["to_leave"] if r["requirement_id"] == ok["requirement_id"]
    )
    assert (found["met"], found["answer"]["value"], found["answer"]["note"]) == (False, "no", "Halve it.")


def test_a_bound_ok_is_never_given_on_a_walk(c):
    asked = task_asks.ask_ok(
        c, FINANCE, "human:ana", "Send this email?", shows=["email:e1"], subject="email_send:e1", content={"to": "x"}
    )
    batch = _start(c)
    assert batch["item"]["preview_only"]
    with pytest.raises(Problem, match="preview"):
        B.respond(c, ANA.actor, batch["id"], "decide", "", decision="ok")
    assert task_asks.standing(c, asked["requirement_id"]) == task_asks.WAITING


def test_an_assistant_walking_the_list_may_send_back_but_never_ok(c):
    ads = _work(c)
    ok = H.task_ask(
        c, FINANCE, ads["id"], "human:ana", "Spend the Q4 ads budget?", kind="ok", seen_version=ads["version"]
    )
    batch = _start(c)
    B.respond(c, ANA.actor, batch["id"], "decide", "", decision="ok")
    out = B.commit(c, Identity("human:ana", "human", via="assistant", confirmed=True), batch["id"], _sent([]))
    assert out["errors"] and "own" in out["errors"][0]
    found = next(
        r for r in H.task_view(c, ads["id"], "human:ana")["to_leave"] if r["requirement_id"] == ok["requirement_id"]
    )
    assert not found["met"] and found["answer"] is None


def test_a_batch_belongs_to_a_person_and_later_keeps_an_item_out_for_a_day(c):
    ads = _work(c)
    H.task_ask(c, FINANCE, ads["id"], "human:ana", "Spend the Q4 ads budget?", kind="ok", seen_version=ads["version"])
    batch = _start(c)
    with pytest.raises(Problem, match="not found"):
        B.respond(c, "human:ben", batch["id"], "skip")
    B.respond(c, ANA.actor, batch["id"], "later")
    B.commit(c, ANA, batch["id"], _sent([]))
    assert _start(c)["total"] == 0


def test_a_comment_that_fails_halfway_leaves_nothing_of_itself_and_the_batch_goes_on(c, monkeypatch):
    """A bot the person may not message gets its lines as task comments; one that writes and then fails is rolled back to
    before it, so the batch's commit never carries half a comment."""
    task = _work(c)
    before = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def half(conn, actor, task_id, text):
        H.event(conn, actor, "comment.half", task_id, {})
        raise RuntimeError("the comment failed after writing")

    monkeypatch.setattr(H, "task_comment", half)
    applied, errors = B._comment_instead(c, ANA, FINANCE, [(f"task:{task['id']}", "Run the ads\nGo.")], "test")
    assert applied == [] and "failed after writing" in errors[0]
    assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
