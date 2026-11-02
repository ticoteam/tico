"""Goals: every node on the org chart says what it is for; colours, not percentages; KPIs anyone
may log."""

from backend.tests.test_api import api, get, post  # noqa: F401


def company_goal(api, title="Grow revenue 30% this year"):
    return post(api, "goals", {"title": title, "owner_id": "company"})["goal"]


def test_company_goals_are_the_owners_and_a_sub_goal_gets_its_first_colour_from_its_owner(api):
    top = company_goal(api)
    assert top["status"] is None and top["parent_id"] is None and top["owner_id"] == "company"
    # Only the owner of the environment sets a company goal, and it supports nothing.
    post(api, "goals", {"title": "Ship faster", "owner_id": "company"}, token="ben-test", expected=403)
    post(api, "goals", {"title": "Nested", "owner_id": "company", "parent_id": top["id"]}, expected=422)
    # Ben sets his own goal under it: no colour yet.
    mine = post(api, "goals", {"title": "Ship the rental stats page", "owner_id": "me", "parent_id": top["id"]},
                token="ben-test")["goal"]
    assert mine["owner_id"] == "human:ben" and mine["status"] is None and mine["chain"][0]["id"] == top["id"]
    # Its owner gives it the first colour by hand, with a sentence; no one above has to agree first.
    post(api, f"goals/{mine['id']}/status", {"status": "green"}, token="ben-test", expected=422)
    first = post(api, f"goals/{mine['id']}/status", {"status": "green", "note": "Design is done, build starts Monday."},
                 token="ben-test")["goal"]
    assert first["status"] == "green" and first["status_by_id"] == "human:ben" and first["status_at"]
    # From then on the owner sets the colour, and the history says who said what.
    yellow = post(api, f"goals/{mine['id']}/status", {"status": "yellow", "note": "Backend is late."}, token="ben-test")["goal"]
    assert yellow["status"] == "yellow"
    assert [e["new"] for e in yellow["events"] if e["field"] == "status"] == ["green", "yellow"]
    # Ana owns the parent, so she may still set it; Cara is neither the owner nor above him.
    post(api, f"goals/{mine['id']}/status", {"status": "red", "note": "Ana's call."}, expected=200)
    post(api, f"goals/{mine['id']}/status", {"status": "red", "note": "Nope."}, token="cara-test", expected=403)


def test_a_person_goal_with_goals_under_it_becomes_the_company_goal_on_upgrade(api):
    """Before an owner existed for goals, a goal with no parent and goals under it was the company goal."""
    old = post(api, "goals", {"title": "Grow", "owner_id": "ana"})["goal"]
    post(api, "goals", {"title": "Ship", "owner_id": "ben", "parent_id": old["id"]})
    alone = post(api, "goals", {"title": "Rest", "owner_id": "cara"})["goal"]
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM cloud_migrations WHERE version=43")
    api.app.state.store.initialize(seed_market=False)
    assert owner_of(api, old["id"]) == "company" and owner_of(api, alone["id"]) == "human:cara"


def owner_of(api, goal_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT owner_id FROM goals WHERE id=?", (goal_id,)).fetchone()[0]


def test_a_finished_goal_is_reopened_by_whoever_may_set_its_colour_and_undo_puts_its_colour_back(api):
    top = company_goal(api)
    mine = post(api, "goals", {"title": "Ship the rental stats page", "owner_id": "me", "parent_id": top["id"]},
                token="ben-test")["goal"]
    post(api, f"goals/{mine['id']}/status", {"status": "green", "note": "Design is done."})
    post(api, f"goals/{mine['id']}/status", {"status": "yellow", "note": "Backend is late."}, token="ben-test")
    # Only a finished goal is reopened.
    post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": "Back on"}, token="ben-test", expected=422)
    post(api, f"goals/{mine['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    # Cara may not set its colour, so she may not reopen it; a reason is required.
    post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": "Mine now"}, token="cara-test", expected=403)
    post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": ""}, token="ben-test", expected=422)
    set_at = [e for e in get(api, f"goals/{mine['id']}")["goal"]["events"] if e["new"] == "yellow"][-1]["created_at"]
    # Undo puts back what the history says it had, whatever the page sends: Ben's colour, note and time, though
    # Ana pressed it, and the history says who undid it.
    back = post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": "Fine", "undo": True})["goal"]
    assert (back["status"], back["status_note"], back["status_source"]) == ("yellow", "Backend is late.", "person")
    assert (back["status_by_id"], back["status_at"]) == ("human:ben", set_at)
    assert back["rank"] is not None
    last = [e for e in back["events"] if e["field"] == "status"][-1]
    assert (last["actor"], last["old"], last["new"], last["note"]) == ("human:ana", "dropped", "yellow", "Undo remove")
    # Done, then reopened with a new colour and one line why.
    post(api, f"goals/{mine['id']}/status", {"status": "done", "note": ""}, token="ben-test")
    again = post(api, f"goals/{mine['id']}/reopen", {"status": "red", "note": "The launch slipped"},
                 token="ben-test")["goal"]
    assert (again["status"], again["status_by_id"], again["status_note"]) == ("red", "human:ben", "The launch slipped")
    last = [e for e in again["events"] if e["field"] == "status"][-1]
    assert (last["old"], last["new"], last["note"]) == ("done", "red", "Reopened: The launch slipped")
    # Undo is for a removed goal only; a done one is reopened with a reason.
    post(api, f"goals/{mine['id']}/status", {"status": "done", "note": ""}, token="ben-test")
    post(api, f"goals/{mine['id']}/reopen", {"undo": True}, token="ben-test", expected=422)


def test_a_sub_goal_with_no_colour_is_reopened_with_one_by_its_owner(api):
    top = company_goal(api)
    mine = post(api, "goals", {"title": "Ship the rental stats page", "owner_id": "me", "parent_id": top["id"]},
                token="ben-test")["goal"]
    post(api, f"goals/{mine['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    # Its owner brings it back with its first colour and a reason; nobody above has to accept it first.
    back = post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": "Back on"}, token="ben-test")["goal"]
    assert back["status"] == "green" and back["status_source"] == "person" and back["status_by_id"] == "human:ben"


def test_reopening_with_auto_puts_back_the_colour_it_had_and_undo_gives_none_when_it_never_had_one(api):
    top = company_goal(api)
    sub = post(api, "goals", {"title": "Ship the rental stats page", "owner_id": "me", "parent_id": top["id"]},
               token="ben-test")["goal"]
    post(api, f"goals/{sub['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    # Undo gives a goal that never had a colour none, whatever the page sends.
    back = post(api, f"goals/{sub['id']}/reopen", {"status": "green", "note": "Back on", "undo": True},
                token="ben-test")["goal"]
    assert back["status"] is None and back["status_source"] == "auto"
    # A goal the Strategy Bot coloured gets that colour back when the arithmetic has nothing new to say.
    post(api, f"goals/{sub['id']}/status", {"status": "green", "note": "Set by hand."})
    post(api, f"goals/{sub['id']}/status/auto", {})
    post(api, f"goals/{sub['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    auto = post(api, f"goals/{sub['id']}/reopen", {"status": "auto", "note": "Removed by mistake"},
                token="ben-test")["goal"]
    assert auto["status_source"] == "auto" and auto["status"] is not None and auto["status"] != "dropped"
    # A colour handed back to the Strategy Bot before the removal comes back automatic, not as the hand-set one.
    post(api, f"goals/{sub['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    undone = post(api, f"goals/{sub['id']}/reopen", {"undo": True}, token="ben-test")["goal"]
    assert (undone["status"], undone["status_source"]) == (auto["status"], "auto")


def test_undo_after_an_undo_or_a_reopen_puts_back_the_colour_sentence_not_the_history_reason(api):
    top = company_goal(api)
    mine = post(api, "goals", {"title": "Ship the rental stats page", "owner_id": "me", "parent_id": top["id"]},
                token="ben-test")["goal"]
    post(api, f"goals/{mine['id']}/status", {"status": "green", "note": "Accepted."})
    set_at = post(api, f"goals/{mine['id']}/status", {"status": "yellow", "note": "Backend is late."},
                  token="ben-test")["goal"]["status_at"]

    def drop_and_undo():
        post(api, f"goals/{mine['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
        return post(api, f"goals/{mine['id']}/reopen", {"undo": True})["goal"]

    drop_and_undo()
    twice = drop_and_undo()
    assert (twice["status"], twice["status_note"], twice["status_by_id"], twice["status_at"]) == \
        ("yellow", "Backend is late.", "human:ben", set_at)
    post(api, f"goals/{mine['id']}/status", {"status": "done", "note": ""}, token="ben-test")
    reopened = post(api, f"goals/{mine['id']}/reopen", {"status": "red", "note": "The launch slipped"},
                    token="ben-test")["goal"]
    undone = drop_and_undo()
    assert (undone["status"], undone["status_note"], undone["status_by_id"], undone["status_at"]) == \
        ("red", "The launch slipped", "human:ben", reopened["status_at"])


def test_every_finished_goal_of_one_owner_is_listed_without_the_rest_of_the_team(api):
    top = company_goal(api)
    for token in ("ben-test", "ana-test"):
        g = post(api, "goals", {"title": "Ship it", "owner_id": "me", "parent_id": top["id"]}, token=token)["goal"]
        post(api, f"goals/{g['id']}/status", {"status": "dropped", "note": ""}, token=token)
    listed = get(api, "goals?all=1&status=done,dropped&owner=human:ben")["goals"]
    assert [g["owner_id"] for g in listed] == ["human:ben"]
    assert len(get(api, "goals?all=1&status=done,dropped")["goals"]) == 2
