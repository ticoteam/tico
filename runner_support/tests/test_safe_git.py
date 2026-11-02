"""Trusted Computer config survives maintenance without repository programs."""
import os
import subprocess
from runner import safe_git


def test_system_and_global_settings_are_kept_without_includes(tmp_path, monkeypatch):
    system = tmp_path / 'system.gitconfig'
    global_config = tmp_path / 'global.gitconfig'
    included = tmp_path / 'included.gitconfig'
    included.write_text('[credential]\nhelper = forbidden-include\n')
    system.write_text('[credential]\nhelper = system-helper\n'
                      '[url "ssh://git@example.com/"]\ninsteadOf = https://example.com/\n'
                      '[http]\nproxy = http://proxy.example.com\nsslCAInfo = /example/ca.pem\n'
                      '[user]\nname = Sam\nemail = sam@example.com\n'
                      '[include]\npath = ' + str(included) + '\n')
    global_config.write_text('[credential]\nhelper = global-helper\n'
                             '[core]\nsshCommand = ssh -i /example/key\n'
                             '[user]\nname = Ana\n')
    env = safe_git.machine_environment({**os.environ, 'GIT_CONFIG_SYSTEM': str(system),
                                        'GIT_CONFIG_GLOBAL': str(global_config)})
    entries = [(env['GIT_CONFIG_KEY_' + str(i)], env['GIT_CONFIG_VALUE_' + str(i)])
               for i in range(int(env['GIT_CONFIG_COUNT']))]
    assert ('credential.helper', 'system-helper') in entries
    assert ('credential.helper', 'global-helper') in entries
    assert not any(value == 'forbidden-include' for key, value in entries)
    assert env['GIT_SSH_COMMAND'] == 'ssh -i /example/key'
    for key, value in [('http.proxy', 'http://proxy.example.com'), ('http.sslcainfo', '/example/ca.pem'),
                       ('user.name', 'Ana'), ('user.email', 'sam@example.com'),
                       ('url.ssh://git@example.com/.insteadof', 'https://example.com/')]:
        assert (key, value) in entries
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'core.sshCommand', 'forbidden-repo'], check=True)
    result = subprocess.run([*safe_git.prefix(repo), '-C', str(repo), 'config', '--get', 'core.sshCommand'],
                            env=env, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == 'ssh -i /example/key'


def _mirror(tmp_path, name='mirror "one".git'):
    """A bare repository with one commit, standing in for the computer's mirror."""
    git = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@acme.example',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@acme.example'}
    seed = tmp_path / 'seed'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(seed)], check=True, env=git)
    subprocess.run(['git', '-C', str(seed), 'commit', '-q', '--allow-empty', '-m', 'one'], check=True, env=git)
    mirror = tmp_path / name
    subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(mirror)], check=True, env=git)
    return mirror


def test_a_mirror_another_user_owns_is_cloned_and_fetched_with_the_trust_file(tmp_path):
    """The bot user clones the supervisor's mirror (file://). Git's test switch makes every repository look owned by
    someone else; where it reaches the upload-pack git starts (Linux), `-c safe.directory` alone is refused there."""
    mirror = _mirror(tmp_path)
    env = {**safe_git.clean_environment(), 'GIT_TEST_ASSUME_DIFFERENT_OWNER': '1'}
    local = [*safe_git.PREFIX, '-c', 'safe.directory=' + str(mirror), '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always']
    refused = subprocess.run([*local, 'clone', '-q', mirror.as_uri(), str(tmp_path / 'flag-only')], env=env,
                             capture_output=True, text=True)
    if refused.returncode == 0:
        import pytest
        pytest.skip("this git applies -c safe.directory to a local upload-pack, or ignores the ownership test switch")
    assert 'dubious ownership' in refused.stderr
    trusted = safe_git.trusting(env, mirror)
    clone = tmp_path / 'clone'
    done = subprocess.run([*local, 'clone', '-q', mirror.as_uri(), str(clone)], env=trusted, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    # The checkout itself is trusted on the command line, as runner/repositories.py does: git checks it in-process.
    done = subprocess.run([*local, '-c', 'safe.directory=' + str(clone), '-C', str(clone), 'fetch', '-q', '--', mirror.as_uri(), '+refs/heads/main:refs/remotes/origin/main'],
                          env=trusted, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_trust_file_names_only_the_given_directories_and_anyone_can_read_it(tmp_path):
    env = safe_git.trusting({'PATH': '/bin'}, '/m/a "b".git', '/m/a "b".git', '/m/c\\d.git', 'bad\nline', '')
    path = env['GIT_CONFIG_GLOBAL']
    assert env['PATH'] == '/bin' and oct(os.stat(path).st_mode & 0o777) == '0o644'
    listed = subprocess.run(['git', 'config', '--file', path, '--get-all', 'safe.directory'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert listed.stdout.splitlines() == ['/m/a "b".git', '/m/c\\d.git']
    assert safe_git.trusting({}, '/m/a "b".git', '/m/c\\d.git')['GIT_CONFIG_GLOBAL'] == path     # the same file, reused
