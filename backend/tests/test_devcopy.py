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
from backend.store import H, Problem, Store

OLD = "2020-01-01T00:00:00.000000Z"


def source_db(tmp_path):
    store = Store(Settings(db_path=tmp_path / "live" / "hub.sqlite"))
    store.initialize(seed_market=False)
    now = H.now()
    with store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('morgan','Morgan','morgan@acme.example')")
        c.execute("INSERT INTO bots(slug,token_hash,state) VALUES('sales','botsecret','active')")
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created_at) VALUES('r1','mac','morgan','rh',?)", (now,))
        c.execute("INSERT INTO enrollments(code_hash,operator,expires_at) VALUES('ch','morgan',?)", (now,))
        c.execute("INSERT INTO conversations(id) VALUES('cv')")
        for mid, at in (("old", OLD), ("new", now), ("ran", now), ("ran-long-ago", OLD)):
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created_at) "
                      "VALUES(?,'cv','human:morgan','bot:sales','ask',?,?)", (mid, "Request " + mid, at))   # the trigger queues a job
        c.execute("UPDATE jobs SET state='completed' WHERE id IN ('ran','ran-long-ago')")
        # A run in progress, a finished run in the window, and a finished run from long ago.
        for aid, job, state, at, finished in (("live", "new", "running", now, None), ("done", "ran", "completed", now, now),
                                              ("gone", "ran-long-ago", "completed", OLD, OLD)):
            c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created_at,finished_at) "
                      "VALUES(?,?,'sales','r1',1,?,?,?,?,?)", (aid, job, "hash-" + aid, state, now, at, finished))
            c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,1,'text','{}',?)",
                      (aid, at))
            c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, job))
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
        assert c.execute("SELECT token_hash FROM bots").fetchall() == [(None,)]
        assert c.execute("SELECT id FROM messages ORDER BY id").fetchall() == [("new",), ("ran",)]
        # The finished run stays, with its steps and inputs, and no way back in.
        assert c.execute("SELECT id,token_hash,lease_until=finished_at FROM attempts").fetchall() == [
            ("done", "devcopy-revoked:done", 1)]
        assert c.execute("SELECT id,state FROM jobs").fetchall() == [("ran", "completed")]
        assert c.execute("SELECT attempt_id FROM attempt_events").fetchall() == [("done",)]
        assert c.execute("SELECT attempt_id FROM attempt_inputs").fetchall() == [("done",)]
        assert c.execute("SELECT token_hash,revoked_at IS NOT NULL FROM runners").fetchall() == [("devcopy-revoked:r1", 1)]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='queue_bot_message'").fetchone()[0] == 1
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
    for source in Path(devcopy.__file__).parent.glob("*.py"):         # tables a module creates when it starts
        names |= set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+) ?\(", source.read_text())) - virtual
    groups = (devcopy.KEEP, set(devcopy.HISTORY), set(devcopy.EMPTY))
    assert {t: n for t in names if (n := sum(t in g for g in groups)) != 1} == {}


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
        c.execute("INSERT INTO automations(id,name,owner,trigger_type,enabled,flow,version,created_at,created_by,"
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
