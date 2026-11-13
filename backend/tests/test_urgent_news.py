"""Urgent task news bypasses every profile's digest; ordinary progress remains batched."""

import json

import pytest

from backend import hubdb as H, rules as R, wake_presets as W
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401
from backend.tests.helpers.profiles import put
from backend.tests.helpers.wake_rules import as_cause
from backend.tests.helpers.urgent_news import sent_job


def test_incident_news_is_unheld_and_claimable_within_five_minutes(api):
    r = runner(api)
    assign(api, r, 'cpo')
    ready(api, r, ['cpo'])
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', 'lead')
        incident = H.task_create(c, 'bot:ops', 'Restore sign-in', 'Repair the outage', 'bot:cpo', labels=['incident'])
        c.execute('DELETE FROM jobs')
        # News about an open incident on the bot's turn. A run about a task starts only while its bot has a turn on it
        # (task_wakes.may_start), and a finished task wakes nobody.
        msg, job = sent_job(c, incident, 'bot:cpo', 'Sign-in works again for most people')
        assert job is not None and job['held'] == 0 and job['state'] == 'queued'
        assert msg['refs']['rule'] == 'incident_news'
        created = job['created_at']
    attempt = claim(api, r, 'cpo')
    assert attempt
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'incident-session'}, token=r['token'])
    with api.app.state.store.read() as c:
        started = c.execute('SELECT started_at FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0]
    assert (H.parse_ts(started) - H.parse_ts(created)).total_seconds() < 300


def test_old_check_and_review_news_is_ordinary_digest_news_for_a_lead(api):
    """Stale checks, refused moves and review requests left with the old engine: only incidents are urgent now."""
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', 'lead')
        requested = H.task_create(c, 'bot:cpo', 'Build a feature', 'x', 'bot:ops')
        c.execute('DELETE FROM jobs')
        for text, refs in [
            ('Suggestion from a scheduled check', {'stale_check': 'suggestion-2', 'wake': 'stalled'}),
            ('A refused review move needs attention', {'news': 'refused_move', 'review_gate': 'receipt'}),
            ('Ready for review: feature entered Review 1', {'news': 'ready_for_review'}),
        ]:
            msg, job = sent_job(c, requested, 'bot:cpo', text, refs)
            assert msg['refs']['rule'] == 'profile_lead_digest' and job['held'] == 1
        W.tick(c, job['created_at'])
        due = c.execute('SELECT hold_until FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
        assert due == W.digest_time(job['created_at'], 9)


@pytest.mark.parametrize('profile', ['lead', 'doer', 'custom'])
def test_urgent_news_overrides_batch_rows_for_every_batching_profile(api, profile):
    with api.app.state.store.transaction() as c:
        put(c, 'cpo', profile)
        c.execute(
            "INSERT INTO rules(id,scope_kind,event,then_outcome,created_by,created_at,updated_at) "
            "VALUES('batch-all','team','*','batch','human:ana',?,?)",
            (H.now(), H.now()),
        )
        incident = H.task_create(c, 'bot:cpo', 'Incident: Outage', 'x', 'bot:ops')
        other = H.task_create(c, 'bot:ops', 'Other work', 'x', 'bot:ops')
        c.execute('DELETE FROM jobs')
        msg, job = sent_job(c, incident, 'bot:cpo', 'New incident news')
        assert msg['refs']['rule'] == 'incident_news' and job['held'] == 0
        msg, job = sent_job(c, other, 'bot:cpo', 'Other task news', {'news': 'task_blocked'})
        assert job['held'] == 1 and msg['refs']['rule'] == 'batch-all'
        c.execute("DELETE FROM rules WHERE id='batch-all'")
        with as_cause('bot:cpo'):
            msg, job = sent_job(c, incident, 'bot:cpo', 'Own incident update')
        assert job is None and msg['refs'].get('quiet')


def test_creation_tags_incident_titles_and_preserves_existing_tags(api):
    with api.app.state.store.transaction() as c:
        for actor, title in [
            ('human:ana', 'Incident: GitHub App cannot sign in'),
            ('bot:ops', 'incident: an error monitor finding'),
        ]:
            task = H.task_create(c, actor, title, 'x', 'bot:cpo', labels=['existing'])
            assert set(H.task_labels(task)) == {'incident', 'existing'}
        ordinary = H.task_create(c, 'human:ana', 'Write the incident guide', 'x', 'bot:ops')
        assert 'incident' not in H.task_labels(ordinary)
        # Wake-time classification uses the tag, not the title.
        c.execute("UPDATE tasks SET title='Renamed outage' WHERE id=?", (task['id'],))
        assert R.judge(c, H.KEEPER, 'bot:cpo', 'notice', {'task': task['id']}, 'Moved', None).rule == 'incident_news'


def test_watcher_incident_flag_survives_protocol_and_marks_new_tasks(api):
    from clients.watchers import events
    from backend.tests.helpers.watchers import hosted, report

    r = hosted(api)
    raw = {'op': 'task', 'key': 'error:outage', 'title': 'Error Monitor: Outage', 'incident': True}
    parsed, _ = events('tico-event ' + json.dumps(raw))
    assert parsed[0]['incident'] is True
    first = report(api, r, parsed[0])
    tid = first['tasks']['error:outage']
    with api.app.state.store.transaction() as c:
        assert 'incident' in H.task_labels(H.task(c, tid))
        H.task_close(c, H.KEEPER, tid, 'Not an outage.')
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


def test_incident_news_to_a_requester_bot_without_the_turn_starts_no_run(api):
    """docs/pipelines-v3.md "It only wakes whoever has the turn": urgent only skips the digest. News about an incident
    whose turn is its owner's starts no run for the bot that asked for it; the news stays on the task for its next
    look, and the owner's turn is untouched."""
    r = runner(api)
    assign(api, r, 'cpo')
    ready(api, r, ['cpo'])
    with api.app.state.store.transaction() as c:
        incident = H.task_create(c, 'bot:cpo', 'Restore sign-in', 'Repair the outage', 'bot:ops', labels=['incident'])
        c.execute('DELETE FROM jobs')
        msg, job = sent_job(c, incident, 'bot:cpo', 'Sign-in works again for most people')
        assert msg['refs']['rule'] == 'incident_news'
    assert claim(api, r, 'cpo') is None
    with api.app.state.store.read() as c:
        if job is not None:
            assert c.execute('SELECT state FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'canceled'
        assert c.execute('SELECT 1 FROM messages WHERE id=?', (msg['id'],)).fetchone()
