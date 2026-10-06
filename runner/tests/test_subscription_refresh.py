"""Allowance reads use fake transports and temporary profiles, never real accounts."""
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from runner.profiles import Profile
from runner.subscription_refresh import read_codex, Refreshes


class Host:
    calls = []
    response = {}
    last = None
    def __init__(self, **kwargs):
        Host.last = self
        self.kwargs, self.stopped = kwargs, False
    def start(self):
        pass
    def request(self, method, **kwargs):
        Host.calls.append(method)
        return Host.response
    def stop(self):
        self.stopped = True


class Refresh(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Profile('sample', self.temp.name)
        Host.calls = []
        Host.response = {'rateLimits': {'limitId': 'codex', 'secondary': {
            'windowDurationMins': 10080, 'usedPercent': 42,
            'resetsAt': datetime.now(timezone.utc).timestamp() + 1000}}}

    @patch('runner.subscription_refresh.shutil.which', return_value='/fake/codex')
    def test_account_read_only_and_isolated_env(self, _):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-key', 'CODEX_API_KEY': 'fake-key'}):
            result = read_codex(self.profile, Host)
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(result['weekly']['used_percent'], 42)
        self.assertEqual(Host.calls, ['account/rateLimits/read'])
        self.assertTrue(Host.last.stopped)
        self.assertNotIn('OPENAI_API_KEY', Host.last.kwargs['env'])
        self.assertEqual(Host.last.kwargs['env']['CODEX_HOME'], str(self.profile.home('codex')))

    @patch('runner.subscription_refresh.shutil.which', return_value='/fake/codex')
    def test_exception_is_redacted_and_process_stopped(self, _):
        with patch.object(Host, 'request', side_effect=RuntimeError('fake-secret')):
            self.assertEqual(read_codex(self.profile, Host), {'state': 'failed'})
        self.assertTrue(Host.last.stopped)

    @patch('runner.subscription_refresh.shutil.which', return_value='/fake/codex')
    def test_shutdown_cancels_before_any_account_read(self, _):
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual(read_codex(self.profile, Host, cancelled), {'state': 'failed'})
        self.assertEqual(Host.calls, [])
        self.assertTrue(Host.last.stopped)

    def test_preflight_runner_lazily_keeps_weekly_reports(self):
        from runner.service import Runner
        runner = Runner.__new__(Runner)
        reports = runner.weekly_usage
        self.assertIs(runner.weekly_usage, reports)
        self.assertEqual(reports.attach([]), [])

    def test_missing_profile_never_uses_default_and_one_worker(self):
        class Client:
            def __init__(self): self.posts = []
            def get(self, _): return {'refreshes': [{'id': 'r', 'profile': 'missing', 'runtime': 'codex'}]}
            def post(self, path, body): self.posts.append(body)
        runner = SimpleNamespace(config={'profiles': {'sample': {'dir': self.temp.name}}, 'default_profile': 'sample'}, client=Client())
        manager = Refreshes(runner)
        with patch('runner.subscription_refresh.read_codex') as read:
            manager.poll()
            manager.thread.join(1)
            manager.polled = -15
            manager.poll()
            self.assertEqual(runner.client.posts[0]['state'], 'unavailable')
            read.assert_not_called()
