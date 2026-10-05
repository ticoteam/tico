"""The runner's loop behaviour (runner/service.py, runner/outage.py): quiet outage logging with
backoff, lease renewal that survives a cloud deploy, usage-limit completions, and the push of a
bot's commits after a completed turn. A `FakeHost` answers turns; `FakeClient` stands in for
the cloud and can be scripted to fail renewals."""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest

import pytest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from clients.tico import APIError
from runner import service
from runner.hosts.fake import FakeHost
from runner.outage import Outage, describe, span
from runner.service import Runner
from runner.state import BOT_THREAD

CLOUDFLARE = APIError("http_error", "HTTP 530: Error 1033: Cloudflare Tunnel error", 530, True)
GONE = APIError("conflict", "Attempt is no longer leased to this runner", 409, False)


class FakeClient:
    def __init__(self, renew_error=None):
        self.posts, self.renew_error, self.renewals = [], renew_error, 0

    def post(self, path, body=None, key=None):
        self.posts.append((path, body))
        if path == "jobs/claim":
            return {"attempt": None}
        if path.endswith("/renew"):
            self.renewals += 1
            if self.renew_error:
                raise self.renew_error
            return {"lease_seconds": 90}
        if path.endswith("/events"):
            return {"ack_seq": body["events"][-1]["seq"]}
        if path.endswith("/inputs"):
            return {"messages": []}
        return {}

    def get(self, path, **query):
        return {}

    def completion(self):
        return next(body for path, body in self.posts if path.endswith("/complete"))


def attempt(aid="att-1", bot="coo", lease_seconds=90):
    return {"id": aid, "bot": bot, "token": "turn-token", "lease_seconds": lease_seconds,
            "config": {"runtime": "codex", "max_run_minutes": 1},
            "conversation": {"id": "conv-1", "scope": "shared", "kind": "chat"},
            "message": {"id": "msg-1", "body": "hello", "from_actor": "human:ana"}, "history": []}


class Execution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "emp-coo").mkdir()
        self.host = FakeHost(replies=["done"])
        self.config = {"url": "https://runner.example", "token": "machine", "projects_dir": str(root), "capacity": 1}

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, client, push=None):
        runner = Runner(self.config, Path(self.tmp.name) / "state", host_factory=lambda attempt, env: self.host,
                        client=client, push=push or (lambda path, env=None: (0, "")))
        runner.renew_interval = 0.05
        return runner

    def test_an_input_another_run_took_over_is_not_acknowledged_again(self):
        # A run that lapsed and was restored may find its input moved to a later run: the ack is a 404.
        message = {"id": "msg-2", "kind": "say", "from_actor": "human:ana", "body": "One more thing"}
        for error, settled in ((APIError("not_found", "This input is not assigned to this execution", 404), True),
                               (GONE, False)):
            client = FakeClient()
            post = client.post

            def scripted(path, body=None, key=None, post=post, error=error):
                if path.endswith("/inputs"):
                    post(path, body, key)
                    return {"attempt_id": "att-1", "messages": [message]}
                if path.endswith("/ack"):
                    post(path, body, key)
                    raise error
                return post(path, body, key)
            client.post = scripted
            runner = self.runner(client)
            steered = []
            self.host.steer = lambda thread, turn, text: steered.append(text)
            if settled:
                runner.receive_inputs("att-1", self.host, "thread", "turn")
                runner.receive_inputs("att-1", self.host, "thread", "turn")
                self.assertEqual(len(steered), 1, "applied once, never steered again")
                self.assertEqual(sum(path.endswith("/ack") for path, _ in client.posts), 2)
            else:
                with self.assertRaises(APIError):
                    runner.receive_inputs("att-1", self.host, "thread", "turn")

    def test_a_refused_key_is_held_until_a_credential_changes_or_the_recheck(self):
        runner = self.runner(FakeClient())
        (Path(self.tmp.name) / "secrets").mkdir()
        runner.reject("codex", "unexpected status 401 Unauthorized: Incorrect API key provided: sk-abcdef123456")
        held = runner.rejection("codex")
        self.assertTrue(held and "sk-abc" not in held["reason"])
        self.assertEqual(runner.last_heartbeat, float("-inf"), "the server hears of it at once")
        with mock.patch.object(Runner, "runtime_readiness", return_value={
                "installed": True, "authenticated": "ready", "version": "", "models": [], "controls": [], "detail": ""}):
            row = runner.runtime_report([])["codex"]
            self.assertEqual((row["authenticated"], row["rejected_at"]), ("rejected", held["at"]))
        team = Path(self.tmp.name) / "secrets" / service.TEAM_KEYS_FILE
        team.write_text("OPENAI_API_KEY=synthetic-team-key\n")
        runner.reject("codex", "Unauthorized")
        with mock.patch.object(Runner, "runtime_readiness", return_value={"installed": True}):
            row = runner.runtime_report([])["codex"]
            self.assertEqual(row["credential_source"], "credentials")
            self.assertNotIn("synthetic-team-key", json.dumps(row))
        (Path(self.tmp.name) / "secrets" / "_shared.env").write_text("OPENAI_API_KEY=new\n")
        self.assertIsNone(runner.rejection("codex"), "a changed secrets file lifts it")
        runner.reject("codex", "Incorrect API key")
        with mock.patch.object(Runner, "runtime_readiness", return_value={"installed": True}):
            self.assertEqual(runner.runtime_report([])["codex"]["credential_source"], "computer")
        with mock.patch("runner.service.time.monotonic", return_value=time.monotonic() + service.REJECT_RECHECK_S + 1):
            self.assertIsNone(runner.rejection("codex"), "after a few minutes one turn may find out again")

    def test_credential_source_waits_for_server_support_and_survives_rollback(self):
        import copy

        client = FakeClient()
        client.get = lambda path, **kw: {"bots": {}} if path == "runner-credential-grants" else []
        runner = self.runner(client)
        runner.tools = mock.Mock()
        runner.follower = mock.Mock(following=True)
        runner.follower.fields.return_value = {}
        runner.migrate_credentials = lambda rows: None
        runner.enabled_providers = lambda: []
        runner.runtime_report = lambda rows: {"codex": {"installed": True, "authenticated": "rejected",
                                                       "credential_source": "credentials"}}
        runner.preflight = lambda *args: []
        runner.changed_agent_instructions = lambda rows: ({}, {})
        runner.mail_agent_instructions = lambda rows: {}
        runner.readiness = lambda rows, checks, runtimes: {"schema_version": 1, "runtimes": runtimes,
                                                          "harnesses": {"codex": {"installed": True}}, "bots": {}}
        runner.recover_output = lambda: None
        sent, supported = [], True

        def heartbeat(path, body):
            sent.append(copy.deepcopy(body))
            if not supported and "credential_source" in body["readiness"]["runtimes"]["codex"]:
                raise APIError("validation", "Unknown field", 422)
            return {"runtime_credential_source": True} if supported else {}

        client.post = heartbeat
        runner.maintain()
        assert "credential_source" not in sent[-1]["readiness"]["runtimes"]["codex"]
        runner.maintain()
        assert sent[-1]["readiness"]["runtimes"]["codex"]["credential_source"] == "credentials"
        supported = False
        runner.maintain()
        assert "credential_source" not in sent[-1]["readiness"]["runtimes"]["codex"]
        assert "harnesses" in sent[-1]["readiness"]
        assert runner._harness_after == 0 and not runner._reports_credential_source
        runner.maintain()
        assert "credential_source" not in sent[-1]["readiness"]["runtimes"]["codex"]
        supported = True
        runner.maintain()
        assert "credential_source" not in sent[-1]["readiness"]["runtimes"]["codex"]
        runner.maintain()
        assert sent[-1]["readiness"]["runtimes"]["codex"]["credential_source"] == "credentials"

    def test_a_due_self_update_exits_when_idle_and_drains_after_a_while(self):
        # The runner updates itself instead of asking a person to pull and restart.
        client = FakeClient()
        runner = self.runner(client)
        runner.capacity = 2
        runner.last_heartbeat = time.monotonic()
        busy = Future()
        runner.active["att-1"] = busy
        runner.restart_due = time.monotonic()
        with mock.patch("runner.service.log"):
            runner.tick()
        self.assertFalse(runner.stop.is_set(), "a running turn is never cut off")
        self.assertTrue(any(path == "jobs/claim" for path, _ in client.posts), "claims go on at first")
        # An automatic update never stops the other bots on this Mac; it waits for
        # a moment with nothing running, however long that takes.
        client.posts.clear()
        runner.next_claim = 0           # the idle back-off is not what this test is about
        runner.restart_due = time.monotonic() - service.SELF_UPDATE_DRAIN_S
        with mock.patch("runner.service.log"):
            runner.tick()
        self.assertTrue(any(path == "jobs/claim" for path, _ in client.posts), "an automatic update keeps claiming")
        self.assertFalse(runner.stop.is_set())
        # Only a restart a person asked for drains after a while.
        client.posts.clear()
        runner.restart_forced = True
        runner.next_claim = 0
        with mock.patch("runner.service.log"):
            runner.tick()
        self.assertFalse(any(path == "jobs/claim" for path, _ in client.posts), "a requested restart stops claiming after a while")
        self.assertFalse(runner.stop.is_set())
        busy.set_result(None)
        with mock.patch("runner.service.log"):
            runner.tick()
        self.assertTrue(runner.stop.is_set(), "quiet: exit so the supervisor starts the new code")
        self.assertFalse(any(path == "jobs/claim" for path, _ in client.posts))

    def test_a_worker_exception_does_not_kill_claiming_and_is_recovered(self):
        client = FakeClient()
        runner = self.runner(client)
        runner.state.record(attempt())
        failed = Future()
        failed.set_exception(RuntimeError("worker crashed"))
        runner.active["att-1"] = failed
        runner.last_heartbeat = time.monotonic()
        with mock.patch("runner.service.log") as log:
            runner.tick()
        self.assertEqual(runner.active, {})
        self.assertTrue(any(path == "jobs/claim" for path, _ in client.posts))
        log.assert_called_once()
        runner.recover_output()
        self.assertEqual(client.completion()["outcome"], "interrupted")
        self.assertEqual(runner.state.unfinished(), [])

    def test_a_turn_outlives_a_cloud_outage_longer_than_its_lease(self):
        client = FakeClient(renew_error=CLOUDFLARE)
        runner = self.runner(client)
        self.host.hold_next_turn()
        # A fake clock stands still until the turn is running, so a slow, loaded machine cannot
        # spend the 1 s lease before the turn starts; then it jumps far past the lease deadline.
        start, skew = time.monotonic(), [0.0]
        worker = threading.Thread(target=runner.execute, args=(attempt(lease_seconds=16),))

        def wait_for(condition, what):
            give_up = time.monotonic() + 60                      # real time, only a bound for a hang
            while not condition() and time.monotonic() < give_up:
                time.sleep(0.01)
            self.assertTrue(condition(), what)

        with mock.patch("runner.outage.log"), mock.patch("runner.service.time.monotonic", lambda: start + skew[0]):
            worker.start()
            wait_for(lambda: self.host.turn_of, "the turn starts")
            skew[0] = 30                                         # the lease deadline (1 s) is long gone, the 60 s run limit is not
            seen = client.renewals
            wait_for(lambda: client.renewals >= seen + 5, "renewals keep failing and being retried")
            self.assertTrue(worker.is_alive())
            self.host.complete(next(iter(self.host.turn_of)), "finished anyway")
            worker.join(timeout=60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.host.interrupts, [])
        self.assertEqual(client.completion()["outcome"], "completed")
        self.assertEqual(client.completion()["text"], "finished anyway")

    def test_a_definite_refusal_to_renew_interrupts_the_turn(self):
        client = FakeClient(renew_error=GONE)
        runner = self.runner(client)
        self.host.hold_next_turn()
        post = client.post

        def renew_after_turn_starts(path, body=None, key=None):
            # Runtime preparation can exceed the 50 ms renewal interval on CI.
            # Exercise loss during an active turn, not the separate pre-start fence.
            if path.endswith("/renew") and not self.host.turn_of:
                return {"lease_seconds": 90}
            return post(path, body, key)

        with mock.patch.object(client, "post", side_effect=renew_after_turn_starts):
            runner.execute(attempt())
        self.assertEqual(client.completion()["outcome"], "interrupted")
        self.assertTrue(self.host.interrupts)


def git(path, *args, env=None):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env)


class Pushing(unittest.TestCase):
    """A bare `origin` and a clone with a local commit stand in for GitHub and the emp-* checkout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        self.origin, self.repo = root / "origin.git", root / "emp-coo"
        git(root, "init", "-q", "--bare", str(self.origin), env=self.env)
        # Ubuntu's git still names the unborn branch master; the checkouts below expect main.
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main", env=self.env)
        git(root, "clone", "-q", str(self.origin), str(self.repo), env=self.env)
        git(self.repo, "checkout", "-q", "-b", "main", env=self.env)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "init", env=self.env)
        git(self.repo, "push", "-q", "-u", "origin", "main", env=self.env)
        self.commit("memory")
        self.host = FakeHost(replies=["done"])
        self.config = {"url": "https://runner.example", "token": "machine", "projects_dir": str(root),
                       "capacity": 1, "repos": {"coo": str(self.repo)}}
        self.client = FakeClient()
        self.runner = Runner(self.config, root / "state", host_factory=lambda attempt, env: self.host, client=self.client)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, name, path=None):
        path = path or self.repo
        (path / name).write_text(name)
        git(path, "add", name, env=self.env)
        git(path, "commit", "-q", "-m", name, env=self.env)

    def head(self, path, ref="HEAD"):
        return git(path, "rev-parse", ref, env=self.env).stdout.strip()

    def test_a_diverged_origin_is_left_for_a_person_and_said_once(self):
        other = Path(self.tmp.name) / "other"
        git(self.tmp.name, "clone", "-q", "-b", "main", str(self.origin), str(other), env=self.env)
        self.commit("elsewhere", other)
        git(other, "push", "-q", "origin", "main", env=self.env)
        before = self.head(self.origin, "main")
        with mock.patch.dict(os.environ, self.env), mock.patch("runner.service.log") as log:
            self.runner.execute(attempt())
            self.runner.push(self.repo)                          # the hourly cap: a second failure is quiet
        self.assertEqual(self.client.completion()["outcome"], "completed")
        self.assertEqual(self.head(self.origin, "main"), before)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any("pull before coo turn failed" in m and "using the local tree" in m for m in messages), messages)
        self.assertEqual(messages[-1], "Tico runner: emp-coo has 1 unpushed commits and push failed (non-fast-forward); leaving it for a person")
        self.assertEqual(len(messages), 2)

def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"})


def _behind_checkout(tmp, change="README"):
    """An origin with one commit the checkout does not have yet; returns the checkout and its HEAD."""
    origin, work, clone = Path(tmp, "origin.git"), Path(tmp, "work"), Path(tmp, "clone")
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp, "clone", "-q", str(origin), str(work))
    (work / "README").write_text("one\n")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "one")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(tmp, "clone", "-q", "-b", "main", str(origin), str(clone))
    first = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    Path(work, change).parent.mkdir(parents=True, exist_ok=True)
    Path(work, change).write_text("two\n")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "two")
    _git(work, "push", "-q", "origin", "HEAD:main")
    return clone, first


def test_self_update_fast_forwards_a_clean_checkout_and_asks_for_a_restart():
    with tempfile.TemporaryDirectory() as tmp, mock.patch("runner.service.log"):
        clone, first = _behind_checkout(tmp)
        assert service.self_update(clone, running=first) == (True, "")
        assert Path(clone, "README").read_text() == "two\n"
        assert service.self_update(clone, running=first) == (True, ""), "pulled but not restarted still restarts"


SUPERVISOR_VARS = ("XPC_SERVICE_NAME", "TICO_SUPERVISED", "TICO_RUNNER_SELF_UPDATE")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in SUPERVISOR_VARS}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


def test_only_a_supervisor_lets_the_runner_exit_to_update():
    for extra in ({"XPC_SERVICE_NAME": "team.tico-bot"}, {"TICO_SUPERVISED": "1"}):
        with clean_env(**extra):
            assert service.supervised() is True
            assert service.under_supervisor({}) is True
            assert service.under_supervisor({"self_update": False}) is False
            with mock.patch.dict(os.environ, {"TICO_RUNNER_SELF_UPDATE": "0"}):
                assert service.under_supervisor({}) is False
    for extra in ({}, {"XPC_SERVICE_NAME": "0"}, {"XPC_SERVICE_NAME": "application.com.other"},
                  {"TICO_SUPERVISED": "0"}):
        with clean_env(**extra):
            assert service.supervised() is False
            assert service.under_supervisor({}) is False


if __name__ == "__main__":
    unittest.main()


def antigravity(**over):
    a = attempt()
    a["config"] = {"runtime": "gemini", "harness": "antigravity", "model": "gemini-3.8-flash",
                   "reasoning_effort": "low", "max_run_minutes": 1}
    a["conversation"] = {"id": "private-ana", "scope": "personal", "kind": "chat"}
    a["principal"] = "human:ana"
    a.update(over)
    return a


def with_fallback(row=None):
    row = row or antigravity()
    row["config"] = {**row["config"], "fallback": {
        "harness": "gemini", "model": "gemini-3.8-flash", "reasoning_effort": "low"}}
    return row


class Fallback(unittest.TestCase):
    """A configured fallback harness reruns the turn when the primary is unavailable."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "emp-coo").mkdir()
        self.config = {"url": "https://runner.example", "token": "machine", "projects_dir": str(root), "capacity": 1}
        self.calls = []
        self.hosts = {"antigravity": FakeHost(), "gemini": FakeHost(replies=["Answered on the fallback"])}
        self.hosts["antigravity"].fail_next_turn("UNAVAILABLE (code 503): No capacity available for model gemini-3.8-flash-low")

    def tearDown(self):
        self.tmp.cleanup()

    def factory(self, attempt, env):
        which = attempt.get("fallback") or attempt["config"].get("harness")
        self.calls.append((which, attempt["config"].get("harness"), env["HUB_TOKEN"]))
        return self.hosts[which]

    def runner(self, client):
        runner = Runner(self.config, Path(self.tmp.name) / "state", host_factory=self.factory, client=client,
                        push=lambda path, env=None: (0, ""))
        runner.renew_interval = 0.05
        return runner

    def test_a_limited_turn_runs_on_the_configured_fallback(self):
        client = FakeClient()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-1234567890"}), \
                mock.patch("runner.service.log") as log, mock.patch("runner.service.Client") as vault:
            vault.return_value.get.return_value = {"credentials": [{"id": "gemini", "env": "GEMINI_API_KEY",
                "kind": "api_key", "value": "granted-key-1234567890"}]}
            runner = self.runner(client)
            runner.execute({**with_fallback(), "credential_vault": True})
        completion = client.completion()
        self.assertEqual(completion["outcome"], "completed")
        self.assertEqual(completion["text"], "Answered on the fallback")
        self.assertEqual(completion["fallback"], "gemini")
        self.assertNotIn("limited", completion)
        log.assert_called_once_with("Tico runner: coo: antigravity unavailable; ran the turn on gemini")
        self.assertEqual([c[0] for c in self.calls], ["antigravity", "gemini"])
        self.assertEqual(self.calls[1][1], "gemini")
        self.assertTrue(self.calls[0][2].startswith("tico-file:") and self.calls[1][2] == "turn-token")
        settings = next(iter(self.hosts["gemini"].threads.values()))["settings"]
        self.assertEqual((settings["model"], settings["effort"], settings["cwd"]),
                         ("gemini-3.8-flash", "low", str(Path(self.tmp.name) / "emp-coo")))
        self.assertEqual(settings["env"]["GEMINI_API_KEY"], "granted-key-1234567890")
        self.assertIn("hello", self.hosts["gemini"].prompts[0][1])
        self.assertEqual(runner.warm.entries, {})
        self.assertFalse(self.hosts["antigravity"].alive())
        self.assertEqual(runner.state.unfinished(), [])
        kinds = [json.loads(p)["text"] for (k, p) in self.events(runner, "diagnostic")]
        self.assertIn("antigravity unavailable; running this turn on gemini", kinds)

    @staticmethod
    def events(runner, kind):
        with runner.state.connect() as c:
            return [(r["kind"], r["payload"]) for r in c.execute("SELECT kind,payload FROM events WHERE kind=?", (kind,))]


class ThreadContinuity(unittest.TestCase):
    """A bot keeps one thread. Nothing here ends it; the runtime compacts it when it fills."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "emp-coo").mkdir()
        self.config = {"url": "https://runner.example", "token": "machine", "projects_dir": str(root), "capacity": 1}
        self.hosts = []

    def tearDown(self):
        self.tmp.cleanup()

    def factory(self, attempt, env):
        host = FakeHost(replies=["one", "two", "three"])
        if getattr(self, "factory_fails", False):
            self.factory_fails = False
            host.fail_next_turn("the model returned garbage")
        self.hosts.append(host)
        return host

    def runner(self):
        runner = Runner(self.config, Path(self.tmp.name) / "state", host_factory=self.factory, client=FakeClient(),
                        push=lambda path, env=None: (0, ""))
        runner.renew_interval = 0.05
        return runner

    def test_every_turn_resumes_the_same_thread(self):
        runner = self.runner()
        with mock.patch("runner.service.log") as log:
            runner.execute(attempt("a1"))
            runner.execute(attempt("a2"))
            runner.execute(attempt("a3"))
            self.assertEqual(len(self.hosts[1].resumes), 1)
            self.assertEqual(len(self.hosts[2].resumes), 1)     # heavy or not, it is the same thread
            log.assert_not_called()
            first = self.hosts[0].prompts[0][0]
            self.assertEqual(next(iter(self.hosts[2].threads)), first)
            self.assertEqual(runner.state.session("coo", BOT_THREAD, "codex"), first)

class RefusedReplies(unittest.TestCase):
    """The COO, 2026-09-28: a reply with Codex's local file links into another bot's repository
    was refused on completion twice, and the job read as a run that stopped partway."""
    CODEX = ("Task [`1b874fee`](file:///Volumes/x/projects/emp-coo/state.md#L13-L20) is open "
             "([`emp-legal/reports/2026-09-28-inbound-findings.md`](file:///Volumes/x/projects/emp-legal/reports/f.md)). "
             "See emp-legal/reports/a.md, emp-coo/knowledge/needs.md and ../secrets/_shared.env. "
             "PR https://github.com/acme/emp-legal/pull/3.")

    def test_the_scrubbed_reply_passes_the_hubs_own_rule_and_keeps_the_rest(self):
        from backend import hubdb
        self.assertEqual(hubdb.classify(self.CODEX, where="message", actor="bot:coo"), "escape")
        clean = service.scrub_reply(self.CODEX, "coo")
        self.assertNotEqual(hubdb.classify(clean, where="message", actor="bot:coo"), "escape")
        self.assertIn("Task `1b874fee` is open", clean)
        self.assertIn("emp-coo/knowledge/needs.md", clean, "its own repository is not an escape")
        self.assertIn("(a file in legal's repository), emp-coo", clean, "the comma survives")
        self.assertNotIn("file://", clean)
        self.assertEqual(service.scrub_reply("All clear.", "coo"), "All clear.")

    def test_a_refused_reply_settles_the_attempt_instead_of_letting_the_lease_lapse(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "emp-coo").mkdir()
        client = FakeClient()
        plain = client.post

        def post(path, body=None, key=None):
            if path.endswith("/complete") and body["outcome"] == "completed":
                client.posts.append((path, body))
                raise APIError("escape", "The message includes a secrets path or another bot’s workspace path.", 403, False)
            return plain(path, body, key)
        client.post = post
        runner = Runner({"url": "https://runner.example", "token": "machine", "projects_dir": tmp.name, "capacity": 1},
                        Path(tmp.name) / "state", host_factory=lambda a, env: FakeHost(replies=["done"]),
                        client=client, push=lambda path, env=None: (0, ""))
        with mock.patch("runner.service.log"):
            runner.execute(attempt())
        completes = [body for path, body in client.posts if path.endswith("/complete")]
        self.assertEqual([c["outcome"] for c in completes], ["completed", "failed"])
        self.assertIn("refused this turn's reply", completes[1]["text"])


def test_the_runner_keeps_its_last_trouble_lines_for_a_support_bundle():
    from runner import outage
    outage.RECENT.clear()
    outage.log("Tico runner: heartbeat is fine")
    outage.log("Tico runner: cloud unavailable (http_error, HTTP 530); retrying")
    outage.log("Tico runner: coo: codex sign-in was rejected")
    assert [line.split(" ", 1)[1] for line in outage.RECENT] == [
        "Tico runner: cloud unavailable (http_error, HTTP 530); retrying", "Tico runner: coo: codex sign-in was rejected"]
    for n in range(80):
        outage.log(f"Tico runner: failed {n}")
    assert len(outage.RECENT) == 50 and outage.RECENT[-1].endswith("failed 79")
    outage.RECENT.clear()


def test_busy_bots_follow_live_turn_processes_until_they_exit(tmp_path):
    client = FakeClient()
    runner = Runner({"url": "https://runner.example", "token": "machine", "projects_dir": str(tmp_path)},
                    tmp_path / "state", client=client)
    future = Future()
    runner.active["att-1"] = future
    runner.attempt_bots["att-1"] = "ops"
    # Even after a lost lease, the supervisor tracks the live worker.
    runner.claim_next()
    runner.claim_next()
    assert client.posts == [("jobs/claim", {"next_run": True, "busy_bots": ["ops"]})] * 2
    future.set_result(None)
    runner.claim_next()
    assert client.posts[-1] == ("jobs/claim", {"next_run": True, "busy_bots": []})


def test_busy_bots_fall_back_to_one_claim_on_an_old_server(tmp_path):
    client = FakeClient()
    original = client.post
    seen = []
    def old(path, body=None, key=None):
        seen.append(body)
        if "busy_bots" in body:
            raise APIError("validation", "Extra inputs: busy_bots", 422, False)
        return original(path, body, key)
    client.post = old
    runner = Runner({"url": "https://runner.example", "token": "machine", "projects_dir": str(tmp_path)},
                    tmp_path / "state", client=client)
    runner.assignments_seen = [{"bot": "ops"}, {"bot": "finance"}]
    assert runner.claim_next() == {"attempt": None}
    assert runner.claim_next() == {"attempt": None}
    assert seen == [{"next_run": True, "busy_bots": []}, {"next_run": True}, {"next_run": True}]
    # Re-probe after an old server updates, without restarting the computer.
    runner._busy_bots_after = 0
    assert runner.claim_next() == {"attempt": None}
    assert seen[-2:] == [{"next_run": True, "busy_bots": []}, {"next_run": True}]
    # Heartbeats keep the original contract, with no process report.
    assert runner.report_heartbeat({"readiness": {}}) == {}


def test_started_and_saved_completion_keep_original_idempotency_keys(tmp_path):
    client, host = FakeClient(), FakeHost(replies=['done'])
    runner = Runner({'url': 'https://runner.example', 'token': 'machine', 'projects_dir': str(tmp_path)},
                    tmp_path / 'state', client=client, host_factory=lambda *args: host,
                    push=lambda *args, **kw: (0, ''))
    keys, original = [], client.post
    def post(path, body=None, key=None):
        keys.append(key)
        return original(path, body, key)
    client.post = post
    (tmp_path / 'emp-coo').mkdir()
    runner.execute(attempt())
    runner.complete('att-1', client.completion())
    assert 'started:att-1' in keys
    assert keys.count('complete:att-1') == 2
    assert not any(key and key.startswith(('started:att-1:', 'complete:att-1:')) for key in keys)

def test_combined_heartbeat_reports_retry_only_the_named_field(tmp_path):
    from clients.tico import APIError
    client = FakeClient()
    runner = Runner({'url': 'https://runner.example', 'token': 'machine', 'projects_dir': str(tmp_path)}, tmp_path / 'state', client=client)
    calls = []
    fields = ['profiles', 'worktrees', 'repositories']
    def post(path, body):
        calls.append(json.loads(json.dumps(body)))
        if len(calls) <= len(fields):
            raise APIError('validation', 'body.' + fields[len(calls) - 1] + ': Extra inputs', 422)
        return {'ok': True}
    client.post = post
    body = {'readiness': {'bots': {'coo': {'ready': True}}}, 'profiles': [], 'worktrees': [], 'repositories': []}
    assert runner.report_heartbeat(body) == {'ok': True}
    for before, after, field in zip(calls, calls[1:], fields):
        assert set(before) - set(after) == {field}
        assert before['readiness'] == after['readiness']


def test_lease_renews_while_turn_waits_for_worktree_maintenance(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    client, host = FakeClient(), FakeHost(replies=['done'])
    runner = Runner({'url': 'https://runner.example', 'token': 'machine', 'projects_dir': str(tmp_path)},
                    tmp_path / 'state', client=client, host_factory=lambda *args: host,
                    push=lambda *args, **kw: (0, ''))
    runner.renew_interval = 60
    (tmp_path / 'emp-coo').mkdir()
    renewed = threading.Event()
    original = client.post
    def post(path, body=None, key=None):
        result = original(path, body, key)
        if path.endswith('/renew'):
            renewed.set()
        return result
    client.post = post
    # Advance one renewal only after execute reaches the held maintenance lock.
    tick, timer_waiting, lock_waiting = threading.Event(), threading.Event(), threading.Event()
    renew_loop = runner.renew_loop
    def renew(aid, lost, done, deadline):
        real_wait = done.wait
        first = True
        def wait(interval):
            nonlocal first
            if not first:
                return real_wait(interval)
            first = False
            assert interval == 60
            timer_waiting.set()
            assert tick.wait(10)
            return done.is_set()
        with mock.patch.object(done, 'wait', side_effect=wait):
            renew_loop(aid, lost, done, deadline)
    runner.renew_loop = renew
    lock = runner.worktrees.bot_lock('coo')
    class WaitingLock:
        def acquire(self, **kwargs):
            lock_waiting.set()
            return lock.acquire(**kwargs)
        def release(self):
            lock.release()
    runner.worktrees.bot_lock = lambda bot: WaitingLock()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock:
            future = pool.submit(runner.execute, attempt())
            try:
                assert timer_waiting.wait(10)
                assert lock_waiting.wait(10)
                tick.set()
                assert renewed.wait(10)
                assert host.replies == ['done'] and not future.done()
            finally:
                tick.set()
        future.result(timeout=10)
    assert client.completion()['outcome'] == 'completed'


def test_busy_bots_include_worktree_maintenance(tmp_path):
    client = FakeClient()
    runner = Runner({'url': 'https://runner.example', 'token': 'machine', 'projects_dir': str(tmp_path)},
                    tmp_path / 'state', client=client)
    runner.worktrees.maintaining.add('coo')
    runner.claim_next()
    assert client.posts == [('jobs/claim', {'next_run': True, 'busy_bots': ['coo']})]


@pytest.mark.parametrize("configured", [True, False])
def test_startup_backlog_uses_the_bots_scoped_helper(tmp_path, configured):
    from runner.tests.test_git_credentials import Hub, fill
    from runner import git_credentials as G, safe_git
    from types import SimpleNamespace
    seen = []
    client = Hub({"configured": configured, "token": "test-token", "repository": "acme/emp-ana"})
    client.get = lambda route: [{"bot": "ana", "config": {}}]
    path = tmp_path / "emp-ana"
    path.mkdir()
    runner = SimpleNamespace(stop=threading.Event(), client=client, config_path=None,
                             local_path=lambda *args: path,
                             credential_environment=lambda *args: safe_git.process_environment(),
                             push=lambda path, env, shared: seen.append((path, env)) or True)
    Runner.push_backlog(runner)
    assert len(seen) == 1
    env = seen[0][1]
    assert client.calls == [("github/token", {"bot": "ana"})]
    if configured:
        assert "password=test-token" in fill(safe_git.environment(env), tmp_path)
        assert "test-token" not in env["GIT_CONFIG_VALUE_1"]
        assert not (path / ".gitconfig").exists()
    else:
        assert "GH_TOKEN" not in env

def test_startup_backlog_never_pushes_with_another_login_after_token_failure(tmp_path):
    from runner.tests.test_git_credentials import Hub
    from types import SimpleNamespace
    client = Hub(error=OSError("offline"))
    client.get = lambda route: [{"bot": "ana", "config": {}}]
    push = mock.Mock()
    runner = SimpleNamespace(stop=threading.Event(), client=client, config_path=None,
                             local_path=lambda *args: tmp_path, credential_environment=lambda *args: {}, push=push)
    Runner.push_backlog(runner)
    push.assert_not_called()


def test_startup_push_refreshes_through_the_socket_and_revokes_its_capability(tmp_path):
    from types import SimpleNamespace
    from runner import credential_socket as C, safe_git
    from runner.tests.test_git_credentials import Hub, fill
    socket_dir = tempfile.TemporaryDirectory(dir="/tmp")
    server = C.Server(Path(socket_dir.name) / "cred.sock", lambda bot: "fresh-test-token").start()
    try:
        client = Hub({"configured": True, "token": "initial-test-token"})
        client.get = lambda route: [{"bot": "ana", "config": {}}]
        seen = []
        def push(path, env, shared):
            assert "password=fresh-test-token" in fill(safe_git.environment(env), tmp_path)
            seen.append(env["HUB_TOKEN"])
            return True
        runner = SimpleNamespace(stop=threading.Event(), client=client, config_path=tmp_path / "unreadable.json",
                                 credentials=server, local_path=lambda *args: tmp_path,
                                 credential_environment=lambda *args: safe_git.process_environment(), push=push)
        Runner.push_backlog(runner)
        assert len(seen) == 1 and not server.attempts
        with pytest.raises(ValueError):
            C.request(server.path, seen[0])
    finally:
        server.stop()
        socket_dir.cleanup()
