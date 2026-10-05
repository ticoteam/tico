"""Learnings history: a computer reports memory commits; readers see them, a source only when they may read it."""

import subprocess

from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)
from runner import memory_history
from runner.state import State

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


def test_seen_clears_the_unseen_count(api):
    machine = runner(api)
    assign(api, machine, "ops")
    recent = commit(committed=__import__("backend.hubdb", fromlist=["now"]).now())
    post(api, "bots/ops/memory/report", {"commits": [recent]}, machine["token"])
    assert history(api, "ben-test")["unseen"] == 1
    post(api, "bots/ops/memory/seen", {}, "ben-test")
    assert history(api, "ben-test")["unseen"] == 0
    post(api, "bots/ops/memory/report", {"commits": [commit("c" * 40)]}, machine["token"])
    assert history(api, "ben-test")["unseen"] == 1


def test_the_runner_reads_memory_commits_and_sends_a_diff_only_once_shared(tmp_path):
    origin, root = tmp_path / "origin.git", tmp_path / "bot"
    git = lambda *a, cwd=root: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True, capture_output=True)
    git("config", "user.email", "ops@acme.example"); git("config", "user.name", "ops")
    (root / "AGENT.md").write_text("# ops\n"); git("add", "."); git("commit", "-qm", "Start")
    (root / "memory").mkdir(); (root / "memory/learnings.md").write_text("- Check the live PR\n")
    git("add", "."); git("commit", "-qm", "Learn to check the live PR", "-m", "Tico-Run: run-1")
    git("push", "-qu", "origin", "HEAD")
    (root / "memory/decisions.md").write_text("- Ship on Fridays\n")
    git("add", "."); git("commit", "-qm", "Decide on Fridays")

    class Fake:
        sent = []
        state = State(tmp_path / "state")
        class client:
            @staticmethod
            def post(path, body, key=None):
                Fake.sent.append(body)
    assert memory_history.report(Fake, "ops", root) == 2
    rows = {r["subject"]: r for r in Fake.sent[0]["commits"]}
    assert rows["Learn to check the live PR"]["attempt"] == "run-1" and rows["Learn to check the live PR"]["diff"]
    assert not rows["Decide on Fridays"]["shared"] and rows["Decide on Fridays"]["diff"] == ""
    assert set(Fake.sent[0]["documents"]) == {"memory/learnings.md", "memory/decisions.md"}
    assert memory_history.report(Fake, "ops", root) == 0            # nothing new
    git("push", "-q")
    assert memory_history.report(Fake, "ops", root) == 1            # now shared, with its diff
    assert Fake.sent[-1]["commits"][0]["diff"] and Fake.sent[-1]["commits"][0]["shared"]
