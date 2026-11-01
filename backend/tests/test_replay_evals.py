"""Behaviour replay (evals/replay/) without a model: a finished task splits into its opening state and its outcome,
a case nobody signed or that still holds a secret is refused, and a replay on a fake server is scored against the
original with the same scorers and reported as a win or a regression.

The live runner needs a scratch team with a real model and runs only at release preparation (docs/releasing.md).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixture)

REPLAY = Path(__file__).resolve().parents[2] / "evals" / "replay"
sys.path.insert(0, str(REPLAY))
import replay  # noqa: E402

_spec = importlib.util.spec_from_file_location("replay_run", REPLAY / "run.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)


def message(mid, actor, body, created, kind="say"):
    return {"id": mid, "from_actor": actor, "body": body, "created_at": created, "kind": kind}


def test_capture_splits_opening_state_from_outcome_and_needs_a_signature():
    detail = {"task": {"id": "t1", "title": "Fix the login page", "body": "It 500s.", "owner": "bot:dev",
                       "status": "done", "acceptance_criteria": []},
              "runs": [{"bot": "botops", "started_at": "2026-10-16T09:00:00Z", "finished_at": "2026-10-16T09:01:00Z"},
                       {"bot": "dev", "started_at": "2026-10-16T10:00:00Z", "finished_at": "2026-10-16T10:06:00Z"},
                       {"bot": "dev", "started_at": "2026-10-16T11:00:00Z", "finished_at": "2026-10-16T11:03:00Z"}],
              "events": [{"ts": "2026-10-16T10:20:00Z", "field": "title", "old": "Login page 500s", "new": "Fix the login page"}],
              "messages": [message("n", "keeper", "New task from human:ana", "2026-10-16T09:59:00Z", "notice"),
                           message("a", "human:ana", "Only the login page.", "2026-10-16T09:58:00Z"),
                           message("b", "bot:dev", "Which browser?", "2026-10-16T10:05:00Z"),
                           message("c", "human:ana", "Safari.", "2026-10-16T10:30:00Z"),
                           message("d", "bot:dev", "Fixed in PR 12.", "2026-10-16T11:02:00Z")],
              "comments": [message("a", "human:ana", "Only the login page.", "2026-10-16T09:58:00Z")],
              "children": [{"id": "q", "owner": "bot:dev", "status": "done", "title": "Which account?"},
                           {"id": "r", "owner": "bot:pr-reviewer", "status": "done", "title": "Review PR 12"}]}
    # A question card is owned by the bot that asked and waits on the person: one step for them; a review is not.
    files = {"files": [{"name": "trace.txt", "versions": [{"n": 1, "by": "human:ana", "created_at": "2026-10-16T09:57:00Z",
                                                           "text": "Traceback"}]},
                       {"name": "fix.diff", "versions": [{"n": 1, "by": "bot:dev", "created": "2026-10-16T11:01:00Z"}]}]}
    cards = [{"task": {"id": "q", "owner": "bot:dev", "waiting_on": "human:ana", "status": "done"}},
             {"task": {"id": "r", "owner": "bot:pr-reviewer", "status": "done"}}]
    case = replay.split(detail, files, {"content": "# Dev", "updated_at": "2026-10-15T00:00:00Z"}, card_details=cards)

    assert case["id"] == "fix-the-login-page-t1"         # the task id keeps two tasks with one title apart
    assert case["conversation"] == [{"from": "person", "text": "Only the login page."}]
    # The cut is this bot's first run, not BotOps' earlier one, and the title is the one the bot started with.
    assert case["task"]["title"] == "Login page 500s"
    assert case["files"] == [{"name": "trace.txt", "text": "Traceback"}]
    assert case["original"] | {"transcript": None} == {
        "status": "done", "transcript": None, "files": ["fix.diff"], "runs": 2, "minutes": 9.0, "person_steps": 2}
    assert case["original"]["transcript"] == [["bot", "Which browser?"], ["person", "Safari."], ["bot", "Fixed in PR 12."]]
    assert case["prompt"] == {"sha256": replay.sha("# Dev"), "updated": "2026-10-15T00:00:00Z", "exact": True}
    # A draft is never runnable: a person signs it, and a secret left in it refuses it.
    assert any("reviewed_by" in p for p in replay.problems(case))
    case["reviewed_by"] = "Ana"
    assert replay.problems(case) == []
    case["original"]["transcript"].append(["person", "my key is ghp_" + "a" * 30])
    assert any("secret" in p for p in replay.problems(case))
    # The shipped example is a signed, runnable case.
    assert [replay.problems(c) for c in replay.load(REPLAY / "cases")] == [[]]


class FakeTico:
    """Just enough of the API for run.py. The bot runs when it is handed the task, when a person comments after that,
    and when its question card is done, as `behave` says."""

    def __init__(self, behave):
        self.behave, self.tasks, self.calls = behave, {}, []

    def request(self, method, path, body=None, token=None):
        self.calls.append((method, path))
        if path == "me":
            return 200, {"actor": "human:owner"}
        if path.endswith("/instructions"):
            return 200, {"content": "# New prompt", "published": True}
        if method == "POST" and path == "tasks":
            self.tasks["r1"] = {"task": {"id": "r1", "version": 1, "status": "open", **body},
                                "messages": [], "comments": [], "runs": [], "files": [], "children": []}
            return 200, {"task": self.tasks["r1"]["task"]}
        if path.startswith("messages/") and path.endswith("/answer"):
            row = self.tasks["r1"]
            row["comments"].append(message("ans", "human:owner", body["text"], "2099-01-01T00:01:55Z", "answer")
                                   | {"in_reply_to": path.split("/")[1]})
            self.behave(self, row, "answered")
            return 200, {}
        tid = path.split("/")[1]
        if tid not in self.tasks:
            return 404, {}
        row = self.tasks[tid]
        handed = str(self.tasks.get("r1", {"task": {}})["task"].get("owner")).startswith("bot:")
        if path.endswith("/files"):
            if method == "POST":
                row["files"].append({"name": body["name"], "versions": [{"by": "human:owner"}]})
            return 200, {"files": row["files"]}
        if path.endswith("/comments"):
            row["comments"].append(message(f"m{len(row['comments'])}", "human:owner", body["text"],
                                           f"2099-01-01T00:{len(row['runs']):02}:55Z" if handed else "2099-01-01T00:00:01Z"))
            if handed:
                self.behave(self, row, "commented")
            return 200, {}
        if method == "POST":
            row["task"].update({k: v for k, v in body.items() if k != "version"})
            if body.get("owner", "").startswith("bot:"):
                self.behave(self, row, "assigned")
            if tid != "r1" and body.get("status") == "done":
                next(c for c in self.tasks["r1"]["children"] if c["id"] == tid)["status"] = "done"
                self.behave(self, self.tasks["r1"], "card done")
        return 200, row


def run_at(minute):
    return {"bot": "engineering-lead", "started_at": f"2099-01-01T00:{minute:02}:02Z", "finished_at": f"2099-01-01T00:{minute:02}:50Z"}


def bot_asks_then_finishes(fake, row, event):
    """Asks on the task, which leaves it waiting on nobody, then finishes once answered."""
    if event == "assigned":
        row["runs"].append(run_at(1))
        ask = message("a1", "bot:engineering-lead", "Which repository?", "2099-01-01T00:01:30Z", "ask")
        row["messages"].append(ask | {"to_actor": "human:owner"})
        row["task"].update(status="waiting", waiting_on=None)
    elif event == "answered":
        row["runs"].append(run_at(2))
        row["files"].append({"name": "engineering-summary.md", "versions": [{"by": "bot:engineering-lead"}]})
        row["messages"].append(message("b1", "bot:engineering-lead", "Attached: 5 shipped, nothing broke.", "2099-01-01T00:02:30Z"))
        row["task"]["status"] = "done"


def bot_gives_up(fake, row, event):
    row["runs"] += [run_at(1), run_at(2), run_at(3), run_at(4)]
    row["messages"].append(message("b1", "bot:engineering-lead", "I could not read the runners (see harness logs).",
                                   "2099-01-01T00:02:00Z"))
    row["task"]["status"] = "waiting"


@pytest.mark.parametrize("behave,verdict", [(bot_asks_then_finishes, "win"), (bot_gives_up, "regression")])
def test_replay_scores_candidate_against_original(behave, verdict, monkeypatch, tmp_path):
    server = FakeTico(behave)
    monkeypatch.setattr(live, "Live", lambda url: server)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    report = tmp_path / "replay.md"
    code = live.main(["--url", "http://fake", "--token", "t", "--cases", str(REPLAY / "cases"),
                      "--quiet", "0", "--timeout", "5", "--report", str(report)])

    # The opening state was built before the bot was handed the task; the hand-over is what wakes it.
    posts = [c for c in server.calls if c[0] == "POST"]
    assert posts[:4] == [("POST", "tasks"), ("POST", "tasks/r1/files"), ("POST", "tasks/r1/comments"), ("POST", "tasks/r1")]
    assert ("POST", "tasks/r1/run-now") not in posts
    text = report.read_text()
    assert f"1 case: {int(verdict == 'win')} wins, {int(verdict == 'regression')} regressions" in text
    assert code == (0 if verdict == "win" else 1)
    if verdict == "win":
        # The simulated person answered the question with the case's first answer.
        assert server.tasks["r1"]["comments"][-1]["body"] == "Use the main repository only."
        assert "runs 4→2" in text
    else:
        assert "## Regressions" in text and "no longer done" in text


def test_replay_refuses_the_live_team(monkeypatch, tmp_path, capsys):
    server = FakeTico(bot_gives_up)
    server.tasks["00000000-0000-0000-0000-000000000000"] = {"task": {"id": "00000000-0000-0000-0000-000000000000"}}
    monkeypatch.setattr(live, "Live", lambda url: server)
    assert live.main(["--url", "http://fake", "--token", "t", "--cases", str(REPLAY / "cases")]) == 1
    assert "scratch team" in capsys.readouterr().out and ("POST", "tasks") not in server.calls


class Server:
    """The test client as run.py's server."""

    def __init__(self, api):
        self.api = api

    def request(self, method, path, body=None, token=None):
        r = self.api.request(method, "/api/v2/" + path.lstrip("/"), json=body, headers=headers(token))
        return r.status_code, (r.json() if r.content else {})


def test_replay_answers_the_bots_questions_the_way_the_server_asks_them(api):
    """On the real server: a question on the task leaves it waiting on nobody, and a question card is owned by the
    bot that asked. The simulated person answers both, and the server counts both as answered."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    server, case = Server(api), {"bot": "ops", "task": {"title": "Summarize this week", "body": "One page."}}
    tid = live.prepare(server, "ana-test", "human:ana", case)
    live.hand_over(server, "ana-test", tid, "ops")
    attempt = claim(api, machine, "ops")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, machine["token"])

    post(api, f"tasks/{tid}/ask", {"text": "Which repository?"}, token=attempt["token"])
    now = live.outcome(server, "ana-test", tid, "ops")
    assert now["status"] == "waiting" and not now["waiting_on"] and len(now["open_questions"]) == 1
    assert live.answer(server, "ana-test", "human:ana", tid, now, "The main one.")
    assert live.outcome(server, "ana-test", tid, "ops")["open_questions"] == []
    assert api.get(f"/api/v2/tasks/{tid}", headers=headers()).json()["task"]["open_asks"] == 0

    card = api.post("/api/v2/task-questions", headers=headers(attempt["token"]), json={
        "question": "Which week?", "on": "ana", "parent": tid, "options": [{"label": "This week", "then": "Go with This week"}, {"label": "Last week", "then": "Go with Last week"}]})
    assert card.status_code == 200, card.text
    assert card.json()["task"]["owner"] == "bot:ops"
    now = live.outcome(server, "ana-test", tid, "ops")
    assert [q["task"] for q in now["open_questions"]] == [card.json()["task"]["id"]] and now["person_steps"] == 2
    assert live.answer(server, "ana-test", "human:ana", tid, now, "this week")
    after = live.outcome(server, "ana-test", tid, "ops")
    assert after["open_questions"] == [] and after["person_steps"] == 2
    answered = api.get(f"/api/v2/tasks/{card.json()['task']['id']}/answers", headers=headers()).json()["answers"]
    assert answered and "This week" in str(answered)
