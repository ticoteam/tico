"""Subscription profiles: which provider login a bot's turn and readiness check run against.

No CLI is started. The hosts are inspected for the homes they were built with, and the readiness
checks are driven through a fake `subprocess.run` that records the environment it was given.
"""

import os
import tempfile
import unittest
from pathlib import Path

import pytest

from runner import profiles
from runner.service import Runner


def test_profile_add_does_not_persist_a_derived_state_directory(tmp_path, monkeypatch):
    import json
    from runner import __main__ as cli

    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"runner_id": "test-registration"}))
    monkeypatch.setattr(cli.mail_key, "carry_protected", lambda *args: pytest.fail("profile add must only add a profile"))
    cli.main(["--config", str(config), "profile", "add", "team", "--share-operator"])
    stored = json.loads(config.read_text())
    assert "state_dir" not in stored
    assert stored["default_profile"] == "team" and "team" in stored["profiles"]


BOT = {"bot": "sales", "id": "a1", "token": "t", "config": {"runtime": "codex"},
       "conversation": {"id": "c1"}}


def attempt(bot="sales", runtime="codex", **config):
    return {**BOT, "bot": bot, "config": {"runtime": runtime, **config}}


def service(tmp, **config):
    """A Runner with no outbox, client, or threads: `doctor` builds one the same way."""
    runner = Runner.__new__(Runner)
    runner.config = {"url": "https://example.test", "token": "t", "projects_dir": str(tmp), **config}
    runner.state = type("S", (), {"directory": Path(tmp) / "state"})()
    return runner


class Readiness(unittest.TestCase):
    """The sign-in check has to look in the profile's home, and say whose it is."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.one = profiles.create(root / "profiles", "one")
        self.two = profiles.create(root / "profiles", "two")
        self.runner = service(self.tmp.name, profiles={"one": self.one, "two": self.two},
                              default_profile="one", bot_profiles={"sales": "two"})
        self.seen = []

        def run(argv, **kwargs):
            """Only the profile `one` home holds a Codex login."""
            home = (kwargs.get("env") or {}).get("CODEX_HOME", "")
            self.seen.append((argv, home))
            signed_in = home.startswith(self.one["dir"])
            return type("R", (), {"returncode": 0 if signed_in else 1,
                                  "stdout": "Logged in using ChatGPT" if signed_in else "",
                                  "stderr": ""})()
        self.run = run

    def report(self):
        import runner.service as service_module
        original_run, original_which = service_module.subprocess.run, service_module.shutil.which
        service_module.subprocess.run = self.run
        service_module.shutil.which = lambda name: "/usr/local/bin/" + name
        try:
            assignments = [{"bot": "coo", "config": {"runtime": "codex"}},
                           {"bot": "sales", "config": {"runtime": "codex"}}]
            return assignments, self.runner.runtime_report(assignments)
        finally:
            service_module.subprocess.run, service_module.shutil.which = original_run, original_which

    def test_a_bot_is_blocked_only_by_its_own_profile(self):
        assignments, report = self.report()
        rows = {row["bot"]: row for row in self.runner.preflight(assignments, report)}
        self.assertEqual(rows["coo"]["profile"], "one")
        self.assertEqual(rows["sales"]["profile"], "two")
        self.assertEqual(rows["coo"]["problems"], ["Missing bot repository or AGENT.md"])
        self.assertIn("two: Codex login required", rows["sales"]["problems"])

    def test_server_profile_overrides_local_and_missing_profile_blocks(self):
        config = self.runner.config
        self.assertEqual(profiles.select(config, 'sales', 'one').name, 'one')
        self.assertIsNone(profiles.select(config, 'sales', 'absent'))
        self.assertEqual(profiles.missing(config, 'absent'), 'profile absent not on this computer')
        assignments, report = self.report()
        assignments[1]['profile'] = 'absent'
        assignments[1]['computer_label'] = 'Build Computer'
        row = self.runner.preflight(assignments, report)[1]
        self.assertIn("Subscription absent isn't on Build Computer", row['problems'])
        self.assertFalse(row['ready'])
        self.assertEqual(row['profile'], '')

    def test_reports_all_profiles_and_sign_in_state(self):
        import json
        import subprocess
        from unittest import mock
        probes = []
        def probe(argv, **kwargs):
            probes.append((argv, kwargs))
            signed = self.one['dir'] in str(kwargs['env'])
            return type('R', (), {'returncode': 0 if signed else 1,
                'stdout': json.dumps({'loggedIn': signed}) if 'claude' in argv[0] else 'Logged in using ChatGPT',
                'stderr': ''})()
        with mock.patch('runner.service.shutil.which', side_effect=lambda runtime: runtime), \
                mock.patch('runner.service.isolation.run', side_effect=probe):
            rows = self.runner.profile_report()
            self.assertEqual([r['name'] for r in rows], ['one', 'two'])
            self.assertTrue(rows[0]['runtimes']['codex']['signed_in'])
            self.assertFalse(rows[1]['runtimes']['claude']['signed_in'])
            self.assertIsNone(rows[0]['runtimes']['gemini']['signed_in'])
            self.assertEqual(len(probes), 4)
            self.runner.profile_report()
            self.assertEqual(len(probes), 4)
            self.assertTrue(all('--version' not in argv and kw['timeout'] <= 3 for argv, kw in probes))
            with mock.patch('runner.service.time.monotonic', return_value=self.runner._profile_report_cache[0] + 61):
                self.runner.profile_report()
            self.assertEqual(len(probes), 8)
            self.runner.add_profile('new')
            self.runner.profile_report()
            self.assertEqual(len(probes), 14)
        self.runner._profile_report_cache = None
        with mock.patch('runner.service.shutil.which', return_value='codex'), \
                mock.patch('runner.service.isolation.run', side_effect=subprocess.TimeoutExpired('probe', 3)):
            self.assertTrue(all(status['signed_in'] is None for row in self.runner.profile_report() for status in row['runtimes'].values()))

    def test_named_profile_is_persisted_without_replacing_local_assignments(self):
        import json
        path = Path(self.tmp.name) / 'runner.json'
        path.write_text(json.dumps(self.runner.config))
        self.runner.config_path = path
        self.runner.add_profile('engineering')
        stored = json.loads(path.read_text())
        self.assertEqual(stored['default_profile'], 'one')
        self.assertEqual(stored['bot_profiles'], {'sales': 'two'})
        self.assertIn('engineering', stored['profiles'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(stored['profiles']['engineering']['dir']).stat().st_mode & 0o777, 0o700)

    def test_old_server_rejects_profiles_without_losing_heartbeat(self):
        from clients.tico import APIError
        class Client:
            def post(self, path, body):
                if 'profiles' in body:
                    raise APIError('validation', 'body.profiles: Extra inputs are not permitted', 422)
                return {'server_time': 'now'}
        self.runner.client = Client()
        body = {'readiness': {}, 'repositories': [{'full_name': 'example/product', 'state': 'cloned'}], 'profiles': [{'name': 'one', 'runtimes': {}}]}
        self.assertEqual(self.runner.report_heartbeat(body), {'server_time': 'now'})
        self.assertNotIn('profiles', body)
        self.assertIn('repositories', body)
        self.assertFalse(hasattr(self.runner, '_repositories_after'))

    def test_claim_profile_controls_environment_and_host_home(self):
        from unittest import mock
        work = {**BOT, 'profile': 'one'}
        self.runner.credential_environment = lambda *args: {'CLAUDE_CONFIG_DIR': '/operator/.claude'}
        self.runner.vault_values = {}
        self.runner.runtime_readiness = lambda *args: {"authenticated": "ready"}
        env = self.runner.environment(work)
        self.assertEqual(env['CODEX_HOME'], str(Path(self.one['dir']) / 'codex'))
        with mock.patch('runner.hosts.codex.mcp_disable_config') as disable:
            self.runner.make_host(work, env)
        disable.assert_called_once_with(Path(self.one['dir']) / 'codex')
        work['config'] = {'runtime': 'claude'}
        self.runner.runtime_readiness = lambda *args: {"authenticated": "ready"}
        env = self.runner.environment(work)
        self.assertEqual(env['HOME'], str(Path(self.one['dir']) / 'claude'))
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], '/operator/.claude')
        self.assertNotIn('CLAUDE_CONFIG_DIR', profiles.Profile('one', self.one['dir']).environment('claude', {}))

    def test_named_profile_uses_state_not_workspace_and_rejects_symlinks(self):
        root = Path(self.tmp.name)
        destination = root / 'other-directory'
        destination.mkdir()
        (root / '.profiles').symlink_to(destination, target_is_directory=True)
        entry = self.runner.add_profile('engineering')
        self.assertEqual(Path(entry['dir']), (self.runner.state.directory / 'profiles/engineering').resolve())
        self.assertEqual(list(destination.iterdir()), [])
        for relative in ('claude/.claude', 'profile.json'):
            path = Path(entry['dir']) / relative
            path.rmdir() if path.is_dir() else path.unlink()
            path.symlink_to(destination)
            with self.assertRaises(ValueError):
                self.runner.add_profile('engineering')
            path.unlink()
            if relative == 'claude/.claude':
                path.mkdir()

    def test_profile_file_swap_cannot_overwrite_target(self):
        from unittest import mock
        target = Path(self.tmp.name) / 'target'
        target.write_text('preserve')
        real_open = os.open
        def swapped(path, flags, *args, **kwargs):
            if path == 'profile.json' and flags & os.O_CREAT:
                os.symlink(target, path, dir_fd=kwargs['dir_fd'])
            return real_open(path, flags, *args, **kwargs)
        with mock.patch('runner.profiles.os.open', side_effect=swapped):
            with self.assertRaises(ValueError):
                self.runner.add_profile('swapped')
        self.assertEqual(target.read_text(), 'preserve')

    def test_a_turn_records_the_assigned_profile_and_separates_resume_keys(self):
        from runner.hosts.fake import FakeHost
        from runner.tests.test_runner_resilience import FakeClient, attempt
        (Path(self.tmp.name) / 'emp-coo').mkdir()
        config = {**self.runner.config, 'default_profile': 'two', 'capacity': 1}
        client = FakeClient()
        runner = Runner(config, Path(self.tmp.name) / 'turn-state',
                        host_factory=lambda a, env: FakeHost(replies=['done']), client=client,
                        push=lambda path, env=None: (0, ''))
        runner.renew_interval = 0.05
        runner.runtime_readiness = lambda *args: {"authenticated": "ready"}
        row = {**attempt(), 'profile': 'two'}
        runner.execute(row)
        done = client.completion()
        self.assertEqual(done['profile_used'], 'two')
        self.assertEqual(done['usage']['profile_used'], 'two')
        with runner.state.connect() as c:
            keys = [r[0] for r in c.execute('SELECT conversation FROM sessions')]
        self.assertTrue(any(':profile:two' in key for key in keys))

    def test_warm_host_changes_when_profile_environment_changes(self):
        from unittest import mock
        from runner.warm import WarmSessions
        warm = WarmSessions(Path(self.tmp.name) / 'warm')
        work = {**BOT, 'conversation': {'id': 'conversation'}}
        factory = mock.Mock(side_effect=lambda *args: mock.Mock(alive=lambda: True))
        first, _ = warm.acquire(work, profiles.Profile('one', self.one['dir']).environment('claude', {'HUB_TOKEN': 'turn'}), factory)
        warm.release(first, True)
        second, _ = warm.acquire(work, profiles.Profile('two', self.two['dir']).environment('claude', {'HUB_TOKEN': 'turn'}), factory)
        self.assertIsNot(first, second)
        first.stop.assert_called_once()
        self.assertEqual(factory.call_count, 2)
        warm.release(second, False)

    def test_assigned_profile_cannot_start_on_another_login(self):
        from unittest import mock
        for method in (self.runner.environment, lambda work: self.runner.make_host(work, {})):
            with self.assertRaisesRegex(RuntimeError, "Subscription absent isn't on"):
                method({**BOT, 'profile': 'absent'})
            with mock.patch.object(self.runner, 'runtime_rows', {'codex': {'profiles': {'one': {'authenticated': 'missing'}}}}, create=True):
                with self.assertRaisesRegex(RuntimeError, "Subscription one isn't signed in"):
                    method({**BOT, 'profile': 'one'})

    def test_rejection_is_per_runtime_and_profile(self):
        from unittest import mock
        self.runner.reject('codex', 'Unauthorized', 'two')
        self.assertIsNone(self.runner.rejection('codex', 'one'))
        self.assertIsNone(self.runner.rejection('claude', 'two'))
        with mock.patch.object(self.runner, 'runtime_readiness', return_value={
                'installed': True, 'authenticated': 'ready', 'detail': ''}):
            assignments = [{'bot': 'a', 'profile': 'one', 'config': {'runtime': 'codex'}},
                           {'bot': 'b', 'profile': 'two', 'config': {'runtime': 'codex'}}]
            report = self.runner.runtime_report(assignments)
            rows = self.runner.preflight(assignments, report)
        self.assertNotIn("Subscription one isn't signed in on this computer", rows[0]['problems'])
        self.assertIn("Subscription two isn't signed in on this computer", rows[1]['problems'])
        no_local = {**self.runner.config, 'default_profile': None, 'bot_profiles': {}}
        with mock.patch.object(self.runner, 'config', no_local), mock.patch.object(
                self.runner, 'runtime_readiness', return_value={
                    'installed': True, 'authenticated': 'ready', 'detail': ''}):
            mixed = [*assignments, {'bot': 'operator-login', 'config': {'runtime': 'codex'}}]
            operator_row = self.runner.preflight(mixed, self.runner.runtime_report(mixed))[-1]
            self.assertNotIn('Sign-in rejected: Unauthorized', operator_row['problems'])
        self.runner.clear_rejection('codex', 'one')
        self.assertIsNotNone(self.runner.rejection('codex', 'two'))
        self.runner.clear_rejection('codex', 'two')
        self.assertIsNone(self.runner.rejection('codex', 'two'))

    def test_turn_guard_uses_cached_state_without_live_probes(self):
        from unittest import mock
        work = {**BOT, 'profile': 'one', 'computer_label': 'Build Computer'}
        self.runner.runtime_rows = {'codex': {'profiles': {'one': {'authenticated': 'ready'}}}}
        with mock.patch.object(self.runner, 'runtime_readiness', side_effect=AssertionError('live probe')):
            for status in ('ready', 'failed', 'unknown'):
                self.runner.runtime_rows['codex']['profiles']['one']['authenticated'] = status
                self.assertEqual(self.runner.turn_profile(work).name, 'one')
            self.runner.runtime_rows = {}
            self.assertEqual(self.runner.turn_profile(work).name, 'one')
            self.runner._profile_report_cache = (0, [{'name': 'one', 'runtimes': {'codex': {'signed_in': False}}}])
            with self.assertRaisesRegex(RuntimeError, "Subscription one isn't signed in on Build Computer"):
                self.runner.turn_profile(work)
            self.runner._profile_report_cache[1][0]['runtimes']['codex']['signed_in'] = None
            self.assertEqual(self.runner.turn_profile(work).name, 'one')

    def test_fallback_uses_assigned_profile(self):
        from unittest import mock
        from runner.hosts.fake import FakeHost
        from runner.tests.test_runner_resilience import FakeClient, attempt
        root = Path(self.tmp.name)
        (root / 'emp-coo').mkdir()
        client, calls = FakeClient(), []
        primary, secondary = FakeHost(), FakeHost(replies=['fallback reply'])
        primary.fail_next_turn("You've hit your usage limit")
        def factory(work, env):
            calls.append((work['config']['runtime'], env))
            return secondary if work.get('fallback') else primary
        runner = Runner(self.runner.config, root / 'fallback', host_factory=factory, client=client,
                        push=lambda path, env=None: (0, ''))
        runner.renew_interval = 0.05
        runner.runtime_rows = {runtime: {'profiles': {'one': {'authenticated': 'ready'}}} for runtime in ('codex', 'claude')}
        work = {**attempt(), 'profile': 'one', 'computer_label': 'Build Computer'}
        work['config']['fallback'] = {'harness': 'claude', 'model': 'test-model'}
        with mock.patch.object(runner, 'runtime_readiness', side_effect=AssertionError('live probe')):
            runner.execute(work)
        completion = client.completion()
        self.assertEqual([runtime for runtime, env in calls], ['codex', 'claude'])
        self.assertEqual(calls[1][1]['HOME'], str(Path(self.one['dir']) / 'claude'))
        settings = next(iter(secondary.threads.values()))['settings']
        self.assertEqual(settings['env']['HOME'], calls[1][1]['HOME'])
        self.assertEqual(completion['outcome'], 'completed')

    def test_start_refusal_is_retryable_without_chat_and_prevents_fallback(self):
        import json
        from unittest import mock
        from runner.tests.test_runner_resilience import FakeClient, attempt
        root = Path(self.tmp.name)
        (root / 'emp-coo').mkdir()
        client, factory = FakeClient(), mock.Mock()
        runner = Runner(self.runner.config, root / 'refused', host_factory=factory,
                        client=client, push=lambda path, env=None: (0, ''))
        runner.renew_interval = 0.05
        work = {**attempt(), 'profile': 'absent', 'computer_label': 'Build Computer'}
        work['config']['fallback'] = {'harness': 'claude', 'model': 'test-model'}
        runner.execute(work)
        completion = client.completion()
        message = "Subscription absent isn't on Build Computer"
        self.assertEqual((completion['outcome'], completion['text']), ('failed', message))
        self.assertTrue(completion['retryable'])
        factory.assert_not_called()
        self.assertEqual(runner.last_heartbeat, float('-inf'))
        with runner.state.connect() as c:
            events = [(row['kind'], json.loads(row['payload'])) for row in c.execute('SELECT kind,payload FROM events')]
        self.assertIn(('diagnostic', {'text': message, 'detail': {'runtime': 'codex'}}), events)
        self.assertFalse(any(kind == 'message' for kind, payload in events))
        self.assertEqual(completion['subscription_unavailable'], {'profile': 'absent', 'runtime': 'codex', 'problem': message})

    def test_profile_probe_timeout_is_unknown_in_heartbeat_and_turn_guard(self):
        import subprocess
        from unittest import mock
        for runtime in ('codex',):
            work = {**BOT, 'profile': 'one', 'config': {'runtime': runtime}}
            with mock.patch('runner.service.shutil.which', return_value=runtime), \
                    mock.patch('runner.service.isolation.run', side_effect=subprocess.TimeoutExpired('probe', 3)), \
                    mock.patch.object(self.runner, 'codex_models', return_value=[]):
                status = self.runner.runtime_readiness(runtime, [work], profiles.select(self.runner.config, 'sales', 'one'))
            self.assertEqual(status['authenticated'], 'unknown')
            self.runner.runtime_rows = {runtime: {**status, 'profiles': {'one': status}}}
            checks = self.runner.preflight([work], self.runner.runtime_rows)
            self.assertFalse(any('signed in' in problem for problem in checks[0]['problems']))
            self.assertEqual(self.runner.turn_profile(work).name, 'one')

    def test_assigned_profile_refuses_runtimes_without_home_mapping(self):
        from unittest import mock
        from runner.tests.test_runner_resilience import FakeClient, attempt as run_attempt
        root = Path(self.tmp.name)
        (root / 'emp-coo').mkdir()
        for runtime, harness in [('cursor', 'cursor-agent')]:
            client, factory = FakeClient(), mock.Mock()
            runner = Runner(self.runner.config, root / ('unsupported-' + runtime), host_factory=factory,
                            client=client, push=lambda path, env=None: (0, ''))
            runner.renew_interval = 0.05
            work = {**run_attempt(), 'profile': 'one'}
            work['config'].update(runtime=runtime, harness=harness)
            runner.execute(work)
            result = client.completion()
            self.assertTrue(result['retryable'])
            self.assertEqual(result['text'], f"Subscription one doesn't cover {runtime}")
            self.assertEqual(result['subscription_unavailable']['runtime'], runtime)
            factory.assert_not_called()
            status = {'installed': True, 'authenticated': 'ready', 'detail': 'Signed in'}
            row = self.runner.preflight([work], {runtime: status})[0]
            self.assertFalse(row['ready'])
            self.assertIn(result['text'], row['problems'])
