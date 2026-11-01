"""Learnings history: a computer reports memory commits; readers see them, a source only when they may read it."""

import json

import pytest

from backend.memory_history import MAX_DOCUMENT_BYTES, MAX_REPORT_BYTES
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)

SHA = "a" * 40


def commit(sha=SHA, **extra):
    return {"sha": sha, "subject": "Check the live PR before saying a release shipped", "author": "ops",
            "committed": "2026-10-05T10:00:00+02:00", "files": ["memory/learnings.md"], **extra}


def history(api, who, bot="ops"):
    r = api.get(f"/api/v2/bots/{bot}/memory", headers=headers(who))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_reported_update_shows_its_source_only_to_readers_of_that_chat(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Remember to check the live PR"}, "ben-test")
    attempt = claim(api, machine, "ops")
    body = {"commits": [commit(attempt=attempt["id"], shared=True, diff="+- Check the live PR")],
            "documents": {"memory/learnings.md": "# Learnings\n- Check the live PR\n", "secrets/x": "no"}}
    assert post(api, "bots/ops/memory/report", body, machine["token"])["recorded"] == 1
    # The same report again, or one naming a run of another bot, changes nothing.
    assert post(api, "bots/ops/memory/report", body, machine["token"])["recorded"] == 0

    mine = history(api, "ben-test")["updates"][0]
    assert mine["source"]["conversation"] and "live PR" in mine["source"]["excerpt"]
    assert mine["when"] == "2026-10-05T08:00:00.000000Z" and mine["shared"] and mine["diff"]
    with api.app.state.store.transaction() as c:       # a repository on GitHub gives each pushed commit a link
        c.execute("UPDATE bot_config SET repo='acme/bot-ops' WHERE bot='ops'")
    assert history(api, "ben-test")["updates"][0]["url"] == "https://github.com/acme/bot-ops/commit/" + SHA
    for other in ("ana-test", "cara-test"):      # the owner included: a direct chat is not theirs
        theirs = history(api, other)
        assert theirs["updates"][0]["source"] is None and "live PR" not in str(theirs["updates"][0]["source"])
    files = api.get("/api/employees/ops/files", headers=headers("ben-test")).json()
    assert "Check the live PR" in files["memory/learnings.md"]


def test_only_the_assigned_computer_reports_and_runs_must_be_the_bots_own(api):
    machine, other = runner(api), runner(api, label="Other Mac")
    assign(api, machine, "ops")
    post(api, "bots/ops/memory/report", {"commits": [commit()]}, other["token"], expected=403)
    post(api, "bots/ops/memory/report", {"commits": [commit()]}, "ben-test", expected=403)
    post(api, "bots/ops/memory/report", {"commits": [commit(files=["AGENT.md"])]}, machine["token"])
    post(api, "bots/ops/memory/report", {"commits": [commit("b" * 40, attempt="not-a-run")]}, machine["token"])
    rows = history(api, "ben-test")["updates"]
    assert [r["sha"] for r in rows] == ["b" * 40] and rows[0]["source"] is None


def test_large_documents_round_trip_and_report_allowance_is_runner_route_only(api):
    machine = runner(api)
    assign(api, machine, "ops")
    text = "x" * 2_200_000
    post(api, "bots/ops/memory/report", {"documents": {"memory/learnings.md": text}}, machine["token"])
    assert history(api, "ben-test")["documents"]["memory/learnings.md"] == text
    # Button loads and history pagination keep dates/status but do not transfer multi-megabyte files.
    small = api.get("/api/v2/bots/ops/memory?include_documents=false", headers=headers("ben-test"))
    assert small.status_code == 200
    assert small.json()["documents"] == {}
    assert small.json()["document_status"]["memory/learnings.md"]["updated_at"]
    assert len(small.content) < 10_000
    # Worst-case JSON escaping, both files at the byte ceiling, and all 50 diffs also fit.
    body = {"documents": {path: "\x01" * MAX_DOCUMENT_BYTES
                          for path in ("memory/learnings.md", "memory/decisions.md")},
            "commits": [commit(f"{i:040x}", diff="\x01" * 20_000) for i in range(50)]}
    encoded = json.dumps(body).encode()
    assert 8_000_000 < len(encoded) < MAX_REPORT_BYTES
    r = api.post("/api/v2/bots/ops/memory/report", content=encoded,
                 headers={**headers(machine["token"]), "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    saved = history(api, "ben-test")["documents"]
    assert saved == body["documents"]
    post(api, "bots/ops/memory/report", {"documents": {"memory/learnings.md": text}}, "ben-test", expected=413)
    post(api, "bots/ops/memory/seen", {"padding": text}, machine["token"], expected=413)
    # Reject an excessive declared length before reading a potentially enormous request.
    r = api.post("/api/v2/bots/ops/memory/report", content=b"{}",
                 headers={**headers(machine["token"]), "Content-Length": str(MAX_REPORT_BYTES + 1)})
    assert r.status_code == 413


@pytest.mark.parametrize("path", ["memory/learnings.md", "memory/decisions.md"])
def test_oversized_documents_keep_the_copy_date_and_clear_when_the_file_fits(api, path):
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "bots/ops/memory/report", {"documents": {path: "original"}}, machine["token"])
    saved = history(api, "ben-test")["document_status"][path]["updated_at"]
    # The runner sends only the path; the stored date must remain the copy's date.
    post(api, "bots/ops/memory/report", {"oversized": [path]}, machine["token"])
    result = history(api, "ben-test")
    assert result["documents"][path] == "original"
    assert result["document_status"][path] == {"oversized": True, "updated": saved}
    # Server-side enforcement uses bytes too, even for a caller that sends the content.
    over = "é" * (MAX_DOCUMENT_BYTES // 2 + 1)
    assert len(over) < MAX_DOCUMENT_BYTES < len(over.encode("utf-8"))
    post(api, "bots/ops/memory/report", {"documents": {path: over}}, machine["token"])
    assert history(api, "ben-test")["documents"][path] == "original"
    assert history(api, "ben-test")["document_status"][path] == {"oversized": True, "updated": saved}
    # Returning to the previous content also clears the warning without redating the copy.
    post(api, "bots/ops/memory/report", {"documents": {path: "original"}}, machine["token"])
    assert history(api, "ben-test")["document_status"][path] == {"oversized": False, "updated": saved}
    post(api, "bots/ops/memory/report", {"documents": {path: "current"}}, machine["token"])
    result = history(api, "ben-test")
    assert result["documents"][path] == "current"
    assert result["document_status"][path]["oversized"] is False


def test_oversized_without_a_saved_copy_stays_visible_and_unknown_paths_are_ignored(api):
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "bots/ops/memory/report", {"oversized": ["memory/learnings.md", "secrets/x"]}, machine["token"])
    result = history(api, "ben-test")
    assert result["documents"] == {}
    assert result["document_status"] == {"memory/learnings.md": {"oversized": True, "updated": ""}}
