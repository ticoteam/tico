"""The task calls everything outside the engine uses (backend/task_calls.py, re-exported by hubdb), and what a task's
life looks like through them: made by `H.task_create`, worked with `H.task_done`, `task_move`, `task_close`,
`task_reassign`, `task_ask`, `task_answer`, `task_snooze`, edited with `H.task_update`, deleted and restored."""

import json

import pytest

from backend import task_delete
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN = "human:ana", "human:ben"
BUILDER, HELPER, MANAGER = "bot:builder", "bot:helper", "bot:manager"
BODY = "What to do and how to tell it is done."


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "helper", "manager"):
            X.bot(conn, slug)
        conn.execute("UPDATE pipelines SET manager_id=? WHERE id='general'", (MANAGER,))
        yield conn


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


def _new(c, by=ANA, owner=BUILDER, title="Write the launch note", **more):
    return H.task_create(c, by, title, BODY, owner, lint=False, **more)


def _version(c, task_id):
    return H.task(c, task_id)["version"]


def _events(c, task_id=None):
    """(action, target) of the audit rows, oldest first, for one task or all."""
    rows = c.execute("SELECT action, target FROM events ORDER BY rowid").fetchall()
    return [tuple(r) for r in rows if task_id is None or r[1] == task_id]


def _visits(c, task_id):
    return c.execute("SELECT COUNT(*), SUM(left_at IS NULL) FROM visits WHERE task_id=?", (task_id,)).fetchone()


# ----------------------------------------------------------------------------- create
def test_a_new_task_is_open_in_the_first_stage_of_general_with_one_visit_and_its_owner_bot_woken_once(c):
    row = _new(c, wake_refs={"automation_run": "run-1"})
    assert (row["status"], row["pipeline_id"], row["stage_id"]) == ("open", "general", "general-todo")
    assert (row["owner_id"], row["assignee_id"], row["requester_id"]) == (BUILDER, BUILDER, ANA)
    assert tuple(_visits(c, row["id"])) == (1, 1)
    wakes = X.wakes(c, row["id"])
    assert [w[1] for w in wakes] == [BUILDER]
    refs = json.loads(c.execute("SELECT refs FROM messages WHERE wake_key=?", (wakes[0][0],)).fetchone()[0])
    assert (refs["wake"], refs["task"], refs["news"], refs["automation_run"]) == (
        "task_turn",
        row["id"],
        "assignment",
        "run-1",
    )
    assert refs["version"] == row["version"]


def test_a_task_for_a_person_wakes_nobody_and_is_in_their_needs_you(c):
    from backend import waiting_on as W

    row = _new(c, by=BUILDER, owner=ANA, title="Pick the launch date")
    assert X.wakes(c, row["id"]) == []
    mine = [item for item in W.needs(c, ANA) if item["task_id"] == row["id"]]
    assert [item["kind"] for item in mine] == ["task"]


def test_a_sub_task_is_linked_both_ways_and_its_parent_waits_on_nothing_it_did_not_ask_for(c):
    parent = _new(c)
    child = _new(c, by=BUILDER, owner=HELPER, title="Gather the numbers", parent_id=parent["id"])
    link = c.execute("SELECT from_task_id, kind FROM links WHERE to_task_id=?", (child["id"],)).fetchone()
    assert tuple(link) == (parent["id"], "sub_task")
    assert H.TR.parent_of(c, child["id"]) == parent["id"]
    view = H.task_view(c, parent["id"], ANA)
    assert [f["id"] for f in view["filed"]] == [child["id"]]
    assert H.task_view(c, child["id"], ANA)["filed_by"] == parent["id"]


def test_a_refused_create_leaves_no_row_no_thread_and_no_wake(c):
    before = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "conversations", "messages")]
    with _refused("task_no_such_stage"):
        _new(c, pipeline="no-such-pipeline")
    chain = _new(c)
    for depth in range(E.MAX_DEPTH):
        chain = _new(c, by=BUILDER, owner=BUILDER, title=f"Level {depth}", parent_id=chain["id"])
    counted = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "conversations", "messages")]
    with _refused("task_too_deep"):
        _new(c, by=BUILDER, owner=BUILDER, title="One too deep", parent_id=chain["id"])
    after = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "conversations", "messages")]
    assert after == counted and counted[0] == before[0] + 1 + E.MAX_DEPTH


# ----------------------------------------------------------------------------- done, and going on anyway
def test_done_from_the_owner_finishes_the_task_and_what_a_move_causes_happens_once(c):
    row = _new(c, by=HELPER)
    X.settled(c)
    out = H.task_done(c, BUILDER, row["id"], "Wrote it and posted the link.", _version(c, row["id"]))
    assert (out["status"], out["stage_id"]) == ("done", "general-done")
    assert tuple(_visits(c, row["id"])) == (2, 1)
    assert ("task.update", row["id"]) in _events(c, row["id"])
    told = c.execute(
        "SELECT recipient_id, body FROM messages WHERE wake_key LIKE ? AND wake_key LIKE '%:result'",
        (row["id"] + ":%",),
    ).fetchall()
    assert [(t[0], t[1].splitlines()[0]) for t in told] == [(HELPER, "Finished: Write the launch note")]
    # The bot that asked for it reads it on its next run: nothing wakes a bot about a Done task (never-stuck rule 7).
    queued = "SELECT 1 FROM jobs j JOIN messages m ON m.id = j.message_id WHERE m.wake_key LIKE '%:result'"
    assert c.execute(queued).fetchall() == []
    assert H.task_done(c, BUILDER, row["id"], "Again.", _version(c, row["id"]))["status"] == "done"  # already done
    assert len(c.execute("SELECT 1 FROM messages WHERE wake_key LIKE '%:result'").fetchall()) == 1


def test_done_never_pushes_past_and_a_move_with_a_reason_does(c):
    row = _new(c)
    asked = H.task_ask(c, BUILDER, row["id"], ANA, "Ship it on Monday or Friday?", seen_version=_version(c, row["id"]))
    with _refused("identity"):
        H.task_done(c, HELPER, row["id"], "Looks finished to me.", _version(c, row["id"]))  # not on the task
    H.task_done(c, BUILDER, row["id"], "Drafted both.", _version(c, row["id"]))
    still = H.task(c, row["id"])
    assert (still["status"], still["stage_id"]) == ("open", "general-todo")  # waits on Ana's answer
    with _refused("task_not_met") as refusal:
        H.task_move(c, BUILDER, row["id"], H.TASK_NEXT, None, _version(c, row["id"]))
    assert refusal.value.extra["missing"] and refusal.value.extra["version"] == _version(c, row["id"])
    assert any("tico task move" in command for command in refusal.value.extra["next"])
    moved = H.task_move(c, BUILDER, row["id"], H.TASK_NEXT, "Ana is away; Friday is safe.", _version(c, row["id"]))
    assert moved["status"] == "done"
    assert asked["requirement_id"]


def test_a_person_says_a_bots_task_is_done_and_tico_finishing_one_needs_no_push(c):
    one = _new(c)
    assert H.task_done(c, BEN, one["id"], "Saw it live.", mover=True)["status"] == "done"
    two = _new(c, title="File the receipts")
    assert H.task_done(c, H.KEEPER, two["id"], "The bot was archived.")["status"] == "done"


# ----------------------------------------------------------------------------- ask and answer
def test_a_bot_asks_a_person_who_answers_and_the_bot_is_woken_with_the_answer(c):
    row = _new(c)
    X.settled(c)
    asked = H.task_ask(
        c,
        BUILDER,
        row["id"],
        ANA,
        "Ship it on Monday or Friday?",
        choices=["Monday", "Friday"],
        seen_version=_version(c, row["id"]),
    )
    rid = asked["requirement_id"]
    view = H.task_view(c, row["id"], ANA)
    ask = next(r for r in view["to_leave"] if r["requirement_id"] == rid)
    assert (ask["kind"], ask["met"], ask["who"], ask["asked_by"], ask["choices"]) == (
        "answer",
        False,
        ANA,
        BUILDER,
        ["Monday", "Friday"],
    )
    assert {"answer", "give", "thumbs"} <= set(ask["may"])
    assert (view["turn"]["state"], view["turn"]["who"]) == ("person", ANA)
    assert (
        "answer" not in next(r for r in H.task_view(c, row["id"], BEN)["to_leave"] if r["requirement_id"] == rid)["may"]
    )
    with _refused("task_requirement"):
        H.task_answer(c, BEN, row["id"], rid, "Monday")
    H.task_answer(c, ANA, row["id"], rid, "Friday")
    after = next(r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == rid)
    assert (after["met"], after["answer"]["value"], after["answer"]["by"]) == (True, "Friday", ANA)
    assert [w[1] for w in X.wakes(c, row["id"]) if ":answer:" in w[0]] == [BUILDER]


def test_a_bots_open_ask_counts_as_waiting_on_a_person_until_it_is_answered(c):
    row = _new(c)
    before = H.status_counts(c, BUILDER)
    asked = H.task_ask(c, BUILDER, row["id"], ANA, "Ship it on Monday or Friday?", seen_version=_version(c, row["id"]))
    assert H.status_counts(c, BUILDER) == (before[0], before[1] + 1)
    H.task_answer(c, ANA, row["id"], asked["requirement_id"], "Friday")
    assert H.status_counts(c, BUILDER) == before


def test_a_thumbs_down_that_lets_the_task_finish_is_the_persons_move(c):
    row = _new(c)
    asked = H.task_ask(c, BUILDER, row["id"], ANA, "Ship it on Monday or Friday?", seen_version=_version(c, row["id"]))
    H.task_done(c, BUILDER, row["id"], "Drafted both.", _version(c, row["id"]))
    out = H.task_thumbs(c, ANA, row["id"], asked["requirement_id"], False, "Either day is fine; do not ask.")
    assert out["status"] == "done"
    updates = c.execute(
        "SELECT actor FROM events WHERE action='task.update' AND target=? AND detail LIKE '%\"done\"%'", (row["id"],)
    ).fetchall()
    assert [u[0] for u in updates] == [ANA]  # what the move causes is Ana's, not Tico's


def test_a_thumb_is_offered_only_on_a_bots_ask(c):
    row = _new(c, by=BEN)
    theirs = H.task_ask(c, BEN, row["id"], ANA, "Which venue do we book?", seen_version=_version(c, row["id"]))
    ask = next(r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == theirs["requirement_id"])
    assert "thumbs" not in ask["may"]  # a person's ask on a bot's task
    with _refused("Only a bot's ask takes a thumb"):
        H.task_thumbs(c, ANA, row["id"], theirs["requirement_id"], True)
    stage_ok = X.requirement(c, "general-todo", "ok", person=ANA, line="OK the venue")
    mine = _new(c, owner=BEN, title="Book the venue")
    on_bot = next(r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == stage_ok)
    on_person = next(r for r in H.task_view(c, mine["id"], ANA)["to_leave"] if r["requirement_id"] == stage_ok)
    assert "thumbs" in on_bot["may"] and "thumbs" not in on_person["may"]  # a stage's ask is the owner bot's


def test_a_bots_ask_to_a_person_is_plain_words(c):
    row = _new(c)
    with _refused("lint"):
        H.task_ask(
            c,
            BUILDER,
            row["id"],
            ANA,
            "Waiting on the owner re db14c907 / reports/2026-10-14-trial.md",
            seen_version=_version(c, row["id"]),
        )
    assert H.task_view(c, row["id"], ANA)["to_leave"][0]["kind"] == "owner_done"
    assert len(H.task_view(c, row["id"], ANA)["to_leave"]) == 1


# ----------------------------------------------------------------------------- close and reopen
def test_a_bot_cannot_close_what_a_person_asked_for_and_a_person_closes_it_with_its_sub_tasks(c):
    row = _new(c)
    child = _new(c, by=BUILDER, owner=HELPER, title="Gather the numbers", parent_id=row["id"])
    with _refused("task_needs_person") as refusal:
        H.task_close(c, BUILDER, row["id"], "Not possible this week.", _version(c, row["id"]))
    assert any("tico task ask" in command for command in refusal.value.extra["next"])
    closed = H.task_close(c, ANA, row["id"])
    assert (closed["status"], closed["closed_by_id"]) == ("closed", ANA)
    assert H.task(c, child["id"])["status"] == "closed"
    assert ("task.close", row["id"]) in _events(c, row["id"])


def _told(c, child_id, parent_id):
    """What the bot that filed `child_id` heard of how it ended: its result, or the engine's wake on the parent."""
    rows = c.execute(
        "SELECT recipient_id, wake_key FROM messages WHERE wake_key LIKE ? OR wake_key LIKE ?",
        (f"{child_id}:%:result", f"{parent_id}:%:filed:{child_id}"),
    ).fetchall()
    return [(r[0], "filed" if ":filed:" in r[1] else "result") for r in rows]


@pytest.mark.parametrize(
    "how,heard",
    [
        ("parent_closed", "result"),
        # A bot's snooze on the parent was a wait for what it filed: it ends, and the engine wakes the bot there.
        ("bot_snoozed", "filed"),
        # A person's snooze is theirs to end: the bot hears the result as news.
        ("person_snoozed", "result"),
        ("parent_told", "filed"),
    ],
)
def test_a_bot_that_filed_a_sub_task_hears_once_how_it_ended(c, how, heard):
    parent = _new(c)
    child = _new(c, by=BUILDER, owner=HELPER, title="Gather the numbers", parent_id=parent["id"])
    if how == "parent_closed":
        H.task_close(c, ANA, parent["id"], "The launch is off.")  # its sub-task closes with it
    else:
        if how.endswith("_snoozed"):
            by = BUILDER if how == "bot_snoozed" else ANA
            H.task_snooze(c, by, parent["id"], "Waiting on the numbers.", None, _version(c, parent["id"]))
        H.task_done(c, HELPER, child["id"], "Here they are.", _version(c, child["id"]))
    assert H.task(c, child["id"])["status"] in ("done", "closed")
    # Once: the result, or the engine's wake on the parent when it gave one.
    assert _told(c, child["id"], parent["id"]) == [(BUILDER, heard)]
    assert bool(E.task(c, parent["id"]).get("snooze")) == (how == "person_snoozed")


def test_a_person_reopens_with_a_reason_and_a_bot_does_not_reopen_what_a_person_closed(c):
    row = _new(c)
    H.task_close(c, ANA, row["id"], "Not now.")
    with _refused("task_needs_person"):
        H.task_move(c, BUILDER, row["id"], H.TASK_REOPEN, "I can do it now.", _version(c, row["id"]))
    with _refused("task_why"):
        H.task_move(c, ANA, row["id"], H.TASK_REOPEN)
    back = H.task_move(c, ANA, row["id"], H.TASK_REOPEN, "The date moved up.")
    assert (back["status"], back["stage_id"], back["closed_at"]) == ("open", "general-todo", None)
    assert [w[1] for w in X.wakes(c, row["id"])][-1] == BUILDER


def test_a_bot_closes_what_a_bot_asked_for_with_a_reason(c):
    row = _new(c, by=HELPER)
    with _refused("task_why"):
        H.task_close(c, HELPER, row["id"], "", _version(c, row["id"]))
    assert H.task_close(c, HELPER, row["id"], "No longer needed.", _version(c, row["id"]))["status"] == "closed"


# ----------------------------------------------------------------------------- reassign
def test_reassign_gives_the_task_to_its_new_owner_and_tells_the_automations_once(c):
    row = _new(c)
    X.settled(c)
    version = _version(c, row["id"])
    out = H.task_reassign(c, ANA, row["id"], HELPER, "Helper has the numbers.")
    assert (out["owner_id"], out["assignee_id"], out["version"]) == (HELPER, HELPER, version + 1)
    assert [w[1] for w in X.wakes(c, row["id"])][-1] == HELPER
    assert H.task_reassign(c, ANA, row["id"], HELPER)["version"] == version + 1  # the same owner: no change
    with _refused("task_requirement|identity|reach"):
        H.task_reassign(c, ANA, row["id"], "bot:nobody")


def _pr(c, task_id, number, repo="acme/app"):
    c.execute(
        "INSERT INTO task_links(id,task_id,kind,url,repo,number,created_at) VALUES(?,?,'pr',?,?,?,?)",
        (H.new_id(), task_id, f"https://github.com/{repo}/pull/{number}", repo, number, H.now()),
    )


def test_a_finding_on_a_pipeline_tasks_own_pull_request_is_never_handed_to_a_person(c):
    """Seen live on PR #3390: Reviewer 2 filed a real bug on its task's own PR to the Engineering Manager, which gave
    it to a person. A finding on the PR is the review verdict, the Builder's to fix: a bot can't give a person a General
    task that links or names it, by reassigning or by filing one."""
    X.pipeline(c, "engineering", "Engineering", stages=[{"name": "Review 2", "owner": HELPER}])
    work = X.new_task(c, ANA, "Order export totals", pipeline_id="engineering", assignee=BUILDER)
    _pr(c, work["id"], 12)
    finding = _new(c, by=HELPER, owner=MANAGER, title="Bug in the totals")
    _pr(c, finding["id"], 12, repo="Acme/App")
    with _refused("review_route") as refused:
        H.task_reassign(c, MANAGER, finding["id"], ANA, "Only a person should see this.", _version(c, finding["id"]))
    assert "acme/app#12" in str(refused.value) and "Order export totals" in str(refused.value)
    assert H.task(c, finding["id"])["owner_id"] == MANAGER
    named = H.task_create(
        c, MANAGER, "Look at this", "See https://github.com/acme/app/pull/12 line 4.", ANA, lint=False
    )
    with _refused("review_route"):
        H.review_finding_route(c, MANAGER, named["id"], ANA)  # app.task_create's check, once its links are written
    # Bot to bot, a person's own hand-over, another PR, and a PR whose pipeline task ended all pass.
    assert (
        H.task_reassign(c, MANAGER, finding["id"], BUILDER, "Yours.", _version(c, finding["id"]))["owner_id"] == BUILDER
    )
    assert H.task_reassign(c, ANA, finding["id"], BEN, "Yours.", _version(c, finding["id"]))["owner_id"] == BEN
    other = _new(c, by=HELPER, owner=MANAGER, title="Pick a release date")
    _pr(c, other["id"], 13)
    assert (
        H.task_reassign(c, MANAGER, other["id"], ANA, "A date is Ana's.", _version(c, other["id"]))["owner_id"] == ANA
    )
    H.task_close(c, ANA, work["id"], "Dropped", _version(c, work["id"]))
    H.review_finding_route(c, MANAGER, named["id"], ANA)


# ----------------------------------------------------------------------------- field edits
def test_an_edit_changes_fields_raises_the_version_once_and_never_the_status(c):
    row = _new(c)
    version = _version(c, row["id"])
    out = H.task_update(c, ANA, row["id"], title="Write the launch note today", due="2026-12-01", seen_version=version)
    assert (out["title"], out["due_at"], out["version"], out["status"]) == (
        "Write the launch note today",
        "2026-12-01",
        version + 1,
        "open",
    )
    with _refused("task_changed"):
        H.task_update(c, ANA, row["id"], title="Stale", seen_version=version)
    assert H.task_update(c, ANA, row["id"], title="Write the launch note today")["version"] == version + 1  # no change
    with pytest.raises(ValueError, match="open, done or closed"):
        H.task_update(c, ANA, row["id"], status="doing")


def test_the_old_way_of_saying_done_closed_reopen_and_a_new_owner_goes_through_the_engine(c):
    row = _new(c, by=HELPER)
    assert H.task_update(c, ANA, row["id"], owner=HELPER, note="Helper has it.")["owner_id"] == HELPER
    done = H.task_update(c, HELPER, row["id"], status="done", note="Sent.", seen_version=_version(c, row["id"]))
    assert (done["status"], done["stage_id"]) == ("done", "general-done")
    again = H.task_update(c, ANA, row["id"], status="open", note="One more pass.")
    assert (again["status"], again["stage_id"]) == ("open", "general-todo")
    assert H.task_update(c, ANA, row["id"], status="closed", note="Dropped.")["status"] == "closed"
    assert tuple(_visits(c, row["id"])) == (4, 1)


# ----------------------------------------------------------------------------- snooze, attempt
def test_a_snoozed_task_wakes_nobody_until_it_is_unsnoozed(c):
    row = _new(c)
    with _refused("task_why"):
        H.task_snooze(c, BUILDER, row["id"], None, "2026-12-01T00:00:00Z", _version(c, row["id"]))
    out = H.task_snooze(
        c, BUILDER, row["id"], "The vendor replies by Dec 1.", "2026-12-01T00:00:00Z", _version(c, row["id"])
    )
    assert out["snooze"]["until_at"] == "2026-12-01T00:00:00Z"
    view = H.task_view(c, row["id"], ANA)
    assert (view["turn"]["state"], view["snooze"]["why"]) == ("snoozed", "The vendor replies by Dec 1.")
    assert H.task_unsnooze(c, ANA, row["id"])["snooze"] is None


def test_attempt_undoes_a_refused_call_alone(c):
    row = _new(c)
    H.task_update(c, ANA, row["id"], title="Kept")
    assert H.attempt(c, H.task_close, BUILDER, row["id"], "Not mine to close.", _version(c, row["id"])) is None
    assert (H.task(c, row["id"])["title"], H.task(c, row["id"])["status"]) == ("Kept", "open")
    assert H.attempt(c, H.task_done, BUILDER, row["id"], "Done.", _version(c, row["id"]))["status"] == "done"


# ----------------------------------------------------------------------------- who may
def test_a_bot_that_is_not_on_the_task_cannot_work_it_and_the_pipelines_manager_can(c):
    row = _new(c)
    with _refused("identity"):
        H.task_snooze(c, HELPER, row["id"], "Parking it.", None, _version(c, row["id"]))
    assert H.task_snooze(c, MANAGER, row["id"], "Parking it.", None, _version(c, row["id"]))["snooze"]


def test_a_private_task_is_not_found_by_someone_who_may_not_read_it(c):
    row = _new(c, private=True)
    with _refused("not-found"):
        H.task_done(c, BEN, row["id"], "Done.")
    with _refused("not-found"):
        H.task_answer(c, BEN, row["id"], "any", "ok")


# ----------------------------------------------------------------------------- the trash
@pytest.mark.parametrize("ended", [None, "done", "closed"])
def test_a_deleted_task_keeps_no_stage_and_a_restored_one_comes_back_as_it_stood(c, ended):
    row = _new(c)
    H.task_ask(c, BUILDER, row["id"], ANA, "Ship it on Monday or Friday?", seen_version=_version(c, row["id"]))
    if ended == "done":
        H.task_move(c, ANA, row["id"], H.TASK_DONE, "Good enough.")
    elif ended == "closed":
        H.task_close(c, ANA, row["id"], "Not now.")
    X.settled(c)
    woken = len(X.wakes(c, row["id"], BUILDER))
    report = task_delete.delete_tasks(c, [row["id"]], apply=True, keep_work=True)
    assert report["applied"]
    for table in ("visits", "snoozes"):
        assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (row["id"],)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM requirements WHERE visit_id IS NOT NULL").fetchone()[0] == 0
    assert task_delete.restore_tasks(c, [row["id"]], ANA)["restored"] == [row["id"]]
    back = E.task(c, row["id"])
    assert (back["status"], back["pipeline_id"], back["owner_id"]) == (ended or "open", "general", BUILDER)
    assert back["stage"]["kind"] == {None: "work", "done": "done", "closed": "closed"}[ended]
    assert tuple(_visits(c, row["id"])) == (1, 1)
    assert [r["kind"] for r in H.task_view(c, row["id"], ANA)["to_leave"]] == (["owner_done"] if not ended else [])
    # An open task is its owner's turn again: one new wake. One that ended wakes nobody.
    assert len(X.wakes(c, row["id"], BUILDER)) == woken + (1 if ended is None else 0)


def test_a_restored_task_goes_back_only_where_its_pipeline_lets_it_in_now(c):
    """An open task comes back by the engine's own rules: into the stage it was in while that stage is still in its
    pipeline, has room and an owner who can read it, else into the first stage, as a new task would. If no stage may
    take it the restore is refused, says why, and the task stays in the trash."""
    X.bot(c, "reviewer")
    X.bot(c, "editor")
    stages = (
        {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {"name": "Review", "owner": "bot:reviewer", "task_limit": 1, "requirements": [{"kind": "owner_done"}]},
    )
    X.pipeline(c, "notes", "Notes", stages, manager=MANAGER)

    def in_review(**more):
        row = _new(c, pipeline="notes", **more)
        if more.get("private"):
            H.set_task_readers(c, ANA, row["id"], add=["bot:reviewer"])
        H.task_done(c, BUILDER, row["id"], "Drafted.", _version(c, row["id"]))
        if more.get("private"):
            H.set_task_readers(c, ANA, row["id"], add=[BUILDER])  # no longer its owner: a reader, to take it back
        X.settled(c)
        return row["id"]

    def trash(tid):
        assert task_delete.delete_tasks(c, [tid], apply=True, keep_work=True)["applied"]

    def placed(tid):
        back = E.task(c, tid)
        return back["stage"]["name"], back["owner_id"]

    kept = in_review()
    trash(kept)
    assert task_delete.restore_tasks(c, [kept], ANA)["restored"] == [kept]
    assert placed(kept) == ("Review", "bot:reviewer")  # its stage, with room: as it stood
    trash(kept)
    holder = in_review()  # Review is full now
    assert task_delete.restore_tasks(c, [kept], ANA)["restored"] == [kept]
    assert placed(kept) == ("Draft", BUILDER)
    H.task_close(c, ANA, holder, "Not now.")
    secret = in_review(private=True, title="Salary numbers")
    trash(secret)
    c.execute("UPDATE stages SET owner='bot:editor' WHERE id='notes-review'")  # Review's new owner cannot read it
    assert task_delete.restore_tasks(c, [secret], ANA)["restored"] == [secret]
    assert placed(secret) == ("Draft", BUILDER)
    assert not H.task_private_readable(c, "bot:editor", H.task(c, secret))
    assert X.wakes(c, secret, "bot:editor") == []
    trash(secret)
    c.execute("UPDATE stages SET owner='bot:editor' WHERE id='notes-draft'")  # and Draft's too
    refused = task_delete.restore_tasks(c, [secret], ANA)
    assert refused["restored"] == [] and "Who can see" not in refused["not_shared"][secret]["fix"]
    assert "editor" in refused["not_shared"][secret]["cause"]
    assert c.execute("SELECT 1 FROM tasks WHERE id=?", (secret,)).fetchone() is None
    assert c.execute("SELECT 1 FROM task_trash WHERE task_id=?", (secret,)).fetchone()


# ----------------------------------------------------------------------------- privacy
def test_a_private_sub_tasks_title_is_not_shown_to_someone_who_reads_only_its_parent(c):
    parent = _new(c, by=BEN, owner=BUILDER, title="Plan the offsite")
    child = _new(c, by=BUILDER, owner=HELPER, title="Salary numbers for the offsite", parent_id=parent["id"])
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (child["id"],))
    c.execute("UPDATE requirements SET kind='linked_done' WHERE stage_id='general-todo'")
    text = json.dumps(H.task_view(c, parent["id"], BEN))
    assert "Salary" not in text and "A sub-task" in text
    assert "Salary" in json.dumps(H.task_view(c, parent["id"]))  # the engine's own read hides nothing


def test_the_wake_that_says_a_tasks_sub_tasks_ended_names_only_those_everyone_who_reads_it_may_read(c):
    """Codex review 2: the wake on the parent, kept in its thread, says each filed task ended. One the woken bot may not
    open, or a private one whose readers are not the parent's (Ben reads the parent and its thread, not it), is "A
    sub-task": that it ended, never its title."""
    from backend import task_privacy

    parent = _new(c, by=BEN, owner=BUILDER, title="Plan the offsite")
    hidden = _new(c, by=ANA, owner=HELPER, title="Salary numbers", parent_id=parent["id"], private=True)
    own = _new(c, by=BUILDER, owner=HELPER, title="Venue shortlist", parent_id=parent["id"], private=True)
    shared = _new(c, by=BUILDER, owner=HELPER, title="Book the coach", parent_id=parent["id"])
    assert not H.task_private_readable(c, BUILDER, H.task(c, hidden["id"]))
    assert not H.task_private_readable(c, BEN, H.task(c, own["id"]))
    for child in (own, shared, hidden):
        H.task_done(c, HELPER, child["id"], "Here.", _version(c, child["id"]))
    (wake,) = c.execute(
        "SELECT id, recipient_id, body FROM messages WHERE wake_key LIKE ?", (f"{parent['id']}:%:filed:%",)
    ).fetchall()
    assert wake["recipient_id"] == BUILDER
    assert "Salary" not in wake["body"] and "Venue" not in wake["body"], wake["body"]
    assert "Book the coach: Done" in wake["body"] and wake["body"].count("A sub-task: Done") == 2, wake["body"]
    assert task_privacy.message_readable(c, BEN, H.message(c, wake["id"]))  # Ben reads it in the parent's thread


def test_a_task_names_the_tasks_it_filed_and_the_one_that_filed_it_only_to_who_may_read_them(c):
    """Nobody who may not read a private task learns its title, its id, its status or that it exists: the parent's
    `filed` leaves it out whole, and the child's `filed_by` says nothing of a private parent."""
    parent = _new(c, by=BEN, owner=BUILDER, title="Plan the offsite")
    child = _new(c, by=BUILDER, owner=HELPER, title="Salary numbers for the offsite", parent_id=parent["id"])
    shown = _new(c, by=BUILDER, owner=HELPER, title="Book the venue", parent_id=parent["id"])
    c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", (json.dumps([ANA]), child["id"]))
    ben = H.task_view(c, parent["id"], BEN)
    assert [f["id"] for f in ben["filed"]] == [shown["id"]]
    assert child["id"] not in json.dumps(ben)
    assert [f["id"] for f in H.task_view(c, parent["id"], ANA)["filed"]] == [child["id"], shown["id"]]
    # A private parent (Ben asked for it, so he still reads it), a public child: Cara reads only the child and is
    # not told which task filed it.
    c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", (json.dumps([ANA]), parent["id"]))
    assert H.task_view(c, shown["id"], "human:cara")["filed_by"] is None
    assert H.task_view(c, shown["id"], ANA)["filed_by"] == parent["id"]


def _offsite(company):
    """A public task Ben asked for, with a private sub-task Ana reads and Ben does not. Its stage waits on it."""
    with company.transaction() as c:
        for slug in ("builder", "helper", "manager"):
            X.bot(c, slug)
        parent = _new(c, by=BEN, owner=BUILDER, title="Plan the offsite")
        child = _new(c, by=BUILDER, owner=HELPER, title="Salary numbers for the offsite", parent_id=parent["id"])
        c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", (json.dumps([ANA]), child["id"]))
        c.execute("UPDATE requirements SET kind='linked_done' WHERE stage_id='general-todo'")
        assert H.task_private_readable(c, ANA, H.task(c, child["id"]))
        assert not H.task_private_readable(c, BEN, H.task(c, child["id"]))
    return parent["id"]


def _move_through_the_store(company, who, task_id, key):
    """A move as a route makes it: one write with an idempotency key. Returns the refusal (a Problem)."""
    from backend.auth import Identity
    from backend.store import Problem

    with pytest.raises(Problem) as refused:
        company.mutate(
            Identity(who, "human"),
            f"/api/v2/tasks/{task_id}/move",
            key,
            {"to": "next"},
            lambda c: H.task_move(c, who, task_id, H.TASK_NEXT, None, None, mover=True),
        )
    return refused.value


def test_a_refusal_names_a_private_sub_task_only_to_who_may_read_it_and_never_on_record(company):
    parent = _offsite(company)
    ben = _move_through_the_store(company, BEN, parent, "ben-1")
    assert ben.code == "task_not_met"
    assert "Salary" not in json.dumps([ben.detail, ben.extra]) and "A sub-task" in json.dumps(ben.extra["missing"])
    ana = _move_through_the_store(company, ANA, parent, "ana-1")
    assert "Salary" in json.dumps([ana.detail, ana.extra["missing"]])  # Ana reads it: she is told which
    with company.transaction() as c:
        kept = [r[0] for r in c.execute("SELECT detail FROM refusals WHERE rule='task_not_met'")]
        kept += [r[0] for r in c.execute("SELECT detail FROM events WHERE action='refused'")]
        kept += [r[0] for r in c.execute("SELECT response FROM idempotency WHERE key IN ('ben-1','ana-1')")]
    assert len(kept) >= 6 and not [k for k in kept if "Salary" in k]  # the audit and the receipts keep neither


def test_a_replayed_refusal_on_a_private_task_says_again_what_is_missing_to_who_may_read_it(company):
    """The receipt keeps no private words; its replay (store.replayed_refusal, which Store.mutate runs on a replayed
    receipt) builds `missing` and `next` again from the task for a caller who may read it."""
    from backend import store

    with company.transaction() as c:
        for slug in ("builder", "helper", "manager"):
            X.bot(c, slug)
        row = _new(c, by=BEN, owner=BUILDER, private=True)
        H.task_ask(c, BUILDER, row["id"], BEN, "Do we drop the Acme contract?", seen_version=_version(c, row["id"]))
    first = _move_through_the_store(company, BEN, row["id"], "ben-1")
    assert first.code == "task_not_met" and "Acme" in json.dumps(first.extra["missing"])
    assert "next" not in first.extra  # a person in the app is told what is missing, never a `tico task ...` line
    with company.transaction() as c:
        stored = c.execute("SELECT response FROM idempotency WHERE key='ben-1'").fetchone()[0]
        assert "Acme" not in stored  # never kept: built again on a replay
        receipt = json.loads(stored)
        replayed = store.replayed_refusal(c, BEN, receipt)["_refusal"]
        assert (replayed["code"], replayed["detail"]) == ("task_not_met", "Private task write refused")
        assert replayed["extra"]["missing"] == first.extra["missing"] and "next" not in replayed["extra"]
        assert store.replayed_refusal(c, "human:cara", receipt) == receipt  # may not read it: the receipt as kept


def test_a_bot_never_answers_its_own_ask_and_an_ok_is_asked_only_of_a_person(c):
    row = _new(c, by=BEN, owner=BUILDER)
    for to, kind in ((BUILDER, "ok"), (BUILDER, "answer"), (HELPER, "ok")):
        with _refused("task_requirement"):
            H.task_ask(c, BUILDER, row["id"], to, "Ship it on Monday?", kind=kind, seen_version=_version(c, row["id"]))
    assert len(H.task_view(c, row["id"], BEN)["to_leave"]) == 1  # nothing was asked
    H.task_ask(c, BUILDER, row["id"], HELPER, "Which day suits the launch?", seen_version=_version(c, row["id"]))


def test_a_persons_own_ask_is_okd_only_by_their_own_click(c):
    """A person who asked for an OK may give it to themself: their own signed-in click is what an OK is. The
    Assistant or BotOps acting for them, or a token (no own click), never gives it; a bot never answers an OK."""
    row = _new(c, by=BEN, owner=BUILDER)
    asked = H.task_ask(c, ANA, row["id"], BEN, "Ship it on Monday?", kind="ok")["requirement_id"]
    H.task_give(c, ANA, row["id"], asked, ANA)  # given back to the one who asked it
    with _refused("task_requirement"):
        H.task_answer(c, ANA, row["id"], asked, "ok")  # a token, or any call that is not her own click
    via = H.VIA.set("assistant")
    try:
        with _refused("task_requirement"):
            H.task_answer(c, ANA, row["id"], asked, "ok", own_click=True)
    finally:
        H.VIA.reset(via)
    with _refused("task_requirement"):
        H.task_answer(
            c, BUILDER, row["id"], asked, "no", "Not yet", _version(c, row["id"])
        )  # a bot answers no OK, not even a send back
    H.task_answer(c, ANA, row["id"], asked, "ok", own_click=True)
    mine = H.task_ask(c, ANA, row["id"], ANA, "OK my own wording?", kind="ok")["requirement_id"]  # asked of herself
    H.task_answer(c, ANA, row["id"], mine, "ok", own_click=True)
    met = {r["requirement_id"]: r["met"] for r in H.task_view(c, row["id"], ANA)["to_leave"]}
    assert met[asked] and met[mine]


def test_a_bots_new_ask_keeps_what_was_decided_and_replaces_only_its_open_ask(c):
    """C15: a bot's new ask replaces its ask that is still open. An answered question and an OK'd OK stay on the task,
    met, with their answers (smoke 2026-11-10, item 3)."""
    row = _new(c, by=BEN, owner=BUILDER)
    question = H.task_ask(c, BUILDER, row["id"], BEN, "Which CRM?", seen_version=_version(c, row["id"]))
    H.task_answer(c, BEN, row["id"], question["requirement_id"], "New CRM")
    ok = H.task_ask(c, BUILDER, row["id"], BEN, "OK the budget?", kind="ok", seen_version=_version(c, row["id"]))
    H.task_answer(c, BEN, row["id"], ok["requirement_id"], "ok")
    open_one = H.task_ask(c, BUILDER, row["id"], BEN, "Which day?", seen_version=_version(c, row["id"]))
    last = H.task_ask(c, BUILDER, row["id"], BEN, "Which week?", seen_version=_version(c, row["id"]))
    shown = {r["requirement_id"]: r for r in H.task_view(c, row["id"], BEN)["to_leave"]}
    assert open_one["requirement_id"] not in shown  # it was still open: replaced
    assert (
        shown[question["requirement_id"]]["met"] and shown[question["requirement_id"]]["answer"]["value"] == "New CRM"
    )
    assert shown[ok["requirement_id"]]["met"] and shown[ok["requirement_id"]]["standing"] == "OK'd"
    assert not shown[last["requirement_id"]]["met"]


def test_a_bots_new_ask_never_drops_an_ok_given_for_exact_content_it_has_not_used(c):
    from backend import task_asks

    row = _new(c, by=BEN, owner=BUILDER)
    asked = task_asks.ask_ok(
        c,
        BUILDER,
        BEN,
        "Raise the budget?",
        shows=[],
        subject="budget_raise:b1",
        content={"cents": 500},
        task_id=row["id"],
    )
    rid = asked["requirement_id"]
    task_asks.show(c, BEN, rid, {"cents": 500}, own_click=True)
    task_asks.give_ok(c, BEN, rid, task_asks.content_hash({"cents": 500}), own_click=True)
    with _refused("task_requirement"):
        H.task_ask(c, BUILDER, row["id"], BEN, "Which week?", seen_version=_version(c, row["id"]))
    assert task_asks.standing(c, rid) == task_asks.OK  # still good, for that content only
    task_asks.use(c, rid, "budget_raise:b1", {"cents": 500}, BUILDER)
    H.task_ask(c, BUILDER, row["id"], BEN, "Which week?", seen_version=_version(c, row["id"]))
    assert task_asks.standing(c, rid) == task_asks.USED
    assert H.task_view(c, row["id"], BEN)["to_leave"][-1]["requirement_id"] == rid  # what was decided stays


def test_an_answer_the_assistant_gives_for_a_person_says_so_on_the_task(c):
    row = _new(c, by=BEN, owner=BUILDER)
    asked = H.task_ask(c, BUILDER, row["id"], BEN, "Which CRM?", seen_version=_version(c, row["id"]))["requirement_id"]
    via = H.VIA.set("assistant")
    try:
        H.task_answer(c, BEN, row["id"], asked, "New CRM")
    finally:
        H.VIA.reset(via)
    answer = next(r for r in H.task_view(c, row["id"], BEN)["to_leave"] if r["requirement_id"] == asked)["answer"]
    assert (answer["by"], answer["via"]) == (BEN, "assistant")
    history = [e for e in H.task_history(c, row["id"]) if e["field"] == "answer"]
    assert [(e["actor"], e["via"]) for e in history] == [(BEN, "assistant")]


def test_an_ask_on_a_private_task_makes_nobody_a_reader_and_says_how_to_share_it(c):
    """Not even the bot that owns a private task no person is on brings a person in by asking them: the ask is refused
    with the fix, and nothing is written. Once they are added under Who can see, the same ask goes."""
    row = _new(c, by=HELPER, owner=BUILDER, private=True)
    for ask in (
        lambda: H.task_ask(c, BUILDER, row["id"], BEN, "Which vendor?", seen_version=_version(c, row["id"])),
        lambda: E.add_requirement(
            c, row["id"], BUILDER, "answer", BEN, "Which vendor?", seen_version=_version(c, row["id"])
        ),
    ):
        with _refused("private") as refused:
            ask()
        assert "Who can see" in refused.value.detail
    task = H.task(c, row["id"])
    assert H.task_readers(task) == [] and not H.task_private_readable(c, BEN, task)
    assert len(H.task_view(c, row["id"], BUILDER)["to_leave"]) == 1
    asked = H.task_ask(c, BUILDER, row["id"], ANA, "Which vendor?", seen_version=_version(c, row["id"]))
    with _refused("private"):
        H.task_give(c, BUILDER, row["id"], asked["requirement_id"], BEN)
    H.set_task_readers(c, BUILDER, row["id"], add=[BEN])
    H.task_ask(c, BUILDER, row["id"], BEN, "Which vendor?", seen_version=_version(c, row["id"]))


def test_a_bot_asks_only_a_person_in_its_reach_or_who_handed_it_the_task(c, monkeypatch):
    """By default a bot reaches its manager, its reports, its peers and its team (reach.py, mode nearby). An ask is a
    write to a person like a message is, so one outside that reach is refused and nothing is asked; the person who
    handed the bot the task may always be asked about it."""
    from backend import reach

    monkeypatch.setenv("TICO_REACH_DEFAULT", "nearby")  # conftest starts tests open
    row = _new(c, by=BEN, owner=BUILDER)
    with pytest.raises(reach.Refused) as refused:
        H.task_ask(c, BUILDER, row["id"], "human:cara", "Which vendor do we keep?", seen_version=_version(c, row["id"]))
    assert refused.value.code == "reach_closed"
    assert len(H.task_view(c, row["id"], BEN)["to_leave"]) == 1  # only the owner's own done
    H.task_ask(c, BUILDER, row["id"], BEN, "Which vendor do we keep?", seen_version=_version(c, row["id"]))


def test_a_refused_write_on_a_private_task_is_recorded_without_its_words(c):
    row = _new(c, by=BEN, owner=BUILDER, private=True)
    H.task_ask(c, BUILDER, row["id"], BEN, "Do we drop the Acme contract?", seen_version=_version(c, row["id"]))
    with _refused("task_not_met") as refusal:
        H.task_move(c, BUILDER, row["id"], H.TASK_NEXT, None, _version(c, row["id"]))
    assert refusal.value.private is True
    recorded = c.execute("SELECT detail FROM refusals WHERE rule='task_not_met' ORDER BY created_at DESC").fetchone()[0]
    assert "Acme" not in recorded and "Private task write refused" in recorded


# ----------------------------------------------------------------------------- versions, owners, the trash
def test_a_bot_that_sends_no_version_is_refused_the_old_way_of_saying_done_too(c):
    row = _new(c)
    with _refused("task_changed"):
        H.task_update(c, BUILDER, row["id"], status="done", note="Sent.")
    assert H.task(c, row["id"])["status"] == "open"
    assert H.task_update(c, ANA, row["id"], status="done", note="Sent.")["status"] == "done"  # a person may


def test_a_move_into_a_stage_someone_else_owns_does_what_a_reassignment_does(c):
    stages = (
        {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {"name": "Check", "owner": HELPER, "requirements": [{"kind": "owner_done"}]},
    )
    X.pipeline(c, "notes", "Notes", stages, manager=MANAGER)
    row = _new(c, pipeline="notes")
    wake = c.execute("SELECT id FROM messages WHERE wake_key LIKE ?", (row["id"] + ":%",)).fetchone()[0]
    c.execute(
        "INSERT INTO task_delegations (task_id, delegate, requested_by, message_id, expires_at) VALUES (?,?,?,?,?)",
        (row["id"], MANAGER, ANA, wake, "2099-01-01T00:00:00Z"),
    )
    rank = H.task(c, row["id"])["rank"]
    out = H.task_done(c, BUILDER, row["id"], "Drafted.", _version(c, row["id"]))
    assert (out["stage"]["name"], out["owner_id"]) == ("Check", HELPER)
    assert c.execute("SELECT COUNT(*) FROM task_delegations WHERE task_id=?", (row["id"],)).fetchone()[0] == 0
    assert H.task(c, row["id"])["rank"] != rank or rank is None  # at the end of the new owner's queue


COUNSEL = "bot:counsel"


def _private_owner(c, approver):
    """A pipeline whose Check stage is owned by a bot that keeps its tasks private and asks `approver` for an OK."""
    from backend import bot_settings as BS

    X.bot(c, "counsel")
    BS.write(c, "counsel", {"name": "counsel", "runtime": "fake", "status": "active", "private_tasks_default": True})
    stages = (
        {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "ok", "person": approver, "line": "OK it"}]},
        {"name": "Check", "owner": COUNSEL, "requirements": [{"kind": "ok", "person": approver, "line": "OK it"}]},
    )
    X.pipeline(c, "terms", "Terms", stages, manager=MANAGER)


def _published(monkeypatch):
    """(event, whether the task was private when it went out) for each event a move publishes."""
    from backend import task_effects

    out, real = [], task_effects._publish

    def publish(conn, name, row, *rest):
        out.append((name, H.task_private(conn, H.task(conn, row["id"]))))
        return real(conn, name, row, *rest)

    monkeypatch.setattr(task_effects, "_publish", publish)
    return out


@pytest.mark.parametrize("how", ["move", "reassign"])
def test_a_task_that_goes_private_with_its_new_owner_is_private_before_anyone_hears(c, monkeypatch, how):
    _private_owner(c, HELPER)  # the stage asks the bot that asked for the task: it reads it once it is private
    row = _new(c, by=HELPER, pipeline="terms")  # a bot's task for a bot: nobody else reads it once it is private
    published = _published(monkeypatch)
    before = {w[0] for w in X.wakes(c, row["id"])}
    if how == "move":
        out = H.task_move(c, BUILDER, row["id"], "Check", "Drafted; Ben checks it there.", _version(c, row["id"]))
    else:
        out = H.task_reassign(c, BUILDER, row["id"], COUNSEL, "Counsel drafts this.", _version(c, row["id"]))
    assert out["owner_id"] == COUNSEL and H.task_private(c, H.task(c, row["id"]))
    assert published and all(private for _, private in published), published
    # Nobody was made a reader.
    task = H.task(c, row["id"])
    assert H.task_readers(task) == [] and not H.task_private_readable(c, BEN, task)
    for key, who in X.wakes(c, row["id"]):
        assert key in before or H.task_private_readable(c, who, task), (key, who)


def test_a_move_into_a_stage_whose_asked_bot_could_not_read_the_private_task_is_held_and_shares_nothing(c):
    """The engine never adds a reader to a private task: a move into a stage that asks a bot who could not read the
    task there (here it goes private with Counsel) is refused with its cause and its fix, and the task stays."""
    _private_owner(c, "bot:reviewer")
    X.bot(c, "reviewer")
    row = _new(c, by=HELPER, pipeline="terms")
    with _refused("task_not_shared") as refused:
        H.task_move(c, BUILDER, row["id"], "Check", "Drafted.", _version(c, row["id"]))
    extra = refused.value.extra
    assert "reviewer" in extra["cause"] and "Who can see" in extra["fix"]
    after = E.task(c, row["id"])
    assert (after["stage"]["name"], after["owner_id"], H.task_private(c, after)) == ("Draft", BUILDER, False)
    assert H.task_readers(after) == []
    assert not c.execute("SELECT 1 FROM history_events WHERE subject_id=? AND kind='reader'", (row["id"],)).fetchone()


def test_a_private_task_is_not_made_into_a_first_stage_that_asks_someone_who_cannot_read_it(c):
    """Codex review 5: creation keeps the rule a move keeps (task_engine.shut_out). A first stage that waits on Ben's OK
    takes a private task only when Ben may read it: else the task would wait on him, out of his Needs you, with
    nobody's turn and no deadline. Refused as a move is, and nothing is left behind; made with Ben under Who can see,
    it goes in."""
    stages = ({"name": "Approve", "owner": "assignee", "requirements": [{"kind": "ok", "person": BEN, "line": "OK"}]},)
    X.pipeline(c, "payroll", "Payroll", stages, manager=MANAGER)
    before = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "conversations", "messages")]
    with _refused("task_not_shared") as refused:
        _new(c, pipeline="payroll", private=True, title="Salary bands")
    assert "Ben" in refused.value.extra["cause"] and refused.value.extra["fix"] == "Add Ben under Who can see"
    after = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "conversations", "messages")]
    assert after == before
    made = E.task(c, _new(c, pipeline="payroll", private=True, title="Salary bands", readers=[BEN])["id"])
    assert made["stage"]["name"] == "Approve" and H.task_readers(made) == [BEN]
    assert _new(c, pipeline="payroll", title="Office hours")["status"] == "open"  # a company task: Ben reads it


def test_a_private_task_waits_before_a_stage_whose_owner_cannot_read_it_until_someone_who_can_shares_it(c):
    """Everything is met, and the next stage's owner cannot read the private task: the engine does not move it (that
    would make the owner a party), says why and what fixes it, and wakes nobody there. Once a person who reads it
    adds the owner under Who can see, the never-stuck pass moves it on."""
    from backend import never_stuck

    X.bot(c, "reviewer")
    stages = (
        {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {"name": "Review", "owner": "bot:reviewer", "requirements": [{"kind": "owner_done"}]},
    )
    X.pipeline(c, "notes", "Notes", stages, manager=MANAGER)
    row = _new(c, pipeline="notes", private=True)
    with _refused("task_not_shared"):
        H.task_move(c, ANA, row["id"], "Review", None, _version(c, row["id"]))
    H.task_done(c, BUILDER, row["id"], "Drafted.", _version(c, row["id"]))
    held = E.task(c, row["id"])
    assert (held["stage"]["name"], held["owner_id"]) == ("Draft", BUILDER)
    assert not H.task_private_readable(c, "bot:reviewer", H.task(c, row["id"]))
    assert X.wakes(c, row["id"], "bot:reviewer") == []
    turn = H.task_view(c, row["id"], ANA)["turn"]
    assert "reviewer" in turn["line"] and "Who can see" in turn["fix"]
    from backend import waiting_on as W

    (wait,) = W.waiting_on(c, row["id"])  # Needs you, lists and briefs read this line, not "Moving to Review"
    assert wait.line == "Waits on Ana to share it: reviewer owns Review and cannot read this private task", wait
    never_stuck.check(c)
    assert E.task(c, row["id"])["stage"]["name"] == "Draft"  # still held: nothing was shared
    H.set_task_readers(c, ANA, row["id"], add=["bot:reviewer"])
    never_stuck.check(c)
    moved = E.task(c, row["id"])
    assert (moved["stage"]["name"], moved["owner_id"]) == ("Review", "bot:reviewer")


def test_a_task_is_never_replaced_by_an_insert(c):
    import sqlite3

    row = _new(c)
    with pytest.raises(sqlite3.IntegrityError, match="never replaced"):
        c.execute("INSERT OR REPLACE INTO tasks (id, status, owner_id) VALUES (?, 'open', ?)", (row["id"], HELPER))
    assert E.task(c, row["id"])["owner_id"] == BUILDER


def test_an_ok_taken_back_before_the_task_moves_on_stays_taken_back_whatever_the_clock_says(c, monkeypatch):
    """Ana OKs an email, then it changes and her OK is taken back; the task moves on once Ben answers where to send
    it. Her OK stays withdrawn and cannot be used, even when every write lands in the same instant: a met OK is not
    told from a taken-back one by two timestamps that happen to match."""
    from backend import hubdb
    from backend import task_asks as K

    monkeypatch.setattr(hubdb, "now", lambda: "2026-11-10T12:00:00.000000Z")
    email = {"to": ["pat@example.com"], "subject": "Your invoice", "body_sha256": "b" * 64}
    row = _new(c, by=ANA, owner=BUILDER)
    asked = K.ask_ok(
        c,
        BUILDER,
        ANA,
        "Send this email?",
        shows=["email:e2"],
        subject="hq_reply:TK-2",
        content=email,
        task_id=row["id"],
    )
    question = H.task_ask(c, ANA, row["id"], BEN, "Which address?", seen_version=_version(c, row["id"]))
    H.task_done(c, BUILDER, row["id"], "Drafted.", _version(c, row["id"]))
    shown = K.show(c, ANA, asked["requirement_id"], email, own_click=True)
    K.give_ok(c, ANA, asked["requirement_id"], shown, own_click=True)
    K.withdraw(c, asked["requirement_id"], "The email changed")
    H.task_answer(c, BEN, row["id"], question["requirement_id"], "The office")
    assert H.task(c, row["id"])["status"] == "done"
    assert K.standing(c, asked["requirement_id"]) == K.WITHDRAWN
    with pytest.raises(Exception):
        K.use(c, asked["requirement_id"], "hq_reply:TK-2", email, BUILDER)
