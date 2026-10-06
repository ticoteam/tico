"""Connection labels never change login keys, assignments, or historical attribution."""
from backend import hubdb as H
from backend.subscription_identity import connection_id
from backend.tests.test_api import api, assign, get, post, put, runner  # noqa: F401


def report(api, computer, profiles=None):
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {},
        'profiles': profiles if profiles is not None else [
            {'name': 'engineering', 'runtimes': {'codex': {'signed_in': True}}}]}, token=computer['token'])


def profiles(api):
    return {c['runner_id']: c['profiles'] for c in get(api, 'subscriptions')['profiles_by_computer']}


def test_rename_checks_computer_operator_and_known_profile(api):
    a, b = runner(api, operator='cara'), runner(api)
    report(api, a)
    report(api, b)
    change = {'runner_id': a['runner_id'], 'profile': 'engineering', 'display_name': 'Personal plan'}
    put(api, 'subscriptions/name', change, token='cara-test')
    put(api, 'subscriptions/name', {**change, 'runner_id': b['runner_id']}, token='cara-test', expected=403)
    put(api, 'subscriptions/name', change, token=a['token'], expected=403)
    put(api, 'subscriptions/name', {**change, 'profile': 'missing'}, expected=404)
    put(api, 'subscriptions/name', {**change, 'display_name': '   '}, expected=422)
    put(api, 'subscriptions/name', {**change, 'display_name': 'x' * 81}, expected=422)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET revoked_at=? WHERE id=?', (H.now(), a['runner_id']))
    put(api, 'subscriptions/name', change, expected=403)


def usage_turn(c, tid, computer=None, bot='ops'):
    now = H.now()
    c.execute('INSERT INTO turns(id,bot,started,finished) VALUES(?,?,?,?)', (tid, bot, now, now))
    part = {'input_tokens': 100, 'cached_tokens': 0, 'output_tokens': 10, 'model': 'gpt-6.1-sol',
            'provider': 'openai', 'est_cost_usd': 0.01, 'billing': 'subscription',
            'runtime': 'codex', 'harness': 'codex', 'effort': 'high', 'profile': 'engineering'}
    H.turn_finish(c, H.KEEPER, tid, usage={**part, 'segments': [part]})
    if computer:
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES(?,'keeper',?,'notice','Fixture',?)",
                  ('message-' + tid, 'bot:' + bot, now))
        job = c.execute('SELECT id FROM jobs WHERE message_id=?', ('message-' + tid,)).fetchone()[0]
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
        c.execute('INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) '
                  "VALUES(?,?,?, ?,1,?,'completed',?,?)", (tid, job, bot, computer['runner_id'], tid, now, now))


def test_usage_groups_immutable_connections_and_rename_preserves_filter(api):
    a, b = runner(api, label='North'), runner(api, label='South')
    report(api, a)
    report(api, b)
    with api.app.state.store.transaction() as c:
        usage_turn(c, 'north-run', a)
        usage_turn(c, 'south-run', b)
        usage_turn(c, 'old-run')
    first = get(api, 'usage?group=subscription')
    aid, bid = connection_id(a['runner_id'], 'engineering'), connection_id(b['runner_id'], 'engineering')
    assert {r['value'] for r in first['rows']} == {aid, bid, 'legacy-profile:engineering'}
    assert first['totals']['runs'] == 3
    put(api, 'subscriptions/name', {'runner_id': a['runner_id'], 'profile': 'engineering', 'display_name': 'North plan'})
    # Moving the bot now cannot relabel past turns as the new computer's subscription.
    assign(api, b, 'ops')
    second = get(api, 'usage?group=subscription')
    assert second['dimension_labels']['subscription'][aid] == 'North plan · North'
    assert second['dimension_labels']['subscription'][bid] == 'engineering · South'
    assert {r['value'] for r in first['rows']} == {r['value'] for r in second['rows']}
    selected = get(api, 'usage?group=subscription&subscription=' + aid)
    assert selected['totals']['runs'] == 1
    assert selected['rows'][0]['name'] == 'North plan · North'
    assert get(api, 'usage?group=subscription', 'cara-test')['rows'] == []
    with api.app.state.store.read() as c:
        assert {r[0] for r in c.execute('SELECT profile FROM turn_usage_segments')} == {'engineering'}
