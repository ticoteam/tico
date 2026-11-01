"""One question card (ui/app/question-card.js): the asking rules a bot's question to a person keeps when it is asked
(hubdb.lint_question), the "not this one" a bot hears for each unticked pick, and what a Needs you item carries for
the card's header and Decide's order (views.holds_up, views.card_why, views.decide_tier)."""

from backend import hubdb as H
from backend import views
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

PICKS = [{"label": "Calls", "then": "Booked on your calendar", "recommended": True},
         {"label": "Visitor notes", "then": "Each becomes a private task for you", "recommended": True},
         {"label": "Pages", "then": "Home, pricing and docs get the chat"},
         {"label": "Spend cap", "then": "Chat stops for the day at $10", "recommended": True}]


def _ask(api, token, key, **body):
    return api.post("/api/v2/task-questions", json={"on": "ana", **body}, headers=headers(token, key))


def _refused(r):
    assert r.status_code == 422 and r.json()["error"]["code"] == "lint", r.text
    return r.json()["error"]["detail"]


def test_a_pick_question_without_its_picks_or_what_happens_or_with_ids_is_refused_plainly(api):
    """Acceptance 18: refused at ask time with a plain reason; nothing is filed for the person."""
    _, _, attempt = setup_attempt(api)
    token = attempt["token"]
    listed = _refused(_ask(api, token, "a", question="Go with these: 1. Calls on your calendar 2. Pages on the site?"))
    assert "lists picks but has no options: give each pick as an option" in listed
    several = _refused(_ask(api, token, "b", question="Which of these should we do?", multi=True))
    assert "lists picks but has no options" in several
    bare = _refused(_ask(api, token, "c", question="Which plan?", options=[{"label": "Small"}, {"label": "Large", "then": "Costs more"}]))
    assert 'option "Small" has no "what happens" line (then)' in bare
    ids = _refused(_ask(api, token, "d", question="Retry run 8719e1a6 from reports/plan.md?", why="ValueError: bad date",
                        options=[{"label": "Retry", "then": "It runs again"}, {"label": "Stop", "then": "It stays stopped"}]))
    assert all(x in ids for x in ('has an id or hash: "8719e1a6"', 'has a file path: "reports/plan.md"',
                                  '"why it matters" line (why) has error output: "ValueError"'))
    # Every visible part of a pick keeps the rule too: its label and its description sit above Details.
    option_text = _refused(_ask(api, token, "d2", question="Which plan?", options=[
        {"label": "Small", "then": "Costs less", "description": "ValueError: bad date in reports/plan.md (8719e1a6)"},
        {"label": "Retry 8719e1a6", "then": "It runs again"}]))
    assert all(x in option_text for x in ('the description of "Small" has error output: "ValueError"',
                                          'option "Retry 8719e1a6" has an id or hash: "8719e1a6"'))
    unmarked = _refused(_ask(api, token, "e", question="Website sales chat: go with these picks?", multi=True,
                             options=[{k: v for k, v in o.items() if k != "recommended"} for o in PICKS]))
    assert "none marked recommended" in unmarked
    # The reason says the rule and gives an example to copy.
    assert "The rule: a question with picks gives each pick as an option" in bare and "Example:" in bare
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE waiting_on='human:ana'").fetchone()[0] == 0
        # A writing refusal: recorded, but never toward a quarantine.
        assert c.execute("SELECT count(*) FROM refusals WHERE actor='bot:ops' AND rule='lint'").fetchone()[0] == 6
    # Yes / No needs no options, and a clean pick question goes through with its card fields kept.
    assert _ask(api, token, "f", question="Archive the September drafts?").status_code == 200
    ok = _ask(api, token, "g", question="Website sales chat: go with these picks?", why="Engineering can't start until these are set.",
              multi=True, options=PICKS)
    assert ok.status_code == 200, ok.text
    q = ok.json()["question"]["ask"]["questions"][0]
    assert q["multi"] and q["why"] == "Engineering can't start until these are set."
    assert q["options"] == [{"description": "", **o} for o in PICKS]


def test_a_comment_question_from_a_bot_to_a_person_keeps_the_rules_and_a_person_s_does_not(api):
    _, _, attempt = setup_attempt(api)
    task = post(api, "tasks", {"owner": "bot:ops", "title": "Pick the logo", "body": "x"})
    ask = {"questions": [{"id": "q", "header": "", "question": "Which logo?", "options": [{"label": "Blue"}, {"label": "Red"}]}],
           "who": "ana"}
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": "Which logo?", "ask": ask},
                 headers=headers(attempt["token"], "c1"))
    assert 'options "Blue", "Red" have no "what happens" line' in _refused(r)
    ask["questions"][0]["options"] = [{"label": "Blue", "then": "Goes on the site", "description": "see logos/blue.svg"},
                                      {"label": "Red", "then": "Goes on the site"}]
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": "Which logo?", "ask": ask},
                 headers=headers(attempt["token"], "c2"))
    assert 'the description of "Blue" has a file path: "logos/blue.svg"' in _refused(r)
    # A person asking a bot is not a card for a person: no rule to keep.
    ask["who"] = "bot:ops"
    assert api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": "Which logo?", "ask": ask},
                    headers=headers()).status_code == 200


def test_send_ticked_says_not_this_one_for_each_unticked_pick_and_yes_to_all_says_none(api):
    """Acceptance 12 and 13, as the bot reads the answer."""
    _, _, attempt = setup_attempt(api)
    made = _ask(api, attempt["token"], "q1", question="Website sales chat: go with these picks?", multi=True, options=PICKS).json()
    tid, target = made["task"]["id"], {"comment": made["question"]["id"]}
    out = post(api, f"tasks/{tid}/answers", {"target": target, "answers": {"decision": ["Calls", "Visitor notes", "Spend cap"]},
                                             "other": "Pages: only pricing for now."})
    assert out["answer"]["not"] == {"decision": ["Pages"]} and out["woke"]
    assert out["comment"]["body"] == ('Ana answered "Website sales chat: go with these picks?": Calls, Visitor notes, Spend cap. '
                                      'Not this one: Pages. Pages: only pricing for now.')
    again = _ask(api, attempt["token"], "q2", question="Second chat: go with these picks?", multi=True, options=PICKS,
                 topic="second").json()
    every = post(api, f"tasks/{again['task']['id']}/answers", {"target": {"comment": again["question"]["id"]},
                                                              "answers": {"decision": [o["label"] for o in PICKS]}})
    assert "not" not in every["answer"] and "Not this one" not in every["comment"]["body"]


def test_the_card_says_what_it_holds_up_and_decide_puts_a_building_item_first(api):
    """Acceptance 15, 17 and 19, from the Needs you item the card reads."""
    _, _, attempt = setup_attempt(api)
    item = post(api, "tasks", {"owner": "bot:ops", "title": "Website sales chat build", "body": "Spec.",
                               "type": "Product roadmap", "step": "Building"})
    ticket = post(api, "tasks", {"owner": "bot:ops", "title": "Build the chat widget", "body": "x",
                                 "relations": [{"task": item["id"], "kind": "parent"}]})
    plain = post(api, "tasks", {"owner": "bot:ops", "title": "Tidy the drafts folder", "body": "x"})
    building = _ask(api, attempt["token"], "h1", question="Website sales chat: go with these picks?", multi=True,
                    options=PICKS, parent=ticket["id"],
                    body="Website sales chat: go with these picks?\n\nRun 8719e1a6 failed: ValueError in reports/plan.md").json()
    old = _ask(api, attempt["token"], "h2", question="Archive the September drafts?", parent=plain["id"],
               body="Clearing space before the October drafts.").json()
    with api.app.state.store.transaction() as c:
        # The archive question was asked yesterday; the Building one an hour ago.
        for tid, hours in ((old["task"]["id"], 30), (building["task"]["id"], 1)):
            at = H.shift(H.now(), seconds=-hours * 3600)
            c.execute("UPDATE messages SET created_at=? WHERE kind='ask' AND json_extract(refs,'$.task')=?", (at, tid))
            c.execute("UPDATE tasks SET created_at=? WHERE id=?", (at, tid))
    items = {i["id"]: i for i in get(api, "needs-you")["items"]}
    card, other = items[building["task"]["id"]], items[old["task"]["id"]]
    assert card["holds_up"] == {"id": item["id"], "title": "Website sales chat build", "kind": "roadmap", "step": "Building",
                                "due": None, "soonest": None}
    assert other["holds_up"]["title"] == "Tidy the drafts folder" and other["holds_up"]["kind"] == "task"
    assert (card["tier"], other["tier"]) == (1, 2)
    # The why line never carries the body's ids, paths or errors; a plain first paragraph is one.
    assert card["why"] == "" and other["why"] == "Clearing space before the October drafts."
    assert card["ask"]["from_actor"] == "bot:ops"


def test_due_or_going_ahead_within_two_working_days_comes_first_and_weekends_do_not_count():
    friday = "2026-10-23T10:00:00.000000Z"
    assert views.business_days_ahead(friday, 2) == "2026-10-27T10:00:00.000000Z"     # Mon, Tue
    soon = {"kind": "question", "ask": {"goes_ahead_at": "2026-10-26T09:00:00.000000Z", "refs": {}}}
    later = {"kind": "question", "ask": {"goes_ahead_at": "2026-10-28T09:00:00.000000Z", "refs": {}},
             "holds_up": {"kind": "roadmap", "step": "Approved"}}
    shipped = {"kind": "question", "ask": {"refs": {}}, "holds_up": {"kind": "roadmap", "step": "Shipped"}}
    assert [views.decide_tier(x, friday) for x in (soon, later, shipped)] == [0, 1, 2]


def test_a_dated_task_under_an_undated_building_item_keeps_its_urgency(api):
    """The header names the roadmap item, but the nearest due date of the work held up still puts the card first."""
    _, _, attempt = setup_attempt(api)
    item = post(api, "tasks", {"owner": "bot:ops", "title": "Website sales chat build", "body": "Spec.",
                               "type": "Product roadmap", "step": "Building"})
    tomorrow = H.shift(H.now(), hours=24)
    ticket = post(api, "tasks", {"owner": "bot:ops", "title": "Build the chat widget", "body": "x", "due": tomorrow,
                                 "relations": [{"task": item["id"], "kind": "parent"}]})
    asked = _ask(api, attempt["token"], "u1", question="Website sales chat: go with these picks?", multi=True,
                 options=PICKS, parent=ticket["id"]).json()
    card = next(i for i in get(api, "needs-you")["items"] if i["id"] == asked["task"]["id"])
    assert card["holds_up"]["kind"] == "roadmap" and card["holds_up"]["due"] is None
    assert card["holds_up"]["soonest"] == tomorrow and card["tier"] == 0


def test_times_with_different_offsets_are_compared_by_when_they_are():
    """Two working days from Friday 10:00Z is Tuesday 10:00Z. 12:00+05:00 is 07:00Z, inside it; 08:00-05:00 is 13:00Z,
    after it, though their text sorts the other way round. A bare date reads as midnight UTC."""
    friday = "2026-10-23T10:00:00.000000Z"
    inside = {"kind": "question", "ask": {"goes_ahead_at": "2026-10-27T12:00:00+05:00", "refs": {}}}
    after = {"kind": "question", "ask": {"goes_ahead_at": "2026-10-27T08:00:00-05:00", "refs": {}}}
    day = {"kind": "question", "ask": {"refs": {"if_no_answer": {"by": "2026-10-27"}}}}
    assert [views.decide_tier(x, friday) for x in (inside, after, day)] == [0, 2, 0]


def test_a_card_on_the_roadmap_item_itself_does_not_say_it_holds_itself_up_but_keeps_its_place(api):
    """The header never names the card's own task; Decide's order still counts its step and due date."""
    _, _, attempt = setup_attempt(api)
    tomorrow = H.shift(H.now(), hours=24)
    dated = post(api, "tasks", {"owner": "bot:ops", "title": "Website sales chat build", "body": "Spec.", "due": tomorrow,
                                "type": "Product roadmap", "step": "Building"})
    undated = post(api, "tasks", {"owner": "bot:ops", "title": "Pricing page refresh", "body": "Spec.",
                                  "type": "Product roadmap", "step": "Building"})
    with api.app.state.store.transaction() as c:
        for row in (dated, undated):
            H.task_comment(c, "bot:ops", row["id"], "Ship it with the chat on Friday?", ask={"questions": [
                {"id": "decision", "header": "", "question": "Ship it with the chat on Friday?",
                 "options": [{"label": "Yes"}, {"label": "No"}], "multi": False, "other": False}], "who": "human:ana"})
    items = {i["id"]: i for i in get(api, "needs-you")["items"]}
    assert [(items[r["id"]]["holds_up"], items[r["id"]]["tier"]) for r in (dated, undated)] == [(None, 0), (None, 1)]

    # Under a parent, the parent is what it holds up, dated by the item's own due.
    parent = post(api, "tasks", {"owner": "bot:ops", "title": "Launch the website", "body": "x"})
    sub = post(api, "tasks", {"owner": "bot:ops", "title": "Website chat for launch", "body": "Spec.",
                              "type": "Product roadmap", "step": "Building", "due": tomorrow,
                              "relations": [{"task": parent["id"], "kind": "parent"}]})
    with api.app.state.store.read() as c:
        held = views.holds_up(c, H.task(c, sub["id"]), lambda tid: True)
    assert (held["title"], held["kind"], held["soonest"]) == ("Launch the website", "task", tomorrow)
