"""Built-in cards and pins (backend/cards.py): a card shows each reader only what they may already see; the pinning
rule (a bot pins only its own pages and cards to its own sidebar or task, a person who arranges the place pins to the
Dashboard and team pages); a page pinned there by a person is readable by the place's audience, never a private
task's file; a team page's audience; and pages and cards as bot sidebar widgets."""

import re
import uuid
from types import SimpleNamespace

import pytest

from backend.store import H
from backend.tests.test_api import api, as_member, get, headers, post, put, restrict, setup_attempt  # noqa: F401
from backend.tests.test_groups import send


def place(api, name, token="ana-test", expected=200):
    return get(api, "places/" + name, token, expected)


def cards_of(view):
    return [(p["card"]["template"], p["card"]["settings"].get("team", "")) for p in view["pins"] if "card" in p]


def page(api, bot, title="Weekly numbers", task=None):
    """A page straight into the database: a bot-wide file, or a task's."""
    fid = "file-" + uuid.uuid4().hex[:24]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,task_id,first_activity_at,"
                  "last_activity_at) VALUES(?,?,?,?,?,'file','text/html','tico_blob',?,?,?)",
                  (fid, bot, "task:" + task if task else "bot", fid, title, task, H.now(), H.now()))
        c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)", (fid, H.now(), H.now()))
    return fid


def task(api, owner, title, private=False, **columns):
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", title, "Details.", owner, lint=False, private=private)["id"]
        for name, value in columns.items():
            c.execute(f"UPDATE tasks SET {name}=? WHERE id=?", (value, tid))
    return tid


def delete(api, path, token="ana-test", expected=200):
    r = api.delete("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_the_dashboard_starts_with_its_defaults_and_counts_only_what_each_reader_may_read(api):
    task(api, "bot:ops", "Ops work")
    task(api, "bot:inbox", "Mail work", status="doing")     # inbox is Ana's alone
    view = place(api, "dashboard", "cara-test")
    assert view["saved"] is False and view["can_edit"] is False
    assert cards_of(view) == [("tasks_by_status", ""), ("done_per_week", ""), ("needs_you", ""), ("latest_pages", "")]
    assert all(p["id"] is None for p in view["pins"])
    counts = lambda v: v["pins"][0]["card"]["data"]["counts"]  # noqa: E731
    assert counts(view)["open"] == 1 and counts(view)["doing"] == 0
    assert counts(place(api, "dashboard"))["doing"] == 1
    assert len(view["pins"][1]["card"]["data"]["weeks"]) == 8
    # Saving makes the defaults real; a member arranges nothing.
    put(api, "places/dashboard", {}, "cara-test", expected=403)
    saved = put(api, "places/dashboard", {})
    assert saved["saved"] is True and all(p["id"] for p in saved["pins"])
    assert [p["item"].startswith("card:") for p in saved["pins"]] == [True] * 4


def test_place_server_timing_names_only_card_kinds(api):
    response = api.get("/api/v2/places/person%3Ame", headers=headers())
    assert response.status_code == 200, response.text
    entries = response.headers["Server-Timing"].split(", ")
    assert all(re.fullmatch(r"[a-z0-9-]+;dur=\d+\.\d", entry) for entry in entries)
    names = [entry.split(";", 1)[0] for entry in entries]
    kinds = {"card-" + pin["card"]["template"].replace("_", "-") for pin in response.json()["pins"]}
    assert kinds and set(names) == {"auth", "app", "prep", "cards"} | kinds
    assert len(names) == len(set(names))
    response = api.get("/api/v2/health", headers=headers())
    assert response.status_code == 200, response.text
    assert re.fullmatch(r"auth;dur=\d+\.\d, app;dur=\d+\.\d", response.headers["Server-Timing"])


@pytest.mark.parametrize("scale", [1, 1000])
def test_place_server_timing_sums_kinds_and_bounds_the_slowest_entries(api, monkeypatch, scale):
    from backend import cards

    clock = [0]
    seconds = {kind: (i + 1) * scale for i, kind in enumerate(cards.TEMPLATES)}
    kinds = [*seconds, "updates"]
    monkeypatch.setitem(cards.DEFAULTS, "person", [(kind, "medium") for kind in kinds])
    monkeypatch.setattr(cards, "time", SimpleNamespace(perf_counter=lambda: clock[0]))

    def build(ctx, pin, cache):
        template = pin["card"]["template"]
        clock[0] += seconds[template]
        return {"card": {"template": template}}

    monkeypatch.setattr(cards, "pin_view", build)
    response = api.get("/api/v2/places/person%3Ame", headers=headers())
    assert response.status_code == 200, response.text
    added = response.headers["Server-Timing"].split(", ")[2:]
    assert len((", " + ", ".join(added)).encode("ascii")) <= 300
    durations = {name: float(ms) for name, ms in (entry.split(";dur=") for entry in added)}
    expected = {"card-" + kind.replace("_", "-"): ms * 1000 for kind, ms in seconds.items()}
    expected["card-updates"] *= 2
    assert durations["prep"] == 0 and durations["cards"] == sum(expected.values())
    measured = [(name, ms) for name, ms in durations.items() if name.startswith("card-")]
    assert 0 < len(measured) <= 8
    if scale == 1:
        assert len(measured) == 8
    assert measured == sorted(expected.items(), key=lambda item: -item[1])[:len(measured)]


def test_place_server_timing_keeps_access_cost_without_naming_hidden_pins(api, monkeypatch):
    from backend import cards

    clock = [0]
    monkeypatch.setitem(cards.DEFAULTS, "person", [("updates", "medium"), ("updates", "medium")])
    monkeypatch.setattr(cards, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    calls = []

    def build(ctx, pin, cache):
        calls.append(pin)
        clock[0] += 2
        return None if len(calls) == 1 else {"card": {"template": pin["card"]["template"]}}

    monkeypatch.setattr(cards, "pin_view", build)
    response = api.get("/api/v2/places/person%3Ame", headers=headers())
    assert response.status_code == 200, response.text
    durations = {name: float(ms) for name, ms in
                 (entry.split(";dur=") for entry in response.headers["Server-Timing"].split(", ")[2:])}
    assert "card-hidden" not in durations
    assert durations["card-updates"] == 2000
    assert durations["cards"] == 4000
    assert len(response.json()["pins"]) == 1
    assert response.json()["pins"][0]["position"] == 0


def test_card_settings_are_checked_and_never_a_query(api):
    put(api, "places/dashboard", {})
    post(api, "cards", {"template": "sql", "settings": {}, "place": "dashboard"}, expected=422)
    post(api, "cards", {"template": "done_per_week", "settings": {"query": "SELECT 1"}}, expected=422)
    post(api, "cards", {"template": "done_per_week", "settings": {"weeks": 99}}, expected=422)
    post(api, "cards", {"template": "tasks_by_status", "settings": {"team": "nowhere"}}, expected=422)
    post(api, "cards", {"template": "kpi", "settings": {"kpi": "missing"}}, expected=404)
    made = post(api, "cards", {"template": "done_per_week", "settings": {"weeks": 12}, "place": "dashboard",
                               "size": "large"})
    assert made["card"]["settings"] == {"team": "", "weeks": 12}
    assert len(made["card"]["data"]["weeks"]) == 12
    edited = patch(api, "cards/" + made["card"]["id"], {"settings": {"weeks": 4}, "title": "Last month"})["card"]
    assert edited["heading"] == "Last month" and len(edited["data"]["weeks"]) == 4
    patch(api, "cards/" + made["card"]["id"], {"settings": {"weeks": 4}}, "cara-test", expected=403)
    # Removing a card takes its pins with it; unpinning a card's only pin removes the card.
    delete(api, "cards/" + made["card"]["id"])
    assert len(place(api, "dashboard")["pins"]) == 4
    first = place(api, "dashboard")["pins"][0]
    delete(api, "pins/" + first["id"])
    assert get(api, "cards?ids=" + first["item"][5:])["cards"] == []


def test_needs_you_and_spend_show_only_their_own_reader(api):
    view = place(api, "dashboard", "cara-test")
    needs = next(p["card"] for p in view["pins"] if p["card"]["template"] == "needs_you")
    assert needs["hidden"] is False and needs["data"]["count"] == 0
    put(api, "places/dashboard", {})
    spend = post(api, "cards", {"template": "spend", "place": "dashboard"})["card"]
    assert spend["hidden"] is False and spend["data"]["spent"] == 0
    as_member(api, "ben@acme.example")
    shown = get(api, "cards?ids=" + spend["id"], "ben-test")["cards"][0]
    assert shown["hidden"] is True and shown["data"] is None       # a member sees no company spend


def test_a_page_pinned_by_a_person_widens_to_the_places_audience_and_is_in_its_activity(api):
    fid = page(api, "inbox", "Mail volume")
    get(api, f"files/{fid}/activity", "cara-test", expected=404)
    hidden = place(api, "dashboard", "cara-test")
    assert "Mail volume" not in str(hidden)
    pinned = post(api, "pins", {"place": "dashboard", "item": "page:" + fid})
    assert pinned["pins"][-1]["page"]["title"] == "Mail volume"
    log = get(api, f"files/{fid}/activity", "cara-test")["activity"]
    assert [(r["action"], r["actor"]) for r in log][:1] == [("pinned", "human:ana")]
    assert place(api, "dashboard", "cara-test")["pins"][-1]["page"]["file_id"] == fid
    delete(api, "pins/" + pinned["id"])
    get(api, f"files/{fid}/activity", "cara-test", expected=404)
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT action FROM bot_file_activity WHERE file_id=? ORDER BY id",
                                        (fid,))] == ["pinned", "unpinned"]


def test_a_private_tasks_page_is_never_pinned_wider(api):
    secret = page(api, "ops", "Private numbers", task=task(api, "bot:ops", "Private", private=True))
    post(api, "pins", {"place": "dashboard", "item": "page:" + secret}, expected=403)


def test_the_pinning_rule(api):
    own, other = page(api, "ops", "Ops page"), page(api, "cpo", "Product page")
    _, _, attempt = setup_attempt(api, "ops")
    bot = attempt["token"]
    post(api, "pins", {"place": "dashboard", "item": "page:" + own}, bot, expected=403)
    post(api, "pins", {"place": "bot:cpo", "item": "page:" + own}, bot, expected=403)
    post(api, "pins", {"place": "bot:ops", "item": "page:" + other}, bot, expected=404)     # not its own
    post(api, "pins", {"place": "bot:ops", "item": "page:" + own}, bot)
    assert [w["id"] for w in get(api, "bots/ops/task-tabs")["widgets"]] == ["tasks", "updates", "files", "page:" + own]
    # A bot pins only cards it made; a person who manages the bot pins another bot's page there.
    card = post(api, "cards", {"template": "tasks_by_status"})["card"]
    post(api, "pins", {"place": "bot:ops", "item": "card:" + card["id"]}, bot, expected=403)
    post(api, "pins", {"place": "bot:ops", "item": "page:" + other})
    post(api, "pins", {"place": "bot:ops", "item": "page:" + other}, expected=409)
    # A member arranges neither the Dashboard nor a bot they do not manage.
    post(api, "pins", {"place": "dashboard", "item": "page:" + own}, "cara-test", expected=403)
    post(api, "pins", {"place": "bot:ops", "item": "page:" + own}, "cara-test", expected=403)


def test_sidebar_pages_and_cards_follow_the_saved_widgets(api):
    mail, ops = page(api, "inbox", "Mail volume"), page(api, "ops", "Ops page")
    widgets = [{"id": "page:" + ops}, {"id": "page:" + mail}, {"id": "tasks", "hidden": True},
               {"id": "updates", "hidden": True}, {"id": "files", "hidden": True}]
    saved = put(api, "bots/ops/task-tabs", {"tabs": get(api, "bots/ops/task-tabs")["tabs"], "widgets": widgets})
    assert [w["id"] for w in saved["widgets"]] == [w["id"] for w in widgets]     # a sidebar all of pages
    assert [p["item"] for p in place(api, "bot:ops")["pins"]] == ["page:" + ops, "page:" + mail]
    # Cara reads ops but not the inbox's page: it is not on her sidebar, and her save keeps it.
    seen = get(api, "bots/ops/task-tabs", "cara-test")
    assert [w["id"] for w in seen["widgets"]] == ["page:" + ops, "tasks", "updates", "files"]
    assert mail not in str(place(api, "bot:ops", "cara-test"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
    put(api, "bots/ops/task-tabs", {"tabs": seen["tabs"], "widgets": [{"id": "tasks"}]}, "cara-test")
    assert [p["item"] for p in place(api, "bot:ops")["pins"]] == ["page:" + mail]
    # The Assistant's sidebar is each person's own: no pages there.
    put(api, "bots/coo/task-tabs", {"tabs": seen["tabs"], "widgets": [{"id": "page:" + ops}]}, expected=422)


def test_a_team_page_has_its_audience_its_defaults_and_its_leads(api):
    send(api, "POST", "groups", {"name": "Product", "add": {"people": ["cara"], "bots": ["cpo"]}, "lead": "person:cara"})
    gid = next(g["id"] for g in get(api, "groups"))
    task(api, "bot:cpo", "Product work")
    task(api, "bot:ops", "Ops work")
    view = place(api, "team:" + gid, "cara-test")
    assert view["name"] == "Product" and view["can_edit"] is True and view["audience"]["everyone"] is True
    assert cards_of(view) == [("tasks_by_status", gid), ("done_per_week", gid), ("needs_you", gid)]
    assert view["pins"][0]["card"]["data"]["counts"]["open"] == 1          # the team's bots' work only
    # Its lead narrows it to herself; Ben, an admin, still arranges it; a member outside it is refused.
    put(api, "places/team:" + gid, {"audience": {"everyone": False, "people": ["cara"]}}, "cara-test")
    place(api, "team:" + gid, "ben-test")
    as_member(api, "ben@acme.example")
    place(api, "team:" + gid, "ben-test", expected=403)
    put(api, "places/team:" + gid, {}, "ben-test", expected=403)
    put(api, "places/dashboard", {"audience": {"everyone": False}}, expected=422)
    place(api, "team:nowhere", expected=404)
