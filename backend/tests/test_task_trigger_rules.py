"""Task Trigger acceptance, using disposable synthetic teams and no live integrations. Rules are rows in the rules
table, written the way the engine reads them (rules made today live in "What wakes" automations, which the engine
reads the same rows from: test_redesign_migration)."""
import json

import pytest

from backend import hubdb as H, task_trigger_rules as T
from backend.tests.test_api import api, headers, post, unrelated_task_managers  # noqa: F401


def rule(api, event="task.created", action="note", params=None, *, scope_kind="team", scope_id="", when=None,
         by="human:ana", then="action"):
    """A rule row as `by` would have saved it: actors named in full, it acts as its maker, last in its scope."""
    params = {"action": action, **(params or {"text": "A note"})} if then == "action" else (params or {})
    with api.app.state.store.transaction() as c:
        when = {k: H.resolve_actor(c, v) if k in ("owner", "requester") else v for k, v in (when or {}).items()}
        position = (c.execute("SELECT max(position) FROM rules WHERE scope_kind=? AND scope_id=?",
                              (scope_kind, scope_id)).fetchone()[0] or 0) + 1
        row = {"id": "rule-" + H.new_id(), "scope_kind": scope_kind, "scope_id": scope_id, "event": event,
               "condition": json.dumps(when, sort_keys=True), "then_outcome": then,
               "params": json.dumps(params, sort_keys=True), "enabled": 1, "position": position, "note": "",
               "created_by": by, "action_by": by, "created_at": H.now(), "updated_at": H.now(), "revision": 1}
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return row


def state(api, rule_id):
    with api.app.state.store.read() as c:
        return T.state(c, rule_id)


def make(c, title="Synthetic work", **kw):
    return H.task_create(c, "human:ana", title, "Synthetic body", "bot:ops", lint=False, deduplicate=False, **kw)


def history(c, tid):
    return [dict(r) for r in c.execute("SELECT * FROM task_events WHERE task_id=? AND field='rule' ORDER BY rowid", (tid,))]


def task_type(c, name="Dev tickets"):
    found = c.execute("SELECT id FROM task_types WHERE name=?", (name,)).fetchone()
    if found:
        return H.type_get(c, found[0])
    return H.type_create(c, "human:ana", name, mover=True, steps=[{"name": "Open", "status": "open"},
                         {"name": "In QA", "status": "review"}, {"name": "Done", "status": "done"}])


def test_created_creates_one_linked_review_and_names_rule_in_history(api):
    with api.app.state.store.transaction() as c:
        typ = task_type(c)
    made = rule(api, action="create_task", params={"owner": "cpo", "title": "Review {{task.title}}"},
                when={"type": typ["id"]})
    with api.app.state.store.transaction() as c:
        original = make(c, "Implement widget", type=typ["id"])
        review = c.execute("SELECT * FROM tasks WHERE title='Review Implement widget'").fetchall()
        assert len(review) == 1 and review[0]["owner_id"] == "bot:cpo"
        assert c.execute("SELECT 1 FROM task_relations WHERE kind='related' AND "
                         "((from_task=? AND to_task=?) OR (from_task=? AND to_task=?))",
                         (original["id"], review[0]["id"], review[0]["id"], original["id"])).fetchone()
        assert made["id"] in history(c, original["id"])[0]["note"]
        T.enqueue(c, "task.created", original["id"], "human:ana", "created")
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Review Implement widget'").fetchone()[0] == 1


def test_done_note_once_per_transition_and_once_more_after_reopen(api):
    with api.app.state.store.transaction() as c:
        typ = task_type(c, "Product roadmap")
    made = rule(api, "task.done", scope_kind="type", scope_id=typ["id"], params={"text": "Finished {{task.title}}"})
    with api.app.state.store.transaction() as c:
        task = make(c, type=typ["id"])
        done_step = next(s["id"] for s in typ["steps"] if s["status"] == "done")
        working_step = next(s["id"] for s in typ["steps"] if s["status"] == "doing")
        for _ in range(2):
            H.task_update(c, "human:ana", task["id"], step=done_step, note="Original write", mover=True)
            assert "Finished Synthetic work" in H.task(c, task["id"])["note"]
            H.task_update(c, "human:ana", task["id"], step=done_step, mover=True)  # no transition
            H.task_update(c, "human:ana", task["id"], step=working_step, mover=True)
        assert [r["new"] for r in history(c, task["id"])] == [made["id"], made["id"]]


def test_created_action_cannot_start_a_same_or_alternate_rule_chain(api):
    made = rule(api, action="create_task", params={"owner": "ops", "title": "Derived work"})
    other = rule(api, action="create_task", params={"owner": "cpo", "title": "Alternate work"})
    with api.app.state.store.transaction() as c:
        make(c)
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 3
        derived = c.execute("SELECT id FROM tasks WHERE title IN ('Derived work','Alternate work')").fetchall()
        for task in derived:
            events = history(c, task[0])
            assert {e["new"] for e in events} == {made["id"], other["id"]}
            assert all("maximum chain depth is one" in e["note"] for e in events)


def test_sourceless_create_action_cannot_start_a_task_trigger_chain(api):
    from backend import rules as R
    rule(api, "webhook.received", "create_task", {"owner": "ops", "title": "From webhook"})
    made = rule(api, action="create_task", params={"owner": "ops", "title": "Must not chain"})
    with api.app.state.store.transaction() as c:
        R.judge_event(c, "webhook.received", {"trigger": "synthetic", "delivery": "one"})
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        task = c.execute("SELECT id FROM tasks WHERE title='From webhook'").fetchone()
        assert history(c, task[0])[0]["new"] == made["id"]
        assert "maximum chain depth is one" in history(c, task[0])[0]["note"]


def test_missing_bot_and_throwing_action_preserve_task_and_row_error_time(api, monkeypatch):
    gone = rule(api, "task.done", "create_task", {"owner": "cpo", "title": "Review work"})
    broken = rule(api, "task.done", params={"text": "Must roll back"})
    from backend import rules as R
    def failing(c, tid, params, said):
        H.task_update(c, H.KEEPER, tid, note="Must roll back")
        raise RuntimeError("private content must never enter shared rule settings")
    monkeypatch.setitem(R.TASK_RUNNERS, "note", failing)
    bot = H.bot
    monkeypatch.setattr(H, "bot", lambda c, slug: None if slug == "cpo" else bot(c, slug))
    with api.app.state.store.transaction() as c:
        task = make(c)
        H.task_update(c, "human:ana", task["id"], status="done", note="Saved original")
        assert H.task(c, task["id"])["status"] == "done"
        assert H.task(c, task["id"])["note"] == "Saved original"
    for made in (gone, broken):
        row = state(api, made["id"])
        assert row["last_error_at"] and "Action failed" in row["last_error"]
        assert "private content" not in row["last_error"]


def test_webhook_transport_failure_reaches_rule_row_after_task_saved(api):
    from backend import webhook_delivery as D
    hook = post(api, "webhooks", {"name": "CRM", "url": "https://hooks.acme.example/crm", "events": ["custom.*"]})["webhook"]
    made = rule(api, "task.done", "send_webhook", {"webhook": hook["id"], "type": "custom.done",
                                                  "data": {"title": "{{task.title}}"}})
    with api.app.state.store.transaction() as c:
        task = make(c)
        H.task_update(c, "human:ana", task["id"], status="done")
        send = dict(c.execute("SELECT * FROM webhook_sends WHERE type='custom.done'").fetchone())
        c.execute("UPDATE webhook_sends SET state='sending' WHERE id=?", (send["id"],))
    D.record(api.app.state.store, send, H.now(), 503, "", "unavailable", False, 1)
    row = state(api, made["id"])
    assert "503" in row["last_error"] and row["last_error_at"]
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "done"


def test_private_source_uses_author_read_view_and_creates_private_copy(api):
    allowed = rule(api, "task.done", "create_task", {"owner": "cpo", "title": "Private copy", "body": "{{task.body}}"})
    denied = rule(api, "task.done", "create_task", {"owner": "cpo", "title": "Must not copy"}, by="human:ben")
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        task = make(c, private=True)
        assert not H.task_private_readable(c, "human:ben", task)
        H.task_update(c, "human:ana", task["id"], status="done")
        copy = dict(c.execute("SELECT * FROM tasks WHERE title='Private copy'").fetchone())
        assert copy["private"] and copy["body"] == "Synthetic body"
        assert not H.task_private_readable(c, "human:cara", copy)
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Must not copy'").fetchone()
        assert T.state(c, denied["id"])["last_error_at"]
        assert not T.state(c, allowed["id"])["last_error"]


def test_wake_action_cannot_disclose_private_source_to_unreadable_bot(api):
    made = rule(api, "task.done", "wake_bot", {"bot": "cpo", "text": "Secret {{task.body}}"})
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        task = make(c, private=True)
        assert H.task_private_readable(c, "human:ana", task)
        assert not H.task_private_readable(c, "bot:cpo", task)
        H.task_update(c, "human:ana", task["id"], status="done")
        assert not c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:cpo' AND body LIKE 'Secret %'").fetchone()
        assert T.state(c, made["id"])["last_error_at"]
        assert "Action failed" in history(c, task["id"])[0]["note"]
        assert H.task(c, task["id"])["status"] == "done"


def test_successful_webhook_preserves_last_failure_from_another_task(api):
    from backend import webhook_delivery as D
    hook = post(api, "webhooks", {"name": "CRM", "url": "https://hooks.acme.example/crm", "events": ["custom.*"]})["webhook"]
    made = rule(api, "task.done", "send_webhook", {"webhook": hook["id"], "type": "custom.done"})
    with api.app.state.store.transaction() as c:
        for title in ("First work", "Other work"):
            task = make(c, title)
            H.task_update(c, "human:ana", task["id"], status="done")
        sends = [dict(r) for r in c.execute("SELECT * FROM webhook_sends WHERE type='custom.done' ORDER BY rowid")]
        assert len(sends) == 2
        c.execute("UPDATE webhook_sends SET state='sending' WHERE type='custom.done'")
    D.record(api.app.state.store, sends[0], H.now(), 503, "", "unavailable", False, 1)
    before = state(api, made["id"])
    D.record(api.app.state.store, sends[1], H.now(), 200, "", "", False, 1)
    after = state(api, made["id"])
    assert "503" in after["last_error"]
    assert (after["last_error"], after["last_error_at"]) == (before["last_error"], before["last_error_at"])


def test_twenty_four_hour_close_runs_closed_actions_without_redoing_done(api, monkeypatch):
    from backend import task_rules
    done_rule = rule(api, "task.done", params={"text": "Finished"})
    closed_rule = rule(api, "task.closed", params={"text": "Accepted"})
    old = H.shift(H.now(), hours=-25)
    with monkeypatch.context() as m:
        m.setattr(H, "now", lambda: old)
        with api.app.state.store.transaction() as c:
            task = make(c)
            H.task_update(c, "human:ana", task["id"], status="done")
    failures = []
    task_rules.tick(api.app.state.store, H.parse_ts(H.now()), failures)
    assert not failures
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "closed"
        assert [r["new"] for r in history(c, task["id"])] == [done_rule["id"], closed_rule["id"]]


def test_fifty_first_attempt_pauses_persistently_without_refusing_task_changes(api):
    made = rule(api, "task.done")
    with api.app.state.store.transaction() as c:
        # Synthetic previous fires; execute the actual boundary transitions below.
        for n in range(49):
            c.execute("INSERT INTO rule_task_runs(rule_id,task_id,event,revision,ran_at,fired) VALUES(?,?,?,?,?,1)",
                      (made["id"], "synthetic-" + str(n), "task.done", str(n), H.now()))
        task = make(c)
        for _ in range(3):
            H.task_update(c, "human:ana", task["id"], status="doing")
            H.task_update(c, "human:ana", task["id"], status="done")
        assert H.task(c, task["id"])["status"] == "done"
        assert len(history(c, task["id"])) == 2  # final allowed Action and pause
    row = state(api, made["id"])
    assert row["paused"] and row["paused_at"] and row["pause_reason"] == "Paused: too many runs"
    with api.app.state.store.transaction() as c:      # paused: the next task finishing runs nothing
        later = make(c, "Later work")
        H.task_update(c, "human:ana", later["id"], status="done")
        assert not history(c, later["id"])


@pytest.mark.parametrize("placement", [None, "Open", "In QA"])
def test_initial_step_follows_created_once_including_default_and_explicit_first(api, placement):
    with api.app.state.store.transaction() as c:
        typ = task_type(c)
    created = rule(api)
    entered = rule(api, "task.step_changed", when={"step": placement or "Open"}, params={"text": "Entered"})
    with api.app.state.store.transaction() as c:
        task = make(c, type=typ["id"], step=placement)
        assert [e["new"] for e in history(c, task["id"])] == [created["id"], entered["id"]]
        assert "Entered" in task["note"]


def test_scopes_and_all_five_conditions_match_task_owner_not_message_recipient(api):
    with api.app.state.store.transaction() as c:
        typ = task_type(c)
        H.tag_create(c, "human:ana", "triage", mover=True)
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["org_groups"] = {"engineering": {"name": "Engineering"}, "web": {"name": "Web", "parent": "engineering"}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        c.execute("UPDATE bot_config SET team='web',config=json_set(config,'$.team','web') WHERE bot='ops'")
    condition = {"type": typ["id"], "step": "Open", "tag": "triage", "owner": "ops", "requester": "ana"}
    scoped = [rule(api, scope_kind=kind, scope_id=ident, when=condition) for kind, ident in
              (("team", ""), ("type", typ["id"]), ("department", "engineering"), ("bot", "ops"))]
    with api.app.state.store.transaction() as c:
        task = make(c, type=typ["id"], labels=["triage"])
        assert [e["new"] for e in history(c, task["id"])] == [r["id"] for r in reversed(scoped)]
        unmatched = make(c, "Untagged work", type=typ["id"])
        assert not history(c, unmatched["id"])


def test_decline_and_step_action_runs_only_after_final_task_write(api):
    with api.app.state.store.transaction() as c:
        typ = task_type(c)
        task = make(c, type=typ["id"])
    moved = rule(api, "task.declined", "move_step", {"step": "In QA"})
    rule(api, "task.step_changed", when={"step": "In QA"}, params={"text": "Must not chain"})
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", task["id"], status="declined", note="Declined")
        after = H.task(c, task["id"])
        assert after["status"] == "review" and after["step_id"] == next(s["id"] for s in typ["steps"] if s["name"] == "In QA")
        assert moved["id"] in after["note"] and "Must not chain" not in after["note"]


def test_department_scope_follows_human_owner_group_and_ancestors(api):
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["org_groups"] = {"engineering": {"name": "Engineering"}, "web": {"name": "Web", "parent": "engineering"}}
        next(p for p in roster["people"] if p["id"] == "ben")["team"] = "web"
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
    made = rule(api, scope_kind="department", scope_id="engineering")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Human work", "Synthetic body", "human:ben", lint=False, deduplicate=False)
        other = H.task_create(c, "human:ana", "Outside department", "Synthetic body", "human:cara", lint=False, deduplicate=False)
        assert [h["new"] for h in history(c, task["id"])] == [made["id"]]
        assert not history(c, other["id"])


def test_wait_link_wake_and_close_actions_use_existing_domain_paths(api):
    with api.app.state.store.transaction() as c:
        related = make(c, "Related fixture")
    made = [rule(api, action=action, params=params) for action, params in (
        ("set_wait", {"blocked_by": related["id"]}),
        ("relate", {"task": related["id"]}),
        ("wake_bot", {"bot": "cpo", "text": "Read {{task.title}}"}),
        ("close", {}),
    )]
    with api.app.state.store.transaction() as c:
        task = make(c)
        assert task["status"] == "closed" and task["closed_at"]
        assert [h["new"] for h in history(c, task["id"])] == [r["id"] for r in made]
        for h in history(c, task["id"]):
            assert "Completed" in h["note"], h["note"]
        assert c.execute("SELECT 1 FROM task_relations WHERE kind='related' AND "
                         "(from_task=? OR to_task=?)", (task["id"], task["id"])).fetchone()
        assert c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:cpo' AND body='Read Synthetic work'").fetchone()


def test_a_rule_moved_into_a_what_wakes_automation_still_runs_and_a_pause_still_holds_it(api):
    from backend import redesign_migration
    made = rule(api, "task.done", params={"text": "Finished {{task.title}}"})
    with api.app.state.store.transaction() as c:
        aid = redesign_migration.adopt(c, "wake_rules", ("team", ""))[0]
        assert not c.execute("SELECT 1 FROM rules WHERE id=?", (made["id"],)).fetchone()
        task = make(c)
        H.task_update(c, "human:ana", task["id"], status="done")
        assert [h["new"] for h in history(c, task["id"])] == [made["id"]]
        assert "Finished Synthetic work" in H.task(c, task["id"])["note"]
        c.execute("INSERT INTO rule_task_state(rule_id,paused_at,pause_reason) VALUES(?,?,?)",
                  (made["id"], H.now(), "Paused: too many runs"))
        other = make(c, "Other work")
        H.task_update(c, "human:ana", other["id"], status="done")
        assert not history(c, other["id"])
    # Turning its automation on (as the rules editor's switch did) resumes it.
    assert api.patch(f"/api/v2/automations/{aid}", json={"enabled": True}, headers=headers()).status_code == 200
    with api.app.state.store.read() as c:
        assert not T.state(c, made["id"])["paused"]


def test_execution_ledgers_have_indexed_hour_and_webhook_lookups(api):
    with api.app.state.store.read() as c:
        hourly = " ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN SELECT count(*) FROM rule_task_runs "
                        "WHERE rule_id=? AND fired=1 AND ran_at>?", ("synthetic", H.now())))
        delivery = " ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN SELECT rule_id FROM rule_task_runs "
                        "WHERE webhook_event_id=?", ("synthetic",)))
        assert "rule_task_runs_hour" in hourly and "rule_task_runs_webhook" in delivery
