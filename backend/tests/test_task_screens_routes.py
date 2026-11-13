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
    # By its name within the pipeline, as a move takes it: a bot that listed "Watching" by name counted none.
    assert ids(api, "pipeline=product&stage=Build") == ids(api, "pipeline=product&stage=build ") == {pushed["id"]}
    assert ids(api, "pipeline=product&pushed_past=true") == {pushed["id"]}
    assert ids(api, "pipeline=product&pushed_past=false") == {spec["id"]}
    rows = {t["id"]: t for t in get(api, "tasks?brief=true")["tasks"]}
    assert rows[pushed["id"]]["pushed_past"] is True and rows[spec["id"]]["pushed_past"] is False
    assert rows[general["id"]]["pushed_past"] is False
    # Each is ben's to do, and none is cara's.
    assert {spec["id"], pushed["id"], general["id"]} <= ids(api, "waiting_on=ben")
    assert ids(api, "waiting_on=ben&pipeline=product") == {spec["id"], pushed["id"]}
    assert ids(api, "waiting_on=cara") == set()
    for bad in ("pipeline=nope", "stage=nope", "stage=Build", "pipeline=product&stage=Ship", "waiting_on=nobody-here"):
        r = api.get("/api/v2/tasks?" + bad, headers=headers())
        assert r.status_code == 422, (bad, r.text)


def test_a_list_row_says_when_it_entered_its_stage_not_when_it_last_changed(api):
    """A board card's age is time in this stage (task #220): `stage_since` is when the task entered its stage, kept
    through edits, else when it was filed."""
    product(api)
    filed = post(api, "tasks", {"title": "Saved carts", "body": "As discussed.", "owner": "ben", "pipeline": "product"})
    moved = post(
        api, "tasks", {"title": "Guest checkout", "body": "As discussed.", "owner": "ben", "pipeline": "product"}
    )
    general = post(api, "tasks", {"title": "Book the barn", "body": "As discussed.", "owner": "ben"})
    post(api, f"tasks/{moved['id']}/move", {"to": "build", "why": "The spec is in the PR"})

    def rows():
        return {t["id"]: t for t in get(api, "tasks?brief=true")["tasks"]}

    before = rows()
    # Filed into its first stage: it entered that stage as it was filed, before the next task was.
    assert before[filed["id"]]["created_at"] <= before[filed["id"]]["stage_since"] < before[moved["id"]]["created_at"]
    assert before[general["id"]]["created_at"] <= before[general["id"]]["stage_since"]
    entered = before[moved["id"]]["stage_since"]
    assert entered > before[moved["id"]]["created_at"], "a moved task counts from the move"
    post(api, f"tasks/{moved['id']}/comments", {"text": "Checked the copy."})
    after = rows()[moved["id"]]
    assert after["stage_since"] == entered, "a comment is not a stage move"


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
    [line] = [x for x in seen["log"] if x["task_id"]]
    assert line["task_id"] == pushed["id"] and line["why"] == "The spec is in the PR"
    assert "Guest checkout" in line["text"] and "past Spec" in line["text"]
    assert [x["text"] for x in seen["log"] if not x["task_id"]] == ["Ana made the pipeline"]
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
    assert answer == {"sent": False, "reason": "no_release_manager", "task": None, "tasks": 0}


def _releasing(api):
    """A Delivery pipeline whose Ship stage waits for a release, and ops as the Release Owner."""
    stages = [
        {"id": "build", "name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {
            "id": "ship",
            "name": "Ship",
            "owner": "assignee",
            "requirements": [{"kind": "item", "source": "github", "condition": "in_release"}],
        },
    ]
    post(api, "pipelines/delivery", {"name": "Delivery", "stages": stages})
    patched = api.patch("/api/v2/release-schedule", json={"bot": "ops"}, headers=headers())
    assert patched.status_code == 200 and patched.json()["release_manager"] == "ops", patched.text


def test_release_now_files_a_task_that_names_no_waiting_work_and_answers_only_what_the_requester_reads(api):
    """Codex review 2, 4 and 9: the release task is company-visible, so it says who asked and their note, never which
    tasks wait or how many; the Release Owner finds them itself. What a requester is answered, and what the schedule
    shows them, counts only the tasks they may read: no total, no "and N more"."""
    import json

    _releasing(api)
    shown = post(api, "tasks", {"title": "Saved carts", "body": "x", "owner": "ben", "pipeline": "delivery"})
    hidden = post(
        api,
        "tasks",
        {"title": "Quiet acquisition", "body": "x", "owner": "ben", "pipeline": "delivery", "private": True},
    )
    for made in (shown, hidden):
        post(api, f"tasks/{made['id']}/done", {}, token="ben-test")
    assert api.get("/api/v2/tasks/" + hidden["id"], headers=headers("cara-test")).status_code == 404
    assert get(api, "release-schedule")["pending"] == 2
    assert get(api, "release-schedule", token="cara-test")["pending"] == 1
    answer = post(api, "release/now", {"note": "Before the weekend"})
    assert answer == {"sent": True, "reason": None, "task": answer["task"], "tasks": 2}
    filed = get(api, "tasks/" + answer["task"], token="cara-test")
    assert filed["task"]["owner_id"] == "bot:ops" and filed["task"]["title"] == "Release: requested by Ana"
    said = json.dumps(filed)
    for leak in (shown["id"], hidden["id"], "Saved carts", "Quiet acquisition", "2 tasks", "1 more"):
        assert leak not in said, leak
    again = post(api, "release/now", {}, token="cara-test")
    assert again == {"sent": False, "reason": "already_open", "task": answer["task"], "tasks": 1}


def test_release_now_is_filed_whether_or_not_anything_waits(api):
    """Codex review 2, 9: "nothing waits" would tell a reader that no hidden task waits either. The request is filed;
    the Release Owner closes it when nothing does."""
    _releasing(api)
    answer = post(api, "release/now", {}, token="cara-test")
    assert answer == {"sent": True, "reason": None, "task": answer["task"], "tasks": 0}


def test_release_now_names_an_open_release_task_only_to_who_may_read_it(api):
    """Codex review 2, 8: the Release Owner's open Release task is private to Ana. Cara's Release now is not
    filed again, and is not told that task's id."""
    import json

    _releasing(api)
    quiet = post(api, "tasks", {"title": "Release: the quiet fix", "body": "x", "owner": "ops", "private": True})
    assert api.get("/api/v2/tasks/" + quiet["id"], headers=headers("cara-test")).status_code == 404
    answer = post(api, "release/now", {"note": "Hotfix"}, token="cara-test")
    assert answer == {"sent": False, "reason": "already_open", "task": None, "tasks": 0}
    assert quiet["id"] not in json.dumps(answer)
    assert post(api, "release/now", {})["task"] == quiet["id"]


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


def test_only_the_screens_preferences_are_kept_and_each_is_small(api):
    for key in ("ui.rails", "org.history", "org.mine", "tasks.view", "tasks.pins", "pipeline.rail_hidden"):
        assert post(api, "preferences/" + key, {"value": {"at": 1}})["value"] == {"at": 1}
    for key in ("onboarding.progress", "librarian.market_setup", "changelog.read", "anything.else"):
        r = api.post(f"/api/v2/preferences/{key}", json={"value": {"skipped": 1}}, headers=headers())
        assert r.status_code == 422, (key, r.text)
        assert api.get(f"/api/v2/preferences/{key}", headers=headers()).status_code == 422
    r = api.post("/api/v2/preferences/org.history", json={"value": {"items": ["x" * 5000]}}, headers=headers())
    assert r.status_code == 422, r.text


def test_a_sub_task_the_reader_may_not_open_is_named_by_neither_title_nor_id(api):
    # A bot's task waits on what it filed (waiting_on.py): here, a sub-task private to ben.
    parent = post(api, "tasks", {"title": "Plan the launch", "body": "x", "owner": "ops"})
    child = post(
        api,
        "tasks",
        {"title": "Quiet acquisition", "body": "x", "owner": "ben", "parent": parent["id"], "private": True},
    )
    assert api.get("/api/v2/tasks/" + child["id"], headers=headers("cara-test")).status_code == 404
    mine = get(api, "tasks/" + parent["id"])["task"]
    assert mine["turn"]["state"] == "tasks" and mine["turn"]["who"] == child["id"]
    row = next(t for t in get(api, "tasks?brief=true", token="cara-test")["tasks"] if t["id"] == parent["id"])
    full = get(api, "tasks/" + parent["id"], token="cara-test")["task"]
    for turn in (row["turn"], full["turn"], *full["waits"]):
        assert turn["line"] == "A sub-task" and turn["who"] is None, turn
    for req in full["to_leave"]:
        assert req["who"] != child["id"] and "Quiet acquisition" not in (req["standing"] or ""), req


def test_a_sub_task_of_a_bot_the_reader_may_not_read_is_named_nowhere_on_its_parent(api):
    """Codex review 2, 7: the child is not private, but its owner, the inbox bot, is read only by Ana. Cara may not
    read it, so its parent's full read names it nowhere: not its title, not its id. (`filed` is core's, review 2, 6.)"""
    import json

    from backend.tests.helpers.api import restrict, unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, "inbox")
        restrict(c, "inbox", see={"everyone": True}, read={"people": ["ana"]}, write={"people": ["ana"]})
    parent = post(api, "tasks", {"title": "Plan the launch", "body": "x", "owner": "ops"})
    child = post(api, "tasks", {"title": "Sort the press list", "body": "x", "owner": "inbox", "parent": parent["id"]})
    assert api.get("/api/v2/tasks/" + child["id"], headers=headers("cara-test")).status_code in (403, 404)
    assert get(api, "tasks/" + parent["id"])["task"]["turn"]["who"] == child["id"]
    full = get(api, "tasks/" + parent["id"], token="cara-test")
    full["task"].pop("filed", None)
    said = json.dumps(full)
    assert child["id"] not in said and "Sort the press list" not in said, said
    assert full["task"]["turn"]["line"] == "A sub-task" and full["task"]["turn"]["who"] is None


def _never_stuck(api, token):
    return next(c for c in get(api, "health", token=token)["checks"] if c["id"] == "never_stuck")


def test_health_counts_tasks_without_naming_them_and_a_member_counts_only_what_they_read(api):
    import json

    from backend import health

    seen = post(api, "tasks", {"title": "Book the barn", "body": "x", "owner": "ops"})
    hidden = post(api, "tasks", {"title": "Quiet acquisition", "body": "x", "owner": "ops", "private": True})
    assert api.get("/api/v2/tasks/" + hidden["id"], headers=headers("cara-test")).status_code == 404
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE visits SET level='person' WHERE left_at IS NULL AND task_id IN (?,?)", (seen["id"], hidden["id"])
        )
    # A broken rule names its task and a bot: an Admin sees how many, never which.
    broke = post(api, "tasks", {"title": "Quiet payroll", "body": "x", "owner": "ops", "private": True})
    _wake_left_queued(api, broke["id"])
    reading = health.record_never_stuck(api.app.state.store)
    assert reading["person"] == 2 and reading["broken"] == 1
    admin = _never_stuck(api, "ben-test")
    assert admin["counts"]["person"] == 2 and admin["counts"]["broken"] == 1 and admin["status"] == "bad"
    for task in (hidden, broke):
        assert task["id"] not in json.dumps(admin) and "bot:ops" not in json.dumps(admin)
    member = _never_stuck(api, "cara-test")
    assert member["counts"]["person"] == 1 and member["counts"]["broken"] == 0
    assert hidden["id"] not in json.dumps(member) and broke["id"] not in json.dumps(member)


def test_an_admins_needs_you_plates_count_only_what_the_admin_may_read(api):
    for n in range(12):
        post(api, "tasks", {"title": f"Private errand {n}", "body": "x", "owner": "cara", "private": True})
    own = next((c for c in get(api, "health", token="cara-test")["checks"] if c["id"] == "needs_plate"), None)
    assert own and own["items"][0]["count"] >= 12
    admin = next((c for c in get(api, "health", token="ben-test")["checks"] if c["id"] == "needs_plate"), None)
    assert admin is None or all(item["person"] != "human:cara" for item in admin.get("items", []))


def test_the_screens_reads_are_in_the_served_document_and_answer_as_it_says(api):
    from backend.tests.helpers.openapi_v2 import conforms

    product(api)
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def call(operation, method, path, **kw):
        response = getattr(api, method)(path, headers=headers(), **kw)
        assert response.status_code == 200, (path, response.text)
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(response.json(), schema, document) is None, (operation, response.text[:300])

    call("getPreference", "get", "/api/v2/preferences/ui.rails")
    call("setPreference", "post", "/api/v2/preferences/ui.rails", json={"value": {"at": 1}})
    call("getReleaseSchedule", "get", "/api/v2/release-schedule")
    call("updateReleaseSchedule", "patch", "/api/v2/release-schedule", json={"on": True})
    call("releaseNow", "post", "/api/v2/release/now", json={})
    call("listFactories", "get", "/api/v2/factories")
    call("createFactory", "post", "/api/v2/factories", json={"name": "Growth"})
    call("getPipelineLog", "get", "/api/v2/pipelines/product/log")


def _wake_left_queued(api, tid):
    """Rule 7 broken for real (never_stuck._rule_7): the task is done, and a wake for it is queued again."""
    post(api, f"tasks/{tid}/done", {})
    with api.app.state.store.transaction() as c:
        found = c.execute(
            "UPDATE jobs SET state='queued' WHERE message_id IN (SELECT id FROM messages WHERE wake_key LIKE ?)",
            (tid + ":%",),
        ).rowcount
    assert found


def test_a_real_rule_break_counts_for_whoever_reads_its_task(api):
    """Codex review 2, 10: a rule-7 break the checker makes ("wake ... is queued for a task that is done") is tied to
    its task when the reading is made, so a member who reads that task sees it counted."""
    from backend import health, never_stuck

    seen = post(api, "tasks", {"title": "Book the barn", "body": "x", "owner": "ops"})
    _wake_left_queued(api, seen["id"])
    health.record_never_stuck(api.app.state.store)
    with api.app.state.store.read() as c:
        assert [line[:2] for line in never_stuck.rules_hold(c)] == ["7:"]
    member = _never_stuck(api, "cara-test")
    assert member["counts"]["broken"] == 1 and member["status"] == "bad"


def test_no_sql_reads_a_task_or_a_break_from_the_never_stuck_reading(api):
    """Codex review 2, 5: Ben, an Admin, may not read the private task the break is about. Guarded SQL over
    registry_metadata gives him the reading's numbers, never that task's id or the break's words."""
    import json

    from backend import health

    hidden = post(api, "tasks", {"title": "Quiet acquisition", "body": "x", "owner": "ops", "private": True})
    _wake_left_queued(api, hidden["id"])
    health.record_never_stuck(api.app.state.store)
    assert api.get("/api/v2/tasks/" + hidden["id"], headers=headers("ben-test")).status_code == 404
    rows = post(
        api, "sql", {"sql": "SELECT key, value FROM registry_metadata WHERE key LIKE 'health:%'"}, token="ben-test"
    )
    said = json.dumps(rows)
    assert health.NEVER_STUCK_KEY in said
    assert hidden["id"] not in said and "wake" not in said and "queued" not in said, said
    assert _never_stuck(api, "ben-test")["counts"]["broken"] == 1


def test_a_closed_sub_task_the_reader_may_not_open_says_neither_its_title_nor_why_it_was_closed(api):
    """Codex review 3: a stage that waits on what the task filed, and a private sub-task Cara may not open is closed
    with a reason. What the parent waits on is Cara's own turn now (she owns it), but its line came from the sub-task:
    the parent's read, its brief row, its waits and her Needs you say a sub-task was closed, never which or why."""
    import json

    stage = {"owner": "assignee", "requirements": [{"kind": "linked_done"}]}
    post(api, "pipelines/hiring", {"name": "Hiring", "stages": [{"id": "plan", "name": "Plan", **stage}]})
    parent = post(api, "tasks", {"title": "Plan the team", "body": "x", "owner": "cara", "pipeline": "hiring"})
    child = post(
        api,
        "tasks",
        {"title": "Quiet acquisition", "body": "x", "owner": "ben", "parent": parent["id"], "private": True},
    )
    assert api.get("/api/v2/tasks/" + child["id"], headers=headers("cara-test")).status_code == 404
    post(api, f"tasks/{child['id']}/move", {"to": "closed", "why": "Salary freeze"})
    mine = get(api, "tasks/" + parent["id"])["task"]  # Ana asked for the sub-task: she is told which, and why
    assert "Quiet acquisition" in json.dumps(mine["waits"]) and "Salary freeze" in json.dumps(mine["waits"])
    full = get(api, "tasks/" + parent["id"], token="cara-test")["task"]
    row = next(t for t in get(api, "tasks?brief=true", token="cara-test")["tasks"] if t["id"] == parent["id"])
    needs = [i for i in get(api, "needs-you", token="cara-test")["items"] if i.get("task_id") == parent["id"]]
    assert needs and full["waits"] and row["turn"]
    said = json.dumps([full, row, needs])
    assert "Quiet acquisition" not in said and "Salary freeze" not in said and child["id"] not in said, said
    assert "A sub-task was closed" in json.dumps([full["waits"], row["turn"], needs])


def test_a_private_task_goes_into_a_first_stage_that_asks_ben_only_with_ben_under_who_can_see(api):
    """Codex review 5, through the route: the readers a create names are set before the task enters its first stage,
    so the stage's sharing rule sees them."""
    ask = {"kind": "ok", "person_id": "human:ben", "line": "OK"}
    post(
        api,
        "pipelines/payroll",
        {"name": "Payroll", "stages": [{"id": "ok", "name": "Approve", "requirements": [ask]}]},
    )
    task = {"title": "Salary bands", "body": "x", "owner": "ops", "pipeline": "payroll", "private": True}
    r = api.post("/api/v2/tasks", json=task, headers=headers())
    assert r.status_code in (409, 422) and "task_not_shared" in r.text, r.text
    made = post(api, "tasks", {**task, "readers": ["ben"]})
    assert made["stage_id"] == "ok" and made["readers"] == ["human:ben"]
    assert get(api, "tasks/" + made["id"], token="ben-test")["task"]["id"] == made["id"]
