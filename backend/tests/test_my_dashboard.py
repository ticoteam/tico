"""My dashboard, the `person:<id>` place (backend/cards.py): its person alone reads and arranges it, an admin has no
way in, BotOps arranges it only for its person, and its cards widen nothing: each shows its reader only what they may
already read.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture)."""

from backend.store import H
from backend.tests.test_api import api, get, post, put  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_cards import delete, page, patch, place, task
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (fixture)


def templates(view):
    return [p["card"]["template"] for p in view["pins"] if "card" in p]


def test_my_dashboard_starts_with_its_defaults_and_is_the_callers_own(api):
    mine = place(api, "person:me", "cara-test")
    assert mine["place"] == "person:cara" and mine["kind"] == "person" and mine["can_edit"] is True
    assert templates(mine) == ["needs_attention", "unread_threads", "my_tasks", "updates", "latest_pages"]
    assert all(not p["card"]["settings"].get("team") for p in mine["pins"])
    assert place(api, "person:cara", "cara-test")["place"] == "person:cara"
    saved = put(api, "places/person:me", {}, "cara-test")
    assert saved["saved"] is True and all(p["id"] for p in saved["pins"])
    put(api, "places/person:me", {"audience": {"everyone": False, "groups": []}}, "cara-test", expected=422)


def test_nobody_else_reads_or_arranges_it_not_even_an_admin(api):
    made = post(api, "cards", {"template": "my_tasks", "settings": {"count": 3}, "place": "person:me"}, "cara-test")
    cid, pid = made["card"]["id"], made["pin"]
    for token in ("ana-test", "ben-test"):                  # the owner and an admin
        place(api, "person:cara", token, expected=404)
        put(api, "places/person:cara", {}, token, expected=404)
        post(api, "pins", {"place": "person:cara", "item": "card:" + cid}, token, expected=404)
        post(api, "cards", {"template": "updates", "place": "person:cara"}, token, expected=404)
        post(api, "places/person:cara/order", {"ids": [pid]}, token, expected=404)
        patch(api, "pins/" + pid, {"size": "large"}, token, expected=404)
        delete(api, "pins/" + pid, token, expected=404)
        patch(api, "cards/" + cid, {"title": "Mine now"}, token, expected=404)
        delete(api, "cards/" + cid, token, expected=404)
        assert cid not in [c["id"] for c in get(api, "cards", token)["cards"]]
        assert get(api, "cards?ids=" + cid, token)["cards"] == []
    # Cara's own card only: a card someone else made is not pinned to her dashboard.
    theirs = post(api, "cards", {"template": "tasks_by_status", "place": "dashboard"})["card"]["id"]
    post(api, "pins", {"place": "person:me", "item": "card:" + theirs}, "cara-test", expected=403)
    patch(api, "cards/" + cid, {"title": "Mine"}, "cara-test")
    assert place(api, "person:me", "cara-test")["pins"][-1]["card"]["heading"] == "Mine"


def test_a_pin_there_widens_nothing(api):
    """A page Cara may not read stays unreadable to her pinned or not, and her pin lends no one else her rights."""
    hidden = page(api, "inbox")                             # Ana's own bot: Cara may not read its pages
    post(api, "pins", {"place": "person:me", "item": "page:" + hidden}, "cara-test", expected=404)
    shared = page(api, "ops")
    post(api, "pins", {"place": "person:me", "item": "page:" + shared}, "cara-test")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM pins WHERE place='person:cara' AND item=?",
                         ("page:" + shared,)).fetchone()[0] == 1
    # Ana pins her private bot's page to her own dashboard: Cara still cannot read it.
    post(api, "pins", {"place": "person:me", "item": "page:" + hidden})
    get(api, f"files/{hidden}/activity", "cara-test", expected=404)


def test_its_cards_show_each_reader_only_their_own(api):
    task(api, "human:cara", "Cara's report", due="2026-11-03")
    task(api, "human:ana", "Ana's private thing", private=True)
    task(api, "bot:inbox", "Inbox work")                    # Ana's bot: not Cara's to read
    mine = place(api, "person:me", "cara-test")
    cards = {p["card"]["template"]: p["card"] for p in mine["pins"]}
    assert [t["title"] for t in cards["my_tasks"]["data"]["tasks"]] == ["Cara's report"]
    assert cards["my_tasks"]["data"]["count"] == 1
    # Her own task's thread may be unread for her; Ana's private task and Ana's bot's work never show.
    assert {t["title"] for t in cards["unread_threads"]["data"]["threads"]} <= {"Cara's report"}
    assert cards["needs_attention"]["data"]["count"] >= 0
    assert set(cards["updates"]["data"]) == {"unread", "updates"}
    ana = {p["card"]["template"]: p["card"] for p in place(api, "person:me")["pins"]}
    assert "Ana's private thing" in [t["title"] for t in ana["my_tasks"]["data"]["tasks"]]
    assert "Cara's report" not in [t["title"] for t in ana["my_tasks"]["data"]["tasks"]]


def test_botops_arranges_it_for_its_person_only(api, botops):
    cara = turn(api, botops, person="cara-test", text="Put my tasks first on my dashboard")
    view = act(api, cara, "GET", "places/person:me")
    assert view.status_code == 200 and view.json()["place"] == "person:cara"
    made = act(api, cara, "POST", "cards", {"template": "updates", "settings": {"count": 2}, "place": "person:me"})
    assert made.status_code == 200, made.text
    assert act(api, cara, "GET", "places/person:ana").status_code == 404
    assert act(api, cara, "POST", "cards", {"template": "updates", "place": "person:ana"}).status_code == 404
    with api.app.state.store.read() as c:
        row = c.execute("SELECT made_by FROM cards WHERE id=?", (made.json()["card"]["id"],)).fetchone()
        assert row["made_by"] == "human:cara"


def test_a_bot_has_no_my_dashboard_and_an_unknown_person_is_no_place(api):
    from backend.tests.test_api import setup_attempt
    attempt = setup_attempt(api)[2]
    get(api, "places/person:me", attempt["token"], expected=404)
    get(api, "places/person:ana", attempt["token"], expected=404)
    place(api, "person:nobody", expected=404)
    with api.app.state.store.read() as c:
        assert H.human(c, "cara")


def test_unread_threads_reads_cores_sidebar_and_only_what_is_unread(api, monkeypatch):
    """The card reads `threads.sidebar` (docs/dev/threads-contract.md §10), which lists only readable threads. Another
    bot's post is activity, not unread."""
    from backend import threads
    seen = []
    def sidebar(c, viewer, cursor=None, limit=50):
        seen.append(viewer.actor)
        me = lambda unread, activity=False: {"unread": unread, "activity": activity}   # noqa: E731
        return {"attention": [{"id": "th-a", "title": "Launch", "last_seq": 9, "last_message_at": "2026-11-01T10:00:00Z",
                               "me": me(2)}],
                "threads": [{"id": "th-a", "title": "Launch", "last_seq": 9, "me": me(2)},
                            {"id": "th-b", "title": "Pricing", "last_seq": 12, "me": me(0, True)},
                            {"id": "th-c", "title": "Read already", "last_seq": 3, "me": me(0)}],
                "dms": [{"id": "dm-1", "title": "Ana", "last_seq": 5, "me": me(1)}], "next_cursor": None}
    monkeypatch.setattr(threads, "sidebar", sidebar)
    cards = {p["card"]["template"]: p["card"] for p in place(api, "person:me", "cara-test")["pins"]}
    data = cards["unread_threads"]["data"]
    assert [t["id"] for t in data["threads"]] == ["th-a", "dm-1"]                  # newest first; th-b is activity only
    assert data["count"] == 2 and data["threads"][0]["unread"] == 2
    assert seen == ["human:cara"]


def test_its_writes_leave_events_that_name_nothing(api):
    """The event log is the company's (the owner reads it whole, `hub sql` included): a write to My dashboard leaves
    its event and names no card, page, template or place, before or after the card is gone."""
    from backend.tests.test_api import headers
    shared = page(api, "ops")
    made = post(api, "cards", {"template": "my_tasks", "settings": {"count": 3}, "place": "person:me"}, "cara-test")
    cid, pid = made["card"]["id"], made["pin"]
    post(api, "pins", {"place": "person:me", "item": "page:" + shared}, "cara-test")
    patch(api, "cards/" + cid, {"title": "Mine"}, "cara-test")
    other = post(api, "cards", {"template": "updates", "place": "person:me"}, "cara-test")["card"]["id"]
    delete(api, "cards/" + other, "cara-test")
    delete(api, "pins/" + pid, "cara-test")                # its only pin: the card goes with it
    unpin = [p["id"] for p in place(api, "person:me", "cara-test")["pins"] if p.get("item") == "page:" + shared]
    delete(api, "pins/" + unpin[0], "cara-test")
    # The page's own activity, read by whoever reads the page, does not say it was pinned there either.
    assert "person:" not in str(get(api, f"files/{shared}/activity"))
    query = {"sql": "SELECT action,target,detail_json FROM events WHERE actor='human:cara'"}
    rows = api.post("/api/v2/sql", json=query, headers=headers("ana-test")).json()["rows"]
    actions = {r[0] if isinstance(r, list) else r["action"] for r in rows}
    assert {"card.added", "pin.added", "card.changed", "pin.removed", "card.removed"} <= actions
    text = str(rows)
    for secret in (cid, other, shared, "person:cara", "my_tasks", "updates", "Mine"):
        assert secret not in text, secret
    # A shared place still says what changed.
    team = post(api, "cards", {"template": "tasks_by_status", "place": "dashboard"})["card"]["id"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='card.added' AND target=?", (team,)).fetchone()[0] == 1


def test_the_library_does_not_say_a_page_is_on_someone_elses_dashboard(api):
    fid = page(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET current_version=1 WHERE id=?", (fid,))     # listed once it has a version
    post(api, "pins", {"place": "person:me", "item": "page:" + fid}, "cara-test")
    def row(token, query=""):
        return [f for f in get(api, "files?limit=100" + query, token)["files"] if f["id"] == fid]
    assert row("cara-test")[0]["pins"] == ["person:cara"] and row("cara-test", "&pinned=true")
    assert row("ana-test")[0]["pins"] == [] and row("ana-test", "&pinned=true") == []
