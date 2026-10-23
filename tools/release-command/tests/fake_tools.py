"""Synthetic git/Actions for the release boundary tests; no network, repositories or credentials."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['RELEASE_FIXTURE'])
config = json.loads((root / 'fixture.json').read_text())
args = sys.argv[1:]
name = os.environ['FAKE_TOOL']
FROZEN, CANDIDATE = 'a' * 40, 'b' * 40

def run(candidate=CANDIDATE, title=None):
    return dict(id=42, path='.github/workflows/ci.yml', event='workflow_dispatch', head_branch='main',
                head_sha=candidate, display_title=title or f'Validate {CANDIDATE} from v1.0.0',
                status='completed' if (root / 'watched').exists() else 'in_progress', conclusion='success',
                created_at='2026-01-02T01:00:00Z')

if name == 'git':
    if args[:1] == ['fetch'] or args[:1] == ['status'] or args[:1] == ['merge-base']:
        pass
    elif args[:1] == ['rev-parse']:
        print((root / 'main').read_text() if 'refs/remotes/origin/main' in args else FROZEN)
    elif args[:2] == ['show', '-s']:
        print('d' * 40 if config.get('bad_parent') else FROZEN)
    elif args[:1] == ['diff']:
        print('app.py' if config.get('code_change') else 'CHANGELOG.md')
    elif args[:1] == ['show']:
        print('# Changelog\n## [Unreleased]\n\n## [1.0.1] - 2026-01-02\n\nFix the task flow.\n')
    elif args[:1] == ['push']:
        source, dest = args[-1].split(':')
        if dest == 'refs/heads/main':
            (root / 'main').write_text(source)
        else:
            (root / 'tag').write_text(json.dumps(dict(sha=source, ref=dest)))
    else:
        sys.exit(f'Unexpected git invocation: {args}')
elif name == 'python3':
    if args[:1] == ['-c']:
        assert 'Fix the task flow.' in sys.stdin.read()
    elif args[0] != 'scripts/freeze_guard.py':
        sys.exit(f'Unexpected Python invocation: {args}')
elif name == 'gh':
    if args[:2] == ['repo', 'view']:
        print('acme/product')
    elif args[:1] == ['api']:
        if '/actions/workflows/ci.yml/runs?' in args[-1]:
            old = dict(id=1, event='schedule', head_branch='main', head_sha=FROZEN,
                       display_title='Nightly gate on main', status='completed',
                       conclusion='failure' if config.get('red') else 'success', created_at='2026-01-01T01:00:00Z')
            runs = [old]
            if (root / 'dispatched').exists() or (root / 'watched').exists():
                runs.insert(0, run())
            if config.get('newer_green'):
                runs.append(dict(old, id=2, conclusion='success', created_at='2026-01-01T02:00:00Z'))
            if config.get('red_during_validation') and (root / 'watched').exists():
                runs.append(dict(old, id=43, conclusion='failure', created_at='2026-01-03T01:00:00Z'))
            print(json.dumps(dict(workflow_runs=runs)))
        else:
            print(json.dumps(run('c' * 40 if config.get('wrong_commit') else CANDIDATE,
                                 'Validate ' + 'c' * 40 + ' from v1.0.0' if config.get('wrong_title') else None)))
    elif args[:2] == ['workflow', 'run']:
        assert f'candidate_sha={CANDIDATE}' in args
        (root / 'dispatched').touch()
    elif args[:2] == ['run', 'watch']:
        (root / 'watched').touch()
        if config.get('move_during_watch'):
            (root / 'main').write_text('e' * 40)
        if config.get('failed'):
            sys.exit(1)
    else:
        sys.exit(f'Unexpected gh invocation: {args}')
