"""Every settings catalogue entry (backend/settings_entries.py) asked of BotOps acting for the owner, an admin and a
member: what the route does (ran, a Confirm card, refused) must be what the entry's class and "who may" say. Chris,
Oct 27: the catalogue called Bot repositories direct, the route refused the owner's Run workflows tick, and BotOps
told him nobody but he could do it.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). Everything here is synthetic: the test
database, made-up bots and repositories, and no request leaves the test server.
"""

import pytest

from backend import settings_catalog
from backend.settings_entries import ENTRIES
from backend.tests.helpers.api import runner
from backend.tests.helpers.member_bots import botops, finish, turn  # noqa: F401  (fixtures)
from backend.tests.helpers.settings_parity import KNOWN_GAPS, NOT_PROVEN, PEOPLE, _objects, ask, expected


def sweep(api, botops, role, entries, objects=None):
    """Each entry asked of BotOps for this person: what disagrees with the catalogue, what wasn't proven, and which
    KNOWN_GAPS showed."""
    token, person = PEOPLE[role]
    objects = objects or _objects(api, runner(api))
    wrong, unproven, known = [], {}, set()
    for e in entries:
        attempt = turn(api, botops, person=token, text="Change " + e.name)
        with api.app.state.store.read() as c:
            want = expected(c, e, role, person)
        got = ask(api, attempt, e, objects, person, token)
        finish(api, botops, attempt)
        if got in ("ran", "card", "refused", "card, then refused"):
            if KNOWN_GAPS.get((e.key, role)) == got:
                known.add((e.key, role))
            elif got != want:
                wrong.append(f"{e.key}: catalogue says {want} for the {role} ({e.kind}, {e.who}), the route {got}")
        else:
            unproven[e.key] = got
    return wrong, unproven, known


# The admin sweep (~23 s) is left out: the owner and the member are the two boundaries, and the route-level
# disagreement check below covers the admin level.
@pytest.mark.parametrize("role", ["owner", "member"])
def test_every_setting_does_what_the_catalogue_says_for_botops_acting_for_each_person(api, botops, role):
    wrong, unproven, known = sweep(api, botops, role, ENTRIES)
    assert not wrong, "\n".join(wrong)
    fixed = [k for k in KNOWN_GAPS if k[1] == role and k not in known]
    assert not fixed, "Fixed now: take these out of KNOWN_GAPS: " + repr(fixed)
    new = {k: v for k, v in unproven.items() if k not in NOT_PROVEN}
    assert not new, "Not proven (fix the sample, or add the entry to NOT_PROVEN with why): " + repr(new)
    if role == "owner":
        proven = [k for k in NOT_PROVEN if k not in unproven]
        assert not proven, "Proven now: take these out of NOT_PROVEN: " + repr(proven)
