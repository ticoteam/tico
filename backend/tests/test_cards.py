"""Built-in cards and pins (backend/cards.py): a card shows each reader only what they may already see; the pinning
rule (a bot pins only its own pages and cards to its own sidebar or task, a person who arranges the place pins to the
Dashboard and team pages); a page pinned there by a person is readable by the place's audience, never a private
task's file; a team page's audience; and pages and cards as bot sidebar widgets."""

import re
from types import SimpleNamespace

import pytest

from backend.tests.helpers.api import as_member, get, headers, post, put, restrict, setup_attempt  # noqa: F401
from backend.tests.helpers.groups import send
from backend.tests.helpers.cards import delete, page, patch, place, task


def cards_of(view):
    return [(p["card"]["template"], p["card"]["settings"].get("team", "")) for p in view["pins"] if "card" in p]


def test_the_dashboard_starts_with_its_defaults_and_counts_only_what_each_reader_may_read(api):
    task(api, "bot:ops", "Ops work")
    task(api, "bot:inbox", "Mail work")  # inbox is Ana's alone
    view = place(api, "dashboard", "cara-test")
    assert view["saved"] is False and view["can_edit"] is False
    assert cards_of(view) == [("tasks_by_status", ""), ("done_per_week", ""), ("needs_you", ""), ("latest_pages", "")]
    assert all(p["id"] is None for p in view["pins"])
    counts = lambda v: v["pins"][0]["card"]["data"]["counts"]  # noqa: E731
    assert counts(view) == {"open": 1, "done": 0}
    assert counts(place(api, "dashboard"))["open"] == 2
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
    assert measured == sorted(expected.items(), key=lambda item: -item[1])[: len(measured)]


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
    durations = {
        name: float(ms)
        for name, ms in (entry.split(";dur=") for entry in response.headers["Server-Timing"].split(", ")[2:])
    }
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
    made = post(
        api, "cards", {"template": "done_per_week", "settings": {"weeks": 12}, "place": "dashboard", "size": "large"}
    )
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
    assert shown["hidden"] is True and shown["data"] is None  # a member sees no company spend


def test_needs_you_rows_lead_with_the_task_and_carry_the_ask(api):
    """A Needs you row is titled by the task it is on, so asks with the same wording on different tasks read apart; the
    ask itself goes along as its line, with the word for what it needs."""
    from backend.store import H
    from backend.tests.helpers.engine import ok_ask

    with api.app.state.store.transaction() as c:
        work = H.task_create(c, "human:ben", "Launch plan", "Plan it.", "human:ben", lint=False)
        ok_ask(c, work["id"], "human:ben", "human:ana", "OK the region?")
    needs = next(p["card"] for p in place(api, "dashboard")["pins"] if p["card"]["template"] == "needs_you")
    asks = [row for row in needs["data"]["items"] if row["kind"] == "ask"]
    assert [(row["title"], row["line"], row["act"], row["task_id"]) for row in asks] == [
        ("Launch plan", "OK the region?", "ok", work["id"])
    ]


def test_an_ask_reads_as_an_ok_a_question_as_a_reply_and_anything_else_as_a_fix():
    """A Needs you row's word (ui/app/decide.ts decideKind sorts the same): an ask is an OK or a page's OK, so ok; a
    bot's ❓ is answered in the chat; a task row is the reader's to fix."""
    from backend.cards import needs_act

    rows = [
        {"kind": "ask", "ask": {"kind": "ok"}},
        {"kind": "ask", "ask": {"kind": "page"}},
        {"kind": "question"},
        {"kind": "task"},
    ]
    assert [needs_act(row) for row in rows] == ["ok", "ok", "reply", "fix"]


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
        assert [
            r[0] for r in c.execute("SELECT action FROM bot_file_activity WHERE file_id=? ORDER BY id", (fid,))
        ] == ["pinned", "unpinned"]


def test_a_private_tasks_page_is_never_pinned_wider(api):
    secret = page(api, "ops", "Private numbers", task=task(api, "bot:ops", "Private", private=True))
    post(api, "pins", {"place": "dashboard", "item": "page:" + secret}, expected=403)


def test_the_pinning_rule(api):
    own, other = page(api, "ops", "Ops page"), page(api, "cpo", "Product page")
    _, _, attempt = setup_attempt(api, "ops")
    bot = attempt["token"]
    post(api, "pins", {"place": "dashboard", "item": "page:" + own}, bot, expected=403)
    post(api, "pins", {"place": "bot:cpo", "item": "page:" + own}, bot, expected=403)
    post(api, "pins", {"place": "bot:ops", "item": "page:" + other}, bot, expected=404)  # not its own
    post(api, "pins", {"place": "bot:ops", "item": "page:" + own}, bot)
    assert [w["id"] for w in get(api, "bots/ops/task-tabs")["widgets"]] == [
        "updates",
        "tasks",
        "files",
        "tools",
        "page:" + own,
    ]
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
    widgets = [
        {"id": "page:" + ops},
        {"id": "page:" + mail},
        {"id": "tasks", "hidden": True},
        {"id": "updates", "hidden": True},
        {"id": "files", "hidden": True},
    ]
    saved = put(api, "bots/ops/task-tabs", {"tabs": get(api, "bots/ops/task-tabs")["tabs"], "widgets": widgets})
    assert [w["id"] for w in saved["widgets"]] == [w["id"] for w in widgets]  # a sidebar all of pages
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
    put(api, "bots/assistant/task-tabs", {"tabs": seen["tabs"], "widgets": [{"id": "page:" + ops}]}, expected=422)


def test_a_runner_reads_no_sidebar_and_one_who_only_sees_the_bot_gets_its_defaults(api):
    """GET /bots/{bot}/task-tabs guards as GET /bots/{bot} does: a runner's credential is refused, and one who may see
    the bot but not read it gets the default lists and built-ins, none of its saved lists, pages or cards."""
    from backend.tests.helpers.api import runner

    ops = page(api, "ops", "Ops page")
    card = post(api, "cards", {"template": "tasks_by_status", "place": "bot:ops"})["card"]
    lists = [{"name": "Launch", "conditions": [{"field": "stage", "value": ["general-todo"]}]}]
    put(api, "bots/ops/task-tabs", {"tabs": lists, "widgets": [{"id": "page:" + ops}, {"id": "card:" + card["id"]}]})
    get(api, "bots/ops/task-tabs", runner(api)["token"], expected=403)
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["ana", "cara"]}, read={"people": ["ana"]}, write={"people": ["ana"]})
    seen = get(api, "bots/ops/task-tabs", "cara-test")
    assert [w["id"] for w in seen["widgets"]] == ["updates", "tasks", "files", "tools"]
    assert "Launch" not in [t["name"] for t in seen["tabs"]]
    assert (seen["saved"], seen["can_edit"]) == (False, False)
    mine = get(api, "bots/ops/task-tabs")
    assert [w["id"] for w in mine["widgets"]] == [
        "page:" + ops,
        "card:" + card["id"],
        "updates",
        "tasks",
        "files",
        "tools",
    ]
    assert [t["name"] for t in mine["tabs"]] == ["Launch"]


def test_one_who_only_sees_the_bot_counts_its_default_lists_not_its_saved_ones(api):
    lists = [{"name": "Launch", "conditions": [{"field": "stage", "value": ["general-todo"]}]}]
    put(api, "bots/ops/task-tabs", {"tabs": lists})
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["ana", "cara"]}, read={"people": ["ana"]}, write={"people": ["ana"]})
    seen = get(api, "bots/ops/task-tabs/tasks", "cara-test")["tabs"]
    assert "Launch" not in [t["name"] for t in seen] and "c0" not in [t["key"] for t in seen]
    assert [t["name"] for t in get(api, "bots/ops/task-tabs/tasks")["tabs"]] == ["Launch"]


def test_the_sidebar_layout_reads_its_pages_rows_and_access_only(api, monkeypatch):
    """GET /bots/{bot}/task-tabs checks its pinned pages may be read; it never reads them in full (their versions,
    links), which only the place's own read draws."""
    from backend import cards, pages

    ops = page(api, "ops", "Ops page")
    full, calls = pages.prime_views, []
    monkeypatch.setattr(pages, "prime_views", lambda c, rows: calls.append(len(rows)) or full(c, rows))
    real = cards.prime_pins
    primed = []
    monkeypatch.setattr(cards, "prime_pins", lambda ctx, pins, **kw: primed.append(pins) or real(ctx, pins, **kw))
    get(api, "bots/ops/task-tabs")
    assert primed == []  # nothing pinned, nothing to read
    lists = [{"name": "Launch", "conditions": [{"field": "stage", "value": ["general-todo"]}]}]
    put(api, "bots/ops/task-tabs", {"tabs": lists, "widgets": [{"id": "page:" + ops}]})
    assert [w["id"] for w in get(api, "bots/ops/task-tabs")["widgets"]][0] == "page:" + ops
    assert primed and all(pins == [{"item": "page:" + ops}] for pins in primed) and calls == []


def test_a_pinned_pages_version_is_declared_but_optional():
    """A page whose full read is not drawn (page_brief) has no version; the contract must not require one."""
    from backend.api_schemas._base import SCHEMAS
    import backend.api_schemas.cards_and_pins  # noqa: F401

    page = SCHEMAS["Place"]["properties"]["pins"]["items"]["properties"]["page"]
    assert "version" in page["properties"] and "version" not in page["required"]


def test_automation_runs_count_the_whole_day_not_its_newest_500(api):
    from backend.cards import ago
    from backend.tests.helpers.automation_runs import FIND, make

    aid = make(api, [FIND])
    runs = [("run_early", aid, ago(hours=23), "failed")]
    runs += [(f"run_{i}", aid, ago(minutes=i + 1), "ok") for i in range(600)]
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO automation_runs(id,automation_id,version,started_at,status) VALUES(?,?,1,?,?)", runs)
    put(api, "places/dashboard", {})
    data = post(api, "cards", {"template": "automation_runs", "place": "dashboard"})["card"]["data"]
    assert (data["total"], data["failed"], data["running"]) == (601, 1, 0)
    assert data["runs"][0]["id"] == "run_early" and len(data["runs"]) == 5


def videos(api):
    """A Videos pipeline (Concept, Draft): Spring promo and Beach clean (private: Ana asked for it, Cara is not on it)
    with ops in Concept, Host intro with cpo in Draft, and Weekly recap of ops done."""
    from backend import task_engine as E
    from backend.store import H
    from backend.tests.helpers.engine import pipeline

    stage = {"owner": "assignee"}
    with api.app.state.store.transaction() as c:
        pipeline(c, "videos", "Videos", stages=({"name": "Concept", **stage}, {"name": "Draft", **stage}))

        def video(owner, title, to=None, private=False):
            tid = H.task_create(
                c, "human:ana", title, "Details.", owner, pipeline="videos", lint=False, private=private
            )
            if to:
                E.move(c, tid["id"], to, "human:ana", "for the test")

        video("bot:ops", "Spring promo")
        video("bot:ops", "Beach clean", private=True)
        video("bot:cpo", "Host intro", to="Draft")
        video("bot:ops", "Weekly recap", to=E.DONE)


def stages(card):
    return [(s["name"], s["count"], [t["title"] for t in s["tasks"]]) for s in card["data"]["stages"]]


def test_tasks_by_stage_shows_each_reader_only_the_tasks_they_may_read(api):
    """A pipeline's open tasks by stage, as the stage list reads them for its reader: a private task its reader is not
    on is in no count and no row, and a card with nothing to show its reader is hidden."""
    videos(api)
    post(api, "cards", {"template": "tasks_by_stage", "settings": {"pipeline": "nowhere"}}, expected=422)
    post(
        api,
        "cards",
        {"template": "tasks_by_stage", "settings": {"pipeline": "videos", "agent": "nobody"}},
        expected=422,
    )
    mine = post(
        api,
        "cards",
        {"template": "tasks_by_stage", "settings": {"pipeline": "VIDEOS", "agent": "ops"}, "place": "bot:ops"},
    )["card"]
    assert mine["settings"] == {"pipeline": "videos", "agent": "ops", "count": 3}  # its name, in any case
    assert stages(mine) == [("Concept", 2, ["Spring promo", "Beach clean"])]
    shown = next(p["card"] for p in place(api, "bot:ops", "cara-test")["pins"] if "card" in p)
    assert stages(shown) == [("Concept", 1, ["Spring promo"])]
    assert "Beach clean" not in str(shown)
    everyone = {"template": "tasks_by_stage", "settings": {"pipeline": "videos", "count": 1}}
    everyone = post(api, "cards", everyone)["card"]
    assert stages(everyone) == [("Concept", 2, ["Spring promo"]), ("Draft", 1, ["Host intro"])]
    # Nothing it may show: no box at all, not an empty one.
    nothing = {"template": "tasks_by_stage", "settings": {"pipeline": "videos", "agent": "inbox"}}
    assert post(api, "cards", nothing)["card"]["hidden"] is True


def test_tasks_by_stage_names_no_agent_its_reader_may_not_see(api):
    """An agent its maker may not see is as unknown as one that is not there; a card about an agent its reader may not
    see shows them nothing, and not which agent."""
    videos(api)
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", people=["ana"])
    unknown = post(
        api,
        "cards",
        {"template": "tasks_by_stage", "settings": {"pipeline": "videos", "agent": "nobody"}},
        "cara-test",
        expected=422,
    )
    hidden = post(
        api,
        "cards",
        {"template": "tasks_by_stage", "settings": {"pipeline": "videos", "agent": "cpo"}},
        "cara-test",
        expected=422,
    )
    assert hidden["error"]["detail"] == unknown["error"]["detail"].replace("nobody", "cpo")
    made = post(
        api,
        "cards",
        {"template": "tasks_by_stage", "settings": {"pipeline": "videos", "agent": "cpo"}, "place": "bot:ops"},
    )["card"]
    assert stages(made) == [("Draft", 1, ["Host intro"])]
    shown = next(p["card"] for p in place(api, "bot:ops", "cara-test")["pins"] if "card" in p)
    assert shown["hidden"] is True and shown["data"] is None
    assert shown["settings"] == {"pipeline": "videos", "count": 3} and "cpo" not in str(shown)
    # Ops, which arranges its own sidebar but may not see cpo, changes only the count: the saved agent stays, unchecked
    # and unnamed; naming cpo itself is refused as an unknown agent is.

    ops = setup_attempt(api, "ops")[2]["token"]
    edit = api.request("PATCH", "/api/v2/cards/" + made["id"], json={"settings": {"count": 5}}, headers=headers(ops))
    assert edit.status_code == 200 and "cpo" not in edit.text and edit.json()["card"]["hidden"] is True
    saved = next(p["card"] for p in place(api, "bot:ops")["pins"] if "card" in p)
    assert saved["settings"] == {"pipeline": "videos", "agent": "cpo", "count": 5}
    named = api.request(
        "PATCH", "/api/v2/cards/" + made["id"], json={"settings": {"agent": "cpo"}}, headers=headers(ops)
    )
    assert named.status_code == 422 and named.json()["error"]["detail"] == hidden["error"]["detail"]
    # The picker offers the pipelines, and only the agents its reader may see.
    offered = get(api, "cards/choices", "cara-test")
    assert {"id": "videos", "name": "Videos"} in offered["pipelines"]
    assert "ops" in [a["slug"] for a in offered["agents"]] and "cpo" not in [a["slug"] for a in offered["agents"]]
    assert "cpo" in [a["slug"] for a in get(api, "cards/choices")["agents"]]


def widgets_of(token="ana-test"):
    return lambda api: [(w["id"], w["hidden"]) for w in get(api, "bots/ops/task-tabs", token)["widgets"]]


def test_an_agent_hides_and_moves_its_built_in_sections_with_tico_pin_update(api):
    from backend.tests.helpers.settings_assistant import mcp

    _, _, attempt = setup_attempt(api, "ops")
    bot = attempt["token"]
    assert mcp(api, "tico_pin_update", {"pin": "files", "position": 0}, bot)[0] is False
    assert mcp(api, "tico_pin_update", {"pin": "updates", "hidden": True}, bot)[0] is False
    assert widgets_of()(api) == [("files", False), ("updates", True), ("tasks", False)]
    assert mcp(api, "tico_pin_update", {"pin": "updates", "place": "bot:ops", "hidden": False}, bot)[0] is False
    assert not get(api, "bots/ops/task-tabs")["widgets"][1]["hidden"]
    # Another agent's sidebar is not its to arrange.
    assert mcp(api, "tico_pin_update", {"pin": "tasks", "place": "bot:cpo", "hidden": True}, bot)[0] is True


def test_a_built_in_hidden_while_a_pin_and_tabs_are_saved_loses_neither(api):
    """The hide is one write on the server: a card pinned and tabs saved between the tool's start and its write stay."""
    from backend.tests.helpers.settings_assistant import mcp

    _, _, attempt = setup_attempt(api, "ops")
    lists = [{"name": "Launch", "conditions": [{"field": "stage", "value": ["general-todo"]}]}]
    put(api, "bots/ops/task-tabs", {"tabs": lists})
    meanwhile = []

    class Racing:
        """The test app, with another writer's pin and tabs landing just before the tool's first write."""

        def request(self, method, path, **kw):
            if method != "GET" and not meanwhile:
                card = post(api, "cards", {"template": "tasks_by_status", "place": "bot:ops"})["card"]
                put(api, "bots/ops/task-tabs", {"tabs": [*lists, {**lists[0], "name": "Later"}]})
                meanwhile.append("card:" + card["id"])
            return api.request(method, path, **kw)

    refused, _ = mcp(
        Racing(), "tico_pin_update", {"pin": "files", "place": "bot:ops", "hidden": True}, attempt["token"]
    )
    sidebar = get(api, "bots/ops/task-tabs")
    assert refused is False and meanwhile
    assert [(w["id"], w["hidden"]) for w in sidebar["widgets"]] == [
        ("updates", False),
        ("tasks", False),
        ("files", True),
        (meanwhile[0], False),
    ]
    assert [t["name"] for t in sidebar["tabs"]] == ["Launch", "Later"]


def test_a_position_on_an_agents_sidebar_counts_its_built_ins(api):
    """A card added at 0, or a pin moved to 0, is drawn first, above Updates, as the skill says."""
    from backend.tests.helpers.settings_assistant import mcp

    videos(api)
    _, _, attempt = setup_attempt(api, "ops")
    bot = attempt["token"]
    settings = {"template": "tasks_by_stage", "pipeline": "videos", "agent": "ops", "place": "bot:ops"}
    refused, added = mcp(api, "tico_card_add", {**settings, "position": 0}, bot)
    assert refused is False, added
    first = "card:" + added["card"]["id"]
    refused, later = mcp(api, "tico_card_add", {**settings, "count": 1}, bot)
    assert refused is False, later
    assert [w for w, _ in widgets_of()(api)] == [
        first,
        "updates",
        "tasks",
        "files",
        "tools",
        "card:" + later["card"]["id"],
    ]
    # Listed at the position it is drawn at, built-ins counted: moved there, it stays.
    listed = {p["item"]: p["position"] for p in mcp(api, "tico_card_list", {"place": "bot:ops"}, bot)[1]["pins"]}
    assert listed == {first: 0, "card:" + later["card"]["id"]: 5}
    assert mcp(api, "tico_pin_update", {"pin": later["pin"], "position": 5}, bot)[0] is False
    assert [w for w, _ in widgets_of()(api)][-1] == "card:" + later["card"]["id"]
    # The last spot on a full sidebar, below its 60 pins and 4 built-ins, is one a pin may be moved to.
    moved = api.request("PATCH", "/api/v2/pins/" + added["pin"], json={"position": 63}, headers=headers(bot))
    assert moved.status_code == 200, moved.text
    assert [w for w, _ in widgets_of()(api)][-1] == first
    assert mcp(api, "tico_pin_update", {"pin": added["pin"], "position": 0}, bot)[0] is False
    assert mcp(api, "tico_pin_update", {"pin": later["pin"], "position": 0}, bot)[0] is False
    assert [w for w, _ in widgets_of()(api)] == [
        "card:" + later["card"]["id"],
        first,
        "updates",
        "tasks",
        "files",
        "tools",
    ]
    pins = [p["item"] for p in place(api, "bot:ops")["pins"]]
    assert pins == ["card:" + later["card"]["id"], first]


def test_a_page_its_reader_may_not_read_moves_no_position_on_an_agents_sidebar(api, monkeypatch):
    """Positions count only what the reader may know is there: a page they may not read leaves no gap, and a pin they
    move to where it is listed, or away and back, keeps that page where it was among the rest."""
    from backend import files as F
    from backend.tests.helpers.settings_assistant import mcp

    _, _, attempt = setup_attempt(api, "ops")
    bot = attempt["token"]
    card = "card:" + post(api, "cards", {"template": "tasks_by_status"})["card"]["id"]
    tabs = get(api, "bots/ops/task-tabs")["tabs"]
    put(api, "bots/ops/task-tabs", {"tabs": tabs, "widgets": [{"id": w} for w in ("updates", "tasks", "files", card)]})

    def listed():
        return {p["item"]: p["position"] for p in mcp(api, "tico_card_list", {"place": "bot:ops"}, bot)[1]["pins"]}

    assert listed() == {card: 3}
    hidden, visible = page(api, "ops", "Not for the agent"), F.file_visible

    def unread_by_the_agent(c, auth, who, row, cache):
        return visible(c, auth, who, row, cache) and (who.role != "bot" or row["id"] != hidden)

    monkeypatch.setattr(F, "file_visible", unread_by_the_agent)
    saved = ["updates", "page:" + hidden, "tasks", "files", card, "tools"]
    put(api, "bots/ops/task-tabs", {"tabs": tabs, "widgets": [{"id": w} for w in saved]})
    assert listed() == {card: 3}
    pin = next(p["id"] for p in place(api, "bot:ops")["pins"] if p["item"] == card)
    for position in (3, 1, 3, 0, 3):
        assert mcp(api, "tico_pin_update", {"pin": pin, "position": position}, bot)[0] is False
        assert listed() == {card: position}
    assert [w for w, _ in widgets_of()(api)] == saved


def test_a_pin_moved_to_where_its_reader_sees_it_stays_before_a_page_they_may_not_read(api, monkeypatch):
    """A card first, then a page its agent may not read: moved to 0, where the agent sees it, the card stays first."""
    from backend import files as F
    from backend.tests.helpers.settings_assistant import mcp

    _, _, attempt = setup_attempt(api, "ops")
    bot = attempt["token"]
    card = "card:" + post(api, "cards", {"template": "tasks_by_status"})["card"]["id"]
    hidden, visible = page(api, "ops", "Not for the agent"), F.file_visible

    def unread_by_the_agent(c, auth, who, row, cache):
        return visible(c, auth, who, row, cache) and (who.role != "bot" or row["id"] != hidden)

    monkeypatch.setattr(F, "file_visible", unread_by_the_agent)
    saved = [card, "page:" + hidden, "updates", "tasks", "files", "tools"]
    tabs = get(api, "bots/ops/task-tabs")["tabs"]
    put(api, "bots/ops/task-tabs", {"tabs": tabs, "widgets": [{"id": w} for w in saved]})
    pin = next(p["id"] for p in place(api, "bot:ops")["pins"] if p["item"] == card)
    assert mcp(api, "tico_pin_update", {"pin": pin, "position": 0}, bot)[0] is False
    assert [w for w, _ in widgets_of()(api)] == saved


def test_the_last_pin_on_the_fullest_sidebar_a_layout_saves_moves_to_where_it_is_listed(api):
    """Every position listed is one a pin may be moved to: the full-layout save holds no more widgets than that."""
    from backend.models import SIDEBAR_WIDGETS

    tabs = get(api, "bots/ops/task-tabs")["tabs"]
    built_ins = ["updates", "tasks", "files", "tools"]
    made = [post(api, "cards", {"template": "tasks_by_status"})["card"]["id"] for _ in range(SIDEBAR_WIDGETS)]
    cards, more = ["card:" + c for c in made[4:]], ["card:" + c for c in made[:4]]
    full = built_ins + cards
    put(api, "bots/ops/task-tabs", {"tabs": tabs, "widgets": [{"id": w} for w in full]})
    last = place(api, "bot:ops")["pins"][-1]
    assert (last["item"], last["position"]) == (cards[-1], SIDEBAR_WIDGETS - 1)
    patch(api, "pins/" + last["id"], {"position": last["position"]})
    assert [w for w, _ in widgets_of()(api)] == full
    # Built-ins left out of a save come back after it: a layout that would run past the furthest position is refused.
    put(api, "bots/ops/task-tabs", {"tabs": tabs, "widgets": [{"id": w} for w in more + cards]}, expected=422)
    assert [w for w, _ in widgets_of()(api)] == full


def test_an_open_agent_page_hears_that_its_sidebar_changed(api):
    """A change to an agent's sidebar is a `bots` event naming it (`sidebar`: its slug), to whoever may see the agent,
    so its open page reads the sidebar again."""
    from backend import events as E
    from backend.auth import Identity
    from backend.tests.helpers.settings_assistant import mcp

    store = api.app.state.store
    with store.read() as c:
        cursor = E.latest(c)
    _, _, attempt = setup_attempt(api, "ops")
    assert mcp(api, "tico_pin_update", {"pin": "files", "hidden": True}, attempt["token"])[0] is False

    def heard(person):
        with store.read() as c:
            events, _, _, _ = E.read(
                c,
                api.app.state.auth,
                Identity(f"human:{person}", "human", f"{person}@acme.example"),
                cursor,
                topics=("bots",),
                task_views=lambda rows, c, who, visible_sql="1": [],
                task_view=None,
            )
        return [d.get("sidebar") for _, topic, d in events if topic == "bots" and d.get("sidebar")]

    assert heard("cara") == ["ops"]
    with store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    assert heard("cara") == []


def test_a_team_page_has_its_audience_its_defaults_and_its_leads(api):
    send(
        api, "POST", "groups", {"name": "Product", "add": {"people": ["cara"], "bots": ["cpo"]}, "lead": "person:cara"}
    )
    gid = next(g["id"] for g in get(api, "groups"))
    task(api, "bot:cpo", "Product work")
    task(api, "bot:ops", "Ops work")
    view = place(api, "team:" + gid, "cara-test")
    assert view["name"] == "Product" and view["can_edit"] is True and view["audience"]["everyone"] is True
    assert cards_of(view) == [("tasks_by_status", gid), ("done_per_week", gid), ("needs_you", gid)]
    assert view["pins"][0]["card"]["data"]["counts"]["open"] == 1  # the team's bots' work only
    # Its lead narrows it to herself; Ben, an admin, still arranges it; a member outside it is refused.
    put(api, "places/team:" + gid, {"audience": {"everyone": False, "people": ["cara"]}}, "cara-test")
    place(api, "team:" + gid, "ben-test")
    as_member(api, "ben@acme.example")
    place(api, "team:" + gid, "ben-test", expected=403)
    put(api, "places/team:" + gid, {}, "ben-test", expected=403)
    put(api, "places/dashboard", {"audience": {"everyone": False}}, expected=422)
    place(api, "team:nowhere", expected=404)
