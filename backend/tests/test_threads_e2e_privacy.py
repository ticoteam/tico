"""Who can open a thread, over the real API: the privacy boundaries of spec sections 1 and 9.

Ana owns the company (the owner), Ben is an admin, Cara is a plain person. Each test is one boundary a person would
hit: a private thread, a team thread and its sub-threads, a company thread, a DM the owner may not read, a bot's Home
for a person without its access, and a mention that grants nothing. The same matrix runs through the Python, `hub sql`
and SSE renderings in core's privacy fixture; these tests check the API a sidebar and a thread view call.
"""

from backend.tests.test_api import api, restrict  # noqa: F401  (the api fixture)
from backend.tests.threads_e2e import (HOME_ROUTE, add_member, call, can_read, create_thread, make_group, ok,
                                       read_status, remove_member, require_threads_api, say, search_ids, sidebar_ids,
                                       timeline)


def test_a_new_thread_is_private_to_its_starter_and_a_hidden_one_looks_missing(api):
    require_threads_api(api)
    t = create_thread(api, title="Pricing draft", body="Quarterly pricing, not for the team yet.")
    assert can_read(api, "ana-test", t["id"])
    assert t["id"] in sidebar_ids(api, "ana-test")
    assert read_status(api, "cara-test", t["id"]) == 404
    assert read_status(api, "ben-test", t["id"]) == 404
    # Hidden is the same answer as missing: nothing says the thread exists.
    missing = call(api, "GET", "threads/" + "0" * 32, "cara-test")
    hidden = call(api, "GET", f"threads/{t['id']}", "cara-test")
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json())
    assert t["id"] not in sidebar_ids(api, "cara-test")
    assert t["id"] not in search_ids(api, "cara-test", "Pricing")


def test_adding_a_member_gives_reading_and_removing_takes_it_back(api):
    require_threads_api(api)
    t = create_thread(api, title="Launch")
    assert can_read(api, "ana-test", t["id"])
    assert add_member(api, "ana-test", t["id"], "human:cara").status_code in (200, 201)
    assert can_read(api, "cara-test", t["id"])
    assert t["id"] in sidebar_ids(api, "cara-test")
    say(api, "cara-test", t["id"], "Looks good")
    assert remove_member(api, "ana-test", t["id"], "human:cara").status_code in (200, 204)
    assert read_status(api, "cara-test", t["id"]) == 404
    assert t["id"] not in sidebar_ids(api, "cara-test")


def test_a_team_thread_reads_by_its_group_and_its_sub_threads_follow_it(api):
    require_threads_api(api)
    make_group(api, "Marketing", people=["cara"])
    t = create_thread(api, title="Campaign", audience="team", group="marketing")
    m = say(api, "ana-test", t["id"], "Campaign outline")
    sub = say(api, "cara-test", t["id"], "Side question", parent_message_id=m["id"])
    assert can_read(api, "cara-test", t["id"])
    assert read_status(api, "ben-test", t["id"]) == 404, "an admin is not in the group"
    assert read_status(api, "ben-test", sub["thread_id"]) == 404
    assert can_read(api, "cara-test", sub["thread_id"])


def test_making_a_thread_public_to_the_company_opens_it_to_its_people(api):
    require_threads_api(api)
    t = create_thread(api, title="Handbook")
    assert read_status(api, "ben-test", t["id"]) == 404
    assert ok(call(api, "PATCH", f"threads/{t['id']}", "ana-test", {"audience": "company"}))["audience"] == "company"
    assert can_read(api, "ben-test", t["id"])
    assert can_read(api, "cara-test", t["id"])
    assert t["id"] in sidebar_ids(api, "ben-test")


def test_a_sub_thread_is_never_more_open_than_its_root_and_follows_root_changes(api):
    require_threads_api(api)
    t = create_thread(api, title="Budget", members=["human:cara"])
    m = say(api, "ana-test", t["id"], "Budget numbers")
    sub = say(api, "cara-test", t["id"], "Which year?", parent_message_id=m["id"])["thread_id"]
    assert read_status(api, "ben-test", sub) == 404
    ok(call(api, "PATCH", f"threads/{t['id']}", "ana-test", {"audience": "company"}))
    assert can_read(api, "ben-test", sub), "a public root opens its sub-threads"
    ok(call(api, "PATCH", f"threads/{t['id']}", "ana-test", {"audience": "members"}))
    assert read_status(api, "ben-test", sub) == 404, "narrowing the root narrows its sub-threads too"


def test_a_dm_is_read_by_its_two_people_and_not_by_the_owner(api):
    require_threads_api(api)
    dm = ok(call(api, "POST", "threads", "ben-test", {"audience": "personal", "members": ["human:cara"],
                                                       "title": ""}))
    assert can_read(api, "ben-test", dm["id"])
    assert can_read(api, "cara-test", dm["id"])
    assert read_status(api, "ana-test", dm["id"]) == 404, "the owner does not read a person's DM"
    assert dm["id"] not in sidebar_ids(api, "ana-test")


def test_a_person_without_a_bots_access_cannot_open_its_home(api):
    require_threads_api(api)
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", see={"everyone": True, "people": [], "teams": [], "bots": []},
                 read={"everyone": False, "people": ["ana"], "teams": [], "bots": []},
                 write={"everyone": False, "people": ["ana"], "teams": [], "bots": []})
    home = call(api, "GET", HOME_ROUTE.format(slug="cpo"), "ana-test")
    assert home.status_code == 200, "the owner reads the bot's Home: " + home.text
    thread_id = home.json()["id"]
    assert read_status(api, "cara-test", thread_id) == 404
    assert thread_id not in sidebar_ids(api, "cara-test")


def test_a_mention_outside_the_audience_warns_the_author_and_grants_nothing(api):
    require_threads_api(api)
    t = create_thread(api, title="Hiring")
    sent = say(api, "ana-test", t["id"], "@ben can you look at the loop?")
    warnings = sent.get("warnings") or []
    assert [w.get("rule") for w in warnings] == ["mention_outside"], sent
    assert warnings[0].get("actor") == "human:ben"
    assert read_status(api, "ben-test", t["id"]) == 404
    assert [m["body"] for m in timeline(api, "ana-test", t["id"])] == ["@ben can you look at the loop?"]
