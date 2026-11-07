"""The Strategy page's market cards (backend/strategy_market.py): which segments are the company's, removing a segment,
and the competitors. A segment the company meant to enter next is one of its own now: there is one flag. Examples use
Acme and Northwind."""

from datetime import datetime, timedelta, timezone

from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401
from backend.tests.test_market import _librarian
from backend.tests.test_market_spend import defined, evidence


def segments(api, token="ana-test"):
    return {s["id"]: s for s in get(api, "market/spend", token=token)["segments"]}


def test_several_segments_are_ours_and_each_click_moves_only_its_own(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": first, "target": "ours"})
    seen = segments(api)
    assert seen[first]["self"] and not seen[second]["self"]
    post(api, "market/segments/target", {"id": second, "target": "ours"})
    assert segments(api)[first]["self"] and segments(api)[second]["self"]  # several of ours
    # Next is ours too: the company's segments are the ones it plays in, whichever it meant to enter first.
    post(api, "market/segments/target", {"id": sub, "target": "next"})
    assert segments(api)[sub]["self"] and "next" not in segments(api)[sub]
    post(api, "market/segments/target", {"id": second, "target": "none"})
    assert {k for k, s in segments(api).items() if s["self"]} == {first, sub}
    post(api, "market/segments/target", {"id": "company/northwind", "target": "ours"}, expected=404)
    post(api, "market/segments/target", {"id": first, "target": "ours"}, token="cara-test", expected=403)


def test_writes_straight_to_the_graph_move_ours_and_only_people_mark_it(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": second, "target": "ours"})  # makes company/self
    proof = [evidence(api)]
    # The market routes: an operates_in edge makes the segment ours, and ending it takes that off.
    edge = post(
        api, "market/edges", {"src": "company/self", "rel": "operates_in", "dst": first, "evidence_ids": proof}
    )["edge"]
    assert segments(api)[first]["self"]
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    post(api, f"market/edges/{edge['id']}", {"until": yesterday, "evidence_ids": proof})
    assert not segments(api)[first]["self"]
    # The Librarian writes the graph, but only a person makes a segment ours.
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")
    post(api, "market/segments/target", {"id": first, "target": "ours"}, token=librarian["token"], expected=403)
    post(api, "market/segments/target", {"id": first, "target": "none"}, token=librarian["token"], expected=403)
    assert not segments(api)[first]["self"]


def test_a_segment_merged_away_stays_ours_under_the_one_it_merged_into(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": first, "target": "ours"})
    post(api, f"market/entities/{first}/merge", {"into": second})
    assert first not in segments(api) and segments(api)[second]["self"]


def test_a_removed_segment_added_again_is_not_ours_until_someone_makes_it_so(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": sub, "target": "ours"})
    post(api, "market/segments/remove", {"id": sub})
    assert sub not in segments(api)
    back = post(api, "market/segments", {"name": "Credit checks"})["segment"]
    assert back["id"] == sub and not segments(api)[sub]["self"]


def test_removing_a_segment_moves_its_sub_segments_up(api):
    first, second, sub = defined(api)
    assert segments(api)[sub]["parent"] == first
    post(api, "market/segments/remove", {"id": first})
    seen = segments(api)
    assert first not in seen and seen[sub]["parent"] is None and second in seen
    post(api, "market/segments/remove", {"id": first}, expected=404)
    back = post(api, "market/segments", {"name": "Screening"})["segment"]  # added again, by name
    assert back["id"] == first and segments(api)[first]["parent"] is None
    post(api, "market/segments/remove", {"id": second}, token="ben-test", expected=403)


def test_competitors_are_added_by_name_or_site_and_removed_and_added_again(api):
    defined(api)
    assert get(api, "market/competitors")["competitors"] == []
    northwind = post(api, "market/competitors", {"name": "northwind.example"})["competitor"]
    assert northwind["name"] == "northwind.example" and northwind["url"] == "https://northwind.example"
    post(api, "market/competitors", {"name": "CleanCo"})
    again = post(api, "market/competitors", {"name": "https://www.northwind.example/pricing"})["competitor"]
    assert again["id"] == northwind["id"]  # the same company, by its domain
    listed = get(api, "market/competitors", token="cara-test")["competitors"]
    assert [c["name"] for c in listed] == ["CleanCo", "northwind.example"]
    post(api, "market/competitors/remove", {"id": northwind["id"]})
    assert [c["name"] for c in get(api, "market/competitors")["competitors"]] == ["CleanCo"]
    post(api, "market/competitors/remove", {"id": northwind["id"]}, expected=404)
    post(api, "market/competitors", {"name": "Northwind.example"})
    assert len(get(api, "market/competitors")["competitors"]) == 2
    post(api, "market/competitors", {"name": "Rival"}, token="cara-test", expected=403)
