"""Settings > Google's team list (GET /api/v2/google/people, backend/google_workspace.py team_statuses): admins only,
people and never bots, and each mailbox's state by the same rule the server uses to pick a credential: a live personal
grant, then the Workspace key's domain, then a Connect Google attempt. Google is never asked (`google_call` replaced);
the key is a throwaway RSA key and the domain is acme.example."""

import contextlib

from backend import bot_settings as BS
from backend import google_workspace as W
from backend import routines
from backend.store import H, encode
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_google_workspace import (  # noqa: F401  (fixtures)
    MAILBOX, connected, fresh_state, google, keys, personal, private)

PEOPLE = "/api/v2/google/people"


def set_roster(api, people):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))


def bot_mailboxes(monkeypatch, *boxes):
    """The message bot mailboxes the roster would list: (address, person_id, bot)."""
    monkeypatch.setattr(routines, "message_bot_mailboxes", lambda c, roster=None: [
        {"address": address, "person_id": person, "bot": bot, "declared": True} for address, person, bot in boxes])


def test_only_an_admin_reads_the_team_list(api):
    assert api.get(PEOPLE, headers=headers("cara-test")).status_code == 403
    answer = api.get(PEOPLE, headers=headers("ana-test"))
    assert answer.status_code == 200, answer.text
    assert {p["person_id"] for p in answer.json()["people"]} == {"ana", "ben", "cara"}


def test_each_person_has_one_state_and_bots_hidden_and_gone_people_are_left_out(api, private, google, monkeypatch):
    # The Workspace key covers acme.example; Ben's message bot declares his acme.example mailbox though he signs in
    # elsewhere, and the company inbox bot's mailbox belongs to no person.
    bot_mailboxes(monkeypatch, (MAILBOX, "inbox", "inbox"), ("ben@acme.example", "ben", "inbox-ben"))
    set_roster(api, [
        {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
        {"id": "ben", "name": "Ben", "email": "ben@other.example"},
        {"id": "cara", "email": "cara@other.example"},
        {"id": "dan", "email": "dan@other.example"},
        {"id": "gus", "email": "gus@other.example"},
        {"id": "hal", "email": "hal@acme.example"},
        {"id": "ida", "email": "ida@other.example"},
        {"id": "jo", "email": "jo@acme.example", "hidden": True},
        {"id": "kim", "email": "kim@acme.example", "directory_left": True},
        {"id": "lee"},
    ])
    connected(api, private)
    personal(api, "ana", "ana@acme.example")                              # a live grant wins over the key
    personal(api, "cara", "cara@other.example", status="failed", holder=None, grant=None)
    personal(api, "gus", "gus@other.example", status="signing_in", holder=None, grant=None)
    personal(api, "hal", "hal@acme.example", status="failed", holder=None, grant=None)   # covered: the key reads it
    personal(api, "ida", "old-ida@other.example")                        # a grant for an address that is not hers now
    answer = api.get(PEOPLE, headers=headers("ana-test")).json()
    assert answer["more"] is False
    got = {p["person_id"]: (p["mailbox"], p["status"]) for p in answer["people"]}
    assert got == {
        "ana": ("ana@acme.example", "connected"),
        "ben": ("ben@acme.example", "workspace"),
        "cara": ("cara@other.example", "failing"),
        "dan": ("dan@other.example", "none"),
        "gus": ("gus@other.example", "pending"),
        "hal": ("hal@acme.example", "workspace"),
        "ida": ("ida@other.example", "none"),
    }


def test_without_a_workspace_key_a_covered_domain_is_just_not_connected(api, monkeypatch):
    bot_mailboxes(monkeypatch)
    personal(api, "ben", "ben@acme.example", status="failed")             # a live grant that Google refused
    answer = api.get(PEOPLE, headers=headers("ana-test")).json()
    got = {p["person_id"]: p["status"] for p in answer["people"]}
    assert got == {"ana": "none", "ben": "failing", "cara": "none"}


def test_a_reassigned_mailbox_shows_the_grant_on_it_as_the_server_picks_a_credential(api, private, google, monkeypatch):
    # Ana's old mailbox now belongs to Ben's message bot, and Ana's refused grant on it is still live: the server
    # reads that mailbox with her grant (authority 'personal'), so Ben is failing, not covered by the Workspace key.
    bot_mailboxes(monkeypatch, (MAILBOX, "inbox", "inbox"), ("old-ana@acme.example", "ben", "inbox-ben"))
    set_roster(api, [
        {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
        {"id": "ben", "email": "ben@other.example"},
        {"id": "cara", "email": "cara@acme.example"},
        {"id": "dan", "email": "dan@other.example"},
    ])
    connected(api, private)
    personal(api, "ana", "Old-Ana@acme.example", status="failed")
    personal(api, "dan", "dan@other.example", status="signing_in", holder=None, grant=None)
    people = api.get(PEOPLE, headers=headers("ana-test")).json()["people"]
    got = {p["person_id"]: p["status"] for p in people}
    assert got == {"ana": "workspace", "ben": "failing", "cara": "workspace", "dan": "pending"}
    with api.app.state.store.read() as c:
        for person in people:
            picked = W.authority(c, person["mailbox"])
            assert (person["status"] == "workspace") == (picked == "workspace"), person
            if picked == "personal":
                assert person["status"] in ("connected", "failing", "pending"), person


def people_statements(api, monkeypatch):
    """The team list as an admin gets it, and the statements it took, from cold roster and row caches."""
    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get(PEOPLE, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_the_team_list_takes_the_same_statements_for_two_thousand_people(api, private, google, monkeypatch):
    """Each person's mailbox was found by scanning every bot mailbox, and each message bot cost two reads: 2000 people
    with 200 message bots must cost what 20 people with 5 do."""
    bots = {f"mb{n:03d}": {"name": f"mb{n:03d}", "runtime": "fake", "status": "archived" if n == 3 else "active"}
            for n in range(200)}
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, bots, [])
        for n, slug in enumerate(bots):
            c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, None, "ana"))
            tools = [{"service": "gmail", "identity": f"box{n}@acme.example"}] if n % 2 == 0 else []
            BS.write(c, slug, {**bots[slug], "tools": tools})

    def roster(size, with_bots):
        people = [{"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]}]
        for n in range(size):
            person = {"id": f"p{n:04d}", "name": f"P{n:04d}", "email": f"p{n}@other.example"}
            if n < with_bots:
                person["inbox_bot"] = f"mb{n:03d}"
            people.append(person)
        return people

    set_roster(api, roster(20, 5))
    connected(api, private, mailbox="box0@acme.example")
    personal(api, "p0001", "p1@other.example")
    small, few = people_statements(api, monkeypatch)
    set_roster(api, roster(2000, 200))
    big, many = people_statements(api, monkeypatch)
    assert len(small["people"]) == 21 and big["more"] is True and len(big["people"]) == W.PEOPLE_MAX
    got = {p["person_id"]: (p["mailbox"], p["status"]) for p in big["people"]}
    assert got["p0000"] == ("box0@acme.example", "workspace")       # the bot's declared mailbox
    assert got["p0001"] == ("p1@other.example", "connected")        # no declaration: the person's email
    assert got["p0003"] == ("p3@other.example", "none")             # an archived bot stands for nobody
    assert few > 0 and many == few, (few, many)
