"""A person's snooze of one ask in their own Needs you (backend/ask_snoozes.py, #3769): it leaves their list, count and
Flow until its time and stays in everyone else's; the task and its bot are untouched; it comes back at the time, or
when the ask is asked again; only a person the ask waits on snoozes it, for themselves, at most a year on."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend import ask_snoozes as S
from backend import events
from backend import task_engine as E
from backend import task_pages as P
from backend import waiting_on as W
from backend.auth import Identity
from backend.blobs import Blobs
from backend.store import H, Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, post
from backend.tests.helpers.page_requirement import _page, _pipeline, _say, _task

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"


def at(**shift):
    return (datetime.now(timezone.utc) + timedelta(**shift)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def ask_ben(api, line="OK the launch note?"):
    """Ana asks Ben on a task of hers; (task, the ask's Needs-you id in Ben's list)."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, ANA, "Write the launch note", "Due Friday.", ANA, lint=False)
        asked = H.task_ask(c, ANA, task["id"], BEN, line, seen_version=H.task(c, task["id"])["version"])
    items = get(api, "needs-you", token="ben-test")["items"]
    return task, next(i["id"] for i in items if i.get("requirement_id") == asked["requirement_id"])


def ids(api, token):
    return [i["id"] for i in get(api, "needs-you", token=token)["items"]]


def count(api, token):
    return get(api, "needs-you?count=true", token=token)["count"]


def test_a_snoozed_ask_leaves_only_that_persons_list_and_count_and_undo_brings_it_back(api):
    task, item = ask_ben(api)
    with api.app.state.store.transaction() as c:
        H.task_create(c, BEN, "Book the venue", "", BEN, lint=False)  # Ana has nothing of Ben's: her list is her own
    ana_before, ben_before = (ids(api, "ana-test"), count(api, "ana-test")), count(api, "ben-test")
    until = at(hours=1)
    done = post(api, "needs-you/snooze", {"id": item, "until": until}, token="ben-test")
    assert done == {"id": item, "snoozed_until": until}
    assert item not in ids(api, "ben-test") and count(api, "ben-test") == ben_before - 1
    assert (ids(api, "ana-test"), count(api, "ana-test")) == ana_before, "nobody else's list moves"
    read = get(api, "needs-you", token="ben-test")
    assert read["snoozed_until"] == until, "the page reads the list again when it comes back"
    held = get(api, f"needs-you?version={read['version']}", token="ben-test")
    assert held["unchanged"] and held["snoozed_until"] == until, "a list it still holds says when to read again too"
    snoozed = get(api, "needs-you/snoozed", token="ben-test")
    assert [(i["id"], i["snoozed_until"]) for i in snoozed["items"]] == [(item, until)] and snoozed["next"] == until
    assert get(api, "needs-you/snoozed", token="ana-test")["items"] == [], "nobody reads another person's snoozes"
    # Still Ben's to answer, on the task as everyone reads it.
    with api.app.state.store.read() as c:
        waits = W.of(c, E.task(c, task["id"]))
        assert [(w.state, w.who) for w in waits if w.ask] == [(W.PERSON, BEN)]
    post(api, "needs-you/unsnooze", {"id": item}, token="ben-test")
    assert item in ids(api, "ben-test") and count(api, "ben-test") == ben_before
    assert get(api, "needs-you", token="ben-test")["snoozed_until"] is None


def test_the_task_and_its_bot_are_untouched(api):
    """The snooze is the person's: no task snooze, no new task version, no task change for anyone, no wake; the only
    change is a `needs` row for that person."""
    with api.app.state.store.transaction() as c:
        X.bot(c, "builder")
        task = H.task_create(c, ANA, "Write the launch note", "", "bot:builder", lint=False)
        asked = H.task_ask(c, "bot:builder", task["id"], BEN, "OK the headline?", seen_version=task["version"])
        before = E.task(c, task["id"])
        mark = events.mark(c)
        wakes = c.execute("SELECT count(*) FROM task_wakes").fetchone()[0] if _has(c, "task_wakes") else None
    item = next(
        i["id"]
        for i in get(api, "needs-you", token="ben-test")["items"]
        if i.get("requirement_id") == asked["requirement_id"]
    )
    post(api, "needs-you/snooze", {"id": item, "until": at(days=1)}, token="ben-test")
    with api.app.state.store.read() as c:
        after = E.task(c, task["id"])
        assert after["version"] == before["version"] and not after.get("snooze")
        assert c.execute("SELECT count(*) FROM snoozes WHERE task_id=?", (task["id"],)).fetchone()[0] == 0
        changes = [tuple(r) for r in c.execute("SELECT topic, subject_id FROM changes WHERE seq > ?", (mark,))]
        assert ("tasks", task["id"]) not in changes and ("needs", BEN) in changes
        assert {s for t, s in changes if t == "needs"} == {BEN}
        if wakes is not None:
            assert c.execute("SELECT count(*) FROM task_wakes").fetchone()[0] == wakes
        assert [(w.state, w.who) for w in W.of(c, after) if w.ask] == [(W.PERSON, BEN)]


def _has(c, table):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def test_it_comes_back_at_its_time_with_nothing_run(api):
    _, item = ask_ben(api)
    post(api, "needs-you/snooze", {"id": item, "until": at(minutes=5)}, token="ben-test")
    assert item not in ids(api, "ben-test")
    with api.app.state.store.transaction() as c:  # five minutes on
        c.execute("UPDATE ask_snoozes SET snoozed_until=?", (at(seconds=-1),))
    assert item in ids(api, "ben-test")
    assert get(api, "needs-you/snoozed", token="ben-test")["items"] == []


def test_given_to_someone_else_the_ask_is_in_their_list(api):
    """The snooze is Ben's alone: given to Cara, the ask is hers to answer at once."""
    task, item = ask_ben(api)
    post(api, "needs-you/snooze", {"id": item, "until": at(days=7)}, token="ben-test")
    with api.app.state.store.transaction() as c:
        H.task_give(c, BEN, task["id"], item.partition(":")[2], CARA, H.task(c, task["id"])["version"])
    assert item in ids(api, "cara-test")


def test_only_a_person_the_ask_waits_on_snoozes_it_for_themselves(api):
    _, item = ask_ben(api)
    for token in ("ana-test", "cara-test"):  # the one who asked, and someone it does not wait on
        post(api, "needs-you/snooze", {"id": item, "until": at(hours=1)}, token=token, expected=404)
    post(api, "needs-you/snooze", {"id": "nope:nothing", "until": at(hours=1)}, token="ben-test", expected=404)
    post(api, "needs-you/snooze", {"id": item, "until": at(hours=1)}, token="ben-test")
    post(api, "needs-you/unsnooze", {"id": item}, token="cara-test")  # touches only her own snoozes: none
    assert item not in ids(api, "ben-test"), "nobody changes another person's snooze"
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT person_id FROM ask_snoozes")] == [BEN]


def test_until_is_to_come_and_at_most_a_year_on(api):
    _, item = ask_ben(api)
    for until, said in ((at(minutes=-1), "to come"), (at(days=400), "a year"), ("soon-ish!!", "ISO-8601")):
        r = api.post(
            "/api/v2/needs-you/snooze",
            json={"id": item, "until": until},
            headers={"Authorization": "Bearer ben-test", "Idempotency-Key": until},
        )
        assert r.status_code == 422 and said in r.text, r.text
    post(api, "needs-you/snooze", {"id": item, "until": at(days=365)}, token="ben-test")


def test_a_bot_or_anyone_acting_for_the_person_but_their_assistant_is_refused():
    me = Identity(BEN, "human", "ben@acme.example")
    assert S.person(me) == BEN
    assert S.person(replace(me, via_token=True)) == BEN, "their own API token"
    assert S.person(replace(me, via="assistant", via_token=True, task_actor="bot:assistant")) == BEN
    for who in (
        Identity("bot:builder", "bot"),
        replace(me, via="botops", task_actor="bot:botops"),
        replace(me, via="page_chat"),
        replace(me, task_actor="bot:builder"),
        replace(me, view="view-1"),
        Identity("human:tico-support", "human", support="app"),
    ):
        with pytest.raises(Problem) as refused:
            S.person(who)
        assert refused.value.status == 403, who


@pytest.fixture
def store(company):
    company.blobs = Blobs(company.settings)
    return company


def test_a_new_version_of_the_page_is_not_hidden(store):
    """A snooze holds only while the ask is the one snoozed: the bot's next version is asked again, and back."""
    with store.transaction() as c:
        for slug in ("writer", "manager"):
            X.bot(c, slug)
        _pipeline(c, person=ANA)
        row = _task(c)
        fid, _ = _page(c, store)
        made = P.attach(
            c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=E.task(c, row["id"])["version"]
        )
        _say(c, made, "met")
        (item,) = [i for i in W.needs(c, ANA) if i["kind"] == "ask"]
        visit_id, requirement_id = W.ask_id(item).split(":")
        now = H.now()
        c.execute(
            "INSERT INTO ask_snoozes (person_id, visit_id, requirement_id, task_id, asked_at, snoozed_until, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (ANA, visit_id, requirement_id, row["id"], item["since"], at(days=7), now, now),
        )
        assert [i for i in W.needs(c, ANA) if i["kind"] == "ask"] == []
        assert [i["snoozed_until"] for i in W.needs(c, ANA, "only")] == [at_of(c)]
        assert W.needs_by_person(c).get(ANA, []) == W.needs(c, ANA), "every person's pass agrees"
        _page(c, store, "<p>v2</p>", fid=fid)
        second = P.attach(
            c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=E.task(c, row["id"])["version"]
        )
        _say(c, second, "met")
        assert [i["requirement_id"] for i in W.needs(c, ANA) if i["kind"] == "ask"] == [requirement_id]
        assert W.needs(c, ANA, "only") == []


def at_of(c):
    return c.execute("SELECT snoozed_until FROM ask_snoozes").fetchone()[0]


def test_never_stuck_counts_a_snoozed_ask_as_listed(store):
    """Rule 3 (each person's turn shows in their Needs you) holds while the person snoozed the ask."""
    from backend import never_stuck

    with store.transaction() as c:
        X.bot(c, "builder")
        X.pipeline(c, manager=None)
        task = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
        E.add_requirement(c, task["id"], "bot:builder", "ok", BEN, "OK the headline", seen_version=task["version"])
        (item,) = [i for i in W.needs(c, BEN) if i["kind"] == "ask"]
        visit_id, requirement_id = W.ask_id(item).split(":")
        now = H.now()
        c.execute(
            "INSERT INTO ask_snoozes (person_id, visit_id, requirement_id, task_id, asked_at, snoozed_until, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (BEN, visit_id, requirement_id, task["id"], item["since"], at(days=1), now, now),
        )
        assert W.needs(c, BEN) == []
        # never_stuck only checks: rule 3 expects the person's Needs you to list each of their waits once, and leaves
        # out the asks they snoozed. The ask is still Ben's turn, the bot's own waits are checked as before, and no
        # rule breaks, so nothing is retried, woken or held over it.
        assert [(w.state, w.who) for w in W.of(c, E.task(c, task["id"])) if w.ask] == [(W.PERSON, BEN)]
        assert [b for b in never_stuck.rules_hold(c) if task["id"] in b] == []
        c.execute("DELETE FROM ask_snoozes")
        assert [b for b in never_stuck.rules_hold(c) if task["id"] in b] == [], "the same with the snooze gone"


def test_deleting_the_task_deletes_its_snoozes(api):
    from backend import task_delete

    task, item = ask_ben(api)
    post(api, "needs-you/snooze", {"id": item, "until": at(days=1)}, token="ben-test")
    with api.app.state.store.transaction() as c:
        task_delete.delete_tasks(c, [task["id"]], apply=True, actor=ANA)
        assert c.execute("SELECT count(*) FROM ask_snoozes WHERE task_id=?", (task["id"],)).fetchone()[0] == 0
    assert get(api, "needs-you/snoozed", token="ben-test")["items"] == []
