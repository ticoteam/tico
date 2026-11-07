"""A thread with no title is named by the people in it: the viewer left out, at most three names and "+N" for the rest,
"You" when nobody else is in it. A title wins and an empty one brings the names back; a change of members changes the
name; search hits and breadcrumbs say the same name; a DM is still named by the other person. Fictional people only:
ana (owner), ben and cara, plus eve and fay (seeded here)."""

from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_thread_api import get_thread, new_thread, say, search


def rename(api, thread_id, title, token="ana-test"):
    response = api.patch(f"/api/v2/threads/{thread_id}", json={"title": title}, headers=headers(token))
    assert response.status_code == 200, response.text


def name_as(api, thread_id, token):
    return get_thread(api, thread_id, token).json()["name"]


def untitled(api, members):
    """A thread whose title is cleared, as a members thread with no name left: the starter (ana) and `members`."""
    thread = new_thread(api, title="Budget", members=members)
    rename(api, thread["id"], "")
    return thread["id"]


def seed_people(api, *slugs):
    with api.app.state.store.transaction() as c:
        for slug in slugs:
            c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (slug, "", slug + "@acme.example"))


def test_an_untitled_thread_is_named_by_its_people_and_the_viewer_is_left_out(api):
    thread = untitled(api, members=("human:ben", "human:cara"))
    assert name_as(api, thread, "ana-test") == "Ben, Cara"
    assert name_as(api, thread, "ben-test") == "Ana, Cara"
    assert name_as(api, thread, "cara-test") == "Ana, Ben"


def test_a_thread_alone_is_named_you_and_a_title_wins_until_it_is_cleared(api):
    thread = untitled(api, members=())
    assert name_as(api, thread, "ana-test") == "You"
    rename(api, thread, "Launch plan")
    assert name_as(api, thread, "ana-test") == "Launch plan"
    rename(api, thread, "")
    assert name_as(api, thread, "ana-test") == "You"


def test_more_than_three_people_show_three_names_and_a_count_for_the_rest(api):
    seed_people(api, "eve", "fay")
    thread = untitled(api, members=("human:ben", "human:cara", "human:eve", "human:fay"))
    assert name_as(api, thread, "ana-test") == "Ben, Cara, Eve +1"
    assert name_as(api, thread, "cara-test") == "Ana, Ben, Eve +1"


def test_adding_or_removing_a_person_changes_the_name(api):
    thread = untitled(api, members=("human:ben",))
    assert name_as(api, thread, "ana-test") == "Ben"
    added = api.post(f"/api/v2/threads/{thread}/members", json={"actor": "human:cara"}, headers=headers("ana-test"))
    assert added.status_code == 200, added.text
    assert name_as(api, thread, "ana-test") == "Ben, Cara"
    removed = api.delete(f"/api/v2/threads/{thread}/members/human:ben", headers=headers("ana-test"))
    assert removed.status_code == 200, removed.text
    assert name_as(api, thread, "ana-test") == "Cara"


def test_a_dm_is_still_named_by_the_other_person(api):
    dm = api.post("/api/v2/threads/dm", json={"members": ["human:ben"]}, headers=headers("ana-test")).json()
    assert name_as(api, dm["id"], "ana-test") == "Ben"
    assert name_as(api, dm["id"], "ben-test") == "Ana"


def test_a_search_hit_and_a_breadcrumb_carry_the_same_name_as_the_thread(api):
    thread = untitled(api, members=("human:ben", "human:cara"))
    topic = say(api, thread, "Kickoff agenda")
    hits = search(api, "kickoff", "ben-test")["hits"]
    assert [(h["thread_id"], h["title"]) for h in hits] == [(thread, "Ana, Cara")]
    sub = new_thread(api, title="Side", members=(), parent_message_id=topic["id"])
    path = get_thread(api, sub["id"], "ana-test").json()["breadcrumb"]
    assert path == [{"id": thread, "title": "Ben, Cara"}, {"id": sub["id"], "title": "Side"}]
