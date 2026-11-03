"""Usage facts (backend/usage_facts.py): one append-only row per metered segment, the estimate a run recorded beside the
Gateway meter's charge, and the migration that moves turn_usage_segments into them."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend import usage_facts, usage_limits
from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the api fixture)

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0053_usage_facts.py"
SONNET = {"input_tokens": 1000, "cached_tokens": 0, "output_tokens": 100, "model": "claude-sonnet-5-5",
          "provider": "anthropic", "est_cost_usd": 0.0045, "billing": "subscription", "runtime": "claude"}
TICO = {"input_tokens": 500, "cached_tokens": 0, "output_tokens": 50, "model": "tico-general-medium-1.0",
        "provider": "tico", "est_cost_usd": 0.002, "billing": "api", "runtime": "pi", "est_cost_low": 0}


def turn(c, tid, bot="ops"):
    c.execute("INSERT INTO turns(id,bot,started_at) VALUES(?,?,?)", (tid, bot, H.now()))


def facts(c, run_id):
    return [dict(r) for r in c.execute("SELECT * FROM usage_facts WHERE run_id=? ORDER BY position", (run_id,))]


def test_a_finished_run_keeps_one_fact_per_segment_and_a_late_finish_adds_none(api):
    with api.app.state.store.transaction() as c:
        turn(c, "r1")
        usage = {**TICO, "input_tokens": 1500, "segments": [SONNET, TICO]}
        H.turn_finish(c, H.KEEPER, "r1", usage=usage)
        rows = facts(c, "r1")
        assert [(r["model"], r["billing"], r["cost_nano"]) for r in rows] == [
            ("claude-sonnet-5-5", "subscription", 4_500_000), ("tico-general-medium-1.0", "api", 2_000_000)]
        month = rows[1]["metered_at"][:7]
        assert rows[0]["meter_key"] is None and rows[1]["meter_key"] == f"gateway:{month}:tico-general-medium-1.0"
        assert rows[0]["family"] == "anthropic" and rows[0]["bot_id"] == "ops"
        # A late result for the same attempt reports again: the first report stands, nothing is counted twice.
        H.turn_finish(c, H.KEEPER, "r1", usage={**TICO, "segments": [TICO]})
        assert len(facts(c, "r1")) == 2
        assert usage_limits.spent(c, "ops", "2000-01-01", "2999-01-01", True) == pytest.approx(0.0065)
        assert usage_limits.spent(c, "ops", "2000-01-01", "2999-01-01", False) == pytest.approx(0.002)


def test_a_run_that_counted_nothing_keeps_no_fact_until_a_late_result_measures_it(api):
    with api.app.state.store.transaction() as c:
        turn(c, "r2")
        H.turn_finish(c, H.KEEPER, "r2", exit_code="interrupted", usage_reason="no_result")
        assert facts(c, "r2") == []
        assert c.execute("SELECT usage_reason FROM turns WHERE id='r2'").fetchone()[0] == "no_result"
        H.turn_finish(c, H.KEEPER, "r2", usage={**TICO, "segments": [TICO]})
        assert [r["model"] for r in facts(c, "r2")] == ["tico-general-medium-1.0"]


def test_facts_refuse_change_and_removal(api):
    with api.app.state.store.transaction() as c:
        turn(c, "r3")
        H.turn_finish(c, H.KEEPER, "r3", usage={**TICO, "segments": [TICO]})
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("UPDATE usage_facts SET cost_nano=0 WHERE run_id='r3'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("DELETE FROM usage_facts WHERE run_id='r3'")


def test_the_meter_adds_what_moved_and_is_preferred_over_the_estimate(api):
    with api.app.state.store.transaction() as c:
        turn(c, "r4")
        H.turn_finish(c, H.KEEPER, "r4", usage={**TICO, "segments": [TICO]})
        month = facts(c, "r4")[0]["metered_at"][:7]
        model = TICO["model"]
        assert usage_facts.reconciled(c, month)[model] == {
            "estimated_nano": 2_000_000, "metered_nano": None, "amount_nano": 2_000_000}
        at = H.now()
        assert usage_facts.record_meter(c, month, f"{month}-02T00:00:00.000000Z", {model: 1_500_000}, at) == 1
        assert usage_facts.record_meter(c, month, f"{month}-03T00:00:00.000000Z", {model: 1_500_000}, at) == 0
        assert usage_facts.record_meter(c, month, f"{month}-04T00:00:00.000000Z", {model: 2_100_000}, at) == 1
        # A read older than the newest kept never walks the month back.
        assert usage_facts.record_meter(c, month, f"{month}-01T00:00:00.000000Z", {model: 900_000}, at) == 0
        assert usage_facts.reconciled(c, month)[model] == {
            "estimated_nano": 2_000_000, "metered_nano": 2_100_000, "amount_nano": 2_100_000}
        # Meter facts are not runs: limits and the usage report count the estimate once.
        assert usage_limits.spent(c, "ops", "2000-01-01", "2999-01-01", True) == pytest.approx(0.002)


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0053", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_moves_segments_and_measured_legacy_runs_into_facts():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
CREATE TABLE turns (id TEXT PRIMARY KEY, bot TEXT, started_at TEXT, finished_at TEXT, task_id TEXT,
  input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER, model TEXT, provider TEXT, est_cost_usd REAL,
  billing TEXT, usage_reason TEXT);
CREATE TABLE turn_usage_segments (turn_id TEXT NOT NULL REFERENCES turns(id), position INTEGER NOT NULL,
  input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER, model TEXT, provider TEXT, est_cost_usd REAL,
  billing TEXT, runtime TEXT, harness TEXT, effort TEXT, profile TEXT, PRIMARY KEY(turn_id, position));
INSERT INTO turns VALUES ('seg', 'ops', '2026-10-31T23:50:00Z', '2026-11-01T00:10:00Z', 't1', 30, 0, 3,
  NULL, NULL, 0.25, 'api', NULL);
INSERT INTO turn_usage_segments VALUES ('seg', 0, 10, 0, 1, 'claude-sonnet-5-5', 'anthropic', 0.05, 'subscription',
  'claude', 'claude-code', 'high', 'engineering');
INSERT INTO turn_usage_segments VALUES ('seg', 1, 20, 0, 2, 'tico-general-medium-1.0', 'tico', 0.25, 'api',
  'pi', 'pi', NULL, NULL);
INSERT INTO turns VALUES ('old', 'cmo', '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', NULL, 7, 1, 2,
  'gpt-6-sol', 'openai', NULL, 'api', 'cost_unpriced');
INSERT INTO turns VALUES ('mid', 'cmo', '2026-02-01T00:00:00Z', '2026-02-01T00:01:00Z', NULL, 5, 0, 1,
  'gpt-6-sol', 'openai', 0.1, 'api', NULL);
INSERT INTO turns VALUES ('none', 'cmo', '2026-01-02T00:00:00Z', '2026-01-02T00:01:00Z', NULL, 0, 0, 0,
  NULL, NULL, 0, NULL, 'no_result');
INSERT INTO turns VALUES ('blank', 'cmo', '2025-01-02T00:00:00Z', '2025-01-02T00:01:00Z', NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL);
""")
    module = _migration()
    module.up(conn)
    module.up(conn)                                      # safe to run again: nothing doubles
    rows = [dict(r) for r in conn.execute("SELECT * FROM usage_facts ORDER BY run_id, position")]
    assert [(r["run_id"], r["position"], r["model"], r["cost_nano"]) for r in rows] == [
        ("mid", 0, "gpt-6-sol", 100_000_000), ("old", 0, "gpt-6-sol", None), ("seg", 0, "claude-sonnet-5-5", 50_000_000),
        ("seg", 1, "tico-general-medium-1.0", 250_000_000)]
    seg = rows[3]
    assert (seg["bot_id"], seg["task_id"], seg["started_at"], seg["metered_at"]) == (
        "ops", "t1", "2026-10-31T23:50:00Z", "2026-11-01T00:10:00Z")
    assert seg["meter_key"] == "gateway:2026-11:tico-general-medium-1.0" and rows[2]["meter_key"] is None
    assert rows[2]["profile"] == "engineering" and rows[2]["family"] == "anthropic"
    assert (rows[1]["input_tokens"], rows[1]["cached_tokens"], rows[1]["output_tokens"]) == (7, 1, 2)
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='turn_usage_segments'").fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM usage_facts")


def test_a_cost_becomes_exact_nanodollars_and_a_bad_one_becomes_no_cost():
    assert [usage_facts.nano(v) for v in (0.35, 0.1 + 0.2, 1234.567891, 0.0000000005, 0.0000000015, -0.5)] == [
        350_000_000, 300_000_000, 1_234_567_891_000, 0, 2, -500_000_000]
    assert [usage_facts.nano(v) for v in (None, "abc", float("inf"), float("nan"))] == [None] * 4
