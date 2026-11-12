"""The Threads list's Important and Recent views, and the pass that ranks threads 1 to 10 (backend/thread_rank.py)."""

from clients import judge as J

from backend import decide_groups as DG
from backend import thread_rank as TR
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import headers  # noqa: F401  (the fixture and its helpers)
from backend.tests.helpers.thread_api import new_thread, say, sidebar
from backend.tests.helpers.thread_tasks import make_task


class FakeDecide:
    """Answers `importance` at the level told (1-10), as the Gateway would; `fail` raises instead. A call about several
    threads (backend/decide_groups.py) answers each; `calls` has one entry per call, `states` each thread asked about."""

    def __init__(self, level=8):
        self.level, self.calls, self.states, self.fail = level, [], [], None

    def __call__(self, state, questions, label=None):
        parts = DG.split(state, questions)
        self.calls.append({"state": state, "label": label, "threads": len(parts) if parts else 1})
        self.states.extend([one for _, one, _ in parts] if parts else [state])
        if self.fail:
            raise self.fail
        answer = {"type": "score", "score": float(self.level - 1), "confidence": 0.8}
        keys = [key for _, _, mine in parts for key in mine.values()] if parts else list(questions)
        return {
            "model": J.GATEWAY_MODEL,
            "provider": J.GATEWAY_PROVIDER,
            "usage": {"input_tokens": 300},
            "ms": 90,
            "cost_usd": 0.00002,
            "answers": {key: dict(answer) for key in keys},
        }


def rank(api, thread_id, level):
    with api.app.state.store.transaction() as c:
        TR.save(c, thread_id, level, 0, H.now())


def ids(listed):
    return [t["id"] for t in listed["threads"]]


def test_important_orders_by_level_then_newest_and_recent_by_newest(api):
    old = new_thread(api, title="Pricing decision", members=("human:ben",), body="Need a call by Friday")
    new = new_thread(api, title="Lunch", members=("human:ben",), body="Tacos?")
    rank(api, old["id"], 9)
    rank(api, new["id"], 3)
    important = sidebar(api, token="ben-test", sort="important")
    assert ids(important)[:2] == [old["id"], new["id"]]
    assert {t["id"]: t["importance"] for t in important["threads"]}[old["id"]] == 9
    recent = sidebar(api, token="ben-test", sort="recent")
    assert ids(recent)[:2] == [new["id"], old["id"]]


def test_noise_level_leaves_important_but_stays_in_recent(api):
    thread = new_thread(api, title="Status ping", members=("human:ben",), body="All green")
    rank(api, thread["id"], 1)
    assert thread["id"] not in ids(sidebar(api, token="ben-test", sort="important"))
    assert thread["id"] in ids(sidebar(api, token="ben-test", sort="recent"))


def test_a_finished_task_thread_is_noise_by_rule(api):
    task = make_task(api, {"owner": "human:ben", "title": "Ben's review", "body": "Details."})
    say(api, task["conversation_id"], "On it", token="ben-test")
    assert task["conversation_id"] in ids(sidebar(api, token="ana-test", sort="important"))
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ben", task["id"], status="done")
    assert task["conversation_id"] not in ids(sidebar(api, token="ana-test", sort="important"))
    assert task["conversation_id"] in ids(sidebar(api, token="ana-test", sort="recent"))


def test_a_task_no_person_owns_or_asked_for_is_noise_by_rule(api):
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:ops", "Review PR #1", "Details.", "ops", lint=False)
        assert (
            c.execute(
                f"SELECT {TR.noise_sql('r')} FROM conversations r WHERE r.id=?", (made["conversation_id"],)
            ).fetchone()[0]
            == 1
        )
        person = H.task_create(c, "human:ana", "Ask Ben", "Details.", "ops", lint=False)
        assert (
            c.execute(
                f"SELECT {TR.noise_sql('r')} FROM conversations r WHERE r.id=?", (person["conversation_id"],)
            ).fetchone()[0]
            == 0
        )


def test_important_pages_read_on_through_every_level(api):
    made = [new_thread(api, title=f"T{n}", members=("human:ben",), body="hi") for n in range(5)]
    for n, thread in enumerate(made):
        rank(api, thread["id"], 2 + n % 3)
    seen, cursor = [], None
    while True:
        page = sidebar(api, token="ben-test", sort="important", limit=2, **({"cursor": cursor} if cursor else {}))
        seen += ids(page)
        cursor = page["next_cursor"]
        if not cursor:
            break
    mine = [i for i in seen if i in {t["id"] for t in made}]
    assert sorted(mine) == sorted(t["id"] for t in made) and len(mine) == len(set(mine))
    levels = [next(t for t in made if t["id"] == i) for i in mine]
    assert [2 + made.index(t) % 3 for t in levels] == sorted((2 + n % 3 for n in range(5)), reverse=True)


def test_an_unknown_sort_is_refused(api):
    response = api.get("/api/v2/threads", params={"sort": "loud"}, headers=headers("ben-test"))
    assert response.status_code == 422


def test_a_pass_ranks_new_threads_once_audits_each_call_and_waits_an_hour(api):
    store = api.app.state.store
    thread = new_thread(api, title="Launch", members=("human:ben",), body="Launch is Tuesday")
    model = FakeDecide(level=8)
    at = H.now()
    done = TR.run_pass(store, at=at, engine=model)
    assert done["ranked"] >= 1
    state = next(state for state in model.states if state["thread"]["title"] == "Launch")
    assert state["thread"]["messages"][-1] == {"from": "person", "text": "Launch is Tuesday"}
    with store.read() as c:
        assert c.execute("SELECT score FROM thread_rankings WHERE thread_id=?", (thread["id"],)).fetchone()[0] == 8
        audited = c.execute(
            "SELECT count(*) FROM events WHERE action='judge.call' AND target=?", (model.calls[0]["label"],)
        ).fetchone()[0]
    assert audited == len(model.calls)
    calls = len(model.calls)
    assert TR.run_pass(store, at=at, engine=model)["ranked"] == 0  # nothing new
    say(api, thread["id"], "Moved to Wednesday")
    assert TR.run_pass(store, at=H.shift(at, minutes=10), engine=model)["ranked"] == 0  # within the hour
    assert len(model.calls) == calls
    later = TR.run_pass(store, at=H.shift(at, minutes=61), engine=model)
    assert later["ranked"] == 1


def test_a_failed_call_keeps_the_rank_and_is_still_audited(api):
    store = api.app.state.store
    thread = new_thread(api, title="Budget", members=("human:ben",), body="Q4 numbers")
    model = FakeDecide()
    model.fail = J.JudgeError("unavailable", "down")
    done = TR.run_pass(store, engine=model)
    assert done["ranked"] == 0 and done["errors"] >= 1
    with store.read() as c:
        assert c.execute("SELECT score FROM thread_rankings WHERE thread_id=?", (thread["id"],)).fetchone()[0] is None
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE action='judge.call' AND detail LIKE '%unavailable%'"
            ).fetchone()[0]
            >= 1
        )
    row = next(t for t in sidebar(api, token="ben-test", sort="important")["threads"] if t["id"] == thread["id"])
    assert row["importance"] == TR.DEFAULT
    # A failed call waits the hour too, so a failing model is not asked again every pass.
    at, calls = H.now(), len(model.calls)
    model.fail = None
    TR.run_pass(store, at=H.shift(at, minutes=10), engine=model)
    assert len(model.calls) == calls
    assert TR.run_pass(store, at=H.shift(at, minutes=61), engine=model)["ranked"] >= 1


def test_a_level_that_moves_tells_the_lists(api):
    store = api.app.state.store
    thread = new_thread(api, title="Outage", members=("human:ben",), body="Site down")

    def ranked():
        with store.read() as c:
            return c.execute(
                "SELECT count(*) FROM changes WHERE topic='threads' AND kind='ranked' AND subject_id=?", (thread["id"],)
            ).fetchone()[0]

    with store.transaction() as c:
        TR.save(c, thread["id"], 10, 1, H.now())
    assert ranked() == 1
    with store.transaction() as c:
        TR.save(c, thread["id"], 10, 2, H.now())
    assert ranked() == 1, "the same level is no change"


def test_ten_threads_are_ranked_in_one_call_and_audited_as_one(api, monkeypatch):
    """AC-8 for thread ranking: ten small threads, one call, each thread its own level."""
    monkeypatch.setattr(TR, "GROUP", DG.MOST)
    store = api.app.state.store
    made = [new_thread(api, title=f"Topic {n}", members=("human:ben",), body=f"Note {n}") for n in range(10)]
    model = FakeDecide(level=7)
    done = TR.run_pass(store, engine=model)
    assert done["ranked"] >= 10 and len(model.calls) <= 2
    with store.read() as c:
        levels = [
            c.execute("SELECT score FROM thread_rankings WHERE thread_id=?", (t["id"],)).fetchone()[0] for t in made
        ]
        audited = c.execute(
            "SELECT count(*) FROM events WHERE action='judge.call' AND target=?", (model.calls[0]["label"],)
        )
        assert audited.fetchone()[0] == len(model.calls)
    assert levels == [7] * 10
