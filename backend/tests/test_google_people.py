"""Settings > Google's team list (GET /api/v2/google/people, backend/google_workspace.py team_statuses): admins only,
people and never bots, and each mailbox's state by the same rule the server uses to pick a credential: a live personal
grant, then the Workspace key's domain, then a Connect Google attempt. Google is never asked (`google_call` replaced);
the key is a throwaway RSA key and the domain is acme.example."""

from backend import routines
from backend.store import encode
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
