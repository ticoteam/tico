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


def test_the_sweep_names_an_entry_whose_route_refuses_a_level_the_catalogue_allows(api, botops, monkeypatch):
    """Acceptance 8. Release times said "anyone" (the route keeps it to the owner and admins); Takes member bots, a
    Confirm entry, said "anyone" too (Review 2 of PR #1700: the card is made by the catalogue, so only the tap reaches
    the route's admin check); and Bot repositories without its Run workflows rule (as on Oct 27). The sweep names each."""
    from dataclasses import replace

    entries = [
        replace(settings_catalog.entry("release.times"), who="anyone"),
        replace(settings_catalog.entry("computer.member_bots"), who="anyone"),
        replace(settings_catalog.entry("bot.repositories"), confirm_when=(), why=""),
    ]
    monkeypatch.setattr(settings_catalog, "_entries", lambda: entries)
    objects = _objects(api, runner(api))
    wrong, _, _ = sweep(api, botops, "member", entries[:2], objects)
    assert wrong == [
        "release.times: catalogue says ran for the member (direct, anyone), the route refused",
        "computer.member_bots: catalogue says card for the member (confirm, anyone), the route card, then refused",
    ], wrong
    wrong, _, _ = sweep(api, botops, "owner", entries[2:], objects)
    assert wrong == ["bot.repositories: catalogue says ran for the owner (direct, admin), the route card"], wrong
