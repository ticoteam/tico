"""scripts/google-setup.sh, the Cloud Shell step that makes Tico's Workspace key and sends it to the server.

gcloud and curl are fakes on PATH. The fake gcloud keeps projects, service accounts, keys, org policies and role
bindings in a JSON state file and logs every call. The fake curl answers the key upload and keep routes and logs each
request. The script runs from a copy with the server address and install id filled in, as the server serves it.
"""

import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/google-setup.sh'
CODE = 'code123'
BASE = 'https://tico.test'
INSTALL = 'abc123'
CALLER = 'admin@acme.example'
ROLE = 'roles/orgpolicy.policyAdmin'
KEY_POLICIES = ['iam.disableServiceAccountKeyCreation', 'iam.managed.disableServiceAccountKeyCreation']
SECRET = 'FAKEKEYMATERIAL'
GENERIC_REFUSAL = 'FAILED_PRECONDITION: Key creation is blocked.'
POLICY_REFUSAL = ('FAILED_PRECONDITION: Key creation is not allowed by organization policy '
                  'constraints/iam.disableServiceAccountKeyCreation.')
ORPHAN = {'kid': 'orphan-old', 'validAfterTime': '2026-11-01T09:00:00.000000Z'}
NEWER = {'kid': 'other-newer', 'validAfterTime': '2026-11-03T12:00:00.000000Z'}

FAKE_GCLOUD = r'''#!/usr/bin/env python3
import json
import os
import sys

STATE = os.environ['FAKE_STATE']
ARGS = sys.argv[1:]
POS = [arg for arg in ARGS if not arg.startswith('-')]
ROLE = 'roles/orgpolicy.policyAdmin'
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(['gcloud', *ARGS]) + '\n')
with open(STATE) as f:
    state = json.load(f)


def opt(name):
    for arg in ARGS:
        if arg.startswith(name + '='):
            return arg.split('=', 1)[1]
    return None


def save():
    with open(STATE, 'w') as f:
        json.dump(state, f)


def fail(message):
    sys.stderr.write(message + '\n')
    save()
    sys.exit(1)


project = opt('--project')
if POS[:2] == ['organizations', 'list']:
    for org in state['orgs']:
        print('organizations/' + org)
elif POS[:2] == ['projects', 'list']:
    value = opt('--filter').rsplit('=', 1)[1]
    for proj in state['projects']:
        if proj['labels'].get('tico-setup') == value:
            print(proj['id'])
            break
elif POS[:2] == ['projects', 'create']:
    labels = dict(part.split('=', 1) for part in opt('--labels').split(','))
    state['projects'].append({'id': POS[2], 'labels': labels, 'org': opt('--organization')})
elif POS[:2] == ['services', 'enable']:
    pass
elif POS[:2] == ['iam', 'service-accounts'] and POS[2] == 'describe':
    if POS[3] not in state['sa']:
        fail('ERROR: (gcloud.iam.service-accounts.describe) NOT_FOUND: Unknown service account')
elif POS[:2] == ['iam', 'service-accounts'] and POS[2] == 'create':
    state['sa'].append('%s@%s.iam.gserviceaccount.com' % (POS[3], project))
elif POS[:3] == ['iam', 'service-accounts', 'keys']:
    account = opt('--iam-account')
    if POS[3] == 'create':
        if state['key_refusals'] > 0:
            state['key_refusals'] -= 1
            fail('ERROR: (gcloud.iam.service-accounts.keys.create) ' + state['key_refusal_text'])
        state['counter'] += 1
        kid = 'key%03d' % state['counter']
        with open(POS[4], 'w') as f:
            json.dump({'type': 'service_account', 'project_id': project, 'private_key_id': kid,
                       'private_key': '-----BEGIN PRIVATE KEY-----\n%s\n-----END PRIVATE KEY-----\n' % 'FAKEKEYMATERIAL',
                       'client_email': account}, f)
        state['keys'].append({'kid': kid, 'validAfterTime': '2026-11-03T10:00:%02d.000000Z' % state['counter']})
    elif POS[3] == 'list':
        print(json.dumps([{'name': 'projects/%s/serviceAccounts/%s/keys/%s' % (project, account, k['kid']),
                           'keyType': 'USER_MANAGED', 'validAfterTime': k['validAfterTime']} for k in state['keys']]))
    elif POS[3] == 'delete':
        kid = POS[4]
        if not any(k['kid'] == kid for k in state['keys']):
            fail('ERROR: NOT_FOUND: key %s' % kid)
        state['keys'] = [k for k in state['keys'] if k['kid'] != kid]
        state['deleted'].append(kid)
elif POS[:2] == ['org-policies', 'describe']:
    name = POS[2].rsplit('/', 1)[-1]
    if state['describe_refused']:
        fail("ERROR: (gcloud.org-policies.describe) PERMISSION_DENIED: Permission 'orgpolicy.policies.get' denied.")
    if name in state['overridden']:
        print(json.dumps({'spec': {'rules': [{'enforce': False}]}}))
    elif name in state['enforced']:
        if name.startswith('iam.managed.'):
            print(json.dumps({'spec': {'rules': [{'enforce': True}]}}))
        else:
            print(json.dumps({'booleanPolicy': {'enforced': True}}))
    else:
        fail('ERROR: (gcloud.org-policies.describe) NOT_FOUND: Policy not found')
elif POS[:2] == ['org-policies', 'set-policy']:
    with open(POS[2]) as f:
        policy = json.load(f)
    name = policy['name'].rsplit('/', 1)[-1]
    if state['set_always_denied'] or (state['policy_needs_role'] and ['user:' + state['account'], ROLE] not in state['bindings']):
        fail("ERROR: (gcloud.org-policies.set-policy) PERMISSION_DENIED: Permission 'orgpolicy.policy.set' denied.")
    state['policy_sets'].append({'name': policy['name'], 'spec': policy['spec']})
    state['enforced'] = [n for n in state['enforced'] if n != name]
    state['overridden'].append(name)
elif POS[:3] == ['config', 'get-value', 'account']:
    print(state['account'])
elif POS[:2] == ['organizations', 'get-iam-policy']:
    bindings = [{'role': role, 'members': [member]} for member, role in state['bindings']]
    if state['conditional']:
        bindings.append({'role': 'roles/viewer', 'members': ['user:other@acme.example'],
                         'condition': {'title': 'expires', 'expression': 'request.time < timestamp("2030-01-01T00:00:00Z")'}})
    print(json.dumps({'bindings': bindings}))
elif POS[:2] == ['organizations', 'add-iam-policy-binding']:
    if state['conditional'] and opt('--condition') != 'None':
        fail('ERROR: (gcloud.organizations.add-iam-policy-binding) The policy has conditional bindings; pass --condition.')
    binding = [opt('--member'), opt('--role')]
    if binding not in state['bindings']:
        state['bindings'].append(binding)
elif POS[:2] == ['organizations', 'remove-iam-policy-binding']:
    if state['conditional'] and opt('--condition') != 'None':
        fail('ERROR: (gcloud.organizations.remove-iam-policy-binding) The policy has conditional bindings; pass --condition.')
    binding = [opt('--member'), opt('--role')]
    state['bindings'] = [b for b in state['bindings'] if b != binding]
else:
    fail('unexpected gcloud call: ' + ' '.join(ARGS))
save()
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json
import os
import sys

ARGS = sys.argv[1:]
URL = ARGS[-1]
DATA = ARGS[ARGS.index('--data-binary') + 1]
BODY = open(DATA[1:]).read() if DATA.startswith('@') else DATA
HEADERS = [ARGS[i + 1] for i, arg in enumerate(ARGS[:-1]) if arg == '-H']
AUTH = next((h for h in HEADERS if h.startswith('Authorization')), '')
try:
    SENT = json.loads(BODY)
except ValueError:
    SENT = BODY
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(['curl', URL, AUTH, SENT]) + '\n')
STATE = os.environ['FAKE_STATE']
with open(STATE) as f:
    state = json.load(f)


def save():
    with open(STATE, 'w') as f:
        json.dump(state, f)


if AUTH != 'Authorization: Setup ' + os.environ['FAKE_CODE']:
    sys.stderr.write('curl: (22) The requested URL returned error: 401\n')
    sys.exit(22)
if URL.endswith('/api/v2/google/workspace/key'):
    kid = SENT['key']['private_key_id']
    mode = state['upload_mode']
    if mode == '409':
        sys.stderr.write('curl: (22) The requested URL returned error: 409\n')
        sys.exit(22)
    state['uploaded'] = kid
    save()
    if mode == 'lost':
        sys.stderr.write('curl: (28) Operation timed out after 30000 milliseconds with 0 bytes received\n')
        sys.exit(28)
    print(json.dumps({'client_id': '1234-acme.apps.googleusercontent.com', 'delegation_url': 'https://admin.google.com/',
                      'keep_key_ids': [kid]}))
elif URL.endswith('/api/v2/google/workspace/key/keep'):
    keep = [k for k in (state.get('active'), state.get('uploaded')) if k]
    print(json.dumps({'latest': state['latest'], 'keep_key_ids': keep}))
else:
    sys.stderr.write('curl: (22) The requested URL returned error: 404\n')
    sys.exit(22)
'''

DEFAULTS = {
    'orgs': ['111'], 'projects': [], 'sa': [], 'enforced': [], 'overridden': [], 'policy_sets': [], 'bindings': [],
    'keys': [], 'deleted': [], 'counter': 0, 'key_refusals': 0, 'key_refusal_text': GENERIC_REFUSAL,
    'describe_refused': False, 'policy_needs_role': False, 'conditional': False, 'set_always_denied': False,
    'upload_mode': 'ok',
    'latest': True, 'active': None, 'uploaded': None, 'account': CALLER,
}


def filled_script():
    return SCRIPT.read_text().replace('__TICO_BASE__', BASE).replace('__TICO_INSTALL__', INSTALL)


class Hub:
    """One run's world: fake gcloud and curl on PATH, a state file, a call log, and a scratch TMPDIR."""

    def __init__(self, tmp_path, script=None, **state):
        self.bin = tmp_path / 'bin'
        self.bin.mkdir()
        self.state_path = tmp_path / 'state.json'
        self.log = tmp_path / 'calls.log'
        self.scratch = tmp_path / 'scratch'
        self.scratch.mkdir()
        self.script = tmp_path / 'google-setup.sh'
        self.script.write_text(filled_script() if script is None else script)
        self.write_state({**DEFAULTS, **state})
        for name, text in (('gcloud', FAKE_GCLOUD), ('curl', FAKE_CURL)):
            path = self.bin / name
            path.write_text(text)
            path.chmod(0o755)
        (self.bin / 'python3').symlink_to(os.path.realpath(sys.executable))

    def write_state(self, data):
        self.state_path.write_text(json.dumps(data))

    def state(self):
        return json.loads(self.state_path.read_text())

    def set(self, **changes):
        data = self.state()
        data.update(changes)
        self.write_state(data)

    def kids(self):
        return {k['kid'] for k in self.state()['keys']}

    def env(self):
        return {'PATH': f'{self.bin}:/usr/bin:/bin', 'HOME': str(self.bin.parent), 'FAKE_STATE': str(self.state_path),
                'FAKE_LOG': str(self.log), 'FAKE_CODE': CODE, 'TMPDIR': str(self.scratch),
                'TICO_RETRY_BASE': '0', 'TICO_KEY_WAIT': '5'}

    def run(self, *args, **extra_env):
        env = {**self.env(), **extra_env}
        return subprocess.run(['bash', str(self.script), *(args or (CODE,))], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120, start_new_session=True)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def gcloud(self, *prefix):
        """The arguments after the prefix, for each gcloud call that starts with it."""
        return [c[1 + len(prefix):] for c in self.calls() if c[0] == 'gcloud' and c[1:1 + len(prefix)] == list(prefix)]

    def index(self, *prefix):
        for i, c in enumerate(self.calls()):
            if c[0] == 'gcloud' and c[1:1 + len(prefix)] == list(prefix):
                return i
        raise AssertionError(f'no gcloud call starting with {prefix}')

    def curl(self, path):
        """Each request to the server path, with its authorization header and the body it sent."""
        return [{'auth': c[2], 'sent': c[3]} for c in self.calls() if c[0] == 'curl' and c[1] == BASE + path]

    def own_key(self):
        return self.curl('/api/v2/google/workspace/key')[-1]['sent']['key']['private_key_id']


def run_on_terminal(hub, answer):
    """Runs the script with a pseudo-terminal as its controlling terminal, answers the first prompt, returns output."""
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvpe('bash', ['bash', str(hub.script), CODE], hub.env())
        finally:
            os._exit(127)
    output = b''
    answered = False
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
        if not answered and b'[1-2]: ' in output:
            os.write(fd, answer.encode() + b'\n')
            answered = True
    else:
        os.kill(pid, signal.SIGKILL)
    _, status = os.waitpid(pid, 0)
    assert answered, output.decode(errors='replace')
    return output.decode(errors='replace'), os.waitstatus_to_exitcode(status)


def test_fresh_run_makes_the_key_sends_it_and_removes_only_the_orphans(tmp_path):
    hub = Hub(tmp_path, keys=[ORPHAN, NEWER])
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert 'Done. Go back to Tico.' in result.stdout

    (created,) = hub.gcloud('projects', 'create')
    project = created[0]
    assert project.startswith('tico-mail-') and len(project.split('-')[-1]) == 6
    assert created[1:] == [f'--labels=tico-setup={INSTALL}', '--organization=111']

    services = hub.gcloud('services', 'enable')[0]
    for api in ['gmail.googleapis.com', 'calendar-json.googleapis.com', 'orgpolicy.googleapis.com', 'iam.googleapis.com']:
        assert api in services

    sa = f'tico-mail@{project}.iam.gserviceaccount.com'
    assert hub.gcloud('iam', 'service-accounts', 'create')[0][0] == 'tico-mail'
    (key_create,) = hub.gcloud('iam', 'service-accounts', 'keys', 'create')
    assert f'--iam-account={sa}' in key_create

    (upload,) = hub.curl('/api/v2/google/workspace/key')
    assert upload['auth'] == f'Authorization: Setup {CODE}'
    assert set(upload['sent']) == {'key'}
    kid = upload['sent']['key']['private_key_id']

    (keep,) = hub.curl('/api/v2/google/workspace/key/keep')
    assert keep['auth'] == f'Authorization: Setup {CODE}'

    state = hub.state()
    assert state['deleted'] == [ORPHAN['kid']]
    assert hub.kids() == {kid, NEWER['kid']}
    assert state['policy_sets'] == []
    assert hub.gcloud('organizations', 'add-iam-policy-binding') == []

    assert SECRET not in result.stdout + result.stderr
    assert 'BEGIN PRIVATE KEY' not in result.stdout + result.stderr
    assert list(hub.scratch.iterdir()) == []


def test_a_rerun_resumes_the_labeled_project_instead_of_making_another(tmp_path):
    hub = Hub(tmp_path, projects=[{'id': 'tico-mail-old', 'labels': {'tico-setup': INSTALL}, 'org': '111'}])
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert 'Using project tico-mail-old' in result.stdout
    assert hub.gcloud('projects', 'create') == []
    assert '--project=tico-mail-old' in hub.gcloud('services', 'enable')[0]
    assert '--project=tico-mail-old' in hub.gcloud('iam', 'service-accounts', 'create')[0]
    assert hub.state()['sa'] == ['tico-mail@tico-mail-old.iam.gserviceaccount.com']


def test_enforced_key_policies_are_overridden_and_key_creation_is_retried(tmp_path):
    hub = Hub(tmp_path, enforced=list(KEY_POLICIES), key_refusals=1)
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert 'Waiting for the key policy to apply' in result.stdout
    project = hub.state()['projects'][0]['id']
    sets = hub.state()['policy_sets']
    assert [s['name'] for s in sets] == [f'projects/{project}/policies/{name}' for name in KEY_POLICIES]
    assert all(s['spec'] == {'rules': [{'enforce': False}]} for s in sets)
    assert len(hub.gcloud('iam', 'service-accounts', 'keys', 'create')) == 2
    assert hub.gcloud('organizations', 'add-iam-policy-binding') == []


def test_a_refused_override_grants_the_policy_admin_role_then_removes_it(tmp_path):
    hub = Hub(tmp_path, enforced=list(KEY_POLICIES), policy_needs_role=True)
    result = hub.run()
    assert result.returncode == 0, result.stderr
    project = hub.state()['projects'][0]['id']
    assert len(hub.state()['policy_sets']) == 2
    assert [s['name'] for s in hub.state()['policy_sets']] == [f'projects/{project}/policies/{n}' for n in KEY_POLICIES]

    (add,) = hub.gcloud('organizations', 'add-iam-policy-binding')
    assert f'--member=user:{CALLER}' in add and f'--role={ROLE}' in add
    (remove,) = hub.gcloud('organizations', 'remove-iam-policy-binding')
    assert f'--member=user:{CALLER}' in remove and f'--role={ROLE}' in remove
    assert hub.index('organizations', 'add-iam-policy-binding') < hub.index('organizations', 'remove-iam-policy-binding')
    assert hub.state()['bindings'] == []


def test_a_policy_admin_role_already_held_is_left_alone(tmp_path):
    hub = Hub(tmp_path, enforced=list(KEY_POLICIES), policy_needs_role=True, bindings=[[f'user:{CALLER}', ROLE]])
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert len(hub.state()['policy_sets']) == 2
    assert hub.gcloud('organizations', 'add-iam-policy-binding') == []
    assert hub.gcloud('organizations', 'remove-iam-policy-binding') == []
    assert hub.state()['bindings'] == [[f'user:{CALLER}', ROLE]]


def test_a_key_refused_by_policy_with_unreadable_policies_is_overridden_once_then_retried(tmp_path):
    hub = Hub(tmp_path, describe_refused=True, key_refusals=2, key_refusal_text=POLICY_REFUSAL)
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert 'Done. Go back to Tico.' in result.stdout
    assert len(hub.gcloud('org-policies', 'describe')) == 2
    project = hub.state()['projects'][0]['id']
    assert [s['name'] for s in hub.state()['policy_sets']] == [f'projects/{project}/policies/{n}' for n in KEY_POLICIES]
    assert all(s['spec'] == {'rules': [{'enforce': False}]} for s in hub.state()['policy_sets'])
    assert result.stdout.count('Allowing service account keys on the project') == 2
    assert len(hub.gcloud('iam', 'service-accounts', 'keys', 'create')) == 3
    assert hub.index('iam', 'service-accounts', 'keys', 'create') < hub.index('org-policies', 'set-policy')
    assert hub.gcloud('organizations', 'add-iam-policy-binding') == []
    assert hub.kids() == {hub.own_key()}


def test_a_managed_policy_that_is_not_found_still_overrides_the_legacy_one(tmp_path):
    hub = Hub(tmp_path, enforced=[KEY_POLICIES[0]])
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert 'Done. Go back to Tico.' in result.stdout
    project = hub.state()['projects'][0]['id']
    assert [s['name'] for s in hub.state()['policy_sets']] == [f'projects/{project}/policies/{KEY_POLICIES[0]}']
    assert len(hub.gcloud('iam', 'service-accounts', 'keys', 'create')) == 1


def test_a_key_refusal_that_does_not_name_the_policy_leaves_the_policy_alone(tmp_path):
    hub = Hub(tmp_path, key_refusals=5)
    result = hub.run(TICO_KEY_WAIT='0')
    assert result.returncode == 1
    assert 'did not create the key' in result.stderr
    assert hub.state()['policy_sets'] == []
    assert len(hub.gcloud('iam', 'service-accounts', 'keys', 'create')) == 1
    assert hub.gcloud('organizations', 'add-iam-policy-binding') == []


def test_a_discovered_organization_is_passed_without_its_resource_prefix(tmp_path):
    hub = Hub(tmp_path, orgs=['111'])
    result = hub.run()
    assert result.returncode == 0, result.stderr
    (created,) = hub.gcloud('projects', 'create')
    assert '--organization=111' in created
    assert not any('organizations/' in arg for arg in created)


def test_the_temporary_role_is_unconditional_when_the_organization_has_conditional_bindings(tmp_path):
    hub = Hub(tmp_path, enforced=list(KEY_POLICIES), policy_needs_role=True, conditional=True)
    result = hub.run()
    assert result.returncode == 0, result.stderr
    (add,) = hub.gcloud('organizations', 'add-iam-policy-binding')
    (remove,) = hub.gcloud('organizations', 'remove-iam-policy-binding')
    assert '--condition=None' in add and '--condition=None' in remove
    assert hub.state()['bindings'] == []


def test_a_role_already_held_is_never_removed_even_when_the_change_is_still_refused(tmp_path):
    hub = Hub(tmp_path, enforced=list(KEY_POLICIES), bindings=[[f'user:{CALLER}', ROLE]], set_always_denied=True)
    result = hub.run()
    assert result.returncode == 1
    assert 'already has the Organization Policy Administrator role' in result.stderr
    assert hub.gcloud('organizations', 'add-iam-policy-binding') == []
    assert hub.gcloud('organizations', 'remove-iam-policy-binding') == []
    assert hub.state()['bindings'] == [[f'user:{CALLER}', ROLE]]


def test_a_lost_upload_answer_is_fixed_by_running_again(tmp_path):
    hub = Hub(tmp_path, upload_mode='lost')
    first = hub.run()
    assert first.returncode == 1
    assert "didn't confirm" in first.stderr
    first_kid = hub.own_key()

    hub.set(upload_mode='ok')
    second = hub.run()
    assert second.returncode == 0, second.stderr
    assert len(hub.gcloud('projects', 'create')) == 1
    second_kid = hub.own_key()
    assert second_kid != first_kid
    assert hub.state()['deleted'] == [first_kid]
    assert hub.kids() == {second_kid}


def test_a_superseded_upload_deletes_its_own_key_and_leaves_the_newer_one(tmp_path):
    hub = Hub(tmp_path, upload_mode='409', keys=[NEWER])
    result = hub.run()
    assert result.returncode == 1
    assert 'A newer setup started' in result.stderr
    assert 'Done.' not in result.stdout
    assert hub.state()['deleted'] == [hub.own_key()]
    assert hub.kids() == {NEWER['kid']}
    assert hub.curl('/api/v2/google/workspace/key/keep') == []


def test_a_run_that_is_not_the_latest_deletes_nothing(tmp_path):
    hub = Hub(tmp_path, keys=[ORPHAN], latest=False)
    result = hub.run()
    assert result.returncode == 0, result.stderr
    assert 'leaves the keys alone' in result.stdout
    assert hub.state()['deleted'] == []
    assert hub.kids() == {ORPHAN['kid'], hub.own_key()}


def test_two_organizations_are_answered_on_the_terminal(tmp_path):
    hub = Hub(tmp_path, orgs=['111', '222'])
    output, code = run_on_terminal(hub, '2')
    assert code == 0, output
    assert 'Choose the organization' in output
    (created,) = hub.gcloud('projects', 'create')
    assert '--organization=222' in created
    assert 'Done. Go back to Tico.' in output


def test_two_organizations_without_a_terminal_stop_with_a_clear_message(tmp_path):
    hub = Hub(tmp_path, orgs=['111', '222'])
    result = hub.run()
    assert result.returncode == 1
    assert 'needs a terminal' in result.stderr
    assert hub.gcloud('projects', 'create') == []


def test_an_empty_setup_code_is_refused_before_anything_runs(tmp_path):
    hub = Hub(tmp_path)
    result = hub.run('')
    assert result.returncode == 1
    assert 'setup code is missing' in result.stderr
    assert hub.calls() == []


@pytest.mark.parametrize('cut', ['last_line', 'middle'])
def test_a_truncated_download_runs_nothing(tmp_path, cut):
    text = filled_script()
    text = text[:text.rindex('main "$@"')] if cut == 'last_line' else text[:len(text) // 2]
    hub = Hub(tmp_path, script=text)
    result = hub.run()
    assert hub.calls() == []
    assert 'Done. Go back to Tico.' not in result.stdout


def test_an_unfilled_copy_refuses_to_run(tmp_path):
    hub = Hub(tmp_path, script=SCRIPT.read_text())
    result = hub.run()
    assert result.returncode == 1
    assert 'no server address' in result.stderr
    assert hub.calls() == []
