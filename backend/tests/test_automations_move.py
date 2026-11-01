"""The one-time move to Automations at startup (backend/redesign_migration.py): the cutover every install runs on its
first start, the report an operator reads before and after, and the read-only check beside it. docs/updates.md,
"Moving to Automations (one time)"."""
import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from backend import redesign_migration as M
from backend.store import H, Store
from backend.tests.test_api import api, api_settings, get  # noqa: F401  (api: the fixture)

ROOT = Path(__file__).resolve().parents[2]


def close(store):
    for c in store.pool:
        c.close()
    store.pool.clear()


def old_install(tmp_path, *, routines=True):
    """An install as the release before left it: a routine, a wake rule and an hourly limit, and no cutover record.
    Its server stopped cleanly, so there is no -wal beside it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = api_settings(tmp_path)
    store = Store(settings)
    store.initialize(seed_market=False)
    with store.transaction() as c:
        H.sync_registry(c, {"coo": {"name": "coo", "runtime": "fake", "status": "active"}},
                        {"people": [{"id": "ana", "email": "ana@acme.example"}]})
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('people',?)", (json.dumps({"people": [
            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]}]}),))
        c.execute("INSERT OR IGNORE INTO bot_config(bot, config) VALUES('coo', '{}')")
        if routines:
            c.execute("INSERT INTO schedules(id, bot, cron, title, playbook, next_due_at) VALUES(?,?,?,?,?,?)",
                      ("sch_notes", "coo", "0 9 * * *", "Morning notes", "Post yesterday's work.",
                       "2026-11-02T09:00:00Z"))
            c.execute("INSERT INTO rules(id, scope_kind, scope_id, event, then_outcome, created_by, created_at, updated_at) "
                      "VALUES('rule_comment','bot','coo','comment','wake','human:ana','2026-10-01','2026-10-01')")
            c.execute("INSERT INTO usage_limits(bot, hourly_usd) VALUES('coo', 0.35)")
        c.execute("DELETE FROM registry_metadata WHERE key=?", (M.CUTOVER,))
        c.execute("DELETE FROM redesign_migrated")
    close(store)
    return settings


def files(path):
    """Every file of the database (main, -wal, journal) by name, with its hash. Not -shm: it is SQLite's shared-memory
    index, which every reader updates, and connections leave it on disk at close (hubdb.leave_wal_for_litestream)."""
    path = Path(path)
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.parent.glob(path.name + "*"))
            if not p.name.endswith("-shm")}


def start(settings, **changes):
    store = Store(replace(settings, **changes))
    store.initialize(seed_market=False)
    return store


def read(store, sql, args=()):
    with store.read() as c:
        return [dict(r) for r in c.execute(sql, args)]


def test_a_database_a_server_left_with_a_wal_is_read_from_a_copy_and_neither_file_changes(tmp_path):
    settings = old_install(tmp_path)
    # A server that stopped without closing: its last writes are still in the -wal.
    crashed = tmp_path / "crashed"
    crashed.mkdir()
    live = sqlite3.connect(settings.db_path)
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO schedules(id, bot, cron, title, playbook) VALUES('sch_wal','coo','0 8 * * *','In the wal','x')")
    live.commit()
    for name in ("hub.db", "hub.db-wal"):
        shutil.copyfile(tmp_path / name, crashed / name)
    live.close()
    before = files(crashed / "hub.db")
    assert set(before) == {"hub.db", "hub.db-wal"}
    assert M.waiting(crashed / "hub.db")["routine"] == 2       # the -wal's routine is counted
    assert files(crashed / "hub.db") == before                 # the report's read changes neither file


def test_a_new_install_and_one_with_nothing_to_move_start_without_the_setting(tmp_path):
    (tmp_path / "fresh").mkdir()
    store = start(api_settings(tmp_path / "fresh"))
    with store.read() as c:
        assert M.state(c)["state"] == "done"
    close(store)
    quiet = old_install(tmp_path / "quiet", routines=False)
    assert M.waiting(quiet.db_path) == {}
    store = start(quiet)
    with store.read() as c:
        assert M.state(c)["state"] == "done"
    close(store)


def test_an_old_install_moves_once_on_its_first_start_with_no_setting(tmp_path):
    settings = old_install(tmp_path)
    store = start(settings)
    [routine] = read(store, "SELECT * FROM redesign_migrated WHERE kind='routine'")
    assert routine["outcome"] == "converted" and routine["automation"]
    [flow] = read(store, "SELECT * FROM automations WHERE id=?", (routine["automation"],))
    assert flow["next_due"] == "2026-11-02T09:00:00Z"        # the routine's own next run, once, from one system
    assert read(store, "SELECT deleted_at FROM schedules WHERE id='sch_notes'")[0]["deleted_at"]
    assert not read(store, "SELECT 1 FROM rules WHERE id='rule_comment'")
    assert read(store, "SELECT per_30m_usd, hourly_usd FROM usage_limits WHERE bot='coo'") == [
        {"per_30m_usd": 0.2, "hourly_usd": None}]
    with store.read() as c:
        record = M.state(c)
    assert record["state"] == "done" and record["told"]
    automations = read(store, "SELECT id FROM automations ORDER BY id")
    close(store)
    for _ in range(2):                                         # moved: later starts do nothing, and nothing repeats
        store = start(settings)
        with store.read() as c:
            assert M.state(c)["migration"] == record["migration"]
        assert read(store, "SELECT id FROM automations ORDER BY id") == automations
        assert len(read(store, "SELECT 1 FROM tasks WHERE id=? UNION ALL SELECT 1 FROM messages WHERE id=?",
                        (record["told"], record["told"].removeprefix("message:")))) == 1
        close(store)


def test_the_report_reads_an_old_copy_and_writes_nothing(tmp_path, capsys):
    settings = old_install(tmp_path)
    before = files(settings.db_path)
    found = M.main(["report", "--db", str(settings.db_path)])
    assert files(settings.db_path) == before
    assert found["waiting"] == {"routine": 1, "spend_limit": 1, "wake_rule": 1}
    now = {(i["kind"], i["id"]): i["now"] for i in found["before"]["items"]}
    after = {(i["kind"], i["id"]): i["now"] for i in found["after"]["items"]}
    assert now[("routine", "sch_notes")] == "pending: would move" and after[("routine", "sch_notes")] == "moved"
    assert after[("wake_rule", "rule_comment")] == "moved"
    assert not [k for k, v in after.items() if v.startswith("pending")]
    text = capsys.readouterr().out
    assert "On the next start: moves routine 1, spend_limit 1, wake_rule 1" in text and "sch_notes" in text


def test_the_setting_is_on_by_default_and_the_environment_no_longer_sets_it(monkeypatch):
    import yaml
    from backend.config import Settings
    monkeypatch.setenv("TICO_DB", "/tmp/never-opened.db")
    for value in ("1", "0", ""):
        monkeypatch.setenv("TICO_MIGRATE_TO_AUTOMATIONS", value)
        assert Settings.from_env().migrate_to_automations is True
    server = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["server"]["environment"]
    assert "TICO_MIGRATE_TO_AUTOMATIONS" not in server
    assert "TICO_MIGRATE_TO_AUTOMATIONS" not in (ROOT / ".env.example").read_text()
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text()
    assert "redesign_migration" not in entrypoint


def test_pending_reads_a_database_an_older_release_wrote(tmp_path):
    """Plain SQL over the tables as they were: no automations table, no cutover columns."""
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT);"
                    "CREATE TABLE schedules(id TEXT PRIMARY KEY, bot TEXT, cron TEXT, title TEXT, playbook TEXT, "
                    "last_fired_at TEXT, next_due_at TEXT);"
                    "INSERT INTO schedules(id, bot) VALUES('s1', 'coo'), ('s2', 'coo');")
    c.commit()
    c.close()
    assert M.waiting(path) == {"routine": 2}
    assert M.waiting(tmp_path / "missing.db") == {}


@pytest.mark.slow
def test_the_container_starts_an_old_install_with_no_setting(tmp_path):
    """docker/entrypoint.sh `prepare` (what `server` runs first) on a database that still holds routines: it starts, and
    the install's id is written. The move itself runs in the server, at Store.initialize."""
    from backend.tests.test_replication import prepare
    settings = old_install(tmp_path / "old")
    (tmp_path / "data").mkdir()
    database = tmp_path / "data" / "hub.sqlite"
    shutil.copyfile(settings.db_path, database)
    before = files(database)
    result, _ = prepare(tmp_path, TICO_BACKUP="off")
    assert result.returncode == 0, result.stderr
    assert files(database) != before


GONE_ROUTES = (("GET", "settings/redesign"), ("PUT", "settings/redesign"), ("GET", "routines"),
               ("GET", "bots/coo/routines"), ("POST", "bots/coo/routines"), ("POST", "routines/sch_1"),
               ("POST", "routines/sch_1/delete"), ("POST", "routines/sch_1/run"), ("GET", "routines/sch_1/occurrences"),
               ("POST", "rules/r1/delete"),     # GET/POST rules and rules/{id} are org rules now (backend/org_rules.py)
               ("GET", "rules/suggestions"), ("POST", "rules/suggestions/s1/apply"),
               ("POST", "rules/suggestions/s1/dismiss"), ("POST", "redesign/migration/dry-run"),
               ("POST", "redesign/migration/resolve"), ("POST", "redesign/migration/convert"),
               ("POST", "redesign/migration/restore"))
GONE_TOOLS = ("hub_routine_list", "hub_routine_set", "hub_routine_update", "hub_routine_delete", "hub_routine_run",
              "hub_rule_list", "hub_rule_set", "hub_rule_update", "hub_rule_delete", "hub_rule_suggestions",
              "hub_rule_apply", "hub_rule_dismiss", "hub_redesign", "hub_redesign_migrate")


def test_the_old_systems_routes_and_tools_are_gone(api):
    from backend.tests.test_api import headers
    from clients import hubtools
    for method, path in GONE_ROUTES:
        r = api.request(method, "/api/v2/" + path, json={} if method != "GET" else None, headers=headers())
        assert r.status_code in (404, 405), (method, path, r.status_code)
    assert get(api, "automations")["automations"] is not None              # the one design answers
    names = {t["name"] for t in hubtools.TOOLS}
    assert "hub_automation_list" in names and not names.intersection(GONE_TOOLS)
    # A bot whose instructions still name an old tool is told the automation tool that does it now.
    assert hubtools.renamed_to("hub_routine_set") == "hub_automation_create"
    assert hubtools.renamed_to("hub_rule_update") == "hub_automation_update"
    assert all(new in hubtools.BY_NAME for new in hubtools.RENAMED_TOOLS.values())


def test_a_paused_bots_routine_keeps_its_switch_and_runs_again_when_the_bot_is_back(tmp_path):
    from datetime import datetime, timezone
    from backend import automations
    settings = old_install(tmp_path)
    store = Store(settings)
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='coo'")
    close(store)
    store = start(settings)
    [routine] = read(store, "SELECT automation FROM redesign_migrated WHERE kind='routine'")
    aid = routine["automation"]
    assert read(store, "SELECT enabled FROM automations WHERE id=?", (aid,)) == [{"enabled": 1}]
    runs = "SELECT 1 FROM automation_runs WHERE automation_id=?"
    automations.tick(store, at=datetime(2026, 11, 2, 9, 1, tzinfo=timezone.utc))      # due while the bot is paused
    assert not read(store, runs, (aid,))
    assert read(store, "SELECT next_due_at FROM automations WHERE id=?", (aid,))[0]["next_due_at"] > "2026-11-02T09:01"
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='coo'")
    automations.tick(store, at=datetime(2026, 11, 3, 9, 1, tzinfo=timezone.utc))      # back: the next one runs
    assert len(read(store, runs, (aid,))) == 1
    close(store)
