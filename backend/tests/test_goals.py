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
