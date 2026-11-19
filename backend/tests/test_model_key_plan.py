"""Every runner polls `GET runner-model-credentials` (backend/model_subscriptions.py keys_for) and every claim reads the
bot's own key grants (model_subscriptions.for_claim): since D11-4 both read the key from computer_subscriptions ->
model_subscriptions -> credentials, and each step must seek by key, whatever sqlite_stat1 says. The Computers page reads
every computer's key in one pass (key_listing). Timings before and after: scripts/benchmark_model_keys.py."""

import importlib
import sqlite3

import pytest

from backend import model_subscriptions
from backend.config import Settings
from backend.credentials import Vault
from backend.store import Store
from scripts.benchmark_model_keys import NOW, seed

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
        for sql in dict.fromkeys(s for s in seen if s.lstrip().upper().startswith("SELECT"))
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
def test_a_bots_key_grants_and_a_keys_subscription_are_sought_by_index(tmp_path, stats):
    c, _, runners, bots = hub(tmp_path, stats)
    found = plans(c, lambda: model_subscriptions.for_claim(c, {"id": runners[0]}, {"runtime": "claude"}, bot=bots[1]))
    [grants] = [p for sql, p in found.items() if "FROM credential_grants g" in sql]
    assert any(p.startswith("SEARCH g USING INDEX credential_grants_subject (subject=?)") for p in grants), grants
    assert not [p for p in grants if p.startswith("SCAN")], grants
    by_key = c.execute("EXPLAIN QUERY PLAN SELECT id FROM model_subscriptions WHERE credential_id=?", ("x",))
    assert [r[3] for r in by_key] == [
        "SEARCH model_subscriptions USING INDEX model_subscriptions_credential (credential_id=?)"
    ]


@pytest.mark.parametrize("stats", [None, STALE], ids=["analysed", "stale"])
def test_the_computers_page_reads_every_key_in_one_pass(tmp_path, stats):
    """One read for every computer's key, whatever the number of computers: a scan of the given rows (the page lists
    them all), each joined by key, and the sign-out mark sought per row."""
    c, _, runners, _ = hub(tmp_path, stats)
    found = plans(c, lambda: model_subscriptions.key_listing(c))
    listing = model_subscriptions.key_listing(c)
    assert set(listing) == set(runners)
    [key] = [p for sql, p in found.items() if "FROM computer_subscriptions g" in sql]
    assert any(p.startswith("SEARCH s USING INDEX sqlite_autoindex_model_subscriptions_1") for p in key), key
    assert any(p.startswith("SEARCH cr USING INDEX sqlite_autoindex_credentials_1") for p in key), key
    assert any(p.startswith("SEARCH r USING INDEX sqlite_autoindex_runners_1") for p in key), key
    assert any(p.startswith("SEARCH m USING COVERING INDEX sqlite_autoindex_model_login_cleared_1") for p in key), key
    assert [p for p in key if p.startswith("SCAN")] in (
        ["SCAN g"],
        ["SCAN g USING INDEX sqlite_autoindex_computer_subscriptions_1"],
    ), key
