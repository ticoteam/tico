"""Company › Floor (GET /api/v2/company/floor, backend/company_floor.py): each factory's pipelines and each work stage's
open tasks by who holds them, how many are in the reader's Needs you, and the pipelines' in and out rates. Only what
the reader may read is counted, and a private pipeline they may not read is not there at all."""

from backend import hubdb as H
from backend.tests.helpers.api import get, headers, post


def pipeline(api, ident, factory_id, private=False, stages=("spec", "build"), manager=None):
    """A pipeline of work stages, each left when its owner is done with it."""
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": ident.title(),
        "goal": "Ship it",
        "factory_id": factory_id,
        "private": private,
        "manager_id": manager,
        "stages": [{"id": f"{ident}-{s}", "name": s.title(), **stage} for s in stages],
    }
    return post(api, f"pipelines/{ident}", record)["pipeline"]


def task(api, title, owner, pipe, token="ana-test", private=False):
    body = {"title": title, "body": "As discussed.", "owner": owner, "pipeline": pipe}
    return post(api, "tasks", {**body, "private": True} if private else body, token=token)


def floor(api, token="ana-test"):
    return get(api, "company/floor", token=token)["factories"]


def find(factories, ident):
    return next(p for f in factories for p in f["pipelines"] if p["id"] == ident)


def test_each_stage_counts_its_open_tasks_by_holder_and_what_waits_on_the_reader(api):
    made = post(api, "factories", {"name": "Product development"})["factory"]
    pipeline(api, "product", made["id"])
    for n in range(3):
        task(api, f"Saved carts {n}", "ben", "product")
    mine = task(api, "Order export", "ana", "product")
    moved = task(api, "Gift receipts", "ops", "product")
    post(api, f"tasks/{moved['id']}/move", {"to": "product-build", "why": "The spec is in the PR"})
    seen = floor(api)
    [factory] = [f for f in seen if f["id"] == made["id"]]
    assert factory["name"] == "Product development" and factory["manager_id"] is None
    [pipe] = factory["pipelines"]
    spec, build = pipe["stages"]
    assert (spec["name"], spec["count"], build["name"], build["count"]) == ("Spec", 4, "Build", 1)
    # The most loaded holder first; only Ana's own task is in her Needs you.
    assert spec["owners"] == [
        {"actor": "human:ben", "count": 3, "needs_you": 0},
        {"actor": "human:ana", "count": 1, "needs_you": 1},
    ]
    assert spec["needs_you"] == 1 and spec["more"] == {"owners": 0, "count": 0}
    assert build["owners"] == [{"actor": "bot:ops", "count": 1, "needs_you": 0}] and build["needs_you"] == 0
    # Ben's Needs you holds his three, not Ana's.
    assert find(floor(api, "ben-test"), "product")["stages"][0]["needs_you"] == 3
    # Done leaves the stage's count.
    post(api, f"tasks/{mine['id']}/move", {"to": "done", "why": "Shipped"})
    assert find(floor(api), "product")["stages"][0]["count"] == 3


def test_a_stage_lists_its_twelve_most_loaded_holders_and_sums_the_rest(api):
    made = post(api, "factories", {"name": "Support"})["factory"]
    pipeline(api, "help", made["id"], stages=("triage",))
    tids = [task(api, f"Question {n}", "ops", "help")["id"] for n in range(17)]
    # Fifteen holders: the first holds three tasks, the rest one each (the grouping reads owner_id as it stands).
    holders = ["bot:big"] * 3 + [f"bot:b{n:02d}" for n in range(14)]
    with api.app.state.store.transaction() as c:
        for tid, who in zip(tids, holders):
            c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (tid,))
            c.execute("UPDATE tasks SET owner_id=? WHERE id=?", (who, tid))
        c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
    [stage] = find(floor(api), "help")["stages"]
    assert stage["count"] == 17
    assert len(stage["owners"]) == 12 and stage["owners"][0] == {"actor": "bot:big", "count": 3, "needs_you": 0}
    assert [o["actor"] for o in stage["owners"][1:]] == [f"bot:b{n:02d}" for n in range(11)]
    assert stage["more"] == {"owners": 3, "count": 3}


def test_in_and_out_count_each_task_once_in_the_last_day_and_week(api):
    made = post(api, "factories", {"name": "Product development"})["factory"]
    pipeline(api, "product", made["id"])
    today = task(api, "Saved carts", "ben", "product")
    days_ago = task(api, "Order export", "ben", "product")
    long_ago = task(api, "Gift receipts", "ben", "product")
    shipped = task(api, "Guest checkout", "ben", "product")
    post(api, f"tasks/{shipped['id']}/move", {"to": "done", "why": "Shipped"})
    # Sent back and done again: still one task out.
    post(api, f"tasks/{shipped['id']}/move", {"to": "product-spec", "why": "One more fix"})
    post(api, f"tasks/{shipped['id']}/move", {"to": "done", "why": "Shipped again"})
    now = H.now()
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE visits SET entered_at=? WHERE task_id=?", (H.shift(now, days=-3), days_ago["id"]))
        c.execute("UPDATE visits SET entered_at=? WHERE task_id=?", (H.shift(now, days=-10), long_ago["id"]))
    pipe = find(floor(api), "product")
    assert pipe["in"] == {"day": 2, "week": 3} and pipe["out"] == {"day": 1, "week": 1}
    assert today["id"] and pipe["stages"][0]["count"] == 3


def test_a_private_pipeline_a_reader_cannot_read_is_not_there(api):
    open_one = post(api, "factories", {"name": "Product development"})["factory"]
    quiet = post(api, "factories", {"name": "Deals"})["factory"]
    pipeline(api, "product", open_one["id"])
    pipeline(api, "deals", quiet["id"], private=True, manager="human:ben")
    task(api, "Saved carts", "cara", "product")
    task(api, "Quiet acquisition", "ben", "deals", token="ben-test", private=True)
    # Ana is the company's person: every private pipeline adds her as a reader.
    assert find(floor(api), "deals")["stages"][0]["count"] == 1
    assert find(floor(api, "ben-test"), "deals")["stages"][0]["count"] == 1
    # Cara reads none of its tasks: the pipeline is not drawn, nor the factory it leaves empty.
    theirs = floor(api, "cara-test")
    assert [f["id"] for f in theirs] == [open_one["id"]]
    assert find(theirs, "product")["stages"][0]["count"] == 1


def test_retired_pipelines_and_empty_factories_are_left_out_unless_the_reader_manages_the_factory(api):
    kept = post(api, "factories", {"name": "Product development"})["factory"]
    empty = post(api, "factories", {"name": "Growth"})["factory"]
    pipeline(api, "product", kept["id"])
    pipeline(api, "ideas", kept["id"])
    r = api.post("/api/v2/pipelines/ideas/retire", json={}, headers=headers())
    assert r.status_code == 200, r.text
    seen = floor(api)
    assert [p["id"] for f in seen for p in f["pipelines"]] == ["product"]
    assert empty["id"] not in {f["id"] for f in seen}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE factories SET manager_id='human:ana' WHERE id=?", (empty["id"],))
    managed = next(f for f in floor(api) if f["id"] == empty["id"])
    assert managed["pipelines"] == [] and managed["manager_id"] == "human:ana"
    assert empty["id"] not in {f["id"] for f in floor(api, "cara-test")}


def test_the_floor_reads_in_the_same_number_of_queries_however_many_pipelines(api, monkeypatch):
    """Its own reads are a fixed handful, never one per factory, pipeline or stage. Needs you is read the one way every
    screen reads it, and the task access filter keeps its own caches: both are held still here."""
    from backend import company_floor

    made = post(api, "factories", {"name": "Operations"})["factory"]
    store, auth = api.app.state.store, api.app.state.auth
    who = auth.authenticate({"authorization": "Bearer ana-test"})
    monkeypatch.setattr(company_floor, "_needs_task_ids", lambda c, auth, who: [])

    def queries():
        with store.read() as c:
            company_floor.floor(c, auth, who)  # the access filter's caches, warmed
            seen: list[str] = []
            c.set_trace_callback(seen.append)
            try:
                company_floor.floor(c, auth, who)
            finally:
                c.set_trace_callback(None)
        return len(seen)

    pipeline(api, "ops0", made["id"])
    task(api, "Rotate keys 0", "ops", "ops0")
    one = queries()
    for n in range(1, 6):
        pipeline(api, f"ops{n}", made["id"], stages=("triage", "fix", "check"))
        task(api, f"Rotate keys {n}", "ops", f"ops{n}")
    assert queries() == one


def test_the_floor_answers_as_the_served_document_says(api):
    from backend.tests.helpers.openapi_v2 import conforms

    made = post(api, "factories", {"name": "Product development"})["factory"]
    pipeline(api, "product", made["id"])
    task(api, "Saved carts", "ana", "product")
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    [op] = [op for ops in document["paths"].values() for op in ops.values() if op["operationId"] == "getCompanyFloor"]
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    response = api.get("/api/v2/company/floor", headers=headers())
    assert response.status_code == 200 and conforms(response.json(), schema, document) is None, response.text[:300]
