"""Source recovery exercises the real claim, completion, queue and scheduler boundaries on the factory clock.

These small scenarios isolate the interruption from normal build decisions. Their fixture writes supply status,
backlog or missing-contact conditions; recovery uses the production API and scheduler implementation.
"""
import json

from evals.factory.scenarios import Scenario

BOT = 'builder-claude'


def probe(w):
    w.post('chat/' + BOT, {'text': 'Read the existing files and continue the saved work.'})
    attempt = w.post('jobs/claim', {'bot': BOT, 'start_places': True}, w.runner['token'])['attempt']
    w.post(f"attempts/{attempt['id']}/started", {'thread_id': 'factory-source-session'}, w.runner['token'])
    return attempt


def jump(w, seconds):
    # Clock travel represents a reporting computer, without replaying thousands of identical heartbeats.
    w.clock.shift(seconds)
    with w.store.transaction() as c:
        c.execute('UPDATE runners SET last_seen_at=?,awake_since=NULL', (w.H.now(),))
    w.heartbeat(w.bot_slugs)


def quota(w, attempt, fallback=False):
    until = w.H.shift(w.H.now(), seconds=3600)
    rows = ([('diagnostic', {'text': 'fake unavailable; running this turn on codex'})] if fallback else [])
    rows += [('tool', {'name': 'Read', 'status': 'started'}),
             ('error', {'error': 'Usage limit; resets at ' + until, 'limit': True})]
    w.post(f"attempts/{attempt['id']}/events", {'events': [
        {'seq': n, 'kind': kind, 'payload': payload} for n, (kind, payload) in enumerate(rows, 1)]}, w.runner['token'])
    result = w.post(f"attempts/{attempt['id']}/complete", {
        'outcome': 'failed', 'limited': True, 'limit_resets_at': until, 'last_seq': len(rows),
        'text': 'Saved checkpoint: startup files read.',
        **({'primary_held': True, 'fallback': 'codex'} if fallback else {})}, w.runner['token'])
    return result, until


def fallback_setup(run, w, team):
    attempt = probe(w)
    result, until = quota(w, attempt, fallback=True)
    saved = json.loads(w.sql('SELECT result FROM attempts WHERE id=?', (attempt['id'],))[0]['result'])
    jump(w, 3660)
    resumed = w.post('jobs/claim', {'bot': BOT, 'start_places': True}, w.runner['token'])['attempt']
    run.proof = (result['outcome'] == 'interrupted' and saved['retryable'] and
                 bool(resumed and resumed['job_id'] == attempt['job_id']))


def stale_setup(run, w, team):
    from backend.execution import release_limits
    attempt = probe(w)
    _, until = quota(w, attempt)
    with w.store.transaction() as c:
        c.execute('UPDATE bot_status SET changed_at=? WHERE bot=?', (w.H.shift(until, seconds=-60), BOT))
    jump(w, 3660)
    with w.store.transaction() as c:
        release_limits(c)
    resumed = w.post('jobs/claim', {'bot': BOT, 'start_places': True}, w.runner['token'])['attempt']
    run.proof = bool(resumed and resumed['job_id'] == attempt['job_id'])


def backlog_setup(run, w, team):
    from backend import source_outages
    with w.store.transaction() as c:
        task = w.H.task_create(c, 'human:ana', 'Review the source recovery', 'Read the existing change.',
                               'bot:' + BOT, lint=False)
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (task['id'],))
        c.execute("UPDATE jobs SET state='completed'")
        c.execute('UPDATE runners SET capacity=1')
        for n in range(100):
            from backend.rules import Decision, WAKE
            w.H._write_message(c, w.H.KEEPER, 'bot:' + BOT, f'Recovery note {n}',
                              w.H.conversation(c, task['conversation_id']), 'notice', {'task': task['id']},
                              None, None, decided=Decision(WAKE, 'assignment'))
        source_outages.batch_waiters(c, BOT)
        pending = c.execute("SELECT count(*) FROM jobs WHERE bot_id=? AND state='queued'", (BOT,)).fetchone()[0]
    first = w.post('jobs/claim', {'bot': BOT, 'start_places': True}, w.runner['token'])['attempt']
    second = w.post('jobs/claim', {'bot': BOT, 'start_places': True}, w.runner['token'])['attempt']
    carried = w.sql('SELECT count(*) n FROM jobs WHERE wake_carrier IS NOT NULL')[0]['n']
    run.proof = pending == 1 and carried == 100 and first is not None and second is None


def bulk_setup(run, w, team):
    owner, requester = 'bot:' + BOT, 'bot:engineering-lead'
    with w.store.transaction() as c:
        tasks = [w.H.task_create(c, requester, f'Finish change {n}', 'The work has shipped.', owner, lint=False)
                 for n in range(30)]
        c.execute("UPDATE jobs SET state='completed'")
        c.execute("UPDATE bots SET state='paused' WHERE slug IN (?,?)", (BOT, 'engineering-lead'))
        for task in tasks:
            w.H.task_close(c, 'human:ana', task['id'], note='Finished and shipped.')
        count = c.execute('SELECT count(*) FROM notes WHERE to_actor IN (?,?) AND body LIKE ?',
                          (owner, requester, 'A person finished or closed 30 tasks.%')).fetchone()[0]
        queued = c.execute("SELECT count(*) FROM jobs WHERE state='queued' AND bot_id IN (?,?)",
                           (BOT, 'engineering-lead')).fetchone()[0]
        run.proof = count == 2 and queued == 0


def server_setup(run, w, team):
    from backend.store import Problem
    attempt = probe(w)
    execution = w.app.state.execution
    execution.served_at = None
    original = execution.renew
    def busy(*args, **kwargs):
        raise Problem('unavailable', 'Synthetic write lock is busy', 503, retryable=True)
    execution.renew = busy
    running = True
    try:
        for _ in range(20):
            w.clock.shift(60)
            # World.call answers None for a 5xx and records it as a finding; the busy lock is the outage itself, so its
            # 503 is read here and taken back out of the findings.
            status, _ = w.request("post", f"attempts/{attempt['id']}/renew", {}, w.runner['token'])
            if status != 503:
                raise AssertionError(f'the blocked renewal answered {status}, not the busy lock')
            w.server_errors.pop()
            with w.store.transaction() as c:
                execution.expire(c)
                running &= c.execute('SELECT state FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'running'
    finally:
        execution.renew = original
    renewed = w.post(f"attempts/{attempt['id']}/renew", {}, w.runner['token'])
    thread = w.sql('SELECT thread_id FROM attempts WHERE id=?', (attempt['id'],))[0]['thread_id']
    run.proof = running and bool(renewed) and thread == 'factory-source-session'


def recovered(run):
    return run.proof, 'real claim/completion/queue recovery on the factory clock'


SCENARIOS = [Scenario(name, about, setup=setup, hours=0, drain_hours=0,
                      checks=[('the source interruption preserves work without a wake cascade', None, recovered)])
             for name, about, setup in (
                 ('mid_fallback_quota', 'Quota interrupts a fallback after reading; its saved job resumes at reset.', fallback_setup),
                 ('stale_quota_reset', 'A later status refresh cannot extend the absolute provider reset.', stale_setup),
                 ('restore_backlog_100', 'One hundred waiting notices recover through one carrier and one computer slot.', backlog_setup),
                 ('bulk_close_30', 'Thirty human closures leave one note per paused recipient and no queued wake.', bulk_setup),
                 ('server_renew_outage_20m', 'Twenty minutes of busy server renewals keeps the running lease and session.', server_setup),
             )]
