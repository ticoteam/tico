"""Rules with task triggers (`rules.task_trigger`): a task is created, enters a step, is done, declined or closed, and
the rule's action runs once; the chain guard, the run cap, privacy and a failed action that leaves the change saved."""
import json

from backend import rules as R
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def kind(c, name="Dev tickets"):
    return H.type_create(c, H.KEEPER, name, [
        {"name": "In Progress", "status": "doing"}, {"name": "In QA", "status": "review"},
        {"name": "Done", "status": "done"}], bots="work")


def rule(api, event, action, expected=200, token="ana-test", **body):
    body = {"scope_kind": "team", "event": event, "then": "action", "params": action, **body}
    return post(api, "rules", body, token=token, expected=expected)


def note_rule(api, event, text, **body):
    return rule(api, event, {"action": "note", "note": text}, **body)["rule"]


def history(c, task_id):
    return [r["new"] for r in c.execute("SELECT new FROM task_events WHERE task_id=? AND field='rule' ORDER BY rowid",
                                        (task_id,))]


def ran(c, rule_id):
    return [json.loads(r["detail_json"]) for r in c.execute(
        "SELECT detail_json FROM events WHERE action=? AND target=? ORDER BY rowid", (R.RAN, rule_id))]


def listed(api, rule_id):
    return next(r for r in get(api, "rules?scope_kind=team")["rules"] if r["id"] == rule_id)


def test_a_created_rule_makes_exactly_one_linked_review_task_and_the_history_names_it(api):
    with api.app.state.store.transaction() as c:
        dev = kind(c)
    made = rule(api, "task.created", {"action": "create_task", "owner": "cpo", "title": "Review {{task.title}}"},
                when={"type": "Dev tickets"}, note="Review new Dev tickets")["rule"]
    with api.app.state.store.transaction() as c:
        ticket = H.task_create(c, "bot:coo", "Improve messages", "Build it.", "bot:ops", type=dev["id"], lint=False)
        other = H.task_create(c, "bot:coo", "Water the plants", "Weekly.", "bot:ops", lint=False)
        reviews = c.execute("SELECT * FROM tasks WHERE title='Review Improve messages'").fetchall()
        assert len(reviews) == 1 and reviews[0]["owner"] == "bot:cpo" and reviews[0]["requester"] == "human:ana"
        assert c.execute("SELECT 1 FROM task_relations WHERE kind='related' AND ((from_task=? AND to_task=?) OR "
                         "(from_task=? AND to_task=?))", (reviews[0]["id"], ticket["id"], ticket["id"],
                                                          reviews[0]["id"])).fetchone()
        assert history(c, ticket["id"]) == [
            f"Rule “Review new Dev tickets” ran: made the task “Review Improve messages” for cpo ({reviews[0]['id']})"]
        assert history(c, other["id"]) == [], "the type condition keeps it to Dev tickets"
        assert [(d["task"], d["ok"]) for d in ran(c, made["id"])] == [(ticket["id"], True)]
    assert listed(api, made["id"])["task_trigger"] == "created"
    assert listed(api, made["id"])["runs_7d"] == 1


def test_the_chain_guard_stops_a_created_task_setting_off_the_rule_again_and_says_why(api):
    made = rule(api, "task.created", {"action": "create_task", "owner": "cpo", "title": "Follow up {{task.title}}"},
                note="Follow up")["rule"]
    also = note_rule(api, "task.created", "Seen")
    with api.app.state.store.transaction() as c:
        first = H.task_create(c, "bot:coo", "Plan the week", "Plan.", "bot:ops", lint=False)
        follow = c.execute("SELECT * FROM tasks WHERE title='Follow up Plan the week'").fetchall()
        assert len(follow) == 1
        assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Follow up Follow up%'").fetchone()
        assert history(c, follow[0]["id"]) == [
            "Rule “Follow up” did not run: its own action made this change, and a rule's action sets off no rule "
            "(no chains deeper than one)",
            f"Rule {also['id']} did not run: Rule “Follow up” made this change, and a rule's action sets off no rule "
            "(no chains deeper than one)"]
        assert len(history(c, first["id"])) == 2                # both ran on the task a person's bot filed
        assert len(ran(c, made["id"])) == 1


def test_a_done_rule_notes_once_per_finish_and_again_after_reopening(api):
    with api.app.state.store.transaction() as c:
        roadmap = kind(c, "Roadmap items")
    made = note_rule(api, "task.done", "Shipped: tell the owner", scope_kind="type", scope_id=roadmap["id"])
    with api.app.state.store.transaction() as c:
        item = H.task_create(c, "bot:coo", "Faster search", "Ship it.", "bot:ops", type=roadmap["id"], lint=False)
        H.task_update(c, "bot:ops", item["id"], status="done")
        assert len(ran(c, made["id"])) == 1
        H.task_update(c, "bot:ops", item["id"], step="In Progress")
        assert len(ran(c, made["id"])) == 1, "reopening is not finishing"
        H.task_update(c, "bot:ops", item["id"], status="done")
        notes = [r["new"] for r in c.execute("SELECT new FROM task_events WHERE task_id=? AND field='note'", (item["id"],))]
        assert sum("Shipped: tell the owner" in str(n) for n in notes) == 2
        assert len(history(c, item["id"])) == 2


def test_the_automatic_close_runs_closed_rules_and_not_done_rules_again(api):
    done = note_rule(api, "task.done", "Done")
    closed = note_rule(api, "task.closed", "Closed")
    declined = note_rule(api, "task.declined", "Declined")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:coo", "Tidy the docs", "Tidy.", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", task["id"], status="done")
        H.task_close(c, H.KEEPER, task["id"], quiet=True)          # what the 24-hour rule does (task_rules.run)
        assert (len(ran(c, done["id"])), len(ran(c, closed["id"])), len(ran(c, declined["id"]))) == (1, 1, 0)
        assert H.task(c, task["id"])["status"] == "closed"
    assert listed(api, closed["id"])["task_trigger"] == "after"
    assert listed(api, declined["id"])["task_trigger"] == "finished"


def test_a_failed_action_leaves_the_change_saved_and_the_rule_row_shows_the_error_and_its_time(api):
    wake = rule(api, "task.done", {"action": "wake_bot", "bot": "finance", "text": "{{task.title}} is done"})["rule"]
    move = rule(api, "task.done", {"action": "move_step", "step": "No such step"})["rule"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='finance'")          # the bot is gone
        task = H.task_create(c, "bot:coo", "Count the stock", "Count.", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", task["id"], status="done")
        assert H.task(c, task["id"])["status"] == "done"
        assert [d["ok"] for d in ran(c, wake["id"])] == [False] and [d["ok"] for d in ran(c, move["id"])] == [False]
        assert not c.execute("SELECT 1 FROM messages WHERE to_actor='bot:finance'").fetchone()
        said = history(c, task["id"])
        assert said[0] == f"Rule {wake['id']} could not wake a bot: finance is archived. The change still saved."
        assert said[1].startswith(f"Rule {move['id']} could not move its step: ")
    row = listed(api, wake["id"])
    assert row["last_error"]["detail"] == "finance is archived" and row["last_error"]["at"] == row["last_run_at"]
    # Once the bot is back the next run clears the error.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='finance'")
        again = H.task_create(c, "bot:coo", "Count it again", "Count.", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", again["id"], status="done")
        assert c.execute("SELECT 1 FROM messages WHERE to_actor='bot:finance'").fetchone()
    assert "last_error" not in listed(api, wake["id"])


def test_the_run_cap_pauses_a_rule_and_the_task_change_still_saves(api):
    made = note_rule(api, "task.created", "Hello")
    with api.app.state.store.transaction() as c:
        for n in range(R.RUN_CAP):
            H.event(c, H.KEEPER, R.RAN, made["id"], {"task": f"t{n}", "ok": True})
        task = H.task_create(c, "bot:coo", "One too many", "More.", "bot:ops", lint=False)
        assert H.task(c, task["id"])["title"] == "One too many"
        assert history(c, task["id"]) == [
            f"Rule {made['id']} did not run: it ran {R.RUN_CAP} times in the last hour, so it is paused"]
        assert len(ran(c, made["id"])) == R.RUN_CAP
    row = listed(api, made["id"])
    assert row["paused"] == "too_many_runs" and row["enabled"] is False
    post(api, f"rules/{made['id']}", {"on": True, "expected_revision": row["revision"]})
    row = listed(api, made["id"])
    assert "paused" not in row and row["enabled"] is True


def test_a_rule_acts_only_on_tasks_its_maker_may_read_and_a_task_it_makes_is_private(api):
    with api.app.state.store.transaction() as c:
        at = H.now()
        c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,when_json,then_outcome,params_json,enabled,position,"
                  "note,created_by,created,updated,revision) VALUES('rule-ben','team','','task.done','{}','action',?,1,1,"
                  "'','human:ben',?,?,1)", (json.dumps({"action": "create_task", "owner": "cpo", "title": "After {{task.title}}"}),
                                            at, at))
        hidden = H.task_create(c, "bot:finance", "Payroll", "Private.", "bot:coo", lint=False, private=True)
        H.task_update(c, "bot:coo", hidden["id"], status="done")
        assert ran(c, "rule-ben") == [] and history(c, hidden["id"]) == []
        assert not c.execute("SELECT 1 FROM tasks WHERE title='After Payroll'").fetchone()
        mine = H.task_create(c, "human:ben", "Ben's plan", "Private.", "bot:coo", lint=False, private=True)
        H.task_update(c, "bot:coo", mine["id"], status="done")
        made = c.execute("SELECT * FROM tasks WHERE title='After Ben''s plan'").fetchone()
        assert made and made["private"] == 1
        assert ran(c, "rule-ben")[0]["detail"] == "", "what it did on a private task stays on that task"


def test_a_step_rule_runs_on_entering_its_step_and_a_task_filed_into_a_later_step_runs_it_before_created(api):
    with api.app.state.store.transaction() as c:
        dev = kind(c)
    step = note_rule(api, "task.step_changed", "Now in QA", scope_kind="type", scope_id=dev["id"],
                     when={"step": "In QA"})
    created = note_rule(api, "task.created", "New")
    with api.app.state.store.transaction() as c:
        first = H.task_create(c, "bot:coo", "Build A", "Build.", "bot:ops", type=dev["id"], step="In Progress",
                              lint=False)
        assert [d["task"] for d in ran(c, step["id"])] == []
        H.task_update(c, "bot:ops", first["id"], step="In QA")
        assert [d["task"] for d in ran(c, step["id"])] == [first["id"]]
        straight = H.task_create(c, "bot:coo", "Build B", "Build.", "bot:ops", type=dev["id"], step="In QA",
                                 lint=False)
        said = history(c, straight["id"])
        assert [s.split(" ran:")[0] for s in said] == [f"Rule {step['id']}", f"Rule {created['id']}"]


def test_task_trigger_rows_are_checked_when_saved(api):
    action = {"action": "note", "note": "x"}
    rule(api, "task.created", action, then="wake", expected=422)
    rule(api, "task.created", action, scope_kind="task", scope_id="nope", expected=404)
    rule(api, "task.created", {"action": "forward", "webhook": "w"}, expected=422)
    rule(api, "task.created", action, when={"actor": "bot"}, expected=422)
    rule(api, "task.created", action, when={"owner": "bot:ops", "requester": "ana", "tag": "qa"})
    rule(api, "task.declined", {"action": "create_task", "owner": "cpo", "title": "Why?"})
    # A person who may not change team rules may not add a team task trigger either (no new rights check).
    rule(api, "task.created", action, token="cara-test", expected=403)
    listing = get(api, "rules?scope_kind=builtin")
    assert set(listing["task_triggers"]) == set(R.TASK_TRIGGERS)
    assert "create_task" in listing["task_trigger_actions"] and "forward" not in listing["task_trigger_actions"]
