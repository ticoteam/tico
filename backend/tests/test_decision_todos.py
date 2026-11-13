"""The release sweep of decisions filed as to-dos (backend/decision_todos.py; Tasks spec "Bots ask people a question
with choices", AC-11): each open to-do a bot filed for a person before the release is judged once by the ask check; a
decision is closed with a note and the bot that filed it is woken to ask it, so the person's list has no such to-do."""

import json
from datetime import datetime, timedelta, timezone

from backend import decision_todos as D
from backend import hubdb as H
from backend.tests.helpers.ask_ingest import FakeModel, gate  # noqa: F401
from backend.tests.helpers.tasks_board import api, get  # noqa: F401

DECISION = "Pick the pricing page we ship"
CHORE = "Turn on read-only viewing for Builder"


class ByText(FakeModel):
    """A decision when the to-do's words hold DECISION, else a chore."""

    def __call__(self, state, questions, label=None):
        self.kind = "decision" if DECISION in json.dumps(state) else "chore"
        return super().__call__(state, questions, label)


def in_an_hour():
    return (datetime.now(timezone.utc) + timedelta(hours=1, minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def todo(api, title, owner="human:ben", by="bot:ops"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, by, title, f"{title}, please.", owner, lint=False)


def task(api, task_id):
    with api.app.state.store.read() as c:
        return H.task(c, task_id)


def ben_list(api):
    return {i.get("task_id") for i in get(api, "needs-you", "ben-test")["items"]}


def notices(api, bot="bot:ops"):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT body, refs FROM messages WHERE sender_id=? AND recipient_id=? AND kind='notice'",
                (H.KEEPER, bot),
            )
        ]


def test_a_decision_filed_as_a_to_do_is_closed_with_a_note_and_its_bot_is_woken_to_ask_it(gate):  # noqa: F811
    api, _, _ = gate
    model = ByText()
    api.app.state.judge = model
    decision, chore = todo(api, DECISION), todo(api, CHORE)
    assert {decision["id"], chore["id"]} <= ben_list(api)
    out = D.sweep(api.app.state.store, model)
    assert (out["judged"], out["closed"], out["done"]) == (2, [decision["id"]], True)
    assert len(model.calls) == 2  # one ask check each, no other call
    closed = task(api, decision["id"])
    assert closed["status"] == "closed"
    with api.app.state.store.read() as c:
        why = c.execute(
            "SELECT why FROM visits WHERE task_id=? AND left_at IS NOT NULL ORDER BY left_at DESC LIMIT 1",
            (decision["id"],),
        ).fetchone()[0]
    assert "this is a decision for" in why and "asks it as a question" in why
    # The bot that filed it is woken with how to ask it; the chore stays a to-do.
    [told] = [n for n in notices(api) if "was a decision" in n["body"]]
    assert DECISION in told["body"] and "tico task ask <task> --to ben" in told["body"]
    refs = json.loads(told["refs"])
    assert refs["task"] == decision["id"] and refs["needs_action"] is True and not refs.get("quiet")  # it wakes
    assert task(api, chore["id"])["status"] == "open"
    assert decision["id"] not in ben_list(api) and chore["id"] in ben_list(api)
    # Done: a later pass asks nothing.
    todo(api, DECISION + " again")
    assert D.sweep(api.app.state.store, model)["done"] and len(model.calls) == 2


def test_a_to_do_the_check_could_not_judge_waits_for_the_next_pass_and_nothing_is_asked_twice(gate):  # noqa: F811
    api, _, _ = gate
    model = ByText()
    api.app.state.judge = model
    first, second = todo(api, DECISION), todo(api, DECISION + " too")
    model.fail = RuntimeError("provider down")
    out = D.sweep(api.app.state.store, model)
    assert (out["judged"], out["closed"], out["done"]) == (0, [], False)
    # The first failed call ends the pass: a model outage does not spend the day's ask checks every few seconds.
    assert len(model.calls) == 1
    assert task(api, first["id"])["status"] == "open"
    model.fail = None
    # Within the hour after a failure no pass asks anything.
    assert D.sweep(api.app.state.store, model)["judged"] == 0 and len(model.calls) == 1
    # Filed after the sweep began: the ask check at the write sees it, so the sweep leaves it alone.
    later = todo(api, DECISION + " later")
    out = D.sweep(api.app.state.store, model, now=in_an_hour())
    assert (sorted(out["closed"]), out["done"]) == (sorted([first["id"], second["id"]]), True)
    assert task(api, later["id"])["status"] == "open"
    calls = len(model.calls)
    D.sweep(api.app.state.store, model)
    assert len(model.calls) == calls


def test_with_no_decision_model_the_sweep_writes_nothing_and_waits_for_one(gate):  # noqa: F811
    """Review 1: a pass with no model must not mark the sweep done, or a team that sets Decide up later never has its
    decision to-dos closed."""
    api, _, _ = gate
    api.app.state.judge = None
    decision = todo(api, DECISION)
    out = D.sweep(api.app.state.store, None)
    assert (out["judged"], out["closed"], out["done"]) == (0, [], False)
    with api.app.state.store.read() as c:
        assert D.state(c) == {}
        assert not c.execute("SELECT 1 FROM events WHERE action=?", (D.JUDGED,)).fetchone()
    model = ByText()
    api.app.state.judge = model
    out = D.sweep(api.app.state.store, model)
    assert (out["closed"], out["done"]) == ([decision["id"]], True)
    assert task(api, decision["id"])["status"] == "closed"


def test_only_open_to_dos_a_bot_filed_for_a_person_are_judged(gate):  # noqa: F811
    api, _, _ = gate
    model = FakeModel(kind="decision")
    api.app.state.judge = model
    bots = todo(api, DECISION)
    persons = todo(api, DECISION + " (a person's)", by="human:ana")
    for_a_bot = todo(api, DECISION + " (for a bot)", owner="bot:cpo")
    done = todo(api, DECISION + " (done)")
    with api.app.state.store.transaction() as c:
        H.task_close(c, H.KEEPER, done["id"], "Not needed")
    out = D.sweep(api.app.state.store, model)
    assert out["closed"] == [bots["id"]] and len(model.calls) == 1
    assert task(api, persons["id"])["status"] == "open" and task(api, for_a_bot["id"])["status"] == "open"


def test_the_sweep_does_not_run_on_a_test_server_from_the_tick(gate):  # noqa: F811
    api, _, _ = gate
    assert D.tick(api.app.state.store) is False
