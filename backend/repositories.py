"""Team repository selection, bot grants and computer base-clone access."""
import base64
import json
import time
import threading
import logging
import uuid
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, field_validator

from . import hubdb as H
from . import read_cache
from .auth import validate_identity
from .store import Problem

SETTINGS = 'repos_new_bot_default'


def metadata(c, key):
    row = read_cache.metadata(c, key)
    try:
        value = json.loads(row[0]) if row else {}
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def save_metadata(c, key, value):
    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
              (key, json.dumps(value)))


def migrate(c):
    if metadata(c, 'repositories-access-migrated').get('done'):
        return
    for bot, repos in metadata(c, 'github-extra-repos').items():
        if not repos:
            continue
        row = c.execute('SELECT config_json FROM bot_config WHERE bot=?', (bot,)).fetchone()
        if row:
            config = json.loads(row[0] or '{}')
            config['repo_access_mode'] = 'chosen'
            c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (json.dumps(config), bot))
        for repo in repos:
            c.execute('INSERT INTO repositories(id,full_name,enabled,updated) VALUES(?,?,1,?) '
                      'ON CONFLICT(full_name) DO UPDATE SET enabled=1', (uuid.uuid4().hex, repo, H.now()))
            c.execute("INSERT INTO bot_repo_access VALUES(?,?,'write') ON CONFLICT(bot,full_name) DO UPDATE SET access='write'",
                      (bot, repo))

    save_metadata(c, 'repositories-access-migrated', {'done': True})


def unreachable(c):
    marks = metadata(c, 'repositories-confirmed-missing')
    return {name for name, stamp in marks.items()
            if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and time.time() - stamp < 300}


def reachable(c, names):
    marks = metadata(c, 'repositories-confirmed-missing')
    changed = False
    for name in names:
        if name.lower() in marks:
            marks.pop(name.lower())
            changed = True
        row = c.execute('SELECT reachable FROM repositories WHERE full_name=? COLLATE NOCASE', (name,)).fetchone()
        if row is not None and row['reachable'] != 1:
            c.execute('UPDATE repositories SET reachable=1 WHERE full_name=? COLLATE NOCASE', (name,))
            changed = True
    if changed:
        save_metadata(c, 'repositories-confirmed-missing', marks)
    return changed


def mark_reachable(store, names):
    """Persist only a changed reachability state; routine token minting often finds no change."""
    with store.read() as c:
        marks = metadata(c, 'repositories-confirmed-missing')
        needs_write = any(name.lower() in marks for name in names)
        if not needs_write:
            for name in names:
                row = c.execute('SELECT reachable FROM repositories WHERE full_name=? COLLATE NOCASE', (name,)).fetchone()
                if row is not None and row['reachable'] != 1:
                    needs_write = True
                    break
    if not needs_write:
        return False
    with store.transaction() as c:
        return reachable(c, names)


def _botops():
    from .github_app import BOTOPS
    return BOTOPS


def can_create_repositories(c, bot):
    # Keep this grant outside editable bot definitions; bots cannot grant it to themselves.
    return bot == _botops() or metadata(c, 'repository-creation-grants').get(bot) is True


_NOCASE = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


def nocase(name):
    """A repository name as the `COLLATE NOCASE` columns compare it: ASCII letters folded only."""
    return str(name).translate(_NOCASE)


class Reads:
    """What `access` reads for `bots`, read once for all of them: one query per kind, so a computer
    hosting many bots costs about what one bot does. Rows the answer may not need are read when asked."""

    def __init__(self, c, bots):
        from .shared_bots import _json, source_of
        self.c = c
        bots = list(dict.fromkeys(bots))
        self.configs = self._rows('SELECT bot,config_json,repo FROM bot_config WHERE bot IN ({})', bots)
        sources = {source_of(_json(row['config_json'])) for row in self.configs.values()} - {''} - set(self.configs)
        self.configs.update(self._rows('SELECT bot,config_json,repo FROM bot_config WHERE bot IN ({})', sorted(sources)))
        self.chosen = {bot: [] for bot in bots}
        for part in H.chunks(bots):
            for r in c.execute('SELECT bot,full_name,access FROM bot_repo_access WHERE bot IN (%s) ORDER BY bot,full_name'
                               % ','.join('?' * len(part)), part):
                self.chosen[r['bot']].append({'full_name': r['full_name'], 'access': r['access']})
        self.missing = unreachable(c)
        self.creation = metadata(c, 'repository-creation-grants')
        self._enabled = self._bot_repos = self._all = None

    def _rows(self, sql, keys):
        out = {}
        for part in H.chunks(keys):
            out.update((r[0], r) for r in self.c.execute(sql.format(','.join('?' * len(part))), part))
        return out

    def enabled(self, name):
        """The enabled repository row (full_name, default_branch, setup_command) by this name, or None."""
        if self._enabled is None:
            self._enabled = {nocase(r['full_name']): r for r in self.c.execute(
                'SELECT full_name,default_branch,setup_command FROM repositories WHERE enabled=1')}
        return self._enabled.get(nocase(name))

    def all_mode(self):
        """Every bot's own repository, and the enabled shared ones, for a bot granted all of them."""
        if self._all is None:
            self._all = ([r[0] for r in self.c.execute('SELECT repo FROM bot_config')],
                         [r[0] for r in self.c.execute('SELECT full_name FROM repositories WHERE enabled=1 AND bot_repo=0')])
        return self._all


def access(c, bot, org, reads=None):
    """`reads` (a `Reads` covering `bot`) when the caller answers for many bots."""
    from .github_app import repo_of
    from .shared_bots import _json, source_of
    reads = reads or Reads(c, [bot])
    row = reads.configs.get(bot)
    if not row:
        raise Problem('not_found', 'No such bot', 404)
    config = json.loads(row['config_json'] or '{}')
    if config.get('assignment_branch'):
        # Assignment code is a private local checkout. Its runner already has the
        # source role's same-machine clone access; never mint or advertise a source
        # role GitHub grant for the temporary actor.
        return {'mode': 'chosen', 'all_access': 'read', 'chosen': [], 'effective': [],
                'create_repositories': False}
    mode, all_access = config.get('repo_access_mode', 'own'), config.get('repo_all_access', 'write')
    source = source_of(_json(row['config_json'])) or bot
    own_row = reads.configs.get(source)
    own = repo_of(own_row['repo'] if own_row else '', org)
    chosen = [dict(r) for r in reads.chosen[bot]]
    grants = {}
    if mode == 'all':
        repos, shared = reads.all_mode()
        bot_repos = {str(repo_of(repo, org) or '').lower() for repo in repos}
        grants = {name.lower(): {'full_name': name, 'access': all_access} for name in shared
                  if name.lower() not in bot_repos and not name.split('/')[1].lower().startswith('bot-')}
    elif mode == 'chosen':
        grants = {r['full_name'].lower(): r for r in chosen if reads.enabled(r['full_name'])}
    if own:
        grants[own.lower()] = {'full_name': own, 'access': 'write'}
    missing = reads.missing
    effective = [r for _, r in sorted(grants.items()) if (r['full_name'].lower() not in missing or r['full_name'].lower() == str(own).lower()) and org and r['full_name'].split('/')[0].lower() == org.lower()]
    return {'mode': mode, 'all_access': all_access, 'chosen': chosen, 'effective': effective,
            'create_repositories': bot == _botops() or reads.creation.get(bot) is True}


def set_access(c, bot, body, org, actor, legacy=False, team_list=False):
    from .github_app import repo_of, save_extra_repos
    before = access(c, bot, org)
    if body.create_repositories is not None:
        from .github_app import BOTOPS
        if not team_list or not H.is_human(actor):
            raise Problem('forbidden', 'Only an Owner or admin grants repository creation', 403)
        if bot == BOTOPS and not body.create_repositories:
            raise Problem('forbidden', 'BotOps retains its built-in repository creation access', 403)
    wanted = {}
    for grant in body.chosen or []:
        name = repo_of(grant.full_name, org)
        if not name or name.split('/')[0].lower() != org.lower():
            raise Problem('github_repo', 'Choose a repository in the connected organization', 422)
        existing = c.execute('SELECT enabled,bot_repo FROM repositories WHERE full_name=?', (name,)).fetchone()
        if legacy and not team_list and (not existing or not existing['enabled']):
            raise Problem('github_repo', 'Ask an Owner or admin to tick it in Settings > Repositories', 422)
        previous = {r['full_name'].lower(): r['access'] for r in before['chosen']}
        bot_repo = name.split('/')[1].lower().startswith('bot-') or any(
            str(repo_of(r[0], org) or '').lower() == name.lower() for r in c.execute('SELECT repo FROM bot_config'))
        if (bot_repo or existing and existing['bot_repo']) and not team_list and (name.lower() not in previous or previous[name.lower()] == 'read' and grant.access == 'write'):
            raise Problem('forbidden', 'An Owner or admin chooses bot repositories', 403)
        if legacy and team_list:
            c.execute('INSERT INTO repositories(id,full_name,enabled,updated) VALUES(?,?,1,?) '
                      'ON CONFLICT(full_name) DO UPDATE SET enabled=1', (uuid.uuid4().hex, name, H.now()))
        elif not c.execute('SELECT 1 FROM repositories WHERE full_name=?', (name,)).fetchone():
            raise Problem('github_repo', 'Choose a repository from the team list', 422)
        wanted[name.lower()] = (name, grant.access)
    config = json.loads(c.execute('SELECT config_json FROM bot_config WHERE bot=?', (bot,)).fetchone()[0] or '{}')
    config.update(repo_access_mode=body.mode, repo_all_access=body.all_access or before['all_access'])
    c.execute('UPDATE bot_config SET config_json=? WHERE bot=?', (json.dumps(config), bot))
    if body.chosen is not None:
        c.execute('DELETE FROM bot_repo_access WHERE bot=?', (bot,))
        c.executemany('INSERT INTO bot_repo_access VALUES(?,?,?)', [(bot, name, level) for name, level in wanted.values()])
    if body.create_repositories is not None:
        grants = metadata(c, 'repository-creation-grants')
        if body.create_repositories:
            grants[bot] = True
        else:
            grants.pop(bot, None)
        save_metadata(c, 'repository-creation-grants', grants)
    after = access(c, bot, org)
    save_extra_repos(c, bot, [r['full_name'] for r in after['chosen'] if r['access'] == 'write'] if body.mode == 'chosen' else [])
    H.event(c, actor, 'bot.repos_changed', bot, {'before': before, 'after': after})
    return after


def sync(service):
    with service.repository_sync_lock:
        return _sync(service)


def _sync(service):
    if service.repository_stop.is_set():
        return 'stopped'
    row = service.row()
    if not row or not service.installation():
        return 'not_installed'
    if service.repository_stop.is_set():
        return 'stopped'
    token, _ = service.mint(None, {'contents': 'read', 'metadata': 'read'})
    headers = {'Authorization': 'Bearer ' + token}
    repos, page = [], 1
    while True:
        if service.repository_stop.is_set():
            return 'stopped'
        response = service._call('GET', '/installation/repositories', params={'per_page': 100, 'page': page}, headers=headers)
        if response.status_code >= 300:
            raise Problem('github_repositories', 'GitHub could not list repositories; retry Refresh', 502)
        batch = response.json()['repositories']
        repos.extend(r for r in batch if r['full_name'].split('/')[0].lower() == row['org'].lower())
        if len(batch) < 100:
            break
        page += 1
    if service.repository_stop.is_set():
        return 'stopped'
    with service.store.read() as c:
        from .github_app import repo_of
        own = {str(repo_of(r[0], row['org']) or '').lower() for r in c.execute('SELECT repo FROM bot_config')}
        previous = {r['full_name'].lower(): dict(r) for r in c.execute('SELECT * FROM repositories')}
        overrides = {name for name, r in previous.items() if r['setup_source'] == 'settings'}
    for repo in repos:
        if service.repository_stop.is_set():
            return 'stopped'
        old = previous.get(repo['full_name'].lower(), {})
        repo['setup_command'], repo['setup_source'] = old.get('setup_command'), old.get('setup_source')
        if not old.get('enabled') or repo['full_name'].lower() in overrides:
            continue
        absent_files = 0
        for filename in ('tico.json', 'conductor.json'):
            if service.repository_stop.is_set():
                return 'stopped'
            try:
                response = service._call('GET', f"/repos/{repo['full_name']}/contents/{filename}", headers=headers,
                                         params={'ref': repo['default_branch']} if repo.get('default_branch') else {})
            except Problem:
                break
            if response.status_code == 404:
                absent_files += 1
                continue
            if response.status_code >= 300:
                break
            try:
                data = response.json()
                if data.get('size', 0) > 1024 * 1024:
                    continue
                config = json.loads(base64.b64decode(data['content']))
                command = config.get('setup') if filename == 'tico.json' else (config.get('scripts') or {}).get('setup')
                if isinstance(command, str) and len(command.encode('utf-8')) <= 4096:
                    repo['setup_command'], repo['setup_source'] = command, filename
                    break
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        if absent_files == 2:
            repo['setup_command'], repo['setup_source'] = None, None
    if service.repository_stop.is_set():
        return 'stopped'
    with service.store.transaction() as c:
        c.execute('UPDATE repositories SET reachable=0')
        for repo in repos:
            name = repo['full_name']
            c.execute('INSERT INTO repositories(id,full_name,bot_repo,default_branch,setup_command,setup_source,reachable,last_seen,updated) '
                      'VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(full_name) DO UPDATE SET bot_repo=excluded.bot_repo,'
                      'default_branch=excluded.default_branch,reachable=1,last_seen=excluded.last_seen,updated=excluded.updated,'
                      "setup_command=CASE WHEN repositories.setup_source='settings' THEN repositories.setup_command ELSE excluded.setup_command END,"
                      "setup_source=CASE WHEN repositories.setup_source='settings' THEN 'settings' ELSE excluded.setup_source END",
                      (uuid.uuid4().hex, name, int(name.split('/')[1].lower().startswith('bot-') or name.lower() in own),
                       repo.get('default_branch'), repo['setup_command'], repo['setup_source'], H.now(), H.now()))
        save_metadata(c, 'repositories-reachability-verified', {'done': True})
        save_metadata(c, 'repositories-confirmed-missing', {r['full_name'].lower(): time.time() for r in c.execute('SELECT full_name FROM repositories WHERE reachable=0')})
        save_metadata(c, 'repositories-archived', {r['full_name'].lower(): bool(r.get('archived')) for r in repos})
        save_metadata(c, 'repositories-sizes', {r['full_name'].lower(): r.get('size', 0) for r in repos if r.get('size')})
        save_metadata(c, 'repositories-synced', {'day': H.now()[:10]})
    repository_health(service)
    return 'synced'


def repository_health(service):
    from .health import note_github_token
    from .github_app import repo_of
    with service.store.read() as c:
        row = service.row(c)
        org = row['org'] if row else ''
        own = {str(repo_of(r[0], org) or '').lower() for r in c.execute('SELECT repo FROM bot_config')}
        confirmed = unreachable(c)
        missing = [r['full_name'] + ' is not reachable' for r in c.execute(
            'SELECT full_name,enabled FROM repositories WHERE reachable=0 ORDER BY full_name')
            if r['full_name'].lower() in confirmed and r['enabled'] and r['full_name'].lower() not in own]
    note_github_token(service.store, '; '.join(missing) or None,
                      'Refresh Settings > Repositories or check the GitHub App installation.')


def queue_sync(service, refresh=False):
    """One worker per connection; a burst requests at most one follow-up sync."""
    with service.repository_queue_lock:
        if service.repository_stop.is_set():
            return
        service.repository_sync_wanted = True
        service.repository_refresh_wanted |= refresh
        if service.repository_running:
            return
        def work():
            while True:
                with service.repository_queue_lock:
                    if service.repository_stop.is_set() or not service.repository_sync_wanted:
                        service.repository_running = False
                        return
                    service.repository_sync_wanted = False
                    refresh_now = service.repository_refresh_wanted
                    service.repository_refresh_wanted = False
                try:
                    if refresh_now:
                        with service.lock:
                            service.cache.clear()
                            service.live.clear()
                        service.installation(refresh=True)
                    result = sync(service)
                    service.repository_sync_failures = 0 if result == 'synced' else 1
                    service.repository_sync_attempt = 0 if result == 'synced' else time.time()
                except Exception:
                    service.repository_sync_failures += 1
                    service.repository_sync_attempt = time.time()
                    logging.getLogger('tico.repositories').warning('Repository refresh failed; retrying later')
        service.repository_running = True
        service.repository_worker = threading.Thread(target=work, daemon=True)
        service.repository_worker.start()


def stop_sync(service):
    service.repository_stop.set()
    if service.repository_worker:
        service.repository_worker.join(timeout=2)


def daily(service):
    if service.repository_running or service.repository_stop.is_set():
        return
    with service.store.read() as c:
        if metadata(c, 'repositories-synced').get('day') == H.now()[:10] and metadata(c, 'repositories-reachability-verified').get('done'):
            return
    delay = 300 if service.repository_sync_failures <= 1 else 3600
    if service.repository_sync_attempt and time.time() - service.repository_sync_attempt < delay:
        return
    queue_sync(service)


def runner_repos(c, runner_id, org):
    result = {}
    sizes = metadata(c, 'repositories-sizes')
    hosted = c.execute("SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot WHERE a.runner_id=? AND b.state='active'",
                       (runner_id,)).fetchall()
    reads = Reads(c, [row[0] for row in hosted])
    for row in hosted:
        for grant in access(c, row[0], org, reads)['effective']:
            repo = reads.enabled(grant['full_name'])
            if not repo:
                continue
            key = repo['full_name'].lower()
            entry = result.setdefault(key, {**dict(repo), 'bots': [], 'access': 'read'})
            if key in sizes:
                entry['size_kb'] = sizes[key]
            entry['bots'].append(row[0])
            if grant['access'] == 'write':
                entry['access'] = 'write'
    return {'repositories': [result[k] for k in sorted(result)]}


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Grant(Contract):
    full_name: str
    access: Literal['read', 'write'] = 'write'


class RepoAccessUpdate(Contract):
    create_repositories: bool | None = None
    mode: Literal['own', 'all', 'chosen']
    all_access: Literal['read', 'write'] | None = None
    chosen: list[Grant] | None = None


class RepoUpdate(Contract):
    enabled: bool | None = None
    setup_command: str | None = None

    @field_validator('setup_command')
    @classmethod
    def command_size(cls, value):
        if value is not None and len(value.encode('utf-8')) > 4096:
            raise ValueError('Setup command must fit within 4 KB')
        return value


class RepoSettings(Contract):
    new_bot_default: Literal['own', 'all']


def install(app, store, service):
    def human(request, change=False):
        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
        if who.role not in ('human', 'owner') or (change and who.role != 'owner' and not app.state.auth.bot_admin(who)):
            raise Problem('forbidden', 'Only an owner or admin changes repository access' if change else 'A teammate may read repositories', 403)
        return who

    def listing():
        with store.read() as c:
            repos = [dict(r) for r in c.execute('SELECT full_name,enabled,bot_repo,default_branch,setup_command,setup_source,reachable,last_seen FROM repositories ORDER BY full_name')]
            archived = metadata(c, 'repositories-archived')
            for r in repos:
                r['archived'] = archived.get(r['full_name'].lower())
                for key in ('enabled', 'bot_repo', 'reachable'):
                    r[key] = bool(r[key])
            return {'repositories': repos, 'new_bot_default': metadata(c, SETTINGS).get('new_bot_default', 'own'),
                    'github_connected': bool(service.row(c))}

    @app.get('/api/v2/repositories')
    def repo_list(request: Request):
        human(request)
        return listing()

    @app.post('/api/v2/repositories/refresh')
    def refresh(request: Request):
        human(request, True)
        sync(service)
        return listing()

    @app.put('/api/v2/repositories/settings')
    def settings(request: Request, body: RepoSettings):
        who = human(request, True)
        with store.transaction() as c:
            before = metadata(c, SETTINGS)
            save_metadata(c, SETTINGS, body.model_dump())
            H.event(c, who.actor, 'repositories.changed', 'settings', {'before': before, 'after': body.model_dump()})
        return body.model_dump()

    @app.put('/api/v2/repositories/{owner}/{repo}')
    def update(request: Request, owner: str, repo: str, body: RepoUpdate):
        who = human(request, True)
        name = owner + '/' + repo
        with store.transaction() as c:
            before = c.execute('SELECT * FROM repositories WHERE full_name=?', (name,)).fetchone()
            if not before:
                raise Problem('not_found', 'No such repository; refresh the list', 404)
            if body.enabled is not None:
                c.execute('UPDATE repositories SET enabled=? WHERE full_name=?', (int(body.enabled), name))
            if 'setup_command' in body.model_fields_set:
                c.execute("UPDATE repositories SET setup_command=?,setup_source='settings' WHERE full_name=?", (body.setup_command, name))
            c.execute('UPDATE repositories SET updated=? WHERE full_name=?', (H.now(), name))
            after = dict(c.execute('SELECT * FROM repositories WHERE full_name=?', (name,)).fetchone())
            H.event(c, who.actor, 'repositories.changed', name, {'before': dict(before), 'after': after})
        return after

    def botops(request, bot):
        """BotOps in its own run manages every bot's repositories (`Auth.botops_manages`); None for anyone else."""
        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
            return who if app.state.auth.botops_manages(c, who, bot) else None

    @app.get('/api/v2/bots/{bot}/repositories')
    def bot_get(request: Request, bot: str):
        who = botops(request, bot) or human(request)
        with store.read() as c:
            if who.role != 'bot' and not app.state.auth.bot_access(c, who, bot)['see']:
                raise Problem('forbidden', 'This bot is not visible to you', 403)
            row = service.row(c)
            return access(c, bot, row['org'] if row else store.settings.github_owner)

    @app.put('/api/v2/bots/{bot}/repositories')
    def bot_set(request: Request, bot: str, body: RepoAccessUpdate):
        who = botops(request, bot) or human(request, True)
        if app.state.auth.system_bot(bot) and who.role != 'owner':
            raise Problem('forbidden', 'Only the Owner changes Built-in bots', 403)
        with store.transaction() as c:
            row = service.row(c)
            return set_access(c, bot, body, row['org'] if row else store.settings.github_owner, who.actor, team_list=True)

    def computer(request):
        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
            if not app.state.execution.runner(c, who):
                raise Problem("forbidden", "This computer is no longer registered", 403)
            row = service.row(c)
            return {**runner_repos(c, who.runner_id, row['org'] if row else ''), 'configured': bool(row)}

    @app.get('/api/v2/runners/me/repositories')
    def computer_get(request: Request):
        who = request.state.identity
        if who.role != 'bot':
            return computer(request)
        with store.read() as c:
            validate_identity(c, who)
            row = service.row(c)
            bot = H.actor_id(who.actor)
            grants = access(c, bot, row['org'] if row else store.settings.github_owner)['effective']
            result = []
            for grant in grants:
                repo = c.execute('SELECT full_name,default_branch,setup_command FROM repositories WHERE full_name=?',
                                 (grant['full_name'],)).fetchone()
                result.append({**(dict(repo) if repo else {'full_name': grant['full_name'], 'default_branch': None,
                                                          'setup_command': None}), 'access': grant['access']})
            return {'repositories': result, 'configured': bool(row)}

    @app.post('/api/v2/runners/me/repositories/token')
    def computer_token(request: Request):
        names = [r['full_name'] for r in computer(request)['repositories']]
        if not names:
            return {'token': None, 'expires_at': None, 'repositories': []}
        token, expires, names = service.mint_reachable(names, {'contents': 'read', 'metadata': 'read'})
        repository_health(service)
        return {'token': token, 'expires_at': expires, 'repositories': names}
