"""`files_use_weekly` (backend/file_use.py): how often people and bots list, open, search and review files, per week.

Counts only: the view holds the week, person or bot, the action, how many distinct people or bots did it and how many
times. Never a file, its name, its content, a person or a bot. Like `mcp_call_weekly`, the owner reads it, and so does a
bot the owner grants outcome stats (backend/outcome_stats.py); anyone else gets "no such table".
"""

import json

from backend import file_use
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers
from backend.tests.helpers.mcp import call
from backend.tests.helpers.sql import query

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SIGNED = b"%PDF-1.7\n1 0 obj<<>>endobj\n2 0 obj<</Signed true>>endobj\ntrailer<<>>\n%%EOF\n"


def as_bot(api, slug):
    token = slug + "-bot-test"
    with api.app.state.store.transaction() as c:
        X.bot(c, slug)
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (slug, "hermes", "x-" + slug, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def task_pdf(api):
    """A task Ana asked bot:ops for, with a PDF on it: its file id."""
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", "Sign the declaration", "Sign and return it.", "bot:ops", lint=False)["id"]
    r = api.post(f"/api/v2/tasks/{tid}/files", files={"file": ("d.pdf", PDF, "application/pdf")}, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def sign(api, fid):
    return api.post(
        f"/api/v2/files/{fid}/signings",
        data={"version": "1", "marks": json.dumps([{"kind": "signature", "page": 1}])},
        files={"file": ("d.pdf", SIGNED, "application/pdf")},
        headers=headers(),
    )


def a_file(api, bot, task=None):
    """A bot's published file with one version, as files.py keeps it; on `task` when given."""
    fid = "f" + H.new_id()[:8]
    at = H.now()
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,current_version,first_activity_at,"
            "last_activity_at,task_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid,
                bot,
                f"task:{task}" if task else "bot",
                "cuts/" + fid + ".md",
                "Intro cut",
                "doc",
                "tico_blob",
                1,
                at,
                at,
                task,
            ),
        )
        c.execute(
            "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(?,?,?,?,?,?,?)",
            ("b" + fid, "bot:" + bot, "d" + fid, 10, "intro.md", "text/markdown", at),
        )
        c.execute(
            "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,repo_path,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fid, 1, "b" + fid, "d" + fid, 10, "intro.md", "text/markdown", "cuts/" + fid + ".md", "bot:" + bot, at),
        )
        if task:
            c.execute("INSERT INTO task_assets VALUES(?,?)", (task, "b" + fid))
    return fid


def ops_task(api):
    """A shared task Ana asked bot:ops for."""
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "human:ana", "Post the release note", "Write it and post it.", "bot:ops", lint=False)[
            "id"
        ]
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (tid,))
    return tid


VIEW = "SELECT who, action, actors, uses FROM files_use_weekly ORDER BY who, action"


def rows(api, token="ana-test"):
    return query(api, VIEW, token)["rows"]


def ok(response):
    assert response.status_code == 200, response.text
    return response


def test_each_files_action_counts_once_per_use_by_person_or_bot_and_refusals_count_nothing(api):
    pdf = task_pdf(api)
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
    tid = ops_task(api)
    theirs = a_file(api, "finance", task=tid)
    reviewed = api.post(
        "/api/v2/file-reviews", json={"file": theirs, "task": tid, "verdict": "approve"}, headers=headers(ops)
    )
    ok(reviewed)
    ok(sign(api, pdf))
    # 5. Refusals count nothing: a file nobody has, a version it does not have, a bot's own review.
    assert api.get("/api/v2/files/fnosuchfile", headers=headers()).status_code == 404
    assert api.get(f"/api/v2/files/{pdf}/versions/9", headers=headers()).status_code == 404
    mine = a_file(api, "ops", task=tid)
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
