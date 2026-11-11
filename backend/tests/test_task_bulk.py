"""A bulk change by filter (POST /api/v2/task-bulk): it selects with the task list's own filters, changes each task as
its single call would (the engine's moves, rights, refusals and events alike), is capped, and a dry run writes
nothing."""

from backend import hubdb as H
from backend.tests.helpers import engine as X
from backend.tests.helpers.tasks_board import api, bot_token, get, post  # noqa: F401

STAGES = (
    {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Legal review", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
)


def bulk(api, filter, action, token="ana-test", expected=200, **change):
    return post(api, "task-bulk", {"filter": filter, "action": action, **change}, token=token, expected=expected)


def task(api, title, token="ana-test", **fields):
    return post(api, "tasks", {"owner": "cmo", "title": title, "body": "Please.", **fields}, token=token)


def label(api, t, *keys):
    return post(api, "tasks/" + t["id"], {"version": t["version"], "labels": list(keys)})


def show(api, t, token="ana-test"):
    return get(api, "tasks/" + t["id"], token=token)["task"]


def campaign(api):
    with api.app.state.store.transaction() as c:
        return X.pipeline(c, "campaign", "Campaign", STAGES)["id"]


def test_the_filter_selects_exactly_what_the_task_list_shows_and_each_task_moves_by_the_engine(api):
    pid = campaign(api)
    launch = [label(api, task(api, f"Draft launch post {i}", pipeline=pid), "launch") for i in range(3)]
    untagged = task(api, "Draft the footer", pipeline=pid)
    elsewhere = label(api, task(api, "Draft the partner post", owner="ops", pipeline=pid), "launch")
    chosen = {"label": "launch", "owner": "cmo"}
    listed = get(api, "tasks?label=launch&owner=cmo")["tasks"]
    assert {t["id"] for t in listed} == {t["id"] for t in launch}

    out = bulk(api, chosen, "move", to="Legal review", why="Copy is final")
    assert (out["matched"], out["changed"], out["unchanged"], out["skipped"]) == (3, 3, 0, 0)
    assert {t["id"] for t in out["tasks"]["changed"]} == {t["id"] for t in launch}
    for t in launch:
        assert show(api, t)["stage"]["name"] == "Legal review"
    for t in (untagged, elsewhere):
        assert show(api, t)["stage"]["name"] == "Draft"
    with api.app.state.store.read() as c:
        visits = c.execute(
            "SELECT by_id, how FROM visits WHERE left_at IS NOT NULL AND task_id IN (?,?,?)",
            tuple(t["id"] for t in launch),
        ).fetchall()
    assert sorted(tuple(v) for v in visits) == [("human:ana", "pushed_past")] * 3  # each its own move, by the caller

    # Run again, nothing is left to move; a filter that names nothing, or a person who is nobody, never widens.
    again = bulk(api, chosen, "move", to="Legal review", why="Copy is final")
    assert (again["changed"], again["unchanged"] + again["skipped"]) == (0, 3)
    assert bulk(api, {}, "close", expected=422)["error"]["code"] == "filter"
    assert bulk(api, {"lane": "company"}, "close", expected=422)["error"]["code"] == "filter"
    assert bulk(api, {"owner": "nobody-here"}, "close", expected=422)["error"]["code"] == "owner"
    assert show(api, untagged)["status"] == "open"


def test_tags_close_and_reopen_by_filter(api):
    tasks = [label(api, task(api, f"Draft the brief {i}"), "brief") for i in range(2)]
    out = bulk(api, {"label": "brief"}, "tags", add=["Q4"], remove=["brief"])
    assert out["changed"] == 2
    from backend.tests.helpers.openapi_v2 import conforms

    assert conforms(out, {"$ref": "#/components/schemas/TaskBulkResult"}, get(api, "openapi.json")) is None
    assert {tuple(show(api, t)["labels"]) for t in tasks} == {("q4",)}
    assert bulk(api, {"label": "q4"}, "close", why="Not needed")["changed"] == 2
    assert {show(api, t)["status"] for t in tasks} == {"closed"}
    # A close has its way back: the same filter on closed tasks opens them again.
    assert bulk(api, {"label": "q4", "status": "closed"}, "move", to="reopen", why="Needed after all")["changed"] == 2
    assert {show(api, t)["status"] for t in tasks} == {"open"}


def test_blank_filters_never_widen_to_every_task(api):
    t = task(api, "Draft the unrelated note")
    for field in ("label", "updated_since"):
        for blank in ("", " \t "):
            out = bulk(api, {field: blank}, "close", expected=422)
            assert out["error"]["code"] == "filter"
    now = show(api, t)
    assert (now["status"], now["version"]) == ("open", t["version"])
    assert bulk(api, {"ids": [t["id"]], "label": " \t "}, "close", why="Not needed")["changed"] == 1


def test_a_dry_run_counts_the_real_outcome_and_changes_nothing(api):
    mine = [label(api, task(api, f"Call the lead {i}", owner="priya", token="priya-test"), "calls") for i in range(2)]
    theirs = label(api, task(api, "Draft the call script"), "calls")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM visits").fetchone()[0]
        refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    out = bulk(api, {"label": "calls"}, "close", token="priya-test", why="Lost", dry_run=True)
    assert out["dry_run"] is True and (out["changed"], out["skipped"]) == (2, 1)
    assert [t["id"] for t in out["tasks"]["skipped"]] == [theirs["id"]]
    for t in mine + [theirs]:
        now = show(api, t)
        assert (now["status"], now["version"]) == ("open", t["version"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM visits").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals


def test_nobody_bulk_changes_a_task_they_could_not_change_one_by_one(api):
    mine = label(api, task(api, "Call the lead", owner="priya", token="priya-test"), "calls")
    theirs = label(api, task(api, "Draft the call script"), "calls")
    out = bulk(api, {"label": "calls"}, "close", token="priya-test", why="Lost")
    assert (out["changed"], out["skipped"]) == (1, 1)
    assert out["tasks"]["skipped"][0]["id"] == theirs["id"]
    assert show(api, mine)["status"] == "closed"
    assert show(api, theirs)["status"] == "open"
    # People only: a bot changes tasks one at a time.
    owned = task(api, "Ship the screen", owner="ops")
    bulk(api, {"owner": "ops"}, "close", token=bot_token(api, "ops"), expected=403)
    assert show(api, owned)["status"] == "open"


def test_a_move_the_engine_refuses_is_skipped_with_its_reason(api):
    pid = campaign(api)
    ready = label(api, task(api, "Draft the press note", pipeline=pid), "press")
    waits = label(api, task(api, "Draft the press list", pipeline=pid), "press")
    with api.app.state.store.transaction() as c:
        H.task_done(c, "bot:cmo", ready["id"], "Drafted.", H.task(c, ready["id"])["version"])
    # The press note moved on by itself when its owner said done; neither may go on now without its owner: no reason,
    # no push past. Each is skipped with the engine's own refusal.
    out = bulk(api, {"label": "press"}, "move", to="next")
    assert (out["changed"], out["skipped"]) == (0, 2)
    assert {t["code"] for t in out["tasks"]["skipped"]} == {"task_not_met"}
    assert (show(api, ready)["stage"]["name"], show(api, waits)["stage"]["name"]) == ("Legal review", "Draft")


def test_more_tasks_than_the_cap_are_refused_whole(api):
    with api.app.state.store.transaction() as c:
        for i in range(201):
            made = H.task_create(c, "human:ana", f"Note {i}", "x", "bot:cmo", lint=False)
            H.task_update(c, "human:ana", made["id"], labels=["bulk"])
    out = bulk(api, {"label": "bulk"}, "close", why="Too many", expected=422)
    assert out["error"]["code"] == "too_many"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='closed'").fetchone()[0] == 0


def test_a_private_task_someone_may_not_read_is_neither_changed_nor_named(api):
    hidden = label(api, task(api, "Salary review", private=True), "hr")
    own = label(api, task(api, "Ben's own note", owner="ben", token="ben-test"), "hr")
    for dry in (False, True):
        out = bulk(api, {"label": "hr"}, "close", token="ben-test", why="Done", dry_run=dry)
        assert out["matched"] == 1 and hidden["id"] not in str(out) and "Salary" not in str(out)
    assert show(api, hidden)["status"] == "open"
    assert show(api, own)["status"] == "closed"
