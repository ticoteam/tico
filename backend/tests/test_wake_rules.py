"""Token saving 2/9 (Dev 140): a parked task wakes its bot only when what it waits on arrives, and a bot's own writes
never wake it. Nothing is dropped: a skipped wake reaches the bot's next run, in order, whatever starts it."""

import json

import pytest

from backend import hubdb as H
from backend import wake_rules as W
from backend.tests.helpers.api import post
from backend.tests.helpers.wake_rules import as_cause, claim, finish, jobs, news, parked_task, setup, skips


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
            "bot": "cpo",
            "task": task["id"],
            "rule": "parked",
            "trigger": "status_or_relation",
        }


def test_t2_a_comment_on_a_related_task_does_not_wake_the_parked_tasks_bot(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        related = H.task_create(c, "bot:ops", "Docs for 2/9", "Write them", "bot:ops")
        c.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'related',?)",
            (*sorted((related["id"], task["id"])), H.now()),
        )
        c.execute("DELETE FROM jobs")
        H.task_comment(c, "bot:ops", related["id"], "Docs drafted.")
        with as_cause("bot:ops"):
            news(c, task, f"Related task changed: {related['title']}")
        assert jobs(c, "cpo") == 0


def test_a_skipped_wake_reaches_the_next_run_whatever_starts_it(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:ops"):
            held = news(c, task)
    post(api, "chat/cpo", {"text": "Morning, anything for me?"})
    attempt = claim(api, r, "cpo")
    assert [n["message"] for n in attempt["notes"] if n.get("message")] == [held["id"]]


def test_held_news_reaches_the_run_in_arrival_order_whoever_sent_it_across_the_batch_boundary(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        bot_comment = H.task_comment(c, "bot:ops", task["id"], "Scope add: log every skip.")
        with as_cause("bot:ops"):
            notices = [news(c, task, f"Relation added: Docs {i}") for i in range(50)]
        # Tico's own urgent note (a lifted quarantine's) still comes first, though it is the newest.
        urgent = H.new_id()
        c.execute(
            "INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)",
            (urgent, H.KEEPER, "bot:cpo", "Back from quarantine", H.now()),
        )
        assert jobs(c, "cpo") == 0
        H.task_comment(c, "human:ana", task["id"], "Please start this today.")
    first = claim(api, r, "cpo")
    assert first["notes"][0]["id"] == urgent
    carried = [n["message"] for n in first["notes"] if n.get("message")]
    held = [bot_comment["id"], *(m["id"] for m in notices)]
    assert carried == held[: len(carried)] and len(first["notes"]) == 50
    finish(api, r, first)
    second = claim(api, r, "cpo")
    assert [n["message"] for n in second["notes"] if n.get("message")] == held[len(carried) :]


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
    said = [
        post(api, f"tasks/{t['id']}/comments", {"text": "Started: outline drafted."}, token=attempt["token"])
        for t in (task, mine)
    ]
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == before
        logged = skips(c)
    assert said[0]["woke"] is False
    assert [(s["target"], {k: s["detail"][k] for k in ("bot", "task", "rule", "trigger")}) for s in logged] == [
        (said[0]["comment"]["id"], {"bot": "cpo", "task": task["id"], "rule": "own_write", "trigger": "comment"}),
        (said[1]["comment"]["id"], {"bot": "cpo", "task": mine["id"], "rule": "own_write", "trigger": "comment"}),
    ]


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
    # a bot's plain comment reaches no person who does not follow the task; one that @mentions Ana is delivered, and
    # neither takes a bot turn
    plain = post(api, f"tasks/{mine['id']}/comments", {"text": "Done."}, token=attempt["token"])
    assert plain["woke"] is False and plain["delivered"] is False
    told = post(api, f"tasks/{mine['id']}/comments", {"text": "Done, @ana."}, token=attempt["token"])
    assert told["woke"] is False and told["delivered"] is True
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
            for body in (
                "Step moved: In Progress -> Review 1",
                "Status: review",
                "Relation added: Launch copy",
                "Checkpoint saved",
            ):
                msg = news(c, task, body)
                # The bot's own run already knows what it wrote: ignored, with no note, under the rule `own_write`.
                assert msg["refs"][W.MARK] == "ignored" and msg["refs"]["rule"] == "own_write", body
        assert jobs(c, "cpo") == 0 and not c.execute("SELECT 1 FROM notes WHERE to_actor='bot:cpo'").fetchone()
        assert {s["detail"]["rule_id"] for s in skips(c)} == {"own_write"} and len(skips(c)) == 4


def test_own_writes_still_deliver_what_another_session_waits_for(api):
    # A bot finishing its own last subtask must still wake the task that waits (PR #657).
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:cpo"):
            for refs in (
                {"subtask": "s1"},
                {"worktree_wait": "w1"},
                {"wake": "watcher"},
                {"wake": "watcher-done"},
            ):
                msg = news(c, task, "Done", refs=refs)
                assert not msg["refs"].get(W.MARK), refs
            H._wake(c, task, "bot:cpo", "Checks finished on PR #3", trigger="pr_checks")
        assert jobs(c, "cpo") == 5 and skips(c) == []


def test_an_assignment_the_bot_filed_for_itself_still_starts_its_session(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        with as_cause("bot:cpo"):
            H.task_create(c, "bot:cpo", "Follow up on the launch", "Check the numbers", "bot:cpo")
        assert jobs(c, "cpo") == 1 and skips(c) == []


# ----------------------------------------------------------------------------- always wake


def test_t4_a_persons_message_and_comment_always_wake_a_parked_bot(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        H.say(c, "human:ana", "bot:cpo", "Can you look at the roadmap today?")
        assert jobs(c, "cpo") == 1
        H.task_comment(c, "human:ana", task["id"], "Is this still blocked?")
        assert jobs(c, "cpo") == 2
        # A Tico notice of a person's own act on the task (they moved it) wakes it too.
        with as_cause("human:ana"):
            news(c, task, "Moved by Ana: To do -> Done")
        assert jobs(c, "cpo") == 3
        assert skips(c) == []


def test_each_always_wake_case_is_never_filtered_even_on_a_parked_task_by_its_own_bot(api):
    # One story for every always-wake case, each on its own fresh parked task (one setup, not one per case).
    cases = [
        ("say", {}, "Hi", "person"),
        ("ask", {}, "Which release?", "ask"),
        ("answer", {}, "v0.3.62", "ask"),
        ("steer", {}, "Stop and fix the tests", "ask"),
        ("notice", {"approval": "a1"}, "Approved", "approval"),
        ("notice", {"questions": [{"id": "q"}]}, "Pick one", "approval"),
        ("notice", {"needs_action": True}, "Blocked on you", "needs_action"),
    ]
    setup(api)
    with api.app.state.store.transaction() as c:
        for n, (kind, refs, body, key) in enumerate(cases):
            task, _ = parked_task(c, title=f"Build case {n}", blocker_title=f"Build 1/9 case {n}")
            actor = "human:ana" if key == "person" else H.KEEPER
            cause = "human:ana" if key == "person" else "bot:cpo"
            assert W.always_wakes(c, actor, "bot:cpo", kind, refs, body, cause) == key, body
            assert W.rule_for(c, actor, "bot:cpo", kind, {**refs, "task": task["id"]}, body, task, cause) is None, body


def test_a_reply_the_parked_bot_waits_on_wakes_it(api):
    # A plain reply passes bot_to_bot.can_wait as urgent; the parked rule must not then hold it.
    setup(api)
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        assert W.parked(c, task) == "wake_once"
        room = task["conversation_id"]
        asked = [
            H.say(c, "bot:cpo", "bot:ops", "Need the build now", room, refs={"task": task["id"], "needs_action": True}),
            H.say(c, "bot:cpo", "bot:ops", "Send me the log", room, refs={"task": task["id"]}, wait_s=600),
        ]
        c.execute("DELETE FROM jobs")
        for parent in asked:
            before = jobs(c, "cpo")
            reply = H.say(
                c, "bot:ops", "bot:cpo", "Here you go", room, refs={"task": task["id"]}, in_reply_to=parent["id"]
            )
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
        assert [key for key, _ in W.ALWAYS_WAKE] == [
            "task_turn",
            "keep_warm",
            "person",
            "manager",
            "ask",
            "approval",
            "needs_action",
            "mention",
            "reply",
        ]
        # A bot's plain fyi to a bot waits (Dev 149); the same words with an always-wake reference do not.
        assert B.can_wait(c, "bot:ops", "bot:cpo", "say", {}, "fyi") is True
        assert B.can_wait(c, "bot:ops", "bot:cpo", "say", {"run_now": True}, "fyi") is False


def test_the_gate_fails_open():
    class Broken:
        def execute(self, *a):
            raise RuntimeError("no database")

    assert (
        W.gate(Broken(), H.KEEPER, "bot:cpo", "notice", {"task": "t1", "trigger": "comment"}, "x", {"id": "c"}) is None
    )


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
    post(api, f"tasks/{task['id']}/comments", {"text": "@ops outline drafted."}, token=attempt["token"])
    post(api, f"tasks/{task['id']}/comments", {"text": "Looks good, carry on."})
    assert ("bot:cpo", "bot:ops", "bot:cpo") in seen and ("human:ana", "bot:cpo", "human:ana") in seen
    assert W.CAUSE.get() == ""
