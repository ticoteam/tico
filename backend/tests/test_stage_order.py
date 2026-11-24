"""Tico sorts a stage by priority (backend/stage_order.py, on Needs you's machinery in backend/needs_order.py): a stage's
list read with `sort=priority` shows in the person's saved order, tasks the order does not hold go in by the quick rule
(waiting on them, then due soon, above it; the rest below, newest first) and pages cut that one order. Only the person's
own ask (`POST /api/v2/stages/{id}/sort`) claims one background model call per person and stage when it is due; the
result, or on a failure the order shown, is saved, the claim cleared, and a `setup` change sent to that person alone."""

import json
from dataclasses import replace

import pytest

from backend import events as E
from backend import needs_order as N
from backend import stage_order as S
from backend import teammate_access, updates, waiting_on
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, post
from clients.judge import JudgeError, Written

BEN = "human:ben"


class Model:
    """A writer that answers `order` (task numbers as the prompt lists them), or raises it, and keeps each prompt."""

    def __init__(self, order):
        self.order, self.prompts, self.model = order, [], "acme-order-model"

    def __call__(self, system, user):
        self.prompts.append(user)
        if isinstance(self.order, Exception):
            raise self.order
        return Written({"order": self.order}, {"prompt_tokens": 700, "completion_tokens": 9})


@pytest.fixture
def started(monkeypatch):
    """The sorts handed to a thread, run by the test when it says."""
    found = []
    monkeypatch.setattr(N, "spawn", found.append)
    return found


def model(monkeypatch, order):
    found = Model(order)
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: found)
    return found


def product(api):
    """A pipeline of two work stages, Spec then Build."""
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": "Product",
        "stages": [{"id": "spec", "name": "Spec", **stage}, {"id": "build", "name": "Build", **stage}],
    }
    post(api, "pipelines/product", record)


def task(api, title, owner="ana", due=None, ago=0):
    """A task in Spec, Ben's to read: his own (his turn) or a public one of Ana's. Filed `ago` minutes back."""
    made = post(api, "tasks", {"title": title, "body": "As discussed.", "owner": owner, "pipeline": "product"})
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE tasks SET private=0, visibility='public', due_at=?, created_at=? WHERE id=?",
            (due, H.shift(H.now(), minutes=-ago), made["id"]),
        )
    return made["id"]


def listed(api, query="", token="ben-test", stage="spec"):
    return get(api, f"tasks?stage={stage}&sort=priority&brief=true" + query, token=token)


def order(api, token="ben-test"):
    return [t["id"] for t in listed(api, token=token)["tasks"]]


def ask(api, token="ben-test"):
    """The list opens: its rows, then its ask to sort with the ids it shows; whether Tico sorts it."""
    ids = order(api, token)
    return ids, post(api, "stages/spec/sort", {"ids": ids}, token=token)["sorting"]


def row(api):
    with api.app.state.store.read() as c:
        found = c.execute("SELECT * FROM stage_orders WHERE person_id=? AND stage_id='spec'", (BEN,)).fetchone()
        return dict(found) if found else None


def save(api, ids, minutes=60):
    """Ben's saved order of Spec, sorted `minutes` ago."""
    at = H.shift(H.now(), minutes=-minutes)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO stage_orders (person_id, stage_id, task_ids, sorted_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT (person_id, stage_id) DO UPDATE SET task_ids=excluded.task_ids",
            (BEN, "spec", json.dumps(ids), at, at, at),
        )


def age(api, **when):
    """Move the saved row's times back by minutes (None clears), and `updated_at` with them."""
    moved = [m for m in when.values() if m is not None]
    if moved and "updated_at" not in when:
        when["updated_at"] = min(moved)
    with api.app.state.store.transaction() as c:
        for column, minutes in when.items():
            at = None if minutes is None else H.shift(H.now(), minutes=-minutes)
            c.execute(f"UPDATE stage_orders SET {column}=? WHERE person_id=?", (at, BEN))


def pushed(api):
    """The stage-order changes written: (person, stage) each."""
    with api.app.state.store.read() as c:
        return [
            tuple(r)
            for r in c.execute("SELECT subject_id, ref FROM changes WHERE topic='setup' AND kind='stage_order'")
        ]


def test_the_saved_order_shows_new_tasks_go_in_by_the_quick_rule_and_the_list_is_read_whole(api, monkeypatch):
    product(api)
    a, b, c_ = task(api, "Price list", ago=50), task(api, "Launch note", ago=40), task(api, "Brochure", ago=30)
    save(api, [c_, a, b, "gone-task"])
    older = task(api, "Old idea", ago=20)
    mine = task(api, "Ben's spec", owner="ben", ago=15)  # waiting on Ben: his turn
    soon = task(api, "Invoice", due=H.shift(H.now(), hours=20), ago=10)
    late = task(api, "Contract", due=H.shift(H.now(), days=9), ago=5)
    newest = task(api, "Brand new", ago=1)
    whole = [mine, soon, c_, a, b, newest, late, older]
    assert order(api) == whole

    # Whole, in one answer: never paged, so a move in the order cannot skip or repeat a row. Past `limit`, cut and said.
    whole_read = listed(api)
    assert whole_read["next_offset"] is None and whole_read["next_after"] is None and "truncated" not in whole_read
    cut = listed(api, "&limit=3")
    assert [t["id"] for t in cut["tasks"]] == whole[:3] and cut["truncated"] is True and cut["next_after"] is None
    for query in ("sort=priority", "stage=spec&sort=priority&after=xyz", "stage=spec&sort=priority&offset=3"):
        refused = api.get("/api/v2/tasks?" + query, headers=headers("ben-test"))
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "sort", query
    cursor = whole_read["cursor"]
    refused = api.get(f"/api/v2/tasks?stage=spec&sort=priority&changed_after={cursor}", headers=headers("ben-test"))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "sort"

    # The saved order is Ben's own: Agent Ops or his Assistant reading for him gets the quick rule alone.
    store = api.app.state.store
    ben = store.settings.test_identities["ben-test"]
    for n, acting in enumerate((replace(ben, via="botops"), replace(ben, via="assistant"))):
        monkeypatch.setitem(api.app.state.auth.settings.test_identities, f"ben-acting-{n}", acting)
        assert order(api, token=f"ben-acting-{n}") == [mine, soon, newest, late, older, c_, b, a]


def test_one_background_sort_per_person_and_stage_saved_pushed_and_due_again_only_by_the_rules(
    api, monkeypatch, started
):
    product(api)
    a, b = task(api, "Price list", ago=20), task(api, "Launch note", owner="ben", ago=10)
    blocker = task(api, "Contract", ago=30)
    waits = post(api, "tasks", {"title": "Ship after the contract", "body": "x", "owner": "ana"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0, visibility='public' WHERE id=?", (waits["id"],))
        c.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'depends_on',?)",
            (waits["id"], blocker, H.now()),
        )
    writer = model(monkeypatch, [3, 1, 2])

    # Never sorted: the quick rule shows at once, and one sort is claimed and handed to a thread.
    shown = order(api)
    assert shown == [b, a, blocker]
    assert ask(api) == (shown, True) and len(started) == 1 and not writer.prompts
    assert listed(api)["sorting"] is True
    assert ask(api)[1] is True and len(started) == 1, "one sort per person and stage at a time"
    started.pop()()
    [user] = writer.prompts
    assert "Stage: Product > Spec" in user
    assert f"1. Launch note | has it: you | waiting on you" in user
    assert "3. Contract | has it: Ana | holds up 1 other task" in user
    assert row(api)["started_at"] is None and pushed(api) == [(BEN, "spec")]
    with api.app.state.store.read() as c:
        spent = [json.loads(r[0])["label"] for r in c.execute("SELECT detail FROM events WHERE action='judge.spend'")]
    assert spent == ["stage-order"]
    assert ask(api) == ([blocker, b, a], False) and not started, "saved: shown in the model's order, no call"

    # A new task within 15 minutes of the sort: the quick rule places it, no call. Past them: one call.
    fresh = task(api, "Brand new", ago=0)
    assert ask(api) == ([blocker, b, a, fresh], False) and not started
    age(api, sorted_at=16)
    assert ask(api)[1] is True and len(started) == 1
    writer.order = [4, 3, 2, 1]
    started.pop()()
    assert order(api) == [fresh, a, b, blocker]

    # Unchanged and under 6 hours old: no call; over them, one.
    age(api, sorted_at=5 * 60)
    assert ask(api)[1] is False and not started
    age(api, sorted_at=7 * 60)
    assert ask(api)[1] is True and len(started) == 1


def test_a_failed_call_saves_the_order_shown_and_ends_the_sort(api, monkeypatch, started):
    product(api)
    a, b = task(api, "Price list", ago=20), task(api, "Launch note", ago=10)
    model(monkeypatch, JudgeError("unavailable", "down"))
    assert ask(api) == ([b, a], True)
    started.pop()()
    saved = row(api)
    assert json.loads(saved["task_ids"]) == [b, a] and saved["started_at"] is None and saved["sorted_at"]
    assert pushed(api) == [(BEN, "spec")] and listed(api)["sorting"] is False
    assert ask(api) == ([b, a], False) and not started, "tried: not again for 15 minutes"


def test_the_model_orders_the_top_of_a_long_stage_and_the_rest_keep_their_place(api, monkeypatch, started):
    monkeypatch.setattr(S, "RANK_MAX", 2)
    product(api)
    a, b, c_ = task(api, "Price list", ago=30), task(api, "Launch note", ago=20), task(api, "Brochure", ago=10)
    writer = model(monkeypatch, [2, 1])
    assert ask(api) == ([c_, b, a], True)
    started.pop()()
    assert "2. Launch note" in writer.prompts[0] and "Price list" not in writer.prompts[0]
    assert order(api) == [b, c_, a]


def test_only_the_persons_own_ask_sorts_and_an_unknown_stage_is_refused(api, monkeypatch, started):
    product(api)
    task(api, "Price list", ago=20), task(api, "Launch note", ago=10)
    writer = model(monkeypatch, [2, 1])
    store, auth = api.app.state.store, api.app.state.auth
    ben = store.settings.test_identities["ben-test"]
    ids = order(api)
    for who in (
        replace(ben, view="view-1"),
        replace(ben, via="botops", task_actor="bot:botops"),
        replace(ben, via="assistant"),
    ):
        assert S.start(store, auth, who, "spec", ids) == {"sorting": False, "sorting_until": None}
    kept = store.settings
    store.settings = replace(kept, demo=True)
    try:
        assert S.start(store, auth, ben, "spec", ids) == {"sorting": False, "sorting_until": None}
    finally:
        store.settings = kept
    assert not started and not writer.prompts and row(api) is None
    assert api.post("/api/v2/stages/nope/sort", json={"ids": ids}, headers=headers("ben-test")).status_code == 404

    # The worker reads the list as Ben may read it now: a person who may no longer sign in is not read for at all.
    assert ask(api)[1] is True
    monkeypatch.setattr(api.app.state.auth, "identity_for_actor", lambda *a: (_ for _ in ()).throw(Problem("x", "y")))
    started.pop()()
    assert not writer.prompts and row(api)["started_at"] is None and row(api)["sorted_at"] is None


def test_a_new_order_reaches_its_person_alone(api):
    """Only Ben's own stream: never another person's, nor one acting for him."""
    store, auth = api.app.state.store, api.app.state.auth
    ben, ana = store.settings.test_identities["ben-test"], store.settings.test_identities["ana-test"]
    with store.read() as c:
        before = E.latest(c)
    with store.transaction() as c:
        E.stage_order_moved(c, BEN, "spec")
    acting = replace(ben, via="botops", task_actor="bot:botops")  # Agent Ops for Ben carries his actor, not him
    for who, wanted in ((ben, [{"area": "stage_order", "stage": "spec"}]), (ana, []), (acting, [])):
        with store.read() as c:
            found = E.read(c, auth, who, before, topics=("setup",))[0]
        assert [
            {k: v for k, v in json.loads(d.encoded).items() if k in ("area", "stage")} for _, _, d in found
        ] == wanted
        with store.read() as c:  # the same from a round's shared tail (_Snapshot.changes)
            snapshot = E._Snapshot(c, [type("Poll", (), {"cursor": before, "scope": ()})()])
            rows = snapshot.changes(who, before, 10, ("setup",), (), (), (), (), ())
        assert [r["subject_id"] for r in rows] == [BEN] * len(wanted)


def test_no_call_once_the_stage_is_no_longer_due_when_the_sort_runs(api, monkeypatch, started):
    """The worker checks again on its own read: the one new task gone from the stage by then leaves nothing to sort."""
    product(api)
    a, b = task(api, "Price list", ago=30), task(api, "Launch note", ago=20)
    save(api, [b, a], minutes=20)
    writer = model(monkeypatch, [2, 1])
    fresh = task(api, "Brand new", ago=0)
    assert ask(api) == ([b, a, fresh], True)
    post(api, f"tasks/{fresh}/move", {"to": "build", "why": "Specced"})
    sorted_at = row(api)["sorted_at"]
    started.pop()()
    assert not writer.prompts and row(api)["started_at"] is None and row(api)["sorted_at"] == sorted_at
    assert json.loads(row(api)["task_ids"]) == [b, a]


def test_a_new_task_past_the_ranked_window_is_saved_in_place_with_no_call(api, monkeypatch, started):
    """A task new to the order that lands past the first RANK_MAX (not the reader's, not due soon) is one the model would
    never see: no paid call, the list saved as shown with it in place, so it is not new again."""
    monkeypatch.setattr(S, "RANK_MAX", 2)
    product(api)
    a, b, c_ = task(api, "Price list", ago=30), task(api, "Launch note", ago=20), task(api, "Brochure", ago=10)
    save(api, [a, b, c_], minutes=20)
    writer = model(monkeypatch, [2, 1])
    fresh = task(api, "Brand new", ago=0)
    sorted_at = row(api)["sorted_at"]
    assert ask(api) == ([a, b, c_, fresh], True)
    started.pop()()
    assert not writer.prompts and row(api)["sorted_at"] == sorted_at and row(api)["started_at"] is None
    assert json.loads(row(api)["task_ids"]) == [a, b, c_, fresh]
    age(api, updated_at=16)
    assert ask(api)[1] is False and not started


def test_the_prompt_never_names_an_owner_the_person_may_not_see(api, monkeypatch, started):
    """As the list names them: a hidden teammate's tasks show with no owner, so the model is not told who has them."""
    product(api)
    task(api, "Price list", ago=20), task(api, "Launch note", owner="ben", ago=10)
    real = teammate_access.unseen
    monkeypatch.setattr(
        teammate_access,
        "unseen",
        lambda c, viewer, auth=None: ({"ana"}, set()) if viewer.actor == BEN else real(c, viewer, auth),
    )
    writer = model(monkeypatch, [2, 1])
    assert {t["owner_id"] for t in listed(api)["tasks"]} == {"", BEN}
    assert ask(api)[1] is True
    started.pop()()
    assert "Ana" not in writer.prompts[0] and "| has it: you" in writer.prompts[0]


def test_a_paused_wait_is_never_the_readers_turn(api, monkeypatch):
    """A Paused wait names its bot (the fixer is someone else): only a person's own turn counts as waiting on them."""
    found = {
        "t1": [waiting_on.Wait(waiting_on.PAUSED, who="bot:builder")],
        "t2": [waiting_on.Wait(waiting_on.PERSON, who=BEN)],
    }
    monkeypatch.setattr(waiting_on, "waiting_on_many", lambda c, ids: found)
    assert S.waiting(None, "bot:builder", ["t1", "t2"]) == set() and S.waiting(None, BEN, ["t1", "t2"]) == {"t2"}


def test_a_page_reads_a_few_statements_whatever_the_stages_size(api):
    """A 20-row page of an unsorted stage arranges the whole stage from the tasks' own columns: the same statements for
    30 tasks as for 300, never a wait or a query per task off the page (a ready task whose next stage is full reads
    whether it rides with its filer, one statement each)."""
    stages = [{"id": "triage", "name": "Triage", "owner": "assignee"}, {"id": "ship", "name": "Ship", "task_limit": 1}]
    post(api, "pipelines/ops", {"name": "Ops", "stages": stages})
    filed = [post(api, "tasks", {"title": t, "body": "x", "owner": "ana", "pipeline": "ops"})["id"] for t in "ab"]
    post(api, f"tasks/{filed[0]}/move", {"to": "ship", "why": "Full"})
    base = filed[1]
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=0, visibility='public' WHERE id=?", (base,))

    def grow(n):
        """`n` more copies of the seed task, filed straight into Spec (the move guard opened for each, as move() does)."""
        with store.transaction() as c:
            skip = ("id", "number", "title", "pipeline_id", "stage_id")
            columns = ", ".join(r[1] for r in c.execute("PRAGMA table_info(tasks)") if r[1] not in skip)
            visits = ", ".join(r[1] for r in c.execute("PRAGMA table_info(visits)") if r[1] not in ("id", "task_id"))
            for k in range(n):
                made = f"bulk-{n}-{k}"
                c.execute(
                    f"INSERT INTO tasks (id, number, title, {columns}) "
                    f"SELECT ?, (SELECT MAX(number) + 1 FROM tasks), ?, {columns} FROM tasks WHERE id=?",
                    (made, f"Bulk {k}", base),
                )
                c.execute("INSERT OR REPLACE INTO task_move_guards (id, open, task_id) VALUES (1, 1, ?)", (made,))
                c.execute("UPDATE tasks SET pipeline_id='ops', stage_id='triage' WHERE id=?", (made,))
                c.execute(
                    f"INSERT INTO visits (id, task_id, {visits}) SELECT ?, ?, {visits} FROM visits "
                    "WHERE task_id=? AND left_at IS NULL",
                    (f"visit-{made}", made, base),
                )
            c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")

    def statements():
        seen, original = [], store.read

        import contextlib

        @contextlib.contextmanager
        def traced():
            with original() as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        store.read = traced
        try:
            assert len(listed(api, "&limit=20", stage="triage")["tasks"]) == 20
        finally:
            del store.read
        return len(seen)

    grow(30)
    few = statements()
    grow(270)
    assert statements() <= few + 2
