"""Busy turns B: only disposable synthetic orgs, fake workers and no provider calls."""

import json
from concurrent.futures import ThreadPoolExecutor

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_start_places import waiters


REASON = "Your company is at its limit; Tico is fixing what's stuck."


def people(api, rows):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps({'people': rows}),))


def one_person(api):
    people(api, [{'id': 'ana', 'email': 'ana@acme.example'}])


def independent(api, machine):
    assign(api, machine, 'finance')
    ready(api, machine, ['ops', 'finance'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=200 WHERE id=?', (machine['runner_id'],))
    post(api, 'chat/finance', {'text': 'Independent synthetic work'})


def age_claim(machine):
    """Make the runner's latest claim as old as an idle runner's backup-poll backoff, without a new claim."""
    from backend import execution
    seen = execution.CLAIMED_BUSY[machine['runner_id']]
    execution.CLAIMED_BUSY[machine['runner_id']] = (*seen[:2], seen[2] - execution.CLAIMED_FRESH - 180)


def test_spec5_warning_starts_without_bot_alert_and_opens_health_incident(api):
    one_person(api)
    machine = waiters(api)
    independent(api, machine)
    with api.app.state.store.read() as c:
        before = c.execute('SELECT count(*) FROM messages').fetchone()[0]
    assert claim(api, machine, 'finance') is not None
    incident = next((x for x in get(api, 'health')['checks'] if x['id'] == 'company_turns'), None)
    assert incident and incident['items'][0]['opened_at']
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM messages').fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM events WHERE action='turns.warning_crossed'").fetchone()[0] == 1
    # Repeated scans update the same incident, without duplicate hooks or bot notices.
    from backend import org_turns
    with api.app.state.store.transaction() as c:
        org_turns.observe(c, api.app.state.store.settings)
        assert c.execute("SELECT count(*) FROM events WHERE action='turns.warning_crossed'").fetchone()[0] == 1


def test_spec10_stop_is_org_wide_and_only_new_work_waits_until_count_drops(api):
    one_person(api)
    machine = waiters(api, n=90, running=10)
    other = runner(api, label='Other synthetic computer')
    independent(api, other)
    answer = post(api, 'jobs/claim', {'bot': 'finance', 'start_places': True}, other['token'])
    assert answer['attempt'] is None and answer['waiting'] == REASON
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE state='running' AND stop_requested_at IS NULL").fetchone()[0] == 10
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-99'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-99'")
    assert claim(api, other, 'finance') is not None


def test_spec11_only_sign_ins_count_guests_do_not_and_empty_org_counts_one(api):
    people(api, [{'id': p, 'email': p + '@acme.example'} for p in ['ana', 'ben', 'cara', 'dana']]
           + [{'id': 'guest', 'email': ''}, {'id': 'left', 'email': 'left@acme.example', 'hidden': True},
              {'id': 'off', 'email': 'off@acme.example', 'sign_in': False}])
    value = get(api, 'settings/turn-levels')
    assert (value['people'], value['warning'], value['stop']) == (4, 160, 400)
    people(api, [])
    value = get(api, 'settings/turn-levels')
    assert (value['people'], value['warning'], value['stop']) == (1, 40, 100)


def test_settings_turn_levels_get_uses_admission_not_fleet_snapshot(api):
    """The settings page only renders levels and in_use; classifying every queued bot is wasted work."""
    value = get(api, 'settings/turn-levels')
    assert 'in_use' in value and 'at_stop' in value and 'warning_per_person' in value
    assert 'running' not in value and 'queued' not in value and 'waiting' not in value


def test_spec15_admin_levels_persist_apply_and_non_admin_is_denied_by_server(api):
    """Synthetic permission regression; acceptance 15 still requires a separate live check."""
    value = post(api, 'settings/turn-levels', {'warning_per_person': 5, 'stop_per_person': 9}, 'ben-test')
    assert (value['warning_per_person'], value['stop_per_person'], value['warning'], value['stop']) == (5, 9, 15, 27)
    post(api, 'settings/turn-levels', {'warning_per_person': 1, 'stop_per_person': 2}, 'cara-test', expected=403)
    assert get(api, 'settings/turn-levels')['stop_per_person'] == 9
    for body in ({'warning_per_person': 0, 'stop_per_person': 1}, {'warning_per_person': 3, 'stop_per_person': 2},
                 {'warning_per_person': True, 'stop_per_person': 4}):
        post(api, 'settings/turn-levels', body, expected=422)


def test_recovery_is_fifo_across_computers_and_new_requests_cannot_jump_waiters(api):
    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    other = runner(api, label='Other synthetic computer')
    assign(api, other, 'doc-updater')
    ready(api, other, ['doc-updater'])
    post(api, 'chat/doc-updater', {'text': 'Younger synthetic request'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot='finance' AND state='queued'", (H.shift(H.now(), minutes=-2),))
        c.execute("UPDATE jobs SET created_at=? WHERE bot='doc-updater' AND state='queued'", (H.shift(H.now(), minutes=-1),))
    assert claim(api, other, 'doc-updater') is None
    assert claim(api, machine, 'finance') is None
    # Answered "waiting", the runner backs off to its backup poll: longer than CLAIMED_FRESH, still online.
    age_claim(machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-99'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-99'")
    assert claim(api, other, 'doc-updater') is None  # Older, ready work on the other computer goes first.
    assert claim(api, machine, 'finance') is not None
    assert claim(api, other, 'doc-updater') is None  # Its predecessor consumed the last company place.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-98'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-98'")
    assert claim(api, other, 'doc-updater') is not None


def test_a_cloud_machine_being_stopped_does_not_hold_other_computers_behind_its_waiter(api):
    """The oldest waiter's computer is a cloud machine reserved for a stop (backend/cloud_machines.py `held`): it
    takes no turn, so the next computer's waiter goes instead of every computer waiting on it."""
    import time
    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    other = runner(api, label='Other synthetic computer')
    assign(api, other, 'doc-updater')
    ready(api, other, ['doc-updater'])
    post(api, 'chat/doc-updater', {'text': 'Younger synthetic request'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot='finance' AND state='queued'", (H.shift(H.now(), minutes=-2),))
        c.execute("UPDATE jobs SET created_at=? WHERE bot='doc-updater' AND state='queued'", (H.shift(H.now(), minutes=-1),))
    assert claim(api, other, 'doc-updater') is None
    assert claim(api, machine, 'finance') is None
    age_claim(machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-99'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-99'")
        c.execute("INSERT INTO cloud_machines(bot,runner_id,state,stop_until) VALUES('finance',?,'running',?)",
                  (machine['runner_id'], time.time() + 60))
    assert claim(api, other, 'doc-updater') is not None


def test_recovery_does_not_hold_other_computers_behind_a_lane_busy_only_on_its_runner(api):
    """A lane the attempts table does not show (a worker whose lease lapsed) is known only from the runner's
    claims. Claims during the stop must keep it, or recovery waits on a job its own computer will not take."""
    from clients import sessions
    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    other = runner(api, label='Other synthetic computer')
    assign(api, other, 'doc-updater')
    ready(api, other, ['doc-updater'])
    post(api, 'chat/doc-updater', {'text': 'Younger synthetic request'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot='finance' AND state='queued'", (H.shift(H.now(), minutes=-2),))
        c.execute("UPDATE jobs SET created_at=? WHERE bot='doc-updater' AND state='queued'", (H.shift(H.now(), minutes=-1),))
    busy = {'bot': 'finance', 'start_places': True, 'busy_lanes': ['finance|' + sessions.MAIN_LANE]}
    assert post(api, 'jobs/claim', busy, machine['token'])['waiting'] == REASON
    # The runner backs off past CLAIMED_FRESH without claiming again; its last busy list still holds the lane.
    age_claim(machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-99'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-99'")
    assert claim(api, other, 'doc-updater') is not None


def test_two_computers_claim_the_last_company_place_atomically(api):
    one_person(api)
    machine = waiters(api, n=98, running=1)
    independent(api, machine)
    other = runner(api, label='Other synthetic computer')
    assign(api, other, 'doc-updater')
    ready(api, other, ['doc-updater'])
    post(api, 'chat/doc-updater', {'text': 'Contending synthetic request'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda pair: claim(api, *pair), [(machine, 'finance'), (other, 'doc-updater')]))
    assert sum(answer is not None for answer in answers) == 1
    from backend import org_turns
    with api.app.state.store.read() as c:
        assert org_turns.snapshot(c, api.app.state.store.settings)['in_use'] == 100


def test_changed_levels_apply_immediately_and_revoked_admin_cannot_replay_a_save(api):
    import uuid
    from backend.tests.test_api import as_member
    one_person(api)
    machine = waiters(api, n=3, running=1)
    independent(api, machine)
    body, key = {'warning_per_person': 2, 'stop_per_person': 4}, str(uuid.uuid4())
    post(api, 'settings/turn-levels', body, 'ben-test', key=key)
    assert claim(api, machine, 'finance') is None
    as_member(api, 'ben@acme.example')
    post(api, 'settings/turn-levels', body, 'ben-test', key=key, expected=403)
    post(api, 'settings/turn-levels', {'warning_per_person': 3, 'stop_per_person': 5})
    assert claim(api, machine, 'finance') is not None


def test_incident_resolves_and_recrossing_opens_one_new_incident(api):
    from backend import org_turns
    one_person(api)
    machine = waiters(api, n=39, running=1)
    with api.app.state.store.transaction() as c:
        org_turns.observe(c, api.app.state.store.settings)
        first = org_turns.saved(c, org_turns.INCIDENT)
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-39'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-39'")
        org_turns.observe(c, api.app.state.store.settings)
        assert org_turns.saved(c, org_turns.INCIDENT)['resolved_at']
    independent(api, machine)
    assert claim(api, machine, 'finance') is not None
    with api.app.state.store.read() as c:
        second = org_turns.saved(c, org_turns.INCIDENT)
        assert second['active'] and second['id'] != first['id']
        assert c.execute("SELECT count(*) FROM events WHERE action='turns.warning_crossed'").fetchone()[0] == 2


def test_owner_issue_lists_read_an_open_and_a_resolved_incident(api):
    from backend import org_turns
    one_person(api)
    waiters(api, n=39, running=1)
    with api.app.state.store.transaction() as c:
        org_turns.observe(c, api.app.state.store.settings)
    for path in ('fleet/check', 'health/issues'):
        issue = next(i for i in get(api, path)['issues'] if i['kind'] == 'company_turns')
        assert issue['name'] == 'Company turns' and issue['opened_at'] and issue['severity'] == 'medium'
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-39'")
        c.execute("UPDATE jobs SET state='completed' WHERE attempt_id='attempt-39'")
        org_turns.observe(c, api.app.state.store.settings)
    for path in ('fleet/check', 'health/issues'):
        assert not any(i['kind'] == 'company_turns' for i in get(api, path)['issues'])


def test_stop_level_idle_claim_keeps_computer_contact_fresh(api, monkeypatch):
    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    assert claim(api, machine, 'finance') is None
    now = H.now()
    monkeypatch.setattr(H, 'now', lambda: H.shift(now, seconds=20))
    assert claim(api, machine, 'finance') is None
    with api.app.state.store.read() as c:
        assert c.execute('SELECT last_seen_at FROM runners WHERE id=?', (machine['runner_id'],)).fetchone()[0] == H.now()


def test_stop_level_counted_waiter_takes_its_place_while_new_claims_wait(api):
    """Every counted turn waits on a login: reserving one frees nothing, so it must not be refused."""
    one_person(api)
    machine = waiters(api, n=100, running=0)
    other = runner(api, label='Other synthetic computer')
    independent(api, other)
    assert claim(api, other, 'finance') is None
    grant = post(api, 'attempts/attempt-0/start-place', {}, machine['token'])
    assert grant['reserved'] is True
    from backend import org_turns
    with api.app.state.store.read() as c:
        assert org_turns.snapshot(c, api.app.state.store.settings)['in_use'] == 100
    answer = post(api, 'jobs/claim', {'bot': 'finance', 'start_places': True}, other['token'])
    assert answer['attempt'] is None and answer['waiting'] == REASON


def test_stop_level_wait_is_a_warning_not_a_stuck_bot_outage(api):
    """The configured stop is reported by the company-turns incident; a person's message held by it is not an outage."""
    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    assert claim(api, machine, 'finance') is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot='finance' AND state='queued'", (H.shift(H.now(), minutes=-3),))
    queue = next(x for x in get(api, 'health')['checks'] if x['id'] == 'queue')
    assert queue['status'] == 'warn' and any(i.get('reason') == 'company_limit' for i in queue['items'])


def test_queued_jobs_that_never_started_do_not_count_toward_the_stop(api):
    """Work held by quota, readiness or a paused bot holds no place: counting it stopped the company with nothing running."""
    one_person(api)
    machine = waiters(api, n=0, running=150)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='queued',attempt_id=NULL WHERE bot='ops' AND attempt_id!='attempt-0'")
        c.execute("DELETE FROM attempts WHERE bot='ops' AND id!='attempt-0'")
        # A leased start whose start window lapsed gave up its place, though its lease is still live.
        c.execute("UPDATE attempts SET state='leased' WHERE id='attempt-0'")
        c.execute("INSERT INTO start_places(attempt_id,start_until,managed) VALUES('attempt-0',?,1)",
                  (H.shift(H.now(), seconds=-1),))
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    from backend import org_turns
    with api.app.state.store.transaction() as c:
        value = org_turns.observe(c, api.app.state.store.settings)
        assert value['waiting'] == 149 and value['in_use'] == 0 and not value['at_stop']
        assert c.execute('SELECT count(*) FROM org_turn_waits').fetchone()[0] == 0
    other = runner(api, label='Other synthetic computer')
    independent(api, other)
    assert claim(api, other, 'finance') is not None


def test_waiters_are_dropped_below_the_warning_so_claims_stop_taking_the_write_lock(api, monkeypatch):
    """A waiter that cannot run (its bot cooling off a limit) stayed one for good, and while any waiter was left every
    claim of every computer went to the write lock to check them all: 5-10 s holds per claim on a live install."""
    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    assert claim(api, machine, 'finance') is None
    with api.app.state.store.transaction() as c:
        assert c.execute('SELECT count(*) FROM org_turn_waits').fetchone()[0] == 1
        c.execute("UPDATE attempts SET state='completed' WHERE state IN ('leased','running')")
        c.execute("UPDATE jobs SET state='completed' WHERE state IN ('leased','running')")
        c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")
    assert claim(api, machine, 'finance') is None
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM org_turn_waits').fetchone()[0] == 0
    wrote = []
    monkeypatch.setattr(api.app.state.execution, 'claim', lambda *a, **k: wrote.append(1) or {'attempt': None})
    assert claim(api, machine, 'finance') is None
    assert not wrote                # answered on the read connection


def test_claim_admission_and_quiet_fixer_do_not_classify_the_queued_backlog(api, monkeypatch):
    """Detailed queue explanations must not run inside each claim's writer transaction."""
    from backend import start_places, turn_fixer
    machine = runner(api)
    assign(api, machine, 'ops')
    ready(api, machine, ['ops'])
    post(api, 'chat/ops', {'text': 'A synthetic request still starts while the backlog is large.'})

    def detailed(*args, **kwargs):
        raise AssertionError('Admission must not classify every queued bot')

    monkeypatch.setattr(start_places, 'summary', detailed)
    attempt = claim(api, machine, 'ops')
    assert attempt is not None
    assert post(api, 'attempts/' + attempt['id'] + '/start-place', {}, machine['token'])['reserved']
    with api.app.state.store.transaction() as c:
        assert not turn_fixer.tend(c, api.app.state.store.settings).get('active')


def test_lightweight_observation_keeps_full_counts_only_on_warning_crossings(api, monkeypatch):
    from backend import org_turns, start_places
    one_person(api)
    machine = waiters(api, n=1, running=1)
    store = api.app.state.store
    with store.transaction() as c:
        org_turns.save(c, org_turns.LEVELS, {'warning_per_person': 2, 'stop_per_person': 3})
    summaries = []
    original = start_places.summary

    def detailed(c, rid, *args, **kwargs):
        summaries.append(rid)
        return original(c, rid, *args, **kwargs)

    monkeypatch.setattr(start_places, 'summary', detailed)
    with store.transaction() as c:
        first = org_turns.observe(c, store.settings, detailed=False)
        assert first['in_use'] == 2 and first['at_warning'] and not first['at_stop']
        assert set(('running', 'starting', 'waiting', 'queued')) <= first.keys()
        assert summaries == [machine['runner_id']]
        again = org_turns.observe(c, store.settings, detailed=False)
        assert again['in_use'] == 2 and summaries == [machine['runner_id']]
        c.execute("UPDATE attempts SET state='completed' WHERE id='attempt-1'")
        cleared = org_turns.observe(c, store.settings, detailed=False)
        assert cleared['in_use'] == 1 and not cleared['at_warning']
        assert summaries == [machine['runner_id'], machine['runner_id']]
        incident = org_turns.saved(c, org_turns.INCIDENT)
        assert not incident['active'] and incident['counts']['in_use'] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action='turns.warning_crossed'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action='turns.warning_cleared'").fetchone()[0] == 1


def test_health_and_queued_labels_do_not_classify_the_fleet_for_at_stop(api, monkeypatch):
    """Health's stuck-queue gate and chat queued labels only need the turn ceiling, not every bot's refusal.

    Per-computer Health lines still call start_places.summary for their own counts; the company at_stop
    decision and the company-turns check must not rebuild a whole-fleet snapshot on every poll.
    """
    from backend import org_turns, views

    one_person(api)
    machine = waiters(api, n=99, running=1)
    independent(api, machine)
    assert claim(api, machine, 'finance') is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot='finance' AND state='queued'",
                  (H.shift(H.now(), minutes=-3),))
    monkeypatch.setattr(org_turns, 'snapshot', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('at_stop and company-turns Health paths must use admission, not snapshot')))
    health = get(api, 'health')
    queue = next(x for x in health['checks'] if x['id'] == 'queue')
    assert any(i.get('reason') == 'company_limit' for i in queue['items'])
    company = next(x for x in health['checks'] if x['id'] == 'company_turns')
    assert company['status'] == 'warn' and company['items'][0]['at_stop']
    with api.app.state.store.read() as c:
        location = {'online': True, 'awake': True, 'ready': True, 'machine': {'runner_id': machine['runner_id'],
                                                                              'label': 'Synthetic'}}
        label, _ = views.queued_label(c, 'finance', location, api.app.state.store.settings, human=True)
    assert label == 'Saved — ' + org_turns.REASON


def test_snapshot_shares_one_company_default_across_runners(api, monkeypatch):
    """Each claim_refusal used to rebuild company pools/groups; one Default memo covers the whole pass."""
    from backend import org_turns, usage_limits

    first = waiters(api, n=0, running=0)
    second = runner(api, label='Other synthetic computer')
    assign(api, second, 'finance')
    ready(api, second, ['finance'])
    calls = []
    real = usage_limits.company

    def once(c):
        calls.append(1)
        return real(c)

    monkeypatch.setattr(usage_limits, 'company', once)
    with api.app.state.store.read() as c:
        value = org_turns.snapshot(c, api.app.state.store.settings)
        runners = {row['id'] for row in c.execute('SELECT id FROM runners')}
    assert {first['runner_id'], second['runner_id']} <= runners
    assert len(calls) == 1
    assert set(('running', 'starting', 'waiting', 'queued')) <= value.keys()


def test_turns_in_use_reads_the_live_attempts_index_not_every_attempt(api):
    """Every claim counts the company's turns: a scan of all attempts ever run took ~90 ms a call on a live install."""
    from backend import org_turns
    with api.app.state.store.read() as c:
        sent = []
        c.set_trace_callback(sent.append)
        org_turns.in_use(c)
        c.set_trace_callback(None)
        count = next(sql for sql in sent if "count(*) FROM attempts" in sql)
        plan = " | ".join(row[-1] for row in c.execute("EXPLAIN QUERY PLAN " + count))
    assert "attempts_live_lease" in plan, plan
