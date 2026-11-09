"""A bulk change by filter (POST /api/v2/task-bulk): it selects with the task list's own filters, changes each task as
its single update would (permissions, refusals and events alike), is capped, and a dry run writes nothing."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import bot_settings as BS
from backend import hubdb as H
from backend import pipeline_review
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode
from backend.tests.helpers.tasks_board import api, bot_token, get, pipeline, post  # noqa: F401


def bulk(api, filter, action, token="ana-test", expected=200, **change):
    return post(api, "task-bulk", {"filter": filter, "action": action, **change}, token=token, expected=expected)


def task(api, title, token="ana-test", **fields):
    return post(api, "tasks", {"owner": "cmo", "title": title, "body": "Please.", **fields}, token=token)


def label(api, t, *keys):
    return post(api, "tasks/" + t["id"], {"version": t["version"], "labels": list(keys)})


def events(api, field):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT subject_id AS task_id, actor_id AS actor, new FROM history_events WHERE subject_type='task' AND kind=?",
                (field,),
            )
        ]


def test_the_filter_selects_exactly_what_the_task_list_shows(api):
    typ = pipeline(api)
    launch = [label(api, task(api, f"Draft launch post {i}", type=typ["id"]), "launch") for i in range(3)]
    other_step = label(api, task(api, "Review the launch copy", type=typ["id"], step="Copy review"), "launch")
    untagged = task(api, "Draft the footer", type=typ["id"])
    general = label(api, task(api, "Draft the general note"), "launch")
    elsewhere = label(api, task(api, "Draft the partner post", owner="ops", type=typ["id"]), "launch")
    chosen = {"type": typ["id"], "step": "Draft", "label": "launch", "owner": "cmo"}
    listed = get(api, f"tasks?type={typ['id']}&step=Draft&label=launch&owner=cmo")["tasks"]
    assert {t["id"] for t in listed} == {t["id"] for t in launch}

    out = bulk(api, chosen, "step", step="Legal review")
    assert (out["matched"], out["changed"], out["unchanged"], out["skipped"]) == (3, 3, 0, 0)
    assert {t["id"] for t in out["tasks"]["changed"]} == {t["id"] for t in launch}
    for t in launch:
        assert get(api, "tasks/" + t["id"])["task"]["step"]["name"] == "Legal review"
    for t in (other_step, untagged, general, elsewhere):
        assert get(api, "tasks/" + t["id"])["task"]["step_id"] == t["step_id"]
    # Each task's move is its own event, made by the caller.
    legal = next(s["id"] for s in typ["steps"] if s["name"] == "Legal review")
    moved = [e for e in events(api, "step") if e["new"] == legal]
    assert len(moved) == 3 and {e["actor"] for e in moved} == {"human:ana"}

    # Run again, nothing is left to move; a filter that names nothing, or a person who is nobody, never widens.
    again = bulk(api, {**chosen, "step": "Legal review"}, "step", step="Legal review")
    assert (again["changed"], again["unchanged"]) == (0, 3)
    assert bulk(api, {}, "close", expected=422)["error"]["code"] == "filter"
    assert bulk(api, {"lane": "company"}, "close", expected=422)["error"]["code"] == "filter"
    assert bulk(api, {"owner": "nobody-here"}, "close", expected=422)["error"]["code"] == "owner"
    assert get(api, "tasks/" + general["id"])["task"]["status"] == "open"


def test_tags_close_and_reopen_by_filter(api):
    tasks = [label(api, task(api, f"Draft the brief {i}"), "brief") for i in range(2)]
    out = bulk(api, {"label": "brief"}, "tags", add=["Q4"], remove=["brief"])
    assert out["changed"] == 2
    from backend.tests.helpers.openapi_v2 import conforms

    assert conforms(out, {"$ref": "#/components/schemas/TaskBulkResult"}, get(api, "openapi.json")) is None
    assert {tuple(get(api, "tasks/" + t["id"])["task"]["labels"]) for t in tasks} == {("q4",)}
    assert bulk(api, {"label": "q4"}, "close", note="Not needed")["changed"] == 2
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in tasks} == {"closed"}
    # A close has its way back: the same filter on closed tasks opens them again.
    assert bulk(api, {"label": "q4", "status": "closed"}, "status", status="open")["changed"] == 2
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in tasks} == {"open"}


def test_blank_filters_never_widen_to_every_task(api):
    t = task(api, "Draft the unrelated note")
    for field in ("label", "updated_since"):
        for blank in ("", " \t "):
            out = bulk(api, {field: blank}, "close", expected=422)
            assert out["error"]["code"] == "filter"
    now = get(api, "tasks/" + t["id"])["task"]
    assert (now["status"], now["version"]) == ("open", t["version"])
    assert bulk(api, {"ids": [t["id"]], "label": " \t "}, "close")["changed"] == 1


def test_bulk_replay_rechecks_revoked_bot_read_access(api):
    t = task(api, "Draft the confidential brief", private=False)
    body = {"filter": {"ids": [t["id"]]}, "action": "close"}
    headers = {"Authorization": "Bearer priya-test", "Idempotency-Key": "bulk-replay"}
    original = api.post("/api/v2/task-bulk", json=body, headers=headers)
    assert original.status_code == 200, original.text
    assert original.json()["tasks"]["skipped"][0]["id"] == t["id"]
    assert api.post("/api/v2/task-bulk", json=body, headers=headers).json() == original.json()

    access = get(api, "bots/cmo/access")
    revoked = api.put(
        "/api/v2/bots/cmo/access",
        json={
            "revision": access["revision"],
            "see": {"everyone": True},
            "read": {"people": ["ana"]},
            "write": {"everyone": True},
        },
        headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "revoke-read"},
    )
    assert revoked.status_code == 200, revoked.text
    get(api, "tasks/" + t["id"], token="priya-test", expected=403)
    replay = api.post("/api/v2/task-bulk", json=body, headers=headers)
    assert replay.status_code == 403, replay.text
    assert t["title"] not in replay.text


def test_a_dry_run_counts_the_real_outcome_and_changes_nothing(api):
    mine = [label(api, task(api, f"Call the lead {i}", owner="priya"), "calls") for i in range(2)]
    theirs = label(api, task(api, "Draft the call script"), "calls")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM history_events WHERE subject_type='task'").fetchone()[0]
        refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    out = bulk(api, {"label": "calls"}, "status", token="priya-test", status="done", dry_run=True)
    assert out["dry_run"] is True and (out["changed"], out["skipped"]) == (2, 1)
    assert [t["id"] for t in out["tasks"]["skipped"]] == [theirs["id"]]
    for t in mine + [theirs]:
        now = get(api, "tasks/" + t["id"])["task"]
        assert (now["status"], now["version"]) == ("open", t["version"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM history_events WHERE subject_type='task'").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals


def test_nobody_bulk_changes_a_task_they_could_not_change_one_by_one(api):
    mine = label(api, task(api, "Call the lead", owner="priya"), "calls")
    theirs = label(api, task(api, "Draft the call script"), "calls")
    # Priya moves no one else's task one at a time, so not in bulk either; her own still changes.
    post(
        api, "tasks/" + theirs["id"], {"version": theirs["version"], "status": "done"}, token="priya-test", expected=403
    )
    out = bulk(api, {"label": "calls"}, "status", token="priya-test", status="done")
    assert (out["changed"], out["skipped"]) == (1, 1)
    assert out["tasks"]["skipped"][0]["id"] == theirs["id"] and out["tasks"]["skipped"][0]["code"] == "identity"
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "done"
    assert get(api, "tasks/" + theirs["id"])["task"]["status"] == "open"

    # A bot is held to its own rights too: it closes nothing it did not ask for, and each refusal is on record.
    token = bot_token(api, "ops")
    owned = task(api, "Ship the screen", owner="ops")
    out = bulk(api, {"owner": "ops"}, "close", token=token)
    assert out["changed"] == 0 and owned["id"] in {t["id"] for t in out["tasks"]["skipped"]}
    assert get(api, "tasks/" + owned["id"])["task"]["status"] == "open"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM refusals WHERE actor='bot:ops'").fetchone()[0] >= 1


def test_a_bulk_done_is_held_to_a_waiting_approval_like_a_single_done(api):
    """Finding B1, the bulk route: a bulk status done is the single update each task gets, so a waiting approval on the
    task's stage refuses it, and it goes through once the person has approved."""
    # Booted Engineering's shape: its finish stage is closed, so a status done resolves to no step of its own.
    typ = post(
        api,
        "task-types",
        {"name": "Build", "steps": [{"name": "Draft", "status": "open"}, {"name": "Done", "status": "closed"}]},
        token="ana-test",
    )["type"]
    draft = next(s["id"] for s in typ["steps"] if s["name"] == "Draft")
    t = task(api, "Ship the campaign", type=typ["id"], step=draft)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, reviewer_id, "
            "version, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "req-campaign-signoff",
                typ["id"],
                draft,
                "Ana signs off",
                "approval",
                "{}",
                "person",
                "human:ana",
                1,
                "human:ana",
                "2026-11-07T12:00:00.000000Z",
                "2026-11-07T12:00:00.000000Z",
            ),
        )
        c.execute(
            "INSERT INTO stage_requirement_versions (stage_requirement_id, version, definition, changed_by, created_at) "
            "VALUES (?,?,?,?,?)",
            ("req-campaign-signoff", 1, "{}", "human:ana", "2026-11-07T12:00:00.000000Z"),
        )
    out = bulk(api, {"ids": [t["id"]]}, "status", status="done")
    assert (out["changed"], out["skipped"]) == (0, 1)
    assert get(api, "tasks/" + t["id"])["task"]["status"] == "open"
    with api.app.state.store.transaction() as c:
        pipeline_review.approve(c, "human:ana", t["id"], "req-campaign-signoff", requirement_version=1)
    out = bulk(api, {"ids": [t["id"]]}, "status", status="done")
    assert (out["changed"], out["skipped"]) == (1, 0)
    assert get(api, "tasks/" + t["id"])["task"]["status"] == "done"


def test_more_tasks_than_the_cap_are_refused_whole(api):
    with api.app.state.store.transaction() as c:
        for i in range(201):
            H.task_create(
                c, "human:ana", f"Review the account {i}", "Please.", "bot:cmo", lint=False, deduplicate=False
            )
    out = bulk(api, {"owner": "cmo"}, "status", status="doing", expected=422)
    assert out["error"]["code"] == "too_many" and "200" in out["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE status='doing'").fetchone()[0] == 0


def test_the_cli_and_the_mcp_tool_send_the_bulk_change_the_route_takes(api):
    from clients import hubcli, remotecli
    from backend.tests.helpers.mcp import call

    sent = []

    class Client:
        def get(self, path, **query):
            return {"actor": "human:ana"}

        def post(self, path, body, key=None):
            sent.append((path, body))
            return {}

    args = hubcli.parser().parse_args(
        [
            "task",
            "bulk",
            "step",
            "Legal review",
            "--where-type",
            "Campaign",
            "--where-step",
            "Draft",
            "--where-owner",
            "me",
            "--where-status",
            "open",
            "--where-status",
            "doing",
            "--dry-run",
        ]
    )
    remotecli.via_tool(Client(), args)
    assert sent == [
        (
            "task-bulk",
            {
                "filter": {"type": "Campaign", "step": "Draft", "owner": "human:ana", "status": "open,doing"},
                "action": "step",
                "step": "Legal review",
                "dry_run": True,
            },
        )
    ]

    tasks = [label(api, task(api, f"Draft the brief {i}"), "brief") for i in range(2)]
    err, out = call(api, "hub_task_bulk", {"filter": {"label": "brief", "owner": "cmo"}, "action": "close"})
    assert not err and out["changed"] == 2
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in tasks} == {"closed"}


def test_one_bulk_call_counts_once_toward_a_bots_quarantine(api):
    # Rule 8 quarantines a bot at QUARANTINE_AT repeated refusals in a day. A bot's honest bulk close over 50 tasks it
    # may not close is one refused try: each task's refusal stays on record, and the bot stays active.
    token = bot_token(api, "ops")
    with api.app.state.store.transaction() as c:
        owned = [
            H.task_create(
                c, "human:ana", f"Review the account {i}", "Please.", "bot:ops", lint=False, deduplicate=False
            )
            for i in range(50)
        ]

    def refusals():
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM refusals WHERE actor='bot:ops'").fetchone()[0]

    def state():
        with api.app.state.store.read() as c:
            return H.bot(c, "ops")["state"]

    # A dry run still adds none.
    out = bulk(api, {"owner": "ops"}, "close", token=token, dry_run=True)
    assert (out["changed"], out["skipped"]) == (0, 50) and refusals() == 0
    out = bulk(api, {"owner": "ops"}, "close", token=token)
    assert (out["changed"], out["skipped"]) == (0, 50)
    assert refusals() == 50 and state() == "active"

    # It counts as the same refused close made one by one: nine more single tries are its nine repeats, and the
    # tenth holds the originating chat, as ten single tries in a row would have. The chat's token names the source,
    # even when a write targets another task.
    for i in range(H.QUARANTINE_AT):
        assert state() == "active", i
        row = get(api, "tasks/" + owned[0]["id"])["task"]
        post(api, "tasks/" + row["id"], {"version": row["version"], "close": True}, token=token, expected=403)
    assert state() == "active"
    # What the bot is told when it is back quotes the refused words, never the stored refusal or its call.
    with api.app.state.store.read() as c:
        hold = c.execute(
            "SELECT task_id,conversation_id,refusal FROM quarantine_holds WHERE released_at IS NULL"
        ).fetchone()
        assert hold["task_id"] is None and hold["conversation_id"]
        stopped = H._json(hold["refusal"], {})
        words = H.refusals_for(c, actor="bot:ops")[-1]["detail"]
    assert stopped == {"rule": "close", "detail": words}


def test_repeated_bulk_calls_count_every_source_even_when_each_call_counts_once(api):
    from backend.tests.helpers.agents import credential, hermes_bot

    hermes_bot(api)
    token = credential(api)["token"]
    tasks = [task(api, title, owner="scout") for title in ("Check the export", "Check the archive")]
    for i in range(H.QUARANTINE_AT + 1):
        out = bulk(api, {"ids": [row["id"] for row in tasks]}, "close", token=token)
        assert (out["changed"], out["skipped"]) == (0, 2)
        with api.app.state.store.read() as c:
            assert H.resolve_bot(c, "scout")["state"] == ("quarantined" if i == H.QUARANTINE_AT else "active")
            assert c.execute("SELECT count(*) FROM quarantine_holds WHERE released_at IS NULL").fetchone()[0] == 0


def test_a_non_member_bulk_change_touches_no_ticket_of_a_members_only_pipeline(api):
    # Legal is members-only (owned by Ana): Ben may not open its tickets, so a bulk close by filter or by id leaves them;
    # a ticket Ben owns in it is still his to close.
    legal = pipeline(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE task_types SET pipeline_visibility='members', pipeline_owner_id='human:ana' WHERE id=?",
            (legal["id"],),
        )
    hidden = [task(api, f"Legal memo {i}", owner="cmo", type=legal["id"]) for i in range(2)]
    own = task(api, "Ben's own legal note", owner="ben", type=legal["id"])

    by_filter = bulk(api, {"owner": "cmo"}, "close", token="ben-test")
    assert by_filter["changed"] == 0 and by_filter["matched"] == 0
    by_ids = bulk(api, {"ids": [t["id"] for t in hidden]}, "close", token="ben-test")
    assert by_ids["changed"] == 0 and by_ids["matched"] == 0
    # Not named anywhere in the answer, a dry run included.
    dry = bulk(api, {"owner": "cmo"}, "close", token="ben-test")
    dry_run = bulk(api, {"ids": [t["id"] for t in hidden]}, "close", token="ben-test", dry_run=True)
    for answer in (by_filter, by_ids, dry, dry_run):
        assert not any(t["id"] in str(answer) for t in hidden)
    for t in hidden:
        assert get(api, "tasks/" + t["id"], "ana-test")["task"]["status"] == "open"
    mine = bulk(api, {"owner": "ben"}, "close", token="ben-test")
    assert mine["changed"] == 1
    assert get(api, "tasks/" + own["id"], "ana-test")["task"]["status"] == "closed"


def test_everything_not_mine_closes_by_excluding_the_callers_owner(api):
    """`exclude_owner` is "everything not mine": the caller's own tasks stay, while other people's and bots' tasks in
    the same filter close. A dry run counts the same set and changes nothing; an owner who is nobody is refused, since
    an exclusion that names nobody would otherwise read as every task."""
    mine = [label(api, task(api, f"Ana's note {i}", owner="human:ana"), "sweep") for i in range(2)]
    theirs = [
        label(api, task(api, "Draft the cmo brief", owner="cmo"), "sweep"),
        label(api, task(api, "Ship the ops runbook", owner="ops"), "sweep"),
        label(api, task(api, "Ben's memo", owner="human:ben"), "sweep"),
    ]
    chosen = {"label": "sweep", "exclude_owner": "human:ana", "status": "open,doing,waiting"}

    dry = bulk(api, chosen, "close", dry_run=True)
    assert (dry["matched"], dry["changed"], dry["skipped"]) == (3, 3, 0)
    assert {t["id"] for t in dry["tasks"]["changed"]} == {t["id"] for t in theirs}
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in mine + theirs} == {"open"}

    out = bulk(api, chosen, "close", note="Clearing the sweep")
    assert (out["changed"], out["skipped"]) == (3, 0)
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in theirs} == {"closed"}
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in mine} == {"open"}
    assert bulk(api, chosen, "close")["matched"] == 0

    # Not owned by me is a filter of its own: with nothing else named it selects, and my tasks are never in it.
    alone = bulk(api, {"exclude_owner": "human:ana"}, "close", dry_run=True)
    assert alone["matched"] >= 3 and not {t["id"] for t in mine} & {t["id"] for t in alone["tasks"]["changed"]}
    # An owner who is nobody is refused, never read as "exclude nobody": that would be every task.
    refused = bulk(api, {"label": "sweep", "exclude_owner": "nobody-here"}, "close", expected=422)
    assert refused["error"]["code"] == "exclude_owner"
    assert get(api, "tasks/" + mine[0]["id"])["task"]["status"] == "open"


def test_the_cli_and_the_mcp_tool_leave_out_my_tasks_with_not_owner(api):
    """The Assistant's `--where-not-owner me` is the route's exclude_owner, with `me` resolved as the caller."""
    from clients import hubcli, remotecli
    from backend.tests.helpers.mcp import call

    sent = []

    class Client:
        def get(self, path, **query):
            return {"actor": "human:ana"}

        def post(self, path, body, key=None):
            sent.append((path, body))
            return {}

    args = hubcli.parser().parse_args(
        [
            "task",
            "bulk",
            "close",
            "--where-not-owner",
            "me",
            "--where-status",
            "open",
            "--where-status",
            "doing",
            "--dry-run",
        ]
    )
    remotecli.via_tool(Client(), args)
    assert sent == [
        (
            "task-bulk",
            {
                "filter": {"exclude_owner": "human:ana", "status": "open,doing"},
                "action": "close",
                "dry_run": True,
            },
        )
    ]

    mine = label(api, task(api, "Ana's kept note", owner="human:ana"), "kept")
    theirs = label(api, task(api, "Draft the kept note", owner="cmo"), "kept")
    err, out = call(api, "hub_task_bulk", {"filter": {"label": "kept", "exclude_owner": "me"}, "action": "close"})
    assert not err and out["changed"] == 1
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "open"
    assert get(api, "tasks/" + theirs["id"])["task"]["status"] == "closed"

    # Two owners to leave out name one filter: the pair is refused, not an explicit exclude_owner overwritten by the alias.
    from clients import hubtools

    with pytest.raises(ValueError, match="same filter"):
        hubtools.task_bulk(
            None, {"filter": {"exclude_owner": "human:ana", "not_owner": "human:priya"}, "action": "close"}
        )


def test_a_bulk_close_never_takes_a_subtask_the_change_leaves_out(api):
    """Closing a parent closes its open subtasks as well, so a bulk close must not take one the filter leaves out: the
    caller's own (excluded by exclude_owner), or a private one the caller cannot read. Such a parent is skipped with its
    reason and its subtasks stay open. A parent whose whole tree is in the change closes with all of it, and the counts
    name every task that closed, the subtasks it took with it included."""
    chosen = {"label": "subtree", "exclude_owner": "human:ana", "status": "open,doing,waiting"}
    left = task(api, "Launch the spring campaign", owner="cmo", labels=["subtree"], token="priya-test")
    mine = task(
        api,
        "Ana's launch checklist",
        owner="human:ana",
        labels=["subtree"],
        relations=[{"task": left["id"], "kind": "parent"}],
    )
    secret = task(
        api,
        "Priya's contract note",
        owner="human:priya",
        labels=["subtree"],
        private=True,
        token="priya-test",
        relations=[{"task": left["id"], "kind": "parent"}],
    )
    right = task(api, "Ship the ops runbook", owner="cmo", labels=["subtree"])
    kid = task(
        api, "Ops follow-up", owner="ops", labels=["subtree"], relations=[{"task": right["id"], "kind": "parent"}]
    )

    dry = bulk(api, chosen, "close", dry_run=True)
    assert (dry["matched"], dry["changed"], dry["skipped"]) == (3, 2, 1)
    assert {t["id"] for t in dry["tasks"]["changed"]} == {right["id"], kid["id"]}
    assert [(t["id"], t["code"]) for t in dry["tasks"]["skipped"]] == [(left["id"], "subtasks_left_out")]
    assert {get(api, "tasks/" + t["id"])["task"]["status"] for t in (left, mine, right, kid)} == {"open"}

    out = bulk(api, chosen, "close", note="Closing the ops runbook")
    assert (out["matched"], out["changed"], out["skipped"]) == (3, 2, 1)
    assert {t["id"] for t in out["tasks"]["changed"]} == {right["id"], kid["id"]}
    assert get(api, "tasks/" + left["id"])["task"]["status"] == "open"
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "open"
    assert get(api, "tasks/" + secret["id"], token="priya-test")["task"]["status"] == "open"
    assert get(api, "tasks/" + right["id"])["task"]["status"] == "closed"
    assert get(api, "tasks/" + kid["id"])["task"]["status"] == "closed"


def test_a_bulk_close_leaves_the_callers_own_subtask_open(api):
    """The parent cascade closes an open subtask the filter excluded: a bulk close of a bot's task must leave Ana's own
    subtask open, and skip the parent with its reason."""
    solo = task(api, "Plan the spring webinar", owner="cmo", labels=["solo"])
    mine = task(
        api,
        "Ana's webinar slides",
        owner="human:ana",
        labels=["solo"],
        relations=[{"task": solo["id"], "kind": "parent"}],
    )
    out = bulk(api, {"label": "solo", "exclude_owner": "human:ana", "status": "open"}, "close")
    assert (out["matched"], out["changed"], out["skipped"]) == (1, 0, 1)
    assert [(t["id"], t["code"]) for t in out["tasks"]["skipped"]] == [(solo["id"], "subtasks_left_out")]
    assert get(api, "tasks/" + solo["id"])["task"]["status"] == "open"
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "open"


@pytest.mark.parametrize(("action", "change"), [("status", {"status": "done"}), ("step", {"step": "general-done"})])
def test_a_bulk_finish_leaves_the_callers_own_subtask_open(api, action, change):
    """Done, or a move into a done step, retires open subtasks as a close does: the same guard skips the parent."""
    solo = task(api, "Plan the spring webinar", owner="cmo", labels=["solo"])
    mine = task(
        api,
        "Ana's webinar slides",
        owner="human:ana",
        labels=["solo"],
        relations=[{"task": solo["id"], "kind": "parent"}],
    )
    out = bulk(api, {"label": "solo", "exclude_owner": "human:ana", "status": "open"}, action, **change)
    assert [(t["id"], t["code"]) for t in out["tasks"]["skipped"]] == [(solo["id"], "subtasks_left_out")]
    assert get(api, "tasks/" + solo["id"])["task"]["status"] == "open"
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "open"


def test_a_selected_subtask_the_change_holds_keeps_its_parent_open(api):
    """A subtask the filter selects but the loop does not finish (here a waiting approval on its stage refuses its done)
    stays open. Its parent is skipped with subtasks_left_out: closing the parent would end the held subtask through the
    cascade."""
    typ = post(
        api,
        "task-types",
        {"name": "Build", "steps": [{"name": "Draft", "status": "open"}, {"name": "Done", "status": "closed"}]},
        token="ana-test",
    )["type"]
    draft = next(s["id"] for s in typ["steps"] if s["name"] == "Draft")
    parent = task(api, "Plan the spring webinar", labels=["pair"])
    kid = task(
        api,
        "Ship the campaign",
        type=typ["id"],
        step=draft,
        labels=["pair"],
        relations=[{"task": parent["id"], "kind": "parent"}],
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, reviewer_id, "
            "version, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "req-campaign-signoff",
                typ["id"],
                draft,
                "Ana signs off",
                "approval",
                "{}",
                "person",
                "human:ana",
                1,
                "human:ana",
                "2026-11-07T12:00:00.000000Z",
                "2026-11-07T12:00:00.000000Z",
            ),
        )
        c.execute(
            "INSERT INTO stage_requirement_versions (stage_requirement_id, version, definition, changed_by, created_at) "
            "VALUES (?,?,?,?,?)",
            ("req-campaign-signoff", 1, "{}", "human:ana", "2026-11-07T12:00:00.000000Z"),
        )
    out = bulk(api, {"label": "pair"}, "status", status="done")
    assert (out["changed"], out["skipped"]) == (0, 2)
    assert {t["id"] for t in out["tasks"]["skipped"] if t["code"] == "subtasks_left_out"} == {parent["id"]}
    assert get(api, "tasks/" + parent["id"])["task"]["status"] == "open"
    assert get(api, "tasks/" + kid["id"])["task"]["status"] == "open"


def test_a_bulk_move_between_two_done_steps_is_no_finish(api):
    """From one done step to another nothing is retired, so a left-out open subtask does not hold the parent back."""
    typ = post(
        api,
        "task-types",
        {
            "name": "Release",
            "steps": [
                {"name": "Draft", "status": "open"},
                {"name": "Shipped", "status": "done"},
                {"name": "Measured", "status": "done"},
            ],
        },
    )["type"]
    shipped = task(api, "Ship the spring release", type=typ["id"], step="Shipped", labels=["rel"])
    mine = task(
        api,
        "Ana's release notes",
        owner="human:ana",
        labels=["rel"],
        relations=[{"task": shipped["id"], "kind": "parent"}],
    )
    out = bulk(api, {"label": "rel", "exclude_owner": "human:ana", "type": typ["id"]}, "step", step="Measured")
    assert (out["changed"], out["skipped"]) == (1, 0), out
    assert get(api, "tasks/" + shipped["id"])["task"]["step"]["name"] == "Measured"
    assert get(api, "tasks/" + mine["id"])["task"]["status"] == "open"
