"""Check health (backend/maintenance_steps/check_health.py) and the Tico bug path (backend/maintenance_health.py).

Each run reads the code checks, the production checks, the Health page's warn and bad checks, and files
what they find as the tags say: an urgent problem at once, a noisy one on its second run. One BotOps task per problem however
many computers have it: a repeat adds to its count, a problem that clears gets a cleared comment that wakes BotOps, and a task
BotOps closed is opened again when the problem returns. A Tico bug goes to the Tico team once (a comment when it comes back worse), or as a support approval a person reads
first while the team rule is off. Check health never writes to a person.
"""

import calendar
import dataclasses
import importlib.util
import json
import sqlite3
import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from backend import (
    bot_settings as BS,
    diagnostics,
    health,
    hosted_updates,
    invariants,
    maintenance as M,
    maintenance_health as MH,
    maintenance_limits as L,
    providers,
    replication,
    routines,
    support,
    task_asks,
    task_engine as TE,
)
from backend.enqueue import enqueue_stored
from backend.maintenance_steps import check_health
from backend.store import H, Problem
from backend.tests.helpers.api import get  # noqa: F401  (fixture)
from backend.tests.helpers.goal_manager import BASE
from backend.tests.helpers.onboarding import draft, environment, machine, signed_in  # noqa: F401
from backend.tests.helpers.support import FakeHQ
from backend.tests.helpers.maintenance_health import turn

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0096_retire_health_monitor.py"
HM_SLUG = "health-monitor"  # the retired Health Monitor's slug: migration 0096 archives its row
CANARY = "Acme Payroll Mac"  # a computer's label: local, never in a report
SECRET_BOT = "acme-payroll-bot"


def _readers(monkeypatch):
    monkeypatch.setattr(health, "view", lambda c, *a, **k: {"checks": []})
    monkeypatch.setattr(invariants, "CHECKS", [])
    monkeypatch.setattr(hosted_updates, "notice", lambda *a, **k: {})
    monkeypatch.setattr(replication, "status", lambda *a, **k: {})


def _code_findings(monkeypatch):
    _readers(monkeypatch)
    items = []
    monkeypatch.setattr(MH, "findings", lambda c, settings: list(items))
    return items


@pytest.fixture
def readers(monkeypatch):
    """Only the findings a test sets: the Health page and the production checks read nothing here."""
    _readers(monkeypatch)


@pytest.fixture
def code_findings(monkeypatch, readers):
    """The code checks' findings for the next runs: the test replaces the list's contents."""
    return _code_findings(monkeypatch)


def built(environment):
    """A signed-in install with one computer. BotOps is active: the test environment seeds it."""
    api = environment(seed={}, cards=BASE)
    runner = machine(api, label=CANARY)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api, runner


def item(check_id, key=None, title=None, kind=MH.SETUP, severity=MH.WARN, subject=""):
    """A code check's finding as MH._finding makes it, keyed by `key` (default: its check id)."""
    return MH._finding(
        key or check_id,
        check_id,
        kind,
        title or f"{check_id} finding",
        f"{check_id} detail.",
        "BotOps: fix it.",
        subject=subject,
        severity=severity,
    )


def run(api, when=None, settings=None):
    """One lifecycle pass inside a claimed Maintenance run. `when` overrides the readers' and lifecycle's time."""
    store, auth = api.app.state.store, api.app.state.auth
    ctx = check_health.context(auth, settings or store.settings, store)
    if when is not None:
        ctx["now"] = when
    with store.transaction() as c:
        ctx["run_id"] = M._insert(c, "run_now:" + H.new_id(), "run_now", "check_health", H.now())
    try:
        with store.read_transaction() as r:
            checked, found, notes = check_health.gather(r, ctx)
        with store.transaction() as c:
            return check_health.apply(c, ctx, checked, found, notes)
    finally:
        with store.transaction() as c:
            c.execute("UPDATE maintenance_runs SET finished_at=? WHERE id=?", (H.now(), ctx["run_id"]))


def file_due(api, result):
    """The Tico bug reports a run asked for, filed outside the lifecycle's transaction."""
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
        return [
            dict(r)
            for r in c.execute(
                "SELECT id,title,body,owner_id,status FROM tasks WHERE title LIKE 'Health:%' ORDER BY created_at"
            )
        ]


def said(api, task_id, start):
    """The messages on a task that begin with `start` (Check health's comments)."""
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT m.body FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
            "WHERE t.id=? AND m.body LIKE ? ORDER BY m.created_at",
            (task_id, start + "%"),
        ).fetchall()
    return [r["body"] for r in rows]


def registry(api, check_id):
    """Check health's record for a finding's signature: its task, its count, when it last commented."""
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT value FROM registry_metadata WHERE key=?", ("health-task:" + MH.signature(check_id),)
        ).fetchone()
    return json.loads(row["value"]) if row else {}


def open_approvals(api, kind):
    """The OKs asked for reports of `kind` (support: subject support:<key>) still waiting for their person."""
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT requirement_id FROM ok_grants WHERE subject LIKE ?", (kind + ":%",)).fetchall()
        return [{"id": r[0]} for r in rows if task_asks.standing(c, r[0]) == task_asks.WAITING]


def answer_report(api, rid, ok=True):
    """The owner answers a report's OK through the real routes: OK from its preview, or a send back."""
    from backend import task_asks

    with api.app.state.store.read() as c:
        tid = task_asks.task_of(c, rid)
    if not ok:
        r = api.post(f"/api/v2/tasks/{tid}/asks/{rid}/answer", json={"value": "no", "note": "No."}, headers=signed_in())
        assert r.status_code == 200, r.text
        return
    shown = api.get(f"/api/v2/oks/{rid}", headers=signed_in())
    assert shown.status_code == 200, shown.text
    r = api.post(
        f"/api/v2/tasks/{tid}/asks/{rid}/ok", json={"shown_hash": shown.json()["shown_hash"]}, headers=signed_in()
    )
    assert r.status_code == 200, r.text


def set_rule(api, reports):
    """The team rule: whether a Tico bug goes to the Tico team without a person's approval first."""
    api.put("/api/v2/access/rules", json={"health_monitor_reports": reports}, headers=signed_in())


def set_botops(api, state):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state=? WHERE id='botops'", (state,))


def runner_errors(api, runner, lines):
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET readiness=? WHERE runner_id=?",
            (json.dumps({"schema_version": 1, "bots": {}, "recent_errors": lines}), runner["runner_id"]),
        )


STREAM = [
    "2026-10-14T10:0%dZ Tico runner: event stream lost (ReadTimeout); polling until it is back" % i for i in range(3)
]

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
        providers.save(
            c,
            "human:test",
            {
                "expected_revision": providers.load(c)["revision"],
                "enabled": list(enabled),
                "runtime": providers.PROVIDER_BY_ID[enabled[0]]["runtime"],
                "auto_fallback": auto,
            },
            H.now(),
        )


def limited(api, bot=SECRET_BOT, config=None):
    """A bot whose turn stopped on a usage limit within the day, with its own config when given."""
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {bot: {"name": bot, "status": "active"}}, None)
        if config is not None:
            c.execute("INSERT OR REPLACE INTO bot_config(bot,owner_id) VALUES(?,'ana')", (bot,))
            BS.write(c, bot, config)
        H.event(c, H.KEEPER, "attempt.limited", "a-" + bot, {"bot": bot})


def login_findings(api, rid=""):
    """The default-login findings, those of one computer when `rid` is given."""
    with api.app.state.store.read() as c:
        return {
            i["key"]: i
            for i in MH.findings(c, api.app.state.store.settings)
            if i["check_id"] == "default_login_limited" and rid in i["key"]
        }


def gateway_choice(api, monkeypatch, runner_id, isolated, enabled=("anthropic",)):
    """The recommended models choice with the Gateway connected, on a computer that does (or does not) run Gateway work."""
    from backend import gateway_connection

    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)
    with api.app.state.store.transaction() as c:
        providers.save(
            c,
            "human:test",
            {
                "expected_revision": providers.load(c)["revision"],
                "enabled": list(enabled),
                "runtime": "claude",
                "auto_fallback": True,
                "preset": "subscriptions-gateway",
            },
            H.now(),
        )
        c.execute(
            "UPDATE runner_states SET readiness=? WHERE runner_id=?",
            (json.dumps({"schema_version": 1, "isolated": isolated, "runtimes": {}, "bots": {}}), runner_id),
        )


def test_a_setup_finding_naming_a_bot_is_never_reported_to_the_tico_team(environment, readers, hq):
    api, runner = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        H.event(c, H.KEEPER, "attempt.limited", SECRET_BOT, {"bot": SECRET_BOT})
    runner_errors(api, runner, ["2026-10-14T10:00Z Tico runner: research has unpushed commits and push failed"])
    with store.transaction() as c:  # a computer silent 15 minutes with a request queued for its bot
        H.sync_registry(c, {SECRET_BOT: {"name": SECRET_BOT, "status": "active"}}, None)
        c.execute(
            "INSERT OR REPLACE INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'t')",
            (SECRET_BOT, runner["runner_id"], H.now()),
        )
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m-silent','human:ana',?,'chat','hi',?)",
            ("bot:" + SECRET_BOT, H.now()),
        )
        enqueue_stored(c, 'm-silent')
        c.execute(
            "UPDATE runner_states SET last_seen_at=? WHERE runner_id=?",
            (H.shift(H.now(), minutes=-15), runner["runner_id"]),
        )
    found = keys(api)
    silent = "computer_silent:" + runner["runner_id"]
    assert (
        "limited_no_fallback:" + SECRET_BOT in found
        and "push_stuck:" + runner["runner_id"] in found
        and silent in found
    )
    run(api)  # the step writes the rows the report route reads
    for rule in (True, False):  # neither filed nor put to a person to send
        set_rule(api, rule)
        for key in ("limited_no_fallback:" + SECRET_BOT, "push_stuck:" + runner["runner_id"], silent):
            r = api.post(f"/api/v2/health/findings/{key}/report", json={}, headers=signed_in())
            assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_tico_bug", r.text
    assert hq.seen == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM health_reports").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM ok_grants WHERE subject LIKE 'support:%'").fetchone()[0] == 0


def test_the_idle_load_is_taken_from_a_computer_only(environment):
    api, _ = built(environment)
    r = api.post("/api/v2/runners/idle-load", json={"sampled_at": H.now(), "busy": []}, headers=signed_in())
    assert r.status_code == 403


def fake_check(check_id, hits, label="Runs past their lease"):
    """A production check that reads `hits`: the tests are about what Check health does with a hit, not the read."""
    return SimpleNamespace(
        id=check_id,
        label=label,
        says="Runs whose lease ended over 10 minutes ago.",
        look="The runner that holds each run.",
        owner="botops",
        read=lambda c, ctx: list(hits),
    )


def task_count(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM tasks").fetchone()[0]


def filed_keys(api):
    with api.app.state.store.read() as c:
        return [row["key"] for row in c.execute("SELECT key FROM health_findings WHERE present=1 ORDER BY key")]


def run_step(api, when):
    """One Check health run as the Maintenance engine calls it: run(c, ctx) on a pooled read connection."""
    store, auth = api.app.state.store, api.app.state.auth
    ctx = check_health.context(auth, store.settings, store)
    ctx["now"] = when
    with store.read() as r:
        return check_health.run(r, ctx)


def retire_migration():
    spec = importlib.util.spec_from_file_location("retire_health_monitor", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("saved,seeded,expected", [(False, 1, 0), (True, 0, 1), (None, 0, 0)])
def test_the_cutover_refreshes_the_saved_health_switch_and_leaves_an_inherited_one(api, saved, seeded, expected):
    store, migration = api.app.state.store, retire_migration()
    with store.transaction() as c:
        # 0090's snapshot predates the person's latest choice in System routines.
        c.execute(
            "UPDATE maintenance_steps SET enabled=?, cursor_at='t0', last_ran_at='t0' WHERE key='check_health'",
            (seeded,),
        )
        controls = {"task_roundup": {"on": False}}
        if saved is not None:
            controls["health_scan"] = {"on": saved}
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES('system_routines',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(controls),),
        )
        before = M.steps_of(c)
        migration.up(c)
        after = M.steps_of(c)
    assert after["check_health"]["enabled"] == expected
    assert after["check_health"]["cursor_at"] == after["check_health"]["last_ran_at"] == "t0"
    assert {k: v for k, v in after.items() if k != "check_health"} == {
        k: v for k, v in before.items() if k != "check_health"
    }
    if saved is None:
        assert after == before, "no saved switch: keep the step's setting"
    with store.transaction() as c:
        migration.up(c)
        assert M.steps_of(c) == after, "a replay does not re-stamp the setting"


def test_the_cutover_retires_the_health_scan_mirror_and_its_inventory_entry(api):
    from backend import automations, redesign_migration as RM, system_routines as SR

    store, migration = api.app.state.store, retire_migration()
    with store.transaction() as c:
        flow = RM._mirror(
            "Health scan",
            "person:ana",
            "job.health_scan",
            {"on": True},
            {"kind": "system_job", "id": "job:health_scan"},
        )
        aid = RM._save(c, RM.system_who(c), flow)
        RM._mark(c, "system_job", "job:health_scan", "mig_old", aid, title="Health scan")
        custom = automations._save(
            c,
            "human:ana",
            "auto_custom_health",
            {**flow, "name": "My health automation", "builtin": False, "readonly": False},
            None,
        )["id"]
        rid = SR.save(c, SR.Run("health_scan").did("All well", quiet=True))
        history = dict(c.execute("SELECT * FROM system_runs WHERE id=?", (rid,)).fetchone())
    assert aid in {a["id"] for a in get(api, "automations")["automations"]}
    assert "sys_" + rid in {r["id"] for r in get(api, "automations/runs?trigger=system")["runs"]}
    with store.transaction() as c:
        migration.up(c)
        retired = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
        assert retired["deleted_at"] and retired["enabled"] == 0
        migration.up(c)
        assert dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()) == retired
    listed = {a["id"] for a in get(api, "automations")["automations"]}
    assert aid not in listed and custom in listed, "only the retired built-in mirror leaves Automations"
    assert "sys_" + rid not in {r["id"] for r in get(api, "automations/runs?trigger=system")["runs"]}
    get(api, "automations/" + aid, expected=404)
    with store.read() as c:
        assert dict(c.execute("SELECT * FROM system_runs WHERE id=?", (rid,)).fetchone()) == history
        (entry,) = [i for i in RM.inventory(c) if i["id"] == "job:health_scan"]
    assert entry["now"] == "removed" and entry["automation"] is None and entry["state"] == ""


LEGACY_SCHEMA = """
CREATE TABLE bots(id TEXT PRIMARY KEY, slug TEXT, state TEXT);
CREATE TABLE bot_config(bot TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1, definition_updated_at TEXT,
                        definition_updated_by TEXT);
CREATE TABLE bot_settings(bot_id TEXT PRIMARY KEY, extra TEXT, updated_at TEXT);
CREATE TABLE bot_status(bot TEXT PRIMARY KEY, open_tasks INTEGER NOT NULL DEFAULT 0, needs_human INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT);
CREATE TABLE assignments(bot TEXT, runner_id TEXT, generation INTEGER, updated_at TEXT, updated_by TEXT);
CREATE TABLE jobs(id TEXT PRIMARY KEY, bot_id TEXT, state TEXT);
CREATE TABLE schedules(id TEXT PRIMARY KEY, bot TEXT, deleted_at TEXT, updated_at TEXT);
CREATE TABLE schedule_config(schedule_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT, owner_id TEXT, status TEXT, closed_at TEXT, closed_by_id TEXT,
                   updated_at TEXT, note TEXT, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE history_events(id TEXT PRIMARY KEY, subject_type TEXT, subject_id TEXT, actor_id TEXT, kind TEXT, old TEXT,
                            new TEXT, note TEXT, action_id TEXT, via TEXT, data TEXT, created_at TEXT);
CREATE TABLE health_findings(key TEXT PRIMARY KEY, signature TEXT, present INTEGER NOT NULL DEFAULT 1,
                             occurrences INTEGER NOT NULL DEFAULT 1);
CREATE TABLE watcher_items(bot TEXT NOT NULL, key TEXT NOT NULL, task_id TEXT NOT NULL, created_at TEXT NOT NULL,
                           PRIMARY KEY(bot, key));
CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE invariant_state(check_id TEXT PRIMARY KEY, firing INTEGER NOT NULL DEFAULT 0, count INTEGER NOT NULL DEFAULT 0,
                             examples TEXT NOT NULL DEFAULT '[]', checked_at TEXT, started_firing_at TEXT, task_id TEXT);
"""
PUSH, RUNNER = MH.signature("push_stuck"), MH.signature("runner_errors")
REG = "health-task:"


def legacy_db(botops="active", health_monitor="active"):
    """The tables the migration reads and writes, as they were. The Health Monitor is in `health_monitor`'s state (None: no
    such bot), with its findings, its watcher links and its open tasks; the production checks have alerts open."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEGACY_SCHEMA)
    bots = [("assistant", "active"), ("botops", botops)] + ([(HM_SLUG, health_monitor)] if health_monitor else [])
    conn.executemany("INSERT INTO bots(id,slug,state) VALUES(?,?,?)", [(b, b, s) for b, s in bots])
    conn.execute(
        "INSERT INTO bot_config(bot,revision,definition_updated_at,definition_updated_by) VALUES(?,3,'t0','ana')",
        (HM_SLUG,),
    )
    conn.execute(
        "INSERT INTO bot_settings(bot_id,extra,updated_at) VALUES(?,?,'t0')", (HM_SLUG, json.dumps({"model": "m1"}))
    )
    conn.executemany(
        "INSERT INTO bot_status(bot,open_tasks,needs_human,updated_at) VALUES(?,?,0,'t0')",
        [(HM_SLUG, 5), ("botops", 1)],
    )
    conn.execute("INSERT INTO assignments VALUES(?,'R1',1,'t','t')", (HM_SLUG,))
    conn.executemany("INSERT INTO jobs VALUES(?,?,?)", [("j1", HM_SLUG, "queued"), ("j2", HM_SLUG, "completed")])
    conn.executemany(
        "INSERT INTO schedules(id,bot,deleted_at,updated_at) VALUES(?,?,?,'t0')",
        [("s1", HM_SLUG, None), ("s2", HM_SLUG, "t0")],
    )
    conn.executemany(
        "INSERT INTO health_findings(key,signature,present,occurrences) VALUES(?,?,?,?)",
        [("push_stuck:A", PUSH, 1, 3), ("push_stuck:B", PUSH, 1, 2), ("runner_errors:R1", RUNNER, 0, 1)],
    )
    conn.executemany(
        "INSERT INTO watcher_items(bot,key,task_id,created_at) VALUES(?,?,?,'t0')",
        [
            (HM_SLUG, "hm:push_stuck:A", "t1"),
            (HM_SLUG, "hm:runner_errors:R1", "t2"),
            (HM_SLUG, "hm:push_stuck:B", "t5"),
        ],
    )
    conn.executemany(
        "INSERT INTO tasks(id,title,owner_id,status,updated_at) VALUES(?,?,?,?,'t0')",
        [
            ("t1", "Pushes are stuck", "bot:health-monitor", "open"),  # its finding is push_stuck:A: moves
            ("t2", "Runner errors", "health-monitor", "review"),  # its finding cleared: moves, registered empty
            ("t5", "Pushes are stuck (B)", "bot:health-monitor", "open"),  # the same signature as t1: superseded
            ("t6", "Watch a thing", "bot:health-monitor", "doing"),  # no finding: superseded
            ("t3", "An old finding", "bot:health-monitor", "done"),  # not open: left alone
            ("t4", "Ana's own task", "human:ana", "open"),  # not the Health Monitor's: left alone
            ("t7", "Production check: Runs past their lease", "human:ana", "open"),
            ("t8", "Production check: Tasks in progress with no run", "bot:assistant", "open"),
            ("t9", "Production check: Write lock held too long", "bot:botops", "closed"),
        ],
    )
    conn.executemany(
        "INSERT INTO invariant_state(check_id,firing,count,examples,task_id) VALUES(?,?,?,'[]',?)",
        [("lease_overdue", 1, 2, "t7"), ("task_stuck", 0, 0, "t8"), ("long_holds", 1, 1, "t9")],
    )
    return conn


def statuses(conn):
    return dict(conn.execute("SELECT id,status FROM tasks"))


def owners(conn):
    return dict(conn.execute("SELECT id,owner_id FROM tasks"))


def test_the_archive_archives_the_bot_the_way_archive_bot_does():
    conn = legacy_db()
    retire_migration().up(conn)
    assert conn.execute("SELECT state FROM bots WHERE id=?", (HM_SLUG,)).fetchone()[0] == "archived"
    assert conn.execute("SELECT count(*) FROM assignments WHERE bot=?", (HM_SLUG,)).fetchone()[0] == 0
    assert dict(conn.execute("SELECT id,state FROM jobs")) == {"j1": "canceled", "j2": "completed"}
    assert conn.execute("SELECT revision,definition_updated_by FROM bot_config WHERE bot=?", (HM_SLUG,)).fetchone() == (
        4,
        "keeper",
    )
    assert json.loads(conn.execute("SELECT extra FROM bot_settings WHERE bot_id=?", (HM_SLUG,)).fetchone()[0]) == {
        "model": "m1",
        "status": "archived",
    }
    assert dict(conn.execute("SELECT schedule_id,enabled FROM schedule_config")) == {"s1": 0}
    assert dict(conn.execute("SELECT id,deleted_at IS NOT NULL FROM schedules")) == {"s1": 1, "s2": 1}
    assert conn.execute("SELECT deleted_at FROM schedules WHERE id='s2'").fetchone()[0] == "t0", "kept, not re-stamped"
    assert dict(conn.execute("SELECT bot,open_tasks FROM bot_status")) == {HM_SLUG: 0, "botops": 4}


def test_a_linked_open_task_moves_to_botops_and_the_rest_of_the_health_monitors_close():
    conn = legacy_db()
    retire_migration().up(conn)
    assert {k: v for k, v in owners(conn).items() if k in ("t1", "t2", "t4", "t7", "t8")} == {
        "t1": "bot:botops",
        "t2": "bot:botops",
        "t4": "human:ana",
        "t7": "bot:botops",
        "t8": "bot:botops",
    }
    assert statuses(conn) == {
        "t1": "open",
        "t2": "review",
        "t3": "done",
        "t4": "open",
        "t5": "closed",
        "t6": "closed",
        "t7": "open",
        "t8": "open",
        "t9": "closed",
    }
    assert conn.execute("SELECT owner_id FROM tasks WHERE id='t3'").fetchone()[0] == "bot:health-monitor"


def test_each_moved_task_is_registered_for_its_signature_so_the_step_adopts_it():
    conn = legacy_db()
    retire_migration().up(conn)
    entries = {
        row[0][len(REG) :]: json.loads(row[1]) for row in conn.execute("SELECT key,value FROM registry_metadata")
    }
    for entry in entries.values():
        assert entry.pop("last_comment"), "the migration's day is the last comment: the next one comes a day on"
    assert entries == {
        PUSH: {"task": "t1", "keys": 2, "count": 5, "cleared": False, "approvals": {}},
        RUNNER: {"task": "t2", "keys": 0, "count": 0, "cleared": False, "approvals": {}},
        MH.signature("inv:lease_overdue"): {"task": "t7", "keys": 1, "count": 2, "cleared": False, "approvals": {}},
        MH.signature("inv:task_stuck"): {"task": "t8", "keys": 1, "count": 0, "cleared": False, "approvals": {}},
    }


def test_the_invariant_alerts_lose_their_task_link_and_a_closed_one_is_left_alone():
    conn = legacy_db()
    retire_migration().up(conn)
    assert dict(conn.execute("SELECT check_id,task_id FROM invariant_state")) == {
        "lease_overdue": None,
        "task_stuck": None,
        "long_holds": None,
    }
    assert statuses(conn)["t9"] == "closed" and owners(conn)["t9"] == "bot:botops"


def test_every_change_is_a_history_event_as_the_keeper():
    migration = retire_migration()
    conn = legacy_db()
    migration.up(conn)
    assert conn.execute(
        "SELECT subject_type,subject_id,kind,old,new,actor_id,note FROM history_events WHERE subject_id='t1'"
    ).fetchall() == [("task", "t1", "owner", "bot:health-monitor", "bot:botops", "keeper", migration.NOTE)]
    (closed,) = conn.execute("SELECT kind,new,note FROM history_events WHERE subject_id='t6'").fetchall()
    assert closed[0:2] == ("status", "closed") and closed[2].startswith("Superseded: its bot is retired")
    assert conn.execute("SELECT count(*) FROM history_events").fetchone()[0] == 6, "t1, t2, t7, t8 move; t5, t6 close"


def test_with_no_active_botops_every_open_task_closes_as_superseded():
    conn = legacy_db(botops="paused")
    retire_migration().up(conn)
    assert statuses(conn) == {
        "t1": "closed",
        "t2": "closed",
        "t3": "done",
        "t4": "open",
        "t5": "closed",
        "t6": "closed",
        "t7": "closed",
        "t8": "closed",
        "t9": "closed",
    }
    assert conn.execute("SELECT count(*) FROM registry_metadata").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM invariant_state WHERE task_id IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT note FROM tasks WHERE id='t7'").fetchone()[0].startswith("Superseded: ")


def test_running_the_migration_again_changes_nothing():
    conn, migration = legacy_db(), retire_migration()
    migration.up(conn)
    queries = (
        "SELECT id,owner_id,status FROM tasks ORDER BY id",
        "SELECT count(*) FROM history_events",
        "SELECT key,value FROM registry_metadata ORDER BY key",
        "SELECT check_id,task_id FROM invariant_state ORDER BY check_id",
        "SELECT id,state FROM bots ORDER BY id",
        "SELECT bot,open_tasks FROM bot_status ORDER BY bot",
    )
    before = [conn.execute(q).fetchall() for q in queries]
    migration.up(conn)
    assert [conn.execute(q).fetchall() for q in queries] == before


@pytest.mark.parametrize("health_monitor", [None, "archived"])
def test_without_a_health_monitor_to_retire_its_tasks_stay_and_the_invariant_alerts_still_move(health_monitor):
    conn = legacy_db(health_monitor=health_monitor)
    retire_migration().up(conn)
    owned = owners(conn)
    assert owned["t1"] == "bot:health-monitor" and owned["t2"] == "health-monitor"
    assert owned["t7"] == "bot:botops" and owned["t8"] == "bot:botops"
    assert (
        conn.execute("SELECT count(*) FROM history_events WHERE subject_id IN ('t1','t2','t5','t6')").fetchone()[0] == 0
    )


def test_reported_at_is_added_to_an_existing_health_reports_table_and_kept_on_a_rerun():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE health_reports(signature TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "reported_occurrences INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute("INSERT INTO health_reports(signature,created_at) VALUES('hm-old','t0')")
    migration = retire_migration()
    migration.up(conn)
    migration.up(conn)
    assert "reported_at" in {row[1] for row in conn.execute('PRAGMA table_info("health_reports")')}
    assert conn.execute("SELECT reported_at FROM health_reports").fetchall() == [("",)]


def test_the_pending_column_is_added_once_and_a_file_with_no_table_is_skipped():
    conn = legacy_db()
    migration = retire_migration()
    migration.up(conn)
    migration.up(conn)  # a rerun finds the column and changes nothing
    assert "pending" in {row[1] for row in conn.execute('PRAGMA table_info("health_findings")')}
    rows = conn.execute("SELECT pending FROM health_findings").fetchall()
    assert rows and {row[0] for row in rows} == {0}  # the findings that were there are filed, so 0
    empty = sqlite3.connect(":memory:")
    migration.up(empty)  # no health_findings yet: the module's CREATE makes it
    assert empty.execute("SELECT count(*) FROM sqlite_master WHERE name='health_findings'").fetchone()[0] == 0


def test_a_paused_bots_routines_are_not_a_finding(environment):
    api, _ = built(environment)  # pausing holds routines by design (#500)
    with api.app.state.store.transaction() as c:  # a routine as a template seeds it: its automation, on
        made = routines.create(
            c,
            H.KEEPER,
            "assistant",
            {"cron": "30 16 * * 5", "title": "Weekly backlog", "text": "Review the backlog."},
            key="weekly",
        )
        assert made["automation"] and made["enabled"]
        c.execute("UPDATE bots SET state='paused' WHERE slug='assistant'")
    assert keys(api) == []


def test_private_tasks_no_one_can_move_stay_on_owner_health_and_file_nothing(readers, monkeypatch):
    """Health's `no_one` count (private tasks nobody who reads them can move) is for the company's owner and Admins on
    Health only: it never becomes a finding, so no BotOps task carries it and no bot is started for it. A rule break
    still files, its words without that count."""

    def tasks(status, **counts):
        summary = "0 waiting on nothing. 2 private tasks have no one to move them."
        if counts.get("broken"):
            summary += " 1 rule break."
        return {"id": "never_stuck", "label": "Tasks", "status": status, "summary": summary, "counts": counts}

    page = {"checks": [tasks("warn", no_one=2, broken=0)]}
    monkeypatch.setattr(health, "view", lambda c, *a, **k: page)
    ctx = {"auth": SimpleNamespace(owner_identity=lambda c: None), "settings": None}
    read, found = check_health._page_findings(None, ctx, {})
    assert read == 1 and found == []
    page["checks"] = [tasks("bad", no_one=2, broken=1)]
    _, found = check_health._page_findings(None, ctx, {})
    assert len(found) == 1 and "private" not in json.dumps(found, default=str)


def test_the_monitor_files_for_botops_and_a_task_for_a_person_is_refused(api):
    # The Health Monitor gives its fixes to BotOps only (docs/health-monitor.md): a task for a person, and a hand-off to
    # one, are refused with where to file it and nothing is written; the same task for BotOps is accepted.
    with api.app.state.store.transaction() as c:
        for slug in ("health-monitor", "botops"):
            c.execute(
                "INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (slug, slug, slug)
            )
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


def test_event_stream_counts_drops_and_not_the_ones_a_server_restart_caused(environment, readers, monkeypatch):
    api, runner = built(environment)
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T23:00:00Z")  # this process: far from the lines
    with api.app.state.store.transaction() as c:  # an earlier start: the update
        c.execute(
            "INSERT INTO flight_events(created_at,kind,detail) VALUES(?,?,'{}')",
            (calendar.timegm(time.strptime("2026-01-10T21:06:44Z", "%Y-%m-%dT%H:%M:%SZ")), "start"),
        )
    lost = "%s Tico runner: event stream lost (TimeoutError: error decoding response body); polling until it is back"
    gone = "%s Tico runner: event stream unavailable (HTTP 502); polling"
    two_drops_and_a_restart = [
        lost % "2026-01-10T19:42:10Z",
        lost % "2026-01-10T20:42:47Z",
        gone % "2026-01-10T20:42:48Z",
        lost % "2026-01-10T21:06:39Z",
        "2026-01-10T21:06:41Z Tico runner: hub unavailable (HTTP 502)",
    ]
    runner_errors(api, runner, two_drops_and_a_restart)
    assert keys(api) == []  # the 21:06 drop is the restart; "unavailable" is the same drop
    runner_errors(api, runner, two_drops_and_a_restart + [lost % "2026-13-45T25:00:00Z"])
    key = "event_stream:" + runner["runner_id"]
    assert keys(api) == [key]  # a line with an impossible date counts and stops nothing
    run(api)
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T19:43:00Z")  # this process began at 19:43
    runner_errors(api, runner, two_drops_and_a_restart + [lost % "2026-01-10T22:30:00Z"])
    run(api)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT present FROM health_findings WHERE key=?", (key,)).fetchone()[0] == 0


def test_event_stream_does_not_count_drops_the_runner_says_were_back_within_seconds(environment, readers, monkeypatch):
    api, runner = built(environment)
    monkeypatch.setattr(diagnostics.RING, "started", "2026-01-10T05:48:11Z")  # far from every line below
    lost = (
        "%s Tico runner: event stream lost (ConnectionError: error decoding response body: stream error received: "
        "unexpected internal error encountered); polling until it is back"
    )
    back = "%s Tico runner: event stream back after %d s; polling stops"
    resets = []  # a tunnel's edge resetting the stream: each back in 1-3 s
    for at, took in (("06:43:46", 2), ("07:12:38", 1), ("07:14:27", 3), ("07:24:26", 1), ("07:27:22", 2)):
        resets += [lost % f"2026-01-10T{at}Z", back % (f"2026-01-10T{at[:-2]}59Z", took)]
    runner_errors(api, runner, resets)
    assert keys(api) == []
    key = "event_stream:" + runner["runner_id"]
    stayed_down = [
        lost % "2026-01-10T08:00:00Z",
        back % ("2026-01-10T08:02:00Z", 120),
        lost % "2026-01-10T08:10:00Z",
        "2026-01-10T08:10:01Z Tico runner: event stream unavailable (x); polling",
        back % ("2026-01-10T08:11:00Z", 60),
        lost % "2026-01-10T08:20:00Z",
    ]  # the last: not back yet
    runner_errors(api, runner, resets + stayed_down)
    assert keys(api) == [key]  # three drops that lasted count; the brief ones still do not
    run(api)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT detail FROM health_findings WHERE key=?", (key,)).fetchone()[0].startswith("3 of")
    # A "back" line answers only the drop just before it: two drops in a row and one quick return is one brief drop.
    runner_errors(
        api,
        runner,
        [
            lost % "2026-01-10T09:00:00Z",
            lost % "2026-01-10T09:00:30Z",
            back % ("2026-01-10T09:00:32Z", 2),
            lost % "2026-01-10T09:30:00Z",
        ],
    )
    with api.app.state.store.read() as c:
        assert not [
            f for f in MH._runner_checks(c, api.app.state.store.settings) if f["check_id"] == "event_stream"
        ]  # two drops that lasted: under the threshold
    # A "back" with no "lost" kept before it (a clean end is not an error line) answers nothing; 30 s is brief, 31 is not.
    runner_errors(
        api,
        runner,
        [
            lost % "2026-01-10T10:00:00Z",
            back % ("2026-01-10T10:02:00Z", 120),
            back % ("2026-01-10T10:20:01Z", 1),
            lost % "2026-01-10T10:30:00Z",
            back % ("2026-01-10T10:30:31Z", 31),
            lost % "2026-01-10T10:40:00Z",
            back % ("2026-01-10T10:40:30Z", 30),
            lost % "2026-01-10T10:50:00Z",
        ],
    )
    with api.app.state.store.read() as c:
        (found,) = [f for f in MH._runner_checks(c, api.app.state.store.settings) if f["check_id"] == "event_stream"]
    assert found["detail"].startswith("3 of")


# ------------------------------------------------------------------------------------- the review fixes (one commit)
@pytest.fixture
def clock(monkeypatch):
    """The store's clock for one test: H.now() reads `clock.at`, so a report's time and a run's time are the same clock."""
    box = SimpleNamespace(at=datetime.now(timezone.utc))
    monkeypatch.setattr(H, "now", lambda: box.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z")
    return box


@pytest.fixture
def comments(monkeypatch):
    """Every comment the step writes, as (task id, wake, text)."""
    sent = []
    real = H.task_comment

    def spy(conn, actor, task_id, text, wake=True, **kwargs):
        sent.append((task_id, wake, text))
        return real(conn, actor, task_id, text, wake=wake, **kwargs)

    monkeypatch.setattr(H, "task_comment", spy)
    return sent


def three_new_problems(code_findings):
    code_findings[:] = [
        item("push_stuck", key="push_stuck:A", title="Pushes are stuck"),
        item("worktree_error", key="worktree_error:A", title="Worktrees fail"),
        item("event_stream", key="event_stream:A", title="The event stream drops"),
    ]


def wake_marker(api):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT value FROM registry_metadata WHERE key=?", ("health-wake:pending",)).fetchone()
    return json.loads(row["value"]) if row else None


def wake_jobs(api):
    with api.app.state.store.read() as c:
        return [
            dict(row)
            for row in c.execute(
                "SELECT j.id, json_extract(m.refs,'$.task') task FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE j.bot_id='botops' AND m.body LIKE 'Health wake: %'"
            )
        ]


def test_a_busy_process_keeps_its_computers_sample_time_and_clears_when_that_time_is_too_old(
    environment, readers, code_findings
):
    api, runner = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        MH.record_idle(
            c,
            runner["runner_id"],
            MH.IdleLoad(sampled_at=H.now(), busy=[MH.IdleEntry(name="process:chrome", cpu=80.0, mb=900.0)]),
        )
    key = f"local_busy:{runner['runner_id']}:process:chrome"
    sampled = H.shift(H.now(), hours=-1)
    with store.transaction() as c:
        c.execute("UPDATE health_findings SET first_seen_at=?, last_seen_at=? WHERE key=?", (sampled, sampled, key))
    run(api)
    assert finding(api, key)["last_seen_at"] == sampled and finding(api, key)["present"] == 1
    run(api, datetime.now(timezone.utc) - timedelta(hours=1) + timedelta(hours=3))  # the sample is three hours old
    assert finding(api, key)["present"] == 0


# ----------------------------------------------------------------------------------------------------
# Folded stories: one install walks each lifecycle; `_fresh` wipes the findings, tasks and wakes between sections.


def _part_urgent(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    key = "event_stream:" + runner["runner_id"]
    code_findings[:] = [item("event_stream", key=key, title="The event stream drops")]
    result = run(api)
    assert result["details"]["filed"] == [key] and result["woke"] == 1
    (task,) = botops_tasks(api)
    assert task["title"] == "Health: The event stream drops" and task["owner_id"] == "bot:botops"
    assert [job["task"] for job in wake_jobs(api)] == [task["id"]]
    assert finding(api, key)["pending"] == 0


def _part_noisy(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("runner_outdated", key="runner_outdated:R1", title="Computer R1 is outdated")]
    first = run(api)
    assert first["details"]["pending"] == ["runner_outdated:R1"] and botops_tasks(api) == []
    second = run(api)
    assert second["details"]["filed"] == ["runner_outdated:R1"] and len(botops_tasks(api)) == 1
    assert finding(api, "runner_outdated:R1")["pending"] == 0


def _part_noisy_passes(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("runner_outdated", key="runner_outdated:R1")]
    run(api)
    code_findings[:] = []
    result = run(api)
    assert result["details"]["cleared"] == [] and botops_tasks(api) == []
    assert finding(api, "runner_outdated:R1")["present"] == 0


def _part_repeat(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    now = datetime.now(timezone.utc)
    code_findings[:] = [item("event_stream", key="event_stream:R1", title="The event stream drops")]
    run(api, now)
    (task,) = botops_tasks(api)
    run(api, now + timedelta(minutes=1))
    assert finding(api, "event_stream:R1")["occurrences"] == 2 and registry(api, "event_stream")["count"] == 2
    assert said(api, task["id"], "Still there") == []  # a repeat within the day adds to the count only
    run(api, now + timedelta(hours=25))
    (comment,) = said(api, task["id"], "Still there")  # a day on: one comment, and no wake
    assert "seen 3 times" in comment and registry(api, "event_stream")["count"] == 3
    assert len(botops_tasks(api)) == 1


def _part_two_computers(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("event_stream", key="event_stream:A", title="The event stream drops")]
    run(api)
    code_findings[:] = [
        item("event_stream", key="event_stream:A", title="The event stream drops"),
        item("event_stream", key="event_stream:B", title="The event stream drops"),
    ]
    result = run(api)
    (task,) = botops_tasks(api)
    assert result["woke"] == 0 and len(said(api, task["id"], "Also on 2 places")) == 1
    assert finding(api, "event_stream:B")["present"] == 1


def _part_clears(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
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


def _part_reopens(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    (first,) = botops_tasks(api)
    code_findings[:] = []
    run(api)
    with api.app.state.store.transaction() as c:
        TE.move(c, first["id"], TE.DONE, H.KEEPER, "fixed")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    tasks = botops_tasks(api)
    assert len(tasks) == 2 and tasks[0]["id"] == first["id"]
    assert tasks[1]["status"] not in ("done", "closed", "declined")


def _part_rows_gone(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    (task,) = botops_tasks(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM health_findings")  # rows lost (a restore): nothing says the problem is there
    code_findings.clear()
    run(api)
    assert said(api, task["id"], "Cleared at") and registry(api, "push_stuck")["cleared"] is True


def _part_clear_fails(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    (task,) = botops_tasks(api)
    real = H.task_comment
    failures = []

    def flaky(conn, actor, task_id, text, wake=True, **kwargs):
        if not failures:
            failures.append(text)
            raise RuntimeError("the store is busy")
        return real(conn, actor, task_id, text, wake=wake, **kwargs)

    monkeypatch.setattr(H, "task_comment", flaky)
    code_findings[:] = []
    first = run(api)
    assert len(failures) == 1 and first["woke"] == 0
    assert registry(api, "push_stuck")["cleared"] is False and said(api, task["id"], "Cleared at") == []
    second = run(api)
    assert second["woke"] == 1 and len(said(api, task["id"], "Cleared at")) == 1
    assert registry(api, "push_stuck")["cleared"] is True


def _part_no_botops(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    set_botops(api, "paused")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    result = run(api)
    assert botops_tasks(api) == [] and result["woke"] == 0
    assert result["details"]["unrouted"] == [{"signature": MH.signature("push_stuck"), "titles": ["Pushes are stuck"]}]
    assert finding(api, "push_stuck:A")["present"] == 1


def _part_no_person(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("push_stuck", key="push_stuck:A"), item("runner_outdated", key="runner_outdated:A")]
    run(api)
    run(api)
    tasks = botops_tasks(api)
    assert len(tasks) == 2 and {t["owner_id"] for t in tasks} == {"bot:botops"}


def _part_person_owned(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store = api.app.state.store
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    (first,) = botops_tasks(api)
    with store.read() as c:
        person = api.app.state.auth.owner_identity(c).actor
    with store.transaction() as c:
        TE.reassign(c, first["id"], person, H.KEEPER)
    before = len(comments)  # the creation's wake is on the task already: count from here
    code_findings[:] = []
    cleared = run(api)
    assert cleared["woke"] == 0 and [row for row in comments[before:] if row[0] == first["id"]] == []
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    back = run(api)
    assert back["woke"] == 1 and said(api, first["id"], "Back:") == []
    (new,) = [t for t in botops_tasks(api) if t["id"] != first["id"]]
    assert new["owner_id"] == "bot:botops" and new["status"] not in ("done", "closed", "declined")


def _part_one_ticket(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    now, sig = datetime.now(timezone.utc), MH.signature("queue_idle")
    code_findings[:] = [item("queue_idle", key="queue_idle:bot-1", kind=MH.BUG, title="Work waits with no turn")]
    run(api, now)  # noisy: pending
    first = file_due(api, run(api, now + timedelta(minutes=1)))[sig]
    assert first["ticket"] and len(hq.tickets) == 1
    again = file_due(api, run(api, now + timedelta(hours=25)))  # seen a third time, a day on: a comment, not a ticket
    assert again[sig]["comment"] is True and len(hq.tickets) == 1
    (ticket,) = hq.tickets.values()
    assert [m["body"][:37] for m in ticket["messages"]] == ["[Health Monitor] Seen again (3 times)"]


def _part_five_a_day(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store, sig = api.app.state.store, MH.signature("push_stuck")
    with store.transaction() as c:
        for i in range(5):
            c.execute(
                "INSERT INTO health_reports(signature,ticket_id,created_at,reported_occurrences) VALUES(?,?,?,1)",
                (f"hm-filed{i}", f"ticket-{i}", H.now()),
            )
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    busy = file_due(api, run(api))[sig]
    assert busy["deferred"] == "support_busy" and hq.tickets == {} and hq.seen == []
    with store.transaction() as c:  # the five were filed more than a day ago
        c.execute("UPDATE health_reports SET created_at=?", (H.shift(H.now(), hours=-25),))
    filed = file_due(api, run(api))[sig]
    assert filed["ticket"] and len(hq.tickets) == 1


def _part_after_write(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    now, sig = datetime.now(timezone.utc), MH.signature("queue_idle")
    code_findings[:] = [item("queue_idle", key="queue_idle:bot-1", kind=MH.BUG, title="Work waits with no turn")]
    run_step(api, now)  # noisy: pending until its second run
    result = run_step(api, now + timedelta(minutes=1))
    assert result["details"]["reports_due"] == [sig]
    assert result["details"]["reports"][sig]["ticket"] and len(hq.tickets) == 1


def _part_support_off(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store = api.app.state.store
    sig = MH.signature("push_stuck")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    result = run(api)
    monkeypatch.setenv("TICO_SUPPORT", "off")
    answers = file_due(api, result)
    assert answers[sig]["deferred"] == "support_off"
    assert hq.seen == [] and open_approvals(api, "support") == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM health_reports").fetchone()[0] == 0


def _part_daily_comment(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    t0, sig = clock.at, MH.signature("push_stuck")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    file_due(api, run(api))  # the ticket
    for hours in (1, 2):
        clock.at = t0 + timedelta(hours=hours)
        assert file_due(api, run(api)) == {}  # seen again, under a day from the report

    def seen_again():
        (ticket,) = hq.tickets.values()
        return [m for m in ticket["messages"] if m["body"].startswith("[Health Monitor] Seen again")]

    assert seen_again() == []
    clock.at = t0 + timedelta(hours=25)
    assert file_due(api, run(api))[sig]["comment"] is True and len(seen_again()) == 1
    for hours in (26, 27):
        clock.at = t0 + timedelta(hours=hours)
        file_due(api, run(api))
    assert len(seen_again()) == 1  # the day counts from the comment, not the ticket


def _part_one_approval(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    set_rule(api, False)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    sig = MH.signature("push_stuck")
    first = file_due(api, run(api))[sig]
    assert first["approval"] and hq.seen == []
    (approval,) = open_approvals(api, "support")
    assert approval["id"] == first["approval"]
    again = api.post("/api/v2/health/findings/push_stuck:A/report", json={}, headers=signed_in()).json()
    assert again == {"signature": sig, "approval": first["approval"], "again": True}  # still waiting for the person
    assert len(open_approvals(api, "support")) == 1 and hq.seen == []


def _part_declined(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    set_rule(api, False)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    first = file_due(api, run(api))[MH.signature("push_stuck")]["approval"]
    answer_report(api, first, ok=False)
    second = api.post("/api/v2/health/findings/push_stuck:A/report", json={}, headers=signed_in()).json()
    assert second["approval"] and second["approval"] != first and "again" not in second
    assert hq.seen == []


def _part_owner_approved(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    store = api.app.state.store
    set_rule(api, False)
    runner_errors(api, runner, STREAM)
    run(api)
    url = f"/api/v2/health/findings/event_stream:{runner['runner_id']}/report"
    approval = api.post(url, json={}, headers=signed_in()).json()["approval"]
    with store.read() as c:
        owner = api.app.state.auth.owner_identity(c)
    answer_report(api, approval)
    api.app.state.support.file_approved(owner, approval)
    assert json.loads(hq.seen[-1].content)["source"] == "health_monitor"
    with store.read() as c:
        (request_key, source) = c.execute(
            "SELECT request_key, json_extract(sent,'$.source') FROM support_tickets"
        ).fetchone()
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


def _part_sent_as_requester(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store, support, auth, settings = (
        api.app.state.store,
        api.app.state.support,
        api.app.state.auth,
        api.app.state.store.settings,
    )
    set_rule(api, False)
    sig = MH.signature("push_stuck")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    first = file_due(api, run(api))[sig]
    (approval,) = open_approvals(api, "support")
    assert first["approval"] == approval["id"] and hq.seen == []
    with store.read() as c:
        assert task_asks.grant(c, approval["id"])["asked_by_id"] == "bot:botops"
    answer_report(api, approval["id"])

    def unreachable(who, approval_id):
        raise Problem("support_unreachable", "Tico support is out of reach", 503)

    real = support.file_approved
    monkeypatch.setattr(support, "file_approved", unreachable)
    waiting = MH.file_reports(store, auth, settings, support, [])
    assert "deferred" in waiting[sig] and hq.seen == []
    with store.read() as c:
        assert c.execute("SELECT ticket_id FROM health_reports WHERE signature=?", (sig,)).fetchone()[0] is None
    monkeypatch.setattr(support, "file_approved", real)
    sent = MH.file_reports(store, auth, settings, support, [])
    assert sent[sig]["ticket"] and len(hq.tickets) == 1
    assert json.loads(hq.seen[-1].content)["source"] == "health_monitor"
    with store.read() as c:
        assert (
            c.execute("SELECT ticket_id FROM health_reports WHERE signature=?", (sig,)).fetchone()[0]
            == sent[sig]["ticket"]
        )
    assert MH.file_reports(store, auth, settings, support, []) == {}  # sent once: nothing waits any more


def _part_approved_cap(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store, auth, support = api.app.state.store, api.app.state.auth, api.app.state.support
    set_rule(api, False)
    code_findings[:] = [
        item("push_stuck", key="push_stuck:A", kind=MH.BUG),
        item("worktree_error", key="worktree_error:A", kind=MH.BUG),
    ]
    approvals = file_due(api, run(api))
    clock.at += timedelta(hours=25)  # requests from yesterday still count when sent today
    for answer in approvals.values():
        answer_report(api, answer["approval"])
    with store.transaction() as c:
        for i in range(MH.REPORTS_PER_DAY - 1):
            c.execute(
                "INSERT INTO health_reports(signature,ticket_id,created_at,reported_occurrences) VALUES(?,?,?,1)",
                (f"hm-filed{i}", f"ticket-{i}", H.now()),
            )
    real = support.file_approved

    def send(who, approval_id):
        assert len(store.writer.queue) == 0  # HQ is called outside the write transaction
        return real(who, approval_id)

    monkeypatch.setattr(support, "file_approved", send)
    answers = file_due(api, run(api))
    assert len([a for a in answers.values() if a.get("ticket")]) == 1 and len(hq.tickets) == 1
    (waiting,) = [sig for sig, answer in answers.items() if answer.get("deferred") == "support_busy"]
    with store.read() as c:
        row = c.execute(
            "SELECT h.ticket_id,a.used_at FROM health_reports h JOIN ok_grants a ON a.requirement_id=h.approval_id "
            "WHERE h.signature=?",
            (waiting,),
        ).fetchone()
        # The owner's click claimed it (support.ok_given): it waits in the queue, claimed, for a day with room.
        assert row["ticket_id"] is None and row["used_at"] is not None
    assert MH.file_reports(store, auth, store.settings, support, []) == {
        waiting: {"signature": waiting, "deferred": "support_busy"}
    }
    assert len(hq.tickets) == 1
    clock.at += timedelta(hours=25)
    sent = MH.file_reports(store, auth, store.settings, support, [])
    assert sent[waiting]["ticket"] and len(hq.tickets) == 2


def _part_send_resets(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store, sig = api.app.state.store, MH.signature("push_stuck")
    set_rule(api, False)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG)]
    approval = file_due(api, run(api))[sig]["approval"]
    clock.at += timedelta(hours=25)
    result = run(api)  # seen again while the approval waited
    assert result["details"]["reports_due"] == [sig]
    answer_report(api, approval)
    assert file_due(api, result)[sig]["ticket"]
    (ticket,) = hq.tickets.values()
    assert ticket["messages"] == []
    with store.read() as c:
        row = c.execute(
            "SELECT reported_occurrences,reported_at FROM health_reports WHERE signature=?", (sig,)
        ).fetchone()
        assert row["reported_occurrences"] == 2 and row["reported_at"] == H.now()
    clock.at += timedelta(hours=1)
    assert file_due(api, run(api)) == {} and ticket["messages"] == []
    clock.at += timedelta(hours=24)
    assert file_due(api, run(api))[sig]["comment"] is True and len(ticket["messages"]) == 1


def _part_outside_lock(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    # The Health Monitor's scan read every finding inside the write transaction (7.2 s on TIDY, Oct 29): each runner call
    # waited behind it. The reads run in a snapshot now; only the changes take the lock.
    _readers(monkeypatch)
    store = api.app.state.store
    holders = []
    real = MH.findings

    def watched(c, settings):
        holders.append(len(store.writer.queue))  # the write gate: holder plus waiters, 0 when none holds it
        return real(c, settings)

    monkeypatch.setattr(MH, "findings", watched)
    runner_errors(api, runner, STREAM)
    result = check_health.run_once(store, api.app.state.auth, store.settings)
    assert "event_stream:" + runner["runner_id"] in result["details"]["filed"]
    assert holders == [0]


def _part_read_only(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store, auth = api.app.state.store, api.app.state.auth
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    ctx = check_health.context(auth, store.settings, store)
    with store.read() as r:
        r.execute("PRAGMA query_only=1")  # a write on the read connection fails from here on
        try:
            result = check_health.run(r, ctx)
        finally:
            r.execute("PRAGMA query_only=0")
    assert result["woke"] == 1 and result["details"]["filed"] == ["push_stuck:A"]
    assert [t["owner_id"] for t in botops_tasks(api)] == ["bot:botops"]


def _part_readers_write_nothing(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    # The real readers, the Health page's own checks included: a reader that wrote would fail on the read connection and
    # be noted here.
    store, auth = api.app.state.store, api.app.state.auth
    runner_errors(api, runner, STREAM)
    monkeypatch.setattr(hosted_updates, "notice", lambda *a, **k: {})
    monkeypatch.setattr(replication, "status", lambda *a, **k: {})
    ctx = check_health.context(auth, store.settings, store)
    with store.read() as r:
        r.execute("PRAGMA query_only=1")
        try:
            _, found, notes = check_health.gather(r, ctx)
        finally:
            r.execute("PRAGMA query_only=0")
    assert notes == {}, notes
    assert any(row["key"].startswith("event_stream:") for row in found)


def _part_app_settings(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    # The engine's ctx["settings"] is the step's own row (a dict). The readers and the report path need the app's settings.
    _readers(monkeypatch)
    store, auth = api.app.state.store, api.app.state.auth
    seen = {}

    def view(c, who, settings, auth_, github, config):
        seen["settings"] = settings
        return {"checks": []}

    monkeypatch.setattr(health, "view", view)
    ctx = check_health.context(auth, store.settings, store)
    ctx["settings"] = {"id": "check_health", "enabled": True}
    with store.read() as c:
        result = check_health.run(c, ctx)
    assert seen["settings"] is store.settings
    assert "code" not in result["details"]["notes"] and "page" not in result["details"]["notes"]


def _part_wake_once(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    three_new_problems(code_findings)
    result = run(api)
    assert result["woke"] == 1 and len(botops_tasks(api)) == 3
    ((task_id, wake, text),) = [row for row in comments if row[1]]
    assert wake is True and text.startswith("Health wake: ")
    assert task_id in [t["id"] for t in botops_tasks(api)]
    assert all(f"(task {t['id']})" in text for t in botops_tasks(api))
    assert [job["task"] for job in wake_jobs(api)] == [task_id]
    ids = {t["id"] for t in botops_tasks(api)}
    with api.app.state.store.read() as c:
        (wake,) = c.execute("SELECT target,detail FROM events WHERE action=?", (L.BOT_ACTION,)).fetchall()
        assert wake["target"] == MH.BOTOPS
        assert set(json.loads(wake["detail"])["task_ids"]) == ids
        assert {r[0] for r in c.execute("SELECT target FROM events WHERE action=?", (L.TASK_ACTION,))} == ids


def _part_held_by_rule(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    from backend import rules

    code_findings = _code_findings(monkeypatch)
    three_new_problems(code_findings)
    real = rules.judge

    def held(conn, actor, target, kind, refs, body, conv, in_reply_to=None, decided=None):
        if (refs or {}).get("trigger_source") == "check_health":
            return rules.Decision(rules.NOTE, rules.MANAGER_NOTE, rules.HOLD_ROW)
        return real(conn, actor, target, kind, refs, body, conv, in_reply_to, decided)

    monkeypatch.setattr(rules, "judge", held)
    assert run(api)["woke"] == 0 and wake_jobs(api) == []
    assert len(wake_marker(api)) == 3
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM events WHERE action IN (?,?)", (L.BOT_ACTION, L.TASK_ACTION)).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(rules, "judge", real)
    assert run(api)["woke"] == 1 and len(wake_jobs(api)) == 1
    assert wake_marker(api) is None


def _part_capped_task(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    three_new_problems(code_findings)
    gate = check_health._wake_allowed
    monkeypatch.setattr(check_health, "_wake_allowed", lambda c, ids: [])
    run(api)
    ids = wake_marker(api)
    monkeypatch.setattr(check_health, "_wake_allowed", gate)
    with api.app.state.store.transaction() as c:
        for n in range(L.WAKES_PER_TASK_DAY):
            L.record(c, f"other_{n}", [ids[-1]])  # the last task in the group is at its daily cap
    assert run(api)["woke"] == 1 and wake_marker(api) == [ids[-1]] and len(wake_jobs(api)) == 1
    clock.at += timedelta(hours=25)
    assert run(api)["woke"] == 1 and wake_marker(api) is None
    assert len(wake_jobs(api)) == 2


def _part_fleet_cap(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    three_new_problems(code_findings)
    with api.app.state.store.transaction() as c:
        for n in range(L.WAKES_PER_HOUR):
            L.record(c, f"fleet_{n}")
    assert run(api)["woke"] == 0 and len(wake_marker(api)) == 3 and wake_jobs(api) == []
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM events WHERE action=?", (L.BOT_ACTION,)).fetchone()[0] == L.WAKES_PER_HOUR
        )
        assert c.execute("SELECT count(*) FROM events WHERE action=?", (L.TASK_ACTION,)).fetchone()[0] == 0
    clock.at += timedelta(hours=1, seconds=1)
    assert run(api)["woke"] == 1 and wake_marker(api) is None


def _part_other_wake(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    store, auth = api.app.state.store, api.app.state.auth
    three_new_problems(code_findings)
    with store.transaction() as c:
        run_id = M._insert(c, "run_now:" + H.new_id(), "run_now", None, H.now())
        L.record(c, MH.BOTOPS)
    with store.read() as c:
        result = check_health.run(c, {"store": store, "auth": auth, "now": H.now(), "run_id": run_id})
    assert result["woke"] == 0 and len(wake_marker(api)) == 3 and wake_jobs(api) == []
    with store.transaction() as c:
        c.execute("UPDATE maintenance_runs SET finished_at=? WHERE id=?", (H.now(), run_id))
    clock.at += timedelta(seconds=1)  # the frozen clock: the next run starts after the other wake
    assert run(api)["woke"] == 1 and wake_marker(api) is None


def _part_gate_refuses(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    three_new_problems(code_findings)
    monkeypatch.setattr(check_health, "_wake_allowed", lambda c, ids: [])
    refused = run(api)
    assert refused["woke"] == 0 and [row for row in comments if row[1]] == []
    assert len(wake_marker(api)) == 3
    monkeypatch.setattr(check_health, "_wake_allowed", lambda c, ids: ids)
    allowed = run(api)
    assert allowed["woke"] == 1 and len([row for row in comments if row[1]]) == 1
    assert wake_marker(api) is None


def _part_private_title(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("push_stuck", key="push_stuck:A"), item("worktree_error", key="worktree_error:A")]
    monkeypatch.setattr(check_health, "_wake_allowed", lambda c, ids: [])
    run(api)
    shared, private = wake_marker(api)
    secret = "Confidential merger terms"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (shared,))
        c.execute("UPDATE tasks SET private=1,title=? WHERE id=?", (secret, private))
    monkeypatch.setattr(check_health, "_wake_allowed", lambda c, ids: ids)
    assert run(api)["woke"] == 1
    (text,) = said(api, shared, "Health wake: ")
    assert f"(task {shared})" in text and "a private health task" in text
    assert secret not in text and private not in text


def _part_one_hit(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    monkeypatch.setattr(invariants, "CHECKS", [fake_check("lease_overdue", ["r1", "r2"])])
    result = run(api)
    assert [t["title"] for t in botops_tasks(api)] == ["Health: Production check: Runs past their lease"]
    assert result["woke"] == 1 and result["details"]["filed"] == ["inv:lease_overdue"]
    with api.app.state.store.read() as c:
        person = c.execute(
            "SELECT count(*) FROM tasks WHERE title LIKE '%Production check%' AND owner_id NOT LIKE 'bot:%'"
        ).fetchone()[0]
    assert person == 0


def _part_on_page(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    monkeypatch.setattr(invariants, "CHECKS", [fake_check("lease_overdue", ["r1"])])
    row = {
        "id": "invariant:lease_overdue",
        "label": "Runs past their lease",
        "status": "warn",
        "summary": "x",
        "fixes": [],
    }
    monkeypatch.setattr(health, "view", lambda c, *a, **k: {"checks": [row]})
    run(api)
    assert filed_keys(api) == ["inv:lease_overdue"]
    assert finding(api, "inv:lease_overdue")["check_id"] == "inv:lease_overdue"


def _part_sweep(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    store, auth = api.app.state.store, api.app.state.auth
    monkeypatch.setattr(invariants, "CHECKS", [fake_check("lease_overdue", ["r1"])])
    before = task_count(api)
    assert invariants.sweep(store, auth, store.settings, datetime.now(timezone.utc)) == {"lease_overdue": 1}
    assert task_count(api) == before
    with store.read() as c:
        assert tuple(
            c.execute("SELECT firing, count, task_id FROM invariant_state WHERE check_id='lease_overdue'").fetchone()
        ) == (1, 1, None)


def _part_links(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    store = api.app.state.store
    monkeypatch.setattr(invariants, "CHECKS", [fake_check("lease_overdue", ["r1"])])
    invariants.sweep(store, api.app.state.auth, store.settings, datetime.now(timezone.utc))
    with store.read() as c:
        assert invariants.health_checks(c)[0]["fixes"] == [], "the sweep raised no task to link"
    run(api)
    (task,) = botops_tasks(api)
    with store.read() as c:
        (row,) = invariants.health_checks(c)
    assert row["id"] == "invariant:lease_overdue"
    assert row["fixes"] == [{"label": "Open the alert", "href": "#/tasks/" + task["id"], "tab": "", "click": ""}]


def _part_warning(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    page = [
        {
            "id": "disk:acme-mac",
            "label": "Disk space",
            "status": "warn",
            "summary": "92% full.",
            "fixes": [{"label": "Free space"}],
        },
        {"id": "invariant", "label": "Invariants", "status": "warn", "summary": "Read separately.", "fixes": []},
        {"id": "backups", "label": "Backups", "status": "ok", "summary": "", "fixes": []},
    ]
    monkeypatch.setattr(health, "view", lambda c, *a, **k: {"checks": page})
    run(api)
    run(api)
    (task,) = botops_tasks(api)
    assert task["title"] == "Health: Disk space" and "Free space" in task["body"]
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT key FROM health_findings WHERE present=1 AND pending=0 AND key LIKE 'page:%'"
        ).fetchall()
    assert [r["key"] for r in rows] == ["page:disk:acme-mac"]


def _part_lists(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    code_findings = _code_findings(monkeypatch)
    code_findings[:] = [item("push_stuck", key="push_stuck:A", title="Pushes are stuck")]
    run(api)
    body = api.get("/api/v2/health/findings", headers=signed_in()).json()
    (row,) = body["findings"]
    assert row["key"] == "push_stuck:A" and row["tag"] == "urgent" and row["task_id"] == botops_tasks(api)[0]["id"]
    assert body["botops"] is True and body["reports_without_asking"] is True


def _part_github(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    _readers(monkeypatch)
    store = api.app.state.store
    store.github_app = SimpleNamespace(row=lambda c: {"installation_id": 7, "org": "acme", "administration": 1})
    health.note_github_token(store, "The App's key was refused", "Check the GitHub App's private key")
    seen = {}

    def view(c, who, settings, auth_, github, config):
        seen["github"] = github
        return {"checks": [health._github(c, github, settings)]}

    monkeypatch.setattr(health, "view", view)
    run(api)
    run(api)  # a noisy check files on its second run
    assert seen["github"] is store.github_app
    assert finding(api, "page:github")["severity"] == "bad" and finding(api, "page:github")["present"] == 1


def _part_quota(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    # The first case on the install: nothing earlier leaves a finding, so these read every computer's findings, unfiltered.
    _readers(monkeypatch)
    rid = runner["runner_id"]
    key = f"default_login_limited:{rid}:codex"
    # The Oct 18 case: hops to Codex on the computer's default login (no profile) and a check model, all refused at
    # once with no reset time. A hop's usage names only the primary that spent tokens.
    turn(
        api,
        rid,
        {
            **QUOTA,
            "fallback": "codex",
            "primary_limited": True,
            "usage": {"runtime": "claude", "segments": [{"runtime": "claude"}]},
        },
        50,
    )
    assert key not in login_findings(api)  # one limit is not yet a pattern
    turn(api, rid, {**QUOTA, "check": "codex"}, 40)
    turn(
        api, rid, {"outcome": "failed", "fallback": "codex", "profile_used": None}, 35
    )  # another failure: says nothing
    turn(api, rid, {**QUOTA, "fallback": "codex"}, 30)
    # The same runtime signed in as a subscription profile is not the default login, and works.
    turn(api, rid, {"outcome": "completed", "fallback": "codex", "profile_used": "work"}, 20)
    # Claude on the default login works: only Codex is out.
    turn(api, rid, {"outcome": "completed", "profile_used": None, "usage": {"runtime": "claude"}}, 10)
    found = login_findings(api)
    assert list(found) == [key], found
    row_item = found[key]
    assert row_item["kind"] == "setup" and row_item["title"] == f"{CANARY}'s default Codex login is out of quota", (
        row_item
    )
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


def _part_reset(api, runner, monkeypatch, *, reset_minutes, raised, hq=None, comments=None, clock=None):
    reset = None if reset_minutes is None else H.shift(H.now(), minutes=reset_minutes)
    for ago in (30, 20):
        turn(api, runner["runner_id"], {**QUOTA, "check": "codex", "limit_resets_at": reset}, ago)
    found = login_findings(api, runner['runner_id'])
    assert bool(found) is raised, found
    if reset_minutes == 60:
        assert "resets at " + reset[:16].replace("T", " ") + " UTC" in next(iter(found.values()))["detail"]


def _part_older(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    for ago in (25 * 60, 24 * 60 + 30):
        turn(api, runner["runner_id"], {**QUOTA, "check": "codex"}, ago)
    assert login_findings(api, runner['runner_id']) == {}


def _part_failed(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    for ago in (30, 20):
        turn(api, runner["runner_id"], {"outcome": "failed", "check": "codex", "profile_used": None}, ago)
    assert login_findings(api, runner['runner_id']) == {}
    turn(api, runner["runner_id"], {**QUOTA, "check": "codex"}, 10)  # one limit among them is still one
    assert login_findings(api, runner['runner_id']) == {}


def _part_own_key(api, runner, monkeypatch, hq=None, comments=None, clock=None):
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
    assert login_findings(api, runner['runner_id']) == {}
    # Codex is signed in by the computer: two limits there are its finding, and gem-b's completed turn leaves it.
    turn(api, rid, {**QUOTA, "check": "codex"}, 25, bot="gem-a")
    turn(api, rid, {**QUOTA, "fallback": "codex"}, 20, bot="pi-a")
    turn(api, rid, {"outcome": "completed", "profile_used": None}, 10, bot="gem-b")
    assert list(login_findings(api, runner['runner_id'])) == [f"default_login_limited:{rid}:codex"]


# ------------------------------------------------------------------------------------- the stories
def _fresh(api, hq=None, comments=None):
    """What a section of a story leaves behind, wiped: findings, reports, OKs, wakes and Health tasks (kept, renamed
    and closed), so the next section starts as on a new install. BotOps is active again, the team rule back to its default."""
    with api.app.state.store.transaction() as c:
        for (tid,) in c.execute("SELECT id FROM tasks WHERE title LIKE 'Health:%' AND status='open'").fetchall():
            H.task_close(c, H.KEEPER, tid, "A new section of the story.")
        c.execute("UPDATE tasks SET title='old: '||title WHERE title LIKE 'Health:%'")
        c.execute("UPDATE messages SET body='old '||body WHERE body LIKE 'Health wake: %'")
        for table in ("health_findings", "health_reports", "invariant_state", "support_tickets"):
            c.execute(f"DELETE FROM {table}")
        c.execute("DELETE FROM ok_grants WHERE subject LIKE 'support:%'")
        c.execute("DELETE FROM registry_metadata WHERE key LIKE 'health-%'")
        c.execute("DELETE FROM events WHERE action IN (?,?)", (L.BOT_ACTION, L.TASK_ACTION))
        c.execute("UPDATE bots SET state='active' WHERE id='botops'")
    set_rule(api, True)
    if hq is not None:
        hq.tickets.clear()
        hq.seen.clear()
    if comments is not None:
        comments.clear()


def _story(environment, monkeypatch, parts, **fixtures):
    """One install for every part, each in its own monkeypatch scope after `_fresh`."""
    api, runner = built(environment)
    for part in parts:
        _fresh(api, fixtures.get("hq"), fixtures.get("comments"))
        with monkeypatch.context() as scope:
            if isinstance(part, tuple):
                part[0](api, runner, scope, **part[1], **fixtures)
            else:
                part(api, runner, scope, **fixtures)
    return api


def test_a_findings_botops_task_lifecycle_file_repeat_clear_return_reopen(environment, monkeypatch):
    _story(
        environment,
        monkeypatch,
        [
            _part_urgent,
            _part_noisy,
            _part_noisy_passes,
            _part_repeat,
            _part_two_computers,
            _part_clears,
            _part_reopens,
            _part_rows_gone,
            _part_clear_fails,
        ],
    )


def test_with_no_botops_or_a_person_owned_task_nothing_is_filed_to_a_person(environment, monkeypatch, comments):
    _story(environment, monkeypatch, [_part_no_botops, _part_no_person, _part_person_owned], comments=comments)


def test_a_tico_bug_reaches_hq_ticket_repeat_comment_support_off_and_daily_caps(environment, monkeypatch, hq, clock):
    _story(
        environment,
        monkeypatch,
        [_part_one_ticket, _part_five_a_day, _part_after_write, _part_support_off, _part_daily_comment],
        hq=hq,
        clock=clock,
    )


def test_a_copy_run_from_source_files_no_automatic_report_until_it_names_an_hq(
    environment, code_findings, hq, monkeypatch
):
    # The factory simulation ("Tico: dev", the same seeded install id every run) filed a ticket to production HQ each run.
    monkeypatch.setenv("TICO_VERSION", "dev")
    api, _ = built(environment)
    app, store, sig = api.app, api.app.state.store, MH.signature("push_stuck")
    code_findings[:] = [item("push_stuck", key="push_stuck:A", kind=MH.BUG, title="Pushes are stuck")]
    assert file_due(api, run(api))[sig] == {"signature": sig, "deferred": "source_copy"}
    assert hq.seen == [] and open_approvals(api, "support") == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM health_reports").fetchone()[0] == 0
        (why,) = c.execute("SELECT target, detail FROM events WHERE action='health_monitor.not_reported'").fetchall()
    assert why["target"] == "source_copy" and json.loads(why["detail"])["signatures"] == [sig]
    # BotOps asking for the report directly is refused the same way; the owner's own click still reports.
    with pytest.raises(Problem) as refused:
        MH.report(store, app.state.support, app.state.auth, store.settings, SimpleNamespace(actor="bot:botops"), sig)
    assert refused.value.code == "source_copy" and hq.seen == []
    owner = api.post("/api/v2/health/findings/push_stuck:A/report", json={}, headers=signed_in())
    assert owner.status_code == 200 and owner.json()["ticket"] and len(hq.tickets) == 1
    # TICO_HQ_URL set on purpose sends as a release does.
    monkeypatch.setenv("TICO_HQ_URL", "https://updates.tico.team")
    with store.transaction() as c:
        c.execute("DELETE FROM health_reports")
        c.execute("DELETE FROM support_tickets")
    assert file_due(api, run(api))[sig]["ticket"] and len(hq.tickets) == 2


def _claimed_count(api):
    """The Health page's number of OK'd sends a click claimed and Maintenance has not delivered (this file stubs the
    page's view, so its check is read directly)."""
    with api.app.state.store.read() as c:
        return health._claimed_oks_check(c)["counts"]["claimed_not_delivered"]


def _part_claimed_on_click(api, runner, monkeypatch, hq=None, comments=None, clock=None):
    """The lead's rule on Codex's upkeep review, item 2: a background job never claims an OK. The owner's click claims
    a report's OK and queues it in the same transaction; Maintenance only delivers what a click claimed, rechecking
    who gave the OK before the first send, and a retry goes under the same Idempotency-Key, delivered once."""
    from backend import support as support_queue

    code_findings = _code_findings(monkeypatch)
    store, support, auth, settings = (
        api.app.state.store,
        api.app.state.support,
        api.app.state.auth,
        api.app.state.store.settings,
    )
    set_rule(api, False)
    checks = ("push_stuck", "worktree_error", "queue_idle")
    code_findings[:] = [item(check, key=check + ":A", kind=MH.BUG) for check in checks]
    now = datetime.now(timezone.utc)
    file_due(api, run(api, now))
    file_due(api, run(api, now + timedelta(minutes=1)))  # queue_idle is noisy: asked on its second sighting
    with store.read() as c:
        unclaimed, claimed, lost = (
            c.execute("SELECT approval_id FROM health_reports WHERE signature=?", (MH.signature(check),)).fetchone()[0]
            for check in checks
        )
    # An OK given without its claim (the click as it was before: no ON_OK step for a report): Maintenance never
    # claims it, and neither does a send.
    with monkeypatch.context() as old_click:
        old_click.setattr(task_asks, "ON_OK", {k: v for k, v in task_asks.ON_OK.items() if k != "support"})
        answer_report(api, unclaimed)
    with store.read() as c:
        assert task_asks.standing(c, unclaimed) == task_asks.OK
    assert MH.file_reports(store, auth, settings, support, []) == {} and hq.seen == []
    with pytest.raises(Problem, match="not claimed"):
        support.file_approved(SimpleNamespace(actor="bot:botops"), unclaimed)
    with store.read() as c:
        assert task_asks.grant(c, unclaimed)["used_at"] is None
    assert _claimed_count(api) == 0
    # The click claims it and queues it: used in the click's transaction, waiting for Maintenance.
    answer_report(api, claimed)
    with store.read() as c:
        assert task_asks.standing(c, claimed) == task_asks.USED
        assert [r["requirement_id"] for r in support_queue.claimed(c)] == [claimed]
    assert _claimed_count(api) == 1 and hq.seen == []
    # HQ out of reach: it stays claimed and queued. Then it goes once, under the same key.
    hq.down = True
    assert "deferred" in MH.file_reports(store, auth, settings, support, [])[MH.signature("worktree_error")]
    assert _claimed_count(api) == 1
    hq.down = False
    sent = MH.file_reports(store, auth, settings, support, [])
    assert sent[MH.signature("worktree_error")]["ticket"] and len(hq.tickets) == 1
    keys = {r.headers.get("idempotency-key") for r in hq.seen if r.url.path == "/v1/support"}
    assert keys == {"tico-support:" + claimed}, keys
    assert MH.file_reports(store, auth, settings, support, []) == {} and len(hq.tickets) == 1
    assert _claimed_count(api) == 0
    # Claimed, and whoever gave it may no longer give it before it first leaves: it is given up on, never sent.
    answer_report(api, lost)
    assert _claimed_count(api) == 1
    real = task_asks._authority
    monkeypatch.setattr(task_asks, "_authority", lambda c, person, row: "Ana can no longer OK this")
    answer = MH.file_reports(store, auth, settings, support, [])[MH.signature("queue_idle")]
    assert answer["deferred"] == "approval" and len(hq.tickets) == 1
    monkeypatch.setattr(task_asks, "_authority", real)
    assert MH.file_reports(store, auth, settings, support, []) == {} and len(hq.tickets) == 1
    assert _claimed_count(api) == 0


def test_a_reports_ok_is_claimed_by_the_click_and_maintenance_only_delivers_it(environment, monkeypatch, hq, clock):
    _story(environment, monkeypatch, [_part_claimed_on_click], hq=hq, clock=clock)


def test_with_the_team_rule_off_an_ok_sent_back_given_and_sent_as_whoever_asked(environment, monkeypatch, hq, clock):
    _story(
        environment,
        monkeypatch,
        [
            _part_one_approval,
            _part_declined,
            _part_owner_approved,
            _part_sent_as_requester,
            _part_approved_cap,
            _part_send_resets,
        ],
        hq=hq,
        clock=clock,
    )


def test_the_step_reads_outside_the_write_lock_on_a_read_only_connection_with_the_apps_settings(
    environment, monkeypatch
):
    _story(
        environment, monkeypatch, [_part_outside_lock, _part_read_only, _part_readers_write_nothing, _part_app_settings]
    )


def test_a_grouped_wake_once_held_by_rule_gate_and_caps_then_wakes_once(environment, monkeypatch, comments, clock):
    _story(
        environment,
        monkeypatch,
        [_part_wake_once, _part_held_by_rule, _part_capped_task, _part_fleet_cap, _part_other_wake, _part_gate_refuses],
        comments=comments,
        clock=clock,
    )


def test_a_grouped_wake_hides_a_private_title(environment, monkeypatch, clock):
    _story(environment, monkeypatch, [_part_private_title], clock=clock)


def test_a_production_check_is_one_botops_task_and_the_health_page_links_it(environment, monkeypatch):
    _story(environment, monkeypatch, [_part_one_hit, _part_on_page, _part_sweep, _part_links])


def test_the_health_page_feeds_the_lifecycle_lists_findings_and_reads_the_github_app(environment, monkeypatch):
    _story(environment, monkeypatch, [_part_warning, _part_lists, _part_github])


def test_a_default_login_limit_is_a_finding_until_a_turn_or_the_reset_clears_it(environment, monkeypatch):
    api, _ = built(environment)
    parts = [
        _part_quota,
        (_part_reset, {"reset_minutes": None, "raised": True}),
        (_part_reset, {"reset_minutes": 60, "raised": True}),
        (_part_reset, {"reset_minutes": -1, "raised": False}),
        _part_older,
        _part_failed,
        _part_own_key,
    ]
    for part in parts:
        runner = machine(api, label=CANARY)  # a computer of its own per case: its turns are its own
        with monkeypatch.context() as scope:
            _readers(scope)
            part, extra = part if isinstance(part, tuple) else (part, {})
            part(api, runner, scope, **extra)


LIMITED_CASES = [
    (True, ("anthropic", "openai"), None, False),  # the company's Codex row takes the turn
    (True, ("anthropic", "openai"), {"runtime": "claude"}, False),
    (False, ("anthropic", "openai"), None, True),  # the owner turned automatic fallback off
    (True, ("anthropic",), None, True),  # no row on another runtime
    (True, ("openai",), {"runtime": "codex"}, True),  # the only row is the bot's own runtime...
    (True, ("anthropic", "openai"), {"fallback": {"off": True}}, True),  # ...or the bot turned fallback off...
    (True, ("anthropic", "openai"), {"fallback": {"harness": "codex"}}, True),  # ...or names one with no model
]


def test_limited_no_fallback_counts_the_company_fallback_and_a_bots_own_fallback(environment):
    api, _ = built(environment)
    store = api.app.state.store
    for n, (auto, enabled, config, raised) in enumerate(LIMITED_CASES):
        bot = f"{SECRET_BOT}-{n}"  # a bot of its own per case
        company(api, enabled, auto)
        limited(api, bot=bot, config=config)
        with store.read() as c:
            found = {i["key"] for i in MH.findings(c, store.settings)}
        assert ("limited_no_fallback:" + bot in found) is raised, (n, auto, enabled, config)
    company(api, auto=False)
    limited(api, bot=SECRET_BOT, config={"fallback": {"harness": "codex", "model": "gpt-6-luna"}})
    with store.read() as c:  # a bot's own fallback still counts with the company's off
        assert not [
            i
            for i in MH.findings(c, store.settings)
            if i["check_id"] == "limited_no_fallback" and i["key"].endswith(SECRET_BOT)
        ]


def test_a_used_up_login_is_one_plain_finding_and_the_gateway_row_counts_only_under_the_gateway_choices(
    environment, monkeypatch
):
    api, runner = built(environment)
    rid, store = runner["runner_id"], api.app.state.store

    def found_for(bot, *check_ids):
        with store.read() as c:
            return [
                i
                for i in MH.findings(c, store.settings)
                if i["check_id"] in check_ids
                and i["key"].endswith(bot)
                or i["check_id"] == "default_login_limited"
                and "default_login_limited" in check_ids
                and rid in i["key"]
            ]

    for n, isolated in enumerate((True, False)):
        bot = f"{SECRET_BOT}-{n}"
        gateway_choice(api, monkeypatch, rid, isolated)
        with store.transaction() as c:  # the computer falls back to the Gateway (its own switch)
            c.execute("UPDATE runners SET gateway_fallback=1 WHERE id=?", (rid,))
        limited(api, bot=bot, config={"runtime": "claude"})
        with store.transaction() as c:
            c.execute(
                "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'t')",
                (bot, rid, H.now()),
            )
        for ago in (30, 20):
            turn(api, rid, {**QUOTA, "usage": {"runtime": "claude"}}, ago, bot=bot)
        found = found_for(bot, "limited_no_fallback", "default_login_limited")
        if isolated:
            # The Gateway takes the turn last: the bot is not stuck, and the login says so in plain words.
            assert [(i["check_id"], i["title"]) for i in found] == [
                ("default_login_limited", f"{CANARY}'s Claude login is used up")
            ]
            assert (
                found[0]["detail"] == f"{CANARY}'s Claude login is used up. Bots are on Tico Gateway until it resets."
            )
            # A computer that does not fall back to the Gateway gets the plain out-of-quota finding instead.
            with store.transaction() as c:
                c.execute("UPDATE runners SET gateway_fallback=0 WHERE id=?", (rid,))
            [off] = found_for(bot, "limited_no_fallback", "default_login_limited")
            assert off["check_id"] == "default_login_limited" and "out of quota" in off["title"]
            continue
        # A Mac skips the Gateway row, so the bot is stopped, and that finding says why and what to do.
        stopped = [i for i in found if i["check_id"] == "limited_no_fallback"]
        assert [i["title"] for i in stopped] == [f"{bot} stopped: its Claude login is used up"]
        assert "no computer here can run Tico Gateway work" in stopped[0]["detail"]
        assert stopped[0]["fix"] == "Sign in another Claude login, or add a Docker runner."
    gateway_choice(api, monkeypatch, rid, True)
    with store.transaction() as c:
        providers.save(
            c, "human:test", {"expected_revision": providers.load(c)["revision"], "preset": "subscriptions"}, H.now()
        )
    bot = SECRET_BOT + "-plain"
    limited(api, bot=bot, config={"runtime": "claude"})
    assert [i["title"] for i in found_for(bot, "limited_no_fallback")] == [
        f"{bot} stopped on a usage limit and has no fallback model"
    ]


def test_a_busy_process_is_reported_by_its_tico_name_only(environment, readers, hq):
    api, runner = built(environment)
    for process, shown in [
        ("container:acme-tico-1", "a Tico container"),
        ("container:acme-server-1", "the Tico server container"),
        ("process:AcmePayroll", "a process"),
        ("process:python3", "process python3"),
    ]:
        _fresh(api, hq)
        with api.app.state.store.transaction() as c:
            MH.record_idle(
                c,
                runner["runner_id"],
                MH.IdleLoad(sampled_at=H.now(), busy=[MH.IdleEntry(name=process, cpu=90, mb=300)]),
            )
        result = run(api)
        assert result["details"]["filed"] == [f"local_busy:{runner['runner_id']}:{process}"]
        file_due(api, result)
        message = json.loads(hq.seen[0].content)["message"]
        assert f"Process: {shown}\n" in message and "acme" not in message.lower()


def test_a_computer_on_a_release_the_server_cannot_work_with_is_bad(environment, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.4.45")
    api, runner = built(environment)
    for release, severity in [("0.0.9", "bad"), ("0.4.40", "warn")]:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", (H.now(), runner["runner_id"]))
            c.execute(
                "INSERT INTO runner_versions(runner_id,release,reported_at) VALUES(?,?,?) ON CONFLICT(runner_id) "
                "DO UPDATE SET release=excluded.release,update_state='',update_error=''",
                (runner["runner_id"], release, H.now()),
            )
            found = {f["check_id"]: f for f in MH.findings(c, api.app.state.store.settings)}
        assert found["runner_outdated"]["severity"] == severity


def test_branch_blocks_give_botops_the_branch_and_its_recovery_or_refusal(environment, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.4.45")
    api, runner = built(environment)
    for reason in [
        "will be recovered automatically on the next update",
        "cannot recover: 1 changed file; commit or stash it first: app.py",
        "switch it to main to follow releases",
    ]:
        error = "the checkout is on branch tico/b427e9ee-fix-runner; " + reason
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runner_states SET last_seen_at=? WHERE runner_id=?", (H.now(), runner["runner_id"]))
            c.execute(
                "INSERT INTO runner_versions(runner_id,release,update_state,update_error,reported_at) "
                "VALUES(?,?,'blocked',?,?) ON CONFLICT(runner_id) DO UPDATE SET release=excluded.release,"
                "update_state=excluded.update_state,update_error=excluded.update_error",
                (runner["runner_id"], "0.4.40", error, H.now()),
            )
            found = next(f for f in MH.findings(c, api.app.state.store.settings) if f["check_id"] == "runner_outdated")
        assert error in found["detail"] and found["fix"] == error


def test_maintenance_runs_health_and_files_reports_after_the_write_transaction(environment, readers, monkeypatch):
    api, _ = built(environment)
    store, auth = api.app.state.store, api.app.state.auth
    reports = []

    def findings(c, settings):
        assert settings is store.settings and not store.writer.queue
        return [item("worktree_error"), item("push_stuck", kind=MH.BUG)]

    def file_reports(store_, auth_, settings, support_, due):
        assert store_ is store and auth_ is auth and settings is store.settings
        assert not store.writer.queue
        reports.append(due)
        return {MH.signature("push_stuck"): {"ticket": "synthetic"}}

    monkeypatch.setattr(MH, "findings", findings)
    monkeypatch.setattr(MH, "file_reports", file_reports)
    for trigger in ("run_now", "schedule"):  # both ways a run starts, each on a clean install
        _fresh(api)
        reports.clear()
        with store.transaction() as c:
            M.save_step(c, "human:test", {"key": "check_health", "enabled": True})
            c.execute("UPDATE maintenance_steps SET cursor_at=NULL WHERE key='check_health'")
        if trigger == "run_now":
            record, started = M.run_now(store, auth, "human:test", step="check_health", wait=True)
            assert started
        else:
            monkeypatch.setattr(
                M, "_start", lambda store, auth, run_id, only, wait: M._execute(store, auth, run_id, only)
            )
            run_id = M.tick(store, auth, datetime.now(timezone.utc))
            record = M.get_run(store, run_id)
        step = next(s for s in record["steps"] if s["step"] == "check_health")
        assert record["ok"] == 1 and step["status"] == "ran" and step["found"] == 2 and step["woke"] == 1
        assert step["details"]["reports"][MH.signature("push_stuck")]["ticket"] == "synthetic"
        assert reports == [[MH.signature("push_stuck")]]
        with store.read() as c:
            assert (
                c.execute("SELECT cursor_at FROM maintenance_steps WHERE key='check_health'").fetchone()[0]
                == step["details"]["window_end"]
            )
        with store.transaction() as c:
            M.save_step(c, "human:test", {"key": "check_health", "enabled": False})
        record, _ = M.run_now(store, auth, "human:test", step="check_health", wait=True)
        assert record["steps"][0]["status"] == "off" and len(reports) == 1


def test_the_live_health_step_skips_rehearsal_and_demo_copies(environment, readers, monkeypatch):
    # Rehearsal and demo take the same `or` branch (check_health.run): one case stands for both.
    api, _ = built(environment)
    store = api.app.state.store
    monkeypatch.setattr(store.settings, "rehearsal", True)

    def unexpected(*args, **kwargs):
        raise AssertionError("A rehearsal or demo copy must not scan or send health reports")

    monkeypatch.setattr(check_health, "gather", unexpected)
    monkeypatch.setattr(MH, "file_reports", unexpected)
    record, _ = M.run_now(store, api.app.state.auth, "human:test", step="check_health", wait=True)
    (step,) = record["steps"]
    assert step["status"] == "skipped" and step["reason"] == "rehearsal or demo copy"
    with store.read() as c:
        assert c.execute("SELECT cursor_at FROM maintenance_steps WHERE key='check_health'").fetchone()[0] is None
