"""The Tools chat's key detector and the server backstop (backend/secret_drop.py, spec tools-chat-key-drop).

The case file is shared with the page's detector (ui/tests/secret-drop-unit.cts): both must agree on every row (AC-17).
The ops bot owns Tools here; Ana is the owner (the base fixture).
"""

import json
from pathlib import Path

from backend import secret_drop
from backend.tests.helpers.api import claim, get, headers, post, put
from backend.tests.helpers.cards import patch
from backend.tests.helpers.chat_goals import setup

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'secret_drop_cases.json').read_text())
# Fake keys are stored split in the case file and joined here, so this file holds none either.
KEY = ''.join(['gh', 'p_', 'aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z'])
SUSPECT = 'k3JdP9xQa8LmZ2vR7tYb'


def _join(v):
    return ''.join(v) if isinstance(v, list) else v


def test_every_shared_case_gives_the_items_the_page_gives():
    # AC-2..5, AC-17: the same rows the page's test runs, with the same tier, value and named fields.
    for case in CASES['cases']:
        got = secret_drop.scan(_join(case['text']))
        assert len(got) == len(case['expect']), (case['id'], [(g['tier'], g['name']) for g in got])
        for g, e in zip(got, case['expect']):
            for k, v in e.items():
                assert g[k] == _join(v), (case['id'], k)


def test_ordinary_tools_sentences_give_no_certain_item_and_at_most_two_suspects():
    # AC-11
    found = [i for s in CASES['ordinary'] for i in secret_drop.scan(s)]
    assert [i for i in found if i['tier'] == 'certain'] == []
    assert len([i for i in found if i['tier'] == 'suspect']) <= 2


def _tools_dm(api):
    machine, _ = setup(api)
    put(api, 'page-owners/tools', {'bot': 'ops'})
    return machine, post(api, 'threads/dm', {'members': ['bot:ops']})


def _bodies(api, thread_id):
    return [m['body'] for m in get(api, f'threads/{thread_id}/timeline?limit=50&flat=1')['messages']]


def test_a_key_in_a_message_to_the_tools_owner_is_refused_before_it_is_kept_or_wakes_the_bot(api):
    # AC-15 (web and API): refused before anything is stored, no run starts, the refusal does not repeat the value.
    machine, dm = _tools_dm(api)
    r = api.post(
        f"/api/v2/threads/{dm['id']}/messages",
        json={'text': 'for github ' + KEY},
        headers={'Authorization': 'Bearer ana-test'},
    )
    assert r.status_code == 422 and r.json()['error']['code'] == 'looks_like_key'
    assert KEY not in r.text
    assert not any(KEY in (b or '') for b in _bodies(api, dm['id']))
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM messages WHERE body LIKE ?', ('%' + KEY + '%',)).fetchone()
        assert not c.execute('SELECT 1 FROM events WHERE detail LIKE ?', ('%' + KEY + '%',)).fetchone()


def test_a_hex_or_uuid_value_after_a_key_word_is_refused(api):
    # AC-15 for the values Validate saw stored (task #438): hex and UUIDs are ordinary only with no key word before them.
    machine, dm = _tools_dm(api)
    hexed = ''.join(['9c1e4', 'b7a20', 'd5f83', '6e0c2', 'a9b14', '7d3f8', 'e2c60', 'a5b1'])
    keyed = ''.join(['3f6c2', 'a1e-4', 'b7d-4', 'e9a-8', 'c25-0', 'd4e6f', '7a8b9', 'c'])
    for text, value in (
        ('token: ' + hexed, hexed),
        ('Authorization: Bearer ' + hexed, hexed),
        ('HEROKU_API_KEY=' + keyed, keyed),
    ):
        r = api.post(f"/api/v2/threads/{dm['id']}/messages", json={'text': text}, headers=headers('ana-test'))
        assert r.status_code == 422 and r.json()['error']['code'] == 'looks_like_key', text
        assert value not in r.text
    assert _bodies(api, dm['id']) == []
    assert claim(api, machine) is None
    # Without a key word the same hex is a commit or a hash, and goes through.
    post(api, f"threads/{dm['id']}/messages", {'text': 'deployed ' + hexed})


def test_names_only_go_through_and_reach_the_bots_turn(api):
    # AC-10: the message carries [saved NAME] in the key's place and the turn reads it.
    machine, dm = _tools_dm(api)
    post(api, f"threads/{dm['id']}/messages", {'text': 'for github [saved GITHUB_TOKEN]'})
    assert claim(api, machine)['message']['body'] == 'for github [saved GITHUB_TOKEN]'


def test_a_suspect_goes_through_only_when_the_person_unmarked_it_in_the_web_chat(api):
    # AC-15 (suspect): refused without the un-mark; let through with it, with an audit row holding no value.
    _, dm = _tools_dm(api)
    post(api, f"threads/{dm['id']}/messages", {'text': 'build ' + SUSPECT}, expected=422)
    other = secret_drop.digest('k3JdP9xQa8LmZ2vR7tYc')
    post(api, f"threads/{dm['id']}/messages", {'text': 'build ' + SUSPECT, 'unmarked': [other]}, expected=422)
    sent = post(
        api, f"threads/{dm['id']}/messages", {'text': 'build ' + SUSPECT, 'unmarked': [secret_drop.digest(SUSPECT)]}
    )
    assert 'unmarked' not in sent['refs']
    with api.app.state.store.read() as c:
        row = c.execute("SELECT detail FROM events WHERE action='secret_drop.suspect_let_through'").fetchone()
        assert row and SUSPECT not in row['detail'] and secret_drop.digest(SUSPECT) not in row['detail']


def test_an_edit_and_a_first_message_holding_a_key_are_refused_too(api):
    # AC-15 (edit, and the DM's first message before the DM exists).
    setup(api)
    put(api, 'page-owners/tools', {'bot': 'ops'})
    post(api, 'threads/dm', {'members': ['bot:ops'], 'body': 'here ' + KEY}, expected=422)
    dm = post(api, 'threads/dm', {'members': ['bot:ops']})
    sent = post(api, f"threads/{dm['id']}/messages", {'text': 'one moment'})
    patch(api, f"messages/{sent['id']}", {'text': 'here ' + KEY}, expected=422)
    assert _bodies(api, dm['id']) == ['one moment']


def test_a_slack_edit_into_the_tools_owners_conversation_is_refused():
    # AC-15 (Slack): the refusal names where the key still is.
    import pytest
    from backend.store import Problem

    with pytest.raises(Problem) as refused:
        secret_drop.refuse_found(None, secret_drop.scan(KEY), 'human:ana', '', 'slack', None)  # type: ignore[arg-type]
    assert 'Slack' in refused.value.detail and KEY not in refused.value.detail


def test_other_conversations_and_bots_are_not_checked(api):
    # Out of scope: the detector is this chat's only (no other chat, and nothing a bot writes).
    setup(api)
    dm = post(api, 'threads/dm', {'members': ['bot:ops']})
    post(api, f"threads/{dm['id']}/messages", {'text': 'for github ' + KEY})
    with api.app.state.store.transaction() as c:
        assert not secret_drop.guarded(c, None)


# ---- the owner proposes, the person grants (backend/key_grants.py)
PASSWORD = ''.join(['Tr0ub', '4dor&3x'])


def _proposal_world(api, monkeypatch):
    from backend import api_tools
    from backend.tests.helpers import credentials as creds
    from backend.tests.helpers.api_tools import tool

    machine, dm = _tools_dm(api)
    creds.setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('support','support','Support','active')")
    tid = tool(api)['id']
    saved = post(
        api,
        'credentials',
        {
            'name': 'JIRA_LOGIN',
            'kind': 'password',
            'username': 'chris',
            'env': 'JIRA_LOGIN',
            'secret': PASSWORD,
            'source': 'tools-chat',
        },
    )
    post(api, f"threads/{dm['id']}/messages", {'text': 'for close [saved JIRA_LOGIN for Close CRM]'})
    bot = claim(api, machine)['token']
    sent = []
    monkeypatch.setattr(api_tools, 'send_check', lambda url, hdrs, params: sent.append(hdrs) or sent_code[0])
    sent_code = [200]
    return dm, tid, saved, bot, sent, sent_code


def _propose(api, dm, rows, token, expected=200):
    return post(api, 'tools-chat/proposals', {'thread_id': dm['id'], 'rows': rows}, token, expected=expected)


def test_the_card_never_offers_the_owner_every_bot_or_a_key_from_elsewhere(api, monkeypatch):
    # AC-16: the server builds the rows; the bot's words can't add itself, everyone or every computer.
    dm, tid, _, bot, _, _ = _proposal_world(api, monkeypatch)
    row = {'key': 'JIRA_LOGIN', 'tool': 'Close CRM', 'level': 'read_write'}
    for bots in (['ops'], ['all'], ['computers'], ['*'], ['no-such-bot']):
        _propose(api, dm, [row | {'bots': bots}], bot, expected=422)
    _propose(api, dm, [row | {'key': 'SOMEONE_ELSES'} | {'bots': ['support']}], bot, expected=422)
    _propose(api, dm, [row | {'bots': ['support']}], 'ana-test', expected=403)  # only the owner bot proposes
    # A person can't forge a card by posting its refs.
    post(
        api, f"threads/{dm['id']}/messages", {'text': 'x', 'refs': {'card': 'key_grant', 'key_grant': {}}}, expected=403
    )
    card = _propose(api, dm, [row | {'bots': ['support', 'support']}], bot)
    rows = card['refs']['key_grant']['rows']
    assert [(r['key'], r['tool_id'], r['level'], [b['id'] for b in r['bots']]) for r in rows] == [
        ('JIRA_LOGIN', tid, 'read_write', ['bot:support'])
    ]
    assert PASSWORD not in json.dumps(card)


def test_grant_makes_exactly_the_cards_grants_and_the_check_uses_the_username(api, monkeypatch):
    # AC-16 and AC-18: Grant attaches the key as the tool's key, grants the kept bots, runs the check with the
    # username, and the card says Connected or Didn't work.
    from backend import api_tools

    dm, tid, saved, bot, sent, code = _proposal_world(api, monkeypatch)
    card = _propose(api, dm, [{'key': 'JIRA_LOGIN', 'tool': tid, 'level': 'read', 'bots': ['support']}], bot)
    post(api, f"tools-chat/proposals/{card['id']}/grant", {}, 'cara-test', expected=403)  # not a credential admin
    done = post(api, f"tools-chat/proposals/{card['id']}/grant", {'keep': [0]})
    assert done['state'] == 'granted'
    assert done['results'] == [{'key': 'JIRA_LOGIN', 'tool_id': tid, 'ok': True, 'detail': 'Connected to Close CRM'}]
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, tid, 'bot:support') == 'read'
        assert api_tools.subject_level(c, tid, 'bot:ops') is None  # the owner holds none of the keys
        row = c.execute('SELECT tool_id, kind, username FROM credentials WHERE id=?', (saved['id'],)).fetchone()
        assert (row['tool_id'], row['kind'], row['username']) == (tid, 'api_key', 'chris')
    import base64

    assert sent and base64.b64decode(sent[-1]['Authorization'].split()[1]).decode().startswith('chris:')
    # A second Grant changes nothing; a failing check says why.
    assert post(api, f"tools-chat/proposals/{card['id']}/grant", {})['state'] == 'granted'
    code[0] = 401
    again = _propose(api, dm, [{'key': 'JIRA_LOGIN', 'tool': tid, 'level': 'read', 'bots': ['support']}], bot)
    failed = post(api, f"tools-chat/proposals/{again['id']}/grant", {})
    assert failed['results'][0]['ok'] is False and failed['results'][0]['detail'].startswith("Didn't work")


def test_a_key_that_replaces_the_tools_key_keeps_everyone_who_had_it_and_the_card_says_so(api, monkeypatch):
    # Review 1: Grant used to take the tool's old key away from every bot holding it, silently. Now each bot and person
    # holding the old key keeps their level on the new one, and the card names the key replaced and who keeps access.
    from backend import api_tools
    from backend.tests.helpers.api_tools import key, level

    dm, tid, saved, bot, _, _ = _proposal_world(api, monkeypatch)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('builder','builder','Builder','active')")
    old = key(api, tid, 'acme-close-synthetic-old-5566778899', 'read')
    level(api, tid, 'bot:support', 'read')
    card = _propose(api, dm, [{'key': 'JIRA_LOGIN', 'tool': tid, 'level': 'read', 'bots': ['builder']}], bot)
    row = card['refs']['key_grant']['rows'][0]
    assert row['replaces'] == {'key': old['name'], 'keeps': ['Support'], 'loses': []}
    post(api, f"tools-chat/proposals/{card['id']}/grant", {})
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, tid, 'bot:support') == 'read'
        assert api_tools.subject_level(c, tid, 'bot:builder') == 'read'
        held = c.execute(
            "SELECT credential_id FROM credential_grants WHERE subject='bot:support' AND revoked_at IS NULL"
        ).fetchall()
        assert [r['credential_id'] for r in held] == [saved['id']]


def test_replace_in_the_tools_chat_takes_the_key_from_the_tools_owner(api, monkeypatch):
    # Review 2: Replace kept every grant on the stored credential, so a key dropped in the chat reached the Tools owner
    # when it had held the old value. The write drops the owner's grant, and the key can still be proposed.
    dm, tid, _, bot, _, _ = _proposal_world(api, monkeypatch)
    stored = post(
        api, 'credentials', {'name': 'GITHUB_TOKEN', 'env': 'GITHUB_TOKEN', 'secret': 'acme-synthetic-old-1234567'}
    )
    post(api, f"credentials/{stored['id']}/grants", {'subject': 'bot:ops'})
    post(
        api,
        f"credentials/{stored['id']}",
        {
            'name': 'GITHUB_TOKEN',
            'env': 'GITHUB_TOKEN',
            'kind': 'token',
            'secret': KEY,
            'source': 'tools-chat',
            'expected_revision': 1,
        },
    )
    with api.app.state.store.read() as c:
        assert not c.execute(
            "SELECT 1 FROM credential_grants WHERE credential_id=? AND subject='bot:ops' AND revoked_at IS NULL",
            (stored['id'],),
        ).fetchone()
    _propose(api, dm, [{'key': 'GITHUB_TOKEN', 'tool': tid, 'level': 'read', 'bots': ['support']}], bot)


def test_a_dropped_key_for_a_tool_that_has_a_key_makes_no_extra_tool(api, monkeypatch):
    # Review 2: the vault put a dropped key on a new tool when its service's tool already had a key; it stayed in
    # Problem after Grant moved the key. A tools-chat key waits for Grant to place it.
    from backend.tests.helpers.api_tools import key

    dm, tid, _, bot, _, _ = _proposal_world(api, monkeypatch)
    key(api, tid, 'acme-close-synthetic-rw-0011223344')
    before = len(get(api, 'tool-index')['rows'])
    post(
        api,
        'credentials',
        {
            'name': 'CLOSE_API_KEY',
            'env': 'CLOSE_API_KEY',
            'secret': 'acme-close-new-9988776655',
            'source': 'tools-chat',
        },
    )
    assert len(get(api, 'tool-index')['rows']) == before
    card = _propose(api, dm, [{'key': 'CLOSE_API_KEY', 'tool': tid, 'level': 'read_write', 'bots': ['support']}], bot)
    post(api, f"tools-chat/proposals/{card['id']}/grant", {})
    assert len(get(api, 'tool-index')['rows']) == before


def test_the_owner_loses_the_old_key_and_a_holder_never_gets_more_than_the_keys_level(api, monkeypatch):
    # Review 2's mutations: the owner among the old key's holders is dropped (and the card says so), and a holder
    # with a wider grant on a Read key keeps Read on the new one.
    from backend import api_tools
    from backend.tests.helpers.api_tools import key

    dm, tid, saved, bot, _, _ = _proposal_world(api, monkeypatch)
    old = key(api, tid, 'acme-close-synthetic-ro-5566778899', 'read')
    post(api, f"credentials/{old['id']}/grants", {'subject': 'bot:ops'})
    post(api, f"credentials/{old['id']}/grants", {'subject': 'bot:support'})  # a direct grant: Read and write
    card = _propose(api, dm, [{'key': 'JIRA_LOGIN', 'tool': tid, 'level': 'read', 'bots': ['support']}], bot)
    replaces = card['refs']['key_grant']['rows'][0]['replaces']
    assert (replaces['keeps'], replaces['loses']) == (['Support'], ['ops'])  # the fixture's ops bot has no display name
    post(api, f"tools-chat/proposals/{card['id']}/grant", {})
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, tid, 'bot:ops') is None
        assert api_tools.subject_level(c, tid, 'bot:support') == 'read'
        assert not c.execute(
            "SELECT 1 FROM credential_grants WHERE credential_id=? AND subject='bot:ops' AND revoked_at IS NULL",
            (saved['id'],),
        ).fetchone()


def test_a_suspect_unmarked_through_a_personal_token_is_refused(api, monkeypatch):
    # Review 2's question, AC-15: the API is not the web chat, so a token's un-mark digests let nothing through.
    from backend.auth import Identity

    _, dm = _tools_dm(api)
    api.app.state.store.settings.test_identities['ana-pat'] = Identity(
        'human:ana', 'owner', 'ana@acme.example', via_token=True
    )
    body = {'text': 'build ' + SUSPECT, 'unmarked': [secret_drop.digest(SUSPECT)]}
    post(api, f"threads/{dm['id']}/messages", body, 'ana-pat', expected=422)
    post(api, f"threads/{dm['id']}/messages", body)
