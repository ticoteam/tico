"""Synthetic packet replies and provider-process custody evidence; no model calls."""
import json
import os
import stat


def reply(text):
    packet = json.loads(text)
    if 'rules' in packet:
        return json.dumps({'conflicts': []})
    entry = packet['entries'][0]
    return json.dumps({'edits': [{'number': 1, 'operation': 'generalise',
        'targets': [{'entry_id': entry['entry_id'], 'old_text': entry['old_text']}],
        'replacement': '- General rule. [sources: ' + entry['entry_id'] + ']\n',
        'sources': [entry['entry_id']]}], 'diary': 'One synthetic generalisation.'})


def custody(cwd):
    if not os.environ.get('FAKE_MEMORY_TIDY'):
        return {}
    metadata = os.stat(cwd)
    return {'authority_env_names': sorted(k for k in os.environ if k.startswith(('HUB_', 'GIT_', 'GH_', 'GITHUB_', 'TICO_GITHUB_')) or k in ('TICO_CRED_SOCKET', 'SSH_AUTH_SOCK')), 'packet_custody': {'uid': os.geteuid(), 'owner': metadata.st_uid,
        'mode': stat.S_IMODE(metadata.st_mode), 'accessible': os.access(cwd, os.R_OK | os.W_OK | os.X_OK),
        'files': os.listdir(cwd)}}
