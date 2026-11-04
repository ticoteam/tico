"""A bot's declared settings live in bot_settings (backend/bot_settings.py): each declaration reads back as it was
written, from Python and from SQL alike, and migration 0068 moves every existing bot_config.config there without
losing a key."""
import importlib.util
import json
import random
import re
import sqlite3
from pathlib import Path

from backend import bot_settings as BS
from backend import hubdb as H
from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = {
    "harness": "claude", "runtime": "claude", "model": "claude-haiku-5-5", "reasoning_effort": "high", "host": "keeper",
    "template": "engineering-lead", "profile": "reviewer", "session": "task", "wake_preset": "token-saver",
    "shared_from": "ops", "bot_contact": "tasks", "repo_access_mode": "own", "folder": "acme", "github_login": "acme-bot",
    "compact_at": 250000, "turns_per_hour": 4, "turns_per_day": 40, "max_run_minutes": 30, "stateless": True,
    "fallback": {"harness": "codex", "model": "gpt-6-luna", "off": None}, "app_delivery": {"computer": "r1", "app": "grok"},
}
# Values of the wrong type for their column stay in `extra` under their own name, so they read back unchanged.
ODD = {"model": 5, "compact_at": "250k", "stateless": 1, "turns_per_day": True, "harness": ["claude"],
       "team": "engineering", "reports_to": "human:ana", "status": "active", "materialize": False, "notes": [1, {"a": None}]}


def boot(path):
    store = Store(Settings(db_path=path, registry_dir=FIXTURES / "registry"))
    store.initialize(seed_market=False)
    return store


def _bot(c, bot):
    c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) VALUES(?,?,?,'active','2026-01-01')",
              (bot, bot, bot))
    c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (bot,))


def _random_config(rng):
    pool = [*SETTINGS.items(), *ODD.items(), ("fallback", None), ("app_delivery", []), ("folder", None),
            ("reports_to", None), ("stateless", None), ("compact_at", None)]
    return dict(rng.sample(pool, rng.randint(0, len(pool))))


def test_a_declaration_reads_back_as_written_from_python_and_from_sql(tmp_path):
    store = boot(tmp_path / "hub.db")
    rng = random.Random(7)
    with store.transaction() as c:
        for n in range(300):
            bot = f"b{n}"
            _bot(c, bot)
            config = {**SETTINGS, **ODD} if n == 0 else _random_config(rng)
            BS.write(c, bot, config)
            wanted = dict(config)                      # a null is a value: it reads back as null
            if wanted.get("repo_access_mode") is None:  # a new bot gets the company default (the trigger)
                wanted["repo_access_mode"] = "own"
            assert BS.read(c, bot) == wanted
            assert json.loads(c.execute(BS.ONE_SQL, (bot,)).fetchone()[0]) == wanted
        row = c.execute("SELECT * FROM bot_settings WHERE bot_id='b0'").fetchone()
        assert (row["template"], row["model"], row["compact_at"], row["stateless"]) == ("engineering-lead", None, None, None)
        assert {k: json.loads(row["extra"])[k] for k in ("model", "compact_at", "stateless")} == {
            "model": 5, "compact_at": "250k", "stateless": 1}
        _bot(c, "typed")
        BS.write(c, "typed", SETTINGS)
        row = c.execute("SELECT * FROM bot_settings WHERE bot_id='typed'").fetchone()
        assert (row["compact_at"], row["stateless"], row["extra"]) == (250000, 1, "{}")
        # A bot_config row without settings reads as an empty declaration; no bot_config row reads as no row.
        _bot(c, "bare")
        assert json.loads(c.execute(BS.ONE_SQL, ("bare",)).fetchone()[0]) == {}
        assert c.execute(BS.ONE_SQL, ("nobody",)).fetchone() is None


def test_update_and_remove_keep_the_rest_of_the_declaration(tmp_path):
    store = boot(tmp_path / "hub.db")
    with store.transaction() as c:
        _bot(c, "ops")
        BS.write(c, "ops", {"model": "gpt-6-luna", "team": "engineering", "repo_access_mode": "shared"})
        BS.update(c, "ops", {"session": "task", "model": None})
        assert BS.read(c, "ops") == {"team": "engineering", "repo_access_mode": "shared", "session": "task"}
        BS.remove(c, "ops", "team", "session")
        assert BS.read(c, "ops") == {"repo_access_mode": "shared"}


def _old_shape(path):
    """A database as main before migration 0068 left it: bot_config.config holds the declaration, a trigger reads it."""
    boot(path)
    with sqlite3.connect(path) as c:
        for name, in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='bot_settings'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        c.execute("DROP TABLE bot_settings")
        c.execute("ALTER TABLE bot_config ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
        c.execute("CREATE TRIGGER old_reads_config AFTER UPDATE OF config ON bot_config BEGIN SELECT 1; END")
        c.execute("DELETE FROM schema_migrations WHERE version='0068_bot_settings'")
        rows = [("full", {**SETTINGS, **ODD}), ("empty", {}), ("broken", None), ("list", [1, 2]),
                *((f"many{n}", {"model": f"m{n}", "turns_per_day": n, "team": "ops"}) for n in range(1200))]
        for bot, config in rows:
            c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) VALUES(?,?,?,'active','2026-01-01')",
                      (bot, bot, bot))
            text = "{not json" if config is None else json.dumps(config)
            c.execute("INSERT INTO bot_config(bot,owner_id,config) VALUES(?,'ana',?)", (bot, text))
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("INSERT INTO bot_config(bot,owner_id,config) VALUES('gone','ana','{\"model\":\"x\"}')")


# A trigger or view that names bot_config and reads its `config` column: `config` as a bare word, or as a column of an alias
# (bc.config, NEW.config), never `bot_config` itself, nor a quoted 'config'.
_COLUMN_READ = re.compile(r"(?<![\w$'])config\b", re.I)


def _no_reader_of_the_column(c):
    for name, sql in c.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('trigger','view')").fetchall():
        assert not (sql and "bot_config" in sql and _COLUMN_READ.search(sql)), name


def _migration(name):
    """A migration file as a module: its name starts with a number, so it is not importable by name."""
    path = Path(BS.__file__).parent / "migrations" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0061_gives_a_team_that_0068_moves_into_bot_settings(tmp_path):
    """Migration 0061 (a team-less bot takes its manager's group) runs before 0068 on a database from before the move:
    the team it writes into the config is in bot_settings after 0068, and its team column is kept."""
    path = tmp_path / "hub.db"
    _old_shape(path)
    people = {"people": [{"id": "ana", "team": "engineering"}], "org_groups": {"engineering": {"name": "Engineering"}}}
    with sqlite3.connect(path) as c:
        # The migrations after 0061 are applied too (0067_home_merges among them): they go with it, the runner
        # refuses an unapplied migration that sorts before an applied one.
        c.execute("DELETE FROM schema_migrations WHERE version>='0061' AND version NOT LIKE 'legacy_%'")
        c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(people),))
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) "
                  "VALUES('legacy','legacy','Legacy','active','2026-01-01')")
        c.execute("INSERT INTO bot_config(bot,owner_id,config,reports_to) VALUES('legacy','ana',?,'human:ana')",
                  (json.dumps({"model": "m"}),))
    store = boot(path)
    with store.read() as c:
        assert c.execute("SELECT team FROM bot_config WHERE bot='legacy'").fetchone()[0] == "engineering"
        declared = BS.read(c, "legacy")
        assert declared["model"] == "m" and declared["team"] == "engineering"
        assert "config" not in {r[1] for r in c.execute("PRAGMA table_info(bot_config)")}
        _no_reader_of_the_column(c)


def test_the_migrations_that_read_the_column_skip_a_new_file():
    """On a new file the migrations run before any module table exists: 0043, 0061 and 0068 find no bots, bot_config or
    registry to read, so they change nothing and the store's own schema makes the tables in their new shape."""
    conn = sqlite3.connect(":memory:")
    for name in ("0043_drop_bot_legacy_columns", "0061_bot_team_from_manager", "0068_bot_settings"):
        _migration(name).up(conn)
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_a_new_file_has_no_trigger_or_view_reading_the_column(tmp_path):
    store = boot(tmp_path / "new.db")
    with store.read() as c:
        assert "config" not in {r[1] for r in c.execute("PRAGMA table_info(bot_config)")}
        _no_reader_of_the_column(c)


def _moved(c, bot):
    """The declaration after the move, less the defaults Store.initialize gives a bot that names none (profiles and
    wake_presets.ensure, as they did when the declaration lived in config)."""
    config = BS.read(c, bot)
    assert config.pop("profile", "doer") == "doer" and config.pop("wake_preset", "performance") == "performance"
    return config


def test_migration_moves_every_declaration_drops_the_column_and_runs_once(tmp_path):
    path = tmp_path / "hub.db"
    _old_shape(path)
    store = boot(path)
    with store.read() as c:
        assert "config" not in {r[1] for r in c.execute("PRAGMA table_info(bot_config)")}
        assert BS.read(c, "full") == {k: v for k, v in {**SETTINGS, **ODD}.items() if v is not None}
        assert _moved(c, "empty") == {} and _moved(c, "broken") == {} and _moved(c, "list") == {}
        assert _moved(c, "many1199") == {"model": "m1199", "turns_per_day": 1199, "team": "ops"}
        assert c.execute("SELECT count(*) FROM bot_settings").fetchone()[0] == 1204
        assert c.execute("SELECT 1 FROM bot_settings WHERE bot_id='gone'").fetchone() is None
        triggers = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert "old_reads_config" not in triggers
        assert {"repository_new_bot_default", "wake_preset_hold", "fin_view_bot_update",
                "changes_runner_bot_settings_update"} <= triggers
        _no_reader_of_the_column(c)
        # wake_presets.ensure gives every declaration its preset after the move, as it did inside config.
        assert c.execute("SELECT count(*) FROM bot_settings WHERE wake_preset IS NULL").fetchone()[0] == 0
    before = sqlite3.connect(path).execute("SELECT * FROM bot_settings ORDER BY bot_id").fetchall()
    boot(path)
    assert sqlite3.connect(path).execute("SELECT * FROM bot_settings ORDER BY bot_id").fetchall() == before


def test_an_explicit_null_reads_back_as_null_and_stateless_answers_as_before_the_move(tmp_path):
    """A null is a value, not an absent key: hubdb.stateless() takes an explicit `stateless: null` over the template's
    rule, before the move and after it alike."""
    path = tmp_path / "hub.db"
    _old_shape(path)
    config = {"template": "tico-counsel", "stateless": None, "reports_to": None}
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) "
                  "VALUES('nulls','nulls','nulls','active','2026-01-01')")
        c.execute("INSERT INTO bot_config(bot,owner_id,config) VALUES('nulls','ana',?)", (json.dumps(config),))
        text = c.execute("SELECT config FROM bot_config WHERE bot='nulls'").fetchone()[0]
    before = H.stateless(json.loads(text))
    assert H.stateless({"template": "tico-counsel"}) is True          # the rule a missing key gets
    store = boot(path)
    with store.read() as c:
        assert _moved(c, "nulls") == config
        assert json.loads(c.execute(BS.ONE_SQL, ("nulls",)).fetchone()[0]) == {**config, "profile": "doer",
                                                                              "wake_preset": "performance"}
        after = H.stateless(BS.read(c, "nulls"))
    assert before is False and after is False


def test_an_integer_past_64_bits_stays_in_extra_and_the_move_completes(tmp_path):
    """SQLite stores whole numbers from -2**63 to 2**63-1 only: a bigger one stays in `extra`, so the write and the move
    finish (an OverflowError would roll the migration back and block every start) and the value reads back exactly."""
    path = tmp_path / "hub.db"
    _old_shape(path)
    config = {"turns_per_day": 2**63, "compact_at": -2**63, "max_run_minutes": 2**63 - 1, "model": "m"}
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) "
                  "VALUES('huge','huge','huge','active','2026-01-01')")
        c.execute("INSERT INTO bot_config(bot,owner_id,config) VALUES('huge','ana',?)", (json.dumps(config),))
    store = boot(path)
    with store.transaction() as c:
        row = c.execute("SELECT turns_per_day, compact_at, max_run_minutes FROM bot_settings WHERE bot_id='huge'").fetchone()
        assert tuple(row) == (None, -2**63, 2**63 - 1)
        assert _moved(c, "huge") == config
        assert type(json.loads(c.execute(BS.ONE_SQL, ("huge",)).fetchone()[0])["turns_per_day"]) is int
        _bot(c, "fresh")
        BS.write(c, "fresh", {"turns_per_day": 2**63, "compact_at": 2**63 - 1})
        assert BS.read(c, "fresh") == {"turns_per_day": 2**63, "compact_at": 2**63 - 1, "repo_access_mode": "own"}
        assert json.loads(c.execute(BS.ONE_SQL, ("fresh",)).fetchone()[0])["turns_per_day"] == 2**63
