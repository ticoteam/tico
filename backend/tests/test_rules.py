"""Wake rules (backend/rules.py): every message to a bot names the rule that decided it, table rows override the
built-ins most-specific-first, the always-wake floor holds, and a run records the rule that woke it. Rows are written as
an older install (or a kept row) has them; rules made today live in "What wakes" automations (test_redesign_migration)."""

import json

from backend import hubdb as H
from backend import rules as R
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_wake_rules import as_cause, claim, finish, jobs, news, parked_task, setup, skips


def rule_row(api, by="human:ben", scope_kind="team", scope_id="", event="status_or_relation", then="note", when=None,
             params=None, revision=1, action_by=None):
    """A row in the rules table, as the wake engine reads it."""
    row = {"id": "rule_" + H.new_id()[:8], "scope_kind": scope_kind, "scope_id": scope_id, "event": event,
           "condition": json.dumps(when or {}), "then_outcome": then, "params": json.dumps(params or {}),
           "created_by": by, "action_by": action_by, "created_at": H.now(), "updated_at": H.now(), "revision": revision}
    with api.app.state.store.transaction() as c:
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return {"rule": {**row, "params": params or {}}}


def plain_task(c, title="Plain work", clear=True):
    task = H.task_create(c, "bot:ops", title, "Do it", "bot:cpo")
    if clear:
        c.execute("DELETE FROM jobs")
    return H.task(c, task["id"])


def reopened(c, task):
    """A wake an event names (the task was handed back to its owner): it starts a run under the rule `assignment`."""
    return H._wake(c, task, task["owner_id"], "Open: " + task["title"])


def test_every_message_to_a_bot_names_its_rule_and_the_filters_are_unchanged(api):
    setup(api)
    post(api, "chat/cpo", {"text": "Run your check"})
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:ops"):
            held = news(c, task)
        plain = news(c, plain_task(c))
        person = H.message(c, c.execute("SELECT id FROM messages WHERE sender_id='human:ana' LIMIT 1").fetchone()[0])
    assert person["refs"]["rule"] == "person_message"
    assert held["refs"]["rule"] == "parked" and held["refs"]["quiet"] is True
    # News no rule names waits as a note; an event that hands the bot work wakes under its own rule.
    assert plain["refs"]["rule"] == "status_or_relation" and plain["refs"]["quiet"] is True
    with api.app.state.store.transaction() as c:
        assert not reopened(c, plain_task(c, "Reopened work"))["refs"].get("quiet")
    with api.app.state.store.read() as c:
        assert [s["detail"]["rule_id"] for s in skips(c)] == ["parked", "status_or_relation"]


def test_rows_override_by_scope_task_over_bot_over_team_and_the_floor_stays(api):
    setup(api)
    team = rule_row(api)["rule"]
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = news(c, task)
        assert msg["refs"]["rule"] == team["id"] and msg["refs"]["quiet"] is True and jobs(c, "cpo") == 0
        assert msg["refs"]["held_by"] == "rule"
    bot = rule_row(api, scope_kind="bot", scope_id="cpo", then="wake")["rule"]
    with api.app.state.store.transaction() as c:
        assert news(c, task)["refs"]["rule"] == bot["id"] and jobs(c, "cpo") == 1
    one = rule_row(api, scope_kind="task", scope_id=task["id"], then="ignore")["rule"]
    with api.app.state.store.transaction() as c:
        msg = news(c, task)
        assert msg["refs"]["rule"] == one["id"] and msg["refs"]["held_by"] == "ignored"
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (msg["id"],)).fetchone()[0] == 0
    # A person's message is never made to wait.
    rule_row(api, event="*", then="ignore")
    post(api, "chat/cpo", {"text": "Still there?"})
    with api.app.state.store.read() as c:
        last = c.execute("SELECT refs FROM messages WHERE sender_id='human:ana' ORDER BY rowid DESC").fetchone()
        assert json.loads(last[0])["rule"] == "person_message" and "quiet" not in json.loads(last[0])
        # What the bot waits on still wakes it under a `*` row.
        found = R.judge(c, H.KEEPER, "bot:cpo", "message", {"task": task["id"], "blocker_done": True}, "Done", None)
        assert (found.outcome, found.rule) == ("wake", "awaited_blocker_done")


def test_a_run_records_the_rule_that_woke_it_and_fail_open_wakes(api, monkeypatch):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        reopened(c, plain_task(c))
    attempt = claim(api, r, "cpo")
    finish(api, r, attempt)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT rule_id FROM turns WHERE id=?", (attempt["id"],)).fetchone()[0] == "assignment"
    monkeypatch.setattr(R, "_judge", lambda *a, **k: 1 / 0)
    with api.app.state.store.transaction() as c:
        other = plain_task(c, "Other work", clear=False)
        before = jobs(c, "cpo")
        woke = reopened(c, other)
        assert woke["refs"]["rule"] == R.FAIL_OPEN and jobs(c, "cpo") == before + 1


def test_rule_weekly_counts_runs_per_rule(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        reopened(c, plain_task(c))
    finish(api, r, claim(api, r, "cpo"))
    out = api.post("/api/v2/sql", json={"sql": "SELECT rule, runs FROM rule_weekly WHERE bot='cpo'"},
                   headers={"Authorization": "Bearer ana-test"})
    assert out.status_code == 200, out.text
    assert ["assignment", 1] in [list(row) for row in out.json()["rows"]]


def test_an_event_no_rule_names_is_a_note_and_a_named_one_wakes(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        plain = news(c, task, "Related task changed")
        assert plain["refs"]["rule"] == "status_or_relation" and plain["refs"]["quiet"] is True
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (plain["id"],)).fetchone()[0] == 1
        assert R._rule_for_event("brand_new_event") == (R.NOTE, R.DEFAULT_NOTE)
        for event in ("subtask_finished", "task_done", "blocker", "release_published", "backlog"):
            woke = news(c, task, "x", refs={"wake": event})
            assert woke["refs"]["rule"] == event and not woke["refs"].get("quiet"), event
        assert jobs(c, "cpo") == 5


def test_an_ask_rule_writes_quietly_and_raises_one_needs_you_item_per_task_and_rule(api):
    setup(api)
    row = rule_row(api, then="ask")["rule"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Plan the launch", "Plan it", "bot:cpo")
        c.execute("DELETE FROM jobs")
        first, again = news(c, H.task(c, task["id"])), news(c, H.task(c, task["id"]))
        for msg in (first, again):
            assert msg["refs"]["rule"] == row["id"] and msg["refs"]["quiet"] is True
        assert jobs(c, "cpo") == 0
        cards = c.execute("SELECT t.owner_id, t.title, t.body FROM events e JOIN tasks t ON t.id=e.target "
                          "WHERE e.action='rule.ask'").fetchall()
        assert len(cards) == 1 and cards[0]["owner_id"] == "human:ana" and row["id"] in cards[0]["body"]


def test_an_ask_card_about_a_private_task_is_private(api):
    setup(api)
    rule_row(api, then="ask")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Acme layoff plan", "Plan it", "bot:cpo", private=True)
        c.execute("DELETE FROM jobs")
        news(c, H.task(c, task["id"]))
        card = c.execute("SELECT t.id, t.private FROM events e JOIN tasks t ON t.id=e.target "
                         "WHERE e.action='rule.ask'").fetchone()
        assert card and card["private"] == 1
        assert not H.task_private_readable(c, "human:cara", H.task(c, card["id"]))


def test_an_action_rule_runs_as_the_keeper_and_a_failed_action_wakes_the_bot(api):
    setup(api)
    row = rule_row(api, then="action", params={"action": "close", "note": "Not needed"})["rule"]
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = news(c, task)
        assert msg["refs"]["rule"] == row["id"] and msg["refs"]["quiet"] is True
        assert H.task(c, task["id"])["status"] == "closed"
        ran = c.execute("SELECT detail FROM events WHERE action='rule.action' AND target=?", (task["id"],)).fetchone()
        assert json.loads(ran[0]) == {"rule": row["id"], "action": "close"}
    with api.app.state.store.transaction() as c:         # edited by someone who may: a step that is not there
        c.execute("UPDATE rules SET params=?, revision=2, action_by='human:ana' WHERE id=?",
                  (json.dumps({"action": "move_step", "step": "No such step"}), row["id"]))
    with api.app.state.store.transaction() as c:
        other = plain_task(c, "Other work", clear=False)
        before = jobs(c, "cpo")
        woke = news(c, other)
        assert woke["refs"]["rule"] == R.FAIL_OPEN and jobs(c, "cpo") == before + 1
        assert H.task(c, other["id"])["status"] != "closed"


def tagged(c, task, *keys):
    """`task` with these tags on it, put there by a person's write, as a fresh row."""
    for key in keys:
        if not H.tag_by_key(c, key):
            H.tag_create(c, "human:ana", key, mover=True)
    H.task_update(c, "human:ana", task["id"], labels=[*H.task_labels(task), *keys])
    return H.task(c, task["id"])


def actions(c, rule_id):
    return [json.loads(r[0]) for r in c.execute(
        "SELECT detail FROM events WHERE action='rule.action' AND json_extract(detail,'$.rule')=? "
        "ORDER BY rowid", (rule_id,))]


def test_a_tag_rule_assigns_the_task_through_a_reassignment_and_records_the_rule(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        H.tag_create(c, "human:ana", "billing", mover=True)
    row = rule_row(api, when={"tag": "billing"}, then="action", params={"action": "assign", "owner": "bot:ops"})["rule"]
    with api.app.state.store.transaction() as c:
        plain, task = plain_task(c, "Untagged work"), tagged(c, plain_task(c), "billing")
        c.execute("DELETE FROM jobs")
        news(c, plain)
        assert H.task(c, plain["id"])["owner_id"] == "bot:cpo", "no tag, no match"
        msg = news(c, task)
        assert msg["refs"]["rule"] == row["id"] and msg["refs"]["quiet"] is True
        assert H.task(c, task["id"])["owner_id"] == "bot:ops"
        assert jobs(c, "ops") == 1, "the new owner is woken as on any reassignment"
        moved = c.execute("SELECT actor, old, new, note FROM task_events WHERE task_id=? AND field='owner'",
                          (task["id"],)).fetchone()
        assert tuple(moved) == (H.KEEPER, "bot:cpo", "bot:ops", f"Rule {row['id']}")
        assert actions(c, row["id"]) == [{"rule": row["id"], "action": "assign", "owner": "bot:ops"}]


def test_an_assign_rule_never_hands_a_private_task_to_someone_who_cannot_read_it(api):
    # Rules act as the keeper, which passes the private-task check: the action itself must keep the task to its readers.
    setup(api)
    with api.app.state.store.transaction() as c:
        H.tag_create(c, "human:ana", "billing", mover=True)
    row = rule_row(api, when={"tag": "billing"}, then="action", params={"action": "assign", "owner": "bot:ops"})["rule"]
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, "bot:cpo", "Private work", "Not for ops", "bot:cpo", private=True)
        assert H.task_private(c, hidden) and not H.task_private_readable(c, "bot:ops", hidden)
        hidden = tagged(c, H.task(c, hidden["id"]), "billing")
        readable = tagged(c, plain_task(c), "billing")      # ops filed it, so ops may read it
        c.execute("DELETE FROM jobs")
        news(c, hidden)                 # not handed over, and the owner it stays with is still woken (fail open)
        assert H.task(c, hidden["id"])["owner_id"] == "bot:cpo" and jobs(c, "ops") == 0 and jobs(c, "cpo") == 1
        assert actions(c, row["id"]) == []
        news(c, readable)
        assert H.task(c, readable["id"])["owner_id"] == "bot:ops"


def test_a_rule_never_changes_a_private_task_its_writer_cannot_read(api):
    # The keeper passes the private-task check, so the writer's own access is what keeps a rule off another's task.
    setup(api)
    with api.app.state.store.transaction() as c:
        H.tag_create(c, "human:ana", "urgent", mover=True)
    row = rule_row(api, then="action", params={"action": "add_tag", "tag": ["urgent"]})["rule"]
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, "bot:cpo", "Private work", "Not for ben", "bot:cpo", private=True)
        assert not H.task_private_readable(c, row["created_by"], hidden)
        c.execute("DELETE FROM jobs")
        news(c, hidden)
        assert H.task_labels(H.task(c, hidden["id"])) == [] and jobs(c, "cpo") == 1
        assert actions(c, row["id"]) == []
        task = plain_task(c)
        news(c, task)
        assert H.task_labels(H.task(c, task["id"])) == ["urgent"]


def test_an_edited_rule_uses_its_action_writer_without_changing_its_creator(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        H.tag_create(c, "human:ana", "urgent", mover=True)
        hidden = H.task_create(c, "bot:cpo", "Private work", "Not for ben", "bot:cpo", private=True)
        assert H.task_private_readable(c, "human:ana", hidden)
        assert not H.task_private_readable(c, "human:ben", hidden)
    # Ana made it; Ben's edit gave it its tag action, so it acts with Ben's access, not Ana's.
    row = rule_row(api, by="human:ana", then="action", params={"action": "add_tag", "tag": ["urgent"]}, revision=2,
                   action_by="human:ben")["rule"]
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        msg = news(c, hidden)
        assert H.task_labels(H.task(c, hidden["id"])) == []
        assert jobs(c, "cpo") == 1 and msg["refs"]["rule"] == R.FAIL_OPEN
        assert actions(c, row["id"]) == []
        # An upgraded revised rule with no recorded action writer cannot borrow its creator's access either.
        c.execute("UPDATE rules SET action_by=NULL WHERE id=?", (row["id"],))
        R.ensure_schema(c)
        c.execute("DELETE FROM jobs")
        news(c, hidden)
        assert H.task_labels(H.task(c, hidden["id"])) == [] and jobs(c, "cpo") == 1
        c.execute("UPDATE rules SET action_by='human:ana', condition=? WHERE id=?", (json.dumps({"actor": "keeper"}), row["id"]))
        news(c, hidden)
        assert H.task_labels(H.task(c, hidden["id"])) == ["urgent"]


def test_tag_actions_add_and_remove_once_and_cannot_retrigger_their_rule(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        for key in ("triage", "urgent"):
            H.tag_create(c, "human:ana", key, mover=True)
    add = rule_row(api, then="action", params={"action": "add_tag", "tag": ["urgent"]})["rule"]
    drop = rule_row(api, scope_kind="bot", scope_id="cpo", when={"tag": "triage"}, then="action",
                    params={"action": "remove_tag", "tag": ["triage"]})["rule"]
    with api.app.state.store.transaction() as c:
        task = tagged(c, plain_task(c), "triage")
        first = news(c, task)                       # the bot row comes first: triage comes off
        assert first["refs"]["rule"] == drop["id"] and H.task_labels(H.task(c, task["id"])) == []
        second = news(c, task)                      # no triage now: the bot row no longer matches, the team row adds
        assert second["refs"]["rule"] == add["id"] and H.task_labels(H.task(c, task["id"])) == ["urgent"]
        news(c, task)                               # already on: nothing written
        labels = c.execute("SELECT actor, note FROM task_events WHERE task_id=? AND field='labels' ORDER BY rowid",
                           (task["id"],)).fetchall()
        assert [tuple(r) for r in labels[1:]] == [(H.KEEPER, f"Rule {drop['id']}"), (H.KEEPER, f"Rule {add['id']}")]
        assert [a["changed"] for a in actions(c, add["id"])] == [True, False]
        assert [a["changed"] for a in actions(c, drop["id"])] == [True]


def test_two_assign_rules_that_hand_a_task_back_and_forth_run_once(api):
    setup(api)
    to_ops = rule_row(api, scope_kind="bot", scope_id="cpo", event="assignment", then="action",
                      params={"action": "assign", "owner": "bot:ops"})["rule"]
    to_cpo = rule_row(api, scope_kind="bot", scope_id="ops", event="assignment", then="action",
                      params={"action": "assign", "owner": "bot:cpo"})["rule"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Hand-off work", "Do it", "bot:cpo")
        assert H.task(c, task["id"])["owner_id"] == "bot:ops"
        # ops's assignment, written by cpo's rule, is news: ops's own rule does not hand the task back.
        assert len(actions(c, to_ops["id"])) == 1 and actions(c, to_cpo["id"]) == []
        last = c.execute("SELECT refs FROM messages WHERE recipient_id='bot:ops' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert json.loads(last[0])["rule"] == to_cpo["id"]
