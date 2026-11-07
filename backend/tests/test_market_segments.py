"""The Market page by segment (backend/market_segments.py): the segments compared from stored rows, one segment's page,
and the Librarian's research task for one segment. Examples use Acme and Harborly."""

from collections import Counter
from datetime import datetime, timezone

from backend import bot_settings as BS
from backend.store import H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401
from backend.tests.test_bot_contact import bot as make_bot
from backend.tests.test_bot_contact import token as bot_token
from backend.tests.test_market import _librarian, _seed
from backend.tests.test_market_spend import defined, evidence, size

B = 1_000_000_000
WEEK = "2026-W44"


def ours(api, *ids):
    for sid in ids:
        post(api, "market/segments/target", {"id": sid, "target": "ours"})


def write_briefing(api, segment, items=(), answers=None):
    body = {"week": WEEK, "title": "Screening week", "body": "Page.", "items": list(items), "segment_id": segment}
    if answers is not None:
        body["answers"] = answers
    return post(api, "market/briefings", body)["briefing"]


def figure(api, entity, segment, metric, value, proof, as_of="2026-03"):
    return post(
        api,
        "market/figures",
        {
            "entity_id": entity,
            "segment_id": segment,
            "metric": metric,
            "value": value,
            "kind": "reported",
            "as_of": as_of,
            "evidence_id": proof,
        },
    )


def test_compare_reads_money_roll_up_growth_and_share_from_the_stored_rows(api, tmp_path):
    _seed(api, tmp_path)
    first, second, sub = defined(api)
    third = post(api, "market/segments", {"name": "Invoices", "parent_id": second})["segment"]["id"]
    fourth = post(api, "market/segments", {"name": "Quiet"})["segment"]["id"]
    ours(api, first, second, fourth)
    size(api, first, spend=4 * B, growth_pct=12.5, accept=True)
    size(api, third, spend=2 * B, accept=True)
    size(api, sub, spend=B)  # proposed, so it is not money
    proof = evidence(api)
    figure(api, "company/self", first, "revenue", B, proof)  # 1 of 4: a quarter of the segment

    rows = {s["id"]: s for s in get(api, "market/segments/compare")["segments"]}
    assert set(rows) == {first, second, fourth}  # the sub-segment is not ours, so it is not listed
    assert rows[first] == {
        "id": first,
        "name": "Screening",
        "color": "amber",  # no stored colour: core's fallback for its place (Payments, Quiet, Screening)
        "money": 4 * B,
        "yearly_spend": 4 * B,
        "growth": 12.5,
        "our_share": 25.0,
        "briefing_week": None,
    }
    # No accepted size of its own: its yearly spend is its sub-segment's, and money stays unknown.
    assert (rows[second]["money"], rows[second]["yearly_spend"], rows[second]["growth"]) == (None, 2 * B, None)
    assert rows[second]["our_share"] is None
    assert all(rows[fourth][k] is None for k in ("money", "yearly_spend", "growth", "our_share", "briefing_week"))


def test_compare_lists_the_newest_briefing_items_tagged_with_their_segment(api, tmp_path):
    _seed(api, tmp_path)
    first, second, _ = defined(api)
    ours(api, first, second)
    item = {"text": "Rival cut prices.", "kind": "change", "entity_ids": ["company/harborly"]}
    write_briefing(api, first, items=[item], answers={"money": "The low end shrinks."})

    view = get(api, "market/segments/compare")
    row = {s["id"]: s for s in view["segments"]}
    assert row[first]["briefing_week"] == WEEK and row[second]["briefing_week"] is None
    assert view["changed"] == [{**item, "segment_id": first, "week": WEEK, "bets": []}]


def test_compare_and_page_list_ranked_segments_in_rank_order_and_colour_them_from_core(api, tmp_path):
    _seed(api, tmp_path)
    first, second, _ = defined(api)
    ours(api, first, second)
    # Screening is ranked first, though Payments sorts first by name.
    post(api, "segments/order", {"ids": [first, second]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE market_entities SET properties=json_set(properties, '$.color', 'red') WHERE id=?", (second,))

    listed = get(api, "market/segments/compare")["segments"]
    assert [s["id"] for s in listed] == [first, second]
    # First has no stored colour, so it gets its place's colour; second keeps its own.
    assert [s["color"] for s in listed] == ["blue", "red"]
    page = get(api, f"market/segments/{second}/page")
    assert (page["segment"]["rank"], page["segment"]["color"]) == (1, "red")


def test_a_segment_page_has_its_answers_sizes_players_and_what_changed(api, tmp_path):
    _seed(api, tmp_path)
    first, second, sub = defined(api)
    ours(api, first, sub)
    size(api, first, spend=4 * B, accept=True)
    proof = evidence(api)
    figure(api, "company/harborly", first, "arr", 12_000_000, proof)
    write_briefing(api, first, answers={"changed": "Rivals cut prices.", "money": "The low end shrinks."})

    page = get(api, f"market/segments/{first}/page")
    assert page["segment"]["id"] == first and page["segment"]["name"] == "Screening"
    # Credit checks sorts first by name, so Screening is place 1 and has no stored colour: place 1's colour.
    assert (page["segment"]["color"], page["segment"]["rank"]) == ("green", 1)
    assert page["answers"] == {"changed": "Rivals cut prices.", "money": "The low end shrinks."}
    assert page["briefing"]["segment_id"] == first and page["briefing"]["week"] == WEEK
    assert page["sizes"]["chosen"]["spend_usd"] == 4 * B and page["sizes"]["chosen"]["accepted"] is True
    assert page["sizes"]["rolled_usd"]["spend_usd"] == 4 * B
    holders = {p["entity_id"]: p["figures"] for p in page["players"]}
    assert holders["company/harborly"][0]["metric"] == "arr" and holders["company/harborly"][0]["value"] == 12_000_000
    assert any(row["subject_id"] == first for row in page["what_changed"])
    assert len(page["what_changed"]) <= 30

    empty = get(api, f"market/segments/{sub}/page")
    assert empty["answers"] == {} and empty["briefing"] is None
    # The players are the market page's for the segment: with no figures, the company is listed as its operator.
    assert empty["sizes"]["chosen"] is None
    assert [(p["entity_id"], p["figures"]) for p in empty["players"]] == [("company/self", [])]


def test_a_segment_that_is_not_ours_is_a_404_on_the_page_and_on_research(api, tmp_path):
    _seed(api, tmp_path)
    first, second, _ = defined(api)
    ours(api, first)
    assert get(api, f"market/segments/{second}/page", expected=404)["error"]["code"] == "not_found"
    assert get(api, "market/segments/segment/nope/page", expected=404)["error"]["code"] == "not_found"
    post(api, f"market/segments/{second}/research", {}, expected=404)
    post(api, "market/segments/segment/nope/research", {}, expected=404)


def test_asking_for_a_segment_files_one_open_task_and_asking_again_returns_it(api, tmp_path):
    _seed(api, tmp_path)
    first, second, _ = defined(api)
    ours(api, first, second)
    post(api, f"market/segments/{first}/research", {"note": "Landlords only"}, expected=409)  # no Librarian yet
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")

    first_task = post(api, f"market/segments/{first}/research", {"note": "Landlords only"})
    assert first_task["created"] is True
    again = post(api, f"market/segments/{first}/research", {"note": "Other words"})
    assert again == {"task_id": first_task["task_id"], "created": False}
    other = post(api, f"market/segments/{second}/research", {})
    assert other["created"] is True and other["task_id"] != first_task["task_id"]
    # A bot may ask too, and the open task comes back.
    by_bot = post(api, f"market/segments/{first}/research", {}, token=librarian["token"])
    assert by_bot == {"task_id": first_task["task_id"], "created": False}
    post(api, f"market/segments/{first}/research", {}, token="ben-test")

    with api.app.state.store.read() as c:
        rows = H._rows(
            c.execute("SELECT owner_id, title, body FROM tasks WHERE title LIKE 'Market for%' ORDER BY title")
        )
    assert [r["title"] for r in rows] == ["Market for Payments", "Market for Screening"]
    assert all(r["owner_id"] == "bot:librarian" for r in rows)
    screening = next(r["body"] for r in rows if r["title"] == "Market for Screening")
    assert f"--segment {first}" in screening and "--week" in screening and "Asked: Landlords only" in screening

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (first_task["task_id"],))
    later = post(api, f"market/segments/{first}/research", {})
    assert later["created"] is True and later["task_id"] != first_task["task_id"]


def test_a_bot_the_librarian_refuses_gets_no_research_task(api, tmp_path):
    _seed(api, tmp_path)
    first, _, _ = defined(api)
    ours(api, first)
    _librarian(api)
    with api.app.state.store.transaction() as c:
        BS.write(c, "librarian", {"runtime": "fake", "host": "keeper", "bot_contact": "replies"})
    make_bot(api, "stranger", "Stranger")
    refused = post(api, f"market/segments/{first}/research", {}, token=bot_token(api, "stranger"), expected=403)
    assert refused["error"]["code"] == "bot_contact"
    with api.app.state.store.read() as c:
        assert H._rows(c.execute("SELECT id FROM tasks WHERE title LIKE 'Market for%'")) == []


def test_research_finds_a_segments_task_by_its_id_and_not_by_its_title(api, tmp_path):
    _seed(api, tmp_path)
    first, second, _ = defined(api)
    retail = post(api, "market/segments", {"name": "Retail"})["segment"]["id"]
    eu = post(api, "market/segments", {"name": "Retail (EU)"})["segment"]["id"]
    ours(api, first, second, retail, eu)
    _librarian(api)

    eu_task = post(api, f"market/segments/{eu}/research", {})
    assert eu_task["created"] is True
    # "Retail" is the other name's prefix: it files its own task, and asking again returns that one.
    retail_task = post(api, f"market/segments/{retail}/research", {})
    assert retail_task["created"] is True and retail_task["task_id"] != eu_task["task_id"]
    assert post(api, f"market/segments/{retail}/research", {}) == {"task_id": retail_task["task_id"], "created": False}
    assert post(api, f"market/segments/{eu}/research", {}) == {"task_id": eu_task["task_id"], "created": False}

    # Renamed, the segment keeps its open task: the task is found by the segment's id.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE market_entities SET name='Shops' WHERE id=?", (retail,))
    assert post(api, f"market/segments/{retail}/research", {}) == {"task_id": retail_task["task_id"], "created": False}

    # A task a person filed with the segment's title is not the segment's research: asking files one.
    post(api, "tasks", {"owner": "librarian", "title": "Market for Payments", "body": "Mine."})
    second_task = post(api, f"market/segments/{second}/research", {})
    assert second_task["created"] is True


def test_our_share_counts_only_a_figure_from_the_spend_year(api, tmp_path):
    _seed(api, tmp_path)
    first, _, _ = defined(api)  # the year is 2026
    ours(api, first)
    size(api, first, spend=4 * B, accept=True)
    proof = evidence(api)
    figure(api, "company/self", first, "revenue", B, proof, as_of="2025-12")
    assert {s["id"]: s for s in get(api, "market/segments/compare")["segments"]}[first]["our_share"] is None
    figure(api, "company/self", first, "revenue", B, proof)
    assert {s["id"]: s for s in get(api, "market/segments/compare")["segments"]}[first]["our_share"] == 25.0


def test_compare_caps_the_changes_round_robin_over_the_whole_market_and_each_segment(api, tmp_path):
    _seed(api, tmp_path)
    first, second, _ = defined(api)
    ours(api, first, second)
    many = [
        {"text": f"Brightline change {n}.", "kind": "change", "entity_ids": ["company/brightline"]} for n in range(20)
    ]
    write_briefing(api, "", items=[{"text": "The whole market moved.", "kind": "change", "entity_ids": []}])
    write_briefing(api, first, items=many)
    write_briefing(api, second, items=many)
    changed = get(api, "market/segments/compare")["changed"]
    assert len(changed) == 30
    counts = Counter(item["segment_id"] for item in changed)
    # One from the whole market, and the two segments share the rest in turn: 15 and 14.
    assert counts[""] == 1 and sorted(counts.values()) == [1, 14, 15]


def test_a_segment_page_reads_its_size_when_the_market_has_no_definition(api, tmp_path):
    _seed(api, tmp_path)
    first, _, _ = defined(api, year=datetime.now(timezone.utc).year)
    ours(api, first)
    size(api, first, spend=3 * B, accept=True)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='market.definition'")
    assert get(api, "market/spend")["segments"] == []  # the market view has no tree to show it in
    page = get(api, f"market/segments/{first}/page")
    assert page["sizes"]["chosen"]["spend_usd"] == 3 * B and page["sizes"]["chosen"]["accepted"] is True
    assert page["sizes"]["rolled_usd"]["spend_usd"] == 3 * B
