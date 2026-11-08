"""A manager's reports keep the order someone put them in on the team chart (backend/report_order.py): stored on the
server for every viewer, a new report goes last, and only someone who may put reports under that manager orders them."""
from backend.tests.test_api import api, get, headers, post, put, setup_attempt  # noqa: F401
from backend.tests.test_org_chart import revision, roster


def slugs(api):
    """Bot id to slug: the order holds ids, these tests compare the slugs people see."""
    with api.app.state.store.read() as c:
        return {r["id"]: r["slug"] for r in c.execute("SELECT id,slug FROM bots")}


def order(api, manager, token="ana-test"):
    names = slugs(api)
    return [names.get(r["id"], r["id"]) for r in get(api, "org/order/" + manager, token=token)["reports"]]


def report_to(api, bot, boss, token="ana-test", expected=200):
    post(api, f"bots/{bot}/definition", {"reports_to": boss, "expected_revision": revision(api, bot)},
         token=token, expected=expected)


def chart(api):
    roster(api, [{"id": "ana", "email": "ana@acme.example", "name": "Ana", "primary_for": ["*"]},
                 {"id": "ben", "email": "ben@acme.example", "name": "Ben", "reports_to": "ana"},
                 {"id": "cara", "email": "cara@acme.example", "name": "Cara", "reports_to": "ana"}])
    for bot in ("ops", "finance", "doc-updater"):
        report_to(api, bot, "coo")


def test_the_order_is_kept_for_everyone_and_a_new_report_goes_last(api):
    chart(api)
    assert order(api, "coo") == ["doc-updater", "finance", "ops"]          # nobody placed them: by name
    put(api, "org/order/coo", {"reports": ["ops", "doc-updater", "finance"]})
    assert order(api, "coo") == order(api, "coo", token="cara-test") == ["ops", "doc-updater", "finance"]
    places = {e["name"]: e.get("report_position") for e in api.get("/api/employees", headers=headers("cara-test")).json()}
    assert places["ops"] == 0 and places["finance"] == 2 and places["cpo"] is None
    # Naming two swaps just those two; the others keep their places.
    put(api, "org/order/coo", {"reports": ["finance", "ops"]})
    assert order(api, "coo") == ["finance", "doc-updater", "ops"]
    # A new report goes last, whatever its name; one that leaves and comes back is new again.
    report_to(api, "cpo", "coo")
    assert order(api, "coo") == ["finance", "doc-updater", "ops", "cpo"]
    coo = next(n for n in get(api, "updates/tree?scope=all")["roots"][0]["children"] if n["key"] == "b:coo")
    assert [n["key"] for n in coo["children"]] == ["b:finance", "b:doc-updater", "b:ops", "b:cpo"], "Updates too"
    report_to(api, "ops", "human:ben")
    report_to(api, "ops", "coo")
    assert order(api, "coo") == ["finance", "doc-updater", "cpo", "ops"]
    # Humans are ordered under a human the same way, and only direct reports can be named.
    put(api, "org/order/ana", {"reports": ["cara", "ben"]})
    assert order(api, "human:ana")[:2] == ["cara", "ben"]
    for wrong in (["nobody"], ["ben"], ["ops", "ops"]):
        put(api, "org/order/coo", {"reports": wrong}, expected=422)
    get(api, "org/order/nobody", expected=404)
    report_to(api, "finance", "inbox")                     # only Ana sees the mail bot: to Cara it is nobody
    get(api, "org/order/inbox", token="cara-test", expected=404)
    assert order(api, "inbox") == ["finance"]


def test_only_someone_who_may_put_reports_there_orders_them(api):
    chart(api)
    put(api, "org/order/coo", {"reports": ["ops", "finance", "doc-updater"]})
    # Cara is a member nothing reports up to: she sees the order and may not change it.
    view = get(api, "org/order/coo", token="cara-test")
    assert view["can_order"] is False
    put(api, "org/order/coo", {"reports": ["finance", "ops"]}, token="cara-test", expected=403)
    # Nor may she move a report onto another manager: nothing moves, and the order stays.
    report_to(api, "ops", "human:cara", token="cara-test", expected=403)
    assert order(api, "coo") == ["ops", "finance", "doc-updater"]
    from backend.tests.test_api import restrict
    # The person who runs the COO may order its reports, though nothing reports up to him; Cara still may not.
    from backend.tests.test_api import as_member
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='coo'")
    put(api, "org/order/coo", {"reports": ["finance", "ops"]}, token="ben-test")
    put(api, "org/order/coo", {"reports": ["ops", "finance"]}, token="cara-test", expected=403)
    assert order(api, "coo") == ["finance", "ops", "doc-updater"]
    # A report he may not see keeps its place when he orders the others.
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    assert order(api, "coo", token="ben-test") == ["finance", "doc-updater"]
    put(api, "org/order/coo", {"reports": ["doc-updater", "finance"]}, token="ben-test")
    assert order(api, "coo") == ["doc-updater", "ops", "finance"], "Ops stays where it was, between them"
    # Once the COO reports to her, she may order its reports.
    report_to(api, "coo", "human:cara")
    assert get(api, "org/order/coo", token="cara-test")["can_order"] is True
    put(api, "org/order/coo", {"reports": ["finance", "ops"]}, token="cara-test")
    # A bot orders its own reports, and no other bot's.
    _, _, coo = setup_attempt(api, "coo")
    _, _, ops = setup_attempt(api, "ops")
    put(api, "org/order/coo", {"reports": ["doc-updater", "ops", "finance"]}, token=coo["token"])
    put(api, "org/order/coo", {"reports": ["ops"]}, token=ops["token"], expected=403)
    assert order(api, "coo") == ["doc-updater", "ops", "finance"]


def test_reports_that_join_go_last_in_the_order_they_came_whatever_their_names(api):
    from backend.tests.test_bot_contact import bot
    chart(api)
    put(api, "org/order/coo", {"reports": ["ops", "doc-updater", "finance"]})
    bot(api, "zulu", "Zulu", reports_to="coo")
    bot(api, "alpha", "Alpha", reports_to="coo")
    assert order(api, "coo") == ["ops", "doc-updater", "finance", "zulu", "alpha"]
    report_to(api, "zulu", "human:ben")                    # leaves, and comes back last
    report_to(api, "zulu", "coo")
    assert order(api, "coo") == order(api, "coo", token="cara-test") == ["ops", "doc-updater", "finance", "alpha", "zulu"]
    coo = next(n for n in get(api, "updates/tree?scope=all")["roots"][0]["children"] if n["key"] == "b:coo")
    names = slugs(api)
    assert [names.get(n["key"].removeprefix("b:"), n["key"]) for n in coo["children"]] == [
        "ops", "doc-updater", "finance", "alpha", "zulu"]
    places = {e["name"]: e.get("report_position") for e in api.get("/api/employees", headers=headers("cara-test")).json()}
    assert sorted(("ops", "doc-updater", "finance", "alpha", "zulu"), key=places.get) == [
        "ops", "doc-updater", "finance", "alpha", "zulu"], "the chart reloads the same places"
    # Humans the same: added to the team, or moved under the manager, each goes last.
    put(api, "org/order/ana", {"reports": ["cara", "ben"]})
    zed = post(api, "access/people", {"name": "Zed", "email": "zed@acme.example", "reports_to": "ana"})["person"]
    abe = post(api, "access/people", {"name": "Abe", "email": "abe@acme.example", "reports_to": "ana"})["person"]
    post(api, "people/cara", {"reports_to": "ben"})
    post(api, "people/cara", {"reports_to": "ana"})
    assert [r for r in order(api, "human:ana") if r in ("cara", "ben", zed, abe)] == ["ben", zed, abe, "cara"]
    places = {p["id"]: p.get("report_position") for p in api.get("/api/people", headers=headers("cara-test")).json()["people"]}
    assert sorted(("cara", "ben", zed, abe), key=places.get) == ["ben", zed, abe, "cara"], "the chart reloads the same places"


def test_reports_nobody_placed_follow_one_rule_humans_then_a_bots_order_then_names(api):
    import json
    from backend.store import encode
    roster(api, [{"id": "ana", "email": "ana@acme.example", "name": "Ana", "primary_for": ["*"]},
                 {"id": "zoe", "email": "zoe@acme.example", "name": "Zoë", "reports_to": "ana"},
                 {"id": "eli", "email": "eli@acme.example", "name": "Éli", "reports_to": "ana"},
                 {"id": "bea", "email": "bea@acme.example", "name": "bea", "reports_to": "ana"}])
    for slug in ("ops", "finance", "doc-updater"):
        report_to(api, slug, "coo")
    with api.app.state.store.transaction() as c:          # a bot's own `order`, from its definition
        for slug, place in (("ops", 1), ("finance", 2)):
            config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()[0])
            c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode({**config, "order": place}), slug))
    assert order(api, "coo") == ["ops", "finance", "doc-updater"], "ordered bots first, lowest first, then names"
    assert [r for r in order(api, "human:ana") if r in ("zoe", "eli", "bea")] == ["bea", "eli", "zoe"], (
        "by name, accents and case aside, not as the roster lists them")
    # A place someone saved comes first; the ones with none keep the same rule after it.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('report_order',?)",
                  (encode({"bot:doc-updater": {"manager": "bot:coo", "position": 0}}),))
    assert order(api, "coo") == ["doc-updater", "ops", "finance"]
    coo = next(n for n in get(api, "updates/tree?scope=all")["roots"][0]["children"] if n["key"] == "b:coo")
    assert [n["key"] for n in coo["children"]] == ["b:doc-updater", "b:ops", "b:finance"], "Updates the same"
