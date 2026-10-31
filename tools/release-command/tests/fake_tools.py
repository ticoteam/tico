"""Synthetic git/Actions/hub for the release boundary tests; no network, repositories or credentials."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['RELEASE_FIXTURE'])
config = json.loads((root / 'fixture.json').read_text())
args = sys.argv[1:]
name = os.environ['FAKE_TOOL']
FROZEN = 'a' * 40

def run(candidate=FROZEN, title=None):
    conclusion = (root / 'conclusion').read_text() if (root / 'conclusion').exists() else 'success'
    return dict(id=42, path='.github/workflows/ci.yml', event='workflow_dispatch', head_branch='main',
                head_sha=candidate, display_title=title or f'Validate {FROZEN} as v1.0.1 from v1.0.0',
                status='completed' if (root / 'watched').exists() else 'in_progress', conclusion=conclusion,
                created_at='2026-01-02T01:00:00Z')

if name == 'git':
    if args[:1] == ['fetch'] or args[:1] == ['status']:
        pass
    elif args[:1] == ['rev-parse']:
        print((root / 'main').read_text() if 'refs/remotes/origin/main' in args else FROZEN)
    elif args[:1] == ['push']:
        source, dest = args[-1].split(':')
        (root / 'tag').write_text(json.dumps(dict(sha=source, ref=dest)))
    else:
        sys.exit(f'Unexpected git invocation: {args}')
elif name == 'python3':
    if args[0] != 'scripts/freeze_guard.py':
        sys.exit(f'Unexpected Python invocation: {args}')
elif name == 'gh':
    if args[:2] == ['repo', 'view']:
        print('acme/product')
    elif args[:1] == ['api']:
        if '/actions/workflows/ci.yml/runs?' in args[-1]:
            runs = [run()] if (root / 'dispatched').exists() or (root / 'watched').exists() else []
            print(json.dumps(dict(workflow_runs=runs)))
        else:
            print(json.dumps(run('c' * 40 if config.get('wrong_commit') else FROZEN,
                                 'Validate ' + 'c' * 40 + ' as v1.0.1 from v1.0.0' if config.get('wrong_title') else None)))
    elif args[:2] == ['workflow', 'run']:
        assert f'candidate_sha={FROZEN}' in args and 'version=v1.0.1' in args, args
        (root / 'dispatched').touch()
    elif args[:2] == ['run', 'watch']:
        watches = root / 'watches'
        count = int(watches.read_text()) + 1 if watches.exists() else 1
        watches.write_text(str(count))
        (root / 'watched').touch()
        if config.get('move_during_watch'):
            (root / 'main').write_text('e' * 40)
        if config.get('failed'):
            conclusion = 'failure'
        elif count <= config.get('cancelled_watches', 0):
            conclusion = 'cancelled'
        else:
            conclusion = 'success'
        (root / 'conclusion').write_text(conclusion)
        if conclusion != 'success':
            sys.exit(1)
    elif args[:2] == ['run', 'rerun']:
        assert args[2] == '42' and (root / 'conclusion').read_text() == 'cancelled', args
        with (root / 'reruns').open('a') as stream:
            stream.write(' '.join(args) + '\n')
        (root / 'watched').unlink()
    elif args[:2] == ['release', 'view']:
        if not (root / 'tag').exists():
            sys.exit('release not found')
        print(json.dumps(dict(isDraft=False)))
    else:
        sys.exit(f'Unexpected gh invocation: {args}')
elif name == 'hub':
    assert args[:4] == ['task', 'create', '--owner', 'docs-writer'], args
    body = args[args.index('--body') + 1]
    assert 'v1.0.0..v1.0.1' in body and 'Changelog-Release: v1.0.1' in body and '  ' not in body, body
    with (root / 'writer-tasks').open('a') as stream:
        stream.write(args[args.index('--title') + 1] + '\n')
    if config.get('hub_lost'):
        sys.exit('connection reset')
    print(json.dumps(dict(task=dict(id='t' * 36, short_id='4a28704c'))))
