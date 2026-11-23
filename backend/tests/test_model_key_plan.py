"""Every runner polls `GET runner-model-credentials` (backend/model_subscriptions.py keys_for) and every claim reads the
agent's selected payer (model_subscriptions.for_claim). Lookups by computer or agent must seek by key, whatever
sqlite_stat1 says. The Computers page reads
every computer's key in one pass (key_listing). Timings before and after: scripts/benchmark_model_keys.py."""

import importlib
import sqlite3

import pytest

from backend import model_subscriptions
from backend.config import Settings
from backend.credentials import Vault
from backend.store import Store
from scripts.benchmark_model_keys import NOW, seed, seed_agent_payers

MOVE = importlib.import_module("backend.migrations.20261117061243_computer_keys_to_subscriptions")
DROP = importlib.import_module("backend.migrations.20261117085930_drop_computer_keys")

# Stats from when the install was small (analysed with a few rows): a scan looks as cheap as a seek.
STALE = [
    ("computer_subscriptions", None, "2"),
    ("computer_subscriptions", "sqlite_autoindex_computer_subscriptions_1", "2 1 1"),
    ("model_subscriptions", None, "2"),
    ("credentials", None, "2"),
    ("credential_grants", None, "3"),
    ("credential_grants", "credential_grant_active", "3 1 1"),
    ("credential_grants", "credential_grants_subject", "3 2"),
    ("bot_config", None, "1"),
]


def hub(tmp_path, stats):
    path = tmp_path / "hub.sqlite"
    store = Store(Settings(db_path=path))
    store.initialize(seed_market=False)
    vault = Vault(store)
    with store.transaction() as c:
        runners, bots = seed(c, vault, computers=10, subscriptions=20, bots=20)
        MOVE.up(c, NOW)
        DROP.up(c, NOW)
        seed_agent_payers(c, runners, bots)
    with sqlite3.connect(path) as c:
        c.execute("ANALYZE")
        if stats is not None:
            c.execute("DELETE FROM sqlite_stat1")
            c.executemany("INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES(?,?,?)", stats)
    c = sqlite3.connect(path)  # a fresh connection loads the stats
    c.row_factory = sqlite3.Row
    return c, vault, runners, bots


def plans(c, call):
    seen = []
    c.set_trace_callback(seen.append)
    try:
        call()
    finally:
        c.set_trace_callback(None)
    return {
        sql: [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("x",) * sql.count("?"))]
        for sql in dict.fromkeys(s for s in seen if s.lstrip().upper().startswith(("SELECT", "WITH")))
    }


@pytest.mark.parametrize("stats", [None, STALE], ids=["analysed", "stale"])
def test_the_key_poll_seeks_the_computers_key_and_scans_nothing(tmp_path, stats):
    c, vault, runners, _ = hub(tmp_path, stats)
    reply, _ = model_subscriptions.keys_for(c, vault, runners[0], "claude")  # the computer's own key
    assert reply["credentials"][0]["source"] == "computer"
    found = plans(c, lambda: model_subscriptions.keys_for(c, vault, runners[-1], "claude"))  # the shared key
    [key] = [p for sql, p in found.items() if "FROM computer_subscriptions g" in sql]
    assert any(
        "SEARCH g USING INDEX sqlite_autoindex_computer_subscriptions_1 (runner_id=? AND runtime=?)" in p for p in key
    ), key
    assert any(p.startswith("SEARCH s USING INDEX sqlite_autoindex_model_subscriptions_1") for p in key), key
    assert any(p.startswith("SEARCH cr USING INDEX sqlite_autoindex_credentials_1") for p in key), key
    assert not [p for plan in found.values() for p in plan if p.startswith("SCAN")], found


@pytest.mark.parametrize("stats", [None, STALE], ids=["analysed", "stale"])
def test_a_bots_payer_is_sought_by_agent_and_provider_indexes(tmp_path, stats):
    c, _, runners, bots = hub(tmp_path, stats)
    runner = {"id": runners[0], "capabilities": '["attempt_credentials_v1"]'}
    config = {"runtime": "claude"}
    assert model_subscriptions.for_claim(c, runner, config, bot=bots[1])[0]["subscription"]["kind"] == "anthropic_key"
    found = plans(c, lambda: model_subscriptions.for_claim(c, runner, config, bot=bots[1]))
    [payers] = [p for sql, p in found.items() if "WITH chosen AS MATERIALIZED" in sql]
    assert any(p.startswith("SEARCH p USING INDEX sqlite_autoindex_agent_subscriptions_1") for p in payers), payers
    assert any(p.startswith("SEARCH s USING INDEX sqlite_autoindex_model_subscriptions_1") for p in payers), payers
    assert any(p.startswith("SEARCH s USING COVERING INDEX model_subscriptions_pool") for p in payers), payers
    assert not [p for p in payers if p.startswith(("SCAN p", "SCAN s"))], payers
    by_key = c.execute("EXPLAIN QUERY PLAN SELECT id FROM model_subscriptions WHERE credential_id=?", ("x",))
    assert [r[3] for r in by_key] == [
        "SEARCH model_subscriptions USING INDEX model_subscriptions_credential (credential_id=?)"
    ]


@pytest.mark.parametrize("stats", [None, STALE], ids=["analysed", "stale"])
def test_the_computers_page_reads_every_local_key_in_one_pass(tmp_path, stats):
    """One read for the computers' local keys, with each provider and credential sought by id."""
    c, _, runners, _ = hub(tmp_path, stats)
    found = plans(c, lambda: model_subscriptions.key_listing(c))
    listing = model_subscriptions.key_listing(c)
    assert set(listing) == set(runners[: len(runners) * 4 // 5])
    [key] = [p for sql, p in found.items() if "FROM computer_subscriptions g" in sql]
    assert any(p.startswith("SEARCH s USING INDEX sqlite_autoindex_model_subscriptions_1") for p in key), key
    assert any(p.startswith("SEARCH cr USING INDEX sqlite_autoindex_credentials_1") for p in key), key
    assert [p for p in key if p.startswith("SCAN")] in (
        ["SCAN g"],
        ["SCAN g USING INDEX sqlite_autoindex_computer_subscriptions_1"],
    ), key
