"""Segment tags on goals and tasks (backend/segment_tags.py): a goal or task is for one of the company's segments or none,
a subtask is for its parent's unless it names one, the goal and task lists take one segment or `none`, a goal's words
change proposes a segment, and the Growth board narrows to a segment. Examples use Acme and its segments Screening and
Payments."""

from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_market_spend import defined


def segments(api):
    """Screening and Payments are the company's segments; the sub-segment under Screening is not."""
    screening, payments, sub = defined(api)
    for segment in (screening, payments):
        post(api, "market/segments/target", {"id": segment, "target": "ours"})
    return screening, payments, sub


def goal(api, title, **more):
    return post(api, "goals", {"title": title, "owner": "me", **more})["goal"]


def task(api, title, **more):
    return post(api, "tasks", {"owner": "ben", "title": title, "body": "Acme's first pass.", **more})


def task_ids(api, query):
    return {t["id"] for t in get(api, "tasks?" + query)["tasks"]}


def test_a_goal_is_for_one_of_the_companys_segments_and_an_empty_segment_clears_it(api):
    screening, payments, sub = segments(api)
    gold = goal(api, "Screening pays back in a quarter", segment_id=screening)
    assert gold["segment_id"] == screening
    moved = post(api, f"goals/{gold['id']}", {"segment_id": payments})["goal"]
    assert moved["segment_id"] == payments
    cleared = post(api, f"goals/{gold['id']}", {"segment_id": ""})["goal"]
    assert cleared["segment_id"] is None
    # A segment the company does not play in, or none at all, is refused on create and on update.
    post(api, "goals", {"title": "Nope", "owner": "me", "segment_id": sub}, expected=404)
    post(api, "goals", {"title": "Nope", "owner": "me", "segment_id": "segment/nope"}, expected=404)
    post(api, f"goals/{gold['id']}", {"segment_id": sub}, expected=404)


def test_the_goal_lists_narrow_to_one_segment_or_to_none(api):
    screening, payments, _ = segments(api)
    here = goal(api, "Screening pays back", segment_id=screening)
    there = goal(api, "Payments settle in a day", segment_id=payments)
    plain = goal(api, "Keep the books clean")
    assert {g["id"] for g in get(api, f"goals/tree?segment={screening}")["goals"]} == {here["id"]}
    none = {g["id"] for g in get(api, "goals/tree?segment=none")["goals"]}
    assert plain["id"] in none and not {here["id"], there["id"]} & none
    assert {g["id"] for g in get(api, f"goals?all=true&segment={payments}")["goals"]} == {there["id"]}
    assert {g["id"] for g in get(api, f"goals?segment={screening}")["goals"]} == {here["id"]}
    get(api, "goals/tree?segment=segment/nope", expected=404)


def test_a_goal_wording_proposal_names_a_segment_the_company_plays_in(api):
    screening, _, sub = segments(api)
    mine = goal(api, "Screening pays back")
    proposal = post(
        api,
        "goal-proposals",
        {
            "kind": "goal_wording",
            "goal_id": mine["id"],
            "payload": {"segment_id": screening},
            "reason": "This goal is for the screening segment.",
        },
        token="ben-test",
    )["proposal"]
    card = next(p["card"] for p in get(api, "goals/tree")["proposals"] if p["id"] == proposal["id"])
    assert {"label": "Segment", "now": "", "proposed": "Screening"} in card["changes"]
    post(
        api,
        "goal-proposals",
        {"kind": "goal_wording", "goal_id": mine["id"], "payload": {"segment_id": sub}, "reason": "Not ours."},
        token="ben-test",
        expected=422,
    )


def test_a_task_is_for_a_segment_and_a_subtask_is_for_its_parents_unless_it_names_one(api):
    screening, payments, _ = segments(api)
    parent = task(api, "Screening onboarding", segment_id=screening)
    assert parent["segment_id"] == screening
    under = [{"task": parent["id"], "kind": "parent"}]
    assert task(api, "Draft the form", relations=under)["segment_id"] == screening
    assert task(api, "Price the add-on", relations=under, segment_id=payments)["segment_id"] == payments
    # An empty segment names none, so the subtask does not take the parent's.
    assert task(api, "Tidy the wiki", relations=under, segment_id="")["segment_id"] is None
    assert task(api, "Plan the offsite")["segment_id"] is None
    post(api, "tasks", {"owner": "ben", "title": "Nope", "body": "x", "segment_id": "segment/nope"}, expected=404)


def test_a_task_update_sets_and_clears_its_segment_and_refuses_one_that_is_not_ours(api):
    screening, payments, sub = segments(api)
    first = task(api, "Onboarding copy", segment_id=screening)
    moved = post(api, f"tasks/{first['id']}", {"version": first["version"], "segment_id": payments})
    assert moved["segment_id"] == payments
    cleared = post(api, f"tasks/{first['id']}", {"version": moved["version"], "segment_id": ""})
    assert cleared["segment_id"] is None
    post(api, f"tasks/{first['id']}", {"version": cleared["version"], "segment_id": sub}, expected=404)


def test_the_task_list_narrows_to_one_segment_or_to_none(api):
    screening, payments, _ = segments(api)
    here = task(api, "Screening demo", segment_id=screening)
    there = task(api, "Payments dispute flow", segment_id=payments)
    plain = task(api, "Plan the offsite")
    assert task_ids(api, f"segment={screening}") == {here["id"]}
    none = task_ids(api, "segment=none")
    assert plain["id"] in none and not {here["id"], there["id"]} & none
    get(api, "tasks?segment=segment/nope", expected=404)


def test_the_growth_marketing_board_narrows_to_its_segment(api):
    screening, payments, _ = segments(api)
    task(api, "Screening referral ads", type="idea", step="Live", segment_id=screening)
    task(api, "Payments webinar", type="idea", step="Live", segment_id=payments)

    def live(query):
        board = next(
            p["card"]["data"]
            for p in get(api, "places/growth" + query)["pins"]
            if p.get("card", {}).get("template") == "growth_board"
        )
        return sorted(card["title"] for lane in board["lanes"] if lane["stage"] == "testing" for card in lane["cards"])

    assert live("") == ["Payments webinar", "Screening referral ads"]
    assert live(f"?segment={screening}") == ["Screening referral ads"]
    assert live("?segment=none") == []
    assert get(api, f"places/growth?segment={payments}")["segment"] == payments
    get(api, "places/growth?segment=segment/nope", expected=404)
