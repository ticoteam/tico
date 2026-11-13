"""The Tools chat's key detector and the server backstop (backend/secret_drop.py, spec tools-chat-key-drop).

The case file is shared with the page's detector (ui/tests/secret-drop-unit.cts): both must agree on every row (AC-17).
The ops bot owns Tools here; Ana is the owner (the base fixture).
"""

import json
from pathlib import Path

from backend import secret_drop
from backend.tests.helpers.api import claim, get, post, put
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
