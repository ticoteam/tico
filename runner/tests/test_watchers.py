"""The runner runs a bot's watchers: on schedule, alone, with a timeout, as the bot, with its secrets kept out of what
it reports, and with its state put back when the hub did not take the report (docs/watchers.md)."""
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from clients import watchers as declared
from runner import watchers as W
from runner.service import Runner

SCRIPT = '''#!/usr/bin/env python3
import json, os, sys, time
state = os.path.join(os.environ["TICO_WATCHER_STATE"], "cursor.json")
seen = json.load(open(state))["n"] if os.path.exists(state) else 0
json.dump({"n": seen + 1}, open(state, "w"))
print("run", seen + 1, "key", os.environ.get("HQ_STAFF_KEY"), "hub-token", os.environ.get("HUB_TOKEN"), "cwd", os.getcwd())
mode = sys.argv[1] if len(sys.argv) > 1 else "quiet"
if mode == "event":
    print("tico-event " + json.dumps({"op": "task", "key": "k%d" % (seen + 1), "title": "T", "body": "leak: " + os.environ["HQ_STAFF_KEY"]}))
    print("tico-event not json")
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    sys.exit(3)
'''


class Client:
    def __init__(self):
        self.posts, self.down = [], False
        self.credentials = [{"id": "staff", "env": "HQ_STAFF_KEY", "kind": "api_key", "value": "staff-key-value-123456"}]

    def get(self, path, **query):
        assert (path, query) == ("runner-watcher-credentials", {"bot": "support"})
        return {"credentials": self.credentials}

    def post(self, path, body):
        if self.down:
            raise OSError("hub down")
        self.posts.append((path, body))
        return {}


class Rig(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.projects = Path(tmp.name)
        self.repo = self.projects / "emp-support"
        (self.repo / "software").mkdir(parents=True)
        (self.repo / "AGENT.md").write_text("# Support\n")
        script = self.repo / "software" / "hq-tickets"
        script.write_text(SCRIPT)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.projects / "secrets").mkdir()
        (self.projects / "secrets" / "support.env").write_text("HQ_STAFF_KEY=staff-key-value-123456\n")
        self.client, self.now = Client(), [0.0]
        runner = Runner.__new__(Runner)
        runner.config = {"url": "https://acme.test", "token": "t", "runner_id": "r1", "projects_dir": str(self.projects)}
        runner._names, runner.client, runner.restart_due = None, self.client, None
        runner.assignments_seen = [{"bot": "support", "runner_id": "r1", "state": "active", "config": {"runtime": "codex"}}]
        runner.vault_values, runner.vault_names, runner.vault_files = {}, {}, {}
        runner.state = SimpleNamespace(directory=self.projects)
        self.runner = runner
        self.declare("software/hq-tickets event")
        self.watchers = W.Watchers(runner, clock=lambda: self.now[0])
        self.addCleanup(self.watchers.stop)

    def declare(self, run, every="5m", **more):
        entry = {"name": "hq-tickets", "run": run, "every": every, **more}
        (self.repo / "employee.yaml").write_text("name: support\nwatchers:\n" + "".join(
            f"  - {k}: {json.dumps(v)}\n" if k == "name" else f"    {k}: {json.dumps(v)}\n" for k, v in entry.items()))
        return entry

    def tick(self, at=None):
        if at is not None:
            self.now[0] = at
        self.watchers.tick()

    def settle(self):
        end = time.time() + 15
        while self.watchers.running and time.time() < end:
            time.sleep(0.02)
        self.assertFalse(self.watchers.running, "the watcher did not finish")


class Watchers(Rig):
    def test_revoked_grants_and_legacy_files_do_not_reach_the_next_watcher(self):
        self.client.credentials = []
        self.declare("software/hq-tickets quiet")
        self.tick(0)
        self.settle()
        body = self.client.posts[0][1]
        self.assertEqual(body["exit"], 0)
        self.assertIn("key None hub-token None", body["output"])
        self.assertNotIn("staff-key-value", json.dumps(body))
        self.assertEqual(self.runner.vault_values, {})

    def test_granted_files_are_private_redacted_and_removed_after_the_watcher(self):
        self.client.credentials = [{"id": "file", "env": "FILE_KEY", "kind": "file", "value": "file-fixture-secret"}]
        script = self.repo / "software" / "file.py"
        script.write_text('import os, pathlib, stat\np = pathlib.Path(os.environ["FILE_KEY"])\n'
                          'assert stat.S_IMODE(p.stat().st_mode) == 0o600\nprint(p.read_text())\n')
        self.declare("software/file.py")
        self.tick(0)
        self.settle()
        body = self.client.posts[0][1]
        self.assertEqual((body["exit"], body["output"]), (0, "[redacted]"))
        self.assertEqual(list(self.projects.glob("tico-credential-*")), [])
        self.assertEqual(self.runner.vault_files, {})

    def test_it_runs_as_declared_and_posts_the_events_and_the_run(self):
        self.tick(0)
        self.settle()
        ((path, body),) = self.client.posts
        self.assertEqual(path, "runners/watchers")
        self.assertEqual((body["bot"], body["name"], body["exit"], body["timed_out"], body["every"]),
                         ("support", "hq-tickets", 0, False, 300))
        self.assertEqual(body["events"], [{"op": "task", "key": "k1", "title": "T", "body": "leak: [redacted]"}])
        # The log keeps the line that was not an event, with the secret and the hub token absent.
        self.assertIn("tico-event not json", body["output"])
        self.assertIn("key [redacted] hub-token None", body["output"])
        self.assertNotIn("staff-key-value", json.dumps(body))
        self.assertTrue((self.repo / ".state" / "hq-tickets" / "cursor.json").is_file())

    def test_a_run_that_is_still_going_when_the_next_is_due_is_not_started_again(self):
        self.declare("software/hq-tickets sleep", every="1m", timeout="1s")
        self.tick(0)
        self.tick(61)                                 # due, but the first is still sleeping
        self.assertEqual(len(self.watchers.running), 1)
        self.settle()
        self.assertEqual(len(self.client.posts), 1)

    def test_a_timeout_kills_it_and_says_so(self):
        self.declare("software/hq-tickets sleep", timeout="1s")
        started = time.time()
        self.tick(0)
        self.settle()
        self.assertLess(time.time() - started, 10)
        self.assertTrue(self.client.posts[0][1]["timed_out"])

    def test_the_state_goes_back_when_the_hub_does_not_take_the_report(self):
        self.client.down = True
        self.tick(0)
        self.settle()
        state = self.repo / ".state" / "hq-tickets"
        self.assertEqual(list(state.iterdir()), [])          # the first run's cursor is undone
        self.client.down = False
        self.tick(300)
        self.settle()
        self.assertEqual(json.loads((state / "cursor.json").read_text()), {"n": 1})
        self.assertEqual(self.client.posts[0][1]["events"][0]["key"], "k1")      # the same event, delivered now

    def test_only_active_bots_hosted_here_and_only_files_inside_the_repository(self):
        self.runner.assignments_seen[0]["state"] = "paused"
        self.tick(0)
        self.settle()
        self.assertEqual(self.client.posts, [])
        self.runner.assignments_seen[0].update(state="active", runner_id="r2")
        self.watchers.next_scan = 0
        self.tick(1)
        self.assertEqual(self.client.posts, [])
        self.runner.assignments_seen[0]["runner_id"] = "r1"
        for run in ("../outside", "/bin/echo hi", "software/missing"):
            self.declare(run)
            self.watchers.next_scan = 0
            self.tick(2)
            self.settle()
        self.assertEqual(self.client.posts, [])

class Declaration(unittest.TestCase):
    def test_what_is_refused(self):
        for bad in ({"name": "a", "run": "../x", "every": "5m"}, {"name": "a", "run": "/bin/sh", "every": "5m"},
                    {"name": "a", "run": "x", "every": "5m", "user": "root"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                declared.parse([bad])
        with self.assertRaises(ValueError):
            declared.parse([{"name": "a", "run": "x", "every": "5m"}] * 2)

if __name__ == "__main__":
    unittest.main()


def test_ordinary_words_and_addresses_are_not_masked():
    # A ticket that says "support" or names the watched repository must reach the task as written.
    from runner.watchers import secret_values, redact
    env = {"HQ_STAFF_KEY": "k" * 40, "TICO_HQ_URL": "https://updates.tico.team", "GH_SUPPORT_REPOS": "ticoteam/tico",
           "HUB_EMPLOYEE": "support"}
    values = secret_values(env)
    text = "checking the support path for ticoteam/tico at https://updates.tico.team with " + "k" * 40
    assert redact(text, values) == "checking the support path for ticoteam/tico at https://updates.tico.team with [redacted]"
