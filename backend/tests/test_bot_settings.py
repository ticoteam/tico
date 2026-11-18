"""A bot's declared settings live in bot_settings (backend/bot_settings.py): each declaration reads back as it was
written, from Python and from SQL alike, and nothing reads the bot_config.config column they replaced."""

import json
import random
import re
from pathlib import Path

from backend import bot_settings as BS
from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = {
    "harness": "claude",
    "runtime": "claude",
    "model": "claude-haiku-5-5",
    "reasoning_effort": "high",
    "host": "keeper",
    "template": "engineering-manager",
    "profile": "reviewer",
    "session": "task",
    "wake_preset": "token-saver",
    "shared_from": "ops",
    "bot_contact": "tasks",
    "repo_access_mode": "own",
    "folder": "acme",
    "github_login": "acme-bot",
    "compact_at": 250000,
    "turns_per_hour": 4,
    "turns_per_day": 40,
    "max_run_minutes": 30,
    "stateless": True,
    "fallback": {"harness": "codex", "model": "gpt-6-luna", "off": None},
    "app_delivery": {"computer": "r1", "app": "grok"},
}
# Values of the wrong type for their column stay in `extra` under their own name, so they read back unchanged.
ODD = {
    "model": 5,
    "compact_at": "250k",
    "stateless": 1,
    "turns_per_day": True,
    "harness": ["claude"],
    "team": "engineering",
    "reports_to": "human:ana",
    "status": "active",
    "materialize": False,
    "notes": [1, {"a": None}],
}


def boot(path):
    store = Store(Settings(db_path=path, registry_dir=FIXTURES / "registry"))
    store.initialize(seed_market=False)
    return store


def _bot(c, bot):
    c.execute(
        "INSERT INTO bots(id,slug,display_name,state,created_at) VALUES(?,?,?,'active','2026-01-01')", (bot, bot, bot)
    )
    c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (bot,))


def _random_config(rng):
    pool = [
        *SETTINGS.items(),
        *ODD.items(),
        ("fallback", None),
        ("app_delivery", []),
        ("folder", None),
        ("reports_to", None),
        ("stateless", None),
        ("compact_at", None),
    ]
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
            wanted = dict(config)  # a null is a value: it reads back as null
            if wanted.get("repo_access_mode") is None:  # a new bot gets the company default (the trigger)
                wanted["repo_access_mode"] = "own"
            assert BS.read(c, bot) == wanted
            assert json.loads(c.execute(BS.ONE_SQL, (bot,)).fetchone()[0]) == wanted
        row = c.execute("SELECT * FROM bot_settings WHERE bot_id='b0'").fetchone()
        assert (row["template"], row["model"], row["compact_at"], row["stateless"]) == (
            "engineering-manager",
            None,
            None,
            None,
        )
        assert {k: json.loads(row["extra"])[k] for k in ("model", "compact_at", "stateless")} == {
            "model": 5,
            "compact_at": "250k",
            "stateless": 1,
        }
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


# A trigger or view that names bot_config and reads its `config` column: `config` as a bare word, or as a column of an alias
# (bc.config, NEW.config), never `bot_config` itself, nor a quoted 'config'.
_COLUMN_READ = re.compile(r"(?<![\w$'])config\b", re.I)


def _no_reader_of_the_column(c):
    for name, sql in c.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('trigger','view')").fetchall():
        assert not (sql and "bot_config" in sql and _COLUMN_READ.search(sql)), name


def test_a_new_file_has_no_trigger_or_view_reading_the_column(tmp_path):
    store = boot(tmp_path / "new.db")
    with store.read() as c:
        assert "config" not in {r[1] for r in c.execute("PRAGMA table_info(bot_config)")}
        _no_reader_of_the_column(c)


def test_an_integer_past_64_bits_stays_in_extra_and_reads_back_exactly(tmp_path):
    """SQLite stores whole numbers from -2**63 to 2**63-1 only: a bigger one stays in `extra`, so the write finishes
    and the value reads back exactly."""
    store = boot(tmp_path / "hub.db")
    with store.transaction() as c:
        _bot(c, "fresh")
        BS.write(c, "fresh", {"turns_per_day": 2**63, "compact_at": 2**63 - 1})
        row = c.execute("SELECT turns_per_day, compact_at FROM bot_settings WHERE bot_id='fresh'").fetchone()
        assert tuple(row) == (None, 2**63 - 1)
        assert BS.read(c, "fresh") == {"turns_per_day": 2**63, "compact_at": 2**63 - 1, "repo_access_mode": "own"}
        assert json.loads(c.execute(BS.ONE_SQL, ("fresh",)).fetchone()[0])["turns_per_day"] == 2**63
