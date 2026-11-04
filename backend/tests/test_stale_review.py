"""Stale ticket review (backend/stale_review.py): plain code finds the stale tasks (the candidates), no model is asked, a
pass moves only its watermark, the switch is an admin's, and a person's card from before is still answered. Find stuck
lists a bot owner's candidates (backend/maintenance_steps/find_stuck.py), so no person gets a new card or a comment here.
Synthetic fixtures only."""

import pytest

from backend import hubdb as H
from backend import stale_review as SR
from backend import still_wanted, team_rules
from backend.tests.test_tasks_board import api, get, headers, post   # noqa: F401


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


def stale_card(api, token="ana-test"):
    return next(i for i in get(api, "needs-you", token=token)["items"] if i["kind"] == "stale")


def suggest(store, task_id, owner, action, words, reason="Nothing happened on it for 9 days"):
    """A suggestion an earlier pass made: the row a person's card lists until they answer it."""
    verdict = "close" if action == "close" else "update"
    with store.transaction() as c:
        sid = H.new_id()
        c.execute("INSERT INTO stale_suggestions(id,task_id,owner,reason,verdict,action,suggest,confidence,mark,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (sid, task_id, owner, reason, verdict, action, words, 0.9, SR.mark(H.task(c, task_id)), H.now()))
    return sid


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
        c.execute("INSERT INTO history_events(subject_type,id,subject_id,created_at,actor_id,kind,old,new,note) VALUES('task',?,?,?,?,?,?,?,?)",
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


def test_recent_activity_and_an_open_pr_is_not_a_candidate(stale):
    api, store, task = stale
    task("Building the export", status="review", days_ago=0, pr="open")
    task("Two PRs, one still open", status="review", days_ago=0, pr="merged")
    with store.transaction() as c:
        tid = c.execute("SELECT id FROM tasks WHERE title='Two PRs, one still open'").fetchone()[0]
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,created_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), tid, "pr", "https://github.com/acme/site/pull/13", "open", H.now()))
    with store.read() as c:
        assert SR.candidates(c, H.now()) == []


def test_switch_off_makes_no_pass_and_moves_no_watermark(stale):
    api, store, task = stale
    task("Ship the export button", pr="merged")
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": False})
    assert SR.run_pass(store) is None
    with store.read() as c:
        assert SR.last_pass(c) is None


def test_a_pass_moves_only_its_watermark_and_leaves_no_comment_and_no_card(stale):
    """The candidates are found for both kinds of owner, and the pass writes nothing for them: no model call, no comment on a
    bot's task, no Needs you card for a person's. Find stuck lists the bot owners' candidates instead."""
    api, store, task = stale
    bot_tid = task("Ship the export button", pr="merged")
    person_tid = task("Old chore", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    with store.read() as c:
        assert {row["id"] for row, _ in SR.candidates(c, H.now())} == {bot_tid, person_tid}
    assert SR.run_pass(store, at=H.now()) == {"candidates": 0, "new": 0, "calls": 0, "why": "find_stuck"}
    with store.read() as c:
        assert SR.last_pass(c) is not None
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='judge.call'").fetchone()[0] == 0
        assert [m for m in H.task_comments(c, bot_tid) if m["body"].startswith("Stale check:")] == []
        assert SR.pending(c, "human:ana") == []
    assert [i for i in get(api, "needs-you")["items"] if i["kind"] == "stale"] == []


def test_three_suggestions_of_a_person_are_one_card_and_accept_marks_only_that_one_done(stale):
    api, store, task = stale
    ids = [task(f"Old chore {n}", owner="human:ana", requester="bot:ops", status="open", days_ago=9) for n in range(3)]
    sids = [suggest(store, t, "human:ana", "done", "Mark done") for t in ids]
    cards = [i for i in get(api, "needs-you")["items"] if i["kind"] == "stale"]
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "Stale tickets: 3 suggestions" and card["need"] == "decide"
    assert [r["task"] for r in card["rows"]] == ids
    assert card["rows"][0]["reason"] == "Nothing happened on it for 9 days" and card["rows"][0]["suggest"] == "Mark done"
    post(api, "stale-review/" + sids[0], {"decision": "accept", "version": card["rows"][0]["version"]})
    with store.read() as c:
        assert [H.task(c, t)["status"] for t in ids] == ["done", "open", "open"]
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE id=?", (sids[0],)).fetchone()[0] == "accepted"
    assert stale_card(api)["title"] == "Stale tickets: 2 suggestions"
    post(api, "stale-review/" + sids[1], {"decision": "skip"})
    with store.read() as c:
        assert H.task(c, ids[1])["status"] == "open"
        assert c.execute("SELECT outcome FROM stale_suggestions WHERE id=?", (sids[1],)).fetchone()[0] == "skipped"
    # A suggestion is answered only by the person it was made to.
    post(api, "stale-review/" + sids[2], {"decision": "accept", "version": card["rows"][2]["version"]},
         token="ben-test", expected=404)


def test_the_tick_runs_the_pass_on_its_own_thread_and_never_in_rehearsal(stale):
    api, store, task = stale
    task("Ship the export button", pr="merged")
    store.settings.rehearsal = True
    try:
        assert SR.tick(store) is None
        with store.read() as c:
            assert SR.last_pass(c) is None
    finally:
        store.settings.rehearsal = False
    thread = SR.tick(store)
    assert thread.name == "stale-review"
    thread.join(10)
    with store.read() as c:
        assert SR.last_pass(c) is not None
    assert SR.tick(store) is None, "the next pass waits for the hour"


def test_still_wanted_asks_nobody_any_more_whatever_the_switch(stale):
    api, store, task = stale
    task("Old chore", owner="human:ana", requester="human:ana", status="open", days_ago=9)
    with store.transaction() as c:
        assert still_wanted.ask_idle(c) == []
        team_rules.save(c, "human:ana", {"stale_review": False})
        assert still_wanted.ask_idle(c) == []


def shown(card):
    """What the card's Accept all sends: the rows it showed with an action, and their task versions then."""
    return {"rows": [{"id": r["id"], "version": r["version"]} for r in card["rows"] if r["action"]]}


def test_accept_all_cannot_change_a_task_the_person_could_not_change_by_hand(stale):
    api, store, task = stale
    mine = task("Priya's old chore", owner="human:priya", requester="bot:ops", status="open", days_ago=9)
    moved = task("Priya's other chore", owner="human:priya", requester="bot:ops", status="open", days_ago=9)
    suggest(store, mine, "human:priya", "done", "Mark done")
    suggest(store, moved, "human:priya", "done", "Mark done")
    # Handed to Ben after the suggestion: marking it done is his now, and Priya's team moves no one's tasks.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (moved,))
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


def test_accept_all_applies_only_the_rows_and_task_versions_the_card_showed(stale):
    api, store, task = stale
    edited = task("Old chore one", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    kept = task("Old chore two", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    suggest(store, edited, "human:ana", "done", "Mark done")
    suggest(store, kept, "human:ana", "done", "Mark done")
    card = stale_card(api)
    # After the card was drawn: a row the person never saw is added, and someone gives one task fresh work.
    late = task("Old chore three", owner="human:ana", requester="bot:ops", status="open", days_ago=9)
    suggest(store, late, "human:ana", "done", "Mark done")
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
    suggest(store, tid, "human:ben", "close", "Close it")
    card = next(i for i in get(api, "needs-you", token="ben-test")["items"] if i["kind"] == "stale")
    assert card["rows"][0]["suggest"] == "Close it" and card["rows"][0]["action"] == "close"
    post(api, "stale-review/" + card["rows"][0]["id"], {"decision": "accept", "version": card["rows"][0]["version"]},
         token="ben-test")
    with store.read() as c:
        row = H.task(c, tid)
    assert row["status"] == "closed" and row["closed_by_id"] == "human:ben"


def test_a_private_task_is_never_a_candidate(stale):
    api, store, task = stale
    hidden = task("Settle the Acme dispute", pr="merged")
    seen = task("Ship the export button", pr="merged")
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (hidden,))
    with store.read() as c:
        assert [row["id"] for row, _ in SR.candidates(c, H.now())] == [seen]


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
