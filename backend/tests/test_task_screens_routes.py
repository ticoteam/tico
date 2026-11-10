"""The reads the cut-over's screens need (docs/pipelines-v3.md S7, S10, S12): a list's pipeline, stage, waiting-on and
pushed-past filters, a row's `pushed_past` and `turn.running`, Company › Factories, the manager rail's Log, a person's
preferences and a support report's answer."""

from backend.tests.helpers.api import assign, claim, expire, get, headers, post, ready, runner


def product(api, factory_id=None):
    """A pipeline of two work stages, Spec then Build, each left when its owner is done with it."""
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": "Product",
        "goal": "Specs that ship",
        "factory_id": factory_id,
        "stages": [{"id": "spec", "name": "Spec", **stage}, {"id": "build", "name": "Build", **stage}],
    }
    return post(api, "pipelines/product", record)["pipeline"]


def ids(api, query, token="ana-test"):
    return {t["id"] for t in get(api, "tasks?" + query, token=token)["tasks"]}


def test_a_list_keeps_a_pipelines_or_a_stages_tasks_and_those_pushed_past(api):
    product(api)
    spec = post(api, "tasks", {"title": "Saved carts", "body": "As discussed.", "owner": "ben", "pipeline": "product"})
    pushed = post(
        api, "tasks", {"title": "Guest checkout", "body": "As discussed.", "owner": "ben", "pipeline": "product"}
    )
    general = post(api, "tasks", {"title": "Book the barn", "body": "As discussed.", "owner": "ben"})
    moved = post(api, f"tasks/{pushed['id']}/move", {"to": "build", "why": "The spec is in the PR"})
    assert moved["stage"]["id"] == "build"
    assert ids(api, "pipeline=product") == {spec["id"], pushed["id"]}
    assert ids(api, "stage=build") == {pushed["id"]}
    assert ids(api, "pipeline=product&pushed_past=true") == {pushed["id"]}
    assert ids(api, "pipeline=product&pushed_past=false") == {spec["id"]}
    rows = {t["id"]: t for t in get(api, "tasks?brief=true")["tasks"]}
    assert rows[pushed["id"]]["pushed_past"] is True and rows[spec["id"]]["pushed_past"] is False
    assert rows[general["id"]]["pushed_past"] is False
    # Each is ben's to do, and none is cara's.
    assert {spec["id"], pushed["id"], general["id"]} <= ids(api, "waiting_on=ben")
    assert ids(api, "waiting_on=ben&pipeline=product") == {spec["id"], pushed["id"]}
    assert ids(api, "waiting_on=cara") == set()
    for bad in ("pipeline=nope", "stage=nope", "waiting_on=nobody-here"):
        r = api.get("/api/v2/tasks?" + bad, headers=headers())
        assert r.status_code == 422, (bad, r.text)


def test_a_bots_turn_says_whether_its_run_is_going_now(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    made = post(api, "tasks", {"title": "Draft the release notes", "body": "For 0.6.", "owner": "ops"})

    def turn():
        return next(t for t in get(api, "tasks?brief=true")["tasks"] if t["id"] == made["id"])["turn"]

    assert turn()["state"] == "bot" and turn()["running"] is False
    attempt = claim(api, r, "ops")
    assert attempt, "the task's wake was claimed"
    assert turn()["state"] == "bot" and turn()["running"] is True
    expire(api, attempt["id"])
    assert turn()["running"] is False


def test_factories_count_done_in_the_period_and_mark_what_needs_a_person(api):
    made = post(api, "factories", {"name": "Product development", "goal": "Ship the roadmap"})["factory"]
    r = api.post("/api/v2/factories", json={"name": "product development"}, headers=headers())
    assert r.status_code == 409, r.text
    r = api.post("/api/v2/factories", json={"name": "Growth"}, headers=headers("cara-test"))
    assert r.status_code == 403, r.text
    product(api, made["id"])
    one = post(api, "tasks", {"title": "Saved carts", "body": "As discussed.", "owner": "ana", "pipeline": "product"})
    post(api, "tasks", {"title": "Order export", "body": "As discussed.", "owner": "ana", "pipeline": "product"})
    post(api, f"tasks/{one['id']}/move", {"to": "done", "why": "Shipped early"})
    closed = post(
        api, "tasks", {"title": "Gift receipts", "body": "As discussed.", "owner": "ana", "pipeline": "product"}
    )
    post(api, f"tasks/{closed['id']}/move", {"to": "closed", "why": "Not now"})
    seen = get(api, "factories?period=7d")
    factory = next(f for f in seen["factories"] if f["id"] == made["id"])
    assert factory == {
        "id": made["id"],
        "name": "Product development",
        "goal": "Ship the roadmap",
        "manager_id": None,
        "done": 1,
    }
    pipes = {p["id"]: p for p in seen["pipelines"]}
    assert pipes["product"]["factory_id"] == made["id"] and pipes["product"]["open"] == 1
    assert pipes["product"]["mark"] == "needs_person" and pipes["product"]["files_into"] == []
    assert pipes["general"]["factory_id"] is None
    r = api.get("/api/v2/factories?period=2d", headers=headers())
    assert r.status_code == 422, r.text


def test_the_manager_rails_log_names_each_push_past_by_task_title(api):
    product(api)
    pushed = post(
        api, "tasks", {"title": "Guest checkout", "body": "As discussed.", "owner": "ben", "pipeline": "product"}
    )
    post(api, f"tasks/{pushed['id']}/move", {"to": "build", "why": "The spec is in the PR"})
    seen = get(api, "pipelines/product/log")
    assert seen["learned"] == []
    [line] = seen["log"]
    assert line["task_id"] == pushed["id"] and line["why"] == "The spec is in the PR"
    assert "Guest checkout" in line["text"] and "past Spec" in line["text"]
    r = api.get("/api/v2/pipelines/nope/log", headers=headers())
    assert r.status_code == 404, r.text


def test_a_preference_is_the_callers_own(api):
    assert post(api, "preferences/compose.send_way", {"value": "queue"})["value"] == "queue"
    assert get(api, "preferences/compose.send_way")["value"] == "queue"
    assert get(api, "preferences/compose.send_way", token="ben-test")["value"] is None
    r = api.post("/api/v2/preferences/assistant.decides", json={"value": True}, headers=headers())
    assert r.status_code == 403, r.text


def test_the_release_schedule_reads_and_changes_and_release_now_answers_why_nothing_went(api):
    seen = get(api, "release-schedule")
    assert seen["on"] is True and seen["times"] == ["10:00"] and seen["pending"] == 0
    assert seen["can_change"] is True and seen["can_release"] is True and seen["next"]
    r = api.patch("/api/v2/release-schedule", json={"times": ["09:00", "15:30"]}, headers=headers())
    assert r.status_code == 200 and r.json()["times"] == ["09:00", "15:30"], r.text
    r = api.patch("/api/v2/release-schedule", json={"times": ["25:00"]}, headers=headers())
    assert r.status_code == 422, r.text
    r = api.patch("/api/v2/release-schedule", json={"on": False}, headers=headers("cara-test"))
    assert r.status_code == 403, r.text
    answer = post(api, "release/now", {"note": "Hotfix for the login loop"})
    assert answer["sent"] is False and answer["reason"] in ("no_release_manager", "nothing_pending")


def test_healths_never_stuck_check_gives_its_numbers_as_fields(api):
    from backend import health

    post(api, "tasks", {"title": "Book the barn", "body": "As discussed.", "owner": "ben"})
    health.record_never_stuck(api.app.state.store)
    check = next(c for c in get(api, "health")["checks"] if c["id"] == "never_stuck")
    assert set(check["counts"]) == {"waiting_on_nothing", "retry", "manager", "person", "cant_run", "broken"}
    assert all(isinstance(n, int) for n in check["counts"].values()) and check["at"]


def test_a_bots_page_says_why_it_cant_run_and_the_fix(api):
    r = runner(api)
    assign(api, r, "ops")
    assert get(api, "bots/ops")["cant_run"] is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id='ops'")
    seen = get(api, "bots/ops")["cant_run"]
    assert seen["fix"] == "resume" and seen["cause"].endswith("is paused")


def test_waiting_on_narrows_the_changes_since_a_cursor(api):
    cursor = get(api, "tasks")["cursor"]
    bens = post(api, "tasks", {"title": "Book the barn", "body": "As discussed.", "owner": "ben"})
    post(api, "tasks", {"title": "Order the cake", "body": "As discussed.", "owner": "cara"})
    seen = get(api, f"tasks?waiting_on=ben&changed_after={cursor}")
    assert [t["id"] for t in seen["tasks"]] == [bens["id"]]


def test_a_pipelines_mark_counts_only_the_tasks_the_reader_may_read(api):
    product(api)
    hidden = post(
        api,
        "tasks",
        {"title": "Quiet acquisition", "body": "As discussed.", "owner": "ben", "pipeline": "product", "private": True},
    )
    assert api.get("/api/v2/tasks/" + hidden["id"], headers=headers("cara-test")).status_code == 404
    mine = {p["id"]: p for p in get(api, "factories")["pipelines"]}["product"]
    assert mine["open"] == 1 and mine["mark"] == "needs_person"
    theirs = {p["id"]: p for p in get(api, "factories", token="cara-test")["pipelines"]}["product"]
    assert theirs["open"] == 0 and theirs["mark"] is None
