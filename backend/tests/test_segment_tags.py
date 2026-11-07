"""Segment tags on goals and tasks (backend/segment_tags.py): a goal or task is for one of the company's segments or none,
a subtask is for its parent's unless it names one, the goal and task lists take one segment or `none`, a goal's words
change proposes a segment, and the Growth board narrows to a segment. Examples use Acme and its segments Screening and
Payments."""

from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_market_spend import defined
from backend.tests.test_strategy_bot_chat import strategy_bot, turn


def segments(api):
    """Screening and Payments are the company's segments; the sub-segment under Screening is not."""
    screening, payments, sub = defined(api)
    for segment in (screening, payments):
        post(api, "market/segments/target", {"id": segment, "target": "ours"})
    return screening, payments, sub


def goal(api, title, **more):
    return post(api, "goals", {"title": title, "owner": "me", **more})["goal"]


def written(response):
    """A task write's task: a write that also warns or reads back keeps its task beside that envelope."""
    return response.get("task", response)


def task(api, title, **more):
    return written(post(api, "tasks", {"owner": "ben", "title": title, "body": "Acme's first pass.", **more}))


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
    assert get(api, "goals/tree?segment=segment/nope")["goals"] == []


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
    moved = written(post(api, f"tasks/{first['id']}", {"version": first["version"], "segment_id": payments}))
    assert moved["segment_id"] == payments
    cleared = written(post(api, f"tasks/{first['id']}", {"version": moved["version"], "segment_id": ""}))
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
    assert task_ids(api, "segment=segment/nope") == set()


def test_the_growth_marketing_board_narrows_to_its_segment(api):
    screening, payments, _ = segments(api)
    task(api, "Screening referral ads", type="idea", step="Live", segment_id=screening)
    task(api, "Payments webinar", type="idea", step="Live", segment_id=payments)

    def live(query):
        board = next(
            (
                p["card"]["data"]
                for p in get(api, "places/growth" + query)["pins"]
                if p.get("card", {}).get("template") == "growth_board"
            ),
            None,
        )
        if board is None:  # a board with nothing on it is hidden: its data is None (cards.card_view)
            return []
        return sorted(card["title"] for lane in board["lanes"] if lane["stage"] == "testing" for card in lane["cards"])

    assert live("") == ["Payments webinar", "Screening referral ads"]
    assert live(f"?segment={screening}") == ["Screening referral ads"]
    assert live("?segment=none") == []
    assert get(api, f"places/growth?segment={payments}")["segment"] == payments
    assert live("?segment=segment/nope") == []


def test_a_teammate_who_may_not_colour_a_goal_cannot_move_its_segment(api):
    screening, payments, _ = segments(api)
    gold = goal(api, "Screening pays back in a quarter", segment_id=screening)
    post(api, f"goals/{gold['id']}", {"segment_id": payments}, token="cara-test", expected=403)
    tree = {g["id"]: g for g in get(api, "goals/tree")["goals"]}
    assert tree[gold["id"]]["segment_id"] == screening


def test_accepting_a_goal_wording_proposal_sets_its_segment_and_an_empty_one_clears_it(api):
    screening, _, _ = segments(api)
    mine = goal(api, "Screening pays back")

    def segment_of(goal_id):
        return {g["id"]: g for g in get(api, "goals/tree")["goals"]}[goal_id]["segment_id"]

    for segment, expected in ((screening, screening), ("", None)):
        proposal = post(
            api,
            "goal-proposals",
            {
                "kind": "goal_wording",
                "goal_id": mine["id"],
                "payload": {"segment_id": segment},
                "reason": "The segment this goal is for.",
            },
            token="ben-test",
        )["proposal"]
        post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"})
        assert segment_of(mine["id"]) == expected


def test_a_goal_wording_proposal_that_repeats_the_goals_segment_is_refused(api):
    screening, _, _ = segments(api)
    gold = goal(api, "Screening pays back", segment_id=screening)
    plain = goal(api, "Keep the books clean")
    for goal_id, segment in ((gold["id"], screening), (plain["id"], "")):
        post(
            api,
            "goal-proposals",
            {"kind": "goal_wording", "goal_id": goal_id, "payload": {"segment_id": segment}, "reason": "Same as now."},
            token="ben-test",
            expected=422,
        )


def test_the_goal_tree_keeps_the_goals_above_a_kept_one_as_context(api):
    screening, _, _ = segments(api)
    parent = goal(api, "Grow the company")
    child = goal(api, "Screening pays back", segment_id=screening, parent_id=parent["id"])
    tree = {g["id"]: g for g in get(api, f"goals/tree?segment={screening}")["goals"]}
    assert set(tree) == {child["id"], parent["id"]}
    assert tree[child["id"]]["context"] is False and tree[parent["id"]]["context"] is True


def test_a_task_tagged_with_a_former_segment_is_still_editable_and_its_filter_is_empty(api):
    screening, _, _ = segments(api)
    first = task(api, "Screening onboarding copy", segment_id=screening)
    post(api, "market/segments/target", {"id": screening, "target": "none"})
    assert task_ids(api, f"segment={screening}") == set()
    edited = written(
        post(
            api,
            f"tasks/{first['id']}",
            {"version": first["version"], "title": "Onboarding copy", "segment_id": screening},
        )
    )
    assert edited["title"] == "Onboarding copy" and edited["segment_id"] == screening


def test_a_bulk_change_takes_a_segment_filter(api):
    screening, payments, _ = segments(api)
    task(api, "Screening demo", segment_id=screening)
    task(api, "Payments dispute flow", segment_id=payments)
    here = post(
        api, "task-bulk", {"filter": {"segment": screening}, "action": "owner", "owner": "ben", "dry_run": True}
    )
    assert here["matched"] == 1
    nothing = post(
        api, "task-bulk", {"filter": {"segment": "segment/nope"}, "action": "owner", "owner": "ben", "dry_run": True}
    )
    assert nothing["matched"] == 0


def test_the_growth_markers_narrow_to_the_segment_when_filtered(api):
    from backend import marketing_tasks as MT

    screening, payments, _ = segments(api)
    for title, segment in (("Screening referral ads", screening), ("Payments webinar", payments)):
        made = task(api, title, type="idea", segment_id=segment)
        post(api, f"tasks/{made['id']}", {"version": made["version"], "step": MT.step_id("testing")})

    def marked(query):
        return sorted(m["title"] for m in get(api, "growth/numbers" + query)["markers"])

    assert marked("") == ["Payments webinar", "Screening referral ads"]
    assert marked(f"?segment={screening}") == ["Screening referral ads"]
    assert marked("?segment=none") == []
    assert marked("?segment=segment/nope") == []


def read_back(api, attempt, *changes, expected=200):
    return post(api, "goals/read-back", {"changes": list(changes)}, token=attempt["token"], expected=expected)


def test_a_read_back_repeating_a_goals_segment_is_no_change_and_only_a_new_segment_is_checked(api):
    screening, payments, sub = segments(api)
    gold = post(api, "goals", {"title": "Screening pays back", "owner": "ops", "segment_id": screening})["goal"]
    # Screening stops being one of the company's: the goal keeps it, and a read-back that repeats it is not a change.
    post(api, "market/segments/target", {"id": screening, "target": "none"})
    strategy_bot(api)
    _, attempt = turn(api, "coo", "ana-test", "Fix the screening goal")
    change = {"kind": "goal_update", "goal_id": gold["id"], "title": "Screening pays back in a quarter"}
    text = read_back(api, attempt, {**change, "segment_id": screening})["text"]
    assert "title was" in text and "segment was" not in text
    same = read_back(
        api, attempt, {"kind": "goal_update", "goal_id": gold["id"], "segment_id": screening}, expected=409
    )
    assert same["error"]["code"] == "no_change"
    read_back(api, attempt, {"kind": "goal_update", "goal_id": gold["id"], "segment_id": sub}, expected=404)
