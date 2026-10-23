#!/usr/bin/env python3
"""File a failed scheduled main gate in Tico through hub; never execute candidate code with the credential."""
import json
import os
from pathlib import Path
import subprocess
import sys


def report(event, call=subprocess.run):
    run = event.get('workflow_run', {})
    repo = event.get('repository', {}).get('full_name')
    if (run.get('event') != 'schedule' or run.get('head_branch') != 'main'
            or run.get('path') != '.github/workflows/ci.yml' or run.get('status') != 'completed'
            or run.get('conclusion') == 'success'):
        return False
    if run.get('head_repository', {}).get('full_name') != repo:
        raise ValueError('Nightly run belongs to another repository')
    run_id, sha = run['id'], run['head_sha']
    if type(run_id) is not int or len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid run identity')
    url = f'https://github.com/{repo}/actions/runs/{run_id}'
    hub = [sys.executable, 'clients/hubcli.py']
    def invoke(args):
        response = call(args, capture_output=True, text=True, check=False)
        if response.returncode:
            print(response.stdout)
            print(response.stderr, file=sys.stderr)
            raise RuntimeError('hub failed; full response above, no retry attempted')
        return response
    # An inactive bot refuses delivery; stop after that refusal, without a second route or retry.
    response = invoke(hub + ['api', 'GET', 'bots/pr-reviewer'])
    if json.loads(response.stdout).get('state') != 'active':
        raise RuntimeError('Builder is not active; retain the failed reporter run for operator recovery')
    body = (f'The scheduled full gate on main failed ({run["conclusion"]}). Fix the failing gate before the next '
            f'freeze; a newer full gate on main must be green.\n\nCandidate: {sha}\nRun: {url}\n'
            'Acceptance: repair the failure with focused regressions, then pass a new full Release validation on main.')
    response = invoke(hub + ['task', 'create', '--owner', 'bot:pr-reviewer', '--type', 'Dev tickets', '--step', 'On Deck',
                          '--top', '--title', f'Repair failed nightly gate on main (run {run_id})',
                          '--body', body, '--link', url])
    # Keep the complete acknowledgment/refusal visible; never hide a failed cloud write.
    print(response.stdout)
    result = json.loads(response.stdout)
    if result.get('error') or result.get('ok') is False:
        raise RuntimeError('Tico refused the nightly ticket; no retry attempted')
    return True


if __name__ == '__main__':
    for key in ('HUB_API_URL', 'HUB_TOKEN'):
        if not os.environ.get(key):
            sys.exit(f'{key} is required; nightly reporting is not configured')
    report(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
