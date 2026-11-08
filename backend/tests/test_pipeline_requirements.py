"""Stage requirements and pipeline settings: only the pipeline's owner and admins change them, every change keeps the
version it replaces, a no-op edit adds nothing, and a prefix is one or two letters and unique among pipelines."""

import pytest

from backend import hubdb as H
from backend import pipeline_requirements as R
from backend.store import Problem

from .test_pipeline_foundation import ANA, boot, hub, step_id

BEN = "human:ben"


def _review_stage(c, name="Pipeline"):
    dev = H.type_create(
        c, ANA, name, [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    return dev, step_id(c, dev["id"], "Review")


def _body(**changes):
    body = {
        "name": "Shows the page live",
        "kind": "attachment",
        "needs": {"formats": ["link"]},
        "reviewed_by": "bot",
        "rubric": "The link opens the page",
    }
    body.update(changes)
    return body


def test_only_the_owner_changes_rules_and_each_change_keeps_the_version_it_replaces(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev, stage = _review_stage(c)
    req = R.add(c, ANA, stage, _body())
    assert req["version"] == 1

    with pytest.raises(Problem) as refused:
        R.edit(c, BEN, req["id"], _body(rubric="Anything goes"))
    assert refused.value.status == 403

    unchanged = R.edit(c, ANA, req["id"], _body())
    assert unchanged["version"] == 1  # an edit that changes nothing adds no version

    edited = R.edit(c, ANA, req["id"], _body(rubric="The link opens the live page"))
    assert edited["version"] == 2
    versions = R.versions(c, req["id"])
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["definition"]["rubric"] == "The link opens the page"  # the version it replaced is still there


def test_a_prefix_is_one_or_two_letters_and_unique_among_pipelines(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    first, _ = _review_stage(c, "Dev")
    second, _ = _review_stage(c, "Content")

    assert R.set_prefix(c, ANA, first["id"], "ab") == "AB"
    with pytest.raises(Problem) as too_long:
        R.set_prefix(c, ANA, second["id"], "ABC")
    assert too_long.value.status == 422
    with pytest.raises(Problem) as taken:
        R.set_prefix(c, ANA, second["id"], "AB")
    assert taken.value.status == 409


def test_a_bot_reviewed_attachment_needs_a_rubric_and_a_person_reviewer_is_named(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage = _review_stage(c)
    with pytest.raises(Problem) as no_rubric:
        R.add(c, ANA, stage, _body(rubric=""))
    assert no_rubric.value.status == 422
    with pytest.raises(Problem) as no_person:
        R.add(c, ANA, stage, _body(reviewed_by="person", rubric=None))
    assert no_person.value.status == 422


def test_the_affected_count_is_the_open_tickets_at_the_stage_with_the_label_and_the_readers_visibility(tmp_path):
    """The rule editor shows how many open tickets at a stage a requirement would apply to, before it is saved. A
    labelled requirement counts only tickets with that label, and a ticket the reader cannot see is not counted."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev, stage = _review_stage(c)
    tickets = [H.task_create(c, ANA, f"Card {n}", "", ANA, type=dev["id"], step="Review", lint=False) for n in range(3)]
    c.execute(
        "INSERT INTO tags (id, key, label, created_at, updated_at) VALUES ('tg1', 'urgent', 'Urgent', ?, ?)",
        (H.now(), H.now()),
    )
    c.execute("INSERT INTO task_tags (task_id, tag_id) VALUES (?, 'tg1')", (tickets[0]["id"],))

    everything = R.affected_count(c, stage, None, "1")
    urgent = R.affected_count(c, stage, "urgent", "1")
    hidden = R.affected_count(c, stage, None, "0")

    assert everything == {"stage": "Review", "label": None, "count": 3}
    assert urgent == {"stage": "Review", "label": "urgent", "count": 1}
    assert hidden["count"] == 0


def test_a_field_or_run_is_a_rule_so_a_bot_or_judge_cannot_review_it(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage = _review_stage(c)
    for kind, needs in (("field", {"field": "due"}), ("run", {"source": "github_checks"})):
        for reviewed_by in ("bot", "bot_then_person"):
            body = _body(kind=kind, needs=needs, reviewed_by=reviewed_by, rubric="Judge it")
            with pytest.raises(Problem) as refused:
                R.add(c, ANA, stage, body)
            assert refused.value.status == 422
    nobody = R.add(c, ANA, stage, _body(kind="field", needs={"field": "due"}, reviewed_by="nobody", rubric=None))
    assert nobody["reviewed_by"] == "nobody"


def test_editing_a_subtasks_run_keeps_the_pipeline_it_names(tmp_path):
    """r5: check_definition rebuilt a run's needs as {source, at_head}, so an edit through the editor dropped the named
    pipeline and the run counted every spawned pipeline again (Marketing meeting Engineering shipped)."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    _, stage = _review_stage(c, "Plans")
    target, _ = _review_stage(c, "Builds")
    run = {"name": "Shipped", "kind": "run", "reviewed_by": "nobody"}
    made = R.add(c, ANA, stage, {**run, "needs": {"source": "subtasks", "pipeline": target["id"]}})
    assert made["needs"]["pipeline"] == target["id"]
    assert R.versions(c, made["id"])[0]["definition"]["needs"]["pipeline"] == target["id"]
    edited = R.edit(c, ANA, made["id"], {**run, "name": "Shipped it", "needs": {"source": "subtasks"}})
    assert edited["needs"]["pipeline"] == target["id"]
    with pytest.raises(Problem):
        R.add(c, ANA, stage, {**run, "name": "Other", "needs": {"source": "github_checks", "pipeline": target["id"]}})
    with pytest.raises(Problem):
        R.add(c, ANA, stage, {**run, "name": "Gone", "needs": {"source": "subtasks", "pipeline": "no-such-pipeline"}})


def test_a_ticket_closed_off_a_pipeline_with_no_done_stage_is_still_held_when_marked_done(tmp_path):
    """Codex r6 blocker 1: on a pipeline with no Done stage a Close clears the ticket's stage, and check_completion read
    only the current stage, so `status=done` then shipped it past a waiting approval. The stages it visited still hold it."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    build = H.type_create(c, ANA, "Build only", [{"name": "Build", "status": "doing"}], mover=True)
    stage = step_id(c, build["id"], "Build")
    R.add(
        c,
        ANA,
        stage,
        {"name": "Ana signs off", "kind": "approval", "needs": {}, "reviewed_by": "person", "reviewer_id": ANA},
    )
    ticket = H.task_create(c, ANA, "Card", "", ANA, type=build["id"], step="Build", lint=False)
    c.commit()
    with pytest.raises(H.Refused):
        H.task_update(c, ANA, ticket["id"], status="done")
    c.rollback()
    H.task_close(c, ANA, ticket["id"], note="Dropped")
    c.commit()
    with pytest.raises(H.Refused):
        H.task_update(c, ANA, ticket["id"], status="done")
    c.rollback()
    assert H.task(c, ticket["id"])["status"] == "closed"
