"""Git used by computer maintenance has no turn credentials or repository hooks."""
import atexit
import hashlib
import os
import shutil
import tempfile
import shlex
from pathlib import Path

_SETTINGS = {'HOME', 'PATH', 'SHELL', 'USER', 'LOGNAME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'TZ', 'TERM', 'COLORTERM',
             'CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY',
             'SSL_CERT_FILE', 'SSL_CERT_DIR', 'GIT_SSL_CAINFO', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
             'https_proxy', 'http_proxy', 'no_proxy', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'PYENV_ROOT',
             'CARGO_HOME', 'RUSTUP_HOME', 'GOPATH', 'JAVA_HOME'}
_SETTINGS.add('SSH_AUTH_SOCK')
_hooks = tempfile.mkdtemp(prefix='tico-empty-hooks-')
os.chmod(_hooks, 0o555)
atexit.register(shutil.rmtree, _hooks, ignore_errors=True)
_trust = tempfile.mkdtemp(prefix='tico-git-trust-')
os.chmod(_trust, 0o755)
atexit.register(shutil.rmtree, _trust, ignore_errors=True)
PREFIX = ['git', '--no-optional-locks', '-c', 'core.hooksPath=' + _hooks, '-c', 'core.fsmonitor=false']


def process_environment(source=None):
    source = os.environ if source is None else source
    return {k: v for k, v in source.items() if k in _SETTINGS or k.startswith('LC_')}


def clean_environment(source=None):
    env = process_environment(source)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    return env


IDENTITY = ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL')


def committing_environment(source=None):
    """`environment()` for a rebase or merge the runner makes, which writes commits: the bot's identity when the turn
    set one, else Tico's. A clean environment has none, and git then refuses ("unable to auto-detect email address"),
    leaving the bot's unpushed commits stuck behind its remote."""
    source = os.environ if source is None else source
    env = environment(source)
    env.update({k: source[k] for k in IDENTITY if source.get(k)})
    env.setdefault('GIT_COMMITTER_NAME', env.get('GIT_AUTHOR_NAME') or 'Tico')
    env.setdefault('GIT_COMMITTER_EMAIL', env.get('GIT_AUTHOR_EMAIL') or 'runner@bots.tico.invalid')
    env.setdefault('GIT_AUTHOR_NAME', env['GIT_COMMITTER_NAME'])
    env.setdefault('GIT_AUTHOR_EMAIL', env['GIT_COMMITTER_EMAIL'])
    return env


def prefix(path):
    return [*PREFIX, '-c', 'safe.directory=' + str(path)]


def trusting(env, *directories):
    """`env` with a Git global config that trusts only `directories` (`safe.directory`), for a clone or fetch from
    a local repository another user owns: the computer's mirror, which the supervisor owns while the bot user
    reads it. Git does not hand `-c` settings to the upload-pack it starts for a local repository, so there the
    ownership check only sees system and global config, which a clean environment empties."""
    lines = ''.join('\tdirectory = "' + d.replace('\\', '\\\\').replace('"', '\\"') + '"\n'
                    for d in dict.fromkeys(str(d) for d in directories) if d and '\n' not in d)
    path = Path(_trust) / (hashlib.sha256(lines.encode()).hexdigest()[:20] + '.gitconfig')
    if not path.exists():
        handle, made = tempfile.mkstemp(dir=path.parent, suffix='.tmp')     # one per call: pool threads share a pid
        with os.fdopen(handle, 'w') as out:
            out.write('[safe]\n' + lines)
        os.chmod(made, 0o644)
        os.replace(made, path)
    return {**env, 'GIT_CONFIG_GLOBAL': str(path)}


def machine_environment(source=None):
    """Keep trusted computer settings from system and global Git config, without includes."""
    from . import isolation
    import subprocess
    env = clean_environment(source)
    wrapper = str(Path(__file__).parent / 'credential_bin')
    env['PATH'] = os.pathsep.join(p for p in env.get('PATH', os.defpath).split(os.pathsep) if p != wrapper)
    trusted = process_environment(source)
    trusted['PATH'] = env['PATH']
    # Read outside all checkouts, without includes or repository config. Preserve
    # explicit machine config paths when the owner supplied them.
    for key in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM'):
        if key in (os.environ if source is None else source):
            trusted[key] = (os.environ if source is None else source)[key]
    entries = []
    for scope in ('--system', '--global'):
        try:
            result = isolation.run([*PREFIX, 'config', scope, '--no-includes', '--null', '--get-regexp',
                                    r'^(credential\..*|url\..*\.(insteadof|pushinsteadof)|core\.sshcommand|http\.(.*\.)?(proxy|sslcainfo)|user\.(name|email))$'],
                                   cwd='/', env=trusted, capture_output=True, text=True, timeout=10)
            entries.extend(entry.split('\n', 1) for entry in result.stdout.split('\0') if '\n' in entry)
        except (OSError, subprocess.SubprocessError):
            continue
    gh = shutil.which('gh', path=env['PATH'])
    if gh and not any(key.lower() == 'credential.https://github.com.helper' for key, _ in entries):
        entries.append(['credential.https://github.com.helper', '!' + shlex.quote(gh) + ' auth git-credential'])
    entries.insert(0, ['credential.helper', ''])
    env['GIT_CONFIG_COUNT'] = str(len(entries))
    for i, (key, value) in enumerate(entries):
        env['GIT_CONFIG_KEY_' + str(i)] = key
        env['GIT_CONFIG_VALUE_' + str(i)] = value
    ssh = next((value for key, value in reversed(entries) if key.lower() == 'core.sshcommand'), 'ssh')
    env.update(GIT_SSH_COMMAND=ssh, GIT_TERMINAL_PROMPT='0')
    return env


def environment(source=None):
    from . import git_credentials
    source = os.environ if source is None else source
    env = clean_environment(source) if 'GIT_CONFIG_COUNT' in source else machine_environment(source)
    # These are the per-repository helper settings, never vault grants or the runner token.
    keys = set(git_credentials.environment('')) | {git_credentials.credential_socket.SOCKET_ENV, 'HUB_TOKEN'}
    keys.update(k for k in source if k.startswith('TICO_GITHUB_'))
    keys.update(k for k in source if k.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')))
    keys.add('GIT_SSH_COMMAND')
    env.update({k: v for k, v in source.items() if k in keys})
    env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    return env
