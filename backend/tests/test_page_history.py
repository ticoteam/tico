"""Page history (backend/page_history.py): the pages a person opened, newest first and paged, theirs alone, and Tico's
`tico_page_history` reading them as the person."""

from backend import hubdb as H
from backend import page_history
from backend.tests.helpers.api import headers, setup_attempt
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.mcp import call
from backend.tests.helpers.openapi_v2 import conforms


def answer(api, method, token="ana-test", **kwargs):
    return getattr(api, method)("/api/v2/page-history", headers=headers(token), **kwargs)


def ok(api, method, token="ana-test", **kwargs):
    r = answer(api, method, token, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def opened(api, page, token="ana-test", **fields):
    return ok(api, "post", token, json={"page": page, **fields})["visit"]


def test_a_visit_reads_back_newest_first_as_the_contract_says(api):
    board = opened(api, "/tasks/engineering", title="Tasks,  Engineering\n")
    task = opened(api, "/tasks/engineering/412", title="Fix checkout", subject_type="task", subject_id="412")
    by_tico = opened(api, "/settings/agents", title="Settings, Agents", via="assistant")
    assert board["title"] == "Tasks, Engineering"  # one line
    assert (task["subject_type"], task["subject_id"], task["via"]) == ("task", "412", "")
    assert by_tico["via"] == "assistant"

    listed = ok(api, "get")
    assert listed == {"visits": [by_tico, task, board], "next": None}

    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    for method, result in (("get", listed), ("post", {"visit": task}), ("delete", {"cleared": 3})):
        schema = document["paths"]["/api/v2/page-history"][method]["responses"]["200"]["content"]["application/json"]
        assert conforms(result, schema["schema"], document) is None, method

    assert ok(api, "delete") == {"cleared": 3}
    assert ok(api, "get")["visits"] == []


def test_pages_follow_before_and_page_keeps_one_area(api):
    pages = ["/tasks", "/tasks/engineering", "/tasksheet", "/company", "/tasks/engineering/412"]
    made = [opened(api, page) for page in pages]

    first = ok(api, "get", params={"limit": 2})
    second = ok(api, "get", params={"limit": 2, "before": first["next"]})
    third = ok(api, "get", params={"limit": 2, "before": second["next"]})
    seen = [v["id"] for v in first["visits"] + second["visits"] + third["visits"]]
    assert seen == [v["id"] for v in reversed(made)]  # every visit once, newest first
    assert third["next"] is None

    under = ok(api, "get", params={"page": "/tasks"})["visits"]
    assert [v["page"] for v in under] == ["/tasks/engineering/412", "/tasks/engineering", "/tasks"]

    assert answer(api, "get", params={"before": "not-a-cursor"}).status_code == 422
    assert answer(api, "post", json={"page": "tasks"}).status_code == 422  # a path starts with /
    assert answer(api, "post", json={"page": "/tasks", "subject_type": "task"}).status_code == 422


def test_a_visit_past_thirty_days_never_lists_and_goes_at_the_next_visit(api):
    old = opened(api, "/company")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE page_visits SET created_at=? WHERE id=?",
            (H.shift(H.now(), days=-page_history.KEEP_DAYS - 1), old["id"]),
        )
    assert ok(api, "get")["visits"] == []
    opened(api, "/tasks")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM page_visits WHERE id=?", (old["id"],)).fetchone()[0] == 0


def test_a_history_is_its_persons_alone_and_a_bot_is_refused(api):
    opened(api, "/finance", title="Finance")
    # Another person, the owner included, reads only their own; clearing theirs leaves ana's.
    assert ok(api, "get", "cara-test")["visits"] == []
    assert ok(api, "delete", "cara-test") == {"cleared": 0}
    opened(api, "/company", "ben-test")
    assert [v["page"] for v in ok(api, "get", "ben-test")["visits"]] == ["/company"]
    assert [v["page"] for v in ok(api, "get")["visits"]] == ["/finance"]

    _, _, attempt = setup_attempt(api)  # a bot's own run
    for method in ("get", "post", "delete"):
        r = answer(api, method, attempt["token"], **({"json": {"page": "/finance"}} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["error"]["code"] == "identity", (method, r.text)
    error, _ = call(api, "tico_page_history", {}, token=attempt["token"])
    assert error


def test_tico_reads_the_persons_history_through_its_tool(api):
    opened(api, "/tasks/engineering", title="Tasks, Engineering")
    opened(api, "/tasks/engineering/412", title="Fix checkout", subject_type="task", subject_id="412")
    opened(api, "/company", title="Company")
    opened(api, "/company", "cara-test", title="Company")
    _, attempt = assistant_turn(api, "ana-test")

    error, shown = call(api, "tico_page_history", {"page": "/tasks", "limit": 1}, token=attempt["token"])
    assert not error, shown
    [last] = shown["visits"]
    assert (last["page"], last["title"], last["subject_id"]) == ("/tasks/engineering/412", "Fix checkout", "412")
    error, older = call(api, "tico_page_history", {"page": "/tasks", "before": shown["next"]}, token=attempt["token"])
    assert not error and [v["title"] for v in older["visits"]] == ["Tasks, Engineering"]
    assert older["next"] is None
