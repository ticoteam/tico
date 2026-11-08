"""The Growth page as a place of cards (backend/cards.py): who arranges it (admins and bots, a bot only its own cards),
who reads it (everyone who may see the Dashboard), the month the cards read, what each growth template shows its reader
(the cohorts, KPIs and marketing tests growth_numbers reads), and the growth templates kept to that page. Examples use
Acme, a home-cleaning company."""

import uuid

from backend.store import H
from backend.tests.test_api import api, get, headers, post, put, setup_attempt  # noqa: F401
from backend.tests.test_company import bot  # noqa: F401

GROWTH_NUMBERS = ["new_customers", "cac", "spend", "ltv_cac", "payback_months", "kept_d30"]


def page(api, bot_slug, title="Growth notes"):
    """A bot's page, straight into the database."""
    fid = "file-" + uuid.uuid4().hex[:24]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,task_id,first_activity_at,"
                  "last_activity_at) VALUES(?,?,?,?,?,'file','text/html','tico_blob',NULL,?,?)",
                  (fid, bot_slug, "bot", fid, title, H.now(), H.now()))
        c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)", (fid, H.now(), H.now()))
    return fid


def growth(api, query="", token="ana-test", expected=200):
    r = api.get("/api/v2/places/growth" + query, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def seed(api):
    """Acme's growth for September and October: two channels, the Unknown row, the KPI tree and two marketing tests."""
    put(api, "growth/settings", {"revenue_month": 90, "margin_pct": 40})
    post(api, "growth/channels", {"slug": "google-search", "name": "Google Search", "sort": 1})
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals", "sort": 2})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-09", "spend": 3000, "new_customers": 50,
                                "kept_d30": 75})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 3400, "bot_cost": 260,
                                "new_customers": 71, "kept_d30": 81, "kept_d90": 64, "kept_d180": 52})
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-10", "spend": 500, "new_customers": 20})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18})
    north = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{north['id']}", {"growth_role": "north_star"})
    post(api, f"kpis/{north['id']}/readings", {"value": 50, "period_end": "2026-10-31T00:00:00Z"})
    for name, stage, value in (("Visits", "reach", 1000), ("Sign-ups", "acquire", 200)):
        kpi = post(api, "kpis", {"name": name})["kpi"]
        post(api, f"kpis/{kpi['id']}", {"stage": stage})
        post(api, f"kpis/{kpi['id']}/readings", {"value": value, "period_end": "2026-10-31T00:00:00Z"})


def marketing(api, title, channel, step="idea-open", owner="ops", **more):
    """A marketing idea tagged with its channel, moved to its step (Idea by default)."""
    tid = post(api, "tasks", {"title": title, "body": "Try it.", "owner": owner, "type": "idea",
                              "labels": [f"channel:{channel}"], **more})["id"]
    if step != "idea-open":
        version = get(api, "tasks/" + tid)["task"]["version"]
        post(api, "tasks/" + tid, {"version": version, "step": step})
    return tid


def test_the_growth_page_starts_with_its_cards_and_admins_and_bots_arrange_it(api):
    view = growth(api)
    assert view["saved"] is False and view["can_edit"] is True and view["kind"] == "growth"
    assert [p["card"]["template"] for p in view["pins"]] == (
        ["growth_funnel"] + ["growth_number"] * 6 + ["growth_channels", "growth_retention", "growth_board"])
    assert [p["card"]["settings"]["metric"] for p in view["pins"][1:7]] == GROWTH_NUMBERS
    assert view["months"] == [] and all(p["card"]["hidden"] for p in view["pins"] if p["card"]["template"] == "growth_number")
    assert growth(api, token="cara-test")["can_edit"] is False          # a member reads it, arranges nothing
    put(api, "places/growth", {}, "cara-test", expected=403)
    fid = page(api, "ops")
    post(api, "pins", {"place": "growth", "item": "page:" + fid}, "cara-test", expected=403)
    # A bot arranges the Growth page for its own cards only: no pages there.
    _, _, attempt = setup_attempt(api, "ops")
    bot_token = attempt["token"]
    assert growth(api, token=bot_token)["can_edit"] is True
    card = post(api, "cards", {"template": "growth_funnel"}, bot_token)["card"]
    post(api, "pins", {"place": "growth", "item": "card:" + card["id"]}, bot_token)
    post(api, "pins", {"place": "growth", "item": "page:" + fid}, bot_token, expected=403)
    # Everyone sees it, so an audience is refused; the first change saved the defaults as real pins.
    put(api, "places/growth", {"audience": {"everyone": False}}, expected=422)
    saved = growth(api)
    assert saved["saved"] is True and all(p["id"] for p in saved["pins"]) and len(saved["pins"]) == 11
    assert len(growth(api, token="cara-test")["pins"]) == 11


def test_growth_cards_read_the_month_picked_and_carry_a_figure_back_to_the_last_month_with_one(api):
    seed(api)
    view = growth(api)
    assert view["month"] == "2026-10" and view["months"] == ["2026-09", "2026-10"]
    growth(api, "?month=2026-13", expected=422)
    growth(api, "?month=October", expected=422)
    with api.app.state.store.read() as c:
        from backend import growth as G
        eco = {m: G.economics(c, m)["total"] for m in ("2026-09", "2026-10")}
    cac = next(p["card"] for p in view["pins"] if p["card"]["settings"].get("metric") == "cac")
    assert cac["data"]["value"] == eco["2026-10"]["cac"] and cac["data"]["prev"] == eco["2026-09"]["cac"] == 60.0
    assert cac["data"]["from"] == "" and cac["data"]["good"] == "down" and cac["data"]["format"] == "usd"
    # November has no cohorts yet: retention is the October figure, from October, and its prior month's is September's.
    later = growth(api, "?month=2026-11")
    kept = next(p["card"] for p in later["pins"] if p["card"]["settings"].get("metric") == "kept_d30")
    assert later["month"] == "2026-11" and later["months"] == ["2026-09", "2026-10"]
    assert (kept["data"]["value"], kept["data"]["from"], kept["data"]["prev"]) == (81, "2026-10", 75)
    assert [s["value"] for s in kept["data"]["series"]] == [75, 81, None]
    cac_later = next(p["card"] for p in later["pins"] if p["card"]["settings"].get("metric") == "cac")
    assert cac_later["data"]["value"] is None and cac_later["data"]["from"] == ""


def test_the_growth_templates_read_the_seeded_install_for_what_the_reader_may_see(api):
    seed(api)
    shipped = marketing(api, "Google ads test", "google-search", step="idea-doing")
    marketing(api, "Referral scaling", "google-search", step="idea-scaling")
    marketing(api, "Flyers at the market", "referrals")
    marketing(api, "Old banner", "google-search", step="idea-paused")
    marketing(api, "Inbox test", "google-search", step="idea-doing", owner="inbox", private=True)
    cards = {p["card"]["template"]: p["card"]["data"] for p in growth(api)["pins"] if p["card"]["template"] != "growth_number"}
    with api.app.state.store.read() as c:
        from backend import growth as G
        eco = G.economics(c, "2026-10")
    # Funnel: Visits, then Sign-ups at 20% of them; the North Star on its own.
    stages = cards["growth_funnel"]["stages"]
    assert [s["label"] for s in stages] == ["Reach", "Acquire", "Activate", "Retain", "Refer"]
    assert (stages[0]["value"], stages[1]["value"], stages[1]["conv"]) == (1000, 200, 20.0)
    assert stages[2]["kpi"] is None and stages[2]["value"] is None and stages[0]["conv"] is None
    assert cards["growth_funnel"]["north_star"]["name"] == "Active customers"
    assert cards["growth_funnel"]["north_star"]["value"] == 50
    # A September reading and September picked: the funnel and North Star read as of September's end.
    north_id = cards["growth_funnel"]["north_star"]["id"]
    post(api, f"kpis/{north_id}/readings", {"value": 30, "period_end": "2026-09-30T00:00:00Z"})
    sept = {p["card"]["template"]: p["card"]["data"] for p in growth(api, "?month=2026-09")["pins"]
            if p["card"]["template"] == "growth_funnel"}["growth_funnel"]
    assert (sept["north_star"]["value"], sept["north_star"]["prev"]) == (30, None)
    assert sept["stages"][0]["value"] is None and sept["stages"][1]["conv"] is None
    # Channels: each live channel's own row, the open tests (Scaling then Testing; paused and private ones left out),
    # Unknown last.
    rows = {r["slug"]: r for r in cards["growth_channels"]["rows"]}
    google, referrals = rows["google-search"], rows["referrals"]
    assert [r["slug"] for r in cards["growth_channels"]["rows"]] == ["google-search", "referrals", "unknown"]
    assert (google["new_customers"], google["cac"]) == (71, eco["rows"][0]["cac"])
    assert google["ltv_cac"] == next(r for r in eco["rows"] if r["channel"] == "google-search")["ltv_cac"]
    assert google["series"] == [50, 71] and google["more"] == 0
    # Ana reads the private test too; the paused one is in no lane.
    assert [t["stage"] for t in google["tasks"]] == ["scaling", "testing", "testing"]
    assert google["tasks"][0]["title"] == "Referral scaling"
    assert {t["title"] for t in google["tasks"]} == {"Referral scaling", "Google ads test", "Inbox test"}
    assert referrals["tasks"] == [{"id": referrals["tasks"][0]["id"], "title": "Flyers at the market", "stage": "idea"}]
    assert rows["unknown"]["new_customers"] == 18 and rows["unknown"]["tasks"] == []
    # Retention: September then October, the newest last; a month with no 90 or 180 days is null.
    retention = cards["growth_retention"]
    assert retention["columns"] == ["30 days", "90 days", "180 days"]
    assert [r["month"] for r in retention["rows"]] == ["2026-09", "2026-10"]
    assert retention["rows"][0]["kept"] == [75, None, None] and retention["rows"][1]["kept"] == [81, 64, 52]
    # The board: Testing, Scaling, Idea; each lane's count is all of its tests the reader may see, and its newest one.
    lanes = {lane["stage"]: lane for lane in cards["growth_board"]["lanes"]}
    assert [lane["label"] for lane in cards["growth_board"]["lanes"]] == ["Testing", "Scaling", "Idea"]
    assert lanes["testing"]["count"] == 2 and lanes["scaling"]["count"] == 1 and lanes["idea"]["count"] == 1
    assert shipped in {c["id"] for c in lanes["testing"]["cards"]}
    assert lanes["scaling"]["cards"][0]["channel"] == "Google Search"
    # The private test is Ana's alone: Cara sees one test in Testing, not two.
    cara = {p["card"]["template"]: p["card"]["data"] for p in growth(api, token="cara-test")["pins"]
            if p["card"]["template"] == "growth_board"}
    assert cara["growth_board"]["lanes"][0]["count"] == 1
    # `count` keeps the first live channels (Unknown still comes last when it has a row); one channel's Number names it.
    one = post(api, "cards", {"template": "growth_channels", "place": "growth", "settings": {"count": 1}})["card"]
    assert [r["slug"] for r in one["data"]["rows"]] == ["google-search", "unknown"]
    ads = post(api, "cards", {"template": "growth_number", "place": "growth",
                              "settings": {"metric": "new_customers", "channel": "google-search"}})["card"]
    assert (ads["data"]["value"], ads["data"]["channel"], ads["data"]["channel_name"]) == (71, "google-search",
                                                                                           "Google Search")


def test_growth_cards_are_checked_and_kept_to_the_growth_page(api):
    put(api, "places/growth", {})
    for body in ({"template": "growth_number", "settings": {"metric": "nope"}},
                 {"template": "growth_number", "settings": {}},
                 {"template": "growth_number", "settings": {"metric": "cac", "channel": "missing"}},
                 {"template": "growth_number", "settings": {"metric": "cac", "count": 2}},
                 {"template": "growth_channels", "settings": {"count": 21}},
                 {"template": "growth_retention", "settings": {"months": 2}},
                 {"template": "growth_board", "settings": {"count": 6}}):
        post(api, "cards", body, expected=422)
    refused = api.post("/api/v2/cards", json={"template": "growth_number", "settings": {"metric": "cac"},
                                              "place": "dashboard"}, headers=headers())
    assert refused.status_code == 422 and "Number goes on the Growth page" in refused.text
    card = post(api, "cards", {"template": "growth_funnel"})["card"]
    post(api, "pins", {"place": "dashboard", "item": "card:" + card["id"]}, expected=422)
    post(api, "pins", {"place": "growth", "item": "card:" + card["id"]})
    kpi_card = post(api, "cards", {"template": "growth_funnel", "place": "growth"})["card"]
    assert kpi_card["template"] == "growth_funnel"
    choices = get(api, "cards/choices?place=growth")
    elsewhere = get(api, "cards/choices")
    growth_ids = {"growth_number", "growth_funnel", "growth_channels", "growth_retention", "growth_board"}
    assert {t["id"] for t in choices["templates"]} == growth_ids | {"kpi", "latest_pages"}
    assert not growth_ids & {t["id"] for t in elsewhere["templates"]} and "kpi" in {t["id"] for t in elsewhere["templates"]}
    assert [m["id"] for m in choices["metrics"]][:3] == ["new_customers", "spend", "bot_cost"]
    assert choices["channels"] == []
