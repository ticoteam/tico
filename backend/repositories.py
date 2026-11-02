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
# {bot: [owner/name, ...]}: write grants an Owner or admin let start and manage workflow runs (Actions: write).
WORKFLOWS = 'repository-workflow-grants'
# {bot: [owner/name, ...]}: write grants an Owner or admin let edit workflow files (Workflows: write).
WORKFLOW_EDITS = 'repository-workflow-edit-grants'
# Each per-grant opt-in an Owner or admin sets on a write grant: its grant flag, where it is stored, what it is called
# in a refusal, and the key its `stopped` entry uses.
FLAGS = (('run_workflows', WORKFLOWS, 'Running workflows', 'run workflows', 'workflows_stopped'),
         ('edit_workflows', WORKFLOW_EDITS, 'Editing workflow files', 'edit workflow files', 'workflow_edits_stopped'))
STORED = {flag: key for flag, key, *_ in FLAGS}
STARTED = {flag: key.replace('_stopped', '_started') for flag, *_, key in FLAGS}
# The same opt-ins on All ticked repos, per repository, kept apart from the chosen grants' so each mode keeps its own
# (as a switch to own keeps the chosen rows): {bot: [owner/name, ...]}. They apply while All ticked repos is Write.
ALL_STORED = {flag: key + ':all' for flag, key in STORED.items()}


def metadata(c, key):
    row = read_cache.metadata(c, key)
    try:
        value = json.loads(row[0]) if row else {}
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def computer_repositories(c):
    """{runner_id: repositories status} for every computer, once per read snapshot.

    Health / `/computers` (`computer_details`) and Operations used to look up
    `computer-repositories:<id>` once per machine. Missing keys stay absent so
    callers keep the historical default of \"unknown\".
    """
    values = read_cache.cache(c)
    key = "repositories.computer_repositories"
    if values is not None and key in values:
        return values[key]
    out = {}
    prefix = "computer-repositories:"
    # A key range, not LIKE (case-insensitive, so it cannot use the primary key): ';' sorts right after ':'.
    for row in c.execute("SELECT key, value FROM registry_metadata WHERE key>=? AND key<?",
                         (prefix, prefix[:-1] + ";")):
        rid = row["key"][len(prefix):]
        try:
            value = json.loads(row["value"] or "{}")
        except ValueError:
            continue
        if isinstance(value, dict) and "repositories" in value:
            out[rid] = value["repositories"]
    if values is not None:
        values[key] = out
    return out


def save_metadata(c, key, value):
    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps(value)))


def release_cleared_overrides(c):
    """Hand rows whose Settings command was cleared before a clear released them back to their tico.json /
    conductor.json: they kept setup_source 'settings' with no command, so every sync skipped them. A save can no longer
    make such a row, so this runs on every start and only ever matches rows from before."""
    c.execute("UPDATE repositories SET setup_command=NULL,setup_source=NULL WHERE setup_source='settings' "
              "AND TRIM(COALESCE(setup_command,''),' '||char(9)||char(10)||char(13))=''")


def migrate(c):
    if metadata(c, 'repositories-access-migrated').get('done'):
        return
    for bot, repos in metadata(c, 'github-extra-repos').items():
        if not repos:
            continue
        row = c.execute('SELECT config FROM bot_config WHERE bot=?', (bot,)).fetchone()
        if row:
            config = json.loads(row[0] or '{}')
            config['repo_access_mode'] = 'chosen'
            c.execute('UPDATE bot_config SET config=? WHERE bot=?', (json.dumps(config), bot))
        for repo in repos:
            c.execute('INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?) '
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


PRODUCT_GRANTS = 'product-repository-creation-grants'


def can_create_product_repositories(c, bot):
    """Create product repositories: every bot has it unless the Owner or an admin switched it off for that bot (Chris,
    Oct 17). The grants keep only explicit choices: `False` is a switch-off, kept until the Owner switches it back on,
    and a bot with no entry, including one made later, has the default. Nothing writes the default in, so no
    migration or template can overwrite an off."""
    return product_creation(metadata(c, PRODUCT_GRANTS), bot)


def product_creation(grants, bot):
    return grants.get(bot) is not False


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
        self.configs = self._rows('SELECT bot,config,repo FROM bot_config WHERE bot IN ({})', bots)
        sources = {source_of(_json(row['config'])) for row in self.configs.values()} - {''} - set(self.configs)
        self.configs.update(self._rows('SELECT bot,config,repo FROM bot_config WHERE bot IN ({})', sorted(sources)))
        self.chosen = {bot: [] for bot in bots}
        for part in H.chunks(bots):
            for r in c.execute('SELECT bot,full_name,access FROM bot_repo_access WHERE bot IN (%s) ORDER BY bot,full_name'
                               % ','.join('?' * len(part)), part):
                self.chosen[r['bot']].append({'full_name': r['full_name'], 'access': r['access']})
        self.missing = unreachable(c)
        self.creation = metadata(c, 'repository-creation-grants')
        self.product_creation = metadata(c, PRODUCT_GRANTS)
        self.flags = {flag: metadata(c, key) for flag, key, *_ in FLAGS}
        self.all_flags = {flag: metadata(c, key) for flag, key in ALL_STORED.items()}
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

    def all_ticked(self, org):
        """{lowercase name: owner/name} of the shared repositories All ticked repos reaches: the enabled ones, less
        bot repositories."""
        from .github_app import repo_of
        repos, shared = self.all_mode()
        bot_repos = {str(repo_of(repo, org) or '').lower() for repo in repos}
        return {name.lower(): name for name in shared
                if name.lower() not in bot_repos and not name.split('/')[1].lower().startswith('bot-')}


def access(c, bot, org, reads=None):
    """`reads` (a `Reads` covering `bot`) when the caller answers for many bots."""
    from .github_app import repo_of
    from .shared_bots import _json, source_of
    reads = reads or Reads(c, [bot])
    row = reads.configs.get(bot)
    if not row:
        raise Problem('not_found', 'No such bot', 404)
    config = json.loads(row['config'] or '{}')
    mode, all_access = config.get('repo_access_mode', 'own'), config.get('repo_all_access', 'write')
    source = source_of(_json(row['config'])) or bot
    own_row = reads.configs.get(source)
    own = repo_of(own_row['repo'] if own_row else '', org)
    chosen = [dict(r) for r in reads.chosen[bot]]
    for flag, stored in reads.flags.items():
        flagged = {str(name).lower() for name in stored.get(bot) or ()}
        for r in chosen:
            if r['access'] == 'write' and r['full_name'].lower() in flagged:
                r[flag] = True
    # All ticked repos' own per-repository opt-ins, whatever the mode: the editor shows them there, and they apply
    # while that mode is on with Write.
    ticked = {}
    for flag, stored in reads.all_flags.items():
        for name in stored.get(bot) or ():
            ticked.setdefault(str(name).lower(), {'full_name': str(name)})[flag] = True
    grants = {}
    if mode == 'all':
        grants = {key: {'full_name': name, 'access': all_access} for key, name in reads.all_ticked(org).items()}
        for key, grant in grants.items():
            if all_access == 'write':
                grant.update({flag: True for flag in STORED if ticked.get(key, {}).get(flag)})
    elif mode == 'chosen':
        grants = {r['full_name'].lower(): r for r in chosen if reads.enabled(r['full_name'])}
    if own:
        grants[own.lower()] = {'full_name': own, 'access': 'write'}
    missing = reads.missing
    effective = [r for _, r in sorted(grants.items()) if (r['full_name'].lower() not in missing or r['full_name'].lower() == str(own).lower()) and org and r['full_name'].split('/')[0].lower() == org.lower()]
    return {'mode': mode, 'all_access': all_access, 'chosen': chosen, 'all_repos': [ticked[k] for k in sorted(ticked)],
            'effective': effective,
            'create_repositories': bot == _botops() or reads.creation.get(bot) is True,
            'create_product_repositories': product_creation(reads.product_creation, bot)}


def set_access(c, bot, body, org, actor, legacy=False, team_list=False, own_click=True, owner=False):
    """`own_click` is False when a bot's turn makes the change for a person: it may grant with their rights, but
    not let a bot run workflows or edit workflow files. `owner`: the Owner is making the change."""
    from .github_app import repo_of, save_extra_repos
    before = access(c, bot, org)
    if body.create_repositories is not None:
        from .github_app import BOTOPS
        if not team_list or not H.is_human(actor):
            raise Problem('forbidden', 'Only an Owner or admin grants repository creation', 403)
        if bot == BOTOPS and not body.create_repositories:
            raise Problem('forbidden', 'BotOps retains its built-in repository creation access', 403)
    product = body.create_product_repositories
    if product is not None and product != before['create_product_repositories']:
        # On by default. Switching it back on after an off widens a bot, so it is the Owner's own click, as the Owner
        # creates product repositories themselves. Switching it off is a narrowing: an admin may too.
        if product and not (owner and own_click and H.is_human(actor)):
            raise Problem('forbidden', 'Only the Owner turns on Create product repositories, in the bot\'s settings', 403)
        if not product and (not team_list or not H.is_human(actor)):
            raise Problem('forbidden', 'Only the Owner or an admin turns off Create product repositories', 403)
    wanted = {}
    was = {flag: {r['full_name'].lower() for r in before['chosen'] if r.get(flag)} for flag in STORED}
    flagged = {flag: set(names) for flag, names in was.items()}
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
            c.execute('INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?) '
                      'ON CONFLICT(full_name) DO UPDATE SET enabled=1', (uuid.uuid4().hex, name, H.now()))
        elif not c.execute('SELECT 1 FROM repositories WHERE full_name=?', (name,)).fetchone():
            raise Problem('github_repo', 'Choose a repository from the team list', 422)
        wanted[name.lower()] = (name, grant.access)
        for flag, _, label, verb, _ in FLAGS:
            value = getattr(grant, flag)
            if value and grant.access != 'write':
                raise Problem('github_repo', label + ' needs write access to ' + name, 422)
            if value is not None and value != (name.lower() in was[flag]) and (
                    not team_list or not H.is_human(actor) or not own_click):
                raise Problem('forbidden', 'Only an Owner or admin lets a bot ' + verb + ', themselves in Settings > '
                              'Repositories', 403)
            # Left out, a grant keeps its setting while it stays write.
            if value or value is None and name.lower() in was[flag] and grant.access == 'write':
                flagged[flag].add(name.lower())
            else:
                flagged[flag].discard(name.lower())
    # All ticked repos' opt-ins, per repository, on the same terms as a chosen grant's: write access (All ticked repos
    # set to Write), an Owner's or admin's own click. Left out, a repository keeps its tick while access stays Write.
    all_access = body.all_access or before['all_access']
    reach = Reads(c, [bot]).all_ticked(org)
    named = {r['full_name'].lower(): r['full_name'] for r in before['all_repos']}
    all_was = {flag: {r['full_name'].lower() for r in before['all_repos'] if r.get(flag)} for flag in STORED}
    all_flagged = {flag: set(names) for flag, names in all_was.items()}
    for grant in body.all_repos or []:
        name = repo_of(grant.full_name, org)
        if not name or name.split('/')[0].lower() != org.lower():
            raise Problem('github_repo', 'Choose a repository in the connected organization', 422)
        named.setdefault(name.lower(), reach.get(name.lower()))
        for flag, _, label, verb, _ in FLAGS:
            value = getattr(grant, flag)
            if value and all_access != 'write':
                raise Problem('github_repo', label + ' needs All ticked repos set to Write', 422)
            if value is not None and value != (name.lower() in all_was[flag]) and (
                    not team_list or not H.is_human(actor) or not own_click):
                raise Problem('forbidden', 'Only an Owner or admin lets a bot ' + verb + ', themselves in Settings > '
                              'Repositories', 403)
            # One since unticked in Settings > Repositories may keep its tick or lose it, never gain one.
            if value and name.lower() not in reach and name.lower() not in all_was[flag]:
                raise Problem('github_repo', name + ' is not one of the ticked repositories All ticked repos reaches', 422)
            if value:
                all_flagged[flag].add(name.lower())
            elif value is False:
                all_flagged[flag].discard(name.lower())
    if all_access != 'write':
        all_flagged = {flag: set() for flag in STORED}          # Read for every ticked repository: the ticks go
    for flag, stored in ALL_STORED.items():
        if all_flagged[flag] != all_was[flag]:
            flags = metadata(c, stored)
            flags[bot] = sorted(named[key] for key in all_flagged[flag])
            if not flags[bot]:
                flags.pop(bot)
            save_metadata(c, stored, flags)
    config = json.loads(c.execute('SELECT config FROM bot_config WHERE bot=?', (bot,)).fetchone()[0] or '{}')
    config.update(repo_access_mode=body.mode, repo_all_access=all_access)
    c.execute('UPDATE bot_config SET config=? WHERE bot=?', (json.dumps(config), bot))
    if body.chosen is not None:
        c.execute('DELETE FROM bot_repo_access WHERE bot=?', (bot,))
        c.executemany('INSERT INTO bot_repo_access VALUES(?,?,?)', [(bot, name, level) for name, level in wanted.values()])
        for flag, stored in STORED.items():
            flags = metadata(c, stored)
            names = sorted(wanted[key][0] for key in flagged[flag] if key in wanted)
            if names:
                flags[bot] = names
            else:
                flags.pop(bot, None)
            if flags != metadata(c, stored):
                save_metadata(c, stored, flags)
    if body.create_repositories is not None:
        grants = metadata(c, 'repository-creation-grants')
        if body.create_repositories:
            grants[bot] = True
        else:
            grants.pop(bot, None)
        save_metadata(c, 'repository-creation-grants', grants)
    if product is not None and product != before['create_product_repositories']:
        grants = metadata(c, PRODUCT_GRANTS)
        grants[bot] = bool(product)                           # an explicit choice either way; an off must stick
        save_metadata(c, PRODUCT_GRANTS, grants)
    after = access(c, bot, org)
    # Kept flags apply again when a grant returns to effect (Own or All back to Chosen, Own or Chosen back to All): that is
    # a gain too.
    for flag, _, _, verb, _ in FLAGS:
        had = {r['full_name'].lower() for r in before['effective'] if r.get(flag)}
        if any(r.get(flag) and r['full_name'].lower() not in had for r in after['effective']) and (
                not team_list or not H.is_human(actor) or not own_click):
            raise Problem('forbidden', 'Only an Owner or admin lets a bot ' + verb + ', themselves in Settings > '
                          'Repositories', 403)
    save_extra_repos(c, bot, [r['full_name'] for r in after['chosen'] if r['access'] == 'write'] if body.mode == 'chosen' else [])
    lost = stopped(before, after)
    H.event(c, actor, 'bot.repos_changed', bot, {'before': before, 'after': after, **lost})
    return {**after, **lost}


def stopped(before, after):
    """What a change takes away, said on its reply and in its audit event: `stopped`, the grants that applied before
    and no longer do (a repository gone, or write turned to read), `workflows_stopped` for repositories it may no
    longer run workflows in, `workflow_edits_stopped` for those it may no longer edit workflow files in, and
    `creation_stopped` when repository creation was taken away. A switch to own keeps the chosen rows, but they stop
    applying."""
    kept = {(r['full_name'].lower(), r['access']) for r in after['effective']}
    out = {'stopped': [{'full_name': r['full_name'], 'access': r['access']} for r in before['effective']
                       if (r['full_name'].lower(), r['access']) not in kept]}
    for flag, _, _, _, key in FLAGS:
        still = {r['full_name'].lower() for r in after['effective'] if r.get(flag)}
        lost = [r['full_name'] for r in before['effective'] if r.get(flag) and r['full_name'].lower() not in still]
        if lost:
            out[key] = lost
    if before['create_repositories'] and not after['create_repositories']:
        out['creation_stopped'] = True
    if before.get('create_product_repositories') and not after.get('create_product_repositories'):
        out['product_creation_stopped'] = True
    return out


def started(before, after):
    """What a change gives that only a person's own click may: `workflows_started`, the repositories the bot may now run
    workflows in (a tick, or a kept tick coming back into effect) or may once its grant applies (a tick kept on a grant
    not in effect yet), `workflow_edits_started` the same for editing workflow files, and `product_creation_started`
    when Create product repositories turns back on. BotOps acting for a person puts these on a Confirm card
    (`bot_set`)."""
    out = {}
    for flag, *_ in FLAGS:
        names = []
        for kind in ('effective', 'chosen', 'all_repos'):
            had = {r['full_name'].lower() for r in before[kind] if r.get(flag)}
            names += [r['full_name'] for r in after[kind] if r.get(flag)
                      and r['full_name'].lower() not in had and r['full_name'] not in names]
        if names:
            out[STARTED[flag]] = names
    if not before.get('create_product_repositories') and after.get('create_product_repositories'):
        out['product_creation_started'] = True
    return out


def describe_stopped(result):
    """`Acme/product (write), repository creation` for a reply or a card."""
    parts = [r['full_name'] + ' (' + r['access'] + ')' for r in result.get('stopped') or []]
    parts += [name + ' (run workflows)' for name in result.get('workflows_stopped') or []]
    parts += [name + ' (edit workflow files)' for name in result.get('workflow_edits_stopped') or []]
    parts += ['repository creation'] if result.get('creation_stopped') else []
    return ', '.join(parts + (['product repository creation'] if result.get('product_creation_stopped') else []))


LEVEL = {'read': 'read-only', 'write': 'write'}


def _short(full_name):
    return str(full_name).split('/')[-1]


def _and(names):
    names = list(names)
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def preview_change(c, settings, actor, bot, body):
    """`plain_change` for a card made outside `bot_set` (a carried hand-off): the change tried as the person who would
    confirm it, then rolled back. None when the try is refused; the card then reads the request (`plain_request`)."""
    try:
        update = RepoAccessUpdate(**(body if isinstance(body, dict) else {}))
    except ValueError:
        return None
    row = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    org = row['org'] if row else getattr(settings, 'github_owner', '')
    name = (H.bot(c, bot) or {}).get('display_name') or bot
    try:
        return tried(c, bot, update, org, actor, name)
    except Problem:
        return None


def needs_card(result):
    """Whether a change BotOps makes for a person goes on their Confirm card (`bot_set`, and the settings catalogue's
    class for it): it gives what only their own click may (`started_at`), or takes something away."""
    return bool(result.get('workflows_started') or result.get('workflow_edits_started')
                or result.get('product_creation_started') or result.get('stopped') or result.get('creation_stopped')
                or result.get('workflows_stopped') or result.get('workflow_edits_stopped')
                or result.get('product_creation_stopped'))


def trial(c, bot, update, org, actor, owner=False):
    """The change made as the person's own click on its card would make it (`owner`: the Owner's), then rolled back:
    `access()` before it and what it did (`set_access` and `started_at`), or the refusal that click would get."""
    c.execute('SAVEPOINT repo_preview')
    try:
        before = access(c, bot, org)
        result = set_access(c, bot, update, org, actor, team_list=True, owner=owner)
        return before, {**result, **started(before, access(c, bot, org))}
    finally:
        c.execute('ROLLBACK TO repo_preview')
        c.execute('RELEASE repo_preview')


def tried(c, bot, update, org, actor, name, owner=False):
    """`trial` in plain words (`plain_change`)."""
    return plain_change(name, *trial(c, bot, update, org, actor, owner))


def card_words(c, settings, auth, who, bot, body):
    """For the settings catalogue (settings_catalog.plan), the change BotOps would make for a person tried as that
    person's own click: (whether it needs their Confirm card, the card's plain words). It needs one exactly when
    `bot_set` would make one, a kept Run workflows or Edit workflow files tick coming back included. A change that
    click could not make (no write access for either tick, an admin turning Create product repositories on) is refused
    now, so no card is made for it."""
    if auth.system_bot(bot) and who.role != 'owner':
        raise Problem('forbidden', 'Only the Owner changes Built-in bots', 403)
    try:
        update = RepoAccessUpdate(**(body if isinstance(body, dict) else {}))
    except ValueError as exc:
        raise Problem('validation', 'Bot repositories takes {"mode": "own|all|chosen", ...}: ' + str(exc)[:200], 422)
    row = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    org = row['org'] if row else getattr(settings, 'github_owner', '')
    name = (H.bot(c, bot) or {}).get('display_name') or bot
    before, result = trial(c, bot, update, org, who.actor, owner=who.role == 'owner')
    return needs_card(result), plain_change(name, before, result)


def plain_change(name, before, result):
    """What a repository access change does, in one or two plain sentences for a person's Confirm card: `Security
    Engineer: read-only on tico (was write). Loses access to tico-marketing-site and tico-recorder.` `before` is
    `access()` before the change, `result` what `set_access` returned."""
    was = {r['full_name'].lower(): r for r in before['effective']}
    now = {r['full_name'].lower(): r for r in result['effective']}
    changed = [f"{LEVEL[now[k]['access']]} on {_short(now[k]['full_name'])} (was {LEVEL[was[k]['access']]})"
               for k in sorted(now) if k in was and now[k]['access'] != was[k]['access']]
    gained = [f"{LEVEL[now[k]['access']]} on {_short(now[k]['full_name'])}" for k in sorted(now) if k not in was]
    lost = [_short(was[k]['full_name']) for k in sorted(was) if k not in now]
    lines = ['; '.join(changed)] if changed else []
    if gained:
        lines.append('gets ' + _and(gained))
    # A tick kept for a grant not in effect yet (another mode, or All ticked repos on Read) says when it starts.
    for flag, verb, risk in (('run_workflows', 'run workflows', "it can start CI there, and CI uses that repository's "
                              "secrets"),
                             ('edit_workflows', 'edit workflow files', "a workflow it pushes on a branch runs with that "
                              "repository's secrets before review")):
        names = result.get(STARTED[flag]) or []
        ticked = {r['full_name'].lower() for r in result.get('all_repos') or () if r.get(flag)}
        groups = {'': [], ' once All ticked repos is on with Write': [], ' once Chosen repos is on with Write there': []}
        for n in names:
            groups['' if now.get(n.lower(), {}).get(flag) else ' once All ticked repos is on with Write'
                   if n.lower() in ticked else ' once Chosen repos is on with Write there'].append(n)
        for when, group in groups.items():
            if group:
                lines.append('may ' + verb + ' in ' + _and(_short(n) for n in group) + when + ': ' + risk)
    if result.get('product_creation_started'):
        lines.append('may create product repositories again')
    if lost:
        lines.append('loses access to ' + _and(lost))
    if result.get('workflows_stopped'):
        lines.append('can no longer run workflows in ' + _and(_short(n) for n in result['workflows_stopped']))
    if result.get('workflow_edits_stopped'):
        lines.append('can no longer edit workflow files in ' + _and(_short(n) for n in result['workflow_edits_stopped']))
    if result.get('creation_stopped'):
        lines.append('can no longer create repositories')
    if result.get('product_creation_stopped'):
        lines.append('can no longer create product repositories')
    if not lines:
        return name + ': no change to the repositories it can reach.'
    return name + ': ' + '. '.join(line[0].upper() + line[1:] if i else line for i, line in enumerate(lines)) + '.'


def plain_request(name, body):
    """The same card read from the request alone, when trying it first was refused (`preview_change`): what it is set
    to. A field the request leaves out keeps what is stored (`set_access`), so the line says so instead of guessing."""
    body = body if isinstance(body, dict) else {}
    mode = body.get('mode')
    if mode == 'own':
        what = 'only its own repository'
    elif mode == 'all':
        what = ((LEVEL.get(body['all_access'], 'write') if body.get('all_access') else 'the access level set before')
                + ' on every repository the team uses')
    elif mode == 'chosen':
        if body.get('chosen') is None:
            what = 'the repositories picked for it before, again'
        else:
            chosen = [g for g in body['chosen'] if isinstance(g, dict)]
            what = ('; '.join(f"{LEVEL.get(g.get('access') or 'write', 'write')} on {_short(g.get('full_name'))}" for g in chosen)
                    + ', besides its own repository') if chosen else 'only its own repository'
    else:
        what = 'its repository access changes'
    if body.get('create_repositories') is True:
        what += '; may create repositories'
    elif body.get('create_repositories') is False:
        what += '; may no longer create repositories'
    if body.get('create_product_repositories') is True:
        what += '; may create product repositories'
    elif body.get('create_product_repositories') is False:
        what += '; may no longer create product repositories'
    return name + ': ' + what + '.'


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
            c.execute('INSERT INTO repositories(id,full_name,bot_repo,default_branch,setup_command,setup_source,reachable,last_seen_at,updated_at) '
                      'VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(full_name) DO UPDATE SET bot_repo=excluded.bot_repo,'
                      'default_branch=excluded.default_branch,reachable=1,last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at,'
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


def workflow_health(service, accepted, refused=False, flag='run_workflows'):
    """Remember whether the installation lets repositories with `flag` have the permission it raises (run workflows:
    Actions: write; edit workflows: Workflows: write), and whether GitHub refused that token although it does, for
    Health; written only when it changes. `refused=None` keeps what was learned about the refusal while the
    installation still reports the permission."""
    key = STORED[flag] + ':accepted'
    with service.store.read() as c:
        saved = metadata(c, key)
        if refused is None:
            refused = bool(accepted and saved.get('accepted') is True and saved.get('refused'))
        state = {'accepted': accepted, 'refused': refused}
        if {'accepted': saved.get('accepted'), 'refused': bool(saved.get('refused'))} == state:
            return
    with service.store.transaction() as c:
        save_metadata(c, key, {**state, 'at': H.now()})


def _workflow_grants(c, flag):
    """`bot: owner/name` for each grant with `flag`, a chosen grant's or an All ticked repos tick."""
    return sorted({bot + ': ' + name for key in (STORED[flag], ALL_STORED[flag])
                   for bot, names in metadata(c, key).items() for name in names or ()})


def workflows_waiting(c, flag='run_workflows'):
    """The `bot: owner/name` grants with `flag` that cannot use it yet, because the installation last said it lacks
    the permission; empty when it has it, nothing is flagged, or no token was asked for since."""
    if metadata(c, STORED[flag] + ':accepted').get('accepted') is not False:
        return []
    return _workflow_grants(c, flag)


def workflows_refused(c, flag='run_workflows'):
    """The grants with `flag` whose last token GitHub refused although the installation reports the permission."""
    saved = metadata(c, STORED[flag] + ':accepted')
    if saved.get('accepted') is not True or not saved.get('refused'):
        return []
    return _workflow_grants(c, flag)


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
                if refresh_now:
                    # An installation change (a permission accepted, a repository added) can finish a waiting bot.
                    try:
                        from .repo_waits import tick
                        tick(service, force=True)
                    except Exception:
                        logging.getLogger('tico.repositories').warning('Waiting bot repositories could not be checked')
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
    # Start, rerun and cancel workflow runs (GitHub Actions: write) on a write grant; None keeps the current setting.
    run_workflows: bool | None = None
    # Create and change workflow files under .github/workflows (GitHub Workflows: write) on a write grant; None keeps
    # the current setting.
    edit_workflows: bool | None = None


class TickedRepo(Contract):
    full_name: str
    run_workflows: bool | None = None
    edit_workflows: bool | None = None


class RepoAccessUpdate(Contract):
    create_repositories: bool | None = None
    # On by default; the Owner or an admin turns it off, only the Owner back on. Left out, the bot keeps what it has.
    create_product_repositories: bool | None = None
    mode: Literal['own', 'all', 'chosen']
    all_access: Literal['read', 'write'] | None = None
    chosen: list[Grant] | None = None
    # All ticked repos' per-repository opt-ins (they apply while it is Write); left out, each keeps what it holds.
    all_repos: list[TickedRepo] | None = None


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
            raise Problem('forbidden', 'Only the owner or an admin changes repository access' if change else 'A teammate may read repositories', 403)
        return who

    def listing():
        with store.read() as c:
            repos = [dict(r) for r in c.execute('SELECT full_name,enabled,bot_repo,default_branch,setup_command,setup_source,reachable,last_seen_at FROM repositories ORDER BY full_name')]
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
                # A cleared command hands the row back to the repository's tico.json / conductor.json on the next sync.
                command = (body.setup_command or '').strip() or None
                c.execute('UPDATE repositories SET setup_command=?,setup_source=? WHERE full_name=?',
                          (command, 'settings' if command else None, name))
            c.execute('UPDATE repositories SET updated_at=? WHERE full_name=?', (H.now(), name))
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
            org = row['org'] if row else store.settings.github_owner
            if not who.task_actor or who.task_actor == 'bot:' + bot:
                return set_access(c, bot, body, org, who.actor, team_list=True, own_click=not who.task_actor,
                                  owner=who.role == 'owner')
            # A bot's turn acting for a person (BotOps, the Assistant) may give another bot access with their rights, but
            # taking any away is the person's own call: BotOps once set two bots to own, and their product repositories
            # stopped applying with nobody asked. Letting a bot run workflows or edit workflow files, or turning Create
            # product repositories back on, is the person's own click too: BotOps puts it on a Confirm card, which runs
            # as their own session.
            # The change is tried as that click would make it first, so what their click could not do (no write
            # access, an admin turning product repositories on) is refused now rather than put on a card.
            before = access(c, bot, org)
            c.execute('SAVEPOINT repo_narrowing')
            try:
                result = {**set_access(c, bot, body, org, who.actor, team_list=True, owner=who.role == 'owner'),
                          **started(before, access(c, bot, org))}
            except Problem:
                c.execute('ROLLBACK TO repo_narrowing')
                c.execute('RELEASE repo_narrowing')
                raise
            gains = result.get('workflows_started') or result.get('workflow_edits_started') \
                or result.get('product_creation_started')
            if not needs_card(result):
                c.execute('RELEASE repo_narrowing')
                return result
            c.execute('ROLLBACK TO repo_narrowing')
            c.execute('RELEASE repo_narrowing')
            name = (H.bot(c, bot) or {}).get('display_name') or bot
            propose = getattr(app.state, 'botops_propose', None)
            if who.via == 'botops' and propose:
                plain = plain_change(name, before, result)
                return propose(c, who, 'PUT', '/api/v2/bots/' + bot + '/repositories', body.model_dump(exclude_none=True),
                               plain[:300], what=plain)
            if gains:
                raise Problem('forbidden', 'Only an Owner or admin lets a bot run workflows, edit workflow files or '
                              'create product repositories, themselves in Settings > Repositories', 403)
            raise Problem('forbidden', 'This takes ' + describe_stopped(result) + ' away from ' + name + '; a person '
                          'who manages it makes that change in Settings > Repositories', 403)

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
        # A bot's turn acting for a person (BotOps, the Assistant) is still that bot's turn: its own repositories.
        actor = who.actor if who.role == 'bot' else who.task_actor if str(who.task_actor).startswith('bot:') else ''
        if not actor:
            return computer(request)
        with store.read() as c:
            validate_identity(c, who)
            row = service.row(c)
            bot = H.actor_id(actor)
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
