"""Token saving 2/9 (Dev 140): a parked task wakes its bot only when what it waits on arrives, and a bot's own writes
never wake it. Nothing is dropped: a skipped wake reaches the bot's next run, in order, whatever starts it."""

import json

import pytest

from backend import hubdb as H
from backend import wake_rules as W
from backend.tests.test_api import api, assign, post, ready, runner  # noqa: F401


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    assign(api, r, "cpo")
    ready(api, r, ["ops", "cpo"])
    return r


def claim(api, r, bot):
    return post(api, "jobs/claim", {"next_run": True, "bot": bot}, token=r["token"])["attempt"]


def finish(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, token=r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "ok", "last_seq": 0},
         token=r["token"])


def jobs(c, bot):
    return c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (bot,)).fetchone()[0]


def skips(c):
    return [{**dict(row), "detail": json.loads(row["detail"])} for row in c.execute(
        "SELECT * FROM events WHERE action=? ORDER BY created_at, rowid", (W.SKIPPED,))]


def parked_task(c, title="Build 2/9"):
    """cpo's task, filed by ops, blocked by an open task of ops's."""
    blocker = H.task_create(c, "bot:ops", "Build 1/9", "First", "bot:ops")
    task = H.task_create(c, "bot:ops", title, "Second", "bot:cpo")
    c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)",
              (blocker["id"], task["id"], H.now()))
    c.execute("DELETE FROM jobs")
    return H.task(c, task["id"]), blocker


def as_cause(actor):
    """Run the next writes as `actor`'s request, as backend/app.py request_guard does."""
    class Cause:
        def __enter__(self):
            self.token = W.CAUSE.set(actor)

        def __exit__(self, *exc):
            W.CAUSE.reset(self.token)
    return Cause()


def news(c, task, body="Step moved: In Progress -> Review 1", refs=None):
    """A keeper notice about `task` to its owner, the kind a step move or relation change writes."""
    return H._wake(c, task, task["owner_id"], body, refs=refs)


# ----------------------------------------------------------------------------- parked: waiting means asleep

def test_news_about_a_parked_task_waits_and_is_logged(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:ops"):
            msg = news(c, task)
        assert jobs(c, "cpo") == 0
        assert msg["refs"]["quiet"] is True and msg["refs"][W.MARK] == "parked"
        logged = skips(c)
        assert len(logged) == 1 and logged[0]["target"] == msg["id"]
        assert {k: logged[0]["detail"][k] for k in ("bot", "task", "rule", "trigger")} == {
            "bot": "cpo", "task": task["id"], "rule": "parked", "trigger": "status_or_relation"}


def test_a_task_parked_on_wake_once_sleeps_too(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Ship the export", "Build it", "bot:cpo")
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,detail) "
                  "VALUES('l1',?,'pr','https://github.com/o/r/pull/1','PR','open','bot:cpo',?,?)",
                  (task["id"], H.now(), json.dumps({"wake_once": {"by": "bot:cpo", "at": H.now()}})))
        c.execute("DELETE FROM jobs")
        assert W.parked(c, H.task(c, task["id"])) == "wake_once"
        with as_cause("bot:ops"):
            msg = news(c, H.task(c, task["id"]))
        assert jobs(c, "cpo") == 0 and msg["refs"][W.MARK] == "parked"
        # Its result is what it waits for: that wakes it.
        H._wake(c, H.task(c, task["id"]), "bot:cpo", "Checks finished: all green", trigger="pr_checks")
        assert jobs(c, "cpo") == 1


def test_t2_a_comment_on_a_related_task_does_not_wake_the_parked_tasks_bot(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        related = H.task_create(c, "bot:ops", "Docs for 2/9", "Write them", "bot:ops")
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'related',?)",
                  (*sorted((related["id"], task["id"])), H.now()))
        c.execute("DELETE FROM jobs")
        H.task_comment(c, "bot:ops", related["id"], "Docs drafted.")
        with as_cause("bot:ops"):
            news(c, task, f"Related task changed: {related['title']}")
        assert jobs(c, "cpo") == 0


def test_the_blocker_finishing_wakes_the_parked_task_and_that_run_carries_what_waited_in_order(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, blocker = parked_task(c)
        with as_cause("bot:ops"):
            first = news(c, task, "Step moved: On Deck -> In Progress")
            second = news(c, task, "Relation added: Docs for 2/9")
        assert jobs(c, "cpo") == 0
        H.task_update(c, "bot:ops", blocker["id"], status="done")
        woke = [dict(row) for row in c.execute(
            "SELECT m.body, m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='cpo'")]
    assert len(woke) == 1 and woke[0]["body"].startswith("Unblocked:")
    assert json.loads(woke[0]["refs"])["blocker_done"] == blocker["id"]
    attempt = claim(api, r, "cpo")
    assert attempt["task"]["id"] == task["id"]
    assert [n["message"] for n in attempt["notes"] if n.get("message")] == [first["id"], second["id"]]
    held = [n for n in attempt["notes"] if n.get("message") == first["id"]][0]
    assert "On Deck -> In Progress" in held["text"] and f"({task['id']})" in held["text"]
    with api.app.state.store.read() as c:
        assert all(c.execute("SELECT read_at FROM messages WHERE id=?", (m,)).fetchone()[0]
                   for m in (first["id"], second["id"]))


def test_a_skipped_wake_reaches_the_next_run_whatever_starts_it(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:ops"):
            held = news(c, task)
    post(api, "chat/cpo", {"text": "Morning, anything for me?"})
    attempt = claim(api, r, "cpo")
    assert [n["message"] for n in attempt["notes"] if n.get("message")] == [held["id"]]


def test_lead_case_a_held_bot_comment_then_a_persons_comment_the_run_shows_both_in_order(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        bot_comment = H.task_comment(c, "bot:ops", task["id"], "Scope add: log every skip.")
        assert jobs(c, "cpo") == 0 and bot_comment["refs"].get("quiet")
        person = H.task_comment(c, "human:ana", task["id"], "Please start this today.")
        assert jobs(c, "cpo") == 1
    attempt = claim(api, r, "cpo")
    assert attempt["message"]["id"] == person["id"]
    notes = [n for n in attempt["notes"] if n.get("message")]
    assert [n["message"] for n in notes] == [bot_comment["id"]] and notes[0]["sent"] <= person["created_at"]


def test_held_news_reaches_the_run_in_arrival_order_whoever_sent_it_across_the_batch_boundary(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        bot_comment = H.task_comment(c, "bot:ops", task["id"], "Scope add: log every skip.")
        with as_cause("bot:ops"):
            notices = [news(c, task, f"Relation added: Docs {i}") for i in range(50)]
        # Tico's own urgent note (a lifted quarantine's) still comes first, though it is the newest.
        urgent = H.new_id()
        c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)",
                  (urgent, H.KEEPER, "bot:cpo", "Back from quarantine", H.now()))
        assert jobs(c, "cpo") == 0
        H.task_comment(c, "human:ana", task["id"], "Please start this today.")
    first = claim(api, r, "cpo")
    assert first["notes"][0]["id"] == urgent
    carried = [n["message"] for n in first["notes"] if n.get("message")]
    held = [bot_comment["id"], *(m["id"] for m in notices)]
    assert carried == held[:len(carried)] and len(first["notes"]) == 50
    finish(api, r, first)
    second = claim(api, r, "cpo")
    assert [n["message"] for n in second["notes"] if n.get("message")] == held[len(carried):]


# ----------------------------------------------------------------------------- own writes never wake

def test_t3_a_bots_own_comment_never_wakes_it_and_the_skip_is_logged(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        mine = H.task_create(c, "human:ana", "Plan the launch", "Plan it", "bot:cpo")
        c.execute("DELETE FROM jobs")
    post(api, "chat/cpo", {"text": "Run your check"})
    attempt = claim(api, r, "cpo")
    with api.app.state.store.read() as c:
        before = jobs(c, "cpo")
    said = [post(api, f"tasks/{t['id']}/comments", {"text": "Started: outline drafted."}, token=attempt["token"])
            for t in (task, mine)]
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == before
        logged = skips(c)
    assert said[0]["woke"] is False
    assert [(s["target"], {k: s["detail"][k] for k in ("bot", "task", "rule", "trigger")}) for s in logged] == [
        (said[0]["comment"]["id"], {"bot": "cpo", "task": task["id"], "rule": "own_write", "trigger": "comment"}),
        (said[1]["comment"]["id"], {"bot": "cpo", "task": mine["id"], "rule": "own_write", "trigger": "comment"})]


def test_woke_means_a_bot_job_was_queued_and_delivered_means_a_person_got_it(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        mine = H.task_create(c, "human:ana", "Plan the launch", "Plan it", "bot:cpo")
        c.execute("DELETE FROM jobs")
    post(api, "chat/cpo", {"text": "Run your check"})
    attempt = claim(api, r, "cpo")
    with api.app.state.store.read() as c:
        before = jobs(c, "cpo")
    # a bot comments on a task whose recipient is a person: delivered, no bot turn
    told = post(api, f"tasks/{mine['id']}/comments", {"text": "Done, Ana."}, token=attempt["token"])
    assert told["comment"]["recipient_id"] == "human:ana" and told["woke"] is False and told["delivered"] is True
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == before
    # a person comments to a bot owner: one job
    asked = post(api, f"tasks/{mine['id']}/comments", {"text": "Please start this today."})
    assert asked["woke"] is True and asked["delivered"] is False
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == before + 1


def test_a_notice_the_bots_own_write_caused_waits_on_any_of_its_tasks(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Plan the launch", "Plan it", "bot:cpo")
        c.execute("DELETE FROM jobs")
        with as_cause("bot:cpo"):
            for body in ("Step moved: In Progress -> Review 1", "Status: review", "Relation added: Launch copy",
                         "Checkpoint saved"):
                msg = news(c, task, body)
                # The bot's own run already knows what it wrote: ignored, with no note, under the rule `own_write`.
                assert msg["refs"][W.MARK] == "ignored" and msg["refs"]["rule"] == "own_write", body
        assert jobs(c, "cpo") == 0 and not c.execute("SELECT 1 FROM notes WHERE to_actor='bot:cpo'").fetchone()
        assert {s["detail"]["rule_id"] for s in skips(c)} == {"own_write"} and len(skips(c)) == 4


def test_own_writes_still_deliver_what_another_session_waits_for(api):
    # A bot finishing its own blocker or its own last subtask must still wake the task that waits (PR #657).
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:cpo"):
            for refs in ({"blocker_done": "b1"}, {"subtask": "s1"}, {"worktree_wait": "w1"}, {"wake": "watcher"},
                         {"wake": "watcher-done"}):
                msg = news(c, task, "Done", refs=refs)
                assert not msg["refs"].get(W.MARK), refs
            H._wake(c, task, "bot:cpo", "Checks finished on PR #3", trigger="pr_checks")
        assert jobs(c, "cpo") == 6 and skips(c) == []


def test_an_assignment_the_bot_filed_for_itself_still_starts_its_session(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        with as_cause("bot:cpo"):
            H.task_create(c, "bot:cpo", "Follow up on the launch", "Check the numbers", "bot:cpo")
        assert jobs(c, "cpo") == 1 and skips(c) == []


# ----------------------------------------------------------------------------- always wake

def test_t4_a_persons_message_comment_and_approval_always_wake_a_parked_bot(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        H.say(c, "human:ana", "bot:cpo", "Can you look at the roadmap today?")
        assert jobs(c, "cpo") == 1
        H.task_comment(c, "human:ana", task["id"], "Is this still blocked?")
        assert jobs(c, "cpo") == 2
        approval = H.approval_request(c, "bot:cpo", "spend", {"account": "AWS", "what": "a test box", "amount": 5},
                                      task_id=task["id"])
        before = jobs(c, "cpo")
        H.approval_decide(c, "human:ana", approval["id"], "approved")
        assert jobs(c, "cpo") == before + 1
        # A keeper notice of a person's own act on the task (they moved its step) wakes it too.
        with as_cause("human:ana"):
            news(c, task, "Step moved by Ana: On Deck -> In Progress")
        assert jobs(c, "cpo") == before + 2
        assert skips(c) == []


@pytest.mark.parametrize("kind,refs,body,key", [
    ("say", {}, "Hi", "person"),
    ("ask", {}, "Which release?", "ask"),
    ("answer", {}, "v0.3.62", "ask"),
    ("steer", {}, "Stop and fix the tests", "ask"),
    ("notice", {"approval": "a1"}, "Approved", "approval"),
    ("notice", {"questions": [{"id": "q"}]}, "Pick one", "approval"),
    ("notice", {"needs_action": True}, "Blocked on you", "needs_action"),
    ("notice", {}, "Review done.\nVERDICT: changes PR #12 head " + "a" * 40, "gate"),
])
def test_each_always_wake_case_is_never_filtered_even_on_a_parked_task_by_its_own_bot(api, kind, refs, body, key):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        actor = "human:ana" if key == "person" else H.KEEPER
        cause = "human:ana" if key == "person" else "bot:cpo"
        assert W.always_wakes(c, actor, "bot:cpo", kind, refs, body, cause) == key
        assert W.rule_for(c, actor, "bot:cpo", kind, {**refs, "task": task["id"]}, body, task, cause) is None


@pytest.mark.parametrize("parked_on", ["blocked_by", "wake_once"])
def test_a_reply_the_parked_bot_waits_on_wakes_it(api, parked_on):
    # A plain reply passes bot_to_bot.can_wait as urgent; the parked rule must not then hold it.
    setup(api)
    with api.app.state.store.transaction() as c:
        if parked_on == "blocked_by":
            task, _ = parked_task(c)
        else:
            task = H.task_create(c, "bot:ops", "Ship the export", "Build it", "bot:cpo")
            c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,detail) "
                      "VALUES('l1',?,'pr','https://github.com/o/r/pull/1','PR','open','bot:cpo',?,?)",
                      (task["id"], H.now(), json.dumps({"wake_once": {"by": "bot:cpo", "at": H.now()}})))
            task = H.task(c, task["id"])
        assert W.parked(c, task) == parked_on
        room = task["conversation_id"]
        asked = [H.say(c, "bot:cpo", "bot:ops", "Which release has the fix?", room, kind="ask", refs={"task": task["id"]}),
                 H.say(c, "bot:cpo", "bot:ops", "Need the build now", room, refs={"task": task["id"], "needs_action": True}),
                 H.say(c, "bot:cpo", "bot:ops", "Send me the log", room, refs={"task": task["id"]}, wait_s=600)]
        c.execute("DELETE FROM jobs")
        for parent in asked:
            before = jobs(c, "cpo")
            reply = H.say(c, "bot:ops", "bot:cpo", "Here you go", room, refs={"task": task["id"]},
                          in_reply_to=parent["id"])
            assert jobs(c, "cpo") == before + 1 and not reply["refs"].get(W.MARK), parent["body"]
        # So does a malformed flag, which is not plain news. A reference no rule knows is never held as parked news;
        # no rule names that event, so it waits as a default note the next run carries.
        assert not news(c, task, "Moved", refs={"needs_action": "yes"})["refs"].get(W.MARK)
        assert news(c, task, "Moved", refs={"something_new": 1})["refs"]["rule"] != "parked"
        assert [s["detail"]["rule"] for s in skips(c)] == ["rule"]


def test_the_always_wake_list_is_one_list_the_bot_to_bot_rule_reads_first(api):
    from backend import bot_to_bot as B
    setup(api)
    with api.app.state.store.transaction() as c:
        assert [key for key, _ in W.ALWAYS_WAKE] == ["person", "manager", "ask", "approval", "needs_action", "reply", "gate", "handoff"]
        # A bot's plain fyi to a bot waits (Dev 149); the same words with an always-wake reference do not.
        assert B.can_wait(c, "bot:ops", "bot:cpo", "say", {}, "fyi") is True
        assert B.can_wait(c, "bot:ops", "bot:cpo", "say", {"run_now": True}, "fyi") is False


def test_sweeps_schedules_checks_and_blockers_keep_their_own_rules(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:cpo"):
            for body, refs, trigger in (("Status check", {"wake": "status_check"}, "stall_check"),
                                        ("Due today", {"wake": "due"}, "schedule"),
                                        ("Checks failed on PR #3", {}, "pr_checks"),
                                        ("Stuck: no worktree", {}, "status_or_relation"),
                                        ("New task from bot:ops: Build 3/9", {}, "status_or_relation")):
                msg = H._wake(c, task, "bot:cpo", body, refs=refs, trigger=trigger)
                assert not msg["refs"].get(W.MARK), body
        assert skips(c) == []


def test_the_gate_fails_open():
    class Broken:
        def execute(self, *a):
            raise RuntimeError("no database")
    assert W.gate(Broken(), H.KEEPER, "bot:cpo", "notice", {"task": "t1", "trigger": "comment"}, "x", {"id": "c"}) \
        is None


def test_the_weekly_audit_samples_skips_in_hub_sql(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:ops"):
            news(c, task)
    out = post(api, "sql", {"sql": "SELECT json_extract(detail,'$.bot') AS bot, json_extract(detail,'$.rule') "
                                   "AS rule, json_extract(detail,'$.task') AS task FROM events "
                                   "WHERE action='wake.skipped' ORDER BY random() LIMIT 50"})
    assert [dict(zip(out["columns"], row)) if isinstance(row, list) else row for row in out["rows"]] == [
        {"bot": "cpo", "rule": "parked", "task": task["id"]}]


def test_the_request_says_whose_write_it_is_and_the_keepers_sweeps_are_nobodys(api, monkeypatch):
    r = setup(api)
    post(api, "chat/cpo", {"text": "Run your check"})
    attempt = claim(api, r, "cpo")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Plan the launch", "Plan it", "bot:cpo")
    seen = []
    real = W.gate

    def spy(conn, actor, target, *rest):
        seen.append((actor, target, W.CAUSE.get()))
        return real(conn, actor, target, *rest)
    monkeypatch.setattr(W, "gate", spy)
    post(api, f"tasks/{task['id']}/comments", {"text": "Outline drafted."}, token=attempt["token"])
    post(api, f"tasks/{task['id']}/comments", {"text": "Looks good, carry on."})
    assert ("bot:cpo", "bot:ops", "bot:cpo") in seen and ("human:ana", "bot:cpo", "human:ana") in seen
    assert W.CAUSE.get() == ""
