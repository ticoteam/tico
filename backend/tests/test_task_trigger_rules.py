"""Task Trigger rules on the events a task's moves publish (backend/task_effects.py): created, a new stage, done,
closed, reopened, assigned. Rules are rows in the rules table, written the way the engine reads them (rules made today
live in "What wakes" automations, which the engine reads the same rows from: test_redesign_migration). Synthetic team,
no live integrations."""

import json

import pytest

from backend import hubdb as H, task_trigger_rules as T
from backend.tests.helpers.api import unrelated_task_managers

ANA = "human:ana"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def rule(c, event="task.created", action="note", params=None, *, scope_kind="team", scope_id="", when=None, by=ANA):
    """A rule row as `by` would have saved it: actors named in full, it acts as its maker, last in its scope."""
    params = {"action": action, **(params or {"text": "A note"})}
    when = {k: H.resolve_actor(c, v) if k in ("owner", "requester") else v for k, v in (when or {}).items()}
    position = (
        c.execute(
            "SELECT max(position) FROM rules WHERE scope_kind=? AND scope_id=?", (scope_kind, scope_id)
        ).fetchone()[0]
        or 0
    ) + 1
    row = {
        "id": "rule-" + H.new_id(),
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "event": event,
        "condition": json.dumps(when, sort_keys=True),
        "then_outcome": "action",
        "params": json.dumps(params, sort_keys=True),
        "enabled": 1,
        "position": position,
        "note": "",
        "created_by": by,
        "action_by": by,
        "created_at": H.now(),
        "updated_at": H.now(),
        "revision": 1,
    }
    c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return row


def make(c, title="Synthetic work", owner="bot:ops", **kw):
    return H.task_create(c, ANA, title, "Synthetic body", owner, lint=False, deduplicate=False, **kw)


def history(c, tid):
    """The rule rows of a task's history: (rule id, note), oldest first."""
    return [
        (r[0], r[1])
        for r in c.execute(
            "SELECT new, note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='rule' "
            "ORDER BY rowid",
            (tid,),
        )
    ]


def fired(c, tid):
    return [rid for rid, _ in history(c, tid)]


def test_created_files_one_related_task_and_names_the_rule_in_history(c):
    made = rule(c, action="create_task", params={"owner": "cpo", "title": "Review {{task.title}}"})
    original = make(c, "Implement widget")
    review = c.execute("SELECT * FROM tasks WHERE title='Review Implement widget'").fetchall()
    assert len(review) == 1 and review[0]["owner_id"] == "bot:cpo"
    assert c.execute(
        "SELECT 1 FROM task_relations WHERE kind='related' AND "
        "((from_task=? AND to_task=?) OR (from_task=? AND to_task=?))",
        (original["id"], review[0]["id"], review[0]["id"], original["id"]),
    ).fetchone()
    assert made["id"] in history(c, original["id"])[0][1]
    T.enqueue(c, "task.created", original["id"], ANA, "created")  # the same event again runs nothing
    assert c.execute("SELECT count(*) FROM tasks WHERE title='Review Implement widget'").fetchone()[0] == 1


def test_entering_its_first_stage_is_part_of_being_filed_not_a_stage_change(c):
    rule(c, "task.step_changed")
    assert fired(c, make(c)["id"]) == []


def test_each_move_runs_the_rules_of_its_events_once(c):
    done = rule(c, "task.done", params={"text": "Finished {{task.title}}"})
    reopened = rule(c, "task.reopened")
    closed = rule(c, "task.closed")
    staged = rule(c, "task.step_changed")
    task = make(c)
    H.task_done(c, ANA, task["id"], "Shipped")
    assert H.task(c, task["id"])["status"] == "done"
    assert "Finished Synthetic work" in H.task(c, task["id"])["note"]
    H.task_done(c, ANA, task["id"], "Again")  # already done: no move, no event
    H.task_move(c, ANA, task["id"], H.TASK_REOPEN, "One more fix")
    H.task_close(c, ANA, task["id"], "Not needed after all")
    assert fired(c, task["id"]) == [staged["id"], done["id"], staged["id"], reopened["id"], staged["id"], closed["id"]]


def test_a_stage_and_a_pipeline_are_matched_by_id_or_by_name(c):
    by_name = rule(c, "task.step_changed", when={"stage": "done", "pipeline": "General"})
    by_id = rule(c, "task.step_changed", when={"stage": "general-closed", "pipeline": "general"})
    first, second = make(c, "First"), make(c, "Second")
    H.task_done(c, ANA, first["id"])
    H.task_close(c, ANA, second["id"], "Dropped")
    assert fired(c, first["id"]) == [by_name["id"]]
    assert fired(c, second["id"]) == [by_id["id"]]


def test_assigned_reads_the_new_owner_and_runs_once(c):
    made = rule(c, "task.assigned", when={"owner": "cpo"})
    other = rule(c, "task.assigned", when={"owner": "finance"})
    task = make(c)
    H.task_reassign(c, ANA, task["id"], "bot:cpo", "CPO takes it")
    H.task_reassign(c, ANA, task["id"], "bot:cpo", "Still the CPO")  # the same owner: nothing changes
    assert fired(c, task["id"]) == [made["id"]]
    assert other["id"] not in fired(c, task["id"])


def test_an_action_cannot_start_a_same_or_alternate_rule_chain(c):
    made = rule(c, action="create_task", params={"owner": "ops", "title": "Derived work"})
    other = rule(c, action="create_task", params={"owner": "cpo", "title": "Alternate work"})
    make(c)
    assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 3
    for (tid,) in c.execute("SELECT id FROM tasks WHERE title IN ('Derived work','Alternate work')").fetchall():
        assert {rid for rid, _ in history(c, tid)} == {made["id"], other["id"]}
        assert all("maximum chain depth is one" in note for _, note in history(c, tid))


def test_a_sourceless_create_action_cannot_start_a_task_trigger_chain(c):
    from backend import rules as R

    rule(c, "webhook.received", "create_task", {"owner": "ops", "title": "From webhook"})
    made = rule(c, action="create_task", params={"owner": "ops", "title": "Must not chain"})
    R.judge_event(c, "webhook.received", {"trigger": "synthetic", "delivery": "one"})
    assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    task = c.execute("SELECT id FROM tasks WHERE title='From webhook'").fetchone()
    [(rid, note)] = history(c, task[0])
    assert rid == made["id"] and "maximum chain depth is one" in note


def test_close_ask_and_relate_actions_go_through_the_task_calls(c):
    related = make(c, "Related fixture", owner="bot:finance")
    asked = rule(
        c,
        action="ask",
        params={"person": "human:ben", "line": "Is the Acme renewal still wanted?"},
        when={"owner": "ops"},
    )
    linked = rule(c, action="relate", params={"task": related["id"]}, when={"owner": "ops"})
    closed = rule(c, action="close", params={"text": "Filed by mistake"}, when={"owner": "cpo"})
    task = make(c)
    waits = H.task_waits(c, [task["id"]])[task["id"]]
    assert [(w.state, w.who) for w in waits if w.ask] == [("person", "human:ben")]
    assert c.execute(
        "SELECT 1 FROM task_relations WHERE kind='related' AND (from_task=? OR to_task=?)", (task["id"], task["id"])
    ).fetchone()
    assert [rid for rid, note in history(c, task["id"]) if "Completed" in note] == [asked["id"], linked["id"]]
    mistaken = make(c, "Mistaken work", owner="bot:cpo")
    after = H.task(c, mistaken["id"])
    assert after["status"] == "closed" and after["closed_at"]
    assert fired(c, mistaken["id"]) == [closed["id"]]


def test_a_move_action_never_pushes_past_what_the_stage_needs(c):
    done = rule(c, action="move", params={"stage": "done"}, when={"owner": "ops"})
    task = make(c)
    # Done needs the owner to say the work is finished: the rule's move is refused, the task is filed all the same.
    assert H.task(c, task["id"])["status"] == "open"
    assert T.state(c, done["id"])["last_error"] == "Action failed (Refused)"
    closed = rule(c, action="move", params={"stage": "closed", "text": "Out of scope"}, when={"owner": "cpo"})
    other = make(c, "Out of scope work", owner="bot:cpo")
    assert H.task(c, other["id"])["status"] == "closed"
    assert not T.state(c, closed["id"])["last_error"]


def test_a_missing_bot_and_a_throwing_action_keep_the_task_and_mark_the_row(c, monkeypatch):
    from backend import rules as R

    gone = rule(c, "task.done", "create_task", {"owner": "cpo", "title": "Review work"})
    broken = rule(c, "task.done", params={"text": "Must roll back"})

    def failing(conn, tid, params, said):
        H.task_update(conn, H.KEEPER, tid, note="Must roll back")
        raise RuntimeError("private content must never enter shared rule settings")

    monkeypatch.setitem(R.TASK_RUNNERS, "note", failing)
    bot = H.bot
    monkeypatch.setattr(H, "bot", lambda conn, slug: None if slug == "cpo" else bot(conn, slug))
    task = make(c)
    H.task_update(c, ANA, task["id"], note="Saved original")
    H.task_done(c, ANA, task["id"])
    after = H.task(c, task["id"])
    assert (after["status"], after["note"]) == ("done", "Saved original")
    for made in (gone, broken):
        row = T.state(c, made["id"])
        assert row["last_error_at"] and "Action failed" in row["last_error"]
        assert "private content" not in row["last_error"]


def test_a_private_task_is_read_with_the_authors_view_and_its_copy_is_private(c):
    allowed = rule(c, "task.done", "create_task", {"owner": "cpo", "title": "Private copy", "body": "{{task.body}}"})
    denied = rule(c, "task.done", "create_task", {"owner": "cpo", "title": "Must not copy"}, by="human:ben")
    unrelated_task_managers(c)
    task = make(c, private=True)
    assert not H.task_private_readable(c, "human:ben", task)
    H.task_done(c, ANA, task["id"])
    copy = dict(c.execute("SELECT * FROM tasks WHERE title='Private copy'").fetchone())
    assert copy["private"] and copy["body"] == "Synthetic body"
    assert not H.task_private_readable(c, "human:cara", copy)
    assert not c.execute("SELECT 1 FROM tasks WHERE title='Must not copy'").fetchone()
    assert T.state(c, denied["id"])["last_error_at"]
    assert not T.state(c, allowed["id"])["last_error"]


def test_a_wake_action_never_shows_a_private_task_to_a_bot_that_cannot_read_it(c):
    made = rule(c, "task.done", "wake_bot", {"bot": "cpo", "text": "Secret {{task.body}}"})
    unrelated_task_managers(c)
    task = make(c, private=True)
    assert not H.task_private_readable(c, "bot:cpo", task)
    H.task_done(c, ANA, task["id"])
    assert not c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:cpo' AND body LIKE 'Secret %'").fetchone()
    assert T.state(c, made["id"])["last_error_at"]
    assert "Action failed" in history(c, task["id"])[0][1]
    assert H.task(c, task["id"])["status"] == "done"


def test_the_fifty_first_run_in_an_hour_pauses_the_rule_and_never_refuses_the_task(c):
    made = rule(c, "task.done")
    for n in range(49):  # earlier runs in this hour
        c.execute(
            "INSERT INTO rule_task_runs(rule_id,task_id,event,revision,ran_at,fired) VALUES(?,?,?,?,?,1)",
            (made["id"], "synthetic-" + str(n), "task.done", str(n), H.now()),
        )
    task = make(c)
    for n in range(3):
        H.task_done(c, ANA, task["id"])
        if n < 2:
            H.task_move(c, ANA, task["id"], H.TASK_REOPEN, "Another pass")
    assert H.task(c, task["id"])["status"] == "done"
    assert len(history(c, task["id"])) == 2  # the last run it was allowed, then the pause
    row = T.state(c, made["id"])
    assert row["paused"] and row["paused_at"] and row["pause_reason"] == "Paused: too many runs"
    later = make(c, "Later work")
    H.task_done(c, ANA, later["id"])
    assert not history(c, later["id"])


def test_scopes_and_the_five_conditions_match_the_tasks_owner(c):
    from backend import bot_settings as BS

    H.tag_create(c, ANA, "triage", mover=True)
    roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    roster["org_groups"] = {"engineering": {"name": "Engineering"}, "web": {"name": "Web", "parent": "engineering"}}
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
    c.execute("UPDATE bot_config SET team='web' WHERE bot='ops'")
    BS.update(c, "ops", {"team": "web"})
    condition = {"pipeline": "general", "stage": "To do", "tag": "triage", "owner": "ops", "requester": "ana"}
    scoped = [
        rule(c, scope_kind=kind, scope_id=ident, when=condition)
        for kind, ident in (("team", ""), ("pipeline", "general"), ("department", "engineering"), ("bot", "ops"))
    ]
    task = make(c, labels=["triage"])
    assert fired(c, task["id"]) == [r["id"] for r in reversed(scoped)]
    assert not history(c, make(c, "Untagged work")["id"])


def test_the_department_scope_follows_a_persons_group_and_its_ancestors(c):
    roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    roster["org_groups"] = {"engineering": {"name": "Engineering"}, "web": {"name": "Web", "parent": "engineering"}}
    next(p for p in roster["people"] if p["id"] == "ben")["team"] = "web"
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
    made = rule(c, scope_kind="department", scope_id="engineering")
    assert fired(c, make(c, "Human work", owner="human:ben")["id"]) == [made["id"]]
    assert not history(c, make(c, "Outside department", owner="human:cara")["id"])
