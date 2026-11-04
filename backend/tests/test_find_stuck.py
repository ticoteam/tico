"""Find stuck (backend/maintenance_steps/find_stuck.py): each kind it lists (due, missed, blocked, unanswered, restate, stale,
quiet) gets one grouped list per bot per run; a task a person holds is never listed by the kinds that are plain code's own,
under any hold kind; a hold the reader cannot see keeps a task off; the blocker's bot is woken, not the owner; a re-armed
follow-up is listed again; nothing is relisted without a change; at most ten bots a run, oldest first; no re-arm text; no
person is messaged; and the Still wanted card migration (migrations/0080). Synthetic fixtures only."""

import importlib

import pytest

from backend import hubdb as H
from backend import task_check_backs, team_rules
from backend.maintenance_steps import find_stuck as FS
from backend.tests.test_api import api  # noqa: F401  (fixture)

DAY = "2026-11-03T14:00:00.000000Z"     # 06:00 Pacific on a Tuesday: before the weekday morning batch
NOON = H.shift(DAY, hours=6)            # 12:00 Pacific: the batch is due
CREW = [f"crew-{n:02d}" for n in range(1, 13)]
PERSON_HELD = ("person", "ask", "approval", "blocker", "child", "private")


def ago(**kw):
    return H.shift(DAY, **{k: -v for k, v in kw.items()})


def step(store, at=DAY, settings=None):
    with store.transaction() as c:
        return FS.run(c, {"now": at, "settings": settings or {}})


def task(c, title, slug="finance"):
    return H.task_create(c, H.human_actor("ana"), title, f"{title}, for find stuck.", H.bot_actor(slug))


def settle(c, tid, slug, turned):
    """The task as it stood before its bot's last turn on it at `turned`: its own row, and its messages and history a day
    older, so nothing has happened on it since the turn."""
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (turned, tid))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (H.shift(turned, days=-1), conv))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
              (H.shift(turned, days=-1), tid))
    c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at,trigger,message_id,task_id,exit,summary) "
              "VALUES(?,?,?,?,'schedule',NULL,?,'done','')", (H.new_id(), slug, turned, turned, tid))


def complete_jobs(c, slug="finance"):
    """The creation wakes ran: nothing is queued for the bot, so none of its tasks is being run already."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id=?", (slug,))


def blocks(c, blocker, tid, when):
    c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'blocks','human:ana',?)",
              (blocker, tid, when))


def ask_to(who, question="Which venue?"):
    return {"questions": [{"id": "venue", "header": "", "question": question,
                           "options": [{"label": "Hall", "description": ""}, {"label": "Barn", "description": ""}],
                           "multi": False, "other": True}], "who": who}


def lists(store, slug="finance"):
    with store.read() as c:
        return [row[0] for row in c.execute("SELECT body FROM messages WHERE recipient_id=? AND refs LIKE '%find_stuck%' "
                                            "ORDER BY created_at", (H.bot_actor(slug),)).fetchall()]


def only_list(store, slug="finance"):
    found = lists(store, slug)
    assert len(found) == 1, found
    return found[0]


def people_messages(store):
    with store.read() as c:
        return c.execute("SELECT COUNT(*) FROM messages WHERE recipient_id LIKE 'human:%'").fetchone()[0]


def hold_by(c, kind, tid, slug):
    """The task held by a person in the way `kind` names: waiting on her, asked of her, awaiting her approval, blocked by
    her task, a child of hers, or blocked by a private task."""
    ana = H.human_actor("ana")
    if kind == "person":
        c.execute("UPDATE tasks SET status='waiting', waiting_on=?, note=? WHERE id=?", (ana, "Ana, which venue?", tid))
    elif kind == "ask":
        H.task_comment(c, H.bot_actor(slug), tid, "Which venue?", wake=False, ask=ask_to(ana))
        H.task_update(c, H.bot_actor(slug), tid, status="waiting", waiting_on=ana, note="Which venue?")
    elif kind == "approval":
        H.approval_request(c, H.bot_actor(slug), "spend", {"amount": 120, "account": "card", "what": "Vendor A"}, task_id=tid)
    elif kind == "blocker":
        blocks(c, H.task_create(c, ana, "Sign the lease", "x", ana)["id"], tid, ago(days=9))
    elif kind == "child":
        child = H.task_create(c, ana, "Pull the quotes", "x", H.human_actor("ben"))["id"]
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'parent','human:ana',?)",
                  (child, tid, ago(days=9)))
    elif kind == "private":
        hidden = H.task_create(c, ana, "Settle the dispute", "x", H.bot_actor("ops"))["id"]
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (hidden,))
        blocks(c, hidden, tid, ago(days=9))


def test_a_quiet_task_is_listed_once_quiet_a_day(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Draft the grant report")["id"]
        settle(c, tid, "finance", ago(days=3))
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    assert f"{tid[:8]} Draft the grant report: Quiet for 3 days, nothing waiting on anyone" in only_list(store)


def test_a_follow_up_whose_time_has_come_is_listed_and_consumed(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Chase the quote")["id"]
        task_check_backs.set_time(c, H.task(c, tid), ago(hours=1), "Check whether the quote came in",
                                  actor=H.human_actor("ana"), at=DAY)
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    body = only_list(store)
    assert f"{tid[:8]} Chase the quote: Follow-up time set by" in body and "Check whether the quote came in" in body
    with store.read() as c:
        assert H.task(c, tid)["check_back_at"] is None, "consumed once it is listed"


def test_a_snooze_that_ended_with_nothing_picked_up_is_listed_as_missed_once(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Call the venue")["id"]
        settle(c, tid, "finance", ago(days=2))
        H._task_event(c, tid, H.KEEPER, "snoozed_until", ago(hours=5), None, "snooze over")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? "
                  "AND kind='snoozed_until'", (ago(hours=3), tid))
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    assert f"{tid[:8]} Call the venue: Snooze ended at 11:00 UTC and nothing has picked it up" in only_list(store)
    assert step(store, H.shift(DAY, hours=2))["details"]["woken"] == [], "listed once for that time"


def test_a_deferred_time_that_passed_unfired_is_listed_as_missed(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Chase the invoice")["id"]
        settle(c, tid, "finance", ago(days=2))
        conv = H.task(c, tid)["conversation_id"]
        c.execute("INSERT INTO deferrals(id,bot,kind,item,conversation_id,task_id,due_at,created_at,fired_job,settled_at,how,attempt) "
                  "VALUES(?,?,'task',?,?,?,?,?,NULL,NULL,NULL,NULL)",
                  (H.new_id(), "finance", tid, conv, tid, ago(hours=2), ago(hours=4)))
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    assert f"{tid[:8]} Chase the invoice: Deferred time passed at 12:00 UTC and nothing has picked it up" in only_list(store)


def test_a_blocked_task_wakes_its_blocker_bot_not_its_owner(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = task(c, "Finish the menu", "ops")["id"]
        mine = task(c, "Print the menu")["id"]
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (mine,))
        blocks(c, blocker, mine, ago(days=1))
        complete_jobs(c)
        complete_jobs(c, "ops")
    result = step(store)
    assert result["details"]["woken"] == ["ops"], "the blocker's bot is woken"
    assert f"{mine[:8]} Print the menu: Waiting on you: Finish the menu" in only_list(store, "ops")
    assert lists(store, "finance") == [], "the owner is not woken about its blocked task"


def test_an_unanswered_question_to_a_person_goes_back_to_its_bot_at_the_weekday_morning(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Pick a venue")["id"]
        said = H.task_comment(c, H.bot_actor("finance"), tid, "Which venue?", wake=False, ask=ask_to(H.human_actor("ana")))
        H.task_update(c, H.bot_actor("finance"), tid, status="waiting", waiting_on=H.human_actor("ana"), note="Which venue?")
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=6), said["id"]))
        complete_jobs(c)
    assert step(store, DAY)["details"]["woken"] == [], "the batch is for the morning, not before it"
    assert step(store, NOON)["details"]["woken"] == ["finance"]
    body = only_list(store)
    assert f'- To ana on "Pick a venue"' in body and '"Which venue?"' in body
    with store.read() as c:
        assert c.execute("SELECT superseded_at FROM messages WHERE id=?", (said["id"],)).fetchone()[0] is not None, \
            "taken back off the person's list"
    assert step(store, NOON)["details"]["woken"] == [], "not again"


def test_a_waiting_note_without_a_plain_ask_gets_one_restate_request(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Renew the lease")["id"]
        c.execute("UPDATE tasks SET status='waiting', waiting_on=?, note=? WHERE id=?",
                  (H.human_actor("ana"), "Need the terms at s3://acme-files/lease.pdf", tid))
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    body = only_list(store)
    assert "Waiting on ana with no plain ask" in body
    assert f"hub task update {tid} --status waiting --on ana" in body
    assert step(store)["details"]["woken"] == [], "asked once, ever"


def test_a_stale_candidate_the_bot_owns_is_listed_while_the_switch_is_on(api):
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": True})
        tid = task(c, "Ship the export button")["id"]
        settle(c, tid, "finance", ago(days=9))
        complete_jobs(c)
    body = only_list(store)
    assert f"{tid[:8]} Ship the export button: Stale check: Nothing happened on it for 9 days." in body
    assert "Quiet for" not in body, "a task is listed once, as its first kind"


def test_with_the_switch_off_the_same_task_is_only_quiet(api):
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": False})
        tid = task(c, "Ship the export button")["id"]
        settle(c, tid, "finance", ago(days=9))
        complete_jobs(c)
    body = only_list(store)
    assert f"{tid[:8]} Ship the export button: Quiet for 9 days, nothing waiting on anyone" in body
    assert "Stale check" not in body


@pytest.mark.parametrize("kind", PERSON_HELD)
def test_a_task_a_person_holds_is_never_listed_under_any_hold_kind(api, kind):
    store = api.app.state.store
    slug = "ops" if kind == "approval" else "finance"
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": True})
        tid = task(c, "Pick the venue", slug)["id"]
        hold_by(c, kind, tid, slug)
        settle(c, tid, slug, ago(days=9))       # old enough to be due, quiet and stale if nothing held it
        complete_jobs(c, slug)
    result = step(store)
    assert result["woke"] == 0 and result["details"]["listed"] == 0, result
    assert lists(store, slug) == []


def test_a_follow_up_on_a_task_a_person_holds_is_not_listed(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Pick the venue")["id"]
        c.execute("UPDATE tasks SET status='waiting', waiting_on=?, note=? WHERE id=?",
                  (H.human_actor("ana"), "Ana, which venue?", tid))
        task_check_backs.set_time(c, H.task(c, tid), ago(hours=1), "Check the venue", actor=H.human_actor("ana"), at=DAY)
        complete_jobs(c)
    assert step(store)["woke"] == 0
    assert lists(store) == []


def test_a_hold_the_reader_cannot_see_keeps_the_task_off_the_blocked_list(api):
    store = api.app.state.store
    with store.transaction() as c:
        hidden = task(c, "Settle the dispute", "ops")["id"]
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (hidden,))
        mine = task(c, "Print the menu")["id"]
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (mine,))
        blocks(c, hidden, mine, ago(days=1))
        complete_jobs(c)
        complete_jobs(c, "ops")
    result = step(store)
    assert result["woke"] == 0, result
    assert lists(store, "ops") == [] and lists(store) == []


def test_a_re_armed_follow_up_on_an_unchanged_task_is_listed_again(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Chase the quote")["id"]
        task_check_backs.set_time(c, H.task(c, tid), ago(hours=1), "Check the quote", actor=H.human_actor("ana"), at=DAY)
        complete_jobs(c)
    step(store)
    with store.transaction() as c:
        task_check_backs.set_time(c, H.task(c, tid), ago(minutes=30), "Check the quote again",
                                  actor=H.human_actor("ana"), at=DAY)
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    again = lists(store)[1]
    assert "Check the quote again" in again
    assert step(store)["woke"] == 0, "no change since the second list"


def test_no_relist_without_a_change(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Draft the grant report")["id"]
        settle(c, tid, "finance", ago(days=3))
        complete_jobs(c)
    assert step(store)["details"]["woken"] == ["finance"]
    assert step(store, H.shift(DAY, hours=2))["woke"] == 0
    assert step(store, H.shift(DAY, hours=20))["woke"] == 0
    assert len(lists(store)) == 1 and f"{tid[:8]}" in lists(store)[0]


def test_at_most_ten_bots_are_woken_a_run_oldest_first_and_each_gets_one_list(api):
    store = api.app.state.store
    with store.transaction() as c:
        for n, slug in enumerate(CREW):
            c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) VALUES(?,?,?,'active',?)", (slug, slug, slug, DAY))
            tid = task(c, f"Old work {slug}", slug)["id"]
            settle(c, tid, slug, ago(days=2 + n))       # crew-01 is the newest, crew-12 the oldest
            complete_jobs(c, slug)
    result = step(store)
    expected = list(reversed(CREW))[:10]
    assert result["details"]["woken"] == expected and result["woke"] == 10
    for slug in expected:
        assert len(lists(store, slug)) == 1
    assert lists(store, "crew-01") == [] and lists(store, "crew-02") == [], "the rest roll over"
    rolled = step(store)
    assert sorted(rolled["details"]["woken"]) == ["crew-01", "crew-02"]
    assert rolled["details"]["woken"] == ["crew-02", "crew-01"], "the older one first"


def test_a_bot_gets_one_list_with_its_kinds_in_order(api):
    store = api.app.state.store
    with store.transaction() as c:
        due = task(c, "Chase the quote")["id"]
        task_check_backs.set_time(c, H.task(c, due), ago(hours=1), "Check the quote", actor=H.human_actor("ana"), at=DAY)
        quiet = task(c, "Draft the grant report")["id"]
        settle(c, quiet, "finance", ago(days=3))
        complete_jobs(c)
    step(store)
    body = only_list(store)
    assert body.index(f"{due[:8]} Chase the quote") < body.index(f"{quiet[:8]} Draft the grant report")


def test_the_wake_text_names_no_re_arm(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Chase the quote")["id"]
        task_check_backs.set_time(c, H.task(c, tid), ago(hours=1), "Check the quote", actor=H.human_actor("ana"), at=DAY)
        complete_jobs(c)
        text = task_check_backs.context(c, H.task(c, tid), H.bot_actor("finance"))
    step(store)
    for words in (text, only_list(store)):
        assert not any(word in words.lower() for word in ("re-arm", "rearm", "set another")), words


def test_find_stuck_never_messages_a_person(api):
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": True})
        asked = task(c, "Pick a venue")["id"]
        said = H.task_comment(c, H.bot_actor("finance"), asked, "Which venue?", wake=False, ask=ask_to(H.human_actor("ana")))
        H.task_update(c, H.bot_actor("finance"), asked, status="waiting", waiting_on=H.human_actor("ana"), note="Which venue?")
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=6), said["id"]))
        restate = task(c, "Renew the lease")["id"]
        c.execute("UPDATE tasks SET status='waiting', waiting_on=?, note=? WHERE id=?",
                  (H.human_actor("ana"), "Need the terms at s3://acme-files/lease.pdf", restate))
        quiet = task(c, "Draft the grant report")["id"]
        settle(c, quiet, "finance", ago(days=9))
        complete_jobs(c)
    before = people_messages(store)
    assert step(store, NOON)["details"]["woken"] == ["finance"]
    assert people_messages(store) == before, "no person was sent anything"
    with store.read() as c:
        recipients = {row[0] for row in c.execute("SELECT recipient_id FROM messages WHERE refs LIKE '%find_stuck%'")}
    assert recipients == {"bot:finance"}


def test_the_still_wanted_card_migration_withdraws_only_open_still_wanted_questions(api):
    store = api.app.state.store
    migration = importlib.import_module("backend.migrations.0080_withdraw_still_wanted")
    with store.transaction() as c:
        tid = task(c, "Old chore")["id"]
        open_card = H.task_comment(c, H.KEEPER, tid, "Nothing has happened on this task for 7 days. Still wanted?",
                                   wake=False, ask=ask_to(H.human_actor("ana")), extra_refs={"still_wanted": True})
        answered_card = H.task_comment(c, H.KEEPER, tid, "Still wanted? (answered)", wake=False,
                                       ask=ask_to(H.human_actor("ana")), extra_refs={"still_wanted": True})
        c.execute("UPDATE messages SET answered_by_id=? WHERE id=?", (H.human_actor("ana"), answered_card["id"]))
        bot_ask = H.task_comment(c, H.bot_actor("finance"), tid, "Which venue?", wake=False,
                                 ask=ask_to(H.human_actor("ana")))
        migration.up(c)
        migration.up(c)                                  # running it again changes nothing
    with store.read() as c:
        def superseded(message_id):
            return c.execute("SELECT superseded_at FROM messages WHERE id=?", (message_id,)).fetchone()[0]
        assert superseded(open_card["id"]) is not None, "an open Still wanted card is withdrawn"
        assert superseded(answered_card["id"]) is None, "an answered one is left as it is"
        assert superseded(bot_ask["id"]) is None, "a bot's question is not a Still wanted card"
