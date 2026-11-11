"""Helpers that other tests share, moved out of test_worktrees.py so they import no test file."""

import json
import shutil
import subprocess
from unittest import mock
import pytest
from runner_support import worktrees as W
from runner_support.repositories import GB


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def trees(tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)], check=True, capture_output=True)
    source = tmp_path / 'source'
    subprocess.run(['git', 'init', '--initial-branch=main', str(source)], check=True, capture_output=True)
    git(source, 'config', 'user.name', 'Sam')
    git(source, 'config', 'user.email', 'sam@example.com')
    (source / 'file').write_text('initial')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'Initial')
    git(source, 'remote', 'add', 'origin', str(remote))
    git(source, 'push', 'origin', 'main')
    workspace = tmp_path / 'workspace'
    base = workspace / 'repos' / 'org__product'
    base.parent.mkdir(parents=True)
    subprocess.run(['git', 'clone', str(remote), str(base)], check=True, capture_output=True)
    git(base, 'config', 'remote.origin.url', 'https://github.com/org/product.git')
    git(base, 'config', f'url.{remote}.insteadOf', 'https://github.com/org/product.git')
    monkeypatch.setattr(W.shutil, 'disk_usage', lambda p: shutil._ntuple_diskusage(100 * GB, 0, 100 * GB))
    monkeypatch.setenv('HUB_WORKSPACE', str(workspace))
    monkeypatch.setenv('HUB_BOT', 'engineer')
    monkeypatch.setenv('HUB_TASK_ID', '12345678abcdef')
    row = {
        'id': 'link1',
        'link_id': 'link1',
        'task_id': '12345678abcdef',
        'path': 'tasks/12345678/org__product',
        'branch': 'tico/12345678-build',
        'repo': 'org/product',
        'full_name': 'org/product',
        'default_branch': 'main',
        'state': 'present',
        'owner': 'bot:engineer',
        'setup_command': 'touch setup-ran',
    }
    client = mock.Mock()
    client.get.return_value = {'repositories': [{**row, 'access': 'write'}]}
    client.post.return_value = {
        'link_id': row['id'],
        'branch': row['branch'],
        'path': row['path'],
        'checkout_state': 'queued',
        'setup_pending': True,
    }

    def patch_link(_route, fields):
        row.update(
            {key: value for key, value in fields.items() if key in ('state', 'path', 'checkout_state', 'setup_pending')}
        )
        detail = json.loads(row.get('detail_json') or '{}')
        detail.update(
            {
                key: value
                for key, value in fields.items()
                if key in ('checkout_state', 'setup_pending', 'expected_head', 'checkout_target', 'expected_base')
            }
        )
        row['detail_json'] = json.dumps(detail)
        client.post.return_value.update(fields)
        return row

    client.patch.side_effect = patch_link
    return workspace, base, remote, row, client
