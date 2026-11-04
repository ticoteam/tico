"""Closing or finishing a task closes open descendants, with the usual history and close permissions."""
import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

ANA, BEN, CMO, SEO = "human:ana", "human:ben", "bot:cmo", "bot:seo"


def hub(path):
    c = H.connect(path)
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cmo", "seo")},
                    {"people": [{"id": "ana", "email": "ana@acme.example"}, {"id": "ben", "email": "ben@acme.example"}]})
    return c


def make(c, actor, title, owner, parent=None):
    return H.task_create(c, actor, title, "", owner, parent_id=parent and parent["id"], lint=False)


def history(c, row):
    return [dict(r) for r in c.execute(
        "SELECT * FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY created_at,rowid", (row["id"],))]


def finish(c, parent, operation):
    if operation == "close":
        return H.task_close(c, ANA, parent["id"], note="Launch cancelled")
    if operation == "bot_close":
        return H.task_close(c, CMO, parent["id"], note="Launch cancelled")
    if operation == "done":
        return H.task_update(c, CMO, parent["id"], status="done", note="Page is live")
    return H.task_update(c, ANA, parent["id"], step=operation)


@pytest.mark.parametrize("operation", ["close", "bot_close", "done", "Shipped", "Cancelled"])
def test_finish_closes_nested_open_subtasks_and_preserves_finished_subtasks(tmp_path, operation):
    c = hub(tmp_path / "hub.db")
    board = H.type_create(c, ANA, "Launch", [{"name": "Build", "status": "doing"},
                         {"name": "Shipped", "status": "done"}, {"name": "Cancelled", "status": "closed"}],
                          numbered=True, mover=True)
    parent = H.task_create(c, CMO if operation == "bot_close" else ANA, "Launch the pricing page", "", CMO,
                           type=board["id"], step="Build", lint=False)
    copy = make(c, CMO, "Write the pricing copy", SEO, parent)
    proof = make(c, CMO, "Proofread the pricing copy", SEO, copy)
    H.task_update(c, SEO, proof["id"], status="doing")
    finished = []
    for state in ("closed", "done", "declined"):
        kid = make(c, CMO, f"Check the {state} plan", SEO, parent)
        if state == "closed":
            H.task_close(c, CMO, kid["id"], note="Already cancelled")
        else:
            H.task_update(c, SEO, kid["id"], status=state, note="Already finished")
        finished.append(kid)
    # Descendants below finished children still close, without rewriting the finished children.
    late = make(c, CMO, "Check the plan names in German", SEO, finished[0])
    before = [(H.task(c, kid["id"]), history(c, kid)) for kid in finished]
    result = finish(c, parent, operation)
    assert result["status"] == ("done" if operation in ("done", "Shipped") else "closed")
    for kid in (copy, proof, late):
        after = H.task(c, kid["id"])
        assert after["status"] == "closed"
        assert after["note"] == f"Closed with #{parent['number']}"
        records = history(c, kid)
        assert any(e["kind"] == "status" and e["new"] == "closed" and e["note"] == after["note"] for e in records)
        assert any(e["kind"] == "closed_with" and e["new"] == parent["id"] for e in records)
        closed = c.execute("SELECT detail FROM events WHERE action='task.close' AND target=?", (kid["id"],)).fetchall()
        assert len(closed) == 1 and json.loads(closed[0][0])["with_parent"] == parent["id"]
        assert TR.parent_of(c, kid["id"]) == (copy["id"] if kid == proof else finished[0]["id"] if kid == late else parent["id"])
    assert [(H.task(c, kid["id"]), history(c, kid)) for kid in finished] == before
    assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.wake')='subtask_finished'").fetchone()
    H.task_update(c, ANA, parent["id"], status="open", note="One more change", mover=True)
    assert all(H.task(c, kid["id"])["status"] == "closed" for kid in (copy, proof, late))
    assert any(e["kind"] == "children" and e["new"].startswith("Still closed") for e in history(c, parent))
    c.close()


def test_a_bot_finishing_its_task_closes_a_persons_subtask_with_one_parent_notice(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Launch the pricing page", CMO)
    ask = make(c, CMO, "Approve the final copy", ANA, parent)
    H.task_update(c, CMO, parent["id"], status="done", note="Page is live")
    assert H.task(c, ask["id"])["status"] == "closed"
    told = [r[0] for r in c.execute("SELECT body FROM messages WHERE recipient_id=? AND body NOT LIKE 'New task%'", (ANA,))]
    assert told == ["Finished: Launch the pricing page\nPage is live\nClosed with it: Approve the final copy"]
    c.close()


@pytest.mark.parametrize("operation", ["close", "done"])
@pytest.mark.parametrize("private", [False, True])
def test_private_subtask_refuses_the_whole_finish_without_detaching_or_leaking_its_title(tmp_path, operation, private):
    c = hub(tmp_path / "hub.db")
    parent = H.task_create(c, ANA, "Plan the offsite", "", CMO, private=private, lint=False)
    visible = make(c, CMO, "Book the venue", SEO, parent)
    secret = H.task_create(c, CMO, "Compare confidential salaries", "", CMO,
                           parent_id=parent["id"], private=True, lint=False)
    assert not H.task_private_readable(c, ANA, secret)
    before = [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, visible, secret)]
    with pytest.raises(H.Refused, match="a private subtask") as exc:
        if operation == "close":
            H.task_close(c, ANA, parent["id"])
        else:
            H.task_update(c, ANA, parent["id"], status="done")
    assert secret["title"] not in str(exc.value)
    assert [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, visible, secret)] == before
    assert TR.parent_of(c, secret["id"]) == parent["id"]
    # A reader unrelated to the parent cannot gain close authority through its children.
    with pytest.raises(H.Refused):
        H.task_close(c, BEN, parent["id"], mover=False)
    c.close()


@pytest.mark.parametrize("operation", ["close", "done"])
def test_private_titles_never_enter_a_private_parents_records_or_notice(tmp_path, operation):
    c = hub(tmp_path / "hub.db")
    parent = H.task_create(c, CMO if operation == "close" else ANA, "Plan the offsite", "",
                           ANA if operation == "close" else CMO, private=True, lint=False)
    secret = H.task_create(c, CMO, "Compare confidential salaries", "", SEO,
                           parent_id=parent["id"], private=True, lint=False)
    assert not H.task_private_readable(c, ANA, secret)
    before = c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
    if operation == "close":
        H.task_close(c, CMO, parent["id"])
    else:
        H.task_update(c, CMO, parent["id"], status="done")
    assert H.task(c, secret["id"])["status"] == "closed"
    records = json.dumps(history(c, parent))
    notices = "\n".join(r[0] for r in c.execute("SELECT body FROM messages WHERE rowid>?", (before,)))
    detail = c.execute("SELECT detail FROM events WHERE target=? AND action=? ORDER BY rowid DESC LIMIT 1",
                       (parent["id"], "task.close" if operation == "close" else "task.update")).fetchone()[0]
    for text in (records, notices, detail):
        assert secret["title"] not in text
        assert "a private subtask" in text
    c.close()


def test_a_refusal_names_a_private_descendant_only_to_its_reader(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = H.task_create(c, ANA, "Plan the offsite", "", CMO, private=True, lint=False)
    secret = H.task_create(c, ANA, "Compare confidential salaries", "", CMO,
                           parent_id=parent["id"], private=True, lint=False)
    assert H.task_private_readable(c, CMO, secret)
    with pytest.raises(H.Refused) as exc:
        H.task_update(c, CMO, parent["id"], status="done")
    assert secret["title"] in str(exc.value)
    c.close()


@pytest.mark.parametrize("operation", ["done", "Cancelled"])
def test_refused_finish_is_atomic_on_a_direct_db_call(tmp_path, operation):
    c = hub(tmp_path / "hub.db")
    board = H.type_create(c, ANA, "Launch", [{"name": "Build", "status": "doing"},
                          {"name": "Shipped", "status": "done"}, {"name": "Cancelled", "status": "closed"}],
                          mover=True)
    parent = H.task_create(c, ANA, "Plan the launch", "Original body", CMO,
                           type=board["id"], step="Build", lint=False)
    make(c, ANA, "Approve the launch", SEO, parent)
    before, records = H.task(c, parent["id"]), history(c, parent)
    assert before["number"] is None
    with pytest.raises(H.Refused):
        H.task_update(c, CMO, parent["id"], number=1234, mover=True, note="Shipped", body="Changed body",
                      title="Launch the new page", labels=["launch"], verdict="approve", verdict_head="abc1234",
                      **({"status": "done"} if operation == "done" else {"step": operation}))
    assert H.task(c, parent["id"]) == before
    assert history(c, parent) == records
    assert not c.execute("SELECT 1 FROM task_tags WHERE task_id=?", (parent["id"],)).fetchone()
    c.close()


@pytest.mark.parametrize("operation", ["close", "done", "Cancelled"])
@pytest.mark.parametrize("strict", [True, False])
def test_bot_requested_steps_only_descendant_obeys_the_direct_close_check(tmp_path, operation, strict):
    c = hub(tmp_path / "hub.db")
    board = H.type_create(c, ANA, "Launch", [{"name": "Build", "status": "doing"},
                          {"name": "Cancelled", "status": "closed"}], mover=True)
    steps = H.type_create(c, ANA, "Approval", [{"name": "Check", "status": "doing"},
                          {"name": "Accepted", "status": "closed"}], steps_only=True, mover=True)
    parent = H.task_create(c, CMO, "Plan the launch", "Original body", CMO,
                           type=board["id"], step="Build", lint=False)
    own = make(c, CMO, "Draft the launch copy", SEO, parent)
    child = H.task_create(c, CMO, "Check the launch copy", "", SEO, parent_id=parent["id"],
                          type=steps["id"], step="Check", lint=False)
    deep = make(c, CMO, "Check the launch links", SEO, child)
    before = [(H.task(c, t["id"]), history(c, t)) for t in (parent, own, child, deep)]
    refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]

    def complete():
        if operation == "close":
            return H.task_close(c, CMO, parent["id"], note="Cancelled", strict=strict)
        return H.task_update(c, CMO, parent["id"], note="Shipped", number=1234, body="Changed body",
                             labels=["launch"], mover=True, strict=strict,
                             **({"status": "done"} if operation == "done" else {"step": operation}))

    if strict:
        with pytest.raises(H.Refused, match="The open subtask .*changes status only by moving it to a step") as exc:
            complete()
        assert child["title"] in exc.value.detail
        assert [(H.task(c, t["id"]), history(c, t)) for t in (parent, own, child, deep)] == before
        assert not c.execute("SELECT 1 FROM task_tags WHERE task_id=?", (parent["id"],)).fetchone()
    else:
        complete()
        assert H.task(c, parent["id"])["status"] == ("done" if operation == "done" else "closed")
        assert all(H.task(c, t["id"])["status"] == "closed" for t in (own, deep))
        assert (H.task(c, child["id"]), history(c, child)) == before[2]
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals
        assert any(e["kind"] == "children" and child["title"] in e["new"] and e["new"].startswith("Still open:")
                   for e in history(c, parent))
        notices = c.execute("SELECT recipient_id FROM messages WHERE json_extract(refs,'$.task')=? "
                            "AND json_extract(refs,'$.finish_left_open')=?", (child["id"], parent["id"])).fetchall()
        assert [r[0] for r in notices] == [CMO]
    c.close()


@pytest.mark.parametrize("operation", ["close", "done", "Cancelled"])
def test_automatic_finishes_close_permitted_descendants_and_wake_left_open_requesters_once(tmp_path, operation):
    c = hub(tmp_path / "hub.db")
    board = H.type_create(c, ANA, "Launch", [{"name": "Build", "status": "doing"},
                          {"name": "Cancelled", "status": "closed"}], mover=True)
    parent = H.task_create(c, CMO, "Plan the launch", "", CMO, type=board["id"], step="Build", lint=False)
    own = make(c, CMO, "Draft the launch copy", SEO, parent)
    other = make(c, ANA, "Approve the launch copy", CMO, parent)
    secret = H.task_create(c, ANA, "Compare confidential salaries", "", SEO,
                           parent_id=parent["id"], private=True, lint=False)
    # Traversal must continue below a descendant the actor cannot close.
    deep = make(c, CMO, "Check the launch links", SEO, other)
    skipped_before = [(H.task(c, t["id"]), history(c, t)) for t in (other, secret)]
    refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    for _ in range(2):
        if operation == "close":
            H.task_close(c, CMO, parent["id"], strict=False)
        else:
            H.task_update(c, CMO, parent["id"], strict=False,
                          **({"status": "done"} if operation == "done" else {"step": operation}))
    assert H.task(c, parent["id"])["status"] == ("done" if operation == "done" else "closed")
    assert all(H.task(c, t["id"])["status"] == "closed" for t in (own, deep))
    assert [(H.task(c, t["id"]), history(c, t)) for t in (other, secret)] == skipped_before
    assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals
    records = json.dumps(history(c, parent))
    assert "Still open: " in records and other["title"] in records and "a private subtask" in records
    assert secret["title"] not in records
    for t in (other, secret):
        notices = c.execute("SELECT id,recipient_id FROM messages WHERE json_extract(refs,'$.task')=? "
                            "AND json_extract(refs,'$.finish_left_open')=?", (t["id"], parent["id"])).fetchall()
        assert len(notices) == 1 and notices[0]["recipient_id"] == ANA
    c.close()


@pytest.mark.parametrize("status", ["open", "review", "waiting", "ready"])
def test_idle_cascaded_children_send_no_notices_to_their_parties(tmp_path, status):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Plan the launch", CMO)
    child = make(c, CMO, "Draft the launch copy", SEO, parent)
    if status != "open":
        H.task_update(c, H.KEEPER if status == "ready" else SEO, child["id"], status=status, note="Ready for the next step")
    before = c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
    H.task_close(c, ANA, parent["id"])
    assert H.task(c, child["id"])["status"] == "closed"
    assert not c.execute("SELECT 1 FROM messages WHERE rowid>? AND json_extract(refs,'$.task')=?",
                         (before, child["id"])).fetchone()
    c.close()


def test_idle_cascade_withdraws_questions_and_approvals_without_notifying_the_childs_bot(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Plan the launch", CMO)
    child = make(c, CMO, "Draft the launch copy", SEO, parent)
    asked = H.task_comment(c, SEO, child["id"], "Which launch date?", wake=False,
                           ask={"who": ANA})
    approval = H.approval_request(c, SEO, "merge", {"repo": "acme/docs", "pr": 12}, task_id=child["id"])
    H.task_update(c, SEO, child["id"], status="review", note="Ready to review")
    before = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
    H.task_close(c, ANA, parent["id"])
    assert H.message(c, asked["id"])["superseded_at"]
    assert H.approval(c, approval["id"])["decision"] == "withdrawn"
    assert not c.execute("SELECT 1 FROM messages WHERE rowid>? AND recipient_id=?", (before, SEO)).fetchone()
    c.close()


@pytest.mark.parametrize("operation", ["close", "done"])
def test_cascade_bumps_child_version_and_rejects_a_prepared_update(api, operation):
    parent = post(api, "tasks", {"owner": "ops", "title": "Plan the launch", "body": "x"})
    child = post(api, "tasks", {"owner": "cpo", "title": "Draft the launch copy", "body": "x",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    post(api, "tasks/" + parent["id"], {"version": parent["version"],
         **({"close": True} if operation == "close" else {"status": "done"})})
    after = get(api, "tasks/" + child["id"])["task"]
    assert after["status"] == "closed" and after["version"] == child["version"] + 1
    stale = post(api, "tasks/" + child["id"], {"version": child["version"], "status": "doing"}, expected=409)
    assert stale["error"]["code"] == "version_conflict"
    assert get(api, "tasks/" + child["id"])["task"] == after


@pytest.mark.parametrize("run_state", ["leased", "running", "input"])
def test_a_cascaded_child_with_a_live_run_gets_the_direct_close_notice(api, run_state):
    from backend.tests.test_api import assign, claim, ready, runner

    machine = runner(api)
    assign(api, machine, "cpo")
    ready(api, machine, ["cpo"])
    with api.app.state.store.transaction() as c:
        # The owner is also on the parent: its live run must still hear the child close.
        parent = H.task_create(c, "bot:cpo", "Plan the launch", "", "bot:ops", lint=False)
        child = H.task_create(c, "bot:ops", "Draft the launch copy", "", "bot:cpo",
                              parent_id=parent["id"], lint=False)
    attempt = claim(api, machine, "cpo")
    # Only the child belongs to this computer's bot.
    assert attempt["task"]["id"] == child["id"]
    if run_state != "leased":
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "child-thread"}, machine["token"])
    with api.app.state.store.transaction() as c:
        if run_state == "input":
            c.execute("UPDATE attempts SET state='input' WHERE id=?", (attempt["id"],))
            c.execute("UPDATE jobs SET state='input' WHERE attempt_id=?", (attempt["id"],))
        before = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_close(c, "human:ana", parent["id"], mover=True)
        notices = [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE rowid>? AND json_extract(refs,'$.task')=?", (before, child["id"]))]
        if run_state == "input":
            assert notices == []
        else:
            assert {m["recipient_id"] for m in notices} == {"bot:ops", "bot:cpo"}
            assert len(notices) == 2
            assert all(m["body"] == f"Closed: {child['title']}\nClosed with its parent task" for m in notices)
            owner_notice = next(m for m in notices if m["recipient_id"] == "bot:cpo")
            assert c.execute("SELECT 1 FROM jobs WHERE message_id=?", (owner_notice["id"],)).fetchone()


def test_a_runner_goal_finish_keeps_its_event_batch_and_final_result_with_unclosable_children(api):
    from backend.tests.test_api import claim
    from backend.tests.test_chat_goals import task_goal_world

    machine, parent = task_goal_world(api)
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "bot:ops", "Draft the launch copy", "", "bot:cpo",
                            parent_id=parent["id"], lint=False)
        other = H.task_create(c, "human:ana", "Approve the launch copy", "", "bot:cpo",
                              parent_id=parent["id"], lint=False)
        secret = H.task_create(c, "bot:cpo", "Compare confidential salaries", "", "bot:cpo",
                               parent_id=other["id"], private=True, lint=False)
        typ = H.type_create(c, ANA, "Approval", [{"name": "Check", "status": "doing"},
                            {"name": "Accepted", "status": "closed"}], steps_only=True, mover=True)
        steps = H.task_create(c, "bot:ops", "Check the launch links", "", "bot:cpo", parent_id=parent["id"],
                              type=typ["id"], step="Check", lint=False)
        steps_before = H.task(c, steps["id"]), history(c, steps)
        refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "goal-thread"}, machine["token"])
    goal = attempt["chat_goal"]
    events = {"events": [
        {"seq": 1, "kind": "message", "payload": {"text": "Runner output", "final": True}},
        {"seq": 2, "kind": "goal", "payload": {"goal_id": goal["id"], "revision": goal["updated_at"],
                                               "status": "met", "note": "The summary is in Docs."}}]}
    for _ in range(2):
        assert post(api, f"attempts/{attempt['id']}/events", events, machine["token"])["ack_seq"] == 2
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "text": "Final runner result", "last_seq": 2}, machine["token"])
    with api.app.state.store.read() as c:
        assert H.task(c, parent["id"])["status"] == "done"
        assert H.task(c, own["id"])["status"] == "closed"
        assert H.task(c, other["id"])["status"] == H.task(c, secret["id"])["status"] == "open"
        assert (H.task(c, steps["id"]), history(c, steps)) == steps_before
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals
        run = c.execute("SELECT state,final_text FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        assert tuple(run) == ("completed", "Final runner result")
        assert c.execute("SELECT 1 FROM messages WHERE body='Final runner result'").fetchone()
        notices = [dict(r) for r in c.execute("SELECT * FROM messages WHERE json_extract(refs,'$.finish_left_open')=?",
                                             (parent["id"],))]
        assert len(notices) == 3
        bot_notice = next(m for m in notices if m["recipient_id"] == "bot:cpo")
        assert c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (bot_notice["id"],)).fetchone()[0] == 1
        records = json.dumps(history(c, parent))
        assert "Still open:" in records and secret["title"] not in records
        assert steps["title"] in records


@pytest.mark.parametrize("operation", ["close", "done", "Cancelled"])
def test_cascade_unblocks_outside_work_without_waking_a_sibling_being_closed(api, operation):
    with api.app.state.store.transaction() as c:
        board = H.type_create(c, ANA, "Launch", [{"name": "Build", "status": "doing"},
                              {"name": "Cancelled", "status": "closed"}], mover=True)
        parent = H.task_create(c, ANA, "Plan the launch", "", "bot:ops", type=board["id"], step="Build", lint=False)
        blocker = make(c, "bot:ops", "Draft the launch copy", "bot:cpo", parent)
        sibling = make(c, "bot:ops", "Check the launch copy", "bot:cpo", parent)
        outside = make(c, ANA, "Publish the release notes", "bot:cpo")
        # Exercise the blocker closing first, while the sibling is still active.
        c.execute("UPDATE tasks SET created_at='2020-01-01T00:00:00Z' WHERE id=?", (blocker["id"],))
        for dependent in (sibling, outside):
            TR.add_blocker(c, ANA, dependent["id"], blocker["id"])
        before = c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
        if operation == "close":
            H.task_close(c, ANA, parent["id"])
        else:
            H.task_update(c, ANA, parent["id"], **({"status": "done"} if operation == "done" else {"step": operation}))
        assert all(H.task(c, t["id"])["status"] == "closed" for t in (blocker, sibling))
        assert H.task(c, outside["id"])["status"] in H.ACTIVE_STATUSES
        assert all(TR.blocker_ids(c, t["id"]) == [] for t in (sibling, outside))
        notices = [dict(r) for r in c.execute("SELECT * FROM messages WHERE rowid>? AND body LIKE 'Unblocked: %'", (before,))]
        assert len(notices) == 1
        assert notices[0]["recipient_id"] == "bot:cpo"
        assert json.loads(notices[0]["refs"])["task"] == outside["id"]
        assert c.execute("SELECT count(*) FROM jobs WHERE message_id=?", (notices[0]["id"],)).fetchone()[0] == 1


@pytest.mark.parametrize("status", ["done", "closed"])
def test_direct_automatic_status_writes_apply_partial_descendant_completion(tmp_path, status):
    c = hub(tmp_path / "hub.db")
    parent = make(c, CMO, "Plan the launch", CMO)
    own = make(c, CMO, "Draft the launch copy", SEO, parent)
    other = make(c, ANA, "Approve the launch copy", SEO, parent)
    before = H.task(c, other["id"]), history(c, other)
    H._set_status(c, CMO, H.task(c, parent["id"]), status=status)
    assert H.task(c, parent["id"])["status"] == status
    assert H.task(c, own["id"])["status"] == "closed"
    assert (H.task(c, other["id"]), history(c, other)) == before
    assert any(e["kind"] == "children" and e["new"] == "Still open: " + other["title"] for e in history(c, parent))
    notices = c.execute("SELECT recipient_id FROM messages WHERE json_extract(refs,'$.finish_left_open')=?",
                        (parent["id"],)).fetchall()
    assert [r[0] for r in notices] == [ANA]
    c.close()


def test_roadmap_automatic_completion_closes_ordinary_descendants(api):
    parent = post(api, "tasks", {"owner": "cpo", "title": "Improve exports", "body": "Spec.",
                                "type": "Product roadmap", "step": "Approved"})
    engineering = post(api, "tasks", {"owner": "ops", "title": "Build exports", "body": "Implement exports.",
                                     "type": "Engineering", "relations": [{"task": parent["id"], "kind": "parent"}]})
    with api.app.state.store.transaction() as c:
        child = make(c, "bot:cpo", "Review the export guide", "bot:ops", parent)
        deep = make(c, "bot:ops", "Check the export examples", "bot:cpo", child)
    post(api, "tasks/" + engineering["id"], {"version": engineering["version"], "close": True})
    with api.app.state.store.read() as c:
        assert H.task(c, parent["id"])["status"] == "done"
        assert all(H.task(c, t["id"])["status"] == "closed" for t in (child, deep))
        for t in (child, deep):
            assert any(e["kind"] == "closed_with" and e["new"] == parent["id"] for e in history(c, t))
        assert any(e["kind"] == "children" and child["title"] in e["new"] for e in history(c, parent))


@pytest.mark.parametrize("error", [H.Refused("close", "State changed after preflight"), RuntimeError("Close hook failed")])
def test_automatic_finish_rolls_back_a_failed_descendant_and_its_queued_triggers(api, monkeypatch, error):
    from backend.tests.test_task_trigger_rules import rule

    with api.app.state.store.transaction() as c:
        parent = make(c, "bot:ops", "Plan the launch", "bot:ops")
        typ = H.type_create(c, ANA, "Approval", [{"name": "Check", "status": "doing"}], mover=True)
        child = H.task_create(c, "bot:ops", "Review the launch copy", "", "bot:cpo", parent_id=parent["id"],
                              type=typ["id"], lint=False)
        own = make(c, "bot:ops", "Check the launch links", "bot:cpo", parent)
        before = H.task(c, child["id"]), history(c, child)
    rule(api, "task.closed", action="create_task", params={"owner": "ops", "title": "Unexpected followup"},
         when={"type": typ["id"]})
    set_status = H._set_status

    def failing(c, actor, row, *args, **kwargs):
        result = set_status(c, actor, row, *args, **kwargs)
        if row["id"] == child["id"]:
            raise error
        return result

    monkeypatch.setattr(H, "_set_status", failing)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", parent["id"], status="done", strict=False)
        assert H.task(c, parent["id"])["status"] == "done"
        assert H.task(c, own["id"])["status"] == "closed"
        assert (H.task(c, child["id"]), history(c, child)) == before
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Unexpected followup'").fetchone()
        assert any(e["kind"] == "children" and e["new"] == "Still open: " + child["title"] for e in history(c, parent))
        notices = c.execute("SELECT recipient_id FROM messages WHERE json_extract(refs,'$.finish_left_open')=?",
                            (parent["id"],)).fetchall()
        assert [r[0] for r in notices] == ["bot:ops"]


@pytest.mark.parametrize("operation", ["close", "done"])
def test_bot_cannot_close_a_persons_subtask_and_api_finish_rolls_back(api, operation):
    _, _, attempt = setup_attempt(api, "ops")
    parent = post(api, "tasks", {"owner": "ops", "title": "Ship the refund page", "body": "x"},
                  token=attempt["token"] if operation == "close" else "ana-test")
    with api.app.state.store.transaction() as c:
        own = H.task_create(c, "bot:ops", "Draft the refund copy", "", "bot:cpo", parent_id=parent["id"], lint=False)
    child = post(api, "tasks", {"owner": "cpo", "title": "Approve the refund copy", "body": "x",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    with api.app.state.store.read() as c:
        before = [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, own, child)]
    body = {"version": parent["version"], "note": "Shipped", **({"close": True} if operation == "close" else {"status": "done"})}
    response = api.post("/api/v2/tasks/" + parent["id"], json=body, headers=headers(attempt["token"]))
    assert response.status_code >= 400, response.text
    assert child["title"] in response.text
    with api.app.state.store.read() as c:
        assert [(H.task(c, kid["id"]), history(c, kid)) for kid in (parent, own, child)] == before
        assert TR.parent_of(c, child["id"]) == parent["id"]
    version = get(api, "tasks/" + parent["id"])["task"]["version"]
    post(api, "tasks/" + parent["id"], {"version": version, **({"close": True} if operation == "close" else {"status": "done"})})
    assert get(api, "tasks/" + child["id"])["task"]["status"] == "closed"
    assert get(api, "tasks/" + own["id"])["task"]["status"] == "closed"


def test_api_has_no_detach_option(api):
    parent = post(api, "tasks", {"owner": "ops", "title": "Plan the launch", "body": "x"})
    child = post(api, "tasks", {"owner": "cpo", "title": "Review the launch plan", "body": "x",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    post(api, "tasks/" + parent["id"], {"version": parent["version"], "close": True, "children": "detach"}, expected=422)
    assert get(api, "tasks/" + parent["id"])["task"]["status"] == "open"
    post(api, "tasks/" + parent["id"], {"version": parent["version"], "close": True})
    assert get(api, "tasks/" + child["id"])["task"]["status"] == "closed"
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, child["id"]) == parent["id"]
