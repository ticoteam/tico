"""A bot's Files rail: search and kind narrow the newest-first list, recent means the content changed, and the files a
bot names in its messages resolve to links (docs/files.md)."""

import importlib.util
from pathlib import Path

from backend.tests.helpers.api import headers
from backend.tests.helpers.files import publish, turn

SEPT = 1757000000  # 2025-09-04T15:33:20Z, an old commit's time


def page(api, query="", who="ben-test"):
    r = api.get("/api/v2/bots/ops/files?" + query, headers=headers(who))
    assert r.status_code == 200, r.text
    return r.json()


def titles(body):
    return [f["title"] for f in body["files"]]


def put(api, attempt, path, text="x", **fields):
    r = publish(api, attempt, path.rsplit("/", 1)[-1], text, path=path, **fields)
    assert r.status_code == 200, r.text
    return r.json()["file"]["id"]


def test_search_and_kind_narrow_the_newest_first_list_and_page_on(api):
    _, attempt = turn(api)
    put(api, attempt, "reports/launch-plan.md", "# Launch plan\n\nMonday.")
    put(api, attempt, "drafts/2026-11-11-winberg-reply/letter.docx")
    put(api, attempt, "reports/traffic.csv", "page,visits\n/home,1\n")
    put(api, attempt, "reports/launch-budget.csv", "line,cost\nads,1\n")
    assert titles(page(api, "q=winberg")) == ["letter.docx"], "a folder's name finds the file under it"
    assert titles(page(api, "q=monday")) == ["launch-plan.md"], "the text Library search reads"
    assert titles(page(api, "q=launch")) == ["launch-budget.csv", "launch-plan.md"], "still newest first"
    assert titles(page(api, "kind=data")) == ["launch-budget.csv", "traffic.csv"]
    assert titles(page(api, "kind=data&q=launch")) == ["launch-budget.csv"]
    assert page(api, "q=nothing-like-this")["total"] == 0
    first = page(api, "q=launch&limit=1")
    assert titles(first) == ["launch-budget.csv"] and first["total"] == 2 and first["has_more"]
    rest = page(api, "q=launch&limit=1&cursor=" + first["next_cursor"])
    assert titles(rest) == ["launch-plan.md"] and not rest["has_more"]
    assert api.get("/api/v2/bots/ops/files?kind=word", headers=headers("ben-test")).status_code == 422
    assert page(api, "q=winberg", who="cara-test")["files"] == [], "a private chat's file stays out of a search"


def test_recent_is_when_the_content_changed_not_when_it_was_copied(api):
    _, attempt = turn(api)
    # A computer publishing a checkout for the first time: the file's commit time, not the copy's.
    old = put(api, attempt, "notes/2025-09-04-context.md", "# Context", modified=str(SEPT))
    new = put(api, attempt, "reports/2026-11-12-legal-review-pack.docx")
    listed = page(api)["files"]
    assert [f["id"] for f in listed] == [new, old]
    assert listed[1]["last_activity_at"].startswith("2025-09-04T15:33:20")
    # The bug: the same bytes sent again (a new computer, a lost outbox) moved the old file to the top.
    put(api, attempt, "notes/2025-09-04-context.md", "# Context")
    put(api, attempt, "notes/2025-09-04-context.md", "# Context", modified=str(SEPT + 86400 * 400))
    assert [f["id"] for f in page(api)["files"]] == [new, old], "an unchanged publish is no activity"
    # A file copied before uploads carried a time goes back to when it was written, once its time arrives.
    copied = put(api, attempt, "notes/2025-09-05-call.md", "# Call")
    assert [f["id"] for f in page(api)["files"]] == [copied, new, old]
    put(api, attempt, "notes/2025-09-05-call.md", "# Call", modified=str(SEPT + 86400))
    listed = page(api)["files"]
    assert [f["id"] for f in listed] == [new, copied, old]
    assert listed[1]["last_activity_at"].startswith("2025-09-05") and listed[1]["first_activity_at"].startswith(
        "2025-09-05"
    )
    # A real edit is activity; a time in the future or nonsense is the server's own clock.
    put(api, attempt, "notes/2025-09-04-context.md", "# Context, revised", modified="4102444800")
    put(api, attempt, "notes/2025-09-05-call.md", "# Call, revised", modified="not a time")
    assert [f["id"] for f in page(api)["files"]] == [copied, old, new]


def test_the_files_a_bot_names_resolve_to_one_file_or_none(api):
    _, attempt = turn(api)
    pack = put(api, attempt, "reports/2026-11-12-legal-review-pack.docx")
    paiva = put(api, attempt, "drafts/2026-11-10-paiva-reply/notes-for-chris.docx")
    put(api, attempt, "drafts/2026-11-11-winberg-reply/notes-for-chris.docx")
    said = [
        "reports/2026-11-12-legal-review-pack.docx",
        "2026-11-12-legal-review-pack.docx",
        "/home/bot/checkout/reports/2026-11-12-legal-review-pack.docx",
        "drafts/2026-11-10-paiva-reply/notes-for-chris.docx",
        "notes-for-chris.docx",
        "reports/missing.docx",
    ]
    query = "&".join("m=" + s for s in said)
    r = api.get("/api/v2/bots/ops/files/mentions?" + query, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    found = {k: v["id"] for k, v in r.json()["files"].items()}
    assert found == {said[0]: pack, said[1]: pack, said[2]: pack, said[3]: paiva}, "two of a name is no guess"
    other = api.get("/api/v2/bots/ops/files/mentions?" + query, headers=headers("cara-test"))
    assert other.status_code == 200 and other.json()["files"] == {}, "only files the reader sees"


def test_migration_puts_a_touched_files_time_back_to_its_last_real_activity(api):
    _, attempt = turn(api)
    fid = put(api, attempt, "reports/a.md", "# A")
    store = api.app.state.store
    with store.transaction() as c:
        made = c.execute("SELECT last_activity_at FROM bot_files WHERE id=?", (fid,)).fetchone()[0]
        # As an unchanged publish left a file before this change: a touch, and the row's time moved to it.
        c.execute(
            "INSERT INTO bot_file_activity(file_id,actor,action,version,created_at) VALUES(?,?,?,?,?)",
            (fid, "bot:ops", "published", 1, "2099-01-01T00:00:00.000000Z"),
        )
        c.execute("UPDATE bot_files SET last_activity_at='2099-01-01T00:00:00.000000Z' WHERE id=?", (fid,))
        path = next(Path("backend/migrations").glob("*_bot_files_activity_from_content.py"))
        spec = importlib.util.spec_from_file_location("bot_files_activity_from_content", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.up(c)
        assert c.execute("SELECT last_activity_at FROM bot_files WHERE id=?", (fid,)).fetchone()[0] == made
