"""`python -m backend.devcopy`: a dev server's copy of real data keeps the team's history but nothing that acts,
and the rehearsal it starts in refuses every runner. Synthetic databases only; a real one is never a fixture."""
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from backend import devcopy
from backend.auth import Identity
from backend.config import Settings
from backend.execution import Execution
from backend.finance import actions as finance
from backend.enqueue import enqueue_stored
from backend.store import H, Problem, Store

OLD = "2020-01-01T00:00:00.000000Z"


def source_db(tmp_path):
    store = Store(Settings(db_path=tmp_path / "live" / "hub.sqlite"))
    store.initialize(seed_market=False)
    now = H.now()
    with store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('morgan','Morgan','morgan@acme.example')")
        c.execute("INSERT INTO bots(id,slug,state) VALUES('sales','sales','active')")
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','mac','morgan','rh',?)", (now,))
        c.execute("INSERT INTO enrollments(code_hash,owner_id,expires_at) VALUES('ch','morgan',?)", (now,))
        c.execute("INSERT INTO conversations(id) VALUES('cv')")
        c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status) "
                  "VALUES('linked-task','Review the plan','human:morgan','human:morgan','open')")
        c.execute("INSERT INTO meetings(id,title,owner,metadata,created_at,updated_at) "
                  "VALUES('linked-meeting','Plan review','morgan@acme.example','{}',?,?)", (now, now))
        c.execute("INSERT INTO meeting_task_links VALUES('meeting-link','linked-meeting','linked-task',"
                  "'linked','human:morgan',?)", (now,))
        for mid, at in (("old", OLD), ("new", now), ("ran", now), ("ran-long-ago", OLD)):
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
                      "VALUES(?,'cv','human:morgan','bot:sales','ask',?,?)", (mid, "Request " + mid, at))
            enqueue_stored(c, mid)
        c.execute("UPDATE jobs SET state='completed' WHERE id IN ('ran','ran-long-ago')")
        # A run in progress, a finished run in the window, and a finished run from long ago.
        for aid, job, state, at, finished in (("live", "new", "running", now, None), ("done", "ran", "completed", now, now),
                                              ("gone", "ran-long-ago", "completed", OLD, OLD)):
            c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,finished_at) "
                      "VALUES(?,?,'sales','r1',1,?,?,?,?,?)", (aid, job, "hash-" + aid, state, now, at, finished))
            c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,1,'text','{}',?)",
                      (aid, at))
            c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, job))
        # A run recorded with no lease (migration 0072): history, kept in the window, with no token to revoke.
        c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at,trigger) VALUES('recorded','sales',?,?,'message')",
                  (now, now))
        c.execute("INSERT INTO human_tokens(id,human,label,token_hash,created_at,created_by) VALUES('t1','morgan','cli','th',?,'morgan')", (now,))
        c.execute("INSERT INTO credentials(id,name,kind,created_at,updated_at,updated_by) VALUES('k1','gh','token',?,?,'morgan')",
                  (now, now))
        c.execute("INSERT INTO schedules(id,bot,cron) VALUES('s1','sales','* * * * *')")
    for name in ("credential.key", "slack.key"):
        (store.settings.db_path.parent / name).write_bytes(b"k" * 32)
    # Close the store's idle connections now: the last one to close checkpoints the WAL into the file, which would
    # otherwise land whenever the garbage collector runs, possibly after the test has taken its "before" bytes.
    for c in store.pool:
        c.close()
    store.pool.clear()
    return store.settings.db_path


def test_a_dev_copy_keeps_history_empties_what_acts_and_refuses_runners(tmp_path):
    source = source_db(tmp_path)
    before = source.read_bytes()
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, loose = devcopy.build(source, dest, days=30)
    devcopy.write_environment(dest, 8879, "morgan@acme.example")
    assert removed["task_rules"] == 1                                  # scheduled actions never enter the copy
    assert source.read_bytes() == before                                    # the source is only read
    assert loose == [] and removed["attempts"] == 2 and removed["attempt_events"] == 2 and removed["messages"] == 2
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert not any((dest.parent / name).exists() for name in ("credential.key", "github-app.key", "slack.key"))
    with closing(sqlite3.connect(dest)) as c:
        for table in devcopy.emptied(c):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
        assert c.execute("SELECT id FROM messages ORDER BY id").fetchall() == [("new",), ("ran",)]
        # The finished run stays, with its steps and inputs, and no way back in.
        assert c.execute("SELECT id,token_hash,lease_until=finished_at FROM attempts ORDER BY id").fetchall() == [
            ("done", "devcopy-revoked:done", 1), ("recorded", None, None)]
        assert c.execute("SELECT id,state FROM jobs").fetchall() == [("ran", "completed")]
        assert c.execute("SELECT attempt_id FROM attempt_events").fetchall() == [("done",)]
        assert c.execute("SELECT attempt_id FROM attempt_inputs").fetchall() == [("done",)]
        assert c.execute("SELECT token_hash,revoked_at IS NOT NULL FROM runners").fetchall() == [("devcopy-revoked:r1", 1)]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("SELECT meeting_id,task_id FROM meeting_task_links").fetchall() == [
            ('linked-meeting', 'linked-task')]
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='message_links_insert'").fetchone()[0] == 1
    env = dict(line.split("=", 1) for line in (dest.parent / "server.env").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    assert (env["TICO_REHEARSAL"], env["TICO_DB"], env["TICO_PUBLIC_URL"]) == ("1", str(dest.resolve()), "http://127.0.0.1:8879")
    assert stat.S_IMODE((dest.parent / "server.env").stat().st_mode) == 0o600

    with pytest.raises(SystemExit, match="already exists"):            # never replaced without --force
        devcopy.build(source, dest)
    with pytest.raises(SystemExit, match="is the source"):
        devcopy.build(source, source, force=True)
    with closing(sqlite3.connect(dest)) as held:                         # a server holding it open
        held.execute("PRAGMA journal_mode=WAL")
        held.execute("SELECT 1 FROM bots").fetchall()
        with pytest.raises(SystemExit, match="in use"):
            devcopy.build(source, dest, force=True)

    store = Store(Settings(db_path=dest, rehearsal=True))
    execution = Execution(store, None)
    runner = Identity("runner:r1", "runner", runner_id="r1")
    for call in (lambda c: execution.claim(c, runner, None), lambda c: execution.heartbeat(c, runner, None),
                 lambda c: execution.enroll(None),
                 lambda c: execution.issue_enrollment(c, Identity("human:morgan", "owner"), None)):
        with pytest.raises(Problem) as refused:
            call(None)
        assert refused.value.code == "rehearsal"


def test_every_table_is_classified(tmp_path):
    """A new table fails here until someone decides whether a dev copy keeps it, cuts it to --days or empties it."""
    store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    store.initialize(seed_market=False)
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        names = devcopy.data_tables(c)
        virtual = {t for t in devcopy.tables(c) if t not in names}
    dropped = set()
    for source in Path(devcopy.__file__).parent.glob("*.py"):         # tables a module creates when it starts
        text = source.read_text()
        names |= set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+) ?\(", text)) - virtual
        dropped |= set(re.findall(r"DROP TABLE IF EXISTS ([a-z_]+);", text))     # and a migration script drops again
    for source in (Path(devcopy.__file__).parent / "migrations").glob("*.py"):  # and a migration file drops for good
        dropped |= set(re.findall(r"DROP TABLE IF EXISTS ([a-z_]+);", source.read_text()))
    names -= dropped
    groups = (devcopy.KEEP, set(devcopy.HISTORY), set(devcopy.EMPTY), set(finance.DEV_COPY_EMPTIED))
    assert {t: n for t in names if (n := sum(t in g for g in groups)) != 1} == {}


def test_devcopy_names_no_finance_table_as_one_it_empties(tmp_path):
    """Finance empties its own tables (finance.DEV_COPY_EMPTIED, in its scrub), so devcopy's emptied list has none."""
    store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
    store.initialize(seed_market=False)
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        assert [t for t in devcopy.emptied(c) if t.startswith("fin_")] == []


def test_server_env_sources_whole_with_spaces(tmp_path):
    import subprocess
    dest = tmp_path / "my copies" / "dev.sqlite"
    dest.parent.mkdir()
    env, _, _ = devcopy.write_environment(dest, 8879, "o@example.com")
    out = subprocess.run(["sh", "-c", 'TICO_DB=/live.sqlite; set -a; . "$1"; set +a; printf %s "$TICO_DB"', "sh", str(env)],
                         capture_output=True, text=True, check=True)
    assert out.stdout == str(dest)


def test_a_dev_copy_drops_deleted_tasks_requesters_and_gets_its_own_roadmap_secret(tmp_path):
    import json
    from backend import roadmaps
    from backend.task_delete import delete_tasks
    store = Store(Settings(db_path=tmp_path / "live" / "hub.sqlite"))
    store.initialize(seed_market=False)
    with store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('morgan','Morgan','morgan@acme.example')")
        task = H.task_create(c, "human:morgan", "Faster exports", "Customer asks", "human:morgan")
        roadmaps.add_requester(c, task["id"], {"email": "jane@customer.example", "phone": "555-0100", "source": "public_form"})
        assert delete_tasks(c, [task["id"]], apply=True)["applied"]
        secret = c.execute("SELECT value FROM registry_metadata WHERE key='roadmap_ip_secret'").fetchone()[0]
        assert "jane@customer.example" in c.execute("SELECT rows FROM task_trash").fetchone()[0]
    for c in store.pool:
        c.close()
    store.pool.clear()
    dest = tmp_path / "copy" / "hub.sqlite"
    devcopy.build(store.settings.db_path, dest, days=30)
    with closing(sqlite3.connect(dest)) as c:
        rows = json.loads(c.execute("SELECT rows FROM task_trash").fetchone()[0])["rows"]
        assert "task_requesters" not in rows and rows["tasks"][0]["title"] == "Faster exports"
        assert "jane@customer.example" not in json.dumps(rows) and "555-0100" not in json.dumps(rows)
        copied = c.execute("SELECT value FROM registry_metadata WHERE key='roadmap_ip_secret'").fetchone()[0]
        assert copied != secret and len(json.loads(copied)) == 64


def test_keep_automations_keeps_definitions_and_empties_their_queues_and_secrets(tmp_path):
    """`--keep-automations` (the redesign converter's rehearsal): routines, task rules, triggers, webhooks and
    automations stay; their occurrences, deliveries and runs go; no trigger URL or webhook signature still works."""
    source = source_db(tmp_path)
    with closing(sqlite3.connect(source)) as c, c:
        now = H.now()
        c.execute("INSERT INTO schedule_occurrences(schedule_id,occurrence,outcome) VALUES('s1',?,'opened')", (now,))
        c.execute("INSERT INTO triggers(id,name,kind,secret_hash,created_by,created_at,updated_at,signing_secret) "
                  "VALUES('deploy-failed','Deploy failed','webhook','live-hash','human:morgan',?,?,'live-signing')",
                  (now, now))
        c.execute("INSERT INTO trigger_deliveries(trigger_id,delivery,received_at) VALUES('deploy-failed','d1',?)", (now,))
        c.execute("INSERT INTO webhooks(id,name,url,host,secret,secret_prev,created_by,created_at,updated_at) "
                  "VALUES('wh1','Acme CI','https://ci.acme.example/hook','ci.acme.example','whsec_live','whsec_old',"
                  "'human:morgan',?,?)", (now, now))
        c.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,"
                  "updated_at) VALUES('auto_1','Morning check','person:morgan','schedule',1,'{}',1,?,'human:morgan',?)",
                  (now, now))
        c.execute("INSERT INTO automation_runs(id,automation_id,version,started_at,status) VALUES('run1','auto_1',1,?,"
                  "'queued')", (now,))
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, loose = devcopy.build(source, dest, days=30, keep_automations=True)
    devcopy.write_environment(dest, 8879, "morgan@acme.example")
    assert loose == []
    with closing(sqlite3.connect(dest)) as c:
        kept = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("schedules", "task_rules", "triggers", "webhooks", "automations")}
        assert kept == {"schedules": 1, "task_rules": 1, "triggers": 1, "webhooks": 1, "automations": 1}
        for table in ("schedule_occurrences", "trigger_deliveries", "automation_runs", "credentials", "human_tokens"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
        assert c.execute("SELECT secret_hash, signing_secret FROM triggers").fetchall() == [
            ("devcopy-revoked:deploy-failed", None)]
        assert c.execute("SELECT secret, secret_prev FROM webhooks").fetchall() == [("", "")]
    env = (dest.parent / "server.env").read_text()
    assert "TICO_REHEARSAL=1" in env and "TICO_SCHEDULER=0" in env    # still a rehearsal: nothing runs on a timer
    # Without the option the same tables are emptied, as before.
    plain, _ = devcopy.build(source, tmp_path / "plain" / "hub.sqlite", days=30)
    assert plain["schedules"] == 1 and plain["triggers"] == 1 and plain["automations"] == 1


def test_a_dev_copy_cuts_old_audit_rows_and_keeps_the_audit_log_append_only(tmp_path):
    source = source_db(tmp_path)
    with closing(sqlite3.connect(source)) as c:
        c.executemany("INSERT INTO audit_log(id,created_at,actor,object,before,after,via) VALUES(?,?,?,?,?,?,?)",
                      [("old", OLD, "human:morgan", "task_cap:1", None, "{}", None),
                       ("new", H.now(), "human:morgan", "task_cap:1", None, "{}", None)])
        c.commit()
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, _ = devcopy.build(source, dest, days=30)
    assert removed["audit_log"] == 1
    with closing(sqlite3.connect(dest)) as c:
        assert [r[0] for r in c.execute("SELECT id FROM audit_log ORDER BY id")] == ["new"]
        triggers = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"audit_log_no_update", "audit_log_no_delete"} <= triggers


def test_a_copy_of_a_database_from_before_usage_facts_keeps_what_the_migration_moves(tmp_path):
    """devcopy prunes before the copy is migrated: the tables 0053 and 0054 move from keep their rows."""
    source = source_db(tmp_path)
    with closing(sqlite3.connect(source)) as c:
        c.execute("CREATE TABLE turn_usage_segments (turn_id TEXT, position INTEGER, est_cost_usd REAL)")
        c.execute("CREATE TABLE usage_limits (bot TEXT PRIMARY KEY, daily_usd REAL)")
        c.execute("CREATE TABLE usage_pools (scope TEXT PRIMARY KEY, daily_usd REAL)")
        c.execute("INSERT INTO turn_usage_segments VALUES ('t1', 0, 0.25)")
        c.execute("INSERT INTO usage_limits VALUES ('sales', 5)")
        c.execute("INSERT INTO usage_pools VALUES ('company', 80)")
        c.commit()
    dest = tmp_path / "copy" / "hub.sqlite"
    _, loose = devcopy.build(source, dest, days=30)
    assert loose == []
    with closing(sqlite3.connect(dest)) as c:
        assert [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("turn_usage_segments", "usage_limits", "usage_pools")] == [1, 1, 1]


def test_a_copy_of_a_database_from_before_the_history_move_keeps_its_change_logs_for_the_move(tmp_path):
    """devcopy prunes before the copy is migrated: the five change logs migration 0064 moves are kept whole (their rows,
    and a deleted task's trash snapshot of them), so the move still has every row to copy into history_events."""
    import json
    from backend.tests.test_history_events import OLD, migration
    stamp = "2020-01-01T00:00:00.000000Z"
    source = source_db(tmp_path)
    snapshot = json.dumps({"rows": {"task_events": [{"_rowid": 1, "id": "t9", "task_id": "task-9", "field": "status",
                                                     "old": "open", "new": "done", "actor": "human:ana",
                                                     "created_at": stamp}]}})
    with closing(sqlite3.connect(source)) as c, c:
        # Before 0064: no history table, the five logs are there, and the migration has not run.
        c.execute("DROP TABLE history_events")
        c.execute("DELETE FROM schema_migrations WHERE version>='0064_history_events' AND version NOT LIKE 'legacy_%'")
        for statement in OLD:
            c.execute(statement)
        c.execute("INSERT INTO task_events VALUES('t1','task-1','2026-01-01T00:00:00Z','human:ana','status','open',"
                  "'done','',NULL,NULL)")
        c.execute("INSERT INTO task_events VALUES('t2','task-1',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
        c.execute("INSERT INTO goal_events(id, goal_id, created_at, actor, field, old, new, note) "
                  "VALUES('g1','goal-1',?,'human:ana','status','on_track','at_risk','')", (stamp,))
        c.execute("INSERT INTO task_trash(task_id, number, title, status, private, deleted_at, deleted_by, rows) "
                  "VALUES('task-9', 9, 'Gone', 'done', 1, ?, 'human:ana', ?)", (stamp, snapshot))
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, loose = devcopy.build(source, dest, days=30)
    assert loose == [] and not {"task_events", "goal_events", "task_trash"} & set(removed)
    with closing(sqlite3.connect(dest)) as c:
        assert c.execute("SELECT count(*) FROM task_events").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM goal_events").fetchone()[0] == 1
        assert "task_events" in json.loads(c.execute("SELECT rows FROM task_trash").fetchone()[0])["rows"]
        # The move the dev server runs on its first start takes every one of them.
        migration().up(c)
        assert [r[0] for r in c.execute("SELECT id FROM history_events WHERE subject_type='task' ORDER BY rowid")] == [
            "t1", "t2"]
        assert c.execute("SELECT subject_id, kind, created_at FROM history_events WHERE id='t2'").fetchone() == (
            "task-1", None, None)
        assert c.execute("SELECT id FROM history_events WHERE subject_type='goal'").fetchall() == [("g1",)]
        assert not {"task_events", "goal_events"} & {r[0] for r in c.execute("SELECT name FROM sqlite_master")}


def test_a_lease_less_run_is_kept_only_once_settled(tmp_path):
    """A run with no job (a chat run, or a turn migration 0072 made into an attempts row) has no lease: it is open until
    settled_at. A copy keeps the settled ones and removes the open ones, as it removes a job-backed attempt still running."""
    source = source_db(tmp_path)
    now = H.now()
    with closing(sqlite3.connect(source)) as c:
        c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at) VALUES('chat-done','sales',?,?)", (now, now))
        c.execute("INSERT INTO attempts(id,bot_id,created_at) VALUES('chat-open','sales',?)", (now,))
        c.commit()
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, loose = devcopy.build(source, dest, days=30)
    assert loose == []
    with closing(sqlite3.connect(dest)) as c:
        assert {row[0] for row in c.execute("SELECT id FROM attempts")} == {"done", "chat-done", "recorded"}
    assert removed["attempts"] == 3                                    # the running job, the old one and chat-open


def test_a_copy_keeps_no_turn_whose_attempt_the_cut_removed(tmp_path):
    """A copy from before migration 0072 keeps turns whole. A turn whose attempts row the cut removed goes with it, so
    migration 0072 does not bring the pruned run back as a lease-less one."""
    source = source_db(tmp_path)
    with closing(sqlite3.connect(source)) as c:
        c.execute("CREATE TABLE turns (id TEXT PRIMARY KEY, bot TEXT, started_at TEXT)")
        c.executemany("INSERT INTO turns VALUES(?,?,?)", [("done", "sales", H.now()), ("gone", "sales", OLD)])
        c.commit()
    dest = tmp_path / "copy" / "hub.sqlite"
    removed, loose = devcopy.build(source, dest, days=30)
    assert loose == []
    with closing(sqlite3.connect(dest)) as c:
        assert c.execute("SELECT id FROM turns").fetchall() == [("done",)]
    assert removed["turns"] == 1
