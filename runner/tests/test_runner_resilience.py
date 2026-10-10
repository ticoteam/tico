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
from runner.service import Runner

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
        self.assertIn("RuntimeError", client.completion()["text"], "the error, not a runner restart, is the reason given")
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

    def test_a_follow_up_refused_by_an_ending_turn_gets_a_turn_of_its_own_in_the_same_run(self):
        # The turn ends between the inputs poll and the steer: the host writes nothing and says
        # so. The poll already assigned the message to this run, so the run answers it itself.
        client = FakeClient()
        runner = self.runner(client)
        self.host.hold_next_turn()
        self.host.replies = ["the follow-up's answer"]          # the held first turn takes none
        follow_up = {"id": "msg-2", "kind": "chat", "body": "one more thing", "from_actor": "human:ana"}
        post, polls = client.post, []

        def inputs_once(path, body=None, key=None):
            if path.endswith("/inputs") and self.host.turn_of:
                polls.append(path)
                return {"messages": [follow_up]}
            return post(path, body, key)

        def refuse(thread_id, turn_id, text):
            self.host.emit("tokens", thread_id, turn_id, input=40, output=2, total=42,
                           usage={"input": 40, "cached": 0, "output": 2})
            self.host.complete(thread_id, "the first answer")
            raise service.SteerRefused("turn is ending")

        with mock.patch.object(client, "post", side_effect=inputs_once), \
                mock.patch.object(self.host, "steer", side_effect=refuse):
            runner.execute(attempt())
        self.assertEqual(len(polls), 1)
        self.assertEqual(len(self.host.prompts), 2)
        self.assertIn("human:ana: one more thing", self.host.prompts[1][1])
        self.assertIn("already given; answer only the follow-up", self.host.prompts[1][1])
        self.assertIn(("attempts/att-1/inputs/msg-2/ack", {}), client.posts)
        self.assertEqual(runner.state.input_phase("att-1", "msg-2"), "applied")
        self.assertEqual(client.completion()["outcome"], "completed")
        self.assertEqual(client.completion()["text"], "the first answer\n\nthe follow-up's answer")
        reply = "the follow-up's answer"
        self.assertEqual((client.completion()["tokens_in"], client.completion()["tokens_out"]),
                         (40 + 100, 2 + len(reply)))           # both turns, not just the last
        self.assertNotIn("undelivered", client.completion())

    def refused_follow_up(self, client, refuse, run):
        """Run one attempt whose single inputs poll returns a follow-up the host refuses with `refuse`."""
        runner = self.runner(client)
        self.host.hold_next_turn()
        post, polls = client.post, []

        def inputs_once(path, body=None, key=None):
            if path.endswith("/inputs") and self.host.turn_of and not polls:
                polls.append(path)
                return {"messages": [{"id": "msg-2", "kind": "chat", "body": "one more thing",
                                      "from_actor": "human:ana"}]}
            return post(path, body, key)

        with mock.patch.object(client, "post", side_effect=inputs_once), \
                mock.patch.object(self.host, "steer", side_effect=refuse):
            runner.execute(run)
        return runner

    def test_a_follow_up_refused_by_a_failing_turn_is_handed_back_undelivered(self):
        def refuse(thread_id, turn_id, text):
            self.host.turn_of.pop(thread_id, None)
            self.host.emit("turn_failed", thread_id, turn_id, error="the turn failed", limit=False, auth_retry=False)
            raise service.SteerRefused("turn is ending")

        client = FakeClient()
        self.refused_follow_up(client, refuse, attempt())
        self.assertEqual(len(self.host.prompts), 1)            # no turn for it on a failed run
        self.assertEqual(client.completion()["outcome"], "failed")
        self.assertEqual(client.completion()["undelivered"], ["msg-2"])
        self.assertNotIn(("attempts/att-1/inputs/msg-2/ack", {}), client.posts)

    def test_a_follow_up_refused_before_a_goal_control_is_handed_back_undelivered(self):
        goal = {"id": "goal-1", "status": "active", "updated_at": "r1", "objective": "ship it"}
        client = FakeClient()
        refused = threading.Event()
        # The goal is unchanged until the steer was refused; then a person pauses it.
        client.get = lambda path, **query: ({"goal": goal} if not refused.is_set() else {"goal": None})

        def refuse(thread_id, turn_id, text):
            refused.set()
            raise service.SteerRefused("turn is ending")

        self.refused_follow_up(client, refuse, {**attempt(), "chat_goal": goal})
        self.assertEqual(client.completion()["outcome"], "completed")
        self.assertEqual(client.completion()["undelivered"], ["msg-2"])
        self.assertEqual(len(self.host.prompts), 1)

    def test_a_server_from_before_undelivered_still_gets_the_result(self):
        client = FakeClient()
        sent = []

        def older_server(path, body=None, key=None):
            sent.append(dict(body))
            if "undelivered" in body:
                raise APIError("validation", "body.undelivered: Extra inputs are not permitted", 422)
            return {}

        client.post = older_server
        self.runner(client).complete("att-1", {"outcome": "failed", "text": "", "last_seq": 0,
                                               "undelivered": ["msg-2"]})
        self.assertEqual([("undelivered" in body) for body in sent], [True, False])

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

    def test_a_stop_on_the_lease_renewal_interrupts_the_running_turn(self):
        client = FakeClient()
        runner = self.runner(client)
        self.host.hold_next_turn()
        post = client.post
        stopped_turns = set()
        stop_flags, active_drains = [], []
        stop_ready = threading.Event()
        renew, drain = runner.renew_loop, self.host.drain

        def observe_stop(aid, lost, done, deadline, stopped=None):
            stop_flags.append(stopped)
            stop_ready.set()
            return renew(aid, lost, done, deadline, stopped)

        def drain_until_stop():
            if self.host.turn_of:
                active_drains.append(True)
                # Let the real renewal process Stop before this iteration finishes. Its
                # next iteration must interrupt, not poll the still-held turn again.
                self.assertTrue(stop_ready.wait(60), "the renewer starts")
                self.assertTrue(stop_flags[0].wait(60), "the Stop renewal is processed")
            return drain()

        runner.renew_loop = observe_stop

        def stop_once_running(path, body=None, key=None):
            if path.endswith("/renew") and self.host.turn_of:
                stopped_turns.update(self.host.turn_of.items())
                return {"lease_seconds": 90, "stop": True}
            return post(path, body, key)

        with mock.patch.object(client, "post", side_effect=stop_once_running), \
                mock.patch.object(self.host, "drain", side_effect=drain_until_stop), \
                mock.patch.object(self.host, "interrupt", wraps=self.host.interrupt) as interrupt:
            runner.execute(attempt())
        # Preparation and thread scheduling are not part of the Stop contract. The renewal
        # carrying Stop must interrupt that held turn and finish without a retry.
        self.assertEqual(len(stopped_turns), 1)
        self.assertTrue(stop_flags[0].is_set())
        self.assertLessEqual(len(active_drains), 1, "Stop takes effect before another host poll")
        interrupt.assert_called_with(*next(iter(stopped_turns)))
        completion = client.completion()
        self.assertEqual(completion["outcome"], "interrupted")
        self.assertNotIn("retryable", completion)


def git(path, *args, env=None):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env)


@pytest.mark.slow
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
        # Placement says once that the diverged copy was kept; the pull and push failures follow.
        self.assertTrue(messages[0].startswith("Tico runner: coo: kept this computer's copy as it is"), messages)
        self.assertEqual(len(messages), 3)

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


@pytest.mark.slow
def test_self_update_fast_forwards_a_clean_checkout_and_asks_for_a_restart():
    with tempfile.TemporaryDirectory() as tmp, mock.patch("runner.service.log"):
        clone, first = _behind_checkout(tmp)
        assert service.self_update(clone, running=first) == (True, "")
        assert Path(clone, "README").read_text() == "two\n"
        assert service.self_update(clone, running=first) == (True, ""), "pulled but not restarted still restarts"




if __name__ == "__main__":
    unittest.main()


class RefusedReplies(unittest.TestCase):
    """The COO, 2026-09-28: a reply with Codex's local file links into another bot's repository
    was refused on completion twice, and the job read as a run that stopped partway."""
    CODEX = ("Task [`1b874fee`](file:///Volumes/x/projects/emp-coo/state.md#L13-L20) is open "
             "([`emp-legal/reports/2026-09-28-inbound-findings.md`](file:///Volumes/x/projects/emp-legal/reports/f.md)). "
             "See emp-legal/reports/a.md, emp-coo/knowledge/needs.md and ../secrets/_shared.env. "
             "PR https://github.com/acme/emp-legal/pull/3.")

    def test_the_scrubbed_reply_drops_local_links_and_keeps_paths_as_written(self):
        clean = service.scrub_reply(self.CODEX, "coo")
        self.assertIn("Task `1b874fee` is open", clean)
        self.assertIn("See emp-legal/reports/a.md, emp-coo/knowledge/needs.md and ../secrets/_shared.env.", clean,
                      "a path written as text is the bot's words, not an escape")
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


def test_lease_renews_while_turn_waits_for_worktree_maintenance(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    client, host = FakeClient(), FakeHost(replies=['done'])
    runner = Runner({'url': 'https://runner.example', 'token': 'machine', 'projects_dir': str(tmp_path)},
                    tmp_path / 'state', client=client, host_factory=lambda *args: host,
                    push=lambda *args, **kw: (0, ''))
    runner.renew_interval = 60
    # These are hang guards for thread/filesystem work, not performance assertions.
    wait_bound = 60
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
    def renew(aid, lost, done, deadline, stopped=None):
        real_wait = done.wait
        first = True
        def wait(interval):
            nonlocal first
            if not first:
                return real_wait(interval)
            first = False
            assert interval == 60
            timer_waiting.set()
            assert tick.wait(wait_bound)
            return done.is_set()
        with mock.patch.object(done, 'wait', side_effect=wait):
            renew_loop(aid, lost, done, deadline, stopped)
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
                assert timer_waiting.wait(wait_bound)
                assert lock_waiting.wait(wait_bound)
                tick.set()
                assert renewed.wait(wait_bound)
                assert host.replies == ['done'] and not future.done()
            finally:
                tick.set()
        future.result(timeout=wait_bound)
    assert client.completion()['outcome'] == 'completed'


@pytest.mark.parametrize("configured", [True])
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


def test_a_credential_under_a_reserved_name_is_left_out_and_the_turn_still_starts(tmp_path):
    runner = Runner.__new__(Runner)
    runner.config = {"url": "https://acme.test", "projects_dir": str(tmp_path)}
    runner.vault_values, runner.vault_names, runner.vault_files = {}, {}, {}
    granted = {"credentials": [
        {"id": "c1", "name": "Acme update key", "env": "TICO_UPDATE_KEY_ACME", "kind": "api_key", "value": "k1-synthetic"},
        {"id": "c2", "name": "PostHog", "env": "POSTHOG_API_KEY", "kind": "api_key", "value": "ph-synthetic"}]}
    env = runner.environment({"id": "a1", "bot": "rel", "token": "t", "config": {"runtime": "codex"}}, granted)
    assert "TICO_UPDATE_KEY_ACME" not in env and "k1-synthetic" not in env.values()
    assert env["POSTHOG_API_KEY"] == "ph-synthetic" and runner.vault_names["a1"] == {"POSTHOG_API_KEY"}


def test_a_claude_deny_on_a_command_agent_md_asks_for_is_named(tmp_path):
    """Codex ignores .claude/settings.json, so a bot moved to Claude Code would otherwise find these mid-task."""
    import json as _json
    from runner.service import claude_denied
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(_json.dumps({"permissions": {"deny": [
        "Bash(gh pr create *)", "Bash(gh pr merge *)", "Bash(gh secret *)"]}}))
    (tmp_path / "AGENT.md").write_text("Open your pull request with `gh pr create`. The settings deny `gh pr merge`.\n"
                                       "Read with `gh pr view`.\n")
    assert claude_denied(tmp_path) == ["gh pr create"]
