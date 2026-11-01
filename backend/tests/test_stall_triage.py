"""Stall triage (backend/stall_triage.py): Decide reads a quiet task nothing has changed on, once per stand. Decide is a
fake engine here; no network."""
from contextlib import contextmanager

import pytest

from backend import stall_triage as ST
from backend import team_rules, wake_rules
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def ready(api, title="Recheck the cash warning", messages=(), private=None):
    """A quiet task `finance` owns that stands as at its last checkpoint, with the room's `messages` (actor, text)."""
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stall_triage": True})
        task = H.task_create(c, H.human_actor("ana"), title, "Before the deadline.", "bot:finance", private=private)
        for actor, text in messages:
            H.task_comment(c, actor, task["id"], text, wake=False)
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        row = H.task(c, task["id"])
        c.execute("UPDATE tasks SET checkpoint_state=?, checkpoint_by_id='bot:finance', updated_at=? WHERE id=?",
                  (wake_rules.state_of(c, row), H.shift(H.now(), seconds=-400), task["id"]))
    return task["id"]


def found(api, tid):
    with api.app.state.store.read() as c:
        return ST.inputs(c, H.now(), [dict(H.task(c, tid), quiet_minutes=7)])


def engine(cls, wait=None, calls=None, store=None):
    """A fake Decide: answers `cls` (and `wait`, a waiting_on option key, when it is asked)."""
    def ask(state, questions, label):
        if calls is not None:
            calls.append(state)
        answers = {"class": {"choice": cls, "confidence": 0.95, "probabilities": {cls: 0.95}}}
        if "waiting_on" in questions:
            key = wait or "unclear"
            answers["waiting_on"] = {"choice": key, "confidence": 0.95, "probabilities": {key: 0.95}}
        return {"answers": answers, "provider": "gateway"}
    return ask


def sweep(api, tid, at=None):
    with api.app.state.store.transaction() as c:
        return H.wake_stalled(c, at or H.now(), task_ids=[tid])


def stalls(api, tid):
    with api.app.state.store.read() as c:
        """Runs started by a stall wake on the task."""
        return c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE m.refs LIKE ? "
                         "AND m.refs LIKE '%stalled%'", (f'%{tid}%',)).fetchone()[0]


def test_a_held_quiet_task_decide_calls_stuck_gets_one_wake_and_none_after(api):
    tid = ready(api)
    assert sweep(api, tid)["woke"] == [] and stalls(api, tid) == 0, "today: same_state holds it, silently"
    [item] = found(api, tid)
    calls = []
    ST.run(api.app.state.store, [item], engine=engine(ST.STUCK, calls=calls))
    assert len(calls) == 1 and calls[0]["task"]["title"] == "Recheck the cash warning"
    assert sweep(api, tid)["woke"] == [tid] and stalls(api, tid) == 1, "the one wake, with the way out"
    assert sweep(api, tid)["woke"] == [] and found(api, tid) == [], "not asked again, not woken again, at this stand"
    with api.app.state.store.read() as c:
        assert ST.verdict_of(c, tid)["woke_at"]


def test_decide_never_reads_a_private_stalled_task_it_would_read_were_it_company_visible(api):
    shared = ready(api, title="Recheck the cash warning")
    private = ready(api, title="Recheck the salary review", private=True)
    with api.app.state.store.read() as c:
        assert H.task_private(c, H.task(c, private)) and not H.task_private(c, H.task(c, shared))
        assert private in [r["id"] for r in H.stalled_tasks(c, at=H.shift(H.now(), hours=3))], "private ones stall too"
    assert [i["id"] for i in found(api, shared)] == [shared]
    assert found(api, private) == []


def test_a_task_waiting_on_a_person_or_a_task_is_parked_with_no_run_and_finished_work_is_closed_crediting_the_speaker(api):
    store = api.app.state.store
    with store.transaction() as c:
        other = H.task_create(c, H.human_actor("ana"), "Bank statement", "x", "human:ben")
    sam = "human:ben"
    tid = ready(api, messages=[("bot:finance", f"Asked for the statement in {other['id']}; nothing back yet.")])
    [item] = found(api, tid)
    ST.run(store, [item], engine=engine(ST.WAITING, wait="task_1"))
    with store.read() as c:
        row = H.task(c, tid)
        assert [b["id"] for b in H.TR.live_blockers(c, tid, H.ACTIVE_STATUSES)] == [other["id"]]
        assert ST.verdict_of(c, tid)["outcome"] == "wait_set"
    assert sweep(api, tid)["woke"] == [] and stalls(api, tid) == 0

    ask = ready(api, "Get sign-off", messages=[(sam, "I will look at the draft tomorrow.")])
    [item] = found(api, ask)
    ST.run(store, [item], engine=engine(ST.WAITING, wait="person_1"))
    with store.read() as c:
        assert H.task(c, ask)["status"] == "waiting" and ST.verdict_of(c, ask)["outcome"] == "wait_set"
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id=? AND title LIKE 'Can you reply%'", (sam,)).fetchone()[0] == 1

    done = ready(api, "Send the invoice", messages=[(sam, "Thanks, the invoice arrived and is paid.")])
    [item] = found(api, done)
    ST.run(store, [item], engine=engine(ST.FINISHED))
    with store.read() as c:
        closed = H.task(c, done)
        assert closed["status"] == "closed" and H.person_name(c, sam) in closed["note"]


def test_decide_off_unsure_or_failing_changes_nothing_and_unsure_reaches_the_task_managers_line(api):
    store = api.app.state.store
    tid = ready(api)
    [item] = found(api, tid)

    def boom(state, questions, label):
        raise RuntimeError("provider down")
    ST.run(store, [item], engine=boom)
    assert ST.run(store, [item], engine=None) is not None
    with store.read() as c:
        assert ST.verdict_of(c, tid) is None, "an error records no verdict: it is asked again"
        assert H.task(c, tid)["status"] == "open"
    ST.run(store, [item], engine=engine(ST.UNSURE))
    with store.read() as c:
        row = dict(H.task(c, tid), quiet_minutes=90)
        assert ST.class_of(c, row) == ST.UNSURE
        from backend import task_manager
        assert "class: unsure" in task_manager._decide_words(c, row)
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stall_triage": False})
        assert ST.inputs(c, H.now(), [dict(H.task(c, tid), quiet_minutes=7)]) == []


def test_the_model_is_never_called_inside_a_transaction(api, monkeypatch):
    store = api.app.state.store
    tid = ready(api)
    [item] = found(api, tid)
    depth = {"n": 0}
    for name in ("transaction", "read"):
        real = getattr(store, name)

        @contextmanager
        def wrapped(*a, _real=real, **k):
            depth["n"] += 1
            try:
                with _real(*a, **k) as c:
                    yield c
            finally:
                depth["n"] -= 1
        monkeypatch.setattr(store, name, wrapped)

    def ask(state, questions, label):
        assert depth["n"] == 0
        return engine(ST.UNSURE)(state, questions, label)
    assert ST.run(store, [item], engine=ask)["asked"] == 1
