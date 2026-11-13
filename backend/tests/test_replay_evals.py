"""Behaviour replay (evals/replay/) without a model: a finished task splits into its opening state and its outcome,
a case nobody signed or that still holds a secret is refused, and a replay on a fake server is scored against the
original with the same scorers and reported as a win or a regression.

The live runner needs a scratch team with a real model and runs only at release preparation (docs/releasing.md).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from backend.tests.helpers.api import assign, claim, headers, post, ready, runner  # noqa: F401  (fixture)

REPLAY = Path(__file__).resolve().parents[2] / "evals" / "replay"
sys.path.insert(0, str(REPLAY))
import replay  # noqa: E402

_spec = importlib.util.spec_from_file_location("replay_run", REPLAY / "run.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)


def message(mid, actor, body, created, kind="say"):
    return {"id": mid, "sender_id": actor, "body": body, "created_at": created, "kind": kind}


def test_capture_splits_opening_state_from_outcome_and_needs_a_signature():
    detail = {
        "task": {
            "id": "t1",
            "title": "Fix the login page",
            "body": "It 500s.",
            "owner_id": "bot:dev",
            "status": "done",
            "acceptance_criteria": [],
            # The bot's ask to Ana on the task, answered: one step for her.
            "to_leave": [
                {"requirement_id": "o", "kind": "owner_done", "who": "bot:dev", "asked_by": None, "met": True},
                {
                    "requirement_id": "k",
                    "kind": "answer",
                    "who": "human:ana",
                    "asked_by": "bot:dev",
                    "choices": ["Chrome", "Safari"],
                    "met": True,
                },
            ],
        },
        "runs": [
            {"bot": "botops", "started_at": "2026-10-16T09:00:00Z", "finished_at": "2026-10-16T09:01:00Z"},
            {"bot": "dev", "started_at": "2026-10-16T10:00:00Z", "finished_at": "2026-10-16T10:06:00Z"},
            {"bot": "dev", "started_at": "2026-10-16T11:00:00Z", "finished_at": "2026-10-16T11:03:00Z"},
        ],
        "events": [
            {
                "created_at": "2026-10-16T10:20:00Z",
                "field": "title",
                "old": "Login page 500s",
                "new": "Fix the login page",
            }
        ],
        "messages": [
            message("n", "tico", "New task from human:ana", "2026-10-16T09:59:00Z", "notice"),
            message("a", "human:ana", "Only the login page.", "2026-10-16T09:58:00Z"),
            message("b", "bot:dev", "Which browser?", "2026-10-16T10:05:00Z"),
            message("c", "human:ana", "Safari.", "2026-10-16T10:30:00Z"),
            message("d", "bot:dev", "Fixed in PR 12.", "2026-10-16T11:02:00Z"),
        ],
        "comments": [message("a", "human:ana", "Only the login page.", "2026-10-16T09:58:00Z")],
        "children": [
            {"id": "q", "owner_id": "human:ana", "status": "done", "title": "Check the account"},
            {"id": "r", "owner_id": "bot:reviewer", "status": "done", "title": "Review PR 12"},
        ],
    }
    # A task filed for a person is one step for them; one filed for another bot is not.
    files = {
        "files": [
            {
                "name": "trace.txt",
                "versions": [{"n": 1, "by": "human:ana", "created_at": "2026-10-16T09:57:00Z", "text": "Traceback"}],
            },
            {"name": "fix.diff", "versions": [{"n": 1, "by": "bot:dev", "created_at": "2026-10-16T11:01:00Z"}]},
        ]
    }
    children = [
        {"task": {"id": "q", "owner_id": "human:ana", "status": "done"}},
        {"task": {"id": "r", "owner_id": "bot:reviewer", "status": "done"}},
    ]
    case = replay.split(
        detail, files, {"content": "# Dev", "updated_at": "2026-10-15T00:00:00Z"}, child_details=children
    )

    assert case["id"] == "fix-the-login-page-t1"  # the task id keeps two tasks with one title apart
    assert case["conversation"] == [{"from": "person", "text": "Only the login page."}]
    # The cut is this bot's first run, not BotOps' earlier one, and the title is the one the bot started with.
    assert case["task"]["title"] == "Login page 500s"
    assert case["files"] == [{"name": "trace.txt", "text": "Traceback"}]
    assert case["original"] | {"transcript": None} == {
        "status": "done",
        "transcript": None,
        "files": ["fix.diff"],
        "runs": 2,
        "minutes": 9.0,
        "person_steps": 3,  # "Safari.", the bot's ask on the task and the task filed for Ana
    }
    assert case["done"] == [{"status": ["done"]}]
    assert case["original"]["transcript"] == [
        ["bot", "Which browser?"],
        ["person", "Safari."],
        ["bot", "Fixed in PR 12."],
    ]
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
    and when its ask is answered, as `behave` says."""

    def __init__(self, behave):
        self.behave, self.tasks, self.calls = behave, {}, []

    def request(self, method, path, body=None, token=None):
        self.calls.append((method, path))
        if path == "me":
            return 200, {"actor": "human:owner"}
        if path.endswith("/instructions"):
            return 200, {"content": "# New prompt", "published": True}
        if method == "POST" and path == "tasks":
            self.tasks["r1"] = {
                "task": {"id": "r1", "version": 1, "status": "open", "to_leave": [], "turn": None, **body},
                "messages": [],
                "comments": [],
                "runs": [],
                "files": [],
                "children": [],
            }
            self.tasks["r1"]["task"]["owner_id"] = body["owner"]
            return 200, {"task": self.tasks["r1"]["task"]}
        tid = path.split("/")[1]
        if tid not in self.tasks:
            return 404, {}
        row = self.tasks[tid]
        if path.endswith("/files"):
            if method == "POST":
                row["files"].append({"name": body["name"], "versions": [{"by": "human:owner"}]})
            return 200, {"files": row["files"]}
        if path.endswith("/comments"):
            handed = str(row["task"].get("owner_id")).startswith("bot:")
            row["comments"].append(
                message(
                    f"m{len(row['comments'])}",
                    "human:owner",
                    body["text"],
                    f"2099-01-01T00:{len(row['runs']):02}:55Z" if handed else "2099-01-01T00:00:01Z",
                )
            )
            if handed:
                self.behave(self, row, "commented")
            return 200, {}
        if path.endswith("/owner"):
            row["task"]["owner_id"] = body["owner"]
            self.behave(self, row, "assigned")
            return 200, {}
        if path.endswith("/answer"):
            asked = next(r for r in row["task"]["to_leave"] if r["requirement_id"] == path.split("/")[3])
            asked.update(met=True, answer=body["value"])
            row["task"]["turn"] = None
            self.behave(self, row, "answered")
            return 200, {}
        return 200, row


def run_at(minute):
    return {
        "bot": "engineering-manager",
        "started_at": f"2099-01-01T00:{minute:02}:02Z",
        "finished_at": f"2099-01-01T00:{minute:02}:50Z",
    }


def bot_asks_then_finishes(fake, row, event):
    """Asks the owner on the task, which makes it their turn, then finishes once answered."""
    if event == "assigned":
        row["runs"].append(run_at(1))
        row["task"]["to_leave"].append(
            {
                "requirement_id": "a1",
                "kind": "answer",
                "who": "human:owner",
                "asked_by": "bot:engineering-manager",
                "choices": None,
                "met": False,
            }
        )
        row["task"]["turn"] = {"state": "person", "who": "human:owner"}
    elif event == "answered":
        row["runs"].append(run_at(2))
        row["files"].append({"name": "engineering-summary.md", "versions": [{"by": "bot:engineering-manager"}]})
        row["messages"].append(
            message("b1", "bot:engineering-manager", "Attached: 5 shipped, nothing broke.", "2099-01-01T00:02:30Z")
        )
        row["task"]["status"] = "done"


def bot_gives_up(fake, row, event):
    row["runs"] += [run_at(1), run_at(2), run_at(3), run_at(4)]
    row["messages"].append(
        message(
            "b1",
            "bot:engineering-manager",
            "I could not read the runners (see harness logs).",
            "2099-01-01T00:02:00Z",
        )
    )
    row["task"]["status"] = "closed"


@pytest.mark.parametrize("behave,verdict", [(bot_asks_then_finishes, "win"), (bot_gives_up, "regression")])
def test_replay_scores_candidate_against_original(behave, verdict, monkeypatch, tmp_path):
    server = FakeTico(behave)
    monkeypatch.setattr(live, "Live", lambda url: server)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    report = tmp_path / "replay.md"
    code = live.main(
        [
            "--url",
            "http://fake",
            "--token",
            "t",
            "--cases",
            str(REPLAY / "cases"),
            "--quiet",
            "0",
            "--timeout",
            "5",
            "--report",
            str(report),
        ]
    )

    # The opening state was built before the bot was handed the task; the hand-over is what wakes it.
    posts = [c for c in server.calls if c[0] == "POST"]
    assert posts[:4] == [
        ("POST", "tasks"),
        ("POST", "tasks/r1/files"),
        ("POST", "tasks/r1/comments"),
        ("POST", "tasks/r1/owner"),
    ]
    assert ("POST", "tasks/r1/run-now") not in posts
    text = report.read_text()
    assert f"1 case: {int(verdict == 'win')} wins, {int(verdict == 'regression')} regressions" in text
    assert code == (0 if verdict == "win" else 1)
    if verdict == "win":
        # The simulated person answered the bot's ask with the case's first answer, through the ask.
        assert server.tasks["r1"]["task"]["to_leave"][0]["answer"] == "Use the main repository only."
        assert ("POST", "tasks/r1/asks/a1/answer") in posts
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


def test_replay_answers_the_bots_asks_the_way_the_server_asks_them(api):
    """On the real server: the bot asks Ana on the task. The simulated person answers the ask with the choice the
    case's words name, and the server counts it as answered."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    server, case = Server(api), {"bot": "ops", "task": {"title": "Summarize this week", "body": "One page."}}
    tid = live.prepare(server, "ana-test", "human:ana", case)
    live.hand_over(server, "ana-test", tid, "ops")
    attempt = claim(api, machine, "ops")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, machine["token"])

    version = api.get(f"/api/v2/tasks/{tid}", headers=headers()).json()["task"]["version"]
    asked = api.post(
        f"/api/v2/tasks/{tid}/asks",
        headers=headers(attempt["token"]),
        json={
            "to": "ana",
            "line": "Which week?",
            "choices": ["This week", "Last week"],
            "why": "It decides what ships next.",
            "details": ["The newest numbers.", "The full week."],
            "recommend": "This week",
            "version": version,
        },
    )
    assert asked.status_code == 200, asked.text
    rid = asked.json()["requirement_id"]
    now = live.outcome(server, "ana-test", tid, "ops")
    assert [q["id"] for q in now["open_questions"]] == [rid] and now["person_steps"] == 1
    assert now["turn"] == "human:ana" and replay.settled(now)
    assert live.answer(server, "ana-test", "human:ana", tid, now, "this week")
    after = live.outcome(server, "ana-test", tid, "ops")
    assert after["open_questions"] == [] and after["person_steps"] == 1
    answered = [
        r
        for r in api.get(f"/api/v2/tasks/{tid}", headers=headers()).json()["task"]["to_leave"]
        if r["requirement_id"] == rid
    ]
    assert answered and answered[0]["met"] and "This week" in str(answered[0]["answer"])
