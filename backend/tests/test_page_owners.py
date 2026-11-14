"""A page's owner and the chat from the page (backend/page_owners.py, spec page-owner-chat).

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). The ops bot runs on a test computer.
"""

from backend import settings_catalog
from backend.tests.helpers.api import claim, get, post, put
from backend.tests.helpers.chat_goals import setup


def automations_owner(api):
    """The built-in Automations Owner every new company gets at setup (the base fixture skips setup)."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('automations-owner','automations-owner','Automations Owner','active')"
        )


FROM = {
    'page': 'listening',
    'label': 'Listening',
    'item': {'kind': 'automation', 'id': 'au_1', 'name': 'Reddit: AI teammates'},
}


def test_listening_is_owned_by_the_automations_owner_until_an_admin_changes_it(api):
    # AC-6: the page follows Settings at its next read, with no release; AC-4: no owner, no chat.
    automations_owner(api)
    pages = get(api, 'page-owners', 'cara-test')['pages']
    assert pages['listening'] == {'bot': 'bot:automations-owner', 'name': 'Listening'}
    assert pages['tools'] == {'bot': None, 'name': 'Tools'}  # no Tools Product Owner in the base fixture: no chat
    setup(api)
    assert put(api, 'page-owners/listening', {'bot': 'ops'}, 'ben-test')['bot'] == 'bot:ops'
    assert get(api, 'page-owners', 'cara-test')['pages']['listening']['bot'] == 'bot:ops'
    assert put(api, 'page-owners/listening', {'bot': None})['bot'] is None
    assert get(api, 'page-owners')['pages']['listening']['bot'] is None


def test_only_an_owner_or_admin_changes_a_page_owner_and_only_to_a_live_bot(api):
    automations_owner(api)
    put(api, 'page-owners/listening', {'bot': 'automations-owner'}, 'cara-test', expected=403)
    put(api, 'page-owners/listening', {'bot': 'no-such-bot'}, expected=404)
    put(api, 'page-owners/calendar', {'bot': 'automations-owner'}, expected=404)  # Calendar opens Ask: no owner
    assert get(api, 'page-owners')['pages']['listening']['bot'] == 'bot:automations-owner'


def test_an_archived_owner_leaves_the_page_without_a_chat(api):
    setup(api)
    put(api, 'page-owners/listening', {'bot': 'ops'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='ops'")
    assert get(api, 'page-owners')['pages']['listening']['bot'] is None


def test_goals_is_owned_by_the_strategy_owner_until_an_admin_changes_it(api):
    # Spec chat-on-every-page, Release 1: Goals' chat is the Strategy Owner's, changeable like Listening's.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('strategy-owner','strategy-owner','Strategy Owner','active')"
        )
    assert get(api, 'page-owners', 'cara-test')['pages']['goals'] == {'bot': 'bot:strategy-owner', 'name': 'Goals'}
    setup(api)
    assert put(api, 'page-owners/goals', {'bot': 'ops'}, 'ben-test')['bot'] == 'bot:ops'
    assert get(api, 'page-owners')['pages']['goals']['bot'] == 'bot:ops'
    assert get(api, 'page-owners')['pages']['listening']['bot'] is None
    e = settings_catalog.entry('pages.goals.owner')
    assert (e.method, e.route, e.field, e.who) == ('PUT', 'page-owners/goals', 'bot', 'admin')
    assert e.read == 'page-owners#pages.goals.bot'


# Spec chat-on-every-page, Release 3: each page's chat is the bot that does its work, changeable like Listening's.
RELEASE_3 = {
    'strategy': ('Strategy', 'strategy-owner'),
    'finance': ('Finance', 'finance-owner'),
    'growth': ('Growth', 'growth-manager'),
    'automations': ('Automations', 'automations-owner'),
    'docs': ('Docs', 'librarian'),
    'updates': ('Updates', 'updates-owner'),
    'rules': ('Rules', 'rules-owner'),
}


def test_release_3_pages_are_owned_by_the_bot_that_does_their_work_until_an_admin_changes_it(api):
    with api.app.state.store.transaction() as c:
        for _name, slug in RELEASE_3.values():
            c.execute(
                "INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')",
                (slug, slug, slug.replace('-', ' ').title()),
            )
    pages = get(api, 'page-owners', 'cara-test')['pages']
    for page, (name, slug) in RELEASE_3.items():
        assert pages[page] == {'bot': 'bot:' + slug, 'name': name}, page
        e = settings_catalog.entry('pages.' + page + '.owner')
        assert (e.method, e.route, e.field, e.who) == ('PUT', 'page-owners/' + page, 'bot', 'admin'), page
        assert e.read == 'page-owners#pages.' + page + '.bot', page
    # Calendar and Meetings open Ask (the Assistant's one chat): no page owner of their own.
    assert 'calendar' not in pages and 'meetings' not in pages
    setup(api)
    assert put(api, 'page-owners/rules', {'bot': 'ops'}, 'ben-test')['bot'] == 'bot:ops'
    assert get(api, 'page-owners')['pages']['rules']['bot'] == 'bot:ops'
    assert get(api, 'page-owners')['pages']['finance']['bot'] == 'bot:finance-owner'


def test_the_settings_catalogue_sets_listenings_owner(api):
    e = settings_catalog.entry('pages.listening.owner')
    assert (e.method, e.route, e.field, e.who) == ('PUT', 'page-owners/listening', 'bot', 'admin')
    assert e.read == 'page-owners#pages.listening.bot'


def test_a_message_from_the_page_reaches_the_bots_turn_with_the_page_and_the_listening(api):
    # AC-2: the chat is the direct one, the same as on the bot's page, and the turn gets refs.from_page.
    machine, _ = setup(api)
    dm = post(api, 'threads/dm', {'members': ['bot:ops']})
    sent = post(
        api,
        f"threads/{dm['id']}/messages",
        {'text': 'Why did this one send nothing yesterday?', 'refs': {'from_page': FROM}},
    )
    assert sent['refs']['from_page'] == FROM
    assert post(api, 'threads/dm', {'members': ['bot:ops']})['id'] == dm['id']
    shown = get(api, f"threads/{dm['id']}/timeline?limit=50&flat=1")['messages']
    assert [m['refs']['from_page'] for m in shown if m['id'] == sent['id']] == [FROM]
    attempt = claim(api, machine)
    assert attempt['message']['body'] == 'Why did this one send nothing yesterday?'
    assert attempt['message']['refs']['from_page'] == FROM


def test_a_from_page_that_is_not_a_page_and_its_item_is_refused(api):
    setup(api)
    dm = post(api, 'threads/dm', {'members': ['bot:ops']})
    for wrong in (
        'listening',
        {'page': 'listening'},
        {'page': 'listening', 'label': 'Listening', 'extra': 1},
        {'page': 'listening', 'label': 'Listening', 'item': {'kind': 'automation', 'id': 'au_1'}},
        {'page': 'listening', 'label': 'x' * 81},
    ):
        post(api, f"threads/{dm['id']}/messages", {'text': 'Hi', 'refs': {'from_page': wrong}}, expected=422)
