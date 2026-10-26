"""Goals: every node on the org chart says what it is for; colours, not percentages; KPIs anyone
may log."""

from backend.tests.test_api import api, post  # noqa: F401


def company_goal(api, title="Grow revenue 30% this year"):
    return post(api, "goals", {"title": title, "owner": "company"})["goal"]


def test_company_goals_are_the_owners_and_a_proposal_is_accepted_by_the_parents_owner(api):
    top = company_goal(api)
    assert top["status"] is None and top["parent_id"] is None and top["owner"] == "company"
    # Only the owner of the environment sets a company goal, and it supports nothing.
    post(api, "goals", {"title": "Ship faster", "owner": "company"}, token="ben-test", expected=403)
    post(api, "goals", {"title": "Nested", "owner": "company", "parent_id": top["id"]}, expected=422)
    # Ben proposes his own goal under it: proposed, no colour yet.
    mine = post(api, "goals", {"title": "Ship the rental stats page", "owner": "me", "parent_id": top["id"]},
                token="ben-test")["goal"]
    assert mine["owner"] == "human:ben" and mine["status"] is None and mine["chain"][0]["id"] == top["id"]
    # He cannot accept it himself; Ana, who owns the parent, does, and a colour needs a sentence.
    post(api, f"goals/{mine['id']}/status", {"status": "green", "note": "on track"}, token="ben-test", expected=403)
    post(api, f"goals/{mine['id']}/status", {"status": "green"}, expected=422)
    accepted = post(api, f"goals/{mine['id']}/status", {"status": "green", "note": "Design is done, build starts Monday."})["goal"]
    assert accepted["status"] == "green" and accepted["status_by"] == "human:ana" and accepted["status_at"]
    # From then on the owner sets the colour, and the history says who said what.
    yellow = post(api, f"goals/{mine['id']}/status", {"status": "yellow", "note": "Backend is late."}, token="ben-test")["goal"]
    assert yellow["status"] == "yellow"
    assert [e["new"] for e in yellow["events"] if e["field"] == "status"] == ["green", "yellow"]
    # Cara is neither the owner nor above him.
    post(api, f"goals/{mine['id']}/status", {"status": "red", "note": "Nope."}, token="cara-test", expected=403)


def test_a_person_goal_with_goals_under_it_becomes_the_company_goal_on_upgrade(api):
    """Before an owner existed for goals, a goal with no parent and goals under it was the company goal."""
    old = post(api, "goals", {"title": "Grow", "owner": "ana"})["goal"]
    post(api, "goals", {"title": "Ship", "owner": "ben", "parent_id": old["id"]})
    alone = post(api, "goals", {"title": "Rest", "owner": "cara"})["goal"]
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM cloud_migrations WHERE version=43")
    api.app.state.store.initialize(seed_market=False)
    assert owner_of(api, old["id"]) == "company" and owner_of(api, alone["id"]) == "human:cara"


def owner_of(api, goal_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT owner FROM goals WHERE id=?", (goal_id,)).fetchone()[0]


def test_a_finished_goal_is_reopened_by_whoever_may_set_its_colour_and_undo_puts_its_colour_back(api):
    top = company_goal(api)
    mine = post(api, "goals", {"title": "Ship the rental stats page", "owner": "me", "parent_id": top["id"]},
                token="ben-test")["goal"]
    post(api, f"goals/{mine['id']}/status", {"status": "green", "note": "Design is done."})
    post(api, f"goals/{mine['id']}/status", {"status": "yellow", "note": "Backend is late."}, token="ben-test")
    # Only a finished goal is reopened.
    post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": "Back on"}, token="ben-test", expected=422)
    post(api, f"goals/{mine['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    # Cara may not set its colour, so she may not reopen it; a reason is required.
    post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": "Mine now"}, token="cara-test", expected=403)
    post(api, f"goals/{mine['id']}/reopen", {"status": "green", "note": ""}, token="ben-test", expected=422)
    # Undo passes the colour and the note it had: both come back, and the history says it was undone.
    back = post(api, f"goals/{mine['id']}/reopen", {"status": "yellow", "note": "Backend is late.", "undo": True},
                token="ben-test")["goal"]
    assert (back["status"], back["status_note"], back["status_source"]) == ("yellow", "Backend is late.", "person")
    assert back["rank"] is not None
    last = [e for e in back["events"] if e["field"] == "status"][-1]
    assert (last["old"], last["new"], last["note"]) == ("dropped", "yellow", "Undo remove: Backend is late.")
    # Done, then reopened with a new colour and one line why.
    post(api, f"goals/{mine['id']}/status", {"status": "done", "note": ""}, token="ben-test")
    again = post(api, f"goals/{mine['id']}/reopen", {"status": "red", "note": "The launch slipped"},
                 token="ben-test")["goal"]
    assert (again["status"], again["status_by"], again["status_note"]) == ("red", "human:ben", "The launch slipped")
    last = [e for e in again["events"] if e["field"] == "status"][-1]
    assert (last["old"], last["new"], last["note"]) == ("done", "red", "Reopened: The launch slipped")


def test_reopening_with_auto_puts_back_the_colour_it_had_and_a_proposal_stays_one(api):
    top = company_goal(api)
    proposed = post(api, "goals", {"title": "Ship the rental stats page", "owner": "me", "parent_id": top["id"]},
                    token="ben-test")["goal"]
    post(api, f"goals/{proposed['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    # Dropping and reopening is not a way round the acceptance: Ben's own colour is refused, Ana's is the acceptance.
    post(api, f"goals/{proposed['id']}/reopen", {"status": "green", "note": "Accept it"}, token="ben-test",
         expected=403)
    back = post(api, f"goals/{proposed['id']}/reopen", {"status": "auto", "note": "Removed by mistake",
                                                         "undo": True}, token="ben-test")["goal"]
    assert back["status"] is None and back["status_source"] == "auto"
    # A goal the CEO Bot coloured gets that colour back when the arithmetic has nothing new to say.
    post(api, f"goals/{proposed['id']}/status", {"status": "green", "note": "Accepted."})
    post(api, f"goals/{proposed['id']}/status/auto", {})
    post(api, f"goals/{proposed['id']}/status", {"status": "dropped", "note": ""}, token="ben-test")
    auto = post(api, f"goals/{proposed['id']}/reopen", {"status": "auto", "note": "Removed by mistake"},
                token="ben-test")["goal"]
    assert auto["status_source"] == "auto" and auto["status"] is not None and auto["status"] != "dropped"
