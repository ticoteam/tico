"""A run cannot move to another login when its local assignments change mid-flight."""
import json
import pytest

from runner import profiles
from runner.hosts.fake import FakeHost
from runner.service import Runner
from runner.tests.test_runner_resilience import FakeClient, attempt


@pytest.fixture
def execution(tmp_path, monkeypatch):
    # Fake hosts and an empty operator home keep provider credentials and networks out of scope.
    monkeypatch.setenv('HOME', str(tmp_path / 'operator'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'operator' / '.codex'))
    (tmp_path / 'emp-coo').mkdir()
    client = FakeClient()
    runner = Runner({'url': 'https://runner.example', 'token': 'machine',
                     'projects_dir': str(tmp_path), 'capacity': 1,
                     'profiles': {'one': {'dir': str(tmp_path / 'one')},
                                  'two': {'dir': str(tmp_path / 'two')}},
                     'default_profile': 'one'}, tmp_path / 'state', client=client,
                    push=lambda path, env=None: (0, ''))
    runner.renew_interval = 0.05
    monkeypatch.setattr(runner, 'publish', lambda *args: None)
    monkeypatch.setattr(runner, 'refresh_workspace', lambda *args: None)
    monkeypatch.setattr(runner, 'refresh_product_files', lambda *args: None)
    monkeypatch.setattr(runner, 'mcp_for_run', lambda *args: [])
    monkeypatch.setattr(runner, 'runtime_readiness', lambda *args: pytest.fail('live sign-in probe'))
    yield runner, client, tmp_path
    runner.pool.shutdown(wait=True)


@pytest.mark.parametrize('selection', ['assigned'])
def test_binding_is_shared_by_environment_host_usage_and_resume(execution, monkeypatch, selection):
    runner, client, root = execution
    work = attempt()
    if selection == 'assigned':
        work['profile'] = 'one'
    elif selection == 'operator':
        runner.config['default_profile'] = None
    homes, seen = [], []
    original_environment = runner.environment

    def environment(row):
        # This happens after execute selects a subscription but before building its environment.
        runner.config['default_profile'] = 'two'
        runner.config['profiles']['one']['dir'] = str(root / 'replacement')
        runner.assignments_seen = [{'bot': 'coo', 'profile': 'two'}]
        return original_environment(row)

    def host(row, env):
        runner.make_host(row, env)  # Inspect the real Codex host's config-home lookup, without starting it.
        seen.append((row, env))
        return FakeHost(replies=['done'])

    monkeypatch.setattr(runner, 'environment', environment)
    monkeypatch.setattr(runner, 'host_factory', host)
    monkeypatch.setattr('runner.hosts.codex.mcp_disable_config', lambda home=None: homes.append(home) or {})
    runner.execute(work)
    result = client.completion()
    expected_name = None if selection == 'operator' else 'one'
    expected_home = root / ('operator/.codex' if selection == 'operator' else 'one/codex')
    assert result['outcome'] == 'completed'
    assert result.get('profile_used') == expected_name
    assert result['usage']['profile_used'] == expected_name
    assert seen[0][1]['CODEX_HOME'] == str(expected_home)
    assert homes == ([None] if selection == 'operator' else [expected_home])
    assert '_subscription_profile' not in work
    with runner.state.connect() as connection:
        payload = connection.execute('SELECT payload FROM attempts').fetchone()[0]
        keys = [row[0] for row in connection.execute('SELECT conversation FROM sessions')]
    assert '_subscription_profile' not in json.loads(payload)
    assert (':profile:one:' in keys[0]) == (selection != 'operator')

    # A later run uses the new assignment; it must not resume the earlier profile's session.
    monkeypatch.setattr(runner, 'environment', original_environment)
    next_host = FakeHost(replies=['next'])
    monkeypatch.setattr(runner, 'host_factory', lambda *args: next_host)
    runner.execute({**work, 'id': 'att-2'})
    assert next_host.resumes == []


@pytest.mark.parametrize('selection', ['operator'])
def test_fallback_keeps_the_initial_profile_and_usage(execution, monkeypatch, selection):
    runner, client, root = execution
    primary, secondary = FakeHost(), FakeHost(replies=['fallback'])
    primary.fail_next_turn("You've hit your usage limit")
    work, calls = attempt(), []
    work['config']['fallback'] = {'harness': 'claude', 'model': 'test-model'}
    if selection == 'assigned':
        work['profile'] = 'one'
    elif selection == 'operator':
        runner.config['default_profile'] = None

    def host(row, env):
        calls.append((row['config']['runtime'], env))
        if not row.get('fallback'):
            runner.config['default_profile'] = 'two'
            runner.config['profiles']['one']['dir'] = str(root / 'replacement')
            return primary
        return secondary

    monkeypatch.setattr(runner, 'host_factory', host)
    runner.execute(work)
    result = client.completion()
    name = None if selection == 'operator' else 'one'
    assert result['outcome'] == 'completed'
    assert [runtime for runtime, _ in calls] == ['codex', 'claude']
    assert calls[1][1]['HOME'] == str(root / ('operator' if selection == 'operator' else 'one/claude'))
    assert result.get('profile_used') == name
    assert all(segment['profile_used'] == name for segment in result['usage']['segments'])


def test_rejection_fingerprints_the_login_that_failed(execution, monkeypatch):
    runner, client, root = execution
    original = profiles.select(runner.config, 'coo')
    host = FakeHost()
    host.fail_next_turn('Unauthorized: invalid API key')

    def factory(row, env):
        runner.config['default_profile'] = 'two'
        runner.config['profiles']['one']['dir'] = str(root / 'replacement')
        return host

    monkeypatch.setattr(runner, 'host_factory', factory)
    runner.execute(attempt())
    assert client.completion()['outcome'] == 'failed'
    assert ('codex', 'one', runner.credential_fingerprint(original)) in runner._rejected
    assert runner.rejection('codex', original) is not None
    assert not any(key[:2] == ('codex', 'two') for key in runner._rejected)
    # Rebinding the slot does not incorrectly quarantine its new directory.
    assert runner.rejection('codex', 'one') is None


def test_pinned_fallback_still_honors_sign_in_refusal(execution, monkeypatch):
    runner, client, _ = execution
    primary = FakeHost()
    primary.fail_next_turn("You've hit your usage limit")
    work = {**attempt(), 'profile': 'one'}
    work['config']['fallback'] = {'harness': 'claude', 'model': 'test-model'}
    calls = []

    def factory(row, env):
        calls.append(row['config']['runtime'])
        runner.config['default_profile'] = 'two'
        runner.runtime_rows = {'claude': {'profiles': {'one': {'authenticated': 'missing'}}}}
        return primary

    monkeypatch.setattr(runner, 'host_factory', factory)
    runner.execute(work)
    result = client.completion()
    assert calls == ['codex']
    assert result['outcome'] == 'failed'
    assert result['subscription_unavailable']['profile'] == 'one'
    assert result['subscription_unavailable']['runtime'] == 'claude'
    assert result['retryable'] is True


def test_old_binding_cannot_overwrite_or_clear_new_binding_rejection(execution):
    runner, _, root = execution
    old = profiles.select(runner.config, 'coo')
    runner.config['profiles']['one']['dir'] = str(root / 'replacement')
    runner.reject('codex', 'Unauthorized new login', 'one')
    current = runner.rejection('codex', 'one')
    assert current is not None
    assert runner.rejection('codex', old) is None
    assert runner.rejection('codex', 'one') is current
    runner.reject('codex', 'Unauthorized old login', old)
    assert runner.rejection('codex', old) is not None
    assert runner.rejection('codex', 'one') is current
    runner.clear_rejection('codex', 'one')
    assert runner.rejection('codex', 'one') is None
    assert runner.rejection('codex', old) is None
