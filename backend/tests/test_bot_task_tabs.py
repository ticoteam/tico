"""A bot's sidebar (backend/bot_task_tabs.py): one layout per bot that every viewer shares, the Assistant's per person,
changed only by the bot or a person who manages it, with no cap on tabs; widgets in order, hidden and with heights; and
filter tabs of rows the server checks, counted for each viewer ("me" and "others" are the person looking)."""

import json
from datetime import datetime, timedelta, timezone

from backend import hubdb as H, task_relations as TR
from backend.tests.test_api import api, as_member, get, post, put, setup_attempt  # noqa: F401  (api is a fixture)

ACTIVE = ["open", "doing", "waiting"]
OTHERS = {"name": "Assigned to Others", "show_count": True, "conditions": [
    {"field": "asked_by", "value": "this_bot"}, {"field": "assigned_to", "value": "others"},
    {"field": "status", "value": ACTIVE}]}
DEFAULT = [{"builtin": "needs", "name": "Needs You", "show_count": True},
           {"builtin": "active", "name": "Doing", "show_count": True},
           {"builtin": "blocked", "name": "Blocked", "show_count": True}, OTHERS]
DEFAULT[0]['conditions'] = [{'field': 'needs', 'value': 'you'}]
DEFAULT[1]['conditions'] = [{'field': 'assigned_to', 'value': 'this_bot'}, {'field': 'status', 'value': ['open', 'doing']}]
DEFAULT[2]['conditions'] = [{'field': 'assigned_to', 'value': 'this_bot'}, {'field': 'status', 'value': ['waiting']},
                            {'field': 'waiting_on', 'value': 'not_me'}]
WIDGETS = [{"id": w, "hidden": False, "height": None} for w in ("tasks", "updates", "files")]
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
            if name in ("created_at", "updated_at", "done_at"):
                value = stamp(value)
            c.execute(f"UPDATE tasks SET {name}=? WHERE id=?", (value, tid))
    return tid


def bot_wait(api, title, owner="bot:ops", requester="human:ana", prerequisite_owner="bot:cpo", **columns):
    tid = task(api, title, owner, requester, status="waiting", **columns)
    pre = task(api, "Prerequisite for " + title, prerequisite_owner, "human:ana")
    with api.app.state.store.transaction() as c:
        TR.relate(c, owner, tid, pre, "blocked_by")
    return tid, pre


def test_a_bot_starts_with_the_new_default_tabs_and_widgets(api):
    layout = tabs(api, "ops")
    assert layout["tabs"] == DEFAULT and layout["widgets"] == WIDGETS
    assert layout["saved"] is False and layout["personal"] is False and layout["can_edit"] is True
    assert "done" not in [tab.get("builtin") for tab in layout["tabs"]]


def test_a_layout_is_the_bots_and_changing_one_never_moves_anothers(api):
    layout = [DEFAULT[1], PRODUCT, {**DEFAULT[0], "name": "For you", "show_count": False}]
    widgets = [{"id": "files", "hidden": False, "height": 240}, {"id": "tasks", "hidden": False, "height": None},
               {"id": "updates", "hidden": True, "height": None}]
    saved = save(api, "ops", layout, widgets=widgets)
    assert saved["tabs"] == layout and saved["widgets"] == widgets and saved["updated_by"] == "human:ana"
    # Everyone who opens the bot sees the same sidebar: Cara, who cannot change it, too.
    assert tabs(api, "ops", "cara-test")["tabs"] == layout and tabs(api, "ops", "cara-test")["widgets"] == widgets
    assert tabs(api, "ops", "cara-test")["can_edit"] is False
    # Leaving the widgets out keeps them; another bot keeps its own.
    assert save(api, "ops", [PRODUCT])["widgets"] == widgets
    assert tabs(api, "cpo")["tabs"] == DEFAULT and tabs(api, "cpo")["widgets"] == WIDGETS
    # A partial widget list keeps the rest after it, as they come by default.
    assert [w["id"] for w in save(api, "cpo", DEFAULT, "ben-test", widgets=[{"id": "files"}])["widgets"]] == \
        ["files", "tasks", "updates"]
    # An old saved Routines widget is dropped: routines are automations.
    assert [w["id"] for w in save(api, "cpo", DEFAULT, "ben-test", widgets=[{"id": "routines"}])["widgets"]] == \
        ["tasks", "updates", "files"]
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
    refused([{"field": "waiting_on", "value": "someone"}], "Waiting on is any, person, bot, me or not_me")
    refused([{"field": "needs", "value": "everyone"}], "Needs is you")
    refused([{"field": "created_within", "value": 0}], "number of days")
    refused([{"field": "done_within", "value": "3"}], "number of days")
    refused([{"field": "due", "value": "soon"}], "Due within")
    refused([{"field": "label", "value": " "}], "label is 1 to 60")
    refused([{"field": "status", "value": ACTIVE, "extra": 1}], "extra")
    r = save(api, "ops", [{"builtin": "done", "name": "x", "conditions": [{"field": "status", "value": "open"}]}],
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


def test_a_filter_with_more_than_one_page_reaches_every_task(api, monkeypatch):
    from backend import bot_task_tabs
    monkeypatch.setattr(bot_task_tabs, "ROWS", 2)
    for n in range(5):
        task(api, f"T{n}", "bot:ops", "human:ana", created_at=5 - n)
    save(api, "ops", [DEFAULT[0], {"name": "All", "conditions": [{"field": "assigned_to", "value": "this_bot"}]}])
    first = get(api, "bots/ops/task-tabs/tasks?tab=c1", "ben-test")["tabs"][0]
    assert first["key"] == "c1" and first["count"] == 5 and len(first["ids"]) == 2 and first["next"]
    seen, page = list(first["ids"]), first
    while page["next"]:
        out = get(api, f"bots/ops/task-tabs/tasks?tab=c1&before={page['next']}", "ben-test")
        [page] = out["tabs"]
        assert {t["id"] for t in out["tasks"]} == set(page["ids"])
        seen += page["ids"]
    title = {t["id"]: t["title"] for t in get(api, "bots/ops/task-tabs/tasks?tab=c1", "ben-test")["tasks"]}
    assert len(seen) == len(set(seen)) == 5 and [title[i] for i in seen[:2]] == ["T4", "T3"]
    get(api, "bots/ops/task-tabs/tasks?before=x|y", "ben-test", expected=422)


def test_each_field_filters(api):
    task(api, "New", "bot:ops", "human:ana", created_at=1)
    task(api, "Old", "bot:ops", "human:ana", created_at=10, updated_at=10)
    task(api, "Waits on Ben", "bot:ops", "human:ana", status="waiting", waiting_on="human:ben", created_at=10, updated_at=10)
    bot_wait(api, "Waits on cpo", created_at=10, updated_at=10)
    task(api, "Done lately", "bot:ops", "human:ana", status="done", done_at=5, created_at=10, updated_at=5)
    task(api, "Done long ago", "bot:ops", "human:ana", status="done", done_at=40, created_at=50, updated_at=40)
    task(api, "Overdue", "bot:ops", "human:ana", due_at="2020-01-01", created_at=10, updated_at=10)
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
        c.execute("INSERT INTO bot_task_tabs(bot,viewer,tabs,updated_at,updated_by) VALUES('ops','',?,?,?)",
                  (json.dumps(old), "2026-10-20T00:00:00Z", "human:ana"))
    bot_wait(api, "Waits on cpo")
    bot_wait(api, "Asked of cpo, waiting", "bot:cpo", "bot:ops", prerequisite_owner="bot:finance")
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


def test_default_filters_are_disjoint_and_viewer_relative(api):
    task(api, "Open", "bot:ops", "human:ana", status="open")
    task(api, "Doing", "bot:ops", "human:ana", status="doing")
    bot_wait(api, "Waits on bot")
    task(api, "Waits on Ana", "bot:ops", "human:ana", status="waiting", waiting_on="human:ana")
    task(api, "Unspecified wait", "bot:ops", "human:ana", status="waiting", waiting_on=None)
    task(api, "Review", "bot:ops", "human:ana", status="review")
    task(api, "Viewer owns open", "human:ana", "bot:ops", status="open")
    task(api, "Viewer owns finished", "human:ana", "bot:ops", status="done")
    task(api, "Viewer owns unrelated", "human:ana", "bot:cpo", status="open")
    seen = shown(api, "ops")
    assert seen["Doing"] == (2, ["Doing", "Open"])
    assert seen["Blocked"] == (2, ["Unspecified wait", "Waits on bot"])
    assert seen["Needs You"] == (2, ["Viewer owns open", "Waits on Ana"])
    assert shown(api, "ops", "ben-test")["Blocked"] == (3, ["Unspecified wait", "Waits on Ana", "Waits on bot"])


def test_default_tab_edits_persist_and_empty_conditions_are_not_reset(api):
    task(api, "Open", "bot:ops", "human:ana", status="open")
    task(api, "Wait", "bot:ops", "human:ana", status="waiting", waiting_on="human:ana")
    changed = {**DEFAULT[2], "name": "Changed", "show_count": False,
               "conditions": [{"field": "status", "value": ["open"]}]}
    assert save(api, "ops", [changed])["tabs"] == [changed]
    assert tabs(api, "ops")["tabs"] == [changed]
    assert shown(api, "ops")["Changed"] == (1, ["Open"])
    changed["conditions"] = []
    assert save(api, "ops", [changed])["tabs"][0]["conditions"] == []
    assert shown(api, "ops")["Changed"] == (2, ["Open", "Wait"])
    assert save(api, "ops", [DEFAULT[2]])["tabs"] == [DEFAULT[2]]
    assert shown(api, "ops")["Blocked"] == (0, [])


def test_all_legacy_builtins_keep_order_names_counts_and_receive_default_conditions(api):
    old = [{"builtin": key, "name": "Old " + key, "show_count": False} for key in ("blocked", "needs", "active", "done")]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_task_tabs(bot,viewer,tabs,updated_at,updated_by) VALUES('ops','',?,?,?)",
                  (json.dumps(old), "2026-10-20T00:00:00Z", "human:ana"))
    new = tabs(api, "ops")["tabs"]
    assert [(t["builtin"], t["name"], t["show_count"]) for t in new] == [(t["builtin"], t["name"], False) for t in old]
    for i, default in zip((1, 2, 0), DEFAULT):
        assert new[i]["conditions"] == default["conditions"]
    assert "conditions" not in new[3]
    # An older client can still save its original shape.
    assert save(api, "ops", old)["tabs"] == new


def test_needs_you_supplements_deduplicate_beyond_first_page_without_leaking_private_tasks(api, monkeypatch):
    from backend import bot_task_tabs
    monkeypatch.setattr(bot_task_tabs, "ROWS", 1)
    older = task(api, "Older question", "bot:ops", "human:ana", status="waiting", waiting_on="human:cara", created_at=5)
    newer = task(api, "New wait", "bot:ops", "human:ana", status="waiting", waiting_on="human:cara", created_at=1)
    secret = task(api, "Private", "bot:ops", "human:ben", status="waiting", waiting_on="human:cara")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (secret,))
    # Ops reports to no one, so it hangs under the founder: Ana reads its private task; Cara is no party and no manager.
    assert get(api, f"tasks/{secret}")["task"]["title"] == "Private"
    get(api, f"tasks/{secret}", "cara-test", expected=404)
    out = get(api, f"bots/ops/task-tabs/tasks?tab=needs&match_ids={older},{secret}", "cara-test")
    [tab] = out["tabs"]
    assert tab["ids"] == [newer] and tab["count"] == 2 and tab["next"]
    assert tab["matched_ids"] == [older]
    assert secret not in {t["id"] for t in out["tasks"]}


def test_attention_counts_include_approvals_in_whole_list():
    from backend import views
    items = [{"kind": "task", "id": "owned", "owner_id": "human:ana", "requester_id": "bot:ops", "status": "open"},
             {"kind": "waiting", "id": "wait", "owner_id": "bot:ops", "requester_id": "human:ana", "status": "waiting", "waiting_on": "human:ana"},
             {"kind": "approval", "id": "approval", "requested_by_id": "bot:ops"},
             {"kind": "approval", "id": "other", "requested_by_id": "human:ben"}]
    assert views.needs_bots(items, "human:ana") == {"bot:ops": 3}


def test_needs_condition_can_be_removed_changed_and_restored(api):
    task(api, "Mine", "human:ana", "bot:ops", status="open")
    task(api, "Wait on viewer", "bot:ops", "human:ana", status="waiting", waiting_on="human:ana")
    task(api, "Bot work", "bot:ops", "human:ana", status="doing")
    assert shown(api, "ops")["Needs You"] == (2, ["Mine", "Wait on viewer"])
    changed = {**DEFAULT[0], "conditions": [{"field": "status", "value": ["doing"]}]}
    save(api, "ops", [changed])
    assert tabs(api, "ops")["tabs"] == [changed]
    assert shown(api, "ops")["Needs You"] == (1, ["Bot work"])
    save(api, "ops", [{**DEFAULT[0], "conditions": []}])
    assert shown(api, "ops")["Needs You"] == (3, ["Bot work", "Mine", "Wait on viewer"])
    save(api, "ops", [DEFAULT[0]])
    assert shown(api, "ops")["Needs You"] == (2, ["Mine", "Wait on viewer"])


def test_bot_filter_finds_an_api_saved_wait_and_drops_it_on_resolution(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug IN ('ops','cpo')")
    _, _, attempt = setup_attempt(api, "ops")
    tid, pre = bot_wait(api, "Ship after the review")
    before = get(api, "tasks/" + tid, attempt["token"])["task"]
    saved = post(api, "tasks/" + tid, {"version": before["version"], "status": "waiting",
                "waiting_on": "bot:cpo", "note": "Needs the review first."}, attempt["token"])
    saved = saved.get("task", saved)
    assert saved["waiting_on"] is None and saved["note"] == "Needs the review first."
    save(api, "ops", [{"name": "Bot waits", "conditions": [{"field": "waiting_on", "value": "bot"}]}])
    out = get(api, "bots/ops/task-tabs/tasks")
    assert out["tabs"][0]["count"] == 1 and out["tabs"][0]["ids"] == [tid]
    assert out["tasks"][0]["waiting_on"] is None
    assert out["tasks"][0]["relations"]["blocks"][0]["owner_id"] == "bot:cpo"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", pre, status="done", note="Reviewed.")
    out = get(api, "bots/ops/task-tabs/tasks")
    assert out["tabs"][0]["count"] == 0 and out["tabs"][0]["ids"] == [] and out["tasks"] == []


def test_bot_filter_uses_readable_blockers_and_preserves_other_waits(api):
    normal, _ = bot_wait(api, "Bot prerequisite")
    person, _ = bot_wait(api, "Person wait", waiting_on="human:ana")
    for kind in ("gate", "worktree"):
        bot_wait(api, kind + " hold", blocked_kind=kind)
    mixed, _ = bot_wait(api, "Person blocker after bot")
    asked, _ = bot_wait(api, "Open question")
    hidden, pre = bot_wait(api, "Private prerequisite")
    with api.app.state.store.transaction() as c:
        human = H.task_create(c, "human:ana", "Answer the question", "", "human:ana", lint=False, private=False)
        TR.relate(c, "bot:ops", mixed, human["id"], "blocked_by")
        H.task_ask(c, "bot:ops", asked, "Is this ready for review?")
        # The relation existed before its prerequisite became private; Cara must not infer it from the count.
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (pre,))
    save(api, "ops", [{"name": "Bot waits", "conditions": [{"field": "waiting_on", "value": "bot"}]},
                      {"name": "People", "conditions": [{"field": "waiting_on", "value": "person"}]}])
    ana = get(api, "bots/ops/task-tabs/tasks")["tabs"]
    cara = get(api, "bots/ops/task-tabs/tasks", "cara-test")["tabs"]
    assert ana[0]["count"] == 2 and set(ana[0]["ids"]) == {normal, hidden}
    assert cara[0]["count"] == 1 and cara[0]["ids"] == [normal]
    assert ana[1]["count"] == 1 and ana[1]["ids"] == [person]
