"""Usage facts (backend/usage_facts.py): one append-only row per metered segment, the estimate a run recorded beside the
Gateway meter's charge."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from backend import usage_facts, usage_limits
from backend.store import H

SONNET = {
    "input_tokens": 1000,
    "cached_tokens": 0,
    "output_tokens": 100,
    "model": "claude-sonnet-5-5",
    "provider": "anthropic",
    "est_cost_usd": 0.0045,
    "billing": "subscription",
    "runtime": "claude",
}
TICO = {
    "input_tokens": 500,
    "cached_tokens": 0,
    "output_tokens": 50,
    "model": "tico-medium-1.0",
    "provider": "tico",
    "est_cost_usd": 0.002,
    "billing": "api",
    "runtime": "pi",
    "est_cost_low": 0,
}


def turn(c, tid, bot="ops"):
    c.execute("INSERT INTO attempts(id,bot_id,created_at) VALUES(?,?,?)", (tid, bot, H.now()))


def facts(c, run_id):
    return [dict(r) for r in c.execute("SELECT * FROM usage_facts WHERE run_id=? ORDER BY position", (run_id,))]


def test_a_finished_run_keeps_one_fact_per_segment_and_a_late_finish_adds_none(api):
    with api.app.state.store.transaction() as c:
        turn(c, "r1")
        usage = {**TICO, "input_tokens": 1500, "segments": [SONNET, TICO]}
        H.turn_finish(c, H.KEEPER, "r1", usage=usage)
        rows = facts(c, "r1")
        assert [(r["model"], r["billing"], r["cost_nano"]) for r in rows] == [
            ("claude-sonnet-5-5", "subscription", 4_500_000),
            ("tico-medium-1.0", "api", 2_000_000),
        ]
        month = rows[1]["metered_at"][:7]
        assert rows[0]["meter_key"] is None and rows[1]["meter_key"] == f"gateway:{month}:tico-medium-1.0"
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
        assert c.execute("SELECT usage_reason FROM attempts WHERE id='r2'").fetchone()[0] == "no_result"
        H.turn_finish(c, H.KEEPER, "r2", usage={**TICO, "segments": [TICO]})
        assert [r["model"] for r in facts(c, "r2")] == ["tico-medium-1.0"]


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
            "estimated_nano": 2_000_000,
            "metered_nano": None,
            "amount_nano": 2_000_000,
        }
        at = H.now()
        assert usage_facts.record_meter(c, month, f"{month}-02T00:00:00.000000Z", {model: 1_500_000}, at) == 1
        assert usage_facts.record_meter(c, month, f"{month}-03T00:00:00.000000Z", {model: 1_500_000}, at) == 0
        assert usage_facts.record_meter(c, month, f"{month}-04T00:00:00.000000Z", {model: 2_100_000}, at) == 1
        # A read older than the newest kept never walks the month back.
        assert usage_facts.record_meter(c, month, f"{month}-01T00:00:00.000000Z", {model: 900_000}, at) == 0
        assert usage_facts.reconciled(c, month)[model] == {
            "estimated_nano": 2_000_000,
            "metered_nano": 2_100_000,
            "amount_nano": 2_100_000,
        }
        # Meter facts are not runs: limits and the usage report count the estimate once.
        assert usage_limits.spent(c, "ops", "2000-01-01", "2999-01-01", True) == pytest.approx(0.002)


def test_a_cost_becomes_exact_nanodollars_and_a_bad_one_becomes_no_cost():
    assert [usage_facts.nano(v) for v in (0.35, 0.1 + 0.2, 1234.567891, 0.0000000005, 0.0000000015, -0.5)] == [
        350_000_000,
        300_000_000,
        1_234_567_891_000,
        0,
        2,
        -500_000_000,
    ]
    assert [usage_facts.nano(v) for v in (None, "abc", float("inf"), float("nan"))] == [None] * 4


def meter_total(c, month, model):
    return usage_facts.reconciled(c, month).get(model)


def test_a_first_meter_reading_of_zero_is_kept_and_preferred_over_the_estimate(api):
    with api.app.state.store.transaction() as c:
        turn(c, "r5")
        H.turn_finish(c, H.KEEPER, "r5", usage={**TICO, "segments": [TICO]})
        month = facts(c, "r5")[0]["metered_at"][:7]
        model = TICO["model"]
        assert usage_facts.record_meter(c, month, f"{month}-02T00:00:00.000000Z", {model: 0}, H.now()) == 1
        assert meter_total(c, month, model) == {"estimated_nano": 2_000_000, "metered_nano": 0, "amount_nano": 0}


def test_a_model_a_newer_reading_leaves_out_goes_back_to_zero(api):
    with api.app.state.store.transaction() as c:
        month, at = "2026-10", H.now()
        usage_facts.record_meter(c, month, "2026-10-02T00:00:00.000000Z", {"tico-a": 700, "tico-b": 300}, at)
        assert usage_facts.record_meter(c, month, "2026-10-03T00:00:00.000000Z", {"tico-a": 900}, at) == 2
        found = usage_facts.reconciled(c, month)
        assert (found["tico-a"]["metered_nano"], found["tico-b"]["metered_nano"]) == (900, 0)


def test_an_unchanged_reading_moves_the_newest_so_a_delayed_older_one_cannot_lower_the_total(api):
    with api.app.state.store.transaction() as c:
        month, at, model = "2026-10", H.now(), "tico-a"
        assert usage_facts.record_meter(c, month, "2026-10-15T04:00:00.000000Z", {model: 600}, at) == 1
        assert usage_facts.record_meter(c, month, "2026-10-15T06:00:00.000000Z", {model: 600}, at) == 0
        assert usage_facts.record_meter(c, month, "2026-10-15T05:00:00.000000Z", {model: 550}, at) == 0
        assert meter_total(c, month, model)["metered_nano"] == 600
        assert (
            c.execute("SELECT as_of FROM usage_meter_reads WHERE month=?", (month,)).fetchone()[0]
            == "2026-10-15T06:00:00.000000Z"
        )
        # At the same time, only a higher total replaces the read (Finance's snapshot rule).
        assert usage_facts.record_meter(c, month, "2026-10-15T06:00:00.000000Z", {model: 500}, at) == 0
        assert usage_facts.record_meter(c, month, "2026-10-15T06:00:00.000000Z", {model: 650}, at) == 1
        assert meter_total(c, month, model)["metered_nano"] == 650


def test_a_late_report_leaves_the_runs_own_usage_columns_as_its_facts_have_them(api):
    """The first measured report stands for the turn's columns too, so they never disagree with its facts."""
    with api.app.state.store.transaction() as c:
        turn(c, "r6")
        H.turn_finish(c, H.KEEPER, "r6", usage={**TICO, "input_tokens": 1500, "segments": [TICO]})
        H.turn_finish(c, H.KEEPER, "r6", usage={**SONNET, "segments": [SONNET]})
        H.turn_finish(c, H.KEEPER, "r6", exit_code="interrupted", usage_reason="no_result")
        row = c.execute(
            "SELECT input_tokens, usage_model, est_cost_usd, usage_reason FROM attempts WHERE id='r6'"
        ).fetchone()
        assert tuple(row) == (1500, TICO["model"], TICO["est_cost_usd"], None)
        assert [(r["model"], r["cost_nano"]) for r in facts(c, "r6")] == [(TICO["model"], 2_000_000)]
