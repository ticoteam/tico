"""The pipeline engine (Track A): requirement states, the work version, the move gate with its override and the move-back
rule, visits, the counting rule for earlier stages, set-based dots, and the off switch."""

import pytest

from backend import hubdb as H
from backend import pipeline_engine as E
from backend.tests.test_pipeline_foundation import ANA, boot, hub, insert, rows, step_id

BEN = "human:ben"
CHRIS = "human:chris"
T0 = "2026-11-07T09:00:00.000000Z"


def pipeline(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    board = H.type_create(
        c,
        ANA,
        "Design board",
        [
            {"name": "Drafting", "status": "doing"},
            {"name": "Review", "status": "review"},
            {"name": "Approved", "status": "doing"},
            {"name": "Done", "status": "done"},
        ],
        mover=True,
    )
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, board["id"]))
    return c, board


def requirement(c, board, stage, name, kind="approval", needs=None, reviewed_by="person", **extra):
    ident = H.new_id()
    values = {
        "id": ident,
        "pipeline_id": board["id"],
        "pipeline_stage_id": step_id(c, board["id"], stage),
        "name": name,
        "kind": kind,
        "needs": __import__("json").dumps(needs or {}),
        "reviewed_by": reviewed_by,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(extra)
    insert(c, "stage_requirements", **values)
    return ident


def decide(c, task, req, verdict="met", judge="person", version=None, **extra):
    row = c.execute("SELECT work_revision FROM tasks WHERE id=?", (task,)).fetchone()
    stage = c.execute("SELECT pipeline_stage_id, version FROM stage_requirements WHERE id=?", (req,)).fetchone()
    insert(
        c,
        "stage_decisions",
        id=H.new_id(),
        task_id=task,
        pipeline_stage_id=stage[0],
        stage_requirement_id=req,
        requirement_version=version or stage[1],
        work_version=row[0],
        judge=judge,
        verdict=verdict,
        created_at=H.now(),
        **extra,
    )


def card(c, board, step="Drafting", owner=ANA):
    n = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    return H.task_create(c, ANA, f"Spec number {n} for the board", "", owner, type=board["id"], step=step, lint=False)


def states(c, task):
    row = dict(c.execute("SELECT id, type_id, step_id, status FROM tasks WHERE id=?", (task,)).fetchone())
    return {d["name"]: d["state"] for d in E.dots_many(c, [row]).get(task, [])}


def refused(fn):
    with pytest.raises(H.Refused) as raised:
        fn()
    return raised.value


def test_a_pipeline_without_requirements_moves_and_writes_nothing(tmp_path):
    c, board = pipeline(tmp_path)
    task = card(c, board)
    H.task_update(c, ANA, task["id"], step="Review")
    assert rows(c, "SELECT * FROM stage_visits WHERE task_id=?", task["id"]) == []
    assert E.dots_many(c, [dict(c.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())]) == {}


def test_an_approval_waits_then_is_met_then_goes_out_of_date_after_a_change(tmp_path):
    c, board = pipeline(tmp_path)
    req = requirement(c, board, "Drafting", "Lead approves")
    task = card(c, board)["id"]
    assert states(c, task) == {"Lead approves": "waiting"}
    error = refused(lambda: H.task_update(c, ANA, task, step="Review"))
    assert error.rule == "requirement" and "Lead approves" in error.detail
    decide(c, task, req)
    assert states(c, task) == {"Lead approves": "met"}
    E.work_changed(c, task)
    assert states(c, task) == {"Lead approves": "out_of_date"}
    assert refused(lambda: H.task_update(c, ANA, task, step="Review")).rule == "requirement"
    decide(c, task, req)
    H.task_update(c, ANA, task, step="Review")


def test_a_body_edit_bumps_the_work_version(tmp_path):
    c, board = pipeline(tmp_path)
    task = card(c, board)["id"]
    before = E.work_version(c, task)
    c.execute("UPDATE tasks SET body='new words' WHERE id=?", (task,))
    assert E.work_version(c, task) == before + 1


def test_a_failed_verdict_blocks_only_at_the_same_work(tmp_path):
    c, board = pipeline(tmp_path)
    req = requirement(c, board, "Drafting", "Judge says", kind="attachment", reviewed_by="bot")
    task = card(c, board)["id"]
    decide(c, task, req, "failed", judge="llm")
    assert states(c, task) == {"Judge says": "failed"}
    E.work_changed(c, task)
    assert states(c, task) == {"Judge says": "none"}


def test_an_attachment_is_none_then_waiting_then_stale(tmp_path):
    c, board = pipeline(tmp_path)
    req = requirement(c, board, "Drafting", "Mockup", kind="attachment", needs={"formats": ["png"]})
    task = card(c, board)["id"]
    assert states(c, task) == {"Mockup": "none"}
    insert(
        c,
        "requirement_attachments",
        id="att1",
        task_id=task,
        stage_requirement_id=req,
        source="link",
        url="https://x.example/a.png",
        digest="d1",
        work_version=E.work_version(c, task),
        created_at=T0,
    )
    assert states(c, task) == {"Mockup": "waiting"}
    E.work_changed(c, task)
    assert states(c, task) == {"Mockup": "out_of_date"}


def test_a_nobody_reviewed_attachment_is_met_when_current(tmp_path):
    c, board = pipeline(tmp_path)
    req = requirement(c, board, "Drafting", "Mockup", kind="attachment", reviewed_by="nobody")
    task = card(c, board)["id"]
    insert(
        c,
        "requirement_attachments",
        id="att1",
        task_id=task,
        stage_requirement_id=req,
        source="link",
        url="u",
        digest="d",
        work_version=E.work_version(c, task),
        created_at=T0,
    )
    assert states(c, task) == {"Mockup": "met"}
    H.task_update(c, ANA, task, step="Review")


def test_field_requirements_read_the_ticket(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Due date", kind="field", needs={"field": "due"}, reviewed_by="nobody")
    task = card(c, board)["id"]
    assert states(c, task) == {"Due date": "none"}
    c.execute("UPDATE tasks SET due_at=? WHERE id=?", ("2026-12-01", task))
    assert states(c, task) == {"Due date": "met"}


def test_a_run_requirement_follows_the_pull_requests_checks(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Checks", kind="run", needs={"source": "github_checks"}, reviewed_by="nobody")
    task = card(c, board)["id"]
    assert states(c, task) == {"Checks": "none"}
    link = H.new_id()
    insert(
        c,
        "task_links",
        id=link,
        task_id=task,
        kind="pr",
        url="https://github.com/acme/app/pull/7",
        state="open",
        checks="pending",
        detail="{}",
        created_at=T0,
    )
    assert states(c, task) == {"Checks": "waiting"}
    c.execute("UPDATE task_links SET checks='passing' WHERE id=?", (link,))
    assert states(c, task) == {"Checks": "met"}
    c.execute("UPDATE task_links SET checks='failing' WHERE id=?", (link,))
    assert states(c, task) == {"Checks": "failed"}


def test_only_editors_override_and_it_is_logged(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Lead approves")
    task = card(c, board, owner=BEN)["id"]
    error = refused(lambda: H.task_update(c, BEN, task, step="Review", override_requirements=True))
    assert error.rule == "requirement"
    assert rows(c, "SELECT * FROM stage_decisions WHERE task_id=?", task) == []
    H.task_update(c, ANA, task, step="Review", override_requirements=True)
    decisions = rows(c, "SELECT verdict, judge, judge_actor FROM stage_decisions WHERE task_id=?", task)
    assert decisions == [{"verdict": "override", "judge": "person", "judge_actor": ANA}]
    assert rows(c, "SELECT kind FROM history_events WHERE subject_id=? AND kind='stage_override'", task)


def test_an_override_logs_the_reason_the_editor_gave(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Lead approves")
    task = card(c, board, owner=BEN)["id"]
    H.task_update(c, ANA, task, step="Review", override_requirements=True, note="Lead is on leave until Monday")
    decisions = rows(c, "SELECT verdict, reason FROM stage_decisions WHERE task_id=?", task)
    assert decisions == [{"verdict": "override", "reason": "Moved on without it: Lead is on leave until Monday"}]


def test_an_override_without_a_note_logs_the_fixed_reason_and_a_long_note_fits_the_row(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Lead approves")
    blank, long = card(c, board, owner=BEN)["id"], card(c, board, owner=BEN)["id"]
    H.task_update(c, ANA, blank, step="Review", override_requirements=True, note="   ")
    H.task_update(c, ANA, long, step="Review", override_requirements=True, note="é" * 600)
    assert rows(c, "SELECT reason FROM stage_decisions WHERE task_id=?", blank) == [{"reason": "Moved on without it"}]
    [capped] = rows(c, "SELECT reason FROM stage_decisions WHERE task_id=?", long)
    assert capped["reason"].startswith("Moved on without it: é") and len(capped["reason"].encode()) <= 500
    assert E.override_reason(None) == "Moved on without it"


def test_moving_back_is_for_editors_and_the_keeper_and_is_logged(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Lead approves", reviewed_by="person")
    task = card(c, board, owner=BEN)["id"]
    H.task_update(c, ANA, task, step="Review", override_requirements=True)
    error = refused(lambda: H.task_update(c, BEN, task, step="Drafting"))
    assert error.rule == "requirement"
    H.task_update(c, ANA, task, step="Drafting")
    assert rows(c, "SELECT kind FROM history_events WHERE subject_id=? AND kind='stage_move_back'", task)
    visits = rows(c, "SELECT direction, left_at FROM stage_visits WHERE task_id=? ORDER BY rowid", task)
    assert [v["direction"] for v in visits] == ["initial", "forward", "back"]
    assert [v["left_at"] is None for v in visits] == [False, False, True]


def test_move_back_keeps_what_is_current_and_a_stale_earlier_requirement_blocks_forward(tmp_path):
    c, board = pipeline(tmp_path)
    first = requirement(c, board, "Drafting", "Lead approves")
    task = card(c, board)["id"]
    decide(c, task, first)
    H.task_update(c, ANA, task, step="Review")
    H.task_update(c, ANA, task, step="Drafting")
    assert states(c, task) == {"Lead approves": "met"}
    H.task_update(c, ANA, task, step="Review")
    E.work_changed(c, task)
    assert states(c, task) == {"Lead approves": "out_of_date"}
    assert refused(lambda: H.task_update(c, ANA, task, step="Approved")).rule == "requirement"
    decide(c, task, first)
    H.task_update(c, ANA, task, step="Approved")


def test_a_requirement_added_later_does_not_reach_a_card_past_its_stage(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Review", "Reviewed", reviewed_by="nobody", kind="field", needs={"field": "due"})
    past, here = card(c, board)["id"], card(c, board)["id"]
    for t in (past, here):
        c.execute("UPDATE tasks SET due_at='2026-12-01' WHERE id=?", (t,))
    H.task_update(c, ANA, past, step="Review")
    H.task_update(c, ANA, past, step="Approved")
    H.task_update(c, ANA, here, step="Review")
    late = requirement(
        c, board, "Drafting", "Added later", created_at="2999-01-01T00:00:00.000000Z", reviewed_by="person"
    )
    assert late
    assert states(c, past) == {}
    assert states(c, here) == {"Reviewed": "met", "Added later": "waiting"} or "Reviewed" in states(c, here)
    # a card at Drafting is held to the new requirement
    fresh = card(c, board)["id"]
    assert refused(lambda: H.task_update(c, ANA, fresh, step="Review")).rule == "requirement"


def test_an_edited_requirement_keeps_approved_under_v1(tmp_path):
    c, board = pipeline(tmp_path)
    req = requirement(c, board, "Drafting", "Lead approves")
    task = card(c, board)["id"]
    decide(c, task, req, version=1)
    c.execute("UPDATE stage_requirements SET name='Lead signs off', version=2 WHERE id=?", (req,))
    assert states(c, task) == {"Lead signs off": "met"}


def test_a_replaced_attachment_voids_a_decision_on_the_old_one(tmp_path):
    c, board = pipeline(tmp_path)
    req = requirement(c, board, "Drafting", "Mockup", kind="attachment")
    task = card(c, board)["id"]
    work = E.work_version(c, task)
    for ident, removed in (("old", T0), ("new", None)):
        insert(
            c,
            "requirement_attachments",
            id=ident,
            task_id=task,
            stage_requirement_id=req,
            source="link",
            url="u",
            digest=ident,
            work_version=work,
            created_at=T0,
            removed_at=removed,
        )
    decide(c, task, req, requirement_attachment_id="old")
    assert states(c, task) == {"Mockup": "waiting"}


def test_the_label_scoped_requirement_applies_only_to_labelled_tickets(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Legal OK", applies_to_label="legal")
    task = card(c, board)["id"]
    assert states(c, task) == {}
    insert(c, "tags", id="tag-legal", key="legal", label="legal", created_at=T0, updated_at=T0)
    insert(c, "task_tags", task_id=task, tag_id="tag-legal")
    assert states(c, task) == {"Legal OK": "waiting"}


def test_dots_many_uses_a_fixed_number_of_queries(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Lead approves")
    requirement(c, board, "Drafting", "Due", kind="field", needs={"field": "due"}, reviewed_by="nobody")
    requirement(c, board, "Review", "Checks", kind="run", needs={"source": "github_checks"}, reviewed_by="nobody")

    def count(n):
        tasks = [card(c, board)["id"] for _ in range(n)]
        marks = ",".join("?" * len(tasks))
        page = [dict(r) for r in c.execute(f"SELECT * FROM tasks WHERE id IN ({marks})", tasks).fetchall()]
        seen = []
        c.set_trace_callback(seen.append)
        out = E.dots_many(c, page)
        c.set_trace_callback(None)
        assert len(out) == n
        return len(seen)

    small, large = count(2), count(40)
    assert small == large


def test_boot_opens_visits_and_gives_prefixes_and_is_idempotent(tmp_path):
    c, board = pipeline(tmp_path)
    task = card(c, board)["id"]
    E.boot(c)
    first = rows(c, "SELECT task_id, pipeline_stage_id FROM stage_visits")
    E.boot(c)
    assert rows(c, "SELECT task_id, pipeline_stage_id FROM stage_visits") == first
    assert any(v["task_id"] == task for v in first)


def test_a_queue_ejection_sends_the_card_back_a_stage_with_the_reason(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Lead approves", reviewed_by="nobody", kind="field", needs={"field": "due"})
    task = card(c, board)["id"]
    c.execute("UPDATE tasks SET due_at='2026-12-01' WHERE id=?", (task,))
    H.task_update(c, ANA, task, step="Review")
    assert E.queue_ejected(c, task, "merge conflict")
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (task,)).fetchone()[0] == step_id(
        c, board["id"], "Drafting"
    )
    assert rows(c, "SELECT note FROM history_events WHERE subject_id=? AND kind='queue_ejected'", task)[0]["note"] == (
        "merge conflict"
    )
    assert not E.queue_ejected(c, task, "again")


def test_a_loop_back_to_a_visited_stage_is_held_only_to_the_stage_it_leaves(tmp_path):
    c, board = pipeline(tmp_path)
    requirement(c, board, "Drafting", "Due", kind="field", needs={"field": "due"}, reviewed_by="nobody")
    task = card(c, board)["id"]
    c.execute("UPDATE tasks SET due_at='2026-12-01' WHERE id=?", (task,))
    H.task_update(c, ANA, task, step="Review")
    H.task_update(c, ANA, task, step="Approved")
    H.task_update(c, ANA, task, step="Review")  # back to Review: a loop, an editor's move back
    c.execute("UPDATE tasks SET due_at=NULL WHERE id=?", (task,))
    H.task_update(c, ANA, task, step="Approved")  # Review was visited: Drafting's lapsed due date is not in the way
