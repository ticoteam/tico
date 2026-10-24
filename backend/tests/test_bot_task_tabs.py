"""A bot's sidebar (backend/bot_task_tabs.py): one layout per bot that every viewer shares, the Assistant's per person,
changed only by the bot or a person who manages it, with no cap on tabs; widgets in order, hidden and with heights; and
filter tabs of rows the server checks, counted for each viewer ("me" and "others" are the person looking)."""

import json
from datetime import datetime, timedelta, timezone

from backend import hubdb as H
from backend.tests.test_api import api, as_member, get, put, setup_attempt  # noqa: F401  (api is a fixture)

ACTIVE = ["open", "doing", "waiting"]
OTHERS = {"name": "Assigned to Others", "show_count": True, "conditions": [
    {"field": "asked_by", "value": "this_bot"}, {"field": "assigned_to", "value": "others"},
    {"field": "status", "value": ACTIVE}]}
DEFAULT = [{"builtin": "needs", "name": "Needs You", "show_count": True},
           {"builtin": "active", "name": "Doing", "show_count": True},
           {"builtin": "blocked", "name": "Blocked", "show_count": True}, OTHERS]
WIDGETS = [{"id": w, "hidden": False, "height": None} for w in ("tasks", "updates", "files", "routines")]
PRODUCT = {"name": "product tasks", "show_count": True, "conditions": [{"field": "type", "value": ["general"]},
                                                                       {"field": "status", "value": ACTIVE}]}


def tabs(api, bot, token="ana-test"):
    return get(api, f"bots/{bot}/task-tabs", token)


def save(api, bot, value, token="ana-test", expected=200, **more):
    return put(api, f"bots/{bot}/task-tabs", {"tabs": value, **more}, token, expected)


def shown(api, bot, token="ana-test"):
    """Each filter tab's count and titles, as the caller sees them."""
    out = get(api, f"bots/{bot}/task-tabs/tasks", token)
    title = {t["id"]: t["title"] for t in out["tasks"]}
    return {tab["name"]: (tab["count"], sorted(title[i] for i in tab["ids"])) for tab in out["tabs"]}


def task(api, title, owner, requester, **columns):
    stamp = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")  # noqa: E731
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, requester, title, "Details.", owner, lint=False, private=False)["id"]
        for name, value in columns.items():
            if name in ("created", "updated", "done_at"):
                value = stamp(value)
            c.execute(f"UPDATE tasks SET {name}=? WHERE id=?", (value, tid))
    return tid


def test_a_bot_starts_with_the_new_default_tabs_and_widgets(api):
    layout = tabs(api, "ops")
    assert layout["tabs"] == DEFAULT and layout["widgets"] == WIDGETS
    assert layout["saved"] is False and layout["personal"] is False and layout["can_edit"] is True
    assert "done" not in [tab.get("builtin") for tab in layout["tabs"]]


def test_a_layout_is_the_bots_and_changing_one_never_moves_anothers(api):
    layout = [DEFAULT[1], PRODUCT, {"builtin": "needs", "name": "For you", "show_count": False}]
    widgets = [{"id": "files", "hidden": False, "height": 240}, {"id": "tasks", "hidden": False, "height": None},
               {"id": "updates", "hidden": True, "height": None}, {"id": "routines", "hidden": False, "height": None}]
    saved = save(api, "ops", layout, widgets=widgets)
    assert saved["tabs"] == layout and saved["widgets"] == widgets and saved["updated_by"] == "human:ana"
    # Everyone who opens the bot sees the same sidebar: Cara, who cannot change it, too.
    assert tabs(api, "ops", "cara-test")["tabs"] == layout and tabs(api, "ops", "cara-test")["widgets"] == widgets
    assert tabs(api, "ops", "cara-test")["can_edit"] is False
    # Leaving the widgets out keeps them; another bot keeps its own.
    assert save(api, "ops", [PRODUCT])["widgets"] == widgets
    assert tabs(api, "cpo")["tabs"] == DEFAULT and tabs(api, "cpo")["widgets"] == WIDGETS
    # A partial widget list keeps the rest after it, as they come by default.
    assert [w["id"] for w in save(api, "cpo", DEFAULT, "ben-test", widgets=[{"id": "routines"}])["widgets"]] == \
        ["routines", "tasks", "updates", "files"]
    # Back to the defaults, widgets and all.
    assert save(api, "ops", None)["tabs"] == DEFAULT
    assert tabs(api, "ops", "cara-test")["saved"] is False and tabs(api, "ops")["widgets"] == WIDGETS


def test_only_the_bot_or_a_person_who_manages_it_changes_its_sidebar(api):
    save(api, "ops", [DEFAULT[0]], "cara-test", expected=403)
    as_member(api, "ben@acme.example")
    save(api, "ops", [DEFAULT[0]], "ben-test", expected=403)      # Ben manages cpo, not ops
    save(api, "cpo", [DEFAULT[0]], "ben-test", widgets=[{"id": "tasks", "hidden": True}])
    _, _, attempt = setup_attempt(api, "ops")
    assert save(api, "ops", [PRODUCT], attempt["token"])["tabs"] == [PRODUCT]
    save(api, "cpo", [PRODUCT], attempt["token"], expected=403)
    assert tabs(api, "cpo")["tabs"] == [DEFAULT[0]] and tabs(api, "cpo")["widgets"][0]["hidden"] is True


def test_the_assistants_sidebar_is_each_persons_own(api):
    assert tabs(api, "coo")["personal"] is True
    save(api, "coo", [PRODUCT], widgets=[{"id": "updates", "hidden": True}])
    assert tabs(api, "coo")["tabs"] == [PRODUCT]
    cara = tabs(api, "coo", "cara-test")
    assert cara["tabs"] == DEFAULT and cara["widgets"] == WIDGETS and cara["can_edit"] is True
    save(api, "coo", [DEFAULT[3]], "cara-test")
    assert tabs(api, "coo")["tabs"] == [PRODUCT] and tabs(api, "coo", "cara-test")["tabs"] == [DEFAULT[3]]
    # A bot never writes anyone's Assistant layout.
    _, _, attempt = setup_attempt(api, "ops")
    save(api, "coo", [DEFAULT[0]], attempt["token"], expected=403)


def test_no_cap_on_tabs(api):
    many = [{**PRODUCT, "name": f"Tab {n}"} for n in range(150)]
    assert len(save(api, "ops", many)["tabs"]) == 150
    assert tabs(api, "ops", "cara-test")["tabs"] == many


def test_a_hidden_bots_sidebar_is_not_found(api):
    save(api, "ops", [PRODUCT])
    access = lambda see: put(api, "bots/ops/access", {"see": see, "read": see, "write": see,  # noqa: E731
                                                      "revision": get(api, "bots/ops/access")["revision"]})
    access({"people": ["ben"]})
    hidden = get(api, "bots/ops/task-tabs", "cara-test", expected=404)
    assert "tabs" not in hidden and "product tasks" not in str(hidden)
    get(api, "bots/ops/task-tabs/tasks", "cara-test", expected=404)
    access({"people": ["ben", "cara"]})
    assert tabs(api, "ops", "cara-test")["tabs"] == [PRODUCT]


def test_every_field_and_value_is_checked(api):
    def refused(conditions, word):
        r = save(api, "ops", [{"name": "x", "conditions": conditions}], expected=422)
        assert word in json.dumps(r), r
    refused([{"field": "colour", "value": "red"}], "field")
    refused([{"field": "status", "value": ["open", "sleeping"]}], "Unknown status: sleeping")
    refused([{"field": "status", "value": []}], "Status needs one or more values")
    refused([{"field": "type", "value": ["nope"]}], "Unknown task type: nope")
    refused([{"field": "step", "value": "nope"}], "Unknown step: nope")
    refused([{"field": "assigned_to", "value": "human:zed"}], "Unknown person or bot: human:zed")
    refused([{"field": "assigned_to", "value": "ben"}], "Unknown person or bot: ben")
    refused([{"field": "asked_by", "value": "others"}], "Unknown person or bot: others")
    refused([{"field": "waiting_on", "value": "someone"}], "Waiting on is any, person, bot or me")
    refused([{"field": "created_within", "value": 0}], "number of days")
    refused([{"field": "done_within", "value": "3"}], "number of days")
    refused([{"field": "due", "value": "soon"}], "Due within")
    refused([{"field": "label", "value": " "}], "label is 1 to 60")
    refused([{"field": "status", "value": ACTIVE, "extra": 1}], "extra")
    r = save(api, "ops", [{"builtin": "needs", "name": "x", "conditions": [{"field": "status", "value": "open"}]}],
             expected=422)
    assert "keeps its own filter" in json.dumps(r)
    put(api, "bots/ops/task-tabs", {"tabs": [PRODUCT], "widgets": [{"id": "chat"}]}, expected=422)
    put(api, "bots/ops/task-tabs", {"tabs": [PRODUCT], "widgets": [{"id": "tasks", "height": 20}]}, expected=422)
    put(api, "bots/ops/task-tabs", {"tabs": [PRODUCT], "widgets": [{"id": "tasks"}, {"id": "tasks"}]}, expected=422)
    put(api, "bots/ops/task-tabs", {"tabs": [PRODUCT], "colour": "red"}, expected=422)
    # Every field, each in its stored form: a lone status becomes a list, a label lower case.
    with api.app.state.store.read() as c:
        step = c.execute("SELECT id FROM task_steps ORDER BY id LIMIT 1").fetchone()[0]
    every = [{"field": "status", "value": "waiting"}, {"field": "step", "value": step},
             {"field": "type", "value": "general"}, {"field": "assigned_to", "value": "human:ben"},
             {"field": "asked_by", "value": "me"}, {"field": "waiting_on", "value": "person"},
             {"field": "created_within", "value": 3}, {"field": "updated_within", "value": 7},
             {"field": "done_within", "value": 30}, {"field": "due", "value": "overdue"},
             {"field": "due", "value": 0}, {"field": "label", "value": " QA "}]
    stored = save(api, "ops", [{"name": "Everything", "show_count": False, "conditions": every}])["tabs"][0]
    assert stored["show_count"] is False
    assert stored["conditions"][0] == {"field": "status", "value": ["waiting"]}
    assert stored["conditions"][2] == {"field": "type", "value": ["general"]}
    assert stored["conditions"][-1] == {"field": "label", "value": "qa"}
    assert len(stored["conditions"]) == len(every)


def test_me_and_others_are_the_person_looking(api):
    task(api, "For Ben", "human:ben", "bot:ops")
    task(api, "For Cara", "human:cara", "bot:ops")
    task(api, "Ops own", "bot:ops", "human:ana")
    task(api, "For cpo", "bot:cpo", "bot:ops")
    task(api, "Not about ops", "human:ben", "bot:cpo")
    mine = {"name": "Mine", "conditions": [{"field": "assigned_to", "value": "me"}]}
    save(api, "ops", [*DEFAULT, mine])
    for token, me, others in (("ben-test", ["For Ben"], ["For Cara", "For cpo"]),
                              ("cara-test", ["For Cara"], ["For Ben", "For cpo"])):
        seen = shown(api, "ops", token)
        assert seen["Mine"] == (len(me), me), token
        assert seen["Assigned to Others"] == (len(others), others), token
    # Ana is neither: everything ops asked of someone else is "others" to her.
    assert shown(api, "ops")["Assigned to Others"] == (3, ["For Ben", "For Cara", "For cpo"])
    assert shown(api, "ops")["Mine"] == (0, [])


def test_a_filter_counts_only_what_the_viewer_may_read(api):
    task(api, "Open to all", "human:ben", "bot:ops")
    secret = task(api, "Ben and ops only", "human:ben", "bot:ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (secret,))
    save(api, "ops", [{"name": "Asked", "conditions": [{"field": "asked_by", "value": "this_bot"}]}])
    assert shown(api, "ops", "ben-test")["Asked"] == (2, ["Ben and ops only", "Open to all"])
    assert shown(api, "ops", "cara-test")["Asked"] == (1, ["Open to all"])


def test_each_field_filters(api):
    task(api, "New", "bot:ops", "human:ana", created=1)
    task(api, "Old", "bot:ops", "human:ana", created=10, updated=10)
    task(api, "Waits on Ben", "bot:ops", "human:ana", status="waiting", waiting_on="human:ben", created=10, updated=10)
    task(api, "Waits on cpo", "bot:ops", "human:ana", status="waiting", waiting_on="bot:cpo", created=10, updated=10)
    task(api, "Done lately", "bot:ops", "human:ana", status="done", done_at=5, created=10, updated=5)
    task(api, "Done long ago", "bot:ops", "human:ana", status="done", done_at=40, created=50, updated=40)
    task(api, "Overdue", "bot:ops", "human:ana", due="2020-01-01", created=10, updated=10)
    rows = lambda *c: [{"field": f, "value": v} for f, v in c]  # noqa: E731
    save(api, "ops", [
        {"name": "Created recently", "show_count": False, "conditions": rows(("created_within", 3))},
        {"name": "Waits on a person", "conditions": rows(("waiting_on", "person"))},
        {"name": "Waits on Ben", "conditions": rows(("waiting_on", "me"))},
        {"name": "Waits", "conditions": rows(("waiting_on", "any"))},
        {"name": "Done", "conditions": rows(("status", ["done"]))},
        {"name": "Done 60", "conditions": rows(("status", ["done"]), ("done_within", 60))},
        {"name": "Overdue", "conditions": rows(("due", "overdue"))},
        {"name": "Asked by Ana", "conditions": rows(("asked_by", "human:ana"), ("status", ["open"]))},
    ])
    seen = shown(api, "ops", "ben-test")
    assert seen["Created recently"] == (1, ["New"])
    assert seen["Waits on a person"] == (1, ["Waits on Ben"])
    assert seen["Waits on Ben"] == (1, ["Waits on Ben"])
    assert shown(api, "ops", "cara-test")["Waits on Ben"] == (0, [])
    assert seen["Waits"] == (2, ["Waits on Ben", "Waits on cpo"])
    # Finished ones for two weeks unless the tab names its own window.
    assert seen["Done"] == (1, ["Done lately"])
    assert seen["Done 60"] == (2, ["Done lately", "Done long ago"])
    assert seen["Overdue"] == (1, ["Overdue"])
    assert seen["Asked by Ana"] == (3, ["New", "Old", "Overdue"])


def test_a_layout_saved_before_filters_had_rows_keeps_working(api):
    # Rows stored by Task-list tabs 2/3: a filter of types, states and whom it waits on, and no widgets column value.
    old = [{"builtin": "done", "name": "Done Recently"}, {"builtin": "active", "name": "Doing"},
           {"name": "Waiting on bots", "types": [], "states": ["waiting"], "waiting_on": "bot"},
           {"name": "Product", "types": ["general"], "states": ["open", "doing"], "waiting_on": ""}]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_task_tabs(bot,viewer,tabs_json,updated,updated_by) VALUES('ops','',?,?,?)",
                  (json.dumps(old), "2026-10-20T00:00:00Z", "human:ana"))
    task(api, "Waits on cpo", "bot:ops", "human:ana", status="waiting", waiting_on="bot:cpo")
    task(api, "Asked of cpo, waiting", "bot:cpo", "bot:ops", status="waiting", waiting_on="bot:finance")
    layout = tabs(api, "ops")
    assert layout["saved"] is True and layout["widgets"] == WIDGETS
    assert [t["name"] for t in layout["tabs"]] == ["Done Recently", "Doing", "Waiting on bots", "Product"]
    assert layout["tabs"][0] == {"builtin": "done", "name": "Done Recently", "show_count": True}
    assert layout["tabs"][2]["conditions"] == [{"field": "assigned_to", "value": "this_bot"},
                                               {"field": "status", "value": ["waiting"]},
                                               {"field": "waiting_on", "value": "bot"}]
    assert layout["tabs"][3]["conditions"][2] == {"field": "type", "value": ["general"]}
    # Still only the bot's own tasks, as before.
    assert shown(api, "ops")["Waiting on bots"] == (1, ["Waits on cpo"])
    # A client still sending that shape is stored as rows.
    saved = save(api, "ops", [{"name": "Old style", "types": [], "states": ["waiting"], "waiting_on": "bot"}])
    assert saved["tabs"][0]["conditions"] == layout["tabs"][2]["conditions"]
