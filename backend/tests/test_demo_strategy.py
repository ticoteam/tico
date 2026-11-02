"""Demo mode's Strategy section: a published strategy with an earlier version to compare, three bets linked to goals, one
Strategy Bot proposal waiting for the owner, and this week's briefing, the market map, listening news and the brand book.
Read as the owner, the way the page reads them."""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend import hubdb as H
from backend import strategy
from backend.app import create_app
from backend.market_briefing import this_week

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)
NEXT_YEAR = datetime(2027, 11, 2, 18, 30, tzinfo=timezone.utc)
FIXED_SECTIONS = ("serve", "sell", "know", "how", "not")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-strategy"), now=NOW)


@pytest.fixture(scope="module")
def built_next_year(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-strategy-2027"), now=NEXT_YEAR)


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
        "summary", "sections.serve", "sections.sell", "sections.proof", "sections.how", "sections.not"]
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
    tables = ("strategy_versions", "strategy_bets", "strategy_events", "market_briefings", "goal_proposals",
              "market_sizes", "market_insights", "brand_versions")

    def rows(path):
        with sqlite3.connect(path) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in tables}

    again = demo.build(tmp_path, now=NOW)
    assert rows(again.db_path) == rows(built.db_path)


def parse(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def test_a_check_day_is_the_servers_calendar_day(monkeypatch):
    """A bet's check falls due on the day the server's own calendar shows. 01:30 UTC on the 3rd is 17:30 on the 2nd in
    Los Angeles, so a server there does not trip a bet a day early."""
    if not os.path.exists("/usr/share/zoneinfo/America/Los_Angeles"):
        pytest.skip("no zone database on this machine")
    monkeypatch.setattr(H, "now", lambda: "2026-11-03T01:30:00.000000Z")
    saved = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        assert strategy.local_day() == "2026-11-02"
        os.environ["TZ"] = "UTC"
        time.tzset()
        assert strategy.local_day() == "2026-11-03"
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


def test_the_owners_card_for_the_proposal_is_dated_when_it_was_proposed(built):
    with sqlite3.connect(built.db_path) as db:
        row = db.execute(
            "SELECT t.owner_id, t.status, t.created_at, p.proposed_at FROM goal_asks g JOIN tasks t ON t.id = g.task_id "
            "JOIN goal_proposals p ON p.id = g.ref WHERE g.kind = 'proposal' AND p.kind = 'strategy_draft' "
            "AND p.status = 'pending'").fetchone()
    assert row is not None
    owner, status, created, proposed = row
    assert owner == "human:ana" and status in H.LIVE_STATUSES
    assert parse(proposed) <= parse(created) < parse(proposed) + timedelta(minutes=1)
    assert parse(created) > NOW - timedelta(days=2)


def test_where_we_play_has_a_market_with_us_next_and_sized_segments(built):
    with signed_in(built) as api:
        spend = api.get("/api/v2/market/spend").json()
    assert spend["definition"]["root"] == "segment/holiday-light-shows"
    assert spend["year"] == spend["definition"]["year"] == NOW.year
    urls = [e["source_url"] for seg in spend["segments"] for row in seg["estimates"] for e in row["evidence"]]
    assert urls and all(f"-{NOW.year}" in url for url in urls)
    segments = {seg["name"]: seg for seg in spend["segments"]}
    assert {"Home light shows", "Shops and hotels", "Towns, malls and HOAs", "Event venues"} <= set(segments)
    assert segments["Home light shows"]["self"] and segments["Shops and hotels"]["self"]
    assert segments["Towns, malls and HOAs"]["next"] and segments["Event venues"]["next"]
    sized = [seg for seg in spend["segments"] if seg["size"]]
    assert len(sized) >= 2 and all(seg["size"]["accepted"] for seg in sized)
    assert spend["total"]["spend_usd"] > 0
    assert "Lumenhaus" in {player["name"] for player in segments["Home light shows"]["players"]}


def test_related_news_lists_listening_finds_from_the_last_week(built):
    with signed_in(built) as api:
        insights = api.get("/api/v2/market/insights").json()["insights"]
    finds = [i for i in insights if i["about"].startswith("Listening:") and i["status"] != "rejected"]
    recent = [i for i in finds if parse(i["reported_at"]) >= NOW - timedelta(days=7)]
    assert {i["about"] for i in recent} >= {"Listening: rival", "Listening: market"}
    assert all(i["source_url"].startswith("https://") for i in recent)


def test_the_brand_book_is_published_by_the_owner(built):
    with signed_in(built) as api:
        published = api.get("/api/v2/brand").json()["published"]
    assert published["n"] == 1 and published["published_by"] == "human:ana"
    assert published["body"]["name"] == "Acme Lights"
    assert published["body"]["words"]["avoid"] and published["body"]["claims"]["never"]


def test_a_build_in_another_year_dates_the_market_and_its_sources_to_that_year(built_next_year):
    with signed_in(built_next_year) as api:
        spend = api.get("/api/v2/market/spend").json()
        insights = api.get("/api/v2/market/insights").json()["insights"]
    assert spend["year"] == spend["definition"]["year"] == 2027
    listening = [i for i in insights if i["about"].startswith("Listening:")]
    urls = [e["source_url"] for seg in spend["segments"] for row in seg["estimates"] for e in row["evidence"]]
    urls += [i["source_url"] for i in listening]
    dated = [url for url in urls if "2026" in url or "2027" in url]
    assert dated and all("-2027" in url for url in dated)
    assert any("2027" in i["quote"] for i in listening)
    assert not any("2026" in i["quote"] + i["claim"] for i in listening)
