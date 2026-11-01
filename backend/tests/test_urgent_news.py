"""Urgent task news bypasses every profile's digest; ordinary progress remains batched."""
import json

import pytest

from backend import hubdb as H, rules as R, wake_presets as W
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401
from backend.tests.test_profiles import put
from backend.tests.test_wake_rules import as_cause


def sent_job(c, task, target, text, refs=None):
    msg = H._wake(c, H.task(c, task['id']), target, text, refs=refs)
    job = c.execute('SELECT * FROM jobs WHERE message_id=? AND bot=?', (msg['id'], H.actor_id(target))).fetchone()
    return msg, dict(job) if job else None


def test_incident_move_is_unheld_and_claimable_within_five_minutes(api):
    r = runner(api)
    assign(api, r, 'cpo')
    ready(api, r, ['cpo'])
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', 'lead')
        incident = H.task_create(c, 'bot:cpo', 'Restore sign-in', 'Repair the outage', 'bot:ops', labels=['incident'])
        c.execute('DELETE FROM jobs')
        H.task_update(c, 'bot:ops', incident['id'], status='doing')
        job = c.execute("SELECT j.*,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                        "WHERE j.bot='cpo' ORDER BY j.rowid DESC LIMIT 1").fetchone()
        assert job is not None and job['held'] == 0 and job['state'] == 'queued'
        assert json.loads(job['refs'])['rule'] == 'incident_news'
        created = job['created_at']
    attempt = claim(api, r, 'cpo')
    assert attempt
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'incident-session'}, token=r['token'])
    with api.app.state.store.read() as c:
        started = c.execute('SELECT started_at FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0]
    assert (H.parse_ts(started) - H.parse_ts(created)).total_seconds() < 300


def test_stale_suggestion_and_refused_move_need_action_but_routine_review_stays_held(api):
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', 'lead')
        mine = H.task_create(c, 'bot:ops', 'Check the rollout', 'x', 'bot:cpo')
        requested = H.task_create(c, 'bot:cpo', 'Build a feature', 'x', 'bot:ops')
        c.execute('DELETE FROM jobs')
        H.task_comment(c, H.KEEPER, mine['id'], 'Stale check: parent is done. Suggest: mark done.',
                       extra_refs={'stale_check': 'suggestion-1', 'wake': 'stall', 'wake_batch': 'rule'})
        raw = c.execute("SELECT m.refs,j.held FROM messages m JOIN jobs j ON j.message_id=m.id "
                        "WHERE j.bot='cpo' ORDER BY j.rowid DESC LIMIT 1").fetchone()
        msg, job = {'refs': json.loads(raw['refs'])}, raw
        assert msg['refs']['rule'] == 'task_action' and job['held'] == 0
        assert 'wake_batch' not in msg['refs']
        # Even a sweep-shaped notice cannot be held again by the enqueue trigger.
        _, job = sent_job(c, mine, 'bot:cpo', 'Suggestion from a scheduled check',
                          {'stale_check': 'suggestion-2', 'wake': 'stalled', 'sweep': 'check'})
        assert job['held'] == 0
        _, job = sent_job(c, requested, 'bot:cpo', 'A refused review move needs attention',
                          {'news': 'blocker', 'review_gate': 'receipt'})
        assert job['held'] == 0
        msg, job = sent_job(c, requested, 'bot:cpo', 'Ready for review: feature entered Review 1',
                            {'news': 'ready_for_review'})
        assert msg['refs']['rule'] == 'profile_lead_digest' and job['held'] == 1
        W.tick(c, job['created_at'])
        due = c.execute('SELECT hold_until FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
        assert due == W.digest_time(job['created_at'], 9)
        # A stale suggestion about another bot's task is not an action for this recipient.
        msg, job = sent_job(c, requested, 'bot:cpo', 'Stale check for the owner', {'stale_check': 'other'})
        assert msg['refs']['rule'] == 'profile_lead_digest' and job['held'] == 1


@pytest.mark.parametrize('profile', ['lead', 'doer', 'custom'])
def test_urgent_news_overrides_batch_rows_for_every_batching_profile(api, profile):
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', profile)
        c.execute("INSERT INTO rules(id,scope_kind,event,then_outcome,created_by,created_at,updated_at) "
                  "VALUES('batch-all','team','*','batch','human:ana',?,?)", (H.now(), H.now()))
        incident = H.task_create(c, 'bot:cpo', 'Incident: Outage', 'x', 'bot:ops')
        mine = H.task_create(c, 'bot:ops', 'Own this work', 'x', 'bot:cpo')
        other = H.task_create(c, 'bot:ops', 'Other work', 'x', 'bot:ops')
        c.execute('DELETE FROM jobs')
        # A lead's own profile already wakes review requests under its own rule name, which urgency preserves.
        review_rule = 'profile_lead_request' if profile == 'lead' else 'direct_review_request'
        for task, refs, rule in [(incident, {}, 'incident_news'),
                                 (mine, {'news': 'task_blocked'}, 'task_blocker'),
                                 (mine, {'wake': 'review_request'}, review_rule),
                                 (mine, {'needs_action': True, 'wake': 'stalled'}, 'needs_action')]:
            msg, job = sent_job(c, task, 'bot:cpo', 'New urgent news ' + rule, refs)
            assert msg['refs']['rule'] == rule and job['held'] == 0
        msg, job = sent_job(c, other, 'bot:cpo', 'Other task is blocked', {'news': 'task_blocked'})
        assert job['held'] == 1 and msg['refs']['rule'] == 'batch-all'
        c.execute("DELETE FROM rules WHERE id='batch-all'")
        with as_cause('bot:cpo'):
            msg, job = sent_job(c, incident, 'bot:cpo', 'Own incident update')
        assert job is None and msg['refs'].get('quiet')


def test_creation_tags_incident_titles_and_preserves_existing_tags(api):
    with api.app.state.store.transaction() as c:
        for actor, title in [('human:ana', 'Incident: GitHub App cannot sign in'),
                             ('bot:ops', 'incident: an error monitor finding')]:
            task = H.task_create(c, actor, title, 'x', 'bot:cpo', labels=['existing'])
            assert set(H.task_labels(task)) == {'incident', 'existing'}
        ordinary = H.task_create(c, 'human:ana', 'Write the incident guide', 'x', 'bot:ops')
        assert 'incident' not in H.task_labels(ordinary)
        # Wake-time classification uses the tag, not the title.
        c.execute("UPDATE tasks SET title='Renamed outage' WHERE id=?", (task['id'],))
        assert R.judge(c, H.KEEPER, 'bot:cpo', 'notice', {'task': task['id']}, 'Moved', None).rule == 'incident_news'


def test_watcher_incident_flag_survives_protocol_and_marks_new_tasks(api):
    from clients.watchers import events
    from backend.tests.test_watchers import hosted, report
    r = hosted(api)
    raw = {'op': 'task', 'key': 'error:outage', 'title': 'Error Monitor: Outage', 'incident': True}
    parsed, _ = events('tico-event ' + json.dumps(raw))
    assert parsed[0]['incident'] is True
    first = report(api, r, parsed[0])
    tid = first['tasks']['error:outage']
    with api.app.state.store.transaction() as c:
        assert 'incident' in H.task_labels(H.task(c, tid))
        H.task_update(c, H.KEEPER, tid, status='declined')
    second = report(api, r, dict(raw, op='comment', ref='new', text='Recurred'))
    with api.app.state.store.read() as c:
        assert 'incident' in H.task_labels(H.task(c, second['tasks']['error:outage']))


def test_a_named_waking_rule_keeps_its_name_and_an_urgent_comment_stays_editable(api):
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', 'lead')
        plain = H.task_create(c, 'bot:ops', 'Plain work', 'x', 'bot:cpo')
        incident = H.task_create(c, 'bot:ops', 'Incident: Outage', 'x', 'bot:cpo')
        c.execute('DELETE FROM jobs')
        msg, job = sent_job(c, plain, 'bot:cpo', 'A blocker needs it', {'wake': 'blocker'})
        assert msg['refs']['rule'] == 'profile_lead_blocker' and job['held'] == 0
        said = H.task_comment(c, H.KEEPER, incident['id'], 'A note on the outage')
        kept = H.message(c, said['id'])
        assert kept['refs']['rule'] == 'incident_news' and H.is_comment(kept)
        H.task_comment_edit(c, H.KEEPER, incident['id'], said['id'], 'Clearer words')


def test_incident_type_and_plain_rule_words_are_listed(api):
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', 'lead')
        c.execute("INSERT INTO task_types(id,name,created_at,updated_at) VALUES('incident-kind','Incident',?,?)", (H.now(), H.now()))
        task = H.task_create(c, 'bot:ops', 'Restore the server', 'x', 'bot:cpo', type='Incident')
        assert R.judge(c, H.KEEPER, 'bot:cpo', 'notice', {'task': task['id']}, 'Moved', None).rule == 'incident_news'
        rows = {r['id']: r for r in R.BUILTINS}
        for key in ('incident_news', 'task_action', 'task_blocker', 'direct_review_request'):
            assert rows[key]['protected'] and rows[key]['then'] == 'wake' and rows[key]['words']
