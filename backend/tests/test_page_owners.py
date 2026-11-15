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
    assert pages['listening'] == {
        'bot': 'bot:automations-owner',
        'name': 'Listening',
        'choice': 'default',
        'default': 'bot:automations-owner',
    }
    # No IT Support Specialist and no General Manager in the base fixture: nothing to fall back to, no chat.
    assert pages['tools'] == {'bot': None, 'name': 'Tools', 'choice': 'default', 'default': None}
    setup(api)
    assert put(api, 'page-owners/listening', {'bot': 'ops'}, 'ben-test')['bot'] == 'bot:ops'
    assert get(api, 'page-owners', 'cara-test')['pages']['listening']['bot'] == 'bot:ops'
    assert put(api, 'page-owners/listening', {'bot': None})['bot'] is None
    assert get(api, 'page-owners')['pages']['listening']['bot'] is None


def test_settings_shows_the_choice_and_who_default_gives_and_default_drops_the_choice(api):
    # Settings > Page owners draws "Default (X)" from `default` and selects what `choice` says.
    automations_owner(api)
    setup(api)
    put(api, 'page-owners/listening', {'bot': 'ops'})
    row = get(api, 'page-owners')['pages']['listening']
    assert (row['bot'], row['choice'], row['default']) == ('bot:ops', 'bot:ops', 'bot:automations-owner')
    put(api, 'page-owners/listening', {'bot': None})
    row = get(api, 'page-owners')['pages']['listening']
    assert (row['bot'], row['choice'], row['default']) == (None, 'none', 'bot:automations-owner')
    back = put(api, 'page-owners/listening', {'bot': 'default'})
    assert (back['bot'], back['choice']) == ('bot:automations-owner', 'default')
    row = get(api, 'page-owners')['pages']['listening']
    assert (row['bot'], row['choice']) == ('bot:automations-owner', 'default')
    put(api, 'page-owners/listening', {'bot': 'default'}, 'cara-test', expected=403)


def test_only_an_owner_or_admin_changes_a_page_owner_and_only_to_a_live_bot(api):
    automations_owner(api)
    put(api, 'page-owners/listening', {'bot': 'automations-owner'}, 'cara-test', expected=403)
    put(api, 'page-owners/listening', {'bot': 'no-such-bot'}, expected=404)
    put(api, 'page-owners/no-such-page', {'bot': 'automations-owner'}, expected=404)
    assert get(api, 'page-owners')['pages']['listening']['bot'] == 'bot:automations-owner'


def general_manager(api):
    """The General Manager every company gets at setup (onboarding.complete_company): every page's last resort."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('general-manager','general-manager','General Manager','active')"
        )


def test_an_archived_owner_gives_way_to_the_default_then_the_general_manager(api):
    # Every page has an owner (style guide "Where chat lives"): a chosen owner that is archived, or a default the company
    # does not have, falls back; only a person's "No owner" leaves a page without a chat.
    setup(api)
    general_manager(api)
    put(api, 'page-owners/listening', {'bot': 'ops'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='ops'")
    assert get(api, 'page-owners')['pages']['listening']['bot'] == 'bot:general-manager'
    automations_owner(api)
    assert get(api, 'page-owners')['pages']['listening']['bot'] == 'bot:automations-owner'
    put(api, 'page-owners/listening', {'bot': None})
    assert get(api, 'page-owners')['pages']['listening']['bot'] is None


def test_every_page_the_app_reaches_has_an_owner_in_a_new_company(api):
    # The base fixture has none of the defaults; with only the General Manager, every page has it as owner.
    general_manager(api)
    pages = get(api, 'page-owners', 'cara-test')['pages']
    assert set(pages) >= {
        'tasks',
        'updates',
        'company',
        'strategy',
        'goals',
        'finance',
        'growth',
        'automations',
        'listening',
        'rules',
        'docs',
        'skills',
        'contacts',
        'tools',
        'mail',
        'usage',
        'settings',
    }
    # Calendar and Meetings open Ask (the Assistant's one chat): no page owner of their own.
    assert 'calendar' not in pages and 'meetings' not in pages
    put(api, 'page-owners/calendar', {'bot': 'general-manager'}, expected=404)
    assert {p['bot'] for p in pages.values()} == {'bot:general-manager'}
    assert pages['finance']['name'] == 'Finance'
    # The bot that does the page's work wins once the company has it.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('finance-owner','finance-owner','Finance Owner','active')"
        )
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('librarian','librarian','Librarian','planned')")
    pages = get(api, 'page-owners')['pages']
    assert pages['finance']['bot'] == 'bot:finance-owner'
    assert pages['docs']['bot'] == 'bot:general-manager', 'a planned default is not live yet'
    # Each page is set like Listening's, by its own settings entry.
    for page in pages:
        e = settings_catalog.entry('pages.' + page + '.owner')
        assert (e.method, e.route, e.field, e.who) == ('PUT', 'page-owners/' + page, 'bot', 'admin'), page
        assert e.read == 'page-owners#pages.' + page + '.bot', page


def test_goals_is_owned_by_the_strategy_owner_until_an_admin_changes_it(api):
    # Spec chat-on-every-page, Release 1: Goals' chat is the Strategy Owner's, changeable like Listening's.
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('strategy-owner','strategy-owner','Strategy Owner','active')"
        )
    goals = get(api, 'page-owners', 'cara-test')['pages']['goals']
    assert (goals['bot'], goals['name']) == ('bot:strategy-owner', 'Goals')
    setup(api)
    assert put(api, 'page-owners/goals', {'bot': 'ops'}, 'ben-test')['bot'] == 'bot:ops'
    assert get(api, 'page-owners')['pages']['goals']['bot'] == 'bot:ops'
    assert get(api, 'page-owners')['pages']['listening']['bot'] is None
    e = settings_catalog.entry('pages.goals.owner')
    assert (e.method, e.route, e.field, e.who) == ('PUT', 'page-owners/goals', 'bot', 'admin')
    assert e.read == 'page-owners#pages.goals.bot'


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
        {'page': 'listening', 'label': 'Listening', 'item': {'kind': 'doc', 'id': 'x' * 201, 'name': 'Plan'}},
    ):
        post(api, f"threads/{dm['id']}/messages", {'text': 'Hi', 'refs': {'from_page': wrong}}, expected=422)


def test_a_long_item_name_is_shortened_not_refused(api):
    # A doc's or a goal's title may run past 200 characters: the message goes, its item's name cut to 200.
    setup(api)
    dm = post(api, 'threads/dm', {'members': ['bot:ops']})
    long = {'page': 'docs', 'label': 'Docs', 'item': {'kind': 'doc', 'id': 'd1', 'name': 'Plan ' * 60}}
    sent = post(api, f"threads/{dm['id']}/messages", {'text': 'Hi', 'refs': {'from_page': long}})
    name = sent['refs']['from_page']['item']['name']
    assert len(name) == 200 and name.endswith('…') and name.startswith('Plan Plan')
