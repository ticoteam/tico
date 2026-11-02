"""Deterministic Keep. Called only by the runner while holding checkout/git/push locks.

Use a temporary bare repository, never the bot checkout/index. Every retry starts
from fetched main, and commit-tree can change only checked memory/knowledge blobs.
No model, shell, rebase, force push, repository hooks or turn instructions execute.
"""
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from backend import memory_tidy as contract
from . import safe_git


class Refused(Exception):
    pass


def candidate(base, current, raw, today):
    """The filed offer is trusted; its checked rule universe is a reusable proof.

    New instructions/rules require a new semantic check, so refuse rather than
    quietly pretending a lexical check can establish instruction compatibility.
    Unchanged rules plus any subset of checked replacements need no model call.
    """
    kwargs = dict(since=None, today=today, checkout_before={}, checkout_after={}, rechecking=True)
    approved = contract.evaluate(base, raw, conflict_check=lambda *_: [], **kwargs)
    if approved.flags:
        raise Refused('Stored proposal no longer satisfies its contract')
    approved_snapshot = {**base, **approved.files}
    checked = {e.text for s in (base, approved_snapshot) for e in contract.parse(s.get(contract.LEARNINGS, '')).rules}
    if current.get('AGENT.md') != base.get('AGENT.md'):
        raise Refused('Instructions changed; a new instruction-conflict check is required')
    edits, skipped = [], []
    text = current.get(contract.LEARNINGS, '')
    entries = {e.entry_id: e.text for e in contract.parse(text).entries}
    for edit in json.loads(raw)['edits']:
        if any(not t['old_text'] or text.count(t['old_text']) != 1
               or entries.get(t['entry_id']) != t['old_text'] for t in edit['targets']):
            skipped.append(edit['number'])
        else:
            edits.append({**edit, 'number': len(edits) + 1})

    def instruction_check(agent, rules):
        if any(rule not in checked for rule in rules):
            raise ValueError('new rules need semantic check')
        return []

    result = contract.evaluate(current, json.dumps({'edits': edits, 'diary': ''}),
                               conflict_check=instruction_check, **kwargs)
    if result.flags:
        raise Refused('; '.join(result.flags))
    if any(not contract._allowed(p) for p in result.files):
        raise Refused('Change outside memory and knowledge')
    return result.files, skipped


class RepositoryError(Exception):
    pass


class Git:
    def __init__(self, path):
        self.path = Path(path)
        self.env = safe_git.committing_environment()

    def run(self, *args, data=None, check=True):
        p = subprocess.run([*safe_git.prefix(self.path), '-C', str(self.path), *args],
                           input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=self.env, timeout=60, check=False)
        if check and p.returncode:
            # Never return git's stderr: credential helpers/remote URLs can contain secrets.
            raise RepositoryError('Repository fetch unavailable') if args[0] == 'fetch' else Refused('Repository operation failed; check computer repository access')
        return p

    def text(self, *args):
        return self.run(*args).stdout.decode().strip()

    def snapshot(self, ref):
        result = {}
        for record in self.run('ls-tree', '-rz', ref).stdout.split(b'\0'):
            if not record:
                continue
            meta, name = record.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            path = name.decode('utf-8')
            if path != 'AGENT.md' and not contract._allowed(path):
                continue
            if kind != 'blob' or mode != '100644':
                raise Refused('Memory and instructions must be regular non-executable files')
            result[path] = self.run('cat-file', 'blob', oid).stdout.decode('utf-8')
        return result


def apply(repo, request, *, before_push=None, journal=None):
    """Three non-force attempts; remote marker recovers a lost completion response."""
    ident = request['id']
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', ident) or not re.fullmatch(r'[a-f0-9]{40,64}', request['memory_commit']):
        raise Refused('Invalid Keep identity')
    day = date.fromisoformat(request['date'])
    marker = 'Nightly-Tidy-Card: ' + request['card_id']
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', request['card_id']):
        raise Refused('Invalid card identity')
    local = Git(repo)
    origin = local.text('remote', 'get-url', 'origin')
    # The remote comes from runner-owned config, never the card. No local worktree writes.
    with tempfile.TemporaryDirectory(prefix='tico-memory-keep-') as temp:
        g = Git(temp)
        g.run('init', '--bare', '--quiet')
        g.run('remote', 'add', 'origin', origin)
        for attempt in range(1, 4):
            g.run('fetch', '--quiet', 'origin', 'refs/heads/main')
            head = g.text('rev-parse', 'FETCH_HEAD')
            # Base must be in current main's history; no arbitrary object reads or grafts.
            if g.run('merge-base', '--is-ancestor', request['memory_commit'], head, check=False).returncode:
                raise Refused('Proposal base is no longer in main history')
            history = g.run('log', '--format=%H%x00%B%x00', request['memory_commit'] + '..' + head).stdout.decode()
            parts = history.split('\0')
            for i in range(0, len(parts) - 1, 2):
                if marker in parts[i+1].splitlines():
                    return {'status': 'kept', 'commit': parts[i].strip(), 'reason': ''}
            if local.run('status', '--porcelain').stdout:
                raise Refused('Bot checkout has uncommitted writes; Keep can be tried after they are saved')
            local_head = local.text('rev-parse', 'HEAD')
            if g.run('merge-base', '--is-ancestor', local_head, head, check=False).returncode:
                raise Refused('Bot has unpushed writes; Keep can be tried after they are pushed')
            base, current = g.snapshot(request['memory_commit']), g.snapshot(head)
            files, skipped = candidate(base, current, request['edits_json'], day)
            g.run('read-tree', head)
            for path, content in files.items():
                blob = g.run('hash-object', '-w', '--stdin', data=content.encode()).stdout.decode().strip()
                g.run('update-index', '--add', '--cacheinfo', '100644', blob, path)
            tree = g.text('write-tree')
            message = ('Dreamed ' + day.isoformat() + '\n\n' + request['diary'].strip() + '\n\n'
                       + ('Skipped edits: ' + ', '.join(map(str, skipped)) + ' (exact unique target no longer matches).'
                          if skipped else 'Skipped edits: none.') + '\n\n' + marker + '\n')
            commit = g.run('commit-tree', tree, '-p', head, data=message.encode()).stdout.decode().strip()
            if before_push:
                before_push(attempt)
            if journal:
                used = int(journal.read_text()) if journal.exists() else 0
                if used >= 3:
                    raise Refused('Three push attempts were already used; card remains open')
                tmp = journal.with_suffix('.tmp')
                tmp.write_text(str(used + 1))
                tmp.replace(journal)
            pushed = g.run('push', '--porcelain', 'origin', commit + ':refs/heads/main', check=False)
            if pushed.returncode == 0:
                return {'status': 'kept', 'commit': commit, 'reason': ''}
            # Includes uncertain transport failure: fetch again and detect our marker.
        g.run('fetch', '--quiet', 'origin', 'refs/heads/main')
        head = g.text('rev-parse', 'FETCH_HEAD')
        history = g.run('log', '--format=%H%x00%B%x00', request['memory_commit'] + '..' + head).stdout.decode().split('\0')
        for i in range(0, len(history) - 1, 2):
            if marker in history[i+1].splitlines():
                return {'status': 'kept', 'commit': history[i].strip(), 'reason': ''}
        raise Refused('Main could not be updated after three push attempts; card remains open')


def main():
    try:
        result = apply(sys.argv[1], json.load(sys.stdin), journal=Path(sys.argv[2]))
    except RepositoryError:
        result = {'status': 'retry', 'commit': '', 'reason': 'Repository fetch unavailable; retrying bounded reconciliation'}
    except (Refused, ValueError, KeyError, OSError, subprocess.SubprocessError):
        # Detailed refusal contains only fixed contract reasons, never proposal/git output.
        exc = sys.exc_info()[1]
        uncertain = not isinstance(exc, Refused) and Path(sys.argv[2]).exists()
        result = {'status': 'retry' if uncertain else 'refused', 'commit': '', 'reason': str(exc) if isinstance(exc, Refused)
                  else 'Keep could not be checked safely; card remains open'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
