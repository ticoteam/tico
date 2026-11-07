"""Demo segments: every segment screen has something in it. Read as the owner, the way the pages read them: each segment's
teammates and leads (the shared specialists are tagged `all`), the goal and the budget that serve each business
segment, the customers' segments and the review list, the P&L by segment, the market compare with this week's segment
briefing in What changed, and a Product roadmap task in Measured next to a task tagged with a segment. The expected
values come from the content the builder posts."""

import sqlite3
from datetime import datetime, timezone

import pytest
import time_machine
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.demo_sections.finance import CONTENT as FC
from backend.demo_sections.strategy import CONTENT as SC

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)
HOME, SHOPS, TOWNS, VENUES = (
    "segment/home-light-shows",
    "segment/shops-hotels",
    "segment/towns-malls",
    "segment/event-venues",
)
BUSINESS = (HOME, SHOPS, TOWNS)  # the segments with a goal and a budget each


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return demo.build(tmp_path_factory.mktemp("demo-segments"), now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def tagged_cents(segment, sign):
    """The cents the entries tag to a segment: credits (revenue, negative in the journal) when sign is -1, debits (costs,
    positive) when sign is 1."""
    return sum(
        abs(amount)
        for entry in FC["segment_entries"]
        for _code, amount, _text, *tags in entry["lines"]
        if tags and tags[0].get("segment") == segment and amount * sign > 0
    )


def test_every_segment_lists_its_teammates_and_leads(built):
    with signed_in(built) as api:
        listed = api.get("/api/v2/segments").json()["segments"]
        detail = {s["id"]: api.get(f"/api/v2/segments/{s['id']}").json() for s in listed}
    assert {s["id"] for s in listed} == {HOME, SHOPS, TOWNS, VENUES}
    for segment, members in SC["team"].items():
        assert {face["ref"] for face in detail[segment]["faces"]} == set(members), segment
    assert all(detail[segment]["leads"] for segment in detail)
    assert [lead["ref"] for lead in detail[HOME]["leads"]] == ["bot:sales-lead"]
    # Cara leads shops and hotels: her marketing lead and content bot report to her there, so she is the one lead.
    assert [lead["ref"] for lead in detail[SHOPS]["leads"]] == ["person:cara"]
    assert {lead["ref"] for lead in detail[TOWNS]["leads"]} == {"bot:bids", "bot:partners"}


def test_the_shared_specialists_are_tagged_all(built):
    with sqlite3.connect(built.db_path) as db:
        tagged = {row[0] for row in db.execute("SELECT member FROM segment_members WHERE segment_id = 'all'")}
    assert tagged == set(SC["shared"])


def test_each_business_segment_has_a_goal_and_a_budget(built):
    with signed_in(built) as api:
        goals = [g for g in api.get("/api/v2/goals", params={"all": "true"}).json()["goals"] if g.get("segment_id")]
        budgets = [b for b in api.get("/api/v2/finance/budgets").json()["budgets"] if b.get("segment_id")]
    assert {g["segment_id"]: g["status"] for g in goals} == {
        spec["segment"]: spec["status"] for spec in SC["segment_goals"]
    }
    assert sorted(b["segment_id"] for b in budgets) == sorted(BUSINESS)


def test_three_customers_have_a_segment_set_by_a_person_and_two_guesses_for_review(built):
    names = {cu["key"]: cu["name"] for cu in FC["customers"]}
    person = {names[key]: segment for key, who, segment in FC["customer_segments"] if who.startswith("human:")}
    guesses = {names[key] for key, who, _segment in FC["customer_segments"] if who.startswith("bot:")}
    with signed_in(built) as api:
        contacts = {c["name"]: c for c in api.get("/api/v2/finance/contacts").json()["contacts"]}
        review = api.get("/api/v2/finance/contacts/segment-review").json()["contacts"]
    assert {name: contacts[name]["segment_id"] for name in person} == person
    assert all(contacts[name]["segment_sure"] for name in person)
    assert len(guesses) == 2
    assert {c["name"] for c in review} == guesses
    assert not any(c["segment_sure"] for c in review)


def test_the_segment_pnl_has_revenue_and_costs_for_each_segment_and_unallocated(built):
    with signed_in(built) as api:
        pnl = api.get("/api/v2/finance/reports/pnl_by", params={"by": "segment"}).json()
    assert {HOME, SHOPS, TOWNS, VENUES, "unallocated"} <= {column["key"] for column in pnl["columns"]}
    rows = {row["key"]: row["values"] for row in pnl["rows"]}
    for segment in (HOME, SHOPS, TOWNS, VENUES):
        assert rows["income"][segment] == tagged_cents(segment, -1) > 0, segment
        assert rows["expense"][segment] == tagged_cents(segment, 1) > 0, segment
    assert rows["income"]["unallocated"] > 0
    assert rows["expense"]["unallocated"] > 0


def test_the_market_compare_has_money_growth_our_share_and_the_segment_briefing_in_what_changed(built):
    with signed_in(built) as api:
        compare = api.get("/api/v2/market/segments/compare").json()
    by_id = {s["id"]: s for s in compare["segments"]}
    for segment in (HOME, SHOPS, TOWNS, VENUES):
        assert by_id[segment]["money"] and by_id[segment]["growth"] is not None, segment
    for figure in SC["segment_figures"]:
        spend = by_id[figure["segment"]]["yearly_spend"]
        assert by_id[figure["segment"]]["our_share"] == round(figure["value"] / spend * 100, 1)
    briefing = SC["segment_briefing"]
    items = {c["text"] for c in compare["changed"] if c["segment_id"] == briefing["segment"]}
    assert items == {text for _kind, text in briefing["items"]}


def test_a_product_roadmap_task_is_measured_and_a_task_is_tagged_with_its_segment(built):
    with signed_in(built) as api:
        roadmap = api.get("/api/v2/tasks", params={"type": "product-roadmap"}).json()["tasks"]
        tagged = api.get("/api/v2/tasks", params={"segment": TOWNS}).json()["tasks"]
    assert [(task["title"], task["step"]["name"]) for task in roadmap] == [(SC["roadmap"]["title"], "Measured")]
    assert [task["title"] for task in tagged] == [SC["bid"]["title"]]
