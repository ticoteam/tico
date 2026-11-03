"""Head of Engineering works by exception (backend/pipeline_exceptions.py): one list of what did not move on its own,
read about hourly, and no wake for a ticket's routine steps, subtask results, checks or verdicts."""
import json

from backend import check_pools, pipeline_exceptions as PE, pipeline_stats, rules, step_age, step_backlog
from backend import task_relations
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type

HEAD = "a" * 40


def board(c, profile="manager"):
    found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
    typ = H.type_get(c, found[0]) if found else dev_type(c)
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    c.execute("UPDATE bot_config SET config=? WHERE bot='coo'",
              (encode({"name": "coo", "runtime": "fake", "status": "active", "profile": profile}),))
    if not c.execute("SELECT 1 FROM runners WHERE id='r1'").fetchone():
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at,last_seen_at) VALUES('r1','box','ana','h',?,?)",
                  (H.now(), H.now()))
    return H.type_get(c, typ["id"])


def profile(c, slug, name):
    c.execute("UPDATE bot_config SET config=json_set(config,'$.profile',?) WHERE bot=?", (name, slug))


def settled(c):
    """Every wake so far has had its run: what is left queued is what a test adds."""
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def doing(c, row):
    """A check its reviewer started (the review gate guards a builder's moves, not this)."""
    c.execute("UPDATE tasks SET status='doing' WHERE id=?", (row["id"],))
    settled(c)
    return H.task(c, row["id"])


def ticket(c, typ, title="Fix the connection", step="In Progress", minutes=0):
    row = H.task_create(c, "bot:coo", title, "Build.", "bot:cpo", type=typ["id"], step=step, lint=False)
    settled(c)
    if minutes:
        then = H.shift(H.now(), minutes=-minutes)
        c.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (then, then, row["id"]))
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (then, row["id"]))
    return H.task(c, row["id"])


def run(c, row, state, minutes_ago, stop=None):
    """One finished attempt of the owner's on the task's lane, `minutes_ago`."""
    when = H.shift(H.now(), minutes=-minutes_ago)
    msg = H._write_message(c, "human:ana", row["owner_id"], "go", H.conversation(c, row["conversation_id"]), "say",
                           {"task": row["id"]}, None, None)
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (msg["id"],)).fetchone()[0]
    c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
    aid = H.new_id()
    c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,started_at,"
              "finished_at,lane,stop_requested_at) VALUES(?,?,?,'r1',1,?,?,?,?,?,?,?,?)",
              (aid, job, H.actor_id(row["owner_id"]), aid, state, when, when, when, when, "task:" + row["id"], stop))
    return when


def check_of(c, row, title="Review 2: PR #1585", owner="bot:ops"):
    """A check filed below the ticket, as the gate leaves it once it let the request through (the gate has its own
    tests: backend/tests/test_review_gate.py)."""
    made = H.task_create(c, "bot:cpo", "Check the change", "Check it.", owner, parent_id=row["id"])
    c.execute("UPDATE tasks SET title=? WHERE id=?", (title, made["id"]))
    return H.task(c, made["id"])


def listed(c, typ, kind=None):
    return [e for e in PE.build(c, typ) if kind is None or e["kind"] == kind]


def late_notices(c, row):
    return c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:coo' AND json_extract(refs,'$.task')=? "
                     "AND json_extract(refs,'$.step_late')", (row["id"],)).fetchone()[0]


def unheld_jobs(c, slug):
    return c.execute("SELECT count(*) FROM jobs WHERE bot_id=? AND state='queued' AND coalesce(held,0)=0",
                     (slug,)).fetchone()[0]


def test_a_ticket_with_no_run_for_45_minutes_is_listed_once_with_its_owner_and_since_when(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        quiet, fresh, snoozed = ticket(c, typ, "Fix Cursor", minutes=50), ticket(c, typ, "Fix Muse", minutes=20), \
            ticket(c, typ, "Fix Grok", minutes=50)
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), hours=5), snoozed["id"]))
        [entry] = listed(c, typ, "stuck")
        assert entry["ticket"]["id"] == quiet["id"] and entry["ticket"]["owner_id"] == "cpo"
        assert "owner cpo" in entry["cause"]
        assert entry["since"] == H.task(c, quiet["id"])["created_at"], "since it last had a run or entered the step"
        assert [e["key"] for e in listed(c, typ, "stuck")] == [entry["key"]], "read again: still one entry"
        # A queued run on it is work: the ticket is not stuck.
        H._write_message(c, "human:ana", "bot:cpo", "carry on", H.conversation(c, quiet["conversation_id"]), "say",
                         {"task": quiet["id"]}, None, None)
        assert listed(c, typ, "stuck") == []
        assert fresh["id"] and snoozed["id"]


def test_a_check_left_doing_with_no_run_is_listed_under_its_ticket_and_the_ticket_itself_is_not(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        row = ticket(c, typ, minutes=400)
        check = check_of(c, row)
        check = doing(c, check)
        run(c, check, "completed", 360)
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (H.shift(H.now(), hours=-6), check["id"]))
        [entry] = listed(c, typ, "stuck")
        assert entry["ticket"]["id"] == row["id"] and entry["check"]["id"] == check["id"]
        assert entry["check"]["owner_id"] == "ops" and "owner ops" in entry["cause"]


def test_a_cut_run_is_one_entry_a_second_cut_is_the_same_entry_and_a_finished_run_clears_it(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        row = ticket(c, typ, minutes=120)
        first = run(c, row, "interrupted", 30)
        [entry] = listed(c, typ, "run")
        assert "was cut" in entry["cause"] and entry["since"] == first
        run(c, row, "interrupted", 10)
        [again] = listed(c, typ, "run")
        assert again["key"] == entry["key"] and again["since"] == first and "2 in a row" in again["cause"]
        run(c, row, "completed", 5)
        assert listed(c, typ, "run") == []


def test_a_refusal_repeated_twice_on_one_ticket_is_one_entry(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        row, other = ticket(c, typ, "Fix Cursor"), ticket(c, typ, "Fix Muse")

        def refuse(task_id):
            c.execute("INSERT INTO refusals(id,created_at,actor,rule,detail,severity) VALUES(?,?,?,?,?,?)",
                      (H.new_id(), H.now(), "bot:cpo", "review_gate", json.dumps({"task": task_id}), "warn"))

        refuse(row["id"])
        refuse(other["id"])
        assert listed(c, typ, "refusal") == [], "once is not repeated"
        refuse(row["id"])
        [entry] = listed(c, typ, "refusal")
        assert entry["ticket"]["id"] == row["id"] and "2 times" in entry["cause"]
        refuse(row["id"])
        assert len(listed(c, typ, "refusal")) == 1


def test_a_failed_merge_is_one_entry_with_its_pr_number_and_head(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        row = ticket(c, typ)
        for number in (41, 42):
            H.task_link(c, "bot:cpo", row["id"], f"https://github.com/acme/app/pull/{number}", kind="pr")
        c.execute("UPDATE task_links SET mergeable='conflict', pr_sha=? WHERE task_id=? AND number=42",
                  (HEAD, row["id"]))
        [entry] = listed(c, typ, "merge")
        assert entry["pr"] == 42 and entry["head"] == HEAD and entry["ticket"]["id"] == row["id"]


def test_only_the_boards_manager_reads_the_list_in_pipeline(api):
    auth = api.app.state.auth
    with api.app.state.store.transaction() as c:
        typ = board(c)
        ticket(c, typ, minutes=50)
        mine = pipeline_stats.report(c, auth, Identity("bot:coo", "bot"), type=typ["id"])
        assert [e["kind"] for e in mine["exceptions"]] == ["stuck"]
        assert "exceptions" not in pipeline_stats.report(c, auth, Identity("bot:cpo", "bot"), type=typ["id"])
        # A caller acting for another reader is not the manager, even when its own actor is.
        assert PE.report(c, auth, Identity("bot:coo", "bot", task_actor="human:ben"), typ) is None
    assert "exceptions" not in get(api, f"pipeline?type={typ['id']}"), "a person reading the board gets no list"


def test_the_hourly_list_wakes_a_manager_only_for_a_new_entry(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        assert PE.deliver(c) == [], "nothing on the list: no turn"
        ticket(c, typ, "Fix Cursor", minutes=50)
        before = unheld_jobs(c, "coo")
        assert PE.deliver(c) == ["coo"]
        assert unheld_jobs(c, "coo") == before + 1
        [wake] = c.execute("SELECT body, refs FROM messages WHERE recipient_id='bot:coo' AND "
                           "json_extract(refs,'$.wake')='exceptions'").fetchall()
        assert "Fix Cursor" in wake[0] and "no run for 45 minutes" in wake[0]
        assert PE.deliver(c) == [], "the same entry an hour later: no second turn"
        ticket(c, typ, "Fix Muse", minutes=50)
        assert PE.deliver(c) == ["coo"], "a new entry wakes it again"
        # A manager still on the Lead profile is not sent the list: it keeps the notices it had.
        board(c, profile="lead")
        ticket(c, typ, "Fix Grok", minutes=50)
        assert PE.deliver(c) == []


def test_routine_ticket_events_never_wake_a_manager_and_file_no_review_without_a_pr(api):
    """Steps, subtask results, checks, verdicts, blocked and finished tickets and late or backlog notices are no turn
    for a manager on the Manager profile. The same events on the Lead profile are the control: its late-ticket and
    blocker notices wake it."""

    def routine(c, typ, title):
        # Both tickets first: making one settles the jobs queued so far, and every event below must stay counted.
        row, late = ticket(c, typ, title), ticket(c, typ, title + " late")
        check = check_of(c, row, "Review 1: PR #7")
        H.task_comment(c, "bot:ops", check["id"], "VERDICT: approve PR #7 head " + HEAD)
        H.task_update(c, "bot:ops", check["id"], status="done", note="approve")
        H.task_update(c, "bot:cpo", row["id"], step="QA Fixes")
        H.task_update(c, "bot:cpo", row["id"], status="waiting", note="Waits on QA")
        H.task_update(c, "bot:cpo", row["id"], status="doing", note="Back on it")
        # Its own words: the same notice again is dropped as a repeat, which would hide a wake.
        H._wake(c, H.task(c, row["id"]), "bot:coo", f"A setup blocker on {title} needs you.", {"wake": "blocker"})
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (H.shift(H.now(), hours=-5), late["id"]))
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), hours=-5), late["id"]))
        step_age.sweep(c, H.shift(H.now(), minutes=1))
        H.task_update(c, "bot:cpo", row["id"], status="done", note="Shipped")
        return late

    with api.app.state.store.transaction() as c:
        typ = board(c, profile="lead")
        before = unheld_jobs(c, "coo")
        late = routine(c, typ, "Fix Cursor")
        assert unheld_jobs(c, "coo") > before, "control: the Lead profile is woken for the blocker and the late one"
        assert step_backlog._targets(c, {"notify": ["manager"], "per": "step"}, late, late["owner_id"]) == ["bot:coo"]
        assert late_notices(c, late) == 1

        typ = board(c, profile="manager")
        settled(c)
        late = routine(c, typ, "Fix Muse")
        assert unheld_jobs(c, "coo") == 0, "no routine event woke the manager"
        assert step_backlog._targets(c, {"notify": ["manager"], "per": "step"}, late, late["owner_id"]) == []
        assert late_notices(c, late) == 0, "not even a held note: the list carries it"
        settled(c)      # its owner's status check ran and left it as it was
        assert any(e["key"] == "ticket:" + late["id"] + ":stuck" for e in listed(c, typ)), "it is on the list instead"
        # Entering a check step with no linked pull request files no review.
        c.execute("UPDATE task_steps SET check_pool=? WHERE type_id=? AND name='In QA'", (json.dumps(["ops"]), typ["id"]))
        fresh = ticket(c, typ, "Fix Codex")
        qa = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='In QA'", (typ["id"],)).fetchone()[0]
        c.execute("UPDATE tasks SET step_id=?, status='review' WHERE id=?", (qa, fresh["id"]))
        assert check_pools.entered(c, "bot:cpo", H.task(c, fresh["id"])) is None
        assert list(H.TR.child_ids(c, fresh["id"])) == []


def test_a_turn_that_ends_doing_with_nothing_queued_wakes_its_owner_once_and_the_sweep_lists_it(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        row = ticket(c, typ, minutes=120)
        check = check_of(c, row)
        check = doing(c, check)
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (H.shift(H.now(), minutes=-120), check["id"]))
        run(c, check, "completed", 60)
        before = unheld_jobs(c, "ops")
        assert step_age.turn_ended(c) == [check["id"]]
        assert unheld_jobs(c, "ops") == before + 1, "a reviewer's profile would hold a status check; this one wakes"
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
        assert step_age.turn_ended(c) == [], "once per stay at the step"
        [entry] = listed(c, typ, "stuck")
        assert entry["check"]["id"] == check["id"]
        assert rules.EVENTS["exceptions"]


def test_a_verdict_a_handoff_and_a_child_or_blocker_result_are_a_note_for_a_manager_and_wake_any_other(api):
    """The rule engine's floor (a real verdict, a builder hand-off) and its awaited results (a subtask's, a blocker's)
    come before the profile's rows; on the Manager profile they still wait for its next run."""
    with api.app.state.store.transaction() as c:
        board(c)
        parent = H.task_create(c, "bot:cpo", "Find why reviews stall", "Look.", "bot:coo", lint=False)
        conv = H.conversation(c, parent["conversation_id"])
        cases = [("bot:ops", "say", {"task": parent["id"]}, "Looked at it.\nVERDICT: approve PR #7 head " + HEAD),
                 ("keeper", "notice", {"task": parent["id"], "handoff": "verdict"}, "Review 1 gave changes."),
                 ("keeper", "notice", {"task": parent["id"], "subtask": "x", "wake": "subtask_finished"}, "Done."),
                 ("keeper", "notice", {"task": parent["id"], "blocker_done": "x"}, "The blocker finished."),
                 ("keeper", "notice", {"task": parent["id"], "wake": "blocker"}, "A setup blocker needs you.")]
        for name, outcome in (("manager", rules.NOTE), ("lead", rules.WAKE), ("doer", rules.WAKE)):
            profile(c, "coo", name)
            got = [rules.judge(c, actor, "bot:coo", kind, refs, body, conv) for actor, kind, refs, body in cases]
            assert [d.outcome for d in got] == [outcome] * len(cases), (name, got)
        # A person still wakes a manager, verdict or not.
        profile(c, "coo", "manager")
        person = rules.judge(c, "human:ana", "bot:coo", "say", {"task": parent["id"]}, "VERDICT: approve", conv)
        assert person.outcome == rules.WAKE

        # End to end: a child of the manager's own task finishes, and a task it waits on finishes.
        def results(name):
            profile(c, "coo", name)
            mine = H.task_create(c, "bot:cpo", f"Sort the {name} pools", "Sort.", "bot:coo", lint=False)
            child = H.task_create(c, "bot:coo", f"Count the {name} pool", "Count.", "bot:ops", parent_id=mine["id"])
            waits = H.task_create(c, "bot:cpo", f"Wait for the {name} fix", "Wait.", "bot:coo", lint=False)
            upstream = H.task_create(c, "bot:cpo", f"Ship the {name} fix", "Ship.", "bot:ops", lint=False)
            task_relations.relate(c, "human:ana", upstream["id"], waits["id"], "blocks")
            settled(c)
            H.task_update(c, "bot:ops", child["id"], status="done", note="Three bots, one free")
            H.task_update(c, "bot:ops", upstream["id"], status="done", note="Shipped")
            return c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='coo' "
                             "AND j.state='queued' AND coalesce(j.held,0)=0 AND json_extract(m.refs,'$.task') "
                             "IN (?,?)", (mine["id"], waits["id"])).fetchone()[0]

        assert results("doer") >= 2, "control: a doer is woken for its child's result and its blocker finishing"
        assert results("manager") == 0


def test_a_reviewers_recorded_waits_keep_its_check_off_the_list_and_unwoken_until_it_is_really_idle(api):
    """A reviewer's profile has no stall sweeps; what its check waits on is read all the same."""
    with api.app.state.store.transaction() as c:
        typ = board(c)
        row = ticket(c, typ, minutes=200)
        check = doing(c, check_of(c, row))
        profile(c, "ops", "reviewer")
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=?", (H.shift(H.now(), minutes=-120), check["id"]))
        run(c, check, "completed", 60)
        upstream = H.task_create(c, "bot:cpo", "Ship the base fix", "Ship.", "bot:cpo", lint=False)
        settled(c)
        conv = H.conversation(c, check["conversation_id"])

        def ask():
            m = H._write_message(c, "bot:ops", "bot:cpo", "Which head should I review?", conv, "ask",
                                 {"task": check["id"]}, None, None)
            return lambda: (c.execute("DELETE FROM jobs WHERE message_id=?", (m["id"],)),
                            c.execute("DELETE FROM messages WHERE id=?", (m["id"],)))

        def snooze():
            c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), hours=3), check["id"]))
            return lambda: c.execute("UPDATE tasks SET snoozed_until=NULL WHERE id=?", (check["id"],))

        def approval():
            aid = H.new_id()
            c.execute("INSERT INTO approvals(id,kind,task_id,requested_by_id,created_at) VALUES(?,?,?,?,?)",
                      (aid, "send", check["id"], "bot:ops", H.now()))
            return lambda: c.execute("DELETE FROM approvals WHERE id=?", (aid,))

        def blocker():
            c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,?,?,?)",
                      (upstream["id"], check["id"], "blocks", "bot:cpo", H.now()))
            return lambda: c.execute("DELETE FROM task_relations WHERE from_task=? AND kind='blocks'", (upstream["id"],))

        for wait in (ask, snooze, approval, blocker):
            undo = wait()
            settled(c)
            assert listed(c, typ, "stuck") == [], wait.__name__
            assert step_age.turn_ended(c) == [], wait.__name__
            undo()
        [entry] = listed(c, typ, "stuck")
        assert entry["check"]["id"] == check["id"], "nothing recorded: the idle reviewer's check is listed"
        before = unheld_jobs(c, "ops")
        assert step_age.turn_ended(c) == [check["id"]], "and its reviewer is woken"
        assert unheld_jobs(c, "ops") == before + 1


def test_a_finished_check_is_filed_so_an_idle_ticket_is_listed_as_not_moving_on_not_as_missing_its_check(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        c.execute("UPDATE task_steps SET check_pool=? WHERE type_id=? AND name='In QA'", (json.dumps(["ops"]), typ["id"]))
        qa = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='In QA'", (typ["id"],)).fetchone()[0]
        row = ticket(c, typ, "Fix Cursor", minutes=120)
        H.task_link(c, "bot:cpo", row["id"], "https://github.com/acme/app/pull/41", kind="pr")
        c.execute("UPDATE tasks SET step_id=?, status='review' WHERE id=?", (qa, row["id"]))
        settled(c)
        [missing] = listed(c, typ, "stuck")
        assert "no check filed at In QA" in missing["cause"], "nothing filed: the missing check is the exception"
        check = check_of(c, row, "QA: PR #41")
        ended = H.shift(H.now(), minutes=-60)
        c.execute("UPDATE tasks SET status='done', done_at=?, updated_at=? WHERE id=?", (ended, ended, check["id"]))
        settled(c)
        [entry] = listed(c, typ, "stuck")
        assert "no check filed" not in entry["cause"]
        assert "QA: PR #41 finished and the ticket has not moved on (owner cpo)" in entry["cause"]
        assert entry["since"] == ended and entry["key"] == missing["key"]
        c.execute("UPDATE tasks SET done_at=?, updated_at=? WHERE id=?", (H.now(), H.now(), check["id"]))
        assert listed(c, typ, "stuck") == [], "a check that just finished: the builder has time to move it on"


def test_held_notes_alone_never_wake_a_manager_and_ride_with_its_next_list(api):
    """A week of normal tickets leaves the manager notes (verdicts, results) and no turn: they are read when something
    real wakes it."""
    with api.app.state.store.transaction() as c:
        typ = board(c)
        for day in range(7):
            c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)",
                      (H.new_id(), "keeper", "bot:coo", f"Review 1 approved #{day}.", H.shift(H.now(), days=-day)))
        assert [PE.deliver(c, H.shift(H.now(), hours=h)) for h in range(0, 7 * 24, 6)] == [[]] * 28
        assert unheld_jobs(c, "coo") == 0
        ticket(c, typ, "Fix Cursor", minutes=50)
        assert PE.deliver(c) == ["coo"], "a real exception wakes it, and its run carries the notes"


# ------------------------------------------------------------------------------------------- what the reader may read
# A private ticket of bots outside the manager's org chart (backend/tests/test_api.py unrelated_task_managers) is
# neither named nor counted: not in a builder's slots, a step's backlog or the GitHub outage, in Pipeline or the hourly
# wake. `finance` is a pool bot the manager does not manage; `ops` asks for its hidden tickets.

def unread(c, typ, title, step="In QA", owner="bot:finance"):
    """A private ticket whose parties the manager neither is nor manages."""
    made = H.task_create(c, "bot:ops", title, "Build.", owner, type=typ["id"], step=step, lint=False)
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (made["id"],))
    settled(c)
    return H.task(c, made["id"])


def outside_the_chart(c, typ):
    from backend.tests.test_api import unrelated_task_managers
    unrelated_task_managers(c, "coo", "ops", "finance", "cpo")
    hidden = unread(c, typ, "Hidden probe")
    assert not H.bot_reads_task(c, "bot:coo", hidden) and H.bot_reads_task(c, "bot:finance", hidden)
    c.execute("UPDATE tasks SET status='closed' WHERE id=?", (hidden["id"],))


def flow(c, typ, step, **value):
    c.execute("UPDATE task_steps SET flow=? WHERE type_id=? AND name=?", (json.dumps(value), typ["id"], step))


def both_reads(api, c, typ, kind):
    """(Pipeline's list, the hourly wake's list) for the manager, each of `kind`."""
    shown = pipeline_stats.report(c, api.app.state.auth, Identity("bot:coo", "bot"), type=typ["id"])["exceptions"]
    c.execute("DELETE FROM events WHERE action=?", (PE.EVENT,))      # every entry is new to this pass
    before = c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
    PE.deliver(c)
    body = (c.execute("SELECT body FROM messages WHERE rowid>? AND recipient_id='bot:coo' AND "
                      "json_extract(refs,'$.wake')='exceptions'", (before,)).fetchone() or [""])[0]
    return [e for e in shown if e["kind"] == kind], body


def test_slots_count_only_the_tickets_the_manager_reads_in_pipeline_and_the_hourly_wake(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        outside_the_chart(c, typ)
        flow(c, typ, "In Progress", assign={"pool": ["finance"], "slots": 1})
        for n in range(3):
            unread(c, typ, f"Hidden slot {n}")
        shown, body = both_reads(api, c, typ, "slots")
        assert shown == [] and "finance holds" not in body, "no visible ticket: no private workload count"
        mine = ticket(c, typ, "Visible slot", step="In QA")
        c.execute("UPDATE tasks SET owner_id='bot:finance' WHERE id=?", (mine["id"],))
        shown, body = both_reads(api, c, typ, "slots")
        assert shown == [] and "finance holds" not in body, "one readable ticket is within its one slot"
        # Authorized positive: a second readable ticket is over the slot, and the count is of readable ones only.
        other = ticket(c, typ, "Visible slot 2", step="QA Fixes")
        c.execute("UPDATE tasks SET owner_id='bot:finance' WHERE id=?", (other["id"],))
        shown, body = both_reads(api, c, typ, "slots")
        assert [e["cause"].split(",")[0] for e in shown] == ["finance holds 2 tickets"]
        assert "finance holds 2 tickets" in body


def test_a_steps_backlog_counts_only_the_tickets_the_manager_reads_in_pipeline_and_the_hourly_wake(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        outside_the_chart(c, typ)
        flow(c, typ, "In QA", backlog={"over": 1})
        unread(c, typ, "Hidden backlog 1")
        unread(c, typ, "Hidden backlog 2")
        ticket(c, typ, "Visible backlog 1", step="In QA")
        shown, body = both_reads(api, c, typ, "backlog")
        assert shown == [] and "over its backlog" not in body, "one readable ticket is not over a backlog of 1"
        ticket(c, typ, "Visible backlog 2", step="In QA")
        shown, body = both_reads(api, c, typ, "backlog")
        assert [e["cause"] for e in shown] == ["2 tickets at In QA, over its backlog of 1"]
        assert "2 tickets at In QA, over its backlog of 1" in body


def test_the_github_outage_folds_in_only_the_tickets_the_manager_reads_in_pipeline_and_the_hourly_wake(api):
    from backend import move_retry
    with api.app.state.store.transaction() as c:
        typ = board(c)
        outside_the_chart(c, typ)
        early, late = H.shift(H.now(), minutes=-30), H.shift(H.now(), minutes=-10)

        def held(row, since):
            c.execute("INSERT INTO task_events(id,task_id,created_at,actor,field,old,new,note) VALUES(?,?,?,?,?,?,?,'')",
                      (H.new_id(), row["id"], H.shift(H.now(), seconds=1), H.KEEPER, move_retry.FIELD,
                       row["step_id"], json.dumps({"state": "waiting", "outage": True, "since": since,
                                                   "step": "Review 1", "kind": "github"})))

        hidden = unread(c, typ, "Hidden outage")
        held(hidden, early)
        shown, body = both_reads(api, c, typ, "outage")
        assert [e for e in shown if e["key"] == "service:github:outage"] == [] and "GitHub cannot be read" not in body
        mine = ticket(c, typ, "Visible outage", step="In QA")
        held(mine, late)
        shown, body = both_reads(api, c, typ, "outage")
        [entry] = [e for e in shown if e["key"] == "service:github:outage"]
        assert "retries 1 ticket(s)" in entry["cause"] and entry["since"] == late, "the hidden one's count and since"
        assert entry["tickets"] == [mine.get("number") or mine["id"]]
        assert "retries 1 ticket(s)" in body
