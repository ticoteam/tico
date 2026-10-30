"""The Strategy page's market cards (backend/strategy_market.py): which segment is Us and which Next, removing a
segment, and the competitors. Examples use Acme and Northwind."""

from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_market_spend import defined


def segments(api, token="ana-test"):
    return {s["id"]: s for s in get(api, "market/spend", token=token)["segments"]}


def test_one_segment_is_us_and_one_next_and_a_click_moves_them(api):
    first, second, sub = defined(api)
    post(api, "market/segments/target", {"id": first, "target": "ours"})
    seen = segments(api)
    assert seen[first]["self"] and not seen[first]["next"] and not seen[second]["self"]
    post(api, "market/segments/target", {"id": second, "target": "ours"})
    post(api, "market/segments/target", {"id": first, "target": "next"})
    seen = segments(api)
    assert (seen[first]["self"], seen[first]["next"]) == (False, True)
    assert (seen[second]["self"], seen[second]["next"]) == (True, False)
    post(api, "market/segments/target", {"id": sub, "target": "next"})               # one Next at a time
    seen = segments(api)
    assert [k for k, s in seen.items() if s["next"]] == [sub] and [k for k, s in seen.items() if s["self"]] == [second]
    post(api, "market/segments/target", {"id": second, "target": "none"})
    assert not any(s["self"] for s in segments(api).values())
    post(api, "market/segments/target", {"id": "company/northwind", "target": "ours"}, expected=404)
    post(api, "market/segments/target", {"id": first, "target": "ours"}, token="cara-test", expected=403)


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
