"""Each action's executor in the hub (backend/automation_actions.py), reusing what the hub has: tasks, tags, wakes,
updates, outgoing webhooks, the mail action queue and the Slack outbox. A refusal is an answer, never a raise."""
import json

from backend import automation_actions as A
from backend.store import H
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_market import _librarian

MAIL = {"message": {"source": "mail", "mailbox": "ana@acme.example", "id": "m1", "subject": "Invoice"}}
NEWS = {"message": {**MAIL["message"], "from": "news@example.org"}}      # a sender the hub knows nothing about
SLACK ={"message": {"source": "slack", "inbox": "slack:#general", "channel": "C1", "ts": "1.1", "text": "Help?"}}


def auto(owner="person:ana"):
    return {"id": "auto_t", "flow": {"name": "Test", "owner": owner, "trigger": {"type": "event", "name": "x.y"}},
            "assistant_bot": "coo"}


def act(api, do, args=None, values=None, owner="person:ana"):
    with api.app.state.store.transaction() as c:
        return A.run(c, auto(owner), "run_1", do, args or {}, values or {})


def task(api, title="Check the deploy"):
    made = post(api, "tasks", {"owner": "ops", "title": title, "body": "Please."})
    return {"task": {"id": made["id"], "title": title}}


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def test_create_task_files_it_from_the_owner(api):
    done = act(api, "create_task", {"title": "Follow up", "owner": "coo"})
    row = sql(api, "SELECT owner_id, requester_id FROM tasks WHERE id=?", (done["task"],))[0]
    assert done["ok"] and row == {"owner_id": "bot:coo", "requester_id": "human:ana"}
    assert act(api, "create_task", {}) == {"ok": False, "detail": "create_task needs a title"}


def test_label_note_and_assign_work_on_the_runs_task(api):
    values = task(api)
    assert act(api, "label", {"tag": "billing"}, values)["detail"] == "labelled"
    assert act(api, "label", {"tag": "billing"}, values)["detail"] == "already labelled"
    assert act(api, "note", {"text": "Seen by the automation"}, values)["ok"]
    assert act(api, "assign", {"owner": "coo"}, values)["ok"]
    assert sql(api, "SELECT owner_id FROM tasks WHERE id=?", (values["task"]["id"],))[0]["owner_id"] == "bot:coo"
    assert not act(api, "assign", {}, values)["ok"]


def test_a_note_with_no_task_or_bot_stays_in_the_run_log(api):
    """A schedule, a mail or a sample task with no id has nothing to note on: the step keeps the note itself."""
    for values in ({}, MAIL, {"task": {"title": "No id"}}):
        done = act(api, "note", {"text": "Seen"}, values)
        assert done == {"ok": True, "detail": "noted in the run log", "note": "Seen"}, done
    assert not sql(api, "SELECT id FROM history_events WHERE subject_type='task' AND note='Seen'")


def test_wake_bot_wakes_it_with_the_run_on_the_notice(api):
    assert act(api, "wake_bot", {"bot": "ops", "text": "Look"})["ok"]
    woke = sql(api, "SELECT body, refs FROM messages WHERE recipient_id='bot:ops' ORDER BY created_at DESC")[0]
    assert woke["body"] == "Look" and json.loads(woke["refs"])["automation_run"] == "run_1"
    assert not act(api, "wake_bot", {"bot": "nobody"})["ok"]


def test_post_update_posts_as_the_owner(api):
    assert act(api, "post_update", {"text": "Shipped the fix"}, owner="bot:ops")["ok"]
    assert act(api, "post_update", {}) == {"ok": False, "detail": "post_update needs text"}


def test_post_update_posts_a_daily_on_a_friday(api, monkeypatch):
    """A Friday's week in review needs slides, which an automation never has: its text is still a daily."""
    from backend import updates
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-10-30")
    assert act(api, "post_update", {"text": "Shipped the fix"}, owner="bot:ops")["ok"]
    assert sql(api, "SELECT kind FROM updates WHERE bot='ops'") == [{"kind": "daily"}]


BRIEF = {"brief": {"ref": "task_9", "turn": {"bot": "librarian", "task": "task_9"},
                   "text": "# Acme market, Tuesday\n\n- Northwind cut its price.\n- No bet is touched."}}


def test_post_update_to_market_and_updates_posts_the_turns_briefing(api):
    """With no text it posts what the turn before wrote: the week's briefing on Market, one line on the Librarian's
    daily update pointing at it. Both name the run; a second run the same day keeps one line."""
    _librarian(api)
    with api.app.state.store.transaction() as c:
        from backend import updates
        updates.post(c, "librarian", "- Mapped three new competitors.", kind="daily")
    for _ in range(2):
        done = act(api, "post_update", {"to": ["market", "updates"]}, BRIEF, owner="bot:librarian")
        assert done["ok"] and done["briefing"] and done["update"], done
    source = {"automation": "auto_t", "run": "run_1", "task": "task_9"}
    latest = api.get("/api/v2/market/briefings/latest", headers={"Authorization": "Bearer ana-test"}).json()["briefing"]
    assert (latest["title"], latest["body"], latest["source"]) == (
        "Acme market, Tuesday", "- Northwind cut its price.\n- No bet is touched.", source)
    row = sql(api, "SELECT body, source FROM updates WHERE bot='librarian' AND kind='daily'")
    assert len(row) == 1 and json.loads(row[0]["source"]) == source
    assert row[0]["body"] == ("- Mapped three new competitors.\n"
                              "- Market briefing: Acme market, Tuesday ([read](#/strategy/market/briefing))")
    listed = api.get("/api/v2/updates", headers={"Authorization": "Bearer ana-test"}).json()["updates"]
    assert [u["source"] for u in listed if u["bot"] == "librarian"] == [source]


def test_post_update_to_market_is_the_librarians_or_the_top_of_the_orgs(api):
    _librarian(api)
    assert act(api, "post_update", {"to": ["market"], "text": "Acme week"}, owner="bot:ops") == {
        "ok": False, "detail": "only the Librarian and the top of the org post to Market"}
    assert act(api, "post_update", {"to": ["market"], "text": "Acme week"}, owner="person:ana")["ok"]
    assert act(api, "post_update", {"to": ["slack"], "text": "x"}) == {
        "ok": False, "detail": "to lists where to post: updates, market"}
    # A post by hand clears the run's mark.
    assert act(api, "post_update", {"text": "- Curated the map."}, owner="bot:librarian")["ok"]
    assert sql(api, "SELECT source FROM updates WHERE bot='librarian'")[0]["source"]
    with api.app.state.store.transaction() as c:
        from backend import updates
        updates.post(c, "librarian", "- Hand-written.", kind="daily")
    assert sql(api, "SELECT source FROM updates WHERE bot='librarian'") == [{"source": None}]


def test_call_webhook_queues_a_delivery_to_a_saved_hook_only(api):
    hook = post(api, "webhooks", {"name": "crm", "url": "https://hooks.acme.example/crm", "events": ["custom.*"]})
    done = act(api, "call_webhook", {"webhook": hook["webhook"]["id"], "data": {"lead": "Acme"}})
    assert done["ok"], done
    assert not act(api, "call_webhook", {"webhook": "wh_nope"})["ok"]


def test_mail_actions_queue_for_the_mailboxs_computer(api):
    for do in ("archive", "leave_for_you", "draft_reply", "forward", "label"):
        assert act(api, do, {"tag": "x"}, MAIL)["ok"]
    queued = [r["action"] for r in sql(api, "SELECT action FROM automation_mail_actions ORDER BY created_at")]
    assert queued == ["archive", "leave", "draft", "forward", "label"]
    assert act(api, "archive") == {"ok": False, "detail": "archive needs a message"}


def test_slack_send_reply_posts_in_the_thread_and_off_leaves_a_draft_task(api):
    assert act(api, "send_reply", {"body": "On it", "bot": "ops"}, SLACK)["detail"] == "posted"
    post_row = sql(api, "SELECT channel, thread_ts, bot, text FROM slack_posts")[0]
    assert post_row == {"channel": "C1", "thread_ts": "1.1", "bot": "ops", "text": "On it"}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO automation_inboxes VALUES('slack:#general',0,?,'human:ana')", (H.now(),))
    drafted = act(api, "send_reply", {"body": "On it", "bot": "ops"}, SLACK)
    assert drafted["ok"] and drafted["task"] and len(sql(api, "SELECT 1 FROM slack_posts")) == 1


def test_an_unknown_action_is_refused(api):
    assert not act(api, "explode")["ok"]


def test_an_idle_pr_find_reads_open_prs_whose_last_activity_is_old(api):
    """The stale-PR find (CONTRACT section 6): with idle_hours, only open or draft PRs whose last activity is that old
    come back, a PR with no activity recorded reads as idle from its creation, and the plain find keeps every PR."""
    task_id = task(api)["task"]["id"]
    now = H.now()
    old, fresh = H.shift(now, hours=-40), now
    links = [("idle", "open", H.shift(now, hours=-30)), ("fresh", "open", fresh),
             ("merged", "merged", H.shift(now, hours=-30)), ("draft", "draft", None)]
    with api.app.state.store.transaction() as c:
        for number, (name, state, activity) in enumerate(links, 1):
            c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,created_at,activity_at,repo,number) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (f"link_{name}", task_id, "pr", f"https://github.com/acme/app/pull/{number}", f"Change {name}",
                       state, old, activity, "acme/app", number))
    with api.app.state.store.read() as c:
        idle = A.find(c, auto(), "prs", "", 20, ("", ""), idle_hours=24)
        every = A.find(c, auto(), "prs", "", 20, ("", ""))
    assert sorted(r["id"] for r in idle) == ["link_draft", "link_idle"]
    assert len(every) == 4


def run_in_step(api, step, do, args=None, values=None):
    """An action run inside the flow step `step`, as the engine runs a filter rule's actions."""
    with api.app.state.store.transaction() as c:
        return A.run(c, auto(), "run_1", do, args or {}, values or MAIL, step=step)


def test_unsubscribe_queues_on_mail_and_is_refused_off_mail(api):
    done = act(api, "unsubscribe", {}, NEWS)
    assert done == {"ok": True, "detail": "unsubscribe queued", "mail_action": done["mail_action"], "queued": True}
    assert sql(api, "SELECT action, state, args FROM automation_mail_actions") == [
        {"action": "unsubscribe", "state": "queued", "args": "{}"}]
    slack = act(api, "unsubscribe", {}, SLACK)
    assert not slack["ok"] and "mail only" in slack["detail"]
    no_mailbox = act(api, "unsubscribe", {}, {"message": {"source": "mail", "id": "m1"}})
    assert not no_mailbox["ok"] and "mail only" in no_mailbox["detail"]
    assert act(api, "unsubscribe") == {"ok": False, "detail": "unsubscribe needs a message"}
    assert len(sql(api, "SELECT 1 FROM automation_mail_actions")) == 1


def test_mail_actions_of_one_step_on_one_message_chain_in_order(api):
    first = run_in_step(api, "gate", "unsubscribe", values=NEWS)["mail_action"]
    second = run_in_step(api, "gate", "archive")["mail_action"]
    other_step = run_in_step(api, "next", "archive")["mail_action"]
    plain = act(api, "archive", {}, MAIL)["mail_action"]
    rows = {r["id"]: r for r in sql(api, 'SELECT id, "step", "after", state FROM automation_mail_actions')}
    assert rows[first] == {"id": first, "step": "gate", "after": None, "state": "queued"}
    assert rows[second] == {"id": second, "step": "gate", "after": first, "state": "queued"}
    assert rows[other_step] == {"id": other_step, "step": "next", "after": None, "state": "queued"}
    assert rows[plain] == {"id": plain, "step": None, "after": None, "state": "queued"}


def test_a_chained_action_behind_a_failed_or_canceled_one_is_never_queued(api):
    failed = run_in_step(api, "gate", "unsubscribe", values=NEWS)["mail_action"]
    canceled = run_in_step(api, "other", "unsubscribe", values=NEWS)["mail_action"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_mail_actions SET state='failed' WHERE id=?", (failed,))
        c.execute("UPDATE automation_mail_actions SET state='canceled' WHERE id=?", (canceled,))
    late = run_in_step(api, "gate", "archive")
    late_canceled = run_in_step(api, "other", "archive")
    assert late == {"ok": False, "detail": "skipped: an earlier action on this message failed",
                    "mail_action": late["mail_action"], "queued": False}
    assert not late_canceled["queued"]
    assert sql(api, 'SELECT id, state, "after" FROM automation_mail_actions WHERE id=?', (late["mail_action"],)) == [
        {"id": late["mail_action"], "state": "skipped", "after": failed}]
    assert sql(api, 'SELECT state, "after" FROM automation_mail_actions WHERE id=?',
               (late_canceled["mail_action"],)) == [{"state": "canceled", "after": canceled}]


def hub_mail(sender, msg_id):
    """A mail message as the hub's trigger hands it over: the sender from the synced message, never from the flow."""
    return {"message": {"source": "mail", "mailbox": "ana@acme.example", "id": msg_id, "from": sender}}


def test_the_hub_keeps_a_roster_persons_and_a_company_domains_mail_and_queues_nothing(api):
    """The hub judges the sender from its own roster and the company's domains, whatever the flow says: a refused
    unsubscribe is recorded as failed and nothing is queued for a computer to claim."""
    from backend import access, views
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:      # the saved roster the hub reads, as the app adds a person
        access.add_person(c, "keeper", views.roster(c), name="Sam", email="sam@gmail.com")
        team = access.company_domains(c, settings)[0]
    with api.app.state.store.transaction() as c:
        roster = A.run(c, {**auto(), "settings": settings}, "run_1", "unsubscribe", {}, hub_mail("sam@gmail.com", "m1"))
        domain = A.run(c, {**auto(), "settings": settings}, "run_1", "unsubscribe", {}, hub_mail(f"ops@{team}", "m2"))
    assert roster == {"ok": False, "detail": "not unsubscribing: the sender is on the people roster",
                      "mail_action": roster["mail_action"], "queued": False}
    assert domain == {"ok": False, "detail": "not unsubscribing: the sender is internal",
                      "mail_action": domain["mail_action"], "queued": False}
    rows = sql(api, "SELECT msg_id, state, result FROM automation_mail_actions ORDER BY created_at, rowid")
    assert [(r["msg_id"], r["state"]) for r in rows] == [("m1", "failed"), ("m2", "failed")]
    assert [json.loads(r["result"])["detail"] for r in rows] == [roster["detail"], domain["detail"]]


def test_an_archive_chained_after_a_refused_unsubscribe_is_skipped_on_the_hub(api):
    from backend import access, views
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        access.add_person(c, "keeper", views.roster(c), name="Sam", email="sam@gmail.com")
    values = hub_mail("sam@gmail.com", "m3")
    with api.app.state.store.transaction() as c:
        first = A.run(c, {**auto(), "settings": settings}, "run_1", "unsubscribe", {}, values, step="gate")
        second = A.run(c, {**auto(), "settings": settings}, "run_1", "archive", {}, values, step="gate")
    assert second == {"ok": False, "detail": "skipped: an earlier action on this message failed",
                      "mail_action": second["mail_action"], "queued": False}
    assert sql(api, "SELECT action, state FROM automation_mail_actions ORDER BY created_at, rowid") == [
        {"action": "unsubscribe", "state": "failed"}, {"action": "archive", "state": "skipped"}]
    assert first["queued"] is False and first["detail"] == "not unsubscribing: the sender is on the people roster"
