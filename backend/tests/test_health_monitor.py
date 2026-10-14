"""The Health Monitor (backend/health_monitor.py, docs/health-monitor.md): off until the owner turns it on, a scan that
wakes it only for a finding the last scan did not have, and one Tico bug report per signature that carries the finding
and the redacted diagnostics, never task text, with a message on the same ticket when it comes back."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from backend import health_monitor, support
from backend.auth import Identity
from backend.store import H
from backend.tests.test_goal_manager import BASE
from backend.tests.test_onboarding import as_person, draft, environment, machine, signed_in  # noqa: F401
from backend.tests.test_support import FakeHQ

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "health-monitor"
SECRET_TASK = "Acme payroll for March"
CANARY = "Acme Payroll Mac"                  # a computer's label: local, never in a report
SECRET_BOT = "acme-payroll-bot"


@pytest.fixture
def hq(monkeypatch):
    fake = FakeHQ()
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(fake))
    for name in ("TICO_SUPPORT", "TICO_TELEMETRY", "DO_NOT_TRACK", "TICO_HQ_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_VERSION", "0.3.45")
    return fake


def built(environment):
    api = environment(seed={}, cards=BASE)
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "health-monitor")
    runner = machine(api, label=CANARY)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api, runner


def as_monitor(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?)",
                  (health_monitor.SLUG, "hermes", "hash-hm", "2026-01-01T00:00:00Z", "human:test"))
    api.app.state.store.settings.test_identities["bot-hm"] = Identity("bot:" + health_monitor.SLUG, "bot", agent="hermes")
    return signed_in("bot-hm")


def scan(api):
    with api.app.state.store.transaction() as c:
        return health_monitor.scan(c, api.app.state.auth, api.app.state.store.settings)


def monitor_tasks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body FROM tasks WHERE owner=? ORDER BY created",
                                           ("bot:" + health_monitor.SLUG,))]


def notices(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                         "WHERE t.id=? AND m.body LIKE 'Seen again%'", (task_id,)).fetchone()[0]


def runner_errors(api, runner, lines):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?",
                  (json.dumps({"schema_version": 1, "bots": {}, "recent_errors": lines}), runner["runner_id"]))


STREAM = ["2026-10-14T10:0%dZ Tico runner: event stream lost (ReadTimeout); polling until it is back" % i for i in range(3)]


def test_it_is_off_until_the_owner_turns_it_on_and_off_again(environment):
    api, _ = built(environment)
    with api.app.state.store.read() as c:
        assert not H.bot(c, health_monitor.SLUG)                         # never added on its own
    assert scan(api) == []
    member = as_person(api, "riley")
    assert api.post("/api/v2/health-monitor/turn-on", json={}, headers=member).status_code == 403
    on = api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    assert on.status_code == 200 and on.json()["state"] == "active", on.text
    seen = api.get("/api/v2/health-monitor", headers=signed_in()).json()
    assert seen["on"] and seen["reports_without_asking"] is True         # on by default once it is on
    off = api.post("/api/v2/health-monitor/turn-off", json={}, headers=signed_in())
    assert off.json()["state"] == "paused" and scan(api) == []


def test_a_finding_wakes_it_once_is_filed_once_without_task_text_and_a_repeat_goes_on_its_ticket(environment, hq):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    api.post("/api/v2/tasks", json={"title": SECRET_TASK, "owner": "bot:coo"}, headers=signed_in())
    runner_errors(api, runner, STREAM)
    key = "event_stream:" + runner["runner_id"]
    assert scan(api) == [key]
    (task,) = monitor_tasks(api)
    assert task["title"].startswith("Health: ") and "Kind: tico-bug" in task["body"] and "Signature: hm-" in task["body"]
    assert scan(api) == [] and len(monitor_tasks(api)) == 1                # still there: nothing said
    bot = as_monitor(api)
    assert api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=as_person(api, "riley")).status_code == 403
    r = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot)
    assert r.status_code == 200, r.text
    (request,) = hq.seen
    sent = json.loads(request.content)
    assert set(sent) == {"message", "version", "install_id", "diagnostics"}
    assert "Signature: hm-" in sent["message"] and SECRET_TASK not in request.content.decode()
    assert CANARY not in sent["message"] and runner["runner_id"] not in sent["message"]
    with api.app.state.store.read() as c:                                 # the owner's ticket, not the bot's
        assert c.execute("SELECT actor FROM support_tickets").fetchone()[0].startswith("human:")
    again = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot).json()
    assert again["again"] and len(hq.seen) == 1
    runner_errors(api, runner, [])
    scan(api)                                                             # gone
    runner_errors(api, runner, STREAM)
    assert scan(api) == [key]                                             # back: the same task is woken
    assert len(monitor_tasks(api)) == 1 and notices(api, task["id"]) == 1
    repeat = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot).json()
    assert repeat["comment"] and len(hq.tickets) == 1
    (ticket,) = hq.tickets.values()
    assert [m["body"][:37] for m in ticket["messages"]] == ["[Health Monitor] Seen again (2 times)"]


def test_with_the_rule_off_a_report_is_an_approval_and_nothing_leaves(environment, hq):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    api.put("/api/v2/access/rules", json={"health_monitor_reports": False}, headers=signed_in())
    runner_errors(api, runner, STREAM)
    scan(api)
    r = api.post(f"/api/v2/health-monitor/findings/event_stream:{runner['runner_id']}/report", json={},
                 headers=as_monitor(api))
    assert r.status_code == 200 and r.json()["approval"], r.text
    assert hq.seen == []


def test_a_setup_finding_naming_a_bot_is_never_reported_to_hq(environment, hq):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "attempt.limited", SECRET_BOT, {"bot": SECRET_BOT})
    runner_errors(api, runner, ["2026-10-14T10:00Z Tico runner: research has unpushed commits and push failed"])
    found = scan(api)
    assert "limited_no_fallback:" + SECRET_BOT in found and "push_stuck:" + runner["runner_id"] in found
    bot = as_monitor(api)
    for rule in (True, False):                                   # neither filed nor put to a person to send
        api.put("/api/v2/access/rules", json={"health_monitor_reports": rule}, headers=signed_in())
        for key in ("limited_no_fallback:" + SECRET_BOT, "push_stuck:" + runner["runner_id"]):
            r = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot)
            assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_tico_bug", r.text
    assert hq.seen == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM health_reports").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM approvals WHERE kind='support'").fetchone()[0] == 0


def test_a_declined_approval_lets_the_signature_be_reported_again(environment, hq):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    api.put("/api/v2/access/rules", json={"health_monitor_reports": False}, headers=signed_in())
    runner_errors(api, runner, STREAM)
    scan(api)
    url, bot = f"/api/v2/health-monitor/findings/event_stream:{runner['runner_id']}/report", as_monitor(api)
    first = api.post(url, json={}, headers=bot).json()["approval"]
    pending = api.post(url, json={}, headers=bot).json()
    assert pending["approval"] == first and pending["again"]          # still waiting for the person
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE approvals SET decision='declined' WHERE id=?", (first,))
    second = api.post(url, json={}, headers=bot).json()
    assert second["approval"] and second["approval"] != first and "again" not in second
    assert hq.seen == []


@pytest.mark.parametrize("process,shown", [
    ("container:acme-tico-1", "a Tico container"),
    ("container:acme-server-1", "the Tico server container"),
    ("process:AcmePayroll", "a process"),
    ("process:python3", "process python3"),
])
def test_a_busy_process_is_reported_by_its_tico_name_only(environment, hq, process, shown):
    api, _ = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    bot = as_monitor(api)
    key = api.post("/api/v2/health-monitor/local-findings", json={"process": process, "cpu": 90, "mb": 300},
                   headers=bot).json()["key"]
    r = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot)
    assert r.status_code == 200, r.text
    message = json.loads(hq.seen[0].content)["message"]
    assert f"Process: {shown}\n" in message and "acme" not in message.lower()
