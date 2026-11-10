"""Helpers that other tests share, moved out of test_worktrees.py so they import no test file."""

import json
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from backend import hubdb as H
from backend.tests.helpers.github_app import api, gh, auth, runner_token
from backend.tests.helpers.repositories import catalog, put
from backend.enqueue import enqueue_stored


@pytest.fixture
def prepared(api, gh):
    catalog(api, gh)
    runner_token(api, 'cmo')
    put(api, 'repositories/Acme/product', {'enabled': True})
    put(api, 'bots/cmo/repositories', {'mode': 'all'})
    with api.app_state.store.transaction() as c:
        # TEMPORARY: task_links v2 is owned by the tasks engineer. Drop this block at merge.
        columns = {
            'repo': 'TEXT',
            'number': 'INTEGER',
            'branch': 'TEXT',
            'computer_id': 'TEXT',
            'path': 'TEXT',
            'checks': 'TEXT',
            'mergeable': 'TEXT',
            'review_state': 'TEXT',
            'pending_comments': 'INTEGER',
            'detail': 'TEXT',
            'updated_at': 'TEXT',
        }
        have = {r[1] for r in c.execute('PRAGMA table_info(task_links)')}
        for name, kind in columns.items():
            if name not in have:
                c.execute(f'ALTER TABLE task_links ADD COLUMN {name} {kind}')
        c.execute(
            "UPDATE runner_states SET readiness=? WHERE runner_id='r1'",
            (json.dumps({'schema_version': 1, 'bots': {}, 'worktrees': True}),),
        )
        task = H.task_create(c, 'human:ana', 'Build product', 'Implement product', 'bot:cmo')
        other = H.task_create(c, 'human:ana', 'Other work', 'Implement docs', 'bot:cpo')
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m1','human:ana','bot:cmo','say','Work',?)",
            (H.now(),),
        )
        enqueue_stored(c, 'm1')
        job = c.execute("SELECT id FROM jobs WHERE message_id='m1'").fetchone()[0]
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) VALUES('a1',?,'cmo','r1',1,'synthetic','running',?,?)",
            (job, until, H.now()),
        )
    return api, task['id'], other['id']


def end(c, tid, status='closed', by='human:ana'):
    """Close, finish or reopen a task through the engine (its status changes only through a move)."""
    seen = H.task(c, tid)['version']
    if status == 'closed':
        return H.task_close(c, by, tid, 'Not needed.', seen)
    if status == 'done':
        return H.task_done(c, by, tid, 'Finished.', seen)
    return H.task_move(c, by, tid, H.TASK_REOPEN, 'Back to work.', seen)


def give(c, tid, owner, by='human:ana'):
    """Hand a task to `owner` through the engine."""
    return H.task_reassign(c, by, tid, owner, 'Over to you.', H.task(c, tid)['version'])


def post(api, path, body, token='owner-test'):
    return api.post('/api/v2/' + path, json=body, headers={**auth(token), 'Idempotency-Key': uuid.uuid4().hex})


def mark_ready(api, tid, link_id):
    return api.patch(
        f'/api/v2/tasks/{tid}/links/{link_id}',
        json={
            'state': 'present',
            'checkout_state': 'ready',
            'setup_pending': False,
            'expected_head': 'a' * 40,
            'checkout_target': 'refs/heads/tico/test',
            'expected_base': 'b' * 64,
        },
        headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex},
    )
