"""Check health (backend/maintenance_steps/check_health.py) and the Tico bug path (backend/maintenance_health.py).

Each run reads the code checks, the production checks, the Health page's warn and bad checks and the mail asks, and files
what they find as the tags say: an urgent problem at once, a noisy one on its second run. One BotOps task per problem however
many computers have it: a repeat adds to its count, a problem that clears gets a cleared comment that wakes BotOps, and a task
BotOps closed is opened again when the problem returns. A mail ask gets one approval, which turns mail sending on when a person
approves it. A Tico bug goes to the Tico team once (a comment when it comes back worse), or as a support approval a person reads
first while the team rule is off. Check health never writes to a person.
"""

import calendar
import dataclasses
import importlib.util
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from backend import (bot_settings as BS, diagnostics, health, hosted_updates, invariants, mail_settings,
                     maintenance_health as MH, providers, replication, routines, support)
from backend.enqueue import enqueue_stored
from backend.maintenance_steps import check_health
from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (fixture)
from backend.tests.test_goal_manager import BASE
from backend.tests.test_onboarding import draft, environment, machine, signed_in  # noqa: F401
from backend.tests.test_support import FakeHQ

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0078_retire_health_monitor.py"
HM_SLUG = "health-monitor"      # the retired Health Monitor's slug: migration 0078 archives its row
CANARY = "Acme Payroll Mac"     # a computer's label: local, never in a report
SECRET_BOT = "acme-payroll-bot"


@pytest.fixture
def readers(monkeypatch):
    """Only the findings a test sets: the Health page, the production checks and the mail asks read nothing here."""
    monkeypatch.setattr(health, "view", lambda c, *a, **k: {"checks": []})
    monkeypatch.setattr(invariants, "CHECKS", [])
    monkeypatch.setattr(mail_settings, "alerts", lambda c: [])
    monkeypatch.setattr(hosted_updates, "notice", lambda *a, **k: {})
    monkeypatch.setattr(replication, "status", lambda *a, **k: {})


@pytest.fixture
def code_findings(monkeypatch, readers):
    """The code checks' findings for the next runs: the test replaces the list's contents."""
    items = []
    monkeypatch.setattr(MH, "findings", lambda c, settings: list(items))
    return items


def built(environment):
    """A signed-in install with one computer. BotOps is active: the test environment seeds it."""
    api = environment(seed={}, cards=BASE)
    runner = machine(api, label=CANARY)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api, runner


def item(check_id, key=None, title=None, kind=MH.SETUP, severity=MH.WARN, subject=""):
    """A code check's finding as MH._finding makes it, keyed by `key` (default: its check id)."""
    return MH._finding(key or check_id, check_id, kind, title or f"{check_id} finding", f"{check_id} detail.",
                       "BotOps: fix it.", subject=subject, severity=severity)


def run(api, when=None, settings=None):
    """One Check health run, as the Maintenance run calls the step. `when` (a timezone-aware datetime) stands for now."""
    store, auth = api.app.state.store, api.app.state.auth
    ctx = check_health.context(auth, settings or store.settings)
    if when is not None:
        ctx["now"] = when
    with store.read_transaction() as r:
        checked, found, notes = check_health.gather(r, ctx)
    with store.transaction() as c:
        return check_health.apply(c, ctx, checked, found, notes)


def file_due(api, result):
    """The Tico bug reports a run asked for, filed the way the scheduler files them: outside the run's transaction."""
    app, store = api.app, api.app.state.store
    return MH.file_reports(store, app.state.auth, store.settings, app.state.support, result["details"]["reports_due"])


def keys(api, settings=None, check=None):
    """The keys of the findings the code checks see now (Check health's reader), all of them or one check's."""
    with api.app.state.store.read() as c:
        found = MH.findings(c, settings or api.app.state.store.settings)
    return sorted(i["key"] for i in found if check is None or i["check_id"] == check)


def botops_tasks(api):
    """The tasks Check health opened: one per problem, owned by BotOps."""
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body,owner_id,status FROM tasks WHERE title LIKE 'Health:%' "
                                           "ORDER BY created_at")]


def said(api, task_id, start):
    """The messages on a task that begin with `start` (Check health's comments)."""
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT m.body FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                         "WHERE t.id=? AND m.body LIKE ? ORDER BY m.created_at", (task_id, start + "%")).fetchall()
    return [r["body"] for r in rows]


def registry(api, check_id):
    """Check health's record for a finding's signature: its task, its count, when it last commented."""
    with api.app.state.store.read() as c:
        row = c.execute("SELECT value FROM registry_metadata WHERE key=?", ("health-task:" + MH.signature(check_id),)).fetchone()
    return json.loads(row["value"]) if row else {}


def open_approvals(api, kind):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id FROM approvals WHERE kind=? AND decision IS NULL", (kind,))]


def set_rule(api, reports):
    """The team rule: whether a Tico bug goes to the Tico team without a person's approval first."""
    api.put("/api/v2/access/rules", json={"health_monitor_reports": reports}, headers=signed_in())


def set_botops(api, state):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state=? WHERE id='botops'", (state,))

def runner_errors(api, runner, lines):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id=?",
                  (json.dumps({"schema_version": 1, "bots": {}, "recent_errors": lines}), runner["runner_id"]))

STREAM = ["2026-10-14T10:0%dZ Tico runner: event stream lost (ReadTimeout); polling until it is back" % i for i in range(3)]

QUOTA = {"outcome": "failed", "limited": True, "limit_resets_at": None, "profile_used": None}

@pytest.fixture
def hq(monkeypatch):
    fake = FakeHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(fake))
    for name in ("TICO_SUPPORT", "TICO_TELEMETRY", "DO_NOT_TRACK", "TICO_HQ_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_VERSION", "0.3.45")
    return fake

def finding(api, key):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM health_findings WHERE key=?", (key,)).fetchone())

def company(api, enabled=("anthropic", "openai"), auto=True):
    """The company's providers, the first one's runtime the default, and whether a bot out of quota falls back to the
    others."""
    with api.app.state.store.transaction() as c:
        providers.save(c, "human:test", {"expected_revision": providers.load(c)["revision"], "enabled": list(enabled),
                                         "runtime": providers.PROVIDER_BY_ID[enabled[0]]["runtime"],
                                         "auto_fallback": auto}, H.now())

def limited(api, bot=SECRET_BOT, config=None):
    """A bot whose turn stopped on a usage limit within the day, with its own config when given."""
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {bot: {"name": bot, "status": "active"}}, None)
        if config is not None:
            c.execute("INSERT OR REPLACE INTO bot_config(bot,owner_id) VALUES(?,'ana')", (bot,))
            BS.write(c, bot, config)
        H.event(c, H.KEEPER, "attempt.limited", "a-" + bot, {"bot": bot})

def turn(api, runner_id, result, minutes_ago, bot="coo", state=None):
    """A finished turn on `runner_id` with the runner's result (backend/execution.py stores it as result)."""
    with api.app.state.store.transaction() as c:
        at = H.shift(H.now(), minutes=-minutes_ago)
        mid = "m-" + H.new_id()
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES(?,'human:ana',?,'chat','hi',?)",
                  (mid, "bot:" + bot, at))
        c.execute("INSERT OR IGNORE INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
                  ("j-" + mid, mid, bot, at))
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()["id"]
        aid = "a-" + mid
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,finished_at,"
                  "result) VALUES(?,?,?,?,1,?,?,?,?,?,?)",
                  (aid, job, bot, runner_id, "hash-" + aid, state or result["outcome"], at, at, at, json.dumps(result)))

def login_findings(api):
    with api.app.state.store.read() as c:
        return {i["key"]: i for i in MH.findings(c, api.app.state.store.settings)
                if i["check_id"] == "default_login_limited"}

def gateway_choice(api, monkeypatch, runner_id, isolated, enabled=("anthropic",)):
    """The recommended models choice with the Gateway connected, on a computer that does (or does not) run Gateway work."""
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)
    with api.app.state.store.transaction() as c:
        providers.save(c, "human:test", {"expected_revision": providers.load(c)["revision"], "enabled": list(enabled),
                                         "runtime": "claude", "auto_fallback": True, "preset": "subscriptions-gateway"},
                       H.now())
        c.execute("UPDATE runner_states SET readiness=? WHERE runner_id=?",
                  (json.dumps({"schema_version": 1, "isolated": isolated, "runtimes": {}, "bots": {}}), runner_id))

def test_an_urgent_finding_files_on_its_first_run(environment, readers, code_findings):
    api, runner = built(environment)
    key = "event_stream:" + runner["runner_id"]
    code_findings[:] = [item("event_stream", key=key, title="The event stream drops")]
    result = run(api)
    assert result["details"]["filed"] == [key] and result["woke"] == 1
    (task,) = botops_tasks(api)
    assert task["title"] == "Health: The event stream drops" and task["owner_id"] == "bot:botops"
    assert finding(api, key)["pending"] == 0


def test_a_noisy_finding_files_on_its_second_run(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("runner_outdated", key="runner_outdated:R1", title="Computer R1 is outdated")]
    first = run(api)
    assert first["details"]["pending"] == ["runner_outdated:R1"] and botops_tasks(api) == []
    second = run(api)
    assert second["details"]["filed"] == ["runner_outdated:R1"] and len(botops_tasks(api)) == 1
    assert finding(api, "runner_outdated:R1")["pending"] == 0


def test_a_noisy_problem_that_passes_within_one_run_files_nothing(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("runner_outdated", key="runner_outdated:R1")]
    run(api)
    code_findings[:] = []
    result = run(api)
    assert result["details"]["cleared"] == [] and botops_tasks(api) == []
    assert finding(api, "runner_outdated:R1")["present"] == 0


def test_a_repeat_adds_to_the_count_and_comments_once_a_day(environment, readers, code_findings):
    api, _ = built(environment)
    now = datetime.now(timezone.utc)
    code_findings[:] = [item("event_stream", key="event_stream:R1", title="The event stream drops")]
    run(api, now)
    (task,) = botops_tasks(api)
    run(api, now + timedelta(minutes=1))
    assert finding(api, "event_stream:R1")["occurrences"] == 2 and registry(api, "event_stream")["count"] == 2
    assert said(api, task["id"], "Still there") == []           # a repeat within the day adds to the count only
    run(api, now + timedelta(hours=25))
    (comment,) = said(api, task["id"], "Still there")            # a day on: one comment, and no wake
    assert "seen 3 times" in comment and registry(api, "event_stream")["count"] == 3
    assert len(botops_tasks(api)) == 1


def test_two_computers_with_one_problem_are_one_botops_task(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("event_stream", key="event_stream:A", title="The event stream drops")]
    run(api)
    code_findings[:] = [item("event_stream", key="event_stream:A", title="The event stream drops"),
                        item("event_stream", key="event_stream:B", title="The event stream drops")]
    result = run(api)
    (task,) = botops_tasks(api)
    assert result["woke"] == 0 and len(said(api, task["id"], "Also on 2 places")) == 1
    assert finding(api, "event_stream:B")["present"] == 1


def test_a_problem_that_clears_comments_and_wakes_botops_then_returns_to_the_same_task(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    (task,) = botops_tasks(api)
    code_findings[:] = []
    cleared = run(api)
    assert cleared["details"]["cleared"] == ["push_stuck:A"] and cleared["woke"] == 1
    (cleared_note,) = said(api, task["id"], "Cleared at")
    assert "BotOps: close this task." in cleared_note
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    back = run(api)
    assert back["woke"] == 1 and said(api, task["id"], "Back: Pushes are stuck")
    assert [t["id"] for t in botops_tasks(api)] == [task["id"]]


def test_a_task_botops_closed_is_opened_again_when_the_problem_returns(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    (first,) = botops_tasks(api)
    code_findings[:] = []
    run(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (first["id"],))
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    tasks = botops_tasks(api)
    assert len(tasks) == 2 and tasks[0]["id"] == first["id"]
    assert tasks[1]["status"] not in ("done", "closed", "declined")


def test_with_no_active_botops_nothing_is_filed_and_the_finding_is_listed(environment, readers, code_findings):
    api, _ = built(environment)
    set_botops(api, "paused")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    result = run(api)
    assert botops_tasks(api) == [] and result["woke"] == 0
    assert result["details"]["unrouted"] == [{"signature": MH.signature("push_stuck"), "titles": ["Pushes are stuck"]}]
    assert finding(api, "push_stuck:A")["present"] == 1


def test_no_person_gets_a_task_from_check_health(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("push_stuck", key="push_stuck:A"), item("runner_outdated", key="runner_outdated:A")]
    run(api)
    run(api)
    tasks = botops_tasks(api)
    assert len(tasks) == 2 and {t["owner_id"] for t in tasks} == {"bot:botops"}


def test_a_mail_ask_is_one_botops_task_and_one_approval_that_a_restart_does_not_repeat(environment, readers, monkeypatch):
    api, _ = built(environment)
    monkeypatch.setattr(mail_settings, "alerts", lambda c: [("coo", {"outbound_send": True, "forward_to": []})])
    run(api)
    assert botops_tasks(api) == []                              # a noisy check files on its second run
    run(api)
    (task,) = botops_tasks(api)
    assert task["title"] == "Health: Mail sending needs a person's approval"
    (approval,) = open_approvals(api, "mail_alert")
    assert registry(api, check_health.MAIL_CHECK)["approvals"] == {"coo": approval["id"]}
    run(api)                                                    # the next run, as after a restart: kept, not raised again
    assert [a["id"] for a in open_approvals(api, "mail_alert")] == [approval["id"]] and len(botops_tasks(api)) == 1


def test_a_mail_approval_that_fails_to_raise_is_tried_again_on_the_next_run(environment, readers, monkeypatch):
    api, _ = built(environment)
    monkeypatch.setattr(mail_settings, "alerts", lambda c: [("coo", {"outbound_send": True, "forward_to": []})])
    real, attempts = H.approval_request, []

    def flaky(c, actor, kind, payload, **kw):
        if kind == "mail_alert":
            attempts.append(kind)
            if len(attempts) == 1:
                raise RuntimeError("the approval store is down")
        return real(c, actor, kind, payload, **kw)

    monkeypatch.setattr(H, "approval_request", flaky)
    run(api)
    run(api)                                                    # the task is filed; its approval fails once
    assert botops_tasks(api) and open_approvals(api, "mail_alert") == []
    run(api)                                                    # the next run raises it
    assert len(open_approvals(api, "mail_alert")) == 1 and len(attempts) == 2


def test_a_health_page_warning_is_filed_through_the_same_lifecycle(environment, readers, monkeypatch):
    api, _ = built(environment)
    page = [{"id": "disk:acme-mac", "label": "Disk space", "status": "warn", "summary": "92% full.",
             "fixes": [{"label": "Free space"}]},
            {"id": "mail_sending", "label": "Mail sending", "status": "warn", "summary": "Waiting.", "fixes": []},
            {"id": "backups", "label": "Backups", "status": "ok", "summary": "", "fixes": []}]
    monkeypatch.setattr(health, "view", lambda c, *a, **k: {"checks": page})
    run(api)
    run(api)
    (task,) = botops_tasks(api)
    assert task["title"] == "Health: Disk space" and "Free space" in task["body"]
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT key FROM health_findings WHERE present=1 AND pending=0 AND key LIKE 'page:%'").fetchall()
    assert [r["key"] for r in rows] == ["page:disk:acme-mac"]


def test_the_health_page_lists_each_open_finding_with_its_task(environment, readers, code_findings):
    api, _ = built(environment)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    body = api.get("/api/v2/health/findings", headers=signed_in()).json()
    (row,) = body["findings"]
    assert row["key"] == "push_stuck:A" and row["tag"] == "urgent" and row["task_id"] == botops_tasks(api)[0]["id"]
    assert body["botops"] is True and body["reports_without_asking"] is True


def test_the_step_reads_its_findings_outside_the_write_lock(environment, readers, monkeypatch):
    # The Health Monitor's scan read every finding inside the write transaction (7.2 s on TIDY, Oct 29): each runner call
    # waited behind it. The reads run in a snapshot now; only the changes take the lock.
    api, runner = built(environment)
    store = api.app.state.store
    holders = []
    real = MH.findings

    def watched(c, settings):
        holders.append(len(store.writer.queue))                 # the write gate: holder plus waiters, 0 when none holds it
        return real(c, settings)

    monkeypatch.setattr(MH, "findings", watched)
    runner_errors(api, runner, STREAM)
    result = check_health.run_once(store, api.app.state.auth, store.settings)
    assert "event_stream:" + runner["runner_id"] in result["details"]["filed"]
    assert holders == [0]


def test_a_tico_bug_is_one_ticket_then_a_comment_when_it_comes_back_worse(environment, readers, code_findings, hq):
    api, _ = built(environment)
    now, sig = datetime.now(timezone.utc), MH.signature("queue_idle")
    code_findings[:] = [item("queue_idle", key="queue_idle:bot-1", kind=MH.BUG, title="Work waits with no turn")]
    run(api, now)                                               # noisy: pending
    first = file_due(api, run(api, now + timedelta(minutes=1)))[sig]
    assert first["ticket"] and len(hq.tickets) == 1
    again = file_due(api, run(api, now + timedelta(hours=25)))  # seen a third time, a day on: a comment, not a ticket
    assert again[sig]["comment"] is True and len(hq.tickets) == 1
    (ticket,) = hq.tickets.values()
    assert [m["body"][:37] for m in ticket["messages"]] == ["[Health Monitor] Seen again (3 times)"]


def test_with_the_team_rule_off_a_tico_bug_is_one_approval_and_nothing_leaves(environment, readers, code_findings, hq):
    api, _ = built(environment)
    set_rule(api, False)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    sig = MH.signature("push_stuck")
    first = file_due(api, run(api))[sig]
    assert first["approval"] and hq.seen == []
    (approval,) = open_approvals(api, "support")
    assert approval["id"] == first["approval"]
    again = api.post("/api/v2/health/findings/push_stuck:A/report", json={}, headers=signed_in()).json()
    assert again == {"signature": sig, "approval": first["approval"], "again": True}     # still waiting for the person
    assert len(open_approvals(api, "support")) == 1 and hq.seen == []


def test_a_declined_approval_lets_the_tico_bug_be_reported_again(environment, readers, code_findings, hq):
    api, _ = built(environment)
    set_rule(api, False)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    first = file_due(api, run(api))[MH.signature("push_stuck")]["approval"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE approvals SET decision='declined' WHERE id=?", (first,))
    second = api.post("/api/v2/health/findings/push_stuck:A/report", json={}, headers=signed_in()).json()
    assert second["approval"] and second["approval"] != first and "again" not in second
    assert hq.seen == []


def test_the_team_gets_at_most_five_new_tickets_a_day(environment, readers, code_findings, hq):
    api, _ = built(environment)
    store, sig = api.app.state.store, MH.signature("push_stuck")
    with store.transaction() as c:
        for i in range(5):
            c.execute("INSERT INTO health_reports(signature,ticket_id,created_at,reported_occurrences) VALUES(?,?,?,1)",
                      (f"hm-filed{i}", f"ticket-{i}", H.now()))
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    busy = file_due(api, run(api))[sig]
    assert busy["deferred"] == "support_busy" and hq.tickets == {} and hq.seen == []
    with store.transaction() as c:                              # the five were filed more than a day ago
        c.execute("UPDATE health_reports SET created_at=?", (H.shift(H.now(), hours=-25),))
    filed = file_due(api, run(api))[sig]
    assert filed["ticket"] and len(hq.tickets) == 1


def test_a_setup_finding_is_never_reported_to_the_tico_team(environment, readers, code_findings, hq):
    api, _ = built(environment)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    for rule in (True, False):
        set_rule(api, rule)
        r = api.post("/api/v2/health/findings/push_stuck:A/report", json={}, headers=signed_in())
        assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_tico_bug", r.text
    assert hq.seen == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM health_reports").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM approvals WHERE kind='support'").fetchone()[0] == 0


def test_a_setup_finding_naming_a_bot_is_never_reported_to_the_tico_team(environment, readers, hq):
    api, runner = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        H.event(c, H.KEEPER, "attempt.limited", SECRET_BOT, {"bot": SECRET_BOT})
    runner_errors(api, runner, ["2026-10-14T10:00Z Tico runner: research has unpushed commits and push failed"])
    with store.transaction() as c:                              # a computer silent 15 minutes with a request queued for its bot
        H.sync_registry(c, {SECRET_BOT: {"name": SECRET_BOT, "status": "active"}}, None)
        c.execute("INSERT OR REPLACE INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'t')",
                  (SECRET_BOT, runner["runner_id"], H.now()))
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-silent','human:ana',?,'chat','hi',?)",
                  ("bot:" + SECRET_BOT, H.now()))
        enqueue_stored(c, 'm-silent')
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", (H.shift(H.now(), minutes=-15), runner["runner_id"]))
    found = keys(api)
    silent = "computer_silent:" + runner["runner_id"]
    assert "limited_no_fallback:" + SECRET_BOT in found and "push_stuck:" + runner["runner_id"] in found and silent in found
    for rule in (True, False):                                  # neither filed nor put to a person to send
        set_rule(api, rule)
        for key in ("limited_no_fallback:" + SECRET_BOT, "push_stuck:" + runner["runner_id"], silent):
            r = api.post(f"/api/v2/health/findings/{key}/report", json={}, headers=signed_in())
            assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_tico_bug", r.text
    assert hq.seen == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM health_reports").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM approvals WHERE kind='support'").fetchone()[0] == 0


def test_a_report_the_owner_approved_is_the_teams_ticket_and_a_repeat_is_a_comment(environment, readers, hq):
    api, runner = built(environment)
    store = api.app.state.store
    set_rule(api, False)
    runner_errors(api, runner, STREAM)
    run(api)
    url = f"/api/v2/health/findings/event_stream:{runner['runner_id']}/report"
    approval = api.post(url, json={}, headers=signed_in()).json()["approval"]
    with store.transaction() as c:
        owner = api.app.state.auth.owner_identity(c)
        c.execute("UPDATE approvals SET decision='approved', decided_by_id=? WHERE id=?", (owner.actor, approval))
    api.app.state.support.file_approved(owner, approval)
    assert json.loads(hq.seen[-1].content)["source"] == "health_monitor"
    with store.read() as c:
        (request_key, source) = c.execute("SELECT request_key, json_extract(sent,'$.source') FROM support_tickets").fetchone()
    assert request_key == "approval:" + approval and source == "health_monitor"
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json()["tickets"] == []
    runner_errors(api, runner, [])
    run(api)
    runner_errors(api, runner, STREAM)
    run(api)
    repeat = api.post(url, json={}, headers=signed_in())
    assert repeat.status_code == 200 and repeat.json()["comment"], repeat.text
    (ticket,) = hq.tickets.values()
    assert [m["body"][:37] for m in ticket["messages"]] == ["[Health Monitor] Seen again (2 times)"]


@pytest.mark.parametrize("process,shown", [
    ("container:acme-tico-1", "a Tico container"),
    ("container:acme-server-1", "the Tico server container"),
    ("process:AcmePayroll", "a process"),
    ("process:python3", "process python3"),
])
def test_a_busy_process_is_reported_by_its_tico_name_only(environment, readers, hq, process, shown):
    api, runner = built(environment)
    with api.app.state.store.transaction() as c:
        MH.record_idle(c, runner["runner_id"],
                       MH.IdleLoad(sampled_at=H.now(), busy=[MH.IdleEntry(name=process, cpu=90, mb=300)]))
    result = run(api)
    assert result["details"]["filed"] == [f"local_busy:{runner['runner_id']}:{process}"]
    file_due(api, result)
    message = json.loads(hq.seen[0].content)["message"]
    assert f"Process: {shown}\n" in message and "acme" not in message.lower()


def test_the_idle_load_is_taken_from_a_computer_only(environment):
    api, _ = built(environment)
    r = api.post("/api/v2/runners/idle-load", json={"sampled_at": H.now(), "busy": []}, headers=signed_in())
    assert r.status_code == 403


def test_a_release_approval_ready_past_the_setting_is_one_finding_counted_not_repeated(environment, readers):
    api, _ = built(environment)
    store = api.app.state.store
    settings = dataclasses.replace(store.settings, release_approval_wait_minutes=5)
    release = {"url": "https://github.com/acme/tico/releases/tag/v1.2.0", "version": "1.2.0", "commit": "c" * 40,
               "content_sha256": "a" * 64}
    with store.transaction() as c:
        appr = H.approval_request(c, "bot:coo", "publish", release)
        blog = H.approval_request(c, "bot:coo", "publish", {"url": "https://acme.example/blog", "content_sha256": "b" * 64})
        c.execute("UPDATE approvals SET ready_since=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-4), appr["id"], blog["id"]))
    key = "release_approval_wait:" + appr["id"]
    assert keys(api, settings, "release_approval_wait") == []   # under the setting: nothing yet
    with store.transaction() as c:
        c.execute("UPDATE approvals SET ready_since=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-6), appr["id"], blog["id"]))
    assert keys(api, settings, "release_approval_wait") == [key]    # a blog post's wait is not a release's
    run(api, settings=settings)
    run(api, settings=settings)                                 # a noisy check: its second run files it
    (task,) = botops_tasks(api)
    with store.read() as c:
        who = H.person_name(c, H.actor_id(appr["decision_owner"]))
    assert f"has waited 6 min on {who}" in task["title"] and "1.2.0 (commit cccccccccccc)" in task["body"]
    run(api, settings=settings)                                 # seen again: counted, one task, no second one
    assert finding(api, key)["occurrences"] == 3 and len(botops_tasks(api)) == 1
    zero = dataclasses.replace(settings, release_approval_wait_minutes=0)
    assert keys(api, zero, "release_approval_wait") == []       # 0 turns the check off
    with store.transaction() as c:
        H.approval_decide(c, appr["decision_owner"], appr["id"], "declined")
    run(api, settings=zero)
    assert finding(api, key)["present"] == 0


def retire_migration():
    spec = importlib.util.spec_from_file_location("retire_health_monitor", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_db(botops="active", health_monitor=True):
    """A database with the tables the migration reads and writes, and the Health Monitor as it was."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE bots(id TEXT PRIMARY KEY, state TEXT);
        CREATE TABLE assignments(bot TEXT, runner_id TEXT, generation INTEGER, updated_at TEXT, updated_by TEXT);
        CREATE TABLE jobs(id TEXT PRIMARY KEY, bot_id TEXT, state TEXT);
        CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT, owner_id TEXT, status TEXT, closed_at TEXT,
                           closed_by_id TEXT, updated_at TEXT, note TEXT);
        CREATE TABLE task_events(id TEXT PRIMARY KEY, task_id TEXT, created_at TEXT, actor TEXT, field TEXT,
                                 old TEXT, new TEXT, note TEXT);
    """)
    conn.executemany("INSERT INTO bots VALUES(?,?)", [("coo", "active"), ("botops", botops)]
                     + ([(HM_SLUG, "active")] if health_monitor else []))
    conn.execute("INSERT INTO assignments VALUES(?,'R1',1,'t','t')", (HM_SLUG,))
    conn.executemany("INSERT INTO jobs VALUES(?,?,?)", [("j1", HM_SLUG, "queued"), ("j2", HM_SLUG, "completed")])
    conn.executemany("INSERT INTO tasks(id,title,owner_id,status) VALUES(?,?,?,?)", [
        ("t1", "Watch the runner", "bot:health-monitor", "open"),
        ("t2", "Check the event stream", "health-monitor", "review"),
        ("t3", "An old finding", "bot:health-monitor", "done"),
        ("t4", "Ana's own task", "human:ana", "open")])
    return conn


def test_the_archive_moves_open_tasks_to_botops_and_archives_the_bot():
    conn = legacy_db()
    retire_migration().up(conn)
    assert conn.execute("SELECT state FROM bots WHERE id=?", (HM_SLUG,)).fetchone()[0] == "archived"
    assert conn.execute("SELECT count(*) FROM assignments WHERE bot=?", (HM_SLUG,)).fetchone()[0] == 0
    assert dict(conn.execute("SELECT id,state FROM jobs").fetchall()) == {"j1": "canceled", "j2": "completed"}
    assert dict(conn.execute("SELECT id,owner_id FROM tasks").fetchall()) == {
        "t1": "bot:botops", "t2": "bot:botops", "t3": "bot:health-monitor", "t4": "human:ana"}
    events = conn.execute("SELECT task_id,field,new,actor FROM task_events ORDER BY task_id").fetchall()
    assert [tuple(e) for e in events] == [("t1", "owner_id", "bot:botops", "keeper"), ("t2", "owner_id", "bot:botops", "keeper")]


def test_running_the_archive_again_changes_nothing():
    conn, migration = legacy_db(), retire_migration()
    migration.up(conn)
    before = conn.execute("SELECT id,owner_id,status FROM tasks ORDER BY id").fetchall()
    migration.up(conn)
    assert conn.execute("SELECT count(*) FROM task_events").fetchone()[0] == 2
    assert conn.execute("SELECT id,owner_id,status FROM tasks ORDER BY id").fetchall() == before


def test_with_no_active_botops_the_open_tasks_close_as_superseded():
    conn = legacy_db(botops="paused")
    retire_migration().up(conn)
    rows = conn.execute("SELECT id,status,closed_by_id,note FROM tasks WHERE id IN ('t1','t2') ORDER BY id").fetchall()
    assert [tuple(r[:3]) for r in rows] == [("t1", "closed", "keeper"), ("t2", "closed", "keeper")]
    assert all(r[3].startswith("Superseded: Health Monitor retired") for r in rows)
    assert conn.execute("SELECT owner_id,status FROM tasks WHERE id='t3'").fetchone() == ("bot:health-monitor", "done")


def test_a_database_without_the_health_monitor_is_left_alone():
    conn = legacy_db(health_monitor=False)
    retire_migration().up(conn)
    assert conn.execute("SELECT count(*) FROM task_events").fetchone()[0] == 0
    assert conn.execute("SELECT owner_id FROM tasks WHERE id='t1'").fetchone()[0] == "bot:health-monitor"


def test_a_default_login_out_of_quota_is_a_finding_until_a_turn_there_completes(environment, readers):
    api, runner = built(environment)
    rid = runner["runner_id"]
    key = f"default_login_limited:{rid}:codex"
    # The Oct 18 case: hops to Codex on the computer's default login (no profile) and a check model, all refused at
    # once with no reset time. A hop's usage names only the primary that spent tokens.
    turn(api, rid, {**QUOTA, "fallback": "codex", "primary_limited": True,
                    "usage": {"runtime": "claude", "segments": [{"runtime": "claude"}]}}, 50)
    assert key not in login_findings(api)                       # one limit is not yet a pattern
    turn(api, rid, {**QUOTA, "check": "codex"}, 40)
    turn(api, rid, {"outcome": "failed", "fallback": "codex", "profile_used": None}, 35)   # another failure: says nothing
    turn(api, rid, {**QUOTA, "fallback": "codex"}, 30)
    # The same runtime signed in as a subscription profile is not the default login, and works.
    turn(api, rid, {"outcome": "completed", "fallback": "codex", "profile_used": "work"}, 20)
    # Claude on the default login works: only Codex is out.
    turn(api, rid, {"outcome": "completed", "profile_used": None, "usage": {"runtime": "claude"}}, 10)
    found = login_findings(api)
    assert list(found) == [key], found
    row_item = found[key]
    assert row_item["kind"] == "setup" and row_item["title"] == f"{CANARY}'s default Codex login is out of quota", row_item
    assert "3 turns on it since" in row_item["detail"] and "no reset time" in row_item["detail"], row_item["detail"]
    assert "fall back to it, will fail" in row_item["detail"]
    # Noisy like the other login checks: its second run files it. A turn on that login that completes clears it.
    assert key in keys(api)
    run(api)
    run(api)
    assert finding(api, key)["present"] == 1
    turn(api, rid, {"outcome": "completed", "check": "codex", "profile_used": None}, 5)
    assert login_findings(api) == {}
    run(api)
    assert finding(api, key)["present"] == 0

def test_a_paused_bots_routines_are_not_a_finding(environment):
    api, _ = built(environment)                                           # pausing holds routines by design (#500)
    with api.app.state.store.transaction() as c:      # a routine as a template seeds it: its automation, on
        made = routines.create(c, H.KEEPER, "coo", {"cron": "30 16 * * 5", "title": "Weekly backlog",
                                                     "text": "Review the backlog."}, key="weekly")
        assert made["automation"] and made["enabled"]
        c.execute("UPDATE bots SET state='paused' WHERE slug='coo'")
    assert keys(api) == []

@pytest.mark.parametrize("auto,enabled,config,raised", [
    (True, ("anthropic", "openai"), None, False),                      # the company's Codex row takes the turn
    (True, ("anthropic", "openai"), {"runtime": "claude"}, False),
    (False, ("anthropic", "openai"), None, True),                      # the owner turned automatic fallback off
    (True, ("anthropic",), None, True),                                # no row on another runtime
    (True, ("openai",), {"runtime": "codex"}, True),                   # the only row is the bot's own runtime...
    (True, ("anthropic", "openai"), {"fallback": {"off": True}}, True),   # ...or the bot turned fallback off...
    (True, ("anthropic", "openai"), {"fallback": {"harness": "codex"}}, True),   # ...or names one with no model
])
def test_limited_no_fallback_counts_the_company_fallback(environment, auto, enabled, config, raised):
    api, _ = built(environment)
    company(api, enabled, auto)
    limited(api, config=config)
    with api.app.state.store.read() as c:
        keys = {item["key"] for item in MH.findings(c, api.app.state.store.settings)}
    assert ("limited_no_fallback:" + SECRET_BOT in keys) is raised

def test_a_bots_own_fallback_still_counts_with_company_fallback_off(environment):
    api, _ = built(environment)
    company(api, auto=False)
    limited(api, config={"fallback": {"harness": "codex", "model": "gpt-6-luna"}})
    with api.app.state.store.read() as c:
        assert not [i for i in MH.findings(c, api.app.state.store.settings)
                    if i["check_id"] == "limited_no_fallback"]

@pytest.mark.parametrize("reset_minutes,raised", [(None, True), (60, True), (-1, False)])
def test_a_default_login_limit_clears_when_its_reset_time_passes(environment, reset_minutes, raised):
    api, runner = built(environment)
    reset = None if reset_minutes is None else H.shift(H.now(), minutes=reset_minutes)
    for ago in (30, 20):
        turn(api, runner["runner_id"], {**QUOTA, "check": "codex", "limit_resets_at": reset}, ago)
    found = login_findings(api)
    assert bool(found) is raised, found
    if reset_minutes == 60:
        assert "resets at " + reset[:16].replace("T", " ") + " UTC" in next(iter(found.values()))["detail"]

def test_a_default_login_limit_older_than_a_day_is_not_a_finding(environment):
    api, runner = built(environment)
    for ago in (25 * 60, 24 * 60 + 30):
        turn(api, runner["runner_id"], {**QUOTA, "check": "codex"}, ago)
    assert login_findings(api) == {}

def test_turns_that_failed_without_a_limit_are_not_a_quota_finding(environment):
    api, runner = built(environment)
    for ago in (30, 20):
        turn(api, runner["runner_id"], {"outcome": "failed", "check": "codex", "profile_used": None}, ago)
    assert login_findings(api) == {}
    turn(api, runner["runner_id"], {**QUOTA, "check": "codex"}, 10)       # one limit among them is still one
    assert login_findings(api) == {}

def test_a_bots_own_key_is_not_the_computers_login(environment):
    api, runner = built(environment)
    rid = runner["runner_id"]
    company(api, ("google", "openrouter", "openai"))
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {b: {"name": b, "status": "active"} for b in ("gem-a", "gem-b", "pi-a")}, None)
        for bot, harness in (("gem-a", "gemini-cli"), ("gem-b", "gemini-cli"), ("pi-a", "pi")):
            c.execute("INSERT OR REPLACE INTO bot_config(bot,owner_id) VALUES(?,'ana')", (bot,))
            BS.write(c, bot, {'harness': harness})
    # The Gemini CLI and pi sign in with each bot's own key (`api_keys = "bot"`): gem-a out of quota on its key, as a
    # turn of its own, a check and a fallback, says nothing about the computer, and gem-b working there clears nothing.
    turn(api, rid, {**QUOTA}, 50, bot="gem-a")
    turn(api, rid, {**QUOTA, "usage": {"runtime": "gemini", "harness": "gemini-cli"}}, 45, bot="gem-a")
    turn(api, rid, {**QUOTA, "check": "gemini"}, 40, bot="gem-a")
    turn(api, rid, {**QUOTA, "fallback": "pi"}, 35, bot="pi-a")
    turn(api, rid, {**QUOTA, "fallback": "pi"}, 30, bot="pi-a")
    assert login_findings(api) == {}
    # Codex is signed in by the computer: two limits there are its finding, and gem-b's completed turn leaves it.
    turn(api, rid, {**QUOTA, "check": "codex"}, 25, bot="gem-a")
    turn(api, rid, {**QUOTA, "fallback": "codex"}, 20, bot="pi-a")
    turn(api, rid, {"outcome": "completed", "profile_used": None}, 10, bot="gem-b")
    assert list(login_findings(api)) == [f"default_login_limited:{rid}:codex"]

@pytest.mark.parametrize("isolated", [True, False])
def test_a_used_up_login_under_the_gateway_choice_is_one_plain_finding(environment, monkeypatch, isolated):
    api, runner = built(environment)
    rid = runner["runner_id"]
    gateway_choice(api, monkeypatch, rid, isolated)
    limited(api, config={"runtime": "claude"})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'t')",
                  (SECRET_BOT, rid, H.now()))
    for ago in (30, 20):
        turn(api, rid, {**QUOTA, "usage": {"runtime": "claude"}}, ago, bot=SECRET_BOT)
    with api.app.state.store.read() as c:
        found = [i for i in MH.findings(c, api.app.state.store.settings)
                 if i["check_id"] in ("limited_no_fallback", "default_login_limited")]
    if isolated:
        # The Gateway takes the turn last: the bot is not stuck, and the login says so in plain words.
        assert [(i["check_id"], i["title"]) for i in found] == [
            ("default_login_limited", f"{CANARY}'s Claude login is used up")]
        assert found[0]["detail"] == f"{CANARY}'s Claude login is used up. Bots are on Tico Gateway until it resets."
        return
    # A Mac skips the Gateway row, so the bot is stopped, and that finding says why and what to do.
    stopped = [i for i in found if i["check_id"] == "limited_no_fallback"]
    assert [i["title"] for i in stopped] == [f"{SECRET_BOT} stopped: its Claude login is used up"]
    assert "no computer here can run Tico Gateway work" in stopped[0]["detail"]
    assert stopped[0]["fix"] == "Sign in another Claude login, or add a Docker runner."

def test_the_gateway_row_counts_as_a_fallback_only_under_the_gateway_choices(environment, monkeypatch):
    api, runner = built(environment)
    gateway_choice(api, monkeypatch, runner["runner_id"], True)
    with api.app.state.store.transaction() as c:
        record = providers.load(c)
        providers.save(c, "human:test", {"expected_revision": record["revision"], "preset": "subscriptions"}, H.now())
    limited(api, config={"runtime": "claude"})
    with api.app.state.store.read() as c:
        found = [i for i in MH.findings(c, api.app.state.store.settings)
                 if i["check_id"] == "limited_no_fallback"]
    assert [i["title"] for i in found] == [f"{SECRET_BOT} stopped on a usage limit and has no fallback model"]

def test_the_monitor_files_for_botops_and_a_task_for_a_person_is_refused(api):
    # The Health Monitor gives its fixes to BotOps only (docs/health-monitor.md): a task for a person, and a hand-off to
    # one, are refused with where to file it and nothing is written; the same task for BotOps is accepted.
    with api.app.state.store.transaction() as c:
        for slug in ("health-monitor", "botops"):
            c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (slug, slug, slug))
            c.execute("UPDATE bots SET state='active' WHERE id=?", (slug,))
            if c.execute("INSERT OR IGNORE INTO bot_config(bot,owner_id) VALUES(?,'ana')", (slug,)).rowcount:
                BS.write(c, slug, {"name": slug, "runtime": "fake", "status": "active"})
        title, body = "Wake Support Agent (cloud) and check it reports in", "Its last check-in was 40 minutes ago."
        with pytest.raises(H.Refused) as refused:
            H.task_create(c, "bot:" + HM_SLUG, title, body, "human:ana", lint=False)
        assert refused.value.rule == "botops-only" and "--owner botops" in str(refused.value)
        assert c.execute("SELECT count(*) FROM tasks WHERE title=?", (title,)).fetchone()[0] == 0
        fix = H.task_create(c, "bot:" + HM_SLUG, title, body, "botops", lint=False)
        assert H.task(c, fix["id"])["owner_id"] == "bot:botops"
        with pytest.raises(H.Refused) as handed:
            H.task_update(c, "bot:" + HM_SLUG, fix["id"], owner="human:ana")
        assert handed.value.rule == "botops-only"
        assert H.task(c, fix["id"])["owner_id"] == "bot:botops"

@pytest.mark.parametrize("release,severity", [("0.0.9", "bad"), ("0.4.40", "warn")])
def test_a_computer_on_a_release_the_server_cannot_work_with_is_bad(environment, monkeypatch, release, severity):
    monkeypatch.setenv("TICO_VERSION", "0.4.45")
    api, runner = built(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", (H.now(), runner["runner_id"]))
        c.execute("INSERT INTO runner_versions(runner_id,release,reported_at) VALUES(?,?,?) ON CONFLICT(runner_id) "
                  "DO UPDATE SET release=excluded.release,update_state='',update_error=''",
                  (runner["runner_id"], release, H.now()))
        found = {f["check_id"]: f for f in MH.findings(c, api.app.state.store.settings)}
    assert found["runner_outdated"]["severity"] == severity

def test_event_stream_counts_drops_and_not_the_ones_a_server_restart_caused(environment, readers, monkeypatch):
    api, runner = built(environment)
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T23:00:00Z")      # this process: far from the lines
    with api.app.state.store.transaction() as c:                                     # an earlier start: the update
        c.execute("INSERT INTO flight_events(created_at,kind,detail) VALUES(?,?,'{}')",
                  (calendar.timegm(time.strptime("2026-01-10T21:06:44Z", "%Y-%m-%dT%H:%M:%SZ")), "start"))
    lost = "%s Tico runner: event stream lost (TimeoutError: error decoding response body); polling until it is back"
    gone = "%s Tico runner: event stream unavailable (HTTP 502); polling"
    two_drops_and_a_restart = [lost % "2026-01-10T19:42:10Z", lost % "2026-01-10T20:42:47Z", gone % "2026-01-10T20:42:48Z",
                               lost % "2026-01-10T21:06:39Z", "2026-01-10T21:06:41Z Tico runner: hub unavailable (HTTP 502)"]
    runner_errors(api, runner, two_drops_and_a_restart)
    assert keys(api) == []                         # the 21:06 drop is the restart; "unavailable" is the same drop
    runner_errors(api, runner, two_drops_and_a_restart + [lost % "2026-13-45T25:00:00Z"])
    key = "event_stream:" + runner["runner_id"]
    assert keys(api) == [key]                      # a line with an impossible date counts and stops nothing
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T19:43:00Z")      # this process began at 19:43
    runner_errors(api, runner, two_drops_and_a_restart + [lost % "2026-01-10T22:30:00Z"])
    run(api)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT present FROM health_findings WHERE key=?", (key,)).fetchone()[0] == 0

def test_event_stream_does_not_count_drops_the_runner_says_were_back_within_seconds(environment, monkeypatch):
    api, runner = built(environment)
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T05:48:11Z")      # far from every line below
    lost = ("%s Tico runner: event stream lost (ConnectionError: error decoding response body: stream error received: "
            "unexpected internal error encountered); polling until it is back")
    back = "%s Tico runner: event stream back after %d s; polling stops"
    resets = []                                    # a tunnel's edge resetting the stream: each back in 1-3 s
    for at, took in (("06:43:46", 2), ("07:12:38", 1), ("07:14:27", 3), ("07:24:26", 1), ("07:27:22", 2)):
        resets += [lost % f"2026-01-10T{at}Z", back % (f"2026-01-10T{at[:-2]}59Z", took)]
    runner_errors(api, runner, resets)
    assert keys(api) == []
    key = "event_stream:" + runner["runner_id"]
    stayed_down = [lost % "2026-01-10T08:00:00Z", back % ("2026-01-10T08:02:00Z", 120),
                   lost % "2026-01-10T08:10:00Z", "2026-01-10T08:10:01Z Tico runner: event stream unavailable (x); polling",
                   back % ("2026-01-10T08:11:00Z", 60), lost % "2026-01-10T08:20:00Z"]     # the last: not back yet
    runner_errors(api, runner, resets + stayed_down)
    assert keys(api) == [key]                      # three drops that lasted count; the brief ones still do not
    with api.app.state.store.read() as c:
        assert c.execute("SELECT detail FROM health_findings WHERE key=?", (key,)).fetchone()[0].startswith("3 of")
    # A "back" line answers only the drop just before it: two drops in a row and one quick return is one brief drop.
    runner_errors(api, runner, [lost % "2026-01-10T09:00:00Z", lost % "2026-01-10T09:00:30Z",
                                back % ("2026-01-10T09:00:32Z", 2), lost % "2026-01-10T09:30:00Z"])
    with api.app.state.store.read() as c:
        assert not [f for f in MH._runner_checks(c, api.app.state.store.settings)
                    if f["check_id"] == "event_stream"]     # two drops that lasted: under the threshold
    # A "back" with no "lost" kept before it (a clean end is not an error line) answers nothing; 30 s is brief, 31 is not.
    runner_errors(api, runner, [lost % "2026-01-10T10:00:00Z", back % ("2026-01-10T10:02:00Z", 120),
                                back % ("2026-01-10T10:20:01Z", 1), lost % "2026-01-10T10:30:00Z",
                                back % ("2026-01-10T10:30:31Z", 31), lost % "2026-01-10T10:40:00Z",
                                back % ("2026-01-10T10:40:30Z", 30), lost % "2026-01-10T10:50:00Z"])
    with api.app.state.store.read() as c:
        (found,) = [f for f in MH._runner_checks(c, api.app.state.store.settings)
                    if f["check_id"] == "event_stream"]
    assert found["detail"].startswith("3 of")
