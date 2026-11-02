"""Demo mode's Strategy section: a published strategy with an earlier version to compare, three bets linked to goals, one
Strategy Bot proposal waiting for the owner, and this week's briefing. Read as the owner, the way the page reads them."""

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.market_briefing import this_week

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)
FIXED_SECTIONS = ("serve", "sell", "know", "how", "not")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-strategy"), now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def test_the_published_strategy_has_three_active_bets_and_its_sections(built):
    with signed_in(built) as api:
        strategy = api.get("/api/v2/strategy").json()
    version = strategy["current"]
    assert version["n"] == 2
    assert version["guide"] == "default"
    assert version["vision"]
    assert all(version["sections"][key].strip() for key in FIXED_SECTIONS)
    active = [bet for bet in strategy["bets"] if bet["status"] == "active"]
    assert len(active) == 3
    for bet in active:
        assert bet["why"] and bet["rules_out"]
        assert bet["wrong_if"]["kind"] == "text" and bet["wrong_if"]["check_by"] > NOW.date().isoformat()
    assert strategy["draft"] is not None and strategy["may_edit"] is True


def test_an_earlier_version_and_a_diff_show_the_change(built):
    with signed_in(built) as api:
        versions = api.get("/api/v2/strategy/versions").json()
        diff = api.get("/api/v2/strategy/diff?from=1&to=2").json()
    assert [v["n"] for v in versions] == [2, 1]
    assert [field["key"] for field in diff["fields"]] == [
        "summary", "sections.serve", "sections.sell", "sections.how", "sections.not"]
    assert [bet["statement"] for bet in diff["bets"]["added"]] == [
        "Business installs are the margin; homes are the volume and the brand."]


def test_bets_serve_the_goals_the_core_seed_made(built):
    with signed_in(built) as api:
        strategy = api.get("/api/v2/strategy").json()
        goals = {g["id"]: g for g in api.get("/api/v2/goals?all=true").json()["goals"]}
    active = [bet["id"] for bet in strategy["bets"] if bet["status"] == "active"]
    assert all(strategy["goals_by_bet"].get(bet_id) for bet_id in active)
    for bet_id, rows in strategy["goals_by_bet"].items():
        assert all(goals[row["id"]]["bet_id"] == bet_id for row in rows)


def test_the_strategy_bot_proposal_waits_for_the_owner(built):
    with signed_in(built) as api:
        strategy = api.get("/api/v2/strategy").json()
        pending = api.get("/api/v2/goal-proposals?status=pending").json()["proposals"]
    assert len(pending) == 1
    proposal = pending[0]
    assert proposal["kind"] == "strategy_draft"
    assert proposal["proposed_by"] == "bot:goal-manager"
    assert proposal["payload"]["source"] == "briefing"
    bet = proposal["payload"]["bets"][0]
    assert "id" not in bet and bet["status"] == "proposed"
    assert bet["statement"] == ("Public bids from towns and HOAs are a motion of their own, with a survey and a fixed "
                                "date.")
    assert bet["wrong_if"]["kind"] == "text" and bet["wrong_if"]["check_by"] > NOW.date().isoformat()
    assert not [b for b in strategy["bets"] if b["status"] == "proposed"]      # nothing is written until the owner confirms


def test_this_weeks_briefing_is_posted_by_the_librarian(built):
    with signed_in(built) as api:
        briefing = api.get("/api/v2/market/briefings/latest").json()["briefing"]
    assert briefing["week"] == this_week(NOW)
    assert briefing["created_by"] == "bot:librarian"
    assert len(briefing["items"]) == 3


def test_the_same_now_builds_the_same_strategy_rows(built, tmp_path):
    tables = ("strategy_versions", "strategy_bets", "strategy_events", "market_briefings", "goal_proposals")

    def rows(path):
        with sqlite3.connect(path) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in tables}

    again = demo.build(tmp_path, now=NOW)
    assert rows(again.db_path) == rows(built.db_path)
