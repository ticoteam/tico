"""The Health Monitor (backend/health_monitor.py, docs/health-monitor.md): off until the owner turns it on, a scan that
wakes it only for a new finding, one whose severity rose or one back after its task was finished (seeing a finding
again counts and starts no run), and one Tico bug report per signature that carries the finding and the redacted
diagnostics, never task text, with a message on the same ticket when it comes back."""

import calendar
import dataclasses
import json
import shutil
import time
from pathlib import Path

import httpx
import pytest

from backend import diagnostics, health_monitor, providers, routines, support
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


def scan_with(api, settings):
    with api.app.state.store.transaction() as c:
        return health_monitor.scan(c, api.app.state.auth, settings)


def monitor_tasks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body FROM tasks WHERE owner=? ORDER BY created",
                                           ("bot:" + health_monitor.SLUG,))]


def notices(api, task_id, start="Seen again"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                         "WHERE t.id=? AND m.body LIKE ?", (task_id, start + "%")).fetchone()[0]


def wakes(api):
    """Runs queued for the Health Monitor: the jobs its messages started (the runs token saving 1/9 records)."""
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot=?", (health_monitor.SLUG,)).fetchone()[0]


def finding(api, key):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM health_findings WHERE key=?", (key,)).fetchone())


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
    assert set(sent) == {"message", "version", "install_id", "diagnostics", "source"} and sent["source"] == "health_monitor"
    assert "Signature: hm-" in sent["message"] and SECRET_TASK not in request.content.decode()
    assert CANARY not in sent["message"] and runner["runner_id"] not in sent["message"]
    with api.app.state.store.read() as c:                                 # the owner's ticket, not the bot's
        assert c.execute("SELECT actor FROM support_tickets").fetchone()[0].startswith("human:")
    again = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot).json()
    assert again["again"] and len(hq.seen) == 1
    runner_errors(api, runner, [])
    scan(api)                                                             # gone
    runner_errors(api, runner, STREAM)
    assert scan(api) == []                                    # back while its task is open: counted, wakes nothing
    assert len(monitor_tasks(api)) == 1 and notices(api, task["id"]) == 1
    assert finding(api, key)["occurrences"] == 3                          # seen by three scans
    repeat = api.post(f"/api/v2/health-monitor/findings/{key}/report", json={}, headers=bot).json()
    assert repeat["comment"] and len(hq.tickets) == 1
    (ticket,) = hq.tickets.values()
    assert [m["body"][:37] for m in ticket["messages"]] == ["[Health Monitor] Seen again (3 times)"]
    seen_again = [x for x in hq.seen if x.url.path.endswith("/messages")][-1]
    assert set(json.loads(seen_again.content)) == {"message"}      # "Seen again" is a message: it names no source
    with api.app.state.store.transaction() as c:          # a day since its last wake no longer wakes it again
        c.execute("UPDATE health_findings SET last_woke=? WHERE key=?", (H.shift(H.now(), hours=-25), key))
    runner_errors(api, runner, [])
    scan(api)
    runner_errors(api, runner, STREAM)
    assert scan(api) == [] and len(monitor_tasks(api)) == 1 and notices(api, task["id"]) == 2


def test_a_finding_seen_again_counts_and_starts_no_run(environment):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    runner_errors(api, runner, STREAM)
    key = "event_stream:" + runner["runner_id"]
    before = wakes(api)
    assert scan(api) == [key] and wakes(api) == before + 1                # new: one run
    (task,) = monitor_tasks(api)
    first = finding(api, key)
    assert first["occurrences"] == 1 and first["severity"] == "warn"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE health_findings SET last_seen=? WHERE key=?", (H.shift(H.now(), hours=-1), key))
    assert scan(api) == []                                                # still there an hour later
    again = finding(api, key)
    assert again["occurrences"] == 2 and again["last_seen"] > H.shift(H.now(), minutes=-1)
    runner_errors(api, runner, [])
    scan(api)                                                             # gone, its task still open
    assert finding(api, key)["present"] == 0
    runner_errors(api, runner, STREAM)
    assert scan(api) == [] and wakes(api) == before + 1                   # back: counted, no run for anyone
    assert finding(api, key)["occurrences"] == 3 and len(monitor_tasks(api)) == 1
    with api.app.state.store.read() as c:                                 # a quiet note the bot reads next turn
        (note,) = c.execute("SELECT m.body,m.refs_json FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                            "WHERE t.id=? AND m.body LIKE 'Seen again%'", (task["id"],)).fetchall()
    assert note["body"].startswith("Seen again (3 times, last ") and json.loads(note["refs_json"])["quiet"]
    shown = api.get("/api/v2/health-monitor", headers=signed_in()).json()["findings"]
    assert [(f["key"], f["occurrences"], f["severity"]) for f in shown] == [(key, 3, "warn")]
    assert shown[0]["last_seen"] == finding(api, key)["last_seen"]


def test_a_finding_back_after_its_task_was_finished_counts_as_new(environment):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    runner_errors(api, runner, STREAM)
    key = "event_stream:" + runner["runner_id"]
    assert scan(api) == [key]
    (task,) = monitor_tasks(api)
    with api.app.state.store.transaction() as c:                          # the bot finished it ("Cleared on its own")
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
    assert scan(api) == []                                                # still there: its task being done says nothing
    runner_errors(api, runner, [])
    scan(api)
    runner_errors(api, runner, STREAM)
    before = wakes(api)
    assert scan(api) == [key] and wakes(api) == before + 1                # back after it was cleared: new, it runs
    assert notices(api, task["id"], "Back after its task was finished (seen 3 times)") == 1
    assert notices(api, task["id"]) == 0
    with api.app.state.store.transaction() as c:                          # a closed task: a new one
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (task["id"],))
    runner_errors(api, runner, [])
    scan(api)
    runner_errors(api, runner, STREAM)
    assert scan(api) == [key] and wakes(api) == before + 2
    old, new = monitor_tasks(api)
    assert old["id"] == task["id"] and "Seen: 4 time(s)" in new["body"]
    with api.app.state.store.transaction() as c:                          # a declined task: a new one too
        c.execute("UPDATE tasks SET status='declined' WHERE id=?", (new["id"],))
    runner_errors(api, runner, [])
    scan(api)
    runner_errors(api, runner, STREAM)
    assert scan(api) == [key] and wakes(api) == before + 3
    assert len(monitor_tasks(api)) == 3 and "Seen: 5 time(s)" in monitor_tasks(api)[-1]["body"]


def test_a_finding_whose_severity_rises_wakes_it(environment, monkeypatch):
    api, _ = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    level = {"now": "warn"}
    item = lambda: health_monitor._finding("runner_outdated:r1", "runner_outdated", health_monitor.SETUP,
                                           "Studio Mac runs an older Tico release", "Release 0.0.9.", "Update it.",
                                           severity=level["now"])
    monkeypatch.setattr(health_monitor, "findings", lambda c, settings: [item()])
    key = "runner_outdated:r1"
    assert scan(api) == [key]
    (task,) = monitor_tasks(api)
    assert "Severity: warn" in task["body"]
    before = wakes(api)
    level["now"] = "bad"
    assert scan(api) == [key] and wakes(api) == before + 1                # worse: woken on its open task
    assert notices(api, task["id"], "Worse now (bad, was warn") == 1 and len(monitor_tasks(api)) == 1
    assert scan(api) == [] and wakes(api) == before + 1                   # still bad: counted only
    level["now"] = "warn"
    assert scan(api) == [] and finding(api, key)["severity"] == "warn"    # better: counted, nobody woken
    level["now"] = "bad"
    assert scan(api) == [key] and wakes(api) == before + 2                # worse again
    assert finding(api, key)["occurrences"] == 5


def test_a_release_approval_ready_past_the_setting_is_one_finding_counted_not_repeated(environment):
    api, _ = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    settings = dataclasses.replace(api.app.state.store.settings, release_approval_wait_minutes=5)
    release = {"url": "https://github.com/acme/tico/releases/tag/v1.2.0", "version": "1.2.0", "commit": "c" * 40,
               "content_sha256": "a" * 64}
    with api.app.state.store.transaction() as c:
        appr = H.approval_request(c, "bot:coo", "publish", release)
        blog = H.approval_request(c, "bot:coo", "publish", {"url": "https://acme.example/blog", "content_sha256": "b" * 64})
        c.execute("UPDATE approvals SET ready_since=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-4), appr["id"], blog["id"]))
    key = "release_approval_wait:" + appr["id"]
    assert scan_with(api, settings) == []                                  # under the setting: nothing yet
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE approvals SET ready_since=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-6), appr["id"], blog["id"]))
    before = wakes(api)
    assert scan_with(api, settings) == [key] and wakes(api) == before + 1  # a blog post's wait is not a release's
    (task,) = monitor_tasks(api)
    with api.app.state.store.read() as c:
        who = H.person_name(c, H.actor_id(appr["decision_owner"]))
    assert f"has waited 6 min on {who}" in task["title"] and "1.2.0 (commit cccccccccccc)" in task["body"]
    assert scan_with(api, settings) == [] and wakes(api) == before + 1     # seen again: counted, no run, one finding
    assert finding(api, key)["occurrences"] == 2 and len(monitor_tasks(api)) == 1
    assert scan_with(api, dataclasses.replace(settings, release_approval_wait_minutes=0)) == []
    assert finding(api, key)["present"] == 0                               # 0 turns the check off
    with api.app.state.store.transaction() as c:
        H.approval_decide(c, appr["decision_owner"], appr["id"], "declined")
    assert scan_with(api, settings) == [] and finding(api, key)["present"] == 0


@pytest.mark.parametrize("release,severity", [("0.0.9", "bad"), ("0.4.40", "warn")])
def test_a_computer_on_a_release_the_server_cannot_work_with_is_bad(environment, monkeypatch, release, severity):
    monkeypatch.setenv("TICO_VERSION", "0.4.45")
    api, runner = built(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen=? WHERE id=?", (H.now(), runner["runner_id"]))
        c.execute("INSERT INTO runner_versions(runner_id,release,reported) VALUES(?,?,?) ON CONFLICT(runner_id) "
                  "DO UPDATE SET release=excluded.release,update_state='',update_error=''",
                  (runner["runner_id"], release, H.now()))
        found = {f["check_id"]: f for f in health_monitor.findings(c, api.app.state.store.settings)}
    assert found["runner_outdated"]["severity"] == severity


def test_event_stream_counts_drops_and_not_the_ones_a_server_restart_caused(environment, monkeypatch):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T23:00:00Z")      # this process: far from the lines
    with api.app.state.store.transaction() as c:                                     # an earlier start: the update
        c.execute("INSERT INTO flight_events(ts,kind,detail_json) VALUES(?,?,'{}')",
                  (calendar.timegm(time.strptime("2026-01-10T21:06:44Z", "%Y-%m-%dT%H:%M:%SZ")), "start"))
    lost = "%s Tico runner: event stream lost (TimeoutError: error decoding response body); polling until it is back"
    gone = "%s Tico runner: event stream unavailable (HTTP 502); polling"
    two_drops_and_a_restart = [lost % "2026-01-10T19:42:10Z", lost % "2026-01-10T20:42:47Z", gone % "2026-01-10T20:42:48Z",
                               lost % "2026-01-10T21:06:39Z", "2026-01-10T21:06:41Z Tico runner: hub unavailable (HTTP 502)"]
    runner_errors(api, runner, two_drops_and_a_restart)
    assert scan(api) == []                         # the 21:06 drop is the restart; "unavailable" is the same drop
    runner_errors(api, runner, two_drops_and_a_restart + [lost % "2026-13-45T25:00:00Z"])
    key = "event_stream:" + runner["runner_id"]
    assert scan(api) == [key]                      # a line with an impossible date counts and stops nothing
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T19:43:00Z")      # this process began at 19:43
    runner_errors(api, runner, two_drops_and_a_restart + [lost % "2026-01-10T22:30:00Z"])
    scan(api)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT present FROM health_findings WHERE key=?", (key,)).fetchone()[0] == 0


def test_event_stream_does_not_count_drops_the_runner_says_were_back_within_seconds(environment, monkeypatch):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T05:48:11Z")      # far from every line below
    lost = ("%s Tico runner: event stream lost (ConnectionError: error decoding response body: stream error received: "
            "unexpected internal error encountered); polling until it is back")
    back = "%s Tico runner: event stream back after %d s; polling stops"
    resets = []                                    # a tunnel's edge resetting the stream: each back in 1-3 s
    for at, took in (("06:43:46", 2), ("07:12:38", 1), ("07:14:27", 3), ("07:24:26", 1), ("07:27:22", 2)):
        resets += [lost % f"2026-01-10T{at}Z", back % (f"2026-01-10T{at[:-2]}59Z", took)]
    runner_errors(api, runner, resets)
    assert scan(api) == []
    key = "event_stream:" + runner["runner_id"]
    stayed_down = [lost % "2026-01-10T08:00:00Z", back % ("2026-01-10T08:02:00Z", 120),
                   lost % "2026-01-10T08:10:00Z", "2026-01-10T08:10:01Z Tico runner: event stream unavailable (x); polling",
                   back % ("2026-01-10T08:11:00Z", 60), lost % "2026-01-10T08:20:00Z"]     # the last: not back yet
    runner_errors(api, runner, resets + stayed_down)
    assert scan(api) == [key]                      # three drops that lasted count; the brief ones still do not
    with api.app.state.store.read() as c:
        assert c.execute("SELECT detail FROM health_findings WHERE key=?", (key,)).fetchone()[0].startswith("3 of")
    # A "back" line answers only the drop just before it: two drops in a row and one quick return is one brief drop.
    runner_errors(api, runner, [lost % "2026-01-10T09:00:00Z", lost % "2026-01-10T09:00:30Z",
                                back % ("2026-01-10T09:00:32Z", 2), lost % "2026-01-10T09:30:00Z"])
    with api.app.state.store.read() as c:
        assert not [f for f in health_monitor._runner_checks(c, api.app.state.store.settings)
                    if f["check_id"] == "event_stream"]     # two drops that lasted: under the threshold
    # A "back" with no "lost" kept before it (a clean end is not an error line) answers nothing; 30 s is brief, 31 is not.
    runner_errors(api, runner, [lost % "2026-01-10T10:00:00Z", back % ("2026-01-10T10:02:00Z", 120),
                                back % ("2026-01-10T10:20:01Z", 1), lost % "2026-01-10T10:30:00Z",
                                back % ("2026-01-10T10:30:31Z", 31), lost % "2026-01-10T10:40:00Z",
                                back % ("2026-01-10T10:40:30Z", 30), lost % "2026-01-10T10:50:00Z"])
    with api.app.state.store.read() as c:
        (found,) = [f for f in health_monitor._runner_checks(c, api.app.state.store.settings)
                    if f["check_id"] == "event_stream"]
    assert found["detail"].startswith("3 of")


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


def test_a_repeat_goes_on_the_ticket_of_the_person_who_approved_it(environment, hq):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    api.put("/api/v2/access/rules", json={"health_monitor_reports": False}, headers=signed_in())
    runner_errors(api, runner, STREAM)
    scan(api)
    url, bot = f"/api/v2/health-monitor/findings/event_stream:{runner['runner_id']}/report", as_monitor(api)
    approval = api.post(url, json={}, headers=bot).json()["approval"]
    as_person(api, "riley")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE approvals SET decision='approved', decided_by='human:riley' WHERE id=?", (approval,))
    api.app.state.support.file_approved(Identity("bot:" + health_monitor.SLUG, "bot"), approval)
    assert json.loads(hq.seen[-1].content)["source"] == "health_monitor"     # approved by a person, still the monitor's
    runner_errors(api, runner, [])
    scan(api)
    runner_errors(api, runner, STREAM)
    scan(api)
    repeat = api.post(url, json={}, headers=bot)
    assert repeat.status_code == 200 and repeat.json()["comment"], repeat.text
    (ticket,) = hq.tickets.values()
    assert [m["body"][:37] for m in ticket["messages"]] == ["[Health Monitor] Seen again (2 times)"]


def test_a_setup_finding_naming_a_bot_is_never_reported_to_hq(environment, hq):
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "attempt.limited", SECRET_BOT, {"bot": SECRET_BOT})
    runner_errors(api, runner, ["2026-10-14T10:00Z Tico runner: research has unpushed commits and push failed"])
    # A computer silent 15 minutes with a request queued for its bot.
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {SECRET_BOT: {"name": SECRET_BOT, "status": "active"}}, None)
        c.execute("INSERT OR REPLACE INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t')",
                  (SECRET_BOT, runner["runner_id"], H.now()))
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES('m-silent','human:ana',?,'chat','hi',?)",
                  ("bot:" + SECRET_BOT, H.now()))
        c.execute("UPDATE runners SET last_seen=? WHERE id=?", (H.shift(H.now(), minutes=-15), runner["runner_id"]))
    found = scan(api)
    silent = "computer_silent:" + runner["runner_id"]
    assert "limited_no_fallback:" + SECRET_BOT in found and "push_stuck:" + runner["runner_id"] in found and silent in found
    bot = as_monitor(api)
    for rule in (True, False):                                   # neither filed nor put to a person to send
        api.put("/api/v2/access/rules", json={"health_monitor_reports": rule}, headers=signed_in())
        for key in ("limited_no_fallback:" + SECRET_BOT, "push_stuck:" + runner["runner_id"], silent):
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


def test_a_paused_bots_routines_are_not_a_finding(environment):
    api, _ = built(environment)                                           # pausing holds routines by design (#500)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    with api.app.state.store.transaction() as c:      # a routine as a template seeds it: its automation, on
        made = routines.create(c, H.KEEPER, "coo", {"cron": "30 16 * * 5", "title": "Weekly backlog",
                                                     "text": "Review the backlog."}, key="weekly")
        assert made["automation"] and made["enabled"]
        c.execute("UPDATE bots SET state='paused' WHERE slug='coo'")
    assert scan(api) == []


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
            c.execute("INSERT OR REPLACE INTO bot_config(bot,config_json,operator) VALUES(?,?,'ana')",
                      (bot, json.dumps(config)))
        H.event(c, H.KEEPER, "attempt.limited", "a-" + bot, {"bot": bot})


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
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    company(api, enabled, auto)
    limited(api, config=config)
    with api.app.state.store.read() as c:
        keys = {item["key"] for item in health_monitor.findings(c, api.app.state.store.settings)}
    assert ("limited_no_fallback:" + SECRET_BOT in keys) is raised


def test_a_bots_own_fallback_still_counts_with_company_fallback_off(environment):
    api, _ = built(environment)
    company(api, auto=False)
    limited(api, config={"fallback": {"harness": "codex", "model": "gpt-6-luna"}})
    with api.app.state.store.read() as c:
        assert not [i for i in health_monitor.findings(c, api.app.state.store.settings)
                    if i["check_id"] == "limited_no_fallback"]


def turn(api, runner_id, result, minutes_ago, bot="coo", state=None):
    """A finished turn on `runner_id` with the runner's result (backend/execution.py stores it as result_json)."""
    with api.app.state.store.transaction() as c:
        at = H.shift(H.now(), minutes=-minutes_ago)
        mid = "m-" + H.new_id()
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES(?,'human:ana',?,'chat','hi',?)",
                  (mid, "bot:" + bot, at))
        c.execute("INSERT OR IGNORE INTO jobs(id,message_id,bot,state,created) VALUES(?,?,?,'completed',?)",
                  ("j-" + mid, mid, bot, at))
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()["id"]
        aid = "a-" + mid
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,finished,"
                  "result_json) VALUES(?,?,?,?,1,?,?,?,?,?,?)",
                  (aid, job, bot, runner_id, "hash-" + aid, state or result["outcome"], at, at, at, json.dumps(result)))


QUOTA = {"outcome": "failed", "limited": True, "limit_resets_at": None, "profile_used": None}


def login_findings(api):
    with api.app.state.store.read() as c:
        return {i["key"]: i for i in health_monitor.findings(c, api.app.state.store.settings)
                if i["check_id"] == "default_login_limited"}


def test_a_default_login_out_of_quota_is_a_finding_until_a_turn_there_completes(environment):
    api, runner = built(environment)
    rid = runner["runner_id"]
    key = f"default_login_limited:{rid}:codex"
    # The Oct 18 case: hops to Codex on the computer's default login (no profile) and a check model, all refused at
    # once with no reset time. A hop's usage names only the primary that spent tokens.
    turn(api, rid, {**QUOTA, "fallback": "codex", "primary_limited": True,
                    "usage": {"runtime": "claude", "segments": [{"runtime": "claude"}]}}, 50)
    assert key not in login_findings(api)                            # one limit is not yet a pattern
    turn(api, rid, {**QUOTA, "check": "codex"}, 40)
    turn(api, rid, {"outcome": "failed", "fallback": "codex", "profile_used": None}, 35)   # another failure: says nothing
    turn(api, rid, {**QUOTA, "fallback": "codex"}, 30)
    # The same runtime signed in as a subscription profile is not the default login, and works.
    turn(api, rid, {"outcome": "completed", "fallback": "codex", "profile_used": "work"}, 20)
    # Claude on the default login works: only Codex is out.
    turn(api, rid, {"outcome": "completed", "profile_used": None, "usage": {"runtime": "claude"}}, 10)
    found = login_findings(api)
    assert list(found) == [key], found
    item = found[key]
    assert item["kind"] == "setup" and item["title"] == f"{CANARY}'s default Codex login is out of quota", item
    assert "3 turns on it since" in item["detail"] and "no reset time" in item["detail"], item["detail"]
    assert "fall back to it, will fail" in item["detail"]
    # The finding goes through the scan like any other, and goes away once a turn on that login completes.
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    assert key in scan(api)
    turn(api, rid, {"outcome": "completed", "check": "codex", "profile_used": None}, 5)
    assert login_findings(api) == {}
    scan(api)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT present FROM health_findings WHERE key=?", (key,)).fetchone()["present"] == 0


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
            c.execute("INSERT OR REPLACE INTO bot_config(bot,config_json,operator) VALUES(?,?,'ana')",
                      (bot, json.dumps({"harness": harness})))
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


def gateway_choice(api, monkeypatch, runner_id, isolated, enabled=("anthropic",)):
    """The recommended models choice with the Gateway connected, on a computer that does (or does not) run Gateway work."""
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)
    with api.app.state.store.transaction() as c:
        providers.save(c, "human:test", {"expected_revision": providers.load(c)["revision"], "enabled": list(enabled),
                                         "runtime": "claude", "auto_fallback": True, "preset": "subscriptions-gateway"},
                       H.now())
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?",
                  (json.dumps({"schema_version": 1, "isolated": isolated, "runtimes": {}, "bots": {}}), runner_id))


@pytest.mark.parametrize("isolated", [True, False])
def test_a_used_up_login_under_the_gateway_choice_is_one_plain_finding(environment, monkeypatch, isolated):
    api, runner = built(environment)
    rid = runner["runner_id"]
    gateway_choice(api, monkeypatch, rid, isolated)
    limited(api, config={"runtime": "claude"})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t')",
                  (SECRET_BOT, rid, H.now()))
    for ago in (30, 20):
        turn(api, rid, {**QUOTA, "usage": {"runtime": "claude"}}, ago, bot=SECRET_BOT)
    with api.app.state.store.read() as c:
        found = [i for i in health_monitor.findings(c, api.app.state.store.settings)
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
        found = [i for i in health_monitor.findings(c, api.app.state.store.settings)
                 if i["check_id"] == "limited_no_fallback"]
    assert [i["title"] for i in found] == [f"{SECRET_BOT} stopped on a usage limit and has no fallback model"]


def test_the_scan_reads_its_findings_outside_the_write_lock(environment, monkeypatch):
    # The hourly scan read every finding inside the write transaction: 7.2 s on TIDY (12:26 UTC, Oct 29, 2026), and each
    # runner call waited behind it. The reads now run in a snapshot; only the changes take the lock.
    api, runner = built(environment)
    api.post("/api/v2/health-monitor/turn-on", json={}, headers=signed_in())
    store = api.app.state.store
    holders = []
    real = health_monitor.findings

    def watched(c, settings):
        holders.append(len(store.writer.queue))            # the write gate: holder plus waiters, 0 when none holds it
        return real(c, settings)

    monkeypatch.setattr(health_monitor, "findings", watched)
    runner_errors(api, runner, STREAM)
    key = "event_stream:" + runner["runner_id"]
    assert health_monitor.check(store, api.app.state.auth, store.settings) == [key]
    assert holders == [0]
    assert health_monitor.check(store, api.app.state.auth, store.settings) == []
