"""The Strategy page's market cards (backend/strategy_market.py): which segment is Us and which Next, removing a
segment, and the competitors. Examples use Acme and Northwind."""

from datetime import datetime, timedelta, timezone

from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401
from backend.tests.test_growth import patch
from backend.tests.test_market import _librarian
from backend.tests.test_market_spend import defined, evidence


def segments(api, token="ana-test"):
    return {s["id"]: s for s in get(api, "market/spend", token=token)["segments"]}


def test_several_segments_are_us_or_next_and_each_click_moves_only_its_own(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": first, "target": "ours"})
    seen = segments(api)
    assert seen[first]["self"] and not seen[first]["next"] and not seen[second]["self"]
    post(api, "market/segments/target", {"id": second, "target": "ours"})
    seen = segments(api)
    assert seen[first]["self"] and seen[second]["self"]                               # several Us
    post(api, "market/segments/target", {"id": first, "target": "next"})
    post(api, "market/segments/target", {"id": sub, "target": "next"})
    seen = segments(api)
    assert (seen[first]["self"], seen[first]["next"]) == (False, True)
    assert sorted(k for k, s in seen.items() if s["next"]) == sorted([first, sub])      # several Next
    assert [k for k, s in seen.items() if s["self"]] == [second]
    # Growth follows each click: Us is serving, Next next, and neither drops it.
    status = lambda: {s["segment_id"]: s["status"] for s in get(api, "growth/segments")["segments"]}  # noqa: E731
    assert status() == {first: "next", second: "serving", sub: "next"}
    post(api, "market/segments/target", {"id": second, "target": "none"})
    assert not any(s["self"] for s in segments(api).values()) and status()[second] == "dropped"
    post(api, "market/segments/target", {"id": "company/northwind", "target": "ours"}, expected=404)
    post(api, "market/segments/target", {"id": first, "target": "ours"}, token="cara-test", expected=403)


def _status(api):
    return {s["segment_id"]: s["status"] for s in get(api, "growth/segments")["segments"]}


def test_writes_straight_to_the_graph_move_growth_and_only_people_mark_us_or_next(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": second, "target": "ours"})              # makes company/self
    proof = [evidence(api)]
    # The market routes: an Us edge makes the segment serving, and ending it drops it.
    edge = post(api, "market/edges", {"src": "company/self", "rel": "operates_in", "dst": first,
                                      "evidence_ids": proof})["edge"]
    assert _status(api)[first] == "serving"
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    post(api, f"market/edges/{edge['id']}", {"until": yesterday, "evidence_ids": proof})
    assert _status(api)[first] == "dropped"
    # Next set and cleared on the segment's properties.
    post(api, f"market/entities/{sub}", {"properties": {"next": True}, "evidence_ids": proof})
    assert _status(api)[sub] == "next"
    post(api, f"market/entities/{sub}", {"properties": {}, "evidence_ids": proof})
    assert _status(api)[sub] == "dropped"
    # The other way: Growth moves the graph, and one change is one Growth event (the graph's echo is not another).
    row = next(s for s in get(api, "growth/segments")["segments"] if s["segment_id"] == second)
    patch(api, f"growth/segments/{row['id']}", {"status": "testing"})
    assert not segments(api)[second]["self"] and _status(api)[second] == "testing"
    with api.app.state.store.transaction() as c:
        moves = c.execute("SELECT count(*) FROM events WHERE action='growth.segment.update' AND target=?",
                          (row["id"],)).fetchone()[0]
    assert moves == 1
    # The Librarian writes the graph, but only a person marks Us or Next.
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")
    post(api, "market/segments/target", {"id": first, "target": "ours"}, token=librarian["token"], expected=403)
    post(api, "market/segments/target", {"id": first, "target": "none"}, token=librarian["token"], expected=403)
    assert _status(api)[first] == "dropped"


def test_a_removed_next_segment_added_again_is_not_next(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": sub, "target": "next"})
    post(api, "market/segments/remove", {"id": sub})
    assert _status(api)[sub] == "dropped"
    back = post(api, "market/segments", {"name": "Credit checks"})["segment"]
    assert back["id"] == sub and not segments(api)[sub]["next"] and _status(api)[sub] == "dropped"


def test_removing_a_segment_moves_its_sub_segments_up(api):
    first, second, sub = defined(api)
    assert segments(api)[sub]["parent"] == first
    post(api, "market/segments/remove", {"id": first})
    seen = segments(api)
    assert first not in seen and seen[sub]["parent"] is None and second in seen
    post(api, "market/segments/remove", {"id": first}, expected=404)
    back = post(api, "market/segments", {"name": "Screening"})["segment"]                 # added again, by name
    assert back["id"] == first and segments(api)[first]["parent"] is None
    post(api, "market/segments/remove", {"id": second}, token="ben-test", expected=403)


def test_competitors_are_added_by_name_or_site_and_removed_and_added_again(api):
    defined(api)
    assert get(api, "market/competitors")["competitors"] == []
    northwind = post(api, "market/competitors", {"name": "northwind.example"})["competitor"]
    assert northwind["name"] == "northwind.example" and northwind["url"] == "https://northwind.example"
    post(api, "market/competitors", {"name": "CleanCo"})
    again = post(api, "market/competitors", {"name": "https://www.northwind.example/pricing"})["competitor"]
    assert again["id"] == northwind["id"]                                             # the same company, by its domain
    listed = get(api, "market/competitors", token="cara-test")["competitors"]
    assert [c["name"] for c in listed] == ["CleanCo", "northwind.example"]
    post(api, "market/competitors/remove", {"id": northwind["id"]})
    assert [c["name"] for c in get(api, "market/competitors")["competitors"]] == ["CleanCo"]
    post(api, "market/competitors/remove", {"id": northwind["id"]}, expected=404)
    post(api, "market/competitors", {"name": "Northwind.example"})
    assert len(get(api, "market/competitors")["competitors"]) == 2
    post(api, "market/competitors", {"name": "Rival"}, token="cara-test", expected=403)
