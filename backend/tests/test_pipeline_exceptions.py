"""Head of Engineering works by exception (backend/pipeline_exceptions.py): one list of what did not move on its own,
read about hourly, and no wake for a ticket's routine steps, subtask results, checks or verdicts."""
import json

from backend import check_pools, pipeline_exceptions as PE, pipeline_stats, rules, step_age, step_backlog
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type

HEAD = "a" * 40


def board(c, profile="manager"):
    found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
    typ = H.type_get(c, found[0]) if found else dev_type(c)
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    c.execute("UPDATE bot_config SET config_json=? WHERE bot='coo'",
              (encode({"name": "coo", "runtime": "fake", "status": "active", "profile": profile}),))
    if not c.execute("SELECT 1 FROM runners WHERE id='r1'").fetchone():
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created,last_seen) VALUES('r1','box','ana','h',?,?)",
                  (H.now(), H.now()))
    return H.type_get(c, typ["id"])


def settled(c):
    """Every wake so far has had its run: what is left queued is what a test adds."""
    c.execute("UPDATE jobs SET state='done' WHERE state='queued'")


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
        c.execute("UPDATE tasks SET created=?, updated=? WHERE id=?", (then, then, row["id"]))
        c.execute("UPDATE task_events SET ts=? WHERE task_id=?", (then, row["id"]))
    return H.task(c, row["id"])


def run(c, row, state, minutes_ago, stop=None):
    """One finished attempt of the owner's on the task's lane, `minutes_ago`."""
    when = H.shift(H.now(), minutes=-minutes_ago)
    msg = H._write_message(c, "human:ana", row["owner"], "go", H.conversation(c, row["conversation_id"]), "say",
                           {"task": row["id"]}, None, None)
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (msg["id"],)).fetchone()[0]
    c.execute("UPDATE jobs SET state='done' WHERE id=?", (job,))
    aid = H.new_id()
    c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,started,"
              "finished,lane,stop_requested) VALUES(?,?,?,'r1',1,?,?,?,?,?,?,?,?)",
              (aid, job, H.actor_id(row["owner"]), aid, state, when, when, when, when, "task:" + row["id"], stop))
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
    return c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:coo' AND json_extract(refs_json,'$.task')=? "
                     "AND json_extract(refs_json,'$.step_late')", (row["id"],)).fetchone()[0]


def unheld_jobs(c, slug):
    return c.execute("SELECT count(*) FROM jobs WHERE bot=? AND state='queued' AND coalesce(held,0)=0",
                     (slug,)).fetchone()[0]


def test_a_ticket_with_no_run_for_45_minutes_is_listed_once_with_its_owner_and_since_when(api):
    with api.app.state.store.transaction() as c:
        typ = board(c)
        quiet, fresh, snoozed = ticket(c, typ, "Fix Cursor", minutes=50), ticket(c, typ, "Fix Muse", minutes=20), \
            ticket(c, typ, "Fix Grok", minutes=50)
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), hours=5), snoozed["id"]))
        [entry] = listed(c, typ, "stuck")
        assert entry["ticket"]["id"] == quiet["id"] and entry["ticket"]["owner"] == "cpo"
        assert "owner cpo" in entry["cause"]
        assert entry["since"] == H.task(c, quiet["id"])["created"], "since it last had a run or entered the step"
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
        c.execute("UPDATE task_events SET ts=? WHERE task_id=?", (H.shift(H.now(), hours=-6), check["id"]))
        [entry] = listed(c, typ, "stuck")
        assert entry["ticket"]["id"] == row["id"] and entry["check"]["id"] == check["id"]
        assert entry["check"]["owner"] == "ops" and "owner ops" in entry["cause"]


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
            c.execute("INSERT INTO refusals(id,ts,actor,rule,detail_json,severity) VALUES(?,?,?,?,?,?)",
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
        [wake] = c.execute("SELECT body, refs_json FROM messages WHERE to_actor='bot:coo' AND "
                           "json_extract(refs_json,'$.wake')='exceptions'").fetchall()
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
        row = ticket(c, typ, title)
        check = check_of(c, row, "Review 1: PR #7")
        H.task_comment(c, "bot:ops", check["id"], "VERDICT: approve at " + HEAD)
        H.task_update(c, "bot:ops", check["id"], status="done", note="approve")
        H.task_update(c, "bot:cpo", row["id"], step="QA Fixes")
        H.task_update(c, "bot:cpo", row["id"], status="waiting", note="Waits on QA")
        H.task_update(c, "bot:cpo", row["id"], status="doing", note="Back on it")
        # Its own words: the same notice again is dropped as a repeat, which would hide a wake.
        H._wake(c, H.task(c, row["id"]), "bot:coo", f"A setup blocker on {title} needs you.", {"wake": "blocker"})
        late = ticket(c, typ, title + " late")
        c.execute("UPDATE task_events SET ts=? WHERE task_id=?", (H.shift(H.now(), hours=-5), late["id"]))
        c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.shift(H.now(), hours=-5), late["id"]))
        step_age.sweep(c, H.shift(H.now(), minutes=1))
        H.task_update(c, "bot:cpo", row["id"], status="done", note="Shipped")
        return late

    with api.app.state.store.transaction() as c:
        typ = board(c, profile="lead")
        before = unheld_jobs(c, "coo")
        late = routine(c, typ, "Fix Cursor")
        assert unheld_jobs(c, "coo") > before, "control: the Lead profile is woken for the blocker and the late one"
        assert step_backlog._targets(c, {"notify": ["manager"], "per": "step"}, late, late["owner"]) == ["bot:coo"]
        assert late_notices(c, late) == 1

        typ = board(c, profile="manager")
        settled(c)
        late = routine(c, typ, "Fix Muse")
        assert unheld_jobs(c, "coo") == 0, "no routine event woke the manager"
        assert step_backlog._targets(c, {"notify": ["manager"], "per": "step"}, late, late["owner"]) == []
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
        c.execute("UPDATE task_events SET ts=? WHERE task_id=?", (H.shift(H.now(), minutes=-120), check["id"]))
        run(c, check, "completed", 60)
        before = unheld_jobs(c, "ops")
        assert step_age.turn_ended(c) == [check["id"]]
        assert unheld_jobs(c, "ops") == before + 1, "a reviewer's profile would hold a status check; this one wakes"
        c.execute("UPDATE jobs SET state='done' WHERE bot='ops'")
        assert step_age.turn_ended(c) == [], "once per stay at the step"
        [entry] = listed(c, typ, "stuck")
        assert entry["check"]["id"] == check["id"]
        assert rules.EVENTS["exceptions"]
