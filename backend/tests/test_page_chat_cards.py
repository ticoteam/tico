"""The Goals card (spec chat-on-every-page, Release 2): the Goals page's owner bot proposes a list of goals as one card in
the person's chat with it; nothing is made until the person confirms, and only the rows they kept are made, as them,
recorded via the page chat (backend/page_chat_cards.py).

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). The ops bot runs on a test computer and owns
Goals here; the Strategy Owner is the default owner and has no computer.
"""

import json

from backend.store import H
from backend.tests.helpers.api import claim, get, post, put
from backend.tests.helpers.chat_goals import setup

FROM_GOALS = {'page': 'goals', 'label': 'Goals'}
ROWS = [
    {'title': '120 paying teams by March'},
    {'title': 'Churn under 3% a month'},
    {'title': 'Ship the Mac app to 50 teams'},
    {'title': 'NPS above 40'},
]


def goals_owned_by_ops(api):
    machine, _ = setup(api)
    put(api, 'page-owners/goals', {'bot': 'ops'})
    return machine


def said_from(api, page, person='cara-test', text='Add these goals'):
    """The person's message to ops from `page`, and the turn it starts (its token is the bot's for that turn)."""
    dm = post(api, 'threads/dm', {'members': ['bot:ops']}, person)
    return dm, post(
        api, f"threads/{dm['id']}/messages", {'text': text, 'refs': {'from_page': FROM_GOALS | {'page': page}}}, person
    )


def goal_titles(api):
    with api.app.state.store.read() as c:
        return sorted(r['title'] for r in c.execute('SELECT title FROM goals'))


def test_a_pasted_list_becomes_one_card_and_only_the_kept_rows_are_made_as_the_person(api):
    # AC-7: four rows, "Add 4 goals"; no goal yet. Drop one and confirm: three goals, Cara's, recorded via page_chat.
    machine = goals_owned_by_ops(api)
    dm, _ = said_from(api, 'goals')
    turn = claim(api, machine)
    card = post(api, 'page-chat/cards', {'page': 'goals', 'for_person': 'human:cara', 'rows': ROWS}, turn['token'])
    action = card['action']
    assert action['status'] == 'pending' and action['proposer'] == 'page:goals'
    assert [d['field'] for d in action['diff']] == [r['title'] for r in ROWS]
    assert {d['new'] for d in action['diff']} == {'Cara'}  # each row's owner, by name: the person by default
    assert goal_titles(api) == []
    with api.app.state.store.read() as c:
        msg = H.message(c, card['message_id'])
    assert (msg['sender_id'], msg['recipient_id'], msg['conversation_id']) == ('bot:ops', 'human:cara', dm['id'])
    # The bot cannot confirm its own card, and nobody else can see Cara's.
    post(api, f"assistant/actions/{action['id']}/confirm", {}, turn['token'], expected=403)
    post(api, f"assistant/actions/{action['id']}/confirm", {}, 'ben-test', expected=404)
    done = post(api, f"assistant/actions/{action['id']}/confirm", {'drop': [2]}, 'cara-test')['action']
    assert done['status'] == 'done', done
    assert goal_titles(api) == sorted(r['title'] for i, r in enumerate(ROWS) if i != 2)
    with api.app.state.store.read() as c:
        owners = {r['owner_id'] for r in c.execute('SELECT owner_id FROM goals')}
        made = [
            (r['actor'], json.loads(r['detail'] or '{}').get('via'))
            for r in c.execute("SELECT actor, detail FROM events WHERE action='goal.create'")
        ]
    assert owners == {'human:cara'}
    assert made == [('human:cara', 'page_chat')] * 3
    # What ran is what was kept: the stored card lists three steps and three lines, so the dropped one can never run
    # later and a reloaded card never shows it as made.
    assert len(done['body']['actions']) == 3
    stored = get(api, f"assistant/actions/{action['id']}", 'cara-test')['action']
    assert [d['field'] for d in stored['diff']] == [r['title'] for i, r in enumerate(ROWS) if i != 2]
    assert len(stored['body']['actions']) == 3


def test_a_card_with_every_row_dropped_or_a_drop_out_of_range_makes_nothing(api):
    machine = goals_owned_by_ops(api)
    said_from(api, 'goals')
    turn = claim(api, machine)
    card = post(api, 'page-chat/cards', {'page': 'goals', 'for_person': 'human:cara', 'rows': ROWS[:2]}, turn['token'])
    aid = card['action']['id']
    post(api, f"assistant/actions/{aid}/confirm", {'drop': [0, 1]}, 'cara-test', expected=422)
    post(api, f"assistant/actions/{aid}/confirm", {'drop': [5]}, 'cara-test', expected=422)
    assert goal_titles(api) == []
    assert post(api, f"assistant/actions/{aid}/confirm", {}, 'cara-test')['action']['status'] == 'done'
    assert goal_titles(api) == sorted(r['title'] for r in ROWS[:2])


def test_only_the_goals_owner_for_a_person_who_just_wrote_from_goals_gets_a_card(api):
    # AC-8: not the page's owner, or no message from Goals in the last 30 minutes: 403 and no card.
    machine = goals_owned_by_ops(api)
    said_from(api, 'listening')  # from another page: not a Goals request
    turn = claim(api, machine)
    body = {'page': 'goals', 'for_person': 'human:cara', 'rows': ROWS}
    post(api, 'page-chat/cards', body, turn['token'], expected=403)
    post(api, 'page-chat/cards', {**body, 'for_person': 'human:ben'}, turn['token'], expected=403)
    _, sent = said_from(api, 'goals')
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE messages SET created_at=? WHERE id=?', (H.shift(H.now(), minutes=-31), sent['id']))
    post(api, 'page-chat/cards', body, turn['token'], expected=403)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE messages SET created_at=? WHERE id=?', (H.now(), sent['id']))
    # Goals moves to another owner: ops' card is refused at once.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('strategy-owner','strategy-owner','Strategy Owner','active')"
        )
    put(api, 'page-owners/goals', {'bot': 'strategy-owner'})
    post(api, 'page-chat/cards', body, turn['token'], expected=403)
    # A person cannot make a card for themselves, and only Goals takes cards.
    post(api, 'page-chat/cards', body, 'cara-test', expected=403)
    put(api, 'page-owners/goals', {'bot': 'ops'})
    post(api, 'page-chat/cards', {**body, 'page': 'listening'}, turn['token'], expected=422)
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM assistant_actions').fetchone()[0] == 0
    assert post(api, 'page-chat/cards', body, turn['token'])['action']['status'] == 'pending'


def test_a_card_takes_one_to_fifty_rows_each_with_a_title(api):
    machine = goals_owned_by_ops(api)
    said_from(api, 'goals')
    turn = claim(api, machine)
    for rows in ([], [{'title': f'Goal {n}'} for n in range(51)], [{'title': ''}], [{'title': 'x', 'colour': 'red'}]):
        post(
            api,
            'page-chat/cards',
            {'page': 'goals', 'for_person': 'human:cara', 'rows': rows},
            turn['token'],
            expected=422,
        )
    assert goal_titles(api) == []


def test_a_bot_that_does_not_own_goals_gets_no_card_even_while_the_person_writes_to_the_owner(api):
    # AC-8, the owner half alone: Cara's Goals message to the owner is fresh; ops, no longer the owner, is refused.
    machine = goals_owned_by_ops(api)
    said_from(api, 'goals')
    turn = claim(api, machine)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('strategy-owner','strategy-owner','Strategy Owner','active')"
        )
    put(api, 'page-owners/goals', {'bot': 'strategy-owner'})
    dm = post(api, 'threads/dm', {'members': ['bot:strategy-owner']}, 'cara-test')
    post(
        api, f"threads/{dm['id']}/messages", {'text': 'Add these goals', 'refs': {'from_page': FROM_GOALS}}, 'cara-test'
    )
    post(
        api, 'page-chat/cards', {'page': 'goals', 'for_person': 'human:cara', 'rows': ROWS}, turn['token'], expected=403
    )
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM assistant_actions').fetchone()[0] == 0


def test_a_card_goes_only_into_the_persons_own_chat_with_the_bot(api):
    # A Goals message in a chat someone else is in is not the person's own chat with the owner: no card there.
    machine = goals_owned_by_ops(api)
    dm, _ = said_from(api, 'goals')
    turn = claim(api, machine)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO thread_members(id, thread_id, actor, added_by, created_at, updated_at) "
            "VALUES(?, ?, 'human:ben', 'human:cara', ?, ?)",
            (H.new_id(), dm['id'], H.now(), H.now()),
        )
    post(
        api, 'page-chat/cards', {'page': 'goals', 'for_person': 'human:cara', 'rows': ROWS}, turn['token'], expected=403
    )
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM assistant_actions').fetchone()[0] == 0


def test_a_stored_page_card_with_another_route_runs_nothing(api):
    # A Goals card holds only POST /api/v2/goals steps: one stored with any other route fails at Confirm, and nothing runs.
    machine = goals_owned_by_ops(api)
    said_from(api, 'goals')
    turn = claim(api, machine)
    card = post(api, 'page-chat/cards', {'page': 'goals', 'for_person': 'human:cara', 'rows': ROWS[:1]}, turn['token'])
    other = {
        'actions': [{'method': 'POST', 'path': '/api/v2/tasks', 'body': {'title': 'Not a goal', 'owner': 'human:cara'}}]
    }
    with api.app.state.store.transaction() as c:
        tasks = c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        c.execute('UPDATE assistant_actions SET body=? WHERE id=?', (json.dumps(other), card['action']['id']))
    done = post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, 'cara-test')['action']
    assert done['status'] == 'failed', done
    with api.app.state.store.read() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0] == tasks
    assert goal_titles(api) == []
