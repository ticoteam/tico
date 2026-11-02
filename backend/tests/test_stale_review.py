"""Stale ticket review (backend/stale_review.py): an hourly plain-code pass finds stale tasks, a faked Decide judges only
the new ones, a bot owner gets one "Stale check" comment, a person one Needs you card whose Accept is their own change,
and a pass with nothing new writes nothing but the watermark. Spec section A acceptance tests 1-8, synthetic fixtures."""

import json

import pytest

from backend import hubdb as H
from backend import stale_review as SR
from backend import still_wanted, team_rules
from backend.tests.test_tasks_board import api, get, headers, post   # noqa: F401
from clients import judge as J


class FakeDecide:
    """Answers `verdict` as told, as the Gateway would; `fail` raises instead."""

    def __init__(self, verdict="update", confidence=0.9, provider=J.GATEWAY_PROVIDER):
        self.verdict, self.confidence, self.provider = verdict, confidence, provider
        self.calls, self.fail = [], None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.fail:
            raise self.fail
        return {"model": J.GATEWAY_MODEL, "provider": self.provider, "usage": {"input_tokens": 300}, "ms": 90,
                "cost_usd": 0.00002,
                "answers": {"verdict": {"type": "choice", "choice": self.verdict, "confidence": self.confidence}}}


@pytest.fixture
def stale(api):
    """The switch on, and a task maker that backdates a task so it is not quiet by accident."""
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": True})

    def task(title, owner="bot:ops", status="review", requester="human:ana", days_ago=1, pr=None):
        with store.transaction() as c:
            row = H.task_create(c, requester, "Placeholder " + H.new_id(), "x", owner)
            when = H.shift(H.now(), days=-days_ago)
            c.execute("UPDATE tasks SET title=?,status=?,updated_at=?,created_at=? WHERE id=?",
                      (title, status, when, when, row["id"]))
            if pr:
                c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,pr_merged_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?)",
                          (H.new_id(), row["id"], "pr", "https://github.com/acme/site/pull/12", "PR", pr, "bot:ops",
                           when, "2026-10-16T10:00:00Z"))
        return row["id"]
    return api, store, task


def calls(store, label="stale-review@1"):
    with store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail FROM events WHERE action='judge.call' AND target=? ORDER BY rowid", (label,))]


def passes(store):
    with store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail FROM events WHERE action='stale_review.pass' ORDER BY rowid")]


def comments(store, task_id):
    with store.read() as c:
        return [m["body"] for m in H.task_comments(c, task_id) if m["body"].startswith("Stale check:")]


def hours(n):
    return H.shift(H.now(), hours=n)


def stale_card(api, token="ana-test"):
    return next(i for i in get(api, "needs-you", token=token)["items"] if i["kind"] == "stale")


def shown(card):
    """What the card's Accept all sends: the rows it showed with an action, and their task versions then."""
    return {"rows": [{"id": r["id"], "version": r["version"]} for r in card["rows"] if r["action"]]}


def start_turn(c, task_id):
    """A bot's turn on the task begins, as a runner's claim writes it: a running attempt and its turn, while the task
    row itself stays as it was."""
    other = H.task_create(c, "human:ana", "Elsewhere " + H.new_id(), "x", "bot:ops")
    H.task_comment(c, "human:ana", other["id"], "Please look at this")
    job = c.execute("SELECT id FROM jobs WHERE bot='ops' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    c.execute("INSERT OR IGNORE INTO runners(id,label,operator,token_hash,created_at) VALUES('r1','r','ana','h','t')")
    aid = H.new_id()
    c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at) "
              "VALUES(?,?,'ops','r1',1,?,'running',?,?)", (aid, job, aid, H.shift(H.now(), seconds=600), H.now()))
    c.execute("INSERT INTO turns(id,bot,started_at,task_id) VALUES(?,'ops',?,?)", (aid, H.now(), task_id))


def an_hour_on(store):
    """The next hour, as the pass sees it: the last pass and this hour's calls are an hour old."""
    with store.transaction() as c:
        c.execute("UPDATE events SET ts=? WHERE action='judge.call'", (hours(-1.01),))
        SR._set_watermark(c, hours(-1.01))


def seed_calls(store, n, label="stale-review@1"):
    with store.transaction() as c:
        for _ in range(n):
            H.event(c, H.KEEPER, "judge.call", label, {"label": label, "verdict": {"value": "leave"}})


def test_a_review_task_with_a_merged_pr_gets_one_stale_check_comment_to_its_bot(stale):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    done = SR.run_pass(store, engine=model)
    assert done["calls"] == 1 and done["suggested"] == 1
    assert comments(store, tid) == ["Stale check: PR #12 merged Oct 16. Suggest: mark done."]
    with store.read() as c:
        said = c.execute("SELECT to_actor FROM messages WHERE body LIKE 'Stale check:%'").fetchone()[0]
    assert said == "bot:ops", "addressed to the bot that owns the task, so it is woken on it"
    # Decide saw only the title, status, step, dates, the reason and the last comments.
    state = model.calls[0]["state"]["task"]
    assert set(state) == {"title", "status", "step", "created_at", "updated_at", "due", "reason", "comments"}
    assert state["reason"] == "PR #12 merged Oct 16" and model.calls[0]["label"] == "stale-review@1"
    (audit,) = calls(store)
    assert audit["task"] == tid and audit["verdict"] == {"value": "update", "confidence": 0.9}
    (summary,) = passes(store)
    assert summary["routes"] == {"gateway": 1} and summary["calls"] == 1 and summary["cost_usd"] == 0.00002


def test_the_audit_keeps_what_decide_answered_and_the_readout_spreads_it(stale):
    """Below the threshold the task is left, but the `judge.call` row keeps Decide's own answer beside the verdict."""
    from backend.decide_readout import readout
    api, store, task = stale
    task("Ship the export button", pr="merged")
    SR.run_pass(store, engine=FakeDecide("close", 0.4))
    (audit,) = calls(store)
    assert audit["verdict"] == {"value": "leave", "confidence": 0.4}, "the verdict row is unchanged"
    assert audit["answers"] == {"verdict": {"type": "choice", "value": "close", "confidence": 0.4}}
    assert set(audit) == {"label", "questions", "types", "task", "ms", "model", "usage", "cost_usd", "provider",
                          "verdict", "answers"}
    with store.read() as c:
        out = readout(c, store.settings)
    (row,) = [r for r in out["sets"] if r["set"] == "stale-review"]
    assert row["answers"] == {"verdict": {"close": 1}} and "not kept" not in out["text"]


def test_no_new_candidate_means_no_decide_call_no_event_and_only_the_watermark_moves(stale):
    api, store, task = stale
    task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    first = H.now()
    SR.run_pass(store, at=first, engine=model)
    later = H.shift(first, hours=1, seconds=1)
    assert SR.run_pass(store, at=later, engine=model) == {"new": 0}
    assert len(model.calls) == 1 and len(calls(store)) == 1 and len(passes(store)) == 1
    with store.read() as c:
        assert SR.last_pass(c) == later
    # Within the hour no pass is due at all.
    assert SR.run_pass(store, at=H.shift(later, minutes=30), engine=model) is None


def test_an_empty_pass_writes_no_events_row(stale):
    api, store, task = stale
    task("Fresh work", status="doing", days_ago=0)
    with store.read() as c:
        before = c.execute("SELECT count(*) FROM events").fetchone()[0]
    assert SR.run_pass(store, engine=FakeDecide()) == {"new": 0}
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM events").fetchone()[0] == before
        assert SR.last_pass(c)


def test_a_task_already_suggested_and_unchanged_gets_no_second_comment(stale):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    SR.run_pass(store, at=H.now(), engine=model)
    # Even past the week's limit, an unchanged task is not judged again.
    with store.transaction() as c:
        c.execute("UPDATE stale_suggestions SET created_at=?", (H.shift(H.now(), days=-8),))
    SR.run_pass(store, at=hours(2), engine=model)
    assert len(model.calls) == 1 and len(comments(store, tid)) == 1


def test_at_most_one_suggestion_a_week_even_when_the_task_changed(stale):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    SR.run_pass(store, at=H.now(), engine=model)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), tid))
    SR.run_pass(store, at=hours(2), engine=model)
    assert len(model.calls) == 1 and len(comments(store, tid)) == 1


def test_three_stale_tasks_of_a_person_are_one_card_and_accept_marks_only_that_one_done(stale):
    api, store, task = stale
    ids = [task(f"Old chore {n}", owner="human:ana", requester="bot:ops", status="open", days_ago=9) for n in range(3)]
    with store.read() as c:
        before = c.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[0]
    SR.run_pass(store, engine=FakeDecide("update"))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM changes WHERE topic='needs' AND kind='stale' AND subject_id='human:ana' "
                         "AND seq>?", (before,)).fetchone()[0] == 3, "an open Needs you page hears of the card"
    items = get(api, "needs-you")["items"]
    cards = [i for i in items if i["kind"] == "stale"]
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "Stale tickets: 3 suggestions" and card["need"] == "decide"
    assert [r["task"] for r in card["rows"]] == ids
    assert card["rows"][0]["reason"] == "Nothing happened on it for 9 days" and card["rows"][0]["suggest"] == "Mark done"
    assert not any(comments(store, t) for t in ids), "a person gets the card, not comments"
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "accept", "version": card["rows"][0]["version"]})
    with store.read() as c:
        assert [H.task(c, t)["status"] for t in ids] == ["done", "open", "open"]
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE id=?", (card["rows"][0]["id"],)).fetchone()[0] \
            == "accepted"
    card = next(i for i in get(api, "needs-you")["items"] if i["kind"] == "stale")
    assert card["title"] == "Stale tickets: 2 suggestions"
    with store.read() as c:
        before = c.execute("SELECT max(seq) FROM changes").fetchone()[0]
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "skip"})
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM changes WHERE topic='needs' AND kind='stale' AND seq>?",
                         (before,)).fetchone()[0] == 1
        assert H.task(c, ids[1])["status"] == "open"
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE task_id=?", (ids[1],)).fetchone()[0] == "skipped"
    # The suggestion is someone else's to answer only if it was made to them.
    post(api, "stale-review/" + card["rows"][1]["id"], {"decision": "accept", "version": card["rows"][1]["version"]},
         token="ben-test", expected=404)


def test_accept_all_cannot_change_a_task_the_person_could_not_change_by_hand(stale):
    api, store, task = stale
    mine = task("Priya's old chore", owner="human:priya", requester="bot:ops", status="open", days_ago=9)
    moved = task("Priya's other chore", owner="human:priya", requester="bot:ops", status="open", days_ago=9)
    SR.run_pass(store, engine=FakeDecide("update"))
    # Handed to Ben after the suggestion: marking it done is his now, and Priya's team moves no one's tasks.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner='human:ben' WHERE id=?", (moved,))
        version = H.task(c, moved)["version"]
    card = stale_card(api, "priya-test")
    by_hand = api.post(f"/api/v2/tasks/{moved}", json={"status": "done", "version": version},
                       headers=headers("priya-test"))
    assert by_hand.status_code == 403 and by_hand.json()["error"]["code"] == "identity", by_hand.text
    out = post(api, "stale-review/accept-all", shown(card), token="priya-test")
    assert [r["task"] for r in out["accepted"]] == [mine]
    assert [r["task"] for r in out["refused"]] == [moved]
    assert out["refused"][0]["reason"] == by_hand.json()["error"]["detail"]
    with store.read() as c:
        assert H.task(c, mine)["status"] == "done" and H.task(c, moved)["status"] == "open"
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE task_id=?", (moved,)).fetchone()[0] is None
    # One row on its own is refused the same way.
    row = stale_card(api, "priya-test")["rows"][0]
    post(api, "stale-review/" + row["id"], {"decision": "accept", "version": row["version"]}, token="priya-test",
         expected=403)


def test_accept_applies_only_the_rows_and_task_versions_the_card_showed(stale):
    api, store, task = stale
    edited = task("Old chore one", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    kept = task("Old chore two", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    SR.run_pass(store, engine=FakeDecide("update"))
    card = stale_card(api)
    # After the card was drawn: the next pass adds a row the person never saw, and someone gives one task fresh work.
    late = task("Old chore three", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    SR.run_pass(store, at=hours(1.01), engine=FakeDecide("update"))
    assert len(stale_card(api)["rows"]) == 3
    with store.transaction() as c:
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (edited,))
    row = next(r for r in card["rows"] if r["task"] == edited)
    post(api, "stale-review/" + row["id"], {"decision": "accept"}, expected=422)
    post(api, "stale-review/" + row["id"], {"decision": "accept", "version": row["version"]}, expected=409)
    out = post(api, "stale-review/accept-all", shown(card))
    assert [r["task"] for r in out["accepted"]] == [kept]
    assert [r["task"] for r in out["refused"]] == [edited] and "changed since" in out["refused"][0]["reason"]
    with store.read() as c:
        assert [H.task(c, t)["status"] for t in (edited, kept, late)] == ["open", "done", "open"]
    assert sorted(r["task"] for r in stale_card(api)["rows"]) == sorted([edited, late]), "both stay to be answered"


def test_a_close_suggestion_is_closed_by_the_person_as_by_hand(stale):
    api, store, task = stale
    tid = task("Duplicate of the export work", owner="human:ben", requester="bot:ops", status="open", days_ago=9)
    SR.run_pass(store, engine=FakeDecide("close"))
    card = next(i for i in get(api, "needs-you", token="ben-test")["items"] if i["kind"] == "stale")
    assert card["rows"][0]["suggest"] == "Close it" and card["rows"][0]["action"] == "close"
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "accept", "version": card["rows"][0]["version"]},
         token="ben-test")
    with store.read() as c:
        row = H.task(c, tid)
    assert row["status"] == "closed" and row["closed_by"] == "human:ben"


def test_recent_activity_and_an_open_pr_is_not_a_candidate(stale):
    api, store, task = stale
    task("Building the export", status="review", days_ago=0, pr="open")
    task("Two PRs, one still open", status="review", days_ago=0, pr="merged")
    with store.transaction() as c:
        tid = c.execute("SELECT id FROM tasks WHERE title='Two PRs, one still open'").fetchone()[0]
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,created_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), tid, "pr", "https://github.com/acme/site/pull/13", "open", H.now()))
    model = FakeDecide()
    assert SR.run_pass(store, engine=model) == {"new": 0} and not model.calls


def test_switch_off_lists_no_candidates_and_makes_no_calls(stale):
    api, store, task = stale
    task("Ship the export button", pr="merged")
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": False})
    model = FakeDecide()
    assert SR.run_pass(store, engine=model) is None and not model.calls and not passes(store)
    with store.read() as c:
        assert SR.last_pass(c) is None


def test_the_scheduler_runs_the_pass_on_its_own_thread_and_never_in_rehearsal(stale, monkeypatch):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    monkeypatch.setattr(SR, "server_engine", lambda *a, **k: model)
    store.settings.rehearsal = True
    try:
        assert SR.tick(store) is None and not model.calls
        with store.read() as c:
            assert SR.last_pass(c) is None
    finally:
        store.settings.rehearsal = False
    thread = SR.tick(store)
    assert thread.name == "stale-review"
    thread.join(10)
    assert len(model.calls) == 1 and len(comments(store, tid)) == 1
    assert SR.tick(store) is None, "the next pass waits for the hour"


def test_a_decide_error_leaves_no_comment_and_the_task_is_judged_next_pass(stale):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    model.fail = J.JudgeError("unavailable", "down", retryable=True)
    done = SR.run_pass(store, at=H.now(), engine=model)
    assert done["errors"] == 1 and done["deferred"] == 1 and not comments(store, tid)
    assert calls(store)[0]["error"] == "unavailable"
    model.fail = None
    SR.run_pass(store, at=hours(2), engine=model)
    assert len(model.calls) == 2 and len(comments(store, tid)) == 1


def test_forty_new_candidates_make_at_most_thirty_calls_that_hour(stale):
    api, store, task = stale
    for n in range(40):
        task(f"Shipped thing {n}", pr="shipped")
    seed_calls(store, 25)                   # this hour's calls so far count against it
    model = FakeDecide("leave")
    done = SR.run_pass(store, at=H.now(), engine=model)
    assert len(model.calls) == 5 and done["deferred"] == 35 and done["why"] == "hourly_cap"
    # The rest are judged the next hours, 30 an hour, and the ones left are not asked again.
    an_hour_on(store)
    SR.run_pass(store, engine=model)
    assert len(model.calls) == 35
    an_hour_on(store)
    SR.run_pass(store, engine=model)
    assert len(model.calls) == 40


def test_the_daily_allowance_is_checked_before_every_call(stale, monkeypatch):
    api, store, task = stale
    ids = [task(f"Shipped thing {n}", pr="shipped") for n in range(4)]
    seed_calls(store, 2)
    with store.read() as c:
        used = SR.used_today(c, H.KEEPER)
    monkeypatch.setattr(store.settings, "decisions_daily_calls_bot", used + 1)
    model = FakeDecide("update")
    done = SR.run_pass(store, engine=model)
    assert len(model.calls) == 1 and done["deferred"] == 3 and done["why"] == "daily_cap"
    assert sum(bool(comments(store, t)) for t in ids) == 1
    # Spent for the day: the next pass asks nothing, and the tasks wait unjudged for tomorrow.
    with store.transaction() as c:
        SR._set_watermark(c, hours(-2))
    done = SR.run_pass(store, engine=model)
    assert len(model.calls) == 1 and done["calls"] == 0 and done["deferred"] == 3 and done["why"] == "daily_cap"
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM stale_judged").fetchone()[0] == 1


def test_a_turn_that_starts_while_decide_is_asked_gets_no_stale_comment(stale):
    api, store, task = stale
    worked = task("Ship the export button", pr="merged")
    moved = task("Ship the import button", pr="merged")
    other = task("Ship the print button", pr="merged")
    model = FakeDecide("update")
    asked = model.__call__

    def busy(state, questions, label=None):
        out = asked(state, questions, label)
        title = state["task"]["title"]
        with store.transaction() as c:
            if title == "Ship the export button":
                start_turn(c, worked)       # the owner bot picks it up: the task row does not change
            elif title == "Ship the import button":
                c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (moved,))
        return out
    done = SR.run_pass(store, engine=busy)
    assert len(model.calls) == 3 and done["changed"] == 2 and done["suggested"] == 1
    assert not comments(store, worked) and not comments(store, moved) and len(comments(store, other)) == 1
    assert len(calls(store)) == 3, "every call made stays in the audit log"
    with store.read() as c:
        judged = {r[0] for r in c.execute("SELECT task_id FROM stale_judged")}
    assert judged == {other}, "the ones it could not use wait for a pass that sees them fresh"


def test_a_private_task_is_never_sent_to_decide(stale):
    api, store, task = stale
    hidden = task("Settle the Acme dispute", pr="merged")
    seen = task("Ship the export button", pr="merged")
    with store.transaction() as c:
        H.task_comment(c, "human:ana", hidden, "The settlement figure is in the shared folder")
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (hidden,))
    model = FakeDecide("update")
    done = SR.run_pass(store, engine=model)
    assert [m["state"]["task"]["title"] for m in model.calls] == ["Ship the export button"]
    assert "Acme" not in json.dumps([m["state"] for m in model.calls])
    assert done["calls"] == 1 and not comments(store, hidden) and len(comments(store, seen)) == 1
    with store.read() as c:
        assert [row["id"] for row, _ in SR.candidates(c, H.now())] == [seen], "not even a candidate"
        assert [r[0] for r in c.execute("SELECT task_id FROM stale_judged")] == [seen]


def test_a_task_made_private_while_decide_is_asked_gets_no_suggestion(stale):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    asked = model.__call__

    def hide(state, questions, label=None):
        out = asked(state, questions, label)
        with store.transaction() as c:
            c.execute("UPDATE tasks SET private=1 WHERE id=?", (tid,))     # the task row's mark stays as it was
        return out
    done = SR.run_pass(store, engine=hide)
    assert len(model.calls) == 1 and done["changed"] == 1 and done["suggested"] == 0
    assert not comments(store, tid)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM stale_judged").fetchone()[0] == 0


def test_turning_the_switch_off_during_a_pass_stops_its_calls_and_output(stale):
    api, store, task = stale
    ids = [task(f"Shipped thing {n}", pr="shipped") for n in range(3)]
    model = FakeDecide("update")
    asked = model.__call__

    def then_off(state, questions, label=None):
        out = asked(state, questions, label)
        with store.transaction() as c:
            team_rules.save(c, "human:ana", {"stale_review": False})
        return out
    done = SR.run_pass(store, engine=then_off)
    assert len(model.calls) == 1 and done["why"] == "switched_off" and done["deferred"] == 3
    assert not any(comments(store, t) for t in ids) and len(calls(store)) == 1


def test_turning_the_switch_off_between_two_calls_stops_the_next_one(stale, monkeypatch):
    api, store, task = stale
    ids = [task(f"Shipped thing {n}", pr="shipped") for n in range(3)]
    apply = SR._apply

    def then_off(c, *a, **k):
        out = apply(c, *a, **k)
        team_rules.save(c, "human:ana", {"stale_review": False})    # right after the first suggestion is written
        return out
    monkeypatch.setattr(SR, "_apply", then_off)
    model = FakeDecide("update")
    done = SR.run_pass(store, engine=model)
    assert len(model.calls) == 1 and done["suggested"] == 1 and done["why"] == "switched_off" and done["deferred"] == 2
    assert sum(bool(comments(store, t)) for t in ids) == 1


def test_the_pass_stops_before_the_hour_is_over(stale):
    api, store, task = stale
    for n in range(3):
        task(f"Shipped thing {n}", pr="shipped")
    ticks = iter([0, 0, SR.PASS_SECONDS])
    done = SR.run_pass(store, engine=FakeDecide("leave"), clock=lambda: next(ticks, SR.PASS_SECONDS))
    assert done["calls"] == 1 and done["deferred"] == 2 and done["why"] == "time"


def test_other_reasons_parent_blockers_snooze_and_twin_titles(stale):
    api, store, task = stale
    parent = task("Launch plan", status="doing", days_ago=0)
    child = task("Launch copy", status="open", days_ago=0)
    blocker = task("Get the logo", status="open", days_ago=0)
    blocked = task("Print the flyer", status="waiting", days_ago=0)
    first = task("Renew the domain", status="open", days_ago=2)
    twin = task("Renew the domain ", status="open", days_ago=0)
    spaced = task("renew  THE domain", status="open", days_ago=0)       # case and inner spaces too (title_key)
    snoozed = task("Ask about the lease", status="open", days_ago=0)
    with store.transaction() as c:
        c.execute("INSERT INTO task_relations VALUES(?,?,'parent','bot:ops',?)", (child, parent, H.now()))
        c.execute("INSERT INTO task_relations VALUES(?,?,'blocks','bot:ops',?)", (blocker, blocked, H.now()))
        c.execute("UPDATE tasks SET status='done' WHERE id IN (?,?)", (parent, blocker))
        ended = H.shift(H.now(), hours=-30)
        c.execute("INSERT INTO task_events(id,task_id,ts,actor,field,old,new,note) VALUES(?,?,?,?,?,?,?,?)",
                  (H.new_id(), snoozed, ended, H.KEEPER, "snoozed_until", ended, None, "snooze over"))
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ended, snoozed))
        found = {row["id"]: why for row, why in SR.candidates(c, H.now())}
    assert found[child] == "Its parent task is done"
    # A company task under a private parent (a task filed from private work): the parent's state stays private.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (parent,))
        hidden = {row["id"]: why for row, why in SR.candidates(c, H.now())}
    assert child not in hidden
    assert found[blocked] == "Everything it was blocked by is done"
    assert found[twin].startswith("Same title as an older open task of the same owner") and first not in found
    assert found[spaced].startswith("Same title as an older open task of the same owner")
    assert found[snoozed].startswith("Its snooze ended")


def test_quiet_means_a_week_and_three_days_for_doing(stale):
    api, store, task = stale
    doing_quiet = task("Draft the pricing page", status="doing", days_ago=4)
    doing_fresh = task("Draft the launch post", status="doing", days_ago=2)
    open_quiet = task("Plan the offsite", status="open", days_ago=8)
    open_fresh = task("Plan the webinar", status="open", days_ago=4)
    with store.read() as c:
        found = {row["id"]: why for row, why in SR.candidates(c, H.now())}
    assert found[doing_quiet] == "Nothing happened on it for 4 days"
    assert found[open_quiet] == "Nothing happened on it for 8 days"
    assert doing_fresh not in found and open_fresh not in found

def test_admins_turn_the_switch_members_cannot_and_the_environment_sets_only_the_default(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('rules','{}') ON CONFLICT(key) DO NOTHING")
        assert team_rules.load(c)["stale_review"] is False, "off by default for customers"
    monkeypatch.setenv("TICO_STALE_REVIEW", "1")
    with store.read() as c:
        assert team_rules.load(c)["stale_review"] is True, "on for Tico Team's own server"
    r = api.put("/api/v2/access/rules", json={"stale_review": False}, headers={"Authorization": "Bearer ben-test"})
    assert r.status_code == 403, "a member may not turn it"
    assert "Only an owner or an admin" in r.text, "the switch alone is an admin's to turn"
    r = api.put("/api/v2/access/rules", json={"stale_review": True, "admin_sql": False},
                headers={"Authorization": "Bearer ben-test"})
    assert r.status_code == 403 and "Only the owner" in r.text, "with any other rule it stays the owner's"
    monkeypatch.setattr(api.app.state.auth, "bot_admins", {"ben@acme.example"})
    r = api.put("/api/v2/access/rules", json={"stale_review": True}, headers=headers("ben-test"))
    assert r.status_code == 200 and r.json()["stale_review"] is True, r.text
    with store.read() as c:
        assert team_rules.load(c)["stale_review"] is True, "an admin who is not the owner turns it"
    r = api.put("/api/v2/access/rules", json={"stale_review": False, "admin_sql": False}, headers=headers("ben-test"))
    assert r.status_code == 403 and "Only the owner" in r.text, "an admin still may not turn the owner's rules"
    monkeypatch.setattr(api.app.state.auth, "bot_admins", set())
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"admin_sql": False})
        assert team_rules.load(c)["stale_review"] is True, "saving another rule does not freeze this one"
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": False})
    monkeypatch.setenv("TICO_STALE_REVIEW", "1")
    with store.read() as c:
        assert team_rules.load(c)["stale_review"] is False, "a saved choice wins over the environment"


def test_still_wanted_stops_while_the_switch_is_on(stale):
    api, store, task = stale
    tid = task("Old chore", owner="human:ana", requester="human:ana", status="open", days_ago=9)
    with store.transaction() as c:
        assert still_wanted.ask_idle(c) == []
        team_rules.save(c, "human:ana", {"stale_review": False})
        assert still_wanted.ask_idle(c) == [tid]
