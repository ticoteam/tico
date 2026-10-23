#!/usr/bin/env python3
"""Report a red scheduled gate through a tasks service key, using trusted main controls only."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

TIMEOUT = 20


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even a same-host redirect would be a second request carrying the key.
        return None


def repository(event):
    repo = event.get('repository', {}).get('full_name')
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository identity')
    return repo


def run_identity(run_id):
    if type(run_id) is not int or run_id <= 0:
        raise ValueError('Invalid run identity')
    return run_id


def connection(origin, token):
    if not origin:
        raise ValueError('HUB_API_URL is required; nightly reporting is not configured')
    if not token:
        raise ValueError('HUB_TOKEN is required; nightly reporting is not configured')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in origin)):
        raise ValueError('HUB_API_URL must be an HTTPS origin without a path or credentials')
    # Force validation of malformed port syntax before constructing a credentialed request.
    parsed.port
    return origin.rstrip('/'), token


def request(origin, token, method, path, payload=None, open_request=None):
    opener = open_request or build_opener(NoRedirect()).open
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(origin + '/api/v2/' + path, data=data, method=method,
                  headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        try:
            response = opener(req, timeout=TIMEOUT)
        except HTTPError as error:
            response = error
        with response:
            status, raw = response.code, response.read().decode('utf-8', errors='replace')
    except (OSError, URLError) as error:
        # Transport exception text can contain request details; never print it with this credential.
        raise RuntimeError(f'HTTP request failed ({type(error).__name__}); no retry attempted') from None
    print(f'HTTP {status}\n{raw.replace(token, "[redacted]")}')
    try:
        result = json.loads(raw)
    except ValueError:
        raise RuntimeError(f'Tico returned invalid JSON (HTTP {status}); no retry attempted') from None
    return status, result


def report(event, origin, token, open_request=None):
    run = event.get('workflow_run', {})
    if (run.get('event') != 'schedule' or run.get('head_branch') != 'main'
            or run.get('path') != '.github/workflows/ci.yml' or run.get('status') != 'completed'
            or run.get('conclusion') == 'success'):
        return False
    repo = repository(event)
    if run.get('head_repository', {}).get('full_name') != repo:
        raise ValueError('Nightly run belongs to another repository')
    run_id, sha = run_identity(run.get('id')), run.get('head_sha')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid run identity')
    origin, token = connection(origin, token)
    url = f'https://github.com/{repo}/actions/runs/{run_id}'
    body = (f'The scheduled full gate on main failed ({run["conclusion"]}). Fix the failing gate before the next '
            f'freeze; a newer full gate on main must be green.\n\nCandidate: {sha}\nRun: {url}\n'
            'Acceptance: repair the failure with focused regressions, then pass a new full Release validation on main.')
    payload = {'key': f'nightly-gate:{run_id}', 'owner': 'bot:pr-reviewer', 'type': 'Dev tickets',
               'step': 'On Deck', 'labels': ['nightly-red'],
               'title': f'Repair failed nightly gate on main (run {run_id})', 'body': body, 'links': [url]}
    status, result = request(origin, token, 'POST', 'inbound/tasks', payload, open_request)
    if (not 200 <= status < 300 or not isinstance(result, dict) or result.get('error') or result.get('ok') is False
            or not isinstance(result.get('task'), dict) or not result['task'].get('id')
            or type(result.get('created')) is not bool or type(result.get('changed')) is not bool):
        raise RuntimeError(f'Tico refused the nightly ticket (HTTP {status}); no retry attempted')
    return True


def dry_run(run_id, origin, token, open_request=None):
    run_identity(run_id)
    origin, token = connection(origin, token)
    status, result = request(origin, token, 'POST', 'inbound/tasks',
                             {'key': f'nightly-dry-run-{run_id}', 'close': True}, open_request)
    if (status != 200 or not isinstance(result, dict) or set(result) != {'task', 'created', 'changed'}
            or result['task'] is not None or result['created'] is not False or result['changed'] is not False):
        raise RuntimeError(f'Dry run did not return the expected no-op (HTTP {status}); no retry attempted')
    status, result = request(origin, token, 'GET', 'bots/pr-reviewer', open_request=open_request)
    error = result.get('error') if isinstance(result, dict) else None
    if (status != 403 or not isinstance(error, dict) or error.get('code') != 'forbidden'
            or not str(error.get('detail', '')).startswith('A service key only files tasks')):
        raise RuntimeError(f'Dry run did not prove the tasks-only scope (HTTP {status}); no retry attempted')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Verify the tasks key without creating a task')
    args = parser.parse_args()
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        origin, token = os.environ.get('HUB_API_URL'), os.environ.get('HUB_TOKEN')
        if args.dry_run:
            if os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or os.environ.get('GITHUB_REF') != 'refs/heads/main':
                raise ValueError('Dry run requires workflow_dispatch on main')
            repository(event)
            dry_run(int(os.environ.get('GITHUB_RUN_ID', '')), origin, token)
        else:
            report(event, origin, token)
    except (KeyError, ValueError, RuntimeError) as error:
        # Redact even a malformed event/config error before it reaches the Actions log.
        print(str(error).replace(os.environ.get('HUB_TOKEN') or '\0', '[redacted]'), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
