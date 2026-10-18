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
            c.execute("UPDATE tasks SET title=?,status=?,updated=?,created=? WHERE id=?",
                      (title, status, when, when, row["id"]))
            if pr:
                c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created,pr_merged_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?)",
                          (H.new_id(), row["id"], "pr", "https://github.com/acme/site/pull/12", "PR", pr, "bot:ops",
                           when, "2026-10-16T10:00:00Z"))
        return row["id"]
    return api, store, task


def calls(store, label="stale-review@1"):
    with store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail_json FROM events WHERE action='judge.call' AND target=? ORDER BY rowid", (label,))]


def passes(store):
    with store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail_json FROM events WHERE action='stale_review.pass' ORDER BY rowid")]


def comments(store, task_id):
    with store.read() as c:
        return [m["body"] for m in H.task_comments(c, task_id) if m["body"].startswith("Stale check:")]


def hours(n):
    return H.shift(H.now(), hours=n)


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
    assert set(state) == {"title", "status", "step", "created", "updated", "due", "reason", "comments"}
    assert state["reason"] == "PR #12 merged Oct 16" and model.calls[0]["label"] == "stale-review@1"
    (audit,) = calls(store)
    assert audit["task"] == tid and audit["verdict"] == {"value": "update", "confidence": 0.9}
    (summary,) = passes(store)
    assert summary["routes"] == {"gateway": 1} and summary["calls"] == 1 and summary["cost_usd"] == 0.00002


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
        c.execute("UPDATE stale_suggestions SET created=?", (H.shift(H.now(), days=-8),))
    SR.run_pass(store, at=hours(2), engine=model)
    assert len(model.calls) == 1 and len(comments(store, tid)) == 1


def test_at_most_one_suggestion_a_week_even_when_the_task_changed(stale):
    api, store, task = stale
    tid = task("Ship the export button", pr="merged")
    model = FakeDecide("update")
    SR.run_pass(store, at=H.now(), engine=model)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), tid))
    SR.run_pass(store, at=hours(2), engine=model)
    assert len(model.calls) == 1 and len(comments(store, tid)) == 1


def test_three_stale_tasks_of_a_person_are_one_card_and_accept_marks_only_that_one_done(stale):
    api, store, task = stale
    ids = [task(f"Old chore {n}", owner="human:ana", requester="bot:ops", status="open", days_ago=9) for n in range(3)]
    SR.run_pass(store, engine=FakeDecide("update"))
    items = get(api, "needs-you")["items"]
    cards = [i for i in items if i["kind"] == "stale"]
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "Stale tickets: 3 suggestions" and card["need"] == "decide"
    assert [r["task"] for r in card["rows"]] == ids
    assert card["rows"][0]["reason"] == "Nothing happened on it for 9 days" and card["rows"][0]["suggest"] == "Mark done"
    assert not any(comments(store, t) for t in ids), "a person gets the card, not comments"
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "accept"})
    with store.read() as c:
        assert [H.task(c, t)["status"] for t in ids] == ["done", "open", "open"]
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE id=?", (card["rows"][0]["id"],)).fetchone()[0] \
            == "accepted"
    card = next(i for i in get(api, "needs-you")["items"] if i["kind"] == "stale")
    assert card["title"] == "Stale tickets: 2 suggestions"
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "skip"})
    with store.read() as c:
        assert H.task(c, ids[1])["status"] == "open"
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE task_id=?", (ids[1],)).fetchone()[0] == "skipped"
    # The suggestion is someone else's to answer only if it was made to them.
    post(api, "stale-review/" + card["rows"][1]["id"], {"decision": "accept"}, token="ben-test", expected=404)


def test_accept_all_cannot_change_a_task_the_person_could_not_change_by_hand(stale):
    api, store, task = stale
    mine = task("Priya's old chore", owner="human:priya", requester="bot:ops", status="open", days_ago=9)
    moved = task("Priya's other chore", owner="human:priya", requester="bot:ops", status="open", days_ago=9)
    SR.run_pass(store, engine=FakeDecide("update"))
    # Handed to Ben after the suggestion: marking it done is his now, and Priya's team moves no one's tasks.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner='human:ben' WHERE id=?", (moved,))
        version = H.task(c, moved)["version"]
    by_hand = api.post(f"/api/v2/tasks/{moved}", json={"status": "done", "version": version},
                       headers=headers("priya-test"))
    assert by_hand.status_code == 403 and "not yours to change" in by_hand.text, by_hand.text
    out = post(api, "stale-review/accept-all", {}, token="priya-test")
    assert [r["task"] for r in out["accepted"]] == [mine]
    assert [r["task"] for r in out["refused"]] == [moved] and "not yours to change" in out["refused"][0]["reason"]
    with store.read() as c:
        assert H.task(c, mine)["status"] == "done" and H.task(c, moved)["status"] == "open"
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE task_id=?", (moved,)).fetchone()[0] is None
    # One row on its own is refused the same way.
    sid = next(r["id"] for r in next(i for i in get(api, "needs-you", token="priya-test")["items"]
                                     if i["kind"] == "stale")["rows"])
    post(api, "stale-review/" + sid, {"decision": "accept"}, token="priya-test", expected=403)


def test_a_close_suggestion_is_closed_by_the_person_as_by_hand(stale):
    api, store, task = stale
    tid = task("Duplicate of the export work", owner="human:ben", requester="bot:ops", status="open", days_ago=9)
    SR.run_pass(store, engine=FakeDecide("close"))
    card = next(i for i in get(api, "needs-you", token="ben-test")["items"] if i["kind"] == "stale")
    assert card["rows"][0]["suggest"] == "Close it" and card["rows"][0]["action"] == "close"
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "accept"}, token="ben-test")
    with store.read() as c:
        row = H.task(c, tid)
    assert row["status"] == "closed" and row["closed_by"] == "human:ben"


def test_recent_activity_and_an_open_pr_is_not_a_candidate(stale):
    api, store, task = stale
    task("Building the export", status="review", days_ago=0, pr="open")
    task("Two PRs, one still open", status="review", days_ago=0, pr="merged")
    with store.transaction() as c:
        tid = c.execute("SELECT id FROM tasks WHERE title='Two PRs, one still open'").fetchone()[0]
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,created) VALUES(?,?,?,?,?,?)",
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
    model = FakeDecide("leave")
    done = SR.run_pass(store, at=H.now(), engine=model)
    assert len(model.calls) == 30 and done["deferred"] == 10 and done["why"] == "hourly_cap"
    # The rest are judged the next hour, and the ones left are not asked again.
    SR.run_pass(store, at=hours(1.01), engine=model)
    assert len(model.calls) == 40


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
    snoozed = task("Ask about the lease", status="open", days_ago=0)
    with store.transaction() as c:
        c.execute("INSERT INTO task_relations VALUES(?,?,'parent','bot:ops',?)", (child, parent, H.now()))
        c.execute("INSERT INTO task_relations VALUES(?,?,'blocks','bot:ops',?)", (blocker, blocked, H.now()))
        c.execute("UPDATE tasks SET status='done' WHERE id IN (?,?)", (parent, blocker))
        ended = H.shift(H.now(), hours=-30)
        c.execute("INSERT INTO task_events(id,task_id,ts,actor,field,old,new,note) VALUES(?,?,?,?,?,?,?,?)",
                  (H.new_id(), snoozed, ended, H.KEEPER, "snoozed_until", ended, None, "snooze over"))
        c.execute("UPDATE tasks SET updated=? WHERE id=?", (ended, snoozed))
        found = {row["id"]: why for row, why in SR.candidates(c, H.now())}
    assert found[child] == "Its parent task is done"
    assert found[blocked] == "Everything it was blocked by is done"
    assert found[twin].startswith("Same title as an older open task of the same owner") and first not in found
    assert found[snoozed].startswith("Its snooze ended")


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
