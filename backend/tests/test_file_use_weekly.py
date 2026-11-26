"""`files_use_weekly` (backend/file_use.py): how often people and bots list, open, search and review files, per week.

Counts only: the view holds the week, person or bot, the action, how many distinct people or bots did it and how many
times. Never a file, its name, its content, a person or a bot. Like `mcp_call_weekly`, the owner reads it, and so does a
bot the owner grants outcome stats (backend/outcome_stats.py); anyone else gets "no such table".
"""

from backend import file_use
from backend.tests.helpers.api import headers
from backend.tests.helpers.mcp import call
from backend.tests.helpers.sql import query
from backend.tests.test_file_signings import as_bot, sign, task_pdf
from backend.tests.test_task_asks_routes import a_file, bots_task

VIEW = "SELECT who, action, actors, uses FROM files_use_weekly ORDER BY who, action"


def rows(api, token="ana-test"):
    return query(api, VIEW, token)["rows"]


def ok(response):
    assert response.status_code == 200, response.text
    return response


def test_each_files_action_counts_once_per_use_by_person_or_bot_and_refusals_count_nothing(api):
    _, pdf = task_pdf(api)
    ops, finance = as_bot(api, "ops"), as_bot(api, "finance")

    # 1. A person opens one file twice.
    for _ in range(2):
        ok(api.get(f"/api/v2/files/{pdf}", headers=headers()))
    # 2. One bot lists files by the CLI's route, a second by the MCP tool.
    ok(api.get("/api/v2/files", headers=headers(ops)))
    failed, _ = call(api, "tico_file_list", {}, token=finance)
    assert not failed
    # 3. A search that finds nothing still counts.
    found = ok(api.get("/api/v2/files/search?q=zzqx-nothing-matches", headers=headers(ops))).json()
    assert not found["results"]
    # 4. A bot's review verdict and a person's signing are each one review.
    theirs = a_file(api, bot="finance")
    tid = bots_task(api, ops)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (tid,))
    reviewed = api.post(
        "/api/v2/file-reviews", json={"file": theirs, "task": tid, "verdict": "approve"}, headers=headers(ops)
    )
    ok(reviewed)
    ok(sign(api, pdf))
    # 5. Refusals count nothing: a file nobody has, one the reader may not see, a bot's own review.
    assert api.get("/api/v2/files/fnosuchfile", headers=headers()).status_code == 404
    assert api.get(f"/api/v2/files/{pdf}", headers=headers("cara-test")).status_code == 404
    mine = a_file(api, bot="ops")
    refused = api.post(
        "/api/v2/file-reviews", json={"file": mine, "task": tid, "verdict": "approve"}, headers=headers(ops)
    )
    assert refused.status_code == 403, refused.text

    assert rows(api) == [
        ["bot", "list", 2, 2],
        ["bot", "review", 1, 1],
        ["bot", "search", 1, 1],
        ["person", "open", 1, 2],
        ["person", "review", 1, 1],
    ]


def test_the_view_holds_counts_only_and_only_the_owner_and_a_granted_bot_read_it(api):
    with api.app.state.store.transaction() as c:
        file_use.record(c, "human:ana", "open")
        file_use.record(c, "bot:ops", "search")
    # 6. No column names a file, its content, a person or a bot.
    every = query(api, "SELECT * FROM files_use_weekly", "ana-test")
    assert every["columns"] == ["week", "who", "action", "actors", "uses"]
    assert not any("ana" in str(v) or "ops" in str(v) for row in every["rows"] for v in row)
    # The base rows are never read, by the owner or anyone else.
    assert query(api, "SELECT * FROM file_use_daily", "ana-test", expected=422)
    # 7. A bot without the grant does not see the view; a granted one does, and loses it on revoke.
    bot = as_bot(api, "ops")
    assert "no such table" in query(api, VIEW, bot, expected=422)["error"]["detail"]
    grant = api.put("/api/v2/outcome-stats/readers/ops", json={"read": True}, headers=headers())
    assert grant.status_code == 200, grant.text
    assert rows(api, bot) == [["bot", "search", 1, 1], ["person", "open", 1, 1]]
    assert query(api, "SELECT * FROM file_use_daily", bot, expected=422)
    api.put("/api/v2/outcome-stats/readers/ops", json={"read": False}, headers=headers())
    assert "no such table" in query(api, VIEW, bot, expected=422)["error"]["detail"]


def test_weeks_start_on_monday_utc(api):
    # 8. Sunday 23:59 and Monday 00:00 UTC fall in two weeks, as in the other weekly views.
    with api.app.state.store.transaction() as c:
        file_use.record(c, "human:ana", "list", at="2026-11-22T23:59:59Z")
        file_use.record(c, "human:ana", "list", at="2026-11-23T00:00:00Z")
        file_use.record(c, "human:ana", "list", at="2026-11-29T23:59:59Z")
    weeks = query(api, "SELECT week, uses FROM files_use_weekly ORDER BY week", "ana-test")["rows"]
    assert weeks == [["2026-11-16", 1], ["2026-11-23", 2]]


def test_one_row_per_day_actor_and_action_and_a_failed_count_never_fails_the_request(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        for _ in range(3):
            file_use.record(c, "bot:ops", "open")
        assert c.execute("SELECT count(*), sum(uses) FROM file_use_daily").fetchone()[:] == (1, 3)
        # Only people and bots: a service or an unknown caller is not counted.
        file_use.record(c, "service:backup", "open")
        assert c.execute("SELECT count(*) FROM file_use_daily").fetchone()[0] == 1

    def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(file_use, "record", broken)
    ok(api.get("/api/v2/files", headers=headers()))  # answers though the count failed
