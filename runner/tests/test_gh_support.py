"""The Support Agent's `gh-support` watcher against a mocked GitHub: a new issue is a task, a repeat poll is nothing, a
comment from outside is a wake, a close is an end, and a quiet poll costs one or two conditional calls."""
import hashlib
import importlib.machinery
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "templates/catalog/support/software/gh-support"
loader = importlib.machinery.SourceFileLoader("gh_support", str(SCRIPT))
gh_support = importlib.util.module_from_spec(importlib.util.spec_from_loader("gh_support", loader))
loader.exec_module(gh_support)

START = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def issue(number, title="The board is empty", state="open", updated="2026-10-01T12:05:00Z", user="carol", assoc="NONE", **more):
    return {"number": number, "title": title, "state": state, "updated_at": updated, "body": "Steps: click the board.\n<b>hi</b>",
            "html_url": f"https://github.com/acme/app/issues/{number}", "user": {"login": user, "type": "User"},
            "author_association": assoc, "labels": [{"name": "bug"}], **more}


def comment(cid, number, body="Same here", created="2026-10-01T12:10:00Z", user="dave", assoc="NONE", kind="User"):
    return {"id": cid, "created_at": created, "updated_at": created, "body": body, "user": {"login": user, "type": kind},
            "author_association": assoc, "issue_url": f"https://api.github.com/repos/acme/app/issues/{number}",
            "html_url": f"https://github.com/acme/app/issues/{number}#issuecomment-{cid}"}


class FakeGitHub:
    def __init__(self):
        self.issues, self.comments, self.nodes, self.calls, self.limited = [], [], [], [], False

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url.replace("https://api.github.com", ""), "Authorization" in headers))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if self.limited:
            return 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}, b"{}"
        if parsed.path == "/graphql":
            return 200, {}, json.dumps({"data": {"repository": {"discussions": {"nodes": self.nodes}}}}).encode()
        if parsed.path.endswith("/issues"):
            data = [i for i in self.issues if i["updated_at"] >= query["since"][0]]
        elif parsed.path.endswith("/issues/comments"):
            data = [c for c in self.comments if c["updated_at"] >= query["since"][0]]
        else:
            number = int(parsed.path.rsplit("/", 1)[-1])
            return 200, {}, json.dumps(next(i for i in self.issues if i["number"] == number)).encode()
        raw = json.dumps(data).encode()
        etag = '"%s"' % hashlib.md5(raw).hexdigest()
        if headers.get("If-None-Match") == etag:
            return 304, {"etag": etag}, b""
        return 200, {"etag": etag}, raw


@pytest.fixture
def rig(tmp_path):
    class Rig:
        github, events, lines, env = FakeGitHub(), [], [], {"TICO_WATCHER_STATE": str(tmp_path / "state")}
        root = tmp_path / "repo"

        def __init__(self):
            (self.root / "config").mkdir(parents=True)
            (self.root / "config" / "github.yaml").write_text("repos:\n  - acme/app\nmaintainers:\n  - erin  # the team\nbackfill_days: 0\n")

        def watch(self, **env):
            self.events.clear()
            code = gh_support.main(["watch"], {**self.env, **env}, self.root, self.github, self.lines.append, self.events.append,
                                   now=START)
            assert code == 0, self.lines
            return list(self.events)
    return Rig()


def test_a_new_issue_is_one_task_and_a_repeat_poll_is_nothing(rig):
    assert rig.watch() == []                                         # the first run only marks "from now on"
    rig.github.issues.append(issue(7))
    (event,) = rig.watch()
    assert event["op"] == "task" and event["key"] == "gh:https://github.com/acme/app/issues/7"
    assert event["title"] == "GitHub issue: acme/app#7 The board is empty"
    assert "> Steps: click the board.\n> <b>hi</b>" in event["body"] and "untrusted data" in event["body"]
    assert "labels: bug" in event["body"] and "Link: https://github.com/acme/app/issues/7" in event["body"]
    assert rig.watch() == []


def test_a_comment_from_outside_wakes_the_bot_and_the_team_and_bots_do_not(rig):
    rig.watch()
    rig.github.issues.append(issue(7))
    rig.watch()
    rig.github.comments += [comment(1, 7, "Same here"), comment(2, 7, "Fixed in main", user="frank", assoc="MEMBER"),
                            comment(3, 7, "Thanks", user="erin"), comment(4, 7, "ci", user="ci-bot", kind="Bot"),
                            comment(5, 7, "Me too", created="2026-10-01T12:11:00Z")]
    events = rig.watch()
    assert [(e["op"], e["ref"]) for e in events] == [("comment", "c:1"), ("comment", "c:5")]
    assert events[0]["key"] == "gh:https://github.com/acme/app/issues/7" and "> Same here" in events[0]["text"]
    assert events[0]["title"].startswith("GitHub issue: acme/app#7") and "acme/app#7" in events[0]["text"]
    assert rig.watch() == []


def test_a_close_is_an_end_and_a_team_members_issue_is_not_support(rig):
    rig.watch()
    rig.github.issues += [issue(7), issue(8, user="frank", assoc="MEMBER")]
    (event,) = rig.watch()
    assert event["key"].endswith("/issues/7")
    rig.github.issues[0] = issue(7, state="closed", updated="2026-10-01T12:20:00Z")
    (done,) = rig.watch()
    assert done == {"op": "done", "key": "gh:https://github.com/acme/app/issues/7", "note": "acme/app#7 was closed on GitHub."}
    rig.github.issues[0] = issue(7, state="closed", updated="2026-10-01T12:30:00Z")
    assert rig.watch() == []


def test_pull_requests_are_ignored_and_a_closed_thread_that_someone_writes_on_again_is_news(rig):
    rig.watch()
    rig.github.issues += [issue(9, pull_request={"url": "x"}), issue(10, state="closed")]
    assert rig.watch() == []                                                 # a PR, and an issue that closed unseen
    rig.github.comments += [comment(1, 9, "Nice PR"), comment(2, 10, "Late question")]
    (event,) = rig.watch()
    assert event["ref"] == "c:2" and event["key"].endswith("/issues/10")

